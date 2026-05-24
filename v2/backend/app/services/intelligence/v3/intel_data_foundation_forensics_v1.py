"""Stage 9B/9E — Intel Data Foundation Forensics v1.

Backend-only, read-only diagnostic. Inspects actual persisted research artifacts,
portfolio positions, and evidence lane outputs to explain, per holding, where the
data foundation is missing and why — before any synthesis is attempted.

Goal:
    Prove, per current holding, which root causes explain the missing data:
    - provider/source limitation (ETF fund data, crypto market data)
    - missing CIK/ticker mapping (SEC EDGAR lookup failure)
    - worker/fanout/backfill gap (evidence lane never ran for this ticker)
    - artifact write gap (runner ran but no artifact was written)
    - artifact exists but readiness/scoring marks it weak
    - artifact exists and is usable but canonical dataset normalization is missing
    - asset type requires a different provider/model
    - no lane exists yet (valuation, ETF composition, thesis history)

Architecture contracts (non-negotiable):
  - Read-only. No writes to any table. No evidence runs triggered.
  - NEVER invokes the policy-decide function or imports the policy module.
  - NEVER invokes LLM calls, provider calls, or evidence workers.
  - safe_for_decision is permanently False.
  - synthesis_ready is permanently False.
  - NEVER returns raw artifact payloads, source URLs, fact contents, API keys,
    secrets, or user PII.
  - Only safe counts, booleans, enums, and plain-English strings are exposed.
  - Fail-soft: DB query errors are captured in errors[], not raised.
  - Feature-flagged off by default (intel_v3_data_foundation_forensics_enabled).
"""
from __future__ import annotations

import logging
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
    ResearchEvidenceCoverageSummary,
    compute_research_evidence_coverage,
)
from .research_evidence_decision_input_adapter_v1 import (
    INSTRUMENT_CATEGORY_CRYPTO,
    INSTRUMENT_CATEGORY_EQUITY,
    INSTRUMENT_CATEGORY_ETF,
    INSTRUMENT_CATEGORY_UNKNOWN,
    _classify_instrument_category,
)
from .canonical_equity_dataset_v1 import (
    CanonicalEquityDatasetRow,
    build_canonical_equity_dataset_row,
    build_asset_parity_roadmap,
)
from .equity_valuation_evidence_v1 import (
    EquityValuationEvidenceRow,
    build_equity_valuation_evidence_row,
)
from .sec_companyfacts_readiness_diagnostic_v1 import (
    diagnose_sec_companyfacts_readiness,
)

logger = logging.getLogger(__name__)

FORENSICS_VERSION = "intel_data_foundation_forensics.v1"

# ── Root cause buckets (deterministic enum strings) ────────────────────────────

BUCKET_SEC_EXISTS_WEAK = "SEC_ARTIFACT_EXISTS_BUT_READINESS_WEAK"
BUCKET_SEC_MISSING_CIK = "SEC_ARTIFACT_MISSING_CIK_OR_MAPPING_UNKNOWN"
BUCKET_SEC_MISSING_WORKER = "SEC_ARTIFACT_MISSING_WORKER_OR_BACKFILL_GAP"
BUCKET_VALUATION_NOT_BUILT = "VALUATION_LANE_NOT_BUILT"
BUCKET_ETF_NOT_BUILT = "ETF_PROVIDER_NOT_BUILT"
BUCKET_CRYPTO_NOT_BUILT = "CRYPTO_PROVIDER_NOT_BUILT"
BUCKET_TARGET_WEIGHT_NOT_BUILT = "TARGET_WEIGHT_MODEL_NOT_BUILT"
BUCKET_THESIS_NOT_BUILT = "THESIS_HISTORY_NOT_BUILT"
BUCKET_NEWS_SUPPRESSED = "NEWS_SENTIMENT_SUPPRESSED_THIN"
BUCKET_DATA_NEEDS_NORMALIZATION = "DATA_PRESENT_NEEDS_CANONICAL_NORMALIZATION"
BUCKET_NOT_APPLICABLE = "ASSET_TYPE_NOT_APPLICABLE"

ALL_BUCKETS: frozenset[str] = frozenset({
    BUCKET_SEC_EXISTS_WEAK,
    BUCKET_SEC_MISSING_CIK,
    BUCKET_SEC_MISSING_WORKER,
    BUCKET_VALUATION_NOT_BUILT,
    BUCKET_ETF_NOT_BUILT,
    BUCKET_CRYPTO_NOT_BUILT,
    BUCKET_TARGET_WEIGHT_NOT_BUILT,
    BUCKET_THESIS_NOT_BUILT,
    BUCKET_NEWS_SUPPRESSED,
    BUCKET_DATA_NEEDS_NORMALIZATION,
    BUCKET_NOT_APPLICABLE,
})

_PROVIDER_LIMITED_BUCKETS = frozenset({BUCKET_ETF_NOT_BUILT, BUCKET_CRYPTO_NOT_BUILT})
_IMPLEMENTATION_LIMITED_BUCKETS = frozenset({
    BUCKET_SEC_EXISTS_WEAK,
    BUCKET_SEC_MISSING_CIK,
    BUCKET_SEC_MISSING_WORKER,
    BUCKET_VALUATION_NOT_BUILT,
    BUCKET_TARGET_WEIGHT_NOT_BUILT,
    BUCKET_THESIS_NOT_BUILT,
    BUCKET_NEWS_SUPPRESSED,
})
_NORMALIZATION_LIMITED_BUCKETS = frozenset({BUCKET_DATA_NEEDS_NORMALIZATION})

_SUPPRESSED_PREFIX = "SUPPRESSED_"
_USABLE_LABELS = frozenset({"USABLE", "USABLE_WITH_LIMITATIONS"})

# Lane names tracked in the per-holding forensics row.
_TRACKED_LANES = (
    LANE_FUNDAMENTALS,
    LANE_TECHNICALS,
    LANE_NEWS_SENTIMENT,
    LANE_SEC_COMPANY_FACTS,
    LANE_SEC_CATALYST_SENTIMENT,
)

# Source authorities that qualify a READY artifact for STRONG (vs PARTIAL).
_PRIMARY_AUTHORITY_LEVELS = frozenset({"PRIMARY_AUTHORITY", "OFFICIAL_FREE_SOURCE"})


# ── Typed output ───────────────────────────────────────────────────────────────


@dataclass
class HoldingForensicsRow:
    """Per-holding forensics row.

    All fields are safe for diagnostics output: no raw payloads, no source URLs,
    no fact contents, no API keys.
    """

    ticker: str
    asset_type: str  # equity | etf | crypto | unknown

    # Portfolio context availability
    current_position_available: bool
    current_weight_available: bool
    target_weight_available: bool

    # yfinance fundamentals lane (fundamental_quality / fundamentals_evidence_v1)
    yfinance_fundamentals_artifact_exists: bool
    yfinance_fundamentals_status: Optional[str]  # usability label, or None

    # Technicals lane (technical_signal / technicals_evidence_v1)
    technical_artifact_exists: bool
    technical_status: Optional[str]

    # News sentiment lane — yfinance editorial; almost always suppressed by design
    news_sentiment_artifact_exists: bool
    news_sentiment_status: Optional[str]

    # SEC company facts lane (fundamental_quality / sec_companyfacts_evidence_v1)
    sec_companyfacts_artifact_exists: bool
    sec_companyfacts_status: Optional[str]           # usability label
    sec_companyfacts_observation_count: Optional[int]  # safe count from facts table
    sec_companyfacts_reason_not_strong: Optional[str]  # plain-English reason when not USABLE

    # SEC catalyst sentiment lane (sentiment_event / sec_catalyst_sentiment_evidence_v1)
    sec_catalyst_artifact_exists: bool
    sec_catalyst_status: Optional[str]
    sec_catalyst_count: Optional[int]                # safe count from facts table

    # Valuation: split into scaffold presence (Stage 9E ran) and numeric readiness.
    valuation_evidence_model_present: bool   # True when Stage 9E scaffold ran (all equity); False for ETF/crypto
    valuation_numeric_ready: bool            # True only when numeric EPS/price are in scope; always False at Stage 9E
    valuation_inputs_available_summary: str

    # ETF fund composition (no provider built; always False at Stage 9B)
    etf_fund_composition_artifact_exists: bool

    # Crypto market context proxy (technical artifact for crypto tickers)
    crypto_market_context_artifact_exists: bool

    # Thesis / decision history
    thesis_history_exists: bool

    # Primary root cause (highest-priority gap; first element of blocking_gap_buckets)
    root_cause_bucket: str
    next_required_fix: str

    # All material blocking gaps (deterministic priority order)
    blocking_gap_buckets: list   # list[str] — all applicable gap bucket names
    blocking_gap_count: int      # len(blocking_gap_buckets)
    next_required_fixes: list    # list[str] — fix message per gap

    # Stage 9C: safe SEC CompanyFacts readiness diagnostic for weak artifacts.
    # Populated when sec_companyfacts_artifact_exists=True and artifact is not
    # USABLE/USABLE_WITH_LIMITATIONS. None otherwise (artifact absent or usable).
    # Contains only safe metadata: no raw payloads, no source URLs, no fact values.
    sec_companyfacts_diagnostic: Optional[dict] = None

    # Stage 9D: canonical equity dataset row for this holding.
    # Populated for equity holdings only. ETF/crypto → None (their own provider
    # lanes are required, not the equity dataset).
    # safe_for_equity_dataset=True only when sec_company_facts is usable.
    canonical_equity_dataset: Optional[dict] = None

    # Stage 9E: equity valuation evidence row for this holding.
    # Populated for equity holdings only. ETF/crypto → None (valuation not
    # applicable; dedicated provider lanes required for those asset classes).
    # valuation_ready=True only when canonical_equity_dataset_safe + price + EPS available.
    valuation_evidence: Optional[dict] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "asset_type": self.asset_type,
            "current_position_available": self.current_position_available,
            "current_weight_available": self.current_weight_available,
            "target_weight_available": self.target_weight_available,
            "yfinance_fundamentals_artifact_exists": self.yfinance_fundamentals_artifact_exists,
            "yfinance_fundamentals_status": self.yfinance_fundamentals_status,
            "technical_artifact_exists": self.technical_artifact_exists,
            "technical_status": self.technical_status,
            "news_sentiment_artifact_exists": self.news_sentiment_artifact_exists,
            "news_sentiment_status": self.news_sentiment_status,
            "sec_companyfacts_artifact_exists": self.sec_companyfacts_artifact_exists,
            "sec_companyfacts_status": self.sec_companyfacts_status,
            "sec_companyfacts_observation_count": self.sec_companyfacts_observation_count,
            "sec_companyfacts_reason_not_strong": self.sec_companyfacts_reason_not_strong,
            "sec_catalyst_artifact_exists": self.sec_catalyst_artifact_exists,
            "sec_catalyst_status": self.sec_catalyst_status,
            "sec_catalyst_count": self.sec_catalyst_count,
            "valuation_evidence_model_present": self.valuation_evidence_model_present,
            "valuation_numeric_ready": self.valuation_numeric_ready,
            "valuation_inputs_available_summary": self.valuation_inputs_available_summary,
            "etf_fund_composition_artifact_exists": self.etf_fund_composition_artifact_exists,
            "crypto_market_context_artifact_exists": self.crypto_market_context_artifact_exists,
            "thesis_history_exists": self.thesis_history_exists,
            "root_cause_bucket": self.root_cause_bucket,
            "next_required_fix": self.next_required_fix,
            "blocking_gap_buckets": list(self.blocking_gap_buckets),
            "blocking_gap_count": self.blocking_gap_count,
            "next_required_fixes": list(self.next_required_fixes),
            "sec_companyfacts_diagnostic": self.sec_companyfacts_diagnostic,
            "canonical_equity_dataset": self.canonical_equity_dataset,
            "valuation_evidence": self.valuation_evidence,
        }


@dataclass
class DataFoundationForensicsResult:
    """Portfolio-wide data foundation forensics result.

    Diagnostic only. safe_for_decision and synthesis_ready are always False.
    """

    schema_version: str
    user_id: str
    generated_at: str
    safe_for_decision: bool = False   # immutable
    synthesis_ready: bool = False     # immutable

    holdings: list[HoldingForensicsRow] = field(default_factory=list)

    # Portfolio-level aggregates
    holdings_by_asset_type: dict[str, int] = field(default_factory=dict)
    # Per lane: count of holdings where artifact exists (artifact_id not None)
    artifacts_existing_by_lane: dict[str, int] = field(default_factory=dict)
    # Per lane: count of holdings where artifact is usable (READY or LIMITED status)
    artifacts_usable_by_lane: dict[str, int] = field(default_factory=dict)
    # Per lane: count of holdings where artifact is STRONG (READY + primary authority)
    artifacts_strong_by_lane: dict[str, int] = field(default_factory=dict)
    # Per root_cause_bucket: count of holdings (primary gap only; backward compatible)
    root_cause_bucket_counts: dict[str, int] = field(default_factory=dict)
    # Per blocking_gap_bucket: count of holdings where that gap appears (multi-gap aware)
    blocking_gap_bucket_counts: dict[str, int] = field(default_factory=dict)
    # Classification of root causes
    provider_limited_count: int = 0       # ETF_PROVIDER_NOT_BUILT | CRYPTO_PROVIDER_NOT_BUILT
    implementation_limited_count: int = 0  # SEC gaps, valuation, target weight, thesis, news
    normalization_limited_count: int = 0   # DATA_PRESENT_NEEDS_CANONICAL_NORMALIZATION

    errors: list[str] = field(default_factory=list)

    # Stage 9D: equity canonical dataset counts.
    # Number of equity holdings where safe_for_equity_dataset=True.
    equity_canonical_dataset_count: int = 0
    # Tickers where the equity dataset row is NOT safe (weak/stale/missing SEC facts).
    equity_canonical_dataset_degraded_tickers: list = field(default_factory=list)
    # Asset-parity roadmap: machine-readable gap summary by asset class.
    asset_parity_roadmap: Optional[dict] = None
    # Per-section count of equity holdings with AVAILABLE or PARTIAL status.
    # Keys are canonical section names (revenue, profitability_or_margin, etc.).
    canonical_equity_dataset_section_counts: dict = field(default_factory=dict)

    # Stage 9E: equity valuation evidence counts.
    # Number of equity holdings where valuation evidence was built (any quality).
    equity_valuation_evidence_count: int = 0
    # Number of equity holdings where valuation_ready=True (defensible evidence exists).
    equity_valuation_ready_count: int = 0
    # Tickers where valuation evidence is degraded (built but not valuation_ready).
    equity_valuation_degraded_tickers: list = field(default_factory=list)
    # Per-missing-reason counts across all equity valuation evidence rows.
    valuation_missing_reason_counts: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "user_id": self.user_id,
            "generated_at": self.generated_at,
            "safe_for_decision": self.safe_for_decision,
            "synthesis_ready": self.synthesis_ready,
            "holdings": [h.to_dict() for h in self.holdings],
            "holdings_by_asset_type": dict(self.holdings_by_asset_type),
            "artifacts_existing_by_lane": dict(self.artifacts_existing_by_lane),
            "artifacts_usable_by_lane": dict(self.artifacts_usable_by_lane),
            "artifacts_strong_by_lane": dict(self.artifacts_strong_by_lane),
            "root_cause_bucket_counts": dict(self.root_cause_bucket_counts),
            "blocking_gap_bucket_counts": dict(self.blocking_gap_bucket_counts),
            "provider_limited_count": self.provider_limited_count,
            "implementation_limited_count": self.implementation_limited_count,
            "normalization_limited_count": self.normalization_limited_count,
            "equity_canonical_dataset_count": self.equity_canonical_dataset_count,
            "equity_canonical_dataset_degraded_tickers": list(self.equity_canonical_dataset_degraded_tickers),
            "asset_parity_roadmap": self.asset_parity_roadmap,
            "canonical_equity_dataset_section_counts": dict(self.canonical_equity_dataset_section_counts),
            "equity_valuation_evidence_count": self.equity_valuation_evidence_count,
            "equity_valuation_ready_count": self.equity_valuation_ready_count,
            "equity_valuation_degraded_tickers": list(self.equity_valuation_degraded_tickers),
            "valuation_missing_reason_counts": dict(self.valuation_missing_reason_counts),
            "errors": list(self.errors),
        }


@dataclass
class _SupplementalData:
    """Supplemental data fetched from DB. Internal use only — not exposed in output."""

    target_tickers: frozenset      # tickers with any target_allocations row
    recommendation_tickers: frozenset  # tickers with any recommendation history
    fact_counts: dict[str, int]   # {artifact_id: count of facts from research_artifact_facts}
    has_portfolio_snapshot: bool  # True if any portfolio_snapshot exists for this user
    # Stage 9D: structured_payload dicts keyed by SEC artifact_id.
    # Used to derive per-section period identities and trend directions.
    # Raw values in payloads are consumed internally by the canonical dataset
    # builder and never serialized.
    sec_fact_records: dict  # {sec_artifact_id: list[structured_payload dict]}


# ── Public API ─────────────────────────────────────────────────────────────────


def compute_data_foundation_forensics(
    *,
    user_id: str,
    tickers: list[str],
    holding_context_by_ticker: dict[str, dict],
    db_client: Any,
) -> DataFoundationForensicsResult:
    """Compute Stage 9B data foundation forensics for the given tickers.

    Inspects persisted artifacts and supplemental portfolio data to classify each
    holding's root causes for missing data foundation.

    Args:
        user_id: authenticated user ID.
        tickers: normalized uppercase ticker symbols to analyze.
        holding_context_by_ticker: {ticker: {"category": ...}} for asset type classification.
        db_client: Supabase client (read-only).

    Returns:
        DataFoundationForensicsResult — always non-None, always safe_for_decision=False,
        always synthesis_ready=False. Errors captured in .errors; never raises.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []

    if not tickers:
        return DataFoundationForensicsResult(
            schema_version=FORENSICS_VERSION,
            user_id=user_id,
            generated_at=now_iso,
            errors=["No tickers provided for forensics analysis."],
        )

    # Stage 5J coverage read model (read-only DB query).
    coverage = compute_research_evidence_coverage(
        user_id=user_id,
        tickers=tickers,
        db_client=db_client,
    )
    errors.extend(coverage.errors)

    # Collect artifact_ids for supplemental fact-count queries.
    artifact_ids_for_counts: set[str] = set()
    sec_artifact_ids_for_facts: set[str] = set()
    for ticker_cov in coverage.ticker_coverage.values():
        for lane_name in (LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST_SENTIMENT):
            lane_cov = ticker_cov.lanes.get(lane_name)
            if lane_cov and lane_cov.artifact_id:
                artifact_ids_for_counts.add(lane_cov.artifact_id)
        # Collect SEC artifact IDs separately for structured_payload fetch (Stage 9D).
        sec_lane = ticker_cov.lanes.get(LANE_SEC_COMPANY_FACTS)
        if sec_lane and sec_lane.artifact_id:
            sec_artifact_ids_for_facts.add(sec_lane.artifact_id)

    # Fetch supplemental data (all fail-soft).
    supplemental = _fetch_supplemental_data(
        user_id=user_id,
        artifact_ids=artifact_ids_for_counts,
        sec_artifact_ids=sec_artifact_ids_for_facts,
        db_client=db_client,
        errors=errors,
    )

    # Build per-holding forensics rows.
    holdings: list[HoldingForensicsRow] = []
    for ticker in tickers:
        ticker_cov = coverage.ticker_coverage.get(ticker)
        holding_ctx = holding_context_by_ticker.get(ticker)
        asset_type = _classify_instrument_category(ticker, holding_ctx)
        lanes = ticker_cov.lanes if ticker_cov else {}

        row = _build_holding_row(
            ticker=ticker,
            asset_type=asset_type,
            lanes=lanes,
            supplemental=supplemental,
        )
        holdings.append(row)

    aggregates = _build_aggregates(holdings, coverage)

    # Stage 9D: compute canonical equity dataset stats + asset parity roadmap.
    equity_canonical_count = 0
    equity_degraded_tickers: list[str] = []
    equity_total = aggregates["holdings_by_asset_type"].get(INSTRUMENT_CATEGORY_EQUITY, 0)
    etf_total = aggregates["holdings_by_asset_type"].get(INSTRUMENT_CATEGORY_ETF, 0)
    crypto_total = aggregates["holdings_by_asset_type"].get(INSTRUMENT_CATEGORY_CRYPTO, 0)

    # Per-section count of equity holdings with AVAILABLE or PARTIAL status.
    _section_available_statuses = {"AVAILABLE", "PARTIAL"}
    section_counts: dict[str, int] = {}

    for h in holdings:
        if h.asset_type == INSTRUMENT_CATEGORY_EQUITY and h.canonical_equity_dataset:
            if h.canonical_equity_dataset.get("safe_for_equity_dataset"):
                equity_canonical_count += 1
            else:
                equity_degraded_tickers.append(h.ticker)
            # Accumulate per-section counts.
            sections = (
                h.canonical_equity_dataset
                .get("operating_trends", {})
                .get("sections", {})
            )
            for section_name, section_data in sections.items():
                if (
                    isinstance(section_data, dict)
                    and section_data.get("status") in _section_available_statuses
                ):
                    section_counts[section_name] = section_counts.get(section_name, 0) + 1

    # Stage 9E: compute equity valuation evidence aggregate counts.
    equity_valuation_evidence_count = 0
    equity_valuation_ready_count = 0
    equity_valuation_degraded_tickers: list[str] = []
    valuation_missing_reason_counts: dict[str, int] = {}

    for h in holdings:
        if h.asset_type == INSTRUMENT_CATEGORY_EQUITY and h.valuation_evidence:
            equity_valuation_evidence_count += 1
            if h.valuation_evidence.get("valuation_ready"):
                equity_valuation_ready_count += 1
            else:
                equity_valuation_degraded_tickers.append(h.ticker)
            for reason_key in h.valuation_evidence.get("missing_reasons", {}):
                valuation_missing_reason_counts[reason_key] = (
                    valuation_missing_reason_counts.get(reason_key, 0) + 1
                )

    parity_roadmap = build_asset_parity_roadmap(
        equity_canonical_count=equity_canonical_count,
        equity_valuation_count=equity_valuation_evidence_count,
        equity_valuation_ready_count=equity_valuation_ready_count,
        equity_total=equity_total,
        equity_edge_case_tickers=equity_degraded_tickers,
        etf_total=etf_total,
        crypto_total=crypto_total,
    )

    return DataFoundationForensicsResult(
        schema_version=FORENSICS_VERSION,
        user_id=user_id,
        generated_at=now_iso,
        safe_for_decision=False,
        synthesis_ready=False,
        holdings=holdings,
        holdings_by_asset_type=aggregates["holdings_by_asset_type"],
        artifacts_existing_by_lane=aggregates["artifacts_existing_by_lane"],
        artifacts_usable_by_lane=aggregates["artifacts_usable_by_lane"],
        artifacts_strong_by_lane=aggregates["artifacts_strong_by_lane"],
        root_cause_bucket_counts=aggregates["root_cause_bucket_counts"],
        blocking_gap_bucket_counts=aggregates["blocking_gap_bucket_counts"],
        provider_limited_count=aggregates["provider_limited_count"],
        implementation_limited_count=aggregates["implementation_limited_count"],
        normalization_limited_count=aggregates["normalization_limited_count"],
        equity_canonical_dataset_count=equity_canonical_count,
        equity_canonical_dataset_degraded_tickers=equity_degraded_tickers,
        asset_parity_roadmap=parity_roadmap.to_dict(),
        canonical_equity_dataset_section_counts=section_counts,
        equity_valuation_evidence_count=equity_valuation_evidence_count,
        equity_valuation_ready_count=equity_valuation_ready_count,
        equity_valuation_degraded_tickers=equity_valuation_degraded_tickers,
        valuation_missing_reason_counts=valuation_missing_reason_counts,
        errors=errors,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────


def _fetch_supplemental_data(
    *,
    user_id: str,
    artifact_ids: set[str],
    sec_artifact_ids: set[str],
    db_client: Any,
    errors: list[str],
) -> _SupplementalData:
    """Fetch supplemental DB data for forensics. All queries are fail-soft."""
    target_tickers: frozenset = frozenset()
    recommendation_tickers: frozenset = frozenset()
    fact_counts: dict[str, int] = {}
    has_portfolio_snapshot = False
    sec_fact_records: dict[str, list[dict]] = {}

    try:
        result = (
            db_client.table("target_allocations")
            .select("ticker")
            .eq("user_id", user_id)
            .execute()
        )
        target_tickers = frozenset(
            r.get("ticker", "").strip().upper()
            for r in (result.data or [])
            if isinstance(r.get("ticker"), str) and r["ticker"].strip()
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"target_allocations_query_failed: {type(exc).__name__}")

    try:
        result = (
            db_client.table("recommendations")
            .select("ticker")
            .eq("user_id", user_id)
            .execute()
        )
        recommendation_tickers = frozenset(
            r.get("ticker", "").strip().upper()
            for r in (result.data or [])
            if isinstance(r.get("ticker"), str) and r["ticker"].strip()
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"recommendations_query_failed: {type(exc).__name__}")

    if artifact_ids:
        try:
            result = (
                db_client.table("research_artifact_facts")
                .select("artifact_id")
                .in_("artifact_id", list(artifact_ids))
                .limit(5000)
                .execute()
            )
            for row in result.data or []:
                aid = row.get("artifact_id")
                if isinstance(aid, str) and aid:
                    fact_counts[aid] = fact_counts.get(aid, 0) + 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"research_artifact_facts_query_failed: {type(exc).__name__}")

    try:
        result = (
            db_client.table("portfolio_snapshots")
            .select("id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        has_portfolio_snapshot = bool(result.data)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"portfolio_snapshots_query_failed: {type(exc).__name__}")

    # Stage 9D: fetch structured_payload for SEC artifacts to enable per-section
    # period identities and trend directions in the canonical equity dataset.
    # Raw values in structured_payload are consumed internally and never serialized.
    if sec_artifact_ids:
        try:
            result = (
                db_client.table("research_artifact_facts")
                .select("artifact_id,structured_payload")
                .in_("artifact_id", list(sec_artifact_ids))
                .limit(5000)
                .execute()
            )
            for row in result.data or []:
                aid = row.get("artifact_id")
                payload = row.get("structured_payload")
                if isinstance(aid, str) and aid and isinstance(payload, dict):
                    if aid not in sec_fact_records:
                        sec_fact_records[aid] = []
                    sec_fact_records[aid].append(payload)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"sec_fact_records_query_failed: {type(exc).__name__}")

    return _SupplementalData(
        target_tickers=target_tickers,
        recommendation_tickers=recommendation_tickers,
        fact_counts=fact_counts,
        has_portfolio_snapshot=has_portfolio_snapshot,
        sec_fact_records=sec_fact_records,
    )


def _build_holding_row(
    *,
    ticker: str,
    asset_type: str,
    lanes: dict[str, LaneCoverage],
    supplemental: _SupplementalData,
) -> HoldingForensicsRow:
    """Build a HoldingForensicsRow from Stage 5J lane coverage and supplemental data."""
    target_weight_available = ticker in supplemental.target_tickers

    # yfinance fundamentals
    fund_cov = lanes.get(LANE_FUNDAMENTALS)
    fund_exists = _artifact_exists(fund_cov)
    fund_status = fund_cov.usability_label if fund_cov and fund_exists else None

    # Technicals
    tech_cov = lanes.get(LANE_TECHNICALS)
    tech_exists = _artifact_exists(tech_cov)
    tech_status = tech_cov.usability_label if tech_cov and tech_exists else None

    # News sentiment
    news_cov = lanes.get(LANE_NEWS_SENTIMENT)
    news_exists = _artifact_exists(news_cov)
    news_status = news_cov.usability_label if news_cov and news_exists else None

    # SEC company facts
    sec_cov = lanes.get(LANE_SEC_COMPANY_FACTS)
    sec_exists = _artifact_exists(sec_cov)
    sec_status = sec_cov.usability_label if sec_cov and sec_exists else None
    sec_obs_count: Optional[int] = None
    if sec_cov and sec_cov.artifact_id:
        sec_obs_count = supplemental.fact_counts.get(sec_cov.artifact_id)
    sec_reason_not_strong = _get_sec_reason_not_strong(sec_cov, asset_type)

    # Stage 9C: build a per-artifact readiness diagnostic for weak SEC artifacts.
    # Only built for equities with an existing but non-USABLE SEC artifact.
    sec_companyfacts_diagnostic: Optional[dict] = None
    _sec_usable = sec_status in _USABLE_LABELS
    if sec_exists and sec_cov and not _sec_usable and asset_type == INSTRUMENT_CATEGORY_EQUITY:
        diag = diagnose_sec_companyfacts_readiness(
            artifact_id=sec_cov.artifact_id,
            generated_at=sec_cov.generated_at,
            model_version=sec_cov.model_version,
            observation_count=sec_obs_count,
            freshness_status=sec_cov.freshness_status,
            source_authority=sec_cov.source_authority,
            completeness_band=sec_cov.completeness_band,
            usability_label=sec_cov.usability_label,
            suppression_reason=sec_cov.suppression_reason,
            contradiction_evaluable=(
                sec_cov.has_contradictions is not None
            ),
            contradiction_count=getattr(sec_cov, "contradiction_count", None),
            not_evaluable_reason=getattr(sec_cov, "not_evaluable_reason", None),
            sample_contradiction_groups=getattr(sec_cov, "sample_contradiction_groups", None),
        )
        sec_companyfacts_diagnostic = diag.to_dict()

    # SEC catalyst sentiment
    cat_cov = lanes.get(LANE_SEC_CATALYST_SENTIMENT)
    cat_exists = _artifact_exists(cat_cov)
    cat_status = cat_cov.usability_label if cat_cov and cat_exists else None
    cat_count: Optional[int] = None
    if cat_cov and cat_cov.artifact_id:
        cat_count = supplemental.fact_counts.get(cat_cov.artifact_id)

    # Stage 9E: scaffold exists for all equity holdings, but numeric inputs are not yet in scope.
    # valuation_evidence_model_present = scaffold ran; valuation_numeric_ready = actual numeric gate.
    # VALUATION_LANE_NOT_BUILT still appears in gaps (valuation_numeric_ready is always False at 9E).
    valuation_evidence_model_present = (asset_type == INSTRUMENT_CATEGORY_EQUITY)
    valuation_numeric_ready = False  # always False at Stage 9E — no numeric EPS/price in scope
    valuation_summary = _get_valuation_summary(asset_type, valuation_lane_built=valuation_evidence_model_present)

    # ETF fund composition — no provider built at Stage 9B
    etf_fund_composition_artifact_exists = False

    # Crypto market context proxy: technical artifact for crypto tickers
    crypto_market_context_exists = tech_exists if asset_type == INSTRUMENT_CATEGORY_CRYPTO else False

    thesis_history_exists = ticker in supplemental.recommendation_tickers

    # Classify all blocking gaps (multi-gap aware).
    all_gaps = _classify_all_gaps(
        asset_type=asset_type,
        has_fundamentals_artifact=fund_exists,
        has_technical_artifact=tech_exists,
        has_sec_companyfacts_artifact=sec_exists,
        sec_companyfacts_usability=sec_status,
        has_sec_catalyst_artifact=cat_exists,
        has_news_sentiment_artifact=news_exists,
        news_sentiment_usability=news_status,
        has_target_weight=target_weight_available,
        has_thesis_history=thesis_history_exists,
        valuation_lane_exists=valuation_numeric_ready,
        valuation_evidence_model_present=valuation_evidence_model_present,
    )

    blocking_gap_buckets = [g[0] for g in all_gaps]
    next_required_fixes = [g[1] for g in all_gaps]
    root_cause_bucket = blocking_gap_buckets[0]
    next_required_fix = next_required_fixes[0]

    # Stage 9D: build canonical equity dataset row for equity holdings.
    # ETF and crypto receive None (their own provider lanes are required separately).
    canonical_equity_dataset: Optional[dict] = None
    valuation_evidence: Optional[dict] = None
    if asset_type == INSTRUMENT_CATEGORY_EQUITY:
        # Get SEC fact records for this ticker's artifact (if available).
        _sec_art_id = sec_cov.artifact_id if sec_cov else None
        _sec_facts = (
            supplemental.sec_fact_records.get(_sec_art_id, [])
            if _sec_art_id else []
        )
        ced_row = build_canonical_equity_dataset_row(
            ticker=ticker,
            asset_type=asset_type,
            lanes=lanes,
            sec_obs_count=sec_obs_count,
            cat_count=cat_count,
            sec_fact_records=_sec_facts if _sec_facts else None,
        )
        canonical_equity_dataset = ced_row.to_dict()

        # Stage 9E: build valuation evidence from the canonical dataset row.
        # price_available is proxied from portfolio snapshot existence.
        val_row = build_equity_valuation_evidence_row(
            canonical_row=ced_row,
            price_available=supplemental.has_portfolio_snapshot,
        )
        valuation_evidence = val_row.to_dict()

    return HoldingForensicsRow(
        ticker=ticker,
        asset_type=asset_type,
        current_position_available=True,
        current_weight_available=supplemental.has_portfolio_snapshot,
        target_weight_available=target_weight_available,
        yfinance_fundamentals_artifact_exists=fund_exists,
        yfinance_fundamentals_status=fund_status,
        technical_artifact_exists=tech_exists,
        technical_status=tech_status,
        news_sentiment_artifact_exists=news_exists,
        news_sentiment_status=news_status,
        sec_companyfacts_artifact_exists=sec_exists,
        sec_companyfacts_status=sec_status,
        sec_companyfacts_observation_count=sec_obs_count,
        sec_companyfacts_reason_not_strong=sec_reason_not_strong,
        sec_catalyst_artifact_exists=cat_exists,
        sec_catalyst_status=cat_status,
        sec_catalyst_count=cat_count,
        valuation_evidence_model_present=valuation_evidence_model_present,
        valuation_numeric_ready=valuation_numeric_ready,
        valuation_inputs_available_summary=valuation_summary,
        etf_fund_composition_artifact_exists=etf_fund_composition_artifact_exists,
        crypto_market_context_artifact_exists=crypto_market_context_exists,
        thesis_history_exists=thesis_history_exists,
        root_cause_bucket=root_cause_bucket,
        next_required_fix=next_required_fix,
        blocking_gap_buckets=blocking_gap_buckets,
        blocking_gap_count=len(blocking_gap_buckets),
        next_required_fixes=next_required_fixes,
        sec_companyfacts_diagnostic=sec_companyfacts_diagnostic,
        canonical_equity_dataset=canonical_equity_dataset,
        valuation_evidence=valuation_evidence,
    )


def _classify_all_gaps(
    *,
    asset_type: str,
    has_fundamentals_artifact: bool,
    has_technical_artifact: bool,
    has_sec_companyfacts_artifact: bool,
    sec_companyfacts_usability: Optional[str],
    has_sec_catalyst_artifact: bool,
    has_news_sentiment_artifact: bool,
    news_sentiment_usability: Optional[str],
    has_target_weight: bool,
    has_thesis_history: bool,
    valuation_lane_exists: bool = False,
    valuation_evidence_model_present: bool = False,
) -> list[tuple[str, str]]:
    """Return all material data foundation gaps in deterministic priority order.

    Returns a list of (bucket, fix_message) pairs. The first element is always
    the primary/most-blocking gap (same as the legacy root_cause_bucket). All
    subsequent elements are secondary gaps that should also be addressed.

    Rules:
    - ETF: ETF_PROVIDER_NOT_BUILT + applicable secondary gaps (target/thesis/news)
    - Crypto: CRYPTO_PROVIDER_NOT_BUILT + applicable secondary gaps
    - Unknown: [ASSET_TYPE_NOT_APPLICABLE] only
    - Equity: SEC gap (if any) + valuation + news + target weight + thesis, in order
      DATA_PRESENT_NEEDS_CANONICAL_NORMALIZATION only when all others resolved
    - No equity SEC/fundamentals gaps for ETF or crypto
    """
    gaps: list[tuple[str, str]] = []

    # ── Unknown asset type ─────────────────────────────────────────────────────
    if asset_type == INSTRUMENT_CATEGORY_UNKNOWN:
        return [
            (
                BUCKET_NOT_APPLICABLE,
                (
                    "Classify the asset type (equity/ETF/crypto) in portfolio category metadata "
                    "before data foundation requirements can be determined."
                ),
            )
        ]

    # ── ETF path ───────────────────────────────────────────────────────────────
    if asset_type == INSTRUMENT_CATEGORY_ETF:
        gaps.append((
            BUCKET_ETF_NOT_BUILT,
            (
                "Build a dedicated ETF fund-data provider lane for holdings, sector exposure, "
                "expense ratio, and yield. No fund-data provider exists in Stage 5J/5K; "
                "ETF composition is MISSING by design until a provider is built (Stage 9C)."
            ),
        ))
        _append_non_equity_secondary_gaps(
            gaps,
            has_news_sentiment_artifact=has_news_sentiment_artifact,
            news_sentiment_usability=news_sentiment_usability,
            has_sec_catalyst_artifact=has_sec_catalyst_artifact,
            has_target_weight=has_target_weight,
            has_thesis_history=has_thesis_history,
        )
        return gaps

    # ── Crypto path ─────────────────────────────────────────────────────────────
    if asset_type == INSTRUMENT_CATEGORY_CRYPTO:
        gaps.append((
            BUCKET_CRYPTO_NOT_BUILT,
            (
                "Build a dedicated crypto market-data provider lane for market regime, liquidity, "
                "and correlation inputs. Equity fundamentals and SEC data are NOT_APPLICABLE for "
                "crypto (Stage 9D). Do not reuse equity fundamentals for crypto."
            ),
        ))
        _append_non_equity_secondary_gaps(
            gaps,
            has_news_sentiment_artifact=has_news_sentiment_artifact,
            news_sentiment_usability=news_sentiment_usability,
            has_sec_catalyst_artifact=has_sec_catalyst_artifact,
            has_target_weight=has_target_weight,
            has_thesis_history=has_thesis_history,
        )
        return gaps

    # ── Equity path ─────────────────────────────────────────────────────────────

    # 1. SEC gap (at most one of: WORKER, CIK, or WEAK).
    sec_usability = sec_companyfacts_usability or ""
    if not has_sec_companyfacts_artifact:
        if has_fundamentals_artifact or has_technical_artifact:
            gaps.append((
                BUCKET_SEC_MISSING_CIK,
                (
                    "Other evidence lanes (fundamentals/technicals) ran for this ticker but "
                    "SEC company facts were skipped. Likely cause: CIK not found in SEC EDGAR "
                    "or zero XBRL observations returned. Investigate sec_companyfacts_skip_non_equity "
                    "or sec_companyfacts_skip_no_artifact logs for this ticker."
                ),
            ))
        else:
            gaps.append((
                BUCKET_SEC_MISSING_WORKER,
                (
                    "No evidence artifacts found for this ticker. Run POST /intel/v3/run with "
                    "INTEL_V3_SEC_COMPANYFACTS_EVIDENCE_ENABLED=true and SEC_EDGAR_USER_AGENT set. "
                    "Also enable INTEL_V3_FUNDAMENTALS_EVIDENCE_ENABLED and "
                    "INTEL_V3_TECHNICALS_EVIDENCE_ENABLED to populate baseline artifacts."
                ),
            ))
    elif (
        not sec_usability
        or sec_usability.startswith(_SUPPRESSED_PREFIX)
        or sec_usability == "NOT_EVALUABLE"
    ):
        gaps.append((
            BUCKET_SEC_EXISTS_WEAK,
            (
                "SEC company facts artifact exists but truth usability is below USABLE "
                f"(label={sec_usability or 'None'}). Investigate XBRL contradiction grouping, "
                "completeness assessment (THIN completeness = SUPPRESSED_INCOMPLETE), or "
                "truth adapter enrichment pipeline for this ticker."
            ),
        ))

    # 2. Valuation lane (equity-specific).
    if not valuation_lane_exists:
        sec_usable_for_gap = bool(
            has_sec_companyfacts_artifact
            and sec_usability in ("USABLE", "USABLE_WITH_LIMITATIONS")
        )
        if valuation_evidence_model_present:
            # Stage 9E scaffold exists but numeric EPS/price are not yet in scope.
            detail = (
                "Pending: numeric EPS/price pipeline confirmation for Stage 9E."
                if sec_usable_for_gap
                else "Blocked by SEC company facts gap above."
            )
            gaps.append((
                BUCKET_VALUATION_NOT_BUILT,
                (
                    "Stage 9E evidence scaffold is built for this equity. "
                    "Numeric EPS/price inputs are intentionally not in scope at this stage; "
                    "valuation_interpretation_band is UNKNOWN. "
                    + detail
                ),
            ))
        elif sec_usable_for_gap:
            gaps.append((
                BUCKET_VALUATION_NOT_BUILT,
                (
                    "Canonical equity research dataset (Stage 9D) is built and usable. "
                    "Next: build the valuation evidence lane (Stage 9E) to normalize "
                    "revenue/margin/FCF trends into P/E, EV/EBITDA, and growth-adjusted "
                    "valuation inputs ready for synthesis gating."
                ),
            ))
        else:
            gaps.append((
                BUCKET_VALUATION_NOT_BUILT,
                (
                    "No valuation evidence lane exists. SEC/fundamentals data is present "
                    "but canonical equity research dataset is not yet safe (see SEC gap above). "
                    "Fix the SEC company facts artifact first, then build the canonical dataset "
                    "adapter and valuation normalization layer (Stage 9D/9E)."
                ),
            ))

    # 3. News sentiment suppressed with no catalyst substitute (equity-specific).
    news_usability = news_sentiment_usability or ""
    if (
        has_news_sentiment_artifact
        and news_usability.startswith(_SUPPRESSED_PREFIX)
        and not has_sec_catalyst_artifact
    ):
        gaps.append((
            BUCKET_NEWS_SUPPRESSED,
            (
                "News sentiment is suppressed by editorial context (by design — yfinance news "
                "is EDITORIAL_CONTEXT → THIN → SUPPRESSED_INCOMPLETE). "
                "Enable INTEL_V3_SENTIMENT_CATALYST_EVIDENCE_ENABLED=true to activate the SEC "
                "catalyst sentiment lane, which provides LIMITED-grade sentiment from real filings."
            ),
        ))

    # 4. Target weight.
    if not has_target_weight:
        gaps.append((
            BUCKET_TARGET_WEIGHT_NOT_BUILT,
            (
                "Portfolio target weight is not set for this ticker. Add a target allocation "
                "in target_allocations to enable portfolio-sizing context in synthesis."
            ),
        ))

    # 5. Thesis / decision history.
    if not has_thesis_history:
        gaps.append((
            BUCKET_THESIS_NOT_BUILT,
            (
                "No recommendation history found for this ticker. Run Intel v3 at least once "
                "to generate an analyst recommendation and build decision history."
            ),
        ))

    # 6. All core data present and usable — canonical dataset built, synthesis still blocked.
    if not gaps:
        gaps.append((
            BUCKET_DATA_NEEDS_NORMALIZATION,
            (
                "Core evidence artifacts exist and are usable. Canonical equity research "
                "dataset (Stage 9D) is built. Synthesis remains blocked until all asset "
                "classes (equities, ETFs, crypto) have S-grade canonical datasets and "
                "valuation lanes are wired (Stage 9E+)."
            ),
        ))

    return gaps


def _append_non_equity_secondary_gaps(
    gaps: list[tuple[str, str]],
    *,
    has_news_sentiment_artifact: bool,
    news_sentiment_usability: Optional[str],
    has_sec_catalyst_artifact: bool,
    has_target_weight: bool,
    has_thesis_history: bool,
) -> None:
    """Append secondary gaps applicable to ETF and crypto holdings."""
    news_usability = news_sentiment_usability or ""
    if (
        has_news_sentiment_artifact
        and news_usability.startswith(_SUPPRESSED_PREFIX)
        and not has_sec_catalyst_artifact
    ):
        gaps.append((
            BUCKET_NEWS_SUPPRESSED,
            (
                "News sentiment is suppressed by editorial context (by design — yfinance news "
                "is EDITORIAL_CONTEXT → THIN → SUPPRESSED_INCOMPLETE). "
                "Enable INTEL_V3_SENTIMENT_CATALYST_EVIDENCE_ENABLED=true to activate the SEC "
                "catalyst sentiment lane, which provides LIMITED-grade sentiment from real filings."
            ),
        ))

    if not has_target_weight:
        gaps.append((
            BUCKET_TARGET_WEIGHT_NOT_BUILT,
            (
                "Portfolio target weight is not set for this ticker. Add a target allocation "
                "in target_allocations to enable portfolio-sizing context in synthesis."
            ),
        ))

    if not has_thesis_history:
        gaps.append((
            BUCKET_THESIS_NOT_BUILT,
            (
                "No recommendation history found for this ticker. Run Intel v3 at least once "
                "to generate an analyst recommendation and build decision history."
            ),
        ))


def _classify_root_cause(
    *,
    asset_type: str,
    has_fundamentals_artifact: bool,
    has_technical_artifact: bool,
    has_sec_companyfacts_artifact: bool,
    sec_companyfacts_usability: Optional[str],
    has_sec_catalyst_artifact: bool,
    has_news_sentiment_artifact: bool,
    news_sentiment_usability: Optional[str],
    has_target_weight: bool,
    has_thesis_history: bool,
    valuation_lane_exists: bool = False,
    valuation_evidence_model_present: bool = False,
) -> tuple[str, str]:
    """Return the primary (highest-priority) root cause bucket and fix message.

    Backward-compatible wrapper around _classify_all_gaps. Returns the first
    (most blocking) gap from the full gap list. Tests that verify specific
    single-bucket behavior call this function directly.
    """
    gaps = _classify_all_gaps(
        asset_type=asset_type,
        has_fundamentals_artifact=has_fundamentals_artifact,
        has_technical_artifact=has_technical_artifact,
        has_sec_companyfacts_artifact=has_sec_companyfacts_artifact,
        sec_companyfacts_usability=sec_companyfacts_usability,
        has_sec_catalyst_artifact=has_sec_catalyst_artifact,
        has_news_sentiment_artifact=has_news_sentiment_artifact,
        news_sentiment_usability=news_sentiment_usability,
        has_target_weight=has_target_weight,
        has_thesis_history=has_thesis_history,
        valuation_lane_exists=valuation_lane_exists,
        valuation_evidence_model_present=valuation_evidence_model_present,
    )
    return gaps[0]


def _artifact_exists(lane_cov: Optional[LaneCoverage]) -> bool:
    """True when a non-null artifact_id is present (artifact was written to DB)."""
    return lane_cov is not None and lane_cov.artifact_id is not None


def _get_sec_reason_not_strong(
    lane_cov: Optional[LaneCoverage],
    asset_type: str,
) -> Optional[str]:
    """Return a plain-English reason why SEC company facts are not STRONG, or None if they are."""
    if asset_type in (INSTRUMENT_CATEGORY_ETF, INSTRUMENT_CATEGORY_CRYPTO):
        return "SEC company facts are not applicable for this asset type."

    if lane_cov is None or not _artifact_exists(lane_cov):
        return "No artifact found — SEC evidence lane has not run or CIK lookup failed for this ticker."

    usability = lane_cov.usability_label or ""

    if usability == "USABLE":
        # Check if authority qualifies it as STRONG.
        authority = lane_cov.source_authority or ""
        if authority in _PRIMARY_AUTHORITY_LEVELS:
            return None  # it IS strong
        return (
            f"Artifact is USABLE but source authority ({authority or 'unknown'}) "
            "is below PRIMARY_AUTHORITY level."
        )

    if usability == "USABLE_WITH_LIMITATIONS":
        return (
            "Artifact is USABLE_WITH_LIMITATIONS (LIMITED coverage). "
            "XBRL observations exist but data is partial or single-source."
        )

    if "SUPPRESSED_CONTRADICTED" in usability:
        return (
            "XBRL observations were flagged as contradictions. "
            "Check sec_companyfacts_usability_summary logs for sample_group_keys."
        )

    if "SUPPRESSED_INCOMPLETE" in usability:
        return (
            "Completeness assessment is THIN — too few qualifying XBRL observations "
            "to reach the PARTIAL completeness threshold."
        )

    if usability.startswith(_SUPPRESSED_PREFIX):
        return f"Artifact suppressed: usability_label={usability}."

    if usability == "NOT_EVALUABLE":
        return (
            "Truth assessment is NOT_EVALUABLE — source credibility enrichment or "
            "truth adapter metadata is missing for this artifact."
        )

    return f"Unknown usability label: {usability}."


def _get_valuation_summary(asset_type: str, *, valuation_lane_built: bool = False) -> str:
    """Return a plain-English summary of available valuation inputs."""
    if asset_type in (INSTRUMENT_CATEGORY_ETF, INSTRUMENT_CATEGORY_CRYPTO):
        return (
            "Valuation metrics are not applicable for this asset type "
            f"({asset_type}). ETF/crypto holdings do not use single-issuer P/E or EV metrics."
        )
    if valuation_lane_built:
        return (
            "Equity valuation evidence lane (Stage 9E) is built. "
            "See valuation_evidence for input_readiness and valuation_context per section. "
            "valuation_interpretation_band is UNKNOWN until numeric EPS/price inputs are in scope."
        )
    return (
        "No valuation evidence lane built in Stage 5J/5K (Stage 9B baseline). "
        "Price-band context is feature-flagged separately via INTEL_V3_PRICEBAND_VISIBLE_CONTEXT_V1_ENABLED. "
        "No P/E, EV/EBITDA, or valuation model inputs are staged in research artifacts yet."
    )


def _build_aggregates(
    holdings: list[HoldingForensicsRow],
    coverage: ResearchEvidenceCoverageSummary,
) -> dict[str, Any]:
    """Build portfolio-level aggregate counts from per-holding rows."""
    holdings_by_asset_type: dict[str, int] = {}
    root_cause_bucket_counts: dict[str, int] = {}
    blocking_gap_bucket_counts: dict[str, int] = {}
    provider_limited = 0
    implementation_limited = 0
    normalization_limited = 0

    existing: dict[str, int] = {ln: 0 for ln in _TRACKED_LANES}
    usable: dict[str, int] = {ln: 0 for ln in _TRACKED_LANES}
    strong: dict[str, int] = {ln: 0 for ln in _TRACKED_LANES}

    for row in holdings:
        holdings_by_asset_type[row.asset_type] = (
            holdings_by_asset_type.get(row.asset_type, 0) + 1
        )

        # root_cause_bucket_counts: primary gap only (backward compatible)
        root_cause_bucket_counts[row.root_cause_bucket] = (
            root_cause_bucket_counts.get(row.root_cause_bucket, 0) + 1
        )

        # blocking_gap_bucket_counts: all gaps across all holdings
        for gap_bucket in row.blocking_gap_buckets:
            blocking_gap_bucket_counts[gap_bucket] = (
                blocking_gap_bucket_counts.get(gap_bucket, 0) + 1
            )

        if row.root_cause_bucket in _PROVIDER_LIMITED_BUCKETS:
            provider_limited += 1
        elif row.root_cause_bucket in _IMPLEMENTATION_LIMITED_BUCKETS:
            implementation_limited += 1
        elif row.root_cause_bucket in _NORMALIZATION_LIMITED_BUCKETS:
            normalization_limited += 1

    # Lane-level aggregates from Stage 5J coverage.
    for ticker, ticker_cov in coverage.ticker_coverage.items():
        for lane_name in _TRACKED_LANES:
            lane_cov = ticker_cov.lanes.get(lane_name)
            if lane_cov is None:
                continue
            if lane_cov.artifact_id is not None:
                existing[lane_name] = existing.get(lane_name, 0) + 1
            if lane_cov.is_usable:
                usable[lane_name] = usable.get(lane_name, 0) + 1
            if (
                lane_cov.status == "READY"
                and (lane_cov.source_authority or "") in _PRIMARY_AUTHORITY_LEVELS
            ):
                strong[lane_name] = strong.get(lane_name, 0) + 1

    return {
        "holdings_by_asset_type": holdings_by_asset_type,
        "artifacts_existing_by_lane": existing,
        "artifacts_usable_by_lane": usable,
        "artifacts_strong_by_lane": strong,
        "root_cause_bucket_counts": root_cause_bucket_counts,
        "blocking_gap_bucket_counts": blocking_gap_bucket_counts,
        "provider_limited_count": provider_limited,
        "implementation_limited_count": implementation_limited,
        "normalization_limited_count": normalization_limited,
    }
