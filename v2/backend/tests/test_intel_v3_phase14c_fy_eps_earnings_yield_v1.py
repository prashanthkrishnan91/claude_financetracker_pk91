"""Phase 14C — FY EPS Earnings Yield v1 tests.

Hard locks, leakage prevention, EPS preference, negative-EPS bucketing,
skip rules, distribution-bucket aggregate-only invariant, future-PriceBand
readiness gate, and static import / write safety for both the pure module
and the router endpoint.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).parent.parent
    / "app/services/intelligence/v3/fy_eps_earnings_yield_v1.py"
)
_DIAGNOSTICS_ROUTER_PATH = (
    Path(__file__).parent.parent / "app/routers/diagnostics.py"
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _rec(
    ticker="AAA",
    *,
    diluted=None,
    basic=None,
    source_linked=False,
    price=None,
    fresh=False,
    sector=False,
    industry=False,
):
    from app.services.intelligence.v3.fy_eps_earnings_yield_v1 import (
        EarningsYieldInputRecord,
    )
    return EarningsYieldInputRecord(
        ticker=ticker,
        fy_diluted_eps=diluted,
        fy_basic_eps=basic,
        eps_source_linked=source_linked,
        price=price,
        price_fresh=fresh,
        sector_available=sector,
        industry_available=industry,
    )


def _build(*, company=10, records=None, errors=None, portfolio=20, non_company=5):
    from app.services.intelligence.v3.fy_eps_earnings_yield_v1 import (
        build_fy_eps_earnings_yield,
    )
    return build_fy_eps_earnings_yield(
        portfolio_ticker_count=portfolio,
        company_ticker_count=company,
        non_company_ticker_count=non_company,
        records=records or [],
        sec_eps_source="research_artifact_facts",
        price_source="market_snapshots_table",
        sector_source="market_snapshots_sector",
        extra_errors=errors or [],
    )


# ── Config flag default ────────────────────────────────────────────────────────

class TestConfigFlagDefault:
    def test_flag_exists_and_default_false(self):
        from app.config import Settings
        s = Settings(
            supabase_url="https://test.supabase.co",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="jwt",
            encryption_key="a" * 64,
        )
        assert s.intel_v3_fy_eps_earnings_yield_v1_diagnostics_enabled is False

    def test_flag_in_model_fields(self):
        from app.config import Settings
        assert (
            "intel_v3_fy_eps_earnings_yield_v1_diagnostics_enabled"
            in Settings.model_fields
        )


# ── Endpoint flag gate ─────────────────────────────────────────────────────────

class TestEndpointFlagGate:
    def _src(self):
        return _DIAGNOSTICS_ROUTER_PATH.read_text()

    def test_endpoint_path_registered(self):
        assert '@router.post("/fy-eps-earnings-yield-v1")' in self._src()

    def test_flag_gate_in_router(self):
        assert "intel_v3_fy_eps_earnings_yield_v1_diagnostics_enabled" in self._src()

    def test_403_when_flag_off(self):
        assert "INTEL_V3_FY_EPS_EARNINGS_YIELD_V1_DIAGNOSTICS_ENABLED is not enabled" in self._src()

    def test_runtime_cert_dep_used(self):
        # Endpoint binding uses the runtime-cert dependency.
        src = self._src()
        idx = src.index('@router.post("/fy-eps-earnings-yield-v1")')
        body = src[idx:idx + 2000]
        assert "_get_runtime_cert_user" in body


# ── Hard locks ─────────────────────────────────────────────────────────────────

class TestHardLocks:
    def test_safe_for_decision_false(self):
        assert _build().safe_for_decision is False

    def test_shadow_only_true(self):
        assert _build().shadow_only is True

    def test_visible_snapshot_unchanged(self):
        assert _build().visible_snapshot_unchanged is True

    def test_read_only(self):
        assert _build().read_only is True

    def test_diagnostics_only(self):
        assert _build().diagnostics_only is True

    def test_price_context_unchanged(self):
        assert _build().price_context_unchanged is True

    def test_priceband_produced_false(self):
        assert _build().priceband_produced is False

    def test_decision_input_mutated_false(self):
        assert _build().decision_input_mutated is False

    def test_visible_decision_changed_false(self):
        assert _build().visible_decision_changed is False

    def test_valuation_ratios_computed_true(self):
        # Phase 14C *intentionally* computes earnings yield as a shadow signal.
        assert _build().valuation_ratios_computed is True

    def test_earnings_yield_computed_true(self):
        assert _build().earnings_yield_computed is True

    def test_ttm_blocked(self):
        assert _build().ttm_computed is False
        assert _build().fy_only is True

    def test_adapter_version(self):
        from app.services.intelligence.v3.fy_eps_earnings_yield_v1 import (
            FY_EPS_EARNINGS_YIELD_V1_CONTRACT_VERSION,
        )
        assert FY_EPS_EARNINGS_YIELD_V1_CONTRACT_VERSION == "phase14c_fy_eps_earnings_yield_v1"
        assert _build().adapter_version == "phase14c_fy_eps_earnings_yield_v1"

    def test_eps_preference_order(self):
        assert _build().eps_preference_order == ["diluted", "basic"]

    def test_hard_locks_hold_when_ready(self):
        # Even when computation succeeds and ready=True, hard locks hold.
        recs = [
            _rec(f"T{i}", diluted=2.0, source_linked=True, price=50.0,
                 fresh=True, sector=True, industry=True)
            for i in range(10)
        ]
        r = _build(company=10, records=recs)
        assert r.ready_for_future_priceband_phase is True
        assert r.safe_for_decision is False
        assert r.shadow_only is True
        assert r.priceband_produced is False
        assert r.decision_input_mutated is False
        assert r.visible_decision_changed is False
        assert r.visible_snapshot_unchanged is True


# ── Leakage prevention ────────────────────────────────────────────────────────

class TestLeakagePrevention:
    _FORBIDDEN = {
        "pe_ratio", "p_e_ratio", "pb_ratio", "p_b_ratio",
        "ev_ebitda", "fcf_yield", "earnings_yield_value",
        "fair_value", "price_target", "price_band_value",
        "intrinsic_value", "forward_pe", "trailing_pe",
        "raw_eps", "raw_price", "per_ticker", "by_ticker",
    }

    def test_no_forbidden_keys(self):
        r = _build()
        for k in self._FORBIDDEN:
            assert k not in r.__dataclass_fields__

    def test_no_priceband_string_values(self):
        r = _build()
        forbidden_strings = {"CHEAP", "EXPENSIVE", "FAIR"}
        for v in r.__dict__.values():
            if isinstance(v, str):
                assert v not in forbidden_strings

    def test_only_bucket_dict_field_present(self):
        # The only dict-typed field is the aggregate bucket distribution —
        # never per-ticker maps.
        r = _build()
        for k, v in r.__dict__.items():
            if isinstance(v, dict):
                assert k == "earnings_yield_distribution_buckets", (
                    f"unexpected dict field {k!r} could leak per-ticker data"
                )

    def test_bucket_keys_are_aggregate_labels_only(self):
        from app.services.intelligence.v3.fy_eps_earnings_yield_v1 import (
            BUCKET_NEGATIVE_EPS, BUCKET_0_TO_2, BUCKET_2_TO_4,
            BUCKET_4_TO_6, BUCKET_6_TO_8, BUCKET_ABOVE_8,
        )
        expected = {
            BUCKET_NEGATIVE_EPS, BUCKET_0_TO_2, BUCKET_2_TO_4,
            BUCKET_4_TO_6, BUCKET_6_TO_8, BUCKET_ABOVE_8,
        }
        r = _build()
        # All bucket keys are stable aggregate labels — no ticker symbols.
        assert set(r.earnings_yield_distribution_buckets.keys()) == expected

    def test_counts_non_negative(self):
        r = _build()
        for k, v in r.__dict__.items():
            if isinstance(v, int) and not isinstance(v, bool):
                assert v >= 0, f"{k} negative: {v}"

    def test_no_raw_metric_keys_in_strings(self):
        # The result must not echo raw SEC tag identifiers anywhere.
        r = _build()
        forbidden = {"EarningsPerShareBasic", "EarningsPerShareDiluted",
                     "StockholdersEquity"}
        for v in r.__dict__.values():
            if isinstance(v, str):
                for f in forbidden:
                    assert f not in v
            elif isinstance(v, list):
                for s in v:
                    if isinstance(s, str):
                        for f in forbidden:
                            assert f not in s

    def test_no_ticker_symbol_leakage_in_response_keys(self):
        # Build with synthetic tickers and confirm none appear in any string
        # or list field of the result.
        recs = [
            _rec("AAPL", diluted=6.0, source_linked=True, price=150.0,
                 fresh=True, sector=True),
            _rec("MSFT", diluted=10.0, source_linked=True, price=300.0,
                 fresh=True, sector=True),
        ]
        r = _build(company=2, records=recs)
        for v in r.__dict__.values():
            if isinstance(v, str):
                assert "AAPL" not in v and "MSFT" not in v
            elif isinstance(v, list):
                for s in v:
                    if isinstance(s, str):
                        assert "AAPL" not in s and "MSFT" not in s
            elif isinstance(v, dict):
                # buckets only
                for k in v.keys():
                    assert "AAPL" not in k and "MSFT" not in k


# ── EPS preference: diluted preferred, basic fallback ────────────────────────

class TestEpsPreference:
    def test_diluted_preferred_when_both_present(self):
        # diluted=4, basic=2, price=100 → yield should use diluted (4%).
        r = _build(
            company=1,
            records=[_rec(diluted=4.0, basic=2.0, source_linked=True,
                          price=100.0, fresh=True, sector=True)],
        )
        assert r.computed_earnings_yield_count == 1
        assert r.diluted_eps_used_count == 1
        assert r.basic_eps_fallback_used_count == 0
        # 4/100 = 4% → boundary lands in four_to_6_percent.
        assert r.earnings_yield_distribution_buckets["four_to_6_percent"] == 1

    def test_basic_fallback_when_diluted_missing(self):
        r = _build(
            company=1,
            records=[_rec(diluted=None, basic=3.0, source_linked=True,
                          price=100.0, fresh=True, sector=True)],
        )
        assert r.computed_earnings_yield_count == 1
        assert r.diluted_eps_used_count == 0
        assert r.basic_eps_fallback_used_count == 1
        assert r.earnings_yield_distribution_buckets["two_to_4_percent"] == 1

    def test_missing_eps_skipped(self):
        r = _build(
            company=1,
            records=[_rec(diluted=None, basic=None, price=100.0,
                          fresh=True, sector=True)],
        )
        assert r.computed_earnings_yield_count == 0
        assert r.skipped_missing_eps_count == 1


# ── Negative EPS: computed but bucketed safely ──────────────────────────────

class TestNegativeEps:
    def test_negative_eps_bucketed_negative_not_cheap(self):
        r = _build(
            company=1,
            records=[_rec(diluted=-2.0, source_linked=True,
                          price=100.0, fresh=True, sector=True)],
        )
        assert r.computed_earnings_yield_count == 1
        assert r.negative_eps_count == 1
        assert r.positive_eps_count == 0
        assert r.earnings_yield_distribution_buckets["negative_eps"] == 1
        # Negative MUST NOT land in any positive bucket.
        for b in ("zero_to_2_percent", "two_to_4_percent",
                  "four_to_6_percent", "six_to_8_percent", "above_8_percent"):
            assert r.earnings_yield_distribution_buckets[b] == 0

    def test_negative_eps_does_not_imply_cheap(self):
        r = _build(
            company=1,
            records=[_rec(diluted=-50.0, source_linked=True,
                          price=10.0, fresh=True, sector=True)],
        )
        # Even with a "very negative" yield, the result must contain no
        # CHEAP/EXPENSIVE label and no priceband production.
        assert r.priceband_produced is False
        for v in r.__dict__.values():
            if isinstance(v, str):
                assert v.upper() not in {"CHEAP", "EXPENSIVE"}


# ── Skip rules ────────────────────────────────────────────────────────────────

class TestSkipRules:
    def test_zero_eps_skipped_invalid(self):
        r = _build(
            company=1,
            records=[_rec(diluted=0.0, source_linked=True,
                          price=100.0, fresh=True, sector=True)],
        )
        assert r.computed_earnings_yield_count == 0
        assert r.skipped_invalid_eps_count == 1
        assert r.zero_eps_count == 1

    def test_missing_price_skipped(self):
        r = _build(
            company=1,
            records=[_rec(diluted=2.0, source_linked=True,
                          price=None, fresh=False, sector=True)],
        )
        assert r.skipped_missing_price_count == 1
        assert r.computed_earnings_yield_count == 0

    def test_stale_price_skipped(self):
        r = _build(
            company=1,
            records=[_rec(diluted=2.0, source_linked=True,
                          price=100.0, fresh=False, sector=True)],
        )
        assert r.skipped_stale_price_count == 1
        assert r.computed_earnings_yield_count == 0

    def test_non_positive_price_skipped(self):
        r = _build(
            company=1,
            records=[_rec(diluted=2.0, source_linked=True,
                          price=0.0, fresh=True, sector=True)],
        )
        assert r.skipped_non_positive_price_count == 1
        assert r.computed_earnings_yield_count == 0

    def test_negative_price_skipped(self):
        r = _build(
            company=1,
            records=[_rec(diluted=2.0, source_linked=True,
                          price=-5.0, fresh=True, sector=True)],
        )
        assert r.skipped_non_positive_price_count == 1
        assert r.computed_earnings_yield_count == 0

    def test_missing_sector_skipped(self):
        r = _build(
            company=1,
            records=[_rec(diluted=2.0, source_linked=True,
                          price=100.0, fresh=True, sector=False)],
        )
        assert r.skipped_missing_sector_count == 1
        assert r.computed_earnings_yield_count == 0


# ── Distribution buckets — aggregate-only correctness ───────────────────────

class TestDistributionBuckets:
    def test_bucket_boundary_assignments(self):
        # price=100 makes yield_pct = eps directly.
        recs = [
            _rec("A", diluted=1.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),  # 1% → zero_to_2
            _rec("B", diluted=2.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),  # 2% → two_to_4 (left-edge inclusive)
            _rec("C", diluted=3.5, source_linked=True, price=100.0,
                 fresh=True, sector=True),  # 3.5% → two_to_4
            _rec("D", diluted=5.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),  # 5% → four_to_6
            _rec("E", diluted=7.5, source_linked=True, price=100.0,
                 fresh=True, sector=True),  # 7.5% → six_to_8
            _rec("F", diluted=12.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),  # 12% → above_8
            _rec("G", diluted=-3.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),  # negative
        ]
        r = _build(company=7, records=recs)
        d = r.earnings_yield_distribution_buckets
        assert d["zero_to_2_percent"] == 1
        assert d["two_to_4_percent"] == 2
        assert d["four_to_6_percent"] == 1
        assert d["six_to_8_percent"] == 1
        assert d["above_8_percent"] == 1
        assert d["negative_eps"] == 1
        assert sum(d.values()) == r.computed_earnings_yield_count

    def test_bucket_total_equals_computed_count(self):
        recs = [
            _rec(f"T{i}", diluted=4.0, source_linked=True, price=100.0,
                 fresh=True, sector=True)
            for i in range(5)
        ]
        # Mix in some skipped rows.
        recs.append(_rec("X", diluted=None, basic=None, price=100.0,
                         fresh=True, sector=True))
        recs.append(_rec("Y", diluted=2.0, price=None, sector=True))
        r = _build(company=7, records=recs)
        assert sum(r.earnings_yield_distribution_buckets.values()) == \
            r.computed_earnings_yield_count


# ── Future-PriceBand readiness gate ─────────────────────────────────────────

class TestReadyForFuturePriceBand:
    def test_not_ready_when_no_records(self):
        r = _build()
        assert r.ready_for_future_priceband_phase is False
        assert r.future_priceband_blocking_reasons  # non-empty

    def test_not_ready_with_partial_coverage(self):
        # 3 of 10 computed → below 70% threshold.
        recs = [
            _rec(f"T{i}", diluted=4.0, source_linked=True, price=100.0,
                 fresh=True, sector=True)
            for i in range(3)
        ]
        r = _build(company=10, records=recs)
        assert r.ready_for_future_priceband_phase is False
        assert any("computed_yield_coverage_below_threshold" in reason
                   for reason in r.future_priceband_blocking_reasons)

    def test_not_ready_when_source_linked_eps_low(self):
        # 10 of 10 computed but only 3 source-linked → below 70%.
        recs = [
            _rec(f"T{i}", diluted=4.0,
                 source_linked=(i < 3),
                 price=100.0, fresh=True, sector=True)
            for i in range(10)
        ]
        r = _build(company=10, records=recs)
        assert r.ready_for_future_priceband_phase is False
        assert any("source_linked_eps_coverage_below_threshold" in reason
                   for reason in r.future_priceband_blocking_reasons)

    def test_ready_when_coverage_strong(self):
        recs = [
            _rec(f"T{i}", diluted=4.0, source_linked=True, price=100.0,
                 fresh=True, sector=True, industry=True)
            for i in range(10)
        ]
        r = _build(company=10, records=recs)
        assert r.ready_for_future_priceband_phase is True
        assert r.future_priceband_blocking_reasons == []
        assert r.recommended_next_step.startswith("design_priceband_policy_phase")

    def test_zero_company_ticker_blocks(self):
        r = _build(company=0)
        assert r.ready_for_future_priceband_phase is False
        assert "company_ticker_count_zero" in r.future_priceband_blocking_reasons


# ── Build error path preserves invariants ─────────────────────────────────

class TestBuildErrorInvariants:
    def test_error_path_preserves_hard_locks(self):
        from app.services.intelligence.v3.fy_eps_earnings_yield_v1 import (
            build_fy_eps_earnings_yield,
        )

        class BadList(list):
            def __iter__(self):
                raise RuntimeError("boom")

        r = build_fy_eps_earnings_yield(
            portfolio_ticker_count=0,
            company_ticker_count=0,
            non_company_ticker_count=0,
            records=BadList(),
            sec_eps_source="research_artifact_facts",
            price_source="market_snapshots_table",
            sector_source="market_snapshots_sector",
        )
        assert r.safe_for_decision is False
        assert r.shadow_only is True
        assert r.read_only is True
        assert r.priceband_produced is False
        assert r.decision_input_mutated is False
        assert r.visible_decision_changed is False
        assert r.ready_for_future_priceband_phase is False
        assert any("build_error" in e for e in r.errors)


# ── Static import / write safety ──────────────────────────────────────────

class TestStaticImportSafety:
    def _module_src(self):
        return _MODULE_PATH.read_text()

    def _ast(self):
        return ast.parse(self._module_src())

    def _imports(self):
        names = []
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    def test_no_decide_or_decision_policy_imports(self):
        imports = self._imports()
        assert not any("decision_policy" in m for m in imports)
        assert not any("decision_contracts" in m for m in imports)
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "decide"

    def test_no_priceband_run_v3_decisioninputv3_references(self):
        forbidden_names = {"DecisionInputV3", "PriceBand", "run_v3", "decide"}
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Name):
                assert node.id not in forbidden_names
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_names

    def test_no_provider_or_llm_or_http_imports(self):
        forbidden = {"yfinance", "openai", "anthropic", "httpx", "requests",
                     "urllib", "aiohttp"}
        for m in self._imports():
            top = m.split(".")[0]
            assert top not in forbidden, f"forbidden import: {m}"

    def test_no_db_or_io_imports(self):
        forbidden = {"supabase", "psycopg2", "sqlalchemy"}
        for m in self._imports():
            top = m.split(".")[0]
            assert top not in forbidden, f"forbidden import: {m}"

    def test_no_db_write_method_calls(self):
        forbidden = {"insert", "upsert", "update", "delete"}
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden, (
                    f"DB write method .{node.func.attr}( found in pure module"
                )

    def test_no_intel_v3_snapshot_table_writes(self):
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value != "intel_v3_snapshots", (
                    "pure module must not reference intel_v3_snapshots table"
                )

    def test_router_endpoint_no_provider_or_db_writes(self):
        router_src = _DIAGNOSTICS_ROUTER_PATH.read_text()
        tree = ast.parse(router_src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "get_fy_eps_earnings_yield_v1_diagnostics":
                    target = node
                    break
        assert target is not None, "endpoint function not found in router AST"

        forbidden_writes = {"insert", "upsert", "update", "delete"}
        forbidden_provider_names = {"yfinance", "openai", "anthropic"}
        for node in ast.walk(target):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_writes, (
                    f"DB write method .{node.func.attr}( in endpoint body"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden_provider_names
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                assert top not in forbidden_provider_names

    def test_router_endpoint_no_decide_or_run_v3_calls(self):
        router_src = _DIAGNOSTICS_ROUTER_PATH.read_text()
        tree = ast.parse(router_src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "get_fy_eps_earnings_yield_v1_diagnostics":
                    target = node
                    break
        assert target is not None
        for node in ast.walk(target):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"decide", "run_v3"}


# ── No frontend / UI changes ──────────────────────────────────────────────

class TestNoFrontendChanges:
    def test_no_frontend_files_in_phase(self):
        # The pure module lives in services/intelligence/v3/* — frontend
        # paths must not be touched as part of this phase. This is a
        # structural guard, not a git-diff check.
        backend_root = Path(__file__).parent.parent / "app"
        assert _MODULE_PATH.is_relative_to(backend_root)
