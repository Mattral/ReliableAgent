"""ReliableOrchestrator: high-level convenience wrapper matching the roadmap's
illustrative DX example (see adr/0008). Accepts `model=`, `tools=`,
`guardrails=`, `enable_checkpointing=`, `enable_observability=`, and
`orchestrator.run(task=str, max_steps=int)`.

NOTE: `model=` is interpreted as an Anthropic model name (not HuggingFace).
Pass `llm_client=` directly to supply any LLMClient without needing a model name.
"""
from __future__ import annotations
from reliableagent.core.models import RunResult, Task
from reliableagent.core.orchestrator import Orchestrator
from reliableagent.executor.tool_registry import ToolRegistry
from reliableagent.llm.mock import MockLLMClient
from reliableagent.memory.backend import FileMemoryBackend, InMemoryBackend
from reliableagent.observability.sinks import ConsoleSink, InMemorySink
from reliableagent.planner.critic import ThresholdCritic
from reliableagent.planner.llm_planner import LLMPlanner


class ReliableOrchestrator:
    """Convenience wrapper: matches the roadmap's `ReliableOrchestrator(model=..., tools=..., ...)` DX."""

    def __init__(self, *, tools: ToolRegistry, guardrails=None, model=None,
                 llm_client=None, critic=None,
                 enable_checkpointing: bool = False,
                 enable_observability: bool = False,
                 checkpoint_dir: str = ".reliableagent/checkpoints") -> None:
        if llm_client is not None:
            resolved_client = llm_client
        elif model is not None:
            from reliableagent.llm.anthropic_client import AnthropicLLMClient
            resolved_client = AnthropicLLMClient(model=model)
        else:
            resolved_client = MockLLMClient()

        self._orchestrator = Orchestrator(
            planner=LLMPlanner(resolved_client),
            critic=critic or ThresholdCritic(),
            tools=tools,
            guardrails=guardrails or [],
            memory=FileMemoryBackend(checkpoint_dir) if enable_checkpointing else InMemoryBackend(),
            sink=ConsoleSink() if enable_observability else InMemorySink(),
        )

    def run(self, task, *, max_steps: int = 20, max_replans: int = 3) -> RunResult:
        resolved = task if isinstance(task, Task) else Task(description=task, max_steps=max_steps, max_replans=max_replans)
        return self._orchestrator.run(resolved)

    def resume(self, run_id: str) -> RunResult:
        return self._orchestrator.resume(run_id)

    def shutdown(self) -> None:
        self._orchestrator.shutdown()

    def __enter__(self): return self
    def __exit__(self, *_): self.shutdown()
