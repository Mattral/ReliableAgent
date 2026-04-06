"""Deterministic mock tools used by the golden task suite.

Every tool here is intentionally simple and fully deterministic (no real
network calls, no real randomness) so the golden task suite is
reproducible by construction, not just "reproducible if you also pin a
real backend's behavior." `flaky_lookup`'s "flakiness" is itself
deterministic — keyed on its `attempt_log`, not on `random` — specifically
so failure-recovery golden tasks reliably exercise the replanning path
without relying on wall-clock timing or actual randomness.
"""

from __future__ import annotations

from reliableagent.executor.tool_registry import ToolRegistry

# A small, fixed "knowledge base" a few golden tasks query against, kept
# deliberately tiny and hand-verifiable so grading the resulting answers
# never requires trusting an external data source.
_KNOWLEDGE_BASE: dict[str, str] = {
    "capital of france": "Paris",
    "capital of japan": "Tokyo",
    "speed of light": "299792458 m/s",
    "boiling point of water": "100 degrees Celsius at sea level",
}


def build_golden_task_tools() -> ToolRegistry:
    """Build the full `ToolRegistry` shared by every golden task in the suite.

    Centralized in one factory (rather than each golden task building its
    own ad-hoc registry) so every task draws from the exact same, fully
    reviewed tool implementations — a task-specific bug in a one-off mock
    tool can't quietly produce a misleading reliability number for that
    one task alone.
    """
    registry = ToolRegistry()

    @registry.register(description="Add two numbers")
    def add(a: float, b: float) -> float:
        return a + b

    @registry.register(description="Subtract b from a")
    def subtract(a: float, b: float) -> float:
        return a - b

    @registry.register(description="Multiply two numbers")
    def multiply(a: float, b: float) -> float:
        return a * b

    @registry.register(description="Divide a by b")
    def divide(a: float, b: float) -> float:
        if b == 0:
            raise ValueError("Division by zero is not allowed.")
        return a / b

    @registry.register(description="Look up a fact in a small fixed knowledge base")
    def lookup_fact(query: str) -> str:
        key = query.strip().lower()
        if key not in _KNOWLEDGE_BASE:
            raise KeyError(f"No fact found for query: {query!r}")
        return _KNOWLEDGE_BASE[key]

    @registry.register(description="A tool that always raises, for failure-recovery tasks")
    def always_fails(reason: str = "simulated failure") -> None:
        raise RuntimeError(reason)

    @registry.register(
        description="A tool that fails its first two calls but succeeds on retry/replan",
        timeout_seconds=5.0,
    )
    def flaky_lookup(query: str, _attempt_log: dict[str, int] = {}) -> str:  # noqa: B006
        # Deliberately deterministic "flakiness": fails the first TWO calls
        # per distinct query, then succeeds every time after. Two failures
        # (not one) are needed so this genuinely exercises the Orchestrator's
        # replanning path even though the Executor itself already retries a
        # failed tool call once (see Executor's default max_retries=1) --
        # with only a single failure, the Executor's own built-in retry
        # would silently absorb it before the Critic/replanner ever gets
        # involved, which would make a "recovery via replan" golden task
        # pass for the wrong reason (or not need a replan at all).
        count = _attempt_log.get(query, 0)
        _attempt_log[query] = count + 1
        if count < 2:
            raise ConnectionError(f"Transient backend error looking up: {query!r}")
        return f"recovered result for {query!r}"

    @registry.register(description="Reverse a string")
    def reverse_text(text: str) -> str:
        return text[::-1]

    @registry.register(description="Count words in a string")
    def count_words(text: str) -> int:
        return len(text.split())

    return registry
