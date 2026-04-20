"""Unit tests for `reliableagent.guardrails.output_filter`."""

from __future__ import annotations

from reliableagent.core.enums import GuardrailBoundary, GuardrailVerdict
from reliableagent.guardrails.output_filter import OutputFilterGuardrail, RedactionPattern


def test_redacts_email_address():
    guardrail = OutputFilterGuardrail()
    decision = guardrail.check(GuardrailBoundary.FINAL_OUTPUT, "Contact me at jane@example.com.")
    assert decision.verdict == GuardrailVerdict.MODIFY
    assert "jane@example.com" not in decision.modified_payload
    assert "[EMAIL_REDACTED]" in decision.modified_payload


def test_redacts_phone_number():
    guardrail = OutputFilterGuardrail()
    decision = guardrail.check(GuardrailBoundary.FINAL_OUTPUT, "Call 555-123-4567 today.")
    assert decision.verdict == GuardrailVerdict.MODIFY
    assert "[PHONE_REDACTED]" in decision.modified_payload


def test_redacts_ssn():
    guardrail = OutputFilterGuardrail()
    decision = guardrail.check(GuardrailBoundary.FINAL_OUTPUT, "SSN on file: 123-45-6789.")
    assert decision.verdict == GuardrailVerdict.MODIFY
    assert "[SSN_REDACTED]" in decision.modified_payload
    assert "123-45-6789" not in decision.modified_payload


def test_redacts_multiple_pattern_types_in_one_payload():
    guardrail = OutputFilterGuardrail()
    text = "Email jane@example.com or call 555-123-4567."
    decision = guardrail.check(GuardrailBoundary.FINAL_OUTPUT, text)
    assert decision.verdict == GuardrailVerdict.MODIFY
    assert "[EMAIL_REDACTED]" in decision.modified_payload
    assert "[PHONE_REDACTED]" in decision.modified_payload


def test_allows_text_with_no_pii():
    guardrail = OutputFilterGuardrail()
    decision = guardrail.check(GuardrailBoundary.FINAL_OUTPUT, "The sum of 2 and 3 is 5.")
    assert decision.verdict == GuardrailVerdict.ALLOW


def test_non_string_payload_is_allowed_unchanged():
    guardrail = OutputFilterGuardrail()
    decision = guardrail.check(GuardrailBoundary.FINAL_OUTPUT, {"not": "a string"})
    assert decision.verdict == GuardrailVerdict.ALLOW


def test_default_boundary_is_final_output_only():
    guardrail = OutputFilterGuardrail()
    assert guardrail.applies_to(GuardrailBoundary.FINAL_OUTPUT) is True
    assert guardrail.applies_to(GuardrailBoundary.TOOL_OUTPUT) is False


def test_custom_boundaries_can_be_configured():
    guardrail = OutputFilterGuardrail(
        boundaries=frozenset({GuardrailBoundary.FINAL_OUTPUT, GuardrailBoundary.TOOL_OUTPUT})
    )
    assert guardrail.applies_to(GuardrailBoundary.TOOL_OUTPUT) is True


def test_custom_pattern_list_replaces_defaults():
    custom = [RedactionPattern(name="custom_id", pattern=r"ID-\d{4}", placeholder="[ID_REDACTED]")]
    guardrail = OutputFilterGuardrail(patterns=custom)
    decision = guardrail.check(GuardrailBoundary.FINAL_OUTPUT, "Reference ID-1234 for details.")
    assert decision.verdict == GuardrailVerdict.MODIFY
    assert "[ID_REDACTED]" in decision.modified_payload

    decision2 = guardrail.check(GuardrailBoundary.FINAL_OUTPUT, "Contact jane@example.com")
    assert decision2.verdict == GuardrailVerdict.ALLOW


def test_never_blocks_only_modifies_or_allows():
    guardrail = OutputFilterGuardrail()
    text_with_lots_of_pii = "Email a@b.com, b@c.com, call 555-123-4567, SSN 123-45-6789."
    decision = guardrail.check(GuardrailBoundary.FINAL_OUTPUT, text_with_lots_of_pii)
    assert decision.verdict in (GuardrailVerdict.MODIFY, GuardrailVerdict.ALLOW)
