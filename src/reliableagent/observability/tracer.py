"""Tracer: the single emission point for every structured event in the system.

Every component that needs to record "something important happened"
calls one of the `emit_*` methods on a `Tracer` instance, rather than
constructing `Event`s by hand or writing ad-hoc log statements. This
keeps the event vocabulary centralized and consistent (see
`reliableagent.core.enums.EventType`) and makes it trivial to add a
new sink (e.g. a real distributed tracing backend) without touching
any call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from reliableagent.core.enums import EventType
from reliableagent.observability.events import Event
from reliableagent.observability.sinks import EventSink, InMemorySink

if TYPE_CHECKING:
    from reliableagent.core.models import (
        GuardrailDecision,
        Plan,
        PlanStep,
        ToolCall,
        ToolResult,
    )


class Tracer:
    """Emits structured `Event`s for a single run to one or more sinks."""

    def __init__(self, run_id: str, sink: EventSink) -> None:
        self.run_id = run_id
        self._sink = sink

    @classmethod
    def noop(cls, run_id: str = "unbound") -> "Tracer":
        """A Tracer with an in-memory sink, useful as a safe default for
        components (like `Executor`) constructed without an explicit
        tracer, e.g. in lightweight unit tests."""
        return cls(run_id=run_id, sink=InMemorySink())

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        self._sink.write(Event(event_type=event_type, run_id=self.run_id, payload=payload))

    def emit_run_started(self, task_id: str, description: str) -> None:
        self._emit(EventType.RUN_STARTED, {"task_id": task_id, "description": description})

    def emit_run_completed(self, final_state: str, succeeded: bool) -> None:
        self._emit(EventType.RUN_COMPLETED, {"final_state": final_state, "succeeded": succeeded})

    def emit_run_failed(self, failure_category: str, reason: str) -> None:
        self._emit(EventType.RUN_FAILED, {"failure_category": failure_category, "reason": reason})

    def emit_state_transition(self, from_state: str, to_state: str) -> None:
        self._emit(EventType.STATE_TRANSITION, {"from": from_state, "to": to_state})

    def emit_plan_generated(self, plan: "Plan") -> None:
        self._emit(
            EventType.PLAN_GENERATED,
            {
                "plan_id": plan.plan_id,
                "num_steps": len(plan.steps),
                "confidence": plan.confidence,
                "replan_attempt": plan.replan_attempt,
            },
        )

    def emit_step_started(self, step: "PlanStep") -> None:
        self._emit(
            EventType.STEP_STARTED,
            {"step_id": step.step_id, "step_type": step.step_type.value},
        )

    def emit_step_completed(self, step: "PlanStep", status: str) -> None:
        self._emit(
            EventType.STEP_COMPLETED,
            {"step_id": step.step_id, "status": status},
        )

    def emit_tool_call_started(self, call: "ToolCall") -> None:
        self._emit(
            EventType.TOOL_CALL_STARTED,
            {"call_id": call.call_id, "tool_name": call.tool_name, "arguments": call.arguments},
        )

    def emit_tool_call_completed(self, call: "ToolCall", result: "ToolResult") -> None:
        self._emit(
            EventType.TOOL_CALL_COMPLETED,
            {
                "call_id": call.call_id,
                "tool_name": call.tool_name,
                "success": result.success,
                "duration_seconds": result.duration_seconds,
                "error": result.error,
            },
        )

    def emit_guardrail_evaluated(self, decision: "GuardrailDecision") -> None:
        self._emit(
            EventType.GUARDRAIL_EVALUATED,
            {
                "guardrail_name": decision.guardrail_name,
                "boundary": decision.boundary.value,
                "verdict": decision.verdict.value,
                "reason": decision.reason,
            },
        )

    def emit_critique_generated(self, quality_score: float, should_replan: bool) -> None:
        self._emit(
            EventType.CRITIQUE_GENERATED,
            {"quality_score": quality_score, "should_replan": should_replan},
        )

    def emit_replan_triggered(self, attempt: int, reason: str) -> None:
        self._emit(EventType.REPLAN_TRIGGERED, {"attempt": attempt, "reason": reason})

    def emit_checkpoint_saved(self, checkpoint_id: str, sequence_number: int) -> None:
        self._emit(
            EventType.CHECKPOINT_SAVED,
            {"checkpoint_id": checkpoint_id, "sequence_number": sequence_number},
        )

    def emit_checkpoint_restored(self, checkpoint_id: str, sequence_number: int) -> None:
        self._emit(
            EventType.CHECKPOINT_RESTORED,
            {"checkpoint_id": checkpoint_id, "sequence_number": sequence_number},
        )
