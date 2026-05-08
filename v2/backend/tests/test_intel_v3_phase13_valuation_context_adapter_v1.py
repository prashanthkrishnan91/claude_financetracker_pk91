"""Phase 13 — Valuation Context Adapter v1 tests.

Acceptance criteria verified by this file:

 1. Backend-only valuation context adapter exists with a stable contract version.
 2. Adapter uses Phase 10 Evidence Source Registry as an explicit governance gate
    for valuation_ratio_computed_v1. Gate checks source_id, lane, trust_tier,
    numeric_authority, decision_input_eligible, explanation_only, lifecycle.
 3. Adapter refuses unsupported / non-governed / explanation-only / non-numeric
    sources (governance gate rejects them).
 4. Adapter uses existing stored data only — no new provider calls, no LLM calls.
    (Static import analysis verifies no provider/LLM imports.)
 5. ETF / fund / crypto tickers are always SUPPRESSED_NON_COMPANY.
 6. Missing / stale / weak / conflicting SEC evidence suppresses the lane.
 7. Missing market price suppresses the lane (SUPPRESSED_MISSING_PRICE_OR_POSITION).
 8. READY tickers produce READY_FOR_FUTURE_VALUATION (non-degraded).
    price_context_contribution is ALWAYS None — readiness-only phase.
 9. PARTIAL tickers produce PARTIAL_FOR_FUTURE_VALUATION (degraded=True).
    price_context_contribution is ALWAYS None — readiness-only phase.
10. Phase 13 NEVER changes DecisionInputV3.price_context for any signal status.
    apply_valuation_context_to_decision_input() is record-only.
11. Deterministic decision policy remains the only final Buy/Hold/Trim/Sell authority.
    (No decide() call in adapter module — static import analysis.)
12. No new provider calls. (Static import analysis on new module.)
13. No LLM calls. (Static import analysis on new module.)
14. No UI changes. (No frontend imports in adapter module.)
15. No raw valuation metric keys or values leak into output.
    (ValuationContextSignal fields contain no metric keys/values/price targets.)
16. ValuationContextSignal is frozen (immutable after creation).
17. source_id is always valuation_ratio_computed_v1.
18. adapter_version is always phase13_v1.
19. price_context_contribution is None for ALL statuses (readiness-only phase).
20. No HOLD→BUY action drift is possible from Phase 13 signals.
    (price_context is never upgraded by this adapter.)
21. source_signal_summary records readiness_only=True and price_context_unchanged=True.
22. Governance gate passes for the real Phase 10 registry (current state).
23. Governance gate rejects wrong lane, wrong trust_tier, explanation_only=True,
    decision_input_eligible=False, numeric_authority=False, bad lifecycle.
24. HANDOFF.md is updated with Phase 13 summary. (Checked separately.)
25. Existing Phase 10/11 tests still pass. (No adapter module imports break them.)

All tests use in-memory fixtures — no Supabase dependency.
"""
from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field
from typing import Optional

import pytest

from app.services.intelligence.v3.valuation_context_adapter_v1 import (
    VALUATION_CONTEXT_ADAPTER_V1_CONTRACT_VERSION,
    ValuationContextSignal,
    ValuationSignalStatus,
    _ACCEPTABLE_LIFECYCLE_STATUSES,
    _GOVERNED_SOURCE_ID,
    _KNOWN_CRYPTO_TICKERS,
    _NON_COMPANY_CATEGORY_KEYWORDS,
    _is_non_company_ticker,
    apply_valuation_context_to_decision_input,
    build_valuation_context_signal,
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
    / "app/services/intelligence/v3/valuation_context_adapter_v1.py"
)
_INTEL_V3_SERVICE_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/v3/intel_v3_service.py"
)
_DECISION_POLICY_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/v3/decision_policy_v1.py"
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
    price_context: PriceBand = PriceBand.SUPPRESSED,
    evidence_quality: AxisBand = AxisBand.OK,
) -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=evidence_quality,
        price_context=price_context,
        portfolio_fit=FitBand.UNKNOWN,
        risk_band=RiskBand.UNKNOWN,
    )


def _make_fake_registry(
    *,
    lane: EvidenceLane = EvidenceLane.VALUATION_CONTEXT,
    trust_tier: TrustTier = TrustTier.SECONDARY_COMPUTED,
    numeric_authority: bool = True,
    decision_input_eligible: bool = True,
    explanation_only: bool = False,
    lifecycle_status: LifecycleStatus = LifecycleStatus.PLANNED,
) -> dict:
    defn = EvidenceSourceDefinition(
        source_id=_GOVERNED_SOURCE_ID,
        lane=lane,
        display_name="Test Valuation",
        description="Test",
        source_type=SourceType.MARKET_DATA,
        trust_tier=trust_tier,
        freshness_sla_hours=24,
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
    def test_contract_version_is_phase13_v1(self) -> None:
        assert VALUATION_CONTEXT_ADAPTER_V1_CONTRACT_VERSION == "phase13_v1"

    def test_governed_source_id_is_valuation_ratio_computed_v1(self) -> None:
        assert _GOVERNED_SOURCE_ID == "valuation_ratio_computed_v1"

    def test_acceptable_lifecycle_statuses_includes_planned(self) -> None:
        assert LifecycleStatus.PLANNED in _ACCEPTABLE_LIFECYCLE_STATUSES

    def test_acceptable_lifecycle_statuses_includes_active(self) -> None:
        assert LifecycleStatus.ACTIVE in _ACCEPTABLE_LIFECYCLE_STATUSES

    def test_acceptable_lifecycle_statuses_excludes_deprecated(self) -> None:
        assert LifecycleStatus.DEPRECATED not in _ACCEPTABLE_LIFECYCLE_STATUSES

    def test_acceptable_lifecycle_statuses_excludes_blocked(self) -> None:
        assert LifecycleStatus.BLOCKED not in _ACCEPTABLE_LIFECYCLE_STATUSES

    def test_non_company_keywords_includes_etf(self) -> None:
        assert "etf" in _NON_COMPANY_CATEGORY_KEYWORDS

    def test_non_company_keywords_includes_crypto(self) -> None:
        assert "crypto" in _NON_COMPANY_CATEGORY_KEYWORDS

    def test_non_company_keywords_includes_fund(self) -> None:
        assert "fund" in _NON_COMPANY_CATEGORY_KEYWORDS

    def test_known_crypto_tickers_includes_btc(self) -> None:
        assert "BTC" in _KNOWN_CRYPTO_TICKERS

    def test_status_enum_has_ready_for_future_valuation(self) -> None:
        assert ValuationSignalStatus.READY_FOR_FUTURE_VALUATION

    def test_status_enum_has_partial_for_future_valuation(self) -> None:
        assert ValuationSignalStatus.PARTIAL_FOR_FUTURE_VALUATION

    def test_status_enum_has_suppressed_missing_price_or_position(self) -> None:
        assert ValuationSignalStatus.SUPPRESSED_MISSING_PRICE_OR_POSITION

    def test_status_enum_has_suppressed_missing_fundamentals(self) -> None:
        assert ValuationSignalStatus.SUPPRESSED_MISSING_FUNDAMENTALS

    def test_status_enum_has_suppressed_non_company(self) -> None:
        assert ValuationSignalStatus.SUPPRESSED_NON_COMPANY

    def test_status_enum_has_governance_blocked(self) -> None:
        assert ValuationSignalStatus.GOVERNANCE_BLOCKED


# ═══════════════════════════════════════════════════════════════════════════════
# AC 2 — Governance gate passes for real Phase 10 registry
# ═══════════════════════════════════════════════════════════════════════════════

class TestGovernanceGatePassesForRealRegistry:
    def test_gate_passes_for_real_phase10_registry(self) -> None:
        passed, reason = check_governance_gate(EVIDENCE_SOURCE_REGISTRY)
        assert passed, f"Gate should pass for real registry: {reason}"
        assert reason == "governance_gate_passed"

    def test_gate_uses_module_level_registry_by_default(self) -> None:
        passed, reason = check_governance_gate()
        assert passed, f"Default registry gate failed: {reason}"

    def test_gate_passes_with_planned_lifecycle(self) -> None:
        reg = _make_fake_registry(lifecycle_status=LifecycleStatus.PLANNED)
        passed, _ = check_governance_gate(reg)
        assert passed

    def test_gate_passes_with_active_lifecycle(self) -> None:
        reg = _make_fake_registry(lifecycle_status=LifecycleStatus.ACTIVE)
        passed, _ = check_governance_gate(reg)
        assert passed

    def test_valuation_ratio_computed_v1_in_real_registry(self) -> None:
        assert _GOVERNED_SOURCE_ID in EVIDENCE_SOURCE_REGISTRY

    def test_valuation_ratio_lane_is_valuation_context(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY[_GOVERNED_SOURCE_ID]
        assert source.lane == EvidenceLane.VALUATION_CONTEXT

    def test_valuation_ratio_trust_tier_is_secondary_computed(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY[_GOVERNED_SOURCE_ID]
        assert source.trust_tier == TrustTier.SECONDARY_COMPUTED

    def test_valuation_ratio_decision_input_eligible(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY[_GOVERNED_SOURCE_ID]
        assert source.decision_input_eligible is True

    def test_valuation_ratio_not_explanation_only(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY[_GOVERNED_SOURCE_ID]
        assert source.explanation_only is False

    def test_valuation_ratio_numeric_authority(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY[_GOVERNED_SOURCE_ID]
        assert source.numeric_authority is True


# ═══════════════════════════════════════════════════════════════════════════════
# AC 3 — Governance gate rejects unsupported / non-governed sources
# ═══════════════════════════════════════════════════════════════════════════════

class TestGovernanceGateRejects:
    def test_gate_fails_source_not_found(self) -> None:
        passed, reason = check_governance_gate({})
        assert not passed
        assert "source_not_found" in reason

    def test_gate_fails_wrong_lane(self) -> None:
        reg = _make_fake_registry(lane=EvidenceLane.SEC_COMPANY_FUNDAMENTALS)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_lane" in reason

    def test_gate_fails_wrong_trust_tier(self) -> None:
        reg = _make_fake_registry(trust_tier=TrustTier.PRIMARY_HARD_DATA)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_trust_tier" in reason

    def test_gate_fails_llm_generated_trust_tier(self) -> None:
        reg = _make_fake_registry(trust_tier=TrustTier.LLM_GENERATED)
        passed, reason = check_governance_gate(reg)
        assert not passed
        assert "wrong_trust_tier" in reason

    def test_gate_fails_open_web_trust_tier(self) -> None:
        reg = _make_fake_registry(trust_tier=TrustTier.OPEN_WEB)
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
        # decision_input_eligible must remain True so the gate reaches explanation_only check.
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

    def test_gate_returns_none_registry_gracefully(self) -> None:
        passed, reason = check_governance_gate(None)
        # Should use module-level registry and pass
        assert passed

    def test_gate_reason_contains_governance_gate_passed_on_success(self) -> None:
        reg = _make_fake_registry()
        _, reason = check_governance_gate(reg)
        assert "governance_gate_passed" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# AC 5 — ETF / fund / crypto tickers are always SUPPRESSED_NON_COMPANY
# ═══════════════════════════════════════════════════════════════════════════════

class TestNonCompanyExclusion:
    def test_is_non_company_etf_category(self) -> None:
        assert _is_non_company_ticker("VTI", "etf") is True

    def test_is_non_company_fund_category(self) -> None:
        assert _is_non_company_ticker("FXAIX", "fund") is True

    def test_is_non_company_crypto_category(self) -> None:
        assert _is_non_company_ticker("SOME", "crypto") is True

    def test_is_non_company_crypto_ticker_btc(self) -> None:
        assert _is_non_company_ticker("BTC", "stock") is True

    def test_is_non_company_crypto_ticker_eth(self) -> None:
        assert _is_non_company_ticker("ETH", None) is True

    def test_is_non_company_index_category(self) -> None:
        assert _is_non_company_ticker("SPY", "index") is True

    def test_is_not_non_company_regular_stock(self) -> None:
        assert _is_non_company_ticker("AAPL", "stock") is False

    def test_is_not_non_company_no_category(self) -> None:
        assert _is_non_company_ticker("MSFT", None) is False

    def test_etf_ticker_builds_suppressed_non_company_signal(self) -> None:
        readiness = _make_readiness(ready=["VTI"])
        sig = build_valuation_context_signal(
            ticker="VTI", category="etf",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.status == ValuationSignalStatus.SUPPRESSED_NON_COMPANY
        assert sig.price_context_contribution is None
        assert sig.degraded is False

    def test_crypto_ticker_builds_suppressed_non_company_signal(self) -> None:
        readiness = _make_readiness()
        sig = build_valuation_context_signal(
            ticker="BTC", category="crypto",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.status == ValuationSignalStatus.SUPPRESSED_NON_COMPANY
        assert sig.price_context_contribution is None

    def test_fund_category_builds_suppressed_non_company_signal(self) -> None:
        readiness = _make_readiness(ready=["FXAIX"])
        sig = build_valuation_context_signal(
            ticker="FXAIX", category="fund",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.status == ValuationSignalStatus.SUPPRESSED_NON_COMPANY
        assert sig.price_context_contribution is None

    def test_sec_skipped_ticker_builds_suppressed_non_company(self) -> None:
        readiness = _make_readiness(skipped={"etf_fund": ["QQQ"]})
        sig = build_valuation_context_signal(
            ticker="QQQ", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.status == ValuationSignalStatus.SUPPRESSED_NON_COMPANY
        assert sig.price_context_contribution is None

    def test_non_company_signal_governance_gate_passed_true(self) -> None:
        readiness = _make_readiness()
        sig = build_valuation_context_signal(
            ticker="VTI", category="etf",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.governance_gate_passed is True


# ═══════════════════════════════════════════════════════════════════════════════
# AC 6 — Missing / stale / weak SEC fundamentals suppresses the lane
# ═══════════════════════════════════════════════════════════════════════════════

class TestFundamentalsSuppression:
    def test_none_sec_readiness_suppresses(self) -> None:
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=None, has_market_price=True,
        )
        assert sig.status == ValuationSignalStatus.SUPPRESSED_MISSING_FUNDAMENTALS
        assert sig.price_context_contribution is None

    def test_blocked_ticker_suppresses(self) -> None:
        readiness = _make_readiness(blocked={"BLSH": ["MISSING_SEC"]})
        sig = build_valuation_context_signal(
            ticker="BLSH", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.status == ValuationSignalStatus.SUPPRESSED_MISSING_FUNDAMENTALS
        assert sig.price_context_contribution is None

    def test_ticker_not_in_readiness_suppresses(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="MSFT", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.status == ValuationSignalStatus.SUPPRESSED_MISSING_FUNDAMENTALS
        assert sig.price_context_contribution is None

    def test_blocked_ticker_suppression_reason_contains_blocked(self) -> None:
        readiness = _make_readiness(blocked={"TSM": ["reason"]})
        sig = build_valuation_context_signal(
            ticker="TSM", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.suppression_reason is not None
        assert "blocked" in sig.suppression_reason

    def test_missing_fundamentals_contribution_is_none(self) -> None:
        readiness = _make_readiness()
        sig = build_valuation_context_signal(
            ticker="NVDA", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution is None


# ═══════════════════════════════════════════════════════════════════════════════
# AC 7 — Missing market price suppresses the lane (SUPPRESSED_MISSING_PRICE_OR_POSITION)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingPriceSuppression:
    def test_no_market_price_suppresses(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=False,
        )
        assert sig.status == ValuationSignalStatus.SUPPRESSED_MISSING_PRICE_OR_POSITION
        assert sig.price_context_contribution is None

    def test_no_price_suppression_reason_present(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=False,
        )
        assert sig.suppression_reason is not None
        assert "price" in sig.suppression_reason.lower()

    def test_no_price_governance_gate_still_passes(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=False,
        )
        assert sig.governance_gate_passed is True

    def test_no_price_degraded_false(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=False,
        )
        assert sig.degraded is False

    def test_suppressed_missing_price_or_position_status_value(self) -> None:
        assert (
            ValuationSignalStatus.SUPPRESSED_MISSING_PRICE_OR_POSITION.value
            == "SUPPRESSED_MISSING_PRICE_OR_POSITION"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC 8 — READY tickers produce READY_FOR_FUTURE_VALUATION (readiness-only)
# ═══════════════════════════════════════════════════════════════════════════════

class TestReadySignal:
    def test_ready_ticker_status_is_ready_for_future_valuation(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.status == ValuationSignalStatus.READY_FOR_FUTURE_VALUATION

    def test_ready_ticker_contribution_is_none(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution is None

    def test_ready_ticker_degraded_false(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.degraded is False

    def test_ready_ticker_suppression_reason_is_none(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.suppression_reason is None

    def test_ready_ticker_governance_gate_passed(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.governance_gate_passed is True

    def test_ready_ticker_contribution_not_cheap(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution != PriceBand.CHEAP

    def test_ready_ticker_contribution_not_fair(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution != PriceBand.FAIR

    def test_ready_ticker_contribution_not_full(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution != PriceBand.FULL

    def test_ready_ticker_contribution_not_expensive(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution != PriceBand.EXPENSIVE

    def test_ready_for_future_valuation_status_value(self) -> None:
        assert (
            ValuationSignalStatus.READY_FOR_FUTURE_VALUATION.value
            == "READY_FOR_FUTURE_VALUATION"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC 9 — PARTIAL tickers produce PARTIAL_FOR_FUTURE_VALUATION (degraded=True)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPartialSignal:
    def test_partial_ticker_status_is_partial_for_future_valuation(self) -> None:
        readiness = _make_readiness(partial={"MSFT": ["eps"]})
        sig = build_valuation_context_signal(
            ticker="MSFT", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.status == ValuationSignalStatus.PARTIAL_FOR_FUTURE_VALUATION

    def test_partial_ticker_contribution_is_none(self) -> None:
        readiness = _make_readiness(partial={"MSFT": ["eps"]})
        sig = build_valuation_context_signal(
            ticker="MSFT", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution is None

    def test_partial_ticker_degraded_true(self) -> None:
        readiness = _make_readiness(partial={"MSFT": ["eps"]})
        sig = build_valuation_context_signal(
            ticker="MSFT", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.degraded is True

    def test_partial_ticker_suppression_reason_is_none(self) -> None:
        readiness = _make_readiness(partial={"MSFT": ["eps"]})
        sig = build_valuation_context_signal(
            ticker="MSFT", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.suppression_reason is None

    def test_partial_contribution_not_cheap(self) -> None:
        readiness = _make_readiness(partial={"MSFT": ["eps"]})
        sig = build_valuation_context_signal(
            ticker="MSFT", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution != PriceBand.CHEAP

    def test_partial_contribution_not_fair(self) -> None:
        readiness = _make_readiness(partial={"MSFT": ["eps"]})
        sig = build_valuation_context_signal(
            ticker="MSFT", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution != PriceBand.FAIR

    def test_partial_contribution_not_full(self) -> None:
        readiness = _make_readiness(partial={"MSFT": ["eps"]})
        sig = build_valuation_context_signal(
            ticker="MSFT", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution != PriceBand.FULL

    def test_partial_for_future_valuation_status_value(self) -> None:
        assert (
            ValuationSignalStatus.PARTIAL_FOR_FUTURE_VALUATION.value
            == "PARTIAL_FOR_FUTURE_VALUATION"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC 10 — Governance blocked produces no contribution
# ═══════════════════════════════════════════════════════════════════════════════

class TestGovernanceBlockedSignal:
    def test_governance_blocked_status(self) -> None:
        reg = _make_fake_registry(lane=EvidenceLane.NEWS_EVENT_RISK)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
            registry=reg,
        )
        assert sig.status == ValuationSignalStatus.GOVERNANCE_BLOCKED

    def test_governance_blocked_contribution_is_none(self) -> None:
        reg = _make_fake_registry(decision_input_eligible=False)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
            registry=reg,
        )
        assert sig.price_context_contribution is None

    def test_governance_blocked_gate_passed_false(self) -> None:
        reg = _make_fake_registry(explanation_only=True, decision_input_eligible=False)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
            registry=reg,
        )
        assert sig.governance_gate_passed is False

    def test_governance_blocked_reason_contains_gate_failed(self) -> None:
        reg = {}
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
            registry=reg,
        )
        assert sig.suppression_reason is not None
        assert "governance_gate_failed" in sig.suppression_reason


# ═══════════════════════════════════════════════════════════════════════════════
# AC 10 / AC 20 — price_context is NEVER changed by Phase 13 (record-only)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoPriceContextChange:
    """Phase 13 is readiness-only. apply_valuation_context_to_decision_input()
    must NEVER modify DecisionInputV3.price_context regardless of signal status."""

    def _make_ready_signal(self, ticker: str = "AAPL") -> ValuationContextSignal:
        readiness = _make_readiness(ready=[ticker])
        return build_valuation_context_signal(
            ticker=ticker, category="stock",
            sec_readiness=readiness, has_market_price=True,
        )

    def _make_partial_signal(self, ticker: str = "AAPL") -> ValuationContextSignal:
        readiness = _make_readiness(partial={ticker: ["eps"]})
        return build_valuation_context_signal(
            ticker=ticker, category="stock",
            sec_readiness=readiness, has_market_price=True,
        )

    def test_suppressed_input_stays_suppressed_after_ready_signal(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        sig = self._make_ready_signal()
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.SUPPRESSED

    def test_suppressed_input_stays_suppressed_after_partial_signal(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        sig = self._make_partial_signal()
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.SUPPRESSED

    def test_fair_input_stays_fair_after_ready_signal(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.FAIR)
        sig = self._make_ready_signal()
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.FAIR

    def test_cheap_input_stays_cheap_after_ready_signal(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.CHEAP)
        sig = self._make_ready_signal()
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.CHEAP

    def test_full_input_stays_full_after_ready_signal(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.FULL)
        sig = self._make_ready_signal()
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.FULL

    def test_expensive_input_stays_expensive_after_ready_signal(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.EXPENSIVE)
        sig = self._make_ready_signal()
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.EXPENSIVE

    def test_suppressed_input_unchanged_after_suppressed_signal(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=None, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.SUPPRESSED

    def test_price_context_unchanged_after_governance_blocked(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        reg = {}
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=_make_readiness(ready=["AAPL"]),
            has_market_price=True,
            registry=reg,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.SUPPRESSED

    def test_price_context_unchanged_after_non_company_signal(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        sig = build_valuation_context_signal(
            ticker="VTI", category="etf",
            sec_readiness=_make_readiness(), has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.SUPPRESSED

    def test_price_context_unchanged_after_missing_price_signal(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=_make_readiness(ready=["AAPL"]),
            has_market_price=False,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.SUPPRESSED


# ═══════════════════════════════════════════════════════════════════════════════
# AC 20 — No HOLD→BUY action drift is possible from Phase 13
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoActionDrift:
    """Phase 13 produces no PriceBand contribution. The decision policy BUY rule
    requires price_context in {CHEAP, FAIR} (or SUPPRESSED + STRONG evidence).
    Since Phase 13 never changes price_context, it can never cause action drift."""

    def _apply_ready_signal(self, inp: DecisionInputV3) -> None:
        readiness = _make_readiness(ready=[inp.ticker])
        sig = build_valuation_context_signal(
            ticker=inp.ticker, category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)

    def test_hold_ticker_price_context_unchanged_cannot_drift_to_buy(self) -> None:
        inp = _make_decision_input(
            ticker="AAPL",
            price_context=PriceBand.SUPPRESSED,
            evidence_quality=AxisBand.THIN,
        )
        original_price_context = inp.price_context
        self._apply_ready_signal(inp)
        assert inp.price_context == original_price_context

    def test_all_ready_tickers_contribute_none_price_context(self) -> None:
        tickers = ["AAPL", "MSFT", "GOOG", "AMZN"]
        readiness = _make_readiness(ready=tickers)
        for ticker in tickers:
            sig = build_valuation_context_signal(
                ticker=ticker, category="stock",
                sec_readiness=readiness, has_market_price=True,
            )
            assert sig.price_context_contribution is None, (
                f"READY ticker {ticker} should not contribute price_context"
            )

    def test_all_partial_tickers_contribute_none_price_context(self) -> None:
        readiness = _make_readiness(partial={"AAPL": ["eps"], "MSFT": ["revenue"]})
        for ticker in ["AAPL", "MSFT"]:
            sig = build_valuation_context_signal(
                ticker=ticker, category="stock",
                sec_readiness=readiness, has_market_price=True,
            )
            assert sig.price_context_contribution is None, (
                f"PARTIAL ticker {ticker} should not contribute price_context"
            )

    def test_apply_does_not_introduce_fair_into_suppressed_input(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context != PriceBand.FAIR
        assert inp.price_context == PriceBand.SUPPRESSED

    def test_apply_does_not_introduce_cheap_into_any_input(self) -> None:
        for initial in [PriceBand.SUPPRESSED, PriceBand.FULL, PriceBand.EXPENSIVE]:
            inp = _make_decision_input(price_context=initial)
            readiness = _make_readiness(ready=["AAPL"])
            sig = build_valuation_context_signal(
                ticker="AAPL", category="stock",
                sec_readiness=readiness, has_market_price=True,
            )
            apply_valuation_context_to_decision_input(inp, sig)
            assert inp.price_context != PriceBand.CHEAP
            assert inp.price_context == initial


# ═══════════════════════════════════════════════════════════════════════════════
# AC 21 — source_signal_summary records readiness_only and price_context_unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestSourceSignalSummary:
    def test_ready_signal_records_valuation_context_lane(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        assert "valuation_context_lane" in inp.source_signal_summary

    def test_suppressed_signal_records_valuation_context_lane(self) -> None:
        inp = _make_decision_input()
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=None, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        assert "valuation_context_lane" in inp.source_signal_summary

    def test_summary_status_matches_signal_status(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        lane_summary = inp.source_signal_summary["valuation_context_lane"]
        assert lane_summary["status"] == ValuationSignalStatus.READY_FOR_FUTURE_VALUATION.value

    def test_summary_records_readiness_only_true(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        lane = inp.source_signal_summary["valuation_context_lane"]
        assert lane["readiness_only"] is True

    def test_summary_records_price_context_unchanged_true(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        lane = inp.source_signal_summary["valuation_context_lane"]
        assert lane["price_context_unchanged"] is True

    def test_summary_price_context_contribution_is_none(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        lane = inp.source_signal_summary["valuation_context_lane"]
        assert lane["price_context_contribution"] is None

    def test_summary_records_price_context_unchanged_true_for_suppressed(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=None, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        lane = inp.source_signal_summary["valuation_context_lane"]
        assert lane["price_context_unchanged"] is True

    def test_summary_records_source_id(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        lane = inp.source_signal_summary["valuation_context_lane"]
        assert lane["source_id"] == _GOVERNED_SOURCE_ID

    def test_summary_records_adapter_version(self) -> None:
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        lane = inp.source_signal_summary["valuation_context_lane"]
        assert lane["adapter_version"] == VALUATION_CONTEXT_ADAPTER_V1_CONTRACT_VERSION

    def test_summary_no_raw_metric_keys_in_lane(self) -> None:
        forbidden = {
            "pe_ratio", "pb_ratio", "ev_ebitda", "eps", "book_value",
            "price_per_share", "earnings_per_share", "equity",
        }
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        lane = inp.source_signal_summary.get("valuation_context_lane", {})
        for key in forbidden:
            assert key not in lane, f"Forbidden key {key!r} found in lane summary"
            for v in lane.values():
                if isinstance(v, str):
                    assert key not in v.lower(), f"Forbidden key {key!r} found in value {v!r}"

    def test_summary_no_price_context_upgraded_key(self) -> None:
        """Phase 13 never upgrades price_context — the old key must not exist."""
        inp = _make_decision_input(price_context=PriceBand.SUPPRESSED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        lane = inp.source_signal_summary["valuation_context_lane"]
        assert "price_context_upgraded" not in lane


# ═══════════════════════════════════════════════════════════════════════════════
# AC 15–18 — Signal contract invariants (immutability, fields, no raw values)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalContractInvariants:
    def test_signal_is_frozen(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        with pytest.raises((AttributeError, TypeError)):
            sig.ticker = "MSFT"  # type: ignore[misc]

    def test_source_id_always_governed_source(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.source_id == "valuation_ratio_computed_v1"

    def test_adapter_version_always_phase13_v1(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.adapter_version == "phase13_v1"

    def test_contribution_none_for_suppressed_missing_price_or_position(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=False,
        )
        assert sig.price_context_contribution is None

    def test_contribution_none_for_governance_blocked(self) -> None:
        reg = _make_fake_registry(lifecycle_status=LifecycleStatus.DEPRECATED)
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
            registry=reg,
        )
        assert sig.price_context_contribution is None

    def test_contribution_none_for_suppressed_non_company(self) -> None:
        sig = build_valuation_context_signal(
            ticker="VTI", category="etf",
            sec_readiness=_make_readiness(), has_market_price=True,
        )
        assert sig.price_context_contribution is None

    def test_contribution_none_for_ready_signal(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution is None

    def test_contribution_none_for_partial_signal(self) -> None:
        readiness = _make_readiness(partial={"AAPL": ["eps"]})
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.price_context_contribution is None

    def test_no_raw_numeric_values_in_signal_fields(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        fields_to_check = [sig.suppression_reason, sig.source_id, sig.adapter_version]
        for val in fields_to_check:
            if val is not None:
                assert not any(c.isdigit() and c not in "13" for c in val[:5]), \
                    f"Unexpected numeric content in signal field: {val!r}"

    def test_signal_ticker_matches_input(self) -> None:
        readiness = _make_readiness(ready=["NVDA"])
        sig = build_valuation_context_signal(
            ticker="NVDA", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.ticker == "NVDA"

    def test_degraded_false_for_ready_for_future_valuation(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.status == ValuationSignalStatus.READY_FOR_FUTURE_VALUATION
        assert sig.degraded is False

    def test_degraded_true_only_for_partial_for_future_valuation(self) -> None:
        readiness = _make_readiness(partial={"AAPL": ["eps"]})
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        assert sig.degraded is True
        assert sig.status == ValuationSignalStatus.PARTIAL_FOR_FUTURE_VALUATION


# ═══════════════════════════════════════════════════════════════════════════════
# AC 11–14 — Static import analysis: no decide(), no LLM, no provider, no UI
# ═══════════════════════════════════════════════════════════════════════════════

class TestStaticImportAnalysis:
    def _get_imports(self, source: str) -> list[str]:
        """Extract all imported names from a Python source string."""
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.append(module)
                for alias in node.names:
                    imports.append(f"{module}.{alias.name}")
        return imports

    def test_adapter_does_not_import_decide(self) -> None:
        src = _load_source(_ADAPTER_MODULE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []):
                    assert alias.name != "decide", "decide() must not be imported"

    def test_adapter_does_not_import_decision_policy(self) -> None:
        src = _load_source(_ADAPTER_MODULE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "decision_policy_v1" not in module, \
                    f"decision_policy_v1 must not be imported: {module}"

    def test_adapter_does_not_import_frontend_modules(self) -> None:
        src = _load_source(_ADAPTER_MODULE)
        tree = ast.parse(src)
        frontend_patterns = ["frontend", "react", "nextjs", "components"]
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                for alias in getattr(node, "names", []):
                    full = f"{module}.{alias.name}".lower()
                    for pattern in frontend_patterns:
                        assert pattern not in full, \
                            f"Frontend import found: {full!r}"

    def test_adapter_does_not_import_llm_modules(self) -> None:
        src = _load_source(_ADAPTER_MODULE)
        tree = ast.parse(src)
        llm_modules = ["anthropic", "openai", "langchain"]
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                for alias in getattr(node, "names", []):
                    full = f"{module}.{alias.name}".lower()
                    for pattern in llm_modules:
                        assert pattern not in full, \
                            f"LLM import found: {full!r}"

    def test_adapter_does_not_import_provider_modules(self) -> None:
        src = _load_source(_ADAPTER_MODULE)
        tree = ast.parse(src)
        provider_modules = [
            "sec_edgar_provider", "polygon", "finnhub", "alpha_vantage",
            "requests", "httpx", "aiohttp",
        ]
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                for alias in getattr(node, "names", []):
                    full = f"{module}.{alias.name}".lower()
                    for pattern in provider_modules:
                        assert pattern not in full, \
                            f"Provider import found: {full!r}"

    def test_adapter_does_not_import_database(self) -> None:
        src = _load_source(_ADAPTER_MODULE)
        tree = ast.parse(src)
        db_modules = ["supabase", "database", "get_supabase_client"]
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                for alias in getattr(node, "names", []):
                    full = f"{module}.{alias.name}".lower()
                    for pattern in db_modules:
                        assert pattern not in full, \
                            f"DB import found: {full!r}"

    def test_adapter_does_not_set_safe_for_decision_true(self) -> None:
        src = _load_source(_ADAPTER_MODULE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        if target.attr == "safe_for_decision":
                            if isinstance(node.value, ast.Constant):
                                assert node.value.value is not True, \
                                    "safe_for_decision must never be set to True"

    def test_adapter_does_not_write_to_snapshots(self) -> None:
        src = _load_source(_ADAPTER_MODULE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                assert "snapshots" not in module

    def test_adapter_does_not_import_price_band(self) -> None:
        """Phase 13 readiness-only adapter must not import PriceBand."""
        src = _load_source(_ADAPTER_MODULE)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    assert alias.name != "PriceBand", (
                        "Phase 13 readiness-only adapter must not import PriceBand"
                    )

    def test_adapter_module_exists(self) -> None:
        assert _ADAPTER_MODULE.exists(), "valuation_context_adapter_v1.py must exist"

    def test_intel_v3_service_references_valuation_adapter(self) -> None:
        src = _load_source(_INTEL_V3_SERVICE_MODULE)
        assert "valuation_context_adapter_v1" in src

    def test_intel_v3_service_references_phase13_gate(self) -> None:
        src = _load_source(_INTEL_V3_SERVICE_MODULE)
        assert "intel_v3_valuation_context_adapter_v1_enabled" in src

    def test_adapter_forbidden_keys_not_in_output_fields(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        forbidden_fields = {"pe_ratio", "pb_ratio", "ev_ebitda", "price_target",
                            "fair_value", "eps_value", "book_value_per_share"}
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(sig)}
        for key in forbidden_fields:
            assert key not in field_names, f"Forbidden key {key!r} found in signal fields"


# ═══════════════════════════════════════════════════════════════════════════════
# AC 22 — Real registry integration and config flags
# ═══════════════════════════════════════════════════════════════════════════════

class TestRealRegistryIntegration:
    def test_real_registry_valuation_source_is_planned(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY[_GOVERNED_SOURCE_ID]
        assert source.lifecycle_status in (LifecycleStatus.PLANNED, LifecycleStatus.ACTIVE)

    def test_real_registry_gate_passes_with_planned_status(self) -> None:
        source = EVIDENCE_SOURCE_REGISTRY[_GOVERNED_SOURCE_ID]
        assert source.lifecycle_status == LifecycleStatus.PLANNED
        passed, _ = check_governance_gate(EVIDENCE_SOURCE_REGISTRY)
        assert passed

    def test_ready_signal_uses_real_registry_by_default(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
            registry=None,
        )
        assert sig.governance_gate_passed is True
        assert sig.status == ValuationSignalStatus.READY_FOR_FUTURE_VALUATION

    def test_valuation_signal_status_enum_has_all_required_statuses(self) -> None:
        required = {
            "READY_FOR_FUTURE_VALUATION",
            "PARTIAL_FOR_FUTURE_VALUATION",
            "SUPPRESSED_MISSING_PRICE_OR_POSITION",
            "SUPPRESSED_MISSING_FUNDAMENTALS",
            "SUPPRESSED_NON_COMPANY",
            "SUPPRESSED_CONFLICTING_OR_STALE",
            "GOVERNANCE_BLOCKED",
        }
        actual = {s.value for s in ValuationSignalStatus}
        assert required.issubset(actual), f"Missing statuses: {required - actual}"

    def test_phase13_service_flag_exists_in_config(self) -> None:
        from app.config import get_settings
        settings = get_settings()
        assert hasattr(settings, "intel_v3_valuation_context_adapter_v1_enabled")
        assert settings.intel_v3_valuation_context_adapter_v1_enabled is False

    def test_phase13_diagnostics_flag_exists_in_config(self) -> None:
        from app.config import get_settings
        settings = get_settings()
        assert hasattr(settings, "intel_v3_valuation_context_adapter_v1_diagnostics_enabled")
        assert settings.intel_v3_valuation_context_adapter_v1_diagnostics_enabled is False

    def test_phase13_flags_off_by_default(self) -> None:
        from app.config import get_settings
        settings = get_settings()
        assert not settings.intel_v3_valuation_context_adapter_v1_enabled
        assert not settings.intel_v3_valuation_context_adapter_v1_diagnostics_enabled

    def test_multiple_ready_tickers_all_produce_none_contribution(self) -> None:
        tickers = ["AAPL", "MSFT", "GOOG", "AMZN"]
        readiness = _make_readiness(ready=tickers)
        for ticker in tickers:
            sig = build_valuation_context_signal(
                ticker=ticker, category="stock",
                sec_readiness=readiness, has_market_price=True,
            )
            assert sig.status == ValuationSignalStatus.READY_FOR_FUTURE_VALUATION
            assert sig.price_context_contribution is None

    def test_mixed_portfolio_signals(self) -> None:
        readiness = _make_readiness(
            ready=["AAPL"],
            partial={"MSFT": ["eps"]},
            blocked={"BLSH": ["manual_block"]},
            skipped={"etf_fund": ["VTI", "QQQ"]},
        )
        cases = [
            ("AAPL", "stock", True, ValuationSignalStatus.READY_FOR_FUTURE_VALUATION),
            ("MSFT", "stock", True, ValuationSignalStatus.PARTIAL_FOR_FUTURE_VALUATION),
            ("BLSH", "stock", True, ValuationSignalStatus.SUPPRESSED_MISSING_FUNDAMENTALS),
            ("VTI", "etf", True, ValuationSignalStatus.SUPPRESSED_NON_COMPANY),
            ("QQQ", "stock", True, ValuationSignalStatus.SUPPRESSED_NON_COMPANY),
        ]
        for ticker, category, has_price, expected_status in cases:
            sig = build_valuation_context_signal(
                ticker=ticker, category=category,
                sec_readiness=readiness, has_market_price=has_price,
            )
            assert sig.status == expected_status, (
                f"Ticker {ticker}: expected {expected_status}, got {sig.status}"
            )
            assert sig.price_context_contribution is None, (
                f"Ticker {ticker}: price_context_contribution must be None"
            )

    def test_mixed_portfolio_no_price_context_changes(self) -> None:
        readiness = _make_readiness(
            ready=["AAPL"],
            partial={"MSFT": ["eps"]},
            blocked={"BLSH": ["manual_block"]},
        )
        for ticker, category in [("AAPL", "stock"), ("MSFT", "stock"), ("BLSH", "stock")]:
            for initial_price in [PriceBand.SUPPRESSED, PriceBand.CHEAP, PriceBand.FULL]:
                inp = _make_decision_input(ticker=ticker, price_context=initial_price)
                sig = build_valuation_context_signal(
                    ticker=ticker, category=category,
                    sec_readiness=readiness, has_market_price=True,
                )
                apply_valuation_context_to_decision_input(inp, sig)
                assert inp.price_context == initial_price, (
                    f"Ticker {ticker} with initial {initial_price}: "
                    f"price_context should not change, got {inp.price_context}"
                )
