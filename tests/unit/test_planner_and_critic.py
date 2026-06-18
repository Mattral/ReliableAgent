"""Unit tests for `reliableagent.planner` (Planner strategies and Critics)."""

from __future__ import annotations

import pytest

from reliableagent.core.enums import StepType
from reliableagent.core.models import Task, ToolResult
from reliableagent.exceptions import PlanGenerationError, PlanParsingError
from reliableagent.planner.critic import LLMCritic, ThresholdCritic
from reliableagent.planner.llm_planner import LLMPlanner

from tests.helpers import (
    critic_json,
    final_answer_step,
    make_mock_llm,
    make_task,
    make_tool_registry,
    plan_json,
    tool_call_step,
)


def test_llm_planner_parses_valid_plan():
    registry = make_tool_registry()
    response = plan_json(
        [tool_call_step("add numbers", "add", {"a": 1, "b": 2}), final_answer_step("done: 3")]
    )
    planner = LLMPlanner(make_mock_llm(response))
    task = make_task()
    plan = planner.plan(task, registry)

    assert len(plan.steps) == 2
    assert plan.steps[0].step_type == StepType.TOOL_CALL
    assert plan.steps[0].tool_name == "add"
    assert plan.steps[1].step_type == StepType.FINAL_ANSWER
    assert plan.task_id == task.task_id


def test_llm_planner_raises_on_invalid_json():
    registry = make_tool_registry()
    planner = LLMPlanner(make_mock_llm("this is not json"))
    with pytest.raises(PlanParsingError):
        planner.plan(make_task(), registry)


def test_llm_planner_raises_on_missing_steps_key():
    registry = make_tool_registry()
    planner = LLMPlanner(make_mock_llm('{"reasoning_trace": "x", "confidence": 0.5}'))
    with pytest.raises(PlanParsingError):
        planner.plan(make_task(), registry)


def test_llm_planner_raises_on_empty_steps_list():
    registry = make_tool_registry()
    response = plan_json([])
    planner = LLMPlanner(make_mock_llm(response))
    with pytest.raises(PlanGenerationError):
        planner.plan(make_task(), registry)


def test_llm_planner_strips_markdown_fences():
    registry = make_tool_registry()
    raw = plan_json([final_answer_step("done")])
    fenced = f"```json\n{raw}\n```"
    planner = LLMPlanner(make_mock_llm(fenced))
    plan = planner.plan(make_task(), registry)
    assert len(plan.steps) == 1


def test_llm_planner_sets_replan_attempt():
    registry = make_tool_registry()
    response = plan_json([final_answer_step("done")])
    planner = LLMPlanner(make_mock_llm(response))
    plan = planner.plan(make_task(), registry, replan_attempt=2)
    assert plan.replan_attempt == 2


def test_threshold_critic_does_not_replan_with_no_results():
    critic = ThresholdCritic()
    task = make_task()
    from reliableagent.core.models import Plan, PlanStep

    plan = Plan(task_id=task.task_id, steps=[PlanStep(step_type=StepType.REASONING, description="x")])
    feedback = critic.critique(task, plan, [])
    assert feedback.should_replan is False
    assert feedback.quality_score == 1.0


def test_threshold_critic_replans_above_threshold():
    critic = ThresholdCritic(failure_threshold=0.4)
    task = make_task()
    from reliableagent.core.models import Plan, PlanStep

    plan = Plan(task_id=task.task_id, steps=[PlanStep(step_type=StepType.REASONING, description="x")])
    results = [
        ToolResult(call_id="c1", success=False, error="failed"),
        ToolResult(call_id="c2", success=False, error="failed again"),
        ToolResult(call_id="c3", success=True, output="ok"),
    ]
    feedback = critic.critique(task, plan, results)
    assert feedback.should_replan is True
    assert len(feedback.issues) == 2


def test_threshold_critic_does_not_replan_below_threshold():
    critic = ThresholdCritic(failure_threshold=0.5)
    task = make_task()
    from reliableagent.core.models import Plan, PlanStep

    plan = Plan(task_id=task.task_id, steps=[PlanStep(step_type=StepType.REASONING, description="x")])
    results = [
        ToolResult(call_id="c1", success=True, output="ok"),
        ToolResult(call_id="c2", success=False, error="one failure"),
    ]
    feedback = critic.critique(task, plan, results)
    assert feedback.should_replan is False


def test_llm_critic_parses_feedback_correctly():
    from reliableagent.core.models import Plan, PlanStep

    task = make_task()
    plan = Plan(task_id=task.task_id, steps=[PlanStep(step_type=StepType.REASONING, description="x")])
    response = critic_json(quality_score=0.4, should_replan=True, issues=["bad tool result"], rationale="needs fix")
    critic = LLMCritic(make_mock_llm(response))
    feedback = critic.critique(task, plan, [])
    assert feedback.quality_score == 0.4
    assert feedback.should_replan is True
    assert feedback.issues == ["bad tool result"]
