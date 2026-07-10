# ADR 0011: The first real mypy run, and a second real ruff run — config
# scoping bugs and ~30 genuine type/lint fixes

## Status
Accepted.

## Context
Following the first real CI run documented in `adr/0010` (one Pydantic-
vs-shim bug found via real pytest), the project owner ran two more real
tools for the first time in this project's history: `mypy .` (351 errors)
and a second `ruff check` after the Phase-4-audit fixes from `adr/0007`-
`adr/0010` (204 errors, down from an initial 225).

The overwhelming majority of both counts traced to a single root cause,
not to hundreds of independent problems: `pyproject.toml`'s
`[tool.mypy]` declared `packages = ["reliableagent"]`, intended to scope
`strict = true` to the library itself when mypy is invoked bare. But
`mypy .` (a natural, common invocation) bypasses that scoping entirely
and applies full strict mode — including `disallow_untyped_defs` — to
every test, example, and script file too, none of which were ever meant
to be held to the library's own strictness bar (mirroring exactly the
reasoning already documented for ruff's `ANN` exemptions on those same
three directories).

## Decision

### Config fix (highest leverage, addressed first)
Added `[[tool.mypy.overrides]]` for `tests.*`, `scripts.*`, and
`examples.*` relaxing `disallow_untyped_defs`, `disallow_incomplete_defs`,
and `warn_return_any` — mirroring ruff's existing `per-file-ignores` for
the same three directories. This alone eliminates the large majority of
the 351 mypy errors (every "Function is missing a return type annotation"
on a test function), regardless of how mypy is invoked.

A second override relaxes `warn_return_any` specifically for
`reliableagent._compat._fallback` — by design (`adr/0001`) a from-scratch
reimplementation of the dynamically-typed slice of the Pydantic v2 API,
which legitimately needs `Any` throughout. Ruff's `per-file-ignores`
gained a matching `ANN401` exemption for the same file and reason.

### Genuine bugs fixed (not just annotation gaps)
Beyond the config scoping issue, both tools found real problems:

- **`_compat/__init__.py`'s conditional import confused mypy**: a classic,
  well-documented mypy limitation where `try: from pydantic import X /
  except ImportError: from _fallback import X` is statically analyzed on
  BOTH branches (even though only one ever runs), so mypy sees the
  fallback's `X` as "redefining" a name already bound to a different,
  incompatible type in the try block. Fixed with targeted, narrowly-
  justified `# type: ignore[assignment,no-redef]` — the standard fix for
  this exact, common pattern, not a design flaw.
- **`ToolSpec.result_validator: object = None`** — a genuinely too-weak
  placeholder type (should have been `Callable[[Any], bool] | None` from
  the start), causing real "object not callable" errors at every call
  site. Fixed in both `ToolSpec` and `ToolRegistry.register()`.
- **`examples/run_evaluation.py`**: a variable initialized as `None` then
  conditionally reassigned to a function was inferred as `None`-typed
  throughout by mypy (first-assignment inference), causing a real
  "object not callable" at its use site. Fixed with an explicit
  `Callable[[GoldenTask], LLMClient] | None` annotation up front.
- **Two `Any | None` union-attr accesses in `orchestrator.py` itself**
  (`GuardrailRunResult.blocking_decision` accessed without narrowing,
  even though the surrounding `if not result.allowed:` guarantees it's
  set) — fixed with explicit `assert ... is not None` statements that
  both satisfy mypy and document the actual invariant for a future
  reader, rather than a blind `# type: ignore`.
- **`examples/roadmap_dx_example.py`'s safe arithmetic evaluator**
  returned `Any` from a function declared `-> float` (since
  `ast.Constant.value` and `operator` module functions are typed loosely)
  — fixed with explicit `float(...)` coercion at each return, which is
  also a genuine runtime-correctness improvement (guarantees a `float`
  even if a caller passes a stray `int`-typed constant).
- **`examples/profile_performance.py`** accessed `pstats.Stats.stats`, a
  real, documented, public runtime attribute with no cleaner alternative
  API — but one that typeshed's stub for `pstats` doesn't declare. Fixed
  with a narrow, explained `# type: ignore[attr-defined]`, not a rewrite
  around a stub gap that isn't a real bug.
- **Several bare `dict`/`list` generic type annotations** (missing their
  `[str, Any]`/`[Guardrail]` type parameters) across `_compat/_fallback.py`,
  `planner/prompts.py`, `evaluation/golden_tasks.py`, `guardrails/runner.py`,
  and `tests/helpers.py` — all mechanical, safe fixes.
- **`planner/prompts.py`'s `safe_json_loads`** returned `dict` (untyped
  generic) and, separately, returned `Any` from `json.loads` against a
  declared `dict[str, Any]` return type. Fixed with a genuine runtime
  check (`raise ValueError` if the parsed JSON isn't actually a dict)
  rather than an unsafe blind cast — every call site already assumed a
  JSON object back, so this also makes a malformed-response failure mode
  clearer at the parse site instead of several lines later.
- **RUF022 (`__all__` not sorted)** in the 3 top-level `__init__.py`
  files — the comment-grouped-by-category style used throughout this
  delivery isn't respected by ruff's sorter; fully alphabetized all three
  as the pragmatic, standard-convention fix.
- **RUF100 (unused `noqa` directives)**: several `# noqa: BLE001`
  comments were "unused" because `BLE` (flake8-blind-except) was never
  actually added to the `select` list, despite clearly being the
  intended rule those comments were written against. Fixed by adding
  `BLE` to `select` (honoring the original intent) rather than deleting
  the justifications. One genuinely unneeded `# noqa: N802` was removed
  (on an already-lowercase function name that never violated N802).
- **UP037 (quoted type annotations)**: 8 files had `-> "ClassName":`
  forward-reference strings left over from before `from __future__ import
  annotations` made them unnecessary. Removed the quotes in all real code
  sites; one false-positive self-inflicted edit inside a *docstring* code
  example (never actually flagged by ruff, which doesn't lint docstring
  text) was found and reverted.
- **SIM102/SIM114, C408, F841, B017**: mechanical simplifications
  (combined nested `if`s, a `dict()` call rewritten as a literal, an
  unused test variable removed) plus one test tightened from
  `pytest.raises(Exception)` to `pytest.raises(ValueError)` — both real
  Pydantic's and this shim's `ValidationError` inherit from `ValueError`,
  so this is strictly more precise, not just quieter.
- **`types-PyYAML` added to dev dependencies** — mypy had no stubs for
  `yaml` at all, a straightforward, real gap.

### Union-attr narrowing in tests
Several tests accessed fields on `X | None`-typed values (`CriterionScores
| None`, `StepCritique | None`, `ToolResult | None`) returned by
legitimately-optional APIs, without first asserting non-None. Added
`assert x is not None` immediately before each access — this both
satisfies mypy and is better test-writing practice regardless (a failed
assert here gives a much clearer failure message than an `AttributeError`
on `None` would).

## Consequences

**Positive:** Real, load-bearing bugs were caught here that a design
review would not have found — the `object`-typed `result_validator` and
the `None`-typed `llm_client_builder` variable are both genuine "this
would break at the exact moment someone tries to use this feature" bugs,
not just missing decoration. The mypy config fix is durable and
independent of invocation style going forward.

**Negative / known limitations:** As with the ruff fixes in the prior
delivery, this project still cannot claim "mypy passes clean" — these
fixes address every specific error the project owner's two pasted runs
surfaced, but there is no way to re-run mypy in this sandboxed
environment to confirm a fresh run reports zero remaining errors.
