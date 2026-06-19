"""Tests for `reliableagent.evaluation.comparison`."""

from __future__ import annotations

from reliableagent.evaluation.comparison import (
    compare_configurations,
    critic_strategy_variants,
    executor_retry_variants,
    guardrail_strictness_variants,
    lenient_guardrails,
    strict_guardrails,
)
from reliableagent.evaluation.golden_tasks import ALL_GOLDEN_TASKS
from reliableagent.evaluation.runner import EvalConfig


def test_guardrail_strictness_comparison_shows_measurable_difference():
    """The core claim this tool exists to support: lenient guardrails must
    score measurably worse than the standard/strict configuration on THIS
    suite, because two golden tasks are specifically designed to require
    guardrails the lenient variant omits."""
    result = compare_configurations(
        ALL_GOLDEN_TASKS, guardrail_strictness_variants(), EvalConfig(seeds=[0])
    )
    lenient = result.variant_reports["guardrails_lenient"]
    standard = result.variant_reports["guardrails_standard"]
    strict = result.variant_reports["guardrails_strict"]

    assert lenient.task_success_rate < standard.task_success_rate
    assert standard.task_success_rate == 1.0
    assert strict.task_success_rate == 1.0


def test_best_by_success_rate_identifies_the_correct_variant():
    result = compare_configurations(
        ALL_GOLDEN_TASKS, guardrail_strictness_variants(), EvalConfig(seeds=[0])
    )
    best = result.best_by_success_rate()
    assert best in {"guardrails_standard", "guardrails_strict"}


def test_critic_strategy_comparison_runs_all_variants():
    result = compare_configurations(
        ALL_GOLDEN_TASKS, critic_strategy_variants(), EvalConfig(seeds=[0])
    )
    assert set(result.variant_reports) == {
        "critic_threshold_lenient",
        "critic_threshold_standard",
        "critic_threshold_strict",
    }
    # All three should still pass the full suite -- the threshold only
    # changes *when* a replan triggers, not whether the suite's own
    # scripted recovery plans are correct.
    for report in result.variant_reports.values():
        assert report.task_success_rate == 1.0


def test_executor_retry_comparison_runs_all_variants():
    result = compare_configurations(
        ALL_GOLDEN_TASKS, executor_retry_variants(), EvalConfig(seeds=[0])
    )
    assert set(result.variant_reports) == {
        "executor_no_retries",
        "executor_standard_retries",
        "executor_high_retries",
    }
    for report in result.variant_reports.values():
        assert report.task_success_rate == 1.0


def test_lenient_and_strict_guardrail_builders_produce_different_guardrail_counts():
    assert len(lenient_guardrails()) < len(strict_guardrails())


def test_comparison_result_summary_lines_render_all_variants():
    result = compare_configurations(
        ALL_GOLDEN_TASKS, guardrail_strictness_variants(), EvalConfig(seeds=[0])
    )
    text = "\n".join(result.summary_lines())
    assert "guardrails_lenient" in text
    assert "guardrails_standard" in text
    assert "guardrails_strict" in text


def test_comparison_descriptions_are_preserved():
    result = compare_configurations(
        ALL_GOLDEN_TASKS, guardrail_strictness_variants(), EvalConfig(seeds=[0])
    )
    assert result.variant_descriptions["guardrails_lenient"] != ""
