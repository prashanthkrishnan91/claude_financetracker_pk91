"""Modifier Evidence Contract v1.

Computes per-card modifier evidence for user-requested spatial/contextual constraints.

Modifier status taxonomy:
- confirmed_listing_context   : provider-level detail (editorial, amenity, description) supports claim
- confirmed_address_or_name_context : verified name or address contains the modifier phrase
- unknown                     : no evidence found; card may still be shown with honest caveat
- contradicted                : evidence actively contradicts the modifier

Safe/unsafe claim rules:
- Name or address contains a river-adjacent phrase → confirmed_address_or_name_context
  → note MAY say "listing/name places it in Riverwalk context"
  → note MUST NOT claim "riverfront views", "waterfront seating", "scenic views"
- Provider Place Details amenity/description → confirmed_listing_context
- No evidence → unknown; note must not claim the modifier
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ModifierStatus(str, Enum):
    CONFIRMED_LISTING_CONTEXT = "confirmed_listing_context"
    CONFIRMED_ADDRESS_OR_NAME_CONTEXT = "confirmed_address_or_name_context"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"


# River-adjacent phrases that are safe to surface from name/address
_RIVER_NAME_PHRASES: frozenset[str] = frozenset([
    "riverwalk",
    "river walk",
    "riverfront",
    "river front",
    "waterfront",
    "chicago river",
    "riverside",
    "riverview",
    "river north",  # neighborhood, not scenic evidence
    "on the river",
    "river district",
])

# View/scenic phrases confirmed only via provider Place Details
_VIEW_PROVIDER_PHRASES: frozenset[str] = frozenset([
    "rooftop",
    "roof top",
    "roof deck",
    "sky bar",
    "skybar",
    "sky lounge",
    "penthouse",
    "observation deck",
    "with a view",
    "view bar",
    "vista",
    "overlook",
    "panoramic",
    "terrace bar",
])

# Phrases that confirm an izakaya / Japanese gastropub category
_IZAKAYA_CATEGORY_PHRASES: frozenset[str] = frozenset([
    "izakaya",
    "japanese gastropub",
    "japanese pub",
    "sake bar",
    "yakitori",
    "robatayaki",
    "japanese small plates",
])


def _normalise(text: str) -> str:
    return text.lower().strip()


def _text_contains_phrase(text: str, phrases: frozenset[str]) -> Optional[str]:
    """Return the first matching phrase found in text using word-boundary matching, or None.

    Uses regex word boundaries so "on the river" does NOT match "on the riverwalk".
    Sorts by length descending to prefer more-specific matches first.
    """
    t = _normalise(text)
    for phrase in sorted(phrases, key=len, reverse=True):  # longest first
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, t):
            return phrase
    return None


@dataclass(frozen=True)
class ModifierEvidence:
    user_modifier: str
    modifier_status: ModifierStatus
    modifier_evidence_source: str  # which field/phrase drove the status


def compute_modifier_evidence(
    user_modifier: str,
    card_name: str,
    card_address: str,
    source_query: str,
    place_details: Optional[dict] = None,
) -> ModifierEvidence:
    """Compute per-card modifier evidence for a single user modifier.

    Args:
        user_modifier: lowercased modifier word extracted from the query, e.g. "river", "view"
        card_name: verified name from provider (e.g. Google Places verified name)
        card_address: formatted address from provider
        source_query: original search query (used for context only)
        place_details: optional Place Details payload from provider (may contain
                       editorial description, amenities, etc.)

    Returns:
        ModifierEvidence with status and provenance.
    """
    mod = user_modifier.strip().lower()

    if mod in ("river", "riverwalk", "waterfront", "riverfront"):
        return _compute_river_evidence(card_name, card_address, place_details)

    if mod in ("view", "views", "scenic", "skyline"):
        return _compute_view_evidence(card_name, card_address, place_details)

    # No recognised modifier → unknown
    return ModifierEvidence(
        user_modifier=user_modifier,
        modifier_status=ModifierStatus.UNKNOWN,
        modifier_evidence_source="no_recognised_modifier",
    )


def _compute_river_evidence(
    card_name: str,
    card_address: str,
    place_details: Optional[dict],
) -> ModifierEvidence:
    """River modifier: safe to confirm from name/address; no scenic claim without details."""
    # Check verified name first (highest trust)
    match = _text_contains_phrase(card_name, _RIVER_NAME_PHRASES)
    if match:
        return ModifierEvidence(
            user_modifier="river",
            modifier_status=ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
            modifier_evidence_source=f"verified_name_contains:{match}",
        )

    # Check address
    match = _text_contains_phrase(card_address, _RIVER_NAME_PHRASES)
    if match:
        return ModifierEvidence(
            user_modifier="river",
            modifier_status=ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
            modifier_evidence_source=f"address_contains:{match}",
        )

    # Check provider details description/editorial
    if place_details:
        desc = place_details.get("description", "") or place_details.get("editorial_summary", "")
        match = _text_contains_phrase(desc, _RIVER_NAME_PHRASES)
        if match:
            return ModifierEvidence(
                user_modifier="river",
                modifier_status=ModifierStatus.CONFIRMED_LISTING_CONTEXT,
                modifier_evidence_source=f"place_details_description_contains:{match}",
            )

    return ModifierEvidence(
        user_modifier="river",
        modifier_status=ModifierStatus.UNKNOWN,
        modifier_evidence_source="no_river_context_found",
    )


def _compute_view_evidence(
    card_name: str,
    card_address: str,
    place_details: Optional[dict],
) -> ModifierEvidence:
    """View modifier: confirmed only via provider details or name containing view phrase."""
    # Check verified name (e.g. "SkyBar at The Rooftop")
    match = _text_contains_phrase(card_name, _VIEW_PROVIDER_PHRASES)
    if match:
        return ModifierEvidence(
            user_modifier="view",
            modifier_status=ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
            modifier_evidence_source=f"verified_name_contains:{match}",
        )

    # Check provider details
    if place_details:
        desc = place_details.get("description", "") or place_details.get("editorial_summary", "")
        types = place_details.get("types", [])
        amenities = place_details.get("amenities", [])
        # Normalise underscores → spaces so "rooftop_patio" becomes "rooftop patio"
        amenities_text = " ".join(a.replace("_", " ") for a in amenities)
        types_text = " ".join(t.replace("_", " ") for t in types)
        all_view_text = " ".join(filter(None, [desc, types_text, amenities_text]))
        match = _text_contains_phrase(all_view_text, _VIEW_PROVIDER_PHRASES)
        if match:
            return ModifierEvidence(
                user_modifier="view",
                modifier_status=ModifierStatus.CONFIRMED_LISTING_CONTEXT,
                modifier_evidence_source=f"place_details_contains:{match}",
            )

    return ModifierEvidence(
        user_modifier="river",
        modifier_status=ModifierStatus.UNKNOWN,
        modifier_evidence_source="no_view_evidence_found",
    )


def extract_user_modifier(query: str) -> str:
    """Extract the primary spatial/contextual modifier from a search query.

    Returns a normalised modifier string, or "none" if no recognised modifier.
    """
    q = _normalise(query)

    river_patterns = [
        r"\bnear\s+the\s+river\b",
        r"\bby\s+the\s+river\b",
        r"\bon\s+the\s+river\b",
        r"\briverwalk\b",
        r"\briverfront\b",
        r"\bwaterfront\b",
    ]
    for pattern in river_patterns:
        if re.search(pattern, q):
            return "river"

    view_patterns = [
        r"\bwith\s+a\s+view\b",
        r"\brooftop\b",
        r"\bsky\s*bar\b",
        r"\bskyline\s+view\b",
        r"\bpanoramic\b",
    ]
    for pattern in view_patterns:
        if re.search(pattern, q):
            return "view"

    return "none"


def venue_head_recognized(query: str, categories: list[str]) -> bool:
    """Check if the query's primary venue concept is recognised in the returned card categories.

    Used for category-head queries like 'izakayas' where venue_head_recognized
    should be True when at least one returned card is classified as an izakaya/related.
    """
    q = _normalise(query)
    izakaya_query_words = {"izakaya", "izakayas", "japanese gastropub", "yakitori"}
    if any(word in q for word in izakaya_query_words):
        for cat in categories:
            if _text_contains_phrase(cat, _IZAKAYA_CATEGORY_PHRASES):
                return True
        return False

    return True  # for other queries, head recognition is not category-gated
