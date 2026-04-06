"""Unit tests for `reliableagent.memory`."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from reliableagent.core.enums import OrchestratorState, StepType
from reliableagent.core.models import Checkpoint, Plan, PlanStep, Task, Trajectory
from reliableagent.exceptions import CheckpointNotFoundError
from reliableagent.memory.backend import FileMemoryBackend, InMemoryBackend


def _sample_checkpoint(run_id: str, sequence_number: int) -> Checkpoint:
    task = Task(description="memory backend test")
    step = PlanStep(step_type=StepType.REASONING, description="think")
    plan = Plan(task_id=task.task_id, steps=[step])
    return Checkpoint(
        run_id=run_id,
        sequence_number=sequence_number,
        orchestrator_state=OrchestratorState.EXECUTING,
        task=task,
        current_plan=plan,
    )


def test_in_memory_backend_save_and_load_latest():
    backend = InMemoryBackend()
    backend.save_checkpoint(_sample_checkpoint("run1", 0))
    backend.save_checkpoint(_sample_checkpoint("run1", 1))
    latest = backend.load_latest_checkpoint("run1")
    assert latest is not None
    assert latest.sequence_number == 1


def test_in_memory_backend_load_latest_returns_none_when_absent():
    backend = InMemoryBackend()
    assert backend.load_latest_checkpoint("nonexistent_run") is None


def test_in_memory_backend_list_checkpoints_sorted():
    backend = InMemoryBackend()
    backend.save_checkpoint(_sample_checkpoint("run1", 2))
    backend.save_checkpoint(_sample_checkpoint("run1", 0))
    backend.save_checkpoint(_sample_checkpoint("run1", 1))
    seqs = [c.sequence_number for c in backend.list_checkpoints("run1")]
    assert seqs == [0, 1, 2]


def test_in_memory_backend_trajectory_roundtrip():
    backend = InMemoryBackend()
    task = Task(description="t")
    traj = Trajectory(task=task)
    backend.save_trajectory(traj)
    loaded = backend.load_trajectory(traj.run_id)
    assert loaded.run_id == traj.run_id


def test_in_memory_backend_missing_trajectory_raises():
    backend = InMemoryBackend()
    with pytest.raises(CheckpointNotFoundError):
        backend.load_trajectory("nonexistent")


def test_file_backend_full_json_roundtrip_preserves_nested_models():
    with tempfile.TemporaryDirectory() as tmpdir:
        backend = FileMemoryBackend(tmpdir)
        ckpt = _sample_checkpoint("run1", 0)
        backend.save_checkpoint(ckpt)
        loaded = backend.load_latest_checkpoint("run1")
        assert loaded is not None
        assert loaded.task.description == ckpt.task.description
        assert loaded.current_plan is not None
        assert len(loaded.current_plan.steps) == 1
        assert loaded.orchestrator_state == OrchestratorState.EXECUTING


def test_file_backend_load_checkpoint_by_id():
    with tempfile.TemporaryDirectory() as tmpdir:
        backend = FileMemoryBackend(tmpdir)
        ckpt = _sample_checkpoint("run1", 0)
        backend.save_checkpoint(ckpt)
        loaded = backend.load_checkpoint("run1", ckpt.checkpoint_id)
        assert loaded.checkpoint_id == ckpt.checkpoint_id


def test_file_backend_missing_checkpoint_raises():
    with tempfile.TemporaryDirectory() as tmpdir:
        backend = FileMemoryBackend(tmpdir)
        with pytest.raises(CheckpointNotFoundError):
            backend.load_checkpoint("run1", "does_not_exist")


def test_file_backend_persists_across_backend_instances():
    """Simulates resuming in a fresh process: a new backend instance pointed
    at the same directory must see checkpoints saved by a prior instance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        backend1 = FileMemoryBackend(tmpdir)
        ckpt = _sample_checkpoint("run1", 0)
        backend1.save_checkpoint(ckpt)

        backend2 = FileMemoryBackend(tmpdir)
        loaded = backend2.load_latest_checkpoint("run1")
        assert loaded is not None
        assert loaded.checkpoint_id == ckpt.checkpoint_id


def test_file_backend_none_optional_field_roundtrips():
    with tempfile.TemporaryDirectory() as tmpdir:
        backend = FileMemoryBackend(tmpdir)
        task = Task(description="no plan yet")
        ckpt = Checkpoint(
            run_id="run2",
            sequence_number=0,
            orchestrator_state=OrchestratorState.PENDING,
            task=task,
            current_plan=None,
        )
        backend.save_checkpoint(ckpt)
        loaded = backend.load_latest_checkpoint("run2")
        assert loaded is not None
        assert loaded.current_plan is None
