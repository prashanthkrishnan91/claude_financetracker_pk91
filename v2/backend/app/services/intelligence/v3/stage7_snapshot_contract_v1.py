"""Stage 7 snapshot explanation contract version and staleness helpers.

When the snapshot payload gains Stage 7 explanation fields
(detail_drawer_payload.evidence_explanation per held card), bump
STAGE7_EXPLANATION_CONTRACT_VERSION so old snapshots are detected as
lacking the explanation UI contract and recertified deterministically.

Pattern mirrors evidence_mapping_version_v1.py — importable by snapshot_builder
and republisher without circular imports.
"""
from __future__ import annotations

from typing import Optional

STAGE7_EXPLANATION_CONTRACT_VERSION = "stage7_explanation_v1"


def is_snapshot_stage7_current(payload: Optional[dict]) -> bool:
    """Return True when the payload carries the current Stage 7 explanation contract marker."""
    if not payload:
        return False
    return (
        payload.get("stage7_explanation_contract_version")
        == STAGE7_EXPLANATION_CONTRACT_VERSION
    )


def is_snapshot_stage7_complete(payload: Optional[dict]) -> bool:
    """Return True when the snapshot satisfies the full Stage 7 explanation contract:
      1. Carries the current Stage 7 contract version marker, AND
      2. All current_holdings cards with detail_drawer_payload have the
         evidence_explanation key (value may be None for governance-off cases).

    Accepts either a full snapshot payload (with current_holdings) or the slim
    dict produced by _fetch_latest_intel_snapshot, which pre-computes
    stage7_explanation_payload_present as a derived boolean.

    Returns False (triggers deterministic republish) for:
    - Missing or wrong contract marker
    - Any card with detail_drawer_payload missing the evidence_explanation key
    - Malformed or empty payloads (fail-closed → deterministic republish)
    """
    if not payload:
        return False
    if not is_snapshot_stage7_current(payload):
        return False

    # Slim republisher dict carries a pre-computed boolean — use it directly.
    if "stage7_explanation_payload_present" in payload:
        return bool(payload["stage7_explanation_payload_present"])

    # Full payload path: verify structural presence of evidence_explanation key.
    holdings = payload.get("current_holdings") or []
    if not holdings:
        # Empty portfolio — version marker alone is sufficient; no cards to check.
        return True

    for card in holdings:
        ddp = card.get("detail_drawer_payload")
        if isinstance(ddp, dict) and "evidence_explanation" not in ddp:
            return False

    return True


def get_snapshot_stage7_version(payload: Optional[dict]) -> Optional[str]:
    """Return the stage7_explanation_contract_version from a snapshot payload, or None."""
    if not payload:
        return None
    return payload.get("stage7_explanation_contract_version") or None
