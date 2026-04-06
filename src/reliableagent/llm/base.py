"""Provider-agnostic LLM client interface.

The Planner and Critic depend only on this `LLMClient` protocol, never
on a concrete provider SDK. This is what makes "Multiple Planner
strategies" and provider swaps (Anthropic, OpenAI, a local model, a
deterministic mock for tests) possible without touching orchestration
code — a direct application of the "Explicit Contracts & Modularity"
principle to the one component most projects hard-wire to a single
vendor.

Two concrete implementations ship with v1:
    - `MockLLMClient` (reliableagent.llm.mock): a deterministic,
      offline, no-network client used in tests and for fast local
      development ("Fast iteration with small models during
      development" from Section 7).
    - `AnthropicLLMClient` (reliableagent.llm.anthropic_client): a real
      adapter over the Anthropic Messages API, imported lazily so the
      `anthropic` package is only required if you actually use it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from reliableagent._compat import BaseModel, ConfigDict, Field


class LLMMessage(BaseModel):
    """A single message in an LLM conversation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str = Field(..., description="'system', 'user', or 'assistant'.")
    content: str = Field(..., min_length=0)


class LLMResponse(BaseModel):
    """The normalized result of a single LLM completion call.

    Normalizing across providers here means every other component
    (Planner, Critic, observability) only ever has to understand this
    one shape, regardless of which provider produced it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    model: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    raw: dict[str, object] = Field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    """The minimal contract any LLM backend must satisfy.

    Deliberately small: a single `complete` method. Tool-calling,
    streaming, and other provider-specific richness are layered on top
    by individual adapters as needed, but everything in the
    Orchestrator/Planner/Critic only ever calls `complete`.
    """

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Generate a completion for the given message history.

        Args:
            messages: The conversation so far (user/assistant turns).
            system: An optional system prompt.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature; 0.0 should be as
                deterministic as the provider allows (important for
                reproducibility, per Section 7 of the roadmap).
            seed: An optional seed for providers that support it.

        Returns:
            A normalized `LLMResponse`.

        Raises:
            reliableagent.exceptions.LLMRequestError: if the request
                to the underlying provider fails.
        """
        ...


class BaseLLMClient(ABC):
    """Convenience abstract base for LLM clients that also tracks identity.

    Concrete clients are not *required* to subclass this (the real
    contract is the `LLMClient` Protocol above, checked structurally),
    but doing so gives you `model_name` bookkeeping for free, which the
    observability layer uses to record exactly which model produced
    each response (Section 7: "Full prompts, model versions ...
    captured per run").
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    @abstractmethod
    def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse: ...
