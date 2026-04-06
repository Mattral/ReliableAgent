"""Unit tests for `reliableagent.guardrails`."""

from __future__ import annotations

from reliableagent.core.enums import GuardrailBoundary, GuardrailVerdict
from reliableagent.guardrails import (
    BasicGuardrail,
    FinalOutputPolicyGuardrail,
    GuardrailRunner,
    ToolArgumentSanityGuardrail,
)


def test_basic_guardrail_allows_normal_text():
    g = BasicGuardrail()
    decision = g.check(GuardrailBoundary.FINAL_OUTPUT, "a perfectly normal answer")
    assert decision.verdict == GuardrailVerdict.ALLOW


def test_basic_guardrail_blocks_empty_text():
    g = BasicGuardrail()
    decision = g.check(GuardrailBoundary.FINAL_OUTPUT, "   ")
    assert decision.verdict == GuardrailVerdict.BLOCK


def test_basic_guardrail_blocks_over_max_length():
    g = BasicGuardrail(max_length=10)
    decision = g.check(GuardrailBoundary.FINAL_OUTPUT, "this text is way too long for the limit")
    assert decision.verdict == GuardrailVerdict.BLOCK


def test_basic_guardrail_blocks_configured_substring():
    g = BasicGuardrail(blocked_substrings=["forbidden phrase"])
    decision = g.check(GuardrailBoundary.PLANNER_OUTPUT, "this has a FORBIDDEN PHRASE in it")
    assert decision.verdict == GuardrailVerdict.BLOCK


def test_tool_argument_sanity_guardrail_blocks_non_dict_payload():
    g = ToolArgumentSanityGuardrail()
    decision = g.check(GuardrailBoundary.TOOL_INPUT, "not-a-dict")
    assert decision.verdict == GuardrailVerdict.BLOCK


def test_tool_argument_sanity_guardrail_allows_reasonable_dict():
    g = ToolArgumentSanityGuardrail()
    decision = g.check(GuardrailBoundary.TOOL_INPUT, {"a": 1, "b": 2})
    assert decision.verdict == GuardrailVerdict.ALLOW


def test_final_output_policy_guardrail_only_applies_to_final_output_boundary():
    g = FinalOutputPolicyGuardrail()
    assert g.applies_to(GuardrailBoundary.FINAL_OUTPUT) is True
    assert g.applies_to(GuardrailBoundary.TOOL_INPUT) is False


def test_guardrail_runner_stops_at_first_block():
    g1 = BasicGuardrail(blocked_substrings=["bad"])
    g2 = ToolArgumentSanityGuardrail()
    runner = GuardrailRunner([g1, g2])
    result = runner.run(GuardrailBoundary.TOOL_INPUT, "bad input string")
    assert result.allowed is False
    # g2 (ToolArgumentSanityGuardrail) never runs because g1 already blocked.
    assert len(result.decisions) == 1


def test_guardrail_runner_allows_when_no_guardrails_block():
    runner = GuardrailRunner([BasicGuardrail(), ToolArgumentSanityGuardrail()])
    result = runner.run(GuardrailBoundary.TOOL_INPUT, {"x": 1})
    assert result.allowed is True
    assert len(result.decisions) == 2


def test_guardrail_runner_skips_inapplicable_guardrails():
    runner = GuardrailRunner([ToolArgumentSanityGuardrail()])  # only applies to TOOL_INPUT
    result = runner.run(GuardrailBoundary.FINAL_OUTPUT, "anything")
    assert result.allowed is True
    assert result.decisions == []
