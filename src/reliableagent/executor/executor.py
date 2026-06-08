"""Executor: runs `ToolCall`s safely, with timeouts, error capture, and timing.

Per the roadmap: "Executor handles timeouts, retries, and sandboxing"
and "Tool results must be validated before being trusted."

This v1 implementation focuses on the P0-critical pieces — timeout
enforcement and structured error capture so a misbehaving tool can
never crash the Orchestrator's control loop — using a thread-based
timeout mechanism that works uniformly for both sync and async tools
without requiring multiprocessing (which would complicate state
sharing for a portfolio-scale project). Retries are layered on top via
`max_retries`, with exponential backoff, since most transient tool
failures (flaky network calls, rate limits) are resolved by a short
retry rather than a full replan.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from typing import Any

from reliableagent.core.models import ToolCall, ToolResult
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.exceptions import ToolNotFoundError
from reliableagent.observability.tracer import Tracer


class Executor:
    """Executes `ToolCall`s against a `ToolRegistry`, returning `ToolResult`s.

    The Executor never raises for *tool-level* failures (timeouts,
    exceptions inside the tool, missing tools) — those are captured as
    `ToolResult(success=False, error=...)` so the Orchestrator and
    Critic can reason about them as ordinary data. This is the
    "Failure is a First-Class Path" principle applied directly to the
    one component that talks to the outside world.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        max_retries: int = 1,
        retry_backoff_seconds: float = 0.5,
        tracer: Tracer | None = None,
    ) -> None:
        self._registry = registry
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._tracer = tracer or Tracer.noop()
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="reliableagent-tool"
        )

    def execute(self, call: ToolCall) -> ToolResult:
        """Execute a single `ToolCall`, validating arguments and enforcing a timeout."""
        self._tracer.emit_tool_call_started(call)
        start = time.monotonic()

        try:
            spec = self._registry.get(call.tool_name)
            validated_args = self._registry.validate_arguments(call.tool_name, call.arguments)
        except ToolNotFoundError as exc:
            result = ToolResult(
                call_id=call.call_id,
                success=False,
                error=exc.message,
                duration_seconds=time.monotonic() - start,
            )
            self._tracer.emit_tool_call_completed(call, result)
            return result
        except Exception as exc:  # noqa: BLE001 - a bad tool call must never crash the Executor
            result = ToolResult(
                call_id=call.call_id,
                success=False,
                error=str(exc),
                duration_seconds=time.monotonic() - start,
            )
            self._tracer.emit_tool_call_completed(call, result)
            return result

        attempt = 0
        last_error: str = ""
        while attempt <= self._max_retries:
            attempt += 1
            try:
                output = self._run_with_timeout(
                    spec.func, validated_args, call.timeout_seconds, spec.is_async
                )
                if self._registry.validate_result(call.tool_name, output):
                    result = ToolResult(
                        call_id=call.call_id,
                        success=True,
                        output=output,
                        duration_seconds=time.monotonic() - start,
                        validated=True,
                    )
                    self._tracer.emit_tool_call_completed(call, result)
                    return result
                last_error = (
                    f"Tool '{call.tool_name}' returned a result that failed its "
                    f"registered output validator: {output!r} "
                    f"(attempt {attempt}/{self._max_retries + 1})."
                )
            except concurrent.futures.TimeoutError:
                last_error = (
                    f"Tool '{call.tool_name}' timed out after {call.timeout_seconds}s "
                    f"(attempt {attempt}/{self._max_retries + 1})."
                )
            except Exception as exc:  # noqa: BLE001 - capture any tool-internal error
                last_error = (
                    f"Tool '{call.tool_name}' raised {type(exc).__name__}: {exc} "
                    f"(attempt {attempt}/{self._max_retries + 1})."
                )

            if attempt <= self._max_retries:
                time.sleep(self._retry_backoff_seconds * attempt)

        result = ToolResult(
            call_id=call.call_id,
            success=False,
            error=last_error,
            duration_seconds=time.monotonic() - start,
        )
        self._tracer.emit_tool_call_completed(call, result)
        return result

    def _run_with_timeout(
        self,
        func: Any,
        arguments: dict[str, Any],
        timeout_seconds: float,
        is_async: bool,
    ) -> Any:
        """Run `func(**arguments)` with a hard wall-clock timeout.

        Sync tools run in a worker thread so a hang can't block the
        whole Orchestrator loop. Async tools are run to completion via
        `asyncio.run` inside that same worker thread, which keeps a
        single, uniform timeout code path for both cases.
        """

        def _call() -> Any:
            if is_async:
                return asyncio.run(func(**arguments))
            return func(**arguments)

        future = self._pool.submit(_call)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def shutdown(self) -> None:
        """Release the underlying thread pool. Call when done with the Executor."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> Executor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()
