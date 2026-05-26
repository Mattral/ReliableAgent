# ADR 0010: A real bug found by real CI — `field_validator` does not run
# on defaulted fields under real Pydantic, unlike this project's fallback
# shim

## Status
Accepted.

## Context
This project's entire test suite (247 tests as of this ADR) was developed
and passed exclusively against this project's own dependency-free Pydantic
compat shim (`reliableagent._compat._fallback`, see `adr/0001`), since the
sandboxed development environment never had network access to install real
`pydantic`. Every prior status document flagged this plainly as an
unverified gap.

The project owner has since run the real GitHub Actions CI workflow on
real infrastructure, with real `pydantic`, `pytest`, `ruff`, and `mypy`
installed. The very first real run found exactly what this gap predicted
it might: **one real, genuine test failure that never manifested offline**.

```
FAILED tests/unit/test_models.py::test_plan_step_requires_tool_name_for_tool_call
    with pytest.raises(ValueError):
E   Failed: DID NOT RAISE ValueError
```

## Root cause
`PlanStep`'s validation that `tool_name` is required when `step_type ==
TOOL_CALL` was implemented as:

```python
@field_validator("tool_name")
@classmethod
def _tool_name_required_for_tool_calls(cls, v, info):
    step_type = info.data.get("step_type")
    if step_type == StepType.TOOL_CALL and not v:
        raise ValueError(...)
    return v
```

This is a well-known Pydantic v2 behavior that this project's own compat
shim did not replicate: **a `field_validator` does NOT run on a field that
falls back to its declared default rather than being explicitly supplied
by the caller**, unless that field's `Field(...)` sets
`validate_default=True` (which this one did not). The failing test
constructs `PlanStep(step_type=StepType.TOOL_CALL, description="missing
tool name")` — `tool_name` is never passed, so it silently takes its
default of `None`, and under real Pydantic the validator is skipped
entirely. Under this project's fallback shim, by contrast, the
constructor validated every resolved field unconditionally, INCLUDING
ones using their default — so the exact same test passed offline while
silently testing nothing.

This is a textbook illustration of the constraint documented from the
start (`adr/0001`, `adr/0003`): a hand-written compatibility shim can
only be as faithful as its author's knowledge of every corner of the
real library's behavior, and some divergences are only discoverable by
actually running the real thing.

## Decision
1. Fixed the specific bug: replaced the `field_validator("tool_name")` +
   `info.data` pattern with `@model_validator(mode="after")`, which
   ALWAYS runs once the full model — every field, defaults included —
   is constructed, in both real Pydantic and this shim. This is also the
   textbook-correct tool for any "field A required when field B has
   value X" cross-field invariant, independent of this bug.
2. Added `model_validator(mode="after")` support to the fallback shim
   (`_compat/_fallback.py`), since it didn't exist at all before this —
   the shim previously only supported `field_validator`. Exported it
   from `_compat/__init__.py` alongside the existing exports.
3. Audited every other `field_validator` in the codebase for the same
   danger pattern (`info.data` cross-field access on a field with a
   default): found exactly one other `field_validator`
   (`Task.description`), which validates a REQUIRED field with no
   cross-field dependency and is therefore unaffected — confirmed by
   grepping for every `info.data` usage in the entire codebase (exactly
   one occurrence, the one just fixed).
4. Added `tests/unit/test_compat_validators.py`, specifically testing
   the omitted-argument case (the exact regression) alongside the
   already-covered explicit-`None` case, so this exact class of bug is
   now caught regardless of which Pydantic backend is active.

## Consequences

**Positive:**
- The actual behavioral bug (a plan step could be constructed with
  `step_type=TOOL_CALL` and no `tool_name`, which the Orchestrator/
  Executor would then fail on far later and less clearly, deep inside
  tool dispatch, rather than at construction time where the mistake was
  made) is fixed under both real Pydantic and the shim.
- `model_validator` is now available in the shim for any FUTURE
  cross-field validation need, closing this specific gap in the shim's
  API surface, not just this one call site.
- This is a genuinely valuable, concrete instance of "if you have
  network access, run the real tools and see what they catch" — exactly
  the caveat every prior status document recommended, now validated in
  practice with a real, fixable finding rather than a hypothetical one.

**Negative / known limitations:**
- This was found by luck of test coverage (the one test that happened to
  hit this exact validator) rather than by systematic verification that
  every `field_validator` in the shim behaves identically to real
  Pydantic across every edge case (defaults, `validate_default=True`,
  `mode="before"`, etc.). The shim remains a best-effort approximation,
  not a byte-for-byte behavioral clone, and this ADR should not be read
  as "now the shim is proven fully faithful" — only "this one specific,
  now-understood gap is closed."
- The coverage report from this same CI run
  (`_compat/_fallback.py`: 0% coverage under real Pydantic) confirms the
  shim's own code is never exercised at all once real Pydantic is
  installed — which is correct, intended behavior (`adr/0001`), but also
  means any other latent shim-vs-real divergence would similarly only
  surface when a test happens to exercise the exact affected code path
  under BOTH backends, as this one did.
