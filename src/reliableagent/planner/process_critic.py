"""ProcessSupervisionCritic: step-level critique + multi-criteria scoring.

Per Phase 3's "Stronger Critic with process supervision," this module adds
two capabilities beyond the Phase 0-2 `Critic`s (`ThresholdCritic`,
`LLMCritic`), both opt-in via the `Critic.critique_step` extension point:

    1. **Step-level critique**: `critique_step()` is called by the
       Orchestrator immediately after every step completes (see
       `core/orchestrator.py`), producing a `StepCritique` attached
       directly to that step's `StepRecord` in the `Trajectory` -- a
       problem is flagged the moment it happens, not only inferred later
       from an aggregate score.
    2. **Multi-criteria scoring**: `critique()` returns a `Feedback` whose
       `criterion_scores` breaks the single `quality_score` into
       correctness / efficiency / safety, so a low score's *cause* is
       visible without re-deriving it from the raw trajectory.

Two concrete implementations, mirroring the existing
`ThresholdCritic`/`LLMCritic` split:
    - `DeterministicProcessCritic`: heuristic, no LLM call, fast and free
      -- computes correctness from the tool-call success rate, efficiency
      from a configurable expected-step-count baseline, and safety from
      whether any tool failure looks safety-relevant. Step critique flags
      a step the moment its tool result fails.
    - `LLMProcessCritic`: prompts an `LLMClient` for both the per-step
      verdict and the end-of-plan multi-criteria assessment, for nuance
      a pure heuristic can't capture (e.g. recognizing that a step
      "failed" in a way that was actually expected and handled).
"""

from __future__ import annotations

from reliableagent.core.models import (
    CriterionScores,
    Feedback,
    Plan,
    PlanStep,
    StepCritique,
    Task,
    ToolResult,
)
from reliableagent.exceptions import ReliableAgentError
from reliableagent.llm.base import LLMClient, LLMMessage
from reliableagent.planner.critic import Critic
from reliableagent.planner.prompts import (
    PROCESS_CRITIC_SYSTEM_PROMPT,
    STEP_CRITIQUE_SYSTEM_PROMPT,
    build_process_critic_user_prompt,
    build_step_critique_user_prompt,
    safe_json_loads,
)


class DeterministicProcessCritic(Critic):
    """A fast, free, heuristic Critic that still does real process supervision.

    Useful as the default process-supervision Critic in tests and as a
    cheap first line of supervision in front of a more expensive
    `LLMProcessCritic` (e.g. only escalate to the LLM critic when this
    one's `quality_score` drops below some threshold -- composing the
    two is left to the caller, since the `Critic` interface makes that
    just an `if` statement, not a new abstraction).
    """

    def __init__(self, *, expected_steps: int = 3) -> None:
        """
        Args:
            expected_steps: The "efficiency baseline" -- a plan that
                finishes in this many steps or fewer scores efficiency
                near 1.0; one that takes substantially more scores lower.
                This is intentionally a simple, configurable heuristic,
                not a learned cost model.
        """
        self.expected_steps = max(expected_steps, 1)

    def critique_step(self, step: PlanStep, result: ToolResult | None) -> StepCritique | None:
        """Flag a step the moment its tool call fails; stay silent otherwise.

        Returning `None` for non-tool-call steps and for successful tool
        calls (rather than an explicit `verdict=True` every time) keeps
        the `Trajectory` from being cluttered with a `StepCritique` for
        every single step when this Critic has nothing notable to say --
        the Orchestrator only attaches a `StepCritique` to a `StepRecord`
        when one was actually returned.
        """
        if result is None:
            return None
        if result.success:
            return None
        return StepCritique(
            step_id=step.step_id,
            verdict=False,
            concern=f"Tool call failed: {result.error}",
        )

    def critique(self, task: Task, plan: Plan, results: list[ToolResult]) -> Feedback:
        """Score correctness/efficiency/safety from the results observed so far.

        Note: `results` is the Orchestrator's full accumulated history for
        this RUN, not just the steps under the current `plan` -- so a
        correctness score computed after a successful replan still
        reflects any earlier failed attempt(s) from before the replan,
        rather than only the most recent plan's (now-clean) track record.
        This is intentional: "this run needed to recover from a failure"
        is a real, useful signal to preserve in the final quality record,
        not noise to discard once the run ultimately succeeds.
        """
        if not results:
            scores = CriterionScores(correctness=1.0, efficiency=1.0, safety=1.0)
            return Feedback(
                plan_id=plan.plan_id,
                quality_score=scores.weighted_overall(),
                should_replan=False,
                criterion_scores=scores,
                rationale="No steps executed yet; nothing to assess.",
            )

        failures = [r for r in results if not r.success]
        failure_rate = len(failures) / len(results)
        correctness = max(0.0, 1.0 - failure_rate)

        efficiency = min(1.0, self.expected_steps / max(len(results), 1))

        # This Critic has no direct visibility into guardrail decisions
        # (those live on the Trajectory/StepRecord, not on a bare
        # ToolResult list) -- a perfectly reasonable heuristic in the
        # absence of that signal is "safety wasn't compromised by
        # anything this Critic CAN see," i.e. no tool raised in a way
        # that looks like a safety-relevant failure (vs. an ordinary
        # operational one). This is intentionally conservative: it can
        # only ever flag a concern it actually has evidence for.
        safety_relevant_failures = [r for r in failures if r.error and _looks_safety_relevant(r.error)]
        safety = 1.0 if not safety_relevant_failures else 0.5

        scores = CriterionScores(correctness=correctness, efficiency=efficiency, safety=safety)
        overall = scores.weighted_overall()
        should_replan = correctness < 0.7 or safety < 1.0

        issues = [f"Tool call {r.call_id} failed: {r.error}" for r in failures]
        rationale = (
            f"correctness={correctness:.2f} (from {len(failures)}/{len(results)} failures), "
            f"efficiency={efficiency:.2f} (vs. expected_steps={self.expected_steps}), "
            f"safety={safety:.2f}."
        )

        return Feedback(
            plan_id=plan.plan_id,
            quality_score=overall,
            should_replan=should_replan,
            issues=issues,
            rationale=rationale,
            criterion_scores=scores,
        )


def _looks_safety_relevant(error_text: str) -> bool:
    """A small, explicit keyword heuristic for whether a tool error sounds
    safety/policy-relevant rather than purely operational.

    Deliberately narrow and conservative -- false negatives (missing a
    real safety-relevant failure) are far more likely than false
    positives here, which is the correct bias for a heuristic that only
    ever *lowers* a safety score, never raises one past 1.0.
    """
    lowered = error_text.lower()
    return any(
        keyword in lowered
        for keyword in ("unauthorized", "permission denied", "forbidden", "policy violation")
    )


class LLMProcessCritic(Critic):
    """Process-supervision Critic backed by an LLM, for both step- and plan-level critique."""

    def __init__(self, llm_client: LLMClient, *, max_tokens: int = 512) -> None:
        self._llm_client = llm_client
        self._max_tokens = max_tokens

    def critique_step(self, step: PlanStep, result: ToolResult | None) -> StepCritique | None:
        if result is None:
            return None

        user_prompt = build_step_critique_user_prompt(step, result)
        try:
            response = self._llm_client.complete(
                [LLMMessage(role="user", content=user_prompt)],
                system=STEP_CRITIQUE_SYSTEM_PROMPT,
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
            data = safe_json_loads(response.text)
            return StepCritique(
                step_id=step.step_id,
                verdict=bool(data["verdict"]),
                concern=str(data.get("concern", "")),
            )
        except Exception as exc:  # noqa: BLE001 - never let critique itself break execution
            return StepCritique(
                step_id=step.step_id,
                verdict=True,
                concern=f"(step critique unavailable: {exc})",
            )

    def critique(self, task: Task, plan: Plan, results: list[ToolResult]) -> Feedback:
        plan_summary = "\n".join(
            f"  {i + 1}. [{s.step_type.value}] {s.description}" for i, s in enumerate(plan.steps)
        )
        user_prompt = build_process_critic_user_prompt(task.description, plan_summary, results)

        try:
            response = self._llm_client.complete(
                [LLMMessage(role="user", content=user_prompt)],
                system=PROCESS_CRITIC_SYSTEM_PROMPT,
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001
            raise ReliableAgentError(
                f"Process critic LLM call failed: {exc}", context={"task_id": task.task_id}
            ) from exc

        try:
            data = safe_json_loads(response.text)
            scores = CriterionScores(
                correctness=float(data["correctness"]),
                efficiency=float(data["efficiency"]),
                safety=float(data["safety"]),
            )
            return Feedback(
                plan_id=plan.plan_id,
                quality_score=scores.weighted_overall(),
                should_replan=bool(data["should_replan"]),
                issues=[str(i) for i in data.get("issues", [])],
                rationale=str(data.get("rationale", "")),
                criterion_scores=scores,
            )
        except Exception as exc:  # noqa: BLE001
            raise ReliableAgentError(
                f"Process critic response could not be parsed: {exc}",
                context={"task_id": task.task_id, "raw_response": response.text[:2000]},
            ) from exc
