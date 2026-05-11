"""Deploy v1 Intel adapter — reads Intel v3 snapshot output read-only.

Translates certified Intel v3 visible card data into DeployPlanInput objects
for the Deploy translation layer.

Contract:
  - Reads Intel v3 snapshot cards read-only.
  - Does not call legacy RecommendationService or intel_v2 paths.
  - Does not mutate any Intel output field.
  - Does not derive new Buy/Hold/Trim/Sell decisions.
  - PriceBand context is passed through as supporting info only (not decision authority).
  - No IO, no LLM, no DB calls.
"""
from __future__ import annotations

from typing import Any

from .deploy_contracts import DeployActionSource, DeployPlanInput

# Evidence bands that indicate missing/insufficient evidence.
_MISSING_EVIDENCE_BANDS = frozenset({"THIN"})


def build_deploy_inputs_from_snapshot(snapshot: dict[str, Any]) -> list[DeployPlanInput]:
    """Convert Intel v3 snapshot current_holdings into DeployPlanInput objects.

    Reads the snapshot payload read-only. Does not mutate any Intel field.
    Returns one DeployPlanInput per card in current_holdings.

    Args:
        snapshot: Intel v3 snapshot payload dict (as returned by build_snapshot).

    Returns:
        List of DeployPlanInput objects — one per card.
    """
    snapshot_id = snapshot.get("snapshot_id", "")
    run_id = snapshot.get("run_id", "")
    is_stale = bool(snapshot.get("is_stale", False))
    cards = snapshot.get("current_holdings") or []

    inputs = []
    for card in cards:
        ticker = card.get("ticker") or "UNKNOWN"
        intel_action = (card.get("action") or "HOLD").upper()
        intel_conviction = (card.get("conviction") or "LOW").upper()
        intel_evidence_band = (card.get("evidence_band") or "THIN").upper()

        # Missing evidence: THIN evidence band.
        has_missing_evidence = intel_evidence_band in _MISSING_EVIDENCE_BANDS

        # Weak evidence: THIN band with LOW conviction.
        has_weak_evidence = (
            intel_evidence_band in _MISSING_EVIDENCE_BANDS
            and intel_conviction == "LOW"
        )

        # Blocked: check flags list for portfolio-fit-blocked indicators.
        flags = card.get("flags") or []
        is_blocked = any("blocked" in str(f).lower() for f in flags)

        # PriceBand context from detail_drawer_payload — supporting info only.
        drawer = card.get("detail_drawer_payload") or {}
        price_context_label = drawer.get("price_context")  # may be None

        inputs.append(DeployPlanInput(
            ticker=ticker,
            intel_action=intel_action,
            intel_conviction=intel_conviction,
            intel_evidence_band=intel_evidence_band,
            intel_snapshot_id=snapshot_id,
            intel_run_id=run_id,
            has_missing_evidence=has_missing_evidence,
            has_stale_evidence=is_stale,
            has_weak_evidence=has_weak_evidence,
            is_blocked=is_blocked,
            price_context_label=price_context_label,
            action_source=DeployActionSource.INTEL_V3,
        ))

    return inputs
