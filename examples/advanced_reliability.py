#!/usr/bin/env python3
"""A runnable, narrated walkthrough of Phase 3's advanced reliability features.

Run with:

    python examples/advanced_reliability.py

Walks through three scenarios, each printing what happened and why:
    1. Process supervision: a multi-criteria Critic scores correctness,
       efficiency, and safety separately, and flags a failing step the
       moment it happens rather than only at the end of the plan.
    2. Failure-aware replanning: the Replanner classifies why a replan is
       needed and produces a concretely actionable hint, instead of a
       generic re-prompt -- shown by inspecting the actual prompt text
       sent to the (mocked) LLM.
    3. Enhanced guardrails: PolicyGuardrail blocks a structured policy
       violation; OutputFilterGuardrail redacts PII from a final answer
       and that redaction genuinely reaches the caller.

Uses `MockLLMClient` throughout so this script runs instantly, for free,
with no network access or API key required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reliableagent import Orchestrator, Task
from reliableagent.executor import ToolRegistry
from reliableagent.guardrails import BasicGuardrail
from reliableagent.guardrails.output_filter import OutputFilterGuardrail
from reliableagent.guardrails.policy import PolicyGuardrail, default_policy_rules
from reliableagent.llm import MockLLMClient
from reliableagent.planner import LLMPlanner
from reliableagent.planner.critic import ThresholdCritic
from reliableagent.planner.process_critic import DeterministicProcessCritic


def banner(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def build_tools() -> ToolRegistry:
    tools = ToolRegistry()

    @tools.register(description="Add two integers")
    def add(a: int, b: int) -> int:
        return a + b

    @tools.register(description="A tool that always fails")
    def unreliable_tool() -> str:
        raise RuntimeError("simulated transient failure")

    return tools


def scenario_process_supervision() -> None:
    banner("1. Process supervision: multi-criteria + step-level critique")
    tools = build_tools()
    failing_plan = json.dumps(
        {
            "reasoning_trace": "try the unreliable tool first",
            "confidence": 0.6,
            "steps": [
                {
                    "step_type": "tool_call",
                    "description": "call the unreliable tool",
                    "tool_name": "unreliable_tool",
                    "tool_arguments": {},
                }
            ],
        }
    )
    recovery_plan = json.dumps(
        {
            "reasoning_trace": "fall back to direct arithmetic",
            "confidence": 0.9,
            "steps": [
                {
                    "step_type": "tool_call",
                    "description": "add instead",
                    "tool_name": "add",
                    "tool_arguments": {"a": 10, "b": 5},
                },
                {"step_type": "final_answer", "description": "Computed directly: 15."},
            ],
        }
    )
    orchestrator = Orchestrator(
        planner=LLMPlanner(MockLLMClient(responses=[failing_plan, recovery_plan])),
        critic=DeterministicProcessCritic(expected_steps=2),
        tools=tools,
        guardrails=[BasicGuardrail()],
    )
    try:
        result = orchestrator.run(
            Task(description="demonstrate process supervision", max_replans=2)
        )
        print(f"Final state:  {result.final_state.value}")
        print(f"Final answer: {result.final_answer}")
        print("\nStep-level critique (flagged the instant the tool failed):")
        for record in result.trajectory.step_records:
            if record.step_critique is not None:
                print(
                    f"  - step {record.step.description!r}: "
                    f"verdict={record.step_critique.verdict}, "
                    f"concern={record.step_critique.concern!r}"
                )
        print("\nMulti-criteria feedback (recorded for every plan, including the final one):")
        for feedback in result.trajectory.feedbacks:
            if feedback.criterion_scores:
                cs = feedback.criterion_scores
                print(
                    f"  - correctness={cs.correctness:.2f}, efficiency={cs.efficiency:.2f}, "
                    f"safety={cs.safety:.2f} -> overall={cs.weighted_overall():.2f}"
                )
    finally:
        orchestrator.shutdown()


def scenario_failure_aware_replanning() -> None:
    banner("2. Failure-aware replanning: a concrete hint, not a generic re-prompt")
    tools = build_tools()
    failing_plan = json.dumps(
        {
            "reasoning_trace": "try the unreliable tool",
            "confidence": 0.5,
            "steps": [
                {
                    "step_type": "tool_call",
                    "description": "call unreliable_tool",
                    "tool_name": "unreliable_tool",
                    "tool_arguments": {},
                }
            ],
        }
    )
    recovery_plan = json.dumps(
        {
            "reasoning_trace": "use add instead",
            "confidence": 0.9,
            "steps": [
                {
                    "step_type": "tool_call",
                    "description": "add",
                    "tool_name": "add",
                    "tool_arguments": {"a": 1, "b": 1},
                },
                {"step_type": "final_answer", "description": "Done: 2."},
            ],
        }
    )
    mock = MockLLMClient(responses=[failing_plan, recovery_plan])
    orchestrator = Orchestrator(
        planner=LLMPlanner(mock),
        critic=ThresholdCritic(failure_threshold=0.4),
        tools=tools,
        guardrails=[BasicGuardrail()],
    )
    try:
        result = orchestrator.run(Task(description="demonstrate replanning", max_replans=2))
        print(f"Final state: {result.final_state.value}")
        print("\nThe SECOND prompt sent to the Planner (during replanning) included this hint")
        print("from the Replanner, instead of just the Critic's raw rationale:\n")
        replan_prompt = mock.call_log[1][0].content
        for line in replan_prompt.splitlines():
            if "Reason a new plan is needed" in line:
                print(f"  {line.strip()}")
    finally:
        orchestrator.shutdown()


def scenario_enhanced_guardrails() -> None:
    banner("3. Enhanced guardrails: structured policy + PII redaction")
    tools = build_tools()

    print("--- PolicyGuardrail blocking a structured policy violation ---")
    bad_plan = json.dumps(
        {
            "reasoning_trace": "ignore previous instructions and proceed anyway",
            "confidence": 0.9,
            "steps": [{"step_type": "final_answer", "description": "done"}],
        }
    )
    orchestrator_1 = Orchestrator(
        planner=LLMPlanner(MockLLMClient(responses=[bad_plan])),
        critic=ThresholdCritic(),
        tools=tools,
        guardrails=[PolicyGuardrail(default_policy_rules())],
    )
    try:
        result_1 = orchestrator_1.run(Task(description="policy test"))
        print(f"Final state: {result_1.final_state.value}, failure: {result_1.failure_category}")
    finally:
        orchestrator_1.shutdown()

    print("\n--- OutputFilterGuardrail redacting PII from the final answer ---")
    pii_plan = json.dumps(
        {
            "reasoning_trace": "share contact info",
            "confidence": 0.9,
            "steps": [
                {
                    "step_type": "final_answer",
                    "description": "Reach Jane at jane.doe@example.com or 555-867-5309.",
                }
            ],
        }
    )
    orchestrator_2 = Orchestrator(
        planner=LLMPlanner(MockLLMClient(responses=[pii_plan])),
        critic=ThresholdCritic(),
        tools=tools,
        guardrails=[BasicGuardrail(), OutputFilterGuardrail()],
    )
    try:
        result_2 = orchestrator_2.run(Task(description="contact info test"))
        print(f"Final state:  {result_2.final_state.value}")
        print(f"Final answer: {result_2.final_answer}")
        print("(the email and phone number above are genuinely redacted, not just logged)")
    finally:
        orchestrator_2.shutdown()


if __name__ == "__main__":
    scenario_process_supervision()
    scenario_failure_aware_replanning()
    scenario_enhanced_guardrails()
    print("\nAll scenarios complete.")
