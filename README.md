# ReliableAgent

A reliability-first orchestration framework for building agentic systems
that don't just *work in the demo* — they fail safely, explain themselves,
and recover.

> **Scope of this build.** This repository implements **Phase 0
> (Foundations), Phase 1 (Core Orchestration), Phase 2 (Evaluation
> Harness & Reliability Measurement), and Phase 3 (Advanced Reliability
> Features: process-supervision Critic, strategy-driven Replanner,
> policy/output-filtering Guardrails)** of the full ReliableAgent roadmap,
> to a production-shape standard: real typed contracts, a working
> plan → execute → critique → replan control loop, guardrails enforced at
> every architectural boundary, checkpoint/resume, a curated 20-task
> golden suite with the five required reliability metrics, a
> configuration-comparison tool, multi-criteria + step-level Critic
> supervision, failure-type-aware replanning, and a passing test suite
> (207 tests, unit + integration + evaluation). Phase 4 (the plugin
> ecosystem) is intentionally out of scope for this delivery — see
> [`docs/roadmap_status.md`](docs/roadmap_status.md) for exactly what's
> done, what's stubbed, and what's not started.

## Why this exists

Most agent frameworks optimize for "it worked in the demo." ReliableAgent
optimizes for the second question every team asks after that: *what happens
when it doesn't?* Concretely, that means:

- **Guardrails are not a wrapper.** Every Planner input/output, every tool
  call's input/output, and the final answer pass through a configurable
  `GuardrailRunner` before they're trusted. A blocked guardrail check halts
  the transition — it never silently passes through.
- **Failure is data, not just an exception.** Tool failures, guardrail
  blocks, and replans are first-class, typed events in the trajectory, not
  log lines you have to grep for after the fact.
- **Every run is fully reconstructable.** Plans, step results, guardrail
  decisions, critic feedback, and checkpoints are all recorded in a single
  `Trajectory` object you can serialize, diff, and replay.
- **Long-running tasks can be killed and resumed.** Checkpoints are saved
  after every plan and step; `orchestrator.resume(run_id)` picks up exactly
  where a killed process left off, without re-calling the LLM for a plan it
  already had.

## Quickstart

```bash
pip install -e ".[dev]"          # editable install + dev tooling
python scripts/run_tests.py      # run the test suite (uses real pytest if
                                  # installed, else a bundled offline runner)
python examples/quickstart.py    # a runnable, narrated walkthrough
python examples/advanced_reliability.py      # Phase 3: process supervision, replanning, guardrails
python examples/run_evaluation.py            # one-command evaluation: metrics + failure analysis
python examples/compare_configurations.py    # quantitative before/after comparison across configs
```

```python
from reliableagent import Orchestrator, Task
from reliableagent.llm import MockLLMClient
from reliableagent.planner import LLMPlanner, ThresholdCritic
from reliableagent.executor import ToolRegistry
from reliableagent.guardrails import BasicGuardrail

tools = ToolRegistry()

@tools.register(description="Add two numbers")
def add(a: int, b: int) -> int:
    return a + b

orchestrator = Orchestrator(
    planner=LLMPlanner(MockLLMClient(responses=[...])),  # swap in AnthropicLLMClient for real calls
    critic=ThresholdCritic(),
    tools=tools,
    guardrails=[BasicGuardrail()],
)

result = orchestrator.run(Task(description="Add 2 and 3"))
print(result.final_answer)   # "The sum of 2 and 3 is 5."
print(result.metrics)        # RunMetrics(total_steps=2, total_tool_calls=1, ...)
```

To use a real LLM instead of the deterministic mock:

```python
from reliableagent.llm import AnthropicLLMClient

planner = LLMPlanner(AnthropicLLMClient(model="claude-sonnet-4-6"))
```

(requires `pip install 'reliableagent[anthropic]'` and an `ANTHROPIC_API_KEY`
in the environment, or pass `api_key=...` explicitly.)

## Architecture at a glance

```
Task
  │
  ▼
Planner ──plan──▶ [Guardrails: planner_input/output]
  │                         │
  │                         ▼
  │                    Executor ──▶ [Guardrails: tool_input/output] ──▶ Tool
  │                         │
  │                         ▼
  │                      Critic ──▶ Feedback(should_replan?)
  │                         │
  │              replan? ──┴── no: final_answer ──▶ [Guardrails: final_output] ──▶ Result
  │                  │
  └──────────────────┘
        (back to Planner, grounded in what went wrong)
```

Every box above also writes to: the `Trajectory` (the durable, structured
history of the run), a `Checkpoint` (so the run can be resumed), and the
`Tracer` (structured observability events). See
[`docs/architecture.md`](docs/architecture.md) for the full breakdown of
every module and the design decisions behind it, and
[`adr/`](adr) for the specific tradeoffs that were deliberated, not just
assumed.

## Measuring reliability, not just claiming it

Phase 2 adds a curated suite of 20 "golden tasks" spanning 5 categories
(arithmetic, fact lookup, failure recovery, guardrail enforcement, text
processing), each with a known-correct outcome and grader. Running it
computes five metrics:

```
$ python examples/run_evaluation.py
Task Success Rate:           100.0% (20/20)
Recovery Rate:                100.0%
Average Replanning Attempts:  0.30
Guardrail Intervention Rate:  15.0%
By category:
  - arithmetic: success=100.0% (4/4), avg_replans=0.25
  - failure_recovery: success=100.0% (4/4), avg_replans=1.00
  ...
```

And `examples/compare_configurations.py` runs the same suite under several
named configurations (guardrail strictness, Critic strategy, executor
retry settings) side by side, so a claim like "stricter guardrails improve
reliability" is a number, not an assertion:

```
Variant                        Success    Recovery   AvgReplans  GuardrailInt.
--------------------------------------------------------------------------------
guardrails_lenient              90.0%       85.7%         0.30          5.0%
guardrails_standard            100.0%      100.0%         0.30         15.0%
```

The suite runs entirely offline against `MockLLMClient` by default (so
it's free, fast, and a genuine regression test of the orchestration
engine itself); pass `--use-real-anthropic-model` to run the identical
tasks and graders against a live model instead. See
[`adr/0004-golden-task-suite-design.md`](adr/0004-golden-task-suite-design.md)
for why it's built this way, including two real bugs this design caught.

## Going beyond pass/fail: process supervision, smarter replanning, redaction

Phase 3 adds three things to the core loop, all enabled by default
(nothing here is an opt-in a caller has to discover):

- **Process-supervision Critics** (`DeterministicProcessCritic`,
  `LLMProcessCritic`) score every plan on three separate criteria —
  correctness, efficiency, safety — instead of one blended number, and
  flag individual steps as they happen, not just at the end of a plan.
- **A strategy-driven `Replanner`** classifies *why* a replan is needed
  (a tool kept failing vs. progress just stalled vs. budget is nearly
  exhausted) and shapes a different, concretely actionable hint for each
  case — including deliberately shrinking the next plan's ambition once
  few replan attempts remain.
- **`PolicyGuardrail`** (structured, named, scoped rules — block or
  redact) and **`OutputFilterGuardrail`** (built-in regex-based PII
  redaction for emails, phone numbers, SSNs, and card numbers) extend the
  guardrail layer beyond Phase 0/1's substring matching.

Building this phase surfaced two real, pre-existing bugs in the
Orchestrator — a guardrail's redaction was being computed and logged but
never actually applied to what a run returns, and successful runs were
silently skipping the Critic entirely. Both are fixed and regression-
tested; full story in
[`adr/0005-phase3-critic-replanner-guardrails.md`](adr/0005-phase3-critic-replanner-guardrails.md).

## Project layout

```
src/reliableagent/
  core/            Task/Plan/Trajectory/RunResult models, OrchestratorState
                    machine, and the Orchestrator control loop itself.
  llm/              Provider-agnostic LLMClient protocol + MockLLMClient
                    (deterministic, offline) + AnthropicLLMClient (real).
  planner/          Planner ABC, LLMPlanner, Critic ABC, ThresholdCritic,
                    LLMCritic, DeterministicProcessCritic/LLMProcessCritic
                    (Phase 3 process supervision), Replanner + ReplanStrategy
                    implementations (Phase 3 failure-aware replanning), and
                    shared prompt-construction helpers.
  executor/         ToolRegistry (schema-validated tool registration) and
                    Executor (timeouts, retries, structured error capture).
  guardrails/       Guardrail ABC, BasicGuardrail + 2 focused guardrails,
                    PolicyGuardrail (Phase 3: structured rule-based policy),
                    OutputFilterGuardrail (Phase 3: PII redaction), and the
                    GuardrailRunner that chains them per-boundary.
  memory/           MemoryBackend protocol, InMemoryBackend, FileMemoryBackend
                    (real on-disk checkpoint/trajectory persistence).
  observability/    Event model, pluggable sinks (in-memory/console/JSONL),
                    and the Tracer every component emits through.
  evaluation/       Phase 2: the curated 20-task golden suite, EvaluationRunner
                    (seed control + trajectory persistence), the 5 reliability
                    metrics, failure analysis reports, and the configuration
                    comparison tool (guardrail strictness / Critic strategy /
                    executor retries).
  exceptions/       The full exception hierarchy (recoverable vs not).
  _compat/          See "A note on the dependency situation" below.
tests/
  unit/             Fast, isolated tests per component (135 tests).
  integration/      Full Orchestrator runs against real components,
                    LLM-mocked only (16 tests).
  eval/             Phase 2 tests: metrics math, the golden suite running
                    against the real Orchestrator, configuration comparison,
                    and failure analysis (56 tests).
examples/           Runnable, narrated example scripts.
adr/                Architecture Decision Records for the non-obvious calls.
docs/                Architecture deep-dive + exact roadmap completion status.
```

## A note on the dependency situation

This project's declared dependency is real **Pydantic v2** (see
`pyproject.toml`: `pydantic>=2.6,<3.0`). It was developed and tested,
however, in a sandboxed environment with no package-registry network
access, where `pip install pydantic` was not possible. Rather than write the
framework against loosely-typed dicts as a result (which would have
betrayed the project's "Explicit Contracts" principle), `reliableagent/
_compat/` ships a small, dependency-free fallback that implements the exact
slice of the Pydantic v2 API this codebase uses. Every module imports
`BaseModel`/`Field`/`field_validator`/`ConfigDict` from
`reliableagent._compat`, which tries real `pydantic` first and only falls
back to the shim if it's unavailable — so the moment you `pip install`
this package normally (with network access), it transparently uses real
Pydantic with zero code changes. See the docstring at the top of
`src/reliableagent/_compat/_fallback.py` for the full rationale, and run
`pip install pydantic` yourself, then re-run the test suite, to confirm
this — it should pass identically either way.

The same situation applies to `pytest`: `scripts/run_tests.py` uses real
`pytest` if it's importable, and only falls back to a small offline
runner (`tests/_pytest_shim/`) otherwise. Test files are 100% standard
pytest syntax (`assert`, `pytest.raises`, plain test functions) and need no
changes to run under real pytest.

## What's deliberately NOT here yet

See [`docs/roadmap_status.md`](docs/roadmap_status.md) for the full,
itemized comparison against every requirement in the original roadmap.
Briefly: multi-agent coordination and the plugin/distribution ecosystem
(Phase 4) are not implemented. `OutputFilterGuardrail`'s PII detection is
regex-based, not an ML classifier — documented honestly as a known
limitation, not oversold. The architecture is designed so Phase 4 can be
added as new modules without breaking the existing P0/P1/P2/P3
contracts — e.g. a new `Guardrail` subclass or `Planner` subclass plugs
in without touching the `Orchestrator`, and a new `ReplanStrategy` plugs
into the existing `Replanner` without changes to either.

## License

MIT
