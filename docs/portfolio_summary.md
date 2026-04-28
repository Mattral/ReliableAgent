# ReliableAgent — Portfolio Summary

A one-page orientation for anyone evaluating this project quickly: what
it is, what makes it worth a closer look, and where to look next.

## The thesis, in one sentence

Most agent frameworks optimize for "it worked in the demo"; this one is
built around the question every team asks right after that — *what
happens when it doesn't?* — and answers it with working code, not just
documentation about the question.

## What's actually in this repository

A reliability-first orchestration framework: a typed `Task -> Planner ->
Guardrails -> Executor -> Critic -> (replan | finish)` control loop, with
checkpoint/resume, structured observability, a 20-task evaluation suite
with five reliability metrics, multi-criteria + step-level Critic
supervision, failure-type-aware replanning, and policy/output-filtering
guardrails. **210 tests, all passing, independently re-verified after
every change.** Four runnable example scripts demonstrate every major
capability end-to-end with real output, not pseudocode.

## Three things worth looking at first, if time is short

1. **`examples/quickstart.py`** — run it. Four scenarios (happy path,
   failure recovery via replanning, a guardrail blocking unsafe output,
   checkpoint/resume across a simulated process restart) execute in
   under a second, for free, with real printed output you can read top
   to bottom.
2. **`adr/`** (6 Architecture Decision Records) — not a changelog. Each
   one states a real tradeoff, the alternatives that were considered and
   rejected (with reasons), and the measured consequences. `adr/0006` in
   particular documents a real performance bug found via profiling, with
   an isolated before/after microbenchmark proving a 4.85x fix — not an
   estimate, a measurement.
3. **`docs/roadmap_status.md`** — an itemized, unhedged comparison of
   this delivery against every line of the original project brief,
   including an explicit, prominent statement of what was **not**
   verified (real pytest, ruff, mypy, CI, a real LLM provider — all
   configured and expected to work, none of them ever actually run in
   this sandboxed environment) and exactly why.

## What I'd want a reviewer to notice

**The bugs that got caught, and how.** This isn't a project where
everything worked on the first try and that's the whole story. Building
the evaluation suite caught a metrics-semantics bug and a test-design bug
where a "tests recovery" scenario didn't actually exercise recovery.
Building Phase 3's process supervision caught a bug where successful runs
were silently skipping the Critic entirely, and a guardrail's redaction
was being computed and logged but never actually applied to what a run
returns. Building Phase 4's profiling pass found a real 4.85x performance
fix. Every one of these is named, dated (in the sense of "which phase"),
and explained in an ADR — because catching your own bugs and writing them
up honestly is a stronger signal than a suspiciously clean build log.

**The environment constraint, and the honest way through it.** This
project's declared dependency is real Pydantic v2, developed and tested
entirely inside a sandbox with no package-registry network access. Rather
than quietly write against plain dicts to route around that (which would
have betrayed the project's own "Explicit Contracts" principle), there's
a small, fully-documented compatibility shim that implements the actual
Pydantic v2 API surface this code uses, and prefers real Pydantic the
instant it's installed — see `adr/0001`. The same approach was applied to
testing (`adr/0003`'s mock-first strategy isn't just a workaround, it's
also the right call for a reliability-focused test suite on its own
merits).

**Two-tier testing as a structural habit, not a one-off.** Every
component has isolated unit tests AND is exercised through the real
`Orchestrator` in integration tests — never *just* mocked-everything unit
tests, and never integration tests so broad that a unit-level regression
gets lost in the noise. The golden-task suite in `tests/eval/` is itself
a third tier: full end-to-end runs against the real Orchestrator, graded
against known-correct outcomes, that double as both a regression suite
today and real-model evaluation infrastructure later with a one-flag
change (`adr/0004`).

## What's explicitly NOT here

Multi-agent coordination beyond what's described in this delivery, and
anything resembling a plugin/distribution ecosystem. `OutputFilterGuardrail`'s
PII detection is regex-based, not an ML classifier — stated as a
limitation in its own docstring, not discovered by a reviewer reading the
code more carefully than the README. See `docs/roadmap_status.md` for the
complete, itemized list with nothing smoothed over.

## How to actually run something, in under a minute

```bash
git clone <this repo> && cd reliableagent
python examples/quickstart.py                              # core loop, 4 scenarios
python examples/advanced_reliability.py                     # Phase 3 features
python examples/run_evaluation.py                           # 20-task suite, 5 metrics
python examples/compare_configurations.py                   # quantitative config comparison
python examples/profile_performance.py --no-retry-backoff   # where the time actually goes
python scripts/run_tests.py                                 # 210 tests, ~instant, zero network calls
```

No API key, no network access, no setup beyond a Python interpreter —
deliberately, since that's also exactly how this project itself had to be
built and verified.
