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
    """Return True when the payload carries the current Stage 7 explanation contract."""
    if not payload:
        return False
    return (
        payload.get("stage7_explanation_contract_version")
        == STAGE7_EXPLANATION_CONTRACT_VERSION
    )


def get_snapshot_stage7_version(payload: Optional[dict]) -> Optional[str]:
    """Return the stage7_explanation_contract_version from a snapshot payload, or None."""
    if not payload:
        return None
    return payload.get("stage7_explanation_contract_version") or None
