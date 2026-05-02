"""Tests for ReliableOrchestrator, the convenience wrapper closing the audit-identified
gap between this project's API and the roadmap's illustrative DX example (adr/0008)."""
from __future__ import annotations
import json, tempfile
from reliableagent import ReliableOrchestrator
from reliableagent.core.enums import OrchestratorState
from reliableagent.core.models import Task
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.guardrails.basic import BasicGuardrail
from reliableagent.llm.mock import MockLLMClient
from reliableagent.memory.backend import FileMemoryBackend, InMemoryBackend
from reliableagent.observability.sinks import ConsoleSink, InMemorySink


def _tools():
    t = ToolRegistry()
    @t.register
    def add(a: int, b: int) -> int:
        return a + b
    return t

def _plan():
    return json.dumps({"reasoning_trace":"add","confidence":0.9,"steps":[
        {"step_type":"tool_call","description":"add","tool_name":"add","tool_arguments":{"a":2,"b":3}},
        {"step_type":"final_answer","description":"The sum is 5."}]})


def test_run_with_string_task_matches_roadmap_dx():
    orch = ReliableOrchestrator(tools=_tools(), guardrails=[BasicGuardrail()],
                                llm_client=MockLLMClient(responses=[_plan()]),
                                enable_checkpointing=True, enable_observability=True)
    try:
        r = orch.run(task="Add 2 and 3", max_steps=20)
        assert r.final_state == OrchestratorState.COMPLETED
        assert r.final_answer == "The sum is 5."
    finally:
        orch.shutdown()

def test_run_with_prebuilt_task_object():
    orch = ReliableOrchestrator(tools=_tools(), llm_client=MockLLMClient(responses=[_plan()]))
    try:
        r = orch.run(Task(description="Add 2 and 3", max_steps=10, max_replans=1))
        assert r.final_state == OrchestratorState.COMPLETED
    finally:
        orch.shutdown()

def test_checkpointing_false_uses_in_memory_backend():
    orch = ReliableOrchestrator(tools=_tools(), llm_client=MockLLMClient(responses=[_plan()]),
                                enable_checkpointing=False)
    try:
        assert isinstance(orch._orchestrator.memory, InMemoryBackend)
    finally:
        orch.shutdown()

def test_checkpointing_true_uses_file_backend():
    with tempfile.TemporaryDirectory() as tmp:
        orch = ReliableOrchestrator(tools=_tools(), llm_client=MockLLMClient(responses=[_plan()]),
                                    enable_checkpointing=True, checkpoint_dir=tmp)
        try:
            assert isinstance(orch._orchestrator.memory, FileMemoryBackend)
        finally:
            orch.shutdown()

def test_observability_false_uses_in_memory_sink():
    orch = ReliableOrchestrator(tools=_tools(), llm_client=MockLLMClient(responses=[_plan()]),
                                enable_observability=False)
    try:
        assert isinstance(orch._orchestrator.sink, InMemorySink)
    finally:
        orch.shutdown()

def test_observability_true_uses_console_sink():
    orch = ReliableOrchestrator(tools=_tools(), llm_client=MockLLMClient(responses=[_plan()]),
                                enable_observability=True)
    try:
        assert isinstance(orch._orchestrator.sink, ConsoleSink)
    finally:
        orch.shutdown()

def test_no_model_no_client_defaults_to_mock():
    orch = ReliableOrchestrator(tools=_tools())
    try:
        assert isinstance(orch._orchestrator.planner._llm_client, MockLLMClient)
    finally:
        orch.shutdown()

def test_explicit_client_takes_precedence_over_model():
    client = MockLLMClient(responses=[_plan()])
    orch = ReliableOrchestrator(tools=_tools(), model="claude-sonnet-4-6", llm_client=client)
    try:
        assert orch._orchestrator.planner._llm_client is client
    finally:
        orch.shutdown()

def test_resume_delegates_correctly():
    with tempfile.TemporaryDirectory() as tmp:
        orch = ReliableOrchestrator(tools=_tools(), llm_client=MockLLMClient(responses=[_plan()]),
                                    enable_checkpointing=True, checkpoint_dir=tmp)
        try:
            r = orch.run(task="Add 2 and 3")
            run_id = r.run_id
            orch2 = ReliableOrchestrator(tools=_tools(), llm_client=MockLLMClient(),
                                         enable_checkpointing=True, checkpoint_dir=tmp)
            try:
                resumed = orch2.resume(run_id)
                assert resumed.final_state == OrchestratorState.COMPLETED
                assert resumed.final_answer == "The sum is 5."
            finally:
                orch2.shutdown()
        finally:
            orch.shutdown()

def test_context_manager_shuts_down_automatically():
    with ReliableOrchestrator(tools=_tools(), llm_client=MockLLMClient(responses=[_plan()])) as orch:
        r = orch.run(task="Add 2 and 3")
        assert r.final_state == OrchestratorState.COMPLETED
