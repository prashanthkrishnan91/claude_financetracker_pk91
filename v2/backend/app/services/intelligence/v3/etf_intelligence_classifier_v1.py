"""Stage 9G — ETF Intelligence Classifier v1.

Pure, no-IO classifier. Classifies ETFs by asset/product type and portfolio
role, maps Stage 9F provider outputs into ETF evidence readiness tiers, and
determines which analyses are safe given available evidence.

Architecture contracts (non-negotiable):
  - Pure function. No IO, no DB, no LLM, no external calls.
  - Never imports or calls decide().
  - Never produces Buy/Hold/Trim/Sell authority.
  - safe_for_decision is always False — this module is diagnostic only.
  - synthesis_ready is always False — this module is diagnostic only.
  - GLD and commodity trusts are classified as commodity_trust / commodity_hedge
    and do NOT fail equity holdings analysis (not_applicable, not failed).
  - Partial ETF holdings (partial_or_suspicious coverage quality) must not
    become overlap-safe or synthesis_ready.
  - HOLD reason codes are explicit — no silent generic HOLD fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

CLASSIFIER_VERSION = "etf_intelligence_classifier.v1"

# ── ETF asset/product type constants ─────────────────────────────────────────

ETF_TYPE_EQUITY_ETF = "equity_etf"
ETF_TYPE_SECTOR_ETF = "sector_etf"
ETF_TYPE_DIVIDEND_ETF = "dividend_etf"
ETF_TYPE_INTERNATIONAL_ETF = "international_etf"
ETF_TYPE_BOND_ETF = "bond_etf"
ETF_TYPE_COMMODITY_TRUST = "commodity_trust"
ETF_TYPE_CRYPTO_ETF = "crypto_etf"
ETF_TYPE_UNKNOWN_FUND = "unknown_fund"

# ── ETF portfolio role constants ──────────────────────────────────────────────

ETF_ROLE_CORE_US_EQUITY = "core_us_equity"
ETF_ROLE_GROWTH_TILT = "growth_tilt"
ETF_ROLE_DIVIDEND_INCOME = "dividend_income"
ETF_ROLE_SECTOR_TILT = "sector_tilt"
ETF_ROLE_INTERNATIONAL_DIVERSIFIER = "international_diversifier"
ETF_ROLE_BOND_STABILITY = "bond_stability"
ETF_ROLE_COMMODITY_HEDGE = "commodity_hedge"
ETF_ROLE_CRYPTO_SPECULATIVE = "crypto_speculative"
ETF_ROLE_CASH_LIKE = "cash_like"
ETF_ROLE_UNKNOWN = "unknown_role"

# ── ETF evidence tier constants ───────────────────────────────────────────────

# holdings + weights + as-of/report date + plausible coverage present
ETF_TIER_HOLDINGS_READY = "holdings_ready"
# profile/category/objective/cost/liquidity available but not full holdings
ETF_TIER_PROFILE_READY = "profile_ready"
# identity/category only — not decision-safe
ETF_TIER_METADATA_ONLY = "metadata_only"
# equity holdings lens not appropriate (commodity trusts, etc.)
ETF_TIER_NOT_APPLICABLE = "not_applicable"

# ── Safety flag keys ──────────────────────────────────────────────────────────

FLAG_SAFE_FOR_ROLE_ANALYSIS = "safe_for_role_analysis"
FLAG_SAFE_FOR_OVERLAP_ANALYSIS = "safe_for_overlap_analysis"
FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS = "safe_for_concentration_analysis"
FLAG_SAFE_FOR_COST_COMPARISON = "safe_for_cost_comparison"
FLAG_SAFE_FOR_DECISION = "safe_for_decision"          # always False
FLAG_SYNTHESIS_READY = "synthesis_ready"              # always False

# ── Known ETF type/role lookup ────────────────────────────────────────────────
# Maps uppercase ticker → (etf_type, etf_role).
# Intentionally conservative: unknown tickers fall back to unknown_fund/unknown_role.

_KNOWN_ETF_MAP: dict[str, tuple[str, str]] = {
    # US Broad Market / Core Equity
    "VOO":   (ETF_TYPE_EQUITY_ETF,        ETF_ROLE_CORE_US_EQUITY),
    "VTI":   (ETF_TYPE_EQUITY_ETF,        ETF_ROLE_CORE_US_EQUITY),
    "SPY":   (ETF_TYPE_EQUITY_ETF,        ETF_ROLE_CORE_US_EQUITY),
    "IVV":   (ETF_TYPE_EQUITY_ETF,        ETF_ROLE_CORE_US_EQUITY),
    "ITOT":  (ETF_TYPE_EQUITY_ETF,        ETF_ROLE_CORE_US_EQUITY),
    "SWTSX": (ETF_TYPE_EQUITY_ETF,        ETF_ROLE_CORE_US_EQUITY),
    # Growth / Large-Cap Tech tilt
    "QQQ":   (ETF_TYPE_EQUITY_ETF,        ETF_ROLE_GROWTH_TILT),
    "QQQM":  (ETF_TYPE_EQUITY_ETF,        ETF_ROLE_GROWTH_TILT),
    "VGT":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_GROWTH_TILT),
    "IVW":   (ETF_TYPE_EQUITY_ETF,        ETF_ROLE_GROWTH_TILT),
    # Dividend / Income
    "SCHD":  (ETF_TYPE_DIVIDEND_ETF,      ETF_ROLE_DIVIDEND_INCOME),
    "VYM":   (ETF_TYPE_DIVIDEND_ETF,      ETF_ROLE_DIVIDEND_INCOME),
    "DVY":   (ETF_TYPE_DIVIDEND_ETF,      ETF_ROLE_DIVIDEND_INCOME),
    "HDV":   (ETF_TYPE_DIVIDEND_ETF,      ETF_ROLE_DIVIDEND_INCOME),
    "DGRO":  (ETF_TYPE_DIVIDEND_ETF,      ETF_ROLE_DIVIDEND_INCOME),
    "NOBL":  (ETF_TYPE_DIVIDEND_ETF,      ETF_ROLE_DIVIDEND_INCOME),
    # Sector ETFs
    "XLE":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    "XLF":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    "XLK":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    "XLV":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    "XLI":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    "XLU":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    "XLP":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    "XLY":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    "VHT":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    "VIS":   (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    "ARKK":  (ETF_TYPE_SECTOR_ETF,        ETF_ROLE_SECTOR_TILT),
    # International / Ex-US
    "VXUS":  (ETF_TYPE_INTERNATIONAL_ETF, ETF_ROLE_INTERNATIONAL_DIVERSIFIER),
    "VEA":   (ETF_TYPE_INTERNATIONAL_ETF, ETF_ROLE_INTERNATIONAL_DIVERSIFIER),
    "VWO":   (ETF_TYPE_INTERNATIONAL_ETF, ETF_ROLE_INTERNATIONAL_DIVERSIFIER),
    "IEFA":  (ETF_TYPE_INTERNATIONAL_ETF, ETF_ROLE_INTERNATIONAL_DIVERSIFIER),
    "IEMG":  (ETF_TYPE_INTERNATIONAL_ETF, ETF_ROLE_INTERNATIONAL_DIVERSIFIER),
    "EFA":   (ETF_TYPE_INTERNATIONAL_ETF, ETF_ROLE_INTERNATIONAL_DIVERSIFIER),
    "EEM":   (ETF_TYPE_INTERNATIONAL_ETF, ETF_ROLE_INTERNATIONAL_DIVERSIFIER),
    # Bond / Fixed Income
    "BND":   (ETF_TYPE_BOND_ETF,          ETF_ROLE_BOND_STABILITY),
    "AGG":   (ETF_TYPE_BOND_ETF,          ETF_ROLE_BOND_STABILITY),
    "TLT":   (ETF_TYPE_BOND_ETF,          ETF_ROLE_BOND_STABILITY),
    "IEF":   (ETF_TYPE_BOND_ETF,          ETF_ROLE_BOND_STABILITY),
    "BNDX":  (ETF_TYPE_BOND_ETF,          ETF_ROLE_BOND_STABILITY),
    "BSV":   (ETF_TYPE_BOND_ETF,          ETF_ROLE_BOND_STABILITY),
    "LQD":   (ETF_TYPE_BOND_ETF,          ETF_ROLE_BOND_STABILITY),
    "HYG":   (ETF_TYPE_BOND_ETF,          ETF_ROLE_BOND_STABILITY),
    "SHY":   (ETF_TYPE_BOND_ETF,          ETF_ROLE_CASH_LIKE),
    "SHV":   (ETF_TYPE_BOND_ETF,          ETF_ROLE_CASH_LIKE),
    # Commodity Trusts — equity holdings NOT applicable
    "GLD":   (ETF_TYPE_COMMODITY_TRUST,   ETF_ROLE_COMMODITY_HEDGE),
    "IAU":   (ETF_TYPE_COMMODITY_TRUST,   ETF_ROLE_COMMODITY_HEDGE),
    "SLV":   (ETF_TYPE_COMMODITY_TRUST,   ETF_ROLE_COMMODITY_HEDGE),
    "PDBC":  (ETF_TYPE_COMMODITY_TRUST,   ETF_ROLE_COMMODITY_HEDGE),
    "GSG":   (ETF_TYPE_COMMODITY_TRUST,   ETF_ROLE_COMMODITY_HEDGE),
    "DJP":   (ETF_TYPE_COMMODITY_TRUST,   ETF_ROLE_COMMODITY_HEDGE),
    # Crypto ETFs
    "IBIT":  (ETF_TYPE_CRYPTO_ETF,        ETF_ROLE_CRYPTO_SPECULATIVE),
    "FBTC":  (ETF_TYPE_CRYPTO_ETF,        ETF_ROLE_CRYPTO_SPECULATIVE),
    "BITB":  (ETF_TYPE_CRYPTO_ETF,        ETF_ROLE_CRYPTO_SPECULATIVE),
    "GBTC":  (ETF_TYPE_CRYPTO_ETF,        ETF_ROLE_CRYPTO_SPECULATIVE),
    "BITO":  (ETF_TYPE_CRYPTO_ETF,        ETF_ROLE_CRYPTO_SPECULATIVE),
}


# ── Output dataclass ──────────────────────────────────────────────────────────


@dataclass
class EtfIntelligenceClassification:
    """Per-ticker ETF intelligence classification output.

    safe_for_decision and synthesis_ready are always False — this module
    provides classification inputs for the composer; it never owns authority.
    """

    ticker: str
    is_etf: bool                       # False for non-ETF tickers
    etf_type: str                      # one of ETF_TYPE_* constants
    etf_role: str                      # one of ETF_ROLE_* constants
    evidence_tier: str                 # one of ETF_TIER_* constants
    safety_flags: dict[str, bool]      # FLAG_* keys → bool

    # Plain-English description of the role suitable for UI display
    role_description: str

    # What analysis is limited/blocked and why
    limitation_reasons: list[str]

    # Always False — this module is diagnostic only
    safe_for_decision: bool = False
    synthesis_ready: bool = False

    classifier_version: str = CLASSIFIER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "is_etf": self.is_etf,
            "etf_type": self.etf_type,
            "etf_role": self.etf_role,
            "evidence_tier": self.evidence_tier,
            "safety_flags": dict(self.safety_flags),
            "role_description": self.role_description,
            "limitation_reasons": list(self.limitation_reasons),
            "safe_for_decision": self.safe_for_decision,
            "synthesis_ready": self.synthesis_ready,
            "classifier_version": self.classifier_version,
        }


# ── Public API ────────────────────────────────────────────────────────────────


def classify_etf_intelligence(
    *,
    ticker: str,
    asset_type: str,
    provider_outputs: Optional[dict[str, Any]] = None,
) -> EtfIntelligenceClassification:
    """Classify an ETF ticker's intelligence type, role, and evidence tier.

    Args:
        ticker:          Uppercase ticker symbol.
        asset_type:      One of 'etf' | 'stock' | 'equity' | 'crypto' | 'unknown'.
                         Non-ETF tickers receive a not-applicable classification.
        provider_outputs: Optional dict of Stage 9F provider output values:
            av_output:   dict from classify_av_etf_output() (AV supplemental).
            fmp_output:  dict from FMP ETF holdings runner result.
            nport_output: dict from SEC NPORT result.
            canonical_etf_row: dict from build_canonical_etf_fund_dataset_row().

    Returns:
        EtfIntelligenceClassification — always non-None, never raises.
        For non-ETF tickers: is_etf=False, etf_type=NOT_APPLICABLE.
        safe_for_decision and synthesis_ready are always False.
    """
    t = (ticker or "").upper().strip()
    normalized_type = (asset_type or "").lower().strip()

    # Non-ETF asset types receive a not-applicable result.
    _ETF_TYPE_ALIASES = {"etf", "fund"}
    if normalized_type not in _ETF_TYPE_ALIASES:
        return _not_applicable_classification(t, asset_type)

    etf_type, etf_role = _lookup_etf_type_and_role(t)

    # Commodity trusts use not_applicable evidence tier (holdings lens is wrong).
    if etf_type == ETF_TYPE_COMMODITY_TRUST:
        return _build_commodity_trust_classification(t, etf_type, etf_role)

    evidence_tier = _derive_evidence_tier(t, etf_type, provider_outputs or {})
    safety_flags = _derive_safety_flags(etf_type, evidence_tier)
    role_description = _build_role_description(t, etf_type, etf_role)
    limitation_reasons = _derive_limitation_reasons(evidence_tier, safety_flags, etf_type)

    return EtfIntelligenceClassification(
        ticker=t,
        is_etf=True,
        etf_type=etf_type,
        etf_role=etf_role,
        evidence_tier=evidence_tier,
        safety_flags=safety_flags,
        role_description=role_description,
        limitation_reasons=limitation_reasons,
        safe_for_decision=False,
        synthesis_ready=False,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────


def _lookup_etf_type_and_role(ticker: str) -> tuple[str, str]:
    """Look up known ETF type/role, fall back to unknown_fund/unknown_role."""
    return _KNOWN_ETF_MAP.get(ticker, (ETF_TYPE_UNKNOWN_FUND, ETF_ROLE_UNKNOWN))


def _derive_evidence_tier(
    ticker: str,
    etf_type: str,
    provider_outputs: dict[str, Any],
) -> str:
    """Derive the evidence tier from Stage 9F provider outputs.

    Tier hierarchy (highest to lowest):
      holdings_ready:  holdings + weights + as-of date + plausible coverage.
      profile_ready:   profile/category/cost/liquidity usable for role analysis.
      metadata_only:   identity/type only — not decision-safe.

    FMP 402/paywalled → no holdings readiness.
    AV missing date → profile_ready at most, not holdings_ready.
    Partial/suspicious coverage (e.g. VXUS with few holdings) → profile_ready,
    never overlap-safe.
    """
    av = provider_outputs.get("av_output") or {}
    fmp = provider_outputs.get("fmp_output") or {}
    nport = provider_outputs.get("nport_output") or {}
    canonical = provider_outputs.get("canonical_etf_row") or {}

    # Check NPORT for holdings_ready.
    if _nport_is_holdings_ready(nport):
        return ETF_TIER_HOLDINGS_READY

    # Check FMP (free tier is paywalled — should never contribute holdings_ready).
    fmp_fetch = (fmp.get("fetch_status") or "").lower()
    if fmp_fetch in ("paywalled", "unauthorized", "error", "no_data", "rate_limited"):
        pass  # FMP contributes nothing
    elif _fmp_is_holdings_ready(fmp):
        return ETF_TIER_HOLDINGS_READY

    # AV is supplemental-only. Date must be present for holdings_ready.
    if _av_is_holdings_ready(av):
        return ETF_TIER_HOLDINGS_READY

    # Profile-ready: enough category/cost/liquidity metadata for role analysis.
    if _any_profile_signals(av, fmp, nport, canonical, etf_type):
        return ETF_TIER_PROFILE_READY

    # Minimum: ticker known to be an ETF — metadata_only.
    return ETF_TIER_METADATA_ONLY


def _extract_date_field(d: dict[str, Any], *keys: str) -> Optional[str]:
    """Return the first non-empty string found for any of the given keys."""
    for k in keys:
        v = d.get(k)
        if v and isinstance(v, str):
            return v
    return None


def _nport_is_holdings_ready(nport: dict[str, Any]) -> bool:
    """NPORT result meets holdings_ready threshold.

    Accepts the real Stage 9F etf_nport_adapter_v1.py output shape which
    uses ``report_period_date`` (not ``as_of_date``).  Tolerates either key
    so callers passing a generic dict with ``as_of_date`` also work.
    """
    if not nport:
        return False
    fetch_status = (nport.get("fetch_status") or "").lower()
    if fetch_status not in ("success",):
        return False
    holdings_count = nport.get("holdings_count", 0) or 0
    weights = bool(nport.get("weights_available", False))
    # Real NPORT payload uses report_period_date; accept as_of_date as fallback.
    as_of = _extract_date_field(nport, "report_period_date", "as_of_date")
    if holdings_count < 5 or not weights or not as_of:
        return False
    # Partial/suspicious coverage is never holdings_ready.
    coverage = (nport.get("coverage_quality") or "").lower()
    if "suspicious" in coverage or "partial" in coverage:
        return False
    return True


def _fmp_is_holdings_ready(fmp: dict[str, Any]) -> bool:
    """FMP result meets holdings_ready threshold (non-paywalled scenario).

    Accepts the real Stage 9F fmp_etf_holdings_runner_v1.py output shape
    which uses ``as_of_date_or_date_field`` (not ``as_of_date``).  Also
    accepts ``report_period_date`` and ``as_of_date`` as fallbacks so fixture
    tests and future callers remain compatible.
    """
    if not fmp:
        return False
    fetch_status = (fmp.get("fetch_status") or "").lower()
    if fetch_status != "success":
        return False
    holdings_count = fmp.get("holdings_count", 0) or 0
    weights = bool(fmp.get("weights_available", False))
    # Real FMP payload uses as_of_date_or_date_field; accept alternatives.
    as_of = _extract_date_field(
        fmp, "as_of_date_or_date_field", "as_of_date", "report_period_date"
    )
    if holdings_count < 5 or not weights or not as_of:
        return False
    coverage = (fmp.get("coverage_quality") or "").lower()
    if "suspicious" in coverage or "partial" in coverage:
        return False
    return True


def _av_is_holdings_ready(av: dict[str, Any]) -> bool:
    """AV supplemental result meets holdings_ready threshold.

    AV NEVER has as_of_date in observed responses — so this always returns
    False in practice. Kept explicit so tests can verify the boundary.
    Date absence is the canonical rejection reason for AV.
    """
    if not av:
        return False
    # AV is accepted as supplemental-only per Stage 9F.3c decision.
    # canonical_ready is always False for AV; holdings_ready requires date.
    as_of_date_verified = bool(av.get("as_of_date_verified", False))
    if not as_of_date_verified:
        return False
    holdings_available = bool(av.get("holdings_available", False))
    coverage = (av.get("coverage_quality") or "").lower()
    if not holdings_available:
        return False
    if "suspicious" in coverage or "partial" in coverage:
        return False
    return True


def _any_profile_signals(
    av: dict[str, Any],
    fmp: dict[str, Any],
    nport: dict[str, Any],
    canonical: dict[str, Any],
    etf_type: str,
) -> bool:
    """Return True if enough profile/category metadata exists for role analysis."""
    # AV holdings (even without date) provide exposure/category signal.
    if av.get("holdings_available") and av.get("coverage_quality") not in (None, "not_supplemental"):
        return True
    # Known ETF type (from _KNOWN_ETF_MAP) always provides at least profile_ready
    # since role/category identity is available from the known-ETF table.
    if etf_type != ETF_TYPE_UNKNOWN_FUND:
        return True
    # canonical scaffold present with a usable fundamentals lane → partial fund
    # category signal (expense ratio may be present in yfinance artifact).
    if canonical.get("canonical_etf_scaffold_present") and not canonical.get("etf_fund_intelligence_ready"):
        fund_identity = canonical.get("fund_identity") or {}
        cost_yield = canonical.get("cost_and_yield") or {}
        if cost_yield.get("expense_ratio_status") == "PARTIAL":
            return True
    return False


def _derive_safety_flags(etf_type: str, evidence_tier: str) -> dict[str, bool]:
    """Derive safety flag dict from ETF type and evidence tier."""
    tier_holdings = evidence_tier == ETF_TIER_HOLDINGS_READY
    tier_profile = evidence_tier in (ETF_TIER_HOLDINGS_READY, ETF_TIER_PROFILE_READY)

    return {
        FLAG_SAFE_FOR_ROLE_ANALYSIS: evidence_tier != ETF_TIER_NOT_APPLICABLE,
        FLAG_SAFE_FOR_OVERLAP_ANALYSIS: tier_holdings,
        FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS: tier_holdings,
        FLAG_SAFE_FOR_COST_COMPARISON: tier_profile,
        FLAG_SAFE_FOR_DECISION: False,     # always False
        FLAG_SYNTHESIS_READY: False,       # always False
    }


def _build_role_description(ticker: str, etf_type: str, etf_role: str) -> str:
    """Build a plain-English role description for UI use."""
    _role_descriptions: dict[str, str] = {
        ETF_ROLE_CORE_US_EQUITY: (
            f"{ticker} is a broad US equity fund providing core market exposure."
        ),
        ETF_ROLE_GROWTH_TILT: (
            f"{ticker} tilts toward growth-oriented or technology-heavy sectors."
        ),
        ETF_ROLE_DIVIDEND_INCOME: (
            f"{ticker} focuses on dividend-paying stocks for income generation."
        ),
        ETF_ROLE_SECTOR_TILT: (
            f"{ticker} concentrates in a specific market sector."
        ),
        ETF_ROLE_INTERNATIONAL_DIVERSIFIER: (
            f"{ticker} provides international or ex-US market diversification."
        ),
        ETF_ROLE_BOND_STABILITY: (
            f"{ticker} holds fixed-income securities for portfolio stability."
        ),
        ETF_ROLE_COMMODITY_HEDGE: (
            f"{ticker} holds physical commodities or commodity contracts "
            "as a portfolio inflation/risk hedge."
        ),
        ETF_ROLE_CRYPTO_SPECULATIVE: (
            f"{ticker} provides exposure to cryptocurrency — speculative position sizing applies."
        ),
        ETF_ROLE_CASH_LIKE: (
            f"{ticker} holds short-duration bonds as a cash-equivalent or liquidity reserve."
        ),
        ETF_ROLE_UNKNOWN: (
            f"{ticker} fund role is not yet classified — "
            "role analysis requires additional category metadata."
        ),
    }
    return _role_descriptions.get(etf_role, f"{ticker}: fund role unknown.")


def _derive_limitation_reasons(
    evidence_tier: str,
    safety_flags: dict[str, bool],
    etf_type: str,
) -> list[str]:
    """Derive explicit limitation reasons from tier and flags."""
    reasons: list[str] = []

    if evidence_tier == ETF_TIER_METADATA_ONLY:
        reasons.append(
            "metadata_only: only ETF type/identity is known; "
            "cost, exposure, and overlap analysis require provider profile data."
        )
    if evidence_tier == ETF_TIER_PROFILE_READY:
        reasons.append(
            "profile_ready: role and cost analysis available; "
            "overlap/concentration analysis requires full holdings data."
        )
    if not safety_flags.get(FLAG_SAFE_FOR_OVERLAP_ANALYSIS):
        reasons.append(
            "overlap_analysis_blocked: holdings not ready; "
            "partial or missing holdings cannot be used for overlap analysis."
        )
    if not safety_flags.get(FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS):
        reasons.append(
            "concentration_analysis_blocked: full holdings required for "
            "top-holding concentration analysis."
        )
    if etf_type == ETF_TYPE_UNKNOWN_FUND:
        reasons.append(
            "unknown_fund_type: ETF product type not identified from known registry; "
            "role analysis may be imprecise."
        )
    return reasons


def _build_commodity_trust_classification(
    ticker: str,
    etf_type: str,
    etf_role: str,
) -> EtfIntelligenceClassification:
    """Return a commodity trust classification with not_applicable evidence tier.

    GLD and similar commodity trusts hold physical commodities, not equity
    baskets. Equity holdings analysis is not applicable — this is NOT a
    failure; it is correct classification.
    """
    safety_flags = {
        FLAG_SAFE_FOR_ROLE_ANALYSIS: True,    # role is known (commodity_hedge)
        FLAG_SAFE_FOR_OVERLAP_ANALYSIS: False, # no equity holdings
        FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS: False,
        FLAG_SAFE_FOR_COST_COMPARISON: True,   # expense ratio may be available
        FLAG_SAFE_FOR_DECISION: False,
        FLAG_SYNTHESIS_READY: False,
    }
    return EtfIntelligenceClassification(
        ticker=ticker,
        is_etf=True,
        etf_type=etf_type,
        etf_role=etf_role,
        evidence_tier=ETF_TIER_NOT_APPLICABLE,
        safety_flags=safety_flags,
        role_description=_build_role_description(ticker, etf_type, etf_role),
        limitation_reasons=[
            "not_applicable: commodity trust holds physical assets, not an equity basket. "
            "Equity holdings analysis (overlap, concentration) does not apply. "
            "Use portfolio hedge lens for role/cost/weight analysis only."
        ],
        safe_for_decision=False,
        synthesis_ready=False,
    )


def _not_applicable_classification(
    ticker: str,
    asset_type: str,
) -> EtfIntelligenceClassification:
    """Return a not-applicable classification for non-ETF tickers."""
    all_flags_false = {
        FLAG_SAFE_FOR_ROLE_ANALYSIS: False,
        FLAG_SAFE_FOR_OVERLAP_ANALYSIS: False,
        FLAG_SAFE_FOR_CONCENTRATION_ANALYSIS: False,
        FLAG_SAFE_FOR_COST_COMPARISON: False,
        FLAG_SAFE_FOR_DECISION: False,
        FLAG_SYNTHESIS_READY: False,
    }
    return EtfIntelligenceClassification(
        ticker=ticker,
        is_etf=False,
        etf_type=ETF_TYPE_UNKNOWN_FUND,
        etf_role=ETF_ROLE_UNKNOWN,
        evidence_tier=ETF_TIER_NOT_APPLICABLE,
        safety_flags=all_flags_false,
        role_description=(
            f"{ticker}: ETF intelligence lens is not applicable "
            f"for asset type '{asset_type}'. "
            "Use stock fundamental analysis lens for equity holdings."
        ),
        limitation_reasons=[
            f"not_applicable: asset_type='{asset_type}' is not an ETF. "
            "ETF intelligence classifier does not apply."
        ],
        safe_for_decision=False,
        synthesis_ready=False,
    )
