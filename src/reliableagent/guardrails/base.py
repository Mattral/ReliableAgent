"""Guardrail Layer: cross-cutting checks at every major architectural boundary.

Per the project philosophy: "Guardrails are not an add-on or
post-processing step. They are a core layer that every major
transition in the system must pass through." This module defines the
`Guardrail` contract; `reliableagent.guardrails.basic` provides a
concrete, configurable starter implementation; and
`reliableagent.guardrails.runner` provides the `GuardrailRunner` that
the Orchestrator actually calls at each boundary.

Design notes:
    - Every guardrail evaluation produces a `GuardrailDecision`
      (defined in `reliableagent.core.models`), never a bare bool —
      so the *reason* for a block is always observable, not just the
      fact of it.
    - A guardrail can ALLOW, BLOCK, or MODIFY a payload. MODIFY lets a
      guardrail sanitize/redact content rather than hard-failing the
      whole step, which is often the better choice for things like PII
      redaction.
    - Guardrails are evaluated at one or more `GuardrailBoundary`
      values; a guardrail declares which boundaries it applies to so
      the `GuardrailRunner` can skip irrelevant checks cheaply.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from reliableagent.core.enums import GuardrailBoundary, GuardrailCategory, GuardrailVerdict
from reliableagent.core.models import GuardrailDecision


class Guardrail(ABC):
    """Base class for all guardrails.

    Subclasses implement `check`, which receives the boundary being
    evaluated and the payload at that boundary (e.g. the Planner's
    raw output text, a tool's arguments, a tool's result, or the final
    answer string) and must return a `GuardrailDecision`.
    """

    name: str = "unnamed_guardrail"
    category: GuardrailCategory = GuardrailCategory.VALIDATION
    boundaries: frozenset[GuardrailBoundary] = frozenset()

    def applies_to(self, boundary: GuardrailBoundary) -> bool:
        """Whether this guardrail should run at the given boundary."""
        return boundary in self.boundaries

    @abstractmethod
    def check(self, boundary: GuardrailBoundary, payload: Any) -> GuardrailDecision:
        """Evaluate `payload` at `boundary` and return a verdict.

        Implementations should never raise for an ordinary policy
        violation — that's what `GuardrailVerdict.BLOCK` is for.
        Raising should be reserved for genuine guardrail-internal bugs.
        """
        raise NotImplementedError

    def _allow(self, reason: str = "") -> GuardrailDecision:
        """Convenience constructor for an ALLOW decision."""
        return GuardrailDecision(
            guardrail_name=self.name,
            boundary=self._current_boundary,
            category=self.category,
            verdict=GuardrailVerdict.ALLOW,
            reason=reason,
        )

    # `_current_boundary` is set transiently by `check()` implementations
    # via `self._with_boundary(boundary)` so the `_allow`/`_block`/`_modify`
    # helpers don't need the boundary repeated at every call site.
    _current_boundary: GuardrailBoundary = GuardrailBoundary.PLANNER_INPUT

    def _with_boundary(self, boundary: GuardrailBoundary) -> Guardrail:
        self._current_boundary = boundary
        return self

    def _block(self, reason: str) -> GuardrailDecision:
        """Convenience constructor for a BLOCK decision."""
        return GuardrailDecision(
            guardrail_name=self.name,
            boundary=self._current_boundary,
            category=self.category,
            verdict=GuardrailVerdict.BLOCK,
            reason=reason,
        )

    def _modify(self, reason: str, modified_payload: Any) -> GuardrailDecision:
        """Convenience constructor for a MODIFY decision."""
        return GuardrailDecision(
            guardrail_name=self.name,
            boundary=self._current_boundary,
            category=self.category,
            verdict=GuardrailVerdict.MODIFY,
            reason=reason,
            modified_payload=modified_payload,
        )
