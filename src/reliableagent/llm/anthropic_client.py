"""Anthropic Messages API adapter for the `LLMClient` protocol.

This is the one place in the framework that depends on the `anthropic`
SDK, and that dependency is intentionally optional (declared under
`[project.optional-dependencies] anthropic` in pyproject.toml, imported
lazily below). Everything else in ReliableAgent — Orchestrator,
Planner, Critic, Guardrails — depends only on the provider-agnostic
`LLMClient` protocol in `reliableagent.llm.base`.

Usage::

    from reliableagent.llm.anthropic_client import AnthropicLLMClient

    client = AnthropicLLMClient(model="claude-sonnet-4-6", api_key="sk-...")
    response = client.complete(
        [LLMMessage(role="user", content="Plan a 3-step research task.")],
        system="You are a careful planning assistant.",
    )

If `api_key` is omitted, the underlying SDK falls back to the
`ANTHROPIC_API_KEY` environment variable, matching standard SDK
behavior.
"""

from __future__ import annotations

from typing import Any

from reliableagent.exceptions import LLMRequestError, LLMResponseParsingError
from reliableagent.llm.base import BaseLLMClient, LLMMessage, LLMResponse

_DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicLLMClient(BaseLLMClient):
    """Real `LLMClient` implementation backed by the Anthropic Messages API."""

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        """Construct an Anthropic-backed LLM client.

        Args:
            model: The Anthropic model identifier to use.
            api_key: Explicit API key; if omitted, the SDK reads
                `ANTHROPIC_API_KEY` from the environment.
            max_retries: Passed through to the underlying SDK client.
            client: An already-constructed `anthropic.Anthropic`
                instance, primarily for dependency injection in tests
                without needing the real package installed.
        """
        super().__init__(model_name=model)
        self._client = (
            client if client is not None else self._build_sdk_client(api_key, max_retries)
        )

    @staticmethod
    def _build_sdk_client(api_key: str | None, max_retries: int) -> Any:
        try:
            import anthropic  # noqa: PLC0415 (intentional lazy import)
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise LLMRequestError(
                "The 'anthropic' package is required to use AnthropicLLMClient. "
                "Install it with: pip install 'reliableagent[anthropic]'",
                context={"missing_dependency": "anthropic"},
            ) from exc

        kwargs: dict[str, Any] = {"max_retries": max_retries}
        if api_key is not None:
            kwargs["api_key"] = api_key
        return anthropic.Anthropic(**kwargs)

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Send `messages` to the Anthropic Messages API and normalize the result."""
        payload = [{"role": m.role, "content": m.content} for m in messages]

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": payload,
        }
        if system is not None:
            kwargs["system"] = system

        try:
            raw_response = self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalize any SDK error
            raise LLMRequestError(
                f"Anthropic API request failed: {exc}",
                context={"model": self.model_name},
            ) from exc

        return self._parse_response(raw_response)

    def _parse_response(self, raw_response: Any) -> LLMResponse:
        try:
            text_blocks = [
                block.text
                for block in raw_response.content
                if getattr(block, "type", None) == "text"
            ]
            text = "\n".join(text_blocks)
            usage = getattr(raw_response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
            output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
        except (AttributeError, TypeError) as exc:
            raise LLMResponseParsingError(
                f"Could not parse Anthropic response: {exc}",
                context={"model": self.model_name},
            ) from exc

        raw_dict: dict[str, Any] = {}
        if hasattr(raw_response, "model_dump"):
            try:
                raw_dict = raw_response.model_dump()
            except Exception:  # noqa: BLE001 - best-effort only, never fatal
                raw_dict = {}

        return LLMResponse(
            text=text,
            model=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw=raw_dict,
        )
