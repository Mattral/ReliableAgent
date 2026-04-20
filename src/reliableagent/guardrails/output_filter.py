"""OutputFilterGuardrail: pattern-based redaction of sensitive content.

Per Phase 3's "Enhanced Guardrail strategies (..., output filtering)."
Distinct from `PolicyGuardrail` (which BLOCKs or MODIFYs based on
named, scoped rules a caller defines): `OutputFilterGuardrail` is
specifically a MODIFY-only guardrail, pre-loaded with a small library of
common PII patterns (email addresses, phone numbers, US SSNs, credit-card
numbers), intended to run at `FINAL_OUTPUT` (and optionally `TOOL_OUTPUT`)
to redact sensitive content before it's ever shown to the user or fed
back into a subsequent plan step -- rather than BLOCKing the whole
transition, which would needlessly fail an otherwise-fine response just
because it happened to mention an email address.

This is regex-based pattern matching, not a machine-learning PII
detector -- it will miss PII that doesn't match a known pattern shape and
can occasionally false-positive on text that merely looks like PII. That
tradeoff (simple, reviewable, zero-dependency, good-not-perfect recall)
is the right one for this delivery and is documented honestly rather than
oversold; see `docs/roadmap_status.md` for where a real ML-based
classifier would sit in a future phase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from reliableagent.core.enums import GuardrailBoundary, GuardrailCategory
from reliableagent.core.models import GuardrailDecision
from reliableagent.guardrails.base import Guardrail


@dataclass(frozen=True)
class RedactionPattern:
    """One named PII pattern this guardrail knows how to redact."""

    name: str
    pattern: str
    placeholder: str

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern)


# A small, named library of common PII shapes. Patterns are intentionally
# straightforward regexes (no attempt at fully RFC-compliant email
# matching, for instance) -- good real-world recall on typical inputs,
# not a formal grammar.
STANDARD_REDACTION_PATTERNS: list[RedactionPattern] = [
    RedactionPattern(
        name="email_address",
        pattern=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        placeholder="[EMAIL_REDACTED]",
    ),
    RedactionPattern(
        name="us_phone_number",
        pattern=r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        placeholder="[PHONE_REDACTED]",
    ),
    RedactionPattern(
        name="us_ssn",
        pattern=r"\b\d{3}-\d{2}-\d{4}\b",
        placeholder="[SSN_REDACTED]",
    ),
    RedactionPattern(
        name="credit_card_number",
        pattern=r"\b(?:\d[ -]*?){13,16}\b",
        placeholder="[CARD_NUMBER_REDACTED]",
    ),
]


class OutputFilterGuardrail(Guardrail):
    """Redacts known PII patterns from text payloads via MODIFY verdicts.

    Defaults to running at `FINAL_OUTPUT` only -- the boundary where
    "what does the user actually see" matters most -- but can be
    configured to also cover `TOOL_OUTPUT`, e.g. to keep PII fetched by a
    tool from propagating into a subsequent plan step's context at all.
    Never BLOCKs: a payload containing redactable PII is always allowed
    through in its redacted form, never rejected outright, since
    redaction (not refusal) is the correct response to "this contains an
    email address," in contrast to `PolicyGuardrail`'s BLOCK-by-default
    rules for genuinely disallowed content.
    """

    name = "output_filter_guardrail"
    category = GuardrailCategory.OUTPUT_FILTERING

    def __init__(
        self,
        *,
        patterns: list[RedactionPattern] | None = None,
        boundaries: frozenset[GuardrailBoundary] | None = None,
    ) -> None:
        self.patterns = patterns or list(STANDARD_REDACTION_PATTERNS)
        self.boundaries = boundaries or frozenset({GuardrailBoundary.FINAL_OUTPUT})

    def check(self, boundary: GuardrailBoundary, payload: Any) -> GuardrailDecision:
        self._with_boundary(boundary)
        if not isinstance(payload, str):
            return self._allow("Payload is not plain text; output filtering only applies to text.")

        redacted_text = payload
        matched_patterns: list[str] = []
        for pattern in self.patterns:
            new_text, count = pattern.compiled().subn(pattern.placeholder, redacted_text)
            if count > 0:
                redacted_text = new_text
                matched_patterns.append(f"{pattern.name} x{count}")

        if not matched_patterns:
            return self._allow("No known PII patterns found.")

        return self._modify(
            f"Redacted {len(matched_patterns)} pattern type(s): {matched_patterns}.",
            redacted_text,
        )
