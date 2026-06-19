#!/usr/bin/env python3
"""One-command evaluation: run the full golden suite and print metrics +
failure analysis.

Per the roadmap's Phase 2 success criterion: "One-command evaluation that
produces clear metrics and failure analysis."

Usage:
    python examples/run_evaluation.py
    python examples/run_evaluation.py --seeds 0 1 2
    python examples/run_evaluation.py --trajectory-dir ./eval_runs
    python examples/run_evaluation.py --category guardrail
    python examples/run_evaluation.py --use-real-anthropic-model claude-sonnet-4-6

By default, runs entirely offline against `MockLLMClient` scripted with
each golden task's known-correct plan (see
`reliableagent.evaluation.golden_tasks`) -- this measures whether the
*orchestration engine itself* (Executor, Guardrails, Critic, replanning,
checkpointing) correctly handles each scenario, which is exactly the
question Phase 0/1 of this project answers.

Pass `--use-real-anthropic-model MODEL_NAME` to instead have a real model
generate plans live (requires `pip install 'reliableagent[anthropic]'`
and an `ANTHROPIC_API_KEY` in the environment) -- this measures the
different, harder question of whether a real model, prompted by
`LLMPlanner`/`LLMCritic`, reliably produces plans that achieve the same
outcomes. Both modes use the identical 20 golden tasks and identical
graders; only the LLM backend differs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reliableagent.evaluation import ALL_GOLDEN_TASKS, EvalConfig, analyze_failures
from reliableagent.evaluation.factory import run_golden_suite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=[0],
        help="Seeds to run each task under (default: 0).",
    )
    parser.add_argument(
        "--category", type=str, default=None, help="Only run golden tasks in this category."
    )
    parser.add_argument(
        "--trajectory-dir",
        type=str,
        default=None,
        help="If set, persist every run's full trajectory as JSON under this directory.",
    )
    parser.add_argument(
        "--use-real-anthropic-model",
        type=str,
        default=None,
        metavar="MODEL_NAME",
        help=(
            "If set, use a real AnthropicLLMClient with this model name instead of the "
            "default offline MockLLMClient. Requires 'anthropic' to be installed and "
            "ANTHROPIC_API_KEY to be set."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    golden_tasks = ALL_GOLDEN_TASKS
    if args.category:
        golden_tasks = [t for t in golden_tasks if t.category == args.category]
        if not golden_tasks:
            print(f"No golden tasks found in category '{args.category}'.")
            return 1

    llm_client_builder = None
    if args.use_real_anthropic_model:
        from reliableagent.llm import AnthropicLLMClient

        model_name = args.use_real_anthropic_model

        def llm_client_builder(_golden_task):  # noqa: ANN001, ANN202
            return AnthropicLLMClient(model=model_name)

        print(f"Using real Anthropic model: {model_name}\n")
    else:
        print("Using offline MockLLMClient (deterministic, scripted, zero API cost).\n")

    config = EvalConfig(seeds=args.seeds, trajectory_dir=args.trajectory_dir)
    print(
        f"Running {len(golden_tasks)} golden task(s) under {len(args.seeds)} seed(s) "
        f"= {len(golden_tasks) * len(args.seeds)} total run(s)...\n"
    )

    graded_runs = run_golden_suite(golden_tasks, config, llm_client_builder=llm_client_builder)
    report = analyze_failures(graded_runs)

    print("\n".join(report.summary_lines()))

    if args.trajectory_dir:
        print(f"\nFull trajectories saved under: {args.trajectory_dir}")

    return 0 if report.metrics.task_success_rate == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
