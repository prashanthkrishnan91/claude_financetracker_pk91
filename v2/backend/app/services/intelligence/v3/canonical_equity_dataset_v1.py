"""Stage 9D — Canonical Equity Research Dataset v1.

Pure, no-IO read model. Converts trusted equity evidence artifact metadata
(Stage 5J LaneCoverage + optional SEC XBRL fact records from Stage 9B forensics)
into one normalized, auditable per-ticker equity research dataset row.

This dataset is the required foundation for future equity valuation (Stage 9E)
and is a mandatory prerequisite before any synthesis can be attempted.

Architecture contracts (non-negotiable):
  - Pure function. No IO, no DB, no LLM, no external calls.
  - Never imports or calls decide().
  - Never produces Buy/Hold/Trim/Sell authority.
  - safe_for_decision is always False.
  - synthesis_ready is always False.
  - valuation_ready is False at Stage 9D (valuation lane not built yet).
  - Does NOT expose raw metric keys, fact values, source URLs, or API keys.
  - Does NOT fabricate availability signals — only derives from trusted metadata
    or from fact records (values consumed internally, never serialized).
  - Equity-only: ETF and crypto return NOT_APPLICABLE rows.
  - TSM/KLAR/BLSH or any ticker with weak/stale SEC facts get honest
    degraded/missing availability signals — no forced parity.
  - safe_for_equity_dataset is only True when sec_company_facts is USABLE or
    USABLE_WITH_LIMITATIONS for an equity ticker.

Asset-parity contract (explicit):
  Stage 9D builds the canonical dataset for equities only.
  ETFs and crypto must build their own canonical datasets (Stage 9E/9F) before
  synthesis can be gated across all asset classes.
  All three asset classes must reach S-grade foundational data before synthesis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .research_evidence_coverage_read_model_v1 import (
    LANE_FUNDAMENTALS,
    LANE_NEWS_SENTIMENT,
    LANE_SEC_CATALYST_SENTIMENT,
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    LaneCoverage,
)
from .research_evidence_decision_input_adapter_v1 import (
    INSTRUMENT_CATEGORY_CRYPTO,
    INSTRUMENT_CATEGORY_EQUITY,
    INSTRUMENT_CATEGORY_ETF,
    INSTRUMENT_CATEGORY_UNKNOWN,
)

DATASET_VERSION = "canonical_equity_dataset.v1"

# Usability labels that qualify an artifact as truth-usable for this dataset.
_USABLE_LABELS = frozenset({"USABLE", "USABLE_WITH_LIMITATIONS"})

# Freshness labels that indicate data is current enough to use.
_FRESH_LABELS = frozenset({"FRESH", "AGING"})

# Completeness bands used in metadata-fallback section derivation.
_COMPLETENESS_COMPLETE = "COMPLETE"
_COMPLETENESS_PARTIAL = "PARTIAL"
_COMPLETENESS_THIN = "THIN"

# Minimum observation count for AVAILABLE status (metadata-fallback path).
_MIN_OBSERVATIONS_FOR_AVAILABILITY = 3

# Trust label applied to technical context section — always limited trust for decision context.
TECHNICAL_TRUST_LABEL = "LIMITED_TRUST"

# Synthesis gate (non-negotiable until all asset classes have canonical datasets).
SYNTHESIS_GATE_BLOCKED = "BLOCKED_ALL_ASSET_CLASSES_NEED_CANONICAL_DATASETS"

# Valuation gate for equities at Stage 9D.
VALUATION_GATE_BLOCKED = "BLOCKED_VALUATION_LANE_NOT_BUILT"

# ── Section identifiers ────────────────────────────────────────────────────────

SECTION_REVENUE = "revenue"
SECTION_PROFITABILITY = "profitability_or_margin"
SECTION_NET_INCOME_EPS = "net_income_or_eps"
SECTION_CASH_FLOW_FCF = "cash_flow_or_fcf"
SECTION_SHARE_COUNT = "share_count_or_dilution"

ALL_SECTIONS = (
    SECTION_REVENUE,
    SECTION_PROFITABILITY,
    SECTION_NET_INCOME_EPS,
    SECTION_CASH_FLOW_FCF,
    SECTION_SHARE_COUNT,
)

# ── Evidence section status ────────────────────────────────────────────────────

SECTION_STATUS_AVAILABLE = "AVAILABLE"
SECTION_STATUS_PARTIAL = "PARTIAL"
SECTION_STATUS_MISSING = "MISSING"
SECTION_STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"

# ── Trend direction values (internally computed, only direction label exposed) ─

TREND_UP = "UP"
TREND_DOWN = "DOWN"
TREND_FLAT = "FLAT"
TREND_MIXED = "MIXED"
TREND_UNKNOWN = "UNKNOWN"

_VALID_TREND_DIRECTIONS = frozenset({TREND_UP, TREND_DOWN, TREND_FLAT, TREND_MIXED, TREND_UNKNOWN})

# ── Evidence basis values ──────────────────────────────────────────────────────

BASIS_SEC_COMPANYFACTS = "SEC_COMPANYFACTS"
BASIS_UNAVAILABLE = "UNAVAILABLE"

# ── SEC XBRL metric → canonical section mapping (internal, never serialized) ──

_SECTION_METRIC_MAP: dict[str, str] = {
    # Revenue
    "Revenues": SECTION_REVENUE,
    "RevenueFromContractWithCustomerExcludingAssessedTax": SECTION_REVENUE,
    "RevenueFromContractWithCustomerIncludingAssessedTax": SECTION_REVENUE,
    "SalesRevenueNet": SECTION_REVENUE,
    "SalesRevenueGoodsNet": SECTION_REVENUE,
    "SalesRevenueServicesNet": SECTION_REVENUE,
    "RevenueFromContractWithCustomer": SECTION_REVENUE,
    "RevenueFromRelatedParties": SECTION_REVENUE,
    # Profitability / margin
    "GrossProfit": SECTION_PROFITABILITY,
    "OperatingIncomeLoss": SECTION_PROFITABILITY,
    # Net income / EPS
    "NetIncomeLoss": SECTION_NET_INCOME_EPS,
    "NetIncomeLossAvailableToCommonStockholdersBasic": SECTION_NET_INCOME_EPS,
    "NetIncomeLossAvailableToCommonStockholdersDiluted": SECTION_NET_INCOME_EPS,
    "EarningsPerShareBasic": SECTION_NET_INCOME_EPS,
    "EarningsPerShareDiluted": SECTION_NET_INCOME_EPS,
    # Cash flow / FCF
    "NetCashProvidedByUsedInOperatingActivities": SECTION_CASH_FLOW_FCF,
    "NetCashProvidedByUsedInInvestingActivities": SECTION_CASH_FLOW_FCF,
    "PaymentsToAcquirePropertyPlantAndEquipment": SECTION_CASH_FLOW_FCF,
    # Share count / dilution
    "CommonStockSharesOutstanding": SECTION_SHARE_COUNT,
    "CommonStockSharesIssued": SECTION_SHARE_COUNT,
    "WeightedAverageNumberOfSharesOutstandingBasic": SECTION_SHARE_COUNT,
    "WeightedAverageNumberOfDilutedSharesOutstanding": SECTION_SHARE_COUNT,
    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted": SECTION_SHARE_COUNT,
}


# ── Period identity dataclass ──────────────────────────────────────────────────


@dataclass
class PeriodIdentity:
    """Safe period identity for one financial observation. No raw fact values."""

    fiscal_year: Optional[int]
    fiscal_period: Optional[str]   # "FY" | "Q1" | "Q2" | "Q3" | "Q4"
    period_end: Optional[str]      # ISO date e.g. "2024-09-28"
    unit: Optional[str]            # "USD" | "shares"
    form: Optional[str]            # "10-K" | "10-Q"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "period_end": self.period_end,
            "unit": self.unit,
            "form": self.form,
        }


# ── Evidence section record ────────────────────────────────────────────────────


@dataclass
class EvidenceSectionRecord:
    """Normalized evidence record for one operating-trends section.

    The primary source of truth for one financial category (revenue, margins,
    etc.) within the canonical equity dataset. Safe for downstream adapters.

    Raw fact values are NEVER stored or serialized here.
    trend_direction is derived internally from raw values (when available) but
    only the direction label (UP/DOWN/FLAT/MIXED/UNKNOWN) is retained.
    """

    section: str
    status: str            # AVAILABLE | PARTIAL | MISSING | NOT_APPLICABLE
    evidence_basis: str    # SEC_COMPANYFACTS | UNAVAILABLE
    latest_period_identity: Optional[PeriodIdentity]
    comparison_period_identity: Optional[PeriodIdentity]
    trend_direction: str   # UP | DOWN | FLAT | MIXED | UNKNOWN
    source_artifact_id: Optional[str]
    missing_reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "status": self.status,
            "evidence_basis": self.evidence_basis,
            "latest_period_identity": (
                self.latest_period_identity.to_dict()
                if self.latest_period_identity else None
            ),
            "comparison_period_identity": (
                self.comparison_period_identity.to_dict()
                if self.comparison_period_identity else None
            ),
            "trend_direction": self.trend_direction,
            "source_artifact_id": self.source_artifact_id,
            "missing_reason": self.missing_reason,
        }


# ── Section dataclasses ────────────────────────────────────────────────────────


@dataclass
class OperatingTrendSection:
    """Section-level normalized evidence records for operating trends.

    The primary source of truth is the `sections` dict — one EvidenceSectionRecord
    per financial category (revenue, profitability, net income/EPS, FCF, share count).

    When sec_fact_records are provided to the builder, sections contain real period
    identities (fiscal_year, period_end, unit, form) and an internally-computed
    trend_direction. When only artifact metadata is available (metadata fallback),
    sections contain derived status/basis but period identities are None and
    trend_direction is UNKNOWN.

    Backward-compatible boolean properties (revenue_trend_available, etc.) delegate
    to sections[...].status == AVAILABLE for legacy callers.

    No raw fact values are stored or serialized anywhere in this structure.
    """

    # Per-section normalized evidence records (primary source of truth).
    sections: dict  # dict[str, EvidenceSectionRecord]

    # Artifact-level metadata governing all sections.
    trend_source: str          # "sec_company_facts" | "unavailable"
    observation_count: Optional[int]
    completeness_band: Optional[str]
    freshness_status: Optional[str]
    usability_label: Optional[str]
    missing_reason: Optional[str]

    # ── Backward-compatible availability signals ──────────────────────────────
    # True only when status == AVAILABLE (not just PARTIAL) to preserve
    # existing behavior: "sufficient data for this section".

    @property
    def revenue_trend_available(self) -> bool:
        s = self.sections.get(SECTION_REVENUE)
        return s is not None and s.status == SECTION_STATUS_AVAILABLE

    @property
    def profitability_margin_available(self) -> bool:
        s = self.sections.get(SECTION_PROFITABILITY)
        return s is not None and s.status == SECTION_STATUS_AVAILABLE

    @property
    def eps_net_income_available(self) -> bool:
        s = self.sections.get(SECTION_NET_INCOME_EPS)
        return s is not None and s.status == SECTION_STATUS_AVAILABLE

    @property
    def fcf_available(self) -> bool:
        s = self.sections.get(SECTION_CASH_FLOW_FCF)
        return s is not None and s.status == SECTION_STATUS_AVAILABLE

    @property
    def share_count_dilution_available(self) -> bool:
        s = self.sections.get(SECTION_SHARE_COUNT)
        return s is not None and s.status == SECTION_STATUS_AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            # Primary: per-section normalized evidence records.
            "sections": {k: v.to_dict() for k, v in self.sections.items()},
            # Artifact-level metadata.
            "trend_source": self.trend_source,
            "observation_count": self.observation_count,
            "completeness_band": self.completeness_band,
            "freshness_status": self.freshness_status,
            "usability_label": self.usability_label,
            "missing_reason": self.missing_reason,
            # Backward-compatible availability signals (derived from sections).
            "revenue_trend_available": self.revenue_trend_available,
            "profitability_margin_available": self.profitability_margin_available,
            "eps_net_income_available": self.eps_net_income_available,
            "fcf_available": self.fcf_available,
            "share_count_dilution_available": self.share_count_dilution_available,
        }


@dataclass
class CatalystContextSection:
    """SEC catalyst availability context. No LLM summary — counts and status only."""

    catalyst_available: bool
    catalyst_count: Optional[int]    # fact count from research_artifact_facts
    catalyst_source: str             # "sec_catalyst_sentiment" | "unavailable"
    catalyst_usability: Optional[str]
    missing_reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalyst_available": self.catalyst_available,
            "catalyst_count": self.catalyst_count,
            "catalyst_source": self.catalyst_source,
            "catalyst_usability": self.catalyst_usability,
            "missing_reason": self.missing_reason,
        }


@dataclass
class TechnicalSupportSection:
    """Technical evidence context. Included only as supporting context with limited trust."""

    technical_available: bool
    technical_usability: Optional[str]
    trust_label: str = TECHNICAL_TRUST_LABEL   # always LIMITED_TRUST
    missing_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "technical_available": self.technical_available,
            "technical_usability": self.technical_usability,
            "trust_label": self.trust_label,
            "missing_reason": self.missing_reason,
        }


@dataclass
class SourceHealthEntry:
    """Safe provenance entry for one evidence lane artifact used in this dataset row."""

    lane: str
    artifact_id: Optional[str]
    usability_label: Optional[str]
    freshness_status: Optional[str]
    model_version: Optional[str]
    is_current_model: bool   # True when model_version matches expected current version

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "artifact_id": self.artifact_id,
            "usability_label": self.usability_label,
            "freshness_status": self.freshness_status,
            "model_version": self.model_version,
            "is_current_model": self.is_current_model,
        }


# ── Row dataclass ──────────────────────────────────────────────────────────────


@dataclass
class CanonicalEquityDatasetRow:
    """Per-ticker canonical equity research dataset row.

    Safe for diagnostics and downstream adapters. No raw payloads, no source
    URLs, no fact values, no API keys.

    safe_for_decision, synthesis_ready, and valuation_ready follow strict
    immutable gate logic — they cannot be set True by caller arguments.
    """

    # Identity
    ticker: str
    asset_type: str
    company_applicable: bool     # True only for equity asset type
    dataset_version: str
    generated_at: str

    # Source health provenance
    source_artifacts: list       # list[SourceHealthEntry]

    # Evidence sections
    operating_trends: OperatingTrendSection
    catalyst_context: CatalystContextSection
    technical_context: TechnicalSupportSection

    # Missing/degraded reasons per section key
    missing_section_reasons: dict[str, str]

    # Readiness gates (immutable)
    safe_for_equity_dataset: bool
    valuation_ready: bool = False    # always False at Stage 9D
    synthesis_ready: bool = False    # always False
    safe_for_decision: bool = False  # always False

    # Why this ticker is not safe for the equity dataset (when safe_for_equity_dataset=False)
    not_safe_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "asset_type": self.asset_type,
            "company_applicable": self.company_applicable,
            "dataset_version": self.dataset_version,
            "generated_at": self.generated_at,
            "source_artifacts": [s.to_dict() for s in self.source_artifacts],
            "operating_trends": self.operating_trends.to_dict(),
            "catalyst_context": self.catalyst_context.to_dict(),
            "technical_context": self.technical_context.to_dict(),
            "missing_section_reasons": dict(self.missing_section_reasons),
            "safe_for_equity_dataset": self.safe_for_equity_dataset,
            "valuation_ready": self.valuation_ready,
            "synthesis_ready": self.synthesis_ready,
            "safe_for_decision": self.safe_for_decision,
            "not_safe_reason": self.not_safe_reason,
        }


# ── Asset parity roadmap ───────────────────────────────────────────────────────


@dataclass
class AssetClassFoundationGap:
    """Foundation gap summary for one asset class.

    Machine-readable summary of what is built, what is missing, and what must
    be built before synthesis can be gated for this asset class.
    """

    asset_class: str          # "equity" | "etf" | "crypto"
    canonical_dataset_built: bool
    valuation_lane_built: bool
    synthesis_gate: str       # why synthesis is blocked for this class
    edge_cases: Optional[str] = None   # e.g., "3 equities SEC weak/stale"

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_class": self.asset_class,
            "canonical_dataset_built": self.canonical_dataset_built,
            "valuation_lane_built": self.valuation_lane_built,
            "synthesis_gate": self.synthesis_gate,
            "edge_cases": self.edge_cases,
        }


@dataclass
class AssetParityRoadmap:
    """Portfolio-level asset-class parity summary.

    Explicitly tracks that all three asset classes (equity, ETF, crypto) must
    reach S-grade foundational data through their own canonical datasets before
    synthesis can proceed. Do not allow partial parity to unlock synthesis.
    """

    parity_version: str
    generated_at: str
    asset_classes: list   # list[AssetClassFoundationGap]
    all_classes_synthesis_ready: bool = False    # always False at Stage 9D
    parity_note: str = (
        "All asset classes must have S-grade canonical datasets before synthesis. "
        "Stage 9D covers equities only. ETF composition/provider and crypto "
        "market-context/provider lanes are required next before synthesis gates open."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "parity_version": self.parity_version,
            "generated_at": self.generated_at,
            "asset_classes": [ac.to_dict() for ac in self.asset_classes],
            "all_classes_synthesis_ready": self.all_classes_synthesis_ready,
            "parity_note": self.parity_note,
        }


# ── Public API ─────────────────────────────────────────────────────────────────


def build_canonical_equity_dataset_row(
    *,
    ticker: str,
    asset_type: str,
    lanes: dict[str, LaneCoverage],
    sec_obs_count: Optional[int],
    cat_count: Optional[int],
    sec_fact_records: Optional[list[dict]] = None,
) -> CanonicalEquityDatasetRow:
    """Build a canonical equity dataset row for one ticker.

    Args:
        ticker: normalized uppercase ticker symbol.
        asset_type: one of equity|etf|crypto|unknown.
        lanes: {lane_name: LaneCoverage} from Stage 5J coverage for this ticker.
        sec_obs_count: COUNT of research_artifact_facts for the SEC company facts
            artifact_id (already fetched by Stage 9B supplemental queries).
        cat_count: COUNT of research_artifact_facts for the SEC catalyst artifact_id.
        sec_fact_records: Optional list of structured_payload dicts from
            research_artifact_facts for the SEC company facts artifact. When
            provided, enables per-section period identities and internally-computed
            trend directions. When absent, falls back to metadata proxy. Raw
            values in these records are used internally and never serialized.

    Returns:
        CanonicalEquityDatasetRow — always non-None, never raises.
        safe_for_equity_dataset is True only for equity + USABLE/USABLE_WITH_LIMITATIONS.
        synthesis_ready and valuation_ready are always False.
        safe_for_decision is always False.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # Non-equity holdings: return a NOT_APPLICABLE row.
    if asset_type != INSTRUMENT_CATEGORY_EQUITY:
        return _build_not_applicable_row(ticker, asset_type, now_iso)

    sec_cov = lanes.get(LANE_SEC_COMPANY_FACTS)
    fund_cov = lanes.get(LANE_FUNDAMENTALS)
    tech_cov = lanes.get(LANE_TECHNICALS)
    cat_cov = lanes.get(LANE_SEC_CATALYST_SENTIMENT)

    sec_usability = (sec_cov.usability_label if sec_cov else None) or ""
    sec_is_usable = sec_usability in _USABLE_LABELS

    # Source health provenance.
    from .sec_companyfacts_readiness_diagnostic_v1 import SEC_COMPANYFACTS_CURRENT_MODEL_VERSION
    source_artifacts = _build_source_health(
        sec_cov=sec_cov,
        fund_cov=fund_cov,
        tech_cov=tech_cov,
        cat_cov=cat_cov,
        current_sec_model=SEC_COMPANYFACTS_CURRENT_MODEL_VERSION,
    )

    # Operating trends — section-level normalized evidence records from SEC company facts.
    operating_trends = _build_operating_trends(
        sec_cov=sec_cov,
        sec_obs_count=sec_obs_count,
        sec_is_usable=sec_is_usable,
        sec_fact_records=sec_fact_records,
    )

    # Catalyst context — from SEC catalyst sentiment lane.
    catalyst_context = _build_catalyst_context(
        cat_cov=cat_cov,
        cat_count=cat_count,
    )

    # Technical context — supporting only, limited trust.
    technical_context = _build_technical_context(tech_cov=tech_cov)

    # Collect missing section reasons.
    missing_reasons: dict[str, str] = {}
    if operating_trends.missing_reason:
        missing_reasons["operating_trends"] = operating_trends.missing_reason
    if catalyst_context.missing_reason:
        missing_reasons["catalyst_context"] = catalyst_context.missing_reason
    if technical_context.missing_reason:
        missing_reasons["technical_context"] = technical_context.missing_reason

    # safe_for_equity_dataset gate.
    if sec_is_usable:
        safe_for_equity_dataset = True
        not_safe_reason = None
    else:
        safe_for_equity_dataset = False
        not_safe_reason = _derive_not_safe_reason(sec_cov, sec_usability)

    return CanonicalEquityDatasetRow(
        ticker=ticker,
        asset_type=asset_type,
        company_applicable=True,
        dataset_version=DATASET_VERSION,
        generated_at=now_iso,
        source_artifacts=source_artifacts,
        operating_trends=operating_trends,
        catalyst_context=catalyst_context,
        technical_context=technical_context,
        missing_section_reasons=missing_reasons,
        safe_for_equity_dataset=safe_for_equity_dataset,
        valuation_ready=False,
        synthesis_ready=False,
        safe_for_decision=False,
        not_safe_reason=not_safe_reason,
    )


def build_asset_parity_roadmap(
    *,
    equity_canonical_count: int,
    equity_total: int,
    equity_edge_case_tickers: list,
    etf_total: int,
    crypto_total: int,
) -> AssetParityRoadmap:
    """Build the portfolio-level asset-class parity roadmap.

    Shows remaining foundation gaps by asset class. Must be updated with every
    stage that advances any asset class toward synthesis readiness.

    Stage 9D state:
      equity: canonical dataset built for USABLE equities / valuation lane missing
      ETF: fund composition/provider lane missing
      crypto: crypto market context/provider lane missing
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    equity_canonical_built = equity_canonical_count > 0
    equity_edge = None
    if equity_edge_case_tickers:
        tickers_str = ", ".join(sorted(equity_edge_case_tickers))
        equity_edge = (
            f"{len(equity_edge_case_tickers)} of {equity_total} equities "
            f"SEC weak/stale/no-facts: {tickers_str}"
        )

    equity_gap = AssetClassFoundationGap(
        asset_class="equity",
        canonical_dataset_built=equity_canonical_built,
        valuation_lane_built=False,
        synthesis_gate=(
            VALUATION_GATE_BLOCKED
            if equity_canonical_built
            else SYNTHESIS_GATE_BLOCKED
        ),
        edge_cases=equity_edge,
    )

    etf_gap = AssetClassFoundationGap(
        asset_class="etf",
        canonical_dataset_built=False,
        valuation_lane_built=False,
        synthesis_gate=SYNTHESIS_GATE_BLOCKED,
        edge_cases=(
            f"{etf_total} ETF(s) need fund composition/provider lane"
            if etf_total > 0 else None
        ),
    )

    crypto_gap = AssetClassFoundationGap(
        asset_class="crypto",
        canonical_dataset_built=False,
        valuation_lane_built=False,
        synthesis_gate=SYNTHESIS_GATE_BLOCKED,
        edge_cases=(
            f"{crypto_total} crypto holding(s) need crypto market context/provider lane"
            if crypto_total > 0 else None
        ),
    )

    return AssetParityRoadmap(
        parity_version="asset_parity_roadmap.v1",
        generated_at=now_iso,
        asset_classes=[equity_gap, etf_gap, crypto_gap],
        all_classes_synthesis_ready=False,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────


def _build_missing_section_record(section: str, reason: str) -> EvidenceSectionRecord:
    return EvidenceSectionRecord(
        section=section,
        status=SECTION_STATUS_MISSING,
        evidence_basis=BASIS_UNAVAILABLE,
        latest_period_identity=None,
        comparison_period_identity=None,
        trend_direction=TREND_UNKNOWN,
        source_artifact_id=None,
        missing_reason=reason,
    )


def _build_not_applicable_section_record(section: str, reason: str) -> EvidenceSectionRecord:
    return EvidenceSectionRecord(
        section=section,
        status=SECTION_STATUS_NOT_APPLICABLE,
        evidence_basis=BASIS_UNAVAILABLE,
        latest_period_identity=None,
        comparison_period_identity=None,
        trend_direction=TREND_UNKNOWN,
        source_artifact_id=None,
        missing_reason=reason,
    )


def _compute_trend_direction(v_latest: float, v_prior: float) -> str:
    """Compute trend direction from two consecutive values. Internal use only.

    Raw values are NEVER exposed outside this function — only the direction
    string is returned and stored in EvidenceSectionRecord.trend_direction.
    """
    try:
        if v_prior == 0:
            return TREND_UNKNOWN
        pct_change = (v_latest - v_prior) / abs(v_prior)
        if pct_change > 0.05:
            return TREND_UP
        if pct_change < -0.05:
            return TREND_DOWN
        return TREND_FLAT
    except (TypeError, ZeroDivisionError, ValueError):
        return TREND_UNKNOWN


def _compute_section_records_from_facts(
    *,
    sec_fact_records: list[dict],
    sec_is_usable: bool,
    artifact_id: Optional[str],
    missing_reason: str,
) -> dict:
    """Build per-section evidence records from actual SEC XBRL fact records.

    Computes trend_direction internally from raw values in structured_payload.
    NEVER serializes raw values — only direction strings and period identities.

    Considers only annual observations (fiscal_period == "FY" or form == "10-K").
    """
    if not sec_is_usable or not sec_fact_records:
        return {s: _build_missing_section_record(s, missing_reason) for s in ALL_SECTIONS}

    # Group annual observations by section.
    section_obs: dict[str, list[dict]] = {s: [] for s in ALL_SECTIONS}
    for record in sec_fact_records:
        metric_name = record.get("metric_name", "")
        section = _SECTION_METRIC_MAP.get(metric_name)
        if section is None:
            continue
        fp = (record.get("fiscal_period") or "").upper()
        form = (record.get("form") or "").upper()
        is_annual = (fp == "FY") or ("10-K" in form)
        if not is_annual:
            continue
        if record.get("fiscal_year") is None:
            continue
        section_obs[section].append(record)

    result: dict[str, EvidenceSectionRecord] = {}
    for section in ALL_SECTIONS:
        obs = section_obs[section]
        obs_sorted = sorted(
            obs,
            key=lambda r: (r.get("fiscal_year") or 0),
            reverse=True,
        )

        if not obs_sorted:
            result[section] = _build_missing_section_record(
                section,
                f"No annual observations found for {section} section in SEC facts.",
            )
            continue

        latest = obs_sorted[0]
        latest_period = PeriodIdentity(
            fiscal_year=latest.get("fiscal_year"),
            fiscal_period=latest.get("fiscal_period"),
            period_end=latest.get("period_end"),
            unit=latest.get("unit"),
            form=latest.get("form"),
        )

        comparison_period: Optional[PeriodIdentity] = None
        trend_direction = TREND_UNKNOWN

        if len(obs_sorted) >= 2:
            prior = obs_sorted[1]
            comparison_period = PeriodIdentity(
                fiscal_year=prior.get("fiscal_year"),
                fiscal_period=prior.get("fiscal_period"),
                period_end=prior.get("period_end"),
                unit=prior.get("unit"),
                form=prior.get("form"),
            )
            # Compute trend direction from raw values — internally only, never serialized.
            try:
                v_latest = float(latest.get("value") or 0)
                v_prior = float(prior.get("value") or 0)
                trend_direction = _compute_trend_direction(v_latest, v_prior)
            except (TypeError, ValueError):
                trend_direction = TREND_UNKNOWN

        status = SECTION_STATUS_AVAILABLE if len(obs_sorted) >= 2 else SECTION_STATUS_PARTIAL

        result[section] = EvidenceSectionRecord(
            section=section,
            status=status,
            evidence_basis=BASIS_SEC_COMPANYFACTS,
            latest_period_identity=latest_period,
            comparison_period_identity=comparison_period,
            trend_direction=trend_direction,
            source_artifact_id=artifact_id,
            missing_reason=None,
        )

    return result


def _compute_section_records_from_metadata(
    *,
    sec_cov: LaneCoverage,
    sec_obs_count: Optional[int],
    sec_is_usable: bool,
    missing_reason: str,
) -> dict:
    """Derive section records from artifact metadata when fact records are unavailable.

    Uses completeness_band + observation_count as proxies for section availability.
    Period identities and trend directions are UNKNOWN in this path — only the
    status and evidence_basis can be derived from metadata alone.
    """
    if not sec_is_usable:
        return {s: _build_missing_section_record(s, missing_reason) for s in ALL_SECTIONS}

    completeness = (sec_cov.completeness_band or "").upper()
    freshness = (sec_cov.freshness_status or "").upper()
    obs = sec_obs_count or 0

    is_fresh = freshness in {s.upper() for s in _FRESH_LABELS}
    has_observations = obs >= _MIN_OBSERVATIONS_FOR_AVAILABILITY
    artifact_id = sec_cov.artifact_id

    result: dict[str, EvidenceSectionRecord] = {}
    for section in ALL_SECTIONS:
        if section == SECTION_CASH_FLOW_FCF:
            # FCF requires COMPLETE completeness + high observation count.
            sufficient_complete = (
                completeness == _COMPLETENESS_COMPLETE
                and is_fresh
                and obs >= _MIN_OBSERVATIONS_FOR_AVAILABILITY * 3
            )
            sufficient_partial = (
                completeness in (_COMPLETENESS_PARTIAL, _COMPLETENESS_COMPLETE)
                and is_fresh
                and has_observations
            )
            if sufficient_complete:
                status = SECTION_STATUS_AVAILABLE
            elif sufficient_partial:
                status = SECTION_STATUS_PARTIAL
            else:
                status = SECTION_STATUS_MISSING
        else:
            # Core sections: PARTIAL or COMPLETE completeness + fresh + sufficient obs.
            sufficient = (
                completeness in (_COMPLETENESS_PARTIAL, _COMPLETENESS_COMPLETE)
                and is_fresh
                and has_observations
            )
            if sufficient:
                status = SECTION_STATUS_AVAILABLE
            elif (
                completeness in (_COMPLETENESS_PARTIAL, _COMPLETENESS_COMPLETE)
                and is_fresh
                and obs > 0
            ):
                status = SECTION_STATUS_PARTIAL
            else:
                status = SECTION_STATUS_MISSING

        result[section] = EvidenceSectionRecord(
            section=section,
            status=status,
            evidence_basis=(
                BASIS_SEC_COMPANYFACTS
                if status in (SECTION_STATUS_AVAILABLE, SECTION_STATUS_PARTIAL)
                else BASIS_UNAVAILABLE
            ),
            latest_period_identity=None,   # not derivable from metadata alone
            comparison_period_identity=None,
            trend_direction=TREND_UNKNOWN,  # not derivable from metadata alone
            source_artifact_id=(
                artifact_id
                if status in (SECTION_STATUS_AVAILABLE, SECTION_STATUS_PARTIAL)
                else None
            ),
            missing_reason=(
                None
                if status in (SECTION_STATUS_AVAILABLE, SECTION_STATUS_PARTIAL)
                else (
                    f"Insufficient XBRL observations for {section} section "
                    f"(completeness={completeness or 'unknown'})."
                )
            ),
        )

    return result


def _build_not_applicable_row(
    ticker: str,
    asset_type: str,
    now_iso: str,
) -> CanonicalEquityDatasetRow:
    """Return a NOT_APPLICABLE dataset row for non-equity tickers."""
    reason = (
        "ETF fund composition/provider lane not built — canonical equity dataset "
        "is not applicable for ETF holdings."
        if asset_type == INSTRUMENT_CATEGORY_ETF
        else (
            "Crypto market context/provider lane not built — canonical equity dataset "
            "is not applicable for crypto holdings."
            if asset_type == INSTRUMENT_CATEGORY_CRYPTO
            else f"Asset type '{asset_type}' is not applicable for the equity dataset."
        )
    )
    sections = {s: _build_not_applicable_section_record(s, reason) for s in ALL_SECTIONS}
    unavailable_trends = OperatingTrendSection(
        sections=sections,
        trend_source="unavailable",
        observation_count=None,
        completeness_band=None,
        freshness_status=None,
        usability_label=None,
        missing_reason=reason,
    )
    unavailable_catalyst = CatalystContextSection(
        catalyst_available=False,
        catalyst_count=None,
        catalyst_source="unavailable",
        catalyst_usability=None,
        missing_reason=reason,
    )
    unavailable_tech = TechnicalSupportSection(
        technical_available=False,
        technical_usability=None,
        trust_label=TECHNICAL_TRUST_LABEL,
        missing_reason=reason,
    )
    return CanonicalEquityDatasetRow(
        ticker=ticker,
        asset_type=asset_type,
        company_applicable=False,
        dataset_version=DATASET_VERSION,
        generated_at=now_iso,
        source_artifacts=[],
        operating_trends=unavailable_trends,
        catalyst_context=unavailable_catalyst,
        technical_context=unavailable_tech,
        missing_section_reasons={"all": reason},
        safe_for_equity_dataset=False,
        valuation_ready=False,
        synthesis_ready=False,
        safe_for_decision=False,
        not_safe_reason=reason,
    )


def _build_source_health(
    *,
    sec_cov: Optional[LaneCoverage],
    fund_cov: Optional[LaneCoverage],
    tech_cov: Optional[LaneCoverage],
    cat_cov: Optional[LaneCoverage],
    current_sec_model: str,
) -> list:
    """Build source health provenance entries for lanes used in this row."""
    entries = []
    for lane_name, cov, is_current_fn in [
        (LANE_SEC_COMPANY_FACTS, sec_cov, lambda mv: mv == current_sec_model),
        (LANE_FUNDAMENTALS, fund_cov, lambda mv: bool(mv)),
        (LANE_TECHNICALS, tech_cov, lambda mv: bool(mv)),
        (LANE_SEC_CATALYST_SENTIMENT, cat_cov, lambda mv: bool(mv)),
    ]:
        if cov is None:
            continue
        entries.append(SourceHealthEntry(
            lane=lane_name,
            artifact_id=cov.artifact_id,
            usability_label=cov.usability_label,
            freshness_status=cov.freshness_status,
            model_version=cov.model_version,
            is_current_model=is_current_fn(cov.model_version or ""),
        ))
    return entries


def _build_operating_trends(
    *,
    sec_cov: Optional[LaneCoverage],
    sec_obs_count: Optional[int],
    sec_is_usable: bool,
    sec_fact_records: Optional[list[dict]] = None,
) -> OperatingTrendSection:
    """Build section-level normalized evidence records for operating trends.

    When sec_fact_records are provided:
      - Groups annual observations by section using SEC XBRL metric mapping.
      - Extracts period identities (fiscal_year, period_end, unit, form).
      - Computes trend_direction internally from raw values (never serialized).

    When sec_fact_records are absent (metadata fallback):
      - Derives section status from completeness_band + observation_count proxy.
      - Period identities are None; trend_direction is UNKNOWN.

    No raw XBRL metric keys, fact values, or accession numbers are serialized.
    """
    missing_reason_base = _derive_not_safe_reason(
        sec_cov, (sec_cov.usability_label if sec_cov else None) or ""
    )

    if not sec_is_usable or sec_cov is None:
        reason = missing_reason_base or "SEC company facts artifact is not usable."
        sections = {s: _build_missing_section_record(s, reason) for s in ALL_SECTIONS}
        return OperatingTrendSection(
            sections=sections,
            trend_source="unavailable",
            observation_count=sec_obs_count,
            completeness_band=sec_cov.completeness_band if sec_cov else None,
            freshness_status=sec_cov.freshness_status if sec_cov else None,
            usability_label=sec_cov.usability_label if sec_cov else None,
            missing_reason=reason,
        )

    if sec_fact_records:
        sections = _compute_section_records_from_facts(
            sec_fact_records=sec_fact_records,
            sec_is_usable=sec_is_usable,
            artifact_id=sec_cov.artifact_id,
            missing_reason=missing_reason_base or "SEC company facts artifact is not usable.",
        )
    else:
        sections = _compute_section_records_from_metadata(
            sec_cov=sec_cov,
            sec_obs_count=sec_obs_count,
            sec_is_usable=sec_is_usable,
            missing_reason=missing_reason_base or "Fact records not available for section derivation.",
        )

    return OperatingTrendSection(
        sections=sections,
        trend_source=LANE_SEC_COMPANY_FACTS,
        observation_count=sec_obs_count,
        completeness_band=sec_cov.completeness_band,
        freshness_status=sec_cov.freshness_status,
        usability_label=sec_cov.usability_label,
        missing_reason=None,
    )


def _build_catalyst_context(
    *,
    cat_cov: Optional[LaneCoverage],
    cat_count: Optional[int],
) -> CatalystContextSection:
    """Build catalyst context section from SEC catalyst sentiment lane metadata."""
    if cat_cov is None or cat_cov.artifact_id is None:
        return CatalystContextSection(
            catalyst_available=False,
            catalyst_count=None,
            catalyst_source="unavailable",
            catalyst_usability=None,
            missing_reason=(
                "SEC catalyst sentiment lane has not run or no catalyst artifact exists. "
                "Enable INTEL_V3_SENTIMENT_CATALYST_EVIDENCE_ENABLED=true to populate."
            ),
        )

    cat_usability = (cat_cov.usability_label or "")
    cat_is_usable = cat_usability in _USABLE_LABELS

    return CatalystContextSection(
        catalyst_available=cat_is_usable,
        catalyst_count=cat_count,
        catalyst_source=LANE_SEC_CATALYST_SENTIMENT,
        catalyst_usability=cat_cov.usability_label,
        missing_reason=(
            None
            if cat_is_usable
            else f"SEC catalyst artifact exists but usability is {cat_usability or 'unknown'}."
        ),
    )


def _build_technical_context(
    *,
    tech_cov: Optional[LaneCoverage],
) -> TechnicalSupportSection:
    """Build technical context section. Always marked LIMITED_TRUST."""
    if tech_cov is None or tech_cov.artifact_id is None:
        return TechnicalSupportSection(
            technical_available=False,
            technical_usability=None,
            trust_label=TECHNICAL_TRUST_LABEL,
            missing_reason=(
                "Technical signal artifact not found. Enable "
                "INTEL_V3_TECHNICALS_EVIDENCE_ENABLED=true to populate."
            ),
        )

    tech_usability = (tech_cov.usability_label or "")
    tech_is_usable = tech_usability in _USABLE_LABELS

    return TechnicalSupportSection(
        technical_available=tech_is_usable,
        technical_usability=tech_cov.usability_label,
        trust_label=TECHNICAL_TRUST_LABEL,
        missing_reason=(
            None
            if tech_is_usable
            else f"Technical artifact exists but usability is {tech_usability or 'unknown'}."
        ),
    )


def _derive_not_safe_reason(
    sec_cov: Optional[LaneCoverage],
    sec_usability: str,
) -> str:
    """Derive a plain-English reason why this ticker is not safe for equity dataset."""
    if sec_cov is None or sec_cov.artifact_id is None:
        return (
            "SEC company facts artifact is missing. "
            "Run POST /intel/v3/run with INTEL_V3_SEC_COMPANYFACTS_EVIDENCE_ENABLED=true."
        )
    if not sec_usability or sec_usability.startswith("SUPPRESSED_"):
        return (
            f"SEC company facts artifact is suppressed (usability={sec_usability or 'None'}). "
            "Investigate sec_companyfacts_readiness_diagnostic for this ticker — "
            "likely causes: stale model version, XBRL contradiction, or THIN completeness."
        )
    if sec_usability == "NOT_EVALUABLE":
        return (
            "SEC company facts artifact is NOT_EVALUABLE. "
            "Re-run POST /intel/v3/run to regenerate with full enrichment pipeline."
        )
    return f"SEC company facts artifact has unexpected usability label: {sec_usability!r}."
