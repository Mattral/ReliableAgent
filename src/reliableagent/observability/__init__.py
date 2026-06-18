"""Observability layer: structured events, sinks, and the Tracer.

See `reliableagent.observability.events` for the `Event` model,
`reliableagent.observability.sinks` for pluggable destinations
(in-memory, console, JSONL file), and
`reliableagent.observability.tracer` for the `Tracer` that every other
component emits events through.
"""

from reliableagent.observability.events import Event
from reliableagent.observability.sinks import (
    ConsoleSink,
    EventSink,
    InMemorySink,
    JSONLFileSink,
    MultiSink,
)
from reliableagent.observability.tracer import Tracer

__all__ = [
    "ConsoleSink",
    "Event",
    "EventSink",
    "InMemorySink",
    "JSONLFileSink",
    "MultiSink",
    "Tracer",
]
