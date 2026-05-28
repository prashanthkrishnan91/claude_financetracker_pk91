"""Stage 9K diagnostic helper — pure functions for artifact-readiness gate classification.

Diagnostic-only module. No DB calls, no provider calls, no LLM. No writes.

Public API:
  classify_stage9k_gate_failure(payload) -> (gate_passed, reason_failed)
  build_stage9k_ticker_entry(ticker, flag_enabled, active_rows, all_rows) -> dict

Used by the POST /diagnostics/finance-intel/etf-stage9k-artifact-readiness endpoint to
explain per-ticker why VTI/SCHD/VXUS (or any ETF) remain "not yet wired" after Stage 9K
deploy even when intel_v3_etf_nport_evidence_enabled=True.

The gate logic mirrors _get_etf_nport_provider_outputs() and _nport_is_holdings_ready()
exactly — any drift here is a bug.

safe_for_decision and synthesis_ready are never set by this module.
"""
from __future__ import annotations

from typing import Optional

_NPORT_SKILL_PACK: str = "etf_sec_nport_holdings_evidence_v1"
_STAGE9K_DIAG_DEFAULT_TICKERS: tuple[str, ...] = ("VTI", "SCHD", "VXUS")
_STAGE9K_DIAG_MAX_TICKERS: int = 20


def classify_stage9k_gate_failure(payload: dict) -> tuple[bool, str]:
    """Return (gate_passed, reason_failed) for an NPORT artifact payload dict.

    Mirrors the holdings-ready gate applied in both:
      - intel_v3_service._get_etf_nport_provider_outputs()
      - etf_intelligence_classifier_v1._nport_is_holdings_ready()

    reason_failed is "" when gate_passed=True.
    All five criteria must pass; all failures are reported together.

    Args:
        payload: the raw JSONB payload dict from the research_artifacts row.
    """
    reasons: list[str] = []

    fetch_status = (payload.get("fetch_status") or "").lower()
    if fetch_status != "success":
        reasons.append(
            f"fetch_status={fetch_status!r} (must be 'success')"
        )

    holdings_count = int(payload.get("holdings_count") or 0)
    if holdings_count < 5:
        reasons.append(f"holdings_count={holdings_count} (must be >=5)")

    if not bool(payload.get("weights_available", False)):
        reasons.append("weights_available=False or missing")

    if not bool(payload.get("report_period_date")):
        reasons.append("report_period_date missing or null")

    cq = (payload.get("coverage_quality") or "").lower()
    if "partial" in cq:
        reasons.append(f"coverage_quality contains 'partial': {cq!r}")
    elif "suspicious" in cq:
        reasons.append(f"coverage_quality contains 'suspicious': {cq!r}")

    if reasons:
        return False, "; ".join(reasons)
    return True, ""


def build_stage9k_ticker_entry(
    ticker: str,
    flag_enabled: bool,
    active_rows: list[dict],
    all_rows: list[dict],
) -> dict:
    """Build the per-ticker Stage 9K readiness diagnostic entry.

    Covers all five failure modes:
      1. flag disabled at runtime
      2. no artifact row at all
      3. artifact exists but is_active=False
      4. artifact exists and is_active=True but payload fails gate
      5. gate passes (wired correctly)

    Args:
        ticker:        uppercase ticker symbol.
        flag_enabled:  value of intel_v3_etf_nport_evidence_enabled at call time.
        active_rows:   rows from research_artifacts filtered by is_active=True (mirrors production query).
        all_rows:      rows from research_artifacts without is_active filter (detects inactive rows).

    Returns a dict with: ticker, flag_enabled, artifact_found, active_artifact_found,
    skill_pack, artifact_type, is_active, fetch_status, holdings_count, weights_available,
    report_period_date, coverage_quality, gate_passed, reason_failed.
    """
    t_upper = ticker.upper()
    active_row: Optional[dict] = next(
        (r for r in active_rows if (r.get("ticker") or "").upper() == t_upper), None
    )
    any_row: Optional[dict] = next(
        (r for r in all_rows if (r.get("ticker") or "").upper() == t_upper), None
    )

    artifact_found = any_row is not None
    active_artifact_found = active_row is not None

    # Use the active row if present (matches what production would see);
    # fall back to any row to show why it was skipped.
    row = active_row or any_row

    if row is None:
        return {
            "ticker": t_upper,
            "flag_enabled": flag_enabled,
            "artifact_found": False,
            "active_artifact_found": False,
            "skill_pack": None,
            "artifact_type": None,
            "is_active": None,
            "fetch_status": None,
            "holdings_count": None,
            "weights_available": None,
            "report_period_date": None,
            "coverage_quality": None,
            "gate_passed": False,
            "reason_failed": (
                "no_artifact_row: no research_artifacts row found for this "
                "user_id / ticker / skill_pack combination — "
                "NPORT artifact has not been produced yet"
            ),
        }

    payload = row.get("payload") or {}
    gate_passed, gate_reason = classify_stage9k_gate_failure(payload)

    if not active_artifact_found:
        # Row exists but is_active=False — production query skips it.
        combined_reason = "is_active=False: row exists but is inactive — production query filters to is_active=True only"
        if gate_reason:
            combined_reason += f"; payload also fails gate: {gate_reason}"
        return {
            "ticker": t_upper,
            "flag_enabled": flag_enabled,
            "artifact_found": True,
            "active_artifact_found": False,
            "skill_pack": row.get("skill_pack"),
            "artifact_type": row.get("artifact_type"),
            "is_active": row.get("is_active"),
            "fetch_status": payload.get("fetch_status"),
            "holdings_count": payload.get("holdings_count"),
            "weights_available": payload.get("weights_available"),
            "report_period_date": payload.get("report_period_date"),
            "coverage_quality": payload.get("coverage_quality"),
            "gate_passed": False,
            "reason_failed": combined_reason,
        }

    return {
        "ticker": t_upper,
        "flag_enabled": flag_enabled,
        "artifact_found": True,
        "active_artifact_found": True,
        "skill_pack": row.get("skill_pack"),
        "artifact_type": row.get("artifact_type"),
        "is_active": row.get("is_active"),
        "fetch_status": payload.get("fetch_status"),
        "holdings_count": payload.get("holdings_count"),
        "weights_available": payload.get("weights_available"),
        "report_period_date": payload.get("report_period_date"),
        "coverage_quality": payload.get("coverage_quality"),
        "gate_passed": gate_passed,
        "reason_failed": gate_reason if not gate_passed else None,
    }
