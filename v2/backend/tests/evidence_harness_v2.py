"""Evidence Harness v2 — simple table output for the three production queries.

Run with:
    cd v2/backend && python -m tests.evidence_harness_v2

Prints per-card evidence table. Pass criteria printed at the end.
"""

from __future__ import annotations

import sys
import os

# Add backend to path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.places.modifier_evidence_v1 import ModifierStatus
from app.services.places.semantic_retrieval_v1 import (
    PlaceCard,
    process_semantic_retrieval,
)
from tests.test_reasoning_reliability_v2 import (
    _make_brewery_cards,
    _make_taproom_cards,
    _make_izakaya_cards,
    _brewery_note_generator,
    _taproom_note_generator,
    _izakaya_note_generator,
)


QUERIES = [
    ("breweries near the river", _make_brewery_cards, _brewery_note_generator),
    ("taprooms with a view", _make_taproom_cards, _taproom_note_generator),
    ("Izakayas", _make_izakaya_cards, _izakaya_note_generator),
]

COL_WIDTHS = {
    "idx": 4,
    "title": 46,
    "adequacy": 8,
    "modifier": 9,
    "mod_status": 30,
    "validated": 9,
    "gate": 22,
}


def _truncate(s: str, n: int) -> str:
    return s[:n] if len(s) <= n else s[: n - 2] + ".."


def _print_header():
    print(
        f"{'#':4} {'Title':46} {'Adequacy':8} {'Modifier':9} "
        f"{'ModStatus':30} {'Valid':9} {'Gate':22}"
    )
    print("-" * 132)


def _print_row(note) -> None:
    print(
        f"{note.card_index:4} "
        f"{_truncate(note.card_title, 46):46} "
        f"{note.evidence_adequacy.value:8} "
        f"{note.user_modifier:9} "
        f"{_truncate(note.modifier_status, 30):30} "
        f"{'YES' if note.validated else 'NO':9} "
        f"{note.quality_gate_result:22}"
    )


def run_harness() -> bool:
    all_pass = True
    for query, card_factory, note_gen in QUERIES:
        cards = card_factory()
        result = process_semantic_retrieval(
            query=query,
            cards=cards,
            note_generator=note_gen,
            card_categories=[c.category for c in cards],
        )

        print()
        print(f"=== Query: '{query}' ===")
        _print_header()
        for note in result.per_card_notes:
            _print_row(note)

        print()
        print(f"  reasoning_success        : {result.reasoning_success}")
        print(f"  llm_accepted_count       : {result.llm_accepted_count} / {len(cards)}")
        print(f"  retry_recovered_count    : {result.retry_recovered_count}")
        print(f"  fallback_model_used_count: {result.fallback_model_used_count}")
        print(f"  deterministic_visible    : {result.deterministic_visible_count}")
        print(f"  final_note_omitted_count : {result.final_note_omitted_count}")
        print(f"  excluded_unvalidated     : {result.excluded_unvalidated_count}")
        print(f"  final_card_count         : {result.final_card_count}")
        print(f"  venue_head_recognized    : {result.venue_head_recognized}")

        # Pass criteria checks
        checks = {
            "8/8 accepted": result.llm_accepted_count == len(cards),
            "no omissions": result.final_note_omitted_count == 0,
            "deterministic=0": result.deterministic_visible_count == 0,
            "excluded=0": result.excluded_unvalidated_count == 0,
            "reasoning_success": result.reasoning_success,
        }
        if query == "Izakayas":
            checks["venue_head_recognized"] = result.venue_head_recognized
        if query == "breweries near the river":
            northman = next(
                (n for n in result.per_card_notes if "Northman" in n.card_title), None
            )
            checks["Northman validated"] = northman is not None and northman.validated
            checks["Northman safe wording"] = northman is not None and (
                "waterfront seating" not in northman.visible_concierge_note.lower()
                and "riverfront view" not in northman.visible_concierge_note.lower()
            )

        print()
        passed = all(checks.values())
        if not passed:
            all_pass = False
        for k, v in checks.items():
            status = "PASS" if v else "FAIL"
            print(f"  [{status}] {k}")

    print()
    if all_pass:
        print("=== HARNESS v2 RESULT: ALL PASS ===")
    else:
        print("=== HARNESS v2 RESULT: FAILURES DETECTED ===")
    return all_pass


if __name__ == "__main__":
    ok = run_harness()
    sys.exit(0 if ok else 1)
