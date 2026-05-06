"""Semantic Retrieval v1 — places concierge note generation with quality contracts.

Orchestrates per-card note generation with:
- Modifier evidence computation (river/view/none)
- Evidence adequacy scoring (STRONG/OK/THIN)
- LLM note generation with retry and fallback
- Note quality gate (reject rating-primary and unsupported claim notes)
- Full per-card observability logging (per_card_notes contract)

Contract guarantees:
- final_note_omitted_count == 0 when production is healthy
- excluded_unvalidated_count == 0 when production is healthy
- deterministic_visible_count == 0 (no deterministic fallback allowed)
- All omissions must survive two LLM attempts (primary + retry) plus fallback model
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from .modifier_evidence_v1 import (
    ModifierEvidence,
    ModifierStatus,
    compute_modifier_evidence,
    extract_user_modifier,
    venue_head_recognized,
)
from .note_quality_v1 import (
    NoteQualityGateResult,
    NoteQualityResult,
    build_retry_prompt_guidance,
    check_note_quality,
)

logger = logging.getLogger(__name__)


class EvidenceAdequacy(str, Enum):
    STRONG = "STRONG"  # ≥1 concrete differentiator beyond name/address/rating/reviews
    OK = "OK"          # concept fit + useful location/modifier caveat
    THIN = "THIN"      # mostly name/address/type/rating/reviews only


@dataclass
class PlaceCard:
    """A single place returned from the search provider."""
    index: int
    title: str             # verified provider name
    address: str
    rating: Optional[float]
    review_count: Optional[int]
    category: str          # primary category label
    source_query: str
    place_details: Optional[dict] = None   # optional Place Details payload

    def has_concrete_differentiator(self) -> bool:
        """True when the card carries at least one detail beyond name/address/rating/reviews.

        Concrete differentiators: editorial description, amenities, review snippets,
        speciality tag, confirmed features (outdoor seating, live music, etc.).
        """
        if not self.place_details:
            return False
        pd = self.place_details
        has_editorial = bool(pd.get("editorial_summary") or pd.get("description"))
        has_amenities = bool(pd.get("amenities"))
        has_snippet = bool(pd.get("review_snippet"))
        has_feature = bool(pd.get("confirmed_features"))
        return has_editorial or has_amenities or has_snippet or has_feature


@dataclass
class PerCardNote:
    """Full per-card note observability payload (per_card_notes contract)."""
    card_index: int
    card_title: str
    evidence_adequacy: EvidenceAdequacy
    user_modifier: str
    modifier_status: str
    display_why_validated: bool
    display_why_source: str
    visible_concierge_note: str
    quality_gate_result: str
    validated: bool
    retry_used: bool
    fallback_used: bool
    source: str             # "llm_primary" / "llm_retry" / "llm_fallback"
    rejection_reason: str = ""


@dataclass
class SemanticRetrievalResult:
    """Full result payload for a single semantic retrieval turn."""
    query: str
    per_card_notes: list[PerCardNote] = field(default_factory=list)
    reasoning_success: bool = False
    reasoning_failure_reason: Optional[str] = None
    llm_accepted_count: int = 0
    retry_recovered_count: int = 0
    fallback_model_used_count: int = 0
    deterministic_visible_count: int = 0   # must stay 0
    final_note_omitted_count: int = 0      # target: 0
    excluded_unvalidated_count: int = 0    # target: 0
    final_card_count: int = 0
    venue_head_recognized: bool = True


# ── Evidence adequacy scoring ────────────────────────────────────────────────

def score_evidence_adequacy(card: PlaceCard) -> EvidenceAdequacy:
    """Score evidence adequacy for a single card.

    Rules:
    - STRONG: card has ≥1 concrete differentiator (editorial/amenity/snippet/feature)
    - OK: card has no extras but category/concept fit is clear and location context is useful
    - THIN: card has only name + address + rating + reviews (no additional evidence)

    Rating/review count alone can NEVER make evidence STRONG.
    """
    if card.has_concrete_differentiator():
        return EvidenceAdequacy.STRONG

    # Even without Place Details, if category is specific enough, it's OK
    if card.category and len(card.category.strip()) > 2:
        return EvidenceAdequacy.OK

    return EvidenceAdequacy.THIN


# ── Retry prompt builder ─────────────────────────────────────────────────────

_BASE_NOTE_PROMPT = (
    "Write a concierge-grade venue note for {title} (category: {category}). "
    "Context: user searched for '{query}'. "
    "Address: {address}. "
    "{modifier_context}"
    "Rules: "
    "1. Start from the venue's concrete characteristics (drink/food style, atmosphere, unique features, location context). "
    "2. If you mention rating ({rating}) or reviews ({review_count}), do so ONLY as secondary context after a real differentiator. "
    "3. Do not claim views, waterfront seating, or scenic features unless the provider details explicitly confirm them. "
    "4. If the venue name or address places it in a Riverwalk/river context, you may say that "
    "   as listing/name context, but do not fabricate actual seating or view details. "
    "5. Be honest about unknown modifiers — explain why the card is being shown. "
    "Write 1-3 sentences maximum."
)


def _build_note_prompt(card: PlaceCard, user_modifier: str, mod_ev: ModifierEvidence) -> str:
    if user_modifier == "river":
        if mod_ev.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT:
            modifier_context = (
                f"The verified listing name/address places this venue in Riverwalk/river context "
                f"(source: {mod_ev.modifier_evidence_source}). "
                "Mention this as listing context only; do NOT claim waterfront seating or river views. "
            )
        elif mod_ev.modifier_status == ModifierStatus.CONFIRMED_LISTING_CONTEXT:
            modifier_context = "Provider details confirm river/waterfront context. "
        else:
            modifier_context = (
                "River proximity is UNVERIFIED for this card. "
                "Explain why it appears for this query (e.g. search area, category relevance) "
                "without claiming river access or views. "
            )
    elif user_modifier == "view":
        if mod_ev.modifier_status in (
            ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
            ModifierStatus.CONFIRMED_LISTING_CONTEXT,
        ):
            modifier_context = "Provider/name confirms view or rooftop context. "
        else:
            modifier_context = (
                "View is UNVERIFIED. "
                "Say so honestly and explain why this card is still relevant. "
            )
    else:
        modifier_context = ""

    return _BASE_NOTE_PROMPT.format(
        title=card.title,
        category=card.category,
        query=card.source_query,
        address=card.address,
        rating=card.rating or "not available",
        review_count=card.review_count or "not available",
        modifier_context=modifier_context,
    )


# ── Core processor ───────────────────────────────────────────────────────────

NoteGeneratorFn = Callable[[str], str]
"""Type alias: a function that takes a prompt string and returns a generated note string."""


def process_semantic_retrieval(
    query: str,
    cards: list[PlaceCard],
    note_generator: NoteGeneratorFn,
    fallback_note_generator: Optional[NoteGeneratorFn] = None,
    card_categories: Optional[list[str]] = None,
) -> SemanticRetrievalResult:
    """Process a semantic retrieval query and generate quality-gated concierge notes.

    Args:
        query: the user search query
        cards: ordered list of PlaceCard results from the search provider
        note_generator: primary LLM note generation function (takes a prompt, returns note text)
        fallback_note_generator: fallback model generator (used if primary + retry both fail)
        card_categories: all categories across all cards (for venue_head_recognized check)

    Returns:
        SemanticRetrievalResult with per_card_notes and full observability counters.
    """
    user_modifier = extract_user_modifier(query)
    all_categories = card_categories or [c.category for c in cards]

    vh_recognized = venue_head_recognized(query, all_categories)
    per_card_notes: list[PerCardNote] = []
    omitted_count = 0
    excluded_count = 0
    retry_count = 0
    fallback_count = 0
    accepted_count = 0

    for card in cards:
        mod_ev = compute_modifier_evidence(
            user_modifier=user_modifier,
            card_name=card.title,
            card_address=card.address,
            source_query=query,
            place_details=card.place_details,
        )
        adequacy = score_evidence_adequacy(card)
        prompt = _build_note_prompt(card, user_modifier, mod_ev)

        # Primary attempt
        note = note_generator(prompt)
        gate = check_note_quality(note, user_modifier, mod_ev.modifier_status)
        retry_used = False
        fallback_used = False
        source = "llm_primary"

        # Retry on primary failure
        if gate.result != NoteQualityResult.PASS:
            retry_guidance = build_retry_prompt_guidance(
                note=note,
                quality_result=gate,
                user_modifier=user_modifier,
                modifier_status=mod_ev.modifier_status,
                card_name=card.title,
            )
            retry_prompt = f"{retry_guidance}\n\nOriginal prompt:\n{prompt}"
            note = note_generator(retry_prompt)
            gate = check_note_quality(note, user_modifier, mod_ev.modifier_status)
            retry_used = True
            retry_count += 1
            source = "llm_retry"

        # Fallback model on retry failure
        if gate.result != NoteQualityResult.PASS and fallback_note_generator is not None:
            fb_prompt = (
                f"Write a short, honest 1-2 sentence concierge note for {card.title}. "
                f"Category: {card.category}. Address: {card.address}. "
                f"User searched: '{query}'. "
                "Do not claim unverified views or waterfront features. "
                "Do not lead with star rating or review count."
            )
            note = fallback_note_generator(fb_prompt)
            gate = check_note_quality(note, user_modifier, mod_ev.modifier_status)
            fallback_used = True
            fallback_count += 1
            source = "llm_fallback"

        validated = gate.result == NoteQualityResult.PASS

        if not validated:
            omitted_count += 1
            excluded_count += 1
        else:
            accepted_count += 1

        per_card_notes.append(PerCardNote(
            card_index=card.index,
            card_title=card.title,
            evidence_adequacy=adequacy,
            user_modifier=user_modifier,
            modifier_status=mod_ev.modifier_status.value,
            display_why_validated=validated,
            display_why_source=mod_ev.modifier_evidence_source,
            visible_concierge_note=note if validated else "",
            quality_gate_result=gate.result.value,
            validated=validated,
            retry_used=retry_used,
            fallback_used=fallback_used,
            source=source if validated else f"omitted_{gate.result.value}",
            rejection_reason=gate.reason if not validated else "",
        ))

        logger.info(
            "semantic_retrieval_v1.per_card_notes",
            extra={
                "card_index": card.index,
                "card_title": card.title,
                "validated": validated,
                "source": source,
                "note": note[:120] if validated else "",
                "evidence_adequacy": adequacy.value,
                "modifier_status": mod_ev.modifier_status.value,
                "quality_gate_result": gate.result.value,
                "retry_used": retry_used,
                "fallback_used": fallback_used,
            },
        )

    missing_count = len(cards) - accepted_count
    reasoning_success = omitted_count == 0
    failure_reason: Optional[str] = None
    if not reasoning_success:
        failed_indices = [n.card_index for n in per_card_notes if not n.validated]
        failure_reason = f"incomplete_reasoning:{len(failed_indices)}_of_{len(cards)}_missing"

    result = SemanticRetrievalResult(
        query=query,
        per_card_notes=per_card_notes,
        reasoning_success=reasoning_success,
        reasoning_failure_reason=failure_reason,
        llm_accepted_count=accepted_count,
        retry_recovered_count=retry_count - excluded_count if retry_count > excluded_count else 0,
        fallback_model_used_count=fallback_count - excluded_count if fallback_count > excluded_count else 0,
        deterministic_visible_count=0,  # never uses deterministic fallback
        final_note_omitted_count=omitted_count,
        excluded_unvalidated_count=excluded_count,
        final_card_count=accepted_count,
        venue_head_recognized=vh_recognized,
    )

    logger.info(
        "semantic_retrieval_v1.turn_summary",
        extra={
            "query": query,
            "reasoning_success": result.reasoning_success,
            "reasoning_failure_reason": result.reasoning_failure_reason,
            "llm_accepted_count": result.llm_accepted_count,
            "retry_recovered_count": result.retry_recovered_count,
            "fallback_model_used_count": result.fallback_model_used_count,
            "deterministic_visible_count": result.deterministic_visible_count,
            "final_note_omitted_count": result.final_note_omitted_count,
            "excluded_unvalidated_count": result.excluded_unvalidated_count,
            "final_card_count": result.final_card_count,
            "venue_head_recognized": result.venue_head_recognized,
        },
    )

    return result
