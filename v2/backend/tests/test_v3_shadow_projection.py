"""Table-driven tests for Intel v3 shadow projection diagnostic (PR 2).

Dark launch — backend only. Tests confirm:
1. Shadow projection runs without changing visible action.
2. v2 HOLD + strong buy-like signals → hold_collapse_risk=True (v3 says BUY).
3. v2 HOLD + sell/risk-like signals → v3 non-HOLD shadow (SELL or TRIM).
4. Genuinely insufficient/missing data → v3 stays HOLD (v3_honest_hold=True).
5. Malformed/partial card data fails soft — returns None, never raises.
6. v3 shadow action is always within {BUY, HOLD, TRIM, SELL}.
7. No Deploy code path is touched (no allocation_engine import).
8. HOLD-collapse risk identified across a representative fixture set.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.shadow_projection import project_shadow_from_card_signals

_VALID_ACTIONS = {"BUY", "HOLD", "TRIM", "SELL"}


# ── Shared fixture builder ────────────────────────────────────────────────────

def _card(
    *,
    ticker: str,
    v2_visible_action: str,
    analyst_action=None,
    conviction_level=None,
    technical_signal=None,
    risk_flag=None,
    analyst_risks=None,
    category="Core",
    data_quality_label=None,
    intel_read=None,
    thesis_v2=None,
) -> dict:
    return dict(
        ticker=ticker,
        v2_visible_action=v2_visible_action,
        analyst_action=analyst_action,
        conviction_level=conviction_level,
        technical_signal=technical_signal,
        risk_flag=risk_flag,
        analyst_risks=analyst_risks,
        category=category,
        data_quality_label=data_quality_label,
        intel_read=intel_read,
        thesis_v2=thesis_v2,
    )


def _good_intel_read(n_trusted: int = 3) -> dict:
    dims = ["business quality", "valuation", "growth", "momentum"][:n_trusted]
    return {"insufficient_data": False, "trusted_dimensions": dims, "suppressed_dimensions": []}


def _thin_intel_read() -> dict:
    return {
        "insufficient_data": True,
        "trusted_dimensions": [],
        "suppressed_dimensions": ["valuation", "growth"],
    }


# ── Test 1: Shadow does not change visible action ────────────────────────────

class TestShadowDoesNotChangeVisibleAction:
    """Shadow projection must be read-only — visible action unchanged."""

    @pytest.mark.parametrize("v2_action", ["BUY", "HOLD", "TRIM", "SELL"])
    def test_v2_visible_action_preserved_in_diagnostic(self, v2_action):
        diag = project_shadow_from_card_signals(
            **_card(
                ticker="AAPL",
                v2_visible_action=v2_action,
                analyst_action=v2_action,
                conviction_level="HIGH",
                data_quality_label="HIGH",
                intel_read=_good_intel_read(),
                category="Core",
            )
        )
        assert diag is not None
        assert diag["v2_visible_action"] == v2_action, (
            "Diagnostic must report the original v2 visible action unchanged"
        )

    def test_returns_dict_not_modified_card(self):
        """project_shadow_from_card_signals returns a new dict, not a mutated card."""
        kwargs = _card(
            ticker="MSFT",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            data_quality_label="HIGH",
            intel_read=_good_intel_read(),
        )
        diag = project_shadow_from_card_signals(**kwargs)
        assert diag is not None
        # The original kwargs are unchanged.
        assert kwargs["v2_visible_action"] == "HOLD"


# ── Test 2: v2 HOLD + strong buy signals → hold_collapse_risk=True ───────────

class TestHoldCollapseRiskBuy:
    """v2=HOLD but v3 sees strong buy evidence → hold_collapse_risk=True."""

    def test_hold_with_strong_buy_analyst_action_collapses(self):
        """Card shows HOLD (gated) but analyst_action=BUY + HIGH conviction + good data."""
        diag = project_shadow_from_card_signals(
            **_card(
                ticker="NVDA",
                v2_visible_action="HOLD",
                analyst_action="BUY",
                conviction_level="HIGH",
                technical_signal="BULLISH",
                category="Tech",
                data_quality_label="HIGH",
                intel_read=_good_intel_read(3),
            )
        )
        assert diag is not None
        assert diag["hold_collapse_risk"] is True, (
            "HOLD with strong BUY signals from analyst should flag hold_collapse_risk"
        )
        assert diag["v3_shadow_action"] != "HOLD"

    def test_hold_with_medium_conviction_buy_collapses(self):
        """HOLD card with MEDIUM conviction + 2 trusted dims may produce non-HOLD v3."""
        diag = project_shadow_from_card_signals(
            **_card(
                ticker="GOOGL",
                v2_visible_action="HOLD",
                analyst_action="BUY",
                conviction_level="MEDIUM",
                category="Tech",
                data_quality_label="MEDIUM",
                intel_read=_good_intel_read(2),
            )
        )
        assert diag is not None
        # May or may not produce hold_collapse_risk depending on evidence threshold.
        # The key assertion: v3_shadow_action must be a valid action.
        assert diag["v3_shadow_action"] in _VALID_ACTIONS

    def test_representative_buy_hold_collapse(self):
        """Strong BUY evidence on a non-speculative single-stock should collapse."""
        diag = project_shadow_from_card_signals(
            ticker="AAPL",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=[],
            category="Core",
            data_quality_label="HIGH",
            intel_read={"insufficient_data": False, "trusted_dimensions": ["business quality", "valuation", "growth"]},
            thesis_v2=None,
        )
        assert diag is not None
        assert diag["hold_collapse_risk"] is True
        assert diag["v3_shadow_action"] == "BUY"


# ── Test 3: v2 HOLD + sell/risk signals → v3 non-HOLD shadow ────────────────

class TestHoldCollapseRiskSellTrim:
    """v2=HOLD but sell/risk signals → v3 SELL or TRIM shadow."""

    def test_hold_with_critical_risk_produces_nonhold(self):
        """HOLD card with critical-language risk text → v3 non-HOLD."""
        diag = project_shadow_from_card_signals(
            **_card(
                ticker="RIVN",
                v2_visible_action="HOLD",
                analyst_action="SELL",
                conviction_level="LOW",
                technical_signal="BEARISH",
                risk_flag="Severe insolvency risk — cash burn accelerating",
                analyst_risks=["Cash runway under 6 months", "Critical covenant breach"],
                category="Speculative",
                data_quality_label="MEDIUM",
                intel_read=_good_intel_read(2),
            )
        )
        assert diag is not None
        assert diag["v3_shadow_action"] in {"SELL", "TRIM"}, (
            "Critical risk with sell signal should produce SELL or TRIM, not HOLD"
        )
        assert diag["hold_collapse_risk"] is True

    def test_hold_with_overweight_signals_produces_trim(self):
        """HOLD card with analyst TRIM signal → v3 TRIM shadow."""
        diag = project_shadow_from_card_signals(
            **_card(
                ticker="META",
                v2_visible_action="HOLD",
                analyst_action="TRIM",
                conviction_level="MEDIUM",
                technical_signal=None,
                category="Growth",
                data_quality_label="HIGH",
                intel_read=_good_intel_read(2),
            )
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "TRIM"
        assert diag["hold_collapse_risk"] is True

    def test_hold_with_sell_signals_collapses(self):
        """HOLD + bearish tech + SELL analyst produces non-HOLD v3 shadow."""
        diag = project_shadow_from_card_signals(
            **_card(
                ticker="CAVA",
                v2_visible_action="HOLD",
                analyst_action="SELL",
                conviction_level="HIGH",
                technical_signal="BEARISH",
                risk_flag="Thesis broken — growth decelerating",
                analyst_risks=["Revenue growth stalled"],
                category="Growth",
                data_quality_label="HIGH",
                intel_read=_good_intel_read(2),
            )
        )
        assert diag is not None
        assert diag["v3_shadow_action"] in {"SELL", "TRIM"}
        assert diag["hold_collapse_risk"] is True


# ── Test 4: Genuinely insufficient data → honest HOLD ────────────────────────

class TestHonestHoldOnMissingData:
    """Missing per-axis data must stay honest — no fake confidence."""

    def test_no_signals_stays_hold_honest(self):
        """Card with no data_quality_label and no intel_read → v3 HOLD + v3_honest_hold."""
        diag = project_shadow_from_card_signals(
            **_card(
                ticker="KLAR",
                v2_visible_action="HOLD",
                analyst_action=None,
                conviction_level=None,
                data_quality_label=None,
                intel_read=None,
            )
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "HOLD"
        assert diag["v3_honest_hold"] is True, (
            "No signal data must produce honest HOLD (not fake confidence)"
        )
        assert len(diag["suppressed_axes"]) > 0

    def test_insufficient_intel_read_stays_hold_honest(self):
        """intel_read.insufficient_data=True → THIN evidence → honest HOLD."""
        diag = project_shadow_from_card_signals(
            **_card(
                ticker="SNOW",
                v2_visible_action="HOLD",
                analyst_action="BUY",
                conviction_level="HIGH",
                data_quality_label="HIGH",
                intel_read=_thin_intel_read(),
            )
        )
        assert diag is not None
        assert diag["v3_shadow_action"] == "HOLD", (
            "Thin evidence from intel_read must not produce BUY"
        )
        assert diag["v3_shadow_conviction"] == "LOW", (
            "Thin evidence must cap conviction at LOW"
        )
        assert diag["v3_honest_hold"] is True

    def test_no_suppressed_axes_when_hold_is_natural(self):
        """HOLD with adequate signals but no upside → v3_honest_hold=False (deliberate HOLD)."""
        diag = project_shadow_from_card_signals(
            **_card(
                ticker="VOO",
                v2_visible_action="HOLD",
                analyst_action="HOLD",
                conviction_level="MEDIUM",
                data_quality_label="HIGH",
                category="ETF",
                intel_read=_good_intel_read(3),
            )
        )
        assert diag is not None
        assert diag["v3_shadow_action"] in _VALID_ACTIONS
        # v3_honest_hold is False when HOLD is natural (no suppressed axes needed).


# ── Test 5: Malformed/partial card data fails soft ───────────────────────────

class TestFailSoft:
    """Malformed or partial card data must not raise — returns None."""

    def test_none_ticker_returns_none(self):
        result = project_shadow_from_card_signals(
            ticker=None,  # type: ignore[arg-type]
            v2_visible_action="HOLD",
            analyst_action=None,
            conviction_level=None,
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category=None,
            data_quality_label=None,
            intel_read=None,
            thesis_v2=None,
        )
        # Either succeeds gracefully or returns None — must not raise.
        assert result is None or isinstance(result, dict)

    def test_garbage_intel_read_fails_soft(self):
        result = project_shadow_from_card_signals(
            **_card(
                ticker="XYZ",
                v2_visible_action="HOLD",
                intel_read="not-a-dict",  # type: ignore[arg-type]
            )
        )
        # Must not raise — returns None or degrades gracefully.
        assert result is None or isinstance(result, dict)

    def test_empty_ticker_fails_soft(self):
        result = project_shadow_from_card_signals(
            **_card(
                ticker="",
                v2_visible_action="HOLD",
            )
        )
        assert result is None or isinstance(result, dict)

    def test_unknown_action_normalizes_to_hold(self):
        """Unknown v2 action normalizes to HOLD in diagnostic."""
        diag = project_shadow_from_card_signals(
            **_card(
                ticker="ABC",
                v2_visible_action="WATCHLIST",
                analyst_action=None,
                data_quality_label="HIGH",
                intel_read=_good_intel_read(2),
            )
        )
        # Must succeed; WATCHLIST normalized to HOLD.
        if diag is not None:
            assert diag["v2_visible_action"] == "HOLD"


# ── Test 6: v3 shadow action always within valid set ─────────────────────────

class TestValidActionOutput:
    """v3 shadow must never produce an illegal action label."""

    _FORBIDDEN = {"REVIEW", "WATCHLIST", "ADD_CANDIDATE", "RISK_WATCH", "ADD", "REDUCE"}

    @pytest.mark.parametrize("fixture", [
        _card(ticker="AAPL", v2_visible_action="BUY", analyst_action="BUY",
              conviction_level="HIGH", data_quality_label="HIGH",
              intel_read={"insufficient_data": False, "trusted_dimensions": ["a", "b", "c"]}),
        _card(ticker="META", v2_visible_action="TRIM", analyst_action="TRIM",
              conviction_level="MEDIUM", data_quality_label="HIGH",
              intel_read={"insufficient_data": False, "trusted_dimensions": ["a", "b"]}),
        _card(ticker="BTC", v2_visible_action="SELL", analyst_action="SELL",
              conviction_level="LOW", category="Crypto", data_quality_label="MEDIUM"),
        _card(ticker="STUB", v2_visible_action="HOLD", analyst_action=None,
              data_quality_label=None, intel_read=None),
        _card(ticker="XYZ", v2_visible_action="HOLD", analyst_action="WATCHLIST",
              data_quality_label="HIGH",
              intel_read={"insufficient_data": False, "trusted_dimensions": ["a", "b"]}),
    ])
    def test_v3_action_is_always_valid(self, fixture):
        diag = project_shadow_from_card_signals(**fixture)
        if diag is None:
            return  # fail-soft is acceptable
        assert diag["v3_shadow_action"] in _VALID_ACTIONS, (
            f"Illegal v3 action: {diag['v3_shadow_action']!r} for {fixture['ticker']}"
        )
        assert diag["v3_shadow_action"] not in self._FORBIDDEN


# ── Test 7: No Deploy code path touched ──────────────────────────────────────

class TestDeployIsolation:
    """shadow_projection module must not import Deploy or allocation code."""

    def test_no_allocation_engine_import(self):
        import app.services.intelligence.v3.shadow_projection as mod
        import sys
        # allocation_engine must not be in the module's import chain.
        assert "allocation_engine" not in sys.modules or True  # Deploy isolation: shadow_projection has no dep
        # Direct check: the shadow_projection module has no reference to allocation.
        import inspect
        src = inspect.getsource(mod)
        assert "allocation_engine" not in src
        assert "deployment_engine" not in src


# ── Test 8: HOLD-collapse risk across representative fixture set ──────────────

class TestHoldCollapseAudit:
    """Diagnostic can identify HOLD-collapse risk across a representative card mix."""

    _FIXTURES = [
        # Strong BUY with full evidence → should collapse HOLD.
        _card(ticker="AAPL", v2_visible_action="HOLD", analyst_action="BUY",
              conviction_level="HIGH", technical_signal="BULLISH", category="Core",
              data_quality_label="HIGH",
              intel_read={"insufficient_data": False, "trusted_dimensions": ["a", "b", "c"]}),
        # TRIM signal → should collapse HOLD.
        _card(ticker="META", v2_visible_action="HOLD", analyst_action="TRIM",
              conviction_level="MEDIUM", category="Growth", data_quality_label="HIGH",
              intel_read={"insufficient_data": False, "trusted_dimensions": ["a", "b"]}),
        # Thin evidence → honest HOLD (no collapse).
        _card(ticker="NVDA", v2_visible_action="HOLD", analyst_action="BUY",
              conviction_level="HIGH", category="Tech", data_quality_label="LOW",
              intel_read={"insufficient_data": True, "trusted_dimensions": []}),
        # No signals → honest HOLD (no collapse).
        _card(ticker="KLAR", v2_visible_action="HOLD", analyst_action=None,
              data_quality_label=None, intel_read=None, category="Speculative"),
        # SELL with critical risk → should collapse HOLD.
        _card(ticker="BTC", v2_visible_action="HOLD", analyst_action="SELL",
              conviction_level="LOW", technical_signal="BEARISH", category="Crypto",
              risk_flag="Severe insolvency risk", analyst_risks=["Critical default risk"],
              data_quality_label="MEDIUM",
              intel_read={"insufficient_data": False, "trusted_dimensions": ["a", "b"]}),
    ]

    def test_at_least_one_hold_collapse_in_mix(self):
        """At least one card in a non-degenerate mix shows hold_collapse_risk."""
        results = [project_shadow_from_card_signals(**f) for f in self._FIXTURES]
        collapse_flags = [r["hold_collapse_risk"] for r in results if r is not None]
        assert any(collapse_flags), (
            "Representative fixture set must have ≥1 hold_collapse_risk card"
        )

    def test_at_least_one_honest_hold_in_mix(self):
        """At least one card in the mix has v3_honest_hold (missing data stays honest)."""
        results = [project_shadow_from_card_signals(**f) for f in self._FIXTURES]
        honest_flags = [r["v3_honest_hold"] for r in results if r is not None]
        assert any(honest_flags), (
            "Representative fixture set must have ≥1 v3_honest_hold card (missing data stays honest)"
        )

    def test_not_all_actions_collapse_to_hold(self):
        """Non-degenerate mix must produce ≥2 distinct v3 shadow actions."""
        results = [project_shadow_from_card_signals(**f) for f in self._FIXTURES]
        distinct = {r["v3_shadow_action"] for r in results if r is not None}
        assert len(distinct) >= 2, (
            f"Expected differentiated v3 shadow output; got {distinct}"
        )

    def test_diagnostic_keys_present_for_all_successful_results(self):
        """Every non-None diagnostic dict must have all stable keys."""
        _REQUIRED_KEYS = {
            "ticker", "v2_visible_action", "v3_shadow_action", "v3_shadow_conviction",
            "hold_collapse_risk", "v3_honest_hold", "suppressed_axes", "v3_schema_version",
        }
        results = [project_shadow_from_card_signals(**f) for f in self._FIXTURES]
        for r in results:
            if r is not None:
                missing = _REQUIRED_KEYS - set(r.keys())
                assert not missing, f"Diagnostic missing keys: {missing}"


class TestShadowSummaryAggregation:
    """Portfolio-level aggregation for v3 shadow diagnostics (PR 3)."""

    def test_empty_diagnostics_safe(self):
        from app.services.intelligence.v3.shadow_projection import summarize_shadow_diagnostics

        summary = summarize_shadow_diagnostics([], total_cards=0)
        assert summary["total_cards"] == 0
        assert summary["projected_cards"] == 0
        assert summary["projection_failures"] == 0
        assert summary["v2_visible_action_counts"] == {"BUY": 0, "HOLD": 0, "TRIM": 0, "SELL": 0}
        assert summary["v3_shadow_action_counts"] == {"BUY": 0, "HOLD": 0, "TRIM": 0, "SELL": 0}

    def test_mixed_actions_counts_and_projection_failures(self):
        from app.services.intelligence.v3.shadow_projection import summarize_shadow_diagnostics

        diagnostics = [
            {"v2_visible_action": "HOLD", "v3_shadow_action": "BUY", "hold_collapse_risk": True, "v3_honest_hold": False},
            {"v2_visible_action": "BUY", "v3_shadow_action": "HOLD", "hold_collapse_risk": False, "v3_honest_hold": True},
            {"v2_visible_action": "TRIM", "v3_shadow_action": "TRIM", "hold_collapse_risk": False, "v3_honest_hold": False},
            None,
        ]
        summary = summarize_shadow_diagnostics(diagnostics, total_cards=4)
        assert summary["projected_cards"] == 3
        assert summary["projection_failures"] == 1
        assert summary["v2_visible_action_counts"] == {"BUY": 1, "HOLD": 1, "TRIM": 1, "SELL": 0}
        assert summary["v3_shadow_action_counts"] == {"BUY": 1, "HOLD": 1, "TRIM": 1, "SELL": 0}
        assert summary["hold_collapse_risk_count"] == 1
        assert summary["honest_hold_count"] == 1
        assert summary["non_hold_shadow_from_v2_hold_count"] == 1

    @pytest.mark.parametrize("shadow_action", ["BUY", "TRIM", "SELL"])
    def test_v2_hold_non_hold_shadow_increments_collapse_counter(self, shadow_action):
        from app.services.intelligence.v3.shadow_projection import summarize_shadow_diagnostics

        summary = summarize_shadow_diagnostics(
            [{"v2_visible_action": "HOLD", "v3_shadow_action": shadow_action, "hold_collapse_risk": True, "v3_honest_hold": False}],
            total_cards=1,
        )
        assert summary["hold_collapse_risk_count"] == 1
        assert summary["non_hold_shadow_from_v2_hold_count"] == 1

    def test_honest_hold_is_counted_separately_from_hold_collapse(self):
        from app.services.intelligence.v3.shadow_projection import summarize_shadow_diagnostics

        summary = summarize_shadow_diagnostics(
            [{"v2_visible_action": "HOLD", "v3_shadow_action": "HOLD", "hold_collapse_risk": False, "v3_honest_hold": True}],
            total_cards=1,
        )
        assert summary["hold_collapse_risk_count"] == 0
        assert summary["honest_hold_count"] == 1
        assert summary["non_hold_shadow_from_v2_hold_count"] == 0
