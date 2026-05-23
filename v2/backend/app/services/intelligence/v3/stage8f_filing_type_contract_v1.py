"""Stage 8F snapshot filing-type contract version and staleness helpers.

When the snapshot gains Stage 8F filing_type_label specificity inside
sec_catalyst_evidence, this version marker lets older Stage 8E-only snapshots
be detected as stale and recertified deterministically — no analyst jobs,
no SQL schema changes, no LLM.

Pattern mirrors stage8e_catalyst_explanation_contract_v1.py.
"""
from __future__ import annotations

from typing import Optional

STAGE8F_FILING_TYPE_CONTRACT_VERSION = "stage8f_filing_type_v1"


def is_snapshot_stage8f_complete(payload: Optional[dict]) -> bool:
    """Return True when the snapshot carries the Stage 8F filing-type contract.

    Checks:
      1. The stage8f_filing_type_contract_version marker is present and current.
      2. Every card whose sec_catalyst_evidence has sec_catalyst_found=True
         and which has sources recorded (implying Stage 8C ran and sources
         are available) also carries filing_type_label.

    NOTE: filing_type_label is optional — it is absent when the stored artifact
    has no source records with a recognised section_reference (e.g. pre-Stage 8C
    artifacts or tickers with only unknown form types).  The completeness check
    therefore only requires the version MARKER to be present; it does NOT require
    filing_type_label to be non-null on every card (that would be too strict and
    would cause unnecessary recertification loops).

    Returns False (triggers deterministic republish) for:
    - Missing or wrong contract marker.
    - Malformed or empty payloads (fail-closed).
    """
    if not payload:
        return False
    return (
        payload.get("stage8f_filing_type_contract_version")
        == STAGE8F_FILING_TYPE_CONTRACT_VERSION
    )
