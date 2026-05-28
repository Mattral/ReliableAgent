"""Replanner: chooses a replanning STRATEGY based on why a replan is needed
and how many replan attempts remain, rather than always just re-prompting
the same Planner with a generic feedback string.

Per Phase 3's "More sophisticated Replanner." Prior to this module, every
replan in the Orchestrator's loop looked the same regardless of cause: call
`Planner.plan(..., feedback_reason=<Critic's rationale string>)` and hope
the Planner figures out what to do differently. That conflates several
genuinely different situations that call for different responses:

    - A single tool kept failing -> try a DIFFERENT tool/approach for that
      sub-problem, not just "try again."
    - The plan's steps were too coarse/ambitious for what's actually
      achievable -> DECOMPOSE into smaller, more verifiable steps.
    - The task itself is ambiguous or under-specified in a way no amount
      of replanning can fix -> surface that rather than burning replan
      budget on guesses (a real `Planner` can't literally ask a human
      mid-run in this delivery, so this strategy degrades to "make the
      most conservative, narrowly-scoped attempt possible" -- see
      `DecomposeFurtherStrategy`, which both failure types share).
    - Replan budget is nearly exhausted -> deliberately shrink ambition
      (fewer steps, simpler approach) rather than attempt something
      equally complex with less room left to recover if it also fails.

`Replanner` sits between the Orchestrator and the `Planner`: it inspects
the failure that triggered replanning plus how many replan attempts
remain, picks a `ReplanStrategy`, and lets that strategy shape the prompt
context handed to the underlying `Planner` -- the `Planner` itself is
unchanged and unaware a Replanner exists; only the *hint* it's given
differs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from reliableagent.core.models import Feedback, Plan, Task, ToolResult
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.planner.base import Planner


class FailureType(str, Enum):
    """A finer-grained classification of why THIS replan is being triggered.

    Distinct from `core.enums.FailureCategory`, which categorizes why a
    *run* ultimately and terminally failed. `FailureType` classifies one
    in-progress replan trigger, mid-run, before it's known whether the
    run will ultimately succeed or fail -- the Replanner needs this
    classification to pick a strategy *before* that outcome exists.
    """

    REPEATED_TOOL_FAILURE = "repeated_tool_failure"
    """A tool call failed -- suggests the approach, not just the
    attempt, was wrong, and a different approach should be tried."""

    LOW_QUALITY_PROGRESS = "low_quality_progress"
    """Nothing concretely failed, but the Critic's quality_score /
    correctness is low -- the plan is technically executing but not
    converging on the task."""

    AMBIGUOUS_OR_UNDERSPECIFIED = "ambiguous_or_underspecified"
    """The Critic's issues suggest the task itself, not the execution,
    is the problem -- no tool failed, but progress stalled anyway."""

    BUDGET_NEARLY_EXHAUSTED = "budget_nearly_exhausted"
    """Independent of WHY a replan is needed, few attempts remain --
    takes priority in strategy selection when true, since "what to try
    next" matters less than "how ambitious can we still afford to be."""


def classify_failure(
    feedback: Feedback, results: list[ToolResult], *, replans_remaining: int, max_replans: int
) -> FailureType:
    """Classify why a replan is being triggered, from the Critic's feedback
    and the results observed so far under the current plan.

    Budget exhaustion is checked FIRST and takes priority over the
    content-based classification below it: with very little room left to
    recover from a second bad guess, HOW to shrink ambition matters more
    than WHY the previous attempt fell short.
    """
    if max_replans > 0 and replans_remaining <= max(1, max_replans // 3):
        return FailureType.BUDGET_NEARLY_EXHAUSTED

    failed_results = [r for r in results if not r.success]
    if failed_results:
        return FailureType.REPEATED_TOOL_FAILURE

    if feedback.issues:
        return FailureType.AMBIGUOUS_OR_UNDERSPECIFIED

    return FailureType.LOW_QUALITY_PROGRESS


@dataclass(frozen=True)
class ReplanContext:
    """Everything a `ReplanStrategy` needs to shape its hint to the Planner."""

    task: Task
    tools: ToolRegistry
    prior_results: list[ToolResult]
    feedback: Feedback
    failure_type: FailureType
    replan_attempt: int
    replans_remaining: int
    max_replans: int


class ReplanStrategy(ABC):
    """A specific approach to recovering from one `FailureType`.

    Each strategy's only real job is to produce a `feedback_reason` hint
    string that's MORE useful to the underlying `Planner` than the
    Critic's raw rationale alone would be -- concretely actionable
    guidance ("avoid repeating the same failed call; try a different
    approach for this sub-goal") rather than a bare description of what
    went wrong. Plan generation itself still happens inside
    `Planner.plan`; the strategy shapes the prompt, it doesn't bypass
    the Planner.
    """

    name: str = "unnamed_strategy"

    @abstractmethod
    def build_hint(self, context: ReplanContext) -> str:
        """Produce the feedback_reason hint to pass to `Planner.plan`."""
        raise NotImplementedError

    def adjust_task_for_retry(self, context: ReplanContext, task: Task) -> Task:
        """Optionally return a modified `Task` for this replan attempt.

        Default: return `task` unchanged. `BudgetAwareDecomposeStrategy`
        overrides this to shrink `max_steps`, which is the concrete
        mechanism behind "the Replanner sees how many attempts remain
        and adjusts plan complexity/risk accordingly."
        """
        return task


class RetryDifferentApproachStrategy(ReplanStrategy):
    """For `REPEATED_TOOL_FAILURE`: explicitly steer away from what just failed."""

    name = "retry_different_approach"

    def build_hint(self, context: ReplanContext) -> str:
        failed_results = [r for r in context.prior_results if not r.success]
        failed_errors = [r.error for r in failed_results if r.error]
        error_summary = (
            "; ".join(failed_errors[:3]) if failed_errors else context.feedback.rationale
        )
        return (
            f"The previous approach failed ({len(failed_results)} failed call(s)): "
            f"{error_summary}. Do not repeat the exact same tool call with the same "
            "arguments -- choose a different tool, different arguments, or break this "
            "sub-goal into smaller steps that are each easier to verify."
        )


class DecomposeFurtherStrategy(ReplanStrategy):
    """For `LOW_QUALITY_PROGRESS` / `AMBIGUOUS_OR_UNDERSPECIFIED`: break the
    task into smaller, more verifiable steps rather than retrying the same
    granularity of plan."""

    name = "decompose_further"

    def build_hint(self, context: ReplanContext) -> str:
        issues = (
            "; ".join(context.feedback.issues)
            if context.feedback.issues
            else "progress has stalled without a specific tool failure"
        )
        return (
            f"Progress is not converging on the task ({issues}). Break the remaining "
            "work into smaller, more specific steps, each independently verifiable, "
            "rather than repeating a similarly broad approach."
        )


class BudgetAwareDecomposeStrategy(ReplanStrategy):
    """For `BUDGET_NEARLY_EXHAUSTED`: shrink ambition, not just change tactics.

    This is the concrete mechanism behind "the Replanner sees how many
    attempts remain and adjusts plan complexity/risk accordingly": with
    few replans left, `adjust_task_for_retry` deliberately lowers
    `max_steps`, forcing the next plan toward the simplest viable
    approach rather than something equally elaborate as what already
    failed.
    """

    name = "budget_aware_decompose"

    def __init__(self, *, max_steps_floor: int = 3) -> None:
        self.max_steps_floor = max(max_steps_floor, 1)

    def build_hint(self, context: ReplanContext) -> str:
        return (
            f"Only {context.replans_remaining} replan attempt(s) remain out of "
            f"{context.max_replans}. Prefer the simplest, most direct approach that "
            "could plausibly work, even if less thorough -- this is not the attempt "
            "to take on additional risk or complexity."
        )

    def adjust_task_for_retry(self, context: ReplanContext, task: Task) -> Task:
        reduced_max_steps = min(task.max_steps, self.max_steps_floor)
        if reduced_max_steps == task.max_steps:
            return task
        return task.model_copy(update={"max_steps": reduced_max_steps})


class Replanner:
    """Classifies a replan trigger and delegates to the matching `ReplanStrategy`.

    The Orchestrator calls `Replanner.replan(...)` exactly where it
    previously called `Planner.plan(...)` directly during a replan cycle
    (see `core/orchestrator.py`'s `_execute_and_continue`); the
    `Replanner` always still calls the same underlying `Planner.plan`
    under the hood, just with a strategy-shaped hint and (for the
    budget-aware strategy) a possibly-adjusted `Task`.
    """

    def __init__(
        self,
        planner: Planner,
        *,
        strategies: dict[FailureType, ReplanStrategy] | None = None,
    ) -> None:
        self._planner = planner
        self._strategies: dict[FailureType, ReplanStrategy] = strategies or {
            FailureType.REPEATED_TOOL_FAILURE: RetryDifferentApproachStrategy(),
            FailureType.LOW_QUALITY_PROGRESS: DecomposeFurtherStrategy(),
            FailureType.AMBIGUOUS_OR_UNDERSPECIFIED: DecomposeFurtherStrategy(),
            FailureType.BUDGET_NEARLY_EXHAUSTED: BudgetAwareDecomposeStrategy(),
        }

    def replan(
        self,
        task: Task,
        tools: ToolRegistry,
        *,
        prior_results: list[ToolResult],
        feedback: Feedback,
        replan_attempt: int,
        max_replans: int,
    ) -> Plan:
        """Classify the failure, pick a strategy, and produce the next `Plan`."""
        replans_remaining = max(max_replans - replan_attempt + 1, 0)
        failure_type = classify_failure(
            feedback, prior_results, replans_remaining=replans_remaining, max_replans=max_replans
        )
        strategy = self._strategies.get(failure_type, DecomposeFurtherStrategy())

        context = ReplanContext(
            task=task,
            tools=tools,
            prior_results=prior_results,
            feedback=feedback,
            failure_type=failure_type,
            replan_attempt=replan_attempt,
            replans_remaining=replans_remaining,
            max_replans=max_replans,
        )

        hint = strategy.build_hint(context)
        adjusted_task = strategy.adjust_task_for_retry(context, task)

        return self._planner.plan(
            adjusted_task,
            tools,
            prior_results=prior_results,
            replan_attempt=replan_attempt,
            feedback_reason=hint,
        )

    def last_failure_type(
        self,
        feedback: Feedback,
        results: list[ToolResult],
        *,
        replans_remaining: int,
        max_replans: int,
    ) -> FailureType:
        """Expose the classification alone, for callers (tests, observability)
        that want to inspect it without triggering an actual replan call."""
        return classify_failure(
            feedback, results, replans_remaining=replans_remaining, max_replans=max_replans
        )
