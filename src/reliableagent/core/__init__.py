"""Core data models and enumerations for ReliableAgent.

This subpackage defines the explicit contracts (Pydantic models) and
shared vocabulary (enums) that every other component in the framework
communicates through. See `reliableagent.core.models` for the full
data model documentation.
"""

from reliableagent.core.enums import (
    EventType,
    FailureCategory,
    GuardrailBoundary,
    GuardrailCategory,
    GuardrailVerdict,
    OrchestratorState,
    StepStatus,
    StepType,
)
from reliableagent.core.models import (
    Checkpoint,
    CriterionScores,
    Feedback,
    GuardrailDecision,
    Plan,
    PlanStep,
    RunMetrics,
    RunResult,
    StepCritique,
    StepRecord,
    Task,
    ToolCall,
    ToolResult,
    Trajectory,
)
from reliableagent.core.orchestrator import Orchestrator
from reliableagent.core.state_machine import StateMachine

__all__ = [
    # Enums
    "EventType",
    "FailureCategory",
    "GuardrailBoundary",
    "GuardrailCategory",
    "GuardrailVerdict",
    "OrchestratorState",
    "StepStatus",
    "StepType",
    # Models
    "Checkpoint",
    "CriterionScores",
    "Feedback",
    "GuardrailDecision",
    "Plan",
    "PlanStep",
    "RunMetrics",
    "RunResult",
    "StepCritique",
    "StepRecord",
    "Task",
    "ToolCall",
    "ToolResult",
    "Trajectory",
    # Orchestration
    "Orchestrator",
    "StateMachine",
]
