"""Tests for the curated golden task suite (`reliableagent.evaluation.golden_tasks`).

This is the closest thing in this codebase to a snapshot/golden-file test
suite for the Orchestrator itself: every golden task's scripted plan is
run against the REAL `Orchestrator`, `Executor`, `GuardrailRunner`, and
`Critic` (only the LLM call is mocked), and graded against its real
grading function. A regression in orchestration logic that breaks one of
these scenarios will show up here directly, often before it would show up
in the more abstract unit tests in `tests/unit/` and `tests/integration/`.
"""

from __future__ import annotations

from reliableagent.core.enums import OrchestratorState
from reliableagent.evaluation.factory import run_golden_suite
from reliableagent.evaluation.golden_tasks import ALL_GOLDEN_TASKS, get_plan_script
from reliableagent.evaluation.metrics import compute_metrics
from reliableagent.evaluation.runner import EvalConfig


def test_suite_has_between_fifteen_and_twenty_five_tasks():
    assert 15 <= len(ALL_GOLDEN_TASKS) <= 25


def test_every_golden_task_has_a_unique_task_id():
    ids = [t.task_id for t in ALL_GOLDEN_TASKS]
    assert len(ids) == len(set(ids))


def test_every_golden_task_has_a_plan_script():
    for golden_task in ALL_GOLDEN_TASKS:
        script = get_plan_script(golden_task.task_id)
        assert len(script) >= 1


def test_suite_covers_at_least_five_categories():
    categories = {t.category for t in ALL_GOLDEN_TASKS}
    assert len(categories) >= 5


def test_every_category_has_at_least_three_tasks():
    counts: dict[str, int] = {}
    for t in ALL_GOLDEN_TASKS:
        counts[t.category] = counts.get(t.category, 0) + 1
    for category, count in counts.items():
        assert count >= 3, f"category {category!r} only has {count} task(s)"


def test_full_suite_achieves_one_hundred_percent_with_standard_configuration():
    """The headline regression test: every golden task's scripted "correct"
    plan, run against the standard Orchestrator configuration, must pass
    its own grader. A failure here means either a real orchestration bug
    or a golden task/script that's gone stale -- both are worth knowing
    about immediately."""
    graded_runs = run_golden_suite(ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]))
    report = compute_metrics(graded_runs)
    failing = [r for r in graded_runs if not r.passed]
    assert not failing, (
        f"{len(failing)} golden task(s) failed: "
        f"{[(r.golden_task_id, r.grading_explanation) for r in failing]}"
    )
    assert report.task_success_rate == 1.0


def test_expect_failure_tasks_actually_end_in_failed_state():
    """Sanity-checks the `expect_failure=True` tagging itself: every golden
    task tagged this way must produce an Orchestrator run that actually
    ended in FAILED -- otherwise the grader could be passing for the wrong
    reason (e.g. a custom predicate that's accidentally too permissive)."""
    expect_failure_tasks = [t for t in ALL_GOLDEN_TASKS if t.expect_failure]
    assert len(expect_failure_tasks) >= 1

    graded_runs = run_golden_suite(expect_failure_tasks, EvalConfig(seeds=[0]))
    for run in graded_runs:
        assert run.run_result.final_state == OrchestratorState.FAILED, (
            f"{run.golden_task_id} was tagged expect_failure=True but ended in "
            f"{run.run_result.final_state}"
        )


def test_failure_recovery_tasks_that_should_pass_show_at_least_one_replan():
    """The 3 failure_recovery tasks NOT tagged expect_failure should genuinely
    exercise the replanning path, not just happen to pass on the first try."""
    recovery_tasks = [
        t for t in ALL_GOLDEN_TASKS if t.category == "failure_recovery" and not t.expect_failure
    ]
    assert len(recovery_tasks) >= 1

    graded_runs = run_golden_suite(recovery_tasks, EvalConfig(seeds=[0]))
    for run in graded_runs:
        assert run.run_result.metrics.total_replans >= 1, (
            f"{run.golden_task_id} is a failure_recovery task but needed zero replans"
        )


def test_suite_is_deterministic_across_repeated_runs():
    """Running the same golden task twice (fresh Orchestrator + fresh
    MockLLMClient each time, as run_golden_suite always does) must produce
    identical pass/fail outcomes -- the entire premise of using this suite
    as a regression check depends on it not being flaky."""
    run_1 = run_golden_suite(ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]))
    run_2 = run_golden_suite(ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]))

    outcomes_1 = {r.golden_task_id: r.passed for r in run_1}
    outcomes_2 = {r.golden_task_id: r.passed for r in run_2}
    assert outcomes_1 == outcomes_2


def test_multiple_seeds_all_produce_passing_runs_for_deterministic_tasks():
    """Tasks in this suite don't depend on the seed at all (no real
    randomness anywhere in golden_tools.py), so running under several
    seeds should yield the same pass outcome every time."""
    graded_runs = run_golden_suite(ALL_GOLDEN_TASKS, EvalConfig(seeds=[0, 1, 2]))
    assert len(graded_runs) == len(ALL_GOLDEN_TASKS) * 3
    assert all(r.passed for r in graded_runs)
