"""Tests for `reliableagent.evaluation.runner.EvaluationRunner`."""

from __future__ import annotations

import tempfile
from pathlib import Path

from reliableagent.evaluation.factory import build_standard_factory, run_golden_suite
from reliableagent.evaluation.golden_tasks import ALL_GOLDEN_TASKS
from reliableagent.evaluation.runner import EvalConfig, EvaluationRunner
from reliableagent.memory.backend import FileMemoryBackend


def _get_task(task_id: str):
    return next(t for t in ALL_GOLDEN_TASKS if t.task_id == task_id)


def test_run_one_attaches_the_given_seed_to_the_graded_run():
    golden_task = _get_task("arith_simple_addition")
    factory = build_standard_factory(golden_task)
    runner = EvaluationRunner(orchestrator_factory=factory)
    graded = runner.run_one(golden_task, seed=42, config=EvalConfig())
    assert graded.seed == 42


def test_run_one_produces_a_unique_run_id_per_call():
    golden_task = _get_task("arith_simple_addition")
    run_ids = set()
    for _ in range(3):
        factory = build_standard_factory(golden_task)
        runner = EvaluationRunner(orchestrator_factory=factory)
        graded = runner.run_one(golden_task, seed=0, config=EvalConfig())
        run_ids.add(graded.run_result.run_id)
    assert len(run_ids) == 3


def test_run_suite_produces_one_graded_run_per_task_per_seed():
    tasks = [_get_task("arith_simple_addition"), _get_task("fact_capital_of_france")]
    graded_runs = run_golden_suite(tasks, EvalConfig(seeds=[0, 1]))
    assert len(graded_runs) == 4  # 2 tasks * 2 seeds


def test_trajectory_dir_persists_full_trajectory_to_disk():
    golden_task = _get_task("arith_simple_addition")
    factory = build_standard_factory(golden_task)
    runner = EvaluationRunner(orchestrator_factory=factory)

    with tempfile.TemporaryDirectory() as tmpdir:
        config = EvalConfig(trajectory_dir=tmpdir, seeds=[0])
        graded = runner.run_one(golden_task, seed=0, config=config)

        run_dir = Path(tmpdir) / golden_task.task_id / "seed_0"
        backend = FileMemoryBackend(run_dir)
        loaded_trajectory = backend.load_trajectory(graded.run_result.run_id)
        assert loaded_trajectory.run_id == graded.run_result.run_id
        assert len(loaded_trajectory.plans) >= 1


def test_no_trajectory_dir_means_run_one_still_succeeds_normally():
    golden_task = _get_task("arith_simple_addition")
    factory = build_standard_factory(golden_task)
    runner = EvaluationRunner(orchestrator_factory=factory)
    config = EvalConfig(trajectory_dir=None, seeds=[0])
    graded = runner.run_one(golden_task, seed=0, config=config)
    assert graded.passed is True


def test_harness_level_exception_does_not_crash_the_whole_suite():
    """If the orchestrator_factory itself raises (a harness-level bug, not
    a normal task failure), run_suite must still return a GradedRun for
    every task rather than propagating the exception and losing all
    results for the remaining tasks."""

    def broken_factory(seed):
        raise RuntimeError("simulated harness bug")

    tasks = [_get_task("arith_simple_addition"), _get_task("fact_capital_of_france")]
    runner = EvaluationRunner(orchestrator_factory=broken_factory)
    graded_runs = runner.run_suite(tasks, EvalConfig(seeds=[0]))

    assert len(graded_runs) == 2
    assert all(not r.passed for r in graded_runs)
    assert all("simulated harness bug" in r.grading_explanation for r in graded_runs)


def test_fail_fast_propagates_harness_exceptions():
    def broken_factory(seed):
        raise RuntimeError("simulated harness bug")

    tasks = [_get_task("arith_simple_addition")]
    runner = EvaluationRunner(orchestrator_factory=broken_factory)
    raised = False
    try:
        runner.run_suite(tasks, EvalConfig(seeds=[0], fail_fast=True))
    except RuntimeError:
        raised = True
    assert raised is True
