# ADR 0007: Switch the build backend from hatchling to setuptools, and
# actually verify the package installs

## Status
Accepted.

## Context
A self-audit found that this package had never once been built or installed
in this project's entire development history — not as a wheel, not via
`pip install -e .`. `pyproject.toml` declared `hatchling` as the build
backend, and `hatchling` was never available in this sandbox (no network
access to install it, same constraint as Pydantic/pytest/ruff/mypy
documented in `adr/0001`/`adr/0003`). Every test and example was run against
the `src/` checkout on `PYTHONPATH`, never against an actually-installed
package — a more basic gap than "the dev tooling never ran," since it means
the roadmap's very first line of usage (`pip install reliableagent`) was
never verified at all.

## Decision
Switch the build backend to `setuptools` (`requires = ["setuptools>=68.0"]`,
`build-backend = "setuptools.build_meta"`), replacing
`[tool.hatch.build.targets.wheel]` with `[tool.setuptools.packages.find]`
(`where = ["src"]`). Both `setuptools` and `wheel` are pre-installed in this
sandbox (confirmed via direct import), unlike `hatchling`.

Added `scripts/verify_build.py`, which: (1) builds a real wheel via
`pip wheel . --no-deps --no-build-isolation`, (2) creates a fresh, empty venv,
(3) installs the wheel with `--no-index --no-deps`, and (4) imports the
package from that fresh venv's `site-packages` and runs one full, real
`Orchestrator.run()` loop against it — not the `src/` checkout.

## Alternatives considered
**A: Leave hatchling declared, document as unverified.** Rejected — there's a
real difference between "the linter never ran" and "the package has never
been installed," a load-bearing claim about basic usability. Once a working
alternative existed, leaving it broken was the wrong call.

**B: Vendor a hatchling wheel.** Rejected — no such wheel exists anywhere in
this sandbox to vendor, and committing arbitrary binaries is poor practice.

**C: Hand-construct a wheel via stdlib zipfile, bypassing any build backend.**
Rejected — `pyproject.toml` would no longer describe how the package is
actually built, a worse state than declaring a backend that's genuinely
available and tested.

## Consequences

**Positive:** `pip install reliableagent` is now a verified claim — proven by
an automated script that builds, installs into a genuinely fresh environment,
and runs a real Orchestrator loop against the installed copy. Repeatable for
any future change via `scripts/verify_build.py`.

**Negative / known limitations:** The full dependency-resolution flow
(fetching pydantic/PyYAML from a real index) is still unverified — this
sandbox has no network access to any package index at all.
