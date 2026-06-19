"""Evaluation Harness & Reliability Measurement (Phase 2).

Per the roadmap: a curated golden task suite, an evaluation runner with
seed control, the five core reliability metrics (Task Success Rate,
Recovery Rate, Average Replanning Attempts, Guardrail Intervention Rate,
Failure Category Distribution), structured trajectory storage + failure
analysis reports, and a configuration comparison tool.

See:
    - `reliableagent.evaluation.golden_task` for the `GoldenTask` /
      grading-function contracts.
    - `reliableagent.evaluation.golden_tasks` for the curated 20-task
      suite (`ALL_GOLDEN_TASKS`).
    - `reliableagent.evaluation.golden_tools` for the shared deterministic
      mock tools the suite is built on.
    - `reliableagent.evaluation.factory` for building `Orchestrator`
      factories (standard or customized) for golden tasks.
    - `reliableagent.evaluation.runner` for `EvaluationRunner`/`EvalConfig`
      (seed control + trajectory persistence).
    - `reliableagent.evaluation.metrics` for `GradedRun`/`MetricsReport`/
      `compute_metrics`.
    - `reliableagent.evaluation.failure_analysis` for
      `analyze_failures`/`FailureAnalysisReport`.
    - `reliableagent.evaluation.comparison` for `ConfigVariant`/
      `compare_configurations` and the three named variant-set builders.
"""

from reliableagent.evaluation.comparison import (
    ComparisonResult,
    ConfigVariant,
    compare_configurations,
    critic_strategy_variants,
    executor_retry_variants,
    guardrail_strictness_variants,
)
from reliableagent.evaluation.factory import build_standard_factory, run_golden_suite
from reliableagent.evaluation.failure_analysis import (
    FailureAnalysisReport,
    FailureDetail,
    analyze_failures,
)
from reliableagent.evaluation.golden_task import (
    GoldenTask,
    GradingFn,
    contains_all_grader,
    custom_predicate_grader,
    exact_match_grader,
    numeric_tolerance_grader,
)
from reliableagent.evaluation.golden_tasks import ALL_GOLDEN_TASKS, get_plan_script
from reliableagent.evaluation.golden_tools import build_golden_task_tools
from reliableagent.evaluation.metrics import (
    GradedRun,
    MetricsReport,
    compute_metrics,
    group_by_golden_task,
    task_pass_rate,
)
from reliableagent.evaluation.runner import EvalConfig, EvaluationRunner

__all__ = [
    "ALL_GOLDEN_TASKS",
    "ComparisonResult",
    "ConfigVariant",
    "EvalConfig",
    "EvaluationRunner",
    "FailureAnalysisReport",
    "FailureDetail",
    "GoldenTask",
    "GradedRun",
    "GradingFn",
    "MetricsReport",
    "analyze_failures",
    "build_golden_task_tools",
    "build_standard_factory",
    "compare_configurations",
    "compute_metrics",
    "contains_all_grader",
    "critic_strategy_variants",
    "custom_predicate_grader",
    "exact_match_grader",
    "executor_retry_variants",
    "get_plan_script",
    "group_by_golden_task",
    "guardrail_strictness_variants",
    "numeric_tolerance_grader",
    "run_golden_suite",
    "task_pass_rate",
]
