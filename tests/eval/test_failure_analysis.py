"""Tests for `reliableagent.evaluation.failure_analysis`."""

from __future__ import annotations

from reliableagent.evaluation.comparison import lenient_guardrails
from reliableagent.evaluation.factory import run_golden_suite
from reliableagent.evaluation.failure_analysis import analyze_failures
from reliableagent.evaluation.golden_tasks import ALL_GOLDEN_TASKS
from reliableagent.evaluation.runner import EvalConfig


def test_no_failures_when_full_suite_passes():
    runs = run_golden_suite(ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]))
    report = analyze_failures(runs)
    assert report.failures == []
    assert report.most_common_failure_category is None


def test_failures_are_populated_when_guardrails_are_weakened():
    runs = run_golden_suite(
        ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]), guardrails_builder=lenient_guardrails
    )
    report = analyze_failures(runs)
    assert len(report.failures) == 2
    failing_ids = {f.golden_task_id for f in report.failures}
    assert "guardrail_blocks_disallowed_keyword_in_plan" in failing_ids
    assert "guardrail_blocks_oversized_tool_arguments" in failing_ids


def test_failure_detail_captures_grading_explanation():
    runs = run_golden_suite(
        ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]), guardrails_builder=lenient_guardrails
    )
    report = analyze_failures(runs)
    for failure in report.failures:
        assert failure.grading_explanation != ""


def test_failure_detail_captures_first_failed_step_when_present():
    runs = run_golden_suite(
        ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]), guardrails_builder=lenient_guardrails
    )
    report = analyze_failures(runs)
    oversized = next(
        f
        for f in report.failures
        if f.golden_task_id == "guardrail_blocks_oversized_tool_arguments"
    )
    assert oversized.first_failed_step_error is not None


def test_summary_lines_include_failure_count_and_metrics():
    runs = run_golden_suite(
        ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]), guardrails_builder=lenient_guardrails
    )
    report = analyze_failures(runs)
    text = "\n".join(report.summary_lines())
    assert "Failure Analysis Report" in text
    assert "Task Success Rate" in text
    assert "failing run(s)" in text


def test_summary_lines_handle_zero_failures_gracefully():
    runs = run_golden_suite(ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]))
    report = analyze_failures(runs)
    text = "\n".join(report.summary_lines())
    assert "No failures in this batch." in text


def test_most_common_failure_category_is_set_when_failures_exist():
    runs = run_golden_suite(
        ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]), guardrails_builder=lenient_guardrails
    )
    report = analyze_failures(runs)
    assert report.most_common_failure_category is not None
