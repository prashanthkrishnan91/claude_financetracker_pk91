"""Phase 14C — FY EPS Earnings Yield v1 (pure, shadow-only diagnostics).

Computes FY-only earnings yield (EPS / market price) for company tickers as a
backend shadow/diagnostic signal. The module is pure and aggregate-only:

    - No IO, no DB, no provider, no LLM.
    - Inputs are pre-sanitized per-ticker records assembled by the router.
    - Output exposes ONLY aggregate counts and bucket distributions.
    - Per-ticker values, raw EPS, raw prices, raw yields, source URLs, and
      structured payloads are NEVER returned.

Hard architectural invariants (non-negotiable):
    - safe_for_decision is always False.
    - shadow_only is always True.
    - visible_snapshot_unchanged is always True.
    - read_only is always True.
    - diagnostics_only is always True.
    - price_context_unchanged is always True.
    - priceband_produced is always False.
    - decision_input_mutated is always False.
    - visible_decision_changed is always False.
    - ttm_computed is always False (FY only — TTM blocked by SEC parser
      _MAX_PERIODS_PER_TAG=2 < 4 periods needed for TTM).
    - fy_only is always True.
    - NEVER imports DecisionInputV3, PriceBand, decide(), run_v3.
    - NEVER computes P/E, P/B, EV/EBITDA, fair value, price target.
    - NEVER produces a "cheap" / "expensive" label.
    - Negative EPS is bucketed under negative_eps and MUST NOT be interpreted
      as cheap.
    - EPS == 0 is rejected as invalid (skipped_invalid_eps_count += 1).
    - Non-positive price is rejected (skipped_non_positive_price_count += 1).

Computation rule (deterministic):
    FY EPS = diluted EPS if available, else basic EPS (fallback).
    Earnings yield = EPS / price, where price > 0.
    Bucket selection uses (yield * 100) percent with the following bins for
    POSITIVE EPS only:
        zero_to_2_percent     0.0  <= y_pct <  2.0
        two_to_4_percent      2.0  <= y_pct <  4.0
        four_to_6_percent     4.0  <= y_pct <  6.0
        six_to_8_percent      6.0  <= y_pct <  8.0
        above_8_percent       8.0  <= y_pct
    Negative EPS records are bucketed under `negative_eps` regardless of
    yield magnitude.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

FY_EPS_EARNINGS_YIELD_V1_CONTRACT_VERSION: str = "phase14c_fy_eps_earnings_yield_v1"

# Coverage thresholds for promoting `ready_for_future_priceband_phase = True`.
# Tunable but deterministic. ratios are fractions of company_ticker_count.
PRICEBAND_READY_MIN_COMPUTED_RATIO: float = 0.70
PRICEBAND_READY_MIN_SECTOR_RATIO: float = 0.70
PRICEBAND_READY_MIN_SOURCE_LINKED_RATIO: float = 0.70

# EPS preference order — diluted preferred, basic fallback.
EPS_PREFERENCE_ORDER: tuple[str, str] = ("diluted", "basic")

# Stable distribution bucket labels (aggregate-safe — no per-ticker leakage).
BUCKET_NEGATIVE_EPS: str = "negative_eps"
BUCKET_0_TO_2: str = "zero_to_2_percent"
BUCKET_2_TO_4: str = "two_to_4_percent"
BUCKET_4_TO_6: str = "four_to_6_percent"
BUCKET_6_TO_8: str = "six_to_8_percent"
BUCKET_ABOVE_8: str = "above_8_percent"

_ALL_BUCKETS: tuple[str, ...] = (
    BUCKET_NEGATIVE_EPS,
    BUCKET_0_TO_2,
    BUCKET_2_TO_4,
    BUCKET_4_TO_6,
    BUCKET_6_TO_8,
    BUCKET_ABOVE_8,
)


@dataclass(frozen=True)
class EarningsYieldInputRecord:
    """One company ticker's sanitized inputs for the FY EPS earnings-yield
    computation. The router assembles this from stored research_artifact_facts
    + market_snapshots; the pure module never touches IO.

    All fields are explicit. Optional values are None when missing. The pure
    module categorises each record into computed / skipped buckets and never
    returns raw values.
    """
    # Internal correlation only — never exposed in the result.
    ticker: str

    # FY (fiscal_period == "FY") most-recent EPS facts from
    # research_artifact_facts (claim == "sec_companyfact_observed").
    fy_diluted_eps: float | None = None
    fy_basic_eps: float | None = None
    eps_source_linked: bool = False  # Source-linked EPS fact present.

    # Latest market_snapshots row for this ticker.
    price: float | None = None
    price_fresh: bool = False  # as_of within freshness window.

    # Sector / industry from market_snapshots (GICS-style, NOT portfolio category).
    sector_available: bool = False
    industry_available: bool = False


@dataclass(frozen=True)
class FyEpsEarningsYieldResult:
    """Aggregate-only Phase 14C FY EPS earnings-yield diagnostic.

    Forbidden in any field:
        - raw EPS values, raw prices, raw yields
        - per-ticker maps (no dict keyed by ticker)
        - source URLs / payloads / accession numbers
        - "cheap" / "expensive" / "fair" / PriceBand labels
    """
    adapter_version: str

    # Hard locks (always the same values).
    safe_for_decision: bool
    shadow_only: bool
    visible_snapshot_unchanged: bool
    read_only: bool
    diagnostics_only: bool
    price_context_unchanged: bool
    priceband_produced: bool
    decision_input_mutated: bool
    visible_decision_changed: bool
    valuation_ratios_computed: bool
    earnings_yield_computed: bool
    ttm_computed: bool
    fy_only: bool

    # Source labels (aggregate-safe — name the table/column, never values).
    sec_eps_source: str
    price_source: str
    sector_source: str
    eps_preference_order: list[str]

    # Population.
    portfolio_ticker_count: int
    company_ticker_count: int
    non_company_ticker_count: int

    # Eligibility funnel.
    eligible_input_count: int
    computed_earnings_yield_count: int
    skipped_missing_eps_count: int
    skipped_missing_price_count: int
    skipped_stale_price_count: int
    skipped_non_positive_price_count: int
    skipped_missing_sector_count: int
    skipped_invalid_eps_count: int

    # EPS sign distribution among COMPUTED records.
    negative_eps_count: int
    positive_eps_count: int
    zero_eps_count: int  # EPS == 0 — counted but never computed.

    # Source-quality counters.
    diluted_eps_used_count: int
    basic_eps_fallback_used_count: int
    source_linked_eps_used_count: int
    fresh_price_used_count: int
    sector_available_count: int
    industry_available_count: int

    # Aggregate distribution — bucket -> count. Never per-ticker.
    earnings_yield_distribution_buckets: dict[str, int]

    # Future-PriceBand readiness gate.
    ready_for_future_priceband_phase: bool
    future_priceband_blocking_reasons: list[str]
    recommended_next_step: str

    errors: list[str] = field(default_factory=list)


def build_fy_eps_earnings_yield(
    *,
    portfolio_ticker_count: int,
    company_ticker_count: int,
    non_company_ticker_count: int,
    records: list[EarningsYieldInputRecord],
    sec_eps_source: str,
    price_source: str,
    sector_source: str,
    extra_errors: list[str] | None = None,
) -> FyEpsEarningsYieldResult:
    """Pure, deterministic, read-only FY EPS earnings-yield computation.

    Args:
        portfolio_ticker_count:    Total user portfolio tickers.
        company_ticker_count:      Tickers classified as companies.
        non_company_ticker_count:  ETF/crypto/other suppressed tickers.
        records:                   One sanitized record per company ticker.
                                   Records for non-company tickers must NOT
                                   appear here.
        sec_eps_source:            Stable label for the SEC EPS table.
        price_source:              Stable label for the price table.
        sector_source:             Stable label for the sector table.
        extra_errors:              Non-fatal data-fetch errors from the router.

    Returns:
        FyEpsEarningsYieldResult — never raises.
    """
    try:
        return _build(
            portfolio_ticker_count=portfolio_ticker_count,
            company_ticker_count=company_ticker_count,
            non_company_ticker_count=non_company_ticker_count,
            records=list(records),
            sec_eps_source=sec_eps_source,
            price_source=price_source,
            sector_source=sector_source,
            extra_errors=list(extra_errors or []),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("fy_eps_earnings_yield_v1_build_error error=%s", exc)
        return _empty_result(
            sec_eps_source=sec_eps_source,
            price_source=price_source,
            sector_source=sector_source,
            errors=[f"build_error: {type(exc).__name__}: {exc}"],
        )


def _empty_result(
    *,
    sec_eps_source: str,
    price_source: str,
    sector_source: str,
    errors: list[str],
) -> FyEpsEarningsYieldResult:
    return FyEpsEarningsYieldResult(
        adapter_version=FY_EPS_EARNINGS_YIELD_V1_CONTRACT_VERSION,
        safe_for_decision=False,
        shadow_only=True,
        visible_snapshot_unchanged=True,
        read_only=True,
        diagnostics_only=True,
        price_context_unchanged=True,
        priceband_produced=False,
        decision_input_mutated=False,
        visible_decision_changed=False,
        valuation_ratios_computed=True,
        earnings_yield_computed=True,
        ttm_computed=False,
        fy_only=True,
        sec_eps_source=sec_eps_source,
        price_source=price_source,
        sector_source=sector_source,
        eps_preference_order=list(EPS_PREFERENCE_ORDER),
        portfolio_ticker_count=0,
        company_ticker_count=0,
        non_company_ticker_count=0,
        eligible_input_count=0,
        computed_earnings_yield_count=0,
        skipped_missing_eps_count=0,
        skipped_missing_price_count=0,
        skipped_stale_price_count=0,
        skipped_non_positive_price_count=0,
        skipped_missing_sector_count=0,
        skipped_invalid_eps_count=0,
        negative_eps_count=0,
        positive_eps_count=0,
        zero_eps_count=0,
        diluted_eps_used_count=0,
        basic_eps_fallback_used_count=0,
        source_linked_eps_used_count=0,
        fresh_price_used_count=0,
        sector_available_count=0,
        industry_available_count=0,
        earnings_yield_distribution_buckets={b: 0 for b in _ALL_BUCKETS},
        ready_for_future_priceband_phase=False,
        future_priceband_blocking_reasons=["build_error"],
        recommended_next_step="investigate_diagnostic_build_error",
        errors=errors,
    )


def _bucket_for_positive_yield(y_pct: float) -> str:
    """Return distribution bucket for a non-negative percent earnings yield."""
    if y_pct < 2.0:
        return BUCKET_0_TO_2
    if y_pct < 4.0:
        return BUCKET_2_TO_4
    if y_pct < 6.0:
        return BUCKET_4_TO_6
    if y_pct < 8.0:
        return BUCKET_6_TO_8
    return BUCKET_ABOVE_8


def _build(
    *,
    portfolio_ticker_count: int,
    company_ticker_count: int,
    non_company_ticker_count: int,
    records: list[EarningsYieldInputRecord],
    sec_eps_source: str,
    price_source: str,
    sector_source: str,
    extra_errors: list[str],
) -> FyEpsEarningsYieldResult:
    errors: list[str] = list(extra_errors)

    eligible_input_count = 0
    computed_earnings_yield_count = 0
    skipped_missing_eps_count = 0
    skipped_missing_price_count = 0
    skipped_stale_price_count = 0
    skipped_non_positive_price_count = 0
    skipped_missing_sector_count = 0
    skipped_invalid_eps_count = 0

    negative_eps_count = 0
    positive_eps_count = 0
    zero_eps_count = 0

    diluted_used = 0
    basic_used = 0
    source_linked_used = 0
    fresh_price_used = 0
    sector_available_count = 0
    industry_available_count = 0

    buckets: dict[str, int] = {b: 0 for b in _ALL_BUCKETS}

    for rec in records:
        eligible_input_count += 1

        if rec.sector_available:
            sector_available_count += 1
        if rec.industry_available:
            industry_available_count += 1

        # ── EPS selection: diluted preferred, basic fallback ──────────────────
        eps_value: float | None = None
        used_kind = ""
        if rec.fy_diluted_eps is not None:
            eps_value = float(rec.fy_diluted_eps)
            used_kind = "diluted"
        elif rec.fy_basic_eps is not None:
            eps_value = float(rec.fy_basic_eps)
            used_kind = "basic"

        # ── Skip rules — order matters; each record falls into ONE skip bin ──
        if eps_value is None:
            skipped_missing_eps_count += 1
            continue
        if rec.price is None:
            skipped_missing_price_count += 1
            continue
        if not rec.price_fresh:
            skipped_stale_price_count += 1
            continue
        price = float(rec.price)
        if price <= 0.0:
            skipped_non_positive_price_count += 1
            continue
        if not rec.sector_available:
            skipped_missing_sector_count += 1
            continue
        if eps_value == 0.0:
            # Zero EPS is undefined for valuation purposes — skip as invalid.
            skipped_invalid_eps_count += 1
            zero_eps_count += 1
            continue

        # ── Compute earnings yield ────────────────────────────────────────────
        # Negative EPS computes a negative yield, but we never label it cheap.
        yield_value = eps_value / price
        y_pct = yield_value * 100.0

        computed_earnings_yield_count += 1
        if used_kind == "diluted":
            diluted_used += 1
        else:
            basic_used += 1
        if rec.eps_source_linked:
            source_linked_used += 1
        if rec.price_fresh:
            fresh_price_used += 1

        if eps_value < 0.0:
            negative_eps_count += 1
            buckets[BUCKET_NEGATIVE_EPS] += 1
        else:
            positive_eps_count += 1
            buckets[_bucket_for_positive_yield(y_pct)] += 1

    # ── Future-PriceBand readiness gate ──────────────────────────────────────
    blocking: list[str] = []
    if company_ticker_count <= 0:
        blocking.append("company_ticker_count_zero")
    else:
        if (
            computed_earnings_yield_count
            < PRICEBAND_READY_MIN_COMPUTED_RATIO * company_ticker_count
        ):
            blocking.append("computed_yield_coverage_below_threshold")
        if (
            sector_available_count
            < PRICEBAND_READY_MIN_SECTOR_RATIO * company_ticker_count
        ):
            blocking.append("sector_coverage_below_threshold")
        if (
            source_linked_used
            < PRICEBAND_READY_MIN_SOURCE_LINKED_RATIO * computed_earnings_yield_count
        ):
            blocking.append("source_linked_eps_coverage_below_threshold")

    ready = not blocking and computed_earnings_yield_count > 0
    recommended = _recommend_next_step(ready=ready, blocking=blocking)

    return FyEpsEarningsYieldResult(
        adapter_version=FY_EPS_EARNINGS_YIELD_V1_CONTRACT_VERSION,
        safe_for_decision=False,
        shadow_only=True,
        visible_snapshot_unchanged=True,
        read_only=True,
        diagnostics_only=True,
        price_context_unchanged=True,
        priceband_produced=False,
        decision_input_mutated=False,
        visible_decision_changed=False,
        valuation_ratios_computed=True,
        earnings_yield_computed=True,
        ttm_computed=False,
        fy_only=True,
        sec_eps_source=sec_eps_source,
        price_source=price_source,
        sector_source=sector_source,
        eps_preference_order=list(EPS_PREFERENCE_ORDER),
        portfolio_ticker_count=portfolio_ticker_count,
        company_ticker_count=company_ticker_count,
        non_company_ticker_count=non_company_ticker_count,
        eligible_input_count=eligible_input_count,
        computed_earnings_yield_count=computed_earnings_yield_count,
        skipped_missing_eps_count=skipped_missing_eps_count,
        skipped_missing_price_count=skipped_missing_price_count,
        skipped_stale_price_count=skipped_stale_price_count,
        skipped_non_positive_price_count=skipped_non_positive_price_count,
        skipped_missing_sector_count=skipped_missing_sector_count,
        skipped_invalid_eps_count=skipped_invalid_eps_count,
        negative_eps_count=negative_eps_count,
        positive_eps_count=positive_eps_count,
        zero_eps_count=zero_eps_count,
        diluted_eps_used_count=diluted_used,
        basic_eps_fallback_used_count=basic_used,
        source_linked_eps_used_count=source_linked_used,
        fresh_price_used_count=fresh_price_used,
        sector_available_count=sector_available_count,
        industry_available_count=industry_available_count,
        earnings_yield_distribution_buckets=buckets,
        ready_for_future_priceband_phase=ready,
        future_priceband_blocking_reasons=blocking,
        recommended_next_step=recommended,
        errors=errors,
    )


def _recommend_next_step(*, ready: bool, blocking: list[str]) -> str:
    """Deterministic next-step decision tree (aggregate-safe label)."""
    if ready:
        return "design_priceband_policy_phase_pending_governance_review"
    if "company_ticker_count_zero" in blocking:
        return "wait_for_company_ticker_population"
    if "computed_yield_coverage_below_threshold" in blocking:
        return "improve_eps_or_price_coverage_before_priceband_design"
    if "sector_coverage_below_threshold" in blocking:
        return "improve_sector_coverage_before_priceband_design"
    if "source_linked_eps_coverage_below_threshold" in blocking:
        return "improve_source_linked_eps_coverage_before_priceband_design"
    return "hold_priceband_design_pending_coverage_review"
