"""Failure analysis reports: turn a batch of `GradedRun`s into a structured,
human-readable report of what failed, why, and how often.

Per the roadmap's "Structured trajectory storage + analysis reports"
requirement. `metrics.py` answers "how reliable is the system, in
aggregate" — this module answers the natural follow-up question, "okay,
so what exactly is going wrong, and where do I look first." It does not
recompute anything `metrics.py` already computes; it consumes the same
`GradedRun`s and adds per-failure detail (which golden task, which seed,
which step, what the guardrail/tool/critic actually said) that an
aggregate metric necessarily discards.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reliableagent.evaluation.metrics import GradedRun, MetricsReport, compute_metrics


@dataclass(frozen=True)
class FailureDetail:
    """One specific, attributable failure found in a `GradedRun`."""

    golden_task_id: str
    category: str
    seed: int | None
    run_id: str
    failure_category: str | None
    grading_explanation: str
    first_failed_step_description: str | None
    first_failed_step_error: str | None
    blocking_guardrail: str | None


@dataclass(frozen=True)
class FailureAnalysisReport:
    """A structured report of every failing run in a batch, plus aggregate metrics.

    Attributes:
        metrics: The same `MetricsReport` `compute_metrics` would
            produce for this batch — included here so a single report
            object carries both "how reliable, in aggregate" and
            "what specifically failed," without a caller needing to
            call both `compute_metrics` and `analyze_failures`
            separately and manually keep them in sync.
        failures: One `FailureDetail` per failing run, in the same
            order as the input `graded_runs`.
        most_common_failure_category: The single most frequent
            `FailureCategory` value among the failures, or `None` if
            there were no failures.
    """

    metrics: MetricsReport
    failures: list[FailureDetail] = field(default_factory=list)
    most_common_failure_category: str | None = None

    def summary_lines(self) -> list[str]:
        """Render a compact, human-readable failure analysis report."""
        lines = ["Failure Analysis Report", "=" * 24, ""]
        lines.extend(self.metrics.summary_lines())
        lines.append("")
        if not self.failures:
            lines.append("No failures in this batch.")
            return lines

        lines.append(f"{len(self.failures)} failing run(s):")
        for failure in self.failures:
            lines.append(
                f"  - [{failure.category}] {failure.golden_task_id} (seed={failure.seed}, "
                f"run_id={failure.run_id})"
            )
            lines.append(f"      category: {failure.failure_category}")
            lines.append(f"      grading:  {failure.grading_explanation}")
            if failure.blocking_guardrail:
                lines.append(f"      blocked by guardrail: {failure.blocking_guardrail}")
            if failure.first_failed_step_error:
                lines.append(
                    f"      first failed step: {failure.first_failed_step_description} "
                    f"-> {failure.first_failed_step_error}"
                )
        return lines


def analyze_failures(graded_runs: list[GradedRun]) -> FailureAnalysisReport:
    """Build a `FailureAnalysisReport` from a batch of graded runs.

    Only runs with `passed=False` produce a `FailureDetail` — a run that
    passed is, by definition, not a reliability gap to investigate, even
    if it happened to hit a transient tool failure along the way (that
    case is exactly what `recovery_rate` in `metrics.py` already credits
    the system for handling correctly).
    """
    metrics = compute_metrics(graded_runs)
    failures = [_build_failure_detail(run) for run in graded_runs if not run.passed]

    most_common: str | None = None
    if metrics.failure_category_distribution:
        most_common = max(
            metrics.failure_category_distribution,
            key=lambda k: metrics.failure_category_distribution[k],
        )

    return FailureAnalysisReport(
        metrics=metrics, failures=failures, most_common_failure_category=most_common
    )


def _build_failure_detail(run: GradedRun) -> FailureDetail:
    trajectory = run.run_result.trajectory

    first_failed_step_description: str | None = None
    first_failed_step_error: str | None = None
    for record in trajectory.step_records:
        if record.tool_result is not None and not record.tool_result.success:
            first_failed_step_description = record.step.description
            first_failed_step_error = record.tool_result.error
            break

    blocking_guardrail: str | None = None
    for decision in trajectory.guardrail_decisions:
        if decision.verdict.value == "block":
            blocking_guardrail = f"{decision.guardrail_name}: {decision.reason}"
            break
    if blocking_guardrail is None:
        for record in trajectory.step_records:
            for decision in record.guardrail_decisions:
                if decision.verdict.value == "block":
                    blocking_guardrail = f"{decision.guardrail_name}: {decision.reason}"
                    break
            if blocking_guardrail:
                break

    return FailureDetail(
        golden_task_id=run.golden_task_id,
        category=run.category,
        seed=run.seed,
        run_id=run.run_result.run_id,
        failure_category=(
            run.run_result.failure_category.value if run.run_result.failure_category else None
        ),
        grading_explanation=run.grading_explanation,
        first_failed_step_description=first_failed_step_description,
        first_failed_step_error=first_failed_step_error,
        blocking_guardrail=blocking_guardrail,
    )
