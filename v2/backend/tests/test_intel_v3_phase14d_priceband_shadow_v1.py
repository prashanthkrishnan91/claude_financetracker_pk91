"""Phase 14D — PriceBand Shadow Policy v1 tests.

Hard locks, leakage prevention, deterministic policy_static_v1 thresholds,
EPS preference, negative-EPS bucketing, skip rules, plain-English summary
shape, confidence policy, aggregate counts, and AST-based static import /
write safety for the pure module and the router endpoint.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).parent.parent
    / "app/services/intelligence/v3/priceband_shadow_policy_v1.py"
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
    sector_label=None,
    industry_label=None,
):
    from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
        PriceBandShadowInput,
    )
    return PriceBandShadowInput(
        ticker=ticker,
        fy_diluted_eps=diluted,
        fy_basic_eps=basic,
        eps_source_linked=source_linked,
        price=price,
        price_fresh=fresh,
        sector_available=sector,
        industry_available=industry,
        sector_label=sector_label,
        industry_label=industry_label,
    )


def _build(records=None, errors=None):
    from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
        build_priceband_shadow,
    )
    return build_priceband_shadow(
        records=records or [],
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
        assert s.intel_v3_priceband_shadow_v1_diagnostics_enabled is False

    def test_flag_in_model_fields(self):
        from app.config import Settings
        assert (
            "intel_v3_priceband_shadow_v1_diagnostics_enabled"
            in Settings.model_fields
        )


# ── Endpoint flag gate ─────────────────────────────────────────────────────────

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

    def test_decision_input_mutated_false(self):
        assert _build().decision_input_mutated is False

    def test_visible_decision_changed_false(self):
        assert _build().visible_decision_changed is False

    def test_no_target_price_emitted(self):
        assert _build().no_target_price_emitted is True

    def test_no_fair_value_emitted(self):
        assert _build().no_fair_value_emitted is True

    def test_fy_only_true(self):
        assert _build().fy_only is True

    def test_ttm_computed_false(self):
        assert _build().ttm_computed is False

    def test_adapter_version(self):
        from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
            PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
        )
        assert (
            PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION
            == "phase14d_priceband_shadow_v1"
        )
        assert _build().adapter_version == "phase14d_priceband_shadow_v1"

    def test_policy_table_id(self):
        from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
            PRICEBAND_POLICY_TABLE_ID,
        )
        assert PRICEBAND_POLICY_TABLE_ID == "policy_static_v1"
        assert _build().policy_table_id == "policy_static_v1"

    def test_policy_basis(self):
        assert _build().policy_basis == "fy_eps_earnings_yield"

    def test_per_ticker_safe_for_decision_false(self):
        recs = [_rec("AAA", diluted=4.0, source_linked=True, price=100.0,
                     fresh=True, sector=True)]
        r = _build(records=recs)
        assert all(d.safe_for_decision is False for d in r.priceband_diagnostics)
        assert all(d.shadow_only is True for d in r.priceband_diagnostics)
        assert all(
            d.visible_decision_changed is False
            for d in r.priceband_diagnostics
        )


# ── Leakage prevention — no target price / fair value / raw values ──────────

class TestLeakagePrevention:
    _FORBIDDEN_KEYS = {
        "target_price", "fair_value", "buy_below", "sell_above",
        "intrinsic_value", "price_target", "forward_pe", "trailing_pe",
        "raw_eps", "raw_price", "earnings_yield_value", "raw_yield",
        "fair_price", "intrinsic_price",
    }

    def test_no_forbidden_keys_in_aggregate_result(self):
        r = _build()
        for k in self._FORBIDDEN_KEYS:
            assert k not in r.__dataclass_fields__

    def test_no_forbidden_keys_in_per_ticker_diag(self):
        recs = [_rec("AAA", diluted=4.0, source_linked=True, price=100.0,
                     fresh=True, sector=True)]
        r = _build(records=recs)
        for d in r.priceband_diagnostics:
            for k in self._FORBIDDEN_KEYS:
                assert k not in d.__dataclass_fields__

    def test_per_ticker_no_raw_eps_or_price_attribute(self):
        recs = [_rec("AAA", diluted=4.0, source_linked=True, price=100.0,
                     fresh=True, sector=True)]
        r = _build(records=recs)
        for d in r.priceband_diagnostics:
            for forbidden in (
                "fy_diluted_eps", "fy_basic_eps", "price",
                "earnings_yield", "y_pct",
            ):
                assert forbidden not in d.__dataclass_fields__

    def test_per_ticker_only_safe_strings(self):
        # Per-ticker diag must not contain "target", "fair", "buy below",
        # "sell above" anywhere in its plain-English summary or limitations.
        recs = [_rec("AAA", diluted=4.0, source_linked=True, price=100.0,
                     fresh=True, sector=True),
                _rec("BBB", diluted=-1.0, source_linked=True, price=10.0,
                     fresh=True, sector=True)]
        r = _build(records=recs)
        forbidden_substr = (
            "target price", "fair value", "buy below", "sell above",
            "intrinsic value",
        )
        for d in r.priceband_diagnostics:
            text = (d.plain_english_summary or "").lower()
            for f in forbidden_substr:
                assert f not in text
            for lim in d.limitations:
                # Limitations may MENTION "not a fair-value estimate" — that's
                # an explicit denial, which is fine. But no positive emission.
                assert "buy below" not in lim.lower()
                assert "sell above" not in lim.lower()
                assert "target price" not in lim.lower() or lim.lower().startswith("not a")

    def test_no_priceband_enum_string_values(self):
        # Module must not echo decision_contracts.PriceBand enum values
        # (CHEAP / FAIR / FULL / EXPENSIVE) as upper-case strings, since the
        # policy uses a separate humble vocabulary.
        recs = [_rec("AAA", diluted=4.0, source_linked=True, price=100.0,
                     fresh=True, sector=True)]
        r = _build(records=recs)
        forbidden_uppercase = {"CHEAP", "FAIR", "FULL", "EXPENSIVE", "SUPPRESSED"}
        for v in r.__dict__.values():
            if isinstance(v, str):
                assert v not in forbidden_uppercase
        for d in r.priceband_diagnostics:
            for v in d.__dict__.values():
                if isinstance(v, str):
                    assert v not in forbidden_uppercase

    def test_counts_non_negative(self):
        r = _build()
        for k, v in r.__dict__.items():
            if isinstance(v, int) and not isinstance(v, bool):
                assert v >= 0, f"{k} negative: {v}"


# ── Policy thresholds — broad-market, FY EPS earnings yield ─────────────────

class TestPolicyThresholds:
    """Verify the deterministic policy_static_v1 mapping at boundary points."""

    @pytest.mark.parametrize(
        "diluted,price,expected_signal,expected_bucket",
        [
            # Below 2% → expensive.
            (1.0, 100.0, "expensive", "zero_to_2_percent"),     # 1.0%
            (1.99, 100.0, "expensive", "zero_to_2_percent"),    # 1.99%
            # 2.0% lower-edge inclusive → elevated.
            (2.0, 100.0, "elevated", "two_to_4_percent"),
            (3.5, 100.0, "elevated", "two_to_4_percent"),
            (3.99, 100.0, "elevated", "two_to_4_percent"),
            # 4.0% lower-edge inclusive → reasonable.
            (4.0, 100.0, "reasonable", "four_to_6_percent"),
            (5.0, 100.0, "reasonable", "four_to_6_percent"),
            (5.99, 100.0, "reasonable", "four_to_6_percent"),
            # 6.0% lower-edge inclusive → attractive.
            (6.0, 100.0, "attractive", "six_to_9_percent"),
            (7.5, 100.0, "attractive", "six_to_9_percent"),
            (8.99, 100.0, "attractive", "six_to_9_percent"),
            # 9.0% lower-edge inclusive → unusually_cheap.
            (9.0, 100.0, "unusually_cheap", "above_9_percent"),
            (15.0, 100.0, "unusually_cheap", "above_9_percent"),
            (50.0, 100.0, "unusually_cheap", "above_9_percent"),
        ],
    )
    def test_positive_eps_signal_and_bucket(
        self, diluted, price, expected_signal, expected_bucket
    ):
        r = _build([_rec("T", diluted=diluted, source_linked=True,
                         price=price, fresh=True, sector=True)])
        assert r.priceband_computed_count == 1
        d = r.priceband_diagnostics[0]
        assert d.valuation_signal == expected_signal
        assert d.earnings_yield_bucket == expected_bucket
        assert d.priceband_produced is True

    def test_high_attractive_yield(self):
        # 8% is attractive bucket.
        r = _build([_rec("T", diluted=8.0, source_linked=True,
                         price=100.0, fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_signal == "attractive"
        assert d.earnings_yield_bucket == "six_to_9_percent"


# ── Negative EPS — never cheap ─────────────────────────────────────────────

class TestNegativeEps:
    def test_negative_eps_signal(self):
        r = _build([_rec("T", diluted=-2.0, source_linked=True,
                         price=100.0, fresh=True, sector=True)])
        assert r.priceband_computed_count == 1
        d = r.priceband_diagnostics[0]
        assert d.valuation_signal == "negative_eps"
        assert d.earnings_yield_bucket == "negative_eps"
        # Even a "very negative yield" must NOT be classified cheap.
        r2 = _build([_rec("T", diluted=-50.0, source_linked=True,
                          price=10.0, fresh=True, sector=True)])
        d2 = r2.priceband_diagnostics[0]
        assert d2.valuation_signal == "negative_eps"
        assert d2.valuation_signal not in (
            "attractive", "unusually_cheap", "reasonable",
        )

    def test_negative_eps_summary_does_not_imply_cheap(self):
        r = _build([_rec("T", diluted=-2.0, source_linked=True,
                         price=100.0, fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        text = d.plain_english_summary.lower()
        assert "cheap" not in text
        assert "attractive" not in text


# ── Unavailable cases ──────────────────────────────────────────────────────

class TestUnavailableCases:
    def test_missing_eps_unavailable(self):
        r = _build([_rec("T", price=100.0, fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_signal == "unavailable"
        assert d.unavailable_reason == "missing_eps"
        assert d.priceband_produced is False
        assert r.priceband_unavailable_count == 1
        assert r.unavailable_reason_counts["missing_eps"] == 1

    def test_zero_eps_unavailable(self):
        r = _build([_rec("T", diluted=0.0, source_linked=True,
                         price=100.0, fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_signal == "unavailable"
        assert d.unavailable_reason == "zero_eps_invalid_for_valuation"

    def test_missing_price_unavailable(self):
        r = _build([_rec("T", diluted=2.0, source_linked=True,
                         price=None, fresh=False, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_signal == "unavailable"
        assert d.unavailable_reason == "missing_price"

    def test_stale_price_unavailable(self):
        r = _build([_rec("T", diluted=2.0, source_linked=True,
                         price=100.0, fresh=False, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_signal == "unavailable"
        assert d.unavailable_reason == "stale_price"

    def test_non_positive_price_unavailable(self):
        r = _build([_rec("T", diluted=2.0, source_linked=True,
                         price=0.0, fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_signal == "unavailable"
        assert d.unavailable_reason == "non_positive_price"


# ── Missing sector → broad fallback (still classifies) ──────────────────────

class TestMissingSectorBroadFallback:
    def test_missing_sector_broad_fallback_classifies(self):
        # Missing sector is allowed: policy explicitly labels broad fallback
        # rather than treating sector absence as a hard skip.
        r = _build([_rec("T", diluted=4.0, source_linked=True,
                         price=100.0, fresh=True, sector=False)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_signal == "reasonable"  # 4% positive bucket
        assert d.broad_fallback_used is True
        assert d.sector_used_for_classification is False
        assert d.input_quality == (
            "source_linked_fy_eps_and_fresh_price_broad_fallback"
        )
        assert r.broad_fallback_count == 1


# ── EPS preference: diluted preferred, basic fallback ───────────────────────

class TestEpsPreference:
    def test_diluted_preferred_when_both_present(self):
        r = _build([_rec("T", diluted=4.0, basic=2.0, source_linked=True,
                         price=100.0, fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        # 4% → reasonable bucket (not 2% → elevated).
        assert d.valuation_signal == "reasonable"

    def test_basic_fallback_when_diluted_missing(self):
        r = _build([_rec("T", diluted=None, basic=3.0, source_linked=True,
                         price=100.0, fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_signal == "elevated"
        # Confidence drops to medium when basic-only is used.
        assert d.valuation_confidence == "medium"


# ── Confidence policy ──────────────────────────────────────────────────────

class TestConfidence:
    def test_high_confidence_diluted_source_linked_full_inputs(self):
        r = _build([_rec("T", diluted=4.0, source_linked=True,
                         price=100.0, fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_confidence == "high"

    def test_medium_confidence_basic_fallback(self):
        r = _build([_rec("T", basic=3.0, source_linked=True,
                         price=100.0, fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_confidence == "medium"

    def test_medium_confidence_broad_fallback(self):
        r = _build([_rec("T", diluted=4.0, source_linked=True,
                         price=100.0, fresh=True, sector=False)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_confidence == "medium"

    def test_low_confidence_non_source_linked(self):
        r = _build([_rec("T", diluted=4.0, source_linked=False,
                         price=100.0, fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_confidence == "low"

    def test_low_confidence_unavailable(self):
        r = _build([_rec("T", diluted=None, price=100.0,
                         fresh=True, sector=True)])
        d = r.priceband_diagnostics[0]
        assert d.valuation_confidence == "low"


# ── Aggregate counts ────────────────────────────────────────────────────────

class TestAggregateCounts:
    def test_aggregate_distribution_sums_match(self):
        recs = [
            _rec("A", diluted=1.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),                # expensive
            _rec("B", diluted=3.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),                # elevated
            _rec("C", diluted=5.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),                # reasonable
            _rec("D", diluted=7.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),                # attractive
            _rec("E", diluted=12.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),                # unusually_cheap
            _rec("F", diluted=-2.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),                # negative_eps
            _rec("G", diluted=None, basic=None, price=100.0,
                 fresh=True, sector=True),                # unavailable
        ]
        r = _build(recs)
        assert r.evaluated_company_ticker_count == 7
        assert r.priceband_computed_count == 6
        assert r.priceband_unavailable_count == 1
        assert r.by_valuation_signal["expensive"] == 1
        assert r.by_valuation_signal["elevated"] == 1
        assert r.by_valuation_signal["reasonable"] == 1
        assert r.by_valuation_signal["attractive"] == 1
        assert r.by_valuation_signal["unusually_cheap"] == 1
        assert r.by_valuation_signal["negative_eps"] == 1
        assert r.by_valuation_signal["unavailable"] == 1
        # by_valuation_signal across ALL signals must equal evaluated count.
        assert sum(r.by_valuation_signal.values()) == r.evaluated_company_ticker_count
        # by_confidence sums to evaluated count.
        assert sum(r.by_confidence.values()) == r.evaluated_company_ticker_count

    def test_aggregate_counts_stable_across_runs(self):
        recs = [
            _rec(f"T{i}", diluted=4.0, source_linked=True, price=100.0,
                 fresh=True, sector=True)
            for i in range(10)
        ]
        r1 = _build(recs)
        r2 = _build(recs)
        assert r1.evaluated_company_ticker_count == r2.evaluated_company_ticker_count
        assert r1.priceband_computed_count == r2.priceband_computed_count
        assert r1.by_valuation_signal == r2.by_valuation_signal
        assert r1.by_confidence == r2.by_confidence
        assert r1.earnings_yield_bucket_counts == r2.earnings_yield_bucket_counts


# ── Determinism ─────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_yields_same_output(self):
        recs = [
            _rec("A", diluted=4.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),
            _rec("B", diluted=8.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),
            _rec("C", diluted=-1.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),
        ]
        r1 = _build(recs)
        r2 = _build(recs)
        assert [d.valuation_signal for d in r1.priceband_diagnostics] == \
               [d.valuation_signal for d in r2.priceband_diagnostics]
        assert [d.valuation_confidence for d in r1.priceband_diagnostics] == \
               [d.valuation_confidence for d in r2.priceband_diagnostics]
        assert [d.earnings_yield_bucket for d in r1.priceband_diagnostics] == \
               [d.earnings_yield_bucket for d in r2.priceband_diagnostics]


# ── Plain-English summary shape ─────────────────────────────────────────────

class TestPlainEnglishSummary:
    def test_summary_present_for_every_signal(self):
        recs = [
            _rec("A", diluted=1.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),
            _rec("B", diluted=4.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),
            _rec("C", diluted=12.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),
            _rec("D", diluted=-2.0, source_linked=True, price=100.0,
                 fresh=True, sector=True),
            _rec("E", price=100.0, fresh=True, sector=True),  # missing_eps
        ]
        r = _build(recs)
        for d in r.priceband_diagnostics:
            assert isinstance(d.plain_english_summary, str)
            assert len(d.plain_english_summary) > 0

    def test_summary_does_not_contain_numeric_values(self):
        # Plain-English summaries must not embed raw numbers.
        recs = [_rec("T", diluted=4.0, source_linked=True, price=100.0,
                     fresh=True, sector=True)]
        r = _build(recs)
        d = r.priceband_diagnostics[0]
        # Allow no digits in the summary.
        for ch in d.plain_english_summary:
            assert not ch.isdigit(), (
                f"raw numeric leaked into summary: {d.plain_english_summary}"
            )

    def test_summary_humble_for_unusually_cheap(self):
        # The unusually_cheap summary should include a "consider why" cautious
        # nudge — not a buy recommendation.
        recs = [_rec("T", diluted=15.0, source_linked=True, price=100.0,
                     fresh=True, sector=True)]
        r = _build(recs)
        d = r.priceband_diagnostics[0]
        text = d.plain_english_summary.lower()
        assert "consider" in text
        assert "buy" not in text
        assert "sell" not in text


# ── Build error preserves invariants ───────────────────────────────────────

class TestBuildErrorInvariants:
    def test_error_path_preserves_hard_locks(self):
        from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
            build_priceband_shadow,
        )

        class BadList(list):
            def __iter__(self):
                raise RuntimeError("boom")

        r = build_priceband_shadow(records=BadList(), extra_errors=None)
        assert r.safe_for_decision is False
        assert r.shadow_only is True
        assert r.read_only is True
        assert r.decision_input_mutated is False
        assert r.visible_decision_changed is False
        assert r.no_target_price_emitted is True
        assert r.no_fair_value_emitted is True
        assert any("build_error" in e for e in r.errors)


# ── Static import / write safety ───────────────────────────────────────────

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

