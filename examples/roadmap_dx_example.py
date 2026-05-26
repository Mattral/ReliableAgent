#!/usr/bin/env python3
"""The roadmap's own illustrative "Target Experience" DX example, made to
actually run -- line for line, with exactly one documented substitution
(model= is an Anthropic model name here, not a HuggingFace identifier;
see ReliableOrchestrator's docstring and adr/0008).

Run with:  python examples/roadmap_dx_example.py

Uses MockLLMClient throughout so this runs instantly, for free, with no
API key or network access required.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reliableagent import ReliableOrchestrator, ToolRegistry
from reliableagent.evaluation import EvaluationHarness
from reliableagent.guardrails import BasicGuardrail
from reliableagent.llm import MockLLMClient


def banner(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def my_search_tool(query: str) -> str:
    return f"(simulated) search results for: {query}"

def my_calculator_tool(expression: str) -> float:
    """A toy calculator tool standing in for a real one.

    Deliberately does NOT use eval() (even in a sandboxed globals dict):
    a portfolio example is exactly the wrong place to demonstrate that
    pattern, since it's easy to copy without noticing the safety
    implications. This uses Python's own `ast` module to parse the
    expression into a syntax tree and evaluates ONLY the small set of
    arithmetic node types explicitly allowed below -- anything else
    (attribute access, function calls, subscripts, etc.) raises.
    """
    import ast
    import operator

    allowed_operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
    }

    def _eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in allowed_operators:
            return allowed_operators[type(node.op)](_eval_node(node.left), _eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_operators:
            return allowed_operators[type(node.op)](_eval_node(node.operand))
        raise ValueError(f"Unsupported expression element: {ast.dump(node)}")

    parsed = ast.parse(expression, mode="eval")
    return _eval_node(parsed.body)


def main() -> None:
    banner("1. Define tools (clean and typed)")
    tools = ToolRegistry()
    tools.register(my_search_tool)
    tools.register(my_calculator_tool)
    print("Registered tools:", [spec.name for spec in tools.list_specs()])

    banner("2. Configure orchestrator with guardrails")
    scripted_plan = json.dumps({
        "reasoning_trace": "Search for recent developments, then summarize.",
        "confidence": 0.85,
        "steps": [
            {"step_type": "tool_call", "description": "search for speculative decoding developments",
             "tool_name": "my_search_tool", "tool_arguments": {"query": "speculative decoding recent developments"}},
            {"step_type": "final_answer", "description":
             "Recent speculative decoding research focuses on better draft-model "
             "selection and verification strategies to reduce wall-clock latency."},
        ],
    })

    orchestrator = ReliableOrchestrator(
        llm_client=MockLLMClient(responses=[scripted_plan]),  # swap for model="claude-sonnet-4-6" for a real model
        tools=tools,
        guardrails=[BasicGuardrail()],
        enable_checkpointing=True,
        enable_observability=True,
    )

    banner("3. Run with automatic reliability features")
    result = orchestrator.run(
        task="Research the latest developments in speculative decoding for LLM inference",
        max_steps=20,
    )
    print(f"\nFinal answer: {result.final_answer}")
    print(f"Metrics: {result.metrics}")

    banner("4. Get detailed observability")
    print(f"Trajectory has {len(result.trajectory.step_records)} step(s) on record.")
    print(f"Run can be resumed later via orchestrator.resume({result.run_id!r}).")
    orchestrator.shutdown()

    banner("5. Run the same setup through the Evaluation Harness")
    from reliableagent.core.orchestrator import Orchestrator
    from reliableagent.evaluation.factory import standard_guardrails
    from reliableagent.evaluation.golden_tools import build_golden_task_tools
    from reliableagent.planner import LLMPlanner, ThresholdCritic

    eval_orchestrator = Orchestrator(
        planner=LLMPlanner(MockLLMClient()),
        critic=ThresholdCritic(),
        tools=build_golden_task_tools(),
        # golden_suite_v1's guardrail-category tasks are specifically
        # authored against this exact guardrail stack (see
        # evaluation/factory.py's standard_guardrails()) -- a plain
        # [BasicGuardrail()] here would fail those tasks, since they
        # need ToolArgumentSanityGuardrail too.
        guardrails=standard_guardrails(),
    )
    try:
        harness = EvaluationHarness(orchestrator=eval_orchestrator)
        results = harness.evaluate(task_set="golden_suite_v1", seeds=[42, 43, 44])
        print(results.summary())
        print()
        print(results.failure_analysis())
    finally:
        eval_orchestrator.shutdown()


if __name__ == "__main__":
    main()
