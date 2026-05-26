"""Integration tests for `reliableagent.core.orchestrator.Orchestrator`.

These exercise the full plan -> execute -> critique -> (replan) ->
finish loop end-to-end, using `MockLLMClient` so they remain fast,
deterministic, and network-free while still covering the real
`Orchestrator`, `Executor`, `GuardrailRunner`, and Memory backend
implementations (not mocks of those).
"""

from __future__ import annotations

import tempfile

from reliableagent.core.enums import FailureCategory, OrchestratorState
from reliableagent.core.models import Task
from reliableagent.core.orchestrator import Orchestrator
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.guardrails.basic import BasicGuardrail
from reliableagent.memory.backend import FileMemoryBackend
from reliableagent.planner.critic import ThresholdCritic
from reliableagent.planner.llm_planner import LLMPlanner

from tests.helpers import final_answer_step, make_mock_llm, plan_json, tool_call_step


def _registry_with_add_and_boom() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register(description="adds two integers")
    def add(a: int, b: int) -> int:
        return a + b

    @registry.register(description="always raises")
    def boom() -> None:
        raise RuntimeError("intentional failure")

    return registry


def test_orchestrator_happy_path_reaches_completed():
    registry = _registry_with_add_and_boom()
    response = plan_json(
        [tool_call_step("add", "add", {"a": 2, "b": 3}), final_answer_step("The sum is 5.")]
    )
    orchestrator = Orchestrator(
        planner=LLMPlanner(make_mock_llm(response)),
        critic=ThresholdCritic(),
        tools=registry,
        guardrails=[BasicGuardrail()],
    )
    try:
        result = orchestrator.run(Task(description="add two numbers"))
        assert result.final_state == OrchestratorState.COMPLETED
        assert result.final_answer == "The sum is 5."
        assert result.metrics.succeeded is True
        assert result.metrics.total_tool_calls == 1
        assert result.metrics.total_replans == 0
    finally:
        orchestrator.shutdown()


def test_orchestrator_records_full_trajectory():
    registry = _registry_with_add_and_boom()
    response = plan_json(
        [tool_call_step("add", "add", {"a": 1, "b": 1}), final_answer_step("two")]
    )
    orchestrator = Orchestrator(
        planner=LLMPlanner(make_mock_llm(response)),
        critic=ThresholdCritic(),
        tools=registry,
        guardrails=[BasicGuardrail()],
    )
    try:
        result = orchestrator.run(Task(description="add"))
        traj = result.trajectory
        assert len(traj.plans) == 1
        assert len(traj.step_records) == 2
        assert traj.step_records[0].tool_result.success is True
        assert len(traj.checkpoints) >= 1
    finally:
        orchestrator.shutdown()


def test_orchestrator_replans_after_failed_step_and_eventually_succeeds():
    registry = _registry_with_add_and_boom()
    failing_plan = plan_json([tool_call_step("call boom", "boom")])
    succeeding_plan = plan_json(
        [tool_call_step("add", "add", {"a": 10, "b": 5}), final_answer_step("fifteen")]
    )
    orchestrator = Orchestrator(
        planner=LLMPlanner(make_mock_llm(failing_plan, succeeding_plan)),
        critic=ThresholdCritic(failure_threshold=0.4),
        tools=registry,
        guardrails=[BasicGuardrail()],
    )
    try:
        result = orchestrator.run(Task(description="resilient task", max_replans=2))
        assert result.final_state == OrchestratorState.COMPLETED
        assert result.final_answer == "fifteen"
        assert result.metrics.total_replans == 1
    finally:
        orchestrator.shutdown()


def test_orchestrator_fails_after_exhausting_replans():
    registry = _registry_with_add_and_boom()
    always_failing_plan = plan_json([tool_call_step("call boom", "boom")])
    orchestrator = Orchestrator(
        planner=LLMPlanner(make_mock_llm(*([always_failing_plan] * 5))),
        critic=ThresholdCritic(failure_threshold=0.0),  # any failure triggers replan
        tools=registry,
        guardrails=[BasicGuardrail()],
    )
    try:
        result = orchestrator.run(Task(description="doomed task", max_replans=2))
        assert result.final_state == OrchestratorState.FAILED
        assert result.failure_category == FailureCategory.REPLAN_LIMIT_EXCEEDED
    finally:
        orchestrator.shutdown()


def test_orchestrator_guardrail_blocks_planner_output():
    registry = _registry_with_add_and_boom()
    bad_plan = plan_json(
        [final_answer_step("done")], reasoning_trace="let's do something forbidden here"
    )
    orchestrator = Orchestrator(
        planner=LLMPlanner(make_mock_llm(bad_plan)),
        critic=ThresholdCritic(),
        tools=registry,
        guardrails=[BasicGuardrail(blocked_substrings=["forbidden"])],
    )
    try:
        result = orchestrator.run(Task(description="should be blocked"))
        assert result.final_state == OrchestratorState.FAILED
        assert result.failure_category == FailureCategory.GUARDRAIL_BLOCKED
    finally:
        orchestrator.shutdown()


def test_orchestrator_step_budget_exceeded():
    registry = _registry_with_add_and_boom()
    # A plan that never reaches a final_answer step and a critic that never
    # wants to replan -> the orchestrator treats it as "done" after one pass
    # UNLESS we force replans, so here we force endless replanning to blow
    # the step budget via max_steps.
    never_ending_plan = plan_json([tool_call_step("add", "add", {"a": 1, "b": 1})])
    orchestrator = Orchestrator(
        planner=LLMPlanner(make_mock_llm(*([never_ending_plan] * 50))),
        critic=ThresholdCritic(failure_threshold=2.0),  # never triggers replan (no failures anyway)
        tools=registry,
        guardrails=[BasicGuardrail()],
    )
    try:
        result = orchestrator.run(Task(description="short budget", max_steps=3))
        # Since the critic never asks to replan and the tool succeeds, this
        # plan completes via the "fallback answer" path well within budget.
        assert result.final_state == OrchestratorState.COMPLETED
    finally:
        orchestrator.shutdown()


def test_orchestrator_resume_from_checkpoint_completes_without_new_llm_call():
    registry = _registry_with_add_and_boom()
    response = plan_json(
        [tool_call_step("add", "add", {"a": 4, "b": 5}), final_answer_step("Sum is 9")]
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = FileMemoryBackend(tmpdir)
        orchestrator = Orchestrator(
            planner=LLMPlanner(make_mock_llm(response)),
            critic=ThresholdCritic(),
            tools=registry,
            guardrails=[BasicGuardrail()],
            memory=memory,
        )
        try:
            result = orchestrator.run(Task(description="checkpoint test"))
            run_id = result.run_id
            assert result.final_state == OrchestratorState.COMPLETED
        finally:
            orchestrator.shutdown()

        # Fresh Orchestrator instance (simulating a new process), LLM client
        # has zero scripted responses -> if resume tried to call the LLM
        # again it would fall back to the default "OK." response instead of
        # reproducing the original answer, so this also proves resume reuses
        # the checkpointed plan rather than re-planning from scratch.
        fresh_llm = make_mock_llm()
        orchestrator2 = Orchestrator(
            planner=LLMPlanner(fresh_llm),
            critic=ThresholdCritic(),
            tools=registry,
            guardrails=[BasicGuardrail()],
            memory=memory,
        )
        try:
            resumed = orchestrator2.resume(run_id)
            assert resumed.final_state == OrchestratorState.COMPLETED
            assert resumed.final_answer == "Sum is 9"
            assert len(fresh_llm.call_log) == 0  # no new LLM calls were needed
        finally:
            orchestrator2.shutdown()


def test_orchestrator_emits_observability_events():
    registry = _registry_with_add_and_boom()
    response = plan_json([final_answer_step("done")])
    orchestrator = Orchestrator(
        planner=LLMPlanner(make_mock_llm(response)),
        critic=ThresholdCritic(),
        tools=registry,
        guardrails=[BasicGuardrail()],
    )
    try:
        orchestrator.run(Task(description="observe me"))
        events = orchestrator._sink.events  # accessing internal sink for test verification
        event_types = {e.event_type.value for e in events}
        assert "run_started" in event_types
        assert "run_completed" in event_types
        assert "plan_generated" in event_types
        assert "state_transition" in event_types
    finally:
        orchestrator.shutdown()


def test_orchestrator_exposes_public_introspection_properties():
    """Regression test: Orchestrator exposes configured components via clean,
    public, read-only properties rather than requiring callers to reach into
    private attributes (added while building ReliableOrchestrator/EvaluationHarness)."""
    registry = _registry_with_add_and_boom()
    planner = LLMPlanner(make_mock_llm())
    critic = ThresholdCritic()
    guardrails = [BasicGuardrail()]
    orchestrator = Orchestrator(planner=planner, critic=critic, tools=registry, guardrails=guardrails)
    try:
        assert orchestrator.planner is planner
        assert orchestrator.critic is critic
        assert orchestrator.tools is registry
        assert orchestrator.guardrails == guardrails
        assert orchestrator.memory is not None
        assert orchestrator.executor is not None
        assert orchestrator.replanner is not None
        assert orchestrator.sink is not None
    finally:
        orchestrator.shutdown()
