"""Tests for EvaluationHarness, closing the audit-identified gap between
EvaluationRunner and the roadmap's illustrative harness.evaluate() DX (adr/0008)."""
from __future__ import annotations
import json
from reliableagent.core.models import Task
from reliableagent.core.orchestrator import Orchestrator
from reliableagent.evaluation.factory import standard_guardrails
from reliableagent.evaluation.golden_task import GoldenTask, exact_match_grader
from reliableagent.evaluation.golden_tools import build_golden_task_tools
from reliableagent.evaluation.harness import EvaluationHarness, get_task_set, register_task_set
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.guardrails.basic import BasicGuardrail
from reliableagent.llm.base import LLMResponse
from reliableagent.llm.mock import MockLLMClient
from reliableagent.planner.critic import ThresholdCritic
from reliableagent.planner.llm_planner import LLMPlanner


class _FakeRealClient:
    def __init__(self, response_text: str) -> None:
        self.call_count = 0
        self._response_text = response_text
    def complete(self, messages, *, system=None, max_tokens=1024, temperature=0.0, seed=None):
        self.call_count += 1
        return LLMResponse(text=self._response_text, model="fake-real-model")


def test_golden_suite_v1_registered_by_default():
    assert len(get_task_set("golden_suite_v1")) == 20

def test_unknown_task_set_raises_helpful_error():
    raised = False
    try:
        get_task_set("nonexistent_xyz")
    except KeyError as exc:
        raised = True
        assert "nonexistent_xyz" in str(exc) and "golden_suite_v1" in str(exc)
    assert raised

def test_register_task_set_adds_retrievable_set():
    tasks = [GoldenTask(task_id="c1", category="c", build_task=lambda: Task(description="q"),
                        grade=exact_match_grader("a"))]
    register_task_set("custom_set_for_test", tasks)
    assert get_task_set("custom_set_for_test") == tasks

def test_mock_backed_matches_canonical_golden_suite_results():
    tools = build_golden_task_tools()
    orch = Orchestrator(planner=LLMPlanner(MockLLMClient()), critic=ThresholdCritic(),
                        tools=tools, guardrails=standard_guardrails())
    try:
        harness = EvaluationHarness(orchestrator=orch)
        results = harness.evaluate(task_set="golden_suite_v1", seeds=[0])
        assert results.report.metrics.task_success_rate == 1.0
        assert len(results.graded_runs) == 20
    finally:
        orch.shutdown()

def test_mock_backed_does_not_shutdown_caller_owned_orchestrator():
    tools = build_golden_task_tools()
    orch = Orchestrator(planner=LLMPlanner(MockLLMClient()), critic=ThresholdCritic(),
                        tools=tools, guardrails=standard_guardrails())
    try:
        harness = EvaluationHarness(orchestrator=orch)
        harness.evaluate(task_set="golden_suite_v1", seeds=[0])
        plan = json.dumps({"reasoning_trace":"x","confidence":0.9,"steps":[{"step_type":"final_answer","description":"still alive"}]})
        orch._planner = LLMPlanner(MockLLMClient(responses=[plan]))
        r = orch.run(Task(description="post-harness check"))
        assert r.final_answer == "still alive"
    finally:
        orch.shutdown()

def test_real_llm_backed_reuses_same_orchestrator():
    fake_response = json.dumps({"reasoning_trace":"x","confidence":0.9,"steps":[{"step_type":"final_answer","description":"always 42"}]})
    fake = _FakeRealClient(fake_response)
    orch = Orchestrator(planner=LLMPlanner(fake), critic=ThresholdCritic(),
                        tools=ToolRegistry(), guardrails=[BasicGuardrail()])
    try:
        register_task_set("tiny_real_model_test", [
            GoldenTask(task_id="t1", category="c", build_task=lambda: Task(description="q1"), grade=exact_match_grader("always 42")),
            GoldenTask(task_id="t2", category="c", build_task=lambda: Task(description="q2"), grade=exact_match_grader("always 42")),
        ])
        harness = EvaluationHarness(orchestrator=orch)
        results = harness.evaluate(task_set="tiny_real_model_test", seeds=[0, 1])
        assert results.report.metrics.task_success_rate == 1.0
        assert len(results.graded_runs) == 4
        assert fake.call_count == 4
    finally:
        orch.shutdown()

def test_summary_and_failure_analysis_return_text():
    tools = build_golden_task_tools()
    orch = Orchestrator(planner=LLMPlanner(MockLLMClient()), critic=ThresholdCritic(),
                        tools=tools, guardrails=standard_guardrails())
    try:
        harness = EvaluationHarness(orchestrator=orch)
        results = harness.evaluate(task_set="golden_suite_v1", seeds=[0])
        assert "Task Success Rate" in results.summary()
        assert "Failure Analysis Report" in results.failure_analysis()
    finally:
        orch.shutdown()


def test_mock_backed_handles_multiple_seeds_without_exhausting_mock_queue():
    """Regression test for a real bug found while building examples/roadmap_dx_example.py:
    the mock-backed path originally built ONE scripted Orchestrator per TASK and reused it
    across all seeds, exhausting MockLLMClient's finite response queue after the first seed
    and silently failing every subsequent seed with a spurious planning error. Fixed by
    building a fresh scripted Orchestrator per (task, seed) pair."""
    tools = build_golden_task_tools()
    orch = Orchestrator(planner=LLMPlanner(MockLLMClient()), critic=ThresholdCritic(),
                        tools=tools, guardrails=standard_guardrails())
    try:
        harness = EvaluationHarness(orchestrator=orch)
        # 3 seeds is the minimum that would have exposed the original bug
        # (seed 1 was fine since it was the FIRST run against each scripted
        # orchestrator; seeds 2+ would fail under the old, buggy code).
        results = harness.evaluate(task_set="golden_suite_v1", seeds=[10, 11, 12])
        assert results.report.metrics.task_success_rate == 1.0, results.failure_analysis()
        assert len(results.graded_runs) == 60  # 20 tasks * 3 seeds
    finally:
        orch.shutdown()
