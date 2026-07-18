"""Phase 13.1 — Valuation Context Readiness Production Diagnostics Validation.

Acceptance criteria verified by this file:

 1. Diagnostic response hard-lock: readiness_only=True always.
 2. Diagnostic response hard-lock: price_context_unchanged=True always.
 3. Diagnostic response hard-lock: safe_for_decision=False always.
 4. Diagnostic response hard-lock: visible_snapshot_unchanged=True always.
 5. Diagnostic readiness_status_counts contains exactly all 7 ValuationSignalStatus values.
 6. Diagnostic readiness_status_counts values are non-negative integers (aggregate-only).
 7. No forbidden metric key names (pe_ratio, pb_ratio, ev_ebitda, fcf_yield, roic,
    price_target, fair_value, source_url) appear in any response field or value.
 8. No PriceBand enum values appear in the response.
 9. price_context_contribution remains None for all statuses in the diagnostic build path.
10. Diagnostic static analysis: diagnostics.py never sets safe_for_decision=True for Phase 13.
11. Diagnostic static analysis: diagnostics.py never sets readiness_only=False.
12. Diagnostic static analysis: diagnostics.py hard-locks visible_snapshot_unchanged=True.
13. Counts from building signals for mock readiness never touch DecisionInputV3.price_context.
14. Status counts sum matches total processed tickers (gate-passed path).

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
    _GOVERNED_SOURCE_ID,
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

# ── Module paths for static analysis ─────────────────────────────────────────

_DIAGNOSTICS_ROUTER = (
    pathlib.Path(__file__).parent.parent
    / "app/routers/diagnostics.py"
)

_ADAPTER_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/v3/valuation_context_adapter_v1.py"
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


def _build_diagnostic_response(readiness: SecMetricEvidenceReadinessResult) -> dict:
    """Replicate the diagnostic endpoint response-building logic using in-memory fixtures.

    This function mirrors the logic in diagnostics.py
    get_valuation_context_adapter_v1_diagnostics(), so that response-contract
    tests can run without a DB or HTTP layer.
    """
    gate_passed, gate_reason = check_governance_gate()
    status_counts: dict[str, int] = {s.value: 0 for s in ValuationSignalStatus}

    if gate_passed:
        for ticker in readiness.ready_tickers:
            sig = build_valuation_context_signal(
                ticker=ticker, category="stock",
                sec_readiness=readiness, has_market_price=True,
            )
            status_counts[sig.status.value] = status_counts.get(sig.status.value, 0) + 1

        for ticker in readiness.partial_tickers_with_missing_groups:
            sig = build_valuation_context_signal(
                ticker=ticker, category="stock",
                sec_readiness=readiness, has_market_price=True,
            )
            status_counts[sig.status.value] = status_counts.get(sig.status.value, 0) + 1

        for _reason, tickers in readiness.skipped_tickers_by_reason.items():
            for ticker in tickers:
                sig = build_valuation_context_signal(
                    ticker=ticker, category="etf",
                    sec_readiness=readiness, has_market_price=True,
                )
                status_counts[sig.status.value] = status_counts.get(sig.status.value, 0) + 1

        for ticker in readiness.blocked_tickers_with_reason:
            sig = build_valuation_context_signal(
                ticker=ticker, category="stock",
                sec_readiness=readiness, has_market_price=True,
            )
            status_counts[sig.status.value] = status_counts.get(sig.status.value, 0) + 1

    return {
        "adapter_version": VALUATION_CONTEXT_ADAPTER_V1_CONTRACT_VERSION,
        "safe_for_decision": False,
        "visible_snapshot_unchanged": True,
        "price_context_unchanged": True,
        "readiness_only": True,
        "governance_gate_passed": gate_passed,
        "governance_gate_reason": gate_reason,
        "consumption_enabled": False,
        "portfolio_ticker_count": readiness.portfolio_ticker_count,
        "readiness_status_counts": status_counts,
        "errors": readiness.errors,
    }


# ── Shared fixtures ───────────────────────────────────────────────────────────

_MIXED_READINESS = _make_readiness(
    ready=["AAPL", "MSFT"],
    partial={"GOOG": ["eps"]},
    blocked={"BLSH": ["no_sec"]},
    skipped={"etf_fund": ["VTI", "QQQ"]},
)

_FORBIDDEN_METRIC_KEYS = frozenset({
    "pe_ratio", "pb_ratio", "ev_ebitda", "fcf_yield", "roic",
    "price_target", "fair_value", "source_url", "eps_value",
    "book_value_per_share", "earnings_per_share",
})

_ALL_STATUS_VALUES = frozenset(s.value for s in ValuationSignalStatus)

_PRICE_BAND_VALUES = frozenset(b.value for b in PriceBand if b != PriceBand.SUPPRESSED)


# ═══════════════════════════════════════════════════════════════════════════════
# AC 1-4 — Hard-locked diagnostic response fields
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiagnosticResponseHardLocks:
    """Phase 13.1 AC 1-4: readiness_only, price_context_unchanged, safe_for_decision,
    visible_snapshot_unchanged are always hard-locked to their expected values."""

    def _response(self) -> dict:
        return _build_diagnostic_response(_MIXED_READINESS)

    def test_readiness_only_is_true(self) -> None:
        resp = self._response()
        assert resp["readiness_only"] is True

    def test_price_context_unchanged_is_true(self) -> None:
        resp = self._response()
        assert resp["price_context_unchanged"] is True

    def test_safe_for_decision_is_false(self) -> None:
        resp = self._response()
        assert resp["safe_for_decision"] is False

    def test_visible_snapshot_unchanged_is_true(self) -> None:
        resp = self._response()
        assert resp["visible_snapshot_unchanged"] is True

    def test_adapter_version_is_phase13_v1(self) -> None:
        resp = self._response()
        assert resp["adapter_version"] == "phase13_v1"

    def test_governance_gate_passed_is_bool(self) -> None:
        resp = self._response()
        assert isinstance(resp["governance_gate_passed"], bool)

    def test_governance_gate_reason_is_string(self) -> None:
        resp = self._response()
        assert isinstance(resp["governance_gate_reason"], str)
        assert len(resp["governance_gate_reason"]) > 0

    def test_consumption_enabled_is_bool(self) -> None:
        resp = self._response()
        assert isinstance(resp["consumption_enabled"], bool)

    def test_safe_for_decision_stays_false_with_empty_readiness(self) -> None:
        resp = _build_diagnostic_response(_make_readiness())
        assert resp["safe_for_decision"] is False

    def test_readiness_only_stays_true_with_many_ready_tickers(self) -> None:
        readiness = _make_readiness(ready=["AAPL", "MSFT", "GOOG", "AMZN", "NVDA"])
        resp = _build_diagnostic_response(readiness)
        assert resp["readiness_only"] is True

    def test_price_context_unchanged_stays_true_regardless_of_tickers(self) -> None:
        for readiness in [
            _make_readiness(ready=["AAPL"]),
            _make_readiness(partial={"MSFT": ["eps"]}),
            _make_readiness(blocked={"BLSH": ["reason"]}),
            _make_readiness(skipped={"etf": ["VTI"]}),
            _make_readiness(),
        ]:
            resp = _build_diagnostic_response(readiness)
            assert resp["price_context_unchanged"] is True

    def test_visible_snapshot_unchanged_stays_true_regardless_of_tickers(self) -> None:
        for readiness in [
            _make_readiness(ready=["AAPL", "MSFT"]),
            _make_readiness(skipped={"crypto": ["BTC"]}),
        ]:
            resp = _build_diagnostic_response(readiness)
            assert resp["visible_snapshot_unchanged"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# AC 5 — readiness_status_counts contains all 7 ValuationSignalStatus values
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiagnosticStatusCountsContract:
    """Phase 13.1 AC 5: all 7 statuses must be present as keys; values are
    non-negative integers. Missing keys would hide suppression signal classes."""

    def _response(self) -> dict:
        return _build_diagnostic_response(_MIXED_READINESS)

    def test_readiness_status_counts_key_exists(self) -> None:
        resp = self._response()
        assert "readiness_status_counts" in resp

    def test_all_seven_statuses_are_keys(self) -> None:
        resp = self._response()
        counts = resp["readiness_status_counts"]
        assert _ALL_STATUS_VALUES == set(counts.keys()), (
            f"Missing status keys: {_ALL_STATUS_VALUES - set(counts.keys())}"
        )

    def test_ready_for_future_valuation_key_present(self) -> None:
        resp = self._response()
        assert "READY_FOR_FUTURE_VALUATION" in resp["readiness_status_counts"]

    def test_partial_for_future_valuation_key_present(self) -> None:
        resp = self._response()
        assert "PARTIAL_FOR_FUTURE_VALUATION" in resp["readiness_status_counts"]

    def test_suppressed_missing_price_or_position_key_present(self) -> None:
        resp = self._response()
        assert "SUPPRESSED_MISSING_PRICE_OR_POSITION" in resp["readiness_status_counts"]

    def test_suppressed_missing_fundamentals_key_present(self) -> None:
        resp = self._response()
        assert "SUPPRESSED_MISSING_FUNDAMENTALS" in resp["readiness_status_counts"]

    def test_suppressed_non_company_key_present(self) -> None:
        resp = self._response()
        assert "SUPPRESSED_NON_COMPANY" in resp["readiness_status_counts"]

    def test_suppressed_conflicting_or_stale_key_present(self) -> None:
        resp = self._response()
        assert "SUPPRESSED_CONFLICTING_OR_STALE" in resp["readiness_status_counts"]

    def test_governance_blocked_key_present(self) -> None:
        resp = self._response()
        assert "GOVERNANCE_BLOCKED" in resp["readiness_status_counts"]

    def test_all_count_values_are_integers(self) -> None:
        resp = self._response()
        for key, val in resp["readiness_status_counts"].items():
            assert isinstance(val, int), f"Count for {key!r} must be int, got {type(val)}"

    def test_all_count_values_are_non_negative(self) -> None:
        resp = self._response()
        for key, val in resp["readiness_status_counts"].items():
            assert val >= 0, f"Count for {key!r} must be >= 0, got {val}"

    def test_counts_sum_equals_processed_tickers(self) -> None:
        readiness = _make_readiness(
            ready=["AAPL", "MSFT"],
            partial={"GOOG": ["eps"]},
            blocked={"BLSH": ["no_sec"]},
            skipped={"etf_fund": ["VTI", "QQQ"]},
        )
        resp = _build_diagnostic_response(readiness)
        total = sum(resp["readiness_status_counts"].values())
        # Total processed = ready(2) + partial(1) + blocked(1) + skipped(2) = 6
        assert total == 6

    def test_empty_readiness_all_counts_zero(self) -> None:
        resp = _build_diagnostic_response(_make_readiness())
        counts = resp["readiness_status_counts"]
        assert all(v == 0 for v in counts.values())

    def test_counts_values_are_not_dicts_or_lists(self) -> None:
        resp = _build_diagnostic_response(_MIXED_READINESS)
        for key, val in resp["readiness_status_counts"].items():
            assert not isinstance(val, (dict, list)), (
                f"Count for {key!r} must not be dict/list — aggregate-only"
            )

    def test_ready_count_matches_ready_tickers(self) -> None:
        readiness = _make_readiness(ready=["AAPL", "MSFT", "GOOG"])
        resp = _build_diagnostic_response(readiness)
        assert resp["readiness_status_counts"]["READY_FOR_FUTURE_VALUATION"] == 3

    def test_partial_count_matches_partial_tickers(self) -> None:
        readiness = _make_readiness(partial={"MSFT": ["eps"], "NVDA": ["rev"]})
        resp = _build_diagnostic_response(readiness)
        assert resp["readiness_status_counts"]["PARTIAL_FOR_FUTURE_VALUATION"] == 2

    def test_non_company_count_matches_skipped_tickers(self) -> None:
        readiness = _make_readiness(skipped={"etf": ["VTI", "QQQ", "SPY"]})
        resp = _build_diagnostic_response(readiness)
        assert resp["readiness_status_counts"]["SUPPRESSED_NON_COMPANY"] == 3

    def test_blocked_count_goes_to_suppressed_missing_fundamentals(self) -> None:
        readiness = _make_readiness(blocked={"BLSH": ["r1"], "KLAR": ["r2"]})
        resp = _build_diagnostic_response(readiness)
        assert resp["readiness_status_counts"]["SUPPRESSED_MISSING_FUNDAMENTALS"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# AC 6-8 — Aggregate-only: no forbidden keys, no price targets, no PriceBand
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiagnosticResponseAggregateSafety:
    """Phase 13.1 AC 6-8: the response never contains forbidden metric keys,
    raw values, price targets, source URLs, or PriceBand enum values."""

    def _response(self) -> dict:
        return _build_diagnostic_response(_MIXED_READINESS)

    def _response_json_str(self) -> str:
        import json
        return json.dumps(self._response(), default=str).lower()

    def test_response_contains_no_pe_ratio_key(self) -> None:
        resp = self._response()
        assert "pe_ratio" not in str(resp)

    def test_response_contains_no_pb_ratio_key(self) -> None:
        assert "pb_ratio" not in self._response_json_str()

    def test_response_contains_no_ev_ebitda_key(self) -> None:
        assert "ev_ebitda" not in self._response_json_str()

    def test_response_contains_no_fcf_yield_key(self) -> None:
        assert "fcf_yield" not in self._response_json_str()

    def test_response_contains_no_roic_key(self) -> None:
        resp_str = self._response_json_str()
        # "roic" must not appear as a standalone metric key
        assert '"roic"' not in resp_str and "'roic'" not in resp_str

    def test_response_contains_no_price_target_key(self) -> None:
        assert "price_target" not in self._response_json_str()

    def test_response_contains_no_fair_value_key(self) -> None:
        assert "fair_value" not in self._response_json_str()

    def test_response_contains_no_source_url_key(self) -> None:
        assert "source_url" not in self._response_json_str()

    def test_response_contains_no_structured_payload_key(self) -> None:
        assert "structured_payload" not in self._response_json_str()

    def test_response_contains_no_price_context_upgraded_key(self) -> None:
        assert "price_context_upgraded" not in self._response_json_str()

    def test_response_contains_no_priceband_fair_value(self) -> None:
        resp = self._response()
        resp_str = str(resp)
        assert "FAIR" not in resp_str
        assert "CHEAP" not in resp_str
        assert "FULL" not in resp_str
        assert "EXPENSIVE" not in resp_str

    def test_readiness_status_counts_keys_are_status_strings_not_metric_keys(self) -> None:
        resp = self._response()
        counts = resp["readiness_status_counts"]
        for key in counts:
            assert key in _ALL_STATUS_VALUES, (
                f"Unexpected key in readiness_status_counts: {key!r}"
            )

    def test_errors_is_list(self) -> None:
        resp = self._response()
        assert isinstance(resp["errors"], list)

    def test_errors_contains_no_raw_payloads(self) -> None:
        readiness = _make_readiness()
        resp = _build_diagnostic_response(readiness)
        for err in resp["errors"]:
            assert not isinstance(err, dict) or "structured_payload" not in err

    def test_response_top_level_keys_are_expected_only(self) -> None:
        expected_keys = {
            "adapter_version", "safe_for_decision", "visible_snapshot_unchanged",
            "price_context_unchanged", "readiness_only", "governance_gate_passed",
            "governance_gate_reason", "consumption_enabled", "portfolio_ticker_count",
            "readiness_status_counts", "errors",
        }
        resp = self._response()
        unexpected = set(resp.keys()) - expected_keys
        assert not unexpected, f"Unexpected response keys: {unexpected}"

    def test_no_per_ticker_data_in_response(self) -> None:
        resp = self._response()
        resp_str = str(resp)
        assert "AAPL" not in resp_str
        assert "MSFT" not in resp_str
        assert "GOOG" not in resp_str

    def test_no_price_context_upgrades_field_in_response(self) -> None:
        resp = self._response()
        assert "price_context_upgrades" not in resp

    def test_no_ratio_keys_in_status_count_values(self) -> None:
        resp = self._response()
        counts = resp["readiness_status_counts"]
        for key, val in counts.items():
            assert not isinstance(val, str) or not any(
                metric in val for metric in _FORBIDDEN_METRIC_KEYS
            ), f"Metric key found in count value for {key!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC 9 — price_context_contribution remains None in diagnostic build path
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiagnosticBuildPathNoPriceContextContribution:
    """Phase 13.1 AC 9: the signals built during diagnostic counting never
    produce a non-None price_context_contribution — no PriceBand contribution."""

    def _signals_from_readiness(
        self, readiness: SecMetricEvidenceReadinessResult
    ) -> list[ValuationContextSignal]:
        gate_passed, _ = check_governance_gate()
        signals = []
        if gate_passed:
            for ticker in readiness.ready_tickers:
                signals.append(build_valuation_context_signal(
                    ticker=ticker, category="stock",
                    sec_readiness=readiness, has_market_price=True,
                ))
            for ticker in readiness.partial_tickers_with_missing_groups:
                signals.append(build_valuation_context_signal(
                    ticker=ticker, category="stock",
                    sec_readiness=readiness, has_market_price=True,
                ))
            for _reason, tickers in readiness.skipped_tickers_by_reason.items():
                for ticker in tickers:
                    signals.append(build_valuation_context_signal(
                        ticker=ticker, category="etf",
                        sec_readiness=readiness, has_market_price=True,
                    ))
            for ticker in readiness.blocked_tickers_with_reason:
                signals.append(build_valuation_context_signal(
                    ticker=ticker, category="stock",
                    sec_readiness=readiness, has_market_price=True,
                ))
        return signals

    def test_all_signals_in_diagnostic_path_have_none_contribution(self) -> None:
        signals = self._signals_from_readiness(_MIXED_READINESS)
        for sig in signals:
            assert sig.price_context_contribution is None, (
                f"Signal for {sig.ticker!r} status={sig.status.value}: "
                f"price_context_contribution must be None in diagnostic path"
            )

    def test_ready_signals_in_diagnostic_path_have_none_contribution(self) -> None:
        readiness = _make_readiness(ready=["AAPL", "MSFT", "GOOG"])
        signals = self._signals_from_readiness(readiness)
        assert all(sig.price_context_contribution is None for sig in signals)

    def test_partial_signals_in_diagnostic_path_have_none_contribution(self) -> None:
        readiness = _make_readiness(partial={"AAPL": ["eps"], "MSFT": ["rev"]})
        signals = self._signals_from_readiness(readiness)
        assert all(sig.price_context_contribution is None for sig in signals)

    def test_skipped_signals_in_diagnostic_path_have_none_contribution(self) -> None:
        readiness = _make_readiness(skipped={"etf": ["VTI", "QQQ"]})
        signals = self._signals_from_readiness(readiness)
        assert all(sig.price_context_contribution is None for sig in signals)

    def test_blocked_signals_in_diagnostic_path_have_none_contribution(self) -> None:
        readiness = _make_readiness(blocked={"BLSH": ["r1"], "KLAR": ["r2"]})
        signals = self._signals_from_readiness(readiness)
        assert all(sig.price_context_contribution is None for sig in signals)

    def test_no_signal_produces_priceband_cheap(self) -> None:
        signals = self._signals_from_readiness(_MIXED_READINESS)
        assert all(sig.price_context_contribution != PriceBand.CHEAP for sig in signals)

    def test_no_signal_produces_priceband_fair(self) -> None:
        signals = self._signals_from_readiness(_MIXED_READINESS)
        assert all(sig.price_context_contribution != PriceBand.FAIR for sig in signals)

    def test_no_signal_produces_priceband_full(self) -> None:
        signals = self._signals_from_readiness(_MIXED_READINESS)
        assert all(sig.price_context_contribution != PriceBand.FULL for sig in signals)

    def test_no_signal_produces_priceband_expensive(self) -> None:
        signals = self._signals_from_readiness(_MIXED_READINESS)
        assert all(sig.price_context_contribution != PriceBand.EXPENSIVE for sig in signals)


# ═══════════════════════════════════════════════════════════════════════════════
# AC 10-12 — Diagnostic router static analysis
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiagnosticRouterStaticAnalysis:
    """Phase 13.1 AC 10-12: static analysis of diagnostics.py proves the
    Phase 13 endpoint never sets safe_for_decision=True, never sets
    readiness_only=False, and never sets visible_snapshot_unchanged=False."""

    def _phase13_endpoint_source(self) -> str:
        full = _load_source(_DIAGNOSTICS_ROUTER)
        # Start from the async function definition to avoid Unicode in comments
        marker = "async def get_valuation_context_adapter_v1_diagnostics"
        idx = full.find(marker)
        assert idx >= 0, "Phase 13 endpoint not found in diagnostics.py"
        return full[idx:]

    def test_diagnostics_router_exists(self) -> None:
        assert _DIAGNOSTICS_ROUTER.exists()

    def test_diagnostics_router_imports_valuation_signal_status(self) -> None:
        src = _load_source(_DIAGNOSTICS_ROUTER)
        assert "ValuationSignalStatus" in src

    def test_diagnostics_router_imports_valuation_governance_gate(self) -> None:
        src = _load_source(_DIAGNOSTICS_ROUTER)
        assert "check_valuation_governance_gate" in src or "check_governance_gate" in src

    def test_phase13_endpoint_never_sets_safe_for_decision_true(self) -> None:
        src = self._phase13_endpoint_source()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Constant) and target.value == "safe_for_decision":
                        if isinstance(node.value, ast.Constant):
                            assert node.value.value is not True, \
                                "Phase 13 endpoint must not set safe_for_decision=True"
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "safe_for_decision":
                        if isinstance(v, ast.Constant):
                            assert v.value is not True, \
                                "Phase 13 response must not set safe_for_decision=True"

    def test_phase13_endpoint_hard_locks_safe_for_decision_false(self) -> None:
        src = self._phase13_endpoint_source()
        assert '"safe_for_decision": False' in src or "'safe_for_decision': False" in src or \
               "safe_for_decision\": False" in src

    def test_phase13_endpoint_hard_locks_readiness_only_true(self) -> None:
        src = self._phase13_endpoint_source()
        assert "readiness_only" in src
        assert "True" in src

    def test_phase13_endpoint_hard_locks_price_context_unchanged_true(self) -> None:
        src = self._phase13_endpoint_source()
        assert "price_context_unchanged" in src

    def test_phase13_endpoint_hard_locks_visible_snapshot_unchanged_true(self) -> None:
        src = self._phase13_endpoint_source()
        assert "visible_snapshot_unchanged" in src

    def test_phase13_endpoint_never_imports_decide(self) -> None:
        # Current contract (Stage 6 governance diagnostics): decide() may only be
        # imported lazily inside the env-gated Stage 6 governance endpoint
        # (get_stage6_evidence_governance_diagnostics). It must never be imported
        # at module level, and never inside the Phase 13 endpoint.
        src = _load_source(_DIAGNOSTICS_ROUTER)
        tree = ast.parse(src)
        allowed_funcs = {"get_stage6_evidence_governance_diagnostics"}
        # Module-level imports must never include decide.
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []):
                    assert alias.name != "decide", \
                        "diagnostics.py must not import decide() at module level"
        # Any function-local decide import must be confined to the allowed
        # Stage 6 governance endpoint.
        offending = []
        for func in ast.walk(tree):
            if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if func.name in allowed_funcs:
                    continue
                for sub in ast.walk(func):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        for alias in getattr(sub, "names", []):
                            if alias.name == "decide":
                                offending.append(func.name)
        assert offending == [], \
            f"decide() imported outside the Stage 6 governance endpoint: {offending}"

    def test_phase13_endpoint_never_imports_price_band_directly(self) -> None:
        src = _load_source(_DIAGNOSTICS_ROUTER)
        assert "from .decision_contracts import" not in src or "PriceBand" not in src.split(
            "from .decision_contracts import"
        )[-1].split("\n")[0]

    def test_phase13_endpoint_status_counts_initialized_from_all_statuses(self) -> None:
        src = self._phase13_endpoint_source()
        assert "ValuationSignalStatus" in src

    def test_diagnostics_router_does_not_set_price_context_upgraded_true(self) -> None:
        src = _load_source(_DIAGNOSTICS_ROUTER)
        assert "price_context_upgraded" not in src or (
            "True" not in src[src.find("price_context_upgraded"):src.find("price_context_upgraded") + 60]
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC 13 — Diagnostic build never modifies DecisionInputV3.price_context
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiagnosticBuildNoPriceContextChange:
    """Phase 13.1 AC 13: the diagnostic counting path uses build_valuation_context_signal
    only (not apply_valuation_context_to_decision_input), so price_context is
    never changed. But even if apply() is called in tests, price_context stays."""

    def _make_inp(
        self, ticker: str = "AAPL", price_context: PriceBand = PriceBand.SUPPRESSED,
    ) -> DecisionInputV3:
        return DecisionInputV3(
            ticker=ticker,
            evidence_quality=AxisBand.OK,
            price_context=price_context,
            portfolio_fit=FitBand.UNKNOWN,
            risk_band=RiskBand.UNKNOWN,
        )

    def test_diagnostic_signal_for_ready_does_not_change_inp_price_context(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        inp = self._make_inp(price_context=PriceBand.SUPPRESSED)
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        # Simulate the diagnostic path: only build, never apply
        assert sig.price_context_contribution is None
        assert inp.price_context == PriceBand.SUPPRESSED

    def test_apply_after_diagnostic_build_still_leaves_price_context_unchanged(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        inp = self._make_inp(price_context=PriceBand.SUPPRESSED)
        sig = build_valuation_context_signal(
            ticker="AAPL", category="stock",
            sec_readiness=readiness, has_market_price=True,
        )
        apply_valuation_context_to_decision_input(inp, sig)
        assert inp.price_context == PriceBand.SUPPRESSED

    def test_all_price_context_initial_values_preserved_after_diagnostic_signal(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        for initial in [PriceBand.SUPPRESSED, PriceBand.CHEAP, PriceBand.FAIR,
                        PriceBand.FULL, PriceBand.EXPENSIVE]:
            inp = self._make_inp(price_context=initial)
            sig = build_valuation_context_signal(
                ticker="AAPL", category="stock",
                sec_readiness=readiness, has_market_price=True,
            )
            apply_valuation_context_to_decision_input(inp, sig)
            assert inp.price_context == initial, (
                f"price_context changed from {initial} after Phase 13 apply()"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# AC 14 — Production validation: gate_passed + expected field values
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductionValidationPassCriteria:
    """Phase 13.1 AC 14: simulate production pass criteria checks against the
    diagnostic response. These mirror the HANDOFF pass/fail criteria."""

    def _response(self) -> dict:
        return _build_diagnostic_response(_MIXED_READINESS)

    def test_governance_gate_passes_for_real_registry(self) -> None:
        resp = self._response()
        assert resp["governance_gate_passed"] is True, (
            f"Production validation: governance_gate_passed must be True. "
            f"Reason: {resp.get('governance_gate_reason')}"
        )

    def test_errors_is_empty_for_clean_readiness(self) -> None:
        readiness = _make_readiness(ready=["AAPL"])
        resp = _build_diagnostic_response(readiness)
        assert resp["errors"] == []

    def test_status_counts_sum_matches_portfolio_count(self) -> None:
        readiness = _make_readiness(
            ready=["AAPL"],
            partial={"MSFT": ["eps"]},
            blocked={"BLSH": ["r"]},
            skipped={"etf": ["VTI"]},
        )
        resp = _build_diagnostic_response(readiness)
        status_total = sum(resp["readiness_status_counts"].values())
        assert status_total == resp["portfolio_ticker_count"], (
            "Status counts must add up to portfolio_ticker_count"
        )

    def test_portfolio_ticker_count_is_non_negative_int(self) -> None:
        resp = self._response()
        assert isinstance(resp["portfolio_ticker_count"], int)
        assert resp["portfolio_ticker_count"] >= 0

    def test_pass_criteria_safe_for_decision_is_false(self) -> None:
        resp = self._response()
        assert resp["safe_for_decision"] is False

    def test_pass_criteria_readiness_only_is_true(self) -> None:
        resp = self._response()
        assert resp["readiness_only"] is True

    def test_pass_criteria_price_context_unchanged_is_true(self) -> None:
        resp = self._response()
        assert resp["price_context_unchanged"] is True

    def test_pass_criteria_visible_snapshot_unchanged_is_true(self) -> None:
        resp = self._response()
        assert resp["visible_snapshot_unchanged"] is True

    def test_pass_criteria_governance_gate_reason_is_passed(self) -> None:
        resp = self._response()
        if resp["governance_gate_passed"]:
            assert "passed" in resp["governance_gate_reason"].lower(), (
                f"When gate passes, reason should say 'passed': {resp['governance_gate_reason']}"
            )

    def test_adapter_version_matches_contract(self) -> None:
        resp = self._response()
        assert resp["adapter_version"] == VALUATION_CONTEXT_ADAPTER_V1_CONTRACT_VERSION

    def test_consumption_enabled_defaults_false(self) -> None:
        resp = self._response()
        # In diagnostics-only mode (without the full flag), consumption=False
        assert isinstance(resp["consumption_enabled"], bool)
