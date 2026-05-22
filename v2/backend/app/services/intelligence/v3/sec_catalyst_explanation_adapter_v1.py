"""Stage 8E — SEC catalyst explanation display adapter.

Pure deterministic adapter (no IO, no DB, no LLM, no providers). Converts an
existing SEC catalyst artifact payload (from research_artifacts.payload JSONB)
into optional plain-English explanation fields for the Intel drawer.

Output contract:
  All returned string fields are safe for direct display. No raw backend codes
  exposed (no skill_pack, fact_kind, model_version, READY, PARTIAL,
  USABLE_WITH_LIMITATIONS, sec_catalyst_sentiment, SEC_CATALYST_MODEL_VERSION, etc.).

  Returns empty dict when payload is missing or lacks sufficient detail.
  Callers must fall back to generic Stage 8D copy when empty dict returned.

Input:
  artifact_payload: the 'payload' JSONB from a research_artifacts row,
    as written by sec_catalyst_sentiment_adapter_v1. Relevant fields:
      catalyst_count (int): material fresh filings processed
      usable_count   (int): how many were usable for the evidence lane

Output fields (all optional — present only when payload has sufficient detail):
  event_summary:          What kind of activity was found and why it matters
  freshness_label:        Brief recency note
  material_filing_label:  "One recent official filing was found." / "N recent..."
  limitation_note:        Scope boundary (official events only)
  decision_authority_note: No Buy/Hold/Trim/Sell authority claim
"""
from __future__ import annotations

from typing import Any, Optional


def build_sec_catalyst_explanation(
    artifact_payload: Optional[dict[str, Any]],
) -> dict[str, str]:
    """Derive optional plain-English explanation fields from an artifact payload.

    Returns empty dict when payload is None, missing required keys, or
    catalyst_count is 0 or non-integer (caller uses generic Stage 8D fallback).

    Args:
        artifact_payload: Full JSONB payload from a research_artifacts row
            written by sec_catalyst_sentiment_adapter_v1. May be None.

    Returns:
        Dict of optional display-safe strings. Keys when present:
          event_summary, freshness_label, material_filing_label,
          limitation_note, decision_authority_note.
    """
    if not artifact_payload or not isinstance(artifact_payload, dict):
        return {}

    catalyst_count = artifact_payload.get("catalyst_count")
    usable_count = artifact_payload.get("usable_count", 0)

    if not isinstance(catalyst_count, int) or catalyst_count <= 0:
        return {}

    # Filing count label — safe plain-English, not a raw metric.
    if catalyst_count == 1:
        material_filing_label = "One recent official filing was found."
    else:
        material_filing_label = f"{catalyst_count} recent official filings were found."

    # Event summary — derived from usability, no raw codes.
    if isinstance(usable_count, int) and usable_count >= 1:
        event_summary = (
            "Recent official filing activity was found. "
            "The filing appears material enough to support the sentiment evidence lane."
        )
    else:
        event_summary = "Recent official filing activity was found."

    return {
        "event_summary": event_summary,
        "freshness_label": "Filing activity is within the relevant reporting window.",
        "material_filing_label": material_filing_label,
        "limitation_note": (
            "This covers official company/SEC events only, not broad market opinion."
        ),
        "decision_authority_note": (
            "This is useful context, but it does not decide Buy, Hold, Trim, or Sell by itself."
        ),
    }
