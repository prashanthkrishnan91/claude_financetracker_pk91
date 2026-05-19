"""Stage 6 — Evidence-Aware Intel v3 Decision Engine Governance v1.

Connects Stage 5K ResearchEvidenceDecisionInputShadow to Intel v3 decision
governance. When INTEL_V3_EVIDENCE_AWARE_POLICY_ENABLED=true, applies
deterministic evidence-readiness rules to DecisionInputV3 before decide().

Architecture contracts (non-negotiable):
  - Consumes TickerDecisionReadiness (Stage 5K output). Never re-fetches
    coverage data, never reads artifacts, never calls providers.
  - Pure deterministic functions. No IO, no LLM, no DB reads, no DB writes.
  - NEVER calls decide(). Never imports decision_policy_v1.
  - Deterministic Intel v3 policy (decision_policy_v1.decide()) remains
    the final and only visible action authority.
  - Flag off: inp is returned completely unchanged. Visible behavior is
    contract-equivalent to the flag-off state.
  - Flag on: only inp.evidence_quality and inp.suppression_reasons are
    mutated. No other DecisionInputV3 fields are touched.
  - ETF/crypto with SEC not_applicable: treated honestly; SEC absence is
    not penalized — the SEC lane is simply marked not_applicable.
  - Macro context: adds advisory labels to suppression_reasons only.
    It never independently forces or blocks any action.
  - No raw artifact payloads, source URLs, API keys, secrets, or PII in output.
  - NEVER writes to intel_v3_snapshots, recommendations, or research_*.

Evidence governance rules (flag on):
  Priority 1: Suppressed/contradicted fundamentals → AxisBand.SUPPRESSED
              Blocks BUY. TRIM/SELL governed by portfolio_fit/risk_band
              (existing policy) — governance does not touch those axes.

  Priority 2: Zero usable axes → AxisBand.THIN
              HOLD is the safe default.

  Priority 3a: Fundamentals READY + corroboration (technicals or sentiment)
               → AxisBand.STRONG (HIGH conviction BUY possible)
  Priority 3b: Fundamentals READY + no corroboration
               → AxisBand.OK (MEDIUM conviction BUY; single-signal discipline)
  Priority 4a: Fundamentals LIMITED + corroboration
               → AxisBand.OK (MEDIUM conviction BUY allowed)
  Priority 4b: Fundamentals LIMITED + no corroboration, asset-type conditional:
               equity or ETF → AxisBand.OK (MEDIUM conviction BUY; conviction cap)
               crypto or unknown → AxisBand.THIN (BUY blocked; yfinance-only
               fundamentals not adequate basis without corroboration)
               [Calibrated 2026-05-19: equity/ETF limited evidence can support BUY
                with cap; crypto/unknown requires corroboration or READY fundamentals.]
  Priority 5:  No usable fundamentals + other signals only
               → AxisBand.THIN (BUY blocked; no fundamental anchor)
  Fallback:    AxisBand.THIN (conservative)

ETF/crypto handling:
  Stage 5K already marks SEC lane as not_applicable for ETF/crypto. The
  company_fundamentals axis for ETF/crypto relies solely on the yfinance
  fundamentals lane, which can reach READY. So an ETF with usable yfinance
  fundamentals is not penalized.

Conviction capping:
  THIN  → conviction capped to LOW   (existing policy Cap 1)
  SUPPRESSED → BUY blocked (HOLD path); conviction LOW
  OK    → HIGH conviction capped to MEDIUM (existing policy guardrail)
  STRONG → no additional cap from governance
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .decision_contracts import AxisBand, DecisionInputV3
from .research_evidence_decision_input_adapter_v1 import (
    AXIS_COMPANY_FUNDAMENTALS,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL_SIGNALS,
    INSTRUMENT_CATEGORY_CRYPTO,
    INSTRUMENT_CATEGORY_ETF,
    INSTRUMENT_CATEGORY_EQUITY,
    READINESS_INSUFFICIENT,
    READINESS_LIMITED,
    READINESS_MISSING,
    READINESS_NOT_APPLICABLE,
    READINESS_NOT_EVALUABLE,
    READINESS_READY,
    READINESS_STALE_OR_UNKNOWN,
    READINESS_SUPPRESSED,
    AxisReadinessSignal,
    ResearchEvidenceDecisionInputShadow,
    TickerDecisionReadiness,
)

logger = logging.getLogger(__name__)

GOVERNANCE_VERSION = "intel_v3_evidence_aware_governance.v1"
FLAG_NAME = "INTEL_V3_EVIDENCE_AWARE_POLICY_ENABLED"

_AXES_THREE = (AXIS_COMPANY_FUNDAMENTALS, AXIS_TECHNICAL_SIGNALS, AXIS_SENTIMENT)

# AxisBand values that cause the existing policy to cap conviction.
_CAPPING_BANDS = frozenset({AxisBand.THIN, AxisBand.SUPPRESSED, AxisBand.OK})


# ── Output types ──────────────────────────────────────────────────────────────


@dataclass
class EvidenceGovernanceResult:
    """Per-ticker Stage 6 evidence governance diagnostics.

    Backend-safe: no raw artifacts, source URLs, fact contents, API keys.

    Diagnostic fields (added 2026-05-19 for calibration visibility):
      primary_evidence_readiness:  readiness of company_fundamentals axis (the anchor).
      auxiliary_evidence_readiness: dict {tech: readiness, sentiment: readiness}.
      corroboration_gap:           True when fundamentals usable but no tech/sent usable.
      governance_priority_applied: which priority rule determined the governed band.
      safe_for_visible_decision_reason: brief reason why safe=True or safe=False.
    """
    ticker: str
    flag_enabled: bool
    governance_applied: bool

    original_evidence_quality: str   # AxisBand.value before governance
    governed_evidence_quality: str   # AxisBand.value after governance (same when flag off)

    conviction_cap_applied: bool
    conviction_cap_reason: Optional[str]

    evidence_governance_status: str  # "active" | "inactive" | "no_readiness_data"
    supported_axis_count: int
    missing_axis_count: int
    degraded_axis_count: int
    not_applicable_axis_count: int
    company_fundamentals_readiness: str
    technical_signals_readiness: str
    sentiment_readiness: str
    portfolio_macro_readiness: str

    action_blocks_applied: list[str] = field(default_factory=list)
    safe_for_visible_decision: bool = False
    reason_codes: list[str] = field(default_factory=list)

    # Calibration diagnostics (2026-05-19)
    primary_evidence_readiness: str = "MISSING"
    auxiliary_evidence_readiness: dict[str, Any] = field(default_factory=dict)
    corroboration_gap: bool = False
    governance_priority_applied: str = "unknown"
    safe_for_visible_decision_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "flag_enabled": self.flag_enabled,
            "governance_applied": self.governance_applied,
            "original_evidence_quality": self.original_evidence_quality,
            "governed_evidence_quality": self.governed_evidence_quality,
            "conviction_cap_applied": self.conviction_cap_applied,
            "conviction_cap_reason": self.conviction_cap_reason,
            "evidence_governance_status": self.evidence_governance_status,
            "supported_axis_count": self.supported_axis_count,
            "missing_axis_count": self.missing_axis_count,
            "degraded_axis_count": self.degraded_axis_count,
            "not_applicable_axis_count": self.not_applicable_axis_count,
            "company_fundamentals_readiness": self.company_fundamentals_readiness,
            "technical_signals_readiness": self.technical_signals_readiness,
            "sentiment_readiness": self.sentiment_readiness,
            "portfolio_macro_readiness": self.portfolio_macro_readiness,
            "action_blocks_applied": list(self.action_blocks_applied),
            "safe_for_visible_decision": self.safe_for_visible_decision,
            "reason_codes": list(self.reason_codes),
            "primary_evidence_readiness": self.primary_evidence_readiness,
            "auxiliary_evidence_readiness": dict(self.auxiliary_evidence_readiness),
            "corroboration_gap": self.corroboration_gap,
            "governance_priority_applied": self.governance_priority_applied,
            "safe_for_visible_decision_reason": self.safe_for_visible_decision_reason,
        }


@dataclass
class PortfolioGovernanceSummary:
    """Portfolio-level Stage 6 governance diagnostics."""
    schema_version: str
    governance_version: str
    flag_enabled: bool
    flag_name: str

    portfolio_ticker_count: int
    evidence_readiness_summary: dict[str, Any]
    governance_summary: dict[str, Any]
    hold_collapse_risk: str  # "high" | "medium" | "low" | "unknown"

    per_ticker: list[dict[str, Any]] = field(default_factory=list)
    action_distribution_flag_off: dict[str, int] = field(default_factory=dict)
    action_distribution_flag_on: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "governance_version": self.governance_version,
            "flag_enabled": self.flag_enabled,
            "flag_name": self.flag_name,
            "portfolio_ticker_count": self.portfolio_ticker_count,
            "evidence_readiness_summary": dict(self.evidence_readiness_summary),
            "governance_summary": dict(self.governance_summary),
            "hold_collapse_risk": self.hold_collapse_risk,
            "per_ticker": list(self.per_ticker),
            "action_distribution_flag_off": dict(self.action_distribution_flag_off),
            "action_distribution_flag_on": dict(self.action_distribution_flag_on),
        }


# ── Public API ────────────────────────────────────────────────────────────────


def apply_evidence_governance(
    inp: DecisionInputV3,
    ticker_readiness: Optional[TickerDecisionReadiness],
    portfolio_macro: Optional[AxisReadinessSignal],
    *,
    flag_enabled: bool,
) -> EvidenceGovernanceResult:
    """Apply evidence governance to DecisionInputV3 in-place.

    When flag_enabled=False: no-op — inp is unchanged, result has
    governance_applied=False.

    When flag_enabled=True and ticker_readiness is available: governed
    evidence_quality replaces inp.evidence_quality and macro advisory
    context is added to inp.suppression_reasons. Only these two fields
    are mutated.

    The deterministic decide() function is still the final authority.
    This function only governs the evidence_quality input axis.

    Returns:
        EvidenceGovernanceResult — full backend-safe diagnostics.
    """
    original_quality = inp.evidence_quality
    original_str = original_quality.value

    macro_readiness = _get_macro_readiness(portfolio_macro)

    if not flag_enabled or ticker_readiness is None:
        status = "inactive" if not flag_enabled else "no_readiness_data"
        axes = ticker_readiness.axes if ticker_readiness else {}
        fund_r = _axis_readiness(axes, AXIS_COMPANY_FUNDAMENTALS)
        return EvidenceGovernanceResult(
            ticker=inp.ticker,
            flag_enabled=flag_enabled,
            governance_applied=False,
            original_evidence_quality=original_str,
            governed_evidence_quality=original_str,
            conviction_cap_applied=False,
            conviction_cap_reason=None,
            evidence_governance_status=status,
            supported_axis_count=_count_axis(axes, "usable"),
            missing_axis_count=_count_axis(axes, "missing"),
            degraded_axis_count=_count_axis(axes, "degraded"),
            not_applicable_axis_count=_count_axis(axes, "not_applicable"),
            company_fundamentals_readiness=fund_r,
            technical_signals_readiness=_axis_readiness(axes, AXIS_TECHNICAL_SIGNALS),
            sentiment_readiness=_axis_readiness(axes, AXIS_SENTIMENT),
            portfolio_macro_readiness=macro_readiness,
            action_blocks_applied=[],
            safe_for_visible_decision=False,
            reason_codes=[
                "governance_flag_off" if not flag_enabled else "no_readiness_data"
            ],
            primary_evidence_readiness=fund_r,
            auxiliary_evidence_readiness={
                "technical_signals": _axis_readiness(axes, AXIS_TECHNICAL_SIGNALS),
                "sentiment": _axis_readiness(axes, AXIS_SENTIMENT),
            },
            corroboration_gap=False,
            governance_priority_applied="governance_inactive",
            safe_for_visible_decision_reason="governance_not_active",
        )

    # Governance active: derive governed evidence quality from Stage 5K readiness.
    axes = ticker_readiness.axes
    governed_band, reason_codes, action_blocks, priority_applied = (
        _derive_governed_evidence_quality(ticker_readiness)
    )
    governed_str = governed_band.value

    # Apply macro advisory context (suppression_reasons only — no action block).
    _apply_macro_advisory(inp, portfolio_macro, macro_readiness)

    # Mutate inp.evidence_quality in-place.
    inp.evidence_quality = governed_band

    cap_applied = _will_cap_conviction(governed_band)
    cap_reason = (
        f"evidence_governance:{governed_str}" if cap_applied else None
    )

    supported = _count_axis(axes, "usable")
    missing = _count_axis(axes, "missing")
    degraded = _count_axis(axes, "degraded")
    not_applicable = _count_axis(axes, "not_applicable")
    safe = (
        supported > 0
        and governed_band not in {AxisBand.SUPPRESSED, AxisBand.THIN}
    )

    fund_r = _axis_readiness(axes, AXIS_COMPANY_FUNDAMENTALS)
    tech_r = _axis_readiness(axes, AXIS_TECHNICAL_SIGNALS)
    sent_r = _axis_readiness(axes, AXIS_SENTIMENT)

    fund_ax = axes.get(AXIS_COMPANY_FUNDAMENTALS)
    tech_ax = axes.get(AXIS_TECHNICAL_SIGNALS)
    sent_ax = axes.get(AXIS_SENTIMENT)
    fund_usable = bool(fund_ax and fund_ax.is_usable)
    tech_usable = bool(tech_ax and tech_ax.is_usable)
    sent_usable = bool(sent_ax and sent_ax.is_usable)
    corroboration_gap = fund_usable and not tech_usable and not sent_usable

    safe_reason = _build_safe_reason(safe, governed_band, supported, action_blocks)

    logger.info(
        "evidence_governance_applied ticker=%s original=%s governed=%s "
        "supported_axes=%d priority=%s corroboration_gap=%s safe=%s reason_codes=%s",
        inp.ticker, original_str, governed_str, supported,
        priority_applied, corroboration_gap, safe, reason_codes,
    )

    return EvidenceGovernanceResult(
        ticker=inp.ticker,
        flag_enabled=True,
        governance_applied=True,
        original_evidence_quality=original_str,
        governed_evidence_quality=governed_str,
        conviction_cap_applied=cap_applied,
        conviction_cap_reason=cap_reason,
        evidence_governance_status="active",
        supported_axis_count=supported,
        missing_axis_count=missing,
        degraded_axis_count=degraded,
        not_applicable_axis_count=not_applicable,
        company_fundamentals_readiness=fund_r,
        technical_signals_readiness=tech_r,
        sentiment_readiness=sent_r,
        portfolio_macro_readiness=macro_readiness,
        action_blocks_applied=action_blocks,
        safe_for_visible_decision=safe,
        reason_codes=reason_codes,
        primary_evidence_readiness=fund_r,
        auxiliary_evidence_readiness={"technical_signals": tech_r, "sentiment": sent_r},
        corroboration_gap=corroboration_gap,
        governance_priority_applied=priority_applied,
        safe_for_visible_decision_reason=safe_reason,
    )


def compute_portfolio_governance_summary(
    shadow: ResearchEvidenceDecisionInputShadow,
    *,
    flag_enabled: bool,
    per_ticker_results: list[EvidenceGovernanceResult],
    action_distribution_off: dict[str, int],
    action_distribution_on: dict[str, int],
) -> PortfolioGovernanceSummary:
    """Build portfolio-level Stage 6 governance diagnostics summary.

    Pure — no IO, no DB reads.
    No raw artifacts/payloads/source URLs/secrets.
    """
    evidence_blocked = sum(1 for r in per_ticker_results if r.action_blocks_applied)
    conviction_capped = sum(1 for r in per_ticker_results if r.conviction_cap_applied)
    safe_count = sum(1 for r in per_ticker_results if r.safe_for_visible_decision)

    active_pct = _hold_pct(
        action_distribution_on if flag_enabled else action_distribution_off
    )
    hold_collapse_risk = _classify_hold_collapse_risk(active_pct)

    macro_readiness = READINESS_MISSING
    if shadow.portfolio_macro:
        macro_readiness = shadow.portfolio_macro.readiness

    return PortfolioGovernanceSummary(
        schema_version="stage6_governance_diagnostics.v1",
        governance_version=GOVERNANCE_VERSION,
        flag_enabled=flag_enabled,
        flag_name=FLAG_NAME,
        portfolio_ticker_count=shadow.portfolio_ticker_count,
        evidence_readiness_summary={
            "tickers_with_any_usable_axis": shadow.tickers_with_any_usable_axis,
            "tickers_fully_missing": shadow.tickers_fully_missing,
            "macro_readiness": macro_readiness,
            "axis_usable_counts": dict(shadow.axis_usable_counts),
        },
        governance_summary={
            "evidence_blocked_action_count": evidence_blocked,
            "conviction_cap_count": conviction_capped,
            "safe_for_visible_decision_count": safe_count,
        },
        hold_collapse_risk=hold_collapse_risk,
        per_ticker=[r.to_dict() for r in per_ticker_results],
        action_distribution_flag_off=dict(action_distribution_off),
        action_distribution_flag_on=dict(action_distribution_on),
    )


# ── Internal helpers ──────────────────────────────────────────────────────────


def _derive_governed_evidence_quality(
    ticker_readiness: TickerDecisionReadiness,
) -> tuple[AxisBand, list[str], list[str], str]:
    """Map Stage 5K axis readiness to a single AxisBand.

    Returns: (governed_band, reason_codes, action_blocks, priority_applied).

    Priority order is defined in the module docstring.

    Calibration (2026-05-19):
      Priority 4b conditional: fund_limited + no corroboration is asset-type gated.
      Equity and ETF/fund: limited-quality company evidence (SEC PARTIAL / yfinance
      VENDOR_DERIVED) supports BUY with conviction cap — adequate primary-evidence
      basis exists even without auxiliary corroboration.
      Crypto and unknown instruments: yfinance-only LIMITED fundamentals alone are
      not an adequate basis for BUY without auxiliary corroboration → THIN.
      Missing/suppressed fundamentals still hard-block BUY (Priority 1 / 2).

    ETF/crypto: SEC not_applicable is not penalized. The company_fundamentals
    axis for non-equity already excludes the SEC lane in Stage 5K, so the
    yfinance fundamentals lane can reach READY independently.
    ETF at READY fundamentals: reaches OK or STRONG via P3a/P3b/P4a.
    Crypto at LIMITED fundamentals, no corroboration: THIN (P4b crypto branch).

    TRIM/SELL paths are not touched by evidence governance; those are governed
    by portfolio_fit and risk_band which belong to deterministic policy.
    """
    axes = ticker_readiness.axes
    fund_axis = axes.get(AXIS_COMPANY_FUNDAMENTALS)
    tech_axis = axes.get(AXIS_TECHNICAL_SIGNALS)
    sent_axis = axes.get(AXIS_SENTIMENT)

    reason_codes: list[str] = []
    action_blocks: list[str] = []

    fund_usable = bool(fund_axis and fund_axis.is_usable)
    fund_ready = bool(fund_axis and fund_axis.readiness == READINESS_READY)
    fund_limited = bool(fund_axis and fund_axis.readiness == READINESS_LIMITED)
    fund_suppressed = bool(
        fund_axis and fund_axis.readiness in {READINESS_SUPPRESSED, READINESS_INSUFFICIENT}
    )
    fund_stale = bool(fund_axis and fund_axis.readiness == READINESS_STALE_OR_UNKNOWN)
    fund_not_evaluable = bool(
        fund_axis and fund_axis.readiness == READINESS_NOT_EVALUABLE
    )

    tech_usable = bool(tech_axis and tech_axis.is_usable)
    sent_usable = bool(sent_axis and sent_axis.is_usable)
    corroborated = tech_usable or sent_usable

    usable_count = sum([fund_usable, tech_usable, sent_usable])

    # Priority 1: Suppressed/contradicted fundamentals block BUY.
    if fund_suppressed:
        reason_codes.append("fundamentals_suppressed_or_contradicted")
        action_blocks.append("buy_blocked_suppressed_fundamentals")
        return AxisBand.SUPPRESSED, reason_codes, action_blocks, "p1_suppressed_fundamentals"

    # Priority 2: Zero usable axes → THIN (safe HOLD default).
    if usable_count == 0:
        if fund_stale:
            reason_codes.append("fundamentals_stale_no_usable_axes")
            action_blocks.append("buy_blocked_stale_evidence")
            return AxisBand.THIN, reason_codes, action_blocks, "p2_stale_no_usable_axes"
        elif fund_not_evaluable:
            reason_codes.append("evidence_not_evaluable")
            action_blocks.append("buy_blocked_not_evaluable_evidence")
            return AxisBand.THIN, reason_codes, action_blocks, "p2_not_evaluable"
        else:
            reason_codes.append("all_evidence_axes_missing_or_degraded")
            action_blocks.append("buy_blocked_missing_evidence")
            return AxisBand.THIN, reason_codes, action_blocks, "p2_all_missing_or_degraded"

    # Priority 3a: Strong fundamentals with corroboration → STRONG.
    if fund_ready and corroborated:
        reason_codes.append("strong_fundamentals_with_corroboration")
        return AxisBand.STRONG, reason_codes, action_blocks, "p3a_ready_corroborated"

    # Priority 3b: Strong fundamentals, no corroboration → OK.
    if fund_ready and not corroborated:
        reason_codes.append("ready_fundamentals_no_signal_corroboration")
        return AxisBand.OK, reason_codes, action_blocks, "p3b_ready_no_corroboration"

    # Priority 4a: Limited fundamentals + corroboration → OK.
    if fund_limited and corroborated:
        reason_codes.append("limited_fundamentals_with_supporting_signal")
        return AxisBand.OK, reason_codes, action_blocks, "p4a_limited_corroborated"

    # Priority 4b: Limited fundamentals, no corroboration.
    # Asset-type conditional: equity and ETF/fund have an acceptable primary-evidence
    # basis even when fundamentals are only LIMITED → OK with conviction cap.
    # Crypto and unknown instruments may reach LIMITED only via generic yfinance
    # data — not an adequate basis for BUY without corroboration → THIN.
    # Conviction for OK band is capped to MEDIUM by decide() guardrail (Cap 5).
    if fund_limited and not corroborated:
        inst_cat = ticker_readiness.instrument_category
        if inst_cat in (INSTRUMENT_CATEGORY_EQUITY, INSTRUMENT_CATEGORY_ETF):
            code = (
                "limited_equity_fundamentals_ok_with_cap"
                if inst_cat == INSTRUMENT_CATEGORY_EQUITY
                else "limited_etf_evidence_ok_with_cap"
            )
            reason_codes.append(code)
            return AxisBand.OK, reason_codes, action_blocks, "p4b_limited_no_corroboration"
        else:
            # crypto or unknown: generic yfinance LIMITED not sufficient for BUY.
            code = (
                "limited_crypto_fundamentals_not_safe"
                if inst_cat == INSTRUMENT_CATEGORY_CRYPTO
                else "limited_unknown_instrument_fundamentals_not_safe"
            )
            reason_codes.append(code)
            action_blocks.append("buy_blocked_insufficient_evidence_basis")
            return AxisBand.THIN, reason_codes, action_blocks, "p4b_crypto_or_unknown_thin"

    # Priority 5: No usable fundamentals, other signals only → THIN.
    if usable_count > 0 and not fund_usable:
        reason_codes.append("no_fundamental_anchor_technicals_sentiment_only")
        action_blocks.append("buy_blocked_no_fundamental_evidence")
        return AxisBand.THIN, reason_codes, action_blocks, "p5_no_fundamental_anchor"

    # Fallback (should not normally be reached).
    reason_codes.append("governance_fallback_insufficient")
    action_blocks.append("buy_blocked_governance_fallback")
    return AxisBand.THIN, reason_codes, action_blocks, "fallback"


def _apply_macro_advisory(
    inp: DecisionInputV3,
    portfolio_macro: Optional[AxisReadinessSignal],
    macro_readiness: str,
) -> None:
    """Add macro context as advisory suppression_reason only.

    Macro cannot independently force or block any action. It adds context
    so operators can see macro state alongside decisions — nothing more.
    """
    if portfolio_macro is None:
        inp.suppression_reasons["macro_context_advisory"] = "macro_context_missing"
        return
    if portfolio_macro.readiness in {READINESS_READY, READINESS_LIMITED}:
        inp.suppression_reasons["macro_context_advisory"] = (
            f"macro_context_{macro_readiness.lower()}_available_advisory_only"
        )
    else:
        inp.suppression_reasons["macro_context_advisory"] = (
            f"macro_context_{macro_readiness.lower()}_degraded_advisory_only"
        )


def _will_cap_conviction(governed_band: AxisBand) -> bool:
    """Return True when governance sets an evidence_quality that caps conviction.

    THIN or SUPPRESSED → conviction forced to LOW (existing policy Cap 1).
    OK → HIGH BUY conviction capped to MEDIUM (existing policy guardrail Cap 5).
    STRONG → no conviction cap from evidence governance.
    """
    return governed_band in _CAPPING_BANDS


def _count_axis(axes: dict, kind: str) -> int:
    """Count axes by kind across the three per-ticker axes."""
    count = 0
    for name in _AXES_THREE:
        ax = axes.get(name)
        if ax is None:
            if kind == "missing":
                count += 1
            continue
        if kind == "usable" and ax.is_usable:
            count += 1
        elif kind == "missing" and ax.readiness == READINESS_MISSING:
            count += 1
        elif kind == "degraded" and (
            not ax.is_usable
            and ax.readiness not in {READINESS_MISSING, READINESS_NOT_APPLICABLE}
        ):
            count += 1
        elif kind == "not_applicable" and ax.readiness == READINESS_NOT_APPLICABLE:
            count += 1
    return count


def _axis_readiness(axes: dict, axis_name: str) -> str:
    ax = axes.get(axis_name)
    if ax is None:
        return READINESS_MISSING
    return ax.readiness


def _get_macro_readiness(portfolio_macro: Optional[AxisReadinessSignal]) -> str:
    if portfolio_macro is None:
        return READINESS_MISSING
    return portfolio_macro.readiness


def _hold_pct(distribution: dict[str, int]) -> float:
    total = sum(distribution.values())
    if total == 0:
        return 1.0
    return distribution.get("HOLD", 0) / total


def _build_safe_reason(
    safe: bool,
    governed_band: AxisBand,
    supported: int,
    action_blocks: list[str],
) -> str:
    """Return a brief string explaining why safe_for_visible_decision is True/False."""
    if safe:
        return f"primary_evidence_usable:band={governed_band.value}"
    if governed_band == AxisBand.SUPPRESSED:
        return "hard_blocked:fundamentals_suppressed"
    if governed_band == AxisBand.THIN:
        if action_blocks:
            return f"hard_blocked:{action_blocks[0]}"
        return "hard_blocked:thin_evidence"
    if supported == 0:
        return "no_usable_axes"
    return f"band={governed_band.value}:supported={supported}"


def _classify_hold_collapse_risk(hold_pct: float) -> str:
    if hold_pct >= 0.9:
        return "high"
    if hold_pct >= 0.6:
        return "medium"
    return "low"
