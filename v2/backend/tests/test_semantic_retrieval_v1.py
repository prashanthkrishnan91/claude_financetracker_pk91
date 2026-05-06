"""Unit tests for semantic_retrieval_v1 module.

Covers:
1. process_semantic_retrieval basic happy path
2. Retry is triggered when primary note fails quality gate
3. Fallback model is used when primary + retry fail
4. Northman/Riverwalk card: name-context confirmation, not rejected as unsupported claim
5. Modifier evidence extraction from query
6. venue_head_recognized for izakaya queries
7. Observability counters are accurate
8. deterministic_visible_count is always 0
9. No LLM, Supabase, or provider calls in tests
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.services.places.modifier_evidence_v1 import (
    ModifierStatus,
    compute_modifier_evidence,
    extract_user_modifier,
    venue_head_recognized,
)
from app.services.places.semantic_retrieval_v1 import (
    EvidenceAdequacy,
    PlaceCard,
    PerCardNote,
    SemanticRetrievalResult,
    process_semantic_retrieval,
    score_evidence_adequacy,
)
from app.services.places.note_quality_v1 import (
    NoteQualityResult,
    check_note_quality,
    build_retry_prompt_guidance,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_card(
    index: int,
    title: str,
    category: str = "Brewery",
    address: str = "123 N Michigan Ave, Chicago IL 60601",
    rating: float = 4.5,
    review_count: int = 400,
    place_details: dict | None = None,
) -> PlaceCard:
    return PlaceCard(
        index=index,
        title=title,
        address=address,
        rating=rating,
        review_count=review_count,
        category=category,
        source_query="breweries near the river",
        place_details=place_details,
    )


_NORTHMAN_CARD = _make_card(
    index=4,
    title="The Northman Beer & Cider Garden on the Riverwalk",
    category="Beer Garden",
    address="1635 N Wells St, Chicago IL 60614",
    rating=4.6,
    review_count=812,
)

_NORTHMAN_VALID_NOTE = (
    "Because the verified listing itself places Northman on the Riverwalk, "
    "it is the strongest river-context beer stop here; "
    "verify actual seating/view details if that matters."
)

_NORTHMAN_INVALID_NOTE = (
    "Great riverfront views and waterfront seating make this the top pick for river lovers."
)

_GENERIC_VALID_NOTE = (
    "A West Loop craft brewery known for its rotating seasonal tap list and spacious industrial taproom."
)

_RATING_PRIMARY_NOTE = (
    "Highest-rated brewery in this set with 4.8★ across 1,200 reviews — the top choice."
)


def _always_valid_generator(prompt: str) -> str:
    """Mock generator that always returns a valid note."""
    return _GENERIC_VALID_NOTE


def _northman_aware_generator(prompt: str) -> str:
    """Mock generator that returns invalid note for Northman on first call, valid on retry."""
    if "repair" in prompt.lower() or "rewrite" in prompt.lower() or "rejected" in prompt.lower():
        # This is a retry prompt
        return _NORTHMAN_VALID_NOTE
    if "Northman" in prompt:
        return _NORTHMAN_INVALID_NOTE
    return _GENERIC_VALID_NOTE


# ── Section A: Modifier evidence ─────────────────────────────────────────────

class TestModifierEvidence:
    def test_extract_river_modifier_near_the_river(self):
        assert extract_user_modifier("breweries near the river") == "river"

    def test_extract_river_modifier_riverwalk(self):
        assert extract_user_modifier("bars on the riverwalk") == "river"

    def test_extract_view_modifier(self):
        assert extract_user_modifier("taprooms with a view") == "view"

    def test_no_modifier_izakaya(self):
        assert extract_user_modifier("Izakayas") == "none"

    def test_northman_name_contains_riverwalk_confirmed(self):
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="The Northman Beer & Cider Garden on the Riverwalk",
            card_address="1635 N Wells St, Chicago IL",
            source_query="breweries near the river",
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT
        assert "riverwalk" in ev.modifier_evidence_source.lower()

    def test_generic_brewery_unknown_river(self):
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="Goose Island Wrigleyville",
            card_address="3535 N Clark St, Chicago IL",
            source_query="breweries near the river",
        )
        assert ev.modifier_status == ModifierStatus.UNKNOWN

    def test_address_with_riverwalk_confirmed(self):
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="Generic Bar",
            card_address="400 N Riverwalk Dr, Chicago IL",
            source_query="breweries near the river",
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT

    def test_rooftop_in_name_confirms_view(self):
        ev = compute_modifier_evidence(
            user_modifier="view",
            card_name="Raised Rooftop Bar",
            card_address="1 S Wacker Dr, Chicago IL",
            source_query="taprooms with a view",
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT


# ── Section B: Note quality gate ─────────────────────────────────────────────

class TestNoteQualityGate:
    def test_valid_note_passes(self):
        result = check_note_quality(_GENERIC_VALID_NOTE, user_modifier="none")
        assert result.result == NoteQualityResult.PASS

    def test_northman_valid_note_passes_with_confirmed_status(self):
        result = check_note_quality(
            _NORTHMAN_VALID_NOTE,
            user_modifier="river",
            modifier_status=ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
        )
        assert result.result == NoteQualityResult.PASS

    def test_northman_invalid_note_fails_without_confirmed_status(self):
        result = check_note_quality(
            _NORTHMAN_INVALID_NOTE,
            user_modifier="river",
            modifier_status=ModifierStatus.UNKNOWN,
        )
        assert result.result == NoteQualityResult.FAIL_UNSUPPORTED_CLAIM

    def test_northman_invalid_note_also_fails_with_confirmed_status(self):
        # "riverfront views" is still blocked — confirmed status only allows listing context,
        # not actual claims of views/seating
        result = check_note_quality(
            "Great riverfront views",
            user_modifier="river",
            modifier_status=ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
        )
        assert result.result == NoteQualityResult.FAIL_UNSUPPORTED_CLAIM

    def test_rating_primary_note_fails(self):
        result = check_note_quality(_RATING_PRIMARY_NOTE, user_modifier="none")
        assert result.result == NoteQualityResult.FAIL_RATING_PRIMARY

    def test_second_most_reviewed_fails(self):
        note = "Second-most-reviewed taproom with a solid review base at 4.7★."
        result = check_note_quality(note)
        assert result.result == NoteQualityResult.FAIL_RATING_PRIMARY

    def test_review_base_fails(self):
        note = "Solid option with the second-largest review base in this set."
        result = check_note_quality(note)
        assert result.result == NoteQualityResult.FAIL_RATING_PRIMARY

    def test_star_rating_in_second_sentence_passes(self):
        note = (
            "A West Loop craft brewery known for its Czech-style pilsner and open fermentation tanks. "
            "Rated 4.6★ by regular customers."
        )
        result = check_note_quality(note, user_modifier="none")
        assert result.result == NoteQualityResult.PASS

    def test_empty_note_fails(self):
        result = check_note_quality("")
        assert result.result == NoteQualityResult.FAIL_GENERIC_ONLY

    def test_waterfront_seating_fails_for_unknown_modifier(self):
        note = "Good taproom with great waterfront seating and views."
        result = check_note_quality(note, user_modifier="river", modifier_status=ModifierStatus.UNKNOWN)
        assert result.result == NoteQualityResult.FAIL_UNSUPPORTED_CLAIM


# ── Section C: Retry prompt guidance ─────────────────────────────────────────

class TestRetryPromptGuidance:
    def test_guidance_for_rating_primary_mentions_differentiator(self):
        from app.services.places.note_quality_v1 import NoteQualityGateResult
        gate = NoteQualityGateResult(
            result=NoteQualityResult.FAIL_RATING_PRIMARY,
            reason="rating_primary",
            is_rating_primary=True,
            has_unsupported_claim=False,
        )
        guidance = build_retry_prompt_guidance(
            note=_RATING_PRIMARY_NOTE,
            quality_result=gate,
            user_modifier="none",
            modifier_status=None,
            card_name="Test Brewery",
        )
        assert "differentiator" in guidance.lower() or "concrete" in guidance.lower()

    def test_guidance_for_riverwalk_card_allows_name_context(self):
        from app.services.places.note_quality_v1 import NoteQualityGateResult
        gate = NoteQualityGateResult(
            result=NoteQualityResult.FAIL_UNSUPPORTED_CLAIM,
            reason="unsupported_river_claim",
            is_rating_primary=False,
            has_unsupported_claim=True,
        )
        guidance = build_retry_prompt_guidance(
            note=_NORTHMAN_INVALID_NOTE,
            quality_result=gate,
            user_modifier="river",
            modifier_status=ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
            card_name="The Northman Beer & Cider Garden on the Riverwalk",
        )
        assert "listing" in guidance.lower() or "riverwalk" in guidance.lower()
        assert "waterfront seating" in guidance.lower() or "river views" in guidance.lower()


# ── Section D: process_semantic_retrieval ───────────────────────────────────

class TestProcessSemanticRetrieval:
    def _make_8_cards(self, query: str = "breweries near the river") -> list[PlaceCard]:
        titles = [
            "Goose Island Wrigleyville",
            "Begyle Brewing",
            "Revolution Brewing Taproom",
            "The Northman Beer & Cider Garden on the Riverwalk",
            "Half Acre Beer Company",
            "Hop Butcher For The World",
            "Off Color Brewing",
            "Pilot Project Brewing",
        ]
        cards = []
        for i, title in enumerate(titles):
            cards.append(PlaceCard(
                index=i + 1,
                title=title,
                address=f"{100 + i} N Example St, Chicago IL",
                rating=4.3 + i * 0.05,
                review_count=300 + i * 50,
                category="Brewery",
                source_query=query,
            ))
        return cards

    def test_all_accepted_with_valid_generator(self):
        cards = self._make_8_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_always_valid_generator,
        )
        assert result.llm_accepted_count == 8
        assert result.final_note_omitted_count == 0
        assert result.excluded_unvalidated_count == 0
        assert result.deterministic_visible_count == 0
        assert result.reasoning_success is True
        assert result.final_card_count == 8

    def test_northman_passes_with_retry(self):
        cards = self._make_8_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_northman_aware_generator,
        )
        northman_notes = [n for n in result.per_card_notes if "Northman" in n.card_title]
        assert len(northman_notes) == 1
        northman = northman_notes[0]
        assert northman.validated is True, f"Northman must be validated; got: {northman.rejection_reason}"
        assert northman.retry_used is True
        assert northman.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT.value

    def test_northman_note_does_not_claim_views(self):
        cards = self._make_8_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_northman_aware_generator,
        )
        northman_notes = [n for n in result.per_card_notes if "Northman" in n.card_title]
        note = northman_notes[0].visible_concierge_note.lower()
        assert "waterfront seating" not in note
        assert "riverfront view" not in note
        assert "river view" not in note

    def test_fallback_used_when_retry_also_fails(self):
        def always_fail_generator(prompt: str) -> str:
            return "Highest-rated brewery with 4.8★ and the largest review base."

        def valid_fallback(prompt: str) -> str:
            return _GENERIC_VALID_NOTE

        cards = self._make_8_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=always_fail_generator,
            fallback_note_generator=valid_fallback,
        )
        assert result.llm_accepted_count == 8
        assert result.final_note_omitted_count == 0
        assert result.fallback_model_used_count > 0

    def test_deterministic_visible_count_always_zero(self):
        cards = self._make_8_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_always_valid_generator,
        )
        assert result.deterministic_visible_count == 0

    def test_per_card_notes_count_matches_input(self):
        cards = self._make_8_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_always_valid_generator,
        )
        assert len(result.per_card_notes) == len(cards)

    def test_per_card_notes_have_required_fields(self):
        cards = self._make_8_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_always_valid_generator,
        )
        for note in result.per_card_notes:
            assert note.card_title
            assert note.evidence_adequacy in (
                EvidenceAdequacy.STRONG, EvidenceAdequacy.OK, EvidenceAdequacy.THIN
            )
            assert note.modifier_status
            assert note.quality_gate_result


# ── Section E: venue_head_recognized ─────────────────────────────────────────

class TestVenueHeadRecognized:
    def test_izakaya_query_recognized_when_category_present(self):
        assert venue_head_recognized("Izakayas", ["izakaya", "japanese restaurant"]) is True

    def test_izakaya_query_not_recognized_when_no_category(self):
        assert venue_head_recognized("Izakayas", ["bar", "american restaurant"]) is False

    def test_brewery_query_not_category_gated(self):
        assert venue_head_recognized("breweries near the river", ["bar", "pub"]) is True

    def test_taproom_query_not_category_gated(self):
        assert venue_head_recognized("taprooms with a view", ["brewery", "bar"]) is True

    def test_izakaya_plural_variations(self):
        assert venue_head_recognized("izakayas", ["izakaya"]) is True
        assert venue_head_recognized("Izakayas", ["Izakaya Restaurant"]) is True
