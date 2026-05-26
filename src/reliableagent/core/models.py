"""Core strongly-typed data models for ReliableAgent.

Every major entity that flows between components is a Pydantic model.
This is the literal implementation of the "Explicit Contracts &
Modularity" principle from the project philosophy: components never
pass around loosely-typed dicts internally — they pass these models,
which means a Planner, Guardrail, or Memory backend can be swapped for
an alternative implementation as long as it still speaks these
contracts.

Design notes:
    * All models are immutable-by-default (`frozen=True`) where they
      represent a fact that happened (e.g. `ToolResult`, `Feedback`,
      `GuardrailDecision`) — once recorded, history should not mutate.
      Models that represent mutable, in-progress state (`Trajectory`,
      `RunState`) are NOT frozen, since the Orchestrator appends to
      them as a run progresses.
    * Every model that gets logged/persisted carries a timestamp and,
      where relevant, a `run_id`, so trajectories and checkpoints are
      always traceable back to a specific run without extra plumbing.
    * IDs are generated with `uuid4` by default but can be supplied
      explicitly, which matters for reproducibility (Section 7 of the
      roadmap: "Unique run_id for every execution").
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from reliableagent._compat import BaseModel, ConfigDict, Field, field_validator
from reliableagent.core.enums import (
    FailureCategory,
    GuardrailBoundary,
    GuardrailCategory,
    GuardrailVerdict,
    OrchestratorState,
    StepStatus,
    StepType,
)


def _utcnow() -> datetime:
    """Return the current UTC time.

    Centralized so that tests can monkeypatch a single function instead
    of patching `datetime.now` calls scattered across the codebase.
    """
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    """Generate a prefixed unique identifier, e.g. ``task_3f9a...``.

    Prefixing IDs by entity type makes raw logs and trajectory dumps
    far easier to read and grep than bare UUIDs.
    """
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class _BaseFrozenModel(BaseModel):
    """Base class for immutable, historical-fact models."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _BaseMutableModel(BaseModel):
    """Base class for mutable, in-progress state models."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class Task(_BaseFrozenModel):
    """A high-level unit of work submitted to the Orchestrator.

    This is the entry point into the whole system: everything else
    (plans, trajectories, checkpoints) exists in service of completing
    a `Task`.
    """

    task_id: str = Field(default_factory=lambda: _new_id("task"))
    description: str = Field(..., min_length=1, description="The task in natural language.")
    max_steps: int = Field(default=20, gt=0, le=500)
    max_replans: int = Field(default=3, ge=0, le=50)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("description")
    @classmethod
    def _description_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Task description must not be blank or whitespace-only.")
        return v


# ---------------------------------------------------------------------------
# Plan & PlanStep
# ---------------------------------------------------------------------------


class PlanStep(_BaseFrozenModel):
    """A single step within a `Plan`.

    `step_type` discriminates between a tool invocation, a pure
    reasoning step (no external effect), and a final-answer step that
    terminates the run.
    """

    step_id: str = Field(default_factory=lambda: _new_id("step"))
    step_type: StepType
    description: str = Field(..., min_length=1)
    tool_name: str | None = Field(
        default=None, description="Required when step_type == TOOL_CALL."
    )
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = Field(
        default=None, description="Why the Planner believes this step is necessary."
    )

    @field_validator("tool_name")
    @classmethod
    def _tool_name_required_for_tool_calls(
        cls, v: str | None, info: Any
    ) -> str | None:
        step_type = info.data.get("step_type")
        if step_type == StepType.TOOL_CALL and not v:
            raise ValueError("tool_name is required when step_type is TOOL_CALL.")
        return v


class Plan(_BaseFrozenModel):
    """A structured plan produced by the Planner for a given task/state.

    Carries an explicit `confidence` and `reasoning_trace` so that the
    Planner's decision process is observable, not just its output —
    per the roadmap's requirement that the Planner "should expose
    reasoning trace for observability."
    """

    plan_id: str = Field(default_factory=lambda: _new_id("plan"))
    task_id: str
    steps: list[PlanStep] = Field(..., min_length=1)
    reasoning_trace: str = Field(
        default="", description="Free-text reasoning that led to this plan."
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    replan_attempt: int = Field(
        default=0, ge=0, description="0 for the initial plan, incremented on each replan."
    )
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Tool calls & results
# ---------------------------------------------------------------------------


class ToolCall(_BaseFrozenModel):
    """A concrete, executable invocation of a tool, derived from a `PlanStep`."""

    call_id: str = Field(default_factory=lambda: _new_id("call"))
    step_id: str
    tool_name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, gt=0)
    created_at: datetime = Field(default_factory=_utcnow)


class ToolResult(_BaseFrozenModel):
    """The outcome of executing a `ToolCall`.

    `success=False` with a populated `error` represents a *handled*
    failure (e.g. the tool raised, or timed out) as opposed to a
    framework-level exception — this is what lets the Executor return
    a normal value that the Critic/Replanner can reason about instead
    of unwinding the stack on every tool error.
    """

    result_id: str = Field(default_factory=lambda: _new_id("result"))
    call_id: str
    success: bool
    output: Any = None
    error: str | None = None
    duration_seconds: float = Field(default=0.0, ge=0.0)
    validated: bool = Field(
        default=False, description="Whether output passed post-execution validation."
    )
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


class GuardrailDecision(_BaseFrozenModel):
    """The verdict rendered by a single guardrail check at a given boundary.

    Every guardrail evaluation — allowed or blocked — produces one of
    these and it is always logged, satisfying "must be highly
    configurable and observable (log every decision)."
    """

    decision_id: str = Field(default_factory=lambda: _new_id("gd"))
    guardrail_name: str
    boundary: GuardrailBoundary
    category: GuardrailCategory
    verdict: GuardrailVerdict
    reason: str = Field(default="")
    modified_payload: Any = Field(
        default=None, description="Set when verdict == MODIFY; the replacement payload."
    )
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Critic feedback (Phase 3: multi-criteria scoring + step-level supervision)
# ---------------------------------------------------------------------------


class CriterionScores(_BaseFrozenModel):
    """Multi-criteria breakdown of a single quality assessment.

    Per Phase 3's "stronger Critic with process supervision": a single
    scalar `quality_score` collapses three genuinely different questions
    into one number — "did this work," "was it wasteful," and "was it
    safe" can disagree (a plan can be perfectly correct and safe while
    burning twice the necessary steps, or efficient and correct while
    skating close to a policy boundary). Keeping them as separate scores
    lets a Critic (and anyone reading a `Trajectory` later) see *which*
    dimension is actually driving a low overall score, rather than just
    that something, somewhere, wasn't great.

    `overall` is derived (see `weighted_overall`), not independently
    settable, so it can never silently drift out of sync with the three
    inputs it's computed from.
    """

    correctness: float = Field(..., ge=0.0, le=1.0, description="Did this achieve what it should have?")
    efficiency: float = Field(
        ..., ge=0.0, le=1.0, description="Was it accomplished without excess steps/waste?"
    )
    safety: float = Field(
        ..., ge=0.0, le=1.0, description="Did it stay clear of policy/safety concerns?"
    )

    def weighted_overall(
        self, *, correctness_weight: float = 0.6, efficiency_weight: float = 0.2, safety_weight: float = 0.2
    ) -> float:
        """Combine the three criteria into a single score, correctness-weighted by default.

        Correctness dominates by default because an efficient, safe plan
        that doesn't actually accomplish the task is not a good outcome
        by any reasonable standard — but the weights are parameters, not
        constants, specifically so a caller can rebalance them (e.g. for
        a safety-critical deployment that wants `safety` to dominate)
        without needing a different model or a new Critic subclass.
        """
        total_weight = correctness_weight + efficiency_weight + safety_weight
        if total_weight <= 0:
            raise ValueError("Criterion weights must sum to a positive number.")
        weighted_sum = (
            self.correctness * correctness_weight
            + self.efficiency * efficiency_weight
            + self.safety * safety_weight
        )
        return weighted_sum / total_weight


class StepCritique(_BaseFrozenModel):
    """A Critic's per-step verdict, produced as each step completes.

    This is what makes Critic supervision "process supervision" rather
    than purely "outcome supervision": a step can be flagged as
    problematic (`verdict=False`) the moment it happens, with a specific
    `concern`, rather than that information only surfacing implicitly
    much later as part of an aggregate end-of-plan `quality_score`.
    """

    step_id: str
    verdict: bool = Field(..., description="Whether this step, taken alone, looks acceptable.")
    concern: str = Field(default="", description="Specific issue noticed, if verdict is False.")
    created_at: datetime = Field(default_factory=_utcnow)


class Feedback(_BaseFrozenModel):
    """Structured feedback produced by the Critic after evaluating a trajectory.

    `should_replan` is the explicit signal consumed by the Orchestrator
    to decide whether to transition into REPLANNING. `criterion_scores`
    and `step_critiques` are optional (default to `None`/empty) so every
    existing Critic implementation and every existing test written
    against the simpler Phase 0-2 `Feedback` shape continues to work
    unchanged — Phase 3's process-supervision Critics populate them;
    `ThresholdCritic` deliberately still does not, since a pure
    failure-rate heuristic has no real multi-criteria basis to report.
    """

    feedback_id: str = Field(default_factory=lambda: _new_id("fb"))
    plan_id: str
    quality_score: float = Field(..., ge=0.0, le=1.0)
    should_replan: bool
    issues: list[str] = Field(default_factory=list)
    rationale: str = Field(default="")
    criterion_scores: CriterionScores | None = Field(
        default=None, description="Multi-criteria breakdown, when the Critic strategy supports it."
    )
    step_critiques: list[StepCritique] = Field(
        default_factory=list, description="Per-step verdicts, for Critics doing process supervision."
    )
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


class Checkpoint(_BaseFrozenModel):
    """A persisted, resumable snapshot of a run's state.

    Checkpoints capture exactly what's needed to resume a run without
    re-deriving it: the orchestrator state, the task, the active plan,
    completed step results, and the replan counter. `sequence_number`
    gives checkpoints within a run a strict, gap-free ordering, which
    the Memory layer uses to find the latest checkpoint quickly.
    """

    checkpoint_id: str = Field(default_factory=lambda: _new_id("ckpt"))
    run_id: str
    sequence_number: int = Field(..., ge=0)
    orchestrator_state: OrchestratorState
    task: Task
    current_plan: Plan | None = None
    completed_results: list[ToolResult] = Field(default_factory=list)
    replan_count: int = Field(default=0, ge=0)
    step_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Step record (one executed step inside a trajectory)
# ---------------------------------------------------------------------------


class StepRecord(_BaseFrozenModel):
    """The full record of one executed plan step, for trajectory storage."""

    step: PlanStep
    status: StepStatus
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    guardrail_decisions: list[GuardrailDecision] = Field(default_factory=list)
    step_critique: StepCritique | None = Field(
        default=None, description="The Critic's per-step verdict, when process supervision is enabled."
    )
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Trajectory (the full history of a run — mutable, built incrementally)
# ---------------------------------------------------------------------------


class Trajectory(_BaseMutableModel):
    """The complete, append-only history of a single run.

    Unlike most models in this module, `Trajectory` is intentionally
    *not* frozen: the Orchestrator appends to it incrementally as a run
    progresses. It is the single object that, dumped to JSON, lets a
    human or another tool fully reconstruct what happened — directly
    satisfying "If you cannot see exactly why an agent made a decision
    or why it failed, you cannot improve it."
    """

    run_id: str = Field(default_factory=lambda: _new_id("run"))
    task: Task
    plans: list[Plan] = Field(default_factory=list)
    step_records: list[StepRecord] = Field(default_factory=list)
    feedbacks: list[Feedback] = Field(default_factory=list)
    guardrail_decisions: list[GuardrailDecision] = Field(default_factory=list)
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    final_state: OrchestratorState = OrchestratorState.PENDING
    failure_category: FailureCategory | None = None
    final_answer: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None

    def add_plan(self, plan: Plan) -> None:
        """Append a newly generated plan to the trajectory."""
        self.plans.append(plan)

    def add_step_record(self, record: StepRecord) -> None:
        """Append a completed step record to the trajectory."""
        self.step_records.append(record)

    def add_feedback(self, feedback: Feedback) -> None:
        """Append Critic feedback to the trajectory."""
        self.feedbacks.append(feedback)

    def add_guardrail_decision(self, decision: GuardrailDecision) -> None:
        """Append a top-level (run-scoped) guardrail decision to the trajectory."""
        self.guardrail_decisions.append(decision)

    def add_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Append a saved checkpoint reference to the trajectory."""
        self.checkpoints.append(checkpoint)

    @property
    def total_replans(self) -> int:
        """Number of replans that occurred, derived from plan history."""
        return max((p.replan_attempt for p in self.plans), default=0)

    @property
    def total_guardrail_blocks(self) -> int:
        """Total number of BLOCK verdicts across the entire trajectory."""
        step_level = sum(
            1
            for record in self.step_records
            for decision in record.guardrail_decisions
            if decision.verdict == GuardrailVerdict.BLOCK
        )
        run_level = sum(
            1 for decision in self.guardrail_decisions if decision.verdict == GuardrailVerdict.BLOCK
        )
        return step_level + run_level

    @property
    def total_tool_calls(self) -> int:
        """Total number of tool calls attempted in this trajectory."""
        return sum(1 for record in self.step_records if record.tool_call is not None)


# ---------------------------------------------------------------------------
# RunResult — the public-facing return value of orchestrator.run()
# ---------------------------------------------------------------------------


class RunMetrics(_BaseFrozenModel):
    """Lightweight, immediately-readable summary metrics for a single run.

    `result.metrics` in the DX example from the roadmap (`print(result.metrics)`)
    maps directly onto this model.
    """

    total_steps: int = Field(..., ge=0)
    total_tool_calls: int = Field(..., ge=0)
    total_replans: int = Field(..., ge=0)
    total_guardrail_blocks: int = Field(..., ge=0)
    succeeded: bool
    duration_seconds: float = Field(..., ge=0.0)
    total_input_tokens: int = Field(default=0, ge=0)
    total_output_tokens: int = Field(default=0, ge=0)
    total_llm_calls: int = Field(default=0, ge=0)
    total_llm_latency_seconds: float = Field(default=0.0, ge=0.0)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


class RunResult(_BaseFrozenModel):
    """The object returned by `Orchestrator.run()`."""

    run_id: str
    task: Task
    final_state: OrchestratorState
    final_answer: str | None
    failure_category: FailureCategory | None
    trajectory: Trajectory
    metrics: RunMetrics

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)
