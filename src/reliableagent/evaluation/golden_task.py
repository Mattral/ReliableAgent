"""Golden tasks: the curated, gradable task suite the Evaluation Harness runs.

Per the roadmap's Phase 2 requirement for a "Curated task suite (15-25
long-horizon tasks)," a `GoldenTask` is a `Task` plus the grading
information needed to score whether a run actually succeeded — which a
bare `Task` deliberately doesn't carry, since "what does success look
like" is an evaluation-time concern, not a runtime one.

Grading is intentionally pluggable (`GradingFn`) rather than a single
hard-coded comparison, because "success" means different things for
different tasks: exact string match for a deterministic arithmetic task,
a numeric tolerance for a computed value, a substring/keyword check for
an open-ended research task, or a custom predicate for anything more
specific. Every golden task in the shipped suite
(`reliableagent.evaluation.golden_tasks`) uses one of the three built-in
graders below; a caller can also supply an arbitrary `GradingFn` for
tasks added later.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from reliableagent.core.models import RunResult, Task

# A grading function receives the completed RunResult and returns
# (passed, explanation). Explanations are always populated, even on
# success, since "it passed, here's why" is exactly the kind of
# evidence a failure-mode report (or a reviewer sanity-checking the
# suite itself) benefits from.
GradingFn = Callable[[RunResult], tuple[bool, str]]


def exact_match_grader(expected: str) -> GradingFn:
    """Grade pass/fail by exact (case-insensitive, whitespace-trimmed) match."""

    def grade(result: RunResult) -> tuple[bool, str]:
        actual = (result.final_answer or "").strip().lower()
        expected_norm = expected.strip().lower()
        if actual == expected_norm:
            return True, f"Final answer exactly matched expected value {expected!r}."
        return False, f"Expected {expected!r} but got {result.final_answer!r}."

    return grade


def contains_all_grader(required_substrings: list[str]) -> GradingFn:
    """Grade pass/fail by requiring every substring to appear (case-insensitive)."""

    def grade(result: RunResult) -> tuple[bool, str]:
        actual = (result.final_answer or "").lower()
        missing = [s for s in required_substrings if s.lower() not in actual]
        if not missing:
            return True, f"Final answer contained all required substrings: {required_substrings}."
        return False, f"Final answer was missing required substring(s): {missing}."

    return grade


def numeric_tolerance_grader(expected: float, *, tolerance: float = 1e-6) -> GradingFn:
    """Grade pass/fail by parsing a number out of the final answer within `tolerance`.

    Extracts the first parseable float/int substring found in the final
    answer text — tasks using this grader should produce an answer where
    that's unambiguous (e.g. "The total is 42." rather than a sentence
    with several numbers in it).
    """
    import re

    number_pattern = re.compile(r"-?\d+(?:\.\d+)?")

    def grade(result: RunResult) -> tuple[bool, str]:
        text = result.final_answer or ""
        match = number_pattern.search(text)
        if match is None:
            return False, f"No numeric value found in final answer: {text!r}."
        actual = float(match.group())
        if abs(actual - expected) <= tolerance:
            return True, f"Extracted value {actual} matched expected {expected} (±{tolerance})."
        return False, f"Extracted value {actual} did not match expected {expected} (±{tolerance})."

    return grade


def custom_predicate_grader(predicate: Callable[[RunResult], bool], description: str) -> GradingFn:
    """Wrap an arbitrary predicate function as a `GradingFn`, for one-off tasks."""

    def grade(result: RunResult) -> tuple[bool, str]:
        passed = predicate(result)
        verdict = "satisfied" if passed else "did not satisfy"
        return passed, f"Custom predicate ({description}) {verdict}."

    return grade


@dataclass(frozen=True)
class GoldenTask:
    """A single curated task in the evaluation suite.

    Attributes:
        task_id: A short, stable, human-readable identifier (distinct
            from the runtime `Task.task_id`, which is a fresh UUID per
            run) — used to identify the same logical task across many
            runs/seeds in aggregate reports.
        category: A coarse label (e.g. "arithmetic", "multi_step",
            "failure_recovery", "guardrail") used to group results in
            reports and to spot category-specific reliability gaps.
        build_task: A factory producing a fresh `Task` instance (tasks
            are frozen/immutable, and a `task_id` is freshly generated
            per run, so this must be a factory, not a single shared
            instance reused across many runs).
        grade: The `GradingFn` used to score a completed `RunResult`.
        expect_failure: True for the small number of tasks whose
            *correct* behavior is to fail or be blocked (e.g. a
            guardrail task whose golden behavior is "the unsafe plan
            gets blocked") — `grade` is still responsible for checking
            this, but the flag makes the suite's intent self-documenting
            and lets reports group "expected failures" separately from
            real reliability gaps.
        tags: Free-form labels for filtering (e.g. "needs_replan",
            "single_tool", "no_tools").
    """

    task_id: str
    category: str
    build_task: Callable[[], Task]
    grade: GradingFn
    expect_failure: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    def make_task(self) -> Task:
        """Build a fresh `Task` instance for one run of this golden task."""
        return self.build_task()
