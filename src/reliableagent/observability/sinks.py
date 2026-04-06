"""Event sinks: pluggable destinations for structured observability events.

Sinks implement a tiny `EventSink` protocol so additional backends
(a real tracing system, a database, a metrics aggregator) can be added
later without touching the `Tracer` or any component that emits
events.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from reliableagent.observability.events import Event


@runtime_checkable
class EventSink(Protocol):
    """Anything that can receive structured `Event`s."""

    def write(self, event: Event) -> None: ...


class InMemorySink:
    """Stores every event in a list, for tests and in-process trajectory inspection.

    Thread-safe: the Executor runs tool calls in worker threads, so
    sinks must tolerate concurrent writes from the main control loop
    thread and tool-call threads.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._lock = threading.Lock()

    def write(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> list[Event]:
        """A snapshot copy of all events recorded so far."""
        with self._lock:
            return list(self._events)

    def events_of_type(self, event_type: str) -> list[Event]:
        """Filter recorded events by `EventType` value."""
        return [e for e in self.events if e.event_type.value == event_type]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class ConsoleSink:
    """Prints each event as a single human-readable line to stdout."""

    def write(self, event: Event) -> None:
        print(event.to_log_line())  # noqa: T201 - intentional console sink


class JSONLFileSink:
    """Appends each event as one JSON line to a file (JSON Lines format).

    JSONL is used (rather than a single JSON array) so a run's log can
    be written incrementally and tailed/streamed without re-writing
    the whole file, and so that a crash mid-run still leaves a valid,
    parseable partial log.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: Event) -> None:
        line = json.dumps(event.model_dump(mode="json"), default=str)
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class MultiSink:
    """Fans a single event out to multiple underlying sinks."""

    def __init__(self, sinks: list[EventSink]) -> None:
        self._sinks = sinks

    def write(self, event: Event) -> None:
        for sink in self._sinks:
            sink.write(event)
