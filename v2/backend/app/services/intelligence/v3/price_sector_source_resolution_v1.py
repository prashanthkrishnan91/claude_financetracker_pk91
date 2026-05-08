"""Phase 14C-Prep — Price + Sector Source Resolution v1 (read-only diagnostics).

Purpose:
    Ranks candidate stored data sources for two durable inputs that Phase 14C
    valuation computation requires for company tickers:

        1. current/fresh-enough market price with a defensible freshness basis
        2. financial sector / industry classification (GICS-style — NOT
           portfolio category)

    The module is pure and aggregate-only. The endpoint that wraps it queries
    each candidate, summarises per-candidate counts, then this module:
        - selects the best candidate for each dimension by deterministic rules
        - reports a certification status (CERTIFIED | PARTIAL | UNCERTIFIED |
          MISSING)
        - returns a ready_for_phase14c_computation flag and blocking reasons

Source-ranking rules (deterministic, no LLM, no provider):

    Price candidates considered:
        - price_history (table)
              has explicit price_date freshness basis
        - market_snapshots (table)
              has explicit as_of freshness basis
        - agent_features (table)
              has explicit as_of/created_at freshness basis
        - positions.market_value / cost_basis  ── REJECTED unconditionally:
              no explicit quote date, derived from holdings, may be stale by
              months. Per task spec: position market_value / cost_basis must
              NOT be treated as fresh/current price unless a defensible
              price/date source exists. None of these have one, so the
              candidate is recorded as REJECTED for source-ranking visibility.

    Sector candidates considered:
        - market_snapshots.sector  (per-run, per-ticker)
        - agent_features.sector    (per-run, per-ticker)
        - intel_v3_snapshots.payload  (peek-only count, JSON blob — UNCERTIFIED
              by default because we will not deserialise the full payload at
              diagnostics time)
        - positions.category  ── REJECTED unconditionally:
              "Crypto/Core/ETF/Other/IPO/SELL" is portfolio category, not a
              GICS financial sector.

    Certification rules:
        CERTIFIED   — fresh count >= sec_company_anchor_count (defaults to
                      sec_ready+sec_partial company tickers) AND a freshness
                      basis exists
        PARTIAL     — fresh count > 0 AND fresh count < anchor count
        UNCERTIFIED — available count > 0 AND fresh count == 0 (stale-only),
                      OR no freshness basis on the candidate
        MISSING     — no records observed for any company ticker

    Selection:
        Among non-REJECTED candidates with the same status, the candidate with
        the higher fresh count wins. Ties broken by deterministic candidate
        order (price_history > market_snapshots > agent_features for price;
        market_snapshots > agent_features > intel_v3_snapshots_blob for
        sector). The selected candidate's status is the reported certification.

    ready_for_phase14c_computation:
        True only if BOTH selected price source status == CERTIFIED AND
        selected sector source status == CERTIFIED. Otherwise False with
        documented blocking_reasons.

Hard architectural invariants (non-negotiable):
    - NEVER imports or calls decide() / decision_policy_v1.
    - NEVER mutates DecisionInputV3 or any visible snapshot.
    - NEVER writes to any DB table.
    - NEVER calls any external provider (yfinance, SEC, OpenAI, Anthropic).
    - NEVER computes a P/E, P/B, EV/EBITDA, FCF yield, earnings yield, or any
      valuation ratio.
    - NEVER produces a PriceBand value.
    - NEVER returns raw price values, raw sector strings (only aggregate
      counts), source URLs, or per-ticker rows.
    - safe_for_decision is always False.
    - visible_snapshot_unchanged is always True.
    - read_only is always True.
    - diagnostics_only is always True.
    - valuation_ratios_computed is always False.
    - earnings_yield_computed is always False.
    - price_context_unchanged is always True.
    - This module is pure — no IO, no DB, no LLM.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PRICE_SECTOR_SOURCE_RESOLUTION_V1_CONTRACT_VERSION: str = "phase14c_prep_v1"

# Calendar days before a stored price is considered stale for Phase 14C purposes.
# Aligned with Phase 14B PRICE_STALE_THRESHOLD_DAYS to keep contracts consistent.
PRICE_STALE_THRESHOLD_DAYS: int = 7

# Stable certification labels.
CERTIFIED: str = "CERTIFIED"
PARTIAL: str = "PARTIAL"
UNCERTIFIED: str = "UNCERTIFIED"
MISSING: str = "MISSING"
REJECTED: str = "REJECTED"  # candidate disqualified before ranking

# Stable candidate names. These are aggregate-safe — they identify the *table*
# the data lives in, not raw values from the table.
PRICE_CANDIDATE_PRICE_HISTORY: str = "price_history_table"
PRICE_CANDIDATE_MARKET_SNAPSHOTS: str = "market_snapshots_table"
PRICE_CANDIDATE_AGENT_FEATURES: str = "agent_features_table"
PRICE_CANDIDATE_POSITIONS_DERIVED: str = "positions_market_value_or_cost_basis_rejected"

SECTOR_CANDIDATE_MARKET_SNAPSHOTS: str = "market_snapshots_sector"
SECTOR_CANDIDATE_AGENT_FEATURES: str = "agent_features_sector"
SECTOR_CANDIDATE_INTEL_V3_PAYLOAD_BLOB: str = "intel_v3_snapshots_payload_blob"
SECTOR_CANDIDATE_POSITIONS_CATEGORY: str = "positions_category_rejected_portfolio_category"

# Deterministic candidate priority for tie-breaking when status is equal.
_PRICE_CANDIDATE_ORDER: tuple[str, ...] = (
    PRICE_CANDIDATE_PRICE_HISTORY,
    PRICE_CANDIDATE_MARKET_SNAPSHOTS,
    PRICE_CANDIDATE_AGENT_FEATURES,
)
_SECTOR_CANDIDATE_ORDER: tuple[str, ...] = (
    SECTOR_CANDIDATE_MARKET_SNAPSHOTS,
    SECTOR_CANDIDATE_AGENT_FEATURES,
    SECTOR_CANDIDATE_INTEL_V3_PAYLOAD_BLOB,
)

_STATUS_RANK: dict[str, int] = {
    CERTIFIED: 4,
    PARTIAL: 3,
    UNCERTIFIED: 2,
    MISSING: 1,
    REJECTED: 0,
}


@dataclass(frozen=True)
class PriceCandidateStats:
    """Aggregate-only stats for a single price candidate source.

    All counts are non-negative integers. The candidate's freshness_basis must
    be one of: 'price_date', 'as_of', 'created_at', or 'none'. A candidate
    with freshness_basis='none' cannot be CERTIFIED — it can be UNCERTIFIED
    at best.

    rejected_reason, when non-empty, marks the candidate as structurally
    disqualified before ranking (e.g. derived from positions market_value
    without a quote date).
    """
    name: str
    available_count: int = 0
    fresh_count: int = 0
    stale_count: int = 0
    missing_count: int = 0
    freshness_basis: str = "none"  # 'price_date' | 'as_of' | 'created_at' | 'none'
    rejected_reason: str = ""


@dataclass(frozen=True)
class SectorCandidateStats:
    """Aggregate-only stats for a single sector/industry candidate source.

    available_count is the number of company tickers with a non-empty,
    non-portfolio-category sector value present in the candidate. industry
    counts are optional — pass 0 when unavailable.
    """
    name: str
    available_count: int = 0
    industry_available_count: int = 0
    missing_count: int = 0
    rejected_reason: str = ""


@dataclass(frozen=True)
class PriceSectorSourceResolutionResult:
    """Aggregate-only Phase 14C-Prep source-resolution result.

    Forbidden (never present in any field):
        - raw price values, sector names, industry names
        - per-ticker dictionaries
        - source URLs / HTTP payloads
        - valuation ratios, PriceBand values, fair value, price targets

    Invariants:
        safe_for_decision is always False.
        visible_snapshot_unchanged is always True.
        read_only is always True.
        diagnostics_only is always True.
        valuation_ratios_computed is always False.
        earnings_yield_computed is always False.
        price_context_unchanged is always True.
    """
    adapter_version: str
    safe_for_decision: bool
    visible_snapshot_unchanged: bool
    read_only: bool
    diagnostics_only: bool
    valuation_ratios_computed: bool
    earnings_yield_computed: bool
    price_context_unchanged: bool

    portfolio_ticker_count: int
    company_ticker_count: int
    non_company_ticker_count: int

    # Price source resolution
    price_source_candidates_checked: list[str]
    selected_price_source_name: str
    selected_price_source_available_count: int
    selected_price_source_fresh_count: int
    selected_price_source_stale_count: int
    selected_price_source_missing_count: int
    selected_price_source_freshness_basis: str
    price_source_certification_status: str

    # Sector source resolution
    sector_source_candidates_checked: list[str]
    selected_sector_source_name: str
    selected_sector_available_count: int
    selected_industry_available_count: int
    selected_sector_missing_count: int
    sector_source_certification_status: str

    # Phase 14C readiness gate
    ready_for_phase14c_computation: bool
    phase14c_blocking_reasons: list[str]
    recommended_next_step: str

    errors: list[str] = field(default_factory=list)


def _classify_price_candidate(
    cand: PriceCandidateStats,
    company_anchor_count: int,
) -> str:
    """Deterministic certification classifier for a price candidate."""
    if cand.rejected_reason:
        return REJECTED
    if cand.freshness_basis == "none":
        # No timestamp/freshness basis at all — can never be certified fresh.
        return UNCERTIFIED if cand.available_count > 0 else MISSING
    if cand.available_count <= 0:
        return MISSING
    if cand.fresh_count <= 0:
        return UNCERTIFIED  # only stale records, no current price
    if company_anchor_count > 0 and cand.fresh_count >= company_anchor_count:
        return CERTIFIED
    return PARTIAL


def _classify_sector_candidate(
    cand: SectorCandidateStats,
    company_anchor_count: int,
) -> str:
    """Deterministic certification classifier for a sector candidate."""
    if cand.rejected_reason:
        return REJECTED
    if cand.available_count <= 0:
        return MISSING
    if company_anchor_count > 0 and cand.available_count >= company_anchor_count:
        return CERTIFIED
    return PARTIAL


def _select_best(
    candidates: list[tuple[str, str, int]],
    priority_order: tuple[str, ...],
) -> tuple[str, str]:
    """Pick winner by (status_rank, fresh-or-available count, priority order).

    Each candidate is (name, status, count). Returns (name, status). Returns
    ("", MISSING) if no eligible (non-REJECTED) candidates.
    """
    eligible = [c for c in candidates if c[1] != REJECTED]
    if not eligible:
        return ("", MISSING)

    def _key(item: tuple[str, str, int]) -> tuple[int, int, int]:
        name, status, count = item
        try:
            order_idx = priority_order.index(name)
        except ValueError:
            order_idx = len(priority_order)
        # Higher status rank wins; higher count wins; lower order index wins.
        return (_STATUS_RANK.get(status, 0), count, -order_idx)

    winner = max(eligible, key=_key)
    return (winner[0], winner[1])


def build_price_sector_source_resolution(
    portfolio_ticker_count: int,
    company_ticker_count: int,
    non_company_ticker_count: int,
    company_anchor_count: int,
    price_candidates: list[PriceCandidateStats],
    sector_candidates: list[SectorCandidateStats],
    extra_errors: list[str] | None = None,
) -> PriceSectorSourceResolutionResult:
    """Pure, deterministic, read-only Phase 14C-Prep source resolution.

    Args:
        portfolio_ticker_count:    Total user portfolio tickers.
        company_ticker_count:      Tickers classified as companies (not ETF/crypto).
        non_company_ticker_count:  ETFs / crypto / other suppressed tickers.
        company_anchor_count:      Anchor count used for CERTIFIED gate. Typically
                                   the count of SEC-fact-ready company tickers
                                   (sec_ready + sec_partial). The anchor floors
                                   what "strong enough for Phase 14C" means.
        price_candidates:          Per-candidate aggregate stats for price.
        sector_candidates:         Per-candidate aggregate stats for sector.
        extra_errors:              Extra errors from the data-fetch layer.

    Returns:
        PriceSectorSourceResolutionResult — never raises.
    """
    try:
        return _build(
            portfolio_ticker_count=portfolio_ticker_count,
            company_ticker_count=company_ticker_count,
            non_company_ticker_count=non_company_ticker_count,
            company_anchor_count=company_anchor_count,
            price_candidates=list(price_candidates),
            sector_candidates=list(sector_candidates),
            extra_errors=list(extra_errors or []),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("price_sector_source_resolution_v1_build_error error=%s", exc)
        return _empty_result(errors=[f"build_error: {type(exc).__name__}: {exc}"])


def _empty_result(errors: list[str]) -> PriceSectorSourceResolutionResult:
    return PriceSectorSourceResolutionResult(
        adapter_version=PRICE_SECTOR_SOURCE_RESOLUTION_V1_CONTRACT_VERSION,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        read_only=True,
        diagnostics_only=True,
        valuation_ratios_computed=False,
        earnings_yield_computed=False,
        price_context_unchanged=True,
        portfolio_ticker_count=0,
        company_ticker_count=0,
        non_company_ticker_count=0,
        price_source_candidates_checked=[],
        selected_price_source_name="",
        selected_price_source_available_count=0,
        selected_price_source_fresh_count=0,
        selected_price_source_stale_count=0,
        selected_price_source_missing_count=0,
        selected_price_source_freshness_basis="none",
        price_source_certification_status=MISSING,
        sector_source_candidates_checked=[],
        selected_sector_source_name="",
        selected_sector_available_count=0,
        selected_industry_available_count=0,
        selected_sector_missing_count=0,
        sector_source_certification_status=MISSING,
        ready_for_phase14c_computation=False,
        phase14c_blocking_reasons=["build_error"],
        recommended_next_step="investigate_diagnostic_build_error",
        errors=errors,
    )


def _build(
    *,
    portfolio_ticker_count: int,
    company_ticker_count: int,
    non_company_ticker_count: int,
    company_anchor_count: int,
    price_candidates: list[PriceCandidateStats],
    sector_candidates: list[SectorCandidateStats],
    extra_errors: list[str],
) -> PriceSectorSourceResolutionResult:
    errors: list[str] = list(extra_errors)

    # ── Price candidate classification ────────────────────────────────────────
    price_classified: list[tuple[str, str, int]] = []
    price_lookup: dict[str, tuple[PriceCandidateStats, str]] = {}
    for cand in price_candidates:
        status = _classify_price_candidate(cand, company_anchor_count)
        # Ranking weight uses fresh_count for price (we want fresh wins).
        price_classified.append((cand.name, status, cand.fresh_count))
        price_lookup[cand.name] = (cand, status)

    selected_price_name, selected_price_status = _select_best(
        price_classified, _PRICE_CANDIDATE_ORDER
    )
    if selected_price_name and selected_price_name in price_lookup:
        sp_stats, _ = price_lookup[selected_price_name]
        selected_price_available = sp_stats.available_count
        selected_price_fresh = sp_stats.fresh_count
        selected_price_stale = sp_stats.stale_count
        selected_price_missing = sp_stats.missing_count
        selected_price_basis = sp_stats.freshness_basis
    else:
        selected_price_available = 0
        selected_price_fresh = 0
        selected_price_stale = 0
        selected_price_missing = max(0, company_ticker_count)
        selected_price_basis = "none"
        selected_price_status = MISSING

    # ── Sector candidate classification ───────────────────────────────────────
    sector_classified: list[tuple[str, str, int]] = []
    sector_lookup: dict[str, tuple[SectorCandidateStats, str]] = {}
    for cand in sector_candidates:
        status = _classify_sector_candidate(cand, company_anchor_count)
        sector_classified.append((cand.name, status, cand.available_count))
        sector_lookup[cand.name] = (cand, status)

    selected_sector_name, selected_sector_status = _select_best(
        sector_classified, _SECTOR_CANDIDATE_ORDER
    )
    if selected_sector_name and selected_sector_name in sector_lookup:
        ss_stats, _ = sector_lookup[selected_sector_name]
        selected_sector_available = ss_stats.available_count
        selected_industry_available = ss_stats.industry_available_count
        selected_sector_missing = ss_stats.missing_count
    else:
        selected_sector_available = 0
        selected_industry_available = 0
        selected_sector_missing = max(0, company_ticker_count)
        selected_sector_status = MISSING

    # ── Phase 14C readiness gate ──────────────────────────────────────────────
    blocking_reasons: list[str] = []
    if selected_price_status != CERTIFIED:
        blocking_reasons.append(f"price_source_status={selected_price_status}")
    if selected_sector_status != CERTIFIED:
        blocking_reasons.append(f"sector_source_status={selected_sector_status}")
    if company_anchor_count <= 0:
        blocking_reasons.append("company_anchor_count_zero")

    ready = (
        selected_price_status == CERTIFIED
        and selected_sector_status == CERTIFIED
        and company_anchor_count > 0
    )

    recommended_next_step = _recommend_next_step(
        price_status=selected_price_status,
        sector_status=selected_sector_status,
        ready=ready,
    )

    return PriceSectorSourceResolutionResult(
        adapter_version=PRICE_SECTOR_SOURCE_RESOLUTION_V1_CONTRACT_VERSION,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        read_only=True,
        diagnostics_only=True,
        valuation_ratios_computed=False,
        earnings_yield_computed=False,
        price_context_unchanged=True,
        portfolio_ticker_count=portfolio_ticker_count,
        company_ticker_count=company_ticker_count,
        non_company_ticker_count=non_company_ticker_count,
        price_source_candidates_checked=[c.name for c in price_candidates],
        selected_price_source_name=selected_price_name,
        selected_price_source_available_count=selected_price_available,
        selected_price_source_fresh_count=selected_price_fresh,
        selected_price_source_stale_count=selected_price_stale,
        selected_price_source_missing_count=selected_price_missing,
        selected_price_source_freshness_basis=selected_price_basis,
        price_source_certification_status=selected_price_status,
        sector_source_candidates_checked=[c.name for c in sector_candidates],
        selected_sector_source_name=selected_sector_name,
        selected_sector_available_count=selected_sector_available,
        selected_industry_available_count=selected_industry_available,
        selected_sector_missing_count=selected_sector_missing,
        sector_source_certification_status=selected_sector_status,
        ready_for_phase14c_computation=ready,
        phase14c_blocking_reasons=blocking_reasons,
        recommended_next_step=recommended_next_step,
        errors=errors,
    )


def _recommend_next_step(*, price_status: str, sector_status: str, ready: bool) -> str:
    """Deterministic next-step decision tree (aggregate-safe label)."""
    if ready:
        return "phase14c_computation_unblocked"
    if price_status == MISSING and sector_status == MISSING:
        return "split_pr_provider_backed_ingestion_for_price_and_sector"
    if price_status in (MISSING, UNCERTIFIED) and sector_status == CERTIFIED:
        return "split_pr_provider_backed_price_ingestion"
    if sector_status in (MISSING, UNCERTIFIED) and price_status == CERTIFIED:
        return "split_pr_provider_backed_sector_ingestion"
    if price_status == PARTIAL or sector_status == PARTIAL:
        return "backfill_existing_source_to_full_company_anchor"
    return "split_pr_provider_backed_ingestion_for_price_and_sector"
