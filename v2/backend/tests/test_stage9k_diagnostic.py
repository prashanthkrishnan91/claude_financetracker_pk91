"""Stage 9K artifact-readiness diagnostic — unit tests.

Tests classify_stage9k_gate_failure() and build_stage9k_ticker_entry() for all
failure modes:
  1. no_artifact_row
  2. is_active=False
  3. payload gate fail (each individual criterion)
  4. gate passed

Fixture-based only.  No IO, no DB, no LLM, no provider calls.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.etf_stage9k_diagnostic_helper import (
    _NPORT_SKILL_PACK,
    _STAGE9K_DIAG_DEFAULT_TICKERS,
    _STAGE9K_DIAG_MAX_TICKERS,
    classify_stage9k_gate_failure,
    build_stage9k_ticker_entry,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _good_payload() -> dict:
    return {
        "fetch_status": "success",
        "holdings_count": 10,
        "weights_available": True,
        "report_period_date": "2024-09-30",
        "coverage_quality": "full",
    }


def _row(ticker: str, is_active: bool = True, payload: dict | None = None) -> dict:
    return {
        "ticker": ticker,
        "skill_pack": _NPORT_SKILL_PACK,
        "artifact_type": "etf_fund_note",
        "is_active": is_active,
        "payload": payload if payload is not None else _good_payload(),
    }


# ── classify_stage9k_gate_failure ─────────────────────────────────────────────


class TestClassifyStage9kGateFailure:
    def test_all_good_passes(self):
        gate_passed, reason = classify_stage9k_gate_failure(_good_payload())
        assert gate_passed is True
        assert reason == ""

    def test_fetch_status_not_success(self):
        p = {**_good_payload(), "fetch_status": "no_nport_filing"}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "fetch_status" in reason
        assert "no_nport_filing" in reason

    def test_fetch_status_empty_string(self):
        p = {**_good_payload(), "fetch_status": ""}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "fetch_status" in reason

    def test_fetch_status_missing(self):
        p = {k: v for k, v in _good_payload().items() if k != "fetch_status"}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "fetch_status" in reason

    def test_holdings_count_too_low(self):
        p = {**_good_payload(), "holdings_count": 4}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "holdings_count=4" in reason

    def test_holdings_count_zero(self):
        p = {**_good_payload(), "holdings_count": 0}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "holdings_count=0" in reason

    def test_holdings_count_exactly_five_passes(self):
        p = {**_good_payload(), "holdings_count": 5}
        gate_passed, _ = classify_stage9k_gate_failure(p)
        assert gate_passed is True

    def test_weights_available_false(self):
        p = {**_good_payload(), "weights_available": False}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "weights_available" in reason

    def test_weights_available_missing(self):
        p = {k: v for k, v in _good_payload().items() if k != "weights_available"}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "weights_available" in reason

    def test_report_period_date_missing(self):
        p = {k: v for k, v in _good_payload().items() if k != "report_period_date"}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "report_period_date" in reason

    def test_report_period_date_none(self):
        p = {**_good_payload(), "report_period_date": None}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "report_period_date" in reason

    def test_coverage_quality_partial_blocks(self):
        p = {**_good_payload(), "coverage_quality": "partial_holdings"}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "partial" in reason

    def test_coverage_quality_suspicious_blocks(self):
        p = {**_good_payload(), "coverage_quality": "suspicious_weights"}
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "suspicious" in reason

    def test_coverage_quality_empty_passes(self):
        p = {**_good_payload(), "coverage_quality": ""}
        gate_passed, _ = classify_stage9k_gate_failure(p)
        assert gate_passed is True

    def test_coverage_quality_full_passes(self):
        p = {**_good_payload(), "coverage_quality": "full"}
        gate_passed, _ = classify_stage9k_gate_failure(p)
        assert gate_passed is True

    def test_multiple_failures_reported_together(self):
        p = {
            "fetch_status": "error",
            "holdings_count": 0,
            "weights_available": False,
            "report_period_date": None,
            "coverage_quality": "partial",
        }
        gate_passed, reason = classify_stage9k_gate_failure(p)
        assert gate_passed is False
        assert "fetch_status" in reason
        assert "holdings_count" in reason
        assert "weights_available" in reason
        assert "report_period_date" in reason

    def test_empty_payload_reports_all_failures(self):
        gate_passed, reason = classify_stage9k_gate_failure({})
        assert gate_passed is False
        assert "fetch_status" in reason
        assert "holdings_count" in reason
        assert "weights_available" in reason
        assert "report_period_date" in reason


# ── build_stage9k_ticker_entry ────────────────────────────────────────────────


class TestBuildStage9kTickerEntry:
    def test_no_artifact_row(self):
        entry = build_stage9k_ticker_entry("VTI", True, [], [])
        assert entry["ticker"] == "VTI"
        assert entry["flag_enabled"] is True
        assert entry["artifact_found"] is False
        assert entry["active_artifact_found"] is False
        assert entry["gate_passed"] is False
        assert "no_artifact_row" in entry["reason_failed"]
        assert entry["fetch_status"] is None
        assert entry["holdings_count"] is None

    def test_inactive_row_only(self):
        row = _row("VTI", is_active=False)
        entry = build_stage9k_ticker_entry("VTI", True, [], [row])
        assert entry["artifact_found"] is True
        assert entry["active_artifact_found"] is False
        assert entry["is_active"] is False
        assert entry["gate_passed"] is False
        assert "is_active=False" in entry["reason_failed"]

    def test_inactive_row_also_fails_gate(self):
        bad_payload = {**_good_payload(), "fetch_status": "error", "holdings_count": 0}
        row = _row("VTI", is_active=False, payload=bad_payload)
        entry = build_stage9k_ticker_entry("VTI", True, [], [row])
        assert entry["gate_passed"] is False
        assert "is_active=False" in entry["reason_failed"]
        assert "fetch_status" in entry["reason_failed"]

    def test_active_row_passes_gate(self):
        row = _row("VTI", is_active=True)
        entry = build_stage9k_ticker_entry("VTI", True, [row], [row])
        assert entry["artifact_found"] is True
        assert entry["active_artifact_found"] is True
        assert entry["gate_passed"] is True
        assert entry["reason_failed"] is None
        assert entry["fetch_status"] == "success"
        assert entry["holdings_count"] == 10
        assert entry["weights_available"] is True
        assert entry["report_period_date"] == "2024-09-30"

    def test_active_row_fails_gate_fetch_status(self):
        bad = {**_good_payload(), "fetch_status": "no_nport_filing"}
        row = _row("SCHD", is_active=True, payload=bad)
        entry = build_stage9k_ticker_entry("SCHD", True, [row], [row])
        assert entry["gate_passed"] is False
        assert "fetch_status" in entry["reason_failed"]
        assert entry["active_artifact_found"] is True

    def test_active_row_fails_gate_holdings_count(self):
        bad = {**_good_payload(), "holdings_count": 3}
        row = _row("VXUS", is_active=True, payload=bad)
        entry = build_stage9k_ticker_entry("VXUS", True, [row], [row])
        assert entry["gate_passed"] is False
        assert "holdings_count=3" in entry["reason_failed"]

    def test_active_row_fails_gate_weights_unavailable(self):
        bad = {**_good_payload(), "weights_available": False}
        row = _row("VTI", is_active=True, payload=bad)
        entry = build_stage9k_ticker_entry("VTI", True, [row], [row])
        assert entry["gate_passed"] is False
        assert "weights_available" in entry["reason_failed"]

    def test_active_row_fails_gate_no_date(self):
        bad = {**_good_payload(), "report_period_date": None}
        row = _row("VTI", is_active=True, payload=bad)
        entry = build_stage9k_ticker_entry("VTI", True, [row], [row])
        assert entry["gate_passed"] is False
        assert "report_period_date" in entry["reason_failed"]

    def test_active_row_fails_gate_partial_coverage(self):
        bad = {**_good_payload(), "coverage_quality": "partial_coverage"}
        row = _row("VTI", is_active=True, payload=bad)
        entry = build_stage9k_ticker_entry("VTI", True, [row], [row])
        assert entry["gate_passed"] is False
        assert "partial" in entry["reason_failed"]

    def test_ticker_case_insensitive(self):
        row = _row("VTI", is_active=True)
        entry = build_stage9k_ticker_entry("vti", True, [row], [row])
        assert entry["ticker"] == "VTI"
        assert entry["gate_passed"] is True

    def test_flag_disabled_surfaced(self):
        entry = build_stage9k_ticker_entry("VTI", False, [], [])
        assert entry["flag_enabled"] is False
        assert entry["gate_passed"] is False

    def test_skill_pack_and_artifact_type_propagated(self):
        row = _row("VTI", is_active=True)
        entry = build_stage9k_ticker_entry("VTI", True, [row], [row])
        assert entry["skill_pack"] == _NPORT_SKILL_PACK
        assert entry["artifact_type"] == "etf_fund_note"

    def test_multiple_tickers_independent(self):
        vti_row = _row("VTI", is_active=True)
        schd_row = _row("SCHD", is_active=False)
        all_rows = [vti_row, schd_row]
        active_rows = [vti_row]

        vti = build_stage9k_ticker_entry("VTI", True, active_rows, all_rows)
        schd = build_stage9k_ticker_entry("SCHD", True, active_rows, all_rows)
        vxus = build_stage9k_ticker_entry("VXUS", True, active_rows, all_rows)

        assert vti["gate_passed"] is True
        assert schd["gate_passed"] is False
        assert "is_active=False" in schd["reason_failed"]
        assert vxus["artifact_found"] is False

    def test_safe_for_decision_never_set(self):
        row = _row("VTI", is_active=True)
        entry = build_stage9k_ticker_entry("VTI", True, [row], [row])
        assert "safe_for_decision" not in entry

    def test_synthesis_ready_never_set(self):
        row = _row("VTI", is_active=True)
        entry = build_stage9k_ticker_entry("VTI", True, [row], [row])
        assert "synthesis_ready" not in entry


# ── Module constants ──────────────────────────────────────────────────────────


class TestModuleConstants:
    def test_default_tickers_are_vti_schd_vxus(self):
        assert set(_STAGE9K_DIAG_DEFAULT_TICKERS) == {"VTI", "SCHD", "VXUS"}

    def test_max_tickers(self):
        assert _STAGE9K_DIAG_MAX_TICKERS == 20

    def test_skill_pack_matches_production(self):
        assert _NPORT_SKILL_PACK == "etf_sec_nport_holdings_evidence_v1"
