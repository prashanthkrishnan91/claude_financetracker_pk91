"""V3 shadow-only BUY conviction guardrail — evidence-quality truth gate.

PR 9: caps HIGH-conviction BUY in v3 shadow when evidence-quality truth is
not PRESENT with HIGH trust. Shadow diagnostic only — no visible action, API,
Deploy, SQL, provider, or LLM changes.

Policy intent:
- SELL/TRIM protective decisions are never blocked by this guardrail.
- BUY at MEDIUM or LOW conviction is unaffected (already below HIGH).
- Only HIGH-conviction BUY is subject to this evidence-quality cap.
- "Strong enough" = DataTruthFinding for evidence_quality axis has
  status==PRESENT AND trust_level==HIGH (≥3 trusted dims or HIGH label).
- If the evidence_quality truth summary is absent, empty, not PRESENT, or
  trust_level is not HIGH, conviction is capped at MEDIUM.

Mapping to existing AxisBand (for reference only — guardrail reads truth):
  STRONG AxisBand ↔ PRESENT/HIGH trust  → guardrail does NOT fire
  OK     AxisBand ↔ PRESENT/MEDIUM trust → guardrail fires (caps HIGH→MEDIUM)
  THIN   AxisBand ↔ WEAK/LOW trust       → already capped LOW by existing policy
  SUPPRESSED      ↔ unsafe truth status   → BUY already blocked by policy

Pure function — no IO, DB, LLM, provider calls.
"""
from __future__ import annotations

from typing import Optional

from .data_truth_contracts import AxisTruthSummary, DataTruthStatus, SourceTrustLevel
from .decision_contracts import ActionV3, AxisBand, ConvictionV3

_CAPPED_CONVICTION = ConvictionV3.MEDIUM


def _evidence_is_high_trust(ev_summary: Optional[AxisTruthSummary]) -> bool:
    """True only when evidence quality axis is PRESENT with HIGH trust.

    The evidence_quality axis always has exactly one DataTruthFinding (from
    classify_evidence_signals()). We inspect the first finding directly.
    """
    if ev_summary is None or not ev_summary.findings:
        return False
    finding = ev_summary.findings[0]
    return (
        finding.status == DataTruthStatus.PRESENT
        and finding.trust_level == SourceTrustLevel.HIGH
    )


def apply_buy_conviction_guardrail(
    *,
    action: ActionV3,
    conviction: ConvictionV3,
    evidence_quality_truth: Optional[AxisTruthSummary],
) -> tuple[ConvictionV3, dict]:
    """Apply the evidence-quality BUY conviction guardrail.

    Returns (post_guardrail_conviction, guardrail_diagnostics).

    Guardrail fires only when all three conditions hold:
      1. action == BUY
      2. conviction == HIGH
      3. evidence_quality truth is not PRESENT/HIGH-trust

    SELL/TRIM are never affected. BUY at MEDIUM/LOW conviction is unaffected.
    When the guardrail fires, conviction is capped at MEDIUM (never below).

    Diagnostic keys (stable, aggregate-safe — no raw metrics/user data):
      buy_high_conviction_guardrail_applied (bool)
      buy_conviction_capped_reason         (str, empty when not applied)
      evidence_quality_truth_status        (str)
      evidence_quality_trust_level         (str)
      pre_guardrail_conviction             (str | None, only set when applied)
      post_guardrail_conviction            (str | None, only set when applied)
    """
    eq_status = "unknown"
    eq_trust = "unknown"
    if evidence_quality_truth is not None and evidence_quality_truth.findings:
        f = evidence_quality_truth.findings[0]
        eq_status = f.status.value
        eq_trust = f.trust_level.value

    guardrail_fires = (
        action == ActionV3.BUY
        and conviction == ConvictionV3.HIGH
        and not _evidence_is_high_trust(evidence_quality_truth)
    )

    if guardrail_fires:
        post_conviction = _CAPPED_CONVICTION
        capped_reason = f"evidence_quality_not_high_trust:{eq_status}:{eq_trust}"
    else:
        post_conviction = conviction
        capped_reason = ""

    diagnostics: dict = {
        "buy_high_conviction_guardrail_applied": guardrail_fires,
        "buy_conviction_capped_reason": capped_reason,
        "evidence_quality_truth_status": eq_status,
        "evidence_quality_trust_level": eq_trust,
        "pre_guardrail_conviction": conviction.value if guardrail_fires else None,
        "post_guardrail_conviction": post_conviction.value if guardrail_fires else None,
    }

    return post_conviction, diagnostics


def apply_buy_conviction_guardrail_by_band(
    *,
    action: ActionV3,
    conviction: ConvictionV3,
    evidence_quality: AxisBand,
) -> ConvictionV3:
    """Apply the BUY conviction guardrail using AxisBand for the visible decision path.

    Used by _compute_conviction() in decision_policy_v1.py where AxisTruthSummary
    is not available. Semantically equivalent to apply_buy_conviction_guardrail()
    with the AxisBand → trust-level mapping:

      STRONG ↔ PRESENT/HIGH trust  → HIGH conviction allowed (no cap)
      OK     ↔ PRESENT/MEDIUM trust → HIGH conviction capped at MEDIUM
      THIN/SUPPRESSED are already handled by Cap 1 in _compute_conviction

    Fires only when: action == BUY AND conviction == HIGH AND evidence != STRONG.
    SELL/TRIM/HOLD are never affected.
    """
    if (
        action == ActionV3.BUY
        and conviction == ConvictionV3.HIGH
        and evidence_quality != AxisBand.STRONG
    ):
        return _CAPPED_CONVICTION
    return conviction
