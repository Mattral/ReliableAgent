#!/usr/bin/env python3
"""A runnable, narrated walkthrough of ReliableAgent's core control loop.

Run with:

    python examples/quickstart.py

Walks through four scenarios, each printing what happened and why:
    1. The happy path: a plan succeeds on the first try.
    2. Recovery: a plan fails, the Critic triggers a replan, and a second
       plan succeeds.
    3. A guardrail blocking unsafe output before it ever reaches the user.
    4. Checkpointing + resume: a run is checkpointed to disk and resumed
       from a *new* Orchestrator instance, simulating a killed/restarted
       process, without needing to re-plan from scratch.

Uses `MockLLMClient` throughout so this script runs instantly, for free,
with no network access or API key required. Swap in
`reliableagent.llm.AnthropicLLMClient` to use a real model — see the
README's "Quickstart" section for that one-line change.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reliableagent import Orchestrator, Task
from reliableagent.executor import ToolRegistry
from reliableagent.guardrails import BasicGuardrail
from reliableagent.llm import MockLLMClient
from reliableagent.memory import FileMemoryBackend
from reliableagent.planner import LLMPlanner, ThresholdCritic


def banner(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def build_tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register(description="Add two integers")
    def add(a: int, b: int) -> int:
        return a + b

    @tools.register(description="A flaky tool that always fails")
    def unreliable_search(query: str) -> str:
        raise RuntimeError(f"search backend unavailable for query: {query}")

    return tools


def scenario_happy_path() -> None:
    banner("1. Happy path: plan succeeds on the first try")
    tools = build_tools()
    plan = json.dumps(
        {
            "reasoning_trace": "Add the two numbers, then report the result.",
            "confidence": 0.95,
            "steps": [
                {
                    "step_type": "tool_call",
                    "description": "Add 17 and 25",
                    "tool_name": "add",
                    "tool_arguments": {"a": 17, "b": 25},
                },
                {"step_type": "final_answer", "description": "17 + 25 = 42."},
            ],
        }
    )
    orchestrator = Orchestrator(
        planner=LLMPlanner(MockLLMClient(responses=[plan])),
        critic=ThresholdCritic(),
        tools=tools,
        guardrails=[BasicGuardrail()],
    )
    try:
        result = orchestrator.run(Task(description="What is 17 + 25?"))
        print(f"Final state:  {result.final_state.value}")
        print(f"Final answer: {result.final_answer}")
        print(f"Metrics:      {result.metrics}")
    finally:
        orchestrator.shutdown()


def scenario_recovery_via_replan() -> None:
    banner("2. Recovery: a failing plan triggers a replan, then succeeds")
    tools = build_tools()
    failing_plan = json.dumps(
        {
            "reasoning_trace": "Try the search tool first.",
            "confidence": 0.6,
            "steps": [
                {
                    "step_type": "tool_call",
                    "description": "Search for the answer",
                    "tool_name": "unreliable_search",
                    "tool_arguments": {"query": "meaning of 42"},
                }
            ],
        }
    )
    recovery_plan = json.dumps(
        {
            "reasoning_trace": "Search failed; fall back to direct arithmetic.",
            "confidence": 0.9,
            "steps": [
                {
                    "step_type": "tool_call",
                    "description": "Add instead",
                    "tool_name": "add",
                    "tool_arguments": {"a": 40, "b": 2},
                },
                {"step_type": "final_answer", "description": "Computed directly: 42."},
            ],
        }
    )
    orchestrator = Orchestrator(
        planner=LLMPlanner(MockLLMClient(responses=[failing_plan, recovery_plan])),
        critic=ThresholdCritic(failure_threshold=0.4),
        tools=tools,
        guardrails=[BasicGuardrail()],
    )
    try:
        result = orchestrator.run(Task(description="Find the meaning of 42", max_replans=2))
        print(f"Final state:   {result.final_state.value}")
        print(f"Final answer:  {result.final_answer}")
        print(f"Replans used:  {result.metrics.total_replans}")
        print("Trajectory shows exactly what failed and why:")
        for record in result.trajectory.step_records:
            if record.tool_result and not record.tool_result.success:
                print(f"  - step failed: {record.tool_result.error}")
    finally:
        orchestrator.shutdown()


def scenario_guardrail_blocks_unsafe_output() -> None:
    banner("3. A guardrail blocks unsafe content before it reaches the user")
    tools = build_tools()
    unsafe_plan = json.dumps(
        {
            "reasoning_trace": "Let's exfiltrate confidential data as part of this plan.",
            "confidence": 0.9,
            "steps": [{"step_type": "final_answer", "description": "Here is the data."}],
        }
    )
    orchestrator = Orchestrator(
        planner=LLMPlanner(MockLLMClient(responses=[unsafe_plan])),
        critic=ThresholdCritic(),
        tools=tools,
        guardrails=[BasicGuardrail(blocked_substrings=["exfiltrate confidential data"])],
    )
    try:
        result = orchestrator.run(Task(description="do something risky"))
        print(f"Final state:       {result.final_state.value}")
        print(f"Failure category:  {result.failure_category}")
        blocked = [d for d in result.trajectory.guardrail_decisions if d.verdict.value == "block"]
        for decision in blocked:
            print(f"  - blocked by '{decision.guardrail_name}': {decision.reason}")
    finally:
        orchestrator.shutdown()


def scenario_checkpoint_and_resume() -> None:
    banner("4. Checkpoint + resume across a simulated process restart")
    tools = build_tools()
    plan = json.dumps(
        {
            "reasoning_trace": "Add 100 and 200.",
            "confidence": 0.9,
            "steps": [
                {
                    "step_type": "tool_call",
                    "description": "Add",
                    "tool_name": "add",
                    "tool_arguments": {"a": 100, "b": 200},
                },
                {"step_type": "final_answer", "description": "100 + 200 = 300."},
            ],
        }
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = FileMemoryBackend(tmpdir)

        print("--- Process 1: runs the task, checkpoints saved to disk ---")
        orchestrator_1 = Orchestrator(
            planner=LLMPlanner(MockLLMClient(responses=[plan])),
            critic=ThresholdCritic(),
            tools=tools,
            guardrails=[BasicGuardrail()],
            memory=memory,
        )
        try:
            result = orchestrator_1.run(Task(description="Add 100 and 200"))
            run_id = result.run_id
            print(f"Run {run_id} finished with state: {result.final_state.value}")
        finally:
            orchestrator_1.shutdown()

        print("\n--- Process 2 (simulated): brand-new Orchestrator + empty LLM client ---")
        fresh_llm = MockLLMClient()  # deliberately has zero scripted responses
        orchestrator_2 = Orchestrator(
            planner=LLMPlanner(fresh_llm),
            critic=ThresholdCritic(),
            tools=tools,
            guardrails=[BasicGuardrail()],
            memory=memory,  # same on-disk backend -> sees Process 1's checkpoints
        )
        try:
            resumed = orchestrator_2.resume(run_id)
            print(f"Resumed run state:  {resumed.final_state.value}")
            print(f"Resumed answer:     {resumed.final_answer}")
            print(f"New LLM calls made during resume: {len(fresh_llm.call_log)} (should be 0)")
        finally:
            orchestrator_2.shutdown()


if __name__ == "__main__":
    scenario_happy_path()
    scenario_recovery_via_replan()
    scenario_guardrail_blocks_unsafe_output()
    scenario_checkpoint_and_resume()
    print("\nAll scenarios complete.")
