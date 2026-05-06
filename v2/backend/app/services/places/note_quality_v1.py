"""Note Quality Gate v1.

Enforces concierge-grade quality on generated venue notes.

Rules:
- Rating/review counts may appear only as SECONDARY context after a real differentiator.
- Rating/review counts may NOT be the primary reason or opening phrase.
- Unsupported scenic/view claims are rejected unless modifier_status confirms them.
- Notes that are purely address + rating, name + rating, or rank-comparison are rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .modifier_evidence_v1 import ModifierStatus


class NoteQualityResult(str, Enum):
    PASS = "PASS"
    FAIL_RATING_PRIMARY = "FAIL_RATING_PRIMARY"
    FAIL_UNSUPPORTED_CLAIM = "FAIL_UNSUPPORTED_CLAIM"
    FAIL_GENERIC_ONLY = "FAIL_GENERIC_ONLY"


# Patterns that signal rating/reviews are the primary differentiator
_RATING_PRIMARY_PATTERNS: list[re.Pattern] = [
    re.compile(r"highest[- ]rated", re.I),
    re.compile(r"second[- ](most[- ]reviewed|largest review base|highest)", re.I),
    re.compile(r"\breview base\b", re.I),
    re.compile(r"\d\.\d\s*★\s+across\b", re.I),
    re.compile(r"from\s+\d{3,},?\d*\s+reviews", re.I),
    re.compile(r"\d\.\d\s*★\s+from\s+\d", re.I),
    re.compile(r"\d\.\d\s*★\s+score\b", re.I),
    re.compile(r"backed\s+by\s+\d", re.I),
    re.compile(r"strong\s+on\s+volume\s+of\s+feedback", re.I),
    re.compile(r"solid\s+mid[- ]tier\s+option", re.I),
    re.compile(r"smallest\s+review\s+base", re.I),
    re.compile(r"second[- ]largest\s+review", re.I),
    re.compile(r"established\s+reputation\s*\.", re.I),
    re.compile(r"consistent\s+crowd\s+draw", re.I),
    re.compile(r"strong\s+flagship\s+choice\s+based\s+on\s+rating", re.I),
    re.compile(r"most[- ]reviewed\s+option\b", re.I),
    re.compile(r"highest[- ]rated\s+option\b", re.I),
]

# Patterns that signal a note opens with or leads with rating/reviews
_RATING_LEAD_PATTERNS: list[re.Pattern] = [
    re.compile(r"^['\"]?[A-Z][^.]*?\d\.\d\s*★[^.]*\.", re.I),  # starts with title + rating sentence
    re.compile(r"^Highest[- ]rated", re.I),
    re.compile(r"^Second[- ](most|largest)", re.I),
    re.compile(r"^With\s+\d\.\d\s*★", re.I),
]

# Unsupported scenic claim patterns (blocked unless modifier_status confirms)
_UNSUPPORTED_SCENIC_PATTERNS: list[re.Pattern] = [
    re.compile(r"riverfront\s+(view|seating|terrace|patio)", re.I),
    re.compile(r"waterfront\s+(seating|view|terrace|patio|dining)", re.I),
    re.compile(r"river\s+view(s?)\b", re.I),
    re.compile(r"scenic\s+(river|waterfront|view)", re.I),
    re.compile(r"great\s+(riverfront|waterfront|river)\s+views", re.I),
    re.compile(r"views?\s+of\s+the\s+(river|water|lake)\b", re.I),
]

# Unsupported view/scenic claim (blocked for view modifier unless confirmed_listing_context).
# Patterns must only catch POSITIVE assertions of a view — not negations like
# "no confirmed view" or "view features are not present".
_UNSUPPORTED_VIEW_PATTERNS: list[re.Pattern] = [
    re.compile(r"panoramic\s+views?\b", re.I),
    re.compile(r"skyline\s+views?\b", re.I),
    re.compile(r"\b(offers?|boasts?|features?|has)\s+(great|stunning|beautiful|incredible)\s+views?\b", re.I),
    re.compile(r"\bwith\s+(stunning|breathtaking|panoramic|spectacular)\s+views?\b", re.I),
    re.compile(r"\bguaranteed\s+views?\b", re.I),
]

# Purely generic filler patterns that add no concierge value
_GENERIC_FILLER_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(a\s+)?great\s+(bar|place|option|choice|pick)\s*\.", re.I),
    re.compile(r"worth\s+a\s+visit\s*\.", re.I),
    re.compile(r"^[A-Z][^.]+\s+is\s+a\s+popular\s+(bar|restaurant|brewery)\s*\.", re.I),
]


@dataclass(frozen=True)
class NoteQualityGateResult:
    result: NoteQualityResult
    reason: str
    is_rating_primary: bool
    has_unsupported_claim: bool


def check_note_quality(
    note: str,
    user_modifier: str = "none",
    modifier_status: Optional[ModifierStatus] = None,
) -> NoteQualityGateResult:
    """Validate a concierge note against quality rules.

    Args:
        note: The generated concierge note text.
        user_modifier: modifier from the query ("river", "view", "none").
        modifier_status: the per-card computed modifier status; used to decide
                         whether scenic/river claims are allowed.

    Returns:
        NoteQualityGateResult with pass/fail verdict and diagnostics.
    """
    if not note or not note.strip():
        return NoteQualityGateResult(
            result=NoteQualityResult.FAIL_GENERIC_ONLY,
            reason="empty_note",
            is_rating_primary=False,
            has_unsupported_claim=False,
        )

    # Check for rating-primary patterns
    for pattern in _RATING_PRIMARY_PATTERNS + _RATING_LEAD_PATTERNS:
        if pattern.search(note):
            return NoteQualityGateResult(
                result=NoteQualityResult.FAIL_RATING_PRIMARY,
                reason=f"rating_primary_pattern:{pattern.pattern[:60]}",
                is_rating_primary=True,
                has_unsupported_claim=False,
            )

    # Check for unsupported scenic/river physical claims.
    # Physical claims (riverfront views, waterfront seating) require CONFIRMED_LISTING_CONTEXT
    # (provider-level evidence). CONFIRMED_ADDRESS_OR_NAME_CONTEXT only allows "listing/name
    # places it in Riverwalk context" — it does NOT allow physical view/seating claims.
    river_modifiers = {"river", "riverwalk", "waterfront", "riverfront"}
    if user_modifier in river_modifiers:
        # Block physical view/seating claims unless provider details explicitly support them
        provider_confirmed = modifier_status == ModifierStatus.CONFIRMED_LISTING_CONTEXT
        if not provider_confirmed:
            for pattern in _UNSUPPORTED_SCENIC_PATTERNS:
                if pattern.search(note):
                    return NoteQualityGateResult(
                        result=NoteQualityResult.FAIL_UNSUPPORTED_CLAIM,
                        reason=f"unsupported_river_claim:{pattern.pattern[:60]}",
                        is_rating_primary=False,
                        has_unsupported_claim=True,
                    )

    # Check for unsupported view claims when modifier is view and not confirmed
    if user_modifier in ("view", "views", "scenic"):
        if modifier_status not in (
            ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
            ModifierStatus.CONFIRMED_LISTING_CONTEXT,
        ):
            for pattern in _UNSUPPORTED_VIEW_PATTERNS:
                if pattern.search(note):
                    return NoteQualityGateResult(
                        result=NoteQualityResult.FAIL_UNSUPPORTED_CLAIM,
                        reason=f"unsupported_view_claim:{pattern.pattern[:60]}",
                        is_rating_primary=False,
                        has_unsupported_claim=True,
                    )

    return NoteQualityGateResult(
        result=NoteQualityResult.PASS,
        reason="ok",
        is_rating_primary=False,
        has_unsupported_claim=False,
    )


def build_retry_prompt_guidance(
    note: str,
    quality_result: NoteQualityGateResult,
    user_modifier: str,
    modifier_status: Optional[ModifierStatus],
    card_name: str,
) -> str:
    """Build repair instructions for a failed note to pass to the LLM on retry."""
    lines: list[str] = ["The previous note was rejected. Please rewrite it following these rules:"]

    if quality_result.is_rating_primary:
        lines.append(
            "- Do NOT lead with or rely primarily on star rating or review count. "
            "Use rating/reviews only as secondary context after mentioning a concrete "
            "differentiator (food/drink style, location context, unique amenity, etc.)."
        )

    if quality_result.has_unsupported_claim:
        if user_modifier in ("river", "riverwalk", "waterfront"):
            mod_status_is_name = modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT
            if mod_status_is_name:
                lines.append(
                    f"- The venue name '{card_name}' itself contains a Riverwalk/river reference. "
                    "You MAY say 'the listing/name places it in Riverwalk context' or similar. "
                    "Do NOT claim 'riverfront views', 'waterfront seating', or 'scenic river views' "
                    "unless provider details explicitly confirm those features."
                )
            else:
                lines.append(
                    "- Do NOT claim river views, waterfront seating, or scenic views. "
                    "These are unverified. Mention why the venue is being shown for this query "
                    "without fabricating river/water proximity features."
                )
        if user_modifier in ("view", "views"):
            lines.append(
                "- Do NOT claim panoramic views, skyline views, or confirmed views unless "
                "provider details confirm it. Instead, be honest that view status is unverified "
                "while still providing useful venue context."
            )

    lines.append(
        "- Write a concierge-grade note: start from the venue's concrete characteristics "
        "(drink/food style, location context, atmosphere, unique features) before any ratings."
    )
    return "\n".join(lines)
