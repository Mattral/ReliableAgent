"""Concrete, ready-to-use guardrail implementations.

`BasicGuardrail` is the default, configurable guardrail referenced
directly in the project's target DX example
(`guardrails=[BasicGuardrail()]`). It composes several focused checks
— max-length validation, blocked-keyword policy, and tool-argument
type sanity — into a single guardrail so a new user gets reasonable
default protection without assembling a guardrail pipeline by hand.
Power users can still register additional, more specific guardrails
(or replace `BasicGuardrail` entirely) since every guardrail shares
the same `Guardrail` contract.
"""

from __future__ import annotations

from typing import Any

from reliableagent.core.enums import GuardrailBoundary, GuardrailCategory
from reliableagent.core.models import GuardrailDecision
from reliableagent.guardrails.base import Guardrail


class BasicGuardrail(Guardrail):
    """A sensible default guardrail covering common, cheap-to-check failure modes.

    Checks applied:
        - Rejects empty/whitespace-only Planner output or final answers.
        - Enforces a configurable maximum payload length (defends
          against runaway generations).
        - Blocks payloads containing any of a configurable set of
          disallowed substrings (simple policy enforcement; swap for a
          real policy/safety classifier in production).

    Runs at every boundary by default, since these checks are cheap
    and broadly applicable; pass a narrower `boundaries` set to scope
    it down.
    """

    name = "basic_guardrail"
    category = GuardrailCategory.VALIDATION

    def __init__(
        self,
        *,
        max_length: int = 50_000,
        blocked_substrings: list[str] | None = None,
        boundaries: frozenset[GuardrailBoundary] | None = None,
    ) -> None:
        self.max_length = max_length
        self.blocked_substrings = [s.lower() for s in (blocked_substrings or [])]
        self.boundaries = boundaries or frozenset(GuardrailBoundary)

    def check(self, boundary: GuardrailBoundary, payload: Any) -> GuardrailDecision:
        self._with_boundary(boundary)
        text = self._extract_text(payload)

        if text is not None:
            if not text.strip():
                return self._block(f"Payload at {boundary.value} is empty or whitespace-only.")
            if len(text) > self.max_length:
                return self._block(
                    f"Payload at {boundary.value} exceeds max_length="
                    f"{self.max_length} (got {len(text)} chars)."
                )
            lowered = text.lower()
            for substring in self.blocked_substrings:
                if substring in lowered:
                    return self._block(
                        f"Payload at {boundary.value} contains blocked content: "
                        f"'{substring}'."
                    )

        return self._allow("Passed basic validation checks.")

    @staticmethod
    def _extract_text(payload: Any) -> str | None:
        """Best-effort extraction of a string to validate from an arbitrary payload."""
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            # Tool arguments / results are often dicts; concatenate string values.
            string_values = [v for v in payload.values() if isinstance(v, str)]
            return " ".join(string_values) if string_values else None
        return None


class ToolArgumentSanityGuardrail(Guardrail):
    """Blocks tool calls whose arguments are obviously malformed.

    Specifically targets `GuardrailBoundary.TOOL_INPUT`: catches cases
    where the Planner has hallucinated argument names/types that a
    naive Executor might otherwise pass straight to a tool and crash
    on. This is intentionally narrow and composable with
    `BasicGuardrail` rather than duplicating its checks.
    """

    name = "tool_argument_sanity"
    category = GuardrailCategory.VALIDATION
    boundaries = frozenset({GuardrailBoundary.TOOL_INPUT})

    def __init__(self, *, max_arguments: int = 20) -> None:
        self.max_arguments = max_arguments

    def check(self, boundary: GuardrailBoundary, payload: Any) -> GuardrailDecision:
        self._with_boundary(boundary)
        if not isinstance(payload, dict):
            return self._block(
                f"Tool arguments must be a dict, got {type(payload).__name__}."
            )
        if len(payload) > self.max_arguments:
            return self._block(
                f"Tool call has {len(payload)} arguments, exceeding max_arguments="
                f"{self.max_arguments}."
            )
        return self._allow("Tool arguments passed sanity checks.")


class FinalOutputPolicyGuardrail(Guardrail):
    """Enforces simple policy rules specifically on the final answer.

    Kept separate from `BasicGuardrail` so policy rules that should
    only ever apply to what the user actually sees (and not, say, to
    intermediate tool arguments) are easy to reason about and audit
    independently.
    """

    name = "final_output_policy"
    category = GuardrailCategory.POLICY
    boundaries = frozenset({GuardrailBoundary.FINAL_OUTPUT})

    def __init__(self, *, require_non_empty: bool = True, max_length: int = 20_000) -> None:
        self.require_non_empty = require_non_empty
        self.max_length = max_length

    def check(self, boundary: GuardrailBoundary, payload: Any) -> GuardrailDecision:
        self._with_boundary(boundary)
        text = payload if isinstance(payload, str) else str(payload)

        if self.require_non_empty and not text.strip():
            return self._block("Final answer must not be empty.")
        if len(text) > self.max_length:
            return self._block(
                f"Final answer exceeds max_length={self.max_length} (got {len(text)} chars)."
            )
        return self._allow("Final output passed policy checks.")
