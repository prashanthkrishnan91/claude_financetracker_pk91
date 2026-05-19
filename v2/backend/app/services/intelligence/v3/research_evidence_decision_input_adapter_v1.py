"""Stage 5K — Research Evidence Decision Input Adapter v1. Shadow-only.

Consumes the Stage 5J Research Evidence Coverage Read Model output and
produces a deterministic, plain-English-safe shadow readiness model for
Intel v3 decision input axes.

This adapter answers:
    "Which Intel v3 decision axes have usable evidence backing them right now,
    which are missing, which are suppressed/stale, and which are not applicable?"

Architecture contracts (non-negotiable):
  - Consumes ResearchEvidenceCoverageSummary (Stage 5J). Never re-fetches
    artifacts or duplicates artifact-selection logic.
  - Pure deterministic function. No DB reads, no DB writes, no LLM calls,
    no provider calls, no external I/O of any kind.
  - NEVER calls decide(), NEVER imports decision_policy_v1 or any Intel v3
    decision kernel module.
  - NEVER writes to intel_v3_snapshots, recommendations, or research_*.
  - NEVER fabricates evidence: SUPPRESSED/STALE/MISSING/NOT_EVALUABLE lanes
    cannot contribute readiness.
  - safe_for_decision is permanently False. This is a shadow/diagnostic adapter;
    the governance gate for safe_for_decision=True is deferred and must be an
    explicit future change in this file.
  - ETF/crypto/non-equity tickers must not be penalized for missing SEC
    CompanyFacts where that lane is not applicable.
  - Output contains only aggregate metadata safe for a diagnostics surface.
    NEVER returns raw artifact payloads, source URLs, fact contents, API keys,
    secrets, or user PII.
  - No visible Buy/Hold/Trim/Sell behavior change. Shadow-only.

Axis mapping (conservative):
  company_fundamentals:
    sec_company_facts (equity tickers only, READY or LIMITED)
    fundamentals      (all tickers, READY or LIMITED)
    → readiness = READY if any contributing lane is READY
                  LIMITED if best contributing lane is LIMITED
                  MISSING / SUPPRESSED / STALE if no lane usable

  technical_signals:
    technicals        (all tickers, READY or LIMITED)

  sentiment:
    news_sentiment    (all tickers, READY or LIMITED)

  macro_context (portfolio-scope, one per portfolio):
    macro_context     (READY or LIMITED)

SEC lane applicability:
  A ticker is SEC-lane-applicable only if it is equity (not ETF/fund/crypto).
  Applicability is determined from holding_context_by_ticker when supplied;
  falls back to a conservative symbol list. When not applicable, the SEC lane
  is reported as not_applicable — never as a missing-evidence failure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .research_evidence_coverage_read_model_v1 import (
    LANE_FUNDAMENTALS,
    LANE_MACRO_CONTEXT,
    LANE_NEWS_SENTIMENT,
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    STATUS_LIMITED,
    STATUS_MISSING,
    STATUS_NOT_EVALUABLE,
    STATUS_READY,
    STATUS_STALE_OR_UNKNOWN,
    STATUS_SUPPRESSED,
    ResearchEvidenceCoverageSummary,
)

logger = logging.getLogger(__name__)

ADAPTER_VERSION = "research_evidence_decision_input_adapter.v1"

# Readiness signal constants (superset of coverage statuses + not_applicable).
READINESS_READY = "READY"
READINESS_LIMITED = "LIMITED"
READINESS_MISSING = "MISSING"
READINESS_SUPPRESSED = "SUPPRESSED"
READINESS_STALE_OR_UNKNOWN = "STALE_OR_UNKNOWN"
READINESS_NOT_EVALUABLE = "NOT_EVALUABLE"
READINESS_NOT_APPLICABLE = "NOT_APPLICABLE"
READINESS_INSUFFICIENT = "INSUFFICIENT"   # no usable lane and at least one degraded

_USABLE_READINESS = frozenset({READINESS_READY, READINESS_LIMITED})

# Axis names.
AXIS_COMPANY_FUNDAMENTALS = "company_fundamentals"
AXIS_TECHNICAL_SIGNALS = "technical_signals"
AXIS_SENTIMENT = "sentiment"
AXIS_MACRO_CONTEXT = "macro_context"

# Non-equity detection (conservative symbol fallback — matches Phase 8F list).
_NON_EQUITY_CATEGORIES: frozenset[str] = frozenset({"ETF", "Crypto"})
_KNOWN_ETF_TICKERS: frozenset[str] = frozenset({
    "GLD", "QQQ", "SCHD", "SPY", "VGT", "VHT", "VIS",
    "VOO", "VTI", "VUG", "VXUS", "VYM", "XLE",
})
_KNOWN_CRYPTO_TICKERS: frozenset[str] = frozenset({"BTC", "XRP"})


# ── Typed output ──────────────────────────────────────────────────────────────


@dataclass
class LaneReadinessContribution:
    """How a single coverage lane contributes to an axis readiness signal."""
    lane: str
    coverage_status: str          # from Stage 5J (READY/LIMITED/SUPPRESSED/…)
    is_usable: bool
    is_applicable: bool           # False when the lane is not relevant for this ticker type
    notes: Optional[str] = None   # e.g. "not_applicable:non_equity"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "coverage_status": self.coverage_status,
            "is_usable": self.is_usable,
            "is_applicable": self.is_applicable,
            "notes": self.notes,
        }


@dataclass
class AxisReadinessSignal:
    """Shadow evidence readiness signal for one Intel v3 decision axis."""
    axis_name: str
    readiness: str        # READY | LIMITED | INSUFFICIENT | MISSING | SUPPRESSED |
                          # STALE_OR_UNKNOWN | NOT_EVALUABLE | NOT_APPLICABLE
    is_usable: bool
    contributing_lanes: list[str]    # lane names that are READY or LIMITED
    degraded_lanes: list[str]        # SUPPRESSED | STALE_OR_UNKNOWN | NOT_EVALUABLE
    missing_lanes: list[str]         # MISSING
    not_applicable_lanes: list[str]  # NOT_APPLICABLE for this ticker
    lane_contributions: list[LaneReadinessContribution]

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_name": self.axis_name,
            "readiness": self.readiness,
            "is_usable": self.is_usable,
            "contributing_lanes": list(self.contributing_lanes),
            "degraded_lanes": list(self.degraded_lanes),
            "missing_lanes": list(self.missing_lanes),
            "not_applicable_lanes": list(self.not_applicable_lanes),
            "lane_contributions": [c.to_dict() for c in self.lane_contributions],
        }


@dataclass
class TickerDecisionReadiness:
    """Shadow decision input readiness for one ticker."""
    ticker: str
    sec_lane_applicable: bool   # False for ETF/fund/crypto
    axes: dict[str, AxisReadinessSignal] = field(default_factory=dict)
    any_axis_usable: bool = False
    usable_axis_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "sec_lane_applicable": self.sec_lane_applicable,
            "axes": {k: v.to_dict() for k, v in self.axes.items()},
            "any_axis_usable": self.any_axis_usable,
            "usable_axis_count": self.usable_axis_count,
        }


@dataclass
class ResearchEvidenceDecisionInputShadow:
    """Stage 5K shadow output: per-ticker and portfolio axis readiness signals.

    This object is SHADOW-ONLY and DIAGNOSTIC-ONLY. It NEVER drives visible
    Buy/Hold/Trim/Sell decisions. safe_for_decision is permanently False.
    """
    schema_version: str
    adapter_version: str
    user_id: str
    generated_at: str
    coverage_schema_version: str          # from input Stage 5J summary
    shadow_only: bool = True              # immutable
    safe_for_decision: bool = False       # immutable — governance gate deferred
    no_guessing: bool = True

    portfolio_ticker_count: int = 0
    ticker_readiness: dict[str, TickerDecisionReadiness] = field(default_factory=dict)
    portfolio_macro: Optional[AxisReadinessSignal] = None

    # Aggregate counts.
    tickers_with_any_usable_axis: int = 0
    tickers_fully_missing: int = 0        # no usable axis at all
    axis_usable_counts: dict[str, int] = field(default_factory=dict)

    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_version": self.adapter_version,
            "user_id": self.user_id,
            "generated_at": self.generated_at,
            "coverage_schema_version": self.coverage_schema_version,
            "shadow_only": self.shadow_only,
            "safe_for_decision": self.safe_for_decision,
            "no_guessing": self.no_guessing,
            "portfolio_ticker_count": self.portfolio_ticker_count,
            "ticker_readiness": {k: v.to_dict() for k, v in self.ticker_readiness.items()},
            "portfolio_macro": self.portfolio_macro.to_dict() if self.portfolio_macro else None,
            "tickers_with_any_usable_axis": self.tickers_with_any_usable_axis,
            "tickers_fully_missing": self.tickers_fully_missing,
            "axis_usable_counts": dict(self.axis_usable_counts),
            "errors": list(self.errors),
        }


# ── Public API ────────────────────────────────────────────────────────────────


def compute_decision_input_readiness(
    coverage: ResearchEvidenceCoverageSummary,
    *,
    holding_context_by_ticker: Optional[dict[str, dict]] = None,
) -> ResearchEvidenceDecisionInputShadow:
    """Derive deterministic shadow axis-readiness signals from Stage 5J coverage.

    Args:
        coverage: Stage 5J ResearchEvidenceCoverageSummary (read-only input).
        holding_context_by_ticker: Optional dict of {ticker: {"category": ...}}
            used to determine SEC lane applicability for each ticker. When
            absent, the conservative symbol-based fallback is used.

    Returns:
        ResearchEvidenceDecisionInputShadow — always non-None, always
        safe_for_decision=False, always shadow_only=True.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    ctx = holding_context_by_ticker or {}

    ticker_readiness: dict[str, TickerDecisionReadiness] = {}
    axis_usable_counts: dict[str, int] = {
        AXIS_COMPANY_FUNDAMENTALS: 0,
        AXIS_TECHNICAL_SIGNALS: 0,
        AXIS_SENTIMENT: 0,
    }
    tickers_with_any = 0
    tickers_fully_missing = 0

    for ticker, ticker_cov in coverage.ticker_coverage.items():
        lanes = ticker_cov.lanes
        sec_applicable = _is_sec_lane_applicable(ticker, ctx.get(ticker))

        fund_axis = _build_company_fundamentals_axis(
            ticker=ticker,
            lanes=lanes,
            sec_applicable=sec_applicable,
        )
        tech_axis = _build_single_lane_axis(
            axis_name=AXIS_TECHNICAL_SIGNALS,
            lane=LANE_TECHNICALS,
            lanes=lanes,
        )
        sent_axis = _build_single_lane_axis(
            axis_name=AXIS_SENTIMENT,
            lane=LANE_NEWS_SENTIMENT,
            lanes=lanes,
        )

        axes = {
            AXIS_COMPANY_FUNDAMENTALS: fund_axis,
            AXIS_TECHNICAL_SIGNALS: tech_axis,
            AXIS_SENTIMENT: sent_axis,
        }
        usable_count = sum(1 for a in axes.values() if a.is_usable)
        any_usable = usable_count > 0

        ticker_readiness[ticker] = TickerDecisionReadiness(
            ticker=ticker,
            sec_lane_applicable=sec_applicable,
            axes=axes,
            any_axis_usable=any_usable,
            usable_axis_count=usable_count,
        )

        if any_usable:
            tickers_with_any += 1
        else:
            tickers_fully_missing += 1

        for axis_name in (AXIS_COMPANY_FUNDAMENTALS, AXIS_TECHNICAL_SIGNALS, AXIS_SENTIMENT):
            if axes[axis_name].is_usable:
                axis_usable_counts[axis_name] = axis_usable_counts.get(axis_name, 0) + 1

    portfolio_macro = _build_single_lane_axis(
        axis_name=AXIS_MACRO_CONTEXT,
        lane=LANE_MACRO_CONTEXT,
        lanes={"macro_context": coverage.portfolio_macro_coverage},
    )

    return ResearchEvidenceDecisionInputShadow(
        schema_version=ADAPTER_VERSION,
        adapter_version=ADAPTER_VERSION,
        user_id=coverage.user_id,
        generated_at=now_iso,
        coverage_schema_version=coverage.schema_version,
        shadow_only=True,
        safe_for_decision=False,
        no_guessing=True,
        portfolio_ticker_count=coverage.portfolio_ticker_count,
        ticker_readiness=ticker_readiness,
        portfolio_macro=portfolio_macro,
        tickers_with_any_usable_axis=tickers_with_any,
        tickers_fully_missing=tickers_fully_missing,
        axis_usable_counts=axis_usable_counts,
        errors=list(coverage.errors),
    )


# ── Axis builders ─────────────────────────────────────────────────────────────


def _build_company_fundamentals_axis(
    *,
    ticker: str,
    lanes: dict[str, Any],
    sec_applicable: bool,
) -> AxisReadinessSignal:
    """Build the company_fundamentals axis from SEC + fundamentals lanes.

    SEC CompanyFacts contributes only when sec_applicable=True.
    The fundamentals (yfinance) lane contributes for all tickers.
    """
    contributions: list[LaneReadinessContribution] = []
    contributing: list[str] = []
    degraded: list[str] = []
    missing: list[str] = []
    not_applicable: list[str] = []

    # SEC lane.
    sec_cov = lanes.get(LANE_SEC_COMPANY_FACTS)
    if not sec_applicable:
        contributions.append(LaneReadinessContribution(
            lane=LANE_SEC_COMPANY_FACTS,
            coverage_status=READINESS_NOT_APPLICABLE,
            is_usable=False,
            is_applicable=False,
            notes="not_applicable:non_equity",
        ))
        not_applicable.append(LANE_SEC_COMPANY_FACTS)
    else:
        c = _classify_lane_contribution(LANE_SEC_COMPANY_FACTS, sec_cov)
        contributions.append(c)
        _bucket(c, contributing, degraded, missing)

    # Fundamentals lane (all tickers).
    fund_cov = lanes.get(LANE_FUNDAMENTALS)
    c = _classify_lane_contribution(LANE_FUNDAMENTALS, fund_cov)
    contributions.append(c)
    _bucket(c, contributing, degraded, missing)

    readiness = _derive_axis_readiness_with_level(contributions, contributing, degraded, missing)
    return AxisReadinessSignal(
        axis_name=AXIS_COMPANY_FUNDAMENTALS,
        readiness=readiness,
        is_usable=readiness in _USABLE_READINESS,
        contributing_lanes=contributing,
        degraded_lanes=degraded,
        missing_lanes=missing,
        not_applicable_lanes=not_applicable,
        lane_contributions=contributions,
    )


def _build_single_lane_axis(
    *,
    axis_name: str,
    lane: str,
    lanes: dict[str, Any],
) -> AxisReadinessSignal:
    """Build an axis backed by exactly one lane."""
    cov = lanes.get(lane)
    c = _classify_lane_contribution(lane, cov)
    contributions = [c]
    contributing: list[str] = []
    degraded: list[str] = []
    missing: list[str] = []
    _bucket(c, contributing, degraded, missing)
    readiness = _derive_axis_readiness_with_level(contributions, contributing, degraded, missing)
    return AxisReadinessSignal(
        axis_name=axis_name,
        readiness=readiness,
        is_usable=readiness in _USABLE_READINESS,
        contributing_lanes=contributing,
        degraded_lanes=degraded,
        missing_lanes=missing,
        not_applicable_lanes=[],
        lane_contributions=[c],
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _classify_lane_contribution(
    lane: str,
    cov: Any,
) -> LaneReadinessContribution:
    """Map a LaneCoverage (or None) to a LaneReadinessContribution."""
    if cov is None:
        return LaneReadinessContribution(
            lane=lane,
            coverage_status=STATUS_MISSING,
            is_usable=False,
            is_applicable=True,
            notes="lane_not_found_in_coverage",
        )
    status = cov.status if hasattr(cov, "status") else STATUS_MISSING
    is_usable = status in (STATUS_READY, STATUS_LIMITED)
    return LaneReadinessContribution(
        lane=lane,
        coverage_status=status,
        is_usable=is_usable,
        is_applicable=True,
    )


def _bucket(
    c: LaneReadinessContribution,
    contributing: list[str],
    degraded: list[str],
    missing: list[str],
) -> None:
    if c.is_usable:
        contributing.append(c.lane)
    elif c.coverage_status == STATUS_MISSING:
        missing.append(c.lane)
    else:
        degraded.append(c.lane)


def _derive_axis_readiness_with_level(
    contributions: list[LaneReadinessContribution],
    contributing: list[str],
    degraded: list[str],
    missing: list[str],
) -> str:
    """Like _derive_axis_readiness but resolves READY vs LIMITED from contributions."""
    if not contributing:
        if degraded:
            return READINESS_INSUFFICIENT
        return READINESS_MISSING

    has_ready = any(
        c.coverage_status == STATUS_READY
        for c in contributions
        if c.is_usable
    )
    if has_ready:
        return READINESS_READY
    return READINESS_LIMITED


def _is_sec_lane_applicable(
    ticker: str,
    holding_context: Optional[dict],
) -> bool:
    """Return True if the SEC CompanyFacts lane is applicable for this ticker.

    Priority:
      1. Holding context category/asset_type: ETF/Crypto → not applicable.
      2. Conservative symbol fallback for known fund/ETF/crypto tickers.
      3. All others → applicable (assume equity).
    """
    if holding_context:
        cat = (holding_context.get("category") or "").strip()
        asset_type = (holding_context.get("asset_type") or "").strip()
        if cat in _NON_EQUITY_CATEGORIES or asset_type in _NON_EQUITY_CATEGORIES:
            return False

    ticker_upper = (ticker or "").upper().strip()
    if ticker_upper in _KNOWN_ETF_TICKERS or ticker_upper in _KNOWN_CRYPTO_TICKERS:
        return False

    return True


def log_decision_readiness_summary(shadow: ResearchEvidenceDecisionInputShadow) -> None:
    """Emit a compact structured log of the shadow readiness. No raw payloads."""
    logger.info(
        "research_evidence_decision_readiness_summary user_id=%s "
        "portfolio_ticker_count=%d tickers_with_any_usable_axis=%d "
        "tickers_fully_missing=%d axis_usable_counts=%s "
        "macro_readiness=%s shadow_only=%s safe_for_decision=%s",
        shadow.user_id,
        shadow.portfolio_ticker_count,
        shadow.tickers_with_any_usable_axis,
        shadow.tickers_fully_missing,
        shadow.axis_usable_counts,
        shadow.portfolio_macro.readiness if shadow.portfolio_macro else "none",
        shadow.shadow_only,
        shadow.safe_for_decision,
    )
