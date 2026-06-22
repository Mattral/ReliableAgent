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

## 9. The Evaluation Harness: metrics as pure functions over graded runs

`evaluation/metrics.py`'s `compute_metrics` takes a `list[GradedRun]` and
returns a `MetricsReport` — a pure function with no dependency on how
those `GradedRun`s were produced. This is why `tests/eval/test_metrics.py`
can verify the exact arithmetic of all five metrics (Task Success Rate,
Recovery Rate, Average Replanning Attempts, Guardrail Intervention Rate,
Failure Category Distribution) against small, hand-built fixtures, with
zero Orchestrator/Planner/LLM involvement, while `tests/eval/test_golden_
suite.py` separately verifies the *real* `Orchestrator` produces the
right `GradedRun`s in the first place. A metrics bug and an orchestration
bug can never be confused with each other, because they're tested in
complete isolation from one another.

The two-question split this enables is deliberate: "is a run's outcome
correctly graded" (the `GoldenTask.grade` function, tested in `test_
golden_task.py`) is a different concern from "given a batch of already-
graded outcomes, what do the aggregate numbers say" (`compute_metrics`,
tested in `test_metrics.py`), and conflating them into one "run the suite
and print some numbers" script would have made both harder to verify
independently — and would have hidden the `failure_category_distribution`
bug described in `adr/0004` for much longer, since the bug was specifically
in the boundary between "what the Orchestrator did" and "what the grader
decided," which only became visible once those two questions had separate,
explicit data structures (`RunResult.failure_category` vs.
`GradedRun.passed`) to be tested against independently.

Every golden task doubles as a `MockLLMClient` script AND a real-model-
ready task definition (see `adr/0004` for the full rationale and the two
concrete bugs this design caught during development). The practical
consequence: `examples/run_evaluation.py --use-real-anthropic-model
claude-sonnet-4-6` and the default offline mode differ by exactly one
constructor call (`llm_client_builder`, threaded through `evaluation/
factory.py::build_standard_factory`) — nothing about the golden tasks,
graders, metrics, or comparison tool needs to know or care which LLM
backend actually produced a given run's plan.

## 10. Phase 3: extension points, not replacements

Every Phase 3 capability was added as an opt-in extension to an existing
contract, never a breaking redefinition of one — this is the same
"Explicit Contracts" discipline from section 1, applied to evolving a
contract after the fact rather than just defining one upfront.

`Critic.critique_step()` is the clearest example: it's a concrete method
on the `Critic` ABC with a default body that returns `None`, not an
abstract method every subclass is forced to implement. A `ThresholdCritic`
written in Phase 1, with zero changes, remains a fully valid `Critic` in
a system that now also supports step-level process supervision — it just
has nothing to say at that extension point. The Orchestrator's
`_execute_step` calls `critique_step()` unconditionally and only attaches
a `StepCritique` to a `StepRecord` when one comes back non-`None`, so a
Phase 0-2-style `Trajectory` is byte-for-byte indistinguishable from one
produced by a Critic that simply chooses not to use the new capability.

`Replanner` follows the same pattern one layer up: it's not a
replacement for `Planner`, it's a thing that *uses* a `Planner` — the
Orchestrator's constructor defaults to wrapping whatever `Planner` it's
given in a `Replanner`, but a caller who passes their own `replanner=`
(or, hypothetically, monkey-patches `Orchestrator._replanner` to skip it
entirely) doesn't lose access to plain `Planner.plan()` semantics; the
`Replanner` only ever calls that same method with a shaped hint and
(optionally) an adjusted `Task`.

This pattern — new capability arrives as something existing components
can be wrapped in or extended with, never as a required reimplementation
— is also why `PolicyGuardrail` and `OutputFilterGuardrail` are ordinary
`Guardrail` subclasses rather than a new guardrail *protocol*: the
`GuardrailRunner` that chains them, and the `Orchestrator` boundaries that
invoke that runner, needed zero changes to support two genuinely new
guardrail behaviors (structured multi-rule policy matching, and PII
redaction), because the contract they implement was already expressive
enough (`check() -> GuardrailDecision`, ALLOW/BLOCK/MODIFY) to carry them.

The one Phase 3 change that *did* alter existing runtime behavior — every
successful run now makes one additional `Critic.critique()` call, fixing
the bug where `Trajectory.feedbacks` was silently empty on the common
completion path — was not a contract change at all; `Critic.critique()`'s
signature and meaning are unchanged. It's a control-flow fix in the
Orchestrator's loop, and is treated with corresponding care: documented
as a named, numbered bug in `adr/0005`, not folded silently into "Phase 3
adds process supervision" framing where a reader could easily miss that
it affects every Critic, including ones written before this phase existed.

## 11. Performance characteristics: what's fast, what's not, and why

Per Phase 4's "Performance profiling" deliverable,
`examples/profile_performance.py` profiles the full 20-task golden suite
with stdlib `cProfile` and attributes time to architectural layers. Two
honest framing points before the numbers themselves:

1. **Every number here is measured against `MockLLMClient`**, which has
   effectively zero latency. In any real deployment with a real LLM
   provider, network round-trip time to that provider will dominate
   total wall-clock time by one or two orders of magnitude over
   everything discussed below — these numbers describe ReliableAgent's
   *own* overhead, which is the thing actually under this project's
   control, not "how fast is an agent run" in any end-to-end sense.
2. **The golden suite's own design includes deliberate waiting** — the
   Executor's retry-backoff `time.sleep()`, triggered by the suite's
   intentional-failure tasks (`always_fails`, `flaky_lookup`). The
   profiler labels this time explicitly as "Deliberate waiting" rather
   than folding it into generic overhead, and `--no-retry-backoff`
   excludes it entirely for a cleaner view of pure computational cost.

With both of those accounted for, profiling surfaced one genuinely
significant, fixable finding: `typing.get_type_hints()`, called inside
`reliableagent._compat._fallback.BaseModel`'s `__init__`/`model_dump`/
`__repr__`/`__eq__`, was being re-resolved from scratch on *every single
model construction or inspection* rather than once per class. Caching it
per-class (`adr/0006`) measured a **4.85x speedup** for bare model
construction in an isolated microbenchmark, and roughly a **3.1x**
reduction in total wall-clock time for the full golden-suite profile
(excluding deliberate waiting). This is the single most impactful change
to come out of this delivery's profiling pass, and is the kind of finding
profiling is specifically good at surfacing that code review alone
typically would not: the original code was correct, well-documented, and
passed every test — its cost was invisible without actually measuring it.

### Complexity of the hot paths, by component

These are intentionally simple, worst-case-style observations about the
*shape* of the cost (how it scales with inputs), as a complement to the
profiler's *measured* numbers above — useful for reasoning about scaling
behavior the profiler's one fixed 20-task suite can't directly show.

- **`Orchestrator._execute_and_continue`**: `O(steps × (guardrail_count +
  tool_cost))` per plan, where `steps` is that plan's step count and
  `guardrail_count` is the number of registered guardrails. Replanning
  multiplies this by `replan_attempts`, bounded by `Task.max_replans`.
  There is no hidden quadratic behavior here: `results` (the accumulated
  `ToolResult` list passed to the Critic) grows linearly with total steps
  across the whole run, and is only ever appended to or iterated once per
  critique call, never re-scanned per-step.
- **`GuardrailRunner.run`**: `O(guardrails_at_this_boundary)`, with an
  early exit on the first BLOCK — a guardrail stack with many guardrails
  that mostly ALLOW pays the full linear cost on every call; a stack
  where an early guardrail frequently BLOCKs pays less in practice than
  the worst case suggests. `PolicyGuardrail` itself is
  `O(rules × text_length)` per check (one regex search per rule, each
  `O(text_length)` for a non-pathological pattern) — a deliberately
  simple cost model, not a rules-engine with its own scaling surprises.
- **`Executor.execute`**: `O(1)` plus the tool's own cost; the
  thread-pool submission/`future.result(timeout=...)` machinery is
  constant-overhead regardless of what the tool itself does. Retries
  multiply wall-clock time (not CPU time) by `1 + max_retries`, plus
  `Σ(retry_backoff_seconds × attempt)` of genuinely-intentional sleeping.
- **`FileMemoryBackend.load_latest_checkpoint`**: `O(checkpoints_in_run)`
  — a directory glob + lexicographic sort over that run's checkpoint
  files, not a scan of every run ever persisted, since checkpoints are
  namespaced by `run_id` into their own subdirectory.
- **`compute_metrics`** (`evaluation/metrics.py`): a single `O(n)` pass
  over the input `GradedRun` list per metric, computed independently per
  metric rather than one fused pass — a deliberate clarity-over-
  micro-optimization choice, since `n` here is bounded by the size of an
  evaluation suite (tens to low hundreds of runs in any realistic usage),
  not a per-request hot path.

None of the above identified a real scaling problem worth fixing in this
delivery — the `get_type_hints` finding was a genuine, measured win;
everything else here is `O(n)` or better in the inputs that actually
matter at this project's current scale, and is recorded as a baseline
for comparison if a future phase's profiling run finds otherwise.
