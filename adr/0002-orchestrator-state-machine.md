# ADR 0002: An explicit, statically-enumerated state machine for the Orchestrator

## Status
Accepted.

## Context
The roadmap calls for a "Working Orchestrator with explicit state machine"
as a P0/P1 requirement, specifically calling out a `PENDING -> PLANNING ->
EXECUTING -> CRITIQUING -> (REPLANNING | COMPLETED | FAILED)` cycle. The
Orchestrator is also the component most directly responsible for the
project's reliability claims: if its internal state can drift into an
inconsistent or illegal configuration, every guarantee built on top of it
(checkpoint/resume correctness, accurate failure categorization,
trustworthy trajectories) is compromised.

## Decision
Implement state transitions as a single, statically-enumerated table
(`_LEGAL_TRANSITIONS` in `core/state_machine.py`) mapping each
`OrchestratorState` to the frozenset of states it may legally transition
to, enforced by a small `StateMachine` class with one mutating method,
`transition(to_state)`, that raises `InvalidStateTransitionError` for any
attempted illegal transition. The Orchestrator never mutates a "current
state" field directly — every transition anywhere in `orchestrator.py`
goes through `StateMachine.transition()` via the `_transition()` helper,
which also emits a `state_transition` observability event.

## Alternatives considered

**A: A bare enum field on the Orchestrator, mutated directly
(`self.state = OrchestratorState.EXECUTING`).** Rejected. This makes
"is this transition even legal" a property nobody enforces — a future
code change (e.g. a new replanning path added under time pressure) could
trivially introduce an illegal jump like `CRITIQUING -> PLANNING` (skipping
`REPLANNING` entirely) without anything failing until a confusing bug
shows up in trajectory analysis much later.

**B: Encode the state machine implicitly via Python control flow alone
(nested if/while loops with no explicit state tracking at all).**
Rejected. This is roughly what `_execute_and_continue`'s loop body
actually *does* operationally, but without an explicit `OrchestratorState`
enum and transition table, `Trajectory.final_state` (which downstream
consumers like a future Evaluation Harness depend on for failure-mode
analysis) would have no canonical source of truth, and the Orchestrator's
control flow would be unreviewable as a state machine at all — you'd have
to read every branch of nested logic to reconstruct what states are even
reachable.

**C: A generic state-machine library (e.g. `transitions`).** Rejected for
this phase: would add a dependency for what is, in this project's case, a
six-state, ~10-edge graph that's easier to review as a 20-line dict
literal than as configuration for a general framework. If the state space
grows substantially in a later phase (e.g. multi-agent coordination in
Phase 3 introduces per-agent sub-states), revisiting this decision would
be reasonable.

## Consequences

**Positive:**
- `tests/unit/test_state_machine.py` can exhaustively test the state
  machine's legal and illegal transitions in complete isolation from the
  rest of the Orchestrator — no LLM, no tools, no guardrails involved.
- A bug that would otherwise manifest as "the trajectory looks wrong in a
  way I can't explain" instead raises `InvalidStateTransitionError`
  immediately, at the exact call site that attempted the illegal jump.
- `StateMachine.is_terminal` gives the Orchestrator (and any future
  external caller) a single, reliable way to ask "is this run actually
  done" without needing to enumerate `{COMPLETED, FAILED}` by hand at
  every call site.

**Negative:**
- Adding a genuinely new state in a future phase (e.g. a `PAUSED` state
  for human-in-the-loop approval gates) requires updating both the enum
  in `core/enums.py` and the transition table in `core/state_machine.py`
  in lockstep — a small but real two-file coordination cost compared to
  encoding state purely as ad-hoc control flow.
