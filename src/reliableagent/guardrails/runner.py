"""GuardrailRunner: evaluates all applicable guardrails at a boundary.

The Orchestrator and Executor never call individual `Guardrail`
instances directly — they go through a `GuardrailRunner`, which:
    1. Filters to guardrails that `applies_to()` the requested boundary.
    2. Runs each one (first BLOCK wins; MODIFY is applied and chained
       into the next guardrail's input).
    3. Returns every individual `GuardrailDecision` produced, so the
       Orchestrator can log all of them (not just the final verdict)
       and the Tracer can emit one event per decision.

This keeps "run guardrails at this boundary" a single call site in the
Orchestrator regardless of how many guardrails are configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reliableagent.core.enums import GuardrailBoundary, GuardrailVerdict
from reliableagent.core.models import GuardrailDecision
from reliableagent.guardrails.base import Guardrail


@dataclass
class GuardrailRunResult:
    """The aggregate outcome of running all applicable guardrails at a boundary."""

    allowed: bool
    final_payload: Any
    decisions: list[GuardrailDecision] = field(default_factory=list)
    blocking_decision: GuardrailDecision | None = None


class GuardrailRunner:
    """Runs a configured list of `Guardrail`s against payloads at a boundary."""

    def __init__(self, guardrails: list[Guardrail] | None = None) -> None:
        self._guardrails = guardrails or []

    @property
    def guardrails(self) -> list[Guardrail]:
        """Defensive copy of configured guardrails."""
        return list(self._guardrails)

    def add(self, guardrail: Guardrail) -> None:
        """Register an additional guardrail at runtime."""
        self._guardrails.append(guardrail)

    def run(self, boundary: GuardrailBoundary, payload: Any) -> GuardrailRunResult:
        """Evaluate every guardrail that applies to `boundary`, in registration order.

        Stops at the first BLOCK (fail-fast: no point running further
        checks once the transition is already going to be rejected).
        MODIFY decisions update `final_payload` and are passed forward
        to subsequent guardrails, so guardrails can be chained (e.g. a
        redaction guardrail followed by a length check on the redacted
        text).
        """
        applicable = [g for g in self._guardrails if g.applies_to(boundary)]
        decisions: list[GuardrailDecision] = []
        current_payload = payload

        for guardrail in applicable:
            decision = guardrail.check(boundary, current_payload)
            decisions.append(decision)

            if decision.verdict == GuardrailVerdict.BLOCK:
                return GuardrailRunResult(
                    allowed=False,
                    final_payload=current_payload,
                    decisions=decisions,
                    blocking_decision=decision,
                )
            if decision.verdict == GuardrailVerdict.MODIFY:
                current_payload = decision.modified_payload

        return GuardrailRunResult(allowed=True, final_payload=current_payload, decisions=decisions)
