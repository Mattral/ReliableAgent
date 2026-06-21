"""ReliableAgent: a reliability-first orchestration framework for agentic systems.

ReliableAgent treats reliability — guardrails, observability,
checkpointing, and explicit failure handling — as first-class
architectural concerns rather than afterthoughts bolted onto a planning
loop. See the project roadmap and `docs/architecture.md` for the full
design rationale.

Typical usage::

    from reliableagent import Orchestrator, Task
    from reliableagent.llm import MockLLMClient
    from reliableagent.planner import LLMPlanner, ThresholdCritic
    from reliableagent.executor import ToolRegistry
    from reliableagent.guardrails import BasicGuardrail

    tools = ToolRegistry()

    @tools.register(description="Add two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    orchestrator = Orchestrator(
        planner=LLMPlanner(MockLLMClient(responses=[...])),
        critic=ThresholdCritic(),
        tools=tools,
        guardrails=[BasicGuardrail()],
    )
    result = orchestrator.run(Task(description="Add 2 and 3"))
    print(result.final_answer)
    print(result.metrics)
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

__version__ = "0.1.0"

__all__ = [
    "__version__",
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
