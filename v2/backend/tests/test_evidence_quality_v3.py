"""Tests for evidence adequacy scoring (v3 contract).

Covers:
1. THIN: card with only name/address/rating/reviews — no Place Details
2. OK: card with category fit but no extras
3. STRONG: card with editorial/amenity/snippet/feature in Place Details
4. Rating/review count alone cannot make evidence STRONG
5. EvidenceAdequacy is deterministic given card structure
6. Northman (name contains Riverwalk) is scored OK at minimum

Tests do NOT call any LLM, Supabase, or external provider.
"""

from __future__ import annotations

import pytest

from app.services.places.semantic_retrieval_v1 import (
    EvidenceAdequacy,
    PlaceCard,
    score_evidence_adequacy,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _card(
    index: int = 0,
    title: str = "Test Venue",
    address: str = "123 Main St, Chicago IL",
    rating: float = 4.5,
    review_count: int = 300,
    category: str = "Brewery",
    place_details: dict | None = None,
) -> PlaceCard:
    return PlaceCard(
        index=index,
        title=title,
        address=address,
        rating=rating,
        review_count=review_count,
        category=category,
        source_query="test query",
        place_details=place_details,
    )


# ── Section A: THIN evidence ──────────────────────────────────────────────────

class TestThinEvidence:
    def test_no_place_details_is_ok_not_thin_when_category_set(self):
        card = _card(place_details=None, category="Brewery")
        assert score_evidence_adequacy(card) == EvidenceAdequacy.OK

    def test_empty_place_details_with_good_category_is_ok(self):
        card = _card(place_details={}, category="Taproom")
        assert score_evidence_adequacy(card) == EvidenceAdequacy.OK

    def test_place_details_with_only_rating_is_thin_or_ok(self):
        # Rating in place_details provides no concrete differentiator
        card = _card(place_details={"rating": 4.8, "user_ratings_total": 1028}, category="")
        # No category, no editorial/amenity — result must not be STRONG
        result = score_evidence_adequacy(card)
        assert result != EvidenceAdequacy.STRONG

    def test_high_rating_alone_never_strong(self):
        card = _card(place_details={"rating": 5.0, "user_ratings_total": 9999}, category="Bar")
        assert score_evidence_adequacy(card) != EvidenceAdequacy.STRONG

    def test_many_reviews_alone_never_strong(self):
        card = _card(place_details={"user_ratings_total": 50000}, category="Restaurant")
        assert score_evidence_adequacy(card) != EvidenceAdequacy.STRONG

    def test_empty_category_with_no_details_is_thin(self):
        card = _card(place_details=None, category="")
        assert score_evidence_adequacy(card) == EvidenceAdequacy.THIN


# ── Section B: OK evidence ────────────────────────────────────────────────────

class TestOkEvidence:
    def test_category_fit_without_extras_is_ok(self):
        card = _card(place_details=None, category="Izakaya")
        assert score_evidence_adequacy(card) == EvidenceAdequacy.OK

    def test_northman_card_minimum_ok(self):
        # The Northman card must be at least OK — it has a distinctive name and category
        card = _card(
            title="The Northman Beer & Cider Garden on the Riverwalk",
            category="Beer Garden",
            place_details=None,
        )
        result = score_evidence_adequacy(card)
        assert result in (EvidenceAdequacy.OK, EvidenceAdequacy.STRONG)

    def test_short_category_is_ok_not_thin(self):
        card = _card(place_details=None, category="Bar")
        assert score_evidence_adequacy(card) == EvidenceAdequacy.OK

    def test_place_details_rating_reviews_only_is_ok_not_strong(self):
        card = _card(
            place_details={"rating": 4.7, "user_ratings_total": 690},
            category="Taproom",
        )
        assert score_evidence_adequacy(card) in (EvidenceAdequacy.OK, EvidenceAdequacy.THIN)
        assert score_evidence_adequacy(card) != EvidenceAdequacy.STRONG


# ── Section C: STRONG evidence ───────────────────────────────────────────────

class TestStrongEvidence:
    def test_editorial_summary_makes_strong(self):
        card = _card(place_details={"editorial_summary": "A beloved Wicker Park taproom known for experimental ales."})
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG

    def test_description_makes_strong(self):
        card = _card(place_details={"description": "Offers outdoor patio with fire pits and locally sourced bar snacks."})
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG

    def test_amenities_makes_strong(self):
        card = _card(place_details={"amenities": ["outdoor_seating", "live_music", "dog_friendly"]})
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG

    def test_review_snippet_makes_strong(self):
        card = _card(place_details={"review_snippet": "The Czech-style lager here is the real deal — cold and crisp."})
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG

    def test_confirmed_features_makes_strong(self):
        card = _card(place_details={"confirmed_features": ["rooftop_bar", "seasonal_menu"]})
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG

    def test_rating_plus_editorial_is_strong(self):
        card = _card(
            place_details={
                "rating": 4.8,
                "user_ratings_total": 1028,
                "editorial_summary": "Flagship West Loop brewery with a rotating seasonal tap list.",
            }
        )
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG

    def test_multiple_extras_still_strong(self):
        card = _card(
            place_details={
                "amenities": ["rooftop"],
                "editorial_summary": "Rooftop bar with city views.",
                "confirmed_features": ["outdoor_seating"],
            }
        )
        assert score_evidence_adequacy(card) == EvidenceAdequacy.STRONG


# ── Section D: Determinism ────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_card_same_result(self):
        card = _card(place_details={"editorial_summary": "Great beer selection."})
        r1 = score_evidence_adequacy(card)
        r2 = score_evidence_adequacy(card)
        assert r1 == r2

    def test_adding_editorial_upgrades_from_ok_to_strong(self):
        card_ok = _card(place_details=None, category="Brewery")
        card_strong = _card(
            place_details={"editorial_summary": "Award-winning hazy IPAs."},
            category="Brewery",
        )
        assert score_evidence_adequacy(card_ok) == EvidenceAdequacy.OK
        assert score_evidence_adequacy(card_strong) == EvidenceAdequacy.STRONG

    def test_rating_review_never_upgrades_thin_to_strong(self):
        for rating in [4.5, 4.8, 5.0]:
            for reviews in [100, 1000, 10000]:
                card = _card(place_details={"rating": rating, "user_ratings_total": reviews}, category="")
                assert score_evidence_adequacy(card) != EvidenceAdequacy.STRONG, (
                    f"Rating {rating}★ / {reviews} reviews must not yield STRONG"
                )
