"""ConfigurationComparison: run the golden suite under several named
configurations and report metrics side-by-side.

Per the roadmap's Phase 2 requirements: "Ability to compare different
configurations" and the success criterion "You can quantitatively show
reliability improvements across iterations." This is the concrete tool
that turns "I made the guardrails stricter, did reliability actually
improve?" from an anecdote into a number.

Comparisons vary across three dimensions: guardrail strictness, Critic
strategy (`ThresholdCritic` vs `LLMCritic`), and Executor retry settings.
A `ConfigVariant` bundles one named choice across (a subset of) these
dimensions; `compare_configurations` runs the full golden suite once per
variant and returns one `MetricsReport` per variant, all directly
comparable since every variant runs the identical set of golden tasks
under the identical seeds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from reliableagent.evaluation.factory import STANDARD_BLOCKED_SUBSTRINGS, run_golden_suite
from reliableagent.evaluation.golden_task import GoldenTask
from reliableagent.evaluation.metrics import MetricsReport, compute_metrics
from reliableagent.executor.executor import Executor
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.guardrails.base import Guardrail
from reliableagent.guardrails.basic import BasicGuardrail, ToolArgumentSanityGuardrail
from reliableagent.planner.critic import Critic, LLMCritic, ThresholdCritic

if TYPE_CHECKING:
    from reliableagent.evaluation.runner import EvalConfig
    from reliableagent.llm.base import LLMClient


@dataclass(frozen=True)
class ConfigVariant:
    """One named configuration to evaluate the golden suite under.

    Each `*_builder` mirrors the corresponding parameter on
    `evaluation.factory.build_standard_factory`. Leaving a builder unset
    means that dimension uses the standard default (see
    `factory.standard_guardrails` / `ThresholdCritic` / the default
    `Executor` settings), so a variant only needs to specify the
    dimension(s) it's actually varying -- exactly what a clean "compare
    guardrail strictness, holding everything else constant" experiment
    needs.
    """

    name: str
    description: str = ""
    guardrails_builder: Callable[[], list[Guardrail]] | None = None
    critic_builder: Callable[[], Critic] | None = None
    executor_builder: Callable[[ToolRegistry], Executor] | None = None


@dataclass(frozen=True)
class ComparisonResult:
    """The outcome of comparing several `ConfigVariant`s on the same suite."""

    variant_reports: dict[str, MetricsReport]
    variant_descriptions: dict[str, str] = field(default_factory=dict)

    def summary_lines(self) -> list[str]:
        """Render a side-by-side comparison table as plain text lines."""
        lines = ["Configuration comparison (same golden suite, same seeds):", ""]
        header = (
            f"{'Variant':<28}{'Success':>10}{'Recovery':>12}"
            f"{'AvgReplans':>13}{'GuardrailInt.':>15}"
        )
        lines.append(header)
        lines.append("-" * len(header))
        for name, report in self.variant_reports.items():
            recovery_str = "n/a" if report.recovery_rate is None else f"{report.recovery_rate:.1%}"
            lines.append(
                f"{name:<28}{report.task_success_rate:>9.1%} {recovery_str:>11}"
                f"{report.average_replanning_attempts:>13.2f}"
                f"{report.guardrail_intervention_rate:>14.1%}"
            )
        return lines

    def best_by_success_rate(self) -> str:
        """Return the variant name with the highest task success rate."""
        return max(
            self.variant_reports, key=lambda name: self.variant_reports[name].task_success_rate
        )


# ---------------------------------------------------------------------------
# Convenience builders for common variant dimensions
# ---------------------------------------------------------------------------


def lenient_guardrails() -> list[Guardrail]:
    """A deliberately looser guardrail stack: only the bare-minimum BasicGuardrail,
    with no blocked-substring policy at all."""
    return [BasicGuardrail()]


def strict_guardrails() -> list[Guardrail]:
    """A deliberately stricter guardrail stack: short max length + tight argument cap."""
    return [
        BasicGuardrail(max_length=2000, blocked_substrings=STANDARD_BLOCKED_SUBSTRINGS),
        ToolArgumentSanityGuardrail(max_arguments=5),
    ]


def no_retry_executor(tools: ToolRegistry) -> Executor:
    """An `Executor` configured with zero retries, wired to `tools`."""
    return Executor(tools, max_retries=0)


def high_retry_executor(tools: ToolRegistry) -> Executor:
    """An `Executor` configured with extra retries and backoff, wired to `tools`."""
    return Executor(tools, max_retries=3, retry_backoff_seconds=0.01)


# ---------------------------------------------------------------------------
# Named, ready-to-use variants for the three required comparison dimensions
# ---------------------------------------------------------------------------


def guardrail_strictness_variants() -> list[ConfigVariant]:
    """Three variants holding Critic/Executor constant, varying only guardrails."""
    return [
        ConfigVariant(
            name="guardrails_lenient",
            description="Only BasicGuardrail, no blocked-substring policy.",
            guardrails_builder=lenient_guardrails,
        ),
        ConfigVariant(
            name="guardrails_standard",
            description="BasicGuardrail + ToolArgumentSanityGuardrail (the suite's baseline).",
        ),
        ConfigVariant(
            name="guardrails_strict",
            description="Tight max_length, tight max_arguments, blocked-substring policy.",
            guardrails_builder=strict_guardrails,
        ),
    ]


def critic_strategy_variants(
    llm_client_for_critic: "LLMClient | None" = None,
) -> list[ConfigVariant]:
    """Variants holding Guardrails/Executor constant, varying only Critic strategy.

    If `llm_client_for_critic` is omitted, only the `ThresholdCritic`
    variants are returned (no LLM call needed); pass a scripted
    `MockLLMClient` (with enough responses for every critique call the
    suite will make) or a real `LLMClient` to also include an
    `LLMCritic` variant.
    """
    variants = [
        ConfigVariant(
            name="critic_threshold_lenient",
            description=(
                "ThresholdCritic(failure_threshold=0.7) -- tolerates more failure "
                "before replanning."
            ),
            critic_builder=lambda: ThresholdCritic(failure_threshold=0.7),
        ),
        ConfigVariant(
            name="critic_threshold_standard",
            description="ThresholdCritic(failure_threshold=0.5) -- the suite's baseline.",
            critic_builder=lambda: ThresholdCritic(failure_threshold=0.5),
        ),
        ConfigVariant(
            name="critic_threshold_strict",
            description=(
                "ThresholdCritic(failure_threshold=0.1) -- replans after almost any failure."
            ),
            critic_builder=lambda: ThresholdCritic(failure_threshold=0.1),
        ),
    ]
    if llm_client_for_critic is not None:
        variants.append(
            ConfigVariant(
                name="critic_llm",
                description="LLMCritic backed by the supplied LLM client.",
                critic_builder=lambda: LLMCritic(llm_client_for_critic),
            )
        )
    return variants


def executor_retry_variants() -> list[ConfigVariant]:
    """Three variants holding Guardrails/Critic constant, varying only Executor retries."""
    return [
        ConfigVariant(
            name="executor_no_retries",
            description="max_retries=0 -- a single failed tool call ends that step immediately.",
            executor_builder=no_retry_executor,
        ),
        ConfigVariant(
            name="executor_standard_retries",
            description="max_retries=1 (the Executor's own default).",
        ),
        ConfigVariant(
            name="executor_high_retries",
            description="max_retries=3 with short backoff.",
            executor_builder=high_retry_executor,
        ),
    ]


# ---------------------------------------------------------------------------
# The comparison entry point
# ---------------------------------------------------------------------------


def compare_configurations(
    golden_tasks: list[GoldenTask],
    variants: list[ConfigVariant],
    eval_config: "EvalConfig",
) -> ComparisonResult:
    """Run `golden_tasks` once per `ConfigVariant` and return all reports.

    Every variant runs against the identical golden tasks under the
    identical seeds in `eval_config`, so any difference in the resulting
    `MetricsReport`s is attributable to the configuration change alone --
    this is the apples-to-apples comparison the roadmap's "quantitatively
    show reliability improvements across iterations" success criterion
    asks for.
    """
    variant_reports: dict[str, MetricsReport] = {}
    variant_descriptions: dict[str, str] = {}

    for variant in variants:
        graded_runs = run_golden_suite(
            golden_tasks,
            eval_config,
            critic_builder=variant.critic_builder,
            guardrails_builder=variant.guardrails_builder,
            executor_builder=variant.executor_builder,
        )
        variant_reports[variant.name] = compute_metrics(graded_runs)
        variant_descriptions[variant.name] = variant.description

    return ComparisonResult(
        variant_reports=variant_reports, variant_descriptions=variant_descriptions
    )
