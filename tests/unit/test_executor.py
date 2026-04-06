"""Unit tests for `reliableagent.executor`."""

from __future__ import annotations

import time

import pytest

from reliableagent.core.models import ToolCall
from reliableagent.exceptions import ToolArgumentValidationError, ToolNotFoundError
from reliableagent.executor.executor import Executor
from reliableagent.executor.tool_registry import ToolRegistry


def test_register_and_get_tool():
    registry = ToolRegistry()

    @registry.register(description="adds")
    def add(a: int, b: int) -> int:
        return a + b

    spec = registry.get("add")
    assert spec.name == "add"
    assert spec.description == "adds"


def test_get_missing_tool_raises():
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        registry.get("missing")


def test_register_duplicate_name_raises():
    registry = ToolRegistry()
    registry.register(lambda: None, name="dup", description="first")
    with pytest.raises(ValueError):
        registry.register(lambda: None, name="dup", description="second")


def test_contains_and_len():
    registry = ToolRegistry()
    registry.register(lambda: None, name="t1", description="")
    assert "t1" in registry
    assert "t2" not in registry
    assert len(registry) == 1


def test_to_prompt_schema_lists_argument_names():
    registry = ToolRegistry()

    @registry.register(description="adds two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    schema = registry.get("add").to_prompt_schema()
    assert schema["name"] == "add"
    assert "description" in schema


def test_executor_runs_successful_tool():
    registry = ToolRegistry()
    registry.register(lambda a, b: a + b, name="add", description="adds")
    executor = Executor(registry, max_retries=0)
    result = executor.execute(ToolCall(step_id="s1", tool_name="add", arguments={"a": 2, "b": 3}))
    assert result.success is True
    assert result.output == 5
    executor.shutdown()


def test_executor_captures_tool_exception_as_failed_result():
    registry = ToolRegistry()

    def boom():
        raise RuntimeError("kaboom")

    registry.register(boom, name="boom", description="")
    executor = Executor(registry, max_retries=0)
    result = executor.execute(ToolCall(step_id="s1", tool_name="boom", arguments={}))
    assert result.success is False
    assert "kaboom" in result.error
    executor.shutdown()


def test_executor_enforces_timeout():
    registry = ToolRegistry()

    def slow(seconds: float) -> str:
        time.sleep(seconds)
        return "done"

    registry.register(slow, name="slow", description="")
    executor = Executor(registry, max_retries=0)
    call = ToolCall(step_id="s1", tool_name="slow", arguments={"seconds": 1.0}, timeout_seconds=0.1)
    result = executor.execute(call)
    assert result.success is False
    assert "timed out" in result.error
    executor.shutdown()


def test_executor_returns_failure_for_missing_tool_without_raising():
    registry = ToolRegistry()
    executor = Executor(registry, max_retries=0)
    result = executor.execute(ToolCall(step_id="s1", tool_name="nonexistent", arguments={}))
    assert result.success is False
    assert "nonexistent" in result.error
    executor.shutdown()


def test_executor_retries_before_giving_up():
    registry = ToolRegistry()
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        raise RuntimeError("transient")

    registry.register(flaky, name="flaky", description="")
    executor = Executor(registry, max_retries=2, retry_backoff_seconds=0.01)
    result = executor.execute(ToolCall(step_id="s1", tool_name="flaky", arguments={}))
    assert result.success is False
    assert attempts["count"] == 3  # initial attempt + 2 retries
    executor.shutdown()
