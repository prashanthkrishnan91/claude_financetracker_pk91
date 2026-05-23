"""Stage 8F — SEC filing type display adapter.

Pure deterministic adapter (no IO, no DB, no LLM). Maps form type identifiers
from existing research_artifact_sources.section_reference values to safe
plain-English display labels for the Intel drawer.

Input:  list[str] of section_reference values (e.g. ["10-K", "10-Q"])
Output: dict with optional filing_type_label field

Display rules:
  - Single known form → "Annual report (10-K)" / "Quarterly report (10-Q)" /
    "Company event filing (8-K)"
  - Multiple distinct known forms → "Multiple recent official filings"
  - Unknown / unrecognised forms only → "Official company filing"
  - Empty / no forms → {} (caller falls back to Stage 8E generic copy)

Hard constraints:
  - No raw backend codes in any returned string.
  - No form code appears WITHOUT an accompanying plain-English label.
    (The form code may appear parenthetically AFTER the plain label.)
  - No decision authority claims.
  - No fabrication of filing content, polarity, or event details.
"""
from __future__ import annotations

from typing import Any

# Map from SEC form type to (plain_label, parenthetical_code).
# parenthetical_code appears in parentheses after the plain label when the
# form type would be recognisable to an investor who reads financial news.
_FORM_TYPE_DISPLAY: dict[str, tuple[str, str]] = {
    "10-K": ("Annual report", "10-K"),
    "10-Q": ("Quarterly report", "10-Q"),
    "8-K": ("Company event filing", "8-K"),
}

_GENERIC_LABEL = "Official company filing"
_MULTIPLE_LABEL = "Multiple recent official filings"


def build_filing_type_display(form_types: list[Any]) -> dict[str, str]:
    """Map a list of section_reference values to a safe plain-English label.

    Args:
        form_types: List of section_reference strings from
            research_artifact_sources rows associated with the active SEC
            catalyst artifact for a ticker.  May contain duplicates (one
            entry per filing source record).

    Returns:
        {"filing_type_label": "<plain-English label>"} when a label can be
        determined, or {} when form_types is empty/all-None (caller shows no
        specificity line and the Stage 8E copy stands as-is).
    """
    if not form_types:
        return {}

    # Normalise and deduplicate, preserving first-seen order.
    seen: dict[str, None] = {}
    for ft in form_types:
        if isinstance(ft, str) and ft.strip():
            seen[ft.upper().strip()] = None
    unique_forms = list(seen.keys())

    if not unique_forms:
        return {}

    known_forms = [f for f in unique_forms if f in _FORM_TYPE_DISPLAY]
    unknown_forms = [f for f in unique_forms if f not in _FORM_TYPE_DISPLAY]

    if not known_forms and unknown_forms:
        # No recognised form types — generic fallback.
        return {"filing_type_label": _GENERIC_LABEL}

    if len(known_forms) == 1:
        plain_label, code = _FORM_TYPE_DISPLAY[known_forms[0]]
        return {"filing_type_label": f"{plain_label} ({code})"}

    # Multiple distinct known form types (e.g. both 10-K and 10-Q found).
    return {"filing_type_label": _MULTIPLE_LABEL}
