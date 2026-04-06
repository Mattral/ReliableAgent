# Architecture

This document explains how ReliableAgent's modules fit together and why
each design decision was made the way it was. It assumes you've read the
README's high-level diagram; this is the "now let's go one level deeper"
follow-up.

## 1. The contract-first principle, concretely

Every object that crosses a module boundary in this codebase is a Pydantic
model defined in `reliableagent.core.models`, not a dict. This is not
incidental — it's the mechanism that makes the rest of the architecture's
claims (swappable Planners, swappable guardrails, swappable memory
backends) actually true rather than aspirational. A `Planner` subclass
that returns something that isn't a valid `Plan` fails loudly, immediately,
at construction time, rather than producing a confusing `KeyError` three
components downstream.

Concretely, the contracts are:

| Producer  | Contract        | Consumer(s)                     |
|-----------|------------------|----------------------------------|
| Planner   | `Plan`           | Orchestrator, GuardrailRunner    |
| Executor  | `ToolResult`     | Orchestrator, Critic             |
| Critic    | `Feedback`       | Orchestrator                     |
| Guardrail | `GuardrailDecision` | GuardrailRunner, Tracer       |
| Orchestrator | `Trajectory`, `Checkpoint`, `RunResult` | Memory, caller |

## 2. The Orchestrator's control loop, in detail

`Orchestrator._run_loop` and `_execute_and_continue`
(`src/reliableagent/core/orchestrator.py`) implement exactly the diagram in
the README. The two methods are split the way they are because `run()` and
`resume()` need to converge on the same execution logic after a different
setup step (generate a fresh plan vs. load a checkpointed one) — splitting
"set up the first plan" from "execute a plan and react to what happens"
means that convergence is structural, not duplicated logic that could drift
out of sync between the two entry points.

The loop's only exit conditions are:
1. A `final_answer` step is reached and passes the `FINAL_OUTPUT` guardrail
   boundary → `COMPLETED`.
2. The Critic returns `should_replan=False` with no explicit final-answer
   step → the Orchestrator derives a fallback answer from the last
   successful tool result → `COMPLETED`. (This matters because not every
   reasonable plan ends in an explicit `final_answer` step — a Planner
   strategy might reasonably consider "ran the tool successfully" to be
   "done.")
3. Any of: a guardrail block, exceeding `max_replans`, exceeding
   `max_steps`, or an unrecoverable exception → `FAILED`, with a
   `FailureCategory` attached so failure-mode analysis (Phase 2) has
   something structured to bucket on immediately, even before that phase
   is built.

Every state transition goes through `StateMachine.transition()`
(`core/state_machine.py`), which is a static, fully-enumerated transition
table — not "set the field and hope." An illegal transition (e.g. something
calling code tries to jump `COMPLETED -> EXECUTING`) raises immediately
instead of corrupting a trajectory silently. This was deliberately kept as
its own ~70-line module instead of inlined into the Orchestrator, because
"is this transition legal" is exactly the kind of invariant that should be
both unit-testable in isolation and impossible to bypass accidentally from
a different code path later.

## 3. Guardrails as a cross-cutting layer, not a wrapper

`GuardrailBoundary` (in `core/enums.py`) enumerates the five points where
guardrails are evaluated: `planner_input`, `planner_output`, `tool_input`,
`tool_output`, `final_output`. The Orchestrator calls `GuardrailRunner.run()`
at every one of these points (see `_guard()` and `_execute_step()` in
`orchestrator.py`) — there is no code path that skips this. A new guardrail
is added by subclassing `Guardrail`, declaring which boundaries it
`applies_to`, and passing an instance into `Orchestrator(guardrails=[...])`;
nothing else changes.

`GuardrailRunner.run()` evaluates guardrails in registration order and
stops at the first `BLOCK` (fail-fast — there's no value in running
cheaper-but-irrelevant checks after the transition is already rejected).
A `MODIFY` verdict updates the payload and chains it into the next
guardrail, so a redaction guardrail and a length-check guardrail compose
naturally without either one needing to know the other exists.

Every individual `GuardrailDecision` — allowed or blocked — is appended to
the `Trajectory` and emitted as an event via the `Tracer`, even when the
overall verdict was ALLOW. This was a deliberate choice over only logging
blocks: "this guardrail ran and allowed it" is exactly the kind of evidence
you want when investigating *why* something unsafe got through a guardrail
stack that, on paper, should have caught it.

## 4. Failure handling: the recoverable/unrecoverable split

Every exception in `reliableagent.exceptions` carries a `recoverable: bool`
class attribute. This is consumed today mostly as documentation/intent (the
"this exception type could plausibly trigger a replan rather than a hard
failure" contract that `Orchestrator._categorize()` uses to assign a
`FailureCategory`), and is the extension point a future, more
sophisticated recovery policy (e.g. "retry tool errors up to N times before
escalating to a full replan, but never retry guardrail blocks") would hook
into without needing a parallel taxonomy.

Tool-level failures specifically do **not** raise — `Executor.execute()`
always returns a `ToolResult`, with `success=False` and a populated `error`
string for failures (timeouts, exceptions, missing tools, bad arguments).
This is what lets the Critic reason about "2 of 5 tool calls failed" as
ordinary structured data instead of the Orchestrator needing a `try/except`
around every single step.

## 5. Memory & checkpointing: why sequence numbers, why two backends

`Checkpoint.sequence_number` gives checkpoints within a run a strict,
gap-free ordering. `FileMemoryBackend` exploits this by zero-padding the
sequence number into the filename (`00000003_ckpt_abc123.json`), so finding
the latest checkpoint is a directory glob + lexicographic sort — no need to
open and inspect every file's contents just to find the newest one.

Two backends ship because they serve genuinely different needs that
shouldn't be conflated behind one "Memory" abstraction with a confusing
"durable" flag: `InMemoryBackend` is for tests and short-lived runs where
durability across a process restart is explicitly not required (and
zero-dependency speed matters more); `FileMemoryBackend` is for anything
where "the process got killed and I need to resume" is a real possibility.
Both implement the exact same `MemoryBackend` protocol, so `Orchestrator`
code never has an `if backend_type == "file"` branch anywhere.

`Orchestrator.resume(run_id)` loads the *latest* checkpoint, reconstructs a
fresh `Trajectory` shell, fast-forwards the `StateMachine` to `EXECUTING`
(since a checkpoint is only ever saved mid-or-post-execution, never
mid-planning), and re-enters `_execute_and_continue` with the checkpointed
plan and already-completed results. Critically, it does **not** call the
Planner again unless the resumed run later needs to replan — a `test_`
case in `tests/integration/test_orchestrator.py` specifically asserts the
resumed run's LLM client received zero new calls, to guard against a
regression where "resume" silently became "re-plan from scratch."

## 6. The LLM abstraction: why a Protocol, why a mock-first design

`LLMClient` (`llm/base.py`) is a `typing.Protocol`, not an ABC that
`MockLLMClient`/`AnthropicLLMClient` must inherit from. This is intentional:
Protocols support structural typing, so a third-party class that happens to
implement `complete(...)` with the right signature satisfies the contract
without needing to know `reliableagent` exists, let alone inherit from one
of its classes. `BaseLLMClient` (an actual ABC) exists alongside it purely
as an opt-in convenience for clients that want `model_name` bookkeeping for
free; it is not the contract itself.

`MockLLMClient` was built and wired in *before* `AnthropicLLMClient`, and
the entire test suite (76 tests) runs exclusively against the mock. This
ordering was deliberate, not just a sandbox-imposed constraint: it forces
every other component's tests to be fast, free, and deterministic by
construction, and it means the *day* a real API key is available, swapping
`MockLLMClient(...)` for `AnthropicLLMClient(...)` in the `LLMPlanner`/
`LLMCritic` constructor is the entire integration step — no other code
changes, because both clients satisfy the identical `LLMClient` protocol.

## 7. Observability: events as the unit of truth, not log lines

`Tracer` (`observability/tracer.py`) is the single emission point for every
`EventType` in the system. Components never construct an `Event` directly
or call `print`/`logging` themselves — they call a typed `emit_*` method
(`emit_plan_generated`, `emit_tool_call_completed`, etc.), which keeps the
event vocabulary centralized in one enum (`core.enums.EventType`) instead
of accreting ad-hoc string literals across a dozen files over time.

Sinks (`InMemorySink`, `ConsoleSink`, `JSONLFileSink`, `MultiSink`) are
where events actually go, and are swappable independently of the Tracer's
emission logic. `JSONLFileSink` specifically writes one JSON object per
line (not one big JSON array) so a run's log file is valid and tailable
even if the process crashes mid-run — a direct, small consequence of taking
"failure is a first-class path" seriously even in the logging layer itself.

## 8. The `_compat` shim: an honest tradeoff, not a hidden one

`reliableagent._compat` exists because the development/test sandbox for
this project had no network access to install Pydantic, pytest, or any
other third-party package. Two choices were available: write the framework
against plain dicts (betraying the project's core "Explicit Contracts"
principle to work around an environment limitation), or implement the
actual Pydantic v2 API surface the codebase needs, as a fallback that's
preferred-away the instant real Pydantic is installed. The second was
chosen, and the tradeoff — and exactly which API surface is and isn't
covered — is documented in detail in
`src/reliableagent/_compat/_fallback.py`'s module docstring and in
[`adr/0001-pydantic-compat-shim.md`](../adr/0001-pydantic-compat-shim.md).
This is flagged here, prominently, rather than left for a reader to
discover by accident, because a fallback like this is exactly the kind of
thing that should be impossible to miss in a project's documentation.
