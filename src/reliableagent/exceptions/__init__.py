"""Exception hierarchy for ReliableAgent.

Design principles:
    1. All framework-raised exceptions inherit from :class:`ReliableAgentError`
       so callers can catch the entire family with a single `except` clause.
    2. Exceptions are organized by the architectural layer that raises them
       (planning, execution, guardrails, memory, orchestration), mirroring
       the system architecture described in the project roadmap.
    3. Every exception carries enough structured context (not just a message
       string) to be logged as a structured event by the observability
       layer, and to drive replanning / recovery decisions in the
       Orchestrator without re-parsing error strings.
    4. Exceptions distinguish *recoverable* failures (the Orchestrator may
       reasonably retry, replan, or degrade gracefully) from
       *unrecoverable* ones (configuration/programmer errors that should
       fail fast).
"""

from __future__ import annotations

from typing import Any


class ReliableAgentError(Exception):
    """Base class for all ReliableAgent exceptions.

    Attributes:
        message: Human-readable description of what went wrong.
        context: Arbitrary structured data useful for logging/debugging
            (e.g. step index, tool name, run_id). Kept JSON-serializable
            wherever possible so it can flow straight into the
            observability layer's structured event log.
        recoverable: Whether the Orchestrator may attempt automatic
            recovery (retry, replan, checkpoint rollback) in response to
            this error. Defaults to ``False`` (fail safe).
    """

    recoverable: bool = False

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context or {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(message={self.message!r}, context={self.context!r})"

    def to_dict(self) -> dict[str, Any]:
        """Serialize the exception into a structured-logging-friendly dict."""
        return {
            "error_type": type(self).__name__,
            "message": self.message,
            "recoverable": self.recoverable,
            "context": self.context,
        }


# ---------------------------------------------------------------------------
# Configuration & setup errors (unrecoverable — programmer/config mistakes)
# ---------------------------------------------------------------------------


class ConfigurationError(ReliableAgentError):
    """Raised when the system is configured incorrectly or inconsistently."""

    recoverable = False


class ComponentNotConfiguredError(ConfigurationError):
    """Raised when a required component (e.g. Planner, LLM client) is missing."""

    recoverable = False


# ---------------------------------------------------------------------------
# Validation errors (data model / schema violations)
# ---------------------------------------------------------------------------


class ValidationError(ReliableAgentError):
    """Raised when data fails validation against an expected schema/contract."""

    recoverable = False


class SchemaValidationError(ValidationError):
    """Raised when structured LLM output fails to validate against a Pydantic schema."""

    recoverable = True  # The Planner/Critic may be able to retry with a corrective prompt.


# ---------------------------------------------------------------------------
# Planning errors
# ---------------------------------------------------------------------------


class PlanningError(ReliableAgentError):
    """Base class for errors raised during the planning phase."""

    recoverable = True


class PlanGenerationError(PlanningError):
    """Raised when the Planner fails to produce a usable plan."""

    recoverable = True


class PlanParsingError(PlanningError):
    """Raised when the Planner's structured output cannot be parsed."""

    recoverable = True


class ReplanLimitExceededError(PlanningError):
    """Raised when the maximum number of replanning attempts has been exhausted."""

    recoverable = False


# ---------------------------------------------------------------------------
# Execution / tool errors
# ---------------------------------------------------------------------------


class ExecutionError(ReliableAgentError):
    """Base class for errors raised during tool/step execution."""

    recoverable = True


class ToolNotFoundError(ExecutionError):
    """Raised when a requested tool is not present in the Tool Registry."""

    recoverable = True  # Replanner may choose a different tool.


class ToolExecutionError(ExecutionError):
    """Raised when a tool raises an exception while running."""

    recoverable = True


class ToolTimeoutError(ExecutionError):
    """Raised when a tool call exceeds its configured timeout."""

    recoverable = True


class ToolArgumentValidationError(ExecutionError):
    """Raised when arguments passed to a tool fail schema validation."""

    recoverable = True


class ToolResultValidationError(ExecutionError):
    """Raised when a tool's result fails post-execution validation."""

    recoverable = True


class StepBudgetExceededError(ExecutionError):
    """Raised when a run exceeds its configured maximum number of steps."""

    recoverable = False


# ---------------------------------------------------------------------------
# Guardrail errors
# ---------------------------------------------------------------------------


class GuardrailError(ReliableAgentError):
    """Base class for guardrail-layer errors."""

    recoverable = False


class GuardrailViolationError(GuardrailError):
    """Raised when a guardrail blocks a transition (input, output, or tool call).

    This is the primary signal used to enforce the "Guardrails as
    Architectural Citizens" principle: a violation halts the relevant
    transition rather than allowing it to silently pass through.
    """

    recoverable = True  # The Orchestrator may replan in response.

    def __init__(
        self,
        message: str,
        *,
        guardrail_name: str,
        boundary: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, context=context)
        self.guardrail_name = guardrail_name
        self.boundary = boundary

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["guardrail_name"] = self.guardrail_name
        data["boundary"] = self.boundary
        return data


# ---------------------------------------------------------------------------
# Memory / checkpoint errors
# ---------------------------------------------------------------------------


class MemoryError_(ReliableAgentError):  # noqa: N818 (avoid shadowing builtin MemoryError)
    """Base class for Memory & State Manager errors."""

    recoverable = False


class CheckpointNotFoundError(MemoryError_):
    """Raised when attempting to resume from a checkpoint that does not exist."""

    recoverable = False


class CheckpointCorruptedError(MemoryError_):
    """Raised when a checkpoint fails integrity validation on load."""

    recoverable = False


# ---------------------------------------------------------------------------
# Orchestration errors
# ---------------------------------------------------------------------------


class OrchestrationError(ReliableAgentError):
    """Base class for top-level orchestration failures."""

    recoverable = False


class InvalidStateTransitionError(OrchestrationError):
    """Raised when the Orchestrator's state machine receives an illegal transition."""

    recoverable = False


class TaskFailedError(OrchestrationError):
    """Raised when a task ultimately fails after all recovery attempts are exhausted."""

    recoverable = False


# ---------------------------------------------------------------------------
# LLM client errors
# ---------------------------------------------------------------------------


class LLMError(ReliableAgentError):
    """Base class for errors raised by LLM provider clients."""

    recoverable = True


class LLMRequestError(LLMError):
    """Raised when a request to an LLM provider fails (network, API error, etc.)."""

    recoverable = True


class LLMResponseParsingError(LLMError):
    """Raised when an LLM response cannot be parsed into the expected structure."""

    recoverable = True
