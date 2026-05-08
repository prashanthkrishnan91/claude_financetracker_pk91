"""Phase 14C-Prep — Price + Sector Source Resolution v1 tests.

Hard locks, leakage prevention, candidate ranking, certification rules,
rejection rules for positions-derived price and positions.category sector,
and static import safety.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).parent.parent
    / "app/services/intelligence/v3/price_sector_source_resolution_v1.py"
)
_DIAGNOSTICS_ROUTER_PATH = (
    Path(__file__).parent.parent / "app/routers/diagnostics.py"
)
_CONFIG_PATH = Path(__file__).parent.parent / "app/config.py"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _pcs(name, *, available=0, fresh=0, stale=0, missing=0, basis="none", rejected=""):
    from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
        PriceCandidateStats,
    )
    return PriceCandidateStats(
        name=name,
        available_count=available,
        fresh_count=fresh,
        stale_count=stale,
        missing_count=missing,
        freshness_basis=basis,
        rejected_reason=rejected,
    )


def _scs(name, *, available=0, industry=0, missing=0, rejected=""):
    from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
        SectorCandidateStats,
    )
    return SectorCandidateStats(
        name=name,
        available_count=available,
        industry_available_count=industry,
        missing_count=missing,
        rejected_reason=rejected,
    )


def _build(*, anchor=10, company=16, price_cands=None, sector_cands=None, errors=None):
    from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
        build_price_sector_source_resolution,
    )
    return build_price_sector_source_resolution(
        portfolio_ticker_count=34,
        company_ticker_count=company,
        non_company_ticker_count=15,
        company_anchor_count=anchor,
        price_candidates=price_cands or [],
        sector_candidates=sector_cands or [],
        extra_errors=errors or [],
    )


# ── Config flag ────────────────────────────────────────────────────────────────

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
        assert s.intel_v3_price_sector_source_resolution_v1_diagnostics_enabled is False

    def test_flag_in_model_fields(self):
        from app.config import Settings
        assert (
            "intel_v3_price_sector_source_resolution_v1_diagnostics_enabled"
            in Settings.model_fields
        )


# ── Endpoint flag gate ─────────────────────────────────────────────────────────

class TestEndpointFlagGate:
    def _src(self):
        return _DIAGNOSTICS_ROUTER_PATH.read_text()

    def test_endpoint_path_registered(self):
        assert '@router.post("/price-sector-source-resolution-v1")' in self._src()

    def test_flag_gate_in_router(self):
        assert "intel_v3_price_sector_source_resolution_v1_diagnostics_enabled" in self._src()

    def test_403_when_flag_off(self):
        assert "INTEL_V3_PRICE_SECTOR_SOURCE_RESOLUTION_V1_DIAGNOSTICS_ENABLED is not enabled" in self._src()

    def test_runtime_cert_dep_used(self):
        # The endpoint binding uses the same runtime-cert dependency.
        assert "_get_runtime_cert_user" in self._src()


# ── Hard locks ─────────────────────────────────────────────────────────────────

class TestHardLocks:
    def test_safe_for_decision_false(self):
        assert _build().safe_for_decision is False

    def test_visible_snapshot_unchanged(self):
        assert _build().visible_snapshot_unchanged is True

    def test_read_only(self):
        assert _build().read_only is True

    def test_diagnostics_only(self):
        assert _build().diagnostics_only is True

    def test_valuation_ratios_computed_false(self):
        assert _build().valuation_ratios_computed is False

    def test_earnings_yield_computed_false(self):
        assert _build().earnings_yield_computed is False

    def test_price_context_unchanged(self):
        assert _build().price_context_unchanged is True

    def test_adapter_version(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            PRICE_SECTOR_SOURCE_RESOLUTION_V1_CONTRACT_VERSION,
        )
        assert PRICE_SECTOR_SOURCE_RESOLUTION_V1_CONTRACT_VERSION == "phase14c_prep_v1"
        assert _build().adapter_version == "phase14c_prep_v1"

    def test_safe_for_decision_remains_false_even_when_certified(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            PRICE_CANDIDATE_PRICE_HISTORY,
            SECTOR_CANDIDATE_MARKET_SNAPSHOTS,
        )
        r = _build(
            anchor=10,
            price_cands=[
                _pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=10, fresh=10, basis="price_date")
            ],
            sector_cands=[
                _scs(SECTOR_CANDIDATE_MARKET_SNAPSHOTS, available=10)
            ],
        )
        assert r.ready_for_phase14c_computation is True
        # Hard lock holds even when ready.
        assert r.safe_for_decision is False
        assert r.valuation_ratios_computed is False
        assert r.earnings_yield_computed is False


# ── Leakage prevention ────────────────────────────────────────────────────────

class TestLeakagePrevention:
    _FORBIDDEN = {
        "pe_ratio", "p_e_ratio", "pb_ratio", "p_b_ratio",
        "ev_ebitda", "fcf_yield", "earnings_yield_value",
        "fair_value", "price_target", "price_band_value",
        "intrinsic_value", "forward_pe", "trailing_pe",
    }

    def test_no_forbidden_keys(self):
        r = _build()
        for k in self._FORBIDDEN:
            assert k not in r.__dataclass_fields__

    def test_no_priceband_string_values(self):
        r = _build()
        forbidden_strings = {"CHEAP", "EXPENSIVE", "FAIR"}
        for k, v in r.__dict__.items():
            if isinstance(v, str):
                assert v not in forbidden_strings

    def test_no_dict_per_ticker_fields(self):
        r = _build()
        for k, v in r.__dict__.items():
            assert not isinstance(v, dict), f"dict field {k!r} could leak per-ticker data"

    def test_counts_non_negative(self):
        r = _build()
        for k, v in r.__dict__.items():
            if isinstance(v, int) and not isinstance(v, bool):
                assert v >= 0, f"{k} negative: {v}"

    def test_no_raw_metric_keys_in_strings(self):
        r = _build()
        forbidden = {"EarningsPerShareBasic", "StockholdersEquity"}
        for k, v in r.__dict__.items():
            if isinstance(v, str):
                for f in forbidden:
                    assert f not in v


# ── Pure function ranking and certification ──────────────────────────────────

class TestRankingAndCertification:
    def test_missing_when_no_candidates(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import MISSING
        r = _build()
        assert r.price_source_certification_status == MISSING
        assert r.sector_source_certification_status == MISSING
        assert r.ready_for_phase14c_computation is False

    def test_uncertified_when_no_freshness_basis(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            UNCERTIFIED,
        )
        r = _build(price_cands=[
            _pcs("custom", available=10, fresh=0, stale=10, basis="none")
        ])
        assert r.price_source_certification_status == UNCERTIFIED

    def test_uncertified_when_only_stale(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            UNCERTIFIED, PRICE_CANDIDATE_PRICE_HISTORY,
        )
        r = _build(price_cands=[
            _pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=10, fresh=0, stale=10, basis="price_date")
        ])
        assert r.price_source_certification_status == UNCERTIFIED

    def test_partial_when_fresh_below_anchor(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            PARTIAL, PRICE_CANDIDATE_PRICE_HISTORY,
        )
        r = _build(anchor=10, price_cands=[
            _pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=5, fresh=5, basis="price_date")
        ])
        assert r.price_source_certification_status == PARTIAL

    def test_certified_when_fresh_meets_anchor(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            CERTIFIED, PRICE_CANDIDATE_PRICE_HISTORY,
        )
        r = _build(anchor=10, price_cands=[
            _pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=10, fresh=10, basis="price_date")
        ])
        assert r.price_source_certification_status == CERTIFIED

    def test_winner_picks_higher_fresh(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            PRICE_CANDIDATE_PRICE_HISTORY, PRICE_CANDIDATE_MARKET_SNAPSHOTS,
        )
        r = _build(
            anchor=10,
            price_cands=[
                _pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=2, fresh=2, basis="price_date"),
                _pcs(PRICE_CANDIDATE_MARKET_SNAPSHOTS, available=10, fresh=10, basis="as_of"),
            ],
        )
        assert r.selected_price_source_name == PRICE_CANDIDATE_MARKET_SNAPSHOTS

    def test_priority_breaks_ties(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            PRICE_CANDIDATE_PRICE_HISTORY, PRICE_CANDIDATE_MARKET_SNAPSHOTS,
        )
        # Both 5 fresh, both same status — price_history wins by priority order.
        r = _build(
            anchor=10,
            price_cands=[
                _pcs(PRICE_CANDIDATE_MARKET_SNAPSHOTS, available=5, fresh=5, basis="as_of"),
                _pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=5, fresh=5, basis="price_date"),
            ],
        )
        assert r.selected_price_source_name == PRICE_CANDIDATE_PRICE_HISTORY

    def test_ready_only_when_both_certified(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            PRICE_CANDIDATE_PRICE_HISTORY, SECTOR_CANDIDATE_MARKET_SNAPSHOTS,
        )
        r = _build(
            anchor=10,
            price_cands=[_pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=10, fresh=10, basis="price_date")],
            sector_cands=[_scs(SECTOR_CANDIDATE_MARKET_SNAPSHOTS, available=4)],
        )
        assert r.ready_for_phase14c_computation is False
        assert any("sector_source_status" in reason for reason in r.phase14c_blocking_reasons)

    def test_ready_when_both_certified(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            PRICE_CANDIDATE_PRICE_HISTORY, SECTOR_CANDIDATE_MARKET_SNAPSHOTS,
        )
        r = _build(
            anchor=10,
            price_cands=[_pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=10, fresh=10, basis="price_date")],
            sector_cands=[_scs(SECTOR_CANDIDATE_MARKET_SNAPSHOTS, available=10)],
        )
        assert r.ready_for_phase14c_computation is True
        assert r.phase14c_blocking_reasons == []
        assert r.recommended_next_step == "phase14c_computation_unblocked"


# ── Rejection rules ────────────────────────────────────────────────────────────

class TestRejectionRules:
    def test_positions_derived_price_rejected(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            REJECTED, PRICE_CANDIDATE_POSITIONS_DERIVED,
        )
        # Even with high counts, rejected_reason disqualifies.
        r = _build(price_cands=[
            _pcs(
                PRICE_CANDIDATE_POSITIONS_DERIVED,
                available=20, fresh=20, basis="none",
                rejected="no_quote_date_position_value_is_not_a_price_source",
            )
        ])
        # No other candidates → MISSING (not selected; rejected excluded from ranking)
        assert r.selected_price_source_name == ""
        # The rejected candidate must still appear in the candidates_checked list.
        assert PRICE_CANDIDATE_POSITIONS_DERIVED in r.price_source_candidates_checked

    def test_positions_category_sector_rejected(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            SECTOR_CANDIDATE_POSITIONS_CATEGORY,
        )
        r = _build(sector_cands=[
            _scs(
                SECTOR_CANDIDATE_POSITIONS_CATEGORY,
                available=16,
                rejected="portfolio_category_not_gics_financial_sector",
            )
        ])
        # Rejected → not selected.
        assert r.selected_sector_source_name == ""
        assert SECTOR_CANDIDATE_POSITIONS_CATEGORY in r.sector_source_candidates_checked

    def test_rejected_does_not_promote_to_certified(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            CERTIFIED, PRICE_CANDIDATE_PRICE_HISTORY, PRICE_CANDIDATE_POSITIONS_DERIVED,
        )
        # Price_history weak, positions strong but rejected → stay weak.
        r = _build(
            anchor=10,
            price_cands=[
                _pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=2, fresh=1, stale=1, basis="price_date"),
                _pcs(
                    PRICE_CANDIDATE_POSITIONS_DERIVED,
                    available=10, fresh=10, basis="none",
                    rejected="no_quote_date_position_value_is_not_a_price_source",
                ),
            ],
        )
        assert r.selected_price_source_name == PRICE_CANDIDATE_PRICE_HISTORY
        assert r.price_source_certification_status != CERTIFIED


# ── Static import / write safety ──────────────────────────────────────────────

class TestStaticImportSafety:
    def _module_src(self):
        return _MODULE_PATH.read_text()

    def _ast(self):
        return ast.parse(self._module_src())

    def _imports(self):
        tree = self._ast()
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        return names

    def test_no_decide_or_decision_policy_imports(self):
        imports = self._imports()
        assert not any("decision_policy" in m for m in imports)
        # No call to decide() — check via AST.
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "decide"

    def test_no_provider_or_llm_imports(self):
        imports = self._imports()
        forbidden = {"yfinance", "openai", "anthropic", "httpx", "requests"}
        for m in imports:
            top = m.split(".")[0]
            assert top not in forbidden, f"forbidden import: {m}"

    def test_no_db_write_method_calls(self):
        # AST: no .insert/.upsert/.update/.delete method calls.
        forbidden = {"insert", "upsert", "update", "delete"}
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden, (
                    f"DB write method .{node.func.attr}( found in pure module"
                )

    def test_no_decision_input_v3_or_priceband_or_run_v3_names_used(self):
        # AST: no Name/Attribute references to forbidden symbols (excluding strings/comments).
        forbidden_names = {"DecisionInputV3", "PriceBand", "run_v3", "decide"}
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Name):
                assert node.id not in forbidden_names
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_names

    def test_no_intel_v3_snapshot_table_writes(self):
        # AST: no string literal == 'intel_v3_snapshots' inside any Call.
        for node in ast.walk(self._ast()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value != "intel_v3_snapshots", (
                    "pure module must not reference intel_v3_snapshots table"
                )

    def test_router_endpoint_no_provider_or_db_writes(self):
        # Parse the whole router AST and locate the endpoint function. Confirm
        # no provider imports/calls and no .insert/.upsert/.update/.delete
        # method calls inside the function body.
        router_src = _DIAGNOSTICS_ROUTER_PATH.read_text()
        tree = ast.parse(router_src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "get_price_sector_source_resolution_v1_diagnostics":
                    target = node
                    break
        assert target is not None, "endpoint function not found in router AST"

        forbidden_write_methods = {"insert", "upsert", "update", "delete"}
        forbidden_provider_names = {"yfinance", "openai", "anthropic"}
        for node in ast.walk(target):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_write_methods, (
                    f"DB write method .{node.func.attr}( in endpoint body"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden_provider_names
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                assert top not in forbidden_provider_names


# ── Recommended next step decision tree ───────────────────────────────────────

class TestRecommendedNextStep:
    def test_unblocked_when_ready(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            PRICE_CANDIDATE_PRICE_HISTORY, SECTOR_CANDIDATE_MARKET_SNAPSHOTS,
        )
        r = _build(
            anchor=10,
            price_cands=[_pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=10, fresh=10, basis="price_date")],
            sector_cands=[_scs(SECTOR_CANDIDATE_MARKET_SNAPSHOTS, available=10)],
        )
        assert r.recommended_next_step == "phase14c_computation_unblocked"

    def test_split_pr_when_both_missing(self):
        r = _build()
        assert r.recommended_next_step.startswith("split_pr_provider_backed_ingestion")

    def test_backfill_when_partial(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            PRICE_CANDIDATE_PRICE_HISTORY, SECTOR_CANDIDATE_MARKET_SNAPSHOTS,
        )
        r = _build(
            anchor=10,
            price_cands=[_pcs(PRICE_CANDIDATE_PRICE_HISTORY, available=5, fresh=5, basis="price_date")],
            sector_cands=[_scs(SECTOR_CANDIDATE_MARKET_SNAPSHOTS, available=5)],
        )
        assert "backfill" in r.recommended_next_step or "split" in r.recommended_next_step


# ── Build error path holds invariants ─────────────────────────────────────────

class TestBuildErrorInvariants:
    def test_error_path_preserves_hard_locks(self):
        from app.services.intelligence.v3.price_sector_source_resolution_v1 import (
            build_price_sector_source_resolution,
        )

        class BadList(list):
            def __iter__(self):
                raise RuntimeError("boom")

        r = build_price_sector_source_resolution(
            portfolio_ticker_count=0,
            company_ticker_count=0,
            non_company_ticker_count=0,
            company_anchor_count=0,
            price_candidates=BadList(),
            sector_candidates=[],
        )
        assert r.safe_for_decision is False
        assert r.read_only is True
        assert r.valuation_ratios_computed is False
        assert r.ready_for_phase14c_computation is False
        assert any("build_error" in e for e in r.errors)
