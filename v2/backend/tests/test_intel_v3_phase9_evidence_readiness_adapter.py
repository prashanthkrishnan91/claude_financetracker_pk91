"""Phase 9 — SEC Metric Evidence Readiness Adapter tests.

Acceptance criteria (10 groups):

 1. Contract version constant exists and matches expected string.
 2. Readiness status constants are correct (READY/PARTIAL/BLOCKED/SKIPPED_NON_COMPANY).
 3. READY tickers from the Phase 8F universe are counted and listed correctly.
 4. PARTIAL tickers from the Phase 8F universe include the correct missing metric groups.
 5. Non-company/ETF/crypto/fund tickers are classified as SKIPPED_NON_COMPANY and
    not treated as failed evidence. They are grouped by sub-reason (likely_fund_or_etf
    vs likely_crypto).
 6. BLSH/KLAR/TSM-style tickers (snapshot present, fact_count == 0) are classified
    as BLOCKED with attempted_no_source_linked_sec_metric_evidence reason.
    They are NOT retried.
 7. safe_for_decision remains False on all result paths.
 8. visible_snapshot_unchanged remains True on all result paths.
 9. Static import guard: no decide(), IntelV3Service, recommendation_engine,
    or frontend imports in the Phase 9 adapter module.
10. Disabled result is returned when the kill switch is off.

Architecture invariants verified by this file:
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER sets safe_for_decision=True.
    - safe_for_decision always False.
    - visible_snapshot_unchanged always True.
    - No decision path changes from Phase 9 adapter code.

All tests use in-memory fixtures — no Supabase dependency.
"""
from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from app.services.intelligence.research_workers.sec_metric_evidence_readiness_adapter import (
    SEC_METRIC_EVIDENCE_READINESS_CONTRACT_VERSION,
    READINESS_STATUS_READY,
    READINESS_STATUS_PARTIAL,
    READINESS_STATUS_BLOCKED,
    READINESS_STATUS_SKIPPED_NON_COMPANY,
    SecMetricEvidenceReadinessResult,
    build_sec_metric_evidence_readiness,
    _disabled_result,
)

_ADAPTER_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/research_workers/sec_metric_evidence_readiness_adapter.py"
)

# ── Phase 8F known universe ───────────────────────────────────────────────────

READY_TICKERS = sorted([
    "AAPL", "COST", "CRM", "GOOGL", "META", "MSFT", "NFLX", "RDDT", "SNOW", "STUB",
])

PARTIAL_TICKERS_WITH_MISSING: dict[str, list[str]] = {
    "ALK": sorted(["capex", "liabilities"]),
    "AMD": sorted(["liabilities"]),
    "BRK-B": sorted(["cash", "eps", "operating_income"]),
    "NVDA": sorted(["capex"]),
    "QCOM": sorted(["capex", "equity"]),
    "WMT": sorted(["liabilities"]),
}

ETF_TICKERS = sorted([
    "GLD", "QQQ", "SCHD", "SPY", "VGT", "VHT", "VIS",
    "VOO", "VTI", "VUG", "VXUS", "VYM", "XLE",
])

CRYPTO_TICKERS = sorted(["BTC", "XRP"])

BLOCKED_TICKERS = sorted(["BLSH", "KLAR", "TSM"])

_ALL_PORTFOLIO_TICKERS = (
    READY_TICKERS
    + sorted(PARTIAL_TICKERS_WITH_MISSING.keys())
    + ETF_TICKERS
    + CRYPTO_TICKERS
    + BLOCKED_TICKERS
)


# ── Fixture helpers ───────────────────────────────────────────────────────────

_ALWAYS_BLOCKING = ["decision_consumption_disabled", "safe_for_decision_db_lock"]


def _ready_snapshot(ticker: str) -> dict:
    """Build a Phase 8B-style snapshot for a READY ticker."""
    return {
        "source_linked_metric_fact_count": 12,
        "future_adapter_readiness": "READY_DRY_RUN_ONLY",
        "present_buckets": sorted([
            "assets", "capex", "cash", "eps", "equity", "liabilities",
            "net_income", "operating_cash_flow", "operating_income", "revenue",
        ]),
        "missing_buckets": [],
        "present_bucket_groups": ["balance_sheet_core", "cash_flow_core", "income_statement_core"],
        "missing_bucket_groups": [],
        "forms": {"10-K": 12},
        "units": {"USD": 10, "USD/shares": 2},
        "blocking_reason_codes": sorted(_ALWAYS_BLOCKING),
    }


def _partial_snapshot(ticker: str, missing_buckets: list[str]) -> dict:
    """Build a Phase 8B-style snapshot for a PARTIAL ticker."""
    all_buckets = {
        "assets", "capex", "cash", "eps", "equity", "liabilities",
        "net_income", "operating_cash_flow", "operating_income", "revenue",
    }
    present = sorted(all_buckets - set(missing_buckets))
    blocking = sorted(
        _ALWAYS_BLOCKING + [f"missing_bucket_{b}" for b in missing_buckets]
    )
    return {
        "source_linked_metric_fact_count": len(present),
        "future_adapter_readiness": "PARTIAL_DRY_RUN_ONLY",
        "present_buckets": present,
        "missing_buckets": sorted(missing_buckets),
        "present_bucket_groups": [],
        "missing_bucket_groups": [],
        "forms": {"10-K": len(present)},
        "units": {"USD": len(present)},
        "blocking_reason_codes": blocking,
    }


def _blocked_snapshot(ticker: str) -> dict:
    """Build a Phase 8B-style snapshot for a BLOCKED ticker (attempted, no facts)."""
    all_buckets = sorted([
        "assets", "capex", "cash", "eps", "equity", "liabilities",
        "net_income", "operating_cash_flow", "operating_income", "revenue",
    ])
    blocking = sorted(
        _ALWAYS_BLOCKING
        + ["attempted_no_source_linked_sec_metric_evidence", "manual_review_required_before_retry",
           "missing_sec_research_artifact"]
        + [f"missing_bucket_{b}" for b in all_buckets]
    )
    return {
        "source_linked_metric_fact_count": 0,
        "future_adapter_readiness": "BLOCKED_DRY_RUN_ONLY",
        "present_buckets": [],
        "missing_buckets": all_buckets,
        "present_bucket_groups": [],
        "missing_bucket_groups": sorted(["balance_sheet_core", "cash_flow_core", "income_statement_core"]),
        "forms": {},
        "units": {},
        "blocking_reason_codes": blocking,
    }


def _build_phase8f_snapshot_by_ticker() -> dict[str, dict]:
    """Build snapshot_by_ticker for the full Phase 8F known universe.

    ETF/crypto tickers have no snapshot entry (classifier handles them).
    BLOCKED tickers have a snapshot with fact_count=0.
    """
    snaps: dict[str, dict] = {}
    for ticker in READY_TICKERS:
        snaps[ticker] = _ready_snapshot(ticker)
    for ticker, missing in PARTIAL_TICKERS_WITH_MISSING.items():
        snaps[ticker] = _partial_snapshot(ticker, missing)
    for ticker in BLOCKED_TICKERS:
        snaps[ticker] = _blocked_snapshot(ticker)
    return snaps


def _build_phase8f_portfolio_positions() -> list[dict]:
    """Build portfolio_positions for the full Phase 8F known universe."""
    positions = []
    for t in READY_TICKERS:
        positions.append({"ticker": t, "category": "Core"})
    for t in PARTIAL_TICKERS_WITH_MISSING:
        positions.append({"ticker": t, "category": "Core"})
    for t in ETF_TICKERS:
        if t == "VUG":
            # Phase 8F: VUG is miscategorized as Core in portfolio data.
            positions.append({"ticker": t, "category": "Core"})
        else:
            positions.append({"ticker": t, "category": "ETF"})
    for t in CRYPTO_TICKERS:
        positions.append({"ticker": t, "category": "Crypto"})
    for t in BLOCKED_TICKERS:
        positions.append({"ticker": t, "category": "Core"})
    return positions


def _run_phase8f_readiness() -> SecMetricEvidenceReadinessResult:
    """Run the Phase 9 readiness adapter against the Phase 8F universe."""
    return build_sec_metric_evidence_readiness(
        portfolio_positions=_build_phase8f_portfolio_positions(),
        snapshot_by_ticker=_build_phase8f_snapshot_by_ticker(),
    )


# =============================================================================
# AC 1 — Contract version constant
# =============================================================================

class TestContractVersion:
    def test_contract_version_exists(self):
        assert SEC_METRIC_EVIDENCE_READINESS_CONTRACT_VERSION == "phase9_v1"


# =============================================================================
# AC 2 — Readiness status constants
# =============================================================================

class TestReadinessStatusConstants:
    def test_ready_constant(self):
        assert READINESS_STATUS_READY == "READY"

    def test_partial_constant(self):
        assert READINESS_STATUS_PARTIAL == "PARTIAL"

    def test_blocked_constant(self):
        assert READINESS_STATUS_BLOCKED == "BLOCKED"

    def test_skipped_non_company_constant(self):
        assert READINESS_STATUS_SKIPPED_NON_COMPANY == "SKIPPED_NON_COMPANY"


# =============================================================================
# AC 3 — READY tickers classified correctly
# =============================================================================

class TestReadyTickers:
    def setup_method(self):
        self.result = _run_phase8f_readiness()

    def test_ready_count_is_10(self):
        assert self.result.ready_count == 10, self.result.ready_tickers

    def test_ready_tickers_list_is_sorted(self):
        assert self.result.ready_tickers == sorted(self.result.ready_tickers)

    def test_all_phase8f_ready_tickers_present(self):
        for ticker in READY_TICKERS:
            assert ticker in self.result.ready_tickers, (
                f"{ticker} not in ready_tickers: {self.result.ready_tickers}"
            )

    def test_ready_tickers_exactly_match_phase8f_universe(self):
        assert sorted(self.result.ready_tickers) == READY_TICKERS

    def test_ready_tickers_not_in_partial(self):
        for ticker in self.result.ready_tickers:
            assert ticker not in self.result.partial_tickers_with_missing_groups

    def test_ready_tickers_not_in_blocked(self):
        for ticker in self.result.ready_tickers:
            assert ticker not in self.result.blocked_tickers_with_reason

    def test_ready_tickers_not_in_skipped(self):
        all_skipped = [
            t for tickers in self.result.skipped_tickers_by_reason.values()
            for t in tickers
        ]
        for ticker in self.result.ready_tickers:
            assert ticker not in all_skipped


# =============================================================================
# AC 4 — PARTIAL tickers with correct missing groups
# =============================================================================

class TestPartialTickers:
    def setup_method(self):
        self.result = _run_phase8f_readiness()

    def test_partial_count_is_6(self):
        assert self.result.partial_count == 6, self.result.partial_tickers_with_missing_groups

    def test_partial_tickers_exactly_match_phase8f_universe(self):
        assert sorted(self.result.partial_tickers_with_missing_groups.keys()) == sorted(
            PARTIAL_TICKERS_WITH_MISSING.keys()
        )

    def test_alk_missing_capex_and_liabilities(self):
        missing = self.result.partial_tickers_with_missing_groups.get("ALK", [])
        assert "capex" in missing, missing
        assert "liabilities" in missing, missing

    def test_amd_missing_liabilities(self):
        missing = self.result.partial_tickers_with_missing_groups.get("AMD", [])
        assert "liabilities" in missing, missing
        assert len(missing) == 1

    def test_brkb_missing_cash_eps_operating_income(self):
        missing = self.result.partial_tickers_with_missing_groups.get("BRK-B", [])
        assert "cash" in missing, missing
        assert "eps" in missing, missing
        assert "operating_income" in missing, missing

    def test_nvda_missing_capex(self):
        missing = self.result.partial_tickers_with_missing_groups.get("NVDA", [])
        assert "capex" in missing, missing
        assert len(missing) == 1

    def test_qcom_missing_capex_and_equity(self):
        missing = self.result.partial_tickers_with_missing_groups.get("QCOM", [])
        assert "capex" in missing, missing
        assert "equity" in missing, missing

    def test_wmt_missing_liabilities(self):
        missing = self.result.partial_tickers_with_missing_groups.get("WMT", [])
        assert "liabilities" in missing, missing
        assert len(missing) == 1

    def test_partial_missing_groups_are_sorted(self):
        for ticker, missing in self.result.partial_tickers_with_missing_groups.items():
            assert missing == sorted(missing), f"{ticker} missing groups not sorted: {missing}"

    def test_all_partial_missing_groups_match_phase8f(self):
        for ticker, expected_missing in PARTIAL_TICKERS_WITH_MISSING.items():
            actual_missing = self.result.partial_tickers_with_missing_groups.get(ticker, [])
            assert sorted(actual_missing) == sorted(expected_missing), (
                f"{ticker}: expected {expected_missing}, got {actual_missing}"
            )

    def test_partial_not_in_ready(self):
        for ticker in self.result.partial_tickers_with_missing_groups:
            assert ticker not in self.result.ready_tickers

    def test_partial_not_in_blocked(self):
        for ticker in self.result.partial_tickers_with_missing_groups:
            assert ticker not in self.result.blocked_tickers_with_reason


# =============================================================================
# AC 5 — Non-company tickers classified as SKIPPED_NON_COMPANY
# =============================================================================

class TestSkippedNonCompany:
    def setup_method(self):
        self.result = _run_phase8f_readiness()

    def test_skipped_non_company_count_is_15(self):
        total = self.result.skipped_non_company_count
        assert total == 15, f"Expected 15 (13 ETF + 2 crypto), got {total}"

    def test_etf_tickers_in_skipped_by_reason(self):
        all_skipped = {
            t for tickers in self.result.skipped_tickers_by_reason.values()
            for t in tickers
        }
        for ticker in ETF_TICKERS:
            assert ticker in all_skipped, f"{ticker} not in skipped"

    def test_crypto_tickers_in_skipped_by_reason(self):
        all_skipped = {
            t for tickers in self.result.skipped_tickers_by_reason.values()
            for t in tickers
        }
        for ticker in CRYPTO_TICKERS:
            assert ticker in all_skipped, f"{ticker} not in skipped"

    def test_vug_classified_as_fund_etf_despite_core_category(self):
        fund_tickers = self.result.skipped_tickers_by_reason.get("likely_fund_or_etf", [])
        assert "VUG" in fund_tickers, f"VUG not in likely_fund_or_etf: {fund_tickers}"

    def test_btc_classified_as_crypto(self):
        crypto_tickers = self.result.skipped_tickers_by_reason.get("likely_crypto", [])
        assert "BTC" in crypto_tickers, f"BTC not in likely_crypto: {crypto_tickers}"

    def test_xrp_classified_as_crypto(self):
        crypto_tickers = self.result.skipped_tickers_by_reason.get("likely_crypto", [])
        assert "XRP" in crypto_tickers, f"XRP not in likely_crypto: {crypto_tickers}"

    def test_etf_tickers_not_in_ready(self):
        for ticker in ETF_TICKERS:
            assert ticker not in self.result.ready_tickers

    def test_etf_tickers_not_in_partial(self):
        for ticker in ETF_TICKERS:
            assert ticker not in self.result.partial_tickers_with_missing_groups

    def test_etf_tickers_not_in_blocked(self):
        for ticker in ETF_TICKERS:
            assert ticker not in self.result.blocked_tickers_with_reason

    def test_skipped_ticker_lists_are_sorted(self):
        for reason, tickers in self.result.skipped_tickers_by_reason.items():
            assert tickers == sorted(tickers), f"Tickers for {reason} not sorted: {tickers}"

    def test_skipped_tickers_do_not_overlap_ready(self):
        all_skipped = {
            t for tickers in self.result.skipped_tickers_by_reason.values()
            for t in tickers
        }
        assert not (all_skipped & set(self.result.ready_tickers))

    def test_skipped_tickers_do_not_overlap_partial(self):
        all_skipped = {
            t for tickers in self.result.skipped_tickers_by_reason.values()
            for t in tickers
        }
        assert not (all_skipped & set(self.result.partial_tickers_with_missing_groups.keys()))


# =============================================================================
# AC 6 — BLOCKED tickers: BLSH/KLAR/TSM not retried
# =============================================================================

class TestBlockedTickers:
    def setup_method(self):
        self.result = _run_phase8f_readiness()

    def test_blocked_count_is_3(self):
        assert self.result.blocked_count == 3, self.result.blocked_tickers_with_reason

    def test_blsh_is_blocked(self):
        assert "BLSH" in self.result.blocked_tickers_with_reason

    def test_klar_is_blocked(self):
        assert "KLAR" in self.result.blocked_tickers_with_reason

    def test_tsm_is_blocked(self):
        assert "TSM" in self.result.blocked_tickers_with_reason

    def test_blocked_tickers_have_attempted_no_evidence_reason(self):
        for ticker in BLOCKED_TICKERS:
            codes = self.result.blocked_tickers_with_reason.get(ticker, [])
            assert "attempted_no_source_linked_sec_metric_evidence" in codes, (
                f"{ticker} reason codes: {codes}"
            )

    def test_blocked_tickers_have_manual_review_reason(self):
        for ticker in BLOCKED_TICKERS:
            codes = self.result.blocked_tickers_with_reason.get(ticker, [])
            assert "manual_review_required_before_retry" in codes, (
                f"{ticker} reason codes: {codes}"
            )

    def test_blocked_reason_codes_sorted(self):
        for ticker, codes in self.result.blocked_tickers_with_reason.items():
            assert codes == sorted(codes), f"{ticker} codes not sorted: {codes}"

    def test_blocked_tickers_not_in_ready(self):
        for ticker in BLOCKED_TICKERS:
            assert ticker not in self.result.ready_tickers

    def test_blocked_tickers_not_in_partial(self):
        for ticker in BLOCKED_TICKERS:
            assert ticker not in self.result.partial_tickers_with_missing_groups

    def test_blocked_tickers_not_in_skipped(self):
        all_skipped = {
            t for tickers in self.result.skipped_tickers_by_reason.values()
            for t in tickers
        }
        for ticker in BLOCKED_TICKERS:
            assert ticker not in all_skipped


# =============================================================================
# AC 7 — safe_for_decision remains False
# =============================================================================

class TestSafeForDecisionFalse:
    def test_safe_for_decision_false_on_phase8f_universe(self):
        result = _run_phase8f_readiness()
        assert result.safe_for_decision is False

    def test_safe_for_decision_false_on_empty_portfolio(self):
        result = build_sec_metric_evidence_readiness(
            portfolio_positions=[],
            snapshot_by_ticker={},
        )
        assert result.safe_for_decision is False

    def test_safe_for_decision_false_on_ready_only(self):
        result = build_sec_metric_evidence_readiness(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker={"AAPL": _ready_snapshot("AAPL")},
        )
        assert result.safe_for_decision is False

    def test_safe_for_decision_false_on_disabled_result(self):
        result = _disabled_result("test_reason")
        assert result.safe_for_decision is False

    def test_safe_for_decision_false_when_all_ready(self):
        positions = [{"ticker": t, "category": "Core"} for t in READY_TICKERS]
        snaps = {t: _ready_snapshot(t) for t in READY_TICKERS}
        result = build_sec_metric_evidence_readiness(
            portfolio_positions=positions,
            snapshot_by_ticker=snaps,
        )
        assert result.safe_for_decision is False


# =============================================================================
# AC 8 — visible_snapshot_unchanged remains True
# =============================================================================

class TestVisibleSnapshotUnchanged:
    def test_visible_snapshot_unchanged_true_on_phase8f_universe(self):
        result = _run_phase8f_readiness()
        assert result.visible_snapshot_unchanged is True

    def test_visible_snapshot_unchanged_true_on_empty(self):
        result = build_sec_metric_evidence_readiness(
            portfolio_positions=[],
            snapshot_by_ticker={},
        )
        assert result.visible_snapshot_unchanged is True

    def test_visible_snapshot_unchanged_true_on_disabled(self):
        result = _disabled_result("test_reason")
        assert result.visible_snapshot_unchanged is True


# =============================================================================
# AC 9 — Static import guard
# =============================================================================

class TestStaticImportGuard:
    """AST-based import guard — checks actual import statements only, not docstrings."""

    def _parse(self) -> ast.Module:
        return ast.parse(_ADAPTER_MODULE.read_text())

    def _collect_imports(self) -> list[tuple[str, str]]:
        """Return (module, name) pairs from all Import/ImportFrom nodes."""
        pairs: list[tuple[str, str]] = []
        for node in ast.walk(self._parse()):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    pairs.append((module, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    pairs.append(("", alias.name))
        return pairs

    def _collect_assignments(self) -> list[str]:
        """Return keyword names/values used in Call arguments (e.g., dataclass fields)."""
        values: list[str] = []
        for node in ast.walk(self._parse()):
            if isinstance(node, ast.keyword):
                if isinstance(node.value, ast.Constant):
                    values.append(f"{node.arg}={node.value.value}")
        return values

    def test_no_decide_import(self):
        for module, name in self._collect_imports():
            assert "decide" not in module, f"decide in module: {module}"
            assert "decide" not in name, f"decide in name: {name}"

    def test_no_intel_v3_service_import(self):
        for module, name in self._collect_imports():
            assert "intel_v3_service" not in module.lower(), f"intel_v3_service import: {module}"
            assert "IntelV3Service" != name, f"IntelV3Service imported as name: {name}"

    def test_no_recommendation_engine_import(self):
        for module, name in self._collect_imports():
            assert "recommendation_engine" not in module, f"rec engine import: {module}"
            assert "recommendation_engine" != name

    def test_no_decision_policy_v1_import(self):
        for module, name in self._collect_imports():
            assert "decision_policy_v1" not in module, f"decision_policy_v1 import: {module}"

    def test_no_safe_for_decision_true_assignment(self):
        assignments = self._collect_assignments()
        assert "safe_for_decision=True" not in assignments

    def test_no_visible_snapshot_unchanged_false_assignment(self):
        assignments = self._collect_assignments()
        assert "visible_snapshot_unchanged=False" not in assignments

    def test_no_frontend_path_import(self):
        for module, name in self._collect_imports():
            for forbidden in ["routers", "middleware", "models.recommendation"]:
                assert forbidden not in module, f"Frontend path import: {module}"


# =============================================================================
# AC 10 — Disabled result when kill switch is off
# =============================================================================

class TestDisabledResult:
    def test_disabled_result_adapter_enabled_false(self):
        result = _disabled_result("kill_switch_off")
        assert result.adapter_enabled is False

    def test_disabled_result_safe_for_decision_false(self):
        result = _disabled_result("kill_switch_off")
        assert result.safe_for_decision is False

    def test_disabled_result_visible_snapshot_unchanged_true(self):
        result = _disabled_result("kill_switch_off")
        assert result.visible_snapshot_unchanged is True

    def test_disabled_result_all_counts_zero(self):
        result = _disabled_result("kill_switch_off")
        assert result.portfolio_ticker_count == 0
        assert result.ready_count == 0
        assert result.partial_count == 0
        assert result.blocked_count == 0
        assert result.skipped_non_company_count == 0

    def test_disabled_result_all_lists_empty(self):
        result = _disabled_result("kill_switch_off")
        assert result.ready_tickers == []
        assert result.partial_tickers_with_missing_groups == {}
        assert result.blocked_tickers_with_reason == {}
        assert result.skipped_tickers_by_reason == {}

    def test_disabled_result_error_contains_reason(self):
        reason = "intel_v3_sec_metric_evidence_readiness_adapter_enabled=false"
        result = _disabled_result(reason)
        assert reason in result.errors

    def test_compute_returns_disabled_when_settings_flag_off(self):
        from app.services.intelligence.research_workers.sec_metric_evidence_readiness_adapter import (
            compute_sec_metric_evidence_readiness,
        )

        class _MockSettings:
            intel_v3_sec_metric_evidence_readiness_adapter_enabled = False

        class _MockDB:
            pass

        result = compute_sec_metric_evidence_readiness(
            user_id="u_test",
            db_client=_MockDB(),
            settings=_MockSettings(),
        )
        assert result.adapter_enabled is False
        assert result.safe_for_decision is False
        assert result.visible_snapshot_unchanged is True


# =============================================================================
# Additional: aggregate counts and portfolio coverage
# =============================================================================

class TestAggregateCounts:
    def setup_method(self):
        self.result = _run_phase8f_readiness()

    def test_portfolio_ticker_count_is_34(self):
        assert self.result.portfolio_ticker_count == 34, (
            f"Expected 34 (10 READY + 6 PARTIAL + 13 ETF + 2 Crypto + 3 BLOCKED), "
            f"got {self.result.portfolio_ticker_count}"
        )

    def test_ready_plus_partial_plus_blocked_plus_skipped_equals_total(self):
        total = (
            self.result.ready_count
            + self.result.partial_count
            + self.result.blocked_count
            + self.result.skipped_non_company_count
        )
        assert total == self.result.portfolio_ticker_count, (
            f"Sum {total} != portfolio_ticker_count {self.result.portfolio_ticker_count}"
        )

    def test_no_ticker_appears_in_multiple_categories(self):
        ready = set(self.result.ready_tickers)
        partial = set(self.result.partial_tickers_with_missing_groups.keys())
        blocked = set(self.result.blocked_tickers_with_reason.keys())
        skipped = {
            t for tickers in self.result.skipped_tickers_by_reason.values()
            for t in tickers
        }
        all_sets = [ready, partial, blocked, skipped]
        for i, a in enumerate(all_sets):
            for j, b in enumerate(all_sets):
                if i != j:
                    overlap = a & b
                    assert not overlap, f"Overlap between category sets {i} and {j}: {overlap}"

    def test_adapter_enabled_true_on_normal_run(self):
        assert self.result.adapter_enabled is True

    def test_errors_empty_on_clean_fixtures(self):
        assert self.result.errors == [], self.result.errors


# =============================================================================
# Additional: edge cases
# =============================================================================

class TestEdgeCases:
    def test_empty_portfolio_returns_zero_counts(self):
        result = build_sec_metric_evidence_readiness(
            portfolio_positions=[],
            snapshot_by_ticker={},
        )
        assert result.portfolio_ticker_count == 0
        assert result.ready_count == 0
        assert result.partial_count == 0
        assert result.blocked_count == 0
        assert result.skipped_non_company_count == 0

    def test_duplicate_positions_deduped(self):
        result = build_sec_metric_evidence_readiness(
            portfolio_positions=[
                {"ticker": "AAPL", "category": "Core"},
                {"ticker": "AAPL", "category": "Core"},
            ],
            snapshot_by_ticker={"AAPL": _ready_snapshot("AAPL")},
        )
        assert result.portfolio_ticker_count == 1
        assert result.ready_count == 1

    def test_unknown_ticker_with_no_snapshot_is_blocked(self):
        result = build_sec_metric_evidence_readiness(
            portfolio_positions=[{"ticker": "NEWCO", "category": "Core"}],
            snapshot_by_ticker={},
        )
        # No snapshot → BLOCKED_DRY_RUN_ONLY in Phase 8D → BLOCKED in Phase 9
        assert result.blocked_count == 1
        assert "NEWCO" in result.blocked_tickers_with_reason

    def test_single_ready_ticker(self):
        result = build_sec_metric_evidence_readiness(
            portfolio_positions=[{"ticker": "MSFT", "category": "Core"}],
            snapshot_by_ticker={"MSFT": _ready_snapshot("MSFT")},
        )
        assert result.ready_count == 1
        assert result.partial_count == 0
        assert result.blocked_count == 0
        assert result.skipped_non_company_count == 0
        assert "MSFT" in result.ready_tickers

    def test_single_etf_is_skipped_non_company(self):
        result = build_sec_metric_evidence_readiness(
            portfolio_positions=[{"ticker": "SPY", "category": "ETF"}],
            snapshot_by_ticker={},
        )
        assert result.skipped_non_company_count == 1
        assert result.ready_count == 0
        assert result.blocked_count == 0

    def test_crypto_without_category_still_skipped(self):
        result = build_sec_metric_evidence_readiness(
            portfolio_positions=[{"ticker": "BTC", "category": ""}],
            snapshot_by_ticker={},
        )
        assert result.skipped_non_company_count == 1
        crypto_tickers = result.skipped_tickers_by_reason.get("likely_crypto", [])
        assert "BTC" in crypto_tickers
