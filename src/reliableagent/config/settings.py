"""Configuration system: both code-based and YAML-based configuration.

Per the roadmap's DX requirements table: "Configuration: Both code-based
and YAML config supported — Good for experiments." `ReliableAgentConfig`
is a single typed model covering every tunable that matters across a run
(step/replan budgets, executor timeouts/retries, guardrail toggles,
memory backend choice, observability sink choice). It can be constructed
directly in code, or loaded from a YAML file via `ReliableAgentConfig.
from_yaml(...)`, so the same settings work equally well hand-written in a
script or checked into a repo as `config.yaml` for reproducible
experiment runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from reliableagent._compat import BaseModel, ConfigDict, Field
from reliableagent.exceptions import ConfigurationError


class ExecutorConfig(BaseModel):
    """Tunables for the `Executor`."""

    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(default=1, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.5, ge=0.0)
    default_timeout_seconds: float = Field(default=30.0, gt=0.0)


class GuardrailConfig(BaseModel):
    """Tunables for the default `BasicGuardrail`."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True)
    max_length: int = Field(default=50_000, gt=0)
    blocked_substrings: list[str] = Field(default_factory=list)


class MemoryConfig(BaseModel):
    """Selects and configures the Memory backend."""

    model_config = ConfigDict(extra="forbid")

    backend: str = Field(default="in_memory", description="'in_memory' or 'file'.")
    file_root: str = Field(default=".reliableagent/runs")

    def _validate_backend(self) -> None:
        if self.backend not in {"in_memory", "file"}:
            raise ConfigurationError(
                f"Unknown memory backend '{self.backend}'. Expected 'in_memory' or 'file'.",
                context={"backend": self.backend},
            )


class ObservabilityConfig(BaseModel):
    """Selects and configures observability sinks."""

    model_config = ConfigDict(extra="forbid")

    console: bool = Field(default=False)
    jsonl_path: str | None = Field(default=None)


class ReliableAgentConfig(BaseModel):
    """Top-level configuration for a ReliableAgent run/deployment.

    Example (code-based)::

        config = ReliableAgentConfig(
            task_max_steps=30,
            executor=ExecutorConfig(max_retries=2),
        )

    Example (YAML-based), given a ``config.yaml``::

        task_max_steps: 30
        task_max_replans: 3
        executor:
          max_retries: 2
          retry_backoff_seconds: 0.5
        guardrails:
          enabled: true
          blocked_substrings: ["ignore previous instructions"]
        memory:
          backend: file
          file_root: ".reliableagent/runs"
        observability:
          console: true
          jsonl_path: "logs/run.jsonl"

    loaded via::

        config = ReliableAgentConfig.from_yaml("config.yaml")
    """

    model_config = ConfigDict(extra="forbid")

    task_max_steps: int = Field(default=20, gt=0, le=500)
    task_max_replans: int = Field(default=3, ge=0, le=50)
    llm_model: str = Field(default="claude-sonnet-4-6")
    llm_max_tokens: int = Field(default=2048, gt=0)
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    guardrails: GuardrailConfig = Field(default_factory=GuardrailConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ReliableAgentConfig":
        """Load configuration from a YAML file.

        Raises:
            ConfigurationError: if the file is missing, isn't valid
                YAML, or its contents don't validate against this
                model's schema.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise ConfigurationError(
                f"Config file not found: {file_path}", context={"path": str(file_path)}
            )
        try:
            raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                f"Config file is not valid YAML: {exc}", context={"path": str(file_path)}
            ) from exc

        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReliableAgentConfig":
        """Construct configuration from a plain dict (e.g. parsed YAML/JSON)."""
        try:
            return cls(**data)
        except Exception as exc:  # noqa: BLE001 - normalize any validation error
            raise ConfigurationError(
                f"Invalid configuration: {exc}", context={"raw_config": data}
            ) from exc

    def to_yaml(self, path: str | Path) -> None:
        """Write this configuration to a YAML file."""
        Path(path).write_text(
            yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
        )
