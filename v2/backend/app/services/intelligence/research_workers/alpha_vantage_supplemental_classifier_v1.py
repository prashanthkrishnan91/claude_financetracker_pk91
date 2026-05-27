"""Stage 9F.3c — Alpha Vantage ETF output supplemental-only classifier.

Pure, no-IO classifier. Maps AV ETF_PROFILE per-ticker fields to a
supplemental-only classification. Decision record:
  - Alpha Vantage ETF_PROFILE is accepted ONLY as non-canonical supplemental
    ETF exposure evidence.
  - Canonical rejection criteria:
      - No as-of/date field in any observed response.
      - VXUS returned only 37 holdings for a fund with thousands of positions
        (partial/top-holdings-like coverage, not full canonical holdings).
      - fund_name null in several responses.
  - This output must NEVER be wired into visible decisions, synthesis,
    Deploy, or Watchtower behavior.

Hard constraints:
  - canonical_ready = False always.
  - safe_for_decision = False always.
  - No artifact writes.
  - No SQL.
  - No live provider calls.
  - API key never present (classifier takes no raw text from AV).
"""
from __future__ import annotations

from typing import Optional

CLASSIFIER_ID = "alpha_vantage_supplemental_v1"

# Rejection reason codes returned in the classification dict.
_REASON_NO_DATE = "as_of_date_missing"
_REASON_PARTIAL_COVERAGE = "partial_or_incomplete_coverage"
_REASON_NO_HOLDINGS = "no_holdings"
_REASON_NO_WEIGHTS = "no_weights"


def classify_av_etf_output(
    holdings_count: int,
    weights_available: bool,
    as_of_date: Optional[str],
    *,
    expected_min_holdings: Optional[int] = None,
) -> dict:
    """Classify an AV ETF_PROFILE per-ticker result as supplemental-only.

    Args:
        holdings_count:        Number of holdings returned by AV.
        weights_available:     Whether per-holding weights were present.
        as_of_date:            Provider date string, or None if missing.
                               fetched_at is NOT a valid source date — pass None
                               when the provider date field is absent.
        expected_min_holdings: When set, a holdings_count below this threshold
                               triggers coverage_quality="partial_or_suspicious".
                               Use for broad international ETFs (e.g. VXUS) where
                               the true holding count far exceeds what AV returned.
                               Do not set for focused/sector ETFs (e.g. XLE) that
                               legitimately have few components.

    Returns dict with:
        classifier_id:       Always "alpha_vantage_supplemental_v1".
        holdings_available:  True when holdings_count > 0 and weights_available.
        canonical_ready:     False always.
        safe_for_decision:   False always.
        as_of_date_verified: False when as_of_date is None.
        freshness_status:    "date_missing" | "date_present_unverified".
        coverage_quality:    "usable_supplemental" | "partial_or_suspicious"
                             | "not_supplemental".
        supplemental_only:   True when holdings_available is True.
        rejection_reasons:   List of reason codes explaining non-canonical status.
    """
    holdings_available = holdings_count > 0 and weights_available
    as_of_date_verified = as_of_date is not None
    freshness_status = "date_present_unverified" if as_of_date_verified else "date_missing"

    rejection_reasons: list[str] = []

    # Date is always a blocking rejection reason for canonical use.
    if not as_of_date_verified:
        rejection_reasons.append(_REASON_NO_DATE)

    if not holdings_available:
        if holdings_count == 0:
            rejection_reasons.append(_REASON_NO_HOLDINGS)
        if not weights_available:
            rejection_reasons.append(_REASON_NO_WEIGHTS)
        coverage_quality = "not_supplemental"
    elif expected_min_holdings is not None and holdings_count < expected_min_holdings:
        # Suspiciously low count for a broad fund — flag as partial/incomplete.
        coverage_quality = "partial_or_suspicious"
        rejection_reasons.append(_REASON_PARTIAL_COVERAGE)
    else:
        coverage_quality = "usable_supplemental"

    return {
        "classifier_id": CLASSIFIER_ID,
        "holdings_available": holdings_available,
        "canonical_ready": False,
        "safe_for_decision": False,
        "as_of_date_verified": as_of_date_verified,
        "freshness_status": freshness_status,
        "coverage_quality": coverage_quality,
        "supplemental_only": holdings_available,
        "rejection_reasons": rejection_reasons,
    }
