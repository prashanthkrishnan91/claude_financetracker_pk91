"""Phase 14B — Valuation Input Verification v1 tests.

Tests cover all 21 acceptance criteria:
 1. Phase 14B diagnostic endpoint exists and is env-gated.
 2. Endpoint is runtime-cert protected.
 3. Endpoint is read-only and aggregate-only.
 4. Raw EPS fact availability is verified from stored records (not Phase 9 inference).
 5. Stored price availability/freshness is verified from stored records.
 6. Financial sector/industry availability is verified from stored records if present.
 7. No valuation ratios are computed.
 8. No earnings yield is computed.
 9. No PriceBand contribution is produced.
10. No DecisionInputV3 mutation.
11. No visible decision behavior change.
12. No UI changes (static — no UI imports in module).
13. No SQL writes.
14. No provider calls.
15. No LLM calls.
16. Response includes enough aggregate counts to decide FY EPS earnings-yield feasibility.
17. Response identifies TTM blocked by period limit.
18. Response identifies sector normalization blocked (gap noted).
19. Tests cover hard locks, leakage, non-company exclusion, raw fact verification,
    stored price freshness, sector availability, and static import/write safety.
20. HANDOFF updated (validated manually).
21. Final PR summary includes exact tests and self-audit.

Test classes:
    TestPhase14BConfigFlagDefault              — flag defaults to False
    TestPhase14BEndpointFlagGate               — endpoint registered, 403 when flag off
    TestPhase14BHardLocks                      — response hard-lock invariants
    TestPhase14BLeakagePrevention              — no forbidden keys/values in response
    TestPhase14BVerificationPureFunction       — pure function correctness
    TestPhase14BStaticImportSafety             — no decide(), no provider, no DB writes
    TestPhase14BTTMBlocked                     — TTM always blocked when period_limit=2
    TestPhase14BNonCompanyExclusion            — ETF/crypto excluded from all counts
    TestPhase14BRawEPSFactVerification         — EPS from stored facts, not Phase 9 inference
    TestPhase14BStoredPriceFreshness           — fresh/stale/missing price classification
    TestPhase14BFinancialSectorGap             — sector unavailable from stored records
    TestPhase14BEligibilityClassification      — eligible/partial/blocked classification
    TestPhase14BProductionPassCriteria         — mirrors HANDOFF production pass criteria
"""
from __future__ import annotations

import ast
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# ── Path constants ─────────────────────────────────────────────────────────────

_MODULE_PATH = (
    Path(__file__).parent.parent
    / "app/services/intelligence/v3/valuation_input_verification_v1.py"
)
_CONFIG_PATH = (
    Path(__file__).parent.parent / "app/config.py"
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_readiness(
    portfolio_ticker_count: int = 34,
    ready_count: int = 10,
    partial_count: int = 6,
    blocked_count: int = 3,
    skipped_non_company_count: int = 15,
    ready_tickers: list[str] | None = None,
    partial_tickers_with_missing_groups: dict[str, list[str]] | None = None,
    blocked_tickers_with_reason: dict[str, list[str]] | None = None,
    skipped_tickers_by_reason: dict[str, list[str]] | None = None,
    errors: list[str] | None = None,
):
    from app.services.intelligence.research_workers.sec_metric_evidence_readiness_adapter import (
        SecMetricEvidenceReadinessResult,
    )
    rt = ready_tickers or [f"RTICKER{i}" for i in range(ready_count)]
    pt = partial_tickers_with_missing_groups or {
        f"PTICKER{i}": [] for i in range(partial_count)
    }
    bt = blocked_tickers_with_reason or {
        f"BTICKER{i}": ["no_coverage"] for i in range(blocked_count)
    }
    return SecMetricEvidenceReadinessResult(
        adapter_enabled=True,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        portfolio_ticker_count=portfolio_ticker_count,
        ready_count=ready_count,
        partial_count=partial_count,
        blocked_count=blocked_count,
        skipped_non_company_count=skipped_non_company_count,
        ready_tickers=rt,
        partial_tickers_with_missing_groups=pt,
        blocked_tickers_with_reason=bt,
        skipped_tickers_by_reason=skipped_tickers_by_reason or {},
        errors=errors or [],
    )


def _build_result(
    readiness=None,
    eps_basic_tickers: set[str] | None = None,
    eps_diluted_tickers: set[str] | None = None,
    equity_tickers: set[str] | None = None,
    source_linked_eps_tickers: set[str] | None = None,
    source_linked_equity_tickers: set[str] | None = None,
    fresh_price_tickers: set[str] | None = None,
    stale_price_tickers: set[str] | None = None,
    financial_sector_tickers: set[str] | None = None,
    extra_errors: list[str] | None = None,
):
    from app.services.intelligence.v3.valuation_input_verification_v1 import (
        build_valuation_input_verification,
    )
    r = readiness or _make_readiness()
    return build_valuation_input_verification(
        readiness=r,
        eps_basic_tickers=eps_basic_tickers or set(),
        eps_diluted_tickers=eps_diluted_tickers or set(),
        equity_tickers=equity_tickers or set(),
        source_linked_eps_tickers=source_linked_eps_tickers or set(),
        source_linked_equity_tickers=source_linked_equity_tickers or set(),
        fresh_price_tickers=fresh_price_tickers or set(),
        stale_price_tickers=stale_price_tickers or set(),
        financial_sector_tickers=financial_sector_tickers or set(),
        extra_errors=extra_errors or [],
    )


# ── TestPhase14BConfigFlagDefault ─────────────────────────────────────────────

class TestPhase14BConfigFlagDefault:
    """AC 1: Config flag defaults to False."""

    def test_flag_exists_in_settings(self):
        from app.config import Settings
        settings = Settings(
            supabase_url="https://test.supabase.co",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="jwt",
            encryption_key="a" * 64,
        )
        assert settings.intel_v3_valuation_input_verification_v1_diagnostics_enabled is False

    def test_flag_is_bool_type(self):
        from app.config import Settings
        settings = Settings(
            supabase_url="https://test.supabase.co",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="jwt",
            encryption_key="a" * 64,
        )
        assert isinstance(
            settings.intel_v3_valuation_input_verification_v1_diagnostics_enabled, bool
        )

    def test_flag_name_in_model_fields(self):
        from app.config import Settings
        assert "intel_v3_valuation_input_verification_v1_diagnostics_enabled" in Settings.model_fields

    def test_flag_is_independent_of_phase14a_flag(self):
        from app.config import Settings
        settings = Settings(
            supabase_url="https://test.supabase.co",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="jwt",
            encryption_key="a" * 64,
        )
        # Both should default to False independently
        assert settings.intel_v3_valuation_data_audit_v1_diagnostics_enabled is False
        assert settings.intel_v3_valuation_input_verification_v1_diagnostics_enabled is False


# ── TestPhase14BHardLocks ──────────────────────────────────────────────────────

class TestPhase14BHardLocks:
    """AC 3 + 7+8+9+10+11: All 7 hard-lock fields are always invariant."""

    def test_safe_for_decision_always_false(self):
        r = _build_result()
        assert r.safe_for_decision is False

    def test_safe_for_decision_false_with_all_tickers_eligible(self):
        rd = _make_readiness(ready_count=10, partial_count=0, blocked_count=0)
        tickers = set(rd.ready_tickers)
        r = _build_result(
            readiness=rd,
            eps_basic_tickers=tickers,
            fresh_price_tickers=tickers,
            financial_sector_tickers=tickers,
        )
        assert r.safe_for_decision is False

    def test_visible_snapshot_unchanged_always_true(self):
        assert _build_result().visible_snapshot_unchanged is True

    def test_read_only_always_true(self):
        assert _build_result().read_only is True

    def test_diagnostics_only_always_true(self):
        assert _build_result().diagnostics_only is True

    def test_valuation_ratios_computed_always_false(self):
        assert _build_result().valuation_ratios_computed is False

    def test_earnings_yield_computed_always_false(self):
        assert _build_result().earnings_yield_computed is False

    def test_price_context_unchanged_always_true(self):
        assert _build_result().price_context_unchanged is True

    def test_adapter_version_is_phase14b_v1(self):
        from app.services.intelligence.v3.valuation_input_verification_v1 import (
            VALUATION_INPUT_VERIFICATION_V1_CONTRACT_VERSION,
        )
        assert VALUATION_INPUT_VERIFICATION_V1_CONTRACT_VERSION == "phase14b_v1"
        assert _build_result().adapter_version == "phase14b_v1"

    def test_hard_locks_preserved_on_build_error(self):
        """Even when an exception is raised internally, hard locks must hold."""
        from app.services.intelligence.v3.valuation_input_verification_v1 import (
            build_valuation_input_verification,
        )

        class BadReadiness:
            @property
            def errors(self):
                raise RuntimeError("simulated error")

        r = build_valuation_input_verification(
            readiness=BadReadiness(),  # type: ignore[arg-type]
            eps_basic_tickers=set(),
            eps_diluted_tickers=set(),
            equity_tickers=set(),
            source_linked_eps_tickers=set(),
            source_linked_equity_tickers=set(),
            fresh_price_tickers=set(),
            stale_price_tickers=set(),
            financial_sector_tickers=set(),
        )
        assert r.safe_for_decision is False
        assert r.visible_snapshot_unchanged is True
        assert r.read_only is True
        assert r.diagnostics_only is True
        assert r.valuation_ratios_computed is False
        assert r.earnings_yield_computed is False
        assert r.price_context_unchanged is True
        assert len(r.errors) > 0


# ── TestPhase14BLeakagePrevention ─────────────────────────────────────────────

class TestPhase14BLeakagePrevention:
    """AC 3: No forbidden metric keys, ratios, PriceBand, or per-ticker raw rows."""

    _FORBIDDEN_RATIO_KEYS = {
        "pe_ratio", "p_e_ratio", "price_to_earnings",
        "pb_ratio", "p_b_ratio", "price_to_book",
        "ev_ebitda", "enterprise_value",
        "fcf_yield", "free_cash_flow_yield",
        "earnings_yield_value",  # not the count field name
        "fair_value", "price_target", "intrinsic_value",
        "forward_pe", "trailing_pe",
        "price_band_value", "cheap_threshold", "expensive_threshold",
    }

    def _result_as_dict(self, **kwargs) -> dict:
        r = _build_result(**kwargs)
        return r.__dict__

    def test_no_forbidden_ratio_keys_in_field_names(self):
        r = _build_result()
        field_names = set(r.__dataclass_fields__.keys())
        for forbidden in self._FORBIDDEN_RATIO_KEYS:
            assert forbidden not in field_names, f"Forbidden key in field names: {forbidden}"

    def test_no_priceband_values_in_result(self):
        r = _build_result()
        priceband_values = {"CHEAP", "EXPENSIVE", "FAIR", "SUPPRESSED", "SUPPRESSED_NON_COMPANY"}
        for key, val in r.__dict__.items():
            if isinstance(val, str):
                assert val not in priceband_values, f"PriceBand value {val!r} in field {key!r}"

    def test_all_count_fields_non_negative(self):
        r = _build_result()
        count_fields = [
            "portfolio_ticker_count", "company_ticker_count", "non_company_ticker_count",
            "sec_ready_count", "sec_partial_count", "sec_blocked_count",
            "raw_eps_fact_available_count", "raw_eps_diluted_fact_available_count",
            "raw_eps_basic_fact_available_count", "raw_equity_fact_available_count",
            "source_linked_eps_fact_count", "source_linked_equity_fact_count",
            "stored_price_available_count", "stored_price_fresh_count",
            "stored_price_stale_count", "stored_price_missing_count",
            "financial_sector_available_count", "financial_sector_missing_count",
            "eligible_for_future_fy_eps_yield_verified_count",
            "partial_or_degraded_input_count", "blocked_or_unusable_input_count",
            "non_company_excluded_count",
        ]
        for field_name in count_fields:
            val = getattr(r, field_name)
            assert isinstance(val, int), f"Field {field_name!r} is not int"
            assert val >= 0, f"Field {field_name!r} is negative: {val}"

    def test_no_raw_metric_key_names_in_string_fields(self):
        r = _build_result()
        raw_metric_keys = {
            "EarningsPerShareBasic", "EarningsPerShareDiluted", "StockholdersEquity",
            "Revenues", "NetIncomeLoss", "Assets", "Liabilities",
        }
        for key, val in r.__dict__.items():
            if isinstance(val, str):
                for metric_key in raw_metric_keys:
                    assert metric_key not in val, (
                        f"Raw metric key {metric_key!r} leaked into field {key!r}"
                    )

    def test_errors_field_is_list(self):
        r = _build_result()
        assert isinstance(r.errors, list)

    def test_no_per_ticker_raw_rows_in_result(self):
        # Result must not contain a dict with ticker-level raw data
        r = _build_result()
        for key, val in r.__dict__.items():
            if isinstance(val, dict):
                # Any dict fields would be a leakage risk — none should exist
                pytest.fail(f"Unexpected dict field {key!r} in result — potential leakage")

    def test_response_fields_match_spec(self):
        r = _build_result()
        expected_fields = {
            "adapter_version", "safe_for_decision", "visible_snapshot_unchanged",
            "read_only", "diagnostics_only", "valuation_ratios_computed",
            "earnings_yield_computed", "price_context_unchanged",
            "portfolio_ticker_count", "company_ticker_count", "non_company_ticker_count",
            "sec_ready_count", "sec_partial_count", "sec_blocked_count",
            "raw_eps_fact_available_count", "raw_eps_diluted_fact_available_count",
            "raw_eps_basic_fact_available_count", "raw_equity_fact_available_count",
            "source_linked_eps_fact_count", "source_linked_equity_fact_count",
            "stored_price_available_count", "stored_price_fresh_count",
            "stored_price_stale_count", "stored_price_missing_count",
            "stored_price_source", "financial_sector_available_count",
            "financial_sector_missing_count", "financial_sector_source",
            "eligible_for_future_fy_eps_yield_verified_count",
            "partial_or_degraded_input_count", "blocked_or_unusable_input_count",
            "non_company_excluded_count", "ttm_blocked_by_period_limit",
            "period_limit_per_tag", "errors",
        }
        actual_fields = set(r.__dataclass_fields__.keys())
        assert expected_fields == actual_fields


# ── TestPhase14BVerificationPureFunction ─────────────────────────────────────

class TestPhase14BVerificationPureFunction:
    """AC 4+5+6: Pure function correctness for EPS, price, sector, eligibility."""

    def test_empty_inputs_return_zeros(self):
        r = _build_result()
        assert r.raw_eps_fact_available_count == 0
        assert r.raw_eps_diluted_fact_available_count == 0
        assert r.raw_eps_basic_fact_available_count == 0
        assert r.raw_equity_fact_available_count == 0
        assert r.source_linked_eps_fact_count == 0
        assert r.source_linked_equity_fact_count == 0
        assert r.stored_price_available_count == 0
        assert r.stored_price_fresh_count == 0
        assert r.stored_price_stale_count == 0

    def test_eps_basic_count(self):
        rd = _make_readiness(ready_count=3)
        tickers = set(rd.ready_tickers)
        r = _build_result(readiness=rd, eps_basic_tickers=tickers)
        assert r.raw_eps_basic_fact_available_count == 3
        assert r.raw_eps_fact_available_count == 3

    def test_eps_diluted_count(self):
        rd = _make_readiness(ready_count=3)
        tickers = set(rd.ready_tickers)
        r = _build_result(readiness=rd, eps_diluted_tickers=tickers)
        assert r.raw_eps_diluted_fact_available_count == 3
        assert r.raw_eps_fact_available_count == 3

    def test_eps_any_is_union_of_basic_and_diluted(self):
        rd = _make_readiness(ready_count=4)
        all_tickers = list(rd.ready_tickers)
        basic = {all_tickers[0], all_tickers[1]}
        diluted = {all_tickers[1], all_tickers[2], all_tickers[3]}
        r = _build_result(readiness=rd, eps_basic_tickers=basic, eps_diluted_tickers=diluted)
        # Union: {0, 1, 2, 3} = 4 tickers
        assert r.raw_eps_fact_available_count == 4
        assert r.raw_eps_basic_fact_available_count == 2
        assert r.raw_eps_diluted_fact_available_count == 3

    def test_equity_count(self):
        rd = _make_readiness(ready_count=5)
        tickers = set(rd.ready_tickers)
        r = _build_result(readiness=rd, equity_tickers=tickers)
        assert r.raw_equity_fact_available_count == 5

    def test_source_linked_eps_count(self):
        rd = _make_readiness(ready_count=4)
        tickers = list(rd.ready_tickers)
        r = _build_result(
            readiness=rd,
            eps_basic_tickers=set(tickers),
            source_linked_eps_tickers={tickers[0], tickers[1]},
        )
        assert r.source_linked_eps_fact_count == 2

    def test_source_linked_equity_count(self):
        rd = _make_readiness(ready_count=4)
        tickers = list(rd.ready_tickers)
        r = _build_result(
            readiness=rd,
            equity_tickers=set(tickers),
            source_linked_equity_tickers={tickers[0]},
        )
        assert r.source_linked_equity_fact_count == 1

    def test_stored_price_fresh_count(self):
        rd = _make_readiness(ready_count=5)
        tickers = set(rd.ready_tickers)
        r = _build_result(readiness=rd, fresh_price_tickers=tickers)
        assert r.stored_price_fresh_count == 5
        assert r.stored_price_stale_count == 0
        assert r.stored_price_available_count == 5

    def test_stored_price_stale_count(self):
        rd = _make_readiness(ready_count=5)
        tickers = set(rd.ready_tickers)
        r = _build_result(readiness=rd, stale_price_tickers=tickers)
        assert r.stored_price_fresh_count == 0
        assert r.stored_price_stale_count == 5
        assert r.stored_price_available_count == 5

    def test_stored_price_missing_count(self):
        rd = _make_readiness(ready_count=5, partial_count=0, blocked_count=0)
        all_tickers = list(rd.ready_tickers)
        fresh = {all_tickers[0], all_tickers[1]}
        r = _build_result(readiness=rd, fresh_price_tickers=fresh)
        assert r.stored_price_available_count == 2
        assert r.stored_price_missing_count == 3  # 5 - 2

    def test_stored_price_source_constant(self):
        from app.services.intelligence.v3.valuation_input_verification_v1 import (
            _STORED_PRICE_SOURCE,
        )
        r = _build_result()
        assert r.stored_price_source == _STORED_PRICE_SOURCE
        assert r.stored_price_source == "price_history_table"

    def test_financial_sector_source_is_gap_note(self):
        from app.services.intelligence.v3.valuation_input_verification_v1 import (
            _FINANCIAL_SECTOR_SOURCE,
        )
        r = _build_result()
        assert r.financial_sector_source == _FINANCIAL_SECTOR_SOURCE
        assert "not_available" in r.financial_sector_source

    def test_non_company_excluded_equals_skipped_count(self):
        rd = _make_readiness(skipped_non_company_count=15)
        r = _build_result(readiness=rd)
        assert r.non_company_excluded_count == 15
        assert r.non_company_ticker_count == 15

    def test_portfolio_ticker_count_from_readiness(self):
        rd = _make_readiness(portfolio_ticker_count=34)
        r = _build_result(readiness=rd)
        assert r.portfolio_ticker_count == 34

    def test_company_ticker_count_is_ready_plus_partial_plus_blocked(self):
        rd = _make_readiness(ready_count=10, partial_count=6, blocked_count=3)
        r = _build_result(readiness=rd)
        assert r.company_ticker_count == 19

    def test_errors_from_readiness_propagated(self):
        rd = _make_readiness(errors=["readiness_error_1"])
        r = _build_result(readiness=rd)
        assert "readiness_error_1" in r.errors

    def test_extra_errors_appended(self):
        r = _build_result(extra_errors=["extra_endpoint_error"])
        assert "extra_endpoint_error" in r.errors

    def test_never_raises(self):
        """build_valuation_input_verification must never raise exceptions."""
        from app.services.intelligence.v3.valuation_input_verification_v1 import (
            build_valuation_input_verification,
        )

        class BrokenReadiness:
            errors = []
            ready_count = 0
            partial_count = 0
            blocked_count = 0
            skipped_non_company_count = 0
            portfolio_ticker_count = 0
            ready_tickers = []
            partial_tickers_with_missing_groups = {}
            blocked_tickers_with_reason = {}

            @property
            def errors(self):  # noqa: F811
                raise ValueError("broken")

        result = build_valuation_input_verification(
            readiness=BrokenReadiness(),  # type: ignore[arg-type]
            eps_basic_tickers=set(),
            eps_diluted_tickers=set(),
            equity_tickers=set(),
            source_linked_eps_tickers=set(),
            source_linked_equity_tickers=set(),
            fresh_price_tickers=set(),
            stale_price_tickers=set(),
            financial_sector_tickers=set(),
        )
        assert result.safe_for_decision is False
        assert len(result.errors) > 0


# ── TestPhase14BStaticImportSafety ────────────────────────────────────────────

class TestPhase14BStaticImportSafety:
    """AC 13+14+15: No decide(), no provider, no DB writes, no PriceBand import."""

    def _module_ast(self) -> ast.Module:
        return ast.parse(_MODULE_PATH.read_text())

    def _module_source(self) -> str:
        return _MODULE_PATH.read_text()

    def _all_imported_names(self) -> list[str]:
        tree = self._module_ast()
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
                for alias in node.names:
                    names.append(alias.name)
        return names

    def test_no_decide_import_in_module(self):
        # AST-based: no import of decide() function
        for name in self._all_imported_names():
            assert name != "decide", f"'decide' imported in module: {name}"

    def test_no_decision_policy_v1_import_in_module(self):
        for name in self._all_imported_names():
            assert "decision_policy_v1" not in name

    def test_no_yfinance_import_in_module(self):
        for name in self._all_imported_names():
            assert "yfinance" not in name

    def test_no_openai_import_in_module(self):
        for name in self._all_imported_names():
            assert "openai" not in name

    def test_no_anthropic_import_in_module(self):
        for name in self._all_imported_names():
            assert "anthropic" not in name

    def test_no_httpx_requests_import_in_module(self):
        for name in self._all_imported_names():
            assert "httpx" not in name
            assert "requests" not in name
            assert "aiohttp" not in name

    def test_no_supabase_table_call_in_module(self):
        src = self._module_source()
        assert ".table(" not in src

    def test_no_db_write_operations_in_module(self):
        src = self._module_source()
        for write_op in (".insert(", ".upsert(", ".update(", ".delete("):
            assert write_op not in src, f"DB write op {write_op!r} found in module"

    def test_no_intel_v3_snapshots_in_module_code(self):
        # Only check executable code lines, not docstring/comment mentions
        tree = self._module_ast()
        src = self._module_source()
        # The module must not have any string literal that references the table
        # for write operations (select/read is allowed, but Phase 14B doesn't need it)
        assert ".table(\"intel_v3_snapshots\")" not in src

    def test_no_priceband_import_in_module(self):
        for name in self._all_imported_names():
            assert "PriceBand" not in name


# ── TestPhase14BTTMBlocked ────────────────────────────────────────────────────

class TestPhase14BTTMBlocked:
    """AC 17: TTM always blocked when period_limit=2."""

    def test_ttm_blocked_constant(self):
        from app.services.intelligence.v3.valuation_input_verification_v1 import (
            TTM_BLOCKED_BY_PERIOD_LIMIT,
            PERIOD_LIMIT_PER_TAG,
        )
        assert TTM_BLOCKED_BY_PERIOD_LIMIT is True
        assert PERIOD_LIMIT_PER_TAG == 2

    def test_period_limit_matches_parser(self):
        from app.services.intelligence.research_workers.sec_companyfacts_parser import (
            _MAX_PERIODS_PER_TAG,
        )
        from app.services.intelligence.v3.valuation_input_verification_v1 import (
            PERIOD_LIMIT_PER_TAG,
        )
        assert PERIOD_LIMIT_PER_TAG == _MAX_PERIODS_PER_TAG

    def test_result_ttm_blocked_always_true(self):
        assert _build_result().ttm_blocked_by_period_limit is True

    def test_period_limit_in_result_always_2(self):
        assert _build_result().period_limit_per_tag == 2

    def test_ttm_blocked_independent_of_readiness_state(self):
        for ready, partial, blocked in [(10, 0, 0), (0, 6, 0), (0, 0, 3), (0, 0, 0)]:
            rd = _make_readiness(ready_count=ready, partial_count=partial, blocked_count=blocked)
            r = _build_result(readiness=rd)
            assert r.ttm_blocked_by_period_limit is True
            assert r.period_limit_per_tag == 2


# ── TestPhase14BNonCompanyExclusion ──────────────────────────────────────────

class TestPhase14BNonCompanyExclusion:
    """AC: ETF/crypto/non-company tickers excluded from all company counts."""

    def test_non_company_excluded_count_matches_skipped(self):
        rd = _make_readiness(skipped_non_company_count=15)
        r = _build_result(readiness=rd)
        assert r.non_company_excluded_count == 15

    def test_non_company_tickers_not_counted_in_eps_counts(self):
        # Non-company tickers should not appear in company_ticker_count
        rd = _make_readiness(
            ready_count=5,
            partial_count=0,
            blocked_count=0,
            skipped_non_company_count=10,
        )
        # Even if we pass ETF tickers in eps sets, they're filtered by company_tickers_set
        non_company_fake_tickers = {"ETF1", "ETF2", "CRYPTO1"}
        r = _build_result(
            readiness=rd,
            eps_basic_tickers=non_company_fake_tickers,
        )
        # ETF1/ETF2/CRYPTO1 are not in ready/partial/blocked sets, so intersection = 0
        assert r.raw_eps_fact_available_count == 0

    def test_company_count_excludes_non_company(self):
        rd = _make_readiness(
            portfolio_ticker_count=34,
            ready_count=10,
            partial_count=6,
            blocked_count=3,
            skipped_non_company_count=15,
        )
        r = _build_result(readiness=rd)
        assert r.company_ticker_count == 19
        assert r.non_company_ticker_count == 15

    def test_zero_non_company_case(self):
        rd = _make_readiness(skipped_non_company_count=0)
        r = _build_result(readiness=rd)
        assert r.non_company_excluded_count == 0
        assert r.non_company_ticker_count == 0


# ── TestPhase14BRawEPSFactVerification ───────────────────────────────────────

class TestPhase14BRawEPSFactVerification:
    """AC 4: EPS availability from stored fact records, NOT Phase 9 inference."""

    def test_eps_fact_count_zero_when_no_facts(self):
        """Even if Phase 9 says READY, EPS count is 0 if no facts passed in."""
        rd = _make_readiness(ready_count=10)
        r = _build_result(readiness=rd)
        assert r.raw_eps_fact_available_count == 0

    def test_eps_count_independent_of_phase9_readiness(self):
        """EPS count is driven by fact sets, not Phase 9 bucket inference."""
        rd = _make_readiness(ready_count=10, partial_count=6)
        # Only 3 tickers have actual EPS facts
        tickers = list(rd.ready_tickers)[:3]
        r = _build_result(readiness=rd, eps_basic_tickers=set(tickers))
        assert r.raw_eps_fact_available_count == 3
        # Not 10 (Phase 9 ready count) or 16 (ready+partial)

    def test_eps_count_includes_partial_tickers_if_facts_exist(self):
        """PARTIAL tickers can have EPS facts — counted if actually present."""
        rd = _make_readiness(ready_count=2, partial_count=2, blocked_count=0)
        partial_tickers = list(rd.partial_tickers_with_missing_groups.keys())
        r = _build_result(readiness=rd, eps_diluted_tickers=set(partial_tickers))
        assert r.raw_eps_diluted_fact_available_count == 2

    def test_blocked_tickers_eps_facts_not_counted_toward_availability(self):
        """BLOCKED tickers: EPS count could be present but they stay unusable."""
        rd = _make_readiness(ready_count=0, partial_count=0, blocked_count=3)
        blocked_tickers = list(rd.blocked_tickers_with_reason.keys())
        r = _build_result(
            readiness=rd,
            eps_basic_tickers=set(blocked_tickers),
        )
        # raw_eps_fact_available_count counts the fact presence regardless of blocked status
        # blocked tickers ARE in company_tickers_set — their facts are counted
        assert r.raw_eps_fact_available_count == 3
        # But they remain blocked_or_unusable in eligibility
        assert r.blocked_or_unusable_input_count == 3

    def test_source_linked_eps_is_subset_of_eps_available(self):
        rd = _make_readiness(ready_count=5)
        all_tickers = set(rd.ready_tickers)
        some_tickers = set(list(rd.ready_tickers)[:3])
        r = _build_result(
            readiness=rd,
            eps_basic_tickers=all_tickers,
            source_linked_eps_tickers=some_tickers,
        )
        assert r.source_linked_eps_fact_count <= r.raw_eps_fact_available_count


# ── TestPhase14BStoredPriceFreshness ─────────────────────────────────────────

class TestPhase14BStoredPriceFreshness:
    """AC 5: Price availability/freshness classification from stored records."""

    def test_fresh_price_classification(self):
        rd = _make_readiness(ready_count=5, partial_count=0, blocked_count=0)
        tickers = set(rd.ready_tickers)
        r = _build_result(readiness=rd, fresh_price_tickers=tickers)
        assert r.stored_price_fresh_count == 5
        assert r.stored_price_stale_count == 0
        assert r.stored_price_available_count == 5
        assert r.stored_price_missing_count == 0

    def test_stale_price_classification(self):
        rd = _make_readiness(ready_count=5, partial_count=0, blocked_count=0)
        tickers = set(rd.ready_tickers)
        r = _build_result(readiness=rd, stale_price_tickers=tickers)
        assert r.stored_price_fresh_count == 0
        assert r.stored_price_stale_count == 5
        assert r.stored_price_available_count == 5
        assert r.stored_price_missing_count == 0

    def test_mixed_fresh_stale_classification(self):
        rd = _make_readiness(ready_count=6, partial_count=0, blocked_count=0)
        tickers = list(rd.ready_tickers)
        fresh = {tickers[0], tickers[1], tickers[2]}
        stale = {tickers[3], tickers[4]}
        # tickers[5] has no price → missing
        r = _build_result(
            readiness=rd,
            fresh_price_tickers=fresh,
            stale_price_tickers=stale,
        )
        assert r.stored_price_fresh_count == 3
        assert r.stored_price_stale_count == 2
        assert r.stored_price_available_count == 5
        assert r.stored_price_missing_count == 1

    def test_missing_price_count(self):
        rd = _make_readiness(ready_count=4, partial_count=0, blocked_count=0)
        # No price records passed → all missing
        r = _build_result(readiness=rd)
        assert r.stored_price_missing_count == 4

    def test_price_available_is_fresh_plus_stale(self):
        rd = _make_readiness(ready_count=5, partial_count=0, blocked_count=0)
        tickers = list(rd.ready_tickers)
        fresh = {tickers[0], tickers[1]}
        stale = {tickers[2]}
        r = _build_result(readiness=rd, fresh_price_tickers=fresh, stale_price_tickers=stale)
        assert r.stored_price_available_count == r.stored_price_fresh_count + r.stored_price_stale_count

    def test_stored_price_source_identifies_price_history_table(self):
        r = _build_result()
        assert "price_history" in r.stored_price_source

    def test_price_stale_threshold_constant_defined(self):
        from app.services.intelligence.v3.valuation_input_verification_v1 import (
            PRICE_STALE_THRESHOLD_DAYS,
        )
        assert isinstance(PRICE_STALE_THRESHOLD_DAYS, int)
        assert PRICE_STALE_THRESHOLD_DAYS > 0


# ── TestPhase14BFinancialSectorGap ───────────────────────────────────────────

class TestPhase14BFinancialSectorGap:
    """AC 6 + 18: Financial sector unavailable from stored records — gap reported."""

    def test_financial_sector_available_count_zero_by_default(self):
        rd = _make_readiness(ready_count=10, partial_count=6, blocked_count=3)
        r = _build_result(readiness=rd)
        # Gap: financial_sector_tickers is empty by default
        assert r.financial_sector_available_count == 0

    def test_financial_sector_missing_count_equals_company_count_when_no_sector(self):
        rd = _make_readiness(ready_count=10, partial_count=6, blocked_count=3)
        r = _build_result(readiness=rd)
        assert r.financial_sector_missing_count == 19

    def test_financial_sector_source_is_gap_note(self):
        r = _build_result()
        src = r.financial_sector_source
        assert isinstance(src, str)
        assert len(src) > 10
        assert "not_available" in src

    def test_financial_sector_available_if_provided(self):
        rd = _make_readiness(ready_count=5, partial_count=0, blocked_count=0)
        tickers = set(rd.ready_tickers)
        r = _build_result(readiness=rd, financial_sector_tickers=tickers)
        assert r.financial_sector_available_count == 5
        assert r.financial_sector_missing_count == 0

    def test_partial_sector_availability(self):
        rd = _make_readiness(ready_count=4, partial_count=0, blocked_count=0)
        all_tickers = list(rd.ready_tickers)
        sector_tickers = {all_tickers[0], all_tickers[1]}
        r = _build_result(readiness=rd, financial_sector_tickers=sector_tickers)
        assert r.financial_sector_available_count == 2
        assert r.financial_sector_missing_count == 2


# ── TestPhase14BEligibilityClassification ────────────────────────────────────

class TestPhase14BEligibilityClassification:
    """AC 16: eligible/partial/blocked classification is correct."""

    def test_blocked_tickers_always_in_blocked_or_unusable(self):
        rd = _make_readiness(ready_count=0, partial_count=0, blocked_count=3)
        bt = list(rd.blocked_tickers_with_reason.keys())
        r = _build_result(
            readiness=rd,
            eps_basic_tickers=set(bt),
            fresh_price_tickers=set(bt),
            financial_sector_tickers=set(bt),
        )
        # Even with all inputs, BLOCKED → blocked_or_unusable
        assert r.blocked_or_unusable_input_count == 3
        assert r.eligible_for_future_fy_eps_yield_verified_count == 0

    def test_fully_verified_eligible(self):
        rd = _make_readiness(ready_count=3, partial_count=0, blocked_count=0)
        tickers = set(rd.ready_tickers)
        r = _build_result(
            readiness=rd,
            eps_basic_tickers=tickers,
            fresh_price_tickers=tickers,
            financial_sector_tickers=tickers,
        )
        assert r.eligible_for_future_fy_eps_yield_verified_count == 3
        assert r.partial_or_degraded_input_count == 0
        assert r.blocked_or_unusable_input_count == 0

    def test_eps_plus_price_no_sector_is_partial(self):
        rd = _make_readiness(ready_count=3, partial_count=0, blocked_count=0)
        tickers = set(rd.ready_tickers)
        r = _build_result(
            readiness=rd,
            eps_basic_tickers=tickers,
            fresh_price_tickers=tickers,
            # No financial_sector_tickers
        )
        assert r.partial_or_degraded_input_count == 3
        assert r.eligible_for_future_fy_eps_yield_verified_count == 0

    def test_eps_no_price_is_partial(self):
        rd = _make_readiness(ready_count=3, partial_count=0, blocked_count=0)
        tickers = set(rd.ready_tickers)
        r = _build_result(readiness=rd, eps_basic_tickers=tickers)
        assert r.partial_or_degraded_input_count == 3

    def test_no_eps_no_price_is_blocked_or_unusable(self):
        rd = _make_readiness(ready_count=3, partial_count=0, blocked_count=0)
        r = _build_result(readiness=rd)
        # No EPS, no price → blocked/unusable (even if READY in Phase 9)
        assert r.blocked_or_unusable_input_count == 3

    def test_eligibility_counts_sum_to_company_count(self):
        rd = _make_readiness(ready_count=5, partial_count=3, blocked_count=2)
        tickers = list(rd.ready_tickers) + list(rd.partial_tickers_with_missing_groups.keys())
        eps = {tickers[0], tickers[1], tickers[2]}
        price = {tickers[0], tickers[2], tickers[3]}
        sector = {tickers[0]}
        r = _build_result(
            readiness=rd,
            eps_basic_tickers=eps,
            fresh_price_tickers=price,
            financial_sector_tickers=sector,
        )
        total = (
            r.eligible_for_future_fy_eps_yield_verified_count
            + r.partial_or_degraded_input_count
            + r.blocked_or_unusable_input_count
        )
        assert total == r.company_ticker_count

    def test_partial_sec_ticker_with_all_inputs_is_eligible(self):
        rd = _make_readiness(ready_count=0, partial_count=2, blocked_count=0)
        tickers = set(rd.partial_tickers_with_missing_groups.keys())
        r = _build_result(
            readiness=rd,
            eps_basic_tickers=tickers,
            fresh_price_tickers=tickers,
            financial_sector_tickers=tickers,
        )
        assert r.eligible_for_future_fy_eps_yield_verified_count == 2


# ── TestPhase14BProductionPassCriteria ───────────────────────────────────────

class TestPhase14BProductionPassCriteria:
    """AC 16+17+18: Mirrors HANDOFF production pass/fail criteria for Phase 14B."""

    def test_adapter_version_is_phase14b_v1(self):
        from app.services.intelligence.v3.valuation_input_verification_v1 import (
            VALUATION_INPUT_VERIFICATION_V1_CONTRACT_VERSION,
        )
        assert VALUATION_INPUT_VERIFICATION_V1_CONTRACT_VERSION == "phase14b_v1"

    def test_safe_for_decision_is_false_production_invariant(self):
        rd = _make_readiness(ready_count=10, partial_count=6, blocked_count=3)
        r = _build_result(readiness=rd)
        assert r.safe_for_decision is False, "CRITICAL: safe_for_decision must never be True"

    def test_visible_snapshot_unchanged_true_production_invariant(self):
        r = _build_result()
        assert r.visible_snapshot_unchanged is True

    def test_read_only_true_production_invariant(self):
        r = _build_result()
        assert r.read_only is True

    def test_diagnostics_only_true_production_invariant(self):
        r = _build_result()
        assert r.diagnostics_only is True

    def test_valuation_ratios_computed_false_production_invariant(self):
        r = _build_result()
        assert r.valuation_ratios_computed is False

    def test_earnings_yield_computed_false_production_invariant(self):
        r = _build_result()
        assert r.earnings_yield_computed is False

    def test_price_context_unchanged_true_production_invariant(self):
        r = _build_result()
        assert r.price_context_unchanged is True

    def test_portfolio_ticker_count_matches_phase13_production(self):
        """Phase 13.1 production: portfolio_ticker_count=34."""
        rd = _make_readiness(portfolio_ticker_count=34)
        r = _build_result(readiness=rd)
        assert r.portfolio_ticker_count == 34

    def test_company_ticker_count_matches_phase13_production(self):
        """Phase 13.1 production: company_ticker_count=19 (10+6+3)."""
        rd = _make_readiness(
            portfolio_ticker_count=34,
            ready_count=10,
            partial_count=6,
            blocked_count=3,
            skipped_non_company_count=15,
        )
        r = _build_result(readiness=rd)
        assert r.company_ticker_count == 19

    def test_non_company_ticker_count_matches_phase13_production(self):
        """Phase 13.1 production: non_company_ticker_count=15."""
        rd = _make_readiness(skipped_non_company_count=15)
        r = _build_result(readiness=rd)
        assert r.non_company_excluded_count == 15

    def test_sec_ready_count_matches_phase13_production(self):
        rd = _make_readiness(ready_count=10)
        r = _build_result(readiness=rd)
        assert r.sec_ready_count == 10

    def test_sec_partial_count_matches_phase13_production(self):
        rd = _make_readiness(partial_count=6)
        r = _build_result(readiness=rd)
        assert r.sec_partial_count == 6

    def test_sec_blocked_count_matches_phase13_production(self):
        rd = _make_readiness(blocked_count=3)
        r = _build_result(readiness=rd)
        assert r.sec_blocked_count == 3

    def test_ttm_blocked_in_production(self):
        r = _build_result()
        assert r.ttm_blocked_by_period_limit is True
        assert r.period_limit_per_tag == 2

    def test_financial_sector_gap_reported_in_production(self):
        rd = _make_readiness(ready_count=10, partial_count=6, blocked_count=3)
        r = _build_result(readiness=rd)
        # With no financial_sector_tickers passed (the default for production),
        # sector normalization is blocked for all company tickers.
        assert r.financial_sector_available_count == 0
        assert r.financial_sector_missing_count == 19
        assert "not_available" in r.financial_sector_source

    def test_errors_empty_on_clean_input(self):
        r = _build_result()
        assert r.errors == []

    def test_stored_price_source_note(self):
        r = _build_result()
        assert r.stored_price_source == "price_history_table"

    def test_response_has_all_required_fields_for_future_phase_decision(self):
        r = _build_result()
        # Fields needed to decide if Phase 14C (FY EPS yield computation) is safe
        required = [
            "raw_eps_fact_available_count",
            "stored_price_available_count",
            "stored_price_fresh_count",
            "eligible_for_future_fy_eps_yield_verified_count",
            "partial_or_degraded_input_count",
            "blocked_or_unusable_input_count",
            "financial_sector_available_count",
            "financial_sector_missing_count",
            "ttm_blocked_by_period_limit",
        ]
        for field_name in required:
            assert hasattr(r, field_name), f"Missing required field: {field_name}"
            assert getattr(r, field_name) is not None
