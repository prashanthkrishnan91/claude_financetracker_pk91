"""Stage 9J — Portfolio-fit and ETF evidence signal wiring tests.

Verifies that Intel context adapters:
  - Distinguish underweight/on-target/overweight/no-target-data for stocks and ETFs.
  - Wire portfolio_current_pct into portfolio_weight_context so the drawer can show
    the actual current weight alongside the FitBand description.
  - Pass ETF provider outputs (NPORT/AV/FMP fixtures) through to context without
    fabricating readiness when data is absent.
  - Pass ETF upstream signals (is_redundant_etf etc.) through when present and
    degrade honestly when absent.
  - Preserve existing visible action and Stage 9I lens routing (regression gate).

Fixture-based only. No IO, no DB, no LLM, no SQL.

Coverage:
  9J-01. Underweight stock with OK evidence → context supports add/build, no unknown-lens copy.
  9J-02. On-target stock HOLD → maintain-current-allocation context.
  9J-03. Overweight stock → trim-oriented context, no add encouragement.
  9J-04. Stock with no portfolio target (UNKNOWN fit) → explicit "no target allocation" context.
  9J-05. Underweight ETF (VTI) with OK evidence → context mentions sleeve/allocation add language.
  9J-06. On-target ETF (SCHD) → HOLD context with target weight language.
  9J-07. Overweight ETF → trim context, no add language.
  9J-08. ETF with no provider outputs → no holdings/cost/overlap readiness claimed;
         PROFILE_READY tier says "not yet wired" explicitly.
  9J-09. ETF with NPORT holdings-ready fixture → holdings context surfaces in why_this_action.
  9J-10. ETF with AV missing-date fixture → profile_ready at most, not holdings_ready.
  9J-11. ETF with FMP 402/paywalled fixture → no holdings readiness claimed.
  9J-12. Upstream signals present (is_redundant_etf=True) → flow into context output.
  9J-13. Upstream signals absent → context degrades honestly (no fake overlap claims).
  9J-14. VTI/SCHD/VXUS/GLD regression — lens routing unchanged.
  9J-15. Existing visible action is always preserved.
  9J-16. portfolio_current_pct in card_meta flows into portfolio_weight_context with
         the actual weight, fit-aware plain-English note, and UNKNOWN says no target.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.intel_context_adapter_v1 import build_intel_context
from app.services.intelligence.v3.asset_intelligence_composer_v1 import (
    LENS_STOCK_FUNDAMENTAL,
    LENS_ETF_ROLE,
    LENS_COMMODITY_HEDGE,
    FIT_UNDERWEIGHT,
    FIT_ON_TARGET,
    FIT_OVERWEIGHT,
    FIT_UNKNOWN,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _stock_ctx(
    ticker: str,
    fit: str = FIT_UNKNOWN,
    eq: str = "OK",
    action: str = "HOLD",
    upstream_signals: dict | None = None,
    portfolio_current_pct: float | None = None,
) -> dict | None:
    return build_intel_context(
        ticker=ticker,
        asset_type="stock",
        portfolio_fit_raw=fit,
        evidence_quality_raw=eq,
        existing_action=action,
        upstream_signals=upstream_signals,
        portfolio_current_pct=portfolio_current_pct,
    )


def _etf_ctx(
    ticker: str,
    fit: str = FIT_UNKNOWN,
    eq: str = "OK",
    action: str = "HOLD",
    provider_outputs: dict | None = None,
    upstream_signals: dict | None = None,
    portfolio_current_pct: float | None = None,
) -> dict | None:
    return build_intel_context(
        ticker=ticker,
        asset_type="etf",
        portfolio_fit_raw=fit,
        evidence_quality_raw=eq,
        existing_action=action,
        provider_outputs=provider_outputs,
        upstream_signals=upstream_signals,
        portfolio_current_pct=portfolio_current_pct,
    )


def _make_decision(
    ticker: str = "TEST",
    action: str = "HOLD",
    fit: str = "UNKNOWN",
    eq: str = "OK",
):
    from app.services.intelligence.v3.decision_contracts import (
        ActionV3, AxisBand, ConvictionV3, DecisionOutputV3,
        FitBand, RiskBand, PriceBand,
    )
    return DecisionOutputV3(
        ticker=ticker,
        action=ActionV3[action],
        conviction=ConvictionV3.MEDIUM,
        evidence_quality=AxisBand[eq],
        portfolio_fit=FitBand[fit],
        risk_band=RiskBand.LOW,
        price_context=PriceBand.SUPPRESSED,
        attractiveness=AxisBand.OK,
        rationale_plain_english="Test rationale.",
        why_now="",
        why_not_now="",
        blockers=[],
        suppression_reasons={},
        source_signal_summary={},
        schema_version="v3.1",
    )


def _snap_card(
    ticker: str,
    category: str,
    action: str = "HOLD",
    fit: str = "UNKNOWN",
    eq: str = "OK",
    extra_meta: dict | None = None,
) -> dict:
    from app.services.intelligence.v3.snapshot_builder import build_snapshot
    decision = _make_decision(ticker=ticker, action=action, fit=fit, eq=eq)
    meta: dict = {"ticker": ticker, "name": ticker, "category": category}
    if extra_meta:
        meta.update(extra_meta)
    snap = build_snapshot(run_id="test-9j", decisions=[decision], card_metas=[meta])
    return snap["current_holdings"][0]


# ── 9J-01: Underweight stock with OK evidence → supports add/build ────────────


class TestStockUnderweightContext:
    """9J-01: underweight stock produces add/build context without unknown-lens copy."""

    def test_underweight_stock_ok_evidence_supports_adding(self):
        ctx = _stock_ctx("MSFT", fit=FIT_UNDERWEIGHT, eq="OK", action="BUY")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_STOCK_FUNDAMENTAL
        combined = (ctx["why_this_action"] + ctx.get("role_lens", "")).lower()
        assert any(kw in combined for kw in ("add", "supports adding", "underweight")), (
            f"MSFT UNDERWEIGHT+OK should mention adding but got: {combined!r}"
        )

    def test_underweight_stock_no_unknown_lens_copy(self):
        ctx = _stock_ctx("AAPL", fit=FIT_UNDERWEIGHT, eq="OK", action="BUY")
        assert ctx is not None
        combined = " ".join([
            ctx.get("role_lens") or "",
            ctx.get("why_this_action") or "",
            ctx.get("evidence_caveat") or "",
        ]).lower()
        assert "asset type not recognized" not in combined
        assert "intelligence lens cannot be applied" not in combined

    def test_underweight_stock_strong_evidence_also_supports_adding(self):
        ctx = _stock_ctx("NVDA", fit=FIT_UNDERWEIGHT, eq="STRONG", action="BUY")
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        assert any(kw in why for kw in ("add", "underweight", "supports adding"))

    def test_underweight_stock_thin_evidence_does_not_claim_adding(self):
        """Thin evidence should not support adding even when underweight."""
        ctx = _stock_ctx("NVDA", fit=FIT_UNDERWEIGHT, eq="THIN", action="HOLD")
        assert ctx is not None
        # Thin evidence → blocked → evidence caveat present
        assert ctx.get("evidence_caveat") is not None

    def test_underweight_stock_through_snapshot_builder(self):
        card = _snap_card("MSFT", "Technology", action="BUY", fit="UNDERWEIGHT", eq="OK")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_STOCK_FUNDAMENTAL
        combined = (ctx["why_this_action"] + ctx.get("role_lens", "")).lower()
        assert any(kw in combined for kw in ("add", "underweight", "supports adding"))


# ── 9J-02: On-target stock HOLD → maintain-current-allocation ────────────────


class TestStockOnTargetContext:
    """9J-02: on-target stock produces maintain-current-allocation context."""

    def test_on_target_stock_hold_produces_maintain_allocation(self):
        ctx = _stock_ctx("META", fit=FIT_ON_TARGET, eq="OK", action="HOLD")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_STOCK_FUNDAMENTAL
        why = ctx["why_this_action"].lower()
        assert any(kw in why for kw in ("target weight", "maintain", "allocation")), (
            f"ON_TARGET HOLD should mention maintaining allocation but got: {why!r}"
        )

    def test_on_target_stock_no_add_encouragement(self):
        ctx = _stock_ctx("COST", fit=FIT_ON_TARGET, eq="OK", action="HOLD")
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        assert "supports adding" not in why
        assert "adds builds" not in why

    def test_on_target_stock_through_snapshot_builder(self):
        card = _snap_card("META", "stock", action="HOLD", fit="ON_TARGET", eq="OK")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        assert any(kw in why for kw in ("target weight", "maintain", "allocation"))


# ── 9J-03: Overweight stock → trim-oriented, no add encouragement ────────────


class TestStockOverweightContext:
    """9J-03: overweight stock does not encourage adding."""

    @pytest.mark.parametrize("ticker,fit", [
        ("AAPL", "OVERWEIGHT"), ("MSFT", "BREACH"),
    ])
    def test_overweight_stock_no_add_language(self, ticker, fit):
        ctx = _stock_ctx(ticker, fit=fit, eq="OK", action="TRIM")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_STOCK_FUNDAMENTAL
        # Check why_this_action only — add_more_trigger is a generic "when to add" description
        # that is lens-level, not portfolio-fit-specific.
        why = ctx["why_this_action"].lower()
        assert "supports adding" not in why
        assert "fundamental analysis supports adding" not in why

    @pytest.mark.parametrize("ticker,fit", [
        ("AAPL", "OVERWEIGHT"), ("MSFT", "BREACH"),
    ])
    def test_overweight_stock_mentions_trim(self, ticker, fit):
        ctx = _stock_ctx(ticker, fit=fit, eq="OK", action="TRIM")
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        assert any(kw in why for kw in ("trim", "overexposure", "above target", "reduce"))

    def test_overweight_etf_no_add_language(self):
        ctx = _etf_ctx("VTI", fit="OVERWEIGHT", eq="OK", action="TRIM")
        assert ctx is not None
        combined = (ctx["why_this_action"] + ctx.get("add_more_trigger", "")).lower()
        assert "adding builds" not in combined


# ── 9J-04: Stock with no portfolio target (UNKNOWN fit) ──────────────────────


class TestStockUnknownFitContext:
    """9J-04: stock with UNKNOWN portfolio fit → explicit 'no target allocation' context."""

    def test_unknown_fit_mentions_no_target_allocation(self):
        ctx = _stock_ctx("NVDA", fit=FIT_UNKNOWN, eq="OK", action="HOLD")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_STOCK_FUNDAMENTAL
        combined = (ctx["why_this_action"] + ctx.get("role_lens", "")).lower()
        assert any(kw in combined for kw in (
            "no target", "no target allocation", "monitoring"
        )), f"UNKNOWN fit should say 'no target allocation' but got: {combined!r}"

    def test_unknown_fit_no_add_encouragement(self):
        ctx = _stock_ctx("NVDA", fit=FIT_UNKNOWN, eq="OK", action="HOLD")
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        assert "supports adding" not in why

    def test_unknown_fit_no_unknown_lens_copy(self):
        ctx = _stock_ctx("QCOM", fit=FIT_UNKNOWN, eq="OK", action="HOLD")
        assert ctx is not None
        combined = " ".join([
            ctx.get("role_lens") or "", ctx.get("why_this_action") or ""
        ]).lower()
        assert "asset type not recognized" not in combined
        assert "intelligence lens cannot be applied" not in combined


# ── 9J-05/06/07: ETF portfolio-fit context ───────────────────────────────────


class TestETFPortfolioFitContext:
    """9J-05/06/07: ETF context reflects underweight/on-target/overweight."""

    def test_underweight_etf_vti_mentions_sleeve(self):
        """9J-05: underweight ETF (VTI) → context mentions sleeve/allocation add language."""
        ctx = _etf_ctx("VTI", fit=FIT_UNDERWEIGHT, eq="OK", action="BUY")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE
        combined = (ctx["why_this_action"] + ctx.get("add_more_trigger", "")).lower()
        assert any(kw in combined for kw in (
            "sleeve", "allocation", "underweight", "adds", "target"
        )), f"VTI UNDERWEIGHT should mention allocation/sleeve but got: {combined!r}"

    def test_on_target_schd_hold_mentions_target(self):
        """9J-06: on-target SCHD → HOLD context with target weight language."""
        ctx = _etf_ctx("SCHD", fit=FIT_ON_TARGET, eq="OK", action="HOLD")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE
        why = ctx["why_this_action"].lower()
        assert any(kw in why for kw in ("target weight", "target", "maintain", "aligned"))

    def test_overweight_etf_trim_language(self):
        """9J-07: overweight ETF → trim context, no add language."""
        ctx = _etf_ctx("VTI", fit=FIT_OVERWEIGHT, eq="OK", action="TRIM")
        assert ctx is not None
        why = ctx["why_this_action"].lower()
        assert any(kw in why for kw in ("trim", "above target", "overweight", "allocation"))
        assert "adds builds" not in why


# ── 9J-08: ETF with no provider outputs → no readiness claimed ───────────────


class TestETFNoProviderOutputs:
    """9J-08: ETF with no provider_outputs must not claim holdings/cost/overlap readiness."""

    @pytest.mark.parametrize("ticker", ["VOO", "VTI", "SCHD", "VXUS"])
    def test_known_etf_no_provider_outputs_no_holdings_claim(self, ticker):
        ctx = _etf_ctx(ticker, fit=FIT_UNKNOWN, eq="OK")
        assert ctx is not None
        serialised = str(ctx).lower()
        assert "holdings_ready" not in serialised
        assert "overlap_safe" not in serialised
        assert "safe_for_decision" not in ctx
        assert "synthesis_ready" not in ctx

    def test_unknown_etf_no_provider_outputs_no_fake_confidence(self):
        ctx = _etf_ctx("UNKNETF", fit=FIT_UNKNOWN, eq="OK")
        if ctx is not None:
            serialised = str(ctx).lower()
            assert "holdings_ready" not in serialised
            assert "safe_for_decision" not in ctx

    def test_no_cost_claim_without_provider(self):
        """ETF without provider data must not claim cost analysis is available."""
        ctx = _etf_ctx("SCHD", fit=FIT_UNKNOWN, eq="OK")
        assert ctx is not None
        combined = (ctx.get("why_this_action", "") + ctx.get("role_lens", "")).lower()
        assert "cost analysis" not in combined or "requires" in combined

    def test_profile_ready_tier_says_not_yet_wired(self):
        """ETF with PROFILE_READY tier (known ETF, no provider data) must say evidence not yet wired,
        not claim holdings/cost/overlap analysis is available."""
        ctx = _etf_ctx("VTI", fit=FIT_UNKNOWN, eq="OK")
        assert ctx is not None
        combined = (ctx.get("why_this_action", "") + ctx.get("role_lens", "")).lower()
        # Must say something about evidence not being wired — not imply it's available
        assert "not yet wired" in combined or "requires provider" in combined, (
            f"PROFILE_READY ETF without providers should say 'not yet wired' but got: {combined!r}"
        )


# ── 9J-09: ETF with NPORT holdings-ready fixture ─────────────────────────────


class TestETFNPORTHoldingsReady:
    """9J-09: NPORT holdings-ready fixture passes provider_outputs into context
    and context reflects holdings-tier evidence language."""

    _NPORT_HOLDINGS_READY = {
        "nport_output": {
            "fetch_status": "ok",
            "holdings_count": 50,
            "weights_available": True,
            "report_period_date": "2024-12-31",
            "coverage_quality": "plausible",
        },
    }

    def test_nport_holdings_ready_context_reflects_holdings_tier(self):
        ctx = _etf_ctx(
            "VOO",
            fit=FIT_UNDERWEIGHT,
            eq="OK",
            action="BUY",
            provider_outputs=self._NPORT_HOLDINGS_READY,
        )
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE
        combined = (ctx["why_this_action"] + ctx.get("role_lens", "")).lower()
        assert any(kw in combined for kw in ("holdings", "overlap", "concentration"))

    def test_nport_holdings_ready_no_fake_safety_flags(self):
        ctx = _etf_ctx("VOO", provider_outputs=self._NPORT_HOLDINGS_READY)
        assert ctx is not None
        assert "safe_for_decision" not in ctx
        assert "synthesis_ready" not in ctx

    def test_nport_holdings_ready_through_snapshot_builder(self):
        card = _snap_card(
            "VOO", "etf", action="BUY", fit="UNDERWEIGHT", eq="OK",
            extra_meta={"etf_provider_outputs": self._NPORT_HOLDINGS_READY},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE
        combined = (ctx["why_this_action"] + ctx.get("role_lens", "")).lower()
        assert any(kw in combined for kw in ("holdings", "overlap", "concentration"))


# ── 9J-10: AV missing-date fixture → supplemental/profile-only ───────────────


class TestETFAVMissingDate:
    """9J-10: AV with missing as-of date must remain supplemental/profile-only
    and must NOT produce holdings_ready tier."""

    _AV_NO_DATE = {
        "av_output": {
            "holdings_available": True,
            "coverage_quality": "usable_supplemental",
            "as_of_date_verified": False,   # no date — canonical rejection reason
            "holdings_count": 519,
            "canonical_ready": False,
            "safe_for_decision": False,
        },
    }

    def test_av_no_date_does_not_produce_holdings_ready(self):
        """AV without as_of_date must not reach holdings_ready tier."""
        ctx = _etf_ctx("VOO", provider_outputs=self._AV_NO_DATE)
        assert ctx is not None
        combined = str(ctx).lower()
        # holdings_ready tier text must not appear
        assert "full holdings data available" not in combined

    def test_av_no_date_profile_ready_acceptable(self):
        """AV without date may reach profile_ready (exposure/category signal available)."""
        ctx = _etf_ctx("VOO", provider_outputs=self._AV_NO_DATE)
        assert ctx is not None
        # Profile-ready context: role is identified, cost/holdings requires provider data
        assert ctx["lens_applied"] == LENS_ETF_ROLE

    def test_av_no_date_safe_for_decision_always_false(self):
        ctx = _etf_ctx("VOO", provider_outputs=self._AV_NO_DATE)
        assert ctx is not None
        assert "safe_for_decision" not in ctx
        assert "synthesis_ready" not in ctx

    def test_av_no_date_through_snapshot_builder(self):
        card = _snap_card(
            "VOO", "etf", action="HOLD", fit="UNKNOWN", eq="OK",
            extra_meta={"etf_provider_outputs": self._AV_NO_DATE},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE
        combined = str(ctx).lower()
        assert "full holdings data available" not in combined


# ── 9J-11: FMP 402/paywalled fixture → no holdings readiness ─────────────────


class TestETFFMPPaywalled:
    """9J-11: FMP 402/paywalled fixture must not imply holdings readiness."""

    _FMP_PAYWALLED = {
        "fmp_output": {
            "fetch_status": "paywalled",
            "error": "HTTP 402 Payment Required",
            "holdings_count": 0,
            "weights_available": False,
        },
    }

    def test_fmp_paywalled_no_holdings_readiness(self):
        ctx = _etf_ctx("VOO", provider_outputs=self._FMP_PAYWALLED)
        assert ctx is not None
        combined = str(ctx).lower()
        assert "full holdings data available" not in combined
        assert "holdings_ready" not in combined

    def test_fmp_paywalled_known_etf_still_gets_profile_tier(self):
        """Known ETF type provides profile_ready floor regardless of FMP status."""
        ctx = _etf_ctx("VOO", provider_outputs=self._FMP_PAYWALLED)
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE

    def test_fmp_paywalled_safe_flags_absent(self):
        ctx = _etf_ctx("VOO", provider_outputs=self._FMP_PAYWALLED)
        assert ctx is not None
        assert "safe_for_decision" not in ctx
        assert "synthesis_ready" not in ctx

    def test_fmp_paywalled_through_snapshot_builder(self):
        card = _snap_card(
            "VOO", "etf", action="HOLD", fit="UNKNOWN", eq="OK",
            extra_meta={"etf_provider_outputs": self._FMP_PAYWALLED},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        combined = str(ctx).lower()
        assert "full holdings data available" not in combined


# ── 9J-12/13: ETF upstream signals ───────────────────────────────────────────


class TestETFUpstreamSignals:
    """9J-12/13: upstream signals flow into context when present; degrade honestly
    when absent."""

    def test_redundant_etf_signal_flows_into_context(self):
        """9J-12: is_redundant_etf=True → duplicate-exposure language appears."""
        ctx = _etf_ctx(
            "VOO",
            fit=FIT_UNKNOWN,
            eq="OK",
            upstream_signals={"is_redundant_etf": True},
        )
        assert ctx is not None
        combined = (ctx["why_this_action"] + ctx["trim_sell_trigger"]).lower()
        assert any(kw in combined for kw in ("duplicate", "redundant", "another holding", "overlaps"))

    def test_role_mismatch_signal_flows_into_context(self):
        ctx = _etf_ctx(
            "SCHD",
            fit=FIT_UNDERWEIGHT,
            eq="OK",
            action="HOLD",
            upstream_signals={"role_mismatch": True},
        )
        assert ctx is not None
        combined = (ctx["why_this_action"] + ctx["trim_sell_trigger"]).lower()
        assert any(kw in combined for kw in ("role", "mismatch", "sleeve", "intended"))

    def test_cost_elevated_signal_flows_into_context(self):
        ctx = _etf_ctx(
            "VOO",
            fit=FIT_UNKNOWN,
            eq="OK",
            upstream_signals={"cost_elevated": True},
        )
        assert ctx is not None
        combined = (ctx["why_this_action"] + ctx["trim_sell_trigger"]).lower()
        assert any(kw in combined for kw in ("expense", "cost", "peer"))

    def test_no_upstream_signals_honest_degradation(self):
        """9J-13: when no upstream signals are present, context does not claim
        overlap/redundancy analysis was performed."""
        ctx = _etf_ctx("VTI", fit=FIT_UNKNOWN, eq="OK")
        assert ctx is not None
        combined = str(ctx).lower()
        assert "duplicate exposure" not in combined
        assert "overlaps significantly" not in combined

    def test_upstream_signals_absent_from_card_meta_degrade_honestly(self):
        """No upstream signals in card_meta → snapshot builder uses None → no fake claims."""
        card = _snap_card("VTI", "etf", action="HOLD")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        combined = str(ctx).lower()
        assert "duplicate exposure" not in combined
        assert "overlaps significantly" not in combined

    def test_upstream_signals_in_card_meta_flow_through_snapshot_builder(self):
        """Upstream signals in card_meta → snapshot builder extracts and passes to adapter."""
        card = _snap_card(
            "VTI", "etf", action="HOLD",
            extra_meta={"etf_upstream_signals": {"is_redundant_etf": True}},
        )
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        combined = (ctx["why_this_action"] + ctx["trim_sell_trigger"]).lower()
        assert any(kw in combined for kw in ("duplicate", "redundant", "another holding", "overlaps"))


# ── 9J-14: VTI/SCHD/VXUS/GLD regression gate ────────────────────────────────


class TestStage9JRegressionGate:
    """9J-14: VTI/SCHD/VXUS/GLD lens routing is unchanged after Stage 9J edits."""

    @pytest.mark.parametrize("ticker,category", [
        ("VTI",  "etf"),
        ("SCHD", "etf"),
        ("VXUS", "etf"),
    ])
    def test_known_etf_routes_to_etf_role_lens(self, ticker, category):
        card = _snap_card(ticker, category, action="HOLD")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None, f"{ticker} must have asset_intelligence_context"
        assert ctx["lens_applied"] == LENS_ETF_ROLE, (
            f"{ticker} got lens {ctx['lens_applied']!r}"
        )

    def test_gld_routes_to_commodity_hedge_lens(self):
        card = _snap_card("GLD", "etf", action="HOLD")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_COMMODITY_HEDGE

    def test_gld_context_no_failure_language(self):
        """GLD context should use commodity/hedge language with correct disclaimers,
        not asset-type-not-recognized failure text."""
        ctx = _etf_ctx("GLD")
        assert ctx is not None
        combined = " ".join([
            ctx.get("role_lens") or "", ctx.get("why_this_action") or ""
        ]).lower()
        # These are failure indicators — should never appear
        assert "asset type not recognized" not in combined
        assert "intelligence lens cannot be applied" not in combined
        # GLD must use commodity/hedge language
        assert any(kw in combined for kw in ("commodity", "hedge", "inflation"))

    @pytest.mark.parametrize("ticker,category", [
        ("AAPL",  "stock"),
        ("MSFT",  "Technology"),
        ("SNOW",  "Other"),
    ])
    def test_stock_routes_to_stock_fundamental_lens(self, ticker, category):
        card = _snap_card(ticker, category, action="HOLD", eq="OK")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_STOCK_FUNDAMENTAL

    def test_known_etf_with_generic_category_still_routes_to_etf_lens(self):
        """After Stage 9J, ETF lens routing with generic 'Other' category unchanged."""
        for ticker in ("VTI", "SCHD", "VXUS"):
            card = _snap_card(ticker, "Other", action="HOLD")
            ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
            assert ctx is not None, f"{ticker} with category='Other' returned no context"
            assert ctx["lens_applied"] == LENS_ETF_ROLE


# ── 9J-15: Existing visible action preserved ─────────────────────────────────


class TestVisibleActionPreserved:
    """9J-15: existing visible action is always preserved regardless of
    portfolio-fit context or upstream signals."""

    @pytest.mark.parametrize("action", ["BUY", "HOLD", "TRIM", "SELL"])
    def test_stock_action_preserved_for_all_actions(self, action):
        card = _snap_card("AAPL", "stock", action=action, fit="UNDERWEIGHT", eq="OK")
        assert card["action"] == action

    @pytest.mark.parametrize("action", ["BUY", "HOLD", "TRIM", "SELL"])
    def test_etf_action_preserved_for_all_actions(self, action):
        card = _snap_card("VTI", "etf", action=action, fit="UNDERWEIGHT", eq="OK")
        assert card["action"] == action

    def test_etf_action_preserved_with_upstream_signals(self):
        card = _snap_card(
            "VTI", "etf", action="HOLD",
            extra_meta={"etf_upstream_signals": {"is_redundant_etf": True}},
        )
        assert card["action"] == "HOLD"


# ── 9J-16: portfolio_current_pct wired into portfolio_weight_context ──────────


class TestPortfolioCurrentPctInContext:
    """9J-16: portfolio_current_pct flows through the pipeline into
    portfolio_weight_context in asset_intelligence_context.

    - Actual weight % appears in the context.
    - UNDERWEIGHT: says "room to grow toward target".
    - ON_TARGET: says "at target allocation".
    - OVERWEIGHT: says "above target".
    - UNKNOWN: says "no target allocation is set", does NOT say "room to add".
    - None/absent: portfolio_weight_context key is absent (no fabrication).
    """

    def test_underweight_with_current_pct_shows_weight_in_context(self):
        """UNDERWEIGHT + current_pct=3.2 → portfolio_weight_context contains '3.2%'."""
        ctx = _stock_ctx("MSFT", fit=FIT_UNDERWEIGHT, eq="OK", action="BUY",
                         portfolio_current_pct=3.2)
        assert ctx is not None
        weight_ctx = ctx.get("portfolio_weight_context")
        assert weight_ctx is not None, "portfolio_weight_context must be present when pct is provided"
        assert "3.2%" in weight_ctx, f"Expected '3.2%' in weight context but got: {weight_ctx!r}"
        assert "room to grow" in weight_ctx.lower() or "underweight" in weight_ctx.lower(), (
            f"UNDERWEIGHT weight context should mention room to grow: {weight_ctx!r}"
        )

    def test_on_target_with_current_pct_shows_at_target(self):
        """ON_TARGET + current_pct=5.0 → portfolio_weight_context says 'at target'."""
        ctx = _stock_ctx("META", fit=FIT_ON_TARGET, eq="OK", action="HOLD",
                         portfolio_current_pct=5.0)
        assert ctx is not None
        weight_ctx = ctx.get("portfolio_weight_context")
        assert weight_ctx is not None
        assert "5.0%" in weight_ctx
        assert "target" in weight_ctx.lower(), (
            f"ON_TARGET weight context should mention target: {weight_ctx!r}"
        )

    def test_overweight_with_current_pct_says_above_target(self):
        """OVERWEIGHT + current_pct=8.5 → portfolio_weight_context says 'above target'."""
        ctx = _stock_ctx("AAPL", fit=FIT_OVERWEIGHT, eq="OK", action="TRIM",
                         portfolio_current_pct=8.5)
        assert ctx is not None
        weight_ctx = ctx.get("portfolio_weight_context")
        assert weight_ctx is not None
        assert "8.5%" in weight_ctx
        assert "above target" in weight_ctx.lower(), (
            f"OVERWEIGHT weight context should say 'above target': {weight_ctx!r}"
        )

    def test_unknown_fit_with_current_pct_says_no_target_allocation(self):
        """UNKNOWN fit + current_pct=2.1 → says 'no target allocation is set', NOT 'room to add'."""
        ctx = _stock_ctx("NVDA", fit=FIT_UNKNOWN, eq="OK", action="HOLD",
                         portfolio_current_pct=2.1)
        assert ctx is not None
        weight_ctx = ctx.get("portfolio_weight_context")
        assert weight_ctx is not None
        assert "2.1%" in weight_ctx
        assert "no target allocation" in weight_ctx.lower(), (
            f"UNKNOWN fit should say 'no target allocation': {weight_ctx!r}"
        )
        assert "room to" not in weight_ctx.lower(), (
            f"UNKNOWN fit must not imply room to add: {weight_ctx!r}"
        )

    def test_no_current_pct_portfolio_weight_context_absent(self):
        """When portfolio_current_pct is not provided, portfolio_weight_context must be absent."""
        ctx = _stock_ctx("AAPL", fit=FIT_UNDERWEIGHT, eq="OK", action="BUY",
                         portfolio_current_pct=None)
        assert ctx is not None
        assert "portfolio_weight_context" not in ctx, (
            "portfolio_weight_context must not appear when no pct is available"
        )

    def test_portfolio_current_pct_through_snapshot_builder(self):
        """portfolio_current_pct in card_meta flows into portfolio_weight_context via snapshot."""
        card = _snap_card(
            "AAPL", "stock", action="BUY", fit="UNDERWEIGHT", eq="OK",
            extra_meta={"portfolio_current_pct": 3.2},
        )
        assert card["action"] == "BUY"
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        weight_ctx = ctx.get("portfolio_weight_context")
        assert weight_ctx is not None, "portfolio_weight_context must be present when pct in card_meta"
        assert "3.2%" in weight_ctx

    def test_portfolio_current_pct_none_in_card_meta_does_not_break_snapshot(self):
        card = _snap_card(
            "MSFT", "stock", action="HOLD", fit="UNKNOWN", eq="OK",
            extra_meta={"portfolio_current_pct": None},
        )
        assert card["action"] == "HOLD"
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert "portfolio_weight_context" not in ctx

    def test_etf_with_current_pct_shows_weight(self):
        """ETF portfolio_current_pct also flows into portfolio_weight_context."""
        ctx = _etf_ctx("VTI", fit=FIT_UNDERWEIGHT, eq="OK", action="BUY",
                       portfolio_current_pct=4.5)
        assert ctx is not None
        weight_ctx = ctx.get("portfolio_weight_context")
        assert weight_ctx is not None
        assert "4.5%" in weight_ctx
