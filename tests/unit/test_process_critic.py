"""Unit tests for `reliableagent.planner.process_critic`."""

from __future__ import annotations

import json

import pytest

from reliableagent.core.enums import StepType
from reliableagent.core.models import Plan, PlanStep, Task, ToolResult
from reliableagent.exceptions import ReliableAgentError
from reliableagent.llm.mock import MockLLMClient
from reliableagent.planner.process_critic import DeterministicProcessCritic, LLMProcessCritic


def _make_plan(task: Task) -> Plan:
    step = PlanStep(step_type=StepType.TOOL_CALL, description="do thing", tool_name="t")
    return Plan(task_id=task.task_id, steps=[step])


def test_critique_step_returns_none_for_successful_result():
    critic = DeterministicProcessCritic()
    step = PlanStep(step_type=StepType.TOOL_CALL, description="x", tool_name="t")
    result = ToolResult(call_id="c1", success=True, output="ok")
    assert critic.critique_step(step, result) is None


def test_critique_step_returns_none_for_non_tool_steps():
    critic = DeterministicProcessCritic()
    step = PlanStep(step_type=StepType.REASONING, description="thinking")
    assert critic.critique_step(step, None) is None


def test_critique_step_flags_failed_tool_call():
    critic = DeterministicProcessCritic()
    step = PlanStep(step_type=StepType.TOOL_CALL, description="x", tool_name="t")
    result = ToolResult(call_id="c1", success=False, error="boom")
    critique = critic.critique_step(step, result)
    assert critique is not None
    assert critique.verdict is False
    assert "boom" in critique.concern


def test_critique_with_no_results_returns_perfect_scores():
    critic = DeterministicProcessCritic()
    task = Task(description="t")
    plan = _make_plan(task)
    feedback = critic.critique(task, plan, [])
    assert feedback.criterion_scores.correctness == 1.0
    assert feedback.should_replan is False


def test_critique_correctness_reflects_failure_rate():
    critic = DeterministicProcessCritic()
    task = Task(description="t")
    plan = _make_plan(task)
    results = [
        ToolResult(call_id="c1", success=True, output="ok"),
        ToolResult(call_id="c2", success=False, error="boom"),
    ]
    feedback = critic.critique(task, plan, results)
    assert feedback.criterion_scores.correctness == pytest.approx(0.5)


def test_critique_efficiency_reflects_expected_steps_baseline():
    critic = DeterministicProcessCritic(expected_steps=2)
    task = Task(description="t")
    plan = _make_plan(task)
    results = [ToolResult(call_id=f"c{i}", success=True, output="ok") for i in range(4)]
    feedback = critic.critique(task, plan, results)
    assert feedback.criterion_scores.efficiency == pytest.approx(0.5)


def test_critique_flags_safety_relevant_failures():
    critic = DeterministicProcessCritic()
    task = Task(description="t")
    plan = _make_plan(task)
    results = [ToolResult(call_id="c1", success=False, error="permission denied: unauthorized")]
    feedback = critic.critique(task, plan, results)
    assert feedback.criterion_scores.safety < 1.0
    assert feedback.should_replan is True


def test_critique_does_not_flag_ordinary_failures_as_safety_issues():
    critic = DeterministicProcessCritic()
    task = Task(description="t")
    plan = _make_plan(task)
    results = [ToolResult(call_id="c1", success=False, error="connection timed out")]
    feedback = critic.critique(task, plan, results)
    assert feedback.criterion_scores.safety == 1.0


def test_critique_should_replan_when_correctness_low():
    critic = DeterministicProcessCritic()
    task = Task(description="t")
    plan = _make_plan(task)
    results = [
        ToolResult(call_id="c1", success=False, error="x"),
        ToolResult(call_id="c2", success=False, error="y"),
        ToolResult(call_id="c3", success=True, output="z"),
    ]
    feedback = critic.critique(task, plan, results)
    assert feedback.should_replan is True


def test_llm_process_critic_critique_step_parses_response():
    step = PlanStep(step_type=StepType.TOOL_CALL, description="x", tool_name="t")
    result = ToolResult(call_id="c1", success=False, error="boom")
    response = json.dumps({"verdict": False, "concern": "risky operation"})
    critic = LLMProcessCritic(MockLLMClient(responses=[response]))
    critique = critic.critique_step(step, result)
    assert critique.verdict is False
    assert critique.concern == "risky operation"


def test_llm_process_critic_critique_step_returns_none_for_none_result():
    critic = LLMProcessCritic(MockLLMClient())
    step = PlanStep(step_type=StepType.REASONING, description="x")
    assert critic.critique_step(step, None) is None


def test_llm_process_critic_critique_step_degrades_gracefully_on_bad_response():
    step = PlanStep(step_type=StepType.TOOL_CALL, description="x", tool_name="t")
    result = ToolResult(call_id="c1", success=True, output="ok")
    critic = LLMProcessCritic(MockLLMClient(responses=["not valid json"]))
    critique = critic.critique_step(step, result)
    assert critique is not None
    assert critique.verdict is True
    assert "unavailable" in critique.concern


def test_llm_process_critic_critique_parses_multi_criteria_response():
    task = Task(description="t")
    plan = _make_plan(task)
    response = json.dumps(
        {
            "correctness": 0.9,
            "efficiency": 0.7,
            "safety": 1.0,
            "should_replan": False,
            "issues": [],
            "rationale": "looks fine",
        }
    )
    critic = LLMProcessCritic(MockLLMClient(responses=[response]))
    feedback = critic.critique(task, plan, [])
    assert feedback.criterion_scores.correctness == 0.9
    assert feedback.criterion_scores.efficiency == 0.7
    assert feedback.should_replan is False


def test_llm_process_critic_critique_raises_on_malformed_response():
    task = Task(description="t")
    plan = _make_plan(task)
    critic = LLMProcessCritic(MockLLMClient(responses=["not json"]))
    with pytest.raises(ReliableAgentError):
        critic.critique(task, plan, [])
