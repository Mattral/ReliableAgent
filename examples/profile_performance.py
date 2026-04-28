#!/usr/bin/env python3
"""Performance profiling: where does time actually go in a ReliableAgent run?

Per Phase 4's "Performance profiling" deliverable. Uses Python's stdlib
`cProfile` (deterministic call-count + cumulative-time profiling, not
sampling) over the full 20-task golden suite, then reports two views:

    1. A coarse, component-level breakdown (Planner / Executor /
       Guardrails / Critic / Memory / Orchestrator-overhead), computed by
       attributing each profiled function's own time to the module it
       lives in -- this answers "which LAYER of the architecture is most
       expensive," which matters more for architectural decisions than
       any single function's number.
    2. The top N individual functions by cumulative time, straight from
       `pstats`, for anyone who wants to drill into a specific hot path.

Run with:

    python examples/profile_performance.py
    python examples/profile_performance.py --top 30
    python examples/profile_performance.py --repeat 5   # average over 5 runs

Everything here runs against `MockLLMClient` (zero network latency), so
the numbers measure ReliableAgent's OWN overhead -- orchestration,
validation, guardrail evaluation, observability, checkpointing -- not an
LLM provider's response time, which would dominate and hide everything
else in a real deployment. That framing is deliberate: see
`docs/architecture.md` section 11 for what these numbers do and don't
tell you, and why an LLM-call-dominated wall-clock measurement would have
been a far less useful thing to report here.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reliableagent.evaluation.factory import run_golden_suite
from reliableagent.evaluation.golden_tasks import ALL_GOLDEN_TASKS
from reliableagent.evaluation.runner import EvalConfig
from reliableagent.executor.executor import Executor

# Maps a dotted module-path FRAGMENT to a human-readable architectural
# layer name. Matched via substring against each profiled function's
# file path, in order -- first match wins, so more specific fragments
# should be listed before more general ones.
LAYER_PATTERNS: list[tuple[str, str]] = [
    ("reliableagent/core/orchestrator.py", "Orchestrator (control loop)"),
    ("reliableagent/core/state_machine.py", "Orchestrator (control loop)"),
    ("reliableagent/planner/llm_planner.py", "Planner"),
    ("reliableagent/planner/critic.py", "Critic"),
    ("reliableagent/planner/process_critic.py", "Critic"),
    ("reliableagent/planner/replanner.py", "Replanner"),
    ("reliableagent/planner/prompts.py", "Planner"),
    ("reliableagent/executor/", "Executor"),
    ("reliableagent/guardrails/", "Guardrails"),
    ("reliableagent/memory/", "Memory"),
    ("reliableagent/observability/", "Observability"),
    ("reliableagent/llm/", "LLM client (mocked: ~0 real latency)"),
    ("reliableagent/core/models.py", "Core models (Pydantic/compat validation)"),
    ("reliableagent/_compat/", "Core models (Pydantic/compat validation)"),
    ("reliableagent/evaluation/", "Evaluation harness (not part of a normal run)"),
]


def _layer_for(filename: str, function_name: str = "") -> str:
    # time.sleep specifically is almost always the Executor's deliberate
    # retry backoff (see executor.py's `time.sleep(self._retry_backoff_seconds
    # * attempt)`), not generic Python runtime overhead -- lumping it into
    # "Python stdlib / runtime" would hide a real, actionable finding
    # (how much of total time is intentional waiting vs. actual compute)
    # inside a bucket whose name suggests it's unavoidable interpreter cost.
    if function_name == "sleep" and "time" in filename.lower():
        return "Deliberate waiting (Executor retry backoff sleeps)"
    for fragment, layer in LAYER_PATTERNS:
        if fragment in filename:
            return layer
    if "reliableagent" in filename:
        return "Other ReliableAgent code"
    return "Python stdlib / runtime"


def run_profiled_suite(*, disable_retry_backoff: bool = False) -> cProfile.Profile:
    """Run the full golden suite once under cProfile and return the raw profile.

    `disable_retry_backoff=True` zeroes the Executor's retry backoff
    sleep, which is otherwise a real but deliberately-inserted wait (not
    computational overhead) that this golden suite's intentional-failure
    tasks trigger -- useful for isolating "how much of this is ReliableAgent
    doing work" from "how much of this is ReliableAgent waiting on
    purpose," which the layer breakdown also makes visible directly (see
    the "Deliberate waiting" row), but a clean run with it disabled
    entirely makes the rest of the breakdown easier to read at a glance.
    """
    executor_builder = (
        (lambda tools: Executor(tools, max_retries=1, retry_backoff_seconds=0.0))
        if disable_retry_backoff
        else None
    )
    profiler = cProfile.Profile()
    profiler.enable()
    run_golden_suite(ALL_GOLDEN_TASKS, EvalConfig(seeds=[0]), executor_builder=executor_builder)
    profiler.disable()
    return profiler


def summarize_by_layer(profiler: cProfile.Profile) -> dict[str, float]:
    """Attribute each function's OWN (not cumulative) time to an architectural layer.

    Using `tottime` (time spent in the function itself, excluding calls
    to other functions) rather than `cumtime` here is deliberate: cumtime
    would double-count time, since `Orchestrator.run` cumulatively
    includes everything it calls, which would make "Orchestrator" look
    artificially dominant in a layer breakdown.
    """
    stats = pstats.Stats(profiler)
    layer_totals: dict[str, float] = defaultdict(float)
    for func, (_cc, _nc, tottime, _ct, _callers) in stats.stats.items():
        filename, _lineno, function_name = func
        layer_totals[_layer_for(filename, function_name)] += tottime
    return dict(layer_totals)


def print_layer_breakdown(layer_totals: dict[str, float]) -> None:
    total = sum(layer_totals.values()) or 1.0
    print("Time by architectural layer (own-time, not cumulative):\n")
    rows = sorted(layer_totals.items(), key=lambda kv: -kv[1])
    width = max(len(name) for name, _ in rows)
    for name, seconds in rows:
        pct = seconds / total * 100
        bar = "#" * max(1, int(pct / 2))
        print(f"  {name:<{width}}  {seconds * 1000:8.2f} ms  {pct:5.1f}%  {bar}")
    print(f"\n  {'TOTAL':<{width}}  {total * 1000:8.2f} ms")


def print_top_functions(profiler: cProfile.Profile, top_n: int) -> None:
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.sort_stats("cumulative")
    stats.print_stats(top_n)
    print(stream.getvalue())


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--top", type=int, default=20, help="Number of top functions to show (default: 20)."
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Run the suite this many times and average the layer breakdown.",
    )
    parser.add_argument(
        "--no-retry-backoff",
        action="store_true",
        help=(
            "Disable the Executor's retry backoff sleep for this profiling run, to "
            "isolate computational overhead from this golden suite's deliberate "
            "intentional-failure/backoff scenarios."
        ),
    )
    args = parser.parse_args()

    print(
        f"Profiling the full {len(ALL_GOLDEN_TASKS)}-task golden suite "
        f"({args.repeat} run(s), retry_backoff={'disabled' if args.no_retry_backoff else 'default'})...\n"
    )

    accumulated_layers: dict[str, float] = defaultdict(float)
    wall_clock_start = time.perf_counter()
    last_profiler: cProfile.Profile | None = None

    for _ in range(args.repeat):
        profiler = run_profiled_suite(disable_retry_backoff=args.no_retry_backoff)
        last_profiler = profiler
        layer_totals = summarize_by_layer(profiler)
        for layer, seconds in layer_totals.items():
            accumulated_layers[layer] += seconds

    wall_clock_total = time.perf_counter() - wall_clock_start
    averaged_layers = {k: v / args.repeat for k, v in accumulated_layers.items()}

    print(
        f"Wall-clock total for {args.repeat} run(s): {wall_clock_total * 1000:.2f} ms "
        f"({wall_clock_total * 1000 / args.repeat:.2f} ms/run average)\n"
    )
    print_layer_breakdown(averaged_layers)

    print(f"\nTop {args.top} functions by cumulative time (last run only):\n")
    assert last_profiler is not None
    print_top_functions(last_profiler, args.top)


if __name__ == "__main__":
    main()
