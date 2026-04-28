"""Unit tests for LLMUsageStats and UsageTrackingLLMClient."""
from __future__ import annotations
from reliableagent.llm.base import LLMMessage, LLMResponse
from reliableagent.llm.mock import MockLLMClient
from reliableagent.llm.usage import LLMUsageStats, UsageTrackingLLMClient


def test_stats_start_at_zero():
    s = LLMUsageStats()
    assert s.total_calls == 0 and s.total_tokens == 0 and s.average_latency_seconds == 0.0

def test_record_accumulates():
    s = LLMUsageStats()
    r = LLMResponse(text="x", model="m", input_tokens=10, output_tokens=5)
    s.record(r, 0.1); s.record(r, 0.2)
    assert s.total_calls == 2 and s.total_tokens == 30
    assert abs(s.total_latency_seconds - 0.3) < 1e-9

def test_snapshot_is_independent():
    s = LLMUsageStats()
    r = LLMResponse(text="x", model="m", input_tokens=1, output_tokens=1)
    s.record(r, 0.1)
    snap = s.snapshot()
    s.record(r, 0.1)
    assert s.total_calls == 2 and snap.total_calls == 1

def test_tracking_client_delegates_and_records():
    s = LLMUsageStats()
    tracked = UsageTrackingLLMClient(MockLLMClient(responses=["hi"]), s)
    resp = tracked.complete([LLMMessage(role="user", content="hello")])
    assert resp.text == "hi" and s.total_calls == 1

def test_per_run_delta_isolation():
    """Multiple runs on same Orchestrator must report isolated per-run usage, not lifetime total."""
    import json
    from reliableagent import Orchestrator, Task
    from reliableagent.llm import MockLLMClient
    from reliableagent.planner import LLMPlanner, ThresholdCritic
    from reliableagent.executor import ToolRegistry
    from reliableagent.guardrails import BasicGuardrail
    def plan(): return json.dumps({"reasoning_trace":"x","confidence":0.9,"steps":[{"step_type":"final_answer","description":"done"}]})
    s = LLMUsageStats()
    tracked = UsageTrackingLLMClient(MockLLMClient(responses=[plan(), plan()]), s)
    orch = Orchestrator(planner=LLMPlanner(tracked), critic=ThresholdCritic(),
                        tools=ToolRegistry(), guardrails=[BasicGuardrail()], usage_tracker=s)
    try:
        r1 = orch.run(Task(description="run1"))
        r2 = orch.run(Task(description="run2"))
        assert r1.metrics.total_llm_calls == 1 and r2.metrics.total_llm_calls == 1
        assert s.total_calls == 2
        assert s.total_tokens == r1.metrics.total_tokens + r2.metrics.total_tokens
    finally:
        orch.shutdown()
