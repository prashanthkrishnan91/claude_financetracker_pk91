"""Stage 9F — Canonical ETF Fund Intelligence Dataset v1.

Pure, no-IO read model. Converts existing evidence artifact metadata
(Stage 5J LaneCoverage for ETF tickers) into one normalized, auditable
per-ticker ETF fund intelligence dataset row.

This is the first honest canonical ETF dataset scaffold. It uses only
existing repo data (yfinance fundamentals/technicals lane metadata) and
clearly marks missing provider gaps where composition/exposure/yield/
expense data is unavailable. It does NOT fake ETF fund intelligence.

Architecture contracts (non-negotiable):
  - Pure function. No IO, no DB, no LLM, no external calls.
  - Never imports or calls decide().
  - Never produces Buy/Hold/Trim/Sell authority.
  - safe_for_decision is always False.
  - synthesis_ready is always False.
  - valuation_ready is always False at Stage 9F.
  - etf_fund_intelligence_ready is True ONLY when real ETF-specific fund
    intelligence inputs exist (composition, expense, yield from dedicated
    fund data provider). Always False at Stage 9F — no such provider exists.
  - ETF-only: equity and crypto return NOT_APPLICABLE rows.
  - Does NOT apply SEC CompanyFacts logic to ETFs.
  - Does NOT fabricate holdings, exposures, expense ratios, or yield values.
  - Does NOT serialize raw provider payloads.
  - All composition/holdings/exposure statuses are MISSING because no
    dedicated fund-data provider is built at Stage 9F.
  - Fund identity fields (fund_name_available, issuer_available,
    category_or_index_strategy_available) are False at Stage 9F because
    ETF-specific fields are not extracted or validated from yfinance artifacts
    even when the fundamentals lane is usable. A usable lane means equity
    fundamentals arrived; it does NOT mean ETF-specific fund metadata was
    extracted or verified.
  - Cost/yield statuses are PARTIAL at best when the fundamentals lane is
    usable, with an explicit reason that these fields are not ETF-specifically
    extracted or validated at Stage 9F.
  - canonical_etf_scaffold_present=True for all ETF rows (Stage 9F built the
    scaffold). canonical_etf_dataset_safe=False always — the dataset is not
    safe/verified because composition is always MISSING and no ETF-specific
    fields are extracted. Scaffold present ≠ dataset safe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .research_evidence_coverage_read_model_v1 import (
    LANE_FUNDAMENTALS,
    LANE_TECHNICALS,
    LaneCoverage,
)
from .research_evidence_decision_input_adapter_v1 import (
    INSTRUMENT_CATEGORY_CRYPTO,
    INSTRUMENT_CATEGORY_EQUITY,
    INSTRUMENT_CATEGORY_ETF,
)

ETF_DATASET_VERSION = "canonical_etf_fund_dataset.v1"

# ── Field status constants ─────────────────────────────────────────────────────

ETF_STATUS_AVAILABLE = "AVAILABLE"
ETF_STATUS_PARTIAL = "PARTIAL"
ETF_STATUS_MISSING = "MISSING"
ETF_STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"

# Labels from the truth adapter that indicate usable evidence.
_USABLE_LABELS = frozenset({"USABLE", "USABLE_WITH_LIMITATIONS"})

# Plain-English blocker reason used in missing_reasons["composition"].
ETF_COMPOSITION_MISSING_REASON = (
    "ETF_FUND_COMPOSITION_NOT_READY: No dedicated fund holdings/composition "
    "provider exists at Stage 9F. Holdings composition, sector/geography "
    "exposure, and concentration data require a dedicated ETF fund data provider."
)

ETF_PROVIDER_MISSING_REASON = (
    "ETF_PROVIDER_DATA_MISSING: No dedicated fund data provider is built. "
    "yfinance fundamentals lane may carry partial fund metadata (expense ratio, "
    "yield, fund family) but does not provide fund composition or holdings."
)


# ── Section dataclasses ────────────────────────────────────────────────────────


@dataclass
class EtfFundIdentitySection:
    """Fund identity: name, issuer, category/index/strategy availability.

    Derived from yfinance fundamentals lane metadata (PARTIAL when the
    fundamentals artifact is usable — yfinance includes fund name, fund
    family, and category for ETF tickers). No raw fund metadata is stored.
    """

    fund_name_available: bool
    issuer_available: bool
    category_or_index_strategy_available: bool
    missing_reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_name_available": self.fund_name_available,
            "issuer_available": self.issuer_available,
            "category_or_index_strategy_available": self.category_or_index_strategy_available,
            "missing_reason": self.missing_reason,
        }


@dataclass
class EtfCostAndYieldSection:
    """ETF cost and distribution yield availability.

    Statuses are PARTIAL when yfinance fundamentals lane is usable (yfinance
    ETF info may include expense ratio and yield) and MISSING otherwise.
    No raw expense ratios or yield values are stored or serialized.
    """

    expense_ratio_status: str      # AVAILABLE | PARTIAL | MISSING
    dividend_or_distribution_yield_status: str
    missing_reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "expense_ratio_status": self.expense_ratio_status,
            "dividend_or_distribution_yield_status": self.dividend_or_distribution_yield_status,
            "missing_reason": self.missing_reason,
        }


@dataclass
class EtfCompositionSection:
    """ETF fund composition availability.

    All statuses are MISSING at Stage 9F — no dedicated fund holdings/
    composition provider exists. Do not mark PARTIAL or AVAILABLE here
    without a real fund composition provider that returns actual holdings.
    """

    holdings_composition_status: str    # always MISSING at Stage 9F
    sector_exposure_status: str         # always MISSING at Stage 9F
    geography_exposure_status: str      # always MISSING at Stage 9F
    concentration_status: str           # always MISSING at Stage 9F
    missing_reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "holdings_composition_status": self.holdings_composition_status,
            "sector_exposure_status": self.sector_exposure_status,
            "geography_exposure_status": self.geography_exposure_status,
            "concentration_status": self.concentration_status,
            "missing_reason": self.missing_reason,
        }


@dataclass
class EtfTradingAndRiskSection:
    """ETF trading and risk support signals.

    Derived from the technicals lane (technical_signal artifact). When the
    technicals artifact is usable, volume/price history provides a liquidity
    proxy and volatility context — marked PARTIAL since dedicated ETF market
    structure analysis is not available. No raw price or volume values stored.
    """

    liquidity_proxy_status: str              # PARTIAL (technicals) | MISSING
    volatility_or_technical_support_status: str  # PARTIAL (technicals) | MISSING
    missing_reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "liquidity_proxy_status": self.liquidity_proxy_status,
            "volatility_or_technical_support_status": self.volatility_or_technical_support_status,
            "missing_reason": self.missing_reason,
        }


@dataclass
class EtfSourceHealthEntry:
    """Safe provenance entry for one evidence lane artifact used in the ETF dataset row."""

    lane: str
    artifact_id: Optional[str]
    usability_label: Optional[str]
    freshness_status: Optional[str]
    model_version: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "artifact_id": self.artifact_id,
            "usability_label": self.usability_label,
            "freshness_status": self.freshness_status,
            "model_version": self.model_version,
        }


# ── Row dataclass ──────────────────────────────────────────────────────────────


@dataclass
class CanonicalEtfFundDatasetRow:
    """Per-ticker canonical ETF fund intelligence dataset row.

    Safe for diagnostics and downstream adapters. No raw payloads, no source
    URLs, no holdings data, no fact values, no API keys.

    safe_for_decision, synthesis_ready, valuation_ready, and
    etf_fund_intelligence_ready follow strict immutable gate logic — they
    cannot be set True by caller arguments.
    """

    # Identity
    ticker: str
    asset_type: str
    dataset_version: str
    generated_at: str

    # Whether this ticker is an ETF (True) or NOT_APPLICABLE (False).
    etf_applicable: bool

    # Source health provenance — lanes consulted to build this row.
    source_artifacts: list   # list[EtfSourceHealthEntry]

    # ETF fund intelligence sections.
    fund_identity: EtfFundIdentitySection
    cost_and_yield: EtfCostAndYieldSection
    composition: EtfCompositionSection
    trading_and_risk_support: EtfTradingAndRiskSection

    # Missing/degraded reasons by subsection key.
    missing_reasons: dict[str, str]

    # Scaffold presence vs dataset safety — these are distinct.
    # canonical_etf_scaffold_present: True when Stage 9F built a scaffold row for this ETF.
    # canonical_etf_dataset_safe: always False at Stage 9F — composition is MISSING and
    #   no ETF-specific fields are extracted/validated. Scaffold present ≠ dataset safe.
    canonical_etf_scaffold_present: bool  # True for ETF rows built by Stage 9F
    canonical_etf_dataset_safe: bool = False  # always False at Stage 9F

    # Readiness gates (immutable — cannot be True at Stage 9F).
    etf_fund_intelligence_ready: bool = False   # always False at Stage 9F
    valuation_ready: bool = False               # always False
    synthesis_ready: bool = False               # always False
    safe_for_decision: bool = False             # always False

    # Why this ticker is NOT_APPLICABLE for the ETF dataset.
    not_applicable_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "asset_type": self.asset_type,
            "dataset_version": self.dataset_version,
            "generated_at": self.generated_at,
            "etf_applicable": self.etf_applicable,
            "source_artifacts": [s.to_dict() for s in self.source_artifacts],
            "fund_identity": self.fund_identity.to_dict(),
            "cost_and_yield": self.cost_and_yield.to_dict(),
            "composition": self.composition.to_dict(),
            "trading_and_risk_support": self.trading_and_risk_support.to_dict(),
            "missing_reasons": dict(self.missing_reasons),
            "canonical_etf_scaffold_present": self.canonical_etf_scaffold_present,
            "canonical_etf_dataset_safe": self.canonical_etf_dataset_safe,
            "etf_fund_intelligence_ready": self.etf_fund_intelligence_ready,
            "valuation_ready": self.valuation_ready,
            "synthesis_ready": self.synthesis_ready,
            "safe_for_decision": self.safe_for_decision,
            "not_applicable_reason": self.not_applicable_reason,
        }


# ── Public API ─────────────────────────────────────────────────────────────────


def build_canonical_etf_fund_dataset_row(
    *,
    ticker: str,
    asset_type: str,
    lanes: dict[str, LaneCoverage],
) -> CanonicalEtfFundDatasetRow:
    """Build a canonical ETF fund intelligence dataset row for one ticker.

    ETF tickers receive a scaffold row that honestly reflects what is known
    from existing evidence lanes (yfinance fundamentals/technicals metadata).
    Equity and crypto tickers receive a NOT_APPLICABLE row.

    Args:
        ticker: normalized uppercase ticker symbol.
        asset_type: one of equity|etf|crypto|unknown.
        lanes: {lane_name: LaneCoverage} from Stage 5J coverage for this ticker.

    Returns:
        CanonicalEtfFundDatasetRow — always non-None, never raises.
        etf_applicable=True only for ETF asset type.
        etf_fund_intelligence_ready is always False (no fund composition provider).
        synthesis_ready, valuation_ready, safe_for_decision are always False.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    if asset_type != INSTRUMENT_CATEGORY_ETF:
        return _build_not_applicable_row(ticker, asset_type, now_iso)

    fund_cov = lanes.get(LANE_FUNDAMENTALS)
    tech_cov = lanes.get(LANE_TECHNICALS)

    fund_usable = (
        fund_cov is not None
        and fund_cov.artifact_id is not None
        and (fund_cov.usability_label or "") in _USABLE_LABELS
    )
    tech_usable = (
        tech_cov is not None
        and tech_cov.artifact_id is not None
        and (tech_cov.usability_label or "") in _USABLE_LABELS
    )

    source_artifacts = _build_etf_source_health(
        fund_cov=fund_cov,
        tech_cov=tech_cov,
    )

    fund_identity = _build_fund_identity(fund_usable=fund_usable)
    cost_and_yield = _build_cost_and_yield(fund_usable=fund_usable)
    composition = _build_composition()
    trading_and_risk = _build_trading_and_risk(tech_usable=tech_usable)

    missing_reasons: dict[str, str] = {}
    if fund_identity.missing_reason:
        missing_reasons["fund_identity"] = fund_identity.missing_reason
    if cost_and_yield.missing_reason:
        missing_reasons["cost_and_yield"] = cost_and_yield.missing_reason
    if composition.missing_reason:
        missing_reasons["composition"] = composition.missing_reason
    if trading_and_risk.missing_reason:
        missing_reasons["trading_and_risk_support"] = trading_and_risk.missing_reason

    return CanonicalEtfFundDatasetRow(
        ticker=ticker,
        asset_type=asset_type,
        dataset_version=ETF_DATASET_VERSION,
        generated_at=now_iso,
        etf_applicable=True,
        source_artifacts=source_artifacts,
        fund_identity=fund_identity,
        cost_and_yield=cost_and_yield,
        composition=composition,
        trading_and_risk_support=trading_and_risk,
        missing_reasons=missing_reasons,
        canonical_etf_scaffold_present=True,
        canonical_etf_dataset_safe=False,
        etf_fund_intelligence_ready=False,
        valuation_ready=False,
        synthesis_ready=False,
        safe_for_decision=False,
        not_applicable_reason=None,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────


def _build_not_applicable_row(
    ticker: str,
    asset_type: str,
    now_iso: str,
) -> CanonicalEtfFundDatasetRow:
    """Return a NOT_APPLICABLE ETF dataset row for non-ETF tickers."""
    if asset_type == INSTRUMENT_CATEGORY_EQUITY:
        reason = (
            "ETF fund intelligence dataset is not applicable for equity holdings. "
            "Use canonical_equity_dataset_v1 for equity tickers."
        )
    elif asset_type == INSTRUMENT_CATEGORY_CRYPTO:
        reason = (
            "ETF fund intelligence dataset is not applicable for crypto holdings. "
            "Crypto market context requires a dedicated crypto provider lane."
        )
    else:
        reason = (
            f"ETF fund intelligence dataset is not applicable for asset type '{asset_type}'."
        )

    na_identity = EtfFundIdentitySection(
        fund_name_available=False,
        issuer_available=False,
        category_or_index_strategy_available=False,
        missing_reason=reason,
    )
    na_cost_yield = EtfCostAndYieldSection(
        expense_ratio_status=ETF_STATUS_NOT_APPLICABLE,
        dividend_or_distribution_yield_status=ETF_STATUS_NOT_APPLICABLE,
        missing_reason=reason,
    )
    na_composition = EtfCompositionSection(
        holdings_composition_status=ETF_STATUS_NOT_APPLICABLE,
        sector_exposure_status=ETF_STATUS_NOT_APPLICABLE,
        geography_exposure_status=ETF_STATUS_NOT_APPLICABLE,
        concentration_status=ETF_STATUS_NOT_APPLICABLE,
        missing_reason=reason,
    )
    na_trading = EtfTradingAndRiskSection(
        liquidity_proxy_status=ETF_STATUS_NOT_APPLICABLE,
        volatility_or_technical_support_status=ETF_STATUS_NOT_APPLICABLE,
        missing_reason=reason,
    )
    return CanonicalEtfFundDatasetRow(
        ticker=ticker,
        asset_type=asset_type,
        dataset_version=ETF_DATASET_VERSION,
        generated_at=now_iso,
        etf_applicable=False,
        source_artifacts=[],
        fund_identity=na_identity,
        cost_and_yield=na_cost_yield,
        composition=na_composition,
        trading_and_risk_support=na_trading,
        missing_reasons={"all": reason},
        canonical_etf_scaffold_present=False,
        canonical_etf_dataset_safe=False,
        etf_fund_intelligence_ready=False,
        valuation_ready=False,
        synthesis_ready=False,
        safe_for_decision=False,
        not_applicable_reason=reason,
    )


def _build_etf_source_health(
    *,
    fund_cov: Optional[LaneCoverage],
    tech_cov: Optional[LaneCoverage],
) -> list:
    """Build source health provenance entries for ETF dataset lanes."""
    entries = []
    for lane_name, cov in [
        (LANE_FUNDAMENTALS, fund_cov),
        (LANE_TECHNICALS, tech_cov),
    ]:
        if cov is None:
            continue
        entries.append(EtfSourceHealthEntry(
            lane=lane_name,
            artifact_id=cov.artifact_id,
            usability_label=cov.usability_label,
            freshness_status=cov.freshness_status,
            model_version=cov.model_version,
        ))
    return entries


def _build_fund_identity(*, fund_usable: bool) -> EtfFundIdentitySection:
    """Derive fund identity availability from fundamentals lane metadata.

    At Stage 9F, all fund identity fields (fund_name, issuer, category) are
    False regardless of lane usability. A usable yfinance fundamentals artifact
    confirms equity-grade fundamentals arrived; it does NOT mean ETF-specific
    fund metadata (fund name, issuer/fund family, index/strategy category) was
    extracted or validated. No ETF-specific field extraction is implemented at
    Stage 9F. Never infer field presence from lane usability.

    When fundamentals is usable, the missing_reason distinguishes the cause
    ("lane usable but fields not extracted") from the no-lane case.
    """
    if fund_usable:
        return EtfFundIdentitySection(
            fund_name_available=False,
            issuer_available=False,
            category_or_index_strategy_available=False,
            missing_reason=(
                "Fund identity not extracted: fundamentals lane is usable but "
                "ETF-specific fund identity fields (fund name, issuer/fund family, "
                "index/strategy category) are not extracted or validated at Stage 9F. "
                "A dedicated ETF fund data provider or ETF-specific field extraction "
                "is required."
            ),
        )
    return EtfFundIdentitySection(
        fund_name_available=False,
        issuer_available=False,
        category_or_index_strategy_available=False,
        missing_reason=(
            "Fund identity not available: yfinance fundamentals artifact is missing "
            "or not usable for this ETF. Enable INTEL_V3_FUNDAMENTALS_EVIDENCE_ENABLED=true "
            "to populate the fundamentals lane; then a dedicated ETF fund data provider "
            "or ETF-specific field extraction is still required for fund identity."
        ),
    )


def _build_cost_and_yield(*, fund_usable: bool) -> EtfCostAndYieldSection:
    """Derive cost and yield status from fundamentals lane metadata.

    When the yfinance fundamentals artifact is usable, expense ratio and
    distribution yield are marked PARTIAL — not because values were extracted
    and validated, but because the lane exists and these fields may be present
    in the raw artifact. PARTIAL here means "lane signal present but ETF-specific
    cost/yield fields not extracted or validated at Stage 9F". Never AVAILABLE
    without dedicated ETF fund data provider validation.

    When fundamentals is not usable or missing, all statuses are MISSING.
    """
    if fund_usable:
        return EtfCostAndYieldSection(
            expense_ratio_status=ETF_STATUS_PARTIAL,
            dividend_or_distribution_yield_status=ETF_STATUS_PARTIAL,
            missing_reason=(
                "Cost/yield PARTIAL: fundamentals lane usable but ETF-specific expense "
                "ratio and distribution yield are not extracted or validated at Stage 9F. "
                "A dedicated ETF fund data provider is required for verified cost/yield data."
            ),
        )
    return EtfCostAndYieldSection(
        expense_ratio_status=ETF_STATUS_MISSING,
        dividend_or_distribution_yield_status=ETF_STATUS_MISSING,
        missing_reason=ETF_PROVIDER_MISSING_REASON,
    )


def _build_composition() -> EtfCompositionSection:
    """ETF fund composition is always MISSING at Stage 9F.

    No dedicated fund holdings/composition provider is built. Composition,
    sector, geography, and concentration data require a provider like a
    fund-data API (e.g. iShares, State Street API, ETF.com) that returns
    actual holdings. Do not mark these PARTIAL or AVAILABLE without real data.
    """
    return EtfCompositionSection(
        holdings_composition_status=ETF_STATUS_MISSING,
        sector_exposure_status=ETF_STATUS_MISSING,
        geography_exposure_status=ETF_STATUS_MISSING,
        concentration_status=ETF_STATUS_MISSING,
        missing_reason=ETF_COMPOSITION_MISSING_REASON,
    )


def _build_trading_and_risk(*, tech_usable: bool) -> EtfTradingAndRiskSection:
    """Derive trading/risk support from technicals lane metadata.

    When the yfinance technicals artifact is usable, volume and price history
    provide a liquidity proxy and volatility context — marked PARTIAL since
    dedicated ETF market structure analysis (bid/ask spread, premium/discount)
    is not available.

    When technicals is not usable or missing, all statuses are MISSING.
    """
    if tech_usable:
        return EtfTradingAndRiskSection(
            liquidity_proxy_status=ETF_STATUS_PARTIAL,
            volatility_or_technical_support_status=ETF_STATUS_PARTIAL,
            missing_reason=None,
        )
    return EtfTradingAndRiskSection(
        liquidity_proxy_status=ETF_STATUS_MISSING,
        volatility_or_technical_support_status=ETF_STATUS_MISSING,
        missing_reason=(
            "Trading and risk support not available: technicals artifact missing "
            "or not usable. Enable INTEL_V3_TECHNICALS_EVIDENCE_ENABLED=true."
        ),
    )
