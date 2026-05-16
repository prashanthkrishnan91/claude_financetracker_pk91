"""Evidence mapping version constant and staleness helpers.

When ReadOnlyEvidenceAdapter.load_cards() changes its trusted-signal synthesis
logic, bump EVIDENCE_MAPPING_VERSION so existing persisted snapshots (built with
the old mapping) are detected as stale and recertified deterministically.

Importable by adapter, snapshot_builder, and republisher without circular imports.
"""
from __future__ import annotations

from typing import Optional

EVIDENCE_MAPPING_VERSION = "analyst_verdict_synthesis_v1"


def is_snapshot_mapping_current(payload: Optional[dict]) -> bool:
    """Return True when the payload carries the current evidence mapping version."""
    if not payload:
        return False
    return payload.get("evidence_mapping_version") == EVIDENCE_MAPPING_VERSION


def get_snapshot_mapping_version(payload: Optional[dict]) -> Optional[str]:
    """Return the evidence_mapping_version from a snapshot payload, or None if absent."""
    if not payload:
        return None
    return payload.get("evidence_mapping_version") or None
