"""Stage 9G/9H — Unified Asset Decision Composer v1.

Pure, no-IO composer. Routes each asset to the correct intelligence lens
(stock vs ETF vs commodity trust vs crypto) based on asset classification,
maps available evidence into safe, explicit decision drivers, and produces
plain-English intelligence output for later UI rendering.

Architecture contracts (non-negotiable):
  - Pure function. No IO, no DB, no LLM, no external calls.
  - Never imports or calls decide(). Never modifies visible Buy/Hold/Trim/Sell.
  - HOLD must carry a specific reason code — never a silent generic fallback.
  - BUY (ETF): build/add underweight needed exposure or strong core role.
  - TRIM (ETF): overweight, redundant, over-concentrated, or role mismatch.
  - SELL (ETF): remove/replace when role wrong, duplicate, or structurally inferior.
  - Commodity trusts (GLD): portfolio hedge lens, not equity holdings analysis.
  - Stocks: stock fundamental lens (business quality, growth, margins, valuation,
    balance sheet, catalysts/news). Never ETF-style role/exposure analysis.
  - Output is diagnostic only. Does not change any visible app behavior.
  - safe_for_decision is always False. synthesis_ready is always False.

HOLD reason codes (explicit, not generic):
  HOLD_ON_TARGET:       role is correct and weight is at or near target.
  HOLD_STABLE_NO_TRIGGER: evidence stable but no action signal present.
  HOLD_WATCH_EVIDENCE:  evidence is weak/partial — watching for improvement.
  HOLD_WATCH_ROLE:      role signal is ambiguous — watching for clarification.
  HOLD_COMMODITY_STABLE: commodity trust; hedge role intact, no weight change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .etf_intelligence_classifier_v1 import (
    ETF_ROLE_BOND_STABILITY,
    ETF_ROLE_CASH_LIKE,
    ETF_ROLE_COMMODITY_HEDGE,
    ETF_ROLE_CORE_US_EQUITY,
    ETF_ROLE_CRYPTO_SPECULATIVE,
    ETF_ROLE_DIVIDEND_INCOME,
    ETF_ROLE_GROWTH_TILT,
    ETF_ROLE_INTERNATIONAL_DIVERSIFIER,
    ETF_ROLE_SECTOR_TILT,
    ETF_ROLE_UNKNOWN,
    ETF_TIER_HOLDINGS_READY,
    ETF_TIER_METADATA_ONLY,
    ETF_TIER_NOT_APPLICABLE,
    ETF_TIER_PROFILE_READY,
    ETF_TYPE_COMMODITY_TRUST,
    ETF_TYPE_CRYPTO_ETF,
    ETF_TYPE_UNKNOWN_FUND,
    EtfIntelligenceClassification,
    _KNOWN_ETF_MAP,
    classify_etf_intelligence,
)

COMPOSER_VERSION = "asset_intelligence_composer.v1"

# ── Asset class constants ─────────────────────────────────────────────────────

ASSET_CLASS_STOCK = "stock"
ASSET_CLASS_ETF = "etf"
ASSET_CLASS_COMMODITY_TRUST = "commodity_trust"
ASSET_CLASS_CRYPTO = "crypto"
ASSET_CLASS_UNKNOWN = "unknown"

# ── Lens constants ────────────────────────────────────────────────────────────

LENS_STOCK_FUNDAMENTAL = "stock_fundamental_lens"
LENS_ETF_ROLE = "etf_role_lens"
LENS_COMMODITY_HEDGE = "commodity_hedge_lens"
LENS_CRYPTO = "crypto_speculative_lens"
LENS_UNKNOWN = "unknown_lens"

# ── Action constants (mirrors existing ActionV3 values, no import) ────────────

ACTION_BUY = "BUY"
ACTION_HOLD = "HOLD"
ACTION_TRIM = "TRIM"
ACTION_SELL = "SELL"

# ── HOLD reason codes ─────────────────────────────────────────────────────────

HOLD_ON_TARGET = "HOLD_ON_TARGET"
HOLD_STABLE_NO_TRIGGER = "HOLD_STABLE_NO_TRIGGER"
HOLD_WATCH_EVIDENCE = "HOLD_WATCH_EVIDENCE"
HOLD_WATCH_ROLE = "HOLD_WATCH_ROLE"
HOLD_COMMODITY_STABLE = "HOLD_COMMODITY_STABLE"

# ── Portfolio fit band constants (mirrors FitBand values, no import) ──────────

FIT_UNDERWEIGHT = "UNDERWEIGHT"
FIT_ON_TARGET = "ON_TARGET"
FIT_OVERWEIGHT = "OVERWEIGHT"
FIT_BREACH = "BREACH"
FIT_BLOCKED = "BLOCKED"
FIT_UNKNOWN = "UNKNOWN"

# ── Evidence quality band constants (mirrors AxisBand values, no import) ──────

EV_THIN = "THIN"
EV_OK = "OK"
EV_STRONG = "STRONG"
EV_SUPPRESSED = "SUPPRESSED"


# ── Output dataclass ──────────────────────────────────────────────────────────


@dataclass
class AssetIntelligenceResult:
    """Unified asset intelligence output for one ticker.

    Diagnostic-only: safe_for_decision and synthesis_ready are always False.
    suggested_action is None when the evidence tier is too weak for any
    recommendation (never a silent HOLD).
    """

    ticker: str
    asset_class: str                        # ASSET_CLASS_* constant
    lens_applied: str                       # LENS_* constant

    # ETF-specific (None for stocks/crypto)
    etf_classification: Optional[EtfIntelligenceClassification]

    # Plain-English decision drivers for future UI rendering
    decision_drivers: list[str]

    # Suggested action — None when evidence is insufficient for any suggestion
    suggested_action: Optional[str]         # ACTION_* or None

    # When suggested_action is ACTION_HOLD, this must be set to a HOLD_* reason code
    hold_reason: Optional[str]

    # When suggested_action is None, explains why no action can be suggested
    blocked_reason: Optional[str]

    # Always False from this module
    safe_for_decision: bool = False
    synthesis_ready: bool = False

    composer_version: str = COMPOSER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "asset_class": self.asset_class,
            "lens_applied": self.lens_applied,
            "etf_classification": (
                self.etf_classification.to_dict()
                if self.etf_classification is not None
                else None
            ),
            "decision_drivers": list(self.decision_drivers),
            "suggested_action": self.suggested_action,
            "hold_reason": self.hold_reason,
            "blocked_reason": self.blocked_reason,
            "safe_for_decision": self.safe_for_decision,
            "synthesis_ready": self.synthesis_ready,
            "composer_version": self.composer_version,
        }


# ── Public API ────────────────────────────────────────────────────────────────


def compose_asset_intelligence(
    *,
    ticker: str,
    asset_type: str,
    portfolio_fit: str = FIT_UNKNOWN,
    evidence_quality: str = EV_SUPPRESSED,
    provider_outputs: Optional[dict[str, Any]] = None,
    upstream_signals: Optional[dict[str, Any]] = None,
) -> AssetIntelligenceResult:
    """Compose unified asset intelligence for one ticker.

    Routes to the correct lens based on asset type classification and
    produces safe, explicit decision drivers and a suggested action.

    Args:
        ticker:            Uppercase ticker symbol.
        asset_type:        Raw asset type string from holdings metadata.
                           Accepted values: 'etf'/'fund' (ETF lens),
                           'stock'/'equity' (stock lens),
                           'crypto' (crypto lens), other → unknown.
        portfolio_fit:     Portfolio weight fit band (FIT_* constant or
                           FitBand value string). Default UNKNOWN.
        evidence_quality:  Overall evidence quality band (EV_* constant or
                           AxisBand value string). Default SUPPRESSED.
        provider_outputs:  Stage 9F provider output dict (passed through to
                           ETF classifier). See classify_etf_intelligence().
        upstream_signals:  Optional dict of upstream signal hints:
            is_redundant_etf:   bool — role duplicates another holding.
            role_mismatch:      bool — ETF role does not match portfolio need.
            structurally_inferior: bool — better fund exists for same role.
            cost_elevated:      bool — expense ratio above peer average.
            concentration_risk: bool — top holdings over-concentrated.

    Returns:
        AssetIntelligenceResult — always non-None, never raises.
        safe_for_decision and synthesis_ready are always False.
    """
    t = (ticker or "").upper().strip()
    pf = (portfolio_fit or FIT_UNKNOWN).upper().strip()
    eq = (evidence_quality or EV_SUPPRESSED).upper().strip()
    signals = upstream_signals or {}
    provider_outs = provider_outputs or {}

    asset_class = _classify_asset_class(asset_type)

    # Ticker-map override: known ETFs/commodity trusts must route to ETF lens.
    # Ticker evidence is authoritative over generic category strings like "Other",
    # "Core", or even sector names — a holding categorized as "Technology" might
    # actually be an ETF (e.g. VGT, XLK). Known tickers always win.
    if t in _KNOWN_ETF_MAP and asset_class != ASSET_CLASS_ETF:
        asset_class = ASSET_CLASS_ETF

    # Stock fallback: non-ETF ticker with a specific sector/industry category
    # that is not a known non-equity instrument type defaults to stock lens.
    # Handles sub-sector labels not yet in _STOCK_CATEGORY_LABELS (e.g. "Cloud",
    # "Data Cloud", "SaaS") without exhaustive enumeration. Ambiguous labels
    # ("Other", "Unknown", "") and instrument types ("derivative", "futures")
    # stay UNKNOWN to remain conservative for truly unrecognized tickers.
    if asset_class == ASSET_CLASS_UNKNOWN and t not in _KNOWN_ETF_MAP:
        _cat_norm = (asset_type or "").lower().strip()
        if (
            _cat_norm
            and _cat_norm not in _AMBIGUOUS_CATEGORY_LABELS
            and _cat_norm not in _NON_STOCK_INSTRUMENT_TYPES
        ):
            asset_class = ASSET_CLASS_STOCK

    if asset_class in (ASSET_CLASS_STOCK,):
        return _compose_stock(t, asset_class, pf, eq, signals)

    if asset_class == ASSET_CLASS_CRYPTO:
        return _compose_crypto(t, asset_class, pf, eq, signals)

    if asset_class == ASSET_CLASS_ETF:
        etf_cls = classify_etf_intelligence(
            ticker=t,
            asset_type="etf",
            provider_outputs=provider_outs,
        )
        if etf_cls.etf_type == ETF_TYPE_COMMODITY_TRUST:
            return _compose_commodity_trust(t, etf_cls, pf, eq, signals)
        return _compose_etf(t, etf_cls, pf, eq, signals)

    # Unknown asset type — use unknown lens with explicit blocked reason.
    return AssetIntelligenceResult(
        ticker=t,
        asset_class=ASSET_CLASS_UNKNOWN,
        lens_applied=LENS_UNKNOWN,
        etf_classification=None,
        decision_drivers=[
            f"{t}: asset type not recognized — intelligence lens cannot be applied."
        ],
        suggested_action=None,
        hold_reason=None,
        blocked_reason=(
            f"unknown_asset_type: asset_type='{asset_type}' is not recognized. "
            "Cannot apply stock, ETF, crypto, or commodity lens."
        ),
        safe_for_decision=False,
        synthesis_ready=False,
    )


# ── Stock lens ────────────────────────────────────────────────────────────────


def _compose_stock(
    ticker: str,
    asset_class: str,
    portfolio_fit: str,
    evidence_quality: str,
    signals: dict[str, Any],
) -> AssetIntelligenceResult:
    """Apply stock fundamental lens — never ETF-style role/exposure analysis."""
    drivers: list[str] = []
    suggested_action: Optional[str] = None
    hold_reason: Optional[str] = None
    blocked_reason: Optional[str] = None

    # Stock lens focuses on: business quality, growth, margins/profitability,
    # valuation context, balance sheet/risk, catalysts/news/filings,
    # deterministic evidence quality.

    if evidence_quality in (EV_THIN, EV_SUPPRESSED):
        drivers.append(
            f"{ticker}: evidence is insufficient for stock analysis — "
            "business quality, growth, and financial health data are incomplete."
        )
        blocked_reason = (
            "evidence_too_thin: stock fundamental analysis requires business quality, "
            "growth, and financial health signals. Current evidence is THIN or SUPPRESSED."
        )
        return AssetIntelligenceResult(
            ticker=ticker,
            asset_class=asset_class,
            lens_applied=LENS_STOCK_FUNDAMENTAL,
            etf_classification=None,
            decision_drivers=drivers,
            suggested_action=None,
            hold_reason=None,
            blocked_reason=blocked_reason,
            safe_for_decision=False,
            synthesis_ready=False,
        )

    # Portfolio fit determines action direction.
    if portfolio_fit in (FIT_OVERWEIGHT, FIT_BREACH):
        drivers.append(
            f"{ticker}: position weight is above target — "
            "trimming reduces overexposure in this equity holding."
        )
        suggested_action = ACTION_TRIM
    elif portfolio_fit == FIT_BLOCKED:
        drivers.append(
            f"{ticker}: position is in a higher-risk category — "
            "maintain exposure without adding."
        )
        suggested_action = ACTION_HOLD
        hold_reason = HOLD_STABLE_NO_TRIGGER
    elif portfolio_fit == FIT_UNDERWEIGHT and evidence_quality in (EV_OK, EV_STRONG):
        drivers.append(
            f"{ticker}: stock shows adequate evidence quality and position "
            "is underweight — fundamental analysis supports adding."
        )
        suggested_action = ACTION_BUY
    elif portfolio_fit == FIT_ON_TARGET:
        drivers.append(
            f"{ticker}: position is at target weight — "
            "maintain current allocation."
        )
        suggested_action = ACTION_HOLD
        hold_reason = HOLD_ON_TARGET
    else:
        # portfolio_fit is UNKNOWN (no target allocation set) or an unexpected value.
        # Distinguish clearly from on-target/underweight so context does not mislead.
        if portfolio_fit == FIT_UNKNOWN:
            drivers.append(
                f"{ticker}: no target allocation is set — "
                "monitoring for business quality changes, catalyst events, and risk signals."
            )
        else:
            drivers.append(
                f"{ticker}: evidence is adequate but no clear trigger to add or reduce. "
                "Monitoring for business quality changes, catalyst events, or risk signals."
            )
        suggested_action = ACTION_HOLD
        hold_reason = HOLD_STABLE_NO_TRIGGER

    return AssetIntelligenceResult(
        ticker=ticker,
        asset_class=asset_class,
        lens_applied=LENS_STOCK_FUNDAMENTAL,
        etf_classification=None,
        decision_drivers=drivers,
        suggested_action=suggested_action,
        hold_reason=hold_reason,
        blocked_reason=None,
        safe_for_decision=False,
        synthesis_ready=False,
    )


# ── ETF role lens ─────────────────────────────────────────────────────────────


def _compose_etf(
    ticker: str,
    etf_cls: EtfIntelligenceClassification,
    portfolio_fit: str,
    evidence_quality: str,
    signals: dict[str, Any],
) -> AssetIntelligenceResult:
    """Apply ETF role/exposure/cost/overlap lens.

    ETF lens focuses on: portfolio role, target-weight fit, exposure/asset class,
    concentration/overlap, cost/expense ratio, liquidity/AUM, structure/tracking/
    index methodology, holdings freshness. NOT stock-style business analysis.
    """
    drivers: list[str] = []
    suggested_action: Optional[str] = None
    hold_reason: Optional[str] = None
    blocked_reason: Optional[str] = None

    evidence_tier = etf_cls.evidence_tier
    etf_role = etf_cls.etf_role

    # Role analysis available?
    role_known = etf_role != ETF_ROLE_UNKNOWN
    role_desc = etf_cls.role_description

    # Metadata-only: can classify role but cannot recommend action.
    if evidence_tier == ETF_TIER_METADATA_ONLY and not role_known:
        drivers.append(
            f"{ticker}: ETF role and category not yet identified — "
            "role and exposure analysis are blocked until metadata is available."
        )
        blocked_reason = (
            "metadata_only_unknown_role: ETF type and role are unknown. "
            "Cannot apply ETF intelligence lens without role/category metadata."
        )
        return AssetIntelligenceResult(
            ticker=ticker,
            asset_class=ASSET_CLASS_ETF,
            lens_applied=LENS_ETF_ROLE,
            etf_classification=etf_cls,
            decision_drivers=drivers,
            suggested_action=None,
            hold_reason=None,
            blocked_reason=blocked_reason,
            safe_for_decision=False,
            synthesis_ready=False,
        )

    # Role is known — add it as a driver.
    if role_known:
        drivers.append(role_desc)

    # Evidence limitation flags.
    if evidence_tier == ETF_TIER_METADATA_ONLY:
        drivers.append(
            f"{ticker}: only identity/category metadata is available. "
            "Cost, overlap, and concentration analysis are blocked."
        )
    elif evidence_tier == ETF_TIER_PROFILE_READY:
        drivers.append(
            f"{ticker}: ETF role is identified; "
            "holdings, overlap, and cost evidence are not yet wired."
        )
    elif evidence_tier == ETF_TIER_HOLDINGS_READY:
        drivers.append(
            f"{ticker}: full holdings data available — "
            "overlap and concentration analysis are safe."
        )

    # Upstream signals: structural concerns.
    is_redundant = bool(signals.get("is_redundant_etf", False))
    role_mismatch = bool(signals.get("role_mismatch", False))
    structurally_inferior = bool(signals.get("structurally_inferior", False))
    cost_elevated = bool(signals.get("cost_elevated", False))
    concentration_risk = bool(signals.get("concentration_risk", False))

    if role_mismatch:
        drivers.append(
            f"{ticker}: ETF role does not match the portfolio's current need — "
            "consider replacing with a fund that fills the intended sleeve."
        )
    if structurally_inferior:
        drivers.append(
            f"{ticker}: a structurally superior alternative exists for this role "
            "(lower cost or broader coverage) — consider replacing."
        )
    if is_redundant:
        drivers.append(
            f"{ticker}: exposure overlaps significantly with another holding — "
            "duplicate exposure reduces diversification without adding new coverage."
        )
    if cost_elevated:
        drivers.append(
            f"{ticker}: expense ratio is above the peer average for this fund category — "
            "cost drag reduces long-term compounding."
        )
    if concentration_risk and evidence_tier == ETF_TIER_HOLDINGS_READY:
        drivers.append(
            f"{ticker}: top holdings are highly concentrated — "
            "sector-level risk is elevated."
        )

    # Determine suggested action from fit and structural signals.
    if portfolio_fit in (FIT_OVERWEIGHT, FIT_BREACH):
        if role_mismatch or structurally_inferior:
            drivers.append(
                f"{ticker}: overweight AND role/structure concern — "
                "sell to remove misaligned exposure."
            )
            suggested_action = ACTION_SELL
        else:
            drivers.append(
                f"{ticker}: position weight is above target — "
                "trimming restores planned allocation."
            )
            suggested_action = ACTION_TRIM

    elif role_mismatch and structurally_inferior:
        drivers.append(
            f"{ticker}: wrong role for this portfolio sleeve and a better alternative exists — "
            "sell and replace."
        )
        suggested_action = ACTION_SELL

    elif is_redundant and structurally_inferior:
        drivers.append(
            f"{ticker}: duplicate exposure with a structurally inferior fund — "
            "sell and consolidate into the better alternative."
        )
        suggested_action = ACTION_SELL

    elif portfolio_fit == FIT_UNDERWEIGHT:
        if role_mismatch:
            drivers.append(
                f"{ticker}: underweight but role mismatch — "
                "do not add; review whether this fund fills the intended sleeve."
            )
            suggested_action = ACTION_HOLD
            hold_reason = HOLD_WATCH_ROLE
        else:
            drivers.append(
                f"{ticker}: position is underweight — "
                "adding builds the intended portfolio sleeve."
            )
            suggested_action = ACTION_BUY

    elif portfolio_fit == FIT_ON_TARGET:
        drivers.append(
            f"{ticker}: fund is at target weight and role is aligned — "
            "maintain regular contribution pace."
        )
        suggested_action = ACTION_HOLD
        hold_reason = HOLD_ON_TARGET

    else:
        # portfolio_fit unknown/blocked — HOLD but with explicit reason.
        if evidence_tier == ETF_TIER_METADATA_ONLY:
            suggested_action = ACTION_HOLD
            hold_reason = HOLD_WATCH_EVIDENCE
            drivers.append(
                f"{ticker}: holding while awaiting better cost, exposure, "
                "and holdings data for a more complete role assessment."
            )
        elif evidence_tier == ETF_TIER_PROFILE_READY:
            suggested_action = ACTION_HOLD
            hold_reason = HOLD_STABLE_NO_TRIGGER
            drivers.append(
                f"{ticker}: no clear trigger to add or reduce — "
                "role is identified and fund appears stable."
            )
        else:
            suggested_action = ACTION_HOLD
            hold_reason = HOLD_STABLE_NO_TRIGGER
            drivers.append(
                f"{ticker}: holdings data available, role is clear, "
                "but no action trigger is present."
            )

    return AssetIntelligenceResult(
        ticker=ticker,
        asset_class=ASSET_CLASS_ETF,
        lens_applied=LENS_ETF_ROLE,
        etf_classification=etf_cls,
        decision_drivers=drivers,
        suggested_action=suggested_action,
        hold_reason=hold_reason,
        blocked_reason=blocked_reason,
        safe_for_decision=False,
        synthesis_ready=False,
    )


# ── Commodity trust lens ──────────────────────────────────────────────────────


def _compose_commodity_trust(
    ticker: str,
    etf_cls: EtfIntelligenceClassification,
    portfolio_fit: str,
    evidence_quality: str,
    signals: dict[str, Any],
) -> AssetIntelligenceResult:
    """Apply commodity/portfolio hedge lens for GLD and similar trusts.

    Equity holdings analysis is not applicable. Use portfolio weight and
    role (commodity_hedge) for action signal.
    """
    drivers: list[str] = [etf_cls.role_description]
    suggested_action: Optional[str] = None
    hold_reason: Optional[str] = None

    drivers.append(
        f"{ticker}: commodity trust analyzed as a portfolio hedge — "
        "equity holdings overlap/concentration analysis does not apply."
    )

    role_mismatch = bool(signals.get("role_mismatch", False))
    structurally_inferior = bool(signals.get("structurally_inferior", False))

    if portfolio_fit in (FIT_OVERWEIGHT, FIT_BREACH):
        if role_mismatch or structurally_inferior:
            drivers.append(
                f"{ticker}: overweight AND hedge role concern — sell to correct allocation."
            )
            suggested_action = ACTION_SELL
        else:
            drivers.append(
                f"{ticker}: commodity hedge position is above target — trim to rebalance."
            )
            suggested_action = ACTION_TRIM

    elif portfolio_fit == FIT_UNDERWEIGHT:
        drivers.append(
            f"{ticker}: portfolio hedge allocation is below target — "
            "adding builds the inflation/risk hedge sleeve."
        )
        suggested_action = ACTION_BUY

    elif portfolio_fit == FIT_ON_TARGET:
        drivers.append(
            f"{ticker}: commodity hedge is at target allocation — maintain current weight."
        )
        suggested_action = ACTION_HOLD
        hold_reason = HOLD_COMMODITY_STABLE

    else:
        drivers.append(
            f"{ticker}: commodity trust position weight unknown — "
            "holding until target weight is established."
        )
        suggested_action = ACTION_HOLD
        hold_reason = HOLD_WATCH_EVIDENCE

    return AssetIntelligenceResult(
        ticker=ticker,
        asset_class=ASSET_CLASS_COMMODITY_TRUST,
        lens_applied=LENS_COMMODITY_HEDGE,
        etf_classification=etf_cls,
        decision_drivers=drivers,
        suggested_action=suggested_action,
        hold_reason=hold_reason,
        blocked_reason=None,
        safe_for_decision=False,
        synthesis_ready=False,
    )


# ── Crypto lens ───────────────────────────────────────────────────────────────


def _compose_crypto(
    ticker: str,
    asset_class: str,
    portfolio_fit: str,
    evidence_quality: str,
    signals: dict[str, Any],
) -> AssetIntelligenceResult:
    """Apply crypto speculative lens."""
    drivers: list[str] = []
    suggested_action: Optional[str] = None
    hold_reason: Optional[str] = None

    drivers.append(
        f"{ticker}: crypto asset — speculative position sizing applies. "
        "Not analyzed using stock fundamental or ETF role lens."
    )

    if portfolio_fit in (FIT_OVERWEIGHT, FIT_BREACH):
        drivers.append(
            f"{ticker}: crypto position is above target — trim speculative exposure."
        )
        suggested_action = ACTION_TRIM
    elif portfolio_fit == FIT_UNDERWEIGHT and evidence_quality in (EV_OK, EV_STRONG):
        drivers.append(
            f"{ticker}: within speculative position limit — adding is within plan."
        )
        suggested_action = ACTION_BUY
    elif portfolio_fit == FIT_ON_TARGET:
        drivers.append(f"{ticker}: speculative position is at target weight.")
        suggested_action = ACTION_HOLD
        hold_reason = HOLD_ON_TARGET
    else:
        drivers.append(
            f"{ticker}: holding at current size — not adding until "
            "risk/entry conditions improve."
        )
        suggested_action = ACTION_HOLD
        hold_reason = HOLD_STABLE_NO_TRIGGER

    return AssetIntelligenceResult(
        ticker=ticker,
        asset_class=asset_class,
        lens_applied=LENS_CRYPTO,
        etf_classification=None,
        decision_drivers=drivers,
        suggested_action=suggested_action,
        hold_reason=hold_reason,
        blocked_reason=None,
        safe_for_decision=False,
        synthesis_ready=False,
    )


# ── Asset class helper ────────────────────────────────────────────────────────


_STOCK_CATEGORY_LABELS: frozenset[str] = frozenset({
    # Explicit type strings
    "stock", "equity", "common_stock",
    "stocks", "equities", "individual_stock", "individual stocks",
    "security", "holding", "holdings", "ipo",
    # Generic portfolio bucket labels (from simulation_engine._CATEGORY_BUCKET)
    "core",
    # Common sector/industry strings that appear in card_meta at runtime
    "technology", "communication services", "consumer", "healthcare",
    "financials", "industrials", "materials", "energy", "utilities",
    "real estate", "semiconductors", "industrials/autos", "software",
    "retail", "banking", "media", "biotech",
    # Cloud/software sub-sectors observed in portfolio metadata (e.g. SNOW, CRM)
    "cloud", "data cloud", "saas", "enterprise software",
    "application software", "software infrastructure",
    # Consumer sub-sectors
    "consumer discretionary", "consumer staples",
})

# Category values that are genuinely ambiguous — do not imply stock or ETF.
# Used by the stock-fallback gate to stay conservative.
_AMBIGUOUS_CATEGORY_LABELS: frozenset[str] = frozenset({
    "", "other", "unknown", "n/a", "none",
})

# Known non-equity instrument types — must not be reclassified as stock
# even when the ticker is unrecognized and the category is specific.
_NON_STOCK_INSTRUMENT_TYPES: frozenset[str] = frozenset({
    "derivative", "derivatives", "option", "options",
    "warrant", "warrants", "futures", "future",
    "forex", "currency", "commodity", "commodities",
    "preferred", "preferred stock", "notes",
})


def _classify_asset_class(asset_type: str) -> str:
    """Map raw asset_type string to ASSET_CLASS_* constant.

    Handles real runtime card_meta category values including sector names,
    portfolio bucket labels, and common ETF/fund strings. Returns UNKNOWN
    for ambiguous values (e.g. "Other") — the caller applies ticker-map
    override as a secondary gate for known ETFs.
    """
    normalized = (asset_type or "").lower().strip()
    # ETF: exact match or substring "etf"
    if normalized in ("etf", "fund", "bond", "bonds", "bond etf", "fixed income"):
        return ASSET_CLASS_ETF
    if "etf" in normalized:
        return ASSET_CLASS_ETF
    if normalized in _STOCK_CATEGORY_LABELS:
        return ASSET_CLASS_STOCK
    if normalized in ("crypto", "cryptocurrency", "digital_asset"):
        return ASSET_CLASS_CRYPTO
    return ASSET_CLASS_UNKNOWN
