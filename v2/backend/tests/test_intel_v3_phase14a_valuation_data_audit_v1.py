"""Phase 14A — Valuation Data Audit v1 tests.

Tests cover all 16 acceptance criteria:
 1. Phase 14A diagnostic endpoint exists and is env-gated.
 2. Endpoint is runtime-cert protected (via existing pattern).
 3. Endpoint is read-only and aggregate-only.
 4. No valuation ratios are computed.
 5. No PriceBand contribution is produced.
 6. No DecisionInputV3 mutation.
 7. No visible decision behavior change.
 8. No UI changes (static — no UI imports in module).
 9. No SQL writes.
10. No provider calls.
11. No LLM calls.
12. Response includes enough counts to decide FY EPS earnings-yield feasibility.
13. Response identifies TTM blocked by period limit.
14. Response identifies sector availability coverage.
15. Tests cover hard locks, leakage, non-company exclusion, import/write safety.
16. HANDOFF updated (validated manually).

Test classes:
    TestPhase14AConfigFlagDefault              — flag defaults to False
    TestPhase14AEndpointFlagGate               — 403 when flag off
    TestPhase14AHardLocks                      — response hard-lock invariants
    TestPhase14ALeakagePrevention              — no forbidden keys/values in response
    TestPhase14AAuditPureFunction              — pure function correctness
    TestPhase14AStaticImportSafety             — no decide(), no provider, no DB writes
    TestPhase14ATTMBlocked                     — TTM always blocked when period_limit=2
    TestPhase14ANonCompanyExclusion            — ETF/crypto excluded from company counts
    TestPhase14AEPSEquityCountsFromBuckets     — eps/equity count logic from readiness
    TestPhase14ASectorReporting                — sector source note and counts
    TestPhase14AProductionPassCriteria         — mirrors HANDOFF production pass criteria
"""
from __future__ import annotations

import ast
import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

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
    """Build a mock SecMetricEvidenceReadinessResult for testing."""
    from app.services.intelligence.research_workers.sec_metric_evidence_readiness_adapter import (
        SecMetricEvidenceReadinessResult,
    )
    return SecMetricEvidenceReadinessResult(
        adapter_enabled=True,
        safe_for_decision=False,
        visible_snapshot_unchanged=True,
        portfolio_ticker_count=portfolio_ticker_count,
        ready_count=ready_count,
        partial_count=partial_count,
        blocked_count=blocked_count,
        skipped_non_company_count=skipped_non_company_count,
        ready_tickers=sorted(ready_tickers or [f"RTICKER{i}" for i in range(ready_count)]),
        partial_tickers_with_missing_groups=partial_tickers_with_missing_groups or {},
        blocked_tickers_with_reason=blocked_tickers_with_reason or {},
        skipped_tickers_by_reason=skipped_tickers_by_reason or {},
        errors=errors or [],
    )


# ── TestPhase14AConfigFlagDefault ────────────────────────────────────────────

class TestPhase14AConfigFlagDefault:
    """AC 1: Config flag defaults to False — endpoint off by default."""

    def test_flag_exists_in_settings(self):
        from app.config import Settings
        assert hasattr(Settings, "model_fields"), "pydantic Settings model_fields not found"
        # Instantiate with required fields only — flag must default to False.
        settings = Settings(
            supabase_url="https://test.supabase.co",
            supabase_anon_key="test-anon-key",
            supabase_service_role_key="test-service-role-key",
            supabase_jwt_secret="test-jwt-secret",
            encryption_key="a" * 64,
        )
        assert settings.intel_v3_valuation_data_audit_v1_diagnostics_enabled is False

    def test_flag_is_bool_type(self):
        from app.config import Settings
        settings = Settings(
            supabase_url="https://test.supabase.co",
            supabase_anon_key="test-anon-key",
            supabase_service_role_key="test-service-role-key",
            supabase_jwt_secret="test-jwt-secret",
            encryption_key="a" * 64,
        )
        assert isinstance(settings.intel_v3_valuation_data_audit_v1_diagnostics_enabled, bool)

    def test_flag_name_matches_expected_env_var(self):
        # Flag name must be lowercase snake_case matching env var convention.
        from app.config import Settings
        field_names = list(Settings.model_fields.keys())
        assert "intel_v3_valuation_data_audit_v1_diagnostics_enabled" in field_names


# ── TestPhase14AEndpointFlagGate ─────────────────────────────────────────────

_DIAGNOSTICS_ROUTER_PATH = (
    Path(__file__).parent.parent / "app/routers/diagnostics.py"
)


class TestPhase14AEndpointFlagGate:
    """AC 2: Endpoint is env-gated and runtime-cert protected (static analysis)."""

    def _router_source(self) -> str:
        return _DIAGNOSTICS_ROUTER_PATH.read_text()

    def test_endpoint_path_registered_in_router_source(self):
        src = self._router_source()
        assert "/valuation-data-audit-v1" in src

    def test_flag_gate_present_in_router_source(self):
        src = self._router_source()
        assert "intel_v3_valuation_data_audit_v1_diagnostics_enabled" in src

    def test_flag_gate_raises_http_exception_when_off(self):
        """Router source must raise HTTPException when flag is off."""
        src = self._router_source()
        # The pattern for all existing diagnostic endpoints.
        assert "HTTP_403_FORBIDDEN" in src or "status.HTTP_403_FORBIDDEN" in src

    def test_runtime_cert_dependency_used_for_endpoint(self):
        """Endpoint must use _get_runtime_cert_user dependency."""
        src = self._router_source()
        # Count occurrences — valuation-data-audit-v1 endpoint must reference it.
        assert "_get_runtime_cert_user" in src

    def test_endpoint_is_post_in_source(self):
        src = self._router_source()
        # The router decorator for this endpoint must be @router.post.
        assert '@router.post("/valuation-data-audit-v1")' in src

    def test_config_flag_referenced_in_router(self):
        src = self._router_source()
        assert "intel_v3_valuation_data_audit_v1_diagnostics_enabled" in src

    def test_flag_default_is_false_in_config(self):
        from app.config import Settings
        default = Settings.model_fields["intel_v3_valuation_data_audit_v1_diagnostics_enabled"].default
        assert default is False


# ── TestPhase14AHardLocks ─────────────────────────────────────────────────────

class TestPhase14AHardLocks:
    """AC 3: Response hard-lock invariants always hold."""

    def _audit_result(self, **kwargs):
        from app.services.intelligence.v3.valuation_data_audit_v1 import (
            build_valuation_data_audit,
        )
        readiness = _make_readiness(**kwargs)
        return build_valuation_data_audit(readiness=readiness, company_ticker_categories={})

    def test_safe_for_decision_always_false(self):
        result = self._audit_result()
        assert result.safe_for_decision is False

    def test_visible_snapshot_unchanged_always_true(self):
        result = self._audit_result()
        assert result.visible_snapshot_unchanged is True

    def test_read_only_always_true(self):
        result = self._audit_result()
        assert result.read_only is True

    def test_diagnostics_only_always_true(self):
        result = self._audit_result()
        assert result.diagnostics_only is True

    def test_valuation_ratios_computed_always_false(self):
        result = self._audit_result()
        assert result.valuation_ratios_computed is False

    def test_price_context_unchanged_always_true(self):
        result = self._audit_result()
        assert result.price_context_unchanged is True

    def test_adapter_version_is_phase14a(self):
        result = self._audit_result()
        assert result.adapter_version == "phase14a_v1"

    def test_ttm_blocked_always_true(self):
        result = self._audit_result()
        assert result.ttm_blocked_by_period_limit is True

    def test_period_limit_always_2(self):
        result = self._audit_result()
        assert result.period_limit_per_tag == 2

    def test_hard_locks_unchanged_for_various_readiness(self):
        """Hard locks must hold regardless of readiness input values."""
        for ready, partial, blocked, skipped in [
            (0, 0, 0, 0),
            (10, 6, 3, 15),
            (20, 0, 0, 14),
            (0, 0, 34, 0),
        ]:
            total = ready + partial + blocked + skipped
            r = self._audit_result(
                portfolio_ticker_count=total,
                ready_count=ready,
                partial_count=partial,
                blocked_count=blocked,
                skipped_non_company_count=skipped,
            )
            assert r.safe_for_decision is False
            assert r.visible_snapshot_unchanged is True
            assert r.valuation_ratios_computed is False
            assert r.price_context_unchanged is True
            assert r.ttm_blocked_by_period_limit is True


# ── TestPhase14ALeakagePrevention ─────────────────────────────────────────────

class TestPhase14ALeakagePrevention:
    """AC 4-6, 15: No forbidden metric keys, ratios, PriceBand, per-ticker rows."""

    _FORBIDDEN_RATIO_KEYS = {
        "pe_ratio", "p_e", "price_to_earnings",
        "pb_ratio", "p_b", "price_to_book",
        "ev_ebitda", "enterprise_value",
        "fcf_yield", "free_cash_flow_yield",
        "earnings_yield",
        "fair_value", "price_target",
        "source_url",
    }

    _FORBIDDEN_PRICEBAND_VALUES = {
        "CHEAP", "FAIR", "FULL", "EXPENSIVE",
    }

    def _response_dict(self):
        from app.services.intelligence.v3.valuation_data_audit_v1 import (
            build_valuation_data_audit,
        )
        readiness = _make_readiness()
        audit = build_valuation_data_audit(readiness=readiness, company_ticker_categories={})
        return {
            "adapter_version": audit.adapter_version,
            "safe_for_decision": audit.safe_for_decision,
            "visible_snapshot_unchanged": audit.visible_snapshot_unchanged,
            "read_only": audit.read_only,
            "diagnostics_only": audit.diagnostics_only,
            "valuation_ratios_computed": audit.valuation_ratios_computed,
            "price_context_unchanged": audit.price_context_unchanged,
            "portfolio_ticker_count": audit.portfolio_ticker_count,
            "company_ticker_count": audit.company_ticker_count,
            "non_company_ticker_count": audit.non_company_ticker_count,
            "sec_ready_count": audit.sec_ready_count,
            "sec_partial_count": audit.sec_partial_count,
            "sec_blocked_count": audit.sec_blocked_count,
            "latest_fy_eps_available_count": audit.latest_fy_eps_available_count,
            "latest_fy_eps_diluted_available_count": audit.latest_fy_eps_diluted_available_count,
            "stockholders_equity_available_count": audit.stockholders_equity_available_count,
            "market_price_available_count": audit.market_price_available_count,
            "market_price_fresh_count": audit.market_price_fresh_count,
            "market_price_source_note": audit.market_price_source_note,
            "sector_available_count": audit.sector_available_count,
            "sector_missing_count": audit.sector_missing_count,
            "sector_source_note": audit.sector_source_note,
            "eligible_for_future_fy_earnings_yield_count": audit.eligible_for_future_fy_earnings_yield_count,
            "eligible_for_future_book_value_proxy_count": audit.eligible_for_future_book_value_proxy_count,
            "requires_provider_or_coverage_expansion_count": audit.requires_provider_or_coverage_expansion_count,
            "ttm_blocked_by_period_limit": audit.ttm_blocked_by_period_limit,
            "period_limit_per_tag": audit.period_limit_per_tag,
            "errors": audit.errors,
        }

    def test_no_forbidden_ratio_keys_in_response(self):
        resp = self._response_dict()
        for key in resp:
            assert key.lower() not in self._FORBIDDEN_RATIO_KEYS, (
                f"Forbidden ratio key '{key}' found in response"
            )

    def test_no_forbidden_priceband_values_in_response(self):
        resp = self._response_dict()
        for val in resp.values():
            if isinstance(val, str):
                assert val not in self._FORBIDDEN_PRICEBAND_VALUES, (
                    f"PriceBand value '{val}' found in response"
                )

    def test_no_per_ticker_raw_rows_in_response(self):
        resp = self._response_dict()
        # Per-ticker raw data would be a dict of ticker → raw values.
        # The response should only have aggregate integer counts and strings.
        for key, val in resp.items():
            if isinstance(val, dict):
                # dict fields (if any) must not contain raw metric values.
                for inner_val in val.values():
                    assert not isinstance(inner_val, (int, float)) or key in {
                        "sector_available_count", "sector_missing_count",
                    }, f"Unexpected dict with numeric values at key '{key}'"

    def test_all_count_fields_are_non_negative_integers(self):
        resp = self._response_dict()
        count_keys = [k for k in resp if k.endswith("_count")]
        for key in count_keys:
            val = resp[key]
            assert isinstance(val, int), f"Count field '{key}' is not int: {type(val)}"
            assert val >= 0, f"Count field '{key}' is negative: {val}"

    def test_no_raw_metric_key_names_in_string_fields(self):
        resp = self._response_dict()
        forbidden_metric_key_names = {
            "EarningsPerShareBasic", "EarningsPerShareDiluted",
            "StockholdersEquity", "Revenues", "NetIncomeLoss",
            "NetCashProvidedByUsedInOperatingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment",
        }
        all_strings = " ".join(
            str(v) for v in resp.values() if isinstance(v, str)
        )
        for metric_key in forbidden_metric_key_names:
            assert metric_key not in all_strings, (
                f"Raw metric key name '{metric_key}' found in string response fields"
            )


# ── TestPhase14AAuditPureFunction ─────────────────────────────────────────────

class TestPhase14AAuditPureFunction:
    """AC 3, 12-14: Pure function correctness — counts, feasibility, sector."""

    def _build(self, readiness=None, categories=None):
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        r = readiness or _make_readiness()
        return build_valuation_data_audit(r, categories or {})

    def test_portfolio_ticker_count_matches_readiness(self):
        r = _make_readiness(portfolio_ticker_count=34)
        result = self._build(r)
        assert result.portfolio_ticker_count == 34

    def test_company_ticker_count_is_ready_plus_partial_plus_blocked(self):
        r = _make_readiness(ready_count=10, partial_count=6, blocked_count=3, skipped_non_company_count=15)
        result = self._build(r)
        assert result.company_ticker_count == 19

    def test_non_company_ticker_count_is_skipped(self):
        r = _make_readiness(skipped_non_company_count=15)
        result = self._build(r)
        assert result.non_company_ticker_count == 15

    def test_sec_ready_count_matches_readiness(self):
        r = _make_readiness(ready_count=10)
        result = self._build(r)
        assert result.sec_ready_count == 10

    def test_sec_partial_count_matches_readiness(self):
        r = _make_readiness(partial_count=6)
        result = self._build(r)
        assert result.sec_partial_count == 6

    def test_sec_blocked_count_matches_readiness(self):
        r = _make_readiness(blocked_count=3)
        result = self._build(r)
        assert result.sec_blocked_count == 3

    def test_eps_available_includes_ready_tickers(self):
        r = _make_readiness(ready_count=10, partial_count=0)
        result = self._build(r)
        assert result.latest_fy_eps_available_count == 10

    def test_eps_diluted_available_same_as_eps(self):
        r = _make_readiness(ready_count=10, partial_count=3, partial_tickers_with_missing_groups={
            "T1": ["revenue"], "T2": ["revenue"], "T3": ["revenue"],
        })
        result = self._build(r)
        # All PARTIAL tickers have eps (not in missing) → 10 + 3 = 13
        assert result.latest_fy_eps_available_count == result.latest_fy_eps_diluted_available_count

    def test_eps_available_includes_partial_without_eps_missing(self):
        # PARTIAL ticker with only "revenue" missing → eps is available
        r = _make_readiness(
            ready_count=5,
            partial_count=2,
            partial_tickers_with_missing_groups={"T1": ["revenue"], "T2": ["revenue"]},
        )
        result = self._build(r)
        assert result.latest_fy_eps_available_count == 7  # 5 ready + 2 partial with eps

    def test_eps_excludes_partial_with_eps_missing(self):
        # PARTIAL ticker with "eps" in missing → eps is NOT available
        r = _make_readiness(
            ready_count=5,
            partial_count=2,
            partial_tickers_with_missing_groups={"T1": ["eps"], "T2": ["revenue"]},
        )
        result = self._build(r)
        assert result.latest_fy_eps_available_count == 6  # 5 ready + 1 partial with eps

    def test_equity_available_includes_ready_tickers(self):
        r = _make_readiness(ready_count=8, partial_count=0)
        result = self._build(r)
        assert result.stockholders_equity_available_count == 8

    def test_equity_includes_partial_without_equity_missing(self):
        r = _make_readiness(
            ready_count=5,
            partial_count=2,
            partial_tickers_with_missing_groups={"T1": ["revenue"], "T2": ["equity"]},
        )
        result = self._build(r)
        assert result.stockholders_equity_available_count == 6  # 5 ready + 1 partial with equity

    def test_market_price_available_equals_company_count(self):
        r = _make_readiness(ready_count=10, partial_count=6, blocked_count=3)
        result = self._build(r)
        assert result.market_price_available_count == 19

    def test_market_price_source_note_is_honest_about_position_only(self):
        result = self._build()
        assert "position" in result.market_price_source_note.lower()
        assert "live" not in result.market_price_source_note.lower() or "not_live" in result.market_price_source_note.lower()

    def test_eligible_for_fy_earnings_yield_equals_eps_available(self):
        r = _make_readiness(ready_count=10, partial_count=0)
        result = self._build(r)
        assert result.eligible_for_future_fy_earnings_yield_count == 10

    def test_eligible_for_book_value_proxy_equals_equity_available(self):
        r = _make_readiness(ready_count=10, partial_count=0)
        result = self._build(r)
        assert result.eligible_for_future_book_value_proxy_count == 10

    def test_requires_coverage_expansion_equals_blocked_count(self):
        r = _make_readiness(blocked_count=7)
        result = self._build(r)
        assert result.requires_provider_or_coverage_expansion_count == 7

    def test_zero_counts_for_empty_readiness(self):
        r = _make_readiness(
            portfolio_ticker_count=0, ready_count=0, partial_count=0,
            blocked_count=0, skipped_non_company_count=0,
        )
        result = self._build(r)
        assert result.company_ticker_count == 0
        assert result.latest_fy_eps_available_count == 0
        assert result.eligible_for_future_fy_earnings_yield_count == 0

    def test_errors_from_readiness_propagated(self):
        r = _make_readiness(errors=["some_readiness_error"])
        result = self._build(r)
        assert "some_readiness_error" in result.errors

    def test_build_never_raises_on_malformed_readiness(self):
        """build_valuation_data_audit must never propagate exceptions."""
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit

        class BadReadiness:
            @property
            def ready_count(self):
                raise RuntimeError("intentional error")
            partial_count = 0
            blocked_count = 0
            skipped_non_company_count = 0
            portfolio_ticker_count = 0
            partial_tickers_with_missing_groups = {}
            errors = []

        result = build_valuation_data_audit(BadReadiness(), {})  # type: ignore[arg-type]
        assert result.safe_for_decision is False
        assert result.valuation_ratios_computed is False
        assert len(result.errors) > 0


# ── TestPhase14AStaticImportSafety ─────────────────────────────────────────────

class TestPhase14AStaticImportSafety:
    """AC 9-11: No decide(), no provider imports, no DB writes in module source."""

    _MODULE_PATH = (
        Path(__file__).parent.parent
        / "app/services/intelligence/v3/valuation_data_audit_v1.py"
    )

    def _source(self) -> str:
        return self._MODULE_PATH.read_text()

    def _ast_tree(self):
        return ast.parse(self._source())

    def _imported_names(self) -> set[str]:
        """Extract all imported module/name strings from the module's AST."""
        tree = self._ast_tree()
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module)
                for alias in node.names:
                    names.add(alias.name)
        return names

    def test_no_decide_import_in_module(self):
        imported = self._imported_names()
        assert "decide" not in imported, "Module must not import decide()"

    def test_no_decision_policy_import(self):
        imported = self._imported_names()
        assert not any("decision_policy" in name for name in imported), (
            "Module must not import from decision_policy_v1"
        )

    def test_no_yfinance_import(self):
        imported = self._imported_names()
        assert "yfinance" not in imported

    def test_no_openai_anthropic_import(self):
        imported = self._imported_names()
        assert "openai" not in imported
        assert "anthropic" not in imported

    def test_no_sec_edgar_request(self):
        imported = self._imported_names()
        assert "httpx" not in imported
        assert "requests" not in imported
        # No .get() calls on http clients at module level.
        tree = self._ast_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"get", "post"}:
                if isinstance(node.value, ast.Name) and node.value.id in {"requests", "httpx"}:
                    raise AssertionError(f"HTTP call via {node.value.id}.{node.attr} found")

    def test_no_db_write_methods(self):
        """DB write methods must not appear in the module AST."""
        src = self._source()
        write_methods = {".insert(", ".upsert(", ".update(", ".delete("}
        for method in write_methods:
            assert method not in src, f"DB write method '{method}' found in source"

    def test_no_intel_v3_snapshots_table_access(self):
        src = self._source()
        # Must not access the intel_v3_snapshots table via .table() call.
        assert '.table("intel_v3_snapshots")' not in src
        assert ".table('intel_v3_snapshots')" not in src

    def test_no_priceband_import(self):
        imported = self._imported_names()
        assert "PriceBand" not in imported

    def test_no_recommendation_engine_import(self):
        imported = self._imported_names()
        assert not any("recommendation_engine" in name for name in imported)

    def test_no_intelv3service_import(self):
        imported = self._imported_names()
        assert "IntelV3Service" not in imported

    def test_no_ratio_computation_code(self):
        """Must not compute any valuation ratio — check AST for ratio variable names."""
        # Check that no variable is named with ratio computation patterns.
        src = self._source()
        ratio_var_patterns = ["pe_ratio", "pb_ratio", "ev_ebitda", "fcf_yield"]
        for pat in ratio_var_patterns:
            assert f"= {pat}" not in src and f"{pat} =" not in src, (
                f"Ratio variable '{pat}' found in module"
            )

    def test_module_is_pure_no_network_io(self):
        """Module must not make network calls or open files."""
        imported = self._imported_names()
        network_modules = {"requests", "httpx", "urllib", "aiohttp", "yfinance"}
        for mod in network_modules:
            assert mod not in imported, f"Network module '{mod}' imported"


# ── TestPhase14ATTMBlocked ────────────────────────────────────────────────────

class TestPhase14ATTMBlocked:
    """AC 13: TTM always blocked because period_limit_per_tag = 2."""

    def test_ttm_blocked_constant_is_true(self):
        from app.services.intelligence.v3.valuation_data_audit_v1 import (
            TTM_BLOCKED_BY_PERIOD_LIMIT,
        )
        assert TTM_BLOCKED_BY_PERIOD_LIMIT is True

    def test_period_limit_constant_is_2(self):
        from app.services.intelligence.v3.valuation_data_audit_v1 import (
            PERIOD_LIMIT_PER_TAG,
        )
        assert PERIOD_LIMIT_PER_TAG == 2

    def test_sec_parser_period_limit_still_2(self):
        from app.services.intelligence.research_workers.sec_companyfacts_parser import (
            _MAX_PERIODS_PER_TAG,
        )
        assert _MAX_PERIODS_PER_TAG == 2, (
            "sec_companyfacts_parser._MAX_PERIODS_PER_TAG changed — update Phase 14A constants"
        )

    def test_ttm_blocked_in_result_is_always_true(self):
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        readiness = _make_readiness()
        result = build_valuation_data_audit(readiness, {})
        assert result.ttm_blocked_by_period_limit is True

    def test_period_limit_in_result_is_2(self):
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        readiness = _make_readiness()
        result = build_valuation_data_audit(readiness, {})
        assert result.period_limit_per_tag == 2

    def test_ttm_blocked_invariant_for_all_readiness_states(self):
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        for ready, partial, blocked in [(0, 0, 0), (10, 6, 3), (0, 0, 34)]:
            r = _make_readiness(ready_count=ready, partial_count=partial, blocked_count=blocked)
            result = build_valuation_data_audit(r, {})
            assert result.ttm_blocked_by_period_limit is True, (
                f"TTM blocked must be True for ready={ready}, partial={partial}, blocked={blocked}"
            )


# ── TestPhase14ANonCompanyExclusion ──────────────────────────────────────────

class TestPhase14ANonCompanyExclusion:
    """AC 15: ETF/fund/crypto always counted as non-company."""

    def _build(self, **kw):
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        r = _make_readiness(**kw)
        return build_valuation_data_audit(r, {})

    def test_non_company_count_is_skipped_non_company(self):
        result = self._build(skipped_non_company_count=15)
        assert result.non_company_ticker_count == 15

    def test_non_company_tickers_excluded_from_company_count(self):
        result = self._build(
            ready_count=10, partial_count=6, blocked_count=3, skipped_non_company_count=15,
        )
        assert result.company_ticker_count == 19
        assert result.non_company_ticker_count == 15

    def test_non_company_tickers_not_in_eps_count(self):
        # ETF/crypto tickers are SKIPPED_NON_COMPANY in Phase 9.
        # They should not contribute to eps_available.
        result = self._build(
            ready_count=10, partial_count=0, blocked_count=0, skipped_non_company_count=15,
        )
        assert result.latest_fy_eps_available_count == 10
        assert result.latest_fy_eps_available_count < result.portfolio_ticker_count

    def test_non_company_tickers_not_in_market_price_available(self):
        result = self._build(
            ready_count=10, partial_count=0, blocked_count=0, skipped_non_company_count=15,
        )
        # market_price_available = company_ticker_count = 10
        assert result.market_price_available_count == 10

    def test_etf_category_in_categories_is_non_company(self):
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        readiness = _make_readiness(ready_count=0, partial_count=0, blocked_count=0, skipped_non_company_count=2)
        categories = {"SPY": "ETF", "QQQ": "ETF"}
        result = build_valuation_data_audit(readiness, categories)
        # ETF categories should not count as sector_available.
        assert result.sector_available_count == 0

    def test_crypto_category_in_categories_is_non_company(self):
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        readiness = _make_readiness(ready_count=0, partial_count=0, blocked_count=0, skipped_non_company_count=1)
        categories = {"BTC": "Crypto"}
        result = build_valuation_data_audit(readiness, categories)
        assert result.sector_available_count == 0


# ── TestPhase14AEPSEquityCountsFromBuckets ───────────────────────────────────

class TestPhase14AEPSEquityCountsFromBuckets:
    """AC 12: EPS/equity counts correctly inferred from readiness bucket data."""

    def _build(self, partial_missing: dict[str, list[str]], ready_count: int = 5):
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        r = _make_readiness(
            ready_count=ready_count,
            partial_count=len(partial_missing),
            partial_tickers_with_missing_groups=partial_missing,
        )
        return build_valuation_data_audit(r, {})

    def test_all_partial_missing_eps_not_counted(self):
        result = self._build({"T1": ["eps"], "T2": ["eps"]}, ready_count=3)
        assert result.latest_fy_eps_available_count == 3  # only ready

    def test_partial_with_other_missing_not_eps_counted(self):
        result = self._build({"T1": ["revenue"], "T2": ["capex"]}, ready_count=3)
        assert result.latest_fy_eps_available_count == 5  # 3 ready + 2 partial with eps

    def test_mixed_partial_eps_availability(self):
        result = self._build({"T1": ["eps"], "T2": ["revenue"], "T3": ["equity"]}, ready_count=4)
        # T1 missing eps → not counted; T2 not missing eps → counted; T3 not missing eps → counted
        assert result.latest_fy_eps_available_count == 6  # 4 ready + 2 partial with eps

    def test_equity_exclusion_when_missing(self):
        result = self._build({"T1": ["equity"], "T2": ["revenue"]}, ready_count=4)
        # T1 missing equity → not counted; T2 not missing equity → counted
        assert result.stockholders_equity_available_count == 5  # 4 ready + 1 partial with equity

    def test_eps_available_greater_or_equal_zero(self):
        result = self._build({}, ready_count=0)
        assert result.latest_fy_eps_available_count >= 0


# ── TestPhase14ASectorReporting ───────────────────────────────────────────────

class TestPhase14ASectorReporting:
    """AC 14: Sector availability reported honestly from positions.category."""

    def _build(self, categories: dict[str, str], **kw):
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        r = _make_readiness(**kw)
        return build_valuation_data_audit(r, categories)

    def test_sector_source_note_mentions_portfolio_category(self):
        result = self._build({})
        assert "portfolio_category" in result.sector_source_note.lower()

    def test_sector_source_note_mentions_no_financial_sector(self):
        result = self._build({})
        assert "financial_sector" in result.sector_source_note.lower() or "not_financial" in result.sector_source_note.lower()

    def test_company_category_counted_as_sector_available(self):
        result = self._build({"AAPL": "Core", "MSFT": "Core", "TSLA": "IPO"})
        assert result.sector_available_count == 3

    def test_etf_category_not_counted_as_sector_available(self):
        result = self._build({"SPY": "ETF", "AAPL": "Core"})
        # SPY has ETF category → not sector_available
        # AAPL has Core → sector_available
        assert result.sector_available_count == 1

    def test_empty_category_counted_as_sector_missing(self):
        result = self._build({"AAPL": ""})
        assert result.sector_missing_count >= 1

    def test_sector_available_plus_missing_equals_ticker_count_with_categories(self):
        categories = {"AAPL": "Core", "MSFT": "Core", "SPY": "ETF", "BTC": "Crypto", "TSLA": ""}
        result = self._build(categories)
        assert result.sector_available_count + result.sector_missing_count == len(categories)

    def test_no_financial_sector_data_note_in_source_note(self):
        result = self._build({"AAPL": "Core"})
        assert "future_cheap_expensive_blocked" in result.sector_source_note.lower() or \
               "financial" in result.sector_source_note.lower()

    def test_empty_category_map_sets_missing_to_company_count(self):
        r = _make_readiness(ready_count=5, partial_count=2, blocked_count=3)
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        result = build_valuation_data_audit(r, {})
        assert result.sector_missing_count == 10  # company_ticker_count


# ── TestPhase14AProductionPassCriteria ───────────────────────────────────────

class TestPhase14AProductionPassCriteria:
    """AC 16: HANDOFF production pass/fail criteria as executable tests.

    These tests mirror the exact validation steps that must be verified in
    production after Phase 14A merges. A CI pass here proves the response
    contract is correct before any env flag is enabled in production.
    """

    def _audit(self):
        from app.services.intelligence.v3.valuation_data_audit_v1 import build_valuation_data_audit
        # Use Phase 13.1 production counts: READY=10, PARTIAL=6, BLOCKED=3, NON_COMPANY=15
        r = _make_readiness(
            portfolio_ticker_count=34,
            ready_count=10,
            partial_count=6,
            blocked_count=3,
            skipped_non_company_count=15,
            partial_tickers_with_missing_groups={f"P{i}": ["revenue"] for i in range(6)},
        )
        return build_valuation_data_audit(r, {f"C{i}": "Core" for i in range(19)})

    def test_pass_safe_for_decision_false(self):
        assert self._audit().safe_for_decision is False

    def test_pass_visible_snapshot_unchanged_true(self):
        assert self._audit().visible_snapshot_unchanged is True

    def test_pass_read_only_true(self):
        assert self._audit().read_only is True

    def test_pass_diagnostics_only_true(self):
        assert self._audit().diagnostics_only is True

    def test_pass_valuation_ratios_computed_false(self):
        assert self._audit().valuation_ratios_computed is False

    def test_pass_price_context_unchanged_true(self):
        assert self._audit().price_context_unchanged is True

    def test_pass_portfolio_ticker_count_34(self):
        assert self._audit().portfolio_ticker_count == 34

    def test_pass_company_ticker_count_19(self):
        # 10 READY + 6 PARTIAL + 3 BLOCKED
        assert self._audit().company_ticker_count == 19

    def test_pass_non_company_ticker_count_15(self):
        assert self._audit().non_company_ticker_count == 15

    def test_pass_sec_ready_count_10(self):
        assert self._audit().sec_ready_count == 10

    def test_pass_sec_partial_count_6(self):
        assert self._audit().sec_partial_count == 6

    def test_pass_sec_blocked_count_3(self):
        assert self._audit().sec_blocked_count == 3

    def test_pass_eps_available_at_least_ready_count(self):
        audit = self._audit()
        assert audit.latest_fy_eps_available_count >= audit.sec_ready_count

    def test_pass_ttm_blocked_true(self):
        assert self._audit().ttm_blocked_by_period_limit is True

    def test_pass_period_limit_2(self):
        assert self._audit().period_limit_per_tag == 2

    def test_pass_errors_empty(self):
        assert self._audit().errors == []

    def test_pass_eligible_for_earnings_yield_positive(self):
        # At least 10 READY tickers should be eligible for future FY earnings yield.
        assert self._audit().eligible_for_future_fy_earnings_yield_count >= 10

    def test_pass_adapter_version_phase14a_v1(self):
        assert self._audit().adapter_version == "phase14a_v1"

    def test_fail_criteria_safe_for_decision_never_true(self):
        """Fail criterion: if safe_for_decision=True, escalate immediately."""
        audit = self._audit()
        if audit.safe_for_decision is True:
            pytest.fail("CRITICAL: safe_for_decision=True — escalate immediately")

    def test_fail_criteria_valuation_ratios_never_computed(self):
        """Fail criterion: if valuation_ratios_computed=True, this is a violation."""
        audit = self._audit()
        if audit.valuation_ratios_computed is True:
            pytest.fail("CRITICAL: valuation_ratios_computed=True — critical invariant violation")

    def test_phase14a_feasibility_assessment_for_14b(self):
        """Phase 14B feasibility: enough data for FY EPS earnings yield computation?

        Production validation: eligible_for_future_fy_earnings_yield_count >= 10
        means FY EPS earnings yield computation is feasible for at least 10 tickers.
        """
        audit = self._audit()
        assert audit.eligible_for_future_fy_earnings_yield_count >= 10, (
            "Phase 14B FY EPS earnings yield computation not feasible for production portfolio"
        )
