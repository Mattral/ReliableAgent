"""LLMPlanner: produces structured `Plan`s by prompting an `LLMClient`.

This is the default, production-shape Planner strategy: it sends a
schema-constrained prompt (see `reliableagent.planner.prompts`) to any
`LLMClient` (mock or real), parses the JSON response into `PlanStep`s,
and validates the result. Parsing/validation failures raise
`PlanParsingError`/`PlanGenerationError` rather than silently
returning a malformed plan, so the Orchestrator always either gets a
valid `Plan` or an explicit, recoverable error it can act on.
"""

from __future__ import annotations

from typing import Any

from reliableagent.core.models import Plan, PlanStep, Task, ToolResult
from reliableagent.core.enums import StepType
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.exceptions import PlanGenerationError, PlanParsingError
from reliableagent.llm.base import LLMClient, LLMMessage
from reliableagent.planner.base import Planner
from reliableagent.planner.prompts import (
    PLANNER_SYSTEM_PROMPT,
    build_planner_user_prompt,
    safe_json_loads,
)


class LLMPlanner(Planner):
    """Planner strategy backed by a single LLM completion call per plan.

    This implements a "Plan-and-Execute" style strategy: it produces
    the *entire* multi-step plan up front in one call, rather than
    interleaving one-step-at-a-time reasoning and execution like a
    ReAct-style planner would. Additional strategies (e.g.
    `ReActPlanner`) can be added later as separate `Planner`
    subclasses without changing this one or the Orchestrator.
    """

    def __init__(self, llm_client: LLMClient, *, max_tokens: int = 2048) -> None:
        self._llm_client = llm_client
        self._max_tokens = max_tokens

    def plan(
        self,
        task: Task,
        tools: ToolRegistry,
        *,
        prior_results: list[ToolResult] | None = None,
        replan_attempt: int = 0,
        feedback_reason: str | None = None,
    ) -> Plan:
        user_prompt = build_planner_user_prompt(
            task.description,
            tools,
            prior_results=prior_results,
            replan_attempt=replan_attempt,
            feedback_reason=feedback_reason,
        )

        try:
            response = self._llm_client.complete(
                [LLMMessage(role="user", content=user_prompt)],
                system=PLANNER_SYSTEM_PROMPT,
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001 - normalize any LLM-layer error
            raise PlanGenerationError(
                f"Planner LLM call failed: {exc}", context={"task_id": task.task_id}
            ) from exc

        return self._parse_plan_response(response.text, task, replan_attempt)

    def _parse_plan_response(self, text: str, task: Task, replan_attempt: int) -> Plan:
        try:
            data = safe_json_loads(text)
        except Exception as exc:  # noqa: BLE001 - json.JSONDecodeError and friends
            raise PlanParsingError(
                f"Planner response was not valid JSON: {exc}",
                context={"task_id": task.task_id, "raw_response": text[:2000]},
            ) from exc

        try:
            steps = [self._parse_step(s) for s in data["steps"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanParsingError(
                f"Planner response had an invalid 'steps' structure: {exc}",
                context={"task_id": task.task_id, "raw_response": text[:2000]},
            ) from exc

        if not steps:
            raise PlanGenerationError(
                "Planner produced a plan with zero steps.", context={"task_id": task.task_id}
            )

        try:
            return Plan(
                task_id=task.task_id,
                steps=steps,
                reasoning_trace=str(data.get("reasoning_trace", "")),
                confidence=float(data.get("confidence", 1.0)),
                replan_attempt=replan_attempt,
            )
        except Exception as exc:  # noqa: BLE001 - Plan model validation error
            raise PlanGenerationError(
                f"Planner produced an invalid plan: {exc}", context={"task_id": task.task_id}
            ) from exc

    @staticmethod
    def _parse_step(raw_step: dict[str, Any]) -> PlanStep:
        return PlanStep(
            step_type=StepType(raw_step["step_type"]),
            description=str(raw_step.get("description", "")),
            tool_name=raw_step.get("tool_name"),
            tool_arguments=dict(raw_step.get("tool_arguments") or {}),
            rationale=raw_step.get("rationale"),
        )
