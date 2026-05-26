#!/usr/bin/env python3
"""Build verification script: actually builds a wheel, installs it into
a fresh venv, and runs a real Orchestrator loop from site-packages.
Added per ADR 0007 after the audit found the package had never been
built or installed in this project's entire development history.

Usage: python scripts/verify_build.py
"""
from __future__ import annotations
import os, subprocess, sys, tempfile, venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def run(cmd):
    print(f"$ {' '.join(cmd)}")
    # Every call site in this script passes a list built from
    # sys.executable / an absolute venv interpreter path plus fixed
    # literal arguments -- no shell=True, no string interpolation, no
    # untrusted input. This is standard build-tooling subprocess usage.
    r = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    if r.stdout: print(r.stdout)
    return r

def main() -> int:
    for m in ("setuptools", "wheel"):
        try: __import__(m)
        except ImportError:
            print(f"'{m}' not importable; cannot verify build.", file=sys.stderr)
            return 1
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        wheel_dir = tmp / "wheels"
        venv_dir = tmp / "venv"
        print("=== Step 1: build wheel ===")
        r = run([sys.executable, "-m", "pip", "wheel", str(REPO_ROOT),
                 "--no-deps", "--no-build-isolation", "-w", str(wheel_dir)])
        if r.returncode != 0: return 1
        wheels = list(wheel_dir.glob("*.whl"))
        if not wheels: print("No wheel produced", file=sys.stderr); return 1
        print(f"Built: {wheels[0].name}\n")
        print("=== Step 2: create fresh venv ===")
        venv.create(venv_dir, with_pip=True)
        py = str(venv_dir / "bin" / "python3")
        print("=== Step 3: install wheel ===")
        r = run([py, "-m", "pip", "install", "--no-index", "--no-deps", str(wheels[0])])
        if r.returncode != 0: return 1
        print("=== Step 4: import from site-packages and run Orchestrator ===")
        check = (
            "import reliableagent; from reliableagent import Orchestrator,Task; "
            "from reliableagent.llm import MockLLMClient; "
            "from reliableagent.planner import LLMPlanner,ThresholdCritic; "
            "from reliableagent.executor import ToolRegistry; "
            "from reliableagent.guardrails import BasicGuardrail; import json; "
            "tools=ToolRegistry(); tools.register(lambda a,b:a+b,name='add',description='adds'); "
            "plan=json.dumps({'reasoning_trace':'x','confidence':0.9,'steps':["
            "{'step_type':'tool_call','description':'add','tool_name':'add','tool_arguments':{'a':1,'b':1}},"
            "{'step_type':'final_answer','description':'two'}]}); "
            "orch=Orchestrator(planner=LLMPlanner(MockLLMClient(responses=[plan])),"
            "critic=ThresholdCritic(),tools=tools,guardrails=[BasicGuardrail()]); "
            "result=orch.run(Task(description='test')); "
            "assert result.final_answer=='two',result.final_answer; "
            "orch.shutdown(); print('INSTALLED PACKAGE WORKS')"
        )
        env_pp = ":".join(p for p in sys.path if p)
        # `py` is an absolute path to the fresh venv's own interpreter
        # (constructed from a tempfile.TemporaryDirectory, not user
        # input), and `check` is a fixed literal string built entirely
        # within this script -- no shell=True, nothing untrusted.
        r2 = subprocess.run(  # noqa: S603
            [py, "-c", check], capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": env_pp},
        )
        print(r2.stdout)
        if r2.returncode != 0 or "INSTALLED PACKAGE WORKS" not in r2.stdout:
            print(r2.stderr, file=sys.stderr); return 1
    print("=== Build verification PASSED ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
