"""EvaluationRunner: executes golden tasks against an Orchestrator factory,
with seed control, structured trajectory storage, and grading.

Per the roadmap's reproducibility requirements: "Seeds logged and
controllable," "Unique run_id for every execution with complete artifacts
saved." This module is where those requirements become concrete:

    - Every `(golden_task, seed)` pair run through `EvaluationRunner.run_one`
      produces one `GradedRun`, whose `run_result.run_id` is unique and
      whose full `Trajectory` (plans, step records, guardrail decisions,
      checkpoints) is preserved exactly as the Orchestrator produced it —
      not summarized or discarded after grading.
    - The `seed` is threaded through to the `Orchestrator` factory (so a
      caller's factory can seed Python's `random`, a tool's own RNG, or
      pass `seed=` to an `LLMClient.complete()` call) AND attached
      directly to the `GradedRun` record, so "what seed produced this
      result" is always answerable from the record alone, not from
      re-deriving it from run order.
    - If an `EvalConfig.trajectory_dir` is set, every run's full
      `Trajectory` is persisted as JSON immediately after grading — this
      is the "structured trajectory storage" P1 deliverable, reusing
      `FileMemoryBackend`'s exact same JSON serialization path rather
      than inventing a second one.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from reliableagent.core.orchestrator import Orchestrator
from reliableagent.evaluation.golden_task import GoldenTask
from reliableagent.evaluation.metrics import GradedRun, MetricsReport, compute_metrics
from reliableagent.memory.backend import FileMemoryBackend

if TYPE_CHECKING:
    from reliableagent.core.models import RunResult

# A factory builds a fresh, fully-wired Orchestrator for one run. Takes the
# seed so it can be threaded into whatever the factory's LLM client / tools
# need (e.g. seeding `random`, or passing `seed=` through to a real LLM
# call for providers that support deterministic sampling).
OrchestratorFactory = Callable[[int | None], Orchestrator]


@dataclass
class EvalConfig:
    """Configuration for one evaluation run of the golden task suite.

    Attributes:
        seeds: The list of seeds to run each golden task under. Running
            every task under multiple seeds (rather than just one) is
            what lets `task_pass_rate` in `metrics.py` distinguish "this
            task reliably passes" from "this task happened to pass
            once."
        trajectory_dir: If set, every run's full `Trajectory` is
            persisted here as JSON
            (`<trajectory_dir>/<golden_task_id>/seed_<seed>/...`),
            reusing `FileMemoryBackend`. If `None`, trajectories exist
            only in memory for the duration of the evaluation process.
        fail_fast: If True, stop the whole evaluation run on the first
            exception raised *by the harness itself* (as opposed to a
            graceful task failure, which is always just recorded as a
            failing `GradedRun` and never stops the suite).
    """

    seeds: list[int] = field(default_factory=lambda: [0])
    trajectory_dir: str | Path | None = None
    fail_fast: bool = False


class EvaluationRunner:
    """Runs a list of `GoldenTask`s, under a list of seeds, against an
    `OrchestratorFactory`, producing a list of `GradedRun`s and a
    `MetricsReport`.

    Example:
        >>> runner = EvaluationRunner(orchestrator_factory=build_orchestrator)
        >>> graded_runs = runner.run_suite(golden_tasks, EvalConfig(seeds=[0, 1, 2]))
        >>> report = runner.report(graded_runs)
        >>> print("\\n".join(report.summary_lines()))  # doctest: +SKIP
    """

    def __init__(self, orchestrator_factory: OrchestratorFactory) -> None:
        self._orchestrator_factory = orchestrator_factory

    def run_one(self, golden_task: GoldenTask, seed: int | None, config: EvalConfig) -> GradedRun:
        """Run a single golden task once, under one seed, and grade the result."""
        # Seed Python's global random module so any tool/component that
        # draws from it (without its own explicit RNG plumbing) still
        # behaves reproducibly for this run. Real per-component seeding
        # (e.g. a real LLM's `seed=` parameter) is the factory's
        # responsibility, since only the factory knows what was built.
        if seed is not None:
            random.seed(seed)

        orchestrator = self._orchestrator_factory(seed)
        try:
            task = golden_task.make_task()
            run_result = orchestrator.run(task)
        finally:
            orchestrator.shutdown()

        passed, explanation = golden_task.grade(run_result)

        if config.trajectory_dir is not None:
            self._persist_trajectory(golden_task, seed, run_result, config.trajectory_dir)

        return GradedRun(
            golden_task_id=golden_task.task_id,
            category=golden_task.category,
            seed=seed,
            run_result=run_result,
            passed=passed,
            grading_explanation=explanation,
        )

    def run_suite(self, golden_tasks: list[GoldenTask], config: EvalConfig) -> list[GradedRun]:
        """Run every golden task under every configured seed.

        Total runs = `len(golden_tasks) * len(config.seeds)`. Each
        `(golden_task, seed)` pair is independent and order does not
        affect outcomes (each run gets a fresh `Orchestrator` instance
        from the factory), so this could trivially be parallelized in
        a future revision without changing the result.
        """
        graded_runs: list[GradedRun] = []
        for golden_task in golden_tasks:
            for seed in config.seeds:
                if config.fail_fast:
                    graded_runs.append(self.run_one(golden_task, seed, config))
                else:
                    try:
                        graded_runs.append(self.run_one(golden_task, seed, config))
                    except Exception as exc:  # noqa: BLE001 - harness-level safety net
                        graded_runs.append(self._build_harness_error_run(golden_task, seed, exc))
        return graded_runs

    @staticmethod
    def report(graded_runs: list[GradedRun]) -> MetricsReport:
        """Compute the full `MetricsReport` for a batch of graded runs."""
        return compute_metrics(graded_runs)

    @staticmethod
    def _persist_trajectory(
        golden_task: GoldenTask,
        seed: int | None,
        run_result: "RunResult",
        trajectory_dir: str | Path,
    ) -> None:
        run_dir = Path(trajectory_dir) / golden_task.task_id / f"seed_{seed}"
        backend = FileMemoryBackend(run_dir)
        backend.save_trajectory(run_result.trajectory)

    def _build_harness_error_run(
        self, golden_task: GoldenTask, seed: int | None, exc: Exception
    ) -> GradedRun:
        """Build a synthetic failing `GradedRun` for a harness-level (not task-level) error.

        Distinguishes "the Orchestrator legitimately failed the task and
        produced a normal FAILED RunResult" (handled entirely inside
        `run_one`/grading, never reaches here) from "something in the
        evaluation harness itself blew up before a RunResult even
        existed" (e.g. the factory function raised). The latter still
        must not crash the whole suite, but it also must not be silently
        swallowed — it shows up as a failing run whose explanation says
        exactly what broke.
        """
        from reliableagent.core.enums import FailureCategory, OrchestratorState
        from reliableagent.core.models import RunMetrics, RunResult, Trajectory

        task = golden_task.make_task()
        trajectory = Trajectory(task=task)
        trajectory.final_state = OrchestratorState.FAILED
        trajectory.failure_category = FailureCategory.UNKNOWN
        run_result = RunResult(
            run_id=trajectory.run_id,
            task=task,
            final_state=OrchestratorState.FAILED,
            final_answer=None,
            failure_category=FailureCategory.UNKNOWN,
            trajectory=trajectory,
            metrics=RunMetrics(
                total_steps=0,
                total_tool_calls=0,
                total_replans=0,
                total_guardrail_blocks=0,
                succeeded=False,
                duration_seconds=0.0,
            ),
        )
        return GradedRun(
            golden_task_id=golden_task.task_id,
            category=golden_task.category,
            seed=seed,
            run_result=run_result,
            passed=False,
            grading_explanation=f"Evaluation harness error before a result was produced: {exc}",
        )
