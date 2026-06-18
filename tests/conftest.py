"""Shared pytest fixtures for the ReliableAgent test suite.

Note: the offline fallback runner (`scripts/run_tests.py`) does not
execute fixtures — ReliableAgent's tests intentionally use plain
helper functions (see `tests/helpers.py`) rather than relying on
fixture injection, specifically so the same test files run identically
under real pytest and under the offline shim. This `conftest.py` is
kept minimal and is here mainly for real-pytest users who want to
extend the suite with fixture-based tests later.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure `src/` is importable without requiring an editable install,
# which matters in the offline/no-network sandbox this project was
# partly developed in.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
