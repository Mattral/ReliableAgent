# ADR 0005: Process supervision, a strategy-driven Replanner, and two bugs
# this phase's design surfaced in the existing Orchestrator

## Status
Accepted.

## Context
Phase 3 calls for "Stronger Critic with process supervision," "More
sophisticated Replanner," and "Enhanced Guardrail strategies (policy-based,
output filtering)." Unlike Phases 0-2, the roadmap gives almost no further
detail on any of these three bullets, so the concrete shape of each was a
genuine design decision rather than an implementation of an already-spelled-out
spec (see the three scoping conversations preceding this phase's
implementation).

## Decisions

### 1. `Critic.critique_step` as an optional, default-`None` extension point
Rather than introducing a separate `ProcessSupervisionCritic` *protocol*
alongside the existing `Critic`, `critique_step()` was added directly to
the `Critic` base class with a default implementation that returns `None`.
This means every Phase 0-2 `Critic` (`ThresholdCritic`, `LLMCritic`)
remains fully valid with zero changes -- they simply opt out of step-level
supervision -- while `DeterministicProcessCritic`/`LLMProcessCritic`
(Phase 3) opt in by overriding it. The Orchestrator calls `critique_step()`
unconditionally after every tool-call step and only attaches a
`StepCritique` to the `StepRecord` when one is actually returned, so a
`Trajectory` produced by a Phase 0/1-style Critic looks identical to one
produced before this phase existed.

### 2. `CriterionScores` as a derived-overall, not an independently-settable field
`CriterionScores.weighted_overall()` is a method, not a stored field,
specifically so `Feedback.quality_score` can never silently drift out of
sync with the `correctness`/`efficiency`/`safety` triple it's computed
from. The weights are parameters to the method (defaulting to
correctness-heavy: 0.6/0.2/0.2), not hardcoded, so a safety-critical
deployment can rebalance them without a new model or Critic subclass.

### 3. `Replanner` wraps `Planner`, defaulting to real sophistication
`Orchestrator.__init__` accepts an optional `replanner=` parameter but
defaults to `Replanner(planner)` when none is given -- failure-type
classification and budget-aware strategy selection are the Orchestrator's
default replanning behavior, not an opt-in a caller has to discover. The
`Replanner` never bypasses `Planner.plan()`; it only shapes the hint
(`feedback_reason`) and, for the budget-exhaustion strategy, the `Task`
passed into it (shrinking `max_steps` via `Task.model_copy`). This keeps
every existing `Planner` implementation, and every test written against
one, fully compatible.

### 4. `PolicyGuardrail` (structured rules) and `OutputFilterGuardrail`
(regex-based PII redaction) as two SEPARATE guardrails, not one
`EnhancedGuardrail` doing both. `PolicyGuardrail`'s rules are
BLOCK-or-MODIFY and caller-defined; `OutputFilterGuardrail` is
MODIFY-only and ships with a small built-in PII pattern library. Conflating
them would have forced one component to support both "caller-authored
arbitrary policy" and "built-in redaction library" through the same
configuration surface, when the natural usage pattern -- supply your own
policy rules, but use the built-in redaction patterns as-is -- splits
cleanly along that exact boundary.

## Two bugs this phase's design surfaced in the EXISTING Orchestrator

Both were found by writing process-supervision/Replanner features and
their tests, not by deliberately auditing for bugs -- recorded here
because they're a useful illustration of how adding real capability
exposes gaps that adding only documentation or tests for existing
behavior would not have.

### Bug 1: `GuardrailRunResult.final_payload` was silently discarded at
run-level boundaries
`Orchestrator._guard()` (used at `PLANNER_INPUT`, `PLANNER_OUTPUT`, and
`FINAL_OUTPUT`) checked `result.allowed` and raised on BLOCK, but never
read `result.final_payload` -- meaning a MODIFY verdict (e.g.
`OutputFilterGuardrail` redacting an email address) was computed,
correctly logged in a `GuardrailDecision`, and then silently thrown away:
`trajectory.final_answer` still received the ORIGINAL, unredacted text.
This bug existed since Phase 0/1 but had zero observable consequence
until this phase introduced the first guardrail whose entire purpose is
to MODIFY rather than BLOCK or pure-ALLOW. Fixed by changing `_guard()` to
return `result.final_payload` and updating both `FINAL_OUTPUT` call sites
in `_execute_and_continue` to use the returned value as
`trajectory.final_answer`, rather than the original unmodified text.
Verified via `tests/integration/test_phase3_features.py::
test_output_filter_guardrail_redaction_reaches_final_answer`, which
asserts the redacted placeholder text appears in `RunResult.final_answer`
and the original PII does not. `PLANNER_INPUT`/`PLANNER_OUTPUT` were
deliberately left as-is (with an explanatory comment) since no guardrail
in this delivery needs the modified payload threaded back at those two
boundaries, and `task.description` lives on an immutable `Task` that
would need a `model_copy` to redact in place -- a real but currently
unnecessary additional change, not attempted here.

### Bug 2: a successful run via an explicit `final_answer` step never
called `Critic.critique()` at all
Discovered while testing that `DeterministicProcessCritic`'s multi-criteria
`Feedback` actually shows up in `Trajectory.feedbacks` for an ordinary
successful run -- it didn't. The Orchestrator's loop only called
`critique()` on the "plan exhausted WITHOUT reaching a final_answer step"
fallback path; a plan that completes via an explicit `final_answer` step
(the common, intended case) skipped straight to `COMPLETED` with no
critique call at all. This meant `Trajectory.feedbacks` was empty for the
large majority of runs, regardless of which `Critic` was configured --
a real, latent gap in trajectory completeness from Phase 0/1, not specific
to this phase's new Critics, just newly visible because this phase
specifically tests for a recorded `Feedback`. Fixed by adding one more
`critique()` call immediately before transitioning to `COMPLETED` via the
`final_answer` path, purely for the observability/trajectory record (its
`should_replan` is deliberately ignored, since the run is already
provably complete at that point). This is a real, measurable behavioral
change worth calling out explicitly: **every successful run now makes one
additional `Critic.critique()` call** compared to Phase 0-2's behavior --
for `ThresholdCritic`/`DeterministicProcessCritic` this is free (no LLM
call), but for `LLMCritic` this is one additional LLM call per run, always,
even when nothing went wrong. Verified via
`test_final_critique_is_recorded_even_on_explicit_final_answer_path` and
a standalone manual check confirming `LLMCritic` now correctly receives
exactly 2 total LLM calls (1 plan + 1 final critique) for a trivial
single-step task, with exactly 1 `Feedback` recorded.

## Consequences

**Positive:**
- Every `Trajectory` now carries a complete quality record
  (`feedbacks`) regardless of which completion path a run took, which is
  a strictly better foundation for the kind of failure-mode analysis
  Phase 2's harness already does, and which a future "is this Critic
  consistently too lenient" analysis would depend on.
- A guardrail's MODIFY verdict is now actually trustworthy: it's a
  documented, tested guarantee that the redacted/modified payload is what
  a caller actually receives, not merely what got logged.
- The two new `ReplanStrategy` implementations, `PolicyGuardrail`, and
  `OutputFilterGuardrail` are all independently unit-tested (50 new tests
  across this phase) AND exercised through the real `Orchestrator` in
  integration tests, the same two-tier testing discipline established in
  Phases 0-2.

**Negative / known limitations:**
- The extra `critique()` call on every successful run is a real,
  non-zero cost/latency change for anyone using `LLMCritic` in production
  that did not exist in Phase 0-2's behavior. This is flagged here
  prominently rather than left as a silent side effect of an unrelated
  feature.
- `OutputFilterGuardrail`'s PII detection is regex-based, not an ML
  classifier; it will miss PII that doesn't match a known pattern shape.
  This is documented honestly in the module's own docstring rather than
  oversold, and is the correct place to plug in a real classifier in a
  future phase without changing the `Guardrail` contract it implements.
- `PolicyGuardrail`'s rule matching is plain regex, not a rules-engine
  DSL; this was a deliberate simplicity choice (see the module docstring)
  but means genuinely complex conditional policies (e.g. "block X unless
  Y was also true earlier in this run") are out of scope for this
  delivery and would need a different mechanism.
