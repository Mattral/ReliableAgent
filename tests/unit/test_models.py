"""Unit tests for `reliableagent.core.models`."""

from __future__ import annotations

import pytest

from reliableagent.core.enums import (
    GuardrailBoundary,
    GuardrailCategory,
    GuardrailVerdict,
    OrchestratorState,
    StepType,
)
from reliableagent.core.models import (
    GuardrailDecision,
    Plan,
    PlanStep,
    Task,
    Trajectory,
)


def test_task_requires_non_blank_description():
    with pytest.raises(ValueError):
        Task(description="   ")


def test_task_has_sane_defaults():
    task = Task(description="do something")
    assert task.max_steps == 20
    assert task.max_replans == 3
    assert task.task_id.startswith("task_")


def test_task_is_frozen():
    task = Task(description="do something")
    # This assignment is exactly what the test verifies is rejected --
    # mypy (via the pydantic plugin) correctly flags it as a static
    # error too, since a frozen model's fields are read-only by design.
    # Both real Pydantic's ValidationError and this project's compat
    # shim's ValidationError inherit from ValueError.
    with pytest.raises(ValueError):
        task.description = "changed"  # type: ignore[misc]


def test_plan_step_requires_tool_name_for_tool_call():
    with pytest.raises(ValueError):
        PlanStep(step_type=StepType.TOOL_CALL, description="missing tool name")


def test_plan_step_allows_missing_tool_name_for_reasoning():
    step = PlanStep(step_type=StepType.REASONING, description="just thinking")
    assert step.tool_name is None


def test_plan_requires_at_least_one_step():
    with pytest.raises(ValueError):
        Plan(task_id="task_x", steps=[])


def test_trajectory_total_replans_tracks_max_plan_replan_attempt():
    task = Task(description="t")
    step = PlanStep(step_type=StepType.REASONING, description="think")
    traj = Trajectory(task=task)
    traj.add_plan(Plan(task_id=task.task_id, steps=[step], replan_attempt=0))
    traj.add_plan(Plan(task_id=task.task_id, steps=[step], replan_attempt=1))
    traj.add_plan(Plan(task_id=task.task_id, steps=[step], replan_attempt=2))
    assert traj.total_replans == 2


def test_trajectory_total_guardrail_blocks_counts_run_level_blocks():
    task = Task(description="t")
    traj = Trajectory(task=task)
    block_decision = GuardrailDecision(
        guardrail_name="g",
        boundary=GuardrailBoundary.FINAL_OUTPUT,
        category=GuardrailCategory.POLICY,
        verdict=GuardrailVerdict.BLOCK,
        reason="nope",
    )
    allow_decision = block_decision.model_copy(update={"verdict": GuardrailVerdict.ALLOW})
    traj.add_guardrail_decision(block_decision)
    traj.add_guardrail_decision(allow_decision)
    assert traj.total_guardrail_blocks == 1


def test_model_dump_json_roundtrips_through_dict():
    task = Task(description="round trip me")
    dumped = task.model_dump(mode="json")
    assert dumped["description"] == "round trip me"
    rebuilt = Task(**dumped)
    assert rebuilt == task


def test_default_orchestrator_state_is_pending():
    task = Task(description="t")
    traj = Trajectory(task=task)
    assert traj.final_state == OrchestratorState.PENDING
