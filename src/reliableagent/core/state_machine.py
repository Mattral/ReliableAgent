"""The Orchestrator's state machine: legal transitions and validation.

Kept separate from `orchestrator.py` so the state machine's rules are
independently testable and reviewable — exactly the kind of logic
where a silent bug (e.g. allowing `COMPLETED -> EXECUTING`) could
corrupt a trajectory in a way that's hard to notice later.
"""

from __future__ import annotations

from reliableagent.core.enums import OrchestratorState
from reliableagent.exceptions import InvalidStateTransitionError

# The complete set of legal transitions. Terminal states (COMPLETED,
# FAILED) have no outgoing transitions — once reached, a run is done.
_LEGAL_TRANSITIONS: dict[OrchestratorState, frozenset[OrchestratorState]] = {
    OrchestratorState.PENDING: frozenset({OrchestratorState.PLANNING}),
    OrchestratorState.PLANNING: frozenset(
        {OrchestratorState.EXECUTING, OrchestratorState.FAILED}
    ),
    OrchestratorState.EXECUTING: frozenset(
        {
            OrchestratorState.CRITIQUING,
            OrchestratorState.COMPLETED,
            OrchestratorState.FAILED,
        }
    ),
    OrchestratorState.CRITIQUING: frozenset(
        {
            OrchestratorState.EXECUTING,
            OrchestratorState.REPLANNING,
            OrchestratorState.COMPLETED,
            OrchestratorState.FAILED,
        }
    ),
    OrchestratorState.REPLANNING: frozenset(
        {OrchestratorState.EXECUTING, OrchestratorState.FAILED}
    ),
    OrchestratorState.COMPLETED: frozenset(),
    OrchestratorState.FAILED: frozenset(),
}


class StateMachine:
    """Enforces legal `OrchestratorState` transitions for a single run."""

    def __init__(self, initial_state: OrchestratorState = OrchestratorState.PENDING) -> None:
        self._state = initial_state

    @property
    def state(self) -> OrchestratorState:
        return self._state

    def can_transition(self, to_state: OrchestratorState) -> bool:
        """Whether moving from the current state to `to_state` is legal."""
        return to_state in _LEGAL_TRANSITIONS.get(self._state, frozenset())

    def transition(self, to_state: OrchestratorState) -> OrchestratorState:
        """Move to `to_state`, raising if the transition is illegal.

        Returns the new state (for convenient chaining/logging at call
        sites: `tracer.emit_state_transition(old, sm.transition(new))`-style
        usage isn't required, but the return value makes one-liners possible).
        """
        if not self.can_transition(to_state):
            raise InvalidStateTransitionError(
                f"Illegal transition: {self._state.value} -> {to_state.value}",
                context={"from_state": self._state.value, "to_state": to_state.value},
            )
        self._state = to_state
        return self._state

    @property
    def is_terminal(self) -> bool:
        """Whether the current state has no legal outgoing transitions."""
        return len(_LEGAL_TRANSITIONS.get(self._state, frozenset())) == 0
