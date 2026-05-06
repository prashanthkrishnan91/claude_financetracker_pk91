"""Reasoning reliability v2 tests — production-log fixture validation.

Derived from PR #251 production log failures:
1. `breweries near the river` — Northman card was omitted; must now be 8/8 accepted.
2. `taprooms with a view` — notes were rating-heavy; must now be 8/8 with quality notes.
3. `Izakayas` — venue_head_recognized=True; must be 8/8 with no rating-primary notes.

Contract:
- All queries: 8/8 accepted (final_note_omitted_count=0)
- Northman card: validated=True, modifier_status=confirmed_address_or_name_context
- No rating/review-primary notes in any accepted card
- deterministic_visible_count=0
- excluded_unvalidated_count=0
- Izakayas: venue_head_recognized=True
"""

from __future__ import annotations

import pytest

from app.services.places.modifier_evidence_v1 import ModifierStatus
from app.services.places.note_quality_v1 import NoteQualityResult, check_note_quality
from app.services.places.semantic_retrieval_v1 import (
    EvidenceAdequacy,
    PlaceCard,
    process_semantic_retrieval,
)


# ── Production-style note fixtures ───────────────────────────────────────────
# Notes are quality-gate compliant (no rating-primary, no unsupported claims)

_BREWERY_NOTES: dict[str, str] = {
    "Goose Island Wrigleyville": (
        "Goose Island's Wrigleyville location offers the full flagship lineup — "
        "including the celebrated Bourbon County Stout series — in a lively sports-bar atmosphere "
        "a short walk from the Chicago River corridor."
    ),
    "Begyle Brewing": (
        "A Ravenswood neighborhood mainstay with a welcoming taproom and a focus on sessionable "
        "lagers and approachable ales; solid stop if exploring breweries north of the river bend."
    ),
    "Revolution Brewing Taproom": (
        "Revolution's Logan Square taproom pours the full range — from the cult Anti-Hero IPA to "
        "barrel-aged releases — in a repurposed industrial space with a full kitchen."
    ),
    "The Northman Beer & Cider Garden on the Riverwalk": (
        "Because the verified listing itself places Northman on the Riverwalk, it is the strongest "
        "river-context beer stop in this set; note that actual patio or seating details should be "
        "confirmed directly before visiting for a riverfront experience."
    ),
    "Half Acre Beer Company": (
        "Half Acre's North Center taproom is best known for its Daisy Cutter Pale Ale and "
        "consistent hop-forward lineup; a reliable destination brewery a reasonable distance "
        "from the river district."
    ),
    "Hop Butcher For The World": (
        "A nomadic Chicago brewer with a strong reputation for hazy IPAs; their flagship taproom "
        "offers rotating small-batch releases and a no-frills, beer-focused environment."
    ),
    "Off Color Brewing": (
        "Off Color specialises in unusual styles — gose, Berliner Weisse, saison — that you won't "
        "find at most Chicago taprooms; their Mousetrap taproom is compact but distinctive."
    ),
    "Pilot Project Brewing": (
        "Pilot Project's Logan Square location functions as an incubator for Chicago's emerging "
        "brewers; the rotating tap list changes frequently, making repeat visits worthwhile."
    ),
}

_TAPROOM_NOTES: dict[str, str] = {
    "Forbidden Root": (
        "Forbidden Root's botanical brewing approach yields unusual flavour profiles; "
        "the River North space is handsome but view status is unverified — worth checking "
        "for outdoor or upper-floor options before visiting for a view."
    ),
    "Corridor Brewing & Provisions": (
        "A Lakeview neighbourhood taproom with a food-forward menu and good beer selection; "
        "no confirmed view but a pleasant interior with large windows."
    ),
    "Spiteful Brewing Rooftop": (
        "Spiteful's rooftop deck offers an open-air setting above Andersonville; "
        "the name and listing confirm rooftop access, making this the strongest view candidate here."
    ),
    "Dovetail Brewery": (
        "Dovetail specialises in European lager styles — Kölsch, Helles, Märzen — brewed "
        "with traditional methods; their Lincoln Square space is elegant but view is unverified."
    ),
    "Baderbrau Brewing": (
        "Baderbrau's Pilsen taproom leans into Czech lager heritage; no confirmed elevated view "
        "but a spacious outdoor beer garden makes it worthwhile in good weather."
    ),
    "Empirical Brewery": (
        "Empirical's Ravenswood taproom has a generous rooftop patio confirmed by provider details — "
        "one of the more reliable open-air options in Chicago's north-side brewery circuit."
    ),
    "Whiner Beer Co.": (
        "Whiner focuses on French and Italian-inspired farmhouse ales brewed in the Back of the Yards; "
        "view/outdoor seating status is unverified — call ahead to confirm patio availability."
    ),
    "Maplewood Brewery & Distillery": (
        "Maplewood combines craft beer and spirits production under one roof in Humboldt Park; "
        "an interesting dual-concept stop though confirmed view features are not present."
    ),
}

_IZAKAYA_NOTES: dict[str, str] = {
    "Gaijin": (
        "Gaijin channels a classic Tokyo izakaya vibe with a tightly curated menu of Japanese "
        "small plates, yakitori skewers, and a strong whisky and sake selection in the West Loop."
    ),
    "Arami": (
        "Arami's Ukrainian Village space pairs refined Japanese small plates with an extensive "
        "sake and shochu list; the omakase option is a standout for a more immersive experience."
    ),
    "Izakaya Mita": (
        "Directly named Izakaya Mita is purpose-built around the format — sharing plates, "
        "grilled skewers, and Japanese cocktails in a lively dinner-bar setting in Wicker Park."
    ),
    "Ryoko's Japanese Restaurant": (
        "Ryoko's brings an izakaya-style menu — small plates, handrolls, and bar snacks — "
        "to the River North area; a reliable choice for casual Japanese gastropub dining."
    ),
    "Ko Japanese Restaurant": (
        "Ko's Andersonville location focuses on Japanese comfort food and izakaya staples; "
        "the neighbourhood setting keeps the atmosphere relaxed and the menu approachable."
    ),
    "Tanuki": (
        "Tanuki's Logan Square izakaya specialises in Japanese street food — karaage, takoyaki, "
        "okonomiyaki — paired with Japanese highballs; a strong category fit for this query."
    ),
    "Katana": (
        "Katana is a River North Japanese concept with a strong robata-grill and sashimi program; "
        "the bar seating at the grill counter provides the most izakaya-adjacent experience here."
    ),
    "Ramen-San": (
        "Ramen-San straddles izakaya and ramen-bar formats; the Fulton Market location has "
        "a full cocktail program and bar-snack menu alongside the ramen bowls."
    ),
}


# ── Fixture builder ──────────────────────────────────────────────────────────

def _make_brewery_cards() -> list[PlaceCard]:
    titles = list(_BREWERY_NOTES.keys())
    cards = []
    for i, title in enumerate(titles):
        category = "Beer Garden" if "Northman" in title else "Brewery"
        cards.append(PlaceCard(
            index=i + 1,
            title=title,
            address=f"{200 + i * 10} N Example Ave, Chicago IL 60614",
            rating=4.2 + i * 0.07,
            review_count=300 + i * 80,
            category=category,
            source_query="breweries near the river",
        ))
    return cards


def _make_taproom_cards() -> list[PlaceCard]:
    titles = list(_TAPROOM_NOTES.keys())
    cards = []
    for i, title in enumerate(titles):
        details = None
        if "Rooftop" in title or "rooftop" in title.lower():
            details = {"confirmed_features": ["rooftop"], "editorial_summary": "Rooftop taproom."}
        elif "Empirical" in title:
            details = {"amenities": ["rooftop_patio"], "editorial_summary": "Open rooftop patio."}
        cards.append(PlaceCard(
            index=i + 1,
            title=title,
            address=f"{300 + i * 10} W Example Blvd, Chicago IL 60618",
            rating=4.1 + i * 0.09,
            review_count=250 + i * 70,
            category="Taproom",
            source_query="taprooms with a view",
            place_details=details,
        ))
    return cards


def _make_izakaya_cards() -> list[PlaceCard]:
    titles = list(_IZAKAYA_NOTES.keys())
    cards = []
    for i, title in enumerate(titles):
        cards.append(PlaceCard(
            index=i + 1,
            title=title,
            address=f"{400 + i * 10} S Example St, Chicago IL 60612",
            rating=4.4 + i * 0.05,
            review_count=400 + i * 100,
            category="izakaya" if "Izakaya" in title else "Japanese Restaurant",
            source_query="Izakayas",
        ))
    return cards


# ── Mock note generators ──────────────────────────────────────────────────────

def _brewery_note_generator(prompt: str) -> str:
    for title, note in _BREWERY_NOTES.items():
        if title in prompt:
            return note
    return (
        "A Chicago craft brewery with a good taproom selection; "
        "worth visiting for rotating seasonal taps."
    )


def _taproom_note_generator(prompt: str) -> str:
    for title, note in _TAPROOM_NOTES.items():
        if title in prompt:
            return note
    return (
        "A Chicago taproom with a solid beer selection; "
        "view status is unverified — check directly before visiting."
    )


def _izakaya_note_generator(prompt: str) -> str:
    for title, note in _IZAKAYA_NOTES.items():
        if title in prompt:
            return note
    return (
        "A Japanese gastropub-style venue with izakaya small plates, "
        "sake selection, and a relaxed bar atmosphere."
    )


# ── Shared assertion helpers ──────────────────────────────────────────────────

def _assert_8_of_8_accepted(result, query_label: str) -> None:
    assert result.llm_accepted_count == 8, (
        f"{query_label}: expected 8/8 accepted, got {result.llm_accepted_count}. "
        f"Omitted: {[n.card_title for n in result.per_card_notes if not n.validated]}"
    )
    assert result.final_note_omitted_count == 0, (
        f"{query_label}: final_note_omitted_count must be 0"
    )
    assert result.excluded_unvalidated_count == 0, (
        f"{query_label}: excluded_unvalidated_count must be 0"
    )
    assert result.deterministic_visible_count == 0, (
        f"{query_label}: deterministic_visible_count must be 0"
    )
    assert result.reasoning_success is True, (
        f"{query_label}: reasoning_success must be True; failure_reason={result.reasoning_failure_reason}"
    )
    assert result.final_card_count == 8, (
        f"{query_label}: final_card_count must be 8"
    )


def _assert_no_rating_primary_notes(result, query_label: str) -> None:
    for note in result.per_card_notes:
        if not note.validated:
            continue
        gate = check_note_quality(note.visible_concierge_note, user_modifier="none")
        assert gate.result != NoteQualityResult.FAIL_RATING_PRIMARY, (
            f"{query_label}: card '{note.card_title}' has rating-primary note: "
            f"'{note.visible_concierge_note[:100]}'"
        )


# ── Section A: Breweries near the river ──────────────────────────────────────

class TestBreweriesNearTheRiver:
    def test_8_of_8_accepted(self):
        cards = _make_brewery_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_brewery_note_generator,
        )
        _assert_8_of_8_accepted(result, "breweries near the river")

    def test_northman_validated(self):
        cards = _make_brewery_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_brewery_note_generator,
        )
        northman = next(n for n in result.per_card_notes if "Northman" in n.card_title)
        assert northman.validated is True, (
            f"Northman must be validated; rejection_reason={northman.rejection_reason}"
        )
        assert northman.modifier_status == ModifierStatus.CONFIRMED_ADDRESS_OR_NAME_CONTEXT.value

    def test_northman_note_has_no_unsupported_claim(self):
        cards = _make_brewery_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_brewery_note_generator,
        )
        northman = next(n for n in result.per_card_notes if "Northman" in n.card_title)
        note_lower = northman.visible_concierge_note.lower()
        assert "waterfront seating" not in note_lower
        assert "riverfront view" not in note_lower
        assert "river view" not in note_lower

    def test_northman_note_mentions_riverwalk_context(self):
        cards = _make_brewery_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_brewery_note_generator,
        )
        northman = next(n for n in result.per_card_notes if "Northman" in n.card_title)
        note_lower = northman.visible_concierge_note.lower()
        assert "riverwalk" in note_lower or "listing" in note_lower or "river-context" in note_lower

    def test_no_rating_primary_notes(self):
        cards = _make_brewery_cards()
        result = process_semantic_retrieval(
            query="breweries near the river",
            cards=cards,
            note_generator=_brewery_note_generator,
        )
        _assert_no_rating_primary_notes(result, "breweries near the river")


# ── Section B: Taprooms with a view ──────────────────────────────────────────

class TestTaproomsWithAView:
    def test_8_of_8_accepted(self):
        cards = _make_taproom_cards()
        result = process_semantic_retrieval(
            query="taprooms with a view",
            cards=cards,
            note_generator=_taproom_note_generator,
        )
        _assert_8_of_8_accepted(result, "taprooms with a view")

    def test_no_rating_primary_notes(self):
        cards = _make_taproom_cards()
        result = process_semantic_retrieval(
            query="taprooms with a view",
            cards=cards,
            note_generator=_taproom_note_generator,
        )
        _assert_no_rating_primary_notes(result, "taprooms with a view")

    def test_confirmed_rooftop_card_has_useful_note(self):
        cards = _make_taproom_cards()
        result = process_semantic_retrieval(
            query="taprooms with a view",
            cards=cards,
            note_generator=_taproom_note_generator,
        )
        rooftop_notes = [
            n for n in result.per_card_notes
            if "rooftop" in n.card_title.lower() or "empirical" in n.card_title.lower()
        ]
        assert rooftop_notes, "Expected at least one rooftop card"
        for rn in rooftop_notes:
            assert rn.validated
            assert "rooftop" in rn.visible_concierge_note.lower() or "view" in rn.visible_concierge_note.lower()

    def test_unconfirmed_view_cards_note_says_unverified(self):
        cards = _make_taproom_cards()
        result = process_semantic_retrieval(
            query="taprooms with a view",
            cards=cards,
            note_generator=_taproom_note_generator,
        )
        unknown_view_notes = [
            n for n in result.per_card_notes
            if n.modifier_status == ModifierStatus.UNKNOWN.value and n.validated
        ]
        for note in unknown_view_notes:
            text_lower = note.visible_concierge_note.lower()
            mentions_uncertainty = (
                "unverified" in text_lower
                or "not confirmed" in text_lower
                or "confirm" in text_lower
                or "check" in text_lower
                or "unknown" in text_lower
            )
            assert mentions_uncertainty, (
                f"Card '{note.card_title}' has unknown view status but note doesn't mention uncertainty: "
                f"'{note.visible_concierge_note[:100]}'"
            )


# ── Section C: Izakayas ───────────────────────────────────────────────────────

class TestIzakayas:
    def test_8_of_8_accepted(self):
        cards = _make_izakaya_cards()
        result = process_semantic_retrieval(
            query="Izakayas",
            cards=cards,
            note_generator=_izakaya_note_generator,
        )
        _assert_8_of_8_accepted(result, "Izakayas")

    def test_venue_head_recognized(self):
        cards = _make_izakaya_cards()
        result = process_semantic_retrieval(
            query="Izakayas",
            cards=cards,
            note_generator=_izakaya_note_generator,
            card_categories=[c.category for c in cards],
        )
        assert result.venue_head_recognized is True

    def test_no_rating_primary_notes(self):
        cards = _make_izakaya_cards()
        result = process_semantic_retrieval(
            query="Izakayas",
            cards=cards,
            note_generator=_izakaya_note_generator,
        )
        _assert_no_rating_primary_notes(result, "Izakayas")

    def test_notes_mention_izakaya_concept(self):
        cards = _make_izakaya_cards()
        result = process_semantic_retrieval(
            query="Izakayas",
            cards=cards,
            note_generator=_izakaya_note_generator,
        )
        for note in result.per_card_notes:
            if not note.validated:
                continue
            text_lower = note.visible_concierge_note.lower()
            has_concept = any(
                word in text_lower
                for word in ["izakaya", "japanese", "sake", "yakitori", "small plate", "gastropub", "skewer", "ramen", "omakase"]
            )
            assert has_concept, (
                f"Card '{note.card_title}' note lacks izakaya/japanese concept: "
                f"'{note.visible_concierge_note[:100]}'"
            )
