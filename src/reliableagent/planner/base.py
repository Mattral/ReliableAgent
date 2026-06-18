"""Planner: turns a `Task` (and prior trajectory, on replan) into a `Plan`.

Per the roadmap: "Multiple Planner strategies (ReAct, Plan-and-Execute,
etc.) ... Planner should expose reasoning trace for observability."

This module defines the `Planner` abstract base. A concrete
implementation lives in `reliableagent.planner.llm_planner`
(`LLMPlanner`), which prompts an `LLMClient` to produce a structured
plan and parses the result into the `Plan`/`PlanStep` models from
`reliableagent.core.models`. Keeping `Planner` abstract (rather than
hard-coding LLM-backed planning everywhere) is what would let someone
add a rule-based or hybrid Planner later without touching the
Orchestrator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from reliableagent.core.models import Plan, Task, ToolResult
from reliableagent.executor.tool_registry import ToolRegistry


class Planner(ABC):
    """Base class for all Planner strategies."""

    @abstractmethod
    def plan(
        self,
        task: Task,
        tools: ToolRegistry,
        *,
        prior_results: list[ToolResult] | None = None,
        replan_attempt: int = 0,
        feedback_reason: str | None = None,
    ) -> Plan:
        """Produce a `Plan` for `task`.

        Args:
            task: The task being worked on.
            tools: The registry of tools available for this run, used
                to describe available actions to the planning strategy.
            prior_results: Results of steps already executed, when
                this call is a replan rather than the initial plan.
            replan_attempt: 0 for the initial plan; incremented by the
                Orchestrator on each subsequent replan.
            feedback_reason: A short explanation of why a replan was
                triggered (from the Critic), to ground the new plan in
                what went wrong.

        Returns:
            A `Plan` with `replan_attempt` set to match the input.

        Raises:
            reliableagent.exceptions.PlanGenerationError: if no usable
                plan could be produced.
        """
        raise NotImplementedError
