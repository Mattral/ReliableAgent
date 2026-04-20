"""PolicyGuardrail: structured, rule-based policy enforcement.

Per Phase 3's "Enhanced Guardrail strategies (policy-based, ...)." The
Phase 0/1 `BasicGuardrail` already enforces a flat list of blocked
substrings, which is a degenerate special case of "policy" but doesn't
let a caller express anything richer -- e.g. "block X only at the
planner_output boundary, but allow it in tool arguments," or "treat this
pattern as MODIFY (redact) rather than an outright BLOCK." `PolicyRule`
makes that distinction explicit and structured rather than something a
caller has to encode by instantiating multiple `BasicGuardrail`s with
different `boundaries` sets.

A `PolicyRule` is deliberately simple (a compiled regex pattern + an
action + an optional scope) rather than a full rules-engine DSL --
matching the project's stated preference for boring, reviewable
mechanisms over a bespoke policy language nobody asked for. Power users
who need more than regex matching can still subclass `Guardrail`
directly; `PolicyGuardrail` covers the common case of "match a pattern,
take an action" well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from reliableagent.core.enums import GuardrailBoundary, GuardrailCategory, GuardrailVerdict
from reliableagent.core.models import GuardrailDecision
from reliableagent.guardrails.base import Guardrail


@dataclass(frozen=True)
class PolicyRule:
    """One structured policy rule: a pattern, an action, and an optional scope.

    Attributes:
        name: A short, stable identifier for this rule, surfaced in the
            `GuardrailDecision.reason` so a blocked/modified payload's
            cause is traceable to a specific named rule, not just "the
            policy guardrail, somehow."
        pattern: A regex pattern (case-insensitive by default) matched
            against the extracted text of a payload.
        action: `GuardrailVerdict.BLOCK` to reject the transition
            outright, or `GuardrailVerdict.MODIFY` to redact the matched
            text and let the (modified) transition proceed.
        scope: The boundaries this rule applies to. Defaults to every
            boundary; narrow it (e.g. `{GuardrailBoundary.FINAL_OUTPUT}`)
            for rules that should only fire on what the user actually
            sees, not on intermediate tool arguments.
        redaction_text: Replacement text used when `action` is MODIFY.
    """

    name: str
    pattern: str
    action: GuardrailVerdict = GuardrailVerdict.BLOCK
    scope: frozenset[GuardrailBoundary] = field(default_factory=lambda: frozenset(GuardrailBoundary))
    redaction_text: str = "[REDACTED]"
    case_sensitive: bool = False

    def __post_init__(self) -> None:
        if self.action not in (GuardrailVerdict.BLOCK, GuardrailVerdict.MODIFY):
            raise ValueError(f"PolicyRule action must be BLOCK or MODIFY, got {self.action!r}.")

    def compiled(self) -> re.Pattern[str]:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return re.compile(self.pattern, flags)


class PolicyGuardrail(Guardrail):
    """Evaluates a payload against an ordered list of structured `PolicyRule`s.

    Rules are evaluated in order; the first matching BLOCK rule whose
    `scope` includes the current boundary determines the verdict for
    THIS guardrail's check (consistent with `GuardrailRunner`'s own
    first-block-wins semantics one layer up, applied here at the
    single-guardrail/multi-rule level). A MODIFY rule's redaction is
    applied and remaining rules are still checked against the redacted
    text, so multiple MODIFY rules can compose (e.g. redact an email
    pattern, then separately redact a phone-number pattern).
    """

    name = "policy_guardrail"
    category = GuardrailCategory.POLICY

    def __init__(
        self, rules: list[PolicyRule], *, boundaries: frozenset[GuardrailBoundary] | None = None
    ) -> None:
        self.rules = rules
        # The guardrail's own `boundaries` (which `applies_to` checks)
        # is the union of every rule's scope -- a rule narrower than
        # that union is still enforced correctly inside `check()`; this
        # union is just what lets the GuardrailRunner know to bother
        # calling this guardrail at all for a given boundary.
        self.boundaries = boundaries or frozenset(b for rule in rules for b in rule.scope) or frozenset(
            GuardrailBoundary
        )

    def check(self, boundary: GuardrailBoundary, payload: Any) -> GuardrailDecision:
        self._with_boundary(boundary)
        text = self._extract_text(payload)
        if text is None:
            return self._allow("Payload has no extractable text; no policy rule applies.")

        current_text = text
        applied_modifications: list[str] = []

        for rule in self.rules:
            if boundary not in rule.scope:
                continue
            match = rule.compiled().search(current_text)
            if not match:
                continue

            if rule.action == GuardrailVerdict.BLOCK:
                return self._block(
                    f"Policy rule '{rule.name}' matched and blocks this transition "
                    f"(matched: {match.group()!r})."
                )

            # MODIFY: redact and keep checking subsequent rules against
            # the redacted text.
            current_text = rule.compiled().sub(rule.redaction_text, current_text)
            applied_modifications.append(rule.name)

        if applied_modifications:
            modified_payload = current_text if isinstance(payload, str) else payload
            return self._modify(
                f"Policy rule(s) {applied_modifications} redacted matched content.",
                modified_payload,
            )

        return self._allow("No policy rule matched.")

    @staticmethod
    def _extract_text(payload: Any) -> str | None:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            string_values = [v for v in payload.values() if isinstance(v, str)]
            return " ".join(string_values) if string_values else None
        return None


# ---------------------------------------------------------------------------
# A small, ready-to-use rule set for common, easily-articulated policies.
# Callers are not required to use these -- PolicyGuardrail works with any
# list of PolicyRule -- but most projects want SOME starting point rather
# than writing every rule from scratch.
# ---------------------------------------------------------------------------


def default_policy_rules() -> list[PolicyRule]:
    """A small set of illustrative, conservative default policy rules.

    Intentionally narrow in scope (a handful of clearly-bad patterns)
    rather than an attempt at a comprehensive safety policy -- this is a
    starting point for `PolicyGuardrail`, not a claim that regex matching
    alone is sufficient content moderation for a production deployment.
    """
    return [
        PolicyRule(
            name="block_credential_exfiltration_language",
            pattern=r"\b(exfiltrate|steal)\s+(confidential|secret|private)\s+(data|information)\b",
            action=GuardrailVerdict.BLOCK,
        ),
        PolicyRule(
            name="block_prompt_injection_phrases",
            pattern=r"\bignore\s+(all\s+)?(previous|prior|above)\s+instructions\b",
            action=GuardrailVerdict.BLOCK,
        ),
    ]
