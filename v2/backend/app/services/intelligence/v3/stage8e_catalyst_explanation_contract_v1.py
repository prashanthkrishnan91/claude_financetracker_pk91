"""Stage 8E snapshot explanation contract version and staleness helpers.

When the snapshot gains Stage 8E catalyst explanation fields
(event_summary, material_filing_label, etc. inside sec_catalyst_evidence),
this version marker lets old Stage 8D-only snapshots be detected as stale
and recertified deterministically — no analyst jobs, no SQL changes, no LLM.

Pattern mirrors stage7_snapshot_contract_v1.py.
"""
from __future__ import annotations

from typing import Optional

STAGE8E_CATALYST_EXPLANATION_CONTRACT_VERSION = "stage8e_catalyst_explanation_v1"


def is_snapshot_stage8e_complete(payload: Optional[dict]) -> bool:
    """Return True when the snapshot carries the Stage 8E catalyst explanation contract.

    Checks:
      1. The stage8e_catalyst_explanation_contract_version marker is present and current.
      2. Every held card whose sec_catalyst_evidence has sec_catalyst_found=True
         also carries event_summary (the primary Stage 8E enrichment field).

    Accepts either:
    - A full snapshot payload (with current_holdings array), OR
    - A slim dict from _fetch_latest_intel_snapshot with a pre-computed
      ``stage8e_contract_complete`` boolean (flat column, Migration 024).

    Returns False (triggers deterministic republish) for:
    - Missing or wrong contract marker
    - Any card with sec_catalyst_found=True but missing event_summary
    - Malformed or empty payloads (fail-closed)
    """
    if not payload:
        return False
    # Fast path: pre-computed boolean from flat DB column (Migration 024) or
    # slim republisher dict with stage8e_contract_complete key.
    if "stage8e_contract_complete" in payload:
        return bool(payload["stage8e_contract_complete"])
    if (
        payload.get("stage8e_catalyst_explanation_contract_version")
        != STAGE8E_CATALYST_EXPLANATION_CONTRACT_VERSION
    ):
        return False

    holdings = payload.get("current_holdings") or []
    for card in holdings:
        ddp = card.get("detail_drawer_payload") or {}
        ex = ddp.get("evidence_explanation") or {}
        cat = ex.get("sec_catalyst_evidence") or {}
        if cat.get("sec_catalyst_found") and "event_summary" not in cat:
            return False

    return True
