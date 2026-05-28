"""Unit tests for `reliableagent.evaluation.metrics`."""

from __future__ import annotations

from reliableagent.core.enums import FailureCategory, OrchestratorState, StepStatus, StepType
from reliableagent.core.models import (
    PlanStep,
    RunMetrics,
    RunResult,
    StepRecord,
    Task,
    ToolCall,
    ToolResult,
    Trajectory,
)
from reliableagent.evaluation.metrics import (
    GradedRun,
    compute_metrics,
    group_by_golden_task,
    task_pass_rate,
)


def _make_run_result(
    *,
    replans: int = 0,
    tool_failed: bool | None = None,
    guardrail_blocks: int = 0,
    failure_category: FailureCategory | None = None,
    final_state: OrchestratorState = OrchestratorState.COMPLETED,
) -> RunResult:
    task = Task(description="metrics test task")
    trajectory = Trajectory(task=task)
    if tool_failed is not None:
        step = PlanStep(step_type=StepType.TOOL_CALL, description="call tool", tool_name="t")
        call = ToolCall(step_id=step.step_id, tool_name="t", arguments={})
        result = ToolResult(
            call_id=call.call_id,
            success=not tool_failed,
            output=None if tool_failed else "ok",
            error="boom" if tool_failed else None,
        )
        trajectory.add_step_record(
            StepRecord(
                step=step,
                status=StepStatus.FAILED if tool_failed else StepStatus.SUCCEEDED,
                tool_call=call,
                tool_result=result,
            )
        )
    trajectory.failure_category = failure_category
    metrics = RunMetrics(
        total_steps=1,
        total_tool_calls=1 if tool_failed is not None else 0,
        total_replans=replans,
        total_guardrail_blocks=guardrail_blocks,
        succeeded=final_state == OrchestratorState.COMPLETED,
        duration_seconds=0.01,
    )
    return RunResult(
        run_id=trajectory.run_id,
        task=task,
        final_state=final_state,
        final_answer="x",
        failure_category=failure_category,
        trajectory=trajectory,
        metrics=metrics,
    )


def test_task_success_rate_basic_fraction():
    runs = [
        GradedRun("t1", "cat_a", 0, _make_run_result(), True, "ok"),
        GradedRun(
            "t2", "cat_a", 0, _make_run_result(final_state=OrchestratorState.FAILED), False, "fail"
        ),
        GradedRun("t3", "cat_a", 0, _make_run_result(), True, "ok"),
        GradedRun("t4", "cat_a", 0, _make_run_result(), True, "ok"),
    ]
    report = compute_metrics(runs)
    assert report.task_success_rate == 0.75
    assert report.passed_runs == 3
    assert report.failed_runs == 1


def test_recovery_rate_is_none_when_no_failures_occurred():
    runs = [GradedRun("t1", "cat_a", 0, _make_run_result(), True, "ok")]
    report = compute_metrics(runs)
    assert report.recovery_rate is None


def test_recovery_rate_counts_only_runs_with_a_tool_failure():
    runs = [
        GradedRun("t1", "cat_a", 0, _make_run_result(tool_failed=True), True, "recovered"),
        GradedRun("t2", "cat_a", 0, _make_run_result(tool_failed=True), False, "did not recover"),
        GradedRun("t3", "cat_a", 0, _make_run_result(), True, "never failed in the first place"),
    ]
    report = compute_metrics(runs)
    assert report.recovery_rate == 0.5  # 1 of 2 runs-with-a-failure passed


def test_average_replanning_attempts_includes_zero_replan_runs():
    runs = [
        GradedRun("t1", "cat_a", 0, _make_run_result(replans=0), True, "ok"),
        GradedRun("t2", "cat_a", 0, _make_run_result(replans=4), True, "ok"),
    ]
    report = compute_metrics(runs)
    assert report.average_replanning_attempts == 2.0


def test_guardrail_intervention_rate():
    runs = [
        GradedRun("t1", "cat_a", 0, _make_run_result(guardrail_blocks=1), True, "ok"),
        GradedRun("t2", "cat_a", 0, _make_run_result(guardrail_blocks=0), True, "ok"),
        GradedRun("t3", "cat_a", 0, _make_run_result(guardrail_blocks=0), True, "ok"),
        GradedRun("t4", "cat_a", 0, _make_run_result(guardrail_blocks=0), True, "ok"),
    ]
    report = compute_metrics(runs)
    assert report.guardrail_intervention_rate == 0.25


def test_failure_category_distribution_only_counts_failed_runs():
    runs = [
        GradedRun(
            "t1", "cat_a", 0,
            _make_run_result(
                failure_category=FailureCategory.TOOL_ERROR, final_state=OrchestratorState.FAILED
            ),
            False, "fail",
        ),
        GradedRun(
            "t2", "cat_a", 0,
            _make_run_result(
                failure_category=FailureCategory.GUARDRAIL_BLOCKED,
                final_state=OrchestratorState.FAILED,
            ),
            False, "fail",
        ),
        GradedRun("t3", "cat_a", 0, _make_run_result(), True, "ok"),
    ]
    report = compute_metrics(runs)
    assert report.failure_category_distribution == {"tool_error": 0.5, "guardrail_blocked": 0.5}


def test_by_category_breakdown_is_isolated_per_category():
    runs = [
        GradedRun("t1", "arithmetic", 0, _make_run_result(), True, "ok"),
        GradedRun("t2", "arithmetic", 0, _make_run_result(), True, "ok"),
        GradedRun(
            "t3", "guardrail", 0,
            _make_run_result(final_state=OrchestratorState.FAILED),
            False, "fail",
        ),
    ]
    report = compute_metrics(runs)
    assert report.by_category["arithmetic"].task_success_rate == 1.0
    assert report.by_category["guardrail"].task_success_rate == 0.0


def test_empty_batch_returns_zeroed_report_without_crashing():
    report = compute_metrics([])
    assert report.total_runs == 0
    assert report.task_success_rate == 0.0
    assert report.recovery_rate is None


def test_group_by_golden_task():
    runs = [
        GradedRun("t1", "cat_a", 0, _make_run_result(), True, "ok"),
        GradedRun("t1", "cat_a", 1, _make_run_result(), True, "ok"),
        GradedRun("t2", "cat_a", 0, _make_run_result(), True, "ok"),
    ]
    grouped = group_by_golden_task(runs)
    assert len(grouped["t1"]) == 2
    assert len(grouped["t2"]) == 1


def test_task_pass_rate_across_seeds():
    runs = [
        GradedRun("t1", "cat_a", 0, _make_run_result(), True, "ok"),
        GradedRun(
            "t1", "cat_a", 1, _make_run_result(final_state=OrchestratorState.FAILED), False, "fail"
        ),
        GradedRun("t1", "cat_a", 2, _make_run_result(), True, "ok"),
    ]
    assert task_pass_rate(runs, "t1") == (2 / 3)


def test_task_pass_rate_returns_none_for_unknown_task():
    runs = [GradedRun("t1", "cat_a", 0, _make_run_result(), True, "ok")]
    assert task_pass_rate(runs, "nonexistent") is None


def test_summary_lines_includes_all_headline_metrics():
    runs = [GradedRun("t1", "cat_a", 0, _make_run_result(), True, "ok")]
    report = compute_metrics(runs)
    text = "\n".join(report.summary_lines())
    assert "Task Success Rate" in text
    assert "Recovery Rate" in text
    assert "Average Replanning Attempts" in text
    assert "Guardrail Intervention Rate" in text
