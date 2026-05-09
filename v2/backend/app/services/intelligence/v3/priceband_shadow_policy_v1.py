"""Phase 14D — PriceBand Shadow Policy v1 (pure, shadow-only diagnostics).

Backend-only deterministic valuation classifier that converts Phase 14C
certified inputs (source-linked FY EPS + fresh price + sector/industry) into
a humble, evidence-bounded valuation signal bucket. The output is shadow
diagnostics ONLY:

    - safe_for_decision is always False.
    - shadow_only is always True.
    - visible_decision_changed is always False.
    - decision_input_mutated is always False.
    - priceband_produced is True (a *shadow* PriceBand classification is
      computed for diagnostic visibility), but it MUST NEVER be wired into
      DecisionInputV3.price_context, intel_v3_snapshots, or any visible path.

Hard architectural invariants (non-negotiable):
    - No IO, no DB, no provider, no LLM.
    - NEVER imports DecisionInputV3, PriceBand (the decision_contracts enum),
      decide(), or run_v3.
    - NEVER computes a fair value or intrinsic value.
    - NEVER emits a target price, buy_below, or sell_above threshold.
    - NEVER returns raw EPS, raw price, or raw earnings yield numbers.
    - Classifies negative EPS as `negative_eps` — NEVER "cheap".
    - Classifies missing EPS, missing/stale/non-positive price as `unavailable`.
    - Missing sector → broad fallback (explicitly labeled), or `unavailable`
      if any other gating condition is also unmet.
    - FY-only EPS (no TTM, no quarterly annualization).
    - Static governance table (`policy_static_v1`) — no sector-specific
      benchmarks (deferred until stored benchmark data is available).

Policy thresholds (`policy_static_v1`, broad-market, positive EPS):
    y_pct < 2.0           → `expensive`
    2.0 <= y_pct < 4.0    → `elevated`
    4.0 <= y_pct < 6.0    → `reasonable`
    6.0 <= y_pct < 9.0    → `attractive`
    y_pct >= 9.0          → `unusually_cheap`
    EPS < 0               → `negative_eps`
    EPS / price / sector unavailable → `unavailable`

Confidence policy (deterministic):
    high   — diluted EPS + source-linked + fresh price + sector available.
    medium — basic-EPS fallback OR sector-missing-broad-fallback path
             (still source-linked EPS + fresh positive price).
    low    — non-source-linked EPS, OR `unavailable` valuation_signal.

Plain-English summary policy:
    Conservative, non-prescriptive sentence per signal. NEVER says
    "buy below X", "sell above Y", "target price", "fair value", or
    suggests an action. NEVER mentions a numeric price or yield.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION: str = "phase14d_priceband_shadow_v1"
PRICEBAND_POLICY_TABLE_ID: str = "policy_static_v1"
PRICEBAND_POLICY_BASIS: str = "fy_eps_earnings_yield"


# ── Valuation signal labels ─────────────────────────────────────────────────
VALUATION_SIGNAL_UNAVAILABLE: str = "unavailable"
VALUATION_SIGNAL_NEGATIVE_EPS: str = "negative_eps"
VALUATION_SIGNAL_EXPENSIVE: str = "expensive"
VALUATION_SIGNAL_ELEVATED: str = "elevated"
VALUATION_SIGNAL_REASONABLE: str = "reasonable"
VALUATION_SIGNAL_ATTRACTIVE: str = "attractive"
VALUATION_SIGNAL_UNUSUALLY_CHEAP: str = "unusually_cheap"

_ALL_VALUATION_SIGNALS: tuple[str, ...] = (
    VALUATION_SIGNAL_UNAVAILABLE,
    VALUATION_SIGNAL_NEGATIVE_EPS,
    VALUATION_SIGNAL_EXPENSIVE,
    VALUATION_SIGNAL_ELEVATED,
    VALUATION_SIGNAL_REASONABLE,
    VALUATION_SIGNAL_ATTRACTIVE,
    VALUATION_SIGNAL_UNUSUALLY_CHEAP,
)


# ── Confidence labels ───────────────────────────────────────────────────────
CONFIDENCE_LOW: str = "low"
CONFIDENCE_MEDIUM: str = "medium"
CONFIDENCE_HIGH: str = "high"

_ALL_CONFIDENCE: tuple[str, ...] = (
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_HIGH,
)


# ── Earnings-yield bucket labels (mirror Phase 14C buckets) ─────────────────
BUCKET_NEGATIVE_EPS: str = "negative_eps"
BUCKET_0_TO_2: str = "zero_to_2_percent"
BUCKET_2_TO_4: str = "two_to_4_percent"
BUCKET_4_TO_6: str = "four_to_6_percent"
BUCKET_6_TO_9: str = "six_to_9_percent"
BUCKET_ABOVE_9: str = "above_9_percent"
BUCKET_NONE: str = ""  # Used for unavailable rows — no bucket assigned.


# ── Unavailable reason codes (stable enum) ──────────────────────────────────
REASON_MISSING_EPS: str = "missing_eps"
REASON_ZERO_EPS: str = "zero_eps_invalid_for_valuation"
REASON_MISSING_PRICE: str = "missing_price"
REASON_STALE_PRICE: str = "stale_price"
REASON_NON_POSITIVE_PRICE: str = "non_positive_price"

_ALL_UNAVAILABLE_REASONS: tuple[str, ...] = (
    REASON_MISSING_EPS,
    REASON_ZERO_EPS,
    REASON_MISSING_PRICE,
    REASON_STALE_PRICE,
    REASON_NON_POSITIVE_PRICE,
)


# ── Input quality labels ────────────────────────────────────────────────────
INPUT_QUALITY_FULL: str = "source_linked_fy_eps_and_fresh_price_and_sector"
INPUT_QUALITY_BROAD_FALLBACK: str = "source_linked_fy_eps_and_fresh_price_broad_fallback"
INPUT_QUALITY_DEGRADED: str = "non_source_linked_fy_eps"
INPUT_QUALITY_UNAVAILABLE: str = "inputs_unavailable"


# ── Plain-English summary table ─────────────────────────────────────────────
# Conservative, non-prescriptive. Never numeric, never an action.
_PLAIN_ENGLISH_SUMMARIES: dict[str, str] = {
    VALUATION_SIGNAL_EXPENSIVE: (
        "Valuation looks demanding based on latest annual earnings "
        "versus current price."
    ),
    VALUATION_SIGNAL_ELEVATED: (
        "Valuation looks somewhat demanding based on latest annual "
        "earnings versus current price."
    ),
    VALUATION_SIGNAL_REASONABLE: (
        "Valuation looks roughly in line with broad market norms based "
        "on latest annual earnings."
    ),
    VALUATION_SIGNAL_ATTRACTIVE: (
        "Valuation looks attractive based on latest annual earnings "
        "versus current price."
    ),
    VALUATION_SIGNAL_UNUSUALLY_CHEAP: (
        "Valuation looks unusually cheap based on latest annual "
        "earnings — consider why before drawing conclusions."
    ),
    VALUATION_SIGNAL_NEGATIVE_EPS: (
        "Latest annual earnings are negative, so a price-based "
        "valuation is not meaningful here."
    ),
    VALUATION_SIGNAL_UNAVAILABLE: (
        "Valuation context is not available because one or more "
        "inputs are missing or stale."
    ),
}


# ── Standard limitations attached to every classification ───────────────────
_STANDARD_LIMITATIONS: tuple[str, ...] = (
    "FY-only EPS (no TTM, no quarterly annualization)",
    "not a fair-value estimate",
    "not a price target",
    "static broad-market policy table (no sector-specific bands)",
    "shadow-only diagnostic — does not influence visible Buy/Hold/Trim/Sell",
)


@dataclass(frozen=True)
class PriceBandShadowInput:
    """One company ticker's sanitized inputs for the PriceBand shadow policy.

    Inputs mirror the Phase 14C `EarningsYieldInputRecord` shape so the
    router can reuse the same data assembly path. The pure module never
    touches IO.
    """
    ticker: str

    # FY EPS facts (research_artifact_facts, sec_companyfact_observed, FY-only).
    fy_diluted_eps: float | None = None
    fy_basic_eps: float | None = None
    eps_source_linked: bool = False

    # Latest market_snapshots row.
    price: float | None = None
    price_fresh: bool = False

    # Sector / industry presence (NOT raw values — only availability).
    sector_available: bool = False
    industry_available: bool = False

    # Optional GICS-style sector / industry labels for diagnostic context.
    # Surfaced ONLY in the cert-gated endpoint per-ticker output. Never raw.
    sector_label: str | None = None
    industry_label: str | None = None


@dataclass(frozen=True)
class PriceBandShadowDiagnostic:
    """One ticker's PriceBand shadow classification (cert-gated endpoint only).

    Forbidden in any field:
        - raw EPS, raw price, raw earnings yield numeric value
        - target_price, fair_value, intrinsic_value
        - buy_below, sell_above
        - DecisionInputV3 / PriceBand enum string values (CHEAP/FAIR/...)
    """
    ticker: str
    priceband_policy_version: str
    safe_for_decision: bool
    shadow_only: bool
    visible_decision_changed: bool
    priceband_produced: bool
    valuation_signal: str
    valuation_confidence: str
    valuation_basis: str
    valuation_policy_table: str
    earnings_yield_bucket: str
    sector: str | None
    industry: str | None
    sector_used_for_classification: bool
    broad_fallback_used: bool
    input_quality: str
    plain_english_summary: str
    limitations: list[str]
    unavailable_reason: str | None  # None when classified


@dataclass(frozen=True)
class PriceBandShadowResult:
    """Aggregate Phase 14D PriceBand shadow diagnostic.

    The per-ticker `priceband_diagnostics` list is cert-gated and never
    surfaced to the frontend. Aggregate counts are aggregate-only and safe.
    """
    adapter_version: str
    policy_table_id: str
    policy_basis: str

    # Hard locks.
    safe_for_decision: bool
    shadow_only: bool
    visible_snapshot_unchanged: bool
    read_only: bool
    diagnostics_only: bool
    decision_input_mutated: bool
    visible_decision_changed: bool
    no_target_price_emitted: bool
    no_fair_value_emitted: bool
    fy_only: bool
    ttm_computed: bool

    # Per-ticker diagnostics (cert-gated, not aggregate).
    priceband_diagnostics: list[PriceBandShadowDiagnostic]

    # Aggregate population.
    evaluated_company_ticker_count: int
    priceband_computed_count: int
    priceband_unavailable_count: int

    # Aggregate distribution: stable enum keys → count.
    by_valuation_signal: dict[str, int]
    by_confidence: dict[str, int]
    unavailable_reason_counts: dict[str, int]
    earnings_yield_bucket_counts: dict[str, int]

    # Source-quality counters.
    negative_eps_count: int
    missing_eps_count: int
    fresh_price_count: int
    source_linked_eps_count: int
    sector_available_count: int
    industry_available_count: int
    broad_fallback_count: int

    # Recommended next governance step (string label only).
    recommended_next_step: str

    errors: list[str] = field(default_factory=list)


# ── Public entrypoint ───────────────────────────────────────────────────────
def build_priceband_shadow(
    *,
    records: list[PriceBandShadowInput],
    extra_errors: list[str] | None = None,
) -> PriceBandShadowResult:
    """Pure, deterministic, read-only PriceBand shadow classification.

    Args:
        records:       One sanitized input per company ticker.
        extra_errors:  Non-fatal data-fetch errors from the router.

    Returns:
        PriceBandShadowResult — never raises.
    """
    try:
        return _build(records=list(records), extra_errors=list(extra_errors or []))
    except Exception as exc:  # noqa: BLE001
        logger.error("priceband_shadow_v1_build_error error=%s", exc)
        return _empty_result(errors=[f"build_error: {type(exc).__name__}: {exc}"])


def _empty_result(*, errors: list[str]) -> PriceBandShadowResult:
    return PriceBandShadowResult(
        adapter_version=PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
        policy_table_id=PRICEBAND_POLICY_TABLE_ID,
        policy_basis=PRICEBAND_POLICY_BASIS,
        safe_for_decision=False,
        shadow_only=True,
        visible_snapshot_unchanged=True,
        read_only=True,
        diagnostics_only=True,
        decision_input_mutated=False,
        visible_decision_changed=False,
        no_target_price_emitted=True,
        no_fair_value_emitted=True,
        fy_only=True,
        ttm_computed=False,
        priceband_diagnostics=[],
        evaluated_company_ticker_count=0,
        priceband_computed_count=0,
        priceband_unavailable_count=0,
        by_valuation_signal={s: 0 for s in _ALL_VALUATION_SIGNALS},
        by_confidence={c: 0 for c in _ALL_CONFIDENCE},
        unavailable_reason_counts={r: 0 for r in _ALL_UNAVAILABLE_REASONS},
        earnings_yield_bucket_counts={
            BUCKET_NEGATIVE_EPS: 0,
            BUCKET_0_TO_2: 0,
            BUCKET_2_TO_4: 0,
            BUCKET_4_TO_6: 0,
            BUCKET_6_TO_9: 0,
            BUCKET_ABOVE_9: 0,
        },
        negative_eps_count=0,
        missing_eps_count=0,
        fresh_price_count=0,
        source_linked_eps_count=0,
        sector_available_count=0,
        industry_available_count=0,
        broad_fallback_count=0,
        recommended_next_step="investigate_diagnostic_build_error",
        errors=errors,
    )


def _bucket_for_positive_yield_pct(y_pct: float) -> str:
    if y_pct < 2.0:
        return BUCKET_0_TO_2
    if y_pct < 4.0:
        return BUCKET_2_TO_4
    if y_pct < 6.0:
        return BUCKET_4_TO_6
    if y_pct < 9.0:
        return BUCKET_6_TO_9
    return BUCKET_ABOVE_9


def _signal_for_positive_yield_pct(y_pct: float) -> str:
    """Deterministic broad-market policy_static_v1 mapping."""
    if y_pct < 2.0:
        return VALUATION_SIGNAL_EXPENSIVE
    if y_pct < 4.0:
        return VALUATION_SIGNAL_ELEVATED
    if y_pct < 6.0:
        return VALUATION_SIGNAL_REASONABLE
    if y_pct < 9.0:
        return VALUATION_SIGNAL_ATTRACTIVE
    return VALUATION_SIGNAL_UNUSUALLY_CHEAP


def _classify_one(rec: PriceBandShadowInput) -> PriceBandShadowDiagnostic:
    """Classify one ticker — exactly one valuation_signal."""
    # ── EPS selection: diluted preferred, basic fallback ────────────────────
    eps_value: float | None = None
    used_diluted = False
    if rec.fy_diluted_eps is not None:
        eps_value = float(rec.fy_diluted_eps)
        used_diluted = True
    elif rec.fy_basic_eps is not None:
        eps_value = float(rec.fy_basic_eps)
        used_diluted = False

    # ── Unavailable gates (in priority order) ──────────────────────────────
    if eps_value is None:
        return _unavailable(rec, REASON_MISSING_EPS)
    if rec.price is None:
        return _unavailable(rec, REASON_MISSING_PRICE)
    if not rec.price_fresh:
        return _unavailable(rec, REASON_STALE_PRICE)
    price = float(rec.price)
    if price <= 0.0:
        return _unavailable(rec, REASON_NON_POSITIVE_PRICE)
    if eps_value == 0.0:
        return _unavailable(rec, REASON_ZERO_EPS)

    # ── Negative EPS — never cheap ─────────────────────────────────────────
    if eps_value < 0.0:
        sector_used_for_classification = bool(rec.sector_available)
        broad_fallback = not rec.sector_available
        confidence = _confidence(
            classified=True,
            source_linked=rec.eps_source_linked,
            used_diluted=used_diluted,
            sector_available=rec.sector_available,
        )
        input_quality = _input_quality(
            source_linked=rec.eps_source_linked,
            sector_available=rec.sector_available,
        )
        return PriceBandShadowDiagnostic(
            ticker=rec.ticker,
            priceband_policy_version=PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
            safe_for_decision=False,
            shadow_only=True,
            visible_decision_changed=False,
            priceband_produced=True,
            valuation_signal=VALUATION_SIGNAL_NEGATIVE_EPS,
            valuation_confidence=confidence,
            valuation_basis=PRICEBAND_POLICY_BASIS,
            valuation_policy_table=PRICEBAND_POLICY_TABLE_ID,
            earnings_yield_bucket=BUCKET_NEGATIVE_EPS,
            sector=(rec.sector_label if rec.sector_available else None),
            industry=(rec.industry_label if rec.industry_available else None),
            sector_used_for_classification=sector_used_for_classification,
            broad_fallback_used=broad_fallback,
            input_quality=input_quality,
            plain_english_summary=_PLAIN_ENGLISH_SUMMARIES[VALUATION_SIGNAL_NEGATIVE_EPS],
            limitations=list(_STANDARD_LIMITATIONS),
            unavailable_reason=None,
        )

    # ── Positive EPS — broad-market policy_static_v1 classification ────────
    yield_value = eps_value / price
    y_pct = yield_value * 100.0
    signal = _signal_for_positive_yield_pct(y_pct)
    bucket = _bucket_for_positive_yield_pct(y_pct)

    sector_used_for_classification = bool(rec.sector_available)
    broad_fallback = not rec.sector_available
    confidence = _confidence(
        classified=True,
        source_linked=rec.eps_source_linked,
        used_diluted=used_diluted,
        sector_available=rec.sector_available,
    )
    input_quality = _input_quality(
        source_linked=rec.eps_source_linked,
        sector_available=rec.sector_available,
    )

    return PriceBandShadowDiagnostic(
        ticker=rec.ticker,
        priceband_policy_version=PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
        safe_for_decision=False,
        shadow_only=True,
        visible_decision_changed=False,
        priceband_produced=True,
        valuation_signal=signal,
        valuation_confidence=confidence,
        valuation_basis=PRICEBAND_POLICY_BASIS,
        valuation_policy_table=PRICEBAND_POLICY_TABLE_ID,
        earnings_yield_bucket=bucket,
        sector=(rec.sector_label if rec.sector_available else None),
        industry=(rec.industry_label if rec.industry_available else None),
        sector_used_for_classification=sector_used_for_classification,
        broad_fallback_used=broad_fallback,
        input_quality=input_quality,
        plain_english_summary=_PLAIN_ENGLISH_SUMMARIES[signal],
        limitations=list(_STANDARD_LIMITATIONS),
        unavailable_reason=None,
    )


def _unavailable(
    rec: PriceBandShadowInput, reason: str
) -> PriceBandShadowDiagnostic:
    return PriceBandShadowDiagnostic(
        ticker=rec.ticker,
        priceband_policy_version=PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
        safe_for_decision=False,
        shadow_only=True,
        visible_decision_changed=False,
        priceband_produced=False,
        valuation_signal=VALUATION_SIGNAL_UNAVAILABLE,
        valuation_confidence=CONFIDENCE_LOW,
        valuation_basis=PRICEBAND_POLICY_BASIS,
        valuation_policy_table=PRICEBAND_POLICY_TABLE_ID,
        earnings_yield_bucket=BUCKET_NONE,
        sector=(rec.sector_label if rec.sector_available else None),
        industry=(rec.industry_label if rec.industry_available else None),
        sector_used_for_classification=False,
        broad_fallback_used=False,
        input_quality=INPUT_QUALITY_UNAVAILABLE,
        plain_english_summary=_PLAIN_ENGLISH_SUMMARIES[VALUATION_SIGNAL_UNAVAILABLE],
        limitations=list(_STANDARD_LIMITATIONS),
        unavailable_reason=reason,
    )


def _confidence(
    *, classified: bool, source_linked: bool, used_diluted: bool, sector_available: bool
) -> str:
    if not classified:
        return CONFIDENCE_LOW
    if not source_linked:
        return CONFIDENCE_LOW
    if used_diluted and sector_available:
        return CONFIDENCE_HIGH
    return CONFIDENCE_MEDIUM


def _input_quality(*, source_linked: bool, sector_available: bool) -> str:
    if not source_linked:
        return INPUT_QUALITY_DEGRADED
    if sector_available:
        return INPUT_QUALITY_FULL
    return INPUT_QUALITY_BROAD_FALLBACK


def _build(
    *, records: list[PriceBandShadowInput], extra_errors: list[str]
) -> PriceBandShadowResult:
    errors: list[str] = list(extra_errors)
    diagnostics: list[PriceBandShadowDiagnostic] = []

    by_valuation_signal: dict[str, int] = {s: 0 for s in _ALL_VALUATION_SIGNALS}
    by_confidence: dict[str, int] = {c: 0 for c in _ALL_CONFIDENCE}
    unavailable_reason_counts: dict[str, int] = {
        r: 0 for r in _ALL_UNAVAILABLE_REASONS
    }
    earnings_yield_bucket_counts: dict[str, int] = {
        BUCKET_NEGATIVE_EPS: 0,
        BUCKET_0_TO_2: 0,
        BUCKET_2_TO_4: 0,
        BUCKET_4_TO_6: 0,
        BUCKET_6_TO_9: 0,
        BUCKET_ABOVE_9: 0,
    }

    priceband_computed_count = 0
    priceband_unavailable_count = 0
    negative_eps_count = 0
    missing_eps_count = 0
    fresh_price_count = 0
    source_linked_eps_count = 0
    sector_available_count = 0
    industry_available_count = 0
    broad_fallback_count = 0

    for rec in records:
        if rec.price_fresh:
            fresh_price_count += 1
        if rec.eps_source_linked:
            source_linked_eps_count += 1
        if rec.sector_available:
            sector_available_count += 1
        if rec.industry_available:
            industry_available_count += 1

        diag = _classify_one(rec)
        diagnostics.append(diag)

        by_valuation_signal[diag.valuation_signal] += 1
        by_confidence[diag.valuation_confidence] += 1

        if diag.valuation_signal == VALUATION_SIGNAL_UNAVAILABLE:
            priceband_unavailable_count += 1
            if diag.unavailable_reason in unavailable_reason_counts:
                unavailable_reason_counts[diag.unavailable_reason] += 1
            if diag.unavailable_reason == REASON_MISSING_EPS:
                missing_eps_count += 1
        else:
            priceband_computed_count += 1
            earnings_yield_bucket_counts[diag.earnings_yield_bucket] += 1
            if diag.broad_fallback_used:
                broad_fallback_count += 1
            if diag.valuation_signal == VALUATION_SIGNAL_NEGATIVE_EPS:
                negative_eps_count += 1

    recommended = _recommend_next_step(
        evaluated=len(records),
        computed=priceband_computed_count,
        unavailable=priceband_unavailable_count,
    )

    return PriceBandShadowResult(
        adapter_version=PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
        policy_table_id=PRICEBAND_POLICY_TABLE_ID,
        policy_basis=PRICEBAND_POLICY_BASIS,
        safe_for_decision=False,
        shadow_only=True,
        visible_snapshot_unchanged=True,
        read_only=True,
        diagnostics_only=True,
        decision_input_mutated=False,
        visible_decision_changed=False,
        no_target_price_emitted=True,
        no_fair_value_emitted=True,
        fy_only=True,
        ttm_computed=False,
        priceband_diagnostics=diagnostics,
        evaluated_company_ticker_count=len(records),
        priceband_computed_count=priceband_computed_count,
        priceband_unavailable_count=priceband_unavailable_count,
        by_valuation_signal=by_valuation_signal,
        by_confidence=by_confidence,
        unavailable_reason_counts=unavailable_reason_counts,
        earnings_yield_bucket_counts=earnings_yield_bucket_counts,
        negative_eps_count=negative_eps_count,
        missing_eps_count=missing_eps_count,
        fresh_price_count=fresh_price_count,
        source_linked_eps_count=source_linked_eps_count,
        sector_available_count=sector_available_count,
        industry_available_count=industry_available_count,
        broad_fallback_count=broad_fallback_count,
        recommended_next_step=recommended,
        errors=errors,
    )


def _recommend_next_step(
    *, evaluated: int, computed: int, unavailable: int
) -> str:
    if evaluated == 0:
        return "wait_for_company_ticker_population"
    if computed == 0:
        return "investigate_full_unavailability_before_governance_review"
    if unavailable > 0:
        return "review_unavailable_reasons_then_governance_decision_on_visible_wiring"
    return "policy_static_v1_full_coverage_pending_governance_review_for_visible_wiring"
