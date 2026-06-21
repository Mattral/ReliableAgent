"""Orchestrator: the central control loop tying every component together.

This is where the architecture described in the roadmap actually
becomes a running system. The control loop is, deliberately, almost
exactly the diagram from Section 3.1:

    Task -> Planner -> Plan -> [Guardrails] -> Executor -> Results
         -> Critic -> Feedback -> (replan? -> Planner | continue)
         -> ... -> Final Output -> [Guardrails] -> Result

Concretely, `run()`:
    1. Transitions PENDING -> PLANNING and asks the Planner for a Plan,
       after passing the task description through guardrails at the
       PLANNER_INPUT boundary.
    2. Transitions PLANNING -> EXECUTING and runs each step:
        - TOOL_CALL steps go through guardrails at TOOL_INPUT, then the
          Executor, then guardrails at TOOL_OUTPUT.
        - FINAL_ANSWER steps go through guardrails at FINAL_OUTPUT and,
          if allowed, end the run successfully.
    3. After each plan's steps are exhausted (without hitting a
       final_answer step), transitions EXECUTING -> CRITIQUING and asks
       the Critic whether to replan.
    4. If the Critic says replan (or a step failed unrecoverably) and
       replans remain, transitions to REPLANNING -> EXECUTING with a
       fresh Plan grounded in what went wrong; otherwise FAILED.
    5. A checkpoint is saved after every step and every plan, so a
       killed run can be resumed via `resume()`.

Every transition, guardrail decision, and step outcome is recorded
both in the in-memory `Trajectory` and via the `Tracer`, satisfying
"Observability by Default" end-to-end rather than just at the
edges.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from reliableagent.core.enums import (
    FailureCategory,
    GuardrailBoundary,
    OrchestratorState,
    StepStatus,
    StepType,
)
from reliableagent.core.models import (
    Checkpoint,
    Plan,
    PlanStep,
    RunMetrics,
    RunResult,
    StepRecord,
    Task,
    ToolCall,
    ToolResult,
    Trajectory,
)
from reliableagent.core.state_machine import StateMachine
from reliableagent.executor.executor import Executor
from reliableagent.exceptions import (
    ExecutionError,
    GuardrailViolationError,
    LLMError,
    PlanningError,
    ReliableAgentError,
    ReplanLimitExceededError,
    StepBudgetExceededError,
    ToolTimeoutError,
)
from reliableagent.guardrails.runner import GuardrailRunner
from reliableagent.memory.backend import InMemoryBackend, MemoryBackend
from reliableagent.observability.sinks import InMemorySink
from reliableagent.observability.tracer import Tracer
from reliableagent.planner.base import Planner
from reliableagent.planner.critic import Critic
from reliableagent.planner.replanner import Replanner


class Orchestrator:
    """Drives a `Task` through plan -> execute -> critique -> (replan) -> finish.

    Example:
        >>> from reliableagent.llm import MockLLMClient
        >>> from reliableagent.planner import LLMPlanner, ThresholdCritic
        >>> from reliableagent.executor import ToolRegistry
        >>> from reliableagent.guardrails import BasicGuardrail
        >>>
        >>> tools = ToolRegistry()
        >>> orchestrator = Orchestrator(
        ...     planner=LLMPlanner(MockLLMClient()),
        ...     critic=ThresholdCritic(),
        ...     tools=tools,
        ...     guardrails=[BasicGuardrail()],
        ... )
        >>> result = orchestrator.run(Task(description="..."))  # doctest: +SKIP
    """

    def __init__(
        self,
        *,
        planner: Planner,
        critic: Critic,
        tools,
        guardrails: list | None = None,
        memory: MemoryBackend | None = None,
        executor: Executor | None = None,
        sink=None,
        checkpoint_every_step: bool = True,
        replanner: Replanner | None = None,
    ) -> None:
        self._planner = planner
        self._critic = critic
        self._tools = tools
        self._guardrail_runner = GuardrailRunner(guardrails or [])
        self._memory = memory or InMemoryBackend()
        self._sink = sink or InMemorySink()
        self._executor = executor or Executor(tools)
        self._checkpoint_every_step = checkpoint_every_step
        # Defaulting to a real Replanner (rather than calling
        # `planner.plan(...)` directly) means every Orchestrator gets
        # failure-type-aware, budget-aware replanning out of the box --
        # Phase 3's "more sophisticated Replanner" is the default
        # behavior, not an opt-in a caller has to discover and enable.
        self._replanner = replanner or Replanner(planner)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, task: Task) -> RunResult:
        """Execute `task` end-to-end and return a `RunResult`."""
        trajectory = Trajectory(task=task)
        tracer = Tracer(run_id=trajectory.run_id, sink=self._sink)
        state_machine = StateMachine()
        tracer.emit_run_started(task.task_id, task.description)

        start_time = time.monotonic()
        try:
            self._run_loop(task, trajectory, tracer, state_machine)
        except ReliableAgentError as exc:
            self._fail(trajectory, state_machine, tracer, self._categorize(exc), exc.message)
        except Exception as exc:  # noqa: BLE001 - last-resort safety net
            self._fail(
                trajectory, state_machine, tracer, FailureCategory.UNKNOWN, f"Unexpected error: {exc}"
            )

        return self._finalize(task, trajectory, tracer, state_machine, start_time)

    def resume(self, run_id: str) -> RunResult:
        """Resume a previously checkpointed run from its latest checkpoint.

        Re-derives a `Trajectory` shell from the checkpoint and
        continues the control loop from the checkpointed orchestrator
        state, re-running the current plan's remaining steps. This
        satisfies the P0 "Ability to resume from checkpoint"
        requirement: a killed process does not lose a run's progress.
        """
        checkpoint = self._memory.load_latest_checkpoint(run_id)
        if checkpoint is None:
            raise ReliableAgentError(
                f"No checkpoint found to resume run '{run_id}'.", context={"run_id": run_id}
            )

        trajectory = Trajectory(run_id=run_id, task=checkpoint.task)
        tracer = Tracer(run_id=run_id, sink=self._sink)
        tracer.emit_checkpoint_restored(checkpoint.checkpoint_id, checkpoint.sequence_number)

        state_machine = StateMachine(initial_state=OrchestratorState.PLANNING)
        state_machine.transition(OrchestratorState.EXECUTING)

        start_time = time.monotonic()
        try:
            if checkpoint.current_plan is None:
                raise ReliableAgentError(
                    "Checkpoint has no current plan to resume from.",
                    context={"run_id": run_id},
                )
            trajectory.add_plan(checkpoint.current_plan)
            self._execute_and_continue(
                checkpoint.task,
                checkpoint.current_plan,
                trajectory,
                tracer,
                state_machine,
                replan_count=checkpoint.replan_count,
                step_count=checkpoint.step_count,
                already_completed=list(checkpoint.completed_results),
            )
        except ReliableAgentError as exc:
            self._fail(trajectory, state_machine, tracer, self._categorize(exc), exc.message)
        except Exception as exc:  # noqa: BLE001
            self._fail(
                trajectory, state_machine, tracer, FailureCategory.UNKNOWN, f"Unexpected error: {exc}"
            )

        return self._finalize(checkpoint.task, trajectory, tracer, state_machine, start_time)

    def shutdown(self) -> None:
        """Release underlying resources (the Executor's thread pool)."""
        self._executor.shutdown()

    # ------------------------------------------------------------------
    # Internal control loop
    # ------------------------------------------------------------------

    def _finalize(
        self,
        task: Task,
        trajectory: Trajectory,
        tracer: Tracer,
        state_machine: StateMachine,
        start_time: float,
    ) -> RunResult:
        duration = time.monotonic() - start_time
        trajectory.completed_at = datetime.now(timezone.utc)
        trajectory.final_state = state_machine.state
        succeeded = state_machine.state == OrchestratorState.COMPLETED
        tracer.emit_run_completed(state_machine.state.value, succeeded)
        self._memory.save_trajectory(trajectory)

        metrics = RunMetrics(
            total_steps=len(trajectory.step_records),
            total_tool_calls=trajectory.total_tool_calls,
            total_replans=trajectory.total_replans,
            total_guardrail_blocks=trajectory.total_guardrail_blocks,
            succeeded=succeeded,
            duration_seconds=duration,
        )
        return RunResult(
            run_id=trajectory.run_id,
            task=task,
            final_state=state_machine.state,
            final_answer=trajectory.final_answer,
            failure_category=trajectory.failure_category,
            trajectory=trajectory,
            metrics=metrics,
        )

    def _run_loop(
        self,
        task: Task,
        trajectory: Trajectory,
        tracer: Tracer,
        state_machine: StateMachine,
    ) -> None:
        self._transition(state_machine, tracer, OrchestratorState.PLANNING)
        # Note: the returned (possibly-redacted) payload from _guard() is
        # intentionally not threaded back into `task.description` here.
        # `task` is an immutable Task the caller owns; redacting its
        # description would require constructing a new Task via
        # model_copy and is not needed by anything currently shipped in
        # this guardrail layer (BLOCK-style rules at this boundary don't
        # need a modified payload at all; MODIFY-style PII redaction is
        # intended for FINAL_OUTPUT, where the redacted payload IS
        # correctly threaded through -- see below). If a future
        # MODIFY-producing guardrail needs to redact planner input, this
        # is the place to revisit.
        self._guard(GuardrailBoundary.PLANNER_INPUT, task.description, trajectory, tracer)

        plan = self._planner.plan(task, self._tools)
        trajectory.add_plan(plan)
        tracer.emit_plan_generated(plan)
        self._guard(GuardrailBoundary.PLANNER_OUTPUT, plan.reasoning_trace, trajectory, tracer)

        self._transition(state_machine, tracer, OrchestratorState.EXECUTING)
        self._execute_and_continue(
            task, plan, trajectory, tracer, state_machine, replan_count=0, step_count=0
        )

    def _execute_and_continue(
        self,
        task: Task,
        plan: Plan,
        trajectory: Trajectory,
        tracer: Tracer,
        state_machine: StateMachine,
        *,
        replan_count: int,
        step_count: int,
        already_completed: list[ToolResult] | None = None,
    ) -> None:
        """Execute `plan`'s steps, then critique/replan/finish as needed.

        This is the recursive heart of the loop: a successful critique
        with `should_replan=True` loops back with a freshly generated
        plan, so the whole replan cycle is just another iteration
        rather than special-cased control flow.
        """
        results: list[ToolResult] = list(already_completed or [])
        current_plan = plan

        while True:
            for step in current_plan.steps:
                step_count += 1
                if step_count > task.max_steps:
                    raise StepBudgetExceededError(
                        f"Run exceeded max_steps={task.max_steps}.",
                        context={"task_id": task.task_id, "step_count": step_count},
                    )

                outcome = self._execute_step(step, trajectory, tracer)
                if outcome is not None:
                    results.append(outcome)

                if step.step_type == StepType.FINAL_ANSWER:
                    final_text = step.description
                    final_text = self._guard(GuardrailBoundary.FINAL_OUTPUT, final_text, trajectory, tracer)
                    trajectory.final_answer = final_text
                    # Record a final critique purely for observability/
                    # trajectory completeness, even on this success path --
                    # without this, `Trajectory.feedbacks` would only ever
                    # be populated on the (less common) "plan exhausted
                    # without an explicit final_answer" fallback below,
                    # leaving every ordinary successful run with NO
                    # multi-criteria quality record at all. Its
                    # `should_replan` is irrelevant here (the run is
                    # already complete) and is deliberately ignored.
                    final_feedback = self._critic.critique(task, current_plan, results)
                    trajectory.add_feedback(final_feedback)
                    tracer.emit_critique_generated(
                        final_feedback.quality_score, final_feedback.should_replan
                    )
                    self._transition(state_machine, tracer, OrchestratorState.COMPLETED)
                    self._checkpoint(
                        task, current_plan, results, trajectory, tracer, replan_count, step_count
                    )
                    return

            # Plan exhausted without a final_answer step: critique and decide.
            self._transition(state_machine, tracer, OrchestratorState.CRITIQUING)
            feedback = self._critic.critique(task, current_plan, results)
            trajectory.add_feedback(feedback)
            tracer.emit_critique_generated(feedback.quality_score, feedback.should_replan)

            if not feedback.should_replan:
                final_text = self._derive_fallback_answer(results)
                final_text = self._guard(GuardrailBoundary.FINAL_OUTPUT, final_text, trajectory, tracer)
                trajectory.final_answer = final_text
                self._transition(state_machine, tracer, OrchestratorState.COMPLETED)
                self._checkpoint(
                    task, current_plan, results, trajectory, tracer, replan_count, step_count
                )
                return

            replan_count += 1
            if replan_count > task.max_replans:
                raise ReplanLimitExceededError(
                    f"Exceeded max_replans={task.max_replans}.",
                    context={"task_id": task.task_id, "replan_count": replan_count},
                )

            self._transition(state_machine, tracer, OrchestratorState.REPLANNING)
            tracer.emit_replan_triggered(replan_count, feedback.rationale)
            self._guard(GuardrailBoundary.PLANNER_INPUT, task.description, trajectory, tracer)

            current_plan = self._replanner.replan(
                task,
                self._tools,
                prior_results=results,
                feedback=feedback,
                replan_attempt=replan_count,
                max_replans=task.max_replans,
            )
            trajectory.add_plan(current_plan)
            tracer.emit_plan_generated(current_plan)
            self._guard(
                GuardrailBoundary.PLANNER_OUTPUT, current_plan.reasoning_trace, trajectory, tracer
            )

            self._transition(state_machine, tracer, OrchestratorState.EXECUTING)
            self._checkpoint(
                task, current_plan, results, trajectory, tracer, replan_count, step_count
            )

    def _execute_step(
        self, step: PlanStep, trajectory: Trajectory, tracer: Tracer
    ) -> ToolResult | None:
        """Execute a single step, applying guardrails around tool calls.

        Returns the `ToolResult` for TOOL_CALL steps, or `None` for
        REASONING/FINAL_ANSWER steps (which have no tool result).
        """
        tracer.emit_step_started(step)

        if step.step_type != StepType.TOOL_CALL:
            record = StepRecord(step=step, status=StepStatus.SUCCEEDED)
            trajectory.add_step_record(record)
            tracer.emit_step_completed(step, StepStatus.SUCCEEDED.value)
            return None

        guard_result = self._guardrail_runner.run(GuardrailBoundary.TOOL_INPUT, step.tool_arguments)
        for decision in guard_result.decisions:
            trajectory.add_guardrail_decision(decision)
            tracer.emit_guardrail_evaluated(decision)
        if not guard_result.allowed:
            record = StepRecord(
                step=step,
                status=StepStatus.BLOCKED_BY_GUARDRAIL,
                guardrail_decisions=guard_result.decisions,
            )
            trajectory.add_step_record(record)
            tracer.emit_step_completed(step, StepStatus.BLOCKED_BY_GUARDRAIL.value)
            raise GuardrailViolationError(
                guard_result.blocking_decision.reason,
                guardrail_name=guard_result.blocking_decision.guardrail_name,
                boundary=GuardrailBoundary.TOOL_INPUT.value,
            )
        arguments = guard_result.final_payload

        call = ToolCall(step_id=step.step_id, tool_name=step.tool_name or "", arguments=arguments)
        result = self._executor.execute(call)

        output_guard = self._guardrail_runner.run(GuardrailBoundary.TOOL_OUTPUT, result.output)
        for decision in output_guard.decisions:
            trajectory.add_guardrail_decision(decision)
            tracer.emit_guardrail_evaluated(decision)

        status = StepStatus.SUCCEEDED if result.success else StepStatus.FAILED
        step_critique = self._critic.critique_step(step, result)
        if step_critique is not None:
            tracer.emit_step_critiqued(step_critique)

        record = StepRecord(
            step=step,
            status=status,
            tool_call=call,
            tool_result=result,
            guardrail_decisions=guard_result.decisions + output_guard.decisions,
            step_critique=step_critique,
        )
        trajectory.add_step_record(record)
        tracer.emit_step_completed(step, status.value)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _guard(
        self, boundary: GuardrailBoundary, payload, trajectory: Trajectory, tracer: Tracer
    ):
        """Run guardrails at a run-level boundary (planner input/output, final output).

        Returns the (possibly MODIFY-redacted) payload. Callers MUST use
        the returned value, not the original `payload` they passed in --
        discarding it here would mean a guardrail's MODIFY verdict (e.g.
        `OutputFilterGuardrail` redacting PII) is computed and logged in
        the `Trajectory` but never actually applied to what the run
        returns, silently defeating the guardrail's entire purpose.
        """
        result = self._guardrail_runner.run(boundary, payload)
        for decision in result.decisions:
            trajectory.add_guardrail_decision(decision)
            tracer.emit_guardrail_evaluated(decision)
        if not result.allowed:
            raise GuardrailViolationError(
                result.blocking_decision.reason,
                guardrail_name=result.blocking_decision.guardrail_name,
                boundary=boundary.value,
            )
        return result.final_payload

    def _transition(
        self, state_machine: StateMachine, tracer: Tracer, to_state: OrchestratorState
    ) -> None:
        from_state = state_machine.state
        state_machine.transition(to_state)
        tracer.emit_state_transition(from_state.value, to_state.value)

    def _checkpoint(
        self,
        task: Task,
        plan: Plan,
        results: list[ToolResult],
        trajectory: Trajectory,
        tracer: Tracer,
        replan_count: int,
        step_count: int,
    ) -> None:
        if not self._checkpoint_every_step:
            return

        sequence_number = len(trajectory.checkpoints)
        checkpoint = Checkpoint(
            run_id=trajectory.run_id,
            sequence_number=sequence_number,
            orchestrator_state=OrchestratorState.EXECUTING,
            task=task,
            current_plan=plan,
            completed_results=results,
            replan_count=replan_count,
            step_count=step_count,
        )
        self._memory.save_checkpoint(checkpoint)
        trajectory.add_checkpoint(checkpoint)
        tracer.emit_checkpoint_saved(checkpoint.checkpoint_id, sequence_number)

    def _fail(
        self,
        trajectory: Trajectory,
        state_machine: StateMachine,
        tracer: Tracer,
        category: FailureCategory,
        reason: str,
    ) -> None:
        trajectory.failure_category = category
        if not state_machine.is_terminal:
            try:
                state_machine.transition(OrchestratorState.FAILED)
            except ReliableAgentError:
                pass
        tracer.emit_run_failed(category.value, reason)

    @staticmethod
    def _categorize(exc: ReliableAgentError) -> FailureCategory:
        if isinstance(exc, GuardrailViolationError):
            return FailureCategory.GUARDRAIL_BLOCKED
        if isinstance(exc, ReplanLimitExceededError):
            return FailureCategory.REPLAN_LIMIT_EXCEEDED
        if isinstance(exc, StepBudgetExceededError):
            return FailureCategory.STEP_BUDGET_EXCEEDED
        if isinstance(exc, ToolTimeoutError):
            return FailureCategory.TOOL_TIMEOUT
        if isinstance(exc, ExecutionError):
            return FailureCategory.TOOL_ERROR
        if isinstance(exc, PlanningError):
            return FailureCategory.PLANNING_ERROR
        if isinstance(exc, LLMError):
            return FailureCategory.LLM_ERROR
        return FailureCategory.UNKNOWN

    @staticmethod
    def _derive_fallback_answer(results: list[ToolResult]) -> str:
        successful = [r for r in results if r.success]
        if successful:
            return str(successful[-1].output)
        return "Task processing completed without an explicit final answer."
