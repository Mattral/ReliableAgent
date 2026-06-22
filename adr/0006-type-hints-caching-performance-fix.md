# ADR 0006: Cache `get_type_hints()` per class in the Pydantic compat shim

## Status
Accepted.

## Context
Phase 4 calls for "Performance profiling." Running
`examples/profile_performance.py` (stdlib `cProfile`, deterministic
call-counting, over the full 20-task golden suite) surfaced a clear,
single dominant cost once LLM-call latency and the golden suite's
deliberate retry-backoff sleeps were excluded from the picture (via
`--no-retry-backoff`): `typing.get_type_hints` was the single most
expensive function in the entire profiled call graph, called 927 times
for a 20-task run, at roughly 0.18s cumulative out of roughly 0.28s
total -- well over half of all non-sleep time.

The root cause: `reliableagent._compat._fallback.BaseModel` (the
dependency-free Pydantic v2 stand-in documented in `adr/0001`) called
`get_type_hints(cls)` fresh inside `__init__`, `model_dump`, `__repr__`,
and `__eq__` -- i.e. on every single model construction and on every
dump/repr/equality check, not once per class. `get_type_hints` is
significantly more expensive than a raw attribute lookup specifically
because this codebase uses `from __future__ import annotations`
throughout (per its own style, for forward-reference-friendly type
hints), which means every annotation is stored as a string and must be
evaluated via the typing module's resolution machinery on each call --
work that produces an IDENTICAL result every time for a given class,
since a class's annotations never change after it's defined.

## Decision
Add a module-level cache, `_type_hints_cache: dict[type, dict[str, Any]]`,
and a `_cached_type_hints(cls)` helper that resolves `get_type_hints(cls)`
once per class and reuses the cached result for every subsequent call.
Replace all 4 call sites inside `BaseModel` with the cached version. The
cache is never invalidated, which is correct and safe specifically
because Python classes (as opposed to instances) are not mutated after
definition anywhere in this codebase or, more generally, in normal usage
of a Pydantic-style model library.

## Alternatives considered

**A: Cache type hints at class-definition time in `_BaseModelMeta.__new__`
instead of lazily on first use.** Considered, and arguably slightly
"more correct" in spirit (compute once, eagerly, rather than once,
lazily) -- but `get_type_hints` needs every referenced name to already
be resolvable in the class's module namespace, and depending on import
order, calling it from inside `__new__` (i.e. at class-body-execution
time, which can happen before sibling classes in the same module are
fully defined) risks `NameError`s for forward references to types
defined later in the same file. Lazy, first-use caching sidesteps this
entirely: by the time any instance of a model is actually constructed,
the whole module has finished importing, so every forward reference is
guaranteed resolvable. The cost is a one-time `if cached is None` branch
per class instead of zero, which is immaterial next to the per-call
`get_type_hints` cost being eliminated entirely after that first call.

**B: Switch to `dataclasses` instead of a hand-rolled Pydantic-style
`BaseModel`.** Rejected for reasons unrelated to this specific
performance question -- see `adr/0001`'s "Alternatives considered" for
why a dict-or-dataclass-only approach would have betrayed the project's
"Explicit Contracts" principle (no `Field()` constraints, no
`field_validator`, no `model_dump(mode="json")`). This ADR is about
fixing a real performance bug in the existing shim, not re-opening that
earlier, separately-justified decision.

**C: Avoid `from __future__ import annotations` to make annotations
directly inspectable without `get_type_hints`'s string-evaluation step.**
Rejected: this would mean giving up forward-reference support (e.g.
`Plan.steps: list["PlanStep"]` referencing a class defined later in the
same file, or models referencing each other circularly) throughout the
entire codebase, for every module, to fix a performance issue that's
fully and correctly addressed by caching alone. The cure would be worse
than the disease.

## Consequences

**Positive (measured, not estimated):**
- A direct, isolated, before/after microbenchmark (monkeypatching the
  cache helper to force the old uncached behavior, then restoring it,
  both within the same process and JIT/warm-cache state) measured
  **136.60 microseconds/construction without the cache vs. 28.15
  microseconds/construction with it -- a 4.85x speedup** for `Task()`
  construction alone, averaged over 50,000 constructions each.
- Re-running the golden-suite profile after the fix (excluding retry
  backoff sleep) showed total wall-clock time per run drop from
  approximately 310ms to approximately 99ms (roughly 3.1x), with total
  function calls dropping from 392,750 to 162,425 (~59% fewer) for the
  full 20-task suite -- `get_type_hints` no longer appears anywhere in
  the top functions by cumulative time afterward.
- This is a zero-risk change in the sense that matters most: the cache
  key is the class object itself, the cached value is read-only data
  derived purely from that class's own (immutable, post-definition)
  annotations, and the full 207-test suite passes identically before and
  after.

**Negative / known limitations:**
- This fix is specific to `reliableagent._compat._fallback`'s shim. Real
  Pydantic v2 (used automatically the instant it's installed, per
  `adr/0001`) has its own, separately-engineered, almost certainly
  faster validation core (compiled Rust via `pydantic-core`) that this
  ADR's finding says nothing about one way or the other -- this fix only
  matters for, and was only measured in, the no-network-access fallback
  configuration this delivery was built and tested under.
- The cache is process-global and never evicted. This is correct for
  this codebase's actual usage pattern (a small, fixed, known-at-import-
  time set of model classes), but would not be the right design for a
  hypothetical system that dynamically generates an unbounded number of
  distinct model classes at runtime -- not a pattern used anywhere in
  ReliableAgent.
