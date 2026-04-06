"""Structured event model emitted by every major transition in the system.

Every important decision or state change — a plan being generated, a
tool call starting/finishing, a guardrail verdict, a state transition,
a checkpoint — is represented as one `Event` and handed to the
`Tracer`. This is the literal implementation of "Observability by
Default": nothing about *why* the system did something lives only in
an in-memory variable or a log string; it lives in a structured,
queryable event.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from reliableagent._compat import BaseModel, ConfigDict, Field
from reliableagent.core.enums import EventType


class Event(BaseModel):
    """A single structured observability event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: EventType
    run_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_log_line(self) -> str:
        """Render a single human-readable line, for console/file sinks."""
        ts = self.timestamp.isoformat(timespec="milliseconds")
        return f"[{ts}] {self.event_type.value:<24} run={self.run_id} {self.payload}"
