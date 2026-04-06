"""A deterministic, offline, no-network mock LLM client.

This is the default backend used throughout ReliableAgent's own test
suite. It exists to satisfy two requirements from the roadmap
simultaneously:

    1. "Fast iteration with small models during development" — there is
       no faster or cheaper model than one that doesn't make a network
       call at all.
    2. "Reproducibility ... seeds logged and controllable" — given the
       same script/queue, this client always returns the same
       sequence of responses, which makes the Orchestrator, Planner,
       and Critic fully unit-testable without flakiness or cost.

Two modes are supported:
    - **Scripted mode**: construct with an explicit list of responses
      (strings or `LLMResponse`s) to return in order. This is what
      most unit tests use — full control over exactly what the
      "model" says at each step.
    - **Rule-based fallback mode**: if no script is provided (or the
      script is exhausted), the mock falls back to simple
      keyword-driven heuristics so example scripts still produce
      plausible-looking plans/critiques without any external
      dependency.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from reliableagent.llm.base import BaseLLMClient, LLMMessage, LLMResponse


class MockLLMClient(BaseLLMClient):
    """Deterministic stand-in for a real LLM provider.

    Example:
        >>> client = MockLLMClient(responses=["plan: search then summarize"])
        >>> resp = client.complete([LLMMessage(role="user", content="hi")])
        >>> resp.text
        'plan: search then summarize'
    """

    def __init__(
        self,
        responses: Sequence[str | LLMResponse] | None = None,
        *,
        model_name: str = "mock-deterministic-v1",
        default_response: str = "OK.",
    ) -> None:
        super().__init__(model_name=model_name)
        self._queue: deque[str | LLMResponse] = deque(responses or [])
        self._default_response = default_response
        self.call_log: list[list[LLMMessage]] = []

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Pop the next scripted response, or fall back to a default string."""
        self.call_log.append(list(messages))

        if self._queue:
            next_item = self._queue.popleft()
            if isinstance(next_item, LLMResponse):
                return next_item
            text = next_item
        else:
            text = self._default_response

        input_tokens = sum(len(m.content.split()) for m in messages)
        return LLMResponse(
            text=text,
            model=self.model_name,
            input_tokens=input_tokens,
            output_tokens=len(text.split()),
            raw={"mock": True, "seed": seed},
        )

    def enqueue(self, response: str | LLMResponse) -> None:
        """Add an additional scripted response to the end of the queue."""
        self._queue.append(response)

    @property
    def remaining(self) -> int:
        """Number of scripted responses left in the queue."""
        return len(self._queue)
