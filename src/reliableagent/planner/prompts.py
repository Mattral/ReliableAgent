"""Shared prompt-construction helpers for LLM-backed Planner and Critic.

Centralizing prompt assembly here (rather than inlining f-strings in
`llm_planner.py` / `llm_critic.py`) keeps the actual prompt text
reviewable and testable in one place, and makes it easy to plug in
versioned prompt management later.
"""

from __future__ import annotations

import json

from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.core.models import PlanStep, ToolResult

PLANNER_SYSTEM_PROMPT = """You are the Planner component of an autonomous agent framework.
Given a task and a list of available tools, produce a step-by-step plan to \
accomplish the task.

You MUST respond with ONLY a single JSON object (no markdown fences, no \
commentary before or after) matching exactly this schema:

{
  "reasoning_trace": "<your reasoning about how to approach this task>",
  "confidence": <float between 0.0 and 1.0>,
  "steps": [
    {
      "step_type": "tool_call" | "reasoning" | "final_answer",
      "description": "<what this step does>",
      "tool_name": "<tool name, required only if step_type is tool_call>",
      "tool_arguments": {<arguments dict, required only if step_type is tool_call>},
      "rationale": "<why this step is needed>"
    }
  ]
}

Rules:
- The final step of every plan must have step_type "final_answer".
- Only use tool names from the provided tool list.
- Keep the plan as short as possible while still accomplishing the task.
- Respond with ONLY the JSON object described above."""


def build_tools_description(tools: ToolRegistry) -> str:
    """Render the available tools as a compact, prompt-friendly block."""
    specs = tools.list_specs()
    if not specs:
        return "(No tools are available. Produce a plan using only reasoning/final_answer steps.)"
    lines = []
    for spec in specs:
        schema = spec.to_prompt_schema()
        arg_str = ", ".join(schema["arguments"]) if schema["arguments"] else "none"
        lines.append(f"- {schema['name']}: {schema['description']} (arguments: {arg_str})")
    return "\n".join(lines)


def build_planner_user_prompt(
    task_description: str,
    tools: ToolRegistry,
    *,
    prior_results: list[ToolResult] | None = None,
    replan_attempt: int = 0,
    feedback_reason: str | None = None,
) -> str:
    """Construct the user-turn prompt sent to the Planner's LLM."""
    sections = [f"Task: {task_description}", "", "Available tools:", build_tools_description(tools)]

    if replan_attempt > 0:
        sections.append("")
        sections.append(f"This is replan attempt #{replan_attempt}.")
        if feedback_reason:
            sections.append(f"Reason a new plan is needed: {feedback_reason}")
        if prior_results:
            sections.append("Results so far:")
            for r in prior_results:
                status = "succeeded" if r.success else "FAILED"
                sections.append(f"  - call {r.call_id}: {status} -> {r.output or r.error}")

    sections.append("")
    sections.append("Respond with the JSON plan now.")
    return "\n".join(sections)


CRITIC_SYSTEM_PROMPT = """You are the Critic component of an autonomous agent framework.
Given a plan and the results of executing it so far, assess whether the \
trajectory is on track to satisfy the task, or whether a new plan is needed.

You MUST respond with ONLY a single JSON object (no markdown fences, no \
commentary) matching exactly this schema:

{
  "quality_score": <float between 0.0 and 1.0, how well things are going>,
  "should_replan": <true or false>,
  "issues": ["<short description of any problems found>", ...],
  "rationale": "<brief explanation of your assessment>"
}"""


def build_critic_user_prompt(
    task_description: str, plan_summary: str, results: list[ToolResult]
) -> str:
    """Construct the user-turn prompt sent to the Critic's LLM."""
    results_lines = []
    for r in results:
        status = "succeeded" if r.success else "FAILED"
        results_lines.append(f"  - {status}: {r.output if r.success else r.error}")
    results_block = "\n".join(results_lines) if results_lines else "(no steps executed yet)"

    return (
        f"Task: {task_description}\n\n"
        f"Plan:\n{plan_summary}\n\n"
        f"Execution results so far:\n{results_block}\n\n"
        "Respond with the JSON assessment now."
    )


PROCESS_CRITIC_SYSTEM_PROMPT = """You are the Process-Supervision Critic component of an \
autonomous agent framework. Given a plan and the results of executing it so far, assess \
the trajectory along THREE separate criteria, rather than a single overall score.

You MUST respond with ONLY a single JSON object (no markdown fences, no \
commentary) matching exactly this schema:

{
  "correctness": <float 0.0-1.0, did this achieve what it should have?>,
  "efficiency": <float 0.0-1.0, was this accomplished without excess steps/waste?>,
  "safety": <float 0.0-1.0, did this stay clear of policy/safety concerns?>,
  "should_replan": <true or false>,
  "issues": ["<short description of any problems found>", ...],
  "rationale": "<brief explanation of your assessment, addressing all three criteria>"
}"""


def build_process_critic_user_prompt(
    task_description: str, plan_summary: str, results: list[ToolResult]
) -> str:
    """Construct the user-turn prompt sent to the process-supervision Critic's LLM."""
    results_lines = []
    for r in results:
        status = "succeeded" if r.success else "FAILED"
        results_lines.append(f"  - {status}: {r.output if r.success else r.error}")
    results_block = "\n".join(results_lines) if results_lines else "(no steps executed yet)"

    return (
        f"Task: {task_description}\n\n"
        f"Plan:\n{plan_summary}\n\n"
        f"Execution results so far:\n{results_block}\n\n"
        "Assess correctness, efficiency, and safety separately, then respond with the "
        "JSON assessment now."
    )


STEP_CRITIQUE_SYSTEM_PROMPT = """You are performing process supervision: assessing a single \
step of an autonomous agent's plan immediately after it completed, in isolation.

You MUST respond with ONLY a single JSON object (no markdown fences, no \
commentary) matching exactly this schema:

{
  "verdict": <true if this step looks acceptable on its own, false if it raises a concern>,
  "concern": "<specific issue noticed, empty string if verdict is true>"
}"""


def build_step_critique_user_prompt(step: PlanStep, result: ToolResult) -> str:
    """Construct the user-turn prompt sent to the step-level critique LLM call."""
    status = "succeeded" if result.success else "FAILED"
    outcome = result.output if result.success else result.error
    return (
        f"Step: [{step.step_type.value}] {step.description}\n"
        f"Outcome: {status} -> {outcome}\n\n"
        "Respond with the JSON verdict now."
    )


def safe_json_loads(text: str) -> dict:
    """Parse `text` as JSON, stripping common LLM-added markdown fences first.

    LLMs frequently wrap JSON in ```json ... ``` fences even when
    explicitly told not to; stripping them here means the parsing
    layer is robust to that without weakening the prompt's
    instructions or adding retry round-trips for a purely cosmetic
    issue.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return json.loads(stripped)
