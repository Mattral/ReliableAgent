"""Unit tests for tool output validation (result_validator on ToolSpec/ToolRegistry/Executor)."""

from __future__ import annotations

from reliableagent.core.models import ToolCall
from reliableagent.executor import Executor, ToolRegistry


def test_no_validator_trusts_any_output():
    reg = ToolRegistry()
    reg.register(lambda: -999, name="t", description="")
    assert reg.validate_result("t", -999) is True

def test_validator_rejects_bad_output():
    reg = ToolRegistry()
    reg.register(lambda: None, name="t", description="", result_validator=lambda r: r >= 0)
    assert reg.validate_result("t", 5.0) is True
    assert reg.validate_result("t", -1.0) is False

def test_crashing_validator_returns_false():
    reg = ToolRegistry()
    reg.register(lambda: None, name="t", description="",
                 result_validator=lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
    assert reg.validate_result("t", "anything") is False

def test_executor_trusts_output_with_no_validator():
    reg = ToolRegistry()
    reg.register(lambda: 42, name="t", description="")
    ex = Executor(reg, max_retries=0)
    r = ex.execute(ToolCall(step_id="s1", tool_name="t", arguments={}))
    assert r.success is True and r.validated is True and r.output == 42
    ex.shutdown()

def test_executor_fails_on_invalid_output():
    reg = ToolRegistry()
    reg.register(lambda: -5.0, name="t", description="", result_validator=lambda r: r >= 0)
    ex = Executor(reg, max_retries=0)
    r = ex.execute(ToolCall(step_id="s1", tool_name="t", arguments={}))
    assert r.success is False and "output validator" in r.error
    ex.shutdown()

def test_executor_retries_failed_validation():
    reg = ToolRegistry()
    calls = {"n": 0}
    def sometimes_bad():
        calls["n"] += 1
        return -1.0 if calls["n"] == 1 else 5.0
    reg.register(sometimes_bad, name="t", description="", result_validator=lambda r: r >= 0)
    ex = Executor(reg, max_retries=1, retry_backoff_seconds=0.01)
    r = ex.execute(ToolCall(step_id="s1", tool_name="t", arguments={}))
    assert r.success is True and r.output == 5.0 and calls["n"] == 2
    ex.shutdown()
