# ADR 0004: A dual-purpose, scripted-and-real-model-ready golden task suite

## Status
Accepted.

## Context
Phase 2 calls for a "curated task suite (15-25 long-horizon tasks)" plus
five core reliability metrics, structured trajectory storage, and a
configuration comparison tool, with the success criterion "one-command
evaluation that produces clear metrics and failure analysis" and "you can
quantitatively show reliability improvements across iterations." The same
network restriction documented in `adr/0001` and `adr/0003` applied here
too: there was no way to validate the suite against a real LLM during
this delivery.

## Decision
Build every golden task (`evaluation/golden_tasks.py`) as a `GoldenTask`
carrying both a grading function AND a hand-written `MockLLMClient`
script representing that task's known-correct plan(s)
(`evaluation/golden_tasks.py`'s `ALL_PLAN_SCRIPTS`). This makes the suite
usable two ways without any code changes between them: (1) **today**,
run scripted against `MockLLMClient` to regression-test the orchestration
engine itself -- does the real `Orchestrator`/`Executor`/`GuardrailRunner`/
`Critic` correctly execute, recover from, or block each scripted scenario;
and (2) **once network access exists**, run the identical 20 tasks and
graders against a real `AnthropicLLMClient`-backed `LLMPlanner`/`LLMCritic`
(via `--use-real-anthropic-model` in `examples/run_evaluation.py`) to
measure the harder, real question: does a live model reliably produce
plans that achieve the same outcomes.

## Alternatives considered

**A: Build the suite only for real-model evaluation, leave it unrunnable
in this delivery.** Rejected -- this would mean Phase 2's most important
deliverable (the actual task suite and its metrics) ships completely
unverified, the same gap explicitly flagged as the top caveat for the
unexecuted CI/lint tooling in `docs/roadmap_status.md`. Phase 2 doesn't
have to inherit that same gap when a dual-purpose design avoids it
entirely.

**B: Use only free-form, LLM-graded "does this look reasonable" scoring
instead of hand-written exact/numeric/substring/predicate graders.**
Rejected. An LLM-as-judge grader would itself need a real model call to
function at all (circular, given the network constraint), and more
fundamentally would make the suite's pass/fail outcomes themselves
non-deterministic -- directly undermining `test_suite_is_deterministic_
across_repeated_runs`, which is exactly the property that makes this
suite trustworthy as a regression check in CI.

**C: Treat "Recovery Rate" and "Failure Category Distribution" as
properties of the *Orchestrator run* (`RunResult.final_state`/
`failure_category`) rather than of the *graded outcome*
(`GradedRun.passed`).** Rejected after a concrete bug surfaced during
development: 4 of the 20 golden tasks are deliberately
`expect_failure=True` (their *correct* behavior is for the Orchestrator
to end in `FAILED` -- e.g. being correctly blocked by a guardrail). An
early implementation of `failure_category_distribution` counted any run
with a non-`None` `failure_category`, which meant a fully-passing 20/20
suite still reported a non-empty failure distribution, because those 4
intentionally-failing-Orchestrator-runs still had a `failure_category`
set even though `GradedRun.passed=True`. Fixed by keying every metric in
`metrics.py` on `GradedRun.passed`, not on `RunResult.final_state`
directly -- "did the grader say this was correct" and "did the
Orchestrator's run end in FAILED" are related but genuinely different
questions, and only the former is what a reliability metric should
report on.

## A second concrete bug this design caught, worth recording
`golden_tools.py`'s `flaky_lookup` tool originally failed exactly once
per distinct query before succeeding, intended to model a transient
backend hiccup that forces a replan to recover from. It didn't actually
require a replan at all: `Executor`'s own default `max_retries=1` means
a failed tool call is automatically retried once *within the same
step*, before the Orchestrator's Critic is ever consulted -- so the
single scripted failure was silently absorbed by the Executor's built-in
retry, and the task passed on the very first plan with zero replans,
making `recovery_flaky_tool_succeeds_after_replan` a misleadingly-named
task that didn't test what it claimed to. This was caught by
`test_failure_recovery_tasks_that_should_pass_show_at_least_one_replan`
in `tests/eval/test_golden_suite.py`, which asserts every non-
`expect_failure` `failure_recovery` task actually exercises at least one
replan -- not just that it eventually passes. Fixed by making
`flaky_lookup` fail its first TWO calls per query, which survives the
Executor's one built-in retry and genuinely forces the replanning path.
This is recorded here because it's a good illustration of why the suite
asserts on *mechanism* (did a replan actually happen) and not just
*outcome* (did the task eventually pass) -- an outcome-only assertion
would have shipped this bug silently.

## Consequences

**Positive:**
- `examples/run_evaluation.py` and `examples/compare_configurations.py`
  are genuinely one-command, genuinely verified (run and inspected
  during this delivery, not just configured), and produce exactly the
  "clear metrics and failure analysis" / "quantitatively show reliability
  improvements" outputs the roadmap's success criteria ask for.
- The exact same golden-task definitions become real-model evaluation
  infrastructure later with a one-flag change, not a rewrite.
- Two real bugs (the failure-category-distribution semantics issue, and
  the Executor-retry-masking-a-replan-scenario issue) were caught and
  fixed during this delivery specifically because the suite asserts on
  mechanism, not just outcome.

**Negative / known limitations:**
- The hand-scripted plans in `ALL_PLAN_SCRIPTS` represent ONE correct way
  to solve each task; a real model that solves a task via a different
  (also valid) tool-call sequence would need its own grading logic to be
  recognized as correct rather than penalized for not matching the
  script -- but this is a non-issue in practice, since the scripts are
  only used to drive the Planner's mocked LLM responses, never compared
  against a real model's plan. Real-model runs are graded purely on the
  final `RunResult` via each task's `GradingFn`, which never inspects
  the plan's shape, only its outcome.
- The suite's tool failure rates are intentionally binary (0% or 100%,
  never partial), as documented honestly in `examples/compare_
  configurations.py`'s narration after this was discovered empirically:
  this means the suite cannot currently distinguish Critic threshold
  tuning effects, since no task in it has a partial-failure profile
  (e.g. "3 of 5 sub-results succeeded"). Adding such a task is a natural
  next addition, not attempted in this delivery.
