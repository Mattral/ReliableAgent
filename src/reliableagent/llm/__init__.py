"""Provider-agnostic LLM client abstraction.

See `reliableagent.llm.base` for the `LLMClient` protocol that all
backends implement, `reliableagent.llm.mock` for the deterministic
offline client used in tests, and
`reliableagent.llm.anthropic_client` for the real Anthropic adapter.
"""

from reliableagent.llm.base import BaseLLMClient, LLMClient, LLMMessage, LLMResponse
from reliableagent.llm.mock import MockLLMClient

__all__ = [
    "BaseLLMClient",
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "MockLLMClient",
]


def __getattr__(name: str) -> object:
    """Lazily expose `AnthropicLLMClient` without importing `anthropic` eagerly."""
    if name == "AnthropicLLMClient":
        from reliableagent.llm.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
