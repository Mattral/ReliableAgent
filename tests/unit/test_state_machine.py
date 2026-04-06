"""Unit tests for `reliableagent.core.state_machine`."""

from __future__ import annotations

import pytest

from reliableagent.core.enums import OrchestratorState as S
from reliableagent.core.state_machine import StateMachine
from reliableagent.exceptions import InvalidStateTransitionError


def test_initial_state_is_pending():
    sm = StateMachine()
    assert sm.state == S.PENDING


def test_full_happy_path_transition_sequence():
    sm = StateMachine()
    sm.transition(S.PLANNING)
    sm.transition(S.EXECUTING)
    sm.transition(S.CRITIQUING)
    sm.transition(S.COMPLETED)
    assert sm.state == S.COMPLETED
    assert sm.is_terminal


def test_replan_cycle_is_legal():
    sm = StateMachine()
    sm.transition(S.PLANNING)
    sm.transition(S.EXECUTING)
    sm.transition(S.CRITIQUING)
    sm.transition(S.REPLANNING)
    sm.transition(S.EXECUTING)
    assert sm.state == S.EXECUTING


def test_illegal_transition_raises():
    sm = StateMachine()
    with pytest.raises(InvalidStateTransitionError):
        sm.transition(S.EXECUTING)  # PENDING -> EXECUTING is not legal


def test_terminal_states_have_no_outgoing_transitions():
    sm = StateMachine(initial_state=S.COMPLETED)
    assert sm.is_terminal
    with pytest.raises(InvalidStateTransitionError):
        sm.transition(S.PLANNING)


def test_failed_is_terminal():
    sm = StateMachine(initial_state=S.FAILED)
    assert sm.is_terminal


def test_can_transition_does_not_mutate_state():
    sm = StateMachine()
    assert sm.can_transition(S.PLANNING) is True
    assert sm.can_transition(S.EXECUTING) is False
    assert sm.state == S.PENDING  # unchanged
