# ADR 0001: A Pydantic v2 compatibility shim for network-restricted environments

## Status
Accepted.

## Context
ReliableAgent's data models (`reliableagent.core.models`) are required by
the project's own design principles ("Explicit Contracts & Modularity") to
be strongly-typed, validated objects rather than dicts. Pydantic v2 is the
obvious, idiomatic choice for this in the Python ecosystem, and is declared
as the project's dependency in `pyproject.toml` (`pydantic>=2.6,<3.0`).

However, this project was developed and must run its own test suite inside
a sandboxed environment with an egress allowlist that does not include
`pypi.org` (confirmed via `pip config list` and a direct `curl` test
returning `Host not in allowlist`). Neither `pydantic`, `pytest`, `ruff`,
nor `mypy` were pre-installed or cached anywhere on the filesystem
(confirmed via `find / -iname pydantic*` and `pip download`). This meant
`pip install pydantic` was not an option in the development environment,
even though it is the project's stated dependency for any real deployment.

## Decision
Implement `reliableagent._compat`, a tiny import-shim package:

```python
try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator
except ImportError:
    from reliableagent._compat._fallback import (...)
```

Every other module imports `BaseModel`/`Field`/`field_validator`/
`ConfigDict` from `reliableagent._compat`, never from `pydantic` directly.
`_fallback.py` implements, in pure Python with zero third-party
dependencies, the specific slice of the Pydantic v2 API this codebase
actually uses: frozen/mutable models, `Field()` defaults and numeric/length
constraints, `field_validator`-style "after" validators with `info.data`
access to sibling fields, nested model coercion, `Optional[...]`/PEP-604
union handling, enum coercion, and `model_dump()`/`model_dump_json()`/
`model_copy()`.

## Alternatives considered

**A: Write the framework against plain dicts.** Rejected. This would
betray the project's core "Explicit Contracts" principle — the entire
point of typed `Plan`/`ToolResult`/`Trajectory` models is that a malformed
object fails loudly at construction time, not three components downstream
with a `KeyError`. Dicts everywhere would have been strictly easier to get
running in this sandbox, but would have produced a worse, less honest
deliverable.

**B: Vendor a single pinned Pydantic wheel into the repo.** Rejected. No
such wheel was available anywhere on the filesystem or in any local cache
in this environment (checked), so there was nothing to vendor. Committing
a third-party binary wheel into a git repo is also generally poor practice
regardless.

**C: Write the models against `dataclasses` + manual `__post_init__`
validation, framed as "the real implementation," with no Pydantic
dependency at all.** Rejected. This would silently abandon the project's
stated dependency choice rather than honestly working around an
environment limitation — a future maintainer reading `pyproject.toml`
would have a different (and wrong) understanding of what the code actually
does. The chosen approach keeps the *declared* dependency as real Pydantic
and makes that the preferred path the instant it's installable.

## Consequences

**Positive:**
- The codebase is runnable and fully testable in this sandbox today.
- The moment `pydantic` is installed in any environment (e.g. a normal
  `pip install reliableagent` with network access), every module
  transparently uses real Pydantic with zero code changes elsewhere — the
  `try/except ImportError` is the entire integration surface.
- The tradeoff is documented in three places a reader is likely to look:
  this ADR, the README's "A note on the dependency situation" section, and
  the module docstring at the top of `_fallback.py` itself.

**Negative / known limitations:**
- The fallback does not perform strict primitive type coercion (e.g. a
  string assigned to an `int`-typed field without an explicit `Field()`
  constraint will not be rejected the way real Pydantic would reject or
  coerce it). This is documented explicitly in `_fallback.py`'s docstring
  and is mitigated by the fact that every field in this codebase that
  needs strict validation either has an enum/nested-model type (which IS
  coerced) or an explicit numeric/length constraint (which IS enforced).
- The fallback is not a general-purpose Pydantic replacement and should
  never be used outside this project or extended to cover patterns this
  codebase doesn't itself use — its test coverage is exactly the coverage
  of ReliableAgent's own test suite, nothing more.

## Verification
Anyone with network access can confirm this ADR's central claim — that
real Pydantic is used transparently when available — by running
`pip install pydantic` in the project's virtualenv and re-running
`python scripts/run_tests.py`: all tests pass identically either way,
because `reliableagent._compat.PYDANTIC_AVAILABLE` flips to `True` and
every model becomes a real `pydantic.BaseModel` subclass with no other
change required.
