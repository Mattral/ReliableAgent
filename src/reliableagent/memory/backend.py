"""Memory & State Manager: checkpoint persistence and trajectory storage.

Per the roadmap: "Supports versioning and checkpointing... Critical
for long-horizon tasks." This module defines the `MemoryBackend`
contract used by the Orchestrator and ships two implementations:

    - `InMemoryBackend`: process-local, dict-backed. Fast, zero
      dependencies, ideal for tests and short-lived runs. Lost on
      process exit (by design — it's a backend choice, not a
      limitation of the contract).
    - `FileMemoryBackend`: persists checkpoints and trajectories as
      JSON files under a configurable directory, so a run can
      actually be killed and resumed in a later process — the literal
      "Ability to resume from checkpoint" P0 requirement.

Both backends support `save_checkpoint` / `load_latest_checkpoint` /
`load_checkpoint` and `save_trajectory` / `load_trajectory`, so the
Orchestrator's checkpointing logic is identical regardless of backend.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from reliableagent.core.models import Checkpoint, Trajectory
from reliableagent.exceptions import CheckpointCorruptedError, CheckpointNotFoundError


@runtime_checkable
class MemoryBackend(Protocol):
    """The contract every Memory backend implements."""

    def save_checkpoint(self, checkpoint: Checkpoint) -> None: ...

    def load_latest_checkpoint(self, run_id: str) -> Checkpoint | None: ...

    def load_checkpoint(self, run_id: str, checkpoint_id: str) -> Checkpoint: ...

    def list_checkpoints(self, run_id: str) -> list[Checkpoint]: ...

    def save_trajectory(self, trajectory: Trajectory) -> None: ...

    def load_trajectory(self, run_id: str) -> Trajectory: ...


class InMemoryBackend:
    """A process-local, dict-backed Memory backend. Not durable across processes."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, list[Checkpoint]] = {}
        self._trajectories: dict[str, Trajectory] = {}
        self._lock = threading.Lock()

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        with self._lock:
            self._checkpoints.setdefault(checkpoint.run_id, []).append(checkpoint)

    def load_latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        with self._lock:
            run_checkpoints = self._checkpoints.get(run_id, [])
            if not run_checkpoints:
                return None
            return max(run_checkpoints, key=lambda c: c.sequence_number)

    def load_checkpoint(self, run_id: str, checkpoint_id: str) -> Checkpoint:
        with self._lock:
            for ckpt in self._checkpoints.get(run_id, []):
                if ckpt.checkpoint_id == checkpoint_id:
                    return ckpt
        raise CheckpointNotFoundError(
            f"No checkpoint '{checkpoint_id}' found for run '{run_id}'.",
            context={"run_id": run_id, "checkpoint_id": checkpoint_id},
        )

    def list_checkpoints(self, run_id: str) -> list[Checkpoint]:
        with self._lock:
            return sorted(
                self._checkpoints.get(run_id, []), key=lambda c: c.sequence_number
            )

    def save_trajectory(self, trajectory: Trajectory) -> None:
        with self._lock:
            self._trajectories[trajectory.run_id] = trajectory

    def load_trajectory(self, run_id: str) -> Trajectory:
        with self._lock:
            if run_id not in self._trajectories:
                raise CheckpointNotFoundError(
                    f"No trajectory found for run '{run_id}'.", context={"run_id": run_id}
                )
            return self._trajectories[run_id]


class FileMemoryBackend:
    """Persists checkpoints and trajectories as JSON files on disk.

    Layout::

        <root>/<run_id>/checkpoints/<seq>_<checkpoint_id>.json
        <root>/<run_id>/trajectory.json

    Sequence-number-prefixed filenames mean `list_checkpoints` can sort
    lexicographically without needing to open every file, and
    `load_latest_checkpoint` is a cheap directory scan.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _run_dir(self, run_id: str) -> Path:
        d = self._root / run_id
        (d / "checkpoints").mkdir(parents=True, exist_ok=True)
        return d

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        run_dir = self._run_dir(checkpoint.run_id)
        filename = f"{checkpoint.sequence_number:08d}_{checkpoint.checkpoint_id}.json"
        path = run_dir / "checkpoints" / filename
        with self._lock:
            path.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")

    def load_latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        checkpoints_dir = self._root / run_id / "checkpoints"
        if not checkpoints_dir.exists():
            return None
        files = sorted(checkpoints_dir.glob("*.json"))
        if not files:
            return None
        return self._read_checkpoint(files[-1])

    def load_checkpoint(self, run_id: str, checkpoint_id: str) -> Checkpoint:
        checkpoints_dir = self._root / run_id / "checkpoints"
        matches = (
            list(checkpoints_dir.glob(f"*_{checkpoint_id}.json"))
            if checkpoints_dir.exists()
            else []
        )
        if not matches:
            raise CheckpointNotFoundError(
                f"No checkpoint '{checkpoint_id}' found for run '{run_id}'.",
                context={"run_id": run_id, "checkpoint_id": checkpoint_id},
            )
        return self._read_checkpoint(matches[0])

    def list_checkpoints(self, run_id: str) -> list[Checkpoint]:
        checkpoints_dir = self._root / run_id / "checkpoints"
        if not checkpoints_dir.exists():
            return []
        return [self._read_checkpoint(p) for p in sorted(checkpoints_dir.glob("*.json"))]

    def save_trajectory(self, trajectory: Trajectory) -> None:
        run_dir = self._run_dir(trajectory.run_id)
        path = run_dir / "trajectory.json"
        with self._lock:
            path.write_text(trajectory.model_dump_json(indent=2), encoding="utf-8")

    def load_trajectory(self, run_id: str) -> Trajectory:
        path = self._root / run_id / "trajectory.json"
        if not path.exists():
            raise CheckpointNotFoundError(
                f"No trajectory found for run '{run_id}'.", context={"run_id": run_id}
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Trajectory(**data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CheckpointCorruptedError(
                f"Trajectory file for run '{run_id}' is corrupted: {exc}",
                context={"run_id": run_id, "path": str(path)},
            ) from exc

    @staticmethod
    def _read_checkpoint(path: Path) -> Checkpoint:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Checkpoint(**data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CheckpointCorruptedError(
                f"Checkpoint file '{path}' is corrupted: {exc}", context={"path": str(path)}
            ) from exc
