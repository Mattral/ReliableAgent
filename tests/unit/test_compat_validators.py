"""Tests for reliableagent._compat's field_validator/model_validator shim behavior.

See adr/0010 for the real bug this file's tests were added to catch and
prevent from recurring: a @field_validator reading another field via
info.data silently never ran on a defaulted (not explicitly supplied) field
under real Pydantic v2, even though this project's offline fallback shim
ran it unconditionally -- a genuine behavioral divergence between the two
that was invisible in 242 offline tests and only surfaced under real
Pydantic in CI.

These tests target the ACTUAL PlanStep model (not synthetic shim-only
models) specifically so they exercise identically under real Pydantic
and under the fallback shim -- the real regression-prevention value here
is that this exact test passes under BOTH, whereas the bug this fixes
was invisible under the shim and only visible under real Pydantic.
"""
from __future__ import annotations
import pytest
from reliableagent.core.enums import StepType
from reliableagent.core.models import PlanStep


def test_tool_call_step_without_tool_name_raises():
    """The exact regression case from adr/0010: omitting tool_name (letting
    it fall back to its default of None) must still raise, in both real
    Pydantic and the offline fallback shim."""
    with pytest.raises(ValueError):
        PlanStep(step_type=StepType.TOOL_CALL, description="missing tool name")


def test_tool_call_step_with_explicit_none_tool_name_also_raises():
    """Explicitly passing tool_name=None (not just omitting it) must also raise --
    this case was NEVER broken (field_validator always runs on an explicitly
    supplied value, even if that value is None), but is tested here for
    completeness alongside the omitted-argument case above."""
    with pytest.raises(ValueError):
        PlanStep(step_type=StepType.TOOL_CALL, description="explicit none", tool_name=None)


def test_tool_call_step_with_tool_name_succeeds():
    step = PlanStep(step_type=StepType.TOOL_CALL, description="has tool", tool_name="my_tool")
    assert step.tool_name == "my_tool"


def test_reasoning_step_allows_omitted_tool_name():
    step = PlanStep(step_type=StepType.REASONING, description="just thinking")
    assert step.tool_name is None


def test_final_answer_step_allows_omitted_tool_name():
    step = PlanStep(step_type=StepType.FINAL_ANSWER, description="done")
    assert step.tool_name is None
