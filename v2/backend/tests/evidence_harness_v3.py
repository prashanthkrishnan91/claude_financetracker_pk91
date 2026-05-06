"""Evidence Harness v3 — full column evidence table for three production queries.

Run with:
    cd v2/backend && python -m tests.evidence_harness_v3

Required columns (per task spec):
  query | card_index | card_title | evidence_adequacy | user_modifier |
  modifier_status | displayWhyValidated | displayWhySource |
  visible_concierge_note | quality_gate_result | retry_used | fallback_used

Pass criteria:
  - all 8/8 validated for each query
  - no NOTE OMITTED
  - final_note_omitted_count=0
  - excluded_unvalidated_count=0
  - deterministic_visible_count=0
  - no rating/review-primary notes
  - no unsupported view/river/waterfront claim
  - Northman/Riverwalk card is validated with safe wording
  - izakaya venue head recognized
"""

from __future__ import annotations

import sys
import os
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.places.note_quality_v1 import NoteQualityResult, check_note_quality
from app.services.places.semantic_retrieval_v1 import (
    PlaceCard,
    PerCardNote,
    SemanticRetrievalResult,
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

NOTE_WRAP = 80


def _wrap(s: str, width: int = NOTE_WRAP, indent: str = "   ") -> str:
    return textwrap.fill(s, width=width, subsequent_indent=indent)


def _print_card_row(query: str, note: PerCardNote) -> None:
    print(f"  card_index        : {note.card_index}")
    print(f"  card_title        : {note.card_title}")
    print(f"  evidence_adequacy : {note.evidence_adequacy.value}")
    print(f"  user_modifier     : {note.user_modifier}")
    print(f"  modifier_status   : {note.modifier_status}")
    print(f"  displayWhyValid   : {note.display_why_validated}")
    print(f"  displayWhySource  : {note.display_why_source}")
    print(f"  quality_gate      : {note.quality_gate_result}")
    print(f"  retry_used        : {note.retry_used}")
    print(f"  fallback_used     : {note.fallback_used}")
    if note.validated:
        wrapped = _wrap(note.visible_concierge_note)
        print(f"  visible_note      : {wrapped}")
    else:
        print(f"  visible_note      : *** NOTE OMITTED *** reason={note.rejection_reason}")
    print()


def _check_no_rating_primary(result: SemanticRetrievalResult, query: str) -> list[str]:
    failures = []
    for note in result.per_card_notes:
        if not note.validated:
            continue
        gate = check_note_quality(note.visible_concierge_note, user_modifier="none")
        if gate.result == NoteQualityResult.FAIL_RATING_PRIMARY:
            failures.append(
                f"Card {note.card_index} '{note.card_title}' has rating-primary note"
            )
    return failures


def run_harness() -> bool:
    all_pass = True
    failures_by_query: dict[str, list[str]] = {}

    for query, card_factory, note_gen in QUERIES:
        cards = card_factory()
        result = process_semantic_retrieval(
            query=query,
            cards=cards,
            note_generator=note_gen,
            card_categories=[c.category for c in cards],
        )

        print()
        print("=" * 90)
        print(f"QUERY: '{query}'")
        print("=" * 90)

        for note in result.per_card_notes:
            print(f"--- Card {note.card_index} ---")
            _print_card_row(query, note)

        print("--- Turn Summary ---")
        print(f"  reasoning_success         : {result.reasoning_success}")
        print(f"  reasoning_failure_reason  : {result.reasoning_failure_reason}")
        print(f"  llm_accepted_count        : {result.llm_accepted_count}")
        print(f"  retry_recovered_count     : {result.retry_recovered_count}")
        print(f"  fallback_model_used_count : {result.fallback_model_used_count}")
        print(f"  deterministic_visible_count: {result.deterministic_visible_count}")
        print(f"  final_note_omitted_count  : {result.final_note_omitted_count}")
        print(f"  excluded_unvalidated_count: {result.excluded_unvalidated_count}")
        print(f"  final_card_count          : {result.final_card_count}")
        print(f"  venue_head_recognized     : {result.venue_head_recognized}")

        # Collect failures for this query
        q_failures: list[str] = []

        if result.llm_accepted_count != len(cards):
            q_failures.append(f"accepted {result.llm_accepted_count}/{len(cards)} (expected 8/8)")
        if result.final_note_omitted_count != 0:
            q_failures.append(f"final_note_omitted_count={result.final_note_omitted_count} (expected 0)")
        if result.excluded_unvalidated_count != 0:
            q_failures.append(f"excluded_unvalidated_count={result.excluded_unvalidated_count} (expected 0)")
        if result.deterministic_visible_count != 0:
            q_failures.append(f"deterministic_visible_count={result.deterministic_visible_count} (expected 0)")
        if not result.reasoning_success:
            q_failures.append(f"reasoning_success=False reason={result.reasoning_failure_reason}")

        # Query-specific checks
        if "izakaya" in query.lower():
            if not result.venue_head_recognized:
                q_failures.append("venue_head_recognized=False (expected True)")

        if "river" in query.lower():
            northman = next(
                (n for n in result.per_card_notes if "Northman" in n.card_title), None
            )
            if northman is None:
                q_failures.append("Northman card not found in results")
            elif not northman.validated:
                q_failures.append(
                    f"Northman not validated: rejection_reason={northman.rejection_reason}"
                )
            else:
                note_lower = northman.visible_concierge_note.lower()
                if "waterfront seating" in note_lower:
                    q_failures.append("Northman note claims 'waterfront seating'")
                if "riverfront view" in note_lower:
                    q_failures.append("Northman note claims 'riverfront view'")
                if "river view" in note_lower:
                    q_failures.append("Northman note claims 'river view'")

        # Rating-primary check
        rating_failures = _check_no_rating_primary(result, query)
        q_failures.extend(rating_failures)

        failures_by_query[query] = q_failures

        print()
        if q_failures:
            all_pass = False
            print(f"  [FAIL] {query}")
            for f in q_failures:
                print(f"         - {f}")
        else:
            print(f"  [PASS] {query} — 8/8 accepted, all criteria met")

    # Final summary
    print()
    print("=" * 90)
    print("HARNESS v3 FINAL SUMMARY")
    print("=" * 90)
    for query, q_failures in failures_by_query.items():
        status = "PASS" if not q_failures else "FAIL"
        print(f"  [{status}] {query}")
        for f in q_failures:
            print(f"           - {f}")

    print()
    if all_pass:
        print("RESULT: ALL PASS")
        print()
        print("Pass criteria satisfied:")
        print("  [PASS] all 8/8 validated for each query")
        print("  [PASS] no NOTE OMITTED")
        print("  [PASS] final_note_omitted_count=0")
        print("  [PASS] excluded_unvalidated_count=0")
        print("  [PASS] deterministic_visible_count=0")
        print("  [PASS] no rating/review-primary notes")
        print("  [PASS] Northman/Riverwalk card validated with safe wording")
        print("  [PASS] izakaya venue head recognized")
    else:
        print("RESULT: FAILURES DETECTED")

    return all_pass


if __name__ == "__main__":
    ok = run_harness()
    sys.exit(0 if ok else 1)
