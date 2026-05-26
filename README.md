# ReliableAgent

**A reliability-first orchestration framework for agentic systems.**

Most agent frameworks optimize for "it worked in the demo." ReliableAgent
optimizes for the question every team asks right after that: *what happens
when it doesn't?* Guardrails are enforced at every boundary, not bolted on.
Failures are typed data in the trajectory, not log lines to grep for.
Every run is checkpointed and fully reconstructable. Long tasks can be
killed and resumed without re-planning from scratch.

247 tests. Zero required dependencies beyond Pydantic. Runs offline out of
the box.

---

## Table of contents

- [Install](#install)
- [60-second quickstart](#60-second-quickstart)
- [Why ReliableAgent](#why-reliableagent)
- [Architecture at a glance](#architecture-at-a-glance)
- [Measuring reliability, not just claiming it](#measuring-reliability-not-just-claiming-it)
- [Process supervision, smarter replanning, redaction](#process-supervision-smarter-replanning-redaction)
- [Performance, measured](#performance-measured)
- [The convenience API](#the-convenience-api)
- [Examples](#examples)
- [Project layout](#project-layout)
- [Development](#development)
- [A note on dependencies](#a-note-on-dependencies)
- [Honest status: what's here, what isn't](#honest-status-whats-here-what-isnt)
- [Changelog](#changelog)
- [License](#license)

---

## Install

```bash
pip install -e ".[dev]"
```

Requires Python ≥3.10. That's it — Pydantic v2 is the only runtime
dependency, and `MockLLMClient` means you can run everything below without
an API key. See [A note on dependencies](#a-note-on-dependencies) for how
this repo behaves with or without Pydantic installed.

## 60-second quickstart

```python
from reliableagent import Orchestrator, Task
from reliableagent.executor import ToolRegistry
from reliableagent.guardrails import BasicGuardrail
from reliableagent.llm import MockLLMClient
from reliableagent.planner import LLMPlanner, ThresholdCritic

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

To use a real model instead of the deterministic mock:

```python
from reliableagent.llm import AnthropicLLMClient

planner = LLMPlanner(AnthropicLLMClient(model="claude-sonnet-4-6"))
```

(requires `pip install 'reliableagent[anthropic]'` and an `ANTHROPIC_API_KEY`
in the environment, or pass `api_key=...` explicitly.)

Prefer fewer moving parts up front? See [The convenience API](#the-convenience-api)
for `ReliableOrchestrator`, a higher-level wrapper with the same
capabilities behind simpler flags.

Then, run the test suite and a narrated walkthrough:

```bash
python scripts/run_tests.py      # 247 tests, uses real pytest if installed
python examples/quickstart.py    # 4 scenarios: happy path, recovery, a
                                  # blocked guardrail, checkpoint/resume
```

## Why ReliableAgent

- **Guardrails are not a wrapper.** Every Planner input/output, every tool
  call's input/output, and the final answer pass through a configurable
  `GuardrailRunner` before they're trusted. A blocked check halts the
  transition — it never silently passes through.
- **Failure is data, not just an exception.** Tool failures, guardrail
  blocks, and replans are first-class, typed events in the trajectory, not
  something you have to grep logs for after the fact.
- **Every run is fully reconstructable.** Plans, step results, guardrail
  decisions, critic feedback, and checkpoints are all recorded in a single
  `Trajectory` object you can serialize, diff, and replay.
- **Long-running tasks can be killed and resumed.** Checkpoints are saved
  after every plan and step; `orchestrator.resume(run_id)` picks up
  exactly where a killed process left off, without re-calling the LLM for
  a plan it already had.
- **Reliability claims are numbers, not adjectives.** A 20-task golden
  suite with 5 standard metrics, plus a tool to compare configurations
  side by side — see [below](#measuring-reliability-not-just-claiming-it).

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
every module and the design decisions behind it, and [`adr/`](adr) for
the specific tradeoffs that were deliberated, not just assumed.

## Measuring reliability, not just claiming it

A curated suite of 20 "golden tasks" spans 5 categories (arithmetic, fact
lookup, failure recovery, guardrail enforcement, text processing), each
with a known-correct outcome and grader. Running it computes five
standard reliability metrics:

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

`examples/compare_configurations.py` runs the same suite under several
named configurations (guardrail strictness, Critic strategy, executor
retry settings) side by side, so "stricter guardrails improve
reliability" is a number, not an assertion:

```
Variant                        Success    Recovery   AvgReplans  GuardrailInt.
--------------------------------------------------------------------------------
guardrails_lenient              90.0%       85.7%         0.30          5.0%
guardrails_standard            100.0%      100.0%         0.30         15.0%
```

The suite runs entirely offline against `MockLLMClient` by default (free,
fast, and a genuine regression test of the orchestration engine itself);
pass `--use-real-anthropic-model` to run the identical tasks and graders
against a live model. See
[`adr/0004`](adr/0004-golden-task-suite-design.md) for why it's built
this way, including two real bugs this design caught during development.

## Process supervision, smarter replanning, redaction

Three things layer onto the core loop, all enabled by default — nothing
here is an opt-in you have to discover:

- **Process-supervision Critics** (`DeterministicProcessCritic`,
  `LLMProcessCritic`) score every plan on three separate criteria —
  correctness, efficiency, safety — instead of one blended number, and
  flag individual steps as they happen, not just at the end of a plan.
- **A strategy-driven `Replanner`** classifies *why* a replan is needed
  (a tool kept failing vs. progress stalled vs. budget nearly exhausted)
  and shapes a concretely actionable hint for each case, including
  deliberately shrinking the next plan's ambition once few attempts
  remain.
- **`PolicyGuardrail`** (structured, named, scoped rules — block or
  redact) and **`OutputFilterGuardrail`** (built-in regex-based PII
  redaction for emails, phone numbers, SSNs, and card numbers) extend
  guardrails beyond simple substring matching.

```bash
python examples/advanced_reliability.py
```

Building this surfaced two real, pre-existing bugs — a guardrail's
redaction was computed and logged but never actually applied to what a
run returns, and successful runs were silently skipping the Critic
entirely. Both fixed and regression-tested; full story in
[`adr/0005`](adr/0005-phase3-critic-replanner-guardrails.md).

## Performance, measured

`examples/profile_performance.py` profiles the full golden suite with
stdlib `cProfile` and attributes time to architectural layers. It found
one real, fixable bottleneck: the Pydantic-compatibility shim (see
[below](#a-note-on-dependencies)) was re-resolving each class's type
hints from scratch on every model construction. Caching it per-class
measured a **4.85x speedup** for bare model construction (isolated
50,000-iteration microbenchmark) and roughly **3.1x** less wall-clock
time for the full suite:

```bash
python examples/profile_performance.py --no-retry-backoff --repeat 5
```

Full writeup, including alternatives considered, in
[`adr/0006`](adr/0006-type-hints-caching-performance-fix.md); complexity
notes for every hot path in `docs/architecture.md` section 11.

## The convenience API

`Orchestrator` is the fully explicit, fully composable core — the right
choice once you need control over Planner/Critic/Memory/Guardrail
composition. `ReliableOrchestrator` and `EvaluationHarness` are thinner
wrappers over the same machinery, for quicker setup:

```python
from reliableagent import ReliableOrchestrator, ToolRegistry
from reliableagent.guardrails import BasicGuardrail
from reliableagent.evaluation import EvaluationHarness

orchestrator = ReliableOrchestrator(
    model="claude-sonnet-4-6",  # or llm_client=... for a mock/custom client
    tools=tools,
    guardrails=[BasicGuardrail()],
    enable_checkpointing=True,
    enable_observability=True,
)
result = orchestrator.run(task="...", max_steps=20)

harness = EvaluationHarness(orchestrator=orchestrator)
results = harness.evaluate(task_set="golden_suite_v1", seeds=[42, 43, 44])
print(results.summary())
print(results.failure_analysis())
```

```bash
python examples/roadmap_dx_example.py
```

Full design rationale — including the one real bug this specific wrapper
had and fixed (a multi-seed `MockLLMClient` queue-exhaustion issue) — in
[`adr/0008`](adr/0008-reliable-orchestrator-and-evaluation-harness-dx.md).

## Examples

Every script below is narrated, runs offline in well under a second, and
needs no API key.

| Script | What it shows |
|---|---|
| `examples/quickstart.py` | Core loop: happy path, replanning after a failure, a blocked guardrail, checkpoint/resume |
| `examples/advanced_reliability.py` | Process-supervision Critics, failure-aware replanning, policy + PII-redaction guardrails |
| `examples/roadmap_dx_example.py` | `ReliableOrchestrator` + `EvaluationHarness`, matching this project's own design brief almost verbatim |
| `examples/run_evaluation.py` | One-command evaluation: the 20-task suite, 5 metrics, failure analysis |
| `examples/compare_configurations.py` | Quantitative before/after comparison across guardrail/critic/executor configs |
| `examples/profile_performance.py` | Where does the time actually go? `cProfile` + a layer-by-layer breakdown |

## Project layout

```
src/reliableagent/
  core/            Task/Plan/Trajectory/RunResult models, OrchestratorState
                    machine, the Orchestrator control loop, and
                    ReliableOrchestrator (the convenience wrapper).
  llm/              Provider-agnostic LLMClient protocol + MockLLMClient
                    (deterministic, offline) + AnthropicLLMClient (real) +
                    LLMUsageStats/UsageTrackingLLMClient (token/latency).
  planner/          Planner ABC, LLMPlanner, Critic ABC, ThresholdCritic,
                    LLMCritic, DeterministicProcessCritic/LLMProcessCritic
                    (process supervision), Replanner + ReplanStrategy
                    implementations (failure-aware replanning), and
                    shared prompt-construction helpers.
  executor/         ToolRegistry (schema-validated tool registration +
                    output validation) and Executor (timeouts, retries,
                    structured error capture).
  guardrails/       Guardrail ABC, BasicGuardrail + 2 focused guardrails,
                    PolicyGuardrail (structured rule-based policy),
                    OutputFilterGuardrail (PII redaction), and the
                    GuardrailRunner that chains them per-boundary.
  memory/           MemoryBackend protocol, InMemoryBackend, FileMemoryBackend
                    (real on-disk checkpoint/trajectory persistence).
  observability/    Event model, pluggable sinks (in-memory/console/JSONL),
                    and the Tracer every component emits through.
  evaluation/       The curated 20-task golden suite, EvaluationRunner
                    (seed control + trajectory persistence), EvaluationHarness
                    (the convenience wrapper), the 5 reliability metrics,
                    failure analysis reports, and the configuration
                    comparison tool.
  exceptions/       The full exception hierarchy (recoverable vs not).
  _compat/          See "A note on dependencies" below.
tests/
  unit/             Fast, isolated tests per component (156 tests).
  integration/      Full Orchestrator runs against real components,
                    LLM-mocked only (27 tests).
  eval/             Metrics math, the golden suite running against the
                    real Orchestrator, configuration comparison, and
                    failure analysis (64 tests).
examples/           Runnable, narrated example scripts (table above).
adr/                Architecture Decision Records — real tradeoffs,
                    alternatives considered, and measured consequences.
docs/               Architecture deep-dive + exact roadmap completion status.
scripts/            run_tests.py (offline-friendly test runner),
                    verify_build.py (builds a real wheel, installs it into
                    a fresh venv, runs a real Orchestrator loop against it).
```

## Development

```bash
pip install -e ".[dev]"
python scripts/run_tests.py             # 247 tests
python scripts/verify_build.py          # build a real wheel + install-test it
ruff check src tests                    # lint
mypy                                    # type-check
pre-commit run --all-files              # everything pre-commit runs in CI
```

`.github/workflows/ci.yml` runs lint + type-check + the full test suite
(with coverage) on Python 3.10/3.11/3.12 on every push and PR.

## A note on dependencies

This project's declared dependency is real **Pydantic v2**
(`pyproject.toml`: `pydantic>=2.6,<3.0`). It was developed in a sandboxed
environment with no package-registry network access, where
`pip install pydantic` wasn't possible. Rather than write the framework
against loosely-typed dicts as a workaround — which would have betrayed
the project's own "explicit, typed contracts" principle —
`reliableagent/_compat/` ships a small, dependency-free fallback that
implements the exact slice of the Pydantic v2 API this codebase uses.
Every module imports `BaseModel`/`Field`/`field_validator`/
`model_validator`/`ConfigDict` from `reliableagent._compat`, which tries
real `pydantic` first and only falls back to the shim if it's
unavailable — so the moment you `pip install` this package normally, it
transparently uses real Pydantic with zero code changes. See the
docstring at the top of `src/reliableagent/_compat/_fallback.py` for the
full rationale.

The same pattern applies to `pytest`: `scripts/run_tests.py` uses real
`pytest` if it's importable, and only falls back to a small offline
runner (`tests/_pytest_shim/`) otherwise. Test files are 100% standard
pytest syntax and need no changes to run under real pytest.

This shim strategy was tested for real: the project owner ran the actual
CI workflow with real Pydantic installed, and it found one genuine
behavioral divergence between the shim and real Pydantic — fixed, with
the full story in [`adr/0010`](adr/0010-real-pydantic-field-validator-default-gap.md).

## Honest status: what's here, what isn't

**All 4 roadmap phases are implemented**, tested, and — as of a real CI
run on real infrastructure — partially verified against real Pydantic,
real pytest, and real ruff (not just this project's offline tooling).
[`docs/roadmap_status.md`](docs/roadmap_status.md) is the itemized,
unhedged comparison against every line of the original project brief,
including a "Post-Delivery Audit" section for gaps a later self-review
found, and a section on exactly what that first real CI run caught.

**Not implemented, by design or by honest omission:** multi-agent
coordination and a plugin/distribution ecosystem; sandboxing (tools run
in plain threads, no process isolation or resource limits); distributed
tracing/spans (only flat structured events exist); selective retrieval
in Memory backends (only full load-by-ID); test coverage has a real
number now (80.9% as of the first real CI run) but hasn't been acted on
further. `OutputFilterGuardrail`'s PII detection is regex-based, not an
ML classifier — stated plainly, not oversold.

The architecture is designed so all of the above can be added as new
modules without breaking existing contracts — a new `Guardrail` subclass
or `Planner` subclass plugs in without touching the `Orchestrator`, and a
new `ReplanStrategy` plugs into the existing `Replanner` without changes
to either.

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md) for a chronological record of what
was added, changed, and fixed at each stage, including every bug found
along the way and the ADR documenting it.

## License

MIT — see [`LICENSE`](LICENSE).
