"""Critic: evaluates a trajectory-so-far and decides whether to replan.

Per the roadmap, the Critic closes the planning loop: it's what turns
"execute the plan and hope" into "execute, evaluate, and adapt."
`should_replan` on the resulting `Feedback` is the explicit signal the
Orchestrator uses to transition into `REPLANNING` rather than
continuing to execute a plan that's clearly not working.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from reliableagent.core.models import Feedback, Plan, Task, ToolResult
from reliableagent.exceptions import ReliableAgentError
from reliableagent.llm.base import LLMClient, LLMMessage
from reliableagent.planner.prompts import (
    CRITIC_SYSTEM_PROMPT,
    build_critic_user_prompt,
    safe_json_loads,
)


class Critic(ABC):
    """Base class for all Critic strategies."""

    @abstractmethod
    def critique(self, task: Task, plan: Plan, results: list[ToolResult]) -> Feedback:
        """Assess the trajectory so far and decide whether to replan.

        Args:
            task: The task being worked on.
            plan: The plan currently being executed.
            results: Results of steps executed so far under this plan.

        Returns:
            A `Feedback` with `plan_id` set to `plan.plan_id`.
        """
        raise NotImplementedError


class ThresholdCritic(Critic):
    """A simple, deterministic, non-LLM Critic based on a failure-rate threshold.

    Useful as a fast default and in tests: replans whenever the
    proportion of failed results so far exceeds `failure_threshold`.
    No LLM call, no latency, no cost — appropriate when "good enough"
    judgment is acceptable, or as a guardrail layer in front of a more
    expensive `LLMCritic`.
    """

    def __init__(self, *, failure_threshold: float = 0.5) -> None:
        self.failure_threshold = failure_threshold

    def critique(self, task: Task, plan: Plan, results: list[ToolResult]) -> Feedback:
        if not results:
            return Feedback(plan_id=plan.plan_id, quality_score=1.0, should_replan=False)

        failures = [r for r in results if not r.success]
        failure_rate = len(failures) / len(results)
        should_replan = failure_rate > self.failure_threshold
        issues = [f"Tool call {r.call_id} failed: {r.error}" for r in failures]

        return Feedback(
            plan_id=plan.plan_id,
            quality_score=max(0.0, 1.0 - failure_rate),
            should_replan=should_replan,
            issues=issues,
            rationale=(
                f"{len(failures)}/{len(results)} steps failed "
                f"({failure_rate:.0%} failure rate, threshold={self.failure_threshold:.0%})."
            ),
        )


class LLMCritic(Critic):
    """Critic strategy backed by an LLM completion call.

    Gives a more nuanced assessment than `ThresholdCritic` (e.g. it
    can recognize that a single failed step is fine if the plan
    already accounted for a fallback), at the cost of an extra LLM
    call per critique.
    """

    def __init__(self, llm_client: LLMClient, *, max_tokens: int = 512) -> None:
        self._llm_client = llm_client
        self._max_tokens = max_tokens

    def critique(self, task: Task, plan: Plan, results: list[ToolResult]) -> Feedback:
        plan_summary = "\n".join(
            f"  {i + 1}. [{s.step_type.value}] {s.description}" for i, s in enumerate(plan.steps)
        )
        user_prompt = build_critic_user_prompt(task.description, plan_summary, results)

        try:
            response = self._llm_client.complete(
                [LLMMessage(role="user", content=user_prompt)],
                system=CRITIC_SYSTEM_PROMPT,
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001
            raise ReliableAgentError(
                f"Critic LLM call failed: {exc}", context={"task_id": task.task_id}
            ) from exc

        try:
            data = safe_json_loads(response.text)
            return Feedback(
                plan_id=plan.plan_id,
                quality_score=float(data["quality_score"]),
                should_replan=bool(data["should_replan"]),
                issues=[str(i) for i in data.get("issues", [])],
                rationale=str(data.get("rationale", "")),
            )
        except Exception as exc:  # noqa: BLE001
            raise ReliableAgentError(
                f"Critic response could not be parsed: {exc}",
                context={"task_id": task.task_id, "raw_response": response.text[:2000]},
            ) from exc
