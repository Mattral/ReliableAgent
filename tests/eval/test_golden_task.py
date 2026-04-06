"""Tests for `reliableagent.evaluation.golden_task` (graders + GoldenTask model)."""

from __future__ import annotations

from reliableagent.core.enums import OrchestratorState
from reliableagent.core.models import RunMetrics, RunResult, Task, Trajectory
from reliableagent.evaluation.golden_task import (
    GoldenTask,
    contains_all_grader,
    custom_predicate_grader,
    exact_match_grader,
    numeric_tolerance_grader,
)


def _make_result(final_answer: str | None) -> RunResult:
    task = Task(description="grader test")
    trajectory = Trajectory(task=task)
    metrics = RunMetrics(
        total_steps=0,
        total_tool_calls=0,
        total_replans=0,
        total_guardrail_blocks=0,
        succeeded=True,
        duration_seconds=0.0,
    )
    return RunResult(
        run_id=trajectory.run_id,
        task=task,
        final_state=OrchestratorState.COMPLETED,
        final_answer=final_answer,
        failure_category=None,
        trajectory=trajectory,
        metrics=metrics,
    )


def test_exact_match_grader_passes_on_exact_match():
    grader = exact_match_grader("The answer is 42.")
    passed, _ = grader(_make_result("The answer is 42."))
    assert passed is True


def test_exact_match_grader_is_case_and_whitespace_insensitive():
    grader = exact_match_grader("The Answer Is 42.")
    passed, _ = grader(_make_result("  the answer is 42.  "))
    assert passed is True


def test_exact_match_grader_fails_on_mismatch():
    grader = exact_match_grader("expected")
    passed, explanation = grader(_make_result("something else"))
    assert passed is False
    assert "expected" in explanation


def test_contains_all_grader_requires_every_substring():
    grader = contains_all_grader(["paris", "france"])
    passed, _ = grader(_make_result("The capital of France is Paris."))
    assert passed is True


def test_contains_all_grader_fails_if_any_substring_missing():
    grader = contains_all_grader(["paris", "spain"])
    passed, explanation = grader(_make_result("The capital of France is Paris."))
    assert passed is False
    assert "spain" in explanation.lower()


def test_numeric_tolerance_grader_passes_within_tolerance():
    grader = numeric_tolerance_grader(42.0, tolerance=0.01)
    passed, _ = grader(_make_result("The result is 42.0."))
    assert passed is True


def test_numeric_tolerance_grader_fails_outside_tolerance():
    grader = numeric_tolerance_grader(42.0, tolerance=0.01)
    passed, _ = grader(_make_result("The result is 50."))
    assert passed is False


def test_numeric_tolerance_grader_fails_when_no_number_present():
    grader = numeric_tolerance_grader(42.0)
    passed, explanation = grader(_make_result("no numbers here at all"))
    assert passed is False
    assert "no numeric value" in explanation.lower()


def test_numeric_tolerance_grader_handles_negative_numbers():
    grader = numeric_tolerance_grader(-5.0)
    passed, _ = grader(_make_result("The result is -5."))
    assert passed is True


def test_custom_predicate_grader_passes_when_predicate_true():
    grader = custom_predicate_grader(lambda r: r.final_answer == "yes", "answer is yes")
    passed, explanation = grader(_make_result("yes"))
    assert passed is True
    assert "satisfied" in explanation


def test_custom_predicate_grader_fails_when_predicate_false():
    grader = custom_predicate_grader(lambda r: r.final_answer == "yes", "answer is yes")
    passed, explanation = grader(_make_result("no"))
    assert passed is False
    assert "did not satisfy" in explanation


def test_golden_task_make_task_invokes_factory_fresh_each_time():
    call_count = {"n": 0}

    def factory():
        call_count["n"] += 1
        return Task(description=f"call {call_count['n']}")

    golden_task = GoldenTask(
        task_id="t1", category="c", build_task=factory, grade=lambda r: (True, "ok")
    )
    task1 = golden_task.make_task()
    task2 = golden_task.make_task()
    assert task1.task_id != task2.task_id  # Task IDs are freshly generated per build
    assert call_count["n"] == 2


def test_golden_task_expect_failure_defaults_to_false():
    golden_task = GoldenTask(
        task_id="t1",
        category="c",
        build_task=lambda: Task(description="x"),
        grade=lambda r: (True, "ok"),
    )
    assert golden_task.expect_failure is False
