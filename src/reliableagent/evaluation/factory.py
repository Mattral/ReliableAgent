"""The standard `OrchestratorFactory` used to run the golden task suite.

Centralizing "how do we build an Orchestrator for golden task X" here
(rather than each caller hand-assembling guardrails/critic/tools) means
every consumer of the golden suite -- `tests/eval/test_golden_suite.py`,
`examples/run_evaluation.py`, and the config-comparison tool in
`comparison.py` -- exercises the exact same baseline wiring unless they
deliberately override a piece of it, which is the whole point of having
a "standard" factory to compare variations against.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from reliableagent.core.orchestrator import Orchestrator
from reliableagent.evaluation.golden_task import GoldenTask
from reliableagent.evaluation.golden_tasks import get_plan_script
from reliableagent.evaluation.golden_tools import build_golden_task_tools
from reliableagent.executor.executor import Executor
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.guardrails.base import Guardrail
from reliableagent.guardrails.basic import BasicGuardrail, ToolArgumentSanityGuardrail
from reliableagent.llm.base import LLMClient
from reliableagent.llm.mock import MockLLMClient
from reliableagent.planner.base import Planner
from reliableagent.planner.critic import Critic, ThresholdCritic
from reliableagent.planner.llm_planner import LLMPlanner

if TYPE_CHECKING:
    from reliableagent.evaluation.metrics import GradedRun
    from reliableagent.evaluation.runner import EvalConfig

# The guardrail stack every golden task in `golden_tasks.py` was authored
# and verified against. `guardrail_blocks_disallowed_keyword_in_plan`
# specifically depends on the blocked-substring policy below; changing it
# here changes what that golden task actually tests.
STANDARD_BLOCKED_SUBSTRINGS = ["exfiltrate confidential data"]


def standard_guardrails() -> list[Guardrail]:
    """The default guardrail stack the golden suite is authored against."""
    return [
        BasicGuardrail(blocked_substrings=STANDARD_BLOCKED_SUBSTRINGS),
        ToolArgumentSanityGuardrail(max_arguments=20),
    ]


def mock_llm_for_golden_task(golden_task: GoldenTask) -> LLMClient:
    """Build a `MockLLMClient` scripted with `golden_task`'s known-correct plan(s)."""
    return MockLLMClient(responses=get_plan_script(golden_task.task_id))


def build_standard_factory(
    golden_task: GoldenTask,
    *,
    llm_client_builder: Callable[[GoldenTask], LLMClient] | None = None,
    planner_builder: Callable[[LLMClient], Planner] | None = None,
    critic_builder: Callable[[], Critic] | None = None,
    guardrails_builder: Callable[[], list[Guardrail]] | None = None,
    executor_builder: Callable[[ToolRegistry], Executor] | None = None,
) -> Callable[[int | None], Orchestrator]:
    """Build the standard `OrchestratorFactory` for ONE golden task.

    Every piece is independently overridable via the `*_builder`
    parameters -- this is what `evaluation.comparison` uses to vary
    guardrail strictness, Critic strategy, and executor retry settings
    while keeping everything else (tools, the scripted LLM responses,
    the golden task itself) held constant, which is exactly what a fair
    "compare configurations" experiment requires.

    `executor_builder`, if given, receives this run's freshly-built
    `ToolRegistry` and must construct an `Executor` wrapping that exact
    registry (e.g. `lambda tools: Executor(tools, max_retries=3)`) --
    the Executor and the Orchestrator it's wired into must always share
    the same registry instance, so this cannot be a zero-argument
    builder the way the other dimensions are.

    Note this factory is specific to `golden_task` (it bakes in that
    task's scripted LLM responses) -- see `run_golden_suite` below for
    the helper that correctly builds a fresh, task-specific factory for
    every task in a suite, rather than reusing one factory across tasks
    that need different scripted responses.
    """
    llm_builder = llm_client_builder or mock_llm_for_golden_task
    plan_builder = planner_builder or LLMPlanner
    crit_builder = critic_builder or ThresholdCritic
    guard_builder = guardrails_builder or standard_guardrails

    def factory(seed: int | None) -> Orchestrator:
        tools = build_golden_task_tools()
        llm_client = llm_builder(golden_task)
        kwargs: dict[str, object] = {
            "planner": plan_builder(llm_client),
            "critic": crit_builder(),
            "tools": tools,
            "guardrails": guard_builder(),
        }
        if executor_builder is not None:
            kwargs["executor"] = executor_builder(tools)
        return Orchestrator(**kwargs)  # type: ignore[arg-type]

    return factory


def run_golden_suite(
    golden_tasks: list[GoldenTask],
    eval_config: "EvalConfig",
    *,
    planner_builder: Callable[[LLMClient], Planner] | None = None,
    critic_builder: Callable[[], Critic] | None = None,
    guardrails_builder: Callable[[], list[Guardrail]] | None = None,
    executor_builder: Callable[[ToolRegistry], Executor] | None = None,
    llm_client_builder: Callable[[GoldenTask], LLMClient] | None = None,
) -> list["GradedRun"]:
    """Run every golden task in `golden_tasks` with the standard factory.

    This is the correct way to run a *list* of golden tasks (as opposed
    to `build_standard_factory`, which builds a factory for a single
    task): it constructs a fresh, task-specific `EvaluationRunner` for
    each task, since each golden task's standard factory bakes in that
    task's own scripted LLM responses and must not be reused across
    different tasks. All `*_builder` overrides are forwarded to every
    task's factory, which is exactly what `evaluation.comparison` needs
    to apply one configuration variation uniformly across the whole
    suite.
    """
    from reliableagent.evaluation.runner import EvaluationRunner

    all_graded_runs = []
    for golden_task in golden_tasks:
        factory = build_standard_factory(
            golden_task,
            llm_client_builder=llm_client_builder,
            planner_builder=planner_builder,
            critic_builder=critic_builder,
            guardrails_builder=guardrails_builder,
            executor_builder=executor_builder,
        )
        runner = EvaluationRunner(orchestrator_factory=factory)
        all_graded_runs.extend(runner.run_suite([golden_task], eval_config))
    return all_graded_runs
