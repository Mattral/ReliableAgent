"""Unit tests for `reliableagent.planner.replanner`."""

from __future__ import annotations

import json

from reliableagent.core.models import Feedback, Task, ToolResult
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.llm.mock import MockLLMClient
from reliableagent.planner.llm_planner import LLMPlanner
from reliableagent.planner.replanner import (
    BudgetAwareDecomposeStrategy,
    DecomposeFurtherStrategy,
    FailureType,
    ReplanContext,
    Replanner,
    RetryDifferentApproachStrategy,
    classify_failure,
)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(lambda a, b: a + b, name="add", description="adds")
    return registry


def _plan_json() -> str:
    return json.dumps(
        {
            "reasoning_trace": "retry",
            "confidence": 0.8,
            "steps": [
                {
                    "step_type": "tool_call",
                    "description": "add",
                    "tool_name": "add",
                    "tool_arguments": {"a": 1, "b": 1},
                }
            ],
        }
    )


def test_classify_failure_detects_repeated_tool_failure():
    feedback = Feedback(plan_id="p1", quality_score=0.2, should_replan=True, rationale="failed")
    results = [ToolResult(call_id="c1", success=False, error="boom")]
    failure_type = classify_failure(feedback, results, replans_remaining=3, max_replans=3)
    assert failure_type == FailureType.REPEATED_TOOL_FAILURE


def test_classify_failure_budget_exhaustion_takes_priority_over_tool_failure():
    feedback = Feedback(plan_id="p1", quality_score=0.2, should_replan=True, rationale="failed")
    results = [ToolResult(call_id="c1", success=False, error="boom")]
    failure_type = classify_failure(feedback, results, replans_remaining=1, max_replans=3)
    assert failure_type == FailureType.BUDGET_NEARLY_EXHAUSTED


def test_classify_failure_detects_ambiguous_when_issues_but_no_tool_failure():
    feedback = Feedback(
        plan_id="p1",
        quality_score=0.4,
        should_replan=True,
        issues=["unclear goal"],
        rationale="stalled",
    )
    failure_type = classify_failure(feedback, [], replans_remaining=3, max_replans=3)
    assert failure_type == FailureType.AMBIGUOUS_OR_UNDERSPECIFIED


def test_classify_failure_defaults_to_low_quality_progress():
    feedback = Feedback(plan_id="p1", quality_score=0.4, should_replan=True, rationale="meh")
    failure_type = classify_failure(feedback, [], replans_remaining=3, max_replans=3)
    assert failure_type == FailureType.LOW_QUALITY_PROGRESS


def test_classify_failure_with_zero_max_replans_never_triggers_budget_exhaustion():
    feedback = Feedback(plan_id="p1", quality_score=0.4, should_replan=True, rationale="meh")
    failure_type = classify_failure(feedback, [], replans_remaining=0, max_replans=0)
    assert failure_type != FailureType.BUDGET_NEARLY_EXHAUSTED


def _context(**overrides):
    defaults = dict(
        task=Task(description="t"),
        tools=_registry(),
        prior_results=[],
        feedback=Feedback(plan_id="p1", quality_score=0.5, should_replan=True, rationale="x"),
        failure_type=FailureType.LOW_QUALITY_PROGRESS,
        replan_attempt=1,
        replans_remaining=2,
        max_replans=3,
    )
    defaults.update(overrides)
    return ReplanContext(**defaults)


def test_retry_different_approach_hint_mentions_avoiding_repetition():
    strategy = RetryDifferentApproachStrategy()
    results = [ToolResult(call_id="c1", success=False, error="connection refused")]
    context = _context(prior_results=results)
    hint = strategy.build_hint(context)
    assert "different" in hint.lower()
    assert "connection refused" in hint


def test_decompose_further_hint_mentions_smaller_steps():
    strategy = DecomposeFurtherStrategy()
    context = _context()
    hint = strategy.build_hint(context)
    assert "smaller" in hint.lower()


def test_budget_aware_strategy_hint_mentions_remaining_attempts():
    strategy = BudgetAwareDecomposeStrategy()
    context = _context(replans_remaining=1, max_replans=3)
    hint = strategy.build_hint(context)
    assert "1 replan attempt" in hint


def test_budget_aware_strategy_shrinks_max_steps():
    strategy = BudgetAwareDecomposeStrategy(max_steps_floor=3)
    task = Task(description="t", max_steps=20)
    context = _context(task=task)
    adjusted = strategy.adjust_task_for_retry(context, task)
    assert adjusted.max_steps == 3


def test_budget_aware_strategy_does_not_increase_max_steps():
    strategy = BudgetAwareDecomposeStrategy(max_steps_floor=10)
    task = Task(description="t", max_steps=5)
    context = _context(task=task)
    adjusted = strategy.adjust_task_for_retry(context, task)
    assert adjusted.max_steps == 5


def test_default_strategy_does_not_modify_task():
    strategy = DecomposeFurtherStrategy()
    task = Task(description="t", max_steps=20)
    context = _context(task=task)
    adjusted = strategy.adjust_task_for_retry(context, task)
    assert adjusted.max_steps == 20
    assert adjusted == task


def test_replanner_produces_a_valid_plan():
    registry = _registry()
    planner = LLMPlanner(MockLLMClient(responses=[_plan_json()]))
    replanner = Replanner(planner)
    feedback = Feedback(plan_id="p1", quality_score=0.3, should_replan=True, rationale="failed")
    results = [ToolResult(call_id="c1", success=False, error="boom")]
    task = Task(description="t", max_replans=3)

    plan = replanner.replan(
        task, registry, prior_results=results, feedback=feedback, replan_attempt=1, max_replans=3
    )
    assert len(plan.steps) >= 1


def test_replanner_passes_budget_aware_hint_to_planner_when_budget_low():
    registry = _registry()
    mock = MockLLMClient(responses=[_plan_json()])
    planner = LLMPlanner(mock)
    replanner = Replanner(planner)
    feedback = Feedback(plan_id="p1", quality_score=0.3, should_replan=True, rationale="failed")
    results = [ToolResult(call_id="c1", success=False, error="boom")]
    task = Task(description="t", max_replans=3, max_steps=20)

    replanner.replan(
        task, registry, prior_results=results, feedback=feedback, replan_attempt=3, max_replans=3
    )

    sent_prompt = mock.call_log[0][0].content
    assert "replan attempt" in sent_prompt.lower()


def test_replanner_custom_strategies_override_defaults():
    registry = _registry()
    planner = LLMPlanner(MockLLMClient(responses=[_plan_json()]))

    calls = {"count": 0}

    class _SpyStrategy(RetryDifferentApproachStrategy):
        def build_hint(self, context):
            calls["count"] += 1
            return "custom spy hint"

    replanner = Replanner(planner, strategies={FailureType.REPEATED_TOOL_FAILURE: _SpyStrategy()})
    feedback = Feedback(plan_id="p1", quality_score=0.3, should_replan=True, rationale="failed")
    results = [ToolResult(call_id="c1", success=False, error="boom")]
    task = Task(description="t", max_replans=3)

    replanner.replan(
        task, registry, prior_results=results, feedback=feedback, replan_attempt=1, max_replans=3
    )
    assert calls["count"] == 1


def test_last_failure_type_does_not_call_the_planner():
    registry = _registry()
    mock = MockLLMClient()
    planner = LLMPlanner(mock)
    replanner = Replanner(planner)
    feedback = Feedback(plan_id="p1", quality_score=0.3, should_replan=True, rationale="failed")
    results = [ToolResult(call_id="c1", success=False, error="boom")]

    failure_type = replanner.last_failure_type(
        feedback, results, replans_remaining=3, max_replans=3
    )
    assert failure_type == FailureType.REPEATED_TOOL_FAILURE
    assert len(mock.call_log) == 0
