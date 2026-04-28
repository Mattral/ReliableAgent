"""LLM usage and latency tracking: decorator pattern, no Planner/Critic contract changes."""
from __future__ import annotations
import threading, time
from dataclasses import dataclass, field
from reliableagent.llm.base import LLMClient, LLMMessage, LLMResponse


@dataclass
class LLMUsageStats:
    """Thread-safe accumulator for token usage and latency across LLM calls."""
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(self, response: LLMResponse, latency_seconds: float) -> None:
        with self._lock:
            self.total_calls += 1
            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens
            self.total_latency_seconds += latency_seconds

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def average_latency_seconds(self) -> float:
        return self.total_latency_seconds / self.total_calls if self.total_calls else 0.0

    def snapshot(self) -> "LLMUsageStats":
        with self._lock:
            return LLMUsageStats(
                total_calls=self.total_calls,
                total_input_tokens=self.total_input_tokens,
                total_output_tokens=self.total_output_tokens,
                total_latency_seconds=self.total_latency_seconds,
            )


class UsageTrackingLLMClient:
    """Wraps any LLMClient, recording token usage/latency into a shared LLMUsageStats."""
    def __init__(self, wrapped: LLMClient, stats: LLMUsageStats) -> None:
        self._wrapped = wrapped
        self._stats = stats

    @property
    def stats(self) -> LLMUsageStats:
        return self._stats

    def complete(self, messages: list[LLMMessage], *, system=None,
                 max_tokens: int = 1024, temperature: float = 0.0,
                 seed=None) -> LLMResponse:
        start = time.monotonic()
        response = self._wrapped.complete(messages, system=system,
                                          max_tokens=max_tokens, temperature=temperature, seed=seed)
        self._stats.record(response, time.monotonic() - start)
        return response
