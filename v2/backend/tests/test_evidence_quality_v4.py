"""Evidence Quality v4 tests — modifier contract + quality gate integration.

Covers:
A. Modifier evidence contract: river, view, none — all status values
B. Quality gate integration with modifier status
C. Evidence adequacy STRONG requires concrete differentiator; rating/reviews never STRONG alone
D. Retry prompt guidance is modifier-aware and allows safe listing-context for Riverwalk
E. Per-card pipeline: modifier evidence → quality gate → validated/omitted decision
F. No LLM, Supabase, or external calls
"""

from __future__ import annotations

import pytest

from app.services.places.modifier_evidence_v1 import (
    ModifierStatus,
    compute_modifier_evidence,
    extract_user_modifier,
    venue_head_recognized,
)
from app.services.places.note_quality_v1 import (
    NoteQualityResult,
    build_retry_prompt_guidance,
    check_note_quality,
)
from app.services.places.semantic_retrieval_v1 import (
    EvidenceAdequacy,
    PlaceCard,
    score_evidence_adequacy,
)


# ── Section A: Modifier evidence contract ────────────────────────────────────

class TestModifierContract:
    """A. Modifier evidence contract: river, view, none."""

    # A1: River modifier — confirmed via name
    def test_river_confirmed_name_riverwalk(self):
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="The Northman Beer & Cider Garden on the Riverwalk",
            card_address="1635 N Wells St, Chicago IL",
            source_query="breweries near the river",
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT
        assert ev.user_modifier == "river"

    def test_river_confirmed_name_riverfront(self):
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="Riverfront Brewing Co",
            card_address="100 N Lake Shore Dr, Chicago IL",
            source_query="breweries near the river",
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT

    def test_river_confirmed_address_riverwalk(self):
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="Generic Pub",
            card_address="330 N Riverwalk Blvd, Chicago IL",
            source_query="breweries near the river",
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT

    def test_river_confirmed_chicago_river_in_name(self):
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="Chicago River Brewing",
            card_address="500 W Kinzie St",
            source_query="breweries near the river",
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT

    def test_river_unknown_for_unrelated_name(self):
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="Revolution Brewing Taproom",
            card_address="2323 N Milwaukee Ave, Chicago IL",
            source_query="breweries near the river",
        )
        assert ev.modifier_status == ModifierStatus.UNKNOWN

    def test_river_confirmed_via_place_details_description(self):
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="Some Generic Brewery",
            card_address="100 W Fulton St",
            source_query="breweries near the river",
            place_details={"description": "Located right on the riverwalk with outdoor seating."},
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_LISTING_CONTEXT

    # A2: View modifier — confirmed via name or details
    def test_view_confirmed_rooftop_in_name(self):
        ev = compute_modifier_evidence(
            user_modifier="view",
            card_name="Spiteful Brewing Rooftop",
            card_address="1815 W Berwyn Ave, Chicago IL",
            source_query="taprooms with a view",
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT

    def test_view_confirmed_via_place_details_amenities(self):
        ev = compute_modifier_evidence(
            user_modifier="view",
            card_name="Empirical Brewery",
            card_address="1801 W Foster Ave",
            source_query="taprooms with a view",
            place_details={"amenities": ["rooftop_patio", "outdoor_seating"]},
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_LISTING_CONTEXT

    def test_view_unknown_for_plain_taproom(self):
        ev = compute_modifier_evidence(
            user_modifier="view",
            card_name="Forbidden Root",
            card_address="1746 W Chicago Ave",
            source_query="taprooms with a view",
        )
        assert ev.modifier_status == ModifierStatus.UNKNOWN

    # A3: No modifier
    def test_none_modifier_for_izakaya(self):
        modifier = extract_user_modifier("Izakayas")
        assert modifier == "none"

    def test_none_modifier_for_generic_restaurant(self):
        modifier = extract_user_modifier("ramen restaurants")
        assert modifier == "none"


# ── Section B: Quality gate with modifier status ──────────────────────────────

class TestQualityGateWithModifierStatus:
    """B. Quality gate integration with modifier status."""

    def test_riverwalk_context_note_passes_when_confirmed(self):
        note = (
            "Because the verified listing itself places Northman on the Riverwalk, "
            "it is the strongest river-context beer stop here."
        )
        result = check_note_quality(
            note, user_modifier="river",
            modifier_status=ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
        )
        assert result.result == NoteQualityResult.PASS

    def test_waterfront_seating_blocked_even_when_name_confirmed(self):
        # Name-context confirmation allows "listing places it on the Riverwalk"
        # but does NOT allow fabricated seating/view claims
        note = "Great waterfront seating and river views make this the top pick."
        result = check_note_quality(
            note, user_modifier="river",
            modifier_status=ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
        )
        assert result.result == NoteQualityResult.FAIL_UNSUPPORTED_CLAIM

    def test_river_view_claim_blocked_for_unknown_status(self):
        note = "Beautiful river views from the patio make this a great summer spot."
        result = check_note_quality(
            note, user_modifier="river",
            modifier_status=ModifierStatus.UNKNOWN,
        )
        assert result.result == NoteQualityResult.FAIL_UNSUPPORTED_CLAIM

    def test_honest_unknown_note_passes_for_view(self):
        note = (
            "Forbidden Root is an interesting botanical brewery; "
            "view status is unverified — check for outdoor seating before visiting."
        )
        result = check_note_quality(
            note, user_modifier="view",
            modifier_status=ModifierStatus.UNKNOWN,
        )
        assert result.result == NoteQualityResult.PASS

    def test_panoramic_view_blocked_for_unknown_view(self):
        note = "Panoramic views of the skyline from the bar counter."
        result = check_note_quality(
            note, user_modifier="view",
            modifier_status=ModifierStatus.UNKNOWN,
        )
        assert result.result == NoteQualityResult.FAIL_UNSUPPORTED_CLAIM

    def test_no_modifier_rating_primary_rejected(self):
        note = "Highest-rated option in this set with 4.8★ across 1,028 reviews."
        result = check_note_quality(note, user_modifier="none")
        assert result.result == NoteQualityResult.FAIL_RATING_PRIMARY

    def test_rating_as_secondary_context_passes(self):
        note = (
            "Izakaya Mita is purpose-built around the format — sharing plates, "
            "grilled skewers, and Japanese cocktails. Rated 4.7★ by frequent visitors."
        )
        result = check_note_quality(note, user_modifier="none")
        assert result.result == NoteQualityResult.PASS

    def test_second_most_reviewed_rejected(self):
        note = "Second-most-reviewed at 1,144 reviews with a 4.6★ average."
        result = check_note_quality(note)
        assert result.result == NoteQualityResult.FAIL_RATING_PRIMARY

    def test_smaller_review_base_rejected(self):
        note = "Smaller review base (245 reviews) but consistent 4.5★ rating."
        result = check_note_quality(note)
        assert result.result == NoteQualityResult.FAIL_RATING_PRIMARY


# ── Section C: Evidence adequacy / rating can't make STRONG ──────────────────

class TestEvidenceAdequacyContract:
    """C. STRONG requires concrete differentiator; rating/reviews never STRONG alone."""

    def _card(self, **kwargs) -> PlaceCard:
        defaults = dict(
            index=1, title="Test Venue", address="100 N Test St",
            rating=4.5, review_count=500, category="Brewery",
            source_query="test", place_details=None,
        )
        defaults.update(kwargs)
        return PlaceCard(**defaults)

    def test_rating_reviews_alone_never_strong_parametrized(self):
        for r in [4.5, 4.8, 5.0]:
            for rc in [100, 1000, 50000]:
                card = self._card(place_details={"rating": r, "user_ratings_total": rc}, category="")
                assert score_evidence_adequacy(card) != EvidenceAdequacy.STRONG

    def test_editorial_makes_strong(self):
        card = self._card(place_details={"editorial_summary": "Award-winning West Loop brewery."})
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG

    def test_amenities_makes_strong(self):
        card = self._card(place_details={"amenities": ["outdoor_seating", "live_music"]})
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG

    def test_review_snippet_makes_strong(self):
        card = self._card(place_details={"review_snippet": "The hazy IPA here is excellent."})
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG

    def test_confirmed_features_makes_strong(self):
        card = self._card(place_details={"confirmed_features": ["rooftop_bar"]})
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG

    def test_category_only_is_ok_not_strong(self):
        card = self._card(place_details=None, category="Izakaya")
        result = score_evidence_adequacy(card)
        assert result in (EvidenceAdequacy.OK, EvidenceAdequacy.THIN)
        assert result != EvidenceAdequacy.STRONG

    def test_empty_category_no_details_is_thin(self):
        card = self._card(place_details=None, category="")
        assert score_evidence_adequacy(card) == EvidenceAdequacy.THIN

    def test_modifier_evidence_alone_does_not_upgrade_to_strong(self):
        # Even when Northman is confirmed_address_or_name_context,
        # the card evidence adequacy is determined by Place Details, not modifier
        card = self._card(
            title="The Northman Beer & Cider Garden on the Riverwalk",
            category="Beer Garden",
            place_details=None,
        )
        result = score_evidence_adequacy(card)
        # Must be OK (not STRONG) without concrete place details
        assert result in (EvidenceAdequacy.OK, EvidenceAdequacy.THIN)
        assert result != EvidenceAdequacy.STRONG


# ── Section D: Retry prompt guidance is modifier-aware ───────────────────────

class TestRetryPromptGuidanceModifierAware:
    """D. Retry prompt guidance correctly handles Riverwalk name-context vs view modifier."""

    def _make_gate_result(self, result: NoteQualityResult, is_rating: bool = False, has_claim: bool = False):
        from app.services.places.note_quality_v1 import NoteQualityGateResult
        return NoteQualityGateResult(
            result=result,
            reason="test",
            is_rating_primary=is_rating,
            has_unsupported_claim=has_claim,
        )

    def test_riverwalk_name_context_guidance_allows_listing_mention(self):
        gate = self._make_gate_result(NoteQualityResult.FAIL_UNSUPPORTED_CLAIM, has_claim=True)
        guidance = build_retry_prompt_guidance(
            note="Great riverfront views.",
            quality_result=gate,
            user_modifier="river",
            modifier_status=ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT,
            card_name="The Northman Beer & Cider Garden on the Riverwalk",
        )
        guidance_lower = guidance.lower()
        assert "listing" in guidance_lower or "name" in guidance_lower
        assert "waterfront seating" in guidance_lower or "river views" in guidance_lower

    def test_unknown_river_guidance_does_not_encourage_claims(self):
        gate = self._make_gate_result(NoteQualityResult.FAIL_UNSUPPORTED_CLAIM, has_claim=True)
        guidance = build_retry_prompt_guidance(
            note="Waterfront seating available.",
            quality_result=gate,
            user_modifier="river",
            modifier_status=ModifierStatus.UNKNOWN,
            card_name="Generic Brewery",
        )
        # Should tell the LLM NOT to claim river views
        assert "not" in guidance.lower() or "do not" in guidance.lower() or "unverified" in guidance.lower()

    def test_view_modifier_guidance_mentions_confirmed_views(self):
        gate = self._make_gate_result(NoteQualityResult.FAIL_UNSUPPORTED_CLAIM, has_claim=True)
        guidance = build_retry_prompt_guidance(
            note="Panoramic views of the city.",
            quality_result=gate,
            user_modifier="view",
            modifier_status=ModifierStatus.UNKNOWN,
            card_name="Some Taproom",
        )
        assert "view" in guidance.lower() or "panoramic" in guidance.lower()

    def test_rating_primary_guidance_mentions_differentiator(self):
        gate = self._make_gate_result(NoteQualityResult.FAIL_RATING_PRIMARY, is_rating=True)
        guidance = build_retry_prompt_guidance(
            note="Highest-rated brewery.",
            quality_result=gate,
            user_modifier="none",
            modifier_status=None,
            card_name="Top Rated Brewery",
        )
        assert "differentiator" in guidance.lower() or "concrete" in guidance.lower()


# ── Section E: Per-card pipeline round-trip ───────────────────────────────────

class TestPerCardPipeline:
    """E. Per-card modifier → quality gate → validated/omitted decision."""

    def test_northman_pipeline_pass_with_safe_note(self):
        """Northman: name-confirmed → safe note → PASS without claiming view/seating."""
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="The Northman Beer & Cider Garden on the Riverwalk",
            card_address="1635 N Wells St",
            source_query="breweries near the river",
        )
        assert ev.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT

        note = (
            "Because the verified listing itself places Northman on the Riverwalk, "
            "it is the strongest river-context beer stop here; verify seating details directly."
        )
        gate = check_note_quality(note, "river", ev.modifier_status)
        assert gate.result == NoteQualityResult.PASS

    def test_northman_pipeline_fail_with_scenic_claim(self):
        """Northman: even with name-confirmed, scenic claims must fail."""
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="The Northman Beer & Cider Garden on the Riverwalk",
            card_address="1635 N Wells St",
            source_query="breweries near the river",
        )
        note = "Great riverfront views and waterfront seating."
        gate = check_note_quality(note, "river", ev.modifier_status)
        assert gate.result == NoteQualityResult.FAIL_UNSUPPORTED_CLAIM

    def test_generic_brewery_unknown_river_safe_note_passes(self):
        """Generic brewery: unknown river status → note without river claims passes."""
        ev = compute_modifier_evidence(
            user_modifier="river",
            card_name="Half Acre Beer Company",
            card_address="4257 N Lincoln Ave, Chicago IL",
            source_query="breweries near the river",
        )
        assert ev.modifier_status == ModifierStatus.UNKNOWN
        note = (
            "Half Acre's Lincoln Square taproom is known for Daisy Cutter Pale Ale; "
            "it appears in this river-area search based on location proximity but "
            "river proximity is not confirmed."
        )
        gate = check_note_quality(note, "river", ev.modifier_status)
        assert gate.result == NoteQualityResult.PASS

    def test_izakaya_no_modifier_note_passes_without_rating_primary(self):
        """Izakaya: no modifier → note without rating-primary passes."""
        ev = compute_modifier_evidence(
            user_modifier="none",
            card_name="Izakaya Mita",
            card_address="1960 W Chicago Ave",
            source_query="Izakayas",
        )
        note = (
            "Directly named Izakaya Mita is purpose-built around the format — "
            "sharing plates, grilled skewers, and Japanese cocktails."
        )
        gate = check_note_quality(note, "none", ev.modifier_status)
        assert gate.result == NoteQualityResult.PASS
