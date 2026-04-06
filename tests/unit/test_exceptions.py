"""Unit tests for `reliableagent.exceptions`."""

from __future__ import annotations

from reliableagent.exceptions import (
    GuardrailViolationError,
    PlanningError,
    ReliableAgentError,
    ToolExecutionError,
    ToolNotFoundError,
)


def test_all_custom_exceptions_inherit_base():
    assert issubclass(ToolNotFoundError, ReliableAgentError)
    assert issubclass(PlanningError, ReliableAgentError)
    assert issubclass(GuardrailViolationError, ReliableAgentError)


def test_to_dict_contains_expected_keys():
    err = ToolExecutionError("tool blew up", context={"tool_name": "x"})
    data = err.to_dict()
    assert data["error_type"] == "ToolExecutionError"
    assert data["message"] == "tool blew up"
    assert data["context"] == {"tool_name": "x"}
    assert "recoverable" in data


def test_guardrail_violation_error_carries_extra_fields():
    err = GuardrailViolationError(
        "blocked", guardrail_name="basic_guardrail", boundary="final_output"
    )
    data = err.to_dict()
    assert data["guardrail_name"] == "basic_guardrail"
    assert data["boundary"] == "final_output"


def test_recoverable_flags_are_set_sensibly():
    assert ToolNotFoundError("x").recoverable is True
    assert PlanningError("x").recoverable is True


def test_context_defaults_to_empty_dict():
    err = ReliableAgentError("just a message")
    assert err.context == {}
