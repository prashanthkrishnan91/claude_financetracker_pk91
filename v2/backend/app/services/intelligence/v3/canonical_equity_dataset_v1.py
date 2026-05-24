"""Stage 9D — Canonical Equity Research Dataset v1.

Pure, no-IO read model. Converts trusted equity evidence artifact metadata
(Stage 5J LaneCoverage + supplemental fact counts from Stage 9B forensics)
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
  - Does NOT fabricate availability signals — only derives from trusted metadata.
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

# Completeness bands for deriving section availability signals.
_COMPLETENESS_COMPLETE = "COMPLETE"
_COMPLETENESS_PARTIAL = "PARTIAL"
_COMPLETENESS_THIN = "THIN"

# Per-section minimum completeness to mark as available.
# Revenue/EPS/profitability: PARTIAL is enough (they appear in most 10-K/10-Q).
# FCF: needs COMPLETE (requires both operating cash and CapEx metrics).
_SECTION_MIN_COMPLETENESS = {
    "revenue_trend": _COMPLETENESS_PARTIAL,
    "profitability_margin": _COMPLETENESS_PARTIAL,
    "eps_net_income": _COMPLETENESS_PARTIAL,
    "fcf": _COMPLETENESS_COMPLETE,
    "share_count_dilution": _COMPLETENESS_PARTIAL,
}

# Minimum observation count proxy for availability (SEC XBRL typically has many observations).
_MIN_OBSERVATIONS_FOR_AVAILABILITY = 3

# Trust label applied to technical context section — always limited trust for decision context.
TECHNICAL_TRUST_LABEL = "LIMITED_TRUST"

# Synthesis gate (non-negotiable until all asset classes have canonical datasets).
SYNTHESIS_GATE_BLOCKED = "BLOCKED_ALL_ASSET_CLASSES_NEED_CANONICAL_DATASETS"

# Valuation gate for equities at Stage 9D.
VALUATION_GATE_BLOCKED = "BLOCKED_VALUATION_LANE_NOT_BUILT"


# ── Section dataclasses ────────────────────────────────────────────────────────


@dataclass
class OperatingTrendSection:
    """Availability signals for operating trend inputs derived from SEC artifact metadata.

    These are AVAILABILITY signals, not actual values.
    Fields indicate whether sufficient artifact metadata exists to derive each
    signal category — not the actual revenue/EPS/FCF numbers.
    """

    revenue_trend_available: bool
    profitability_margin_available: bool
    eps_net_income_available: bool
    fcf_available: bool
    share_count_dilution_available: bool

    # Metadata that governs the availability signals above.
    trend_source: str          # "sec_company_facts" | "unavailable"
    observation_count: Optional[int]
    completeness_band: Optional[str]
    freshness_status: Optional[str]
    usability_label: Optional[str]

    missing_reason: Optional[str]  # non-None when trend_source == "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "revenue_trend_available": self.revenue_trend_available,
            "profitability_margin_available": self.profitability_margin_available,
            "eps_net_income_available": self.eps_net_income_available,
            "fcf_available": self.fcf_available,
            "share_count_dilution_available": self.share_count_dilution_available,
            "trend_source": self.trend_source,
            "observation_count": self.observation_count,
            "completeness_band": self.completeness_band,
            "freshness_status": self.freshness_status,
            "usability_label": self.usability_label,
            "missing_reason": self.missing_reason,
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
) -> CanonicalEquityDatasetRow:
    """Build a canonical equity dataset row for one ticker.

    Args:
        ticker: normalized uppercase ticker symbol.
        asset_type: one of equity|etf|crypto|unknown.
        lanes: {lane_name: LaneCoverage} from Stage 5J coverage for this ticker.
        sec_obs_count: COUNT of research_artifact_facts for the SEC company facts
            artifact_id (already fetched by Stage 9B supplemental queries).
        cat_count: COUNT of research_artifact_facts for the SEC catalyst artifact_id.

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

    # Operating trends — derived from SEC company facts metadata.
    operating_trends = _build_operating_trends(
        sec_cov=sec_cov,
        sec_obs_count=sec_obs_count,
        sec_is_usable=sec_is_usable,
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
    unavailable_trends = OperatingTrendSection(
        revenue_trend_available=False,
        profitability_margin_available=False,
        eps_net_income_available=False,
        fcf_available=False,
        share_count_dilution_available=False,
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
) -> OperatingTrendSection:
    """Derive operating trend availability signals from SEC company facts metadata.

    Uses completeness_band + observation_count as proxies for section availability.
    Does NOT expose actual metric values or raw XBRL keys.
    """
    if not sec_is_usable or sec_cov is None:
        reason = _derive_not_safe_reason(sec_cov, (sec_cov.usability_label if sec_cov else None) or "")
        return OperatingTrendSection(
            revenue_trend_available=False,
            profitability_margin_available=False,
            eps_net_income_available=False,
            fcf_available=False,
            share_count_dilution_available=False,
            trend_source="unavailable",
            observation_count=sec_obs_count,
            completeness_band=sec_cov.completeness_band if sec_cov else None,
            freshness_status=sec_cov.freshness_status if sec_cov else None,
            usability_label=sec_cov.usability_label if sec_cov else None,
            missing_reason=reason or "SEC company facts artifact is not usable.",
        )

    completeness = (sec_cov.completeness_band or "").upper()
    freshness = (sec_cov.freshness_status or "").upper()
    obs = sec_obs_count or 0

    is_fresh = freshness in {s.upper() for s in _FRESH_LABELS}
    has_observations = obs >= _MIN_OBSERVATIONS_FOR_AVAILABILITY

    # Derive section-level availability:
    # PARTIAL or COMPLETE completeness + fresh + min observations → available
    # THIN or stale → limited or unavailable
    sufficient_for_partial = (
        completeness in {_COMPLETENESS_PARTIAL, _COMPLETENESS_COMPLETE}
        and is_fresh
        and has_observations
    )
    sufficient_for_complete = (
        completeness == _COMPLETENESS_COMPLETE
        and is_fresh
        and obs >= _MIN_OBSERVATIONS_FOR_AVAILABILITY * 3
    )

    return OperatingTrendSection(
        revenue_trend_available=sufficient_for_partial,
        profitability_margin_available=sufficient_for_partial,
        eps_net_income_available=sufficient_for_partial,
        fcf_available=sufficient_for_complete,
        share_count_dilution_available=sufficient_for_partial,
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
