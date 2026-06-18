#!/usr/bin/env python3
"""Offline test runner for environments without `pip install pytest`.

Discovers `tests/**/test_*.py`, imports each module, and calls every
top-level `test_*` function, reporting pass/fail/error per test with a
final summary — close enough to `pytest`'s console output to be
immediately familiar.

If real `pytest` IS importable in the current environment, this
script defers to it entirely (`python -m pytest tests/`) instead,
since real pytest is strictly more correct (fixtures, parametrize,
proper assertion rewriting, etc.) than this fallback runner.

Usage:
    python scripts/run_tests.py
    python scripts/run_tests.py tests/unit/test_models.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
TESTS_DIR = REPO_ROOT / "tests"
SHIM_DIR = TESTS_DIR / "_pytest_shim"


def _try_real_pytest(argv: list[str]) -> int | None:
    """Return an exit code if real pytest is available and was run, else None."""
    if importlib.util.find_spec("pytest") is None:
        return None
    cmd = [sys.executable, "-m", "pytest", *argv]
    return subprocess.call(cmd, cwd=str(REPO_ROOT))


def _install_shim() -> None:
    """Make `import pytest` resolve to our offline shim."""
    sys.path.insert(0, str(SHIM_DIR))


def _discover_test_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(target.rglob("test_*.py"))


def _load_module(path: Path):
    rel = path.relative_to(REPO_ROOT)
    module_name = "_offline_test." + ".".join(rel.with_suffix("").parts)
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _run_test_function(name: str, func) -> tuple[bool, str]:
    if getattr(func, "__is_fixture__", False):
        return True, "skipped (fixture)"
    if hasattr(func, "__skip__"):
        return True, f"skipped ({func.__skip__})"

    parametrize = getattr(func, "__parametrize__", None)
    try:
        if parametrize is None:
            func()
        else:
            arg_names, arg_values = parametrize
            names = [n.strip() for n in arg_names.split(",")]
            for values in arg_values:
                values_tuple = values if isinstance(values, tuple) else (values,)
                kwargs = dict(zip(names, values_tuple, strict=True))
                func(**kwargs)
        return True, "ok"
    except AssertionError as exc:
        return False, f"FAILED: {exc}"
    except Exception:  # noqa: BLE001 - report any error as a test failure
        return False, f"ERROR:\n{traceback.format_exc()}"


def main() -> int:
    args = sys.argv[1:]
    pytest_result = _try_real_pytest(args)
    if pytest_result is not None:
        return pytest_result

    print("(real pytest not available in this environment; using offline shim runner)\n")

    _install_shim()
    sys.path.insert(0, str(SRC_DIR))
    sys.path.insert(0, str(REPO_ROOT))

    target = Path(args[0]).resolve() if args else TESTS_DIR
    test_files = _discover_test_files(target)
    if not test_files:
        print(f"No test files found under {target}")
        return 1

    total = 0
    failed = 0
    for path in test_files:
        print(f"--- {path.relative_to(REPO_ROOT)} ---")
        try:
            module = _load_module(path)
        except Exception:  # noqa: BLE001
            print(f"  COLLECTION ERROR:\n{traceback.format_exc()}")
            failed += 1
            total += 1
            continue

        test_names = [n for n in dir(module) if n.startswith("test_")]
        for name in test_names:
            func = getattr(module, name)
            if not callable(func):
                continue
            total += 1
            ok, message = _run_test_function(name, func)
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {name} {'' if ok else '- ' + message}")
            if not ok:
                failed += 1

    print(f"\n{total - failed}/{total} tests passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
