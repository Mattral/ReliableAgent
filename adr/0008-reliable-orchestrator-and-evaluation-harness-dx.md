# ADR 0008: ReliableOrchestrator and EvaluationHarness as genuine convenience
# wrappers matching the roadmap's illustrative DX, not cosmetic aliases

## Status
Accepted.

## Context
A self-audit found this project's public API names diverge from the roadmap's
own "Target Experience" DX example:

```python
from reliableagent import ReliableOrchestrator, ToolRegistry
from reliableagent.guardrails import BasicGuardrail
from reliableagent.evaluation import EvaluationHarness

orchestrator = ReliableOrchestrator(
    model="Qwen/Qwen2.5-7B-Instruct", tools=tools, guardrails=[BasicGuardrail()],
    enable_checkpointing=True, enable_observability=True,
)
result = orchestrator.run(task="...", max_steps=20)

harness = EvaluationHarness(orchestrator=orchestrator)
results = harness.evaluate(task_set="long_horizon_v1", seeds=[42, 43, 44])
print(results.summary())
print(results.failure_analysis())
```

The actual API from Phases 0-3 uses `Orchestrator` (not `ReliableOrchestrator`),
requires explicitly constructing Planner/Critic/MemoryBackend/sink rather than
flags, and has no class literally named `EvaluationHarness` (only
`EvaluationRunner`, with a materially different calling convention: an
`OrchestratorFactory` function, not a single pre-built `Orchestrator`).

Also found in this same pass: `ToolRegistry` itself, despite being used in the
roadmap's own `from reliableagent import ReliableOrchestrator, ToolRegistry`
import line, was never exported from the top-level `reliableagent` package —
only importable via `reliableagent.executor.ToolRegistry`. Fixed alongside the
two new wrapper classes.

## Decision
Added two new, genuinely-functional classes rather than renaming the existing ones:

1. **`ReliableOrchestrator`** (`core/reliable_orchestrator.py`): constructs a
   real `LLMPlanner`/`Critic`/`FileMemoryBackend`/`ConsoleSink` under the hood
   and delegates to a real `Orchestrator`. `model=` is an Anthropic model name
   (not HuggingFace) — documented explicitly as a deviation, since this
   project never implements local model loading anywhere.
2. **`EvaluationHarness`** (`evaluation/harness.py`): wraps a single
   caller-provided `Orchestrator` plus a named task-set registry
   (`register_task_set`/`get_task_set`, `"golden_suite_v1"` registered by
   default). `evaluate(task_set=name, seeds=[...])` returns
   `EvaluationResults` with `.summary()`/`.failure_analysis()`.

The harness makes one real distinction explicit: if the wrapped orchestrator's
Planner is backed by `MockLLMClient`, the harness builds a FRESH scripted
Orchestrator per **(task, seed) pair** (not just per task — see the bug below);
otherwise (a real provider) the same orchestrator is reused across every
task/seed, matching what the roadmap's example implies for a real model.

`Orchestrator` gained public read-only introspection properties (`.planner`,
`.critic`, `.tools`, `.guardrails`, `.memory`, `.executor`, `.replanner`,
`.sink`) and `GuardrailRunner` gained `.guardrails` (a defensive copy) — added
so `EvaluationHarness` could inspect/reuse a wrapped orchestrator's
configuration without reaching into private attributes. `ToolRegistry` was
added to the top-level `reliableagent` package's exports.

## A real bug this work caught: MockLLMClient queue exhaustion across seeds
The first implementation of `EvaluationHarness`'s mock-backed path built ONE
scripted Orchestrator **per golden task** and then looped over every seed
against that SAME orchestrator. `MockLLMClient` holds a finite `deque` of
scripted responses; a task needing exactly one LLM call to pass would have
that response consumed on the first seed's run, then silently fall back to
`MockLLMClient`'s `default_response = "OK."` for every subsequent seed —
which is not valid plan JSON, producing spurious `planning_error` failures.
This looked entirely correct under `seeds=[0]` (the case most manual testing
used) and only surfaced when `examples/roadmap_dx_example.py` was run with
`seeds=[42, 43, 44]`, exactly matching the roadmap's own illustrative example.
Fixed by moving the scripted-Orchestrator construction inside the seed loop,
so each `(task, seed)` pair gets its own fresh, fully-stocked `MockLLMClient`.
Regression-tested via `test_mock_backed_handles_multiple_seeds_without_
exhausting_mock_queue`, which specifically exercises 3 seeds (the minimum
that would have exposed the original bug) and asserts a 100% pass rate.

A second, smaller bug in the same example script (not the harness itself):
the example initially configured `guardrails=[BasicGuardrail()]` for its
`EvaluationHarness` demonstration, but `golden_suite_v1`'s guardrail-category
tasks are authored against `evaluation/factory.py`'s `standard_guardrails()`
(which also includes `ToolArgumentSanityGuardrail`) — fixed by using
`standard_guardrails()` in the example, with a comment explaining why.

## Consequences

**Positive:**
- Code matching the roadmap's illustrative DX example (with the one
  documented `model=` substitution) now genuinely runs —
  `examples/roadmap_dx_example.py` reproduces the roadmap's example nearly
  verbatim and is verified to produce a 100% pass rate (60/60 = 20 tasks × 3
  seeds) through `EvaluationHarness`.
- 18 new tests across both wrappers (10 for `ReliableOrchestrator`, 8 for
  `EvaluationHarness`, including the multi-seed regression test).
- `Orchestrator`'s new introspection properties and `ToolRegistry`'s top-level
  export are durable API improvements independent of this specific fix.

**Negative / known limitations:**
- `ReliableOrchestrator`'s `model=` accepting an Anthropic name rather than a
  HuggingFace identifier remains a documented, real deviation from the
  roadmap's literal example string.
- `EvaluationHarness`'s mock-backed path constructs one `Orchestrator` per
  `(task, seed)` pair internally, not exposed to the caller for individual
  inspection — matches `EvaluationRunner`'s existing behavior, not new.
