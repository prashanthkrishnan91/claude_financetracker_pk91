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
from .evidence_mapping_version_v1 import EVIDENCE_MAPPING_VERSION
from .intel_context_adapter_v1 import build_intel_context
from .stage7_snapshot_contract_v1 import STAGE7_EXPLANATION_CONTRACT_VERSION
from .stage8e_catalyst_explanation_contract_v1 import STAGE8E_CATALYST_EXPLANATION_CONTRACT_VERSION
from .stage8f_filing_type_contract_v1 import STAGE8F_FILING_TYPE_CONTRACT_VERSION

_SCHEMA_VERSION = "v3.1"

# Evidence bands that indicate source-linked analyst evidence was present and scored.
_SOURCE_VALIDATED_BANDS: frozenset = frozenset({AxisBand.OK, AxisBand.STRONG})

# Position categories that are too generic to drive lens selection — resolve to "stock"
# for held positions. Known ETF/commodity tickers still win via ticker-map override inside
# compose_asset_intelligence(), so passing "stock" for GLD/VTI is safe.
_AMBIGUOUS_POSITION_CATEGORIES: frozenset[str] = frozenset({
    "", "other", "unknown", "n/a", "none",
})


def _resolve_intel_asset_type(category: str) -> str:
    if (category or "").lower().strip() in _AMBIGUOUS_POSITION_CATEGORIES:
        return "stock"
    return category

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


# Reasons for each non-"full" lineage status (never "not confirmed" text for
# an assessed/positive state — these are strictly about MISSING references).
_LINEAGE_STATUS_REASON: dict[str, str] = {
    "missing": "No source references recorded for this run — lineage not established.",
    "partial": "Some but not all decision-influencing outputs for this ticker carry a source reference.",
    "unknown": "Source lineage could not be re-verified for this holding.",
}
_REVIEW_STATUS_REASON: dict[str, str] = {
    "failed": "A required conflict review failed for this ticker — shown without successful conflict reconciliation.",
    "pending": "A required conflict review is still pending for this ticker.",
    "unknown": "Conflict-review status could not be re-verified for this holding.",
}


def _build_source_pack_status(
    decision: DecisionOutputV3,
    *,
    lineage_status: Optional[str] = None,
    review_status: Optional[str] = None,
) -> dict:
    """Compute honest source-pack / committee status.

    source_validated: intel_read had 1+ trusted signals AND (when trust
    information is available for this publication path) FULL source lineage
    across every decision-influencing output AND a conflict review that is
    exactly not-required or succeeded (never merely "not failed" — a still-
    pending required review is not validated either).

    ``lineage_status``/``review_status`` are None for callers that don't
    carry per-ticker trust information (legacy/non-distributed publication)
    — preserves the prior evidence-band-only behavior for them. Distributed
    session publication (and its read-time fail-closed overlay) always pass
    explicit values so a session with zero source references, a failed
    review, or unreadable trust state is never mislabeled "source_validated"
    purely because evidence_band is STRONG/PARTIAL.
    """
    if lineage_status is None and review_status is None:
        if decision.evidence_quality in _SOURCE_VALIDATED_BANDS:
            return {"status": "source_validated"}
        ev_reason = decision.suppression_reasons.get("evidence_quality") or ""
        truth_reason = decision.suppression_reasons.get("truth_evidence_quality") or ""
        reason_text = ev_reason or truth_reason or "Source-linked evidence not yet available for this ticker."
        return {"status": "pending", "reason": reason_text}

    if review_status not in (None, "not_required", "succeeded"):
        return {
            "status": "pending",
            "reason": _REVIEW_STATUS_REASON.get(
                str(review_status), "Conflict-review status could not be re-verified for this holding.",
            ),
        }
    if lineage_status != "full":
        return {
            "status": "pending",
            "reason": _LINEAGE_STATUS_REASON.get(
                str(lineage_status), "No source references recorded for this run — lineage not established.",
            ),
        }
    if decision.evidence_quality in _SOURCE_VALIDATED_BANDS:
        return {"status": "source_validated"}

    ev_reason = decision.suppression_reasons.get("evidence_quality") or ""
    truth_reason = decision.suppression_reasons.get("truth_evidence_quality") or ""
    reason_text = ev_reason or truth_reason or "Source-linked evidence not yet available for this ticker."
    return {"status": "pending", "reason": reason_text}


def _build_evidence_explanation(gov: dict) -> dict:
    """Extract frontend-safe Stage 7 explanation fields from a governance result dict.

    Only safe, translated fields are included. Internal metric keys and raw diagnostic
    field names never reach this dict.
    """
    aux = gov.get("auxiliary_evidence_readiness") or {}
    return {
        "primary_evidence_status":          gov.get("primary_evidence_readiness", "MISSING"),
        "technical_signals_status":         aux.get("technical_signals", "MISSING"),
        "sentiment_status":                 aux.get("sentiment", "MISSING"),
        "conviction_cap_applied":           bool(gov.get("conviction_cap_applied", False)),
        "conviction_cap_reason":            gov.get("conviction_cap_reason"),
        "safe_for_visible_decision":        bool(gov.get("safe_for_visible_decision", False)),
        "safe_for_visible_decision_reason": gov.get("safe_for_visible_decision_reason", ""),
        "governance_priority":              gov.get("governance_priority_applied", "unknown"),
        "corroboration_gap":                bool(gov.get("corroboration_gap", False)),
        "action_blocks":                    list(gov.get("action_blocks_applied") or []),
    }


# Synthetic evidence explanation: maps AxisBand to frontend-readable primary_evidence_status.
_BAND_TO_PRIMARY_STATUS: dict[str, str] = {
    AxisBand.STRONG.value:     "READY",
    AxisBand.OK.value:         "LIMITED",
    AxisBand.THIN.value:       "INSUFFICIENT",
    AxisBand.SUPPRESSED.value: "SUPPRESSED",
}

# Conviction cap reason per band (used when Stage 6 is off).
_BAND_TO_CAP_REASON: dict[str, str] = {
    AxisBand.OK.value:         "ok_cap_medium",
    AxisBand.THIN.value:       "band_thin",
    AxisBand.SUPPRESSED.value: "suppressed",
}


def _build_synthetic_evidence_explanation(decision: DecisionOutputV3) -> dict:
    """Build evidence_explanation from decision signals when Stage 6 governance is inactive.

    Synthesizes readiness fields from the final evidence_quality band that decide()
    already computed. Technical signals and sentiment are MISSING because per-axis
    coverage data is only available through the Stage 6 evidence shadow.

    This ensures the drawer shows structured evidence sections (supporting lanes,
    incomplete lanes, conviction-cap reasoning) instead of generic fallback text,
    even when INTEL_V3_EVIDENCE_AWARE_POLICY_ENABLED is off.
    """
    band = decision.evidence_quality
    band_val = band.value

    primary_status = _BAND_TO_PRIMARY_STATUS.get(band_val, "MISSING")
    cap_applied = band in (AxisBand.OK, AxisBand.THIN, AxisBand.SUPPRESSED)
    cap_reason = _BAND_TO_CAP_REASON.get(band_val) if cap_applied else None
    safe = band in (AxisBand.STRONG, AxisBand.OK)
    # Corroboration gap is true unless evidence is STRONG (single-axis discipline)
    corroboration_gap = band != AxisBand.STRONG

    return {
        "primary_evidence_status":          primary_status,
        "technical_signals_status":         "MISSING",
        "sentiment_status":                 "MISSING",
        "conviction_cap_applied":           cap_applied,
        "conviction_cap_reason":            cap_reason,
        "safe_for_visible_decision":        safe,
        "safe_for_visible_decision_reason": "",
        "governance_priority":              "governance_inactive",
        "corroboration_gap":                corroboration_gap,
        "action_blocks":                    list(decision.blockers),
    }


def _build_held_card(
    *,
    decision: DecisionOutputV3,
    card_meta: dict[str, Any],
    snapshot_id: str,
    run_id: str,
    valuation_context: Optional[dict] = None,
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

    # Trust-contract signals (distributed session publication only — absent
    # for legacy/non-distributed card_metas, which keeps the prior
    # evidence-band-only committee behavior via lineage_status=None).
    lineage_status = card_meta.get("session_lineage_status")
    conflict_review_status = card_meta.get("session_conflict_review_status")
    decision_constraints = card_meta.get("session_decision_constraints")
    trust_status = card_meta.get("session_trust_status")
    decision_bands = card_meta.get("session_decision_bands")

    gov_result = card_meta.get("governance_result")
    if gov_result:
        evidence_explanation = _build_evidence_explanation(gov_result)
    else:
        # Stage 6 inactive: synthesize evidence explanation from decision signals so the
        # drawer shows structured supporting/incomplete/cap sections instead of generic fallback.
        evidence_explanation = _build_synthetic_evidence_explanation(decision)
        # Patch technical/sentiment from Stage 5J research axis readiness when available.
        # Stage 6 inactive means the shadow was computed but governance was not applied —
        # we still use the readiness signals for UI display without altering any decision.
        _ra = card_meta.get("research_axis_readiness") or {}
        if _ra.get("technical_signals"):
            evidence_explanation["technical_signals_status"] = _ra["technical_signals"]
        if _ra.get("sentiment"):
            evidence_explanation["sentiment_status"] = _ra["sentiment"]

    # Stage 8D: inject SEC catalyst evidence display fields when available.
    # Safe display fields only — no raw backend codes, no decision authority.
    _ra = card_meta.get("research_axis_readiness") or {}
    if _ra.get("sec_catalyst_display") is not None:
        evidence_explanation["sec_catalyst_evidence"] = _ra["sec_catalyst_display"]

    # Stage 9I: asset intelligence context — explanatory only.
    # Existing visible action is preserved; composer output is context only.
    #
    # Extract Stage 9F provider outputs and portfolio upstream signals when present.
    # These are not yet populated (Stage 9F NPORT lane is off; portfolio overlap/cost
    # signals are not computed) — extracting them here means the wiring is ready for
    # when intel_v3_service.py begins writing these keys into card_meta.
    #
    # TODO(Stage 9F wiring): populate `etf_provider_outputs` and `etf_upstream_signals`
    #   in card_metas inside intel_v3_service.py once:
    #     (a) intel_v3_nport_evidence_enabled=True and NPORT holdings are available, and
    #     (b) portfolio-level overlap/cost/redundancy signals are computed per ticker.
    #   Key shape:
    #     etf_provider_outputs: {nport_output, av_output, fmp_output, canonical_etf_row}
    #     etf_upstream_signals: {is_redundant_etf, role_mismatch, structurally_inferior,
    #                            cost_elevated, concentration_risk}
    _provider_outputs = card_meta.get("etf_provider_outputs") or None
    _upstream_signals = card_meta.get("etf_upstream_signals") or None
    _portfolio_current_pct = card_meta.get("portfolio_current_pct")
    asset_intel_ctx = build_intel_context(
        ticker=card_meta.get("ticker", ""),
        asset_type=_resolve_intel_asset_type(card_meta.get("category", "stock")),
        portfolio_fit_raw=decision.portfolio_fit.value,
        evidence_quality_raw=decision.evidence_quality.value,
        existing_action=action,
        portfolio_current_pct=_portfolio_current_pct,
        provider_outputs=_provider_outputs,
        upstream_signals=_upstream_signals,
    )

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
            # Build 3 PR 2B — plain-English valuation context (None when suppressed).
            "valuation_context":    valuation_context,
            # Source-pack / committee status — computed from real evidence
            # quality AND (when available) real source lineage + conflict
            # review outcome, never evidence_band alone.
            "committee":            _build_source_pack_status(
                decision,
                lineage_status=lineage_status,
                review_status=conflict_review_status,
            ),
            # Stage 7C — evidence explanation for plain-English UI.
            # Always non-None: Stage 6 active → real governance result; Stage 6 off → synthetic.
            "evidence_explanation": evidence_explanation,
            # Stage 9I — asset intelligence context from composer.
            # Explanatory only; never overrides visible action authority.
            "asset_intelligence_context": asset_intel_ctx,
            # Run-trust contract signals (distributed sessions only; None/[]
            # when this card_meta carries no session trust info). Kept
            # separate from `blockers`/`flags` so the drawer can show source
            # lineage, price-context and portfolio-policy limitations without
            # collapsing every nonempty blocker into "Evidence blocked".
            # lineage_status is one of full/partial/missing/unknown — never a
            # bare boolean, so "one axis had a reference" can't read as
            # "everything that fed the decision is sourced".
            "source_lineage": (
                {"status": lineage_status, "has_source_refs": lineage_status == "full"}
                if lineage_status is not None else None
            ),
            "conflict_review_status": conflict_review_status,
            "decision_constraints": (
                list(decision_constraints) if decision_constraints is not None else None
            ),
            "trust_status": trust_status,
            "decision_bands": decision_bands,
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
    valuation_context_map: Optional[dict[str, Optional[dict]]] = None,
) -> dict[str, Any]:
    """Build a complete IntelV3Snapshot payload.

    Args:
        run_id:               Run ID that produced these decisions.
        decisions:            List of DecisionOutputV3 from the v3 kernel.
        card_metas:           Parallel list of card metadata dicts (ticker, name, category...).
        source_health:        Optional source health summary.
        is_stale:             Whether this snapshot is considered stale.
        what_changed:         List of human-readable change notes since last run.
        warnings:             List of warning strings for the banner.
        valuation_context_map: Optional ticker→serialized-valuation-context map from
                              priceband_snapshot_context_v1. None when context disabled.

    Returns:
        Complete snapshot payload dict (to be stored as JSONB).
    """
    snapshot_id = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc).isoformat()

    # Build held cards.
    held_cards = []
    for decision, meta in zip(decisions, card_metas):
        ticker = meta.get("ticker", "")
        val_ctx = (
            valuation_context_map.get(ticker.upper())
            if valuation_context_map else None
        )
        card = _build_held_card(
            decision=decision,
            card_meta=meta,
            snapshot_id=snapshot_id,
            run_id=run_id,
            valuation_context=val_ctx,
        )
        held_cards.append(card)

    # Compute action_counts directly from cards — single source of truth.
    action_counts = dict(Counter(c["action"] for c in held_cards))

    # Evidence band counts.
    evidence_band_counts = dict(Counter(c["evidence_band"] for c in held_cards))

    # Source-pack validation counts — for observability and aggregate logging.
    _source_pack_counts = Counter(
        c["detail_drawer_payload"]["committee"]["status"] for c in held_cards
    )
    source_pack_validated_count = _source_pack_counts.get("source_validated", 0)
    source_pack_pending_count = _source_pack_counts.get("pending", 0)

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
        "source_pack_validated_count": source_pack_validated_count,
        "source_pack_pending_count":   source_pack_pending_count,
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
        "evidence_mapping_version": EVIDENCE_MAPPING_VERSION,
        "stage7_explanation_contract_version": STAGE7_EXPLANATION_CONTRACT_VERSION,
        "stage8e_catalyst_explanation_contract_version": STAGE8E_CATALYST_EXPLANATION_CONTRACT_VERSION,
        "stage8f_filing_type_contract_version": STAGE8F_FILING_TYPE_CONTRACT_VERSION,
    }
