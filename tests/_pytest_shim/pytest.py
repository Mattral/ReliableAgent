"""Minimal, dependency-free stand-in for the small slice of `pytest` used
by ReliableAgent's own test suite, for environments without network
access to `pip install pytest` (see `reliableagent._compat` for the
analogous rationale re: Pydantic).

Provides:
    - `pytest.raises(ExceptionType)` as a context manager, matching
      real pytest's behavior (raises `AssertionError` if the block
      does NOT raise the expected exception type).
    - `pytest.approx(value, rel=...)` for float comparisons.
    - `pytest.mark.parametrize` as a no-op-preserving decorator (the
      decorated test still runs once per parameter set when executed
      via `scripts/run_tests.py`).
    - `pytest.fixture` recognized but unused by the custom runner;
      ReliableAgent's tests deliberately avoid fixtures in favor of
      plain helper functions so they run identically under this shim
      and under real pytest.

This module is NOT imported by test files directly. Instead,
`scripts/run_tests.py` inserts it into `sys.modules['pytest']` *only
if* the real `pytest` package is not importable, so test files can
simply `import pytest` and work either way.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any


@dataclass
class _RaisesContext:
    expected_type: type[BaseException]
    match: str | None = None
    raised: BaseException | None = None

    def __enter__(self) -> "_RaisesContext":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if exc_type is None:
            raise AssertionError(f"Expected {self.expected_type.__name__} but no exception was raised.")
        if not issubclass(exc_type, self.expected_type):
            return False  # let the real (unexpected) exception propagate
        if self.match is not None and self.match not in str(exc_val):
            raise AssertionError(
                f"Exception message {str(exc_val)!r} did not contain expected substring {self.match!r}."
            )
        self.raised = exc_val
        return True  # suppress the expected exception


def raises(expected_type: type[BaseException], *, match: str | None = None) -> _RaisesContext:
    """Stand-in for `pytest.raises`."""
    return _RaisesContext(expected_type=expected_type, match=match)


@contextlib.contextmanager
def warns(*_args: Any, **_kwargs: Any) -> Iterator[None]:
    """Stand-in for `pytest.warns` (best-effort no-op; not used heavily)."""
    yield


def approx(value: float, rel: float = 1e-6, abs: float = 1e-12) -> "_Approx":  # noqa: A002
    return _Approx(value, rel=rel, abs=abs)


@dataclass
class _Approx:
    value: float
    rel: float
    abs: float

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, (int, float)):
            return NotImplemented
        diff = abs(self.value - other)  # type: ignore[name-defined]
        return diff <= max(self.rel * abs(self.value), self.abs)  # type: ignore[name-defined]


class _MarkDecorators:
    @staticmethod
    def parametrize(
        arg_names: str, arg_values: list[Any]
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """No-op-preserving decorator: stores parameter sets for the custom runner."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            func.__parametrize__ = (arg_names, arg_values)  # type: ignore[attr-defined]
            return func

        return decorator

    @staticmethod
    def skip(reason: str = "") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            func.__skip__ = reason  # type: ignore[attr-defined]
            return func

        return decorator

    def __getattr__(self, _name: str) -> Callable[..., Any]:
        # Unknown marks (e.g. `@pytest.mark.unit`) are accepted as no-ops
        # so test files can freely use the markers declared in pyproject.toml.
        def _noop_mark(*_a: Any, **_k: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                return func

            return decorator

        return _noop_mark


mark = _MarkDecorators()


def fixture(func: Callable[..., Any] | None = None, **_kwargs: Any) -> Callable[..., Any]:
    """Stand-in for `pytest.fixture`. Marks a function so the runner can
    recognize (and skip collecting) it as a fixture rather than a test."""

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        f.__is_fixture__ = True  # type: ignore[attr-defined]
        return f

    if func is not None:
        return decorator(func)
    return decorator
