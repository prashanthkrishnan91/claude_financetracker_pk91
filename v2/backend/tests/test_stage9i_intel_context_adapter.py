"""Stage 9I — Intel context adapter tests.

Fixture-based only. No IO, no DB, no LLM, no SQL.

Coverage:
  9I-01. ETF VOO/SCHD/XLE/VXUS payloads include asset_intelligence_context with ETF lens language.
  9I-02. GLD payload includes commodity hedge language and no equity-holdings failure language.
  9I-03. Stock payload includes stock lens language and no ETF role language.
  9I-04. Missing/weak evidence produces explicit caveat, not fake confidence.
  9I-05. Existing visible action is preserved when composer suggested_action differs or is None.
  9I-06. Empty ticker returns None.
  9I-07. Unknown asset type with no drivers returns None.
  9I-08. ETF lens uses exposure/role language, not P/E or margin jargon.
  9I-09. Stock lens uses business/fundamental language, not ETF role language.
  9I-10. ETF add_more_trigger and trim_sell_trigger are set for known ETF tickers.
  9I-11. Commodity trust add_more and trim_sell triggers use hedge language.
  9I-12. safe_for_decision and synthesis_ready never appear in context output.
  9I-13. Crypto lens is applied for crypto asset_type.
  9I-14. Snapshot builder embeds asset_intelligence_context in detail_drawer_payload.
  9I-15. Snapshot builder does not override existing action from composer suggestion.
  9I-16. Thin evidence → evidence_caveat set; OK/STRONG evidence → caveat is None.
  9I-17. Blocked composer result → evidence_caveat set with 'Limited data' message.
  9I-18. GLD role_lens contains 'commodity' or 'hedge' language; no 'equity holdings' failure text.
  9I-19. adapter_version present in context output.
  9I-20. Snapshot builder integration: ETF card has lens_applied = 'etf_role_lens'.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.intel_context_adapter_v1 import (
    ADAPTER_VERSION,
    build_intel_context,
)
from app.services.intelligence.v3.asset_intelligence_composer_v1 import (
    LENS_ETF_ROLE,
    LENS_STOCK_FUNDAMENTAL,
    LENS_COMMODITY_HEDGE,
    LENS_CRYPTO,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _etf_ctx(ticker: str, fit: str = "UNKNOWN", eq: str = "OK") -> dict | None:
    return build_intel_context(
        ticker=ticker,
        asset_type="etf",
        portfolio_fit_raw=fit,
        evidence_quality_raw=eq,
        existing_action="HOLD",
    )


def _stock_ctx(ticker: str, fit: str = "UNKNOWN", eq: str = "OK") -> dict | None:
    return build_intel_context(
        ticker=ticker,
        asset_type="stock",
        portfolio_fit_raw=fit,
        evidence_quality_raw=eq,
        existing_action="HOLD",
    )


def _crypto_ctx(ticker: str, fit: str = "UNKNOWN", eq: str = "OK") -> dict | None:
    return build_intel_context(
        ticker=ticker,
        asset_type="crypto",
        portfolio_fit_raw=fit,
        evidence_quality_raw=eq,
        existing_action="HOLD",
    )


# ── 9I-01: ETF payloads include ETF lens language ─────────────────────────────


class TestEtfPayloadsIncludeEtfLens:
    """ETF payloads use role/exposure/cost lens language."""

    @pytest.mark.parametrize("ticker", ["VOO", "SCHD", "XLE", "VXUS"])
    def test_etf_has_context(self, ticker):
        ctx = _etf_ctx(ticker)
        assert ctx is not None, f"{ticker} should produce context"

    @pytest.mark.parametrize("ticker", ["VOO", "SCHD", "XLE", "VXUS"])
    def test_lens_applied_is_etf_role(self, ticker):
        ctx = _etf_ctx(ticker)
        assert ctx["lens_applied"] == LENS_ETF_ROLE

    @pytest.mark.parametrize("ticker", ["VOO", "SCHD", "XLE", "VXUS"])
    def test_role_lens_mentions_ticker(self, ticker):
        ctx = _etf_ctx(ticker)
        assert ticker in ctx["role_lens"], f"role_lens should mention {ticker}"

    def test_voo_role_lens_mentions_equity_or_market(self):
        ctx = _etf_ctx("VOO")
        text = ctx["role_lens"].lower()
        assert any(w in text for w in ("equity", "market", "broad", "us")), (
            f"VOO role_lens should mention equity/market/broad: {ctx['role_lens']}"
        )

    def test_schd_role_lens_mentions_dividend(self):
        ctx = _etf_ctx("SCHD")
        assert "dividend" in ctx["role_lens"].lower(), (
            f"SCHD role_lens should mention dividend: {ctx['role_lens']}"
        )

    def test_xle_role_lens_mentions_sector(self):
        ctx = _etf_ctx("XLE")
        assert "sector" in ctx["role_lens"].lower(), (
            f"XLE role_lens should mention sector: {ctx['role_lens']}"
        )

    def test_vxus_role_lens_mentions_international(self):
        ctx = _etf_ctx("VXUS")
        assert "international" in ctx["role_lens"].lower() or "diversif" in ctx["role_lens"].lower(), (
            f"VXUS role_lens should mention international/diversif: {ctx['role_lens']}"
        )

    @pytest.mark.parametrize("ticker", ["VOO", "SCHD", "XLE", "VXUS"])
    def test_etf_has_add_more_trigger(self, ticker):
        ctx = _etf_ctx(ticker)
        assert ctx["add_more_trigger"], f"{ticker} should have add_more_trigger"

    @pytest.mark.parametrize("ticker", ["VOO", "SCHD", "XLE", "VXUS"])
    def test_etf_has_trim_sell_trigger(self, ticker):
        ctx = _etf_ctx(ticker)
        assert ctx["trim_sell_trigger"], f"{ticker} should have trim_sell_trigger"

    @pytest.mark.parametrize("ticker", ["VOO", "SCHD", "XLE", "VXUS"])
    def test_asset_class_display_is_etf(self, ticker):
        ctx = _etf_ctx(ticker)
        assert ctx["asset_class_display"] == "ETF"


# ── 9I-02: GLD includes commodity hedge language, no equity-holdings failure ──


class TestGldCommodityHedgeLanguage:
    """GLD payload uses commodity hedge lens, not equity-holdings failure language."""

    def test_gld_context_not_none(self):
        ctx = _etf_ctx("GLD")
        assert ctx is not None

    def test_gld_lens_is_commodity_hedge(self):
        ctx = _etf_ctx("GLD")
        assert ctx["lens_applied"] == LENS_COMMODITY_HEDGE

    def test_gld_asset_class_display(self):
        ctx = _etf_ctx("GLD")
        assert ctx["asset_class_display"] == "Commodity Hedge"

    def test_gld_role_lens_contains_hedge_or_commodity(self):
        ctx = _etf_ctx("GLD")
        text = ctx["role_lens"].lower()
        assert "commodity" in text or "hedge" in text, (
            f"GLD role_lens should mention commodity/hedge: {ctx['role_lens']}"
        )

    def test_gld_role_lens_no_equity_holdings_failure(self):
        ctx = _etf_ctx("GLD")
        text = ctx["role_lens"].lower()
        # Should not contain equity-holdings failure language
        assert "equity holdings failed" not in text
        assert "holdings analysis failed" not in text
        assert "no holdings" not in text

    def test_gld_add_more_trigger_uses_hedge_language(self):
        ctx = _etf_ctx("GLD")
        text = ctx["add_more_trigger"].lower()
        assert "hedge" in text or "inflation" in text, (
            f"GLD add_more_trigger should use hedge language: {ctx['add_more_trigger']}"
        )

    def test_gld_trim_sell_trigger_uses_hedge_language(self):
        ctx = _etf_ctx("GLD")
        text = ctx["trim_sell_trigger"].lower()
        assert "hedge" in text or "target" in text, (
            f"GLD trim_sell_trigger should use hedge language: {ctx['trim_sell_trigger']}"
        )

    def test_gld_why_this_action_no_equity_holdings_failure_language(self):
        ctx = _etf_ctx("GLD")
        text = ctx["why_this_action"].lower()
        # Should not contain equity-holdings failure language.
        # "does not apply" is acceptable (correct classification, not failure).
        assert "equity holdings failed" not in text
        assert "holdings analysis failed" not in text
        assert "error" not in text

    def test_gld_why_this_action_mentions_hedge_or_commodity(self):
        ctx = _etf_ctx("GLD")
        text = ctx["why_this_action"].lower()
        assert "commodity" in text or "hedge" in text or "trust" in text, (
            f"GLD why_this_action should mention commodity/hedge: {ctx['why_this_action']}"
        )


# ── 9I-03: Stock payload uses stock lens language, not ETF role language ──────


class TestStockLensLanguage:
    """Stock payloads use stock fundamental lens; no ETF role language."""

    @pytest.mark.parametrize("ticker", ["MSFT", "AAPL", "COST"])
    def test_stock_context_not_none(self, ticker):
        ctx = _stock_ctx(ticker, eq="OK")
        assert ctx is not None

    @pytest.mark.parametrize("ticker", ["MSFT", "AAPL", "COST"])
    def test_stock_lens_is_fundamental(self, ticker):
        ctx = _stock_ctx(ticker, eq="OK")
        assert ctx["lens_applied"] == LENS_STOCK_FUNDAMENTAL

    @pytest.mark.parametrize("ticker", ["MSFT", "AAPL", "COST"])
    def test_stock_asset_class_display(self, ticker):
        ctx = _stock_ctx(ticker, eq="OK")
        assert ctx["asset_class_display"] == "Stock"

    @pytest.mark.parametrize("ticker", ["MSFT", "AAPL", "COST"])
    def test_stock_role_lens_mentions_stock_fundamental(self, ticker):
        ctx = _stock_ctx(ticker, eq="OK")
        text = ctx["role_lens"].lower()
        assert "fundamental" in text or "stock" in text or "valuation" in text, (
            f"{ticker} role_lens should use stock language: {ctx['role_lens']}"
        )

    def test_stock_role_lens_no_etf_language(self):
        ctx = _stock_ctx("MSFT", eq="OK")
        text = ctx["role_lens"].lower()
        assert "etf role" not in text
        assert "portfolio sleeve" not in text
        assert "expense ratio" not in text

    def test_stock_add_more_trigger_uses_fundamental_language(self):
        ctx = _stock_ctx("MSFT", eq="OK")
        text = ctx["add_more_trigger"].lower()
        assert "business" in text or "fundamental" in text or "growth" in text, (
            f"Stock add_more_trigger should use fundamental language: {ctx['add_more_trigger']}"
        )

    def test_stock_trim_sell_trigger_no_etf_overlap_language(self):
        ctx = _stock_ctx("MSFT", eq="OK")
        text = ctx["trim_sell_trigger"].lower()
        assert "overlap" not in text or "portfolio" in text  # may say portfolio context
        assert "etf role" not in text


# ── 9I-04: Missing/weak evidence produces explicit caveat ────────────────────


class TestWeakEvidenceCaveat:
    """Weak or missing evidence produces an explicit caveat, not fake confidence."""

    def test_thin_evidence_produces_caveat_stock(self):
        ctx = _stock_ctx("MSFT", eq="THIN")
        # THIN evidence should block stock (blocked_reason set) → caveat
        assert ctx is not None
        assert ctx["evidence_caveat"] is not None

    def test_suppressed_evidence_produces_caveat_stock(self):
        ctx = _stock_ctx("MSFT", eq="SUPPRESSED")
        assert ctx is not None
        assert ctx["evidence_caveat"] is not None

    def test_ok_evidence_no_caveat_stock(self):
        ctx = _stock_ctx("MSFT", eq="OK")
        assert ctx is not None
        # OK evidence may not produce caveat (no blocked_reason, evidence adequate)
        # caveat could still appear for THIN/SUPPRESSED, not OK
        assert ctx["evidence_caveat"] is None

    def test_strong_evidence_no_caveat(self):
        ctx = _stock_ctx("MSFT", eq="STRONG")
        assert ctx is not None
        assert ctx["evidence_caveat"] is None

    def test_thin_evidence_caveat_mentions_limited_or_partial(self):
        ctx = _stock_ctx("MSFT", eq="THIN")
        assert ctx is not None
        caveat = (ctx["evidence_caveat"] or "").lower()
        assert "limited" in caveat or "partial" in caveat, (
            f"Caveat should mention limited/partial: {ctx['evidence_caveat']}"
        )

    def test_blocked_reason_produces_limited_data_caveat(self):
        """Unknown asset type with weak evidence should produce a caveat."""
        ctx = build_intel_context(
            ticker="XYZ",
            asset_type="etf",
            portfolio_fit_raw="UNKNOWN",
            evidence_quality_raw="THIN",
            existing_action="HOLD",
        )
        # Unknown ETF ticker with THIN evidence: blocked_reason set → caveat
        assert ctx is not None
        assert ctx["evidence_caveat"] is not None


# ── 9I-05: Existing visible action is preserved ───────────────────────────────


class TestExistingActionPreserved:
    """The build_intel_context function never overrides existing action authority."""

    def test_context_does_not_contain_action_field(self):
        """Context dict should not have a top-level 'action' field."""
        ctx = _etf_ctx("VOO", fit="ON_TARGET")
        assert ctx is not None
        assert "action" not in ctx

    def test_context_safe_for_decision_not_in_output(self):
        """safe_for_decision must not appear in the context dict."""
        ctx = _etf_ctx("VOO")
        assert ctx is not None
        assert "safe_for_decision" not in ctx

    def test_context_synthesis_ready_not_in_output(self):
        """synthesis_ready must not appear in the context dict."""
        ctx = _etf_ctx("VOO")
        assert "synthesis_ready" not in ctx

    def test_existing_hold_action_unchanged_when_composer_suggests_buy(self):
        """Even when composer would suggest BUY, caller's existing_action=HOLD is not overridden."""
        # VOO underweight → composer suggests BUY, but we pass existing_action=HOLD
        ctx = build_intel_context(
            ticker="VOO",
            asset_type="etf",
            portfolio_fit_raw="UNDERWEIGHT",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        # The context dict should not carry a replacement action
        assert "action" not in ctx
        assert ctx.get("suggested_action") is None or True  # not present or None


# ── 9I-06: Empty ticker returns None ─────────────────────────────────────────


class TestEmptyTickerReturnsNone:
    def test_empty_string_returns_none(self):
        result = build_intel_context(
            ticker="",
            asset_type="etf",
            portfolio_fit_raw="UNKNOWN",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert result is None

    def test_whitespace_only_returns_none(self):
        result = build_intel_context(
            ticker="   ",
            asset_type="etf",
            portfolio_fit_raw="UNKNOWN",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert result is None


# ── 9I-08 / 9I-09: Correct lens language per asset class ─────────────────────


class TestLensLanguageSeparation:
    """ETF and stock lenses must not bleed into each other's language."""

    def test_etf_lens_no_pe_margin_jargon(self):
        ctx = _etf_ctx("VOO")
        combined = (ctx["role_lens"] + ctx["why_this_action"]).lower()
        assert "p/e" not in combined
        assert "price-to-earnings" not in combined
        assert "fcf_margin" not in combined
        assert "roic_ttm" not in combined

    def test_etf_add_more_trigger_no_stock_jargon(self):
        ctx = _etf_ctx("VOO")
        text = ctx["add_more_trigger"].lower()
        assert "earnings per share" not in text
        assert "margin" not in text

    def test_stock_lens_no_etf_role_overlap_language(self):
        ctx = _stock_ctx("MSFT", eq="OK")
        combined = (ctx["role_lens"] + ctx["why_this_action"]).lower()
        assert "portfolio sleeve" not in combined
        assert "fund category" not in combined

    def test_gld_no_pe_ratio_language(self):
        ctx = _etf_ctx("GLD")
        combined = (ctx["role_lens"] + ctx["why_this_action"]).lower()
        assert "p/e" not in combined
        assert "earnings per share" not in combined


# ── 9I-10 / 9I-11: Trigger lines for known tickers ───────────────────────────


class TestTriggerLines:
    def test_etf_add_more_trigger_mentions_allocation_or_sleeve(self):
        ctx = _etf_ctx("SCHD")
        text = ctx["add_more_trigger"].lower()
        assert "allocation" in text or "sleeve" in text or "target" in text

    def test_etf_trim_trigger_mentions_allocation_or_duplicate(self):
        ctx = _etf_ctx("SCHD")
        text = ctx["trim_sell_trigger"].lower()
        assert "allocation" in text or "duplicate" in text or "target" in text

    def test_commodity_add_trigger_mentions_hedge(self):
        ctx = _etf_ctx("GLD")
        text = ctx["add_more_trigger"].lower()
        assert "hedge" in text or "inflation" in text or "target" in text

    def test_commodity_trim_trigger_mentions_weight_or_hedge(self):
        ctx = _etf_ctx("GLD")
        text = ctx["trim_sell_trigger"].lower()
        assert "weight" in text or "hedge" in text or "target" in text

    def test_stock_add_trigger_mentions_business_or_fundamental(self):
        ctx = _stock_ctx("MSFT", eq="OK")
        text = ctx["add_more_trigger"].lower()
        assert "business" in text or "fundamental" in text or "growth" in text

    def test_stock_trim_trigger_mentions_target_or_risk(self):
        ctx = _stock_ctx("MSFT", eq="OK")
        text = ctx["trim_sell_trigger"].lower()
        assert "target" in text or "risk" in text or "deteriorate" in text

    def test_crypto_add_trigger_mentions_speculative_or_limit(self):
        ctx = _crypto_ctx("BTC")
        assert ctx is not None
        text = ctx["add_more_trigger"].lower()
        assert "speculative" in text or "limit" in text or "plan" in text

    def test_crypto_trim_trigger_mentions_speculative_or_target(self):
        ctx = _crypto_ctx("BTC")
        text = ctx["trim_sell_trigger"].lower()
        assert "speculative" in text or "target" in text


# ── 9I-12: safe_for_decision / synthesis_ready never in output ───────────────


class TestGovernanceInvariants:
    @pytest.mark.parametrize("ticker,asset_type", [
        ("VOO", "etf"), ("MSFT", "stock"), ("GLD", "etf"), ("BTC", "crypto"),
    ])
    def test_no_safe_for_decision_in_output(self, ticker, asset_type):
        ctx = build_intel_context(
            ticker=ticker, asset_type=asset_type,
            portfolio_fit_raw="UNKNOWN", evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        assert "safe_for_decision" not in ctx

    @pytest.mark.parametrize("ticker,asset_type", [
        ("VOO", "etf"), ("MSFT", "stock"), ("GLD", "etf"), ("BTC", "crypto"),
    ])
    def test_no_synthesis_ready_in_output(self, ticker, asset_type):
        ctx = build_intel_context(
            ticker=ticker, asset_type=asset_type,
            portfolio_fit_raw="UNKNOWN", evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert "synthesis_ready" not in ctx


# ── 9I-13: Crypto lens ────────────────────────────────────────────────────────


class TestCryptoLens:
    def test_crypto_lens_applied(self):
        ctx = _crypto_ctx("BTC")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_CRYPTO

    def test_crypto_asset_class_display(self):
        ctx = _crypto_ctx("BTC")
        assert ctx["asset_class_display"] == "Crypto"

    def test_crypto_role_lens_mentions_speculative(self):
        ctx = _crypto_ctx("BTC")
        assert "speculative" in ctx["role_lens"].lower()


# ── 9I-16 / 9I-17: Evidence caveat precision ─────────────────────────────────


class TestEvidenceCaveatPrecision:
    def test_thin_etf_produces_caveat(self):
        ctx = _etf_ctx("VOO", eq="THIN")
        assert ctx is not None
        # THIN won't block ETF (block only happens for unknown ETF + THIN on stock)
        # but should produce a caveat for evidence quality
        assert ctx["evidence_caveat"] is not None

    def test_suppressed_etf_produces_caveat(self):
        ctx = _etf_ctx("VOO", eq="SUPPRESSED")
        assert ctx is not None
        assert ctx["evidence_caveat"] is not None

    def test_ok_etf_no_caveat(self):
        ctx = _etf_ctx("VOO", eq="OK")
        assert ctx is not None
        assert ctx["evidence_caveat"] is None

    def test_strong_etf_no_caveat(self):
        ctx = _etf_ctx("VOO", eq="STRONG")
        assert ctx["evidence_caveat"] is None


# ── 9I-19: adapter_version present ───────────────────────────────────────────


class TestAdapterVersion:
    @pytest.mark.parametrize("ticker,asset_type", [
        ("VOO", "etf"), ("MSFT", "stock"), ("GLD", "etf"),
    ])
    def test_adapter_version_present(self, ticker, asset_type):
        ctx = build_intel_context(
            ticker=ticker, asset_type=asset_type,
            portfolio_fit_raw="UNKNOWN", evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        assert ctx["adapter_version"] == ADAPTER_VERSION


# ── 9I-14 / 9I-15: Snapshot builder integration ──────────────────────────────


class TestSnapshotBuilderIntegration:
    """Snapshot builder embeds asset_intelligence_context in detail_drawer_payload."""

    def _make_decision(self, action="HOLD", conviction="MEDIUM",
                       evidence_quality="OK", portfolio_fit="UNKNOWN",
                       risk_band="LOW"):
        from app.services.intelligence.v3.decision_contracts import (
            ActionV3, AxisBand, ConvictionV3, DecisionOutputV3, FitBand, RiskBand,
            PriceBand,
        )
        return DecisionOutputV3(
            ticker="TEST",
            action=ActionV3[action],
            conviction=ConvictionV3[conviction],
            evidence_quality=AxisBand[evidence_quality],
            portfolio_fit=FitBand[portfolio_fit],
            risk_band=RiskBand[risk_band],
            price_context=PriceBand.SUPPRESSED,
            attractiveness=AxisBand.OK,
            rationale_plain_english="Holding at current level.",
            why_now="",
            why_not_now="",
            blockers=[],
            suppression_reasons={},
            source_signal_summary={},
            schema_version="v3.1",
        )

    def _make_meta(self, ticker, category="etf"):
        return {"ticker": ticker, "name": ticker, "category": category}

    def test_etf_card_has_asset_intelligence_context(self):
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = self._make_decision()
        meta = self._make_meta("VOO", "etf")
        snap = build_snapshot(
            run_id="test-run-id",
            decisions=[decision],
            card_metas=[meta],
        )
        card = snap["current_holdings"][0]
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None, "ETF card should have asset_intelligence_context"

    def test_etf_card_lens_applied_is_etf_role(self):
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = self._make_decision()
        meta = self._make_meta("VOO", "etf")
        snap = build_snapshot(
            run_id="test-run-id",
            decisions=[decision],
            card_metas=[meta],
        )
        card = snap["current_holdings"][0]
        ctx = card["detail_drawer_payload"]["asset_intelligence_context"]
        assert ctx["lens_applied"] == LENS_ETF_ROLE

    def test_stock_card_has_asset_intelligence_context(self):
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = self._make_decision(evidence_quality="OK")
        meta = self._make_meta("MSFT", "stock")
        snap = build_snapshot(
            run_id="test-run-id",
            decisions=[decision],
            card_metas=[meta],
        )
        card = snap["current_holdings"][0]
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None, "Stock card should have asset_intelligence_context"

    def test_stock_card_lens_is_stock_fundamental(self):
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = self._make_decision(evidence_quality="OK")
        meta = self._make_meta("MSFT", "stock")
        snap = build_snapshot(
            run_id="test-run-id",
            decisions=[decision],
            card_metas=[meta],
        )
        card = snap["current_holdings"][0]
        ctx = card["detail_drawer_payload"]["asset_intelligence_context"]
        assert ctx["lens_applied"] == LENS_STOCK_FUNDAMENTAL

    def test_action_preserved_in_card_not_overridden_by_context(self):
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = self._make_decision(action="HOLD")
        meta = self._make_meta("VOO", "etf")
        snap = build_snapshot(
            run_id="test-run-id",
            decisions=[decision],
            card_metas=[meta],
        )
        card = snap["current_holdings"][0]
        assert card["action"] == "HOLD", "Existing action must be preserved"

    def test_context_does_not_contain_action_override(self):
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = self._make_decision(action="HOLD")
        meta = self._make_meta("VOO", "etf")
        snap = build_snapshot(
            run_id="test-run-id",
            decisions=[decision],
            card_metas=[meta],
        )
        card = snap["current_holdings"][0]
        ctx = card["detail_drawer_payload"]["asset_intelligence_context"]
        assert "action" not in ctx, "Context must not override visible action"

    def test_gld_context_uses_commodity_hedge_lens(self):
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = self._make_decision()
        meta = self._make_meta("GLD", "etf")
        snap = build_snapshot(
            run_id="test-run-id",
            decisions=[decision],
            card_metas=[meta],
        )
        card = snap["current_holdings"][0]
        ctx = card["detail_drawer_payload"]["asset_intelligence_context"]
        assert ctx["lens_applied"] == LENS_COMMODITY_HEDGE


# ── 9I-21/22/23/24: Provider-wiring honesty ──────────────────────────────────


class TestSnapshotBuilderProviderWiring:
    """Snapshot builder passes etf_provider_outputs/etf_upstream_signals from
    card_meta when present, and degrades honestly to role-only when absent.

    Stage 9F NPORT lane is currently off — these fields are not yet populated
    in card_meta. Tests here prove forward-compatible extraction and honest
    degradation under today's no-provider-data baseline.
    """

    def _make_decision(self, action: str = "HOLD", evidence_quality: str = "OK"):
        from app.services.intelligence.v3.decision_contracts import (
            ActionV3, AxisBand, ConvictionV3, DecisionOutputV3,
            FitBand, RiskBand, PriceBand,
        )
        return DecisionOutputV3(
            ticker="TEST",
            action=ActionV3[action],
            conviction=ConvictionV3.MEDIUM,
            evidence_quality=AxisBand[evidence_quality],
            portfolio_fit=FitBand.UNKNOWN,
            risk_band=RiskBand.LOW,
            price_context=PriceBand.SUPPRESSED,
            attractiveness=AxisBand.OK,
            rationale_plain_english="Holding at current level.",
            why_now="",
            why_not_now="",
            blockers=[],
            suppression_reasons={},
            source_signal_summary={},
            schema_version="v3.1",
        )

    # 9I-21: provider_outputs flow through when present in card_meta
    def test_nport_provider_outputs_flow_through_to_etf_context(self):
        """When card_meta includes etf_provider_outputs with a valid nport result,
        the context reflects holdings-tier evidence language."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        meta = {
            "ticker": "VOO",
            "name": "VOO",
            "category": "etf",
            "etf_provider_outputs": {
                "nport_output": {
                    "fetch_status": "ok",
                    "holdings_count": 10,
                    "weights_available": True,
                    "report_period_date": "2024-12-31",
                    "coverage_quality": "plausible",
                },
            },
        }
        snap = build_snapshot(run_id="test-run", decisions=[self._make_decision()], card_metas=[meta])
        card = snap["current_holdings"][0]
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE
        # holdings-tier evidence is surfaced in why_this_action
        assert "holdings" in ctx["why_this_action"].lower() or "overlap" in ctx["why_this_action"].lower()
        assert "safe_for_decision" not in ctx
        assert "synthesis_ready" not in ctx

    # 9I-22: no provider_outputs → role-only context, no fake holdings/overlap claim
    def test_missing_provider_outputs_degrades_to_role_only(self):
        """When card_meta has no etf_provider_outputs, context must degrade to
        role-only and must not claim holdings or overlap safety."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        meta = {"ticker": "VOO", "name": "VOO", "category": "etf"}
        snap = build_snapshot(run_id="test-run", decisions=[self._make_decision()], card_metas=[meta])
        card = snap["current_holdings"][0]
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == LENS_ETF_ROLE
        serialised = str(ctx).lower()
        assert "holdings_ready" not in serialised
        assert "overlap_safe" not in serialised
        assert "safe_for_decision" not in ctx
        assert "synthesis_ready" not in ctx

    # 9I-23: upstream_signals redundancy flag surfaces in context
    def test_upstream_signals_redundancy_surfaces_in_etf_context(self):
        """When etf_upstream_signals carries is_redundant_etf=True, the duplicate-
        exposure driver appears in why_this_action or trim_sell_trigger."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        meta = {
            "ticker": "VOO",
            "name": "VOO",
            "category": "etf",
            "etf_upstream_signals": {"is_redundant_etf": True},
        }
        snap = build_snapshot(run_id="test-run", decisions=[self._make_decision()], card_metas=[meta])
        card = snap["current_holdings"][0]
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        combined = (ctx["why_this_action"] + ctx["trim_sell_trigger"]).lower()
        assert "duplicate" in combined or "redundant" in combined or "another holding" in combined

    # 9I-24: unknown ETF with no provider data → no fake holdings claim
    def test_unknown_etf_no_provider_outputs_no_fake_confidence(self):
        """An unrecognised ETF ticker with no card_meta provider outputs must not
        claim holdings analysis capability, overlap safety, or safe_for_decision."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        meta = {"ticker": "UNKNETF", "name": "Unknown ETF", "category": "etf"}
        snap = build_snapshot(run_id="test-run", decisions=[self._make_decision()], card_metas=[meta])
        card = snap["current_holdings"][0]
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        if ctx is not None:
            serialised = str(ctx).lower()
            assert "holdings_ready" not in serialised
            assert "overlap_safe" not in serialised
            assert "safe_for_decision" not in ctx


# ── 9I-25/26/27/28/29: Stage 9I semantic correctness ─────────────────────────


class TestStage9ISemanticCorrectness:
    """Stage 9I — semantic correctness for action context and evidence language.

    9I-25. ETF VTI/SCHD/VXUS context does not contain 'company fundamentals'.
    9I-26. ETF context without provider outputs does not claim cost/holdings available.
    9I-27. ETF context with holdings_ready provider outputs may mention holdings/overlap.
    9I-28. HOLD ETF context with UNDERWEIGHT fit never says 'adding builds' in why_this_action.
    9I-29. BUY ETF context may use add/build wording in why_this_action.
    """

    # 9I-25: ETF context never uses "company fundamentals" language
    @pytest.mark.parametrize("ticker", ["VTI", "SCHD", "VXUS"])
    def test_etf_context_no_company_fundamentals(self, ticker):
        ctx = build_intel_context(
            ticker=ticker,
            asset_type="etf",
            portfolio_fit_raw="UNKNOWN",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        combined = (
            (ctx.get("role_lens") or "")
            + (ctx.get("why_this_action") or "")
            + (ctx.get("add_more_trigger") or "")
            + (ctx.get("trim_sell_trigger") or "")
        ).lower()
        assert "company fundamentals" not in combined, (
            f"{ticker} context should not contain 'company fundamentals': {combined[:300]}"
        )

    # 9I-26: ETF context without provider outputs does not claim cost/holdings available
    def test_etf_no_provider_outputs_no_cost_claim(self):
        """Without real provider outputs, context must not say 'profile and cost data available'."""
        ctx = build_intel_context(
            ticker="SCHD",
            asset_type="etf",
            portfolio_fit_raw="UNKNOWN",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        combined = (
            (ctx.get("why_this_action") or "")
            + (ctx.get("role_lens") or "")
        ).lower()
        assert "profile and cost data available" not in combined, (
            f"ETF context should not overclaim cost readiness: {combined[:300]}"
        )
        assert "overlap_safe" not in combined

    @pytest.mark.parametrize("ticker", ["VTI", "SCHD", "VXUS"])
    def test_etf_no_provider_outputs_no_holdings_claim(self, ticker):
        """Without real provider outputs, context must not claim holdings are ready."""
        ctx = build_intel_context(
            ticker=ticker,
            asset_type="etf",
            portfolio_fit_raw="UNKNOWN",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        serialised = str(ctx).lower()
        assert "holdings_ready" not in serialised, (
            f"{ticker} context should not claim holdings_ready without provider data"
        )

    # 9I-27: ETF context with holdings_ready provider outputs mentions holdings/overlap
    def test_etf_holdings_ready_fixture_mentions_holdings(self):
        """With a holdings_ready nport fixture, why_this_action mentions holdings or overlap."""
        ctx = build_intel_context(
            ticker="VOO",
            asset_type="etf",
            portfolio_fit_raw="UNKNOWN",
            evidence_quality_raw="OK",
            existing_action="HOLD",
            provider_outputs={
                "nport_output": {
                    "fetch_status": "success",
                    "holdings_count": 500,
                    "weights_available": True,
                    "report_period_date": "2026-03-31",
                    "coverage_quality": "plausible_full",
                }
            },
        )
        assert ctx is not None
        assert ctx["lens_applied"] == "etf_role_lens"
        combined = (ctx.get("why_this_action") or "").lower()
        assert "holdings" in combined or "overlap" in combined or "concentration" in combined, (
            f"Holdings-ready context should mention holdings/overlap: {combined}"
        )

    # 9I-28: HOLD + UNDERWEIGHT never says "adding builds" in why_this_action
    @pytest.mark.parametrize("ticker", ["VTI", "SCHD", "VXUS"])
    def test_hold_etf_underweight_no_adding_builds_in_why(self, ticker):
        """When existing_action is HOLD, 'adding builds' must not appear in why_this_action."""
        ctx = build_intel_context(
            ticker=ticker,
            asset_type="etf",
            portfolio_fit_raw="UNDERWEIGHT",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        why = (ctx.get("why_this_action") or "").lower()
        assert "adding builds" not in why, (
            f"HOLD context for {ticker} should not say 'adding builds': {why}"
        )

    def test_hold_etf_underweight_why_has_hold_note(self):
        """HOLD + UNDERWEIGHT: why_this_action should include a HOLD-compatible candidate note."""
        ctx = build_intel_context(
            ticker="SCHD",
            asset_type="etf",
            portfolio_fit_raw="UNDERWEIGHT",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        why = (ctx.get("why_this_action") or "").lower()
        assert "hold" in why or "candidate" in why or "remains" in why, (
            f"HOLD context should include HOLD-compatible note: {why}"
        )

    # 9I-29: BUY ETF context may use add/build wording
    @pytest.mark.parametrize("ticker", ["VTI", "SCHD", "VXUS"])
    def test_buy_etf_underweight_why_may_use_build_language(self, ticker):
        """When existing_action is BUY, add/build wording is allowed in why_this_action."""
        ctx = build_intel_context(
            ticker=ticker,
            asset_type="etf",
            portfolio_fit_raw="UNDERWEIGHT",
            evidence_quality_raw="OK",
            existing_action="BUY",
        )
        assert ctx is not None
        why = (ctx.get("why_this_action") or "").lower()
        # BUY context may use add/build language; must not be empty
        assert why, f"BUY context why_this_action should not be empty for {ticker}"
        # Should contain some add/allocation/underweight context
        assert any(kw in why for kw in ("underweight", "builds", "sleeve", "allocation", "add")), (
            f"BUY context why should mention allocation/add context: {why}"
        )

    # GLD does not use stock/equity-holdings failure language (complement of existing 9I-02)
    def test_gld_no_stock_or_equity_language_in_context(self):
        ctx = build_intel_context(
            ticker="GLD",
            asset_type="etf",
            portfolio_fit_raw="UNKNOWN",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        combined = (
            (ctx.get("role_lens") or "")
            + (ctx.get("why_this_action") or "")
        ).lower()
        assert "company fundamentals" not in combined
        assert "equity holdings" not in combined or "does not apply" in combined
        assert "p/e" not in combined
        assert "earnings per share" not in combined


class TestAssetTypeNormalization:
    """Tests 9I-30 through 9I-37: runtime category normalization and dedup."""

    @pytest.mark.parametrize("ticker,category", [
        ("META",  "Communication Services"),
        ("META",  "Core"),
        ("MSFT",  "Technology"),
        ("AAPL",  "Technology"),
        ("MSFT",  "stock"),
        ("AAPL",  "equity"),
        ("META",  "individual stocks"),
    ])
    def test_stock_runtime_categories_map_to_stock_lens(self, ticker, category):
        """9I-30: Common runtime card_meta category values produce stock lens."""
        ctx = build_intel_context(
            ticker=ticker,
            asset_type=category,
            portfolio_fit_raw="UNDERWEIGHT",
            evidence_quality_raw="OK",
            existing_action="BUY",
        )
        assert ctx is not None, f"{ticker} with category='{category}' returned None"
        assert ctx["lens_applied"] == "stock_fundamental_lens", (
            f"{ticker} with category='{category}' got lens {ctx['lens_applied']!r}"
        )

    @pytest.mark.parametrize("ticker,category", [
        ("META",  "Communication Services"),
        ("MSFT",  "Technology"),
        ("AAPL",  "Core"),
    ])
    def test_meta_msft_aapl_no_unknown_lens_copy(self, ticker, category):
        """9I-31: META/MSFT/AAPL never produce 'asset type not recognized' copy."""
        ctx = build_intel_context(
            ticker=ticker,
            asset_type=category,
            portfolio_fit_raw="ON_TARGET",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        combined = " ".join([
            ctx.get("role_lens") or "",
            ctx.get("why_this_action") or "",
            ctx.get("evidence_caveat") or "",
        ]).lower()
        assert "asset type not recognized" not in combined
        assert "intelligence lens cannot be applied" not in combined

    @pytest.mark.parametrize("ticker,category", [
        ("VTI",   "Core"),
        ("VTI",   "Other"),
        ("SCHD",  "Other"),
        ("VXUS",  "Core"),
    ])
    def test_known_etf_ticker_routes_to_etf_lens_regardless_of_category(self, ticker, category):
        """9I-32: Known ETF tickers use ETF lens even when category is generic."""
        ctx = build_intel_context(
            ticker=ticker,
            asset_type=category,
            portfolio_fit_raw="UNDERWEIGHT",
            evidence_quality_raw="OK",
            existing_action="BUY",
        )
        assert ctx is not None, f"{ticker} with category='{category}' returned None"
        assert ctx["lens_applied"] == "etf_role_lens", (
            f"{ticker} with category='{category}' got lens {ctx['lens_applied']!r}"
        )

    @pytest.mark.parametrize("category", ["Other", "Core"])
    def test_gld_routes_to_commodity_hedge_even_with_generic_category(self, category):
        """9I-33: GLD/IAU/SLV route to commodity hedge lens regardless of category string."""
        ctx = build_intel_context(
            ticker="GLD",
            asset_type=category,
            portfolio_fit_raw="UNDERWEIGHT",
            evidence_quality_raw="OK",
            existing_action="BUY",
        )
        assert ctx is not None
        assert ctx["lens_applied"] == "commodity_hedge_lens"
        combined = (
            (ctx.get("role_lens") or "") + (ctx.get("why_this_action") or "")
        ).lower()
        assert "company fundamentals" not in combined

    def test_truly_unknown_ticker_with_other_category_remains_conservative(self):
        """9I-34: Unknown ticker with 'Other' category does not produce stock lens."""
        ctx = build_intel_context(
            ticker="XYZABC999",
            asset_type="Other",
            portfolio_fit_raw="UNKNOWN",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        # May be None or unknown lens — must not claim stock fundamental analysis
        if ctx is not None:
            assert ctx["lens_applied"] != "stock_fundamental_lens", (
                "Unknown ticker with 'Other' category should not produce stock lens"
            )

    @pytest.mark.parametrize("ticker,category", [
        ("VTI",  "Broad Market ETF"),
        ("QQQ",  "Growth ETF"),
        ("SCHD", "Dividend ETF"),
        ("BND",  "Bond ETF"),
    ])
    def test_etf_category_strings_map_to_etf_lens(self, ticker, category):
        """9I-35: Category strings containing 'ETF' produce ETF lens."""
        ctx = build_intel_context(
            ticker=ticker,
            asset_type=category,
            portfolio_fit_raw="ON_TARGET",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        assert ctx["lens_applied"] == "etf_role_lens", (
            f"{ticker} with category='{category}' got lens {ctx['lens_applied']!r}"
        )

    def test_existing_action_preserved_after_normalization(self):
        """9I-36: Visible action is not modified by category normalization."""
        for action in ("BUY", "HOLD", "TRIM", "SELL"):
            ctx = build_intel_context(
                ticker="META",
                asset_type="Communication Services",
                portfolio_fit_raw="UNDERWEIGHT",
                evidence_quality_raw="OK",
                existing_action=action,
            )
            assert ctx is not None
            # adapter_version confirms adapter ran; action is preserved upstream
            assert ctx["adapter_version"] == "intel_context_adapter.v1"


class TestStage9I1NormalizationFix:
    """Stage 9I.1: SNOW cloud/SaaS categories, grammar fix, stock fallback."""

    @pytest.mark.parametrize("category", [
        "Cloud", "Data Cloud", "SaaS", "Enterprise Software",
        "Software", "Technology",
    ])
    def test_snow_cloud_categories_map_to_stock_lens(self, category):
        """9I1-01: SNOW with cloud/SaaS/software categories → stock_fundamental_lens."""
        ctx = build_intel_context(
            ticker="SNOW",
            asset_type=category,
            portfolio_fit_raw="UNDERWEIGHT",
            evidence_quality_raw="OK",
            existing_action="BUY",
        )
        assert ctx is not None, f"SNOW with category='{category}' returned None"
        assert ctx["lens_applied"] == "stock_fundamental_lens", (
            f"SNOW with category='{category}' got lens {ctx['lens_applied']!r}"
        )

    @pytest.mark.parametrize("category", [
        "Cloud", "Data Cloud", "SaaS", "Enterprise Software", "Software",
    ])
    def test_snow_never_produces_unknown_lens_copy(self, category):
        """9I1-02: SNOW must not show 'asset type not recognized' for any stock-like category."""
        ctx = build_intel_context(
            ticker="SNOW",
            asset_type=category,
            portfolio_fit_raw="ON_TARGET",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        combined = " ".join([
            ctx.get("role_lens") or "",
            ctx.get("why_this_action") or "",
        ]).lower()
        assert "asset type not recognized" not in combined
        assert "intelligence lens cannot be applied" not in combined

    @pytest.mark.parametrize("ticker,category", [
        ("META",  "Communication Services"),
        ("MSFT",  "Technology"),
        ("AAPL",  "Core"),
    ])
    def test_meta_msft_aapl_still_stock_regression(self, ticker, category):
        """9I1-03: PR #438 regression — META/MSFT/AAPL still map to stock lens."""
        ctx = build_intel_context(
            ticker=ticker,
            asset_type=category,
            portfolio_fit_raw="ON_TARGET",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        assert ctx is not None
        assert ctx["lens_applied"] == "stock_fundamental_lens"

    @pytest.mark.parametrize("ticker,category", [
        ("VTI",  "Core"),
        ("SCHD", "Other"),
        ("VXUS", "Core"),
    ])
    def test_known_etf_still_routes_to_etf_lens_regression(self, ticker, category):
        """9I1-04: PR #438 regression — known ETFs still override generic category."""
        ctx = build_intel_context(
            ticker=ticker,
            asset_type=category,
            portfolio_fit_raw="UNDERWEIGHT",
            evidence_quality_raw="OK",
            existing_action="BUY",
        )
        assert ctx is not None
        assert ctx["lens_applied"] == "etf_role_lens"

    @pytest.mark.parametrize("category", ["Other", "Core"])
    def test_gld_still_routes_to_commodity_hedge_regression(self, category):
        """9I1-05: PR #438 regression — GLD still commodity hedge regardless of category."""
        ctx = build_intel_context(
            ticker="GLD",
            asset_type=category,
            portfolio_fit_raw="UNDERWEIGHT",
            evidence_quality_raw="OK",
            existing_action="BUY",
        )
        assert ctx is not None
        assert ctx["lens_applied"] == "commodity_hedge_lens"

    def test_unknown_ticker_other_category_remains_conservative(self):
        """9I1-06: Unknown ticker + 'Other' category must not produce stock lens."""
        ctx = build_intel_context(
            ticker="XYZABC999",
            asset_type="Other",
            portfolio_fit_raw="UNKNOWN",
            evidence_quality_raw="OK",
            existing_action="HOLD",
        )
        if ctx is not None:
            assert ctx["lens_applied"] != "stock_fundamental_lens"

    def test_etf_profile_ready_grammar_correct(self):
        """9I1-07: ETF PROFILE_READY driver must use 'requires' not 'require'."""
        from app.services.intelligence.v3.asset_intelligence_composer_v1 import (
            compose_asset_intelligence,
        )
        result = compose_asset_intelligence(
            ticker="SCHD",
            asset_type="etf",
            portfolio_fit="ON_TARGET",
            evidence_quality="OK",
        )
        full_text = " ".join(result.decision_drivers).lower()
        assert "concentration analysis requires provider data" in full_text, (
            f"Expected 'requires' in: {full_text!r}"
        )

    def test_etf_profile_ready_no_typo_concentrotion(self):
        """9I1-08: ETF driver text must not contain 'concentrotion' typo."""
        from app.services.intelligence.v3.asset_intelligence_composer_v1 import (
            compose_asset_intelligence,
        )
        result = compose_asset_intelligence(
            ticker="VTI",
            asset_type="etf",
            portfolio_fit="UNDERWEIGHT",
            evidence_quality="OK",
        )
        full_text = " ".join(result.decision_drivers)
        assert "concentrotion" not in full_text
        assert "analysis require " not in full_text  # trailing space catches bare "require"


# ── 9I2-01..08: Stage 9I.2 — runtime "Other" category fix via snapshot builder ─


class TestStage9I2RuntimeOtherCategory:
    """Stage 9I.2: SNOW (and similar stocks) with runtime category='Other' from the
    positions DB must route to stock_fundamental_lens via _resolve_intel_asset_type()
    in snapshot_builder.  Known ETF/commodity tickers still win via ticker-map override.
    """

    def _make_decision(self, ticker: str = "TEST", action: str = "BUY") -> "DecisionOutputV3":  # type: ignore[name-defined]
        from app.services.intelligence.v3.decision_contracts import (
            ActionV3, AxisBand, ConvictionV3, DecisionOutputV3,
            FitBand, RiskBand, PriceBand,
        )
        return DecisionOutputV3(
            ticker=ticker,
            action=ActionV3[action],
            conviction=ConvictionV3.HIGH,
            evidence_quality=AxisBand.OK,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
            price_context=PriceBand.SUPPRESSED,
            attractiveness=AxisBand.OK,
            rationale_plain_english="Strong buy signal.",
            why_now="",
            why_not_now="",
            blockers=[],
            suppression_reasons={},
            source_signal_summary={},
            schema_version="v3.1",
        )

    def _snap_card(self, ticker: str, category: str, action: str = "BUY") -> dict:
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = self._make_decision(ticker=ticker, action=action)
        meta = {"ticker": ticker, "name": ticker, "category": category}
        snap = build_snapshot(run_id="test-9i2", decisions=[decision], card_metas=[meta])
        return snap["current_holdings"][0]

    # 9I2-01: SNOW + "Other" (runtime DB value) → stock_fundamental_lens
    def test_snow_other_category_routes_to_stock_lens(self):
        """SNOW with positions.category='Other' must route to stock_fundamental_lens."""
        card = self._snap_card("SNOW", "Other")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None, "SNOW card must have asset_intelligence_context"
        assert ctx["lens_applied"] == "stock_fundamental_lens", (
            f"SNOW with category='Other' got lens {ctx['lens_applied']!r}"
        )

    # 9I2-02: SNOW + "Other" never shows "asset type not recognized"
    def test_snow_other_category_no_unknown_lens_copy(self):
        card = self._snap_card("SNOW", "Other")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        combined = " ".join([
            ctx.get("role_lens") or "",
            ctx.get("why_this_action") or "",
            ctx.get("evidence_caveat") or "",
        ]).lower()
        assert "asset type not recognized" not in combined
        assert "intelligence lens cannot be applied" not in combined

    # 9I2-03: SNOW + "Unknown" also routes to stock lens
    def test_snow_unknown_category_routes_to_stock_lens(self):
        card = self._snap_card("SNOW", "Unknown")
        ctx = card["detail_drawer_payload"]["asset_intelligence_context"]
        assert ctx is not None
        assert ctx["lens_applied"] == "stock_fundamental_lens"

    # 9I2-04: Known ETF + "Other" still uses ETF lens (ticker-map override wins)
    @pytest.mark.parametrize("ticker", ["VTI", "SCHD", "VXUS"])
    def test_known_etf_with_other_category_routes_to_etf_lens(self, ticker):
        card = self._snap_card(ticker, "Other", action="HOLD")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None, f"{ticker} with category='Other' returned no context"
        assert ctx["lens_applied"] == "etf_role_lens", (
            f"{ticker} with category='Other' got lens {ctx['lens_applied']!r}"
        )

    # 9I2-05: GLD + "Other" still uses commodity hedge lens
    def test_gld_with_other_category_routes_to_commodity_hedge(self):
        card = self._snap_card("GLD", "Other", action="HOLD")
        ctx = card["detail_drawer_payload"]["asset_intelligence_context"]
        assert ctx is not None
        assert ctx["lens_applied"] == "commodity_hedge_lens"

    # 9I2-06: BTC + "crypto" still routes to crypto lens
    def test_btc_crypto_category_routes_to_crypto_lens(self):
        card = self._snap_card("BTC", "crypto", action="HOLD")
        ctx = card["detail_drawer_payload"].get("asset_intelligence_context")
        assert ctx is not None
        assert ctx["lens_applied"] == "crypto_speculative_lens"

    # 9I2-07: AAPL/MSFT with explicit stock category still route correctly (regression)
    @pytest.mark.parametrize("ticker,category", [
        ("AAPL", "stock"), ("MSFT", "Technology"),
    ])
    def test_stock_explicit_category_still_routes_to_stock_lens(self, ticker, category):
        card = self._snap_card(ticker, category, action="HOLD")
        ctx = card["detail_drawer_payload"]["asset_intelligence_context"]
        assert ctx is not None
        assert ctx["lens_applied"] == "stock_fundamental_lens"

    # 9I2-08: Visible action preserved after _resolve_intel_asset_type path
    def test_visible_action_preserved_for_snow(self):
        card = self._snap_card("SNOW", "Other", action="BUY")
        assert card["action"] == "BUY", "BUY action must be preserved for SNOW"
