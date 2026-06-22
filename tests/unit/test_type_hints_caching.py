"""Regression test for the `_compat` shim's `get_type_hints` caching fix
(see `adr/0006-type-hints-caching-performance-fix.md`).

This test is deliberately NOT a strict timing assertion (timing-based
tests are flaky across machines/CI load) -- instead it verifies the
*mechanism* directly: that `_cached_type_hints` actually caches (calls
`typing.get_type_hints` at most once per class, however many model
instances of that class are subsequently constructed). If a future change
accidentally reintroduces a call site that bypasses the cache, this test
catches the mechanism regressing even if a timing-based test would be too
noisy to catch a few-percent slowdown reliably.

Only runs against the fallback shim (skips cleanly if real Pydantic is
installed, since this caching mechanism is specific to the shim and real
Pydantic has its own, separately-engineered performance characteristics
this test makes no claims about).
"""

from __future__ import annotations

from reliableagent._compat import PYDANTIC_AVAILABLE


def test_cached_type_hints_resolves_each_class_at_most_once():
    if PYDANTIC_AVAILABLE:
        return  # This caching mechanism is specific to the fallback shim.

    import typing

    from reliableagent._compat import _fallback
    from reliableagent.core.models import Task

    call_count = {"n": 0}
    real_get_type_hints = typing.get_type_hints

    def counting_get_type_hints(cls, *args, **kwargs):
        call_count["n"] += 1
        return real_get_type_hints(cls, *args, **kwargs)

    # Clear any pre-existing cache entry for Task so this test observes a
    # fresh first-call-then-cached sequence regardless of prior test order.
    _fallback._type_hints_cache.pop(Task, None)

    original = _fallback.get_type_hints
    _fallback.get_type_hints = counting_get_type_hints
    try:
        for i in range(50):
            Task(description=f"instance {i}")
    finally:
        _fallback.get_type_hints = original

    assert call_count["n"] == 1, (
        f"Expected get_type_hints to be called exactly once for 50 Task "
        f"constructions (cached thereafter), but it was called {call_count['n']} times."
    )


def test_cache_is_correct_not_just_fast():
    """The cached hints must be the actual, correct type hints for the
    class -- a cache that's fast but wrong would be strictly worse than
    no cache at all."""
    if PYDANTIC_AVAILABLE:
        return

    import typing

    from reliableagent._compat._fallback import _cached_type_hints
    from reliableagent.core.models import Task

    cached = _cached_type_hints(Task)
    real = typing.get_type_hints(Task)
    assert cached == real
    assert "description" in cached
    assert "task_id" in cached


def test_different_classes_get_independent_cache_entries():
    if PYDANTIC_AVAILABLE:
        return

    from reliableagent._compat._fallback import _cached_type_hints
    from reliableagent.core.models import Plan, Task

    task_hints = _cached_type_hints(Task)
    plan_hints = _cached_type_hints(Plan)
    assert task_hints != plan_hints
    assert "description" in task_hints
    assert "steps" in plan_hints
