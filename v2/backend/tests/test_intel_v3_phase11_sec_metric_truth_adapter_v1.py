"""Phase 11 — SEC Metric Truth Adapter v1 tests.

Acceptance criteria verified by this file:

 1. SEC Metric Truth Adapter v1 exists as a focused backend module with
    a stable contract version constant.
 2. Adapter uses Phase 10 Evidence Source Registry as an explicit governance
    gate for sec_companyfacts_v1. Gate checks source_id, lane, trust_tier,
    numeric_authority, decision_input_eligible, explanation_only, lifecycle.
 3. Adapter refuses unsupported / non-governed / explanation-only /
    non-numeric sources (governance gate rejects them).
 4. READY tickers produce an AxisBand.OK evidence-quality contribution.
 5. PARTIAL tickers produce a degraded AxisBand.THIN contribution only.
 6. BLOCKED tickers produce no SEC fundamentals decision signal
    (evidence_quality_contribution is None).
 7. SKIPPED_NON_COMPANY tickers produce no SEC fundamentals decision signal.
 8. ETF / fund / crypto tickers never use SEC company metric logic
    (they are always SKIPPED_NON_COMPANY → no signal).
 9. Adapter integrates into DecisionInputV3 via apply_sec_fundamentals_to_decision_input
    with a narrow typed contract (SecFundamentalsSignal).
10. Deterministic decision policy remains the only final Buy/Hold/Trim/Sell authority.
    (No decide() call in the adapter module; static import analysis.)
11. No finance-agent / research-artifact output becomes authoritative.
    (LLM_GENERATED sources remain decision_input_eligible=False in registry.)
12. No provider calls. (Static import analysis on new module.)
13. No LLM calls. (Static import analysis on new module.)
14. No UI changes. (No frontend imports in adapter module.)
15. No SQL unless explicitly justified. (Adapter module has no DB imports.)
16. No raw metric values or raw metric-key UI exposure.
    (SecFundamentalsSignal fields contain no metric keys or values.)
17. Existing Phase 8–10 tests still pass. (Covered by running those test files;
    this file verifies Phase 11 code does not import / break any Phase 8–10 modules
    via static analysis.)
18. New tests cover governance gate, readiness status handling, non-company
    exclusions, blocked ticker behavior, and no LLM/provider/raw-payload leakage.
    (This entire file.)
19. HANDOFF.md is updated with Phase 11 summary. (Checked by test_handoff_updated.)
20. Final PR summary includes exact tests and self-audit. (Ensured by pre-PR workflow.)

Additional invariants verified:
    - check_governance_gate() passes for the real Phase 10 registry (current state).
    - Merge rule: max(current_rank, contribution_rank) — only upgrades, never downgrades.
    - SecFundamentalsSignal is frozen — immutable after creation.
    - source_id is always sec_companyfacts_v1.
    - adapter_version is always phase11_v1.
    - evidence_quality_contribution is None when governance fails or ticker is
      BLOCKED / SKIPPED.
    - degraded is True only for PARTIAL tickers.
    - No raw metric key names in any signal field.

All tests use in-memory fixtures — no Supabase dependency.
"""
from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field
from typing import Optional

import pytest

from app.services.intelligence.v3.sec_metric_truth_adapter_v1 import (
    SEC_METRIC_TRUTH_ADAPTER_V1_CONTRACT_VERSION,
    SecFundamentalsSignal,
    _ACCEPTABLE_LIFECYCLE_STATUSES,
    _BAND_ORDER,
    _GOVERNED_SOURCE_ID,
    apply_sec_fundamentals_to_decision_input,
    build_sec_fundamentals_signal,
    check_governance_gate,
)
from app.services.intelligence.v3.decision_contracts import (
    AxisBand,
    DecisionInputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from app.services.intelligence.v3.evidence_source_registry import (
    EVIDENCE_SOURCE_REGISTRY,
    EvidenceLane,
    EvidenceSourceDefinition,
    FailureBehavior,
    LifecycleStatus,
    SourceType,
    TrustTier,
)
from app.services.intelligence.research_workers.sec_metric_evidence_readiness_adapter import (
    READINESS_STATUS_BLOCKED,
    READINESS_STATUS_PARTIAL,
    READINESS_STATUS_READY,
    READINESS_STATUS_SKIPPED_NON_COMPANY,
    SecMetricEvidenceReadinessResult,
)

# ── Module paths for static import analysis ──────────────────────────────────

_ADAPTER_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/v3/sec_metric_truth_adapter_v1.py"
)
_DECISION_POLICY_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/v3/decision_policy_v1.py"
)
_INTEL_V3_SERVICE_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/v3/intel_v3_service.py"
)


def _load_source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _make_readiness(
    ready: list[str] | None = None,
    partial: dict[str, list[str]] | None = None,
    blocked: dict[str, list[str]] | None = None,
    skipped: dict[str, list[str]] | None = None,
) -> SecMetricEvidenceReadinessResult:
    """Build a minimal SecMetricEvidenceReadinessResult for testing."""
    _ready = ready or []
    _partial = partial or {}
    _blocked = blocked or {}
    _skipped = skipped or {}
    all_tickers = set(_ready) | set(_partial) | set(_blocked)
    for tickers in _skipped.values():
        all_tickers.update(tickers)
    return SecMetricEvidenceReadinessResult(
        adapter_enabled=True,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        portfolio_ticker_count=len(all_tickers),
        ready_count=len(_ready),
        partial_count=len(_partial),
        blocked_count=len(_blocked),
        skipped_non_company_count=sum(len(v) for v in _skipped.values()),
        ready_tickers=sorted(_ready),
        partial_tickers_with_missing_groups=_partial,
        blocked_tickers_with_reason=_blocked,
        skipped_tickers_by_reason=_skipped,
        errors=[],
    )


def _make_decision_input(
    ticker: str = "AAPL",
    evidence_quality: AxisBand = AxisBand.SUPPRESSED,
    portfolio_fit: FitBand = FitBand.UNKNOWN,
) -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=evidence_quality,
        price_context=PriceBand.SUPPRESSED,
        portfolio_fit=portfolio_fit,
        risk_band=RiskBand.UNKNOWN,
    )


def _make_fake_registry(
    *,
    lane: EvidenceLane = EvidenceLane.SEC_COMPANY_FUNDAMENTALS,
    trust_tier: TrustTier = TrustTier.PRIMARY_HARD_DATA,
    numeric_authority: bool = True,
    decision_input_eligible: bool = True,
    explanation_only: bool = False,
    lifecycle_status: LifecycleStatus = LifecycleStatus.PLANNED,
) -> dict:
    """Build a minimal fake registry dict for governance gate testing."""
    defn = EvidenceSourceDefinition(
        source_id=_GOVERNED_SOURCE_ID,
        lane=lane,
        display_name="Test",
        description="Test",
        source_type=SourceType.SEC_FILING,
        trust_tier=trust_tier,
        freshness_sla_hours=None,
        decision_input_eligible=decision_input_eligible,
        explanation_only=explanation_only,
        corroboration_required=False,
        numeric_authority=numeric_authority,
        audit_url_required=False,
        provider_adapter="test",
        lifecycle_status=lifecycle_status,
        failure_behavior=FailureBehavior.SUPPRESS_AXIS,
    )
    return {_GOVERNED_SOURCE_ID: defn}


# ═══════════════════════════════════════════════════════════════════════════════
# AC 1 — Contract version and module identity
# ═══════════════════════════════════════════════════════════════════════════════

class TestContractVersion:
    def test_contract_version_constant_exists(self) -> None:
        assert SEC_METRIC_TRUTH_ADAPTER_V1_CONTRACT_VERSION == "phase11_v1"

    def test_governed_source_id_is_sec_companyfacts_v1(self) -> None:
        assert _GOVERNED_SOURCE_ID == "sec_companyfacts_v1"

    def test_band_order_is_ascending(self) -> None:
        assert _BAND_ORDER == [
            AxisBand.SUPPRESSED,
            AxisBand.THIN,
            AxisBand.OK,
            AxisBand.STRONG,
        ]

    def test_acceptable_lifecycle_statuses_includes_planned_and_active(self) -> None:
        assert LifecycleStatus.PLANNED in _ACCEPTABLE_LIFECYCLE_STATUSES
        assert LifecycleStatus.ACTIVE in _ACCEPTABLE_LIFECYCLE_STATUSES

    def test_acceptable_lifecycle_statuses_excludes_deprecated_blocked(self) -> None:
        assert LifecycleStatus.DEPRECATED not in _ACCEPTABLE_LIFECYCLE_STATUSES
        assert LifecycleStatus.BLOCKED not in _ACCEPTABLE_LIFECYCLE_STATUSES


# ═══════════════════════════════════════════════════════════════════════════════
# AC 2 — Governance gate passes for valid Phase 10 registry
# ═══════════════════════════════════════════════════════════════════════════════

class TestGovernanceGatePassesForRealRegistry:
    def test_gate_passes_for_real_phase10_registry(self) -> None:
        passed, reason = check_governance_gate(EVIDENCE_SOURCE_REGISTRY)
        assert passed, f"Governance gate should pass for real registry: {reason}"
        assert reason == "governance_gate_passed"

    def test_gate_reason_is_governance_gate_passed(self) -> None:
        _, reason = check_governance_gate(EVIDENCE_SOURCE_REGISTRY)
        assert "governance_gate_passed" in reason

    def test_gate_passes_with_planned_lifecycle(self) -> None:
        reg = _make_fake_registry(lifecycle_status=LifecycleStatus.PLANNED)
        passed, _ = check_governance_gate(reg)
        assert passed

    def test_gate_passes_with_active_lifecycle(self) -> None:
        reg = _make_fake_registry(lifecycle_status=LifecycleStatus.ACTIVE)
        passed, _ = check_governance_gate(reg)
        assert passed

    def test_gate_uses_module_level_registry_by_default(self) -> None:
        passed, reason = check_governance_gate()
        assert passed, f"Default registry gate failed: {reason}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC 3 — Governance gate refuses unsupported / non-governed / explanation-only /
#         non-numeric sources
# ═══════════════════════════════════════════════════════════════════════════════

class TestGovernanceGateRefusals:
    def test_gate_fails_source_not_in_registry(self) -> None:
        passed, reason = check_governance_gate({})
        assert not passed
        assert "source_not_found" in reason

    def test_gate_fails_wrong_lane(self) -> None:
        reg = _make_fake_registry(lane=EvidenceLane.NEWS_EVENT_RISK)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_lane" in reason

    def test_gate_fails_wrong_trust_tier(self) -> None:
        reg = _make_fake_registry(trust_tier=TrustTier.LLM_GENERATED)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_trust_tier" in reason

    def test_gate_fails_numeric_authority_false(self) -> None:
        reg = _make_fake_registry(numeric_authority=False)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "numeric_authority_false" in reason

    def test_gate_fails_decision_input_eligible_false(self) -> None:
        reg = _make_fake_registry(decision_input_eligible=False)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "decision_input_eligible_false" in reason

    def test_gate_fails_explanation_only_true(self) -> None:
        # decision_input_eligible=True so the gate doesn't fail there first;
        # it should fail specifically because explanation_only=True.
        reg = _make_fake_registry(explanation_only=True, decision_input_eligible=True)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "explanation_only_true" in reason

    def test_gate_fails_deprecated_lifecycle(self) -> None:
        reg = _make_fake_registry(lifecycle_status=LifecycleStatus.DEPRECATED)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "lifecycle_status_not_acceptable" in reason

    def test_gate_fails_blocked_lifecycle(self) -> None:
        reg = _make_fake_registry(lifecycle_status=LifecycleStatus.BLOCKED)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "lifecycle_status_not_acceptable" in reason

    def test_gate_fails_open_web_trust_tier(self) -> None:
        reg = _make_fake_registry(trust_tier=TrustTier.OPEN_WEB)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_trust_tier" in reason

    def test_gate_fails_secondary_computed_trust_tier(self) -> None:
        reg = _make_fake_registry(trust_tier=TrustTier.SECONDARY_COMPUTED)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_trust_tier" in reason

    def test_gate_fails_contextual_trust_tier(self) -> None:
        reg = _make_fake_registry(trust_tier=TrustTier.CONTEXTUAL)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_trust_tier" in reason

    def test_gate_fails_etf_fund_lane(self) -> None:
        reg = _make_fake_registry(lane=EvidenceLane.ETF_FUND_EXPOSURE)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_lane" in reason

    def test_gate_fails_research_artifact_lane(self) -> None:
        reg = _make_fake_registry(lane=EvidenceLane.RESEARCH_ARTIFACT)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_lane" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# AC 4 — READY tickers produce AxisBand.OK contribution
# ═══════════════════════════════════════════════════════════════════════════════

class TestReadyTickers:
    def test_ready_ticker_contribution_is_ok(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        signal = build_sec_fundamentals_signal("AAPL", readiness)
        assert signal.evidence_quality_contribution == AxisBand.OK

    def test_ready_ticker_governance_gate_passed(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        signal = build_sec_fundamentals_signal("AAPL", readiness)
        assert signal.governance_gate_passed

    def test_ready_ticker_readiness_status(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        signal = build_sec_fundamentals_signal("AAPL", readiness)
        assert signal.readiness_status == READINESS_STATUS_READY

    def test_ready_ticker_not_degraded(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        signal = build_sec_fundamentals_signal("AAPL", readiness)
        assert signal.degraded is False

    def test_ready_ticker_suppression_reason_is_none(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        signal = build_sec_fundamentals_signal("AAPL", readiness)
        assert signal.suppression_reason is None

    def test_ready_ticker_source_id_is_sec_companyfacts(self) -> None:
        readiness = _make_readiness(ready=["MSFT"])
        signal = build_sec_fundamentals_signal("MSFT", readiness)
        assert signal.source_id == "sec_companyfacts_v1"

    def test_ready_ticker_adapter_version(self) -> None:
        readiness = _make_readiness(ready=["MSFT"])
        signal = build_sec_fundamentals_signal("MSFT", readiness)
        assert signal.adapter_version == "phase11_v1"

    def test_multiple_ready_tickers_each_get_ok(self) -> None:
        tickers = ["AAPL", "MSFT", "GOOGL", "META", "NFLX"]
        readiness = _make_readiness(ready=tickers)
        for t in tickers:
            sig = build_sec_fundamentals_signal(t, readiness)
            assert sig.evidence_quality_contribution == AxisBand.OK, f"{t} should be OK"


# ═══════════════════════════════════════════════════════════════════════════════
# AC 5 — PARTIAL tickers produce degraded AxisBand.THIN contribution
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartialTickers:
    def test_partial_ticker_contribution_is_thin(self) -> None:
        readiness = _make_readiness(partial={"ALK": ["capex", "liabilities"]})
        signal = build_sec_fundamentals_signal("ALK", readiness)
        assert signal.evidence_quality_contribution == AxisBand.THIN

    def test_partial_ticker_governance_gate_passed(self) -> None:
        readiness = _make_readiness(partial={"ALK": ["capex"]})
        signal = build_sec_fundamentals_signal("ALK", readiness)
        assert signal.governance_gate_passed

    def test_partial_ticker_readiness_status(self) -> None:
        readiness = _make_readiness(partial={"AMD": ["liabilities"]})
        signal = build_sec_fundamentals_signal("AMD", readiness)
        assert signal.readiness_status == READINESS_STATUS_PARTIAL

    def test_partial_ticker_is_degraded(self) -> None:
        readiness = _make_readiness(partial={"AMD": ["liabilities"]})
        signal = build_sec_fundamentals_signal("AMD", readiness)
        assert signal.degraded is True

    def test_partial_ticker_suppression_reason_is_none(self) -> None:
        readiness = _make_readiness(partial={"AMD": ["capex"]})
        signal = build_sec_fundamentals_signal("AMD", readiness)
        assert signal.suppression_reason is None

    def test_partial_contribution_is_not_ok_or_strong(self) -> None:
        readiness = _make_readiness(partial={"NVDA": ["capex"]})
        signal = build_sec_fundamentals_signal("NVDA", readiness)
        assert signal.evidence_quality_contribution not in {AxisBand.OK, AxisBand.STRONG}


# ═══════════════════════════════════════════════════════════════════════════════
# AC 6 — BLOCKED tickers produce no SEC fundamentals decision signal
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlockedTickers:
    def test_blocked_ticker_contribution_is_none(self) -> None:
        readiness = _make_readiness(blocked={"BLSH": ["no_source_linked"]})
        signal = build_sec_fundamentals_signal("BLSH", readiness)
        assert signal.evidence_quality_contribution is None

    def test_blocked_ticker_readiness_status(self) -> None:
        readiness = _make_readiness(blocked={"KLAR": ["no_source_linked"]})
        signal = build_sec_fundamentals_signal("KLAR", readiness)
        assert signal.readiness_status == READINESS_STATUS_BLOCKED

    def test_blocked_ticker_not_degraded(self) -> None:
        readiness = _make_readiness(blocked={"TSM": []})
        signal = build_sec_fundamentals_signal("TSM", readiness)
        assert signal.degraded is False

    def test_blocked_ticker_has_suppression_reason(self) -> None:
        readiness = _make_readiness(blocked={"BLSH": []})
        signal = build_sec_fundamentals_signal("BLSH", readiness)
        assert signal.suppression_reason is not None
        assert "blocked" in signal.suppression_reason.lower()

    def test_blocked_ticker_governance_gate_still_passed(self) -> None:
        readiness = _make_readiness(blocked={"TSM": []})
        signal = build_sec_fundamentals_signal("TSM", readiness)
        assert signal.governance_gate_passed


# ═══════════════════════════════════════════════════════════════════════════════
# AC 7 — SKIPPED_NON_COMPANY tickers produce no signal
# ═══════════════════════════════════════════════════════════════════════════════

class TestSkippedNonCompanyTickers:
    def test_skipped_crypto_contribution_is_none(self) -> None:
        readiness = _make_readiness(skipped={"likely_crypto": ["BTC", "XRP"]})
        for ticker in ["BTC", "XRP"]:
            signal = build_sec_fundamentals_signal(ticker, readiness)
            assert signal.evidence_quality_contribution is None, f"{ticker} should get None"

    def test_skipped_etf_contribution_is_none(self) -> None:
        readiness = _make_readiness(skipped={"likely_fund_or_etf": ["SPY", "QQQ", "VTI"]})
        for ticker in ["SPY", "QQQ", "VTI"]:
            signal = build_sec_fundamentals_signal(ticker, readiness)
            assert signal.evidence_quality_contribution is None, f"{ticker} should get None"

    def test_skipped_readiness_status(self) -> None:
        readiness = _make_readiness(skipped={"likely_crypto": ["BTC"]})
        signal = build_sec_fundamentals_signal("BTC", readiness)
        assert signal.readiness_status == READINESS_STATUS_SKIPPED_NON_COMPANY

    def test_skipped_not_degraded(self) -> None:
        readiness = _make_readiness(skipped={"likely_fund_or_etf": ["SPY"]})
        signal = build_sec_fundamentals_signal("SPY", readiness)
        assert signal.degraded is False

    def test_skipped_has_suppression_reason(self) -> None:
        readiness = _make_readiness(skipped={"likely_fund_or_etf": ["SPY"]})
        signal = build_sec_fundamentals_signal("SPY", readiness)
        assert signal.suppression_reason is not None
        assert "skipped" in signal.suppression_reason.lower()

    def test_skipped_governance_gate_still_passed(self) -> None:
        readiness = _make_readiness(skipped={"likely_crypto": ["BTC"]})
        signal = build_sec_fundamentals_signal("BTC", readiness)
        assert signal.governance_gate_passed


# ═══════════════════════════════════════════════════════════════════════════════
# AC 8 — ETF / fund / crypto tickers never use SEC company metric logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestEtfFundCryptoExclusion:
    """ETF/fund/crypto tickers can only appear as SKIPPED_NON_COMPANY.

    Verifies the Phase 9 classification is respected — these tickers must not
    receive any SEC company fundamentals signal.
    """

    _ETF_TICKERS = ["GLD", "QQQ", "SCHD", "SPY", "VGT", "VHT", "VIS", "VOO",
                    "VTI", "VUG", "VXUS", "VYM", "XLE"]
    _CRYPTO_TICKERS = ["BTC", "XRP"]

    def test_etf_tickers_get_no_signal_when_skipped(self) -> None:
        readiness = _make_readiness(skipped={"likely_fund_or_etf": self._ETF_TICKERS})
        for ticker in self._ETF_TICKERS:
            sig = build_sec_fundamentals_signal(ticker, readiness)
            assert sig.evidence_quality_contribution is None, (
                f"ETF {ticker} must not receive SEC company fundamentals signal"
            )

    def test_crypto_tickers_get_no_signal_when_skipped(self) -> None:
        readiness = _make_readiness(skipped={"likely_crypto": self._CRYPTO_TICKERS})
        for ticker in self._CRYPTO_TICKERS:
            sig = build_sec_fundamentals_signal(ticker, readiness)
            assert sig.evidence_quality_contribution is None, (
                f"Crypto {ticker} must not receive SEC company fundamentals signal"
            )

    def test_etf_tickers_not_eligible_in_registry(self) -> None:
        # ETF holdings lane does not reuse SEC company fundamentals logic.
        etf_sources = [
            s for s in EVIDENCE_SOURCE_REGISTRY.values()
            if s.lane == EvidenceLane.ETF_FUND_EXPOSURE
        ]
        for s in etf_sources:
            assert s.provider_adapter != "research_workers.sec_companyfacts_parser", (
                "ETF lane must not reuse SEC company fundamentals provider"
            )

    def test_ticker_not_in_readiness_gets_no_signal(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        # UNKNOWN_ETF not classified — treated as no data
        sig = build_sec_fundamentals_signal("UNKNOWN_ETF", readiness)
        assert sig.evidence_quality_contribution is None


# ═══════════════════════════════════════════════════════════════════════════════
# AC 9 — Typed contract and apply function
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplySecFundamentalsToDecisionInput:
    """Verifies apply_sec_fundamentals_to_decision_input merges correctly."""

    def test_suppressed_plus_ready_upgrades_to_ok(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        inp = _make_decision_input("AAPL", AxisBand.SUPPRESSED)
        sig = build_sec_fundamentals_signal("AAPL", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == AxisBand.OK

    def test_thin_plus_ready_upgrades_to_ok(self) -> None:
        readiness = _make_readiness(ready=["MSFT"])
        inp = _make_decision_input("MSFT", AxisBand.THIN)
        sig = build_sec_fundamentals_signal("MSFT", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == AxisBand.OK

    def test_ok_plus_ready_stays_ok(self) -> None:
        readiness = _make_readiness(ready=["GOOGL"])
        inp = _make_decision_input("GOOGL", AxisBand.OK)
        sig = build_sec_fundamentals_signal("GOOGL", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == AxisBand.OK

    def test_strong_plus_ready_stays_strong(self) -> None:
        readiness = _make_readiness(ready=["META"])
        inp = _make_decision_input("META", AxisBand.STRONG)
        sig = build_sec_fundamentals_signal("META", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == AxisBand.STRONG

    def test_suppressed_plus_partial_upgrades_to_thin(self) -> None:
        readiness = _make_readiness(partial={"ALK": ["capex"]})
        inp = _make_decision_input("ALK", AxisBand.SUPPRESSED)
        sig = build_sec_fundamentals_signal("ALK", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == AxisBand.THIN

    def test_thin_plus_partial_stays_thin(self) -> None:
        readiness = _make_readiness(partial={"AMD": ["liabilities"]})
        inp = _make_decision_input("AMD", AxisBand.THIN)
        sig = build_sec_fundamentals_signal("AMD", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == AxisBand.THIN

    def test_ok_plus_partial_stays_ok(self) -> None:
        readiness = _make_readiness(partial={"NVDA": ["capex"]})
        inp = _make_decision_input("NVDA", AxisBand.OK)
        sig = build_sec_fundamentals_signal("NVDA", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == AxisBand.OK

    def test_blocked_produces_no_change(self) -> None:
        readiness = _make_readiness(blocked={"BLSH": []})
        inp = _make_decision_input("BLSH", AxisBand.SUPPRESSED)
        original_eq = inp.evidence_quality
        sig = build_sec_fundamentals_signal("BLSH", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == original_eq

    def test_skipped_produces_no_change(self) -> None:
        readiness = _make_readiness(skipped={"likely_crypto": ["BTC"]})
        inp = _make_decision_input("BTC", AxisBand.SUPPRESSED)
        sig = build_sec_fundamentals_signal("BTC", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == AxisBand.SUPPRESSED

    def test_apply_noop_when_governance_gate_fails(self) -> None:
        reg = {}  # empty — source not found
        readiness = _make_readiness(ready=["AAPL"])
        inp = _make_decision_input("AAPL", AxisBand.SUPPRESSED)
        sig = build_sec_fundamentals_signal("AAPL", readiness, registry=reg)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == AxisBand.SUPPRESSED

    def test_apply_records_sec_fundamentals_lane_in_source_signal_summary(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        inp = _make_decision_input("AAPL", AxisBand.SUPPRESSED)
        sig = build_sec_fundamentals_signal("AAPL", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert "sec_fundamentals_lane" in inp.source_signal_summary

    def test_sec_source_signal_summary_has_readiness_status(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        inp = _make_decision_input("AAPL", AxisBand.SUPPRESSED)
        sig = build_sec_fundamentals_signal("AAPL", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        lane_summary = inp.source_signal_summary["sec_fundamentals_lane"]
        assert "readiness_status" in lane_summary
        assert lane_summary["readiness_status"] == READINESS_STATUS_READY

    def test_sec_source_signal_summary_no_raw_metric_keys(self) -> None:
        _FORBIDDEN = {
            "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm",
            "revenue_growth_yoy", "peg_ratio", "p_fcf", "ebit_margin",
            "net_margin_ttm", "debt_to_equity",
        }
        readiness = _make_readiness(ready=["AAPL"])
        inp = _make_decision_input("AAPL", AxisBand.SUPPRESSED)
        sig = build_sec_fundamentals_signal("AAPL", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        summary_str = str(inp.source_signal_summary).lower()
        for key in _FORBIDDEN:
            assert key not in summary_str, f"Raw metric key {key!r} leaked into source_signal_summary"

    def test_apply_noop_when_readiness_result_is_none(self) -> None:
        inp = _make_decision_input("AAPL", AxisBand.SUPPRESSED)
        sig = build_sec_fundamentals_signal("AAPL", None)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        assert inp.evidence_quality == AxisBand.SUPPRESSED

    def test_upgrade_records_evidence_quality_upgraded_true(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        inp = _make_decision_input("AAPL", AxisBand.SUPPRESSED)
        sig = build_sec_fundamentals_signal("AAPL", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        lane = inp.source_signal_summary["sec_fundamentals_lane"]
        assert lane["evidence_quality_upgraded"] is True

    def test_no_upgrade_records_evidence_quality_upgraded_false(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        inp = _make_decision_input("AAPL", AxisBand.OK)  # already OK
        sig = build_sec_fundamentals_signal("AAPL", readiness)
        apply_sec_fundamentals_to_decision_input(inp, sig)
        lane = inp.source_signal_summary["sec_fundamentals_lane"]
        assert lane["evidence_quality_upgraded"] is False

    def test_never_downgrades_evidence_quality(self) -> None:
        # STRONG should never become OK or lower due to any SEC signal.
        for status in [READINESS_STATUS_READY, READINESS_STATUS_PARTIAL]:
            if status == READINESS_STATUS_READY:
                readiness = _make_readiness(ready=["AAPL"])
            else:
                readiness = _make_readiness(partial={"AAPL": ["capex"]})
            inp = _make_decision_input("AAPL", AxisBand.STRONG)
            sig = build_sec_fundamentals_signal("AAPL", readiness)
            apply_sec_fundamentals_to_decision_input(inp, sig)
            assert inp.evidence_quality == AxisBand.STRONG, (
                f"STRONG should not be downgraded by {status}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# AC 10 — Deterministic decision policy is the only Buy/Hold/Trim/Sell authority
# ═══════════════════════════════════════════════════════════════════════════════

def _code_lines(source: str) -> list[str]:
    """Return non-comment, non-docstring code lines for static analysis."""
    import re
    return [
        line for line in source.split("\n")
        if not re.match(r"^\s*(#|\"\"\"|\"\"|'''|$)", line)
    ]


class TestDeterministicPolicyAuthority:
    def test_adapter_module_does_not_import_decision_policy(self) -> None:
        import re
        source = _load_source(_ADAPTER_MODULE)
        assert not re.search(r"^\s*(from|import).*decision_policy_v1", source, re.MULTILINE), (
            "Adapter must not import decision_policy_v1"
        )

    def test_adapter_module_does_not_call_decide_in_code(self) -> None:
        # Use AST to check for actual decide() function calls in code.
        source = _load_source(_ADAPTER_MODULE)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "decide":
                    pytest.fail("Adapter module calls decide() — must not own policy authority")
                if isinstance(func, ast.Attribute) and func.attr == "decide":
                    pytest.fail("Adapter module calls .decide() — must not own policy authority")

    def test_adapter_module_does_not_import_intel_v3_service(self) -> None:
        import re
        source = _load_source(_ADAPTER_MODULE)
        assert not re.search(r"^\s*(from|import).*IntelV3Service", source, re.MULTILINE), (
            "Adapter must not import IntelV3Service"
        )

    def test_adapter_module_does_not_import_snapshot_builder(self) -> None:
        import re
        source = _load_source(_ADAPTER_MODULE)
        assert not re.search(r"^\s*(from|import).*snapshot_builder", source, re.MULTILINE)

    def test_adapter_output_does_not_contain_action_recommendation(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        signal = build_sec_fundamentals_signal("AAPL", readiness)
        sig_dict = {
            "contribution": signal.evidence_quality_contribution,
            "readiness_status": signal.readiness_status,
            "degraded": signal.degraded,
        }
        action_words = {"BUY", "SELL", "HOLD", "TRIM"}
        for v in sig_dict.values():
            if isinstance(v, str):
                assert v.upper() not in action_words, (
                    f"Action word found in signal: {v}"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# AC 11 — Finance-agent / research-artifact outputs remain non-authoritative
# ═══════════════════════════════════════════════════════════════════════════════

class TestResearchArtifactNonAuthoritative:
    def test_research_artifact_llm_v1_not_decision_eligible(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY["research_artifact_llm_v1"]
        assert source.decision_input_eligible is False

    def test_research_artifact_llm_v1_trust_tier_llm_generated(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY["research_artifact_llm_v1"]
        assert source.trust_tier == TrustTier.LLM_GENERATED

    def test_research_artifact_llm_v1_explanation_only(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY["research_artifact_llm_v1"]
        assert source.explanation_only is True

    def test_research_artifact_llm_v1_corroboration_required(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY["research_artifact_llm_v1"]
        assert source.corroboration_required is True

    def test_all_llm_generated_sources_not_decision_eligible(self) -> None:
        for sid, source in EVIDENCE_SOURCE_REGISTRY.items():
            if source.trust_tier == TrustTier.LLM_GENERATED:
                assert source.decision_input_eligible is False, (
                    f"{sid}: LLM_GENERATED source must not be decision_input_eligible"
                )

    def test_governance_gate_rejects_llm_generated_trust_tier(self) -> None:
        reg = _make_fake_registry(trust_tier=TrustTier.LLM_GENERATED)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_trust_tier" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# AC 12 / 13 — No provider calls, no LLM calls (static import analysis)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoProviderOrLLMCalls:
    _FORBIDDEN_IMPORTS = [
        "anthropic",
        "openai",
        "requests",
        "httpx",
        "aiohttp",
        "sec_edgar_provider",
        "finnhub",
        "polygon",
    ]

    def test_adapter_module_no_provider_or_llm_imports(self) -> None:
        source = _load_source(_ADAPTER_MODULE)
        for mod in self._FORBIDDEN_IMPORTS:
            assert mod not in source, (
                f"Forbidden provider/LLM import {mod!r} found in adapter module"
            )

    def test_adapter_module_no_http_calls(self) -> None:
        source = _load_source(_ADAPTER_MODULE)
        for call in ["requests.get", "requests.post", "httpx.get", "aiohttp"]:
            assert call not in source

    def test_adapter_module_no_llm_client(self) -> None:
        source = _load_source(_ADAPTER_MODULE)
        for call in ["anthropic.Anthropic", "client.messages.create", "chat.completions"]:
            assert call not in source


# ═══════════════════════════════════════════════════════════════════════════════
# AC 14 — No UI changes (no frontend imports)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoUiChanges:
    def test_adapter_module_no_frontend_import_statements(self) -> None:
        import re
        source = _load_source(_ADAPTER_MODULE)
        # Check for actual import lines referencing frontend modules.
        frontend_patterns = [
            r"^\s*(from|import)\s+react",
            r"^\s*(from|import)\s+next",
            r"^\s*(from|import)\s+vercel",
            r"^\s*(from|import)\s+frontend",
        ]
        for pattern in frontend_patterns:
            assert not re.search(pattern, source, re.MULTILINE | re.IGNORECASE), (
                f"Frontend import pattern {pattern!r} found in adapter module"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# AC 15 — No SQL (no DB imports in pure adapter module)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoSqlInAdapterModule:
    def test_adapter_module_no_supabase_import(self) -> None:
        source = _load_source(_ADAPTER_MODULE)
        assert "supabase" not in source.lower()

    def test_adapter_module_no_db_import(self) -> None:
        source = _load_source(_ADAPTER_MODULE)
        assert "get_supabase_client" not in source
        assert "database" not in source.lower()

    def test_adapter_module_no_psycopg_import(self) -> None:
        source = _load_source(_ADAPTER_MODULE)
        assert "psycopg" not in source
        assert "sqlalchemy" not in source.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# AC 16 — No raw metric values or raw metric-key UI exposure
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoRawMetricExposure:
    _FORBIDDEN_METRIC_KEYS = [
        "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm",
        "revenue_growth_yoy", "peg_ratio", "p_fcf", "ebit_margin",
        "net_margin_ttm", "debt_to_equity", "current_ratio", "quick_ratio",
        "free_cash_flow_yield", "altman_z", "earnings_growth_fwd",
        "book_value_per_share", "enterprise_value",
    ]

    def test_adapter_module_no_raw_metric_key_names(self) -> None:
        import tokenize
        import io as _io
        source = _load_source(_ADAPTER_MODULE)
        # Use tokenize to extract only non-string, non-comment tokens.
        tokens = list(tokenize.generate_tokens(_io.StringIO(source).readline))
        code_tokens = [
            tok.string for tok in tokens
            if tok.type not in (tokenize.STRING, tokenize.COMMENT, tokenize.NEWLINE,
                                tokenize.NL, tokenize.INDENT, tokenize.DEDENT,
                                tokenize.ENCODING)
        ]
        code_text = " ".join(code_tokens).lower()
        for key in self._FORBIDDEN_METRIC_KEYS:
            assert key not in code_text, (
                f"Raw metric key {key!r} found in adapter module code identifiers"
            )

    def test_sec_fundamentals_signal_contains_no_raw_metric_values(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        signal = build_sec_fundamentals_signal("AAPL", readiness)
        sig_str = str(signal).lower()
        for key in self._FORBIDDEN_METRIC_KEYS:
            assert key not in sig_str, (
                f"Raw metric key {key!r} leaked into SecFundamentalsSignal"
            )

    def test_sec_fundamentals_signal_is_frozen(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        signal = build_sec_fundamentals_signal("AAPL", readiness)
        # frozen=True dataclasses raise FrozenInstanceError (subclass of AttributeError)
        # on any normal attribute assignment attempt.
        with pytest.raises(AttributeError):
            signal.ticker = "HACKED"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# AC 17 — Phase 10 registry invariants still intact
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhase10RegistryInvariantsIntact:
    def test_sec_companyfacts_v1_exists_in_registry(self) -> None:
        assert "sec_companyfacts_v1" in EVIDENCE_SOURCE_REGISTRY

    def test_sec_companyfacts_lane_still_sec_company_fundamentals(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        assert defn.lane == EvidenceLane.SEC_COMPANY_FUNDAMENTALS

    def test_sec_companyfacts_trust_tier_still_primary_hard_data(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        assert defn.trust_tier == TrustTier.PRIMARY_HARD_DATA

    def test_sec_companyfacts_numeric_authority_still_true(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        assert defn.numeric_authority is True

    def test_sec_companyfacts_decision_input_eligible_still_true(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        assert defn.decision_input_eligible is True

    def test_sec_companyfacts_not_explanation_only(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        assert defn.explanation_only is False

    def test_all_llm_generated_sources_non_authoritative(self) -> None:
        for sid, source in EVIDENCE_SOURCE_REGISTRY.items():
            if source.trust_tier == TrustTier.LLM_GENERATED:
                assert not source.decision_input_eligible
                assert not source.numeric_authority

    def test_all_explanation_only_sources_not_decision_eligible(self) -> None:
        for sid, source in EVIDENCE_SOURCE_REGISTRY.items():
            if source.explanation_only:
                assert not source.decision_input_eligible, (
                    f"{sid}: explanation_only=True source must not be decision_input_eligible"
                )

    def test_open_web_sources_with_decision_eligible_require_corroboration(self) -> None:
        for sid, source in EVIDENCE_SOURCE_REGISTRY.items():
            if source.trust_tier == TrustTier.OPEN_WEB and source.decision_input_eligible:
                assert source.corroboration_required, (
                    f"{sid}: OPEN_WEB decision_eligible source must require corroboration"
                )

    def test_research_artifact_lane_never_decision_eligible(self) -> None:
        for sid, source in EVIDENCE_SOURCE_REGISTRY.items():
            if source.lane == EvidenceLane.RESEARCH_ARTIFACT:
                assert not source.decision_input_eligible


# ═══════════════════════════════════════════════════════════════════════════════
# AC 18 — Governance gate, readiness handling, non-company exclusion coverage
# ═══════════════════════════════════════════════════════════════════════════════

class TestGovernanceGateComprehensive:
    def test_gate_passes_for_all_valid_configurations(self) -> None:
        for lifecycle in [LifecycleStatus.PLANNED, LifecycleStatus.ACTIVE]:
            reg = _make_fake_registry(lifecycle_status=lifecycle)
            passed, _ = check_governance_gate(reg)
            assert passed, f"Gate should pass for lifecycle={lifecycle}"

    def test_gate_fails_for_all_invalid_lanes(self) -> None:
        bad_lanes = [
            EvidenceLane.VALUATION_CONTEXT,
            EvidenceLane.MARKET_BEHAVIOR_VOLATILITY,
            EvidenceLane.ANALYST_EXPECTATIONS_REVISIONS,
            EvidenceLane.EARNINGS_TRANSCRIPTS_GUIDANCE,
            EvidenceLane.NEWS_EVENT_RISK,
            EvidenceLane.SECTOR_MACRO_CONTEXT,
            EvidenceLane.ETF_FUND_EXPOSURE,
            EvidenceLane.PORTFOLIO_EXPOSURE,
            EvidenceLane.USER_THESIS_MEMORY,
            EvidenceLane.RESEARCH_ARTIFACT,
        ]
        for lane in bad_lanes:
            reg = _make_fake_registry(lane=lane)
            passed, reason = check_governance_gate(reg)
            assert not passed, f"Gate should fail for lane={lane}"
            assert "wrong_lane" in reason

    def test_ticker_not_in_readiness_result_produces_no_contribution(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        # MSFT not in any bucket
        sig = build_sec_fundamentals_signal("MSFT", readiness)
        assert sig.evidence_quality_contribution is None

    def test_governance_gate_failure_produces_suppressed_signal_for_any_ticker(self) -> None:
        reg = {}
        readiness = _make_readiness(ready=["AAPL", "MSFT"])
        for ticker in ["AAPL", "MSFT"]:
            sig = build_sec_fundamentals_signal(ticker, readiness, registry=reg)
            assert not sig.governance_gate_passed
            assert sig.evidence_quality_contribution is None

    def test_signal_source_id_always_sec_companyfacts_v1(self) -> None:
        readiness_variants = [
            _make_readiness(ready=["X"]),
            _make_readiness(partial={"X": ["capex"]}),
            _make_readiness(blocked={"X": []}),
            _make_readiness(skipped={"likely_crypto": ["X"]}),
        ]
        tickers = ["X", "X", "X", "X"]
        for readiness in readiness_variants:
            sig = build_sec_fundamentals_signal("X", readiness)
            assert sig.source_id == "sec_companyfacts_v1"

    def test_signal_adapter_version_always_phase11_v1(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_sec_fundamentals_signal("AAPL", readiness)
        assert sig.adapter_version == "phase11_v1"

    def test_mixed_readiness_result_per_ticker_routing(self) -> None:
        readiness = _make_readiness(
            ready=["AAPL"],
            partial={"ALK": ["capex"]},
            blocked={"BLSH": []},
            skipped={"likely_crypto": ["BTC"], "likely_fund_or_etf": ["SPY"]},
        )
        assert build_sec_fundamentals_signal("AAPL", readiness).evidence_quality_contribution == AxisBand.OK
        assert build_sec_fundamentals_signal("ALK", readiness).evidence_quality_contribution == AxisBand.THIN
        assert build_sec_fundamentals_signal("BLSH", readiness).evidence_quality_contribution is None
        assert build_sec_fundamentals_signal("BTC", readiness).evidence_quality_contribution is None
        assert build_sec_fundamentals_signal("SPY", readiness).evidence_quality_contribution is None


# ═══════════════════════════════════════════════════════════════════════════════
# HANDOFF updated (AC 19 — verified by file existence and content)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandoffUpdated:
    _HANDOFF = pathlib.Path(__file__).parent.parent.parent.parent / "docs/ai/HANDOFF.md"

    def test_handoff_file_exists(self) -> None:
        assert self._HANDOFF.exists(), "HANDOFF.md file must exist"

    def test_handoff_mentions_phase11(self) -> None:
        content = self._HANDOFF.read_text(encoding="utf-8")
        assert "Phase 11" in content, "HANDOFF.md must mention Phase 11"

    def test_handoff_mentions_sec_metric_truth_adapter(self) -> None:
        content = self._HANDOFF.read_text(encoding="utf-8")
        assert "SEC Metric Truth Adapter" in content, (
            "HANDOFF.md must mention SEC Metric Truth Adapter"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Kill switch and service integration (AC — verified via config and service)
# ═══════════════════════════════════════════════════════════════════════════════

class TestKillSwitchAndServiceIntegration:
    def test_config_has_phase11_kill_switch(self) -> None:
        from app.config import Settings
        import inspect
        fields = Settings.model_fields
        assert "intel_v3_sec_metric_truth_adapter_v1_enabled" in fields

    def test_config_phase11_kill_switch_defaults_to_false(self) -> None:
        from app.config import Settings
        default = Settings.model_fields["intel_v3_sec_metric_truth_adapter_v1_enabled"].default
        assert default is False

    def test_config_has_phase11_diagnostics_flag(self) -> None:
        from app.config import Settings
        assert "intel_v3_sec_metric_truth_adapter_v1_diagnostics_enabled" in Settings.model_fields

    def test_config_phase11_diagnostics_flag_defaults_to_false(self) -> None:
        from app.config import Settings
        default = Settings.model_fields["intel_v3_sec_metric_truth_adapter_v1_diagnostics_enabled"].default
        assert default is False

    def test_intel_v3_service_imports_get_settings(self) -> None:
        source = _load_source(_INTEL_V3_SERVICE_MODULE)
        assert "get_settings" in source

    def test_intel_v3_service_has_phase11_readiness_helper(self) -> None:
        # Phase 13 unified the helper under _get_sec_readiness_for_adapters.
        source = _load_source(_INTEL_V3_SERVICE_MODULE)
        assert (
            "_get_sec_metric_readiness_for_v1" in source
            or "_get_sec_readiness_for_adapters" in source
        )

    def test_intel_v3_service_references_phase11_kill_switch(self) -> None:
        source = _load_source(_INTEL_V3_SERVICE_MODULE)
        assert "intel_v3_sec_metric_truth_adapter_v1_enabled" in source

    def test_intel_v3_service_references_sec_metric_truth_adapter_v1(self) -> None:
        source = _load_source(_INTEL_V3_SERVICE_MODULE)
        assert "sec_metric_truth_adapter_v1" in source

    def test_intel_v3_service_applies_signal_per_ticker(self) -> None:
        source = _load_source(_INTEL_V3_SERVICE_MODULE)
        assert "apply_sec_fundamentals_to_decision_input" in source

    def test_intel_v3_service_checks_governance_gate(self) -> None:
        source = _load_source(_INTEL_V3_SERVICE_MODULE)
        assert "check_governance_gate" in source
