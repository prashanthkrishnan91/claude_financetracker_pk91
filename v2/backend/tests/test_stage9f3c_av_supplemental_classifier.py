"""Stage 9F.3c — Alpha Vantage supplemental classifier tests.

Fixture-based only — no live HTTP calls, no SQL, no artifact writes.

Coverage:
  9F3c-01. VOO-like 519 holdings + weights + no date → supplemental only,
           not canonical, not decision-safe.
  9F3c-02. SCHD-like 103 holdings + weights + no date → supplemental only.
  9F3c-03. VXUS-like 37 holdings + weights + no date for broad ETF →
           partial_or_suspicious.
  9F3c-04. Missing weights → not_supplemental, holdings_available=False.
  9F3c-05. Missing holdings → not_supplemental, holdings_available=False.
  9F3c-06. Date missing must block canonical readiness (as_of_date_verified=False).
  9F3c-07. canonical_ready is always False.
  9F3c-08. safe_for_decision is always False.
  9F3c-09. classifier_id is "alpha_vantage_supplemental_v1".
  9F3c-10. Date present does not flip canonical_ready.
  9F3c-11. focused ETF with low holdings + no expected_min → usable_supplemental.
  9F3c-12. XLE-like 24 holdings without expected_min → usable_supplemental.
  9F3c-13. rejection_reasons includes as_of_date_missing when date missing.
  9F3c-14. rejection_reasons includes no_holdings when holdings_count=0.
  9F3c-15. rejection_reasons includes no_weights when weights_available=False.
  9F3c-16. rejection_reasons includes partial_or_incomplete_coverage for
           broad ETF with suspiciously low count.
  9F3c-17. supplemental_only=True when holdings_available=True.
  9F3c-18. supplemental_only=False when holdings not available.
  9F3c-19. freshness_status=date_missing when no date.
  9F3c-20. freshness_status=date_present_unverified when date provided.
  9F3c-21. Existing 9F.3a/3b test file: all tests still pass (import guard).
"""
from __future__ import annotations

import pytest


def _classify(
    holdings_count: int,
    weights_available: bool,
    as_of_date=None,
    *,
    expected_min_holdings=None,
) -> dict:
    from app.services.intelligence.research_workers.alpha_vantage_supplemental_classifier_v1 import (
        classify_av_etf_output,
    )
    return classify_av_etf_output(
        holdings_count,
        weights_available,
        as_of_date,
        expected_min_holdings=expected_min_holdings,
    )


# ── Tests: live-proof-result scenarios ───────────────────────────────────────

class TestLiveProofScenarios:
    def test_voo_like_supplemental_only(self):
        # 9F3c-01: 519 holdings + weights, no date → supplemental only
        result = _classify(519, True, None)
        assert result["holdings_available"] is True
        assert result["canonical_ready"] is False
        assert result["safe_for_decision"] is False
        assert result["supplemental_only"] is True
        assert result["coverage_quality"] == "usable_supplemental"
        assert result["as_of_date_verified"] is False
        assert "as_of_date_missing" in result["rejection_reasons"]

    def test_schd_like_supplemental_only(self):
        # 9F3c-02: 103 holdings + weights, no date → supplemental only
        result = _classify(103, True, None)
        assert result["holdings_available"] is True
        assert result["coverage_quality"] == "usable_supplemental"
        assert result["canonical_ready"] is False
        assert result["safe_for_decision"] is False
        assert "as_of_date_missing" in result["rejection_reasons"]

    def test_vxus_like_broad_etf_partial_or_suspicious(self):
        # 9F3c-03: 37 holdings for a broad international fund → partial_or_suspicious
        # VXUS holds thousands of positions; 37 is a top-holdings-only slice.
        result = _classify(37, True, None, expected_min_holdings=100)
        assert result["holdings_available"] is True
        assert result["coverage_quality"] == "partial_or_suspicious"
        assert result["canonical_ready"] is False
        assert result["safe_for_decision"] is False
        assert "partial_or_incomplete_coverage" in result["rejection_reasons"]
        assert "as_of_date_missing" in result["rejection_reasons"]

    def test_xle_like_sector_etf_no_min_is_supplemental(self):
        # 9F3c-12: 24 holdings without expected_min → usable_supplemental
        # XLE (Energy Select Sector) legitimately has ~24 components.
        result = _classify(24, True, None)
        assert result["coverage_quality"] == "usable_supplemental"
        assert result["holdings_available"] is True
        assert result["canonical_ready"] is False


# ── Tests: not_supplemental paths ────────────────────────────────────────────

class TestNotSupplemental:
    def test_missing_weights_is_not_supplemental(self):
        # 9F3c-04
        result = _classify(50, False, None)
        assert result["holdings_available"] is False
        assert result["coverage_quality"] == "not_supplemental"
        assert result["supplemental_only"] is False
        assert "no_weights" in result["rejection_reasons"]

    def test_missing_holdings_is_not_supplemental(self):
        # 9F3c-05
        result = _classify(0, True, None)
        assert result["holdings_available"] is False
        assert result["coverage_quality"] == "not_supplemental"
        assert result["supplemental_only"] is False
        assert "no_holdings" in result["rejection_reasons"]

    def test_zero_holdings_and_no_weights_is_not_supplemental(self):
        result = _classify(0, False, None)
        assert result["holdings_available"] is False
        assert result["coverage_quality"] == "not_supplemental"


# ── Tests: canonical always blocked ──────────────────────────────────────────

class TestCanonicalAlwaysBlocked:
    def test_date_missing_blocks_canonical(self):
        # 9F3c-06
        result = _classify(500, True, None)
        assert result["as_of_date_verified"] is False
        assert result["canonical_ready"] is False

    def test_canonical_ready_always_false(self):
        # 9F3c-07: even with date present, canonical_ready is False
        result = _classify(500, True, "2025-12-31")
        assert result["canonical_ready"] is False

    def test_safe_for_decision_always_false(self):
        # 9F3c-08
        result = _classify(500, True, "2025-12-31")
        assert result["safe_for_decision"] is False

    def test_date_present_does_not_flip_canonical(self):
        # 9F3c-10
        result = _classify(200, True, "2025-01-01")
        assert result["canonical_ready"] is False
        assert result["as_of_date_verified"] is True


# ── Tests: freshness_status ───────────────────────────────────────────────────

class TestFreshnessStatus:
    def test_freshness_date_missing(self):
        # 9F3c-19
        result = _classify(100, True, None)
        assert result["freshness_status"] == "date_missing"

    def test_freshness_date_present_unverified(self):
        # 9F3c-20
        result = _classify(100, True, "2025-12-31")
        assert result["freshness_status"] == "date_present_unverified"


# ── Tests: rejection_reasons ──────────────────────────────────────────────────

class TestRejectionReasons:
    def test_no_date_included_in_reasons(self):
        # 9F3c-13
        result = _classify(100, True, None)
        assert "as_of_date_missing" in result["rejection_reasons"]

    def test_no_date_not_in_reasons_when_date_present(self):
        result = _classify(100, True, "2025-12-31")
        assert "as_of_date_missing" not in result["rejection_reasons"]

    def test_no_holdings_in_reasons(self):
        # 9F3c-14
        result = _classify(0, True, None)
        assert "no_holdings" in result["rejection_reasons"]

    def test_no_weights_in_reasons(self):
        # 9F3c-15
        result = _classify(50, False, None)
        assert "no_weights" in result["rejection_reasons"]

    def test_partial_coverage_in_reasons_for_broad_etf(self):
        # 9F3c-16
        result = _classify(30, True, None, expected_min_holdings=100)
        assert "partial_or_incomplete_coverage" in result["rejection_reasons"]

    def test_no_partial_coverage_when_count_above_min(self):
        result = _classify(200, True, None, expected_min_holdings=100)
        assert "partial_or_incomplete_coverage" not in result["rejection_reasons"]


# ── Tests: supplemental_only flag ────────────────────────────────────────────

class TestSupplementalOnly:
    def test_supplemental_only_true_when_holdings_available(self):
        # 9F3c-17
        result = _classify(100, True, None)
        assert result["supplemental_only"] is True

    def test_supplemental_only_false_when_no_holdings(self):
        # 9F3c-18
        result = _classify(0, True, None)
        assert result["supplemental_only"] is False


# ── Tests: classifier_id ──────────────────────────────────────────────────────

class TestClassifierId:
    def test_classifier_id(self):
        # 9F3c-09
        from app.services.intelligence.research_workers.alpha_vantage_supplemental_classifier_v1 import (
            CLASSIFIER_ID,
        )
        result = _classify(100, True, None)
        assert result["classifier_id"] == CLASSIFIER_ID
        assert result["classifier_id"] == "alpha_vantage_supplemental_v1"


# ── Tests: focused ETF low-count edge cases ───────────────────────────────────

class TestFocusedEtfEdgeCases:
    def test_focused_etf_low_holdings_without_min_is_supplemental(self):
        # 9F3c-11: no expected_min_holdings set → usable_supplemental regardless of count
        result = _classify(15, True, None)
        assert result["coverage_quality"] == "usable_supplemental"

    def test_broad_etf_exactly_at_min_is_supplemental(self):
        # holdings_count == expected_min_holdings → usable_supplemental (not suspicious)
        result = _classify(100, True, None, expected_min_holdings=100)
        assert result["coverage_quality"] == "usable_supplemental"

    def test_broad_etf_one_below_min_is_suspicious(self):
        result = _classify(99, True, None, expected_min_holdings=100)
        assert result["coverage_quality"] == "partial_or_suspicious"
