"""Core reliability metrics: Task Success Rate, Recovery Rate, Average
Replanning Attempts — exactly the three metrics named in the roadmap's
Phase 2 spec, computed from a batch of graded runs.

Kept deliberately separate from the runner (`runner.py`) and the golden
task suite (`golden_tasks.py`) so the metric *definitions* are reviewable
and unit-testable as pure functions over a list of `GradedRun` records,
independent of how those records were produced (a real Orchestrator run,
a replayed trajectory from disk, or a hand-built test fixture).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reliableagent.core.models import RunResult
from reliableagent.evaluation.golden_task import GoldenTask


@dataclass(frozen=True)
class GradedRun:
    """The result of running one `GoldenTask` once and grading the outcome."""

    golden_task_id: str
    category: str
    seed: int | None
    run_result: RunResult
    passed: bool
    grading_explanation: str

    @property
    def had_any_replan(self) -> bool:
        """Whether this run needed at least one replan to reach its outcome."""
        return self.run_result.metrics.total_replans > 0

    @property
    def recovered_from_a_failure(self) -> bool:
        """Whether this run hit at least one failed tool call but still passed.

        This is the operational definition of "recovery" used by
        `recovery_rate` below: the run encountered a real failure
        signal (a tool call that did not succeed) partway through, and
        the system adapted (via replanning) to still reach a passing
        outcome, rather than the task simply never hitting trouble in
        the first place.
        """
        had_failed_tool_call = any(
            record.tool_result is not None and not record.tool_result.success
            for record in self.run_result.trajectory.step_records
        )
        return had_failed_tool_call and self.passed


@dataclass(frozen=True)
class MetricsReport:
    """Aggregate reliability metrics computed over a batch of `GradedRun`s.

    Attributes:
        task_success_rate: Fraction of runs that were graded as passing.
            This is the headline reliability number — the roadmap names
            it first for a reason.
        recovery_rate: Of the runs that encountered at least one failed
            tool call, the fraction that still went on to pass. `None`
            (rather than 0.0) when zero runs encountered any failure at
            all, since "0/0" should never be silently reported as "0%
            recovery" — that would imply the system recovers from
            nothing, when the truth is recovery was never tested.
        average_replanning_attempts: Mean number of replans across ALL
            runs (not just ones that needed any), so a suite where most
            tasks succeed first-try but a few need heavy replanning is
            visibly distinguishable from one where every task needs a
            little replanning.
        guardrail_intervention_rate: Fraction of runs in which at least
            one guardrail decision was *not* a plain ALLOW (i.e. a BLOCK
            or MODIFY fired somewhere in the run) — a measure of how
            often the guardrail layer actually does something, as
            opposed to merely being configured but never engaging.
        failure_category_distribution: Of the runs that ended in
            `OrchestratorState.FAILED`, the fraction attributable to
            each `FailureCategory` (e.g. `{"tool_error": 0.6,
            "guardrail_blocked": 0.4}`). Empty dict when no runs failed.
        total_runs: Total number of graded runs this report summarizes.
        passed_runs / failed_runs: Raw counts backing `task_success_rate`.
        by_category: The same headline metrics, broken out per
            `GoldenTask.category`, so a reviewer can see *where*
            reliability is weakest rather than only a single aggregate
            number.
    """

    task_success_rate: float
    recovery_rate: float | None
    average_replanning_attempts: float
    total_runs: int
    passed_runs: int
    failed_runs: int
    guardrail_intervention_rate: float = 0.0
    failure_category_distribution: dict[str, float] = field(default_factory=dict)
    by_category: dict[str, "MetricsReport"] = field(default_factory=dict)

    def summary_lines(self) -> list[str]:
        """Render a compact, human-readable summary (used by the CLI report)."""
        recovery_str = (
            "n/a (no failures encountered)"
            if self.recovery_rate is None
            else f"{self.recovery_rate:.1%}"
        )
        lines = [
            f"Task Success Rate:           {self.task_success_rate:.1%} "
            f"({self.passed_runs}/{self.total_runs})",
            f"Recovery Rate:                {recovery_str}",
            f"Average Replanning Attempts:  {self.average_replanning_attempts:.2f}",
            f"Guardrail Intervention Rate:  {self.guardrail_intervention_rate:.1%}",
        ]
        if self.failure_category_distribution:
            lines.append("Failure Category Distribution:")
            for category, fraction in sorted(
                self.failure_category_distribution.items(), key=lambda kv: -kv[1]
            ):
                lines.append(f"  - {category}: {fraction:.1%}")
        if self.by_category:
            lines.append("By category:")
            for category, report in sorted(self.by_category.items()):
                lines.append(
                    f"  - {category}: success={report.task_success_rate:.1%} "
                    f"({report.passed_runs}/{report.total_runs}), "
                    f"avg_replans={report.average_replanning_attempts:.2f}"
                )
        return lines


def compute_metrics(graded_runs: list[GradedRun]) -> MetricsReport:
    """Compute the full `MetricsReport` (aggregate + per-category) for a batch."""
    if not graded_runs:
        return MetricsReport(
            task_success_rate=0.0,
            recovery_rate=None,
            average_replanning_attempts=0.0,
            total_runs=0,
            passed_runs=0,
            failed_runs=0,
        )

    aggregate = _compute_flat_metrics(graded_runs)

    categories: dict[str, list[GradedRun]] = {}
    for run in graded_runs:
        categories.setdefault(run.category, []).append(run)

    by_category = {category: _compute_flat_metrics(runs) for category, runs in categories.items()}

    return MetricsReport(
        task_success_rate=aggregate.task_success_rate,
        recovery_rate=aggregate.recovery_rate,
        average_replanning_attempts=aggregate.average_replanning_attempts,
        total_runs=aggregate.total_runs,
        passed_runs=aggregate.passed_runs,
        failed_runs=aggregate.failed_runs,
        guardrail_intervention_rate=aggregate.guardrail_intervention_rate,
        failure_category_distribution=aggregate.failure_category_distribution,
        by_category=by_category,
    )


def _compute_flat_metrics(graded_runs: list[GradedRun]) -> MetricsReport:
    """Compute the headline metrics for a single (non-nested) batch."""
    total = len(graded_runs)
    passed = sum(1 for r in graded_runs if r.passed)
    failed = total - passed

    runs_with_a_failure = [
        r
        for r in graded_runs
        if any(
            record.tool_result is not None and not record.tool_result.success
            for record in r.run_result.trajectory.step_records
        )
    ]
    recovered = sum(1 for r in runs_with_a_failure if r.passed)
    recovery_rate = (recovered / len(runs_with_a_failure)) if runs_with_a_failure else None

    average_replans = (
        sum(r.run_result.metrics.total_replans for r in graded_runs) / total if total else 0.0
    )

    runs_with_guardrail_intervention = sum(
        1 for r in graded_runs if r.run_result.metrics.total_guardrail_blocks > 0
    )
    guardrail_intervention_rate = runs_with_guardrail_intervention / total if total else 0.0

    failed_runs_list = [r for r in graded_runs if not r.passed and r.run_result.failure_category is not None]
    failure_category_distribution: dict[str, float] = {}
    if failed_runs_list:
        counts: dict[str, int] = {}
        for r in failed_runs_list:
            key = r.run_result.failure_category.value  # type: ignore[union-attr]
            counts[key] = counts.get(key, 0) + 1
        failure_category_distribution = {
            key: count / len(failed_runs_list) for key, count in counts.items()
        }

    return MetricsReport(
        task_success_rate=passed / total if total else 0.0,
        recovery_rate=recovery_rate,
        average_replanning_attempts=average_replans,
        total_runs=total,
        passed_runs=passed,
        failed_runs=failed,
        guardrail_intervention_rate=guardrail_intervention_rate,
        failure_category_distribution=failure_category_distribution,
    )


def group_by_golden_task(graded_runs: list[GradedRun]) -> dict[str, list[GradedRun]]:
    """Group graded runs by `golden_task_id`, e.g. for per-task seed-variance analysis."""
    grouped: dict[str, list[GradedRun]] = {}
    for run in graded_runs:
        grouped.setdefault(run.golden_task_id, []).append(run)
    return grouped


def task_pass_rate(graded_runs: list[GradedRun], golden_task_id: str) -> float | None:
    """Pass rate for one specific golden task across however many seeds it was run with."""
    matching = [r for r in graded_runs if r.golden_task_id == golden_task_id]
    if not matching:
        return None
    return sum(1 for r in matching if r.passed) / len(matching)
