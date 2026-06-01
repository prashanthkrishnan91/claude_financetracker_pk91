"""Stage 9N — ETF Provider Decision Matrix v1.

Single source of truth for ETF holdings provider readiness classifications.
Backend-only. No UI, no decision policy changes, no SQL, no provider calls.

Resolves the three patch-loop root causes confirmed in Stage 9M runtime evidence:
  1. SEC NPORT for VTI/SCHD/VXUS has WRONG CIKs → will never succeed without manual
     CIK research → keep classifying as manual_research_required, not retrying.
  2. Alpha Vantage is missing as-of date → permanently supplemental_only.
  3. FMP free key returns HTTP 402 → permanently rejected_paywalled.

Canonical readiness gate (S-grade, immutable):
  A provider is canonical_ready ONLY when ALL of:
    - identity_verified: holdings provably belong to the requested ETF.
    - holdings_count: ≥ 1 verifiable holding rows (non-zero).
    - weights_available: per-holding percentage weights present.
    - as_of_or_report_date_present: a provider-supplied date field is present.
    - source_authority: either sec_primary_authority or issuer_official (not editorial).
    - entitlement_status: free/no_key_required (no 402 / paywalled).
  If any criterion fails → not canonical_ready, not safe_for_decision.

Classification values:
  canonical_ready              — all S-grade criteria met.
  supplemental_only            — useful data but missing a canonical criterion.
  manual_research_required     — known-good source path but requires manual resolution
                                 (e.g. wrong CIK; URL not confirmed stable).
  rejected_paywalled           — provider returns 402 / requires paid key.
  rejected_insufficient        — data shape cannot meet canonical gate.
  not_applicable               — not a holdings provider for this ticker class
                                 (e.g. GLD commodity trust has no equity basket).
"""
from __future__ import annotations

from typing import Optional

MATRIX_VERSION = "stage9n_v1"

# ── Classification constants ───────────────────────────────────────────────────

CANONICAL_READY = "canonical_ready"
SUPPLEMENTAL_ONLY = "supplemental_only"
MANUAL_RESEARCH_REQUIRED = "manual_research_required"
REJECTED_PAYWALLED = "rejected_paywalled"
REJECTED_INSUFFICIENT = "rejected_insufficient"
NOT_APPLICABLE = "not_applicable"


# ── S-grade gate criteria (immutable; mirrors Stage 9K holdings-ready gate) ───

S_GRADE_CRITERIA = (
    "identity_verified",
    "holdings_count_nonzero",
    "weights_available",
    "as_of_or_report_date_present",
    "source_authority_primary_or_issuer",
    "entitlement_status_free",
)


# ── Per-provider evidence record ───────────────────────────────────────────────

def _provider_record(
    provider_id: str,
    classification: str,
    *,
    identity_verified: Optional[bool],
    holdings_count_known: Optional[bool],
    weights_available: Optional[bool],
    as_of_or_report_date_present: Optional[bool],
    source_authority: str,
    entitlement_status: str,
    stability_risk: str,
    canonical_readiness: str,
    rejection_reasons: list[str],
    runtime_evidence: str,
    notes: str,
) -> dict:
    return {
        "provider_id": provider_id,
        "classification": classification,
        "canonical_ready": classification == CANONICAL_READY,
        "safe_for_decision": False,
        "criteria": {
            "identity_verified": identity_verified,
            "holdings_count_nonzero": holdings_count_known,
            "weights_available": weights_available,
            "as_of_or_report_date_present": as_of_or_report_date_present,
            "source_authority": source_authority,
            "entitlement_status": entitlement_status,
            "stability_risk": stability_risk,
            "canonical_readiness": canonical_readiness,
        },
        "rejection_reasons": rejection_reasons,
        "runtime_evidence": runtime_evidence,
        "notes": notes,
    }


# ── Provider decision matrix (evidence-based, immutable) ──────────────────────

_PROVIDER_MATRIX: dict[str, dict] = {

    "sec_nport_vti_schd_vxus": _provider_record(
        provider_id="sec_nport_vti_schd_vxus",
        classification=MANUAL_RESEARCH_REQUIRED,
        identity_verified=False,
        holdings_count_known=None,
        weights_available=None,
        as_of_or_report_date_present=None,
        source_authority="sec_primary_authority",
        entitlement_status="free_no_key_required",
        stability_risk="low_rate_limit_risk",
        canonical_readiness="blocked_wrong_cik",
        rejection_reasons=[
            "cik_resolves_to_wrong_filing_entity",
            "no_nport_p_filings_found_under_current_cik",
            "efts_discovery_returned_empty_for_all_three",
            "identity_not_proven_series_not_found",
        ],
        runtime_evidence=(
            "Stage 9M runtime (POST /api/v1/diagnostics/finance-intel/etf-nport-live-check): "
            "VTI CIK 0000764180 → 1000 recent forms, 0 NPORT-P, files page tried. "
            "SCHD CIK 0001477379 → 20 recent forms, 0 NPORT-P, no files pages. "
            "VXUS CIK 0001004244 → 127 recent forms, 0 NPORT-P, no files pages. "
            "resolver_limitation_reason present for all three. "
            "EFTS entity and series search returned no NPORT-P hits."
        ),
        notes=(
            "The correct NPORT-P filer CIKs for VTI/SCHD/VXUS must be found manually "
            "using the efts_entity_search_url and efts_series_search_url surfaced in "
            "the Stage 9M diagnostic output. Do not retry automatically — the current "
            "static CIK map is provably wrong for these three tickers."
        ),
    ),

    "sec_nport_spy_qqq": _provider_record(
        provider_id="sec_nport_spy_qqq",
        classification=CANONICAL_READY,
        identity_verified=True,
        holdings_count_known=True,
        weights_available=True,
        as_of_or_report_date_present=True,
        source_authority="sec_primary_authority",
        entitlement_status="free_no_key_required",
        stability_risk="low_rate_limit_risk",
        canonical_readiness="canonical_ready_standalone_trust",
        rejection_reasons=[],
        runtime_evidence=(
            "Stage 9F.2a runtime: SPY and QQQ are standalone trusts — "
            "the trust IS the filer; identity assumed without series name match. "
            "NPORT-P filings found and parsed successfully for both."
        ),
        notes=(
            "SPY and QQQ are standalone trusts; SEC NPORT is primary and proven. "
            "Holdings parsed, weights available, report_period_date present. "
            "~60-day filing lag from quarter-end is acceptable for long-term allocation analysis."
        ),
    ),

    "alpha_vantage_etf_profile": _provider_record(
        provider_id="alpha_vantage_etf_profile",
        classification=SUPPLEMENTAL_ONLY,
        identity_verified=True,
        holdings_count_known=True,
        weights_available=True,
        as_of_or_report_date_present=False,
        source_authority="third_party_unofficial",
        entitlement_status="free_with_api_key",
        stability_risk="high_quota_and_rate_limit_risk",
        canonical_readiness="blocked_missing_as_of_date",
        rejection_reasons=[
            "as_of_date_absent_in_all_observed_responses",
            "vxus_returned_only_37_holdings_partial_coverage",
            "fund_name_null_in_several_responses",
            "not_sec_primary_authority_or_issuer_official",
        ],
        runtime_evidence=(
            "Stage 9F.3b/3c runtime (AV ETF_PROFILE diagnostic): "
            "XLE 24 holdings+weights, as-of absent; VOO 519 holdings+weights, as-of absent; "
            "SCHD 103 holdings+weights, as-of absent; VXUS 37 holdings+weights (partial), as-of absent. "
            "First run (3a) returned 0 holdings for all Vanguard+SCHD — quota exhaustion. "
            "fund_name null in several ticker responses."
        ),
        notes=(
            "Accepted only as non-canonical supplemental exposure evidence. "
            "Do NOT wire into visible decisions, synthesis, Deploy, or Watchtower. "
            "Do not build a canonical AV adapter. "
            "As-of date is a hard S-grade requirement — its absence blocks canonical use permanently "
            "unless AV changes the API response shape."
        ),
    ),

    "fmp_etf_holdings": _provider_record(
        provider_id="fmp_etf_holdings",
        classification=REJECTED_PAYWALLED,
        identity_verified=None,
        holdings_count_known=None,
        weights_available=None,
        as_of_or_report_date_present=None,
        source_authority="third_party_unofficial",
        entitlement_status="paywalled_402_on_free_key",
        stability_risk="not_applicable_paywalled",
        canonical_readiness="blocked_paywalled",
        rejection_reasons=[
            "http_402_returned_for_free_api_key",
            "holdings_data_inaccessible_on_free_tier",
        ],
        runtime_evidence=(
            "Stage 9F.4 runtime (FMP ETF holdings diagnostic): "
            "Free FMP_API_KEY returns HTTP 402 for ETF holdings endpoint. "
            "No holdings, weights, or date data accessible on free tier."
        ),
        notes=(
            "Do not retry FMP or build a canonical FMP adapter on the free key. "
            "FMP ETF holdings require a paid subscription. "
            "No token wasted on further FMP probing."
        ),
    ),

    "issuer_official_vanguard": _provider_record(
        provider_id="issuer_official_vanguard",
        classification=MANUAL_RESEARCH_REQUIRED,
        identity_verified=None,
        holdings_count_known=None,
        weights_available=None,
        as_of_or_report_date_present=None,
        source_authority="issuer_official",
        entitlement_status="free_no_key_required",
        stability_risk="medium_url_schema_may_change",
        canonical_readiness="blocked_url_not_confirmed_stable",
        rejection_reasons=[
            "url_pattern_needs_post_deploy_validation",
            "csv_schema_subject_to_layout_changes",
            "identity_check_requires_fund_name_in_csv_metadata",
        ],
        runtime_evidence=(
            "Stage 9F.2b registry: vanguard_official_v1 registered but not confirmed. "
            "URL template: investor.vanguard.com/content/dam/fas-portspec-images/"
            "downloads/etf-shares/{TICKER}_QuantDataFundHoldings.csv. "
            "No live confirmation run at Stage 9N."
        ),
        notes=(
            "Issuer-official is the recommended canonical path for Vanguard ETFs (VTI/VXUS etc.) "
            "once the URL is confirmed stable, CSV is accessible, identity is verified via "
            "fund name in metadata, weights are present, and as-of date is present. "
            "Cannot be canonical_ready until a successful live confirmation proves all five criteria."
        ),
    ),

    "issuer_official_schwab": _provider_record(
        provider_id="issuer_official_schwab",
        classification=MANUAL_RESEARCH_REQUIRED,
        identity_verified=None,
        holdings_count_known=None,
        weights_available=None,
        as_of_or_report_date_present=None,
        source_authority="issuer_official",
        entitlement_status="free_no_key_required",
        stability_risk="high_url_not_publicly_documented_stable",
        canonical_readiness="blocked_url_not_confirmed_stable",
        rejection_reasons=[
            "url_pattern_not_publicly_stable_for_schd",
            "identity_check_requires_fund_name_in_csv_metadata",
        ],
        runtime_evidence=(
            "Stage 9F.2b registry: schwab_official_v1 registered for SCHD but URL "
            "is not publicly documented in a stable form. No live confirmation run."
        ),
        notes=(
            "Schwab Strategic Trust SCHD holdings CSV URL is not publicly stable. "
            "Manual research required: locate the official CSV download URL from the "
            "Schwab ETF product page. Then confirm identity, weights, and as-of date "
            "before promoting to canonical_ready."
        ),
    ),

    "issuer_official_ssga_spdr": _provider_record(
        provider_id="issuer_official_ssga_spdr",
        classification=MANUAL_RESEARCH_REQUIRED,
        identity_verified=None,
        holdings_count_known=None,
        weights_available=None,
        as_of_or_report_date_present=None,
        source_authority="issuer_official",
        entitlement_status="free_no_key_required",
        stability_risk="medium_url_schema_may_change",
        canonical_readiness="blocked_url_not_confirmed_stable",
        rejection_reasons=[
            "url_pattern_needs_post_deploy_validation",
            "fund_name_required_for_identity_verification",
        ],
        runtime_evidence=(
            "Stage 9F.2b registry: spdr_official_v1 registered for XLE/SPY. "
            "URL template: ssga.com/library-content/products/fund-data/etfs/us/"
            "holdings-daily-us-en-{ticker_lower}.csv. No live confirmation run."
        ),
        notes=(
            "SPY is already identity-verified via SEC NPORT (standalone trust). "
            "For XLE, issuer-official SSGA is the recommended primary path — "
            "must confirm URL stability, CSV accessibility, identity, weights, and date."
        ),
    ),

    "gld_commodity_trust": _provider_record(
        provider_id="gld_commodity_trust",
        classification=NOT_APPLICABLE,
        identity_verified=True,
        holdings_count_known=False,
        weights_available=False,
        as_of_or_report_date_present=False,
        source_authority="issuer_official",
        entitlement_status="free_no_key_required",
        stability_risk="not_applicable_no_equity_basket",
        canonical_readiness="not_applicable_commodity_trust",
        rejection_reasons=[
            "commodity_trust_holds_physical_gold_not_equity_basket",
            "no_equity_holdings_to_classify",
        ],
        runtime_evidence=(
            "Stage 9F.2a: GLD SPDR Gold Trust — commodity trust, holds physical gold bullion. "
            "No NPORT-P equity holdings expected or returned. Correct behavior."
        ),
        notes=(
            "GLD is a commodity trust with no equity basket. "
            "canonical_ready=False and safe_for_decision=False permanently. "
            "ETF intelligence lens routes GLD to commodity_hedge_lens, not holdings analysis."
        ),
    ),
}


# ── Per-ETF-class recommended next path ───────────────────────────────────────

_ETF_CLASS_NEXT_PATH: dict[str, dict] = {
    "vanguard_etfs": {
        "examples": ["VTI", "VXUS", "VOO", "VGT", "VHT", "VIS", "VYM"],
        "recommended_path": "issuer_official_vanguard",
        "rationale": (
            "SEC NPORT CIKs are confirmed wrong for VTI/SCHD/VXUS (Stage 9M runtime). "
            "Vanguard publishes official holdings CSVs. Confirm URL stability and "
            "identity/weights/date before promoting to canonical_ready."
        ),
        "blocker": "issuer_official_url_not_yet_confirmed_stable",
        "manual_action_required": (
            "Fetch investor.vanguard.com CSV for one Vanguard ticker (e.g. VTI), "
            "confirm fund name in metadata, weights column, and as-of date row. "
            "If confirmed, promote issuer_official_vanguard to canonical_ready."
        ),
    },
    "schwab_etfs": {
        "examples": ["SCHD"],
        "recommended_path": "issuer_official_schwab",
        "rationale": (
            "SEC NPORT CIK confirmed wrong for SCHD (Stage 9M). "
            "Schwab publishes SCHD holdings but URL is not publicly stable. "
            "Manual URL research required before canonical confirmation."
        ),
        "blocker": "schwab_holdings_csv_url_not_publicly_stable",
        "manual_action_required": (
            "Locate official SCHD CSV download URL from schwab.com ETF product page. "
            "Confirm URL stability, identity via fund name, weights, and as-of date."
        ),
    },
    "sector_etfs": {
        "examples": ["XLE", "VIS", "VHT", "VGT"],
        "recommended_path": "issuer_official_ssga_spdr",
        "rationale": (
            "XLE is SSGA/SPDR — issuer-official CSV is the recommended path. "
            "SPY (SSGA) is already confirmed via SEC NPORT as standalone trust. "
            "For sector ETFs: issuer-official first; SEC NPORT as fallback once CIKs confirmed."
        ),
        "blocker": "ssga_csv_url_not_yet_confirmed_stable",
        "manual_action_required": (
            "Fetch ssga.com holdings CSV for XLE, confirm fund name, weights, as-of date."
        ),
    },
    "commodity_etfs": {
        "examples": ["GLD"],
        "recommended_path": "gld_commodity_trust",
        "rationale": (
            "GLD holds physical gold — no equity basket. "
            "ETF intelligence routes GLD to commodity_hedge_lens. "
            "No holdings provider needed or applicable."
        ),
        "blocker": "none_not_applicable",
        "manual_action_required": "none",
    },
    "international_etfs": {
        "examples": ["VXUS"],
        "recommended_path": "issuer_official_vanguard",
        "rationale": (
            "VXUS is a Vanguard international fund. SEC NPORT CIK wrong (Stage 9M). "
            "AV returned only 37 holdings for a fund with thousands of positions — partial, "
            "not canonical. Issuer-official Vanguard CSV is the recommended canonical path."
        ),
        "blocker": "issuer_official_url_not_yet_confirmed_stable",
        "manual_action_required": (
            "Fetch Vanguard holdings CSV for VXUS, confirm fund name, weights, as-of date. "
            "Verify that full holdings depth is present (not just top holdings)."
        ),
    },
    "standalone_trust_etfs": {
        "examples": ["SPY", "QQQ"],
        "recommended_path": "sec_nport_spy_qqq",
        "rationale": (
            "SPY and QQQ are standalone trusts — SEC NPORT identity proven without "
            "series name matching. canonical_ready for these two tickers only."
        ),
        "blocker": "none_already_canonical",
        "manual_action_required": "none",
    },
}


# ── S-grade canonical readiness gate (mirrors Stage 9K; never loosened) ───────

def check_canonical_gate(
    *,
    identity_verified: bool,
    holdings_count: int,
    weights_available: bool,
    as_of_or_report_date_present: bool,
    source_authority: str,
    entitlement_status: str,
) -> tuple[bool, list[str]]:
    """Return (gate_passed, failed_criteria) for S-grade canonical readiness.

    This mirrors the Stage 9K holdings-ready gate. Never loosens the criteria.
    provider is canonical_ready ONLY when all criteria pass.
    """
    failures: list[str] = []

    if not identity_verified:
        failures.append("identity_verified_false")
    if holdings_count < 1:
        failures.append("holdings_count_zero")
    if not weights_available:
        failures.append("weights_not_available")
    if not as_of_or_report_date_present:
        failures.append("as_of_or_report_date_missing")
    if source_authority not in ("sec_primary_authority", "issuer_official"):
        failures.append("source_authority_not_primary_or_issuer")
    if entitlement_status not in ("free_no_key_required", "free_with_api_key"):
        failures.append("entitlement_not_free")

    return (len(failures) == 0, failures)


# ── Classify a single provider/ticker scenario ────────────────────────────────

def classify_provider(provider_id: str) -> Optional[dict]:
    """Return the evidence-based classification for a provider_id.

    Returns None if provider_id is not in the matrix.
    Result is always serializable (no dataclasses, plain dicts).
    """
    return _PROVIDER_MATRIX.get(provider_id)


def get_next_path_for_etf_class(etf_class: str) -> Optional[dict]:
    """Return the recommended next provider path for an ETF class.

    etf_class values: vanguard_etfs, schwab_etfs, sector_etfs,
                      commodity_etfs, international_etfs, standalone_trust_etfs
    Returns None if etf_class is not recognized.
    """
    return _ETF_CLASS_NEXT_PATH.get(etf_class)


# ── Full diagnostic matrix output ─────────────────────────────────────────────

def build_provider_decision_matrix() -> dict:
    """Return the full provider decision matrix as a JSON-serializable dict.

    Backend diagnostic output only. No UI, no decision policy changes.
    """
    canonical_count = sum(
        1 for p in _PROVIDER_MATRIX.values()
        if p["classification"] == CANONICAL_READY
    )
    return {
        "matrix_version": MATRIX_VERSION,
        "s_grade_criteria": list(S_GRADE_CRITERIA),
        "canonical_ready_count": canonical_count,
        "total_provider_paths": len(_PROVIDER_MATRIX),
        "providers": dict(_PROVIDER_MATRIX),
        "etf_class_next_paths": dict(_ETF_CLASS_NEXT_PATH),
        "patch_loop_stop_reasons": {
            "sec_nport_vti_schd_vxus": (
                "CIK confirmed wrong in Stage 9M runtime for all three tickers. "
                "Do not retry — manual CIK research required via EFTS search URLs."
            ),
            "alpha_vantage_etf_profile": (
                "as-of date is absent in every observed AV response. "
                "Supplemental only permanently unless AV changes API shape."
            ),
            "fmp_etf_holdings": (
                "HTTP 402 on free key in Stage 9F.4. "
                "Do not retry FMP with free key."
            ),
        },
    }
