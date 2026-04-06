"""Shared helper functions for ReliableAgent's test suite.

Plain functions rather than pytest fixtures, by design: this lets
every test file run identically under real pytest and under the
offline fallback runner in `scripts/run_tests.py` (see that module's
docstring for the full rationale).
"""

from __future__ import annotations

import json

from reliableagent.core.models import Task
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.llm.mock import MockLLMClient


def make_task(description: str = "A test task", **kwargs) -> Task:
    """Construct a `Task` with sensible test defaults."""
    return Task(description=description, **kwargs)


def make_tool_registry() -> ToolRegistry:
    """Build a small `ToolRegistry` with a few deterministic tools, for tests."""
    registry = ToolRegistry()

    @registry.register(description="Add two integers")
    def add(a: int, b: int) -> int:
        return a + b

    @registry.register(description="Always raises a RuntimeError")
    def boom() -> None:
        raise RuntimeError("intentional test failure")

    return registry


def plan_json(
    steps: list[dict],
    *,
    reasoning_trace: str = "test reasoning",
    confidence: float = 0.9,
) -> str:
    """Build a JSON string in the exact shape `LLMPlanner` expects to parse."""
    return json.dumps(
        {"reasoning_trace": reasoning_trace, "confidence": confidence, "steps": steps}
    )


def critic_json(
    *, quality_score: float, should_replan: bool, issues: list[str] | None = None, rationale: str = ""
) -> str:
    """Build a JSON string in the exact shape `LLMCritic` expects to parse."""
    return json.dumps(
        {
            "quality_score": quality_score,
            "should_replan": should_replan,
            "issues": issues or [],
            "rationale": rationale,
        }
    )


def tool_call_step(description: str, tool_name: str, arguments: dict | None = None) -> dict:
    """Build a raw plan-step dict for a tool_call step."""
    return {
        "step_type": "tool_call",
        "description": description,
        "tool_name": tool_name,
        "tool_arguments": arguments or {},
    }


def final_answer_step(description: str) -> dict:
    """Build a raw plan-step dict for a final_answer step."""
    return {"step_type": "final_answer", "description": description}


def make_mock_llm(*responses: str) -> MockLLMClient:
    """Construct a `MockLLMClient` scripted with `responses`, in order."""
    return MockLLMClient(responses=list(responses))
