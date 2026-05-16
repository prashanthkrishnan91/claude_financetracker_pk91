"""Snapshot builder — K3: assembles IntelV3Snapshot from per-card v3 decisions.

Builds the complete snapshot payload that gets persisted to intel_v3_snapshots
and served via GET /api/v1/intel/v3/snapshot.

Contract:
  - One snapshot per run (immutable after write).
  - action_counts is derived from card actions (same source of truth).
  - Cards include only BUY/HOLD/TRIM/SELL actions.
  - LLM committee deferred (not built here).
  - Opportunity Radar deferred (not built here).
  - legacy_path_used is always False for this path.

Pure function — no IO, DB, LLM.
Schema version: v3.1
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from .decision_contracts import ActionV3, AxisBand, ConvictionV3, DecisionOutputV3

_SCHEMA_VERSION = "v3.1"

# Evidence quality axis → visible evidence band.
# Maps the structural AxisBand (from decide()) to the display label.
# SUPPRESSED collapses to THIN so the UI never shows an internal axis label.
_EVIDENCE_QUALITY_TO_BAND: dict[str, str] = {
    AxisBand.STRONG.value:     "STRONG",
    AxisBand.OK.value:         "PARTIAL",
    AxisBand.THIN.value:       "THIN",
    AxisBand.SUPPRESSED.value: "THIN",
}

# FitBand → portfolio_fit display mapping.
_FIT_DISPLAY: dict[str, str] = {
    "UNDERWEIGHT": "Room to add",
    "ON_TARGET":   "On target",
    "OVERWEIGHT":  "Trim toward target",
    "BREACH":      "Overexposed",
    "BLOCKED":     "Not suitable",
    "UNKNOWN":     "Not assessed",
}

# RiskBand → risk_level display.
_RISK_DISPLAY: dict[str, str] = {
    "NONE":     "LOW",
    "LOW":      "LOW",
    "MEDIUM":   "MEDIUM",
    "HIGH":     "HIGH",
    "CRITICAL": "HIGH",
    "UNKNOWN":  "UNKNOWN",
}


def _build_held_card(
    *,
    decision: DecisionOutputV3,
    card_meta: dict[str, Any],
    snapshot_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Build a single held-card payload from a v3 decision + original card metadata."""
    action = decision.action.value
    conviction = decision.conviction.value
    evidence_band = _EVIDENCE_QUALITY_TO_BAND.get(decision.evidence_quality.value, "THIN")
    portfolio_fit = _FIT_DISPLAY.get(decision.portfolio_fit.value, "Not assessed")
    risk_level = _RISK_DISPLAY.get(decision.risk_band.value, "UNKNOWN")

    # Build why_text from rationale — plain English, no raw metric keys.
    why_text = decision.rationale_plain_english
    risk_text = decision.why_not_now
    action_text = {
        "BUY":  "Add to this position if Deploy has room.",
        "HOLD": "Hold current position — no new capital priority.",
        "TRIM": "Reduce position toward target allocation.",
        "SELL": "Exit or significantly reduce this position.",
    }.get(action, "Maintain current position.")
    what_would_change_view = decision.why_not_now

    evidence_text = {
        "STRONG": "Multiple independent signals confirm this view.",
        "PARTIAL": "Some evidence is available; gaps noted where present.",
        "THIN":    "Limited data available — view may change with more information.",
    }.get(evidence_band, "Evidence quality not assessed.")

    fit_text = portfolio_fit

    # thesis_state: derived from card_meta or default intact.
    thesis_state = card_meta.get("thesis_state") or "intact"

    return {
        "ticker":              card_meta.get("ticker", ""),
        "name":                card_meta.get("name", card_meta.get("ticker", "")),
        "asset_type":          card_meta.get("category", "stock"),
        "action":              action,
        "conviction":          conviction,
        "evidence_band":       evidence_band,
        "portfolio_fit":       portfolio_fit,
        "risk_level":          risk_level,
        "thesis_state":        thesis_state,
        "why_text":            why_text,
        "risk_text":           risk_text,
        "action_text":         action_text,
        "what_would_change_view": what_would_change_view,
        "fit_text":            fit_text,
        "evidence_text":       evidence_text,
        "flags":               list(decision.blockers),
        "source_snapshot_id":  snapshot_id,
        "source_run_id":       run_id,
        "updated_at":          datetime.now(timezone.utc).isoformat(),
        "detail_drawer_payload": {
            "rationale":            decision.rationale_plain_english,
            "why_now":              decision.why_now,
            "why_not_now":          decision.why_not_now,
            "evidence_band":        evidence_band,
            "evidence_quality":     decision.evidence_quality.value,
            "attractiveness":       decision.attractiveness.value,
            "price_context":        decision.price_context.value,
            "portfolio_fit_raw":    decision.portfolio_fit.value,
            "risk_band":            decision.risk_band.value,
            "blockers":             list(decision.blockers),
            "suppression_reasons":  dict(decision.suppression_reasons),
            "schema_version":       decision.schema_version,
            # Committee deferred.
            "committee":            {"status": "deferred", "reason": "Source pack not yet validated."},
        },
    }


def build_snapshot(
    *,
    run_id: str,
    decisions: list[DecisionOutputV3],
    card_metas: list[dict[str, Any]],
    source_health: Optional[dict] = None,
    is_stale: bool = False,
    what_changed: Optional[list[str]] = None,
    warnings: Optional[list[str]] = None,
    diagnostics: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a complete IntelV3Snapshot payload.

    Args:
        run_id:        Run ID that produced these decisions.
        decisions:     List of DecisionOutputV3 from the v3 kernel.
        card_metas:    Parallel list of card metadata dicts (ticker, name, category...).
        source_health: Optional source health summary.
        is_stale:      Whether this snapshot is considered stale.
        what_changed:  List of human-readable change notes since last run.
        warnings:      List of warning strings for the banner.

    Returns:
        Complete snapshot payload dict (to be stored as JSONB).
    """
    snapshot_id = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc).isoformat()

    # Build held cards.
    held_cards = []
    for decision, meta in zip(decisions, card_metas):
        card = _build_held_card(
            decision=decision,
            card_meta=meta,
            snapshot_id=snapshot_id,
            run_id=run_id,
        )
        held_cards.append(card)

    # Compute action_counts directly from cards — single source of truth.
    action_counts = dict(Counter(c["action"] for c in held_cards))

    # Evidence band counts.
    evidence_band_counts = dict(Counter(c["evidence_band"] for c in held_cards))

    # Conviction counts.
    conviction_counts = dict(Counter(c["conviction"] for c in held_cards))

    # Best buys: BUY cards sorted by conviction (HIGH > MEDIUM > LOW).
    conviction_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    best_buys = sorted(
        [c for c in held_cards if c["action"] == "BUY"],
        key=lambda c: conviction_order.get(c["conviction"], 9),
    )

    # Trim/sell desk: TRIM + SELL cards.
    trim_sell_desk = [c for c in held_cards if c["action"] in {"TRIM", "SELL"}]

    # Portfolio command center summary.
    total = len(held_cards)
    portfolio_command_center = {
        "total_holdings":  total,
        "buy_count":       action_counts.get("BUY", 0),
        "hold_count":      action_counts.get("HOLD", 0),
        "trim_count":      action_counts.get("TRIM", 0),
        "sell_count":      action_counts.get("SELL", 0),
        "high_conviction": conviction_counts.get("HIGH", 0),
        "thin_evidence":   evidence_band_counts.get("THIN", 0),
        "source_health":   source_health or {"status": "not_assessed"},
    }

    return {
        "schema_version":           _SCHEMA_VERSION,
        "snapshot_id":              snapshot_id,
        "run_id":                   run_id,
        "generated_at":             generated_at,
        "is_stale":                 is_stale,
        "source_health":            source_health or {"status": "not_assessed"},
        "portfolio_command_center": portfolio_command_center,
        "action_counts":            action_counts,
        "evidence_band_counts":     evidence_band_counts,
        "conviction_counts":        conviction_counts,
        "best_buys":                best_buys,
        "trim_sell_desk":           trim_sell_desk,
        "current_holdings":         held_cards,
        "opportunity_radar_preview": {
            "status": "deferred",
            "reason":  "Opportunity Radar launches after held-position spine is stable.",
        },
        "what_changed":   what_changed or [],
        "warnings":       warnings or [],
        "legacy_path_used": False,
        "diagnostics":    diagnostics,
    }
