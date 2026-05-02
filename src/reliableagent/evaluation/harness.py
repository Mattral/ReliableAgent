"""EvaluationHarness: matches the roadmap's EvaluationHarness(orchestrator=...).evaluate(task_set=..., seeds=...)
DX. Named task-set registry; 'golden_suite_v1' registered by default.

Mock-backed orchestrators get a freshly-scripted Orchestrator per golden task (reusing caller's
Critic/Guardrails/Tools/sink). Real-LLM-backed orchestrators are reused as-is across all tasks/seeds.
The caller-owned orchestrator is NEVER shut down by this class.
"""
from __future__ import annotations
from dataclasses import dataclass
from reliableagent.core.orchestrator import Orchestrator
from reliableagent.evaluation.factory import mock_llm_for_golden_task
from reliableagent.evaluation.failure_analysis import FailureAnalysisReport, analyze_failures
from reliableagent.evaluation.golden_task import GoldenTask
from reliableagent.evaluation.golden_tasks import ALL_GOLDEN_TASKS
from reliableagent.evaluation.metrics import GradedRun
from reliableagent.llm.mock import MockLLMClient
from reliableagent.planner.llm_planner import LLMPlanner

_REGISTRY: dict[str, list[GoldenTask]] = {"golden_suite_v1": ALL_GOLDEN_TASKS}


def register_task_set(name: str, tasks: list[GoldenTask]) -> None:
    _REGISTRY[name] = tasks


def get_task_set(name: str) -> list[GoldenTask]:
    if name not in _REGISTRY:
        raise KeyError(f"No task set {name!r}. Available: {sorted(_REGISTRY)}. Use register_task_set().")
    return _REGISTRY[name]


@dataclass(frozen=True)
class EvaluationResults:
    graded_runs: list[GradedRun]
    _report: FailureAnalysisReport

    def summary(self) -> str:
        return "\n".join(self._report.metrics.summary_lines())

    def failure_analysis(self) -> str:
        return "\n".join(self._report.summary_lines())

    @property
    def report(self) -> FailureAnalysisReport:
        return self._report


class EvaluationHarness:
    """Matches the roadmap's EvaluationHarness DX. Never shuts down the caller-owned orchestrator."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self._orch = orchestrator

    def evaluate(self, task_set: str, seeds: list[int]) -> EvaluationResults:
        tasks = get_task_set(task_set)
        graded = (self._eval_mock(tasks, seeds) if self._is_mock_backed()
                  else self._eval_shared(tasks, seeds))
        return EvaluationResults(graded_runs=graded, _report=analyze_failures(graded))

    def _is_mock_backed(self) -> bool:
        llm = getattr(self._orch.planner, "_llm_client", None)
        return isinstance(llm, MockLLMClient)

    def _eval_shared(self, tasks: list[GoldenTask], seeds: list[int]) -> list[GradedRun]:
        runs: list[GradedRun] = []
        for task in tasks:
            for seed in seeds:
                result = self._orch.run(task.make_task())
                passed, explanation = task.grade(result)
                runs.append(GradedRun(golden_task_id=task.task_id, category=task.category,
                                      seed=seed, run_result=result, passed=passed,
                                      grading_explanation=explanation))
        return runs

    def _eval_mock(self, tasks: list[GoldenTask], seeds: list[int]) -> list[GradedRun]:
        """Build a FRESH scripted Orchestrator per (task, seed) pair, not
        just per task. `MockLLMClient` holds a finite queue of scripted
        responses -- reusing one scripted client/orchestrator across
        multiple seeds would exhaust that queue after the first seed's
        run, silently falling back to a non-plan "OK." default response
        and failing every subsequent seed with a spurious planning error.
        This was a real bug caught by testing this harness against more
        than one seed, not just seeds=[0] (where it looked correct)."""
        runs: list[GradedRun] = []
        for task in tasks:
            for seed in seeds:
                scripted = Orchestrator(
                    planner=LLMPlanner(mock_llm_for_golden_task(task)),
                    critic=self._orch.critic,
                    tools=self._orch.tools,
                    guardrails=self._orch.guardrails,
                    memory=self._orch.memory,
                    executor=self._orch.executor,
                    sink=self._orch.sink,
                )
                # Do NOT call scripted.shutdown() — it shares the caller's Executor/thread pool.
                result = scripted.run(task.make_task())
                passed, explanation = task.grade(result)
                runs.append(GradedRun(golden_task_id=task.task_id, category=task.category,
                                      seed=seed, run_result=result, passed=passed,
                                      grading_explanation=explanation))
        return runs
