"""Shared enumerations used across ReliableAgent's data models.

Keeping these in one module avoids circular imports between
`models.py`, the orchestrator's state machine, and the observability
layer, all of which need to reference the same vocabulary of statuses.
"""

from __future__ import annotations

from enum import Enum


class OrchestratorState(str, Enum):
    """States in the Orchestrator's run-level state machine.

    Transitions are intentionally restricted (see
    `orchestrator.STATE_TRANSITIONS`) so that illegal jumps (e.g.
    COMPLETED -> EXECUTING) are caught immediately rather than silently
    corrupting a trajectory.
    """

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    CRITIQUING = "critiquing"
    REPLANNING = "replanning"
    COMPLETED = "completed"
    FAILED = "failed"


class StepStatus(str, Enum):
    """Outcome status of a single executed plan step."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED_BY_GUARDRAIL = "blocked_by_guardrail"


class StepType(str, Enum):
    """The kind of action a plan step represents."""

    TOOL_CALL = "tool_call"
    REASONING = "reasoning"
    FINAL_ANSWER = "final_answer"


class GuardrailBoundary(str, Enum):
    """The architectural boundary at which a guardrail is evaluated.

    Mirrors section 3.2 of the roadmap: guardrails run at the input to
    the Planner, the output from the Planner, on tool results, and on
    the final output.
    """

    PLANNER_INPUT = "planner_input"
    PLANNER_OUTPUT = "planner_output"
    TOOL_INPUT = "tool_input"
    TOOL_OUTPUT = "tool_output"
    FINAL_OUTPUT = "final_output"


class GuardrailVerdict(str, Enum):
    """The decision a single guardrail check renders."""

    ALLOW = "allow"
    BLOCK = "block"
    MODIFY = "modify"


class GuardrailCategory(str, Enum):
    """The category of concern a guardrail addresses.

    Matches the roadmap's call for guardrails to "support different
    types: validation, policy, safety, output filtering."
    """

    VALIDATION = "validation"
    POLICY = "policy"
    SAFETY = "safety"
    OUTPUT_FILTERING = "output_filtering"


class FailureCategory(str, Enum):
    """Coarse-grained taxonomy of why a task ultimately failed.

    Used by both the Orchestrator (to decide whether replanning is
    worthwhile) and, in Phase 2, the Evaluation Harness's failure
    analysis reports.
    """

    GUARDRAIL_BLOCKED = "guardrail_blocked"
    TOOL_ERROR = "tool_error"
    TOOL_TIMEOUT = "tool_timeout"
    PLANNING_ERROR = "planning_error"
    STEP_BUDGET_EXCEEDED = "step_budget_exceeded"
    REPLAN_LIMIT_EXCEEDED = "replan_limit_exceeded"
    LLM_ERROR = "llm_error"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    """Structured event types emitted to the Observability layer.

    Every important transition in the system should emit exactly one
    of these, with a payload appropriate to the type. This is the
    backbone of "Observability by Default".
    """

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    STATE_TRANSITION = "state_transition"
    PLAN_GENERATED = "plan_generated"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_CRITIQUED = "step_critiqued"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    GUARDRAIL_EVALUATED = "guardrail_evaluated"
    CRITIQUE_GENERATED = "critique_generated"
    REPLAN_TRIGGERED = "replan_triggered"
    CHECKPOINT_SAVED = "checkpoint_saved"
    CHECKPOINT_RESTORED = "checkpoint_restored"
