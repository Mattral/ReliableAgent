"""Unit tests for LLMUsageStats and UsageTrackingLLMClient."""

from __future__ import annotations

import json

from reliableagent import Orchestrator, Task
from reliableagent.executor import ToolRegistry
from reliableagent.guardrails import BasicGuardrail
from reliableagent.llm import MockLLMClient
from reliableagent.llm.base import LLMMessage, LLMResponse
from reliableagent.llm.usage import LLMUsageStats, UsageTrackingLLMClient
from reliableagent.planner import LLMPlanner, ThresholdCritic


def test_stats_start_at_zero():
    stats = LLMUsageStats()
    assert stats.total_calls == 0
    assert stats.total_tokens == 0
    assert stats.average_latency_seconds == 0.0


def test_record_accumulates():
    stats = LLMUsageStats()
    response = LLMResponse(text="x", model="m", input_tokens=10, output_tokens=5)
    stats.record(response, 0.1)
    stats.record(response, 0.2)
    assert stats.total_calls == 2
    assert stats.total_tokens == 30
    assert abs(stats.total_latency_seconds - 0.3) < 1e-9


def test_snapshot_is_independent():
    stats = LLMUsageStats()
    response = LLMResponse(text="x", model="m", input_tokens=1, output_tokens=1)
    stats.record(response, 0.1)
    snapshot = stats.snapshot()
    stats.record(response, 0.1)
    assert stats.total_calls == 2
    assert snapshot.total_calls == 1


def test_tracking_client_delegates_and_records():
    stats = LLMUsageStats()
    tracked = UsageTrackingLLMClient(MockLLMClient(responses=["hi"]), stats)
    response = tracked.complete([LLMMessage(role="user", content="hello")])
    assert response.text == "hi"
    assert stats.total_calls == 1


def _make_plan() -> str:
    return json.dumps(
        {
            "reasoning_trace": "x",
            "confidence": 0.9,
            "steps": [{"step_type": "final_answer", "description": "done"}],
        }
    )


def test_per_run_delta_isolation():
    """Multiple runs on the same Orchestrator must report isolated per-run
    usage, not the tracker's ever-growing lifetime total."""
    stats = LLMUsageStats()
    tracked = UsageTrackingLLMClient(MockLLMClient(responses=[_make_plan(), _make_plan()]), stats)
    orchestrator = Orchestrator(
        planner=LLMPlanner(tracked),
        critic=ThresholdCritic(),
        tools=ToolRegistry(),
        guardrails=[BasicGuardrail()],
        usage_tracker=stats,
    )
    try:
        result_1 = orchestrator.run(Task(description="run1"))
        result_2 = orchestrator.run(Task(description="run2"))
        assert result_1.metrics.total_llm_calls == 1
        assert result_2.metrics.total_llm_calls == 1
        assert stats.total_calls == 2
        assert stats.total_tokens == result_1.metrics.total_tokens + result_2.metrics.total_tokens
    finally:
        orchestrator.shutdown()
