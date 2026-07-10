# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project has not yet had a versioned release, so everything to date is
recorded under [Unreleased]. Every entry that fixes a real bug links to the
ADR with the full root-cause story — this changelog gives the headline,
the ADR gives the reasoning.

## [Unreleased]

### Added — Phase 0: Foundations & DX
- Core typed models (`Task`, `Plan`, `PlanStep`, `ToolCall`, `ToolResult`,
  `Trajectory`, `RunResult`, `RunMetrics`) and the `OrchestratorState`
  state machine.
- `LLMClient` protocol with `MockLLMClient` (deterministic, offline) and
  `AnthropicLLMClient` (real).
- Exception hierarchy with explicit `recoverable` flags.
- A dependency-free Pydantic v2 compatibility shim (`_compat/`) so the
  project runs identically with or without real Pydantic installed — see
  [`adr/0001`](adr/0001-pydantic-compat-shim.md).

### Added — Phase 1: Core Reliable Orchestration
- `Orchestrator`: the full `Task -> Planner -> Guardrails -> Executor ->
  Critic -> (replan | finish)` control loop.
- `GuardrailRunner` + `BasicGuardrail`, `ToolArgumentSanityGuardrail`,
  `FinalOutputPolicyGuardrail`, enforced at every architectural boundary
  (planner input/output, tool input/output, final output).
- `Executor` with timeouts, retries with exponential backoff, and
  structured error capture.
- `ThresholdCritic` (heuristic) and `LLMCritic` (LLM-backed) plus
  `Replanner`-driven recovery.
- `MemoryBackend` protocol with `InMemoryBackend` and `FileMemoryBackend`
  for real on-disk checkpoint/trajectory persistence, plus
  `Orchestrator.resume(run_id)`.
- Structured observability: `Tracer`, pluggable `EventSink`s
  (in-memory / console / JSONL).
- `examples/quickstart.py`: happy path, replanning after failure, a
  blocked guardrail, and checkpoint/resume across a simulated restart.

### Added — Phase 2: Evaluation Harness & Reliability Measurement
- A curated 20-task golden suite across 5 categories (arithmetic, fact
  lookup, failure recovery, guardrail enforcement, text processing), each
  dual-purpose: scripted for offline `MockLLMClient` runs today, and
  real-model-ready via `--use-real-anthropic-model` with a one-flag
  change — see [`adr/0004`](adr/0004-golden-task-suite-design.md).
- `EvaluationRunner` with seed control and trajectory persistence.
- The five standard reliability metrics: Task Success Rate, Recovery
  Rate, Average Replanning Attempts, Guardrail Intervention Rate,
  Failure Category Distribution (`evaluation/metrics.py`).
- `analyze_failures` / `FailureAnalysisReport` for categorized failure
  analysis.
- `compare_configurations` + three named variant-set builders (guardrail
  strictness, Critic strategy, executor retries), so reliability claims
  are measured, not asserted.
- `examples/run_evaluation.py` and `examples/compare_configurations.py`.

### Fixed — Phase 2
- **Failure-category-distribution semantics bug**: the metric originally
  keyed off `RunResult.failure_category is not None`, double-counting
  intentionally-failing golden tasks (whose *correct* behavior is an
  Orchestrator-level `FAILED`) as reliability gaps even when their
  grader said they passed. Fixed to key off `GradedRun.passed` instead.
  ([`adr/0004`](adr/0004-golden-task-suite-design.md))
- **A golden task that didn't test what it claimed**: `flaky_lookup`
  needed only one scripted failure, but the Executor's own default
  `max_retries=1` silently absorbed it before the Critic ever saw it —
  the task passed with zero replans, never exercising the recovery path
  its name promised. Fixed by making the tool fail twice.
  ([`adr/0004`](adr/0004-golden-task-suite-design.md))

### Added — Phase 3: Advanced Reliability Features
- `Critic.critique_step()`: an optional, default-`None` extension point
  for step-level process supervision, with `DeterministicProcessCritic`
  and `LLMProcessCritic` implementing it.
- `CriterionScores`: multi-criteria scoring (correctness / efficiency /
  safety) instead of one blended `quality_score`, with a
  `weighted_overall()` method using configurable weights.
- `Replanner`: classifies *why* a replan is needed (`FailureType`:
  repeated tool failure / low-quality progress / ambiguous-or-
  underspecified / budget nearly exhausted) and delegates to a matching
  `ReplanStrategy`, each producing a concretely actionable hint instead
  of a generic re-prompt. Wired as the Orchestrator's default replanning
  behavior, not an opt-in.
- `PolicyGuardrail`: structured, named, scoped policy rules (block or
  redact via regex).
- `OutputFilterGuardrail`: built-in PII redaction (email, US phone, US
  SSN, credit card).
- `examples/advanced_reliability.py`.

### Fixed — Phase 3
- **Guardrail MODIFY verdicts were computed and logged but never
  applied**: `Orchestrator._guard()` checked whether a guardrail
  allowed a transition but discarded the guardrail's modified payload,
  so `OutputFilterGuardrail`'s redaction never actually reached
  `RunResult.final_answer`. Fixed by threading the modified payload
  through at the `FINAL_OUTPUT` boundary.
  ([`adr/0005`](adr/0005-phase3-critic-replanner-guardrails.md))
- **Successful runs silently skipped the Critic**: a plan completing via
  an explicit `final_answer` step never called `Critic.critique()` at
  all, so `Trajectory.feedbacks` was empty for the common case. Fixed by
  adding a final critique call on that path — note this means every
  successful run now makes one additional Critic call (free for
  heuristic Critics, one extra LLM call for `LLMCritic`).
  ([`adr/0005`](adr/0005-phase3-critic-replanner-guardrails.md))

### Added — Phase 4: Polish, Documentation & Impact
- `examples/profile_performance.py`: `cProfile`-based profiling with a
  component-level (Planner/Executor/Guardrails/Critic/Memory/
  Observability) time breakdown, plus a `--no-retry-backoff` flag to
  isolate computational cost from deliberate retry waiting.
- Complexity/Big-O notes for every hot path in `docs/architecture.md`.
- `docs/portfolio_summary.md`: a one-page orientation document.

### Fixed — Phase 4
- **`get_type_hints()` re-resolved on every model construction**: the
  Pydantic compat shim called `typing.get_type_hints()` fresh inside
  `__init__`/`model_dump`/`__repr__`/`__eq__`, instead of once per class.
  Caching it measured a **4.85x speedup** for bare model construction
  and ~3.1x less wall-clock time for the full golden suite.
  ([`adr/0006`](adr/0006-type-hints-caching-performance-fix.md))

### Added — Post-Delivery Audit
A self-audit re-read the original roadmap against the actual code (not
against prior summaries) and found several gaps not previously flagged:

- `RunMetrics` now carries real token usage and LLM latency
  (`LLMUsageStats`, `UsageTrackingLLMClient`), computed as a correct
  per-run delta rather than a tracker's lifetime cumulative total.
  ([`adr/0009`](adr/0009-token-metrics-and-output-validation.md))
- Tool *output* is now actually validated, not just tool input:
  `result_validator=` on `ToolRegistry.register()`, wired into the
  Executor as a retryable failure mode.
  ([`adr/0009`](adr/0009-token-metrics-and-output-validation.md))
- `ReliableOrchestrator` and `EvaluationHarness`: convenience wrappers
  matching this project's own illustrative usage example almost
  verbatim (`examples/roadmap_dx_example.py`).
  ([`adr/0008`](adr/0008-reliable-orchestrator-and-evaluation-harness-dx.md))
- `Orchestrator` gained public, read-only introspection properties
  (`.planner`, `.critic`, `.tools`, `.guardrails`, `.memory`,
  `.executor`, `.replanner`, `.sink`); `GuardrailRunner` gained a public
  `.guardrails` property. `ToolRegistry` is now exported from the
  top-level `reliableagent` package.
- `scripts/verify_build.py`: builds a real wheel, installs it into a
  fresh virtual environment, and runs a real `Orchestrator` loop against
  the installed copy — not the `src/` checkout.
  ([`adr/0007`](adr/0007-package-build-verification.md))

### Fixed — Post-Delivery Audit
- **The package had never been built or installed, even once**: the
  declared `hatchling` build backend was never available in this
  project's sandboxed development environment. Switched to `setuptools`
  (already available offline) and added `scripts/verify_build.py` to
  make this a real, repeatable, automated check.
  ([`adr/0007`](adr/0007-package-build-verification.md))
- **`EvaluationHarness` exhausted `MockLLMClient`'s response queue
  across multiple seeds**: the mock-backed path built one scripted
  Orchestrator per golden task and reused it across every seed,
  silently failing every seed after the first with a spurious planning
  error. Looked correct under `seeds=[0]`; only surfaced running the
  roadmap's own `seeds=[42, 43, 44]` example. Fixed by building a fresh
  scripted Orchestrator per (task, seed) pair.
  ([`adr/0008`](adr/0008-reliable-orchestrator-and-evaluation-harness-dx.md))
- **A real `eval()` call in an example script**: replaced with a safe,
  AST-based arithmetic evaluator rather than suppressed with a `noqa`
  comment.

### Fixed — First real CI run
The project owner ran the real GitHub Actions CI workflow with real
Pydantic, pytest, and ruff installed for the first time — the first
genuine, non-sandboxed verification this project had ever received.

- **A real Pydantic-vs-shim behavioral divergence**: `PlanStep`'s
  "`tool_name` required for `TOOL_CALL` steps" check was a
  `@field_validator` reading another field via `info.data`. Real
  Pydantic v2 silently skips a `field_validator` on a field that falls
  back to its default rather than being explicitly supplied — this
  project's compat shim validated every field unconditionally,
  defaults included, so the same test passed offline while testing
  nothing. Fixed with `@model_validator(mode="after")` (added to the
  compat shim, since it didn't exist there before), which runs
  correctly under both backends.
  ([`adr/0010`](adr/0010-real-pydantic-field-validator-default-gap.md))
- **CI matrix jobs showing as "cancelled"**: GitHub Actions' default
  `fail-fast: true` canceling the rest of the Python-version matrix once
  one job failed. Added `fail-fast: false` so every version reports
  independently.
- **225 real ruff findings on first run**: narrowed the `ANN`
  (annotations) ruleset with written justification (it was demanding
  return-type annotations on 99 private helpers and 47 dunder methods
  project-wide); fixed every reported import-sorting (`I001`) and
  line-length (`E501`) violation found across two subsequent real runs
  (225 -> 204 -> addressed project-wide); replaced the `eval()` noted
  above; added three precise, justified `# noqa: S603` comments on
  verified-safe `subprocess` calls in build tooling.

### Fixed — Second real CI run (mypy + a second real ruff run)
The project owner ran `mypy .` for the first time (351 errors) and
`ruff check` a second time after the fixes above (204 errors, down from
225). Both counts traced overwhelmingly to one root cause, plus roughly
30 genuine, individually-fixed bugs.

- **The mypy config never actually scoped `strict` mode away from tests/
  scripts/examples**: `packages = ["reliableagent"]` only takes effect
  when mypy is invoked bare; running `mypy .` (a natural, common
  invocation) bypasses it entirely and applies full strict mode,
  including `disallow_untyped_defs`, to every test/example/script file —
  none of which were ever meant to be held to the library's own bar
  (mirroring ruff's existing `ANN` exemptions for the same three
  directories). Fixed with explicit `[[tool.mypy.overrides]]` for
  `tests.*`/`scripts.*`/`examples.*`, which alone resolved the large
  majority of the 351 errors. ([`adr/0011`](adr/0011-second-real-ci-run-mypy-and-ruff-fixes.md))
- **`ToolSpec.result_validator: object = None`**: a genuinely too-weak
  placeholder type, causing real "object not callable" errors at every
  call site. Fixed to `Callable[[Any], bool] | None`.
- **A `None`-initialized variable later reassigned to a function** in
  `examples/run_evaluation.py` was inferred as permanently `None`-typed
  by mypy (first-assignment inference), causing a real "object not
  callable" at its use site. Fixed with an explicit `Callable[...] | None`
  annotation up front.
- **Two real union-attr gaps in `orchestrator.py` itself**
  (`GuardrailRunResult.blocking_decision` accessed without narrowing)
  fixed with explicit `assert ... is not None` statements documenting
  the actual invariant, not blind `type: ignore`s.
- **`safe_json_loads` returned `Any` from a declared `dict[str, Any]`
  return type** — fixed with a genuine runtime check (raises `ValueError`
  if the parsed JSON isn't actually an object) rather than an unsafe
  blind cast, which also surfaces a malformed-response failure mode more
  clearly than letting it fail several lines later.
- A well-documented mypy limitation with `try/except ImportError`
  conditional-same-name imports in `_compat/__init__.py`, a real
  `pstats.Stats.stats` typeshed stub gap in `profile_performance.py`,
  several bare `dict`/`list` generics missing type parameters, `RUF022`
  (`__all__` sorting, fully alphabetized in 3 files), `RUF100` (unused
  `noqa`s — `BLE` was never actually added to ruff's `select` despite
  several `# noqa: BLE001` comments already assuming it; added the rule
  rather than deleting the justifications), `UP037` (quoted annotations
  no longer needed with `from __future__ import annotations`, fixed in
  8 files), and several `SIM`/`C408`/`F841`/`B017` mechanical
  simplifications. `types-PyYAML` added to dev dependencies (mypy had no
  stubs for `yaml` at all). Full accounting in
  [`adr/0011`](adr/0011-second-real-ci-run-mypy-and-ruff-fixes.md).

### Documentation
- `README.md` rewritten for clarity and DX: a scannable table of
  contents, install/quickstart pushed to the top, and the detailed
  audit/bug-fix narrative consolidated into this changelog and
  `docs/roadmap_status.md` rather than sprawling across the README
  itself.
- `docs/roadmap_status.md`: itemized, unhedged comparison against every
  requirement in the original project roadmap, including sections for
  the post-delivery audit and both real CI runs.
- `docs/architecture.md`: an 11-section architecture deep-dive covering
  every module's design decisions, extension points, and performance
  characteristics.
- 11 Architecture Decision Records (`adr/0001`-`adr/0011`), each stating
  a real tradeoff, the alternatives considered and rejected, and the
  measured consequences.
