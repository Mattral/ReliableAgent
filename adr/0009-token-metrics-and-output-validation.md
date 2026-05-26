# ADR 0009: Token/latency metrics via a decorator pattern, and real tool
# output validation

## Status
Accepted.

## Context
A self-audit found two gaps against the roadmap's explicit requirements:
1. Section 4.2 lists "Metrics (latency, token usage, guardrail triggers,
   replan count)" — `RunMetrics` had guardrail/replan/step counts and total
   duration, but no token usage or LLM-call latency fields at all, despite
   `LLMResponse` already capturing `input_tokens`/`output_tokens` per call.
2. "Tool results must be validated before being trusted" — `ToolResult.
   validated` was hardcoded to `True` the instant a tool call didn't raise;
   only tool *input* arguments were ever actually validated (via
   `ToolRegistry.validate_arguments`), never tool *output*.

## Decision

### Token/latency metrics
Added `LLMUsageStats` (thread-safe accumulator) and `UsageTrackingLLMClient`
(a decorator implementing the same `LLMClient` protocol) in
`llm/usage.py`. This is additive and opt-in: `Orchestrator` accepts an
optional `usage_tracker=` parameter; if given, `RunMetrics` gets real
`total_input_tokens`/`total_output_tokens`/`total_llm_calls`/
`total_llm_latency_seconds`, computed as a snapshot-based DELTA (state
before the run vs. after), not the tracker's raw lifetime total — this
matters because a shared tracker reused across multiple `run()` calls on the
same Orchestrator must report each run's own usage, not an ever-growing
cumulative number. Verified via a regression test that runs the same
Orchestrator twice and asserts each `RunMetrics.total_tokens` reflects only
that run's own call, summing correctly to the tracker's lifetime total.

A decorator (not a change to the `Planner`/`Critic` contracts) was chosen
because those contracts deliberately hide the LLM client entirely — the
Orchestrator has no other way to observe token counts without breaking that
abstraction.

### Tool output validation
Added `result_validator: Callable[[Any], bool] | None` to `ToolSpec` and
`ToolRegistry.register()`, and `ToolRegistry.validate_result()` (mirroring
the existing `validate_arguments()`). Wired into `Executor.execute`: a
successful call's raw output is checked; a validator that rejects it (or
itself crashes) is treated as a retryable failure — the same as a raised
exception or timeout — rather than silently returned with `validated=False`,
since nothing downstream (Critic, replanning) reacts to that flag on an
otherwise-`success=True` result. A tool with no validator is trusted as-is,
preserving every existing tool registration's behavior unchanged.

## Consequences
**Positive:** Both fixes are fully opt-in/backward-compatible — zero existing
test or example needed to change. `RunMetrics.total_tokens` is a genuinely
useful, tested, correctly-isolated-per-run number. Tool output validation
closes a real trust gap: a tool that returns a plausible-looking but wrong
value (e.g. a negative price) is now catchable and retryable, not silently
accepted.

**Negative:** Token tracking requires the caller to explicitly wrap their
`LLMClient` in `UsageTrackingLLMClient` and pass the same `LLMUsageStats`
instance to both the Planner's client and the Orchestrator's `usage_tracker=`
— an easy step to forget, silently producing all-zero token metrics rather
than an error. Output validators run synchronously inside the Executor's
retry loop; a slow validator adds directly to per-attempt latency.
