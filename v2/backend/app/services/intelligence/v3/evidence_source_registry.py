"""Phase 10 — Evidence Source Registry v1 / Multi-Lane Governance v1.

Internal governance contract defining approved evidence sources and evidence
lanes for future Intel v3 consumption.

Purpose:
    Provides a single authoritative record of every governed evidence source,
    its lane, trust tier, allowed uses, governance constraints, and failure
    behavior so that future phases can consume sources with explicit, auditable
    approval rather than implicit drift.

Governance invariants (non-negotiable):
    - finance-agent/research-artifact outputs are research artifacts only and
      may NEVER directly own final Buy/Hold/Trim/Sell actions or Deploy sizing.
    - Open-web/news/event-risk sources require corroboration_required=True
      before they can ever be decision_input_eligible.
    - explanation_only=True sources must never be silently promoted to
      decision_input_eligible=True.
    - LLM_GENERATED trust-tier sources are never decision_input_eligible.
    - Missing/stale/weak/conflicting evidence suppresses or degrades lanes;
      it must never fabricate confidence.
    - ETF/fund evidence must not reuse company SEC metric logic.
    - Portfolio exposure may influence future decisions only through
      deterministic rules, not LLM authority.
    - Deploy and Watchtower are future consumers only; they are not
      implemented in Phase 10.

Pure data types only. No IO, no LLM, no DB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, List, Optional

EVIDENCE_SOURCE_REGISTRY_CONTRACT_VERSION = "phase10_v1"

# ── Evidence lanes ────────────────────────────────────────────────────────────


class EvidenceLane(str, Enum):
    """The eleven governed evidence lanes for Intel v3."""
    SEC_COMPANY_FUNDAMENTALS = "SEC_COMPANY_FUNDAMENTALS"
    VALUATION_CONTEXT = "VALUATION_CONTEXT"
    MARKET_BEHAVIOR_VOLATILITY = "MARKET_BEHAVIOR_VOLATILITY"
    ANALYST_EXPECTATIONS_REVISIONS = "ANALYST_EXPECTATIONS_REVISIONS"
    EARNINGS_TRANSCRIPTS_GUIDANCE = "EARNINGS_TRANSCRIPTS_GUIDANCE"
    NEWS_EVENT_RISK = "NEWS_EVENT_RISK"
    SECTOR_MACRO_CONTEXT = "SECTOR_MACRO_CONTEXT"
    ETF_FUND_EXPOSURE = "ETF_FUND_EXPOSURE"
    PORTFOLIO_EXPOSURE = "PORTFOLIO_EXPOSURE"
    USER_THESIS_MEMORY = "USER_THESIS_MEMORY"
    RESEARCH_ARTIFACT = "RESEARCH_ARTIFACT"


ALL_EVIDENCE_LANES: FrozenSet[EvidenceLane] = frozenset(EvidenceLane)

# ── Source and trust types ────────────────────────────────────────────────────


class SourceType(str, Enum):
    """Primary classification of an evidence source by origin."""
    SEC_FILING = "SEC_FILING"
    MARKET_DATA = "MARKET_DATA"
    ANALYST_CONSENSUS = "ANALYST_CONSENSUS"
    EARNINGS_TRANSCRIPT = "EARNINGS_TRANSCRIPT"
    NEWS_FEED = "NEWS_FEED"
    MACRO_INDICATOR = "MACRO_INDICATOR"
    ETF_HOLDINGS = "ETF_HOLDINGS"
    PORTFOLIO_DB = "PORTFOLIO_DB"
    USER_INPUT = "USER_INPUT"
    RESEARCH_ARTIFACT = "RESEARCH_ARTIFACT"


class TrustTier(str, Enum):
    """Trust tier determines the governance constraints on a source."""
    PRIMARY_HARD_DATA = "PRIMARY_HARD_DATA"      # structured, auditable, source-linked
    SECONDARY_COMPUTED = "SECONDARY_COMPUTED"    # derived/computed from PRIMARY_HARD_DATA
    CONTEXTUAL = "CONTEXTUAL"                    # explanatory context, not primary evidence
    OPEN_WEB = "OPEN_WEB"                        # unstructured web; requires corroboration
    LLM_GENERATED = "LLM_GENERATED"             # LLM/agent output; always non-authoritative


class LifecycleStatus(str, Enum):
    """Current lifecycle status of the source definition."""
    ACTIVE = "ACTIVE"        # in use by at least one phase
    PLANNED = "PLANNED"      # defined but not yet consumed by any decision path
    DEPRECATED = "DEPRECATED"
    BLOCKED = "BLOCKED"      # governance-blocked; must not be consumed until unblocked


class FailureBehavior(str, Enum):
    """Required behavior when this source is missing, stale, weak, or unavailable."""
    SUPPRESS_AXIS = "SUPPRESS_AXIS"          # suppress the relevant decision axis
    DEGRADE_CONFIDENCE = "DEGRADE_CONFIDENCE"  # lower conviction but allow decision
    BLOCK_DECISION = "BLOCK_DECISION"        # block the decision entirely
    IGNORE = "IGNORE"                        # source is non-critical; omit silently


# ── Governance definition ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceSourceDefinition:
    """Governance contract for a single evidence source.

    Fields are intentionally frozen so registry entries cannot be mutated
    at runtime.
    """
    # Identity
    source_id: str
    lane: EvidenceLane
    display_name: str
    description: str

    # Classification
    source_type: SourceType
    trust_tier: TrustTier

    # Freshness (None = not applicable / not time-bounded)
    freshness_sla_hours: Optional[int]

    # Governance gates (all three must be evaluated together)
    decision_input_eligible: bool   # may ever be a DecisionInputV3 signal
    explanation_only: bool          # context/rationale only; never decision signal
    corroboration_required: bool    # another governed source must corroborate first
    numeric_authority: bool         # may supply authoritative numeric values

    # Audit requirements
    audit_url_required: bool        # every observation must carry a source URL

    # Adapter / provider identity
    provider_adapter: str           # module or provider name handling this source

    # Lifecycle
    lifecycle_status: LifecycleStatus
    failure_behavior: FailureBehavior

    # Constraints and free-text notes
    notes: str = ""


# ── Registry entries ──────────────────────────────────────────────────────────

_REGISTRY_ENTRIES: List[EvidenceSourceDefinition] = [

    # ── Lane 1: SEC_COMPANY_FUNDAMENTALS ──────────────────────────────────────
    EvidenceSourceDefinition(
        source_id="sec_companyfacts_v1",
        lane=EvidenceLane.SEC_COMPANY_FUNDAMENTALS,
        display_name="SEC EDGAR CompanyFacts",
        description=(
            "Structured XBRL metric observations from SEC EDGAR CompanyFacts API. "
            "Covers revenue, earnings, cash, liabilities, equity, capex, and related "
            "buckets for company tickers. Source-linked with CIK and filing accession. "
            "Phase 8A–9 cover shadow-only readiness; decision consumption requires "
            "explicit Phase 10+ governance approval."
        ),
        source_type=SourceType.SEC_FILING,
        trust_tier=TrustTier.PRIMARY_HARD_DATA,
        freshness_sla_hours=24 * 90,  # SEC filings update quarterly (~90 days)
        decision_input_eligible=True,
        explanation_only=False,
        corroboration_required=False,
        numeric_authority=True,
        audit_url_required=True,
        provider_adapter="research_workers.sec_companyfacts_parser",
        lifecycle_status=LifecycleStatus.ACTIVE,
        failure_behavior=FailureBehavior.SUPPRESS_AXIS,
        notes=(
            "Currently shadow/readiness-only (Phases 8A–9). Decision consumption requires "
            "explicit per-phase governance approval. ETF/fund/crypto tickers are "
            "SKIPPED_NON_COMPANY and must not be processed by this source. "
            "BLSH/KLAR/TSM remain BLOCKED until manual review authorizes expansion."
        ),
    ),

    # ── Lane 2: VALUATION_CONTEXT ─────────────────────────────────────────────
    EvidenceSourceDefinition(
        source_id="valuation_ratio_computed_v1",
        lane=EvidenceLane.VALUATION_CONTEXT,
        display_name="Valuation Ratio (Computed)",
        description=(
            "Price-based valuation ratios (P/E, P/B, EV/EBITDA) computed from "
            "market price data and SEC fundamental metrics. Secondary derived "
            "signal; inherits freshness SLA from both source lanes."
        ),
        source_type=SourceType.MARKET_DATA,
        trust_tier=TrustTier.SECONDARY_COMPUTED,
        freshness_sla_hours=24,
        decision_input_eligible=True,  # future — planned for PriceBand axis
        explanation_only=False,
        corroboration_required=False,
        numeric_authority=True,
        audit_url_required=False,  # computed internally from audited inputs
        provider_adapter="v3.decision_contracts (future PriceBand computation)",
        lifecycle_status=LifecycleStatus.PLANNED,
        failure_behavior=FailureBehavior.SUPPRESS_AXIS,
        notes=(
            "Requires both market price (MARKET_BEHAVIOR_VOLATILITY lane) and "
            "SEC fundamentals (SEC_COMPANY_FUNDAMENTALS lane) to be ACTIVE before "
            "this source can be consumed. Not applicable to ETF/crypto tickers."
        ),
    ),

    # ── Lane 3: MARKET_BEHAVIOR_VOLATILITY ───────────────────────────────────
    EvidenceSourceDefinition(
        source_id="price_history_v1",
        lane=EvidenceLane.MARKET_BEHAVIOR_VOLATILITY,
        display_name="Market Price & Volatility History",
        description=(
            "Historical price, volume, and volatility data from a market data "
            "provider (e.g., Polygon, Finnhub). Used for trend, momentum, and "
            "volatility signals feeding evidence quality and risk axes."
        ),
        source_type=SourceType.MARKET_DATA,
        trust_tier=TrustTier.PRIMARY_HARD_DATA,
        freshness_sla_hours=24,
        decision_input_eligible=True,  # future — planned for AxisBand + RiskBand
        explanation_only=False,
        corroboration_required=False,
        numeric_authority=True,
        audit_url_required=False,
        provider_adapter="services.intelligence.market_snapshot (future)",
        lifecycle_status=LifecycleStatus.PLANNED,
        failure_behavior=FailureBehavior.SUPPRESS_AXIS,
        notes="Applies to all asset types including ETF and crypto.",
    ),

    # ── Lane 4: ANALYST_EXPECTATIONS_REVISIONS ───────────────────────────────
    EvidenceSourceDefinition(
        source_id="analyst_consensus_v1",
        lane=EvidenceLane.ANALYST_EXPECTATIONS_REVISIONS,
        display_name="Analyst Consensus Ratings & Revisions",
        description=(
            "Analyst consensus ratings, price targets, and estimate revisions "
            "from data providers. Partially consumed today via "
            "existing_signal_adapter for raw_analyst_action on the v3 decision path."
        ),
        source_type=SourceType.ANALYST_CONSENSUS,
        trust_tier=TrustTier.SECONDARY_COMPUTED,
        freshness_sla_hours=24 * 7,  # weekly freshness acceptable
        decision_input_eligible=True,  # already partially feeding DecisionInputV3
        explanation_only=False,
        corroboration_required=False,
        numeric_authority=False,  # consensus opinion, not primary numeric authority
        audit_url_required=False,
        provider_adapter="research_workers.earnings_reviewer / existing_signal_adapter",
        lifecycle_status=LifecycleStatus.ACTIVE,
        failure_behavior=FailureBehavior.DEGRADE_CONFIDENCE,
        notes=(
            "Already partially in use. Analyst consensus degrades conviction when "
            "absent but does not block decisions outright. Does not carry numeric "
            "authority — use SEC fundamentals lane for authoritative numbers."
        ),
    ),

    # ── Lane 5: EARNINGS_TRANSCRIPTS_GUIDANCE ────────────────────────────────
    EvidenceSourceDefinition(
        source_id="sec_earnings_transcript_v1",
        lane=EvidenceLane.EARNINGS_TRANSCRIPTS_GUIDANCE,
        display_name="Earnings Transcripts & Guidance (SEC EDGAR)",
        description=(
            "Earnings call transcripts and forward guidance text extracted from "
            "SEC EDGAR 8-K and 10-Q filings. Provides qualitative context only; "
            "not suitable as a primary numeric decision signal."
        ),
        source_type=SourceType.EARNINGS_TRANSCRIPT,
        trust_tier=TrustTier.CONTEXTUAL,
        freshness_sla_hours=24 * 90,
        decision_input_eligible=False,
        explanation_only=True,
        corroboration_required=True,  # requires hard-data corroboration
        numeric_authority=False,
        audit_url_required=True,
        provider_adapter="research_workers.earnings_reviewer (future transcript path)",
        lifecycle_status=LifecycleStatus.PLANNED,
        failure_behavior=FailureBehavior.IGNORE,
        notes=(
            "Explanation/rationale context only. Must never be promoted to "
            "decision_input_eligible without explicit governance review. "
            "Must always cite SEC filing accession as audit URL."
        ),
    ),

    # ── Lane 6: NEWS_EVENT_RISK ───────────────────────────────────────────────
    EvidenceSourceDefinition(
        source_id="news_feed_v1",
        lane=EvidenceLane.NEWS_EVENT_RISK,
        display_name="News Feed & Event Risk",
        description=(
            "Open-web news articles and event-risk signals (regulatory actions, "
            "earnings surprises, macro shocks) from news providers. "
            "Requires corroboration by a governed hard-data source before any "
            "decision-input eligibility can be considered."
        ),
        source_type=SourceType.NEWS_FEED,
        trust_tier=TrustTier.OPEN_WEB,
        freshness_sla_hours=1,
        decision_input_eligible=False,  # requires corroboration first
        explanation_only=True,
        corroboration_required=True,
        numeric_authority=False,
        audit_url_required=True,  # must cite original article URL
        provider_adapter="(no active provider — planned)",
        lifecycle_status=LifecycleStatus.PLANNED,
        failure_behavior=FailureBehavior.IGNORE,
        notes=(
            "Open-web sources are inherently low-trust. corroboration_required=True "
            "is a hard governance constraint: this source may NEVER become "
            "decision_input_eligible=True unless a separate governed source "
            "corroborates the signal and an explicit phase governance approval is "
            "recorded. Missing news signals are ignored, not fabricated."
        ),
    ),

    # ── Lane 7: SECTOR_MACRO_CONTEXT ─────────────────────────────────────────
    EvidenceSourceDefinition(
        source_id="macro_indicator_v1",
        lane=EvidenceLane.SECTOR_MACRO_CONTEXT,
        display_name="Sector & Macro Indicators",
        description=(
            "Sector rotation signals, macro indicators (interest rates, CPI, "
            "unemployment), and relative sector performance context. "
            "Provides background context for decision rationale but is not "
            "a primary signal source for Buy/Hold/Trim/Sell."
        ),
        source_type=SourceType.MACRO_INDICATOR,
        trust_tier=TrustTier.CONTEXTUAL,
        freshness_sla_hours=24,
        decision_input_eligible=False,
        explanation_only=True,
        corroboration_required=False,
        numeric_authority=False,
        audit_url_required=False,
        provider_adapter="(no active provider — planned)",
        lifecycle_status=LifecycleStatus.PLANNED,
        failure_behavior=FailureBehavior.IGNORE,
        notes="Context and rationale enrichment only. No decision authority.",
    ),

    # ── Lane 8: ETF_FUND_EXPOSURE ────────────────────────────────────────────
    EvidenceSourceDefinition(
        source_id="etf_holdings_v1",
        lane=EvidenceLane.ETF_FUND_EXPOSURE,
        display_name="ETF/Fund Holdings & Exposure Overlap",
        description=(
            "ETF and fund composition data showing holdings, overlap with "
            "portfolio positions, and concentration exposure. "
            "ETF/fund governance must not reuse SEC company metric logic — "
            "these are structurally distinct asset types."
        ),
        source_type=SourceType.ETF_HOLDINGS,
        trust_tier=TrustTier.SECONDARY_COMPUTED,
        freshness_sla_hours=24 * 7,
        decision_input_eligible=False,
        explanation_only=True,
        corroboration_required=False,
        numeric_authority=False,
        audit_url_required=False,
        provider_adapter="(no active provider — planned)",
        lifecycle_status=LifecycleStatus.PLANNED,
        failure_behavior=FailureBehavior.IGNORE,
        notes=(
            "ETF/fund holdings must not be processed by SEC company fundamentals "
            "logic (sec_companyfacts_v1). These are separate lanes with separate "
            "governance. Explanation context only for now."
        ),
    ),

    # ── Lane 9: PORTFOLIO_EXPOSURE ───────────────────────────────────────────
    EvidenceSourceDefinition(
        source_id="portfolio_positions_v1",
        lane=EvidenceLane.PORTFOLIO_EXPOSURE,
        display_name="Portfolio Positions & Concentration",
        description=(
            "Internal portfolio positions, weights, and concentration data "
            "from the application database. Provides FitBand signals for "
            "the v3 decision kernel via deterministic concentration rules."
        ),
        source_type=SourceType.PORTFOLIO_DB,
        trust_tier=TrustTier.PRIMARY_HARD_DATA,
        freshness_sla_hours=1,
        decision_input_eligible=True,  # already active via portfolio_governor_lite
        explanation_only=False,
        corroboration_required=False,
        numeric_authority=True,
        audit_url_required=False,
        provider_adapter="v3.portfolio_governor_lite",
        lifecycle_status=LifecycleStatus.ACTIVE,
        failure_behavior=FailureBehavior.BLOCK_DECISION,
        notes=(
            "Portfolio exposure may influence decisions ONLY through deterministic "
            "rules (e.g., weight breach thresholds). LLM or agent outputs must "
            "NEVER mediate portfolio-exposure signals into final decisions."
        ),
    ),

    # ── Lane 10: USER_THESIS_MEMORY ──────────────────────────────────────────
    EvidenceSourceDefinition(
        source_id="user_thesis_v1",
        lane=EvidenceLane.USER_THESIS_MEMORY,
        display_name="User Thesis Memory",
        description=(
            "User-provided investment thesis notes, conviction labels, and "
            "time-to-payoff estimates per holding, stored in the application "
            "database. Used for rationale enrichment and thesis context only."
        ),
        source_type=SourceType.USER_INPUT,
        trust_tier=TrustTier.CONTEXTUAL,
        freshness_sla_hours=None,  # user-defined; no SLA
        decision_input_eligible=False,
        explanation_only=True,
        corroboration_required=False,
        numeric_authority=False,
        audit_url_required=False,
        provider_adapter="services.intelligence.thesis_engine",
        lifecycle_status=LifecycleStatus.ACTIVE,
        failure_behavior=FailureBehavior.IGNORE,
        notes=(
            "User thesis is explanatory context only. It must never be promoted "
            "to a primary decision signal. Missing thesis is silently ignored."
        ),
    ),

    # ── Lane 11: RESEARCH_ARTIFACT ───────────────────────────────────────────
    EvidenceSourceDefinition(
        source_id="research_artifact_llm_v1",
        lane=EvidenceLane.RESEARCH_ARTIFACT,
        display_name="Research Artifact (LLM / Finance Agent)",
        description=(
            "Sourced research artifacts produced by LLM-backed research workers "
            "and finance agents (e.g., earnings reviewer, per-ticker analyst). "
            "These are asynchronous, non-authoritative research outputs. "
            "They may provide sourced explanations but must never directly own "
            "final Buy/Hold/Trim/Sell actions or Deploy sizing."
        ),
        source_type=SourceType.RESEARCH_ARTIFACT,
        trust_tier=TrustTier.LLM_GENERATED,
        freshness_sla_hours=24 * 7,
        decision_input_eligible=False,  # NEVER directly owns final visible actions
        explanation_only=True,
        corroboration_required=True,   # always requires hard-data corroboration
        numeric_authority=False,
        audit_url_required=True,       # artifacts must cite source URLs
        provider_adapter="research_workers.runner / per_ticker_analyst",
        lifecycle_status=LifecycleStatus.ACTIVE,
        failure_behavior=FailureBehavior.IGNORE,
        notes=(
            "LLM_GENERATED sources are permanently non-authoritative for "
            "Buy/Hold/Trim/Sell actions and Deploy sizing. Research is asynchronous; "
            "decisions are deterministic. This source may provide sourced rationale "
            "text that informs the human reader, but the final action authority "
            "remains with the deterministic decision policy (decision_policy_v1). "
            "corroboration_required=True is a hard constraint."
        ),
    ),
]

# ── Registry index ────────────────────────────────────────────────────────────

# Immutable registry indexed by source_id.
EVIDENCE_SOURCE_REGISTRY: Dict[str, EvidenceSourceDefinition] = {
    entry.source_id: entry for entry in _REGISTRY_ENTRIES
}


# ── Governance query helpers ──────────────────────────────────────────────────

def get_all_sources() -> List[EvidenceSourceDefinition]:
    """Return all governed source definitions."""
    return list(_REGISTRY_ENTRIES)


def get_sources_by_lane(lane: EvidenceLane) -> List[EvidenceSourceDefinition]:
    """Return all governed sources for a given evidence lane."""
    return [s for s in _REGISTRY_ENTRIES if s.lane == lane]


def get_decision_eligible_sources() -> List[EvidenceSourceDefinition]:
    """Return sources that have been explicitly marked decision_input_eligible.

    Note: decision_input_eligible=True only means governance allows it in
    principle. The source's lifecycle_status must also be ACTIVE before it
    can be consumed by a decision path.
    """
    return [s for s in _REGISTRY_ENTRIES if s.decision_input_eligible]


def get_active_decision_eligible_sources() -> List[EvidenceSourceDefinition]:
    """Return sources that are both decision_input_eligible and ACTIVE."""
    return [
        s for s in _REGISTRY_ENTRIES
        if s.decision_input_eligible and s.lifecycle_status == LifecycleStatus.ACTIVE
    ]


def get_explanation_only_sources() -> List[EvidenceSourceDefinition]:
    """Return sources that are explanation/context only."""
    return [s for s in _REGISTRY_ENTRIES if s.explanation_only]


def get_sources_requiring_corroboration() -> List[EvidenceSourceDefinition]:
    """Return sources that require corroboration by another governed source."""
    return [s for s in _REGISTRY_ENTRIES if s.corroboration_required]


def get_lanes_represented() -> FrozenSet[EvidenceLane]:
    """Return the set of evidence lanes that have at least one source defined."""
    return frozenset(s.lane for s in _REGISTRY_ENTRIES)


def build_registry_summary() -> dict:
    """Return a governance summary suitable for diagnostics output.

    Never includes raw metric values, structured payloads, or source URLs.
    Always safe_for_decision=False, visible_snapshot_unchanged=True.
    """
    lane_counts: Dict[str, int] = {}
    for lane in EvidenceLane:
        lane_counts[lane.value] = len(get_sources_by_lane(lane))

    decision_eligible = get_decision_eligible_sources()
    active_decision_eligible = get_active_decision_eligible_sources()
    explanation_only = get_explanation_only_sources()
    corroboration_required = get_sources_requiring_corroboration()
    lanes_represented = get_lanes_represented()

    return {
        "contract_version": EVIDENCE_SOURCE_REGISTRY_CONTRACT_VERSION,
        "safe_for_decision": False,
        "visible_snapshot_unchanged": True,
        "total_sources": len(_REGISTRY_ENTRIES),
        "total_lanes": len(EvidenceLane),
        "lanes_represented_count": len(lanes_represented),
        "all_lanes_represented": lanes_represented == ALL_EVIDENCE_LANES,
        "decision_eligible_source_count": len(decision_eligible),
        "active_decision_eligible_source_count": len(active_decision_eligible),
        "explanation_only_source_count": len(explanation_only),
        "corroboration_required_source_count": len(corroboration_required),
        "sources_by_lane": lane_counts,
        "source_ids": sorted(EVIDENCE_SOURCE_REGISTRY.keys()),
        "lifecycle_status_counts": {
            status.value: sum(
                1 for s in _REGISTRY_ENTRIES if s.lifecycle_status == status
            )
            for status in LifecycleStatus
        },
        "trust_tier_counts": {
            tier.value: sum(
                1 for s in _REGISTRY_ENTRIES if s.trust_tier == tier
            )
            for tier in TrustTier
        },
    }
