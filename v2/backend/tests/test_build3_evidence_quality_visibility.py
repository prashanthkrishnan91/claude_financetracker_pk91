"""Build 3 PR 1 — Trust-the-band: evidence quality visibility tests.

Acceptance criteria:
1. HIGH conviction + THIN evidence → evidence_band THIN (not STRONG).
2. MEDIUM conviction + STRONG evidence → evidence_band STRONG (evidence-driven).
3. STRONG evidence + HIGH conviction BUY → evidence_band STRONG, conviction HIGH.
4. OK evidence + HIGH conviction BUY → conviction capped MEDIUM by policy (visible path).
5. Shadow-only guardrail no longer differs from visible path for the capped case.
6. STUB is not in production speculative allowlists.
7. Buy/Hold/Trim/Sell action contract remains deterministic.
8. Snapshot output is plain-English — evidence_text does not claim multiple
   independent signals unless evidence_band is actually STRONG.

Pure function tests — no IO, DB, LLM, or provider calls.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.decision_contracts import (
    ActionV3,
    AxisBand,
    ConvictionV3,
    DecisionInputV3,
    DecisionOutputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from app.services.intelligence.v3.decision_policy_v1 import decide
from app.services.intelligence.v3.existing_signal_adapter import (
    _SPECULATIVE_TICKERS as _ADAPTER_SPECULATIVE_TICKERS,
    build_decision_input_from_card,
)
from app.services.intelligence.v3.portfolio_governor_lite import (
    _SPECULATIVE_TICKERS as _GOVERNOR_SPECULATIVE_TICKERS,
)

# Expose single name for backward compat with older tests in this module.
_SPECULATIVE_TICKERS = _ADAPTER_SPECULATIVE_TICKERS
from app.services.intelligence.v3.shadow_projection import project_shadow_from_card_signals
from app.services.intelligence.v3.snapshot_builder import build_snapshot


# ── Helpers ──────────────────────────────────────────────────────────────────

def _decision(
    ticker: str,
    action: ActionV3,
    conviction: ConvictionV3,
    evidence_quality: AxisBand,
) -> DecisionOutputV3:
    return DecisionOutputV3(
        ticker=ticker,
        action=action,
        conviction=conviction,
        evidence_quality=evidence_quality,
        attractiveness=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.LOW,
        blockers=[],
        suppression_reasons={},
        rationale_plain_english=f"{ticker}: signals support this position.",
        why_now="Evidence and fit support acting now.",
        why_not_now="Watch for evidence weakening.",
        source_signal_summary={},
        schema_version="v3.1",
    )


def _meta(ticker: str) -> dict:
    return {"ticker": ticker, "name": ticker, "category": "stock"}


def _snap(decisions, metas=None):
    if metas is None:
        metas = [_meta(d.ticker) for d in decisions]
    return build_snapshot(run_id="b3-test-run", decisions=decisions, card_metas=metas)


# ── 1. Evidence band comes from evidence_quality, not conviction ──────────────

class TestEvidenceBandFromQuality:
    """evidence_band in the snapshot must reflect the real evidence_quality axis."""

    def test_high_conviction_thin_evidence_renders_thin(self):
        """Acceptance criterion 1: HIGH conviction + THIN evidence → evidence_band THIN."""
        dec = _decision("AAPL", ActionV3.BUY, ConvictionV3.HIGH, AxisBand.THIN)
        snap = _snap([dec])
        card = snap["current_holdings"][0]
        assert card["evidence_band"] == "THIN", (
            f"HIGH conviction with THIN evidence must show THIN band, got {card['evidence_band']}"
        )

    def test_medium_conviction_strong_evidence_renders_strong(self):
        """Acceptance criterion 2: MEDIUM conviction + STRONG evidence → evidence_band STRONG."""
        dec = _decision("MSFT", ActionV3.BUY, ConvictionV3.MEDIUM, AxisBand.STRONG)
        snap = _snap([dec])
        card = snap["current_holdings"][0]
        assert card["evidence_band"] == "STRONG", (
            f"STRONG evidence must show STRONG band regardless of conviction, "
            f"got {card['evidence_band']}"
        )

    def test_low_conviction_strong_evidence_renders_strong(self):
        """LOW conviction does not downgrade STRONG evidence band."""
        dec = _decision("NVDA", ActionV3.HOLD, ConvictionV3.LOW, AxisBand.STRONG)
        snap = _snap([dec])
        card = snap["current_holdings"][0]
        assert card["evidence_band"] == "STRONG"

    def test_ok_evidence_renders_partial(self):
        """AxisBand.OK maps to PARTIAL evidence_band, independent of conviction."""
        for conviction in [ConvictionV3.HIGH, ConvictionV3.MEDIUM, ConvictionV3.LOW]:
            dec = _decision("TEST", ActionV3.HOLD, conviction, AxisBand.OK)
            snap = _snap([dec])
            card = snap["current_holdings"][0]
            assert card["evidence_band"] == "PARTIAL", (
                f"OK evidence with {conviction} conviction must show PARTIAL, "
                f"got {card['evidence_band']}"
            )

    def test_suppressed_evidence_renders_thin(self):
        """SUPPRESSED evidence collapses to THIN band."""
        dec = _decision("UNK", ActionV3.HOLD, ConvictionV3.LOW, AxisBand.SUPPRESSED)
        snap = _snap([dec])
        card = snap["current_holdings"][0]
        assert card["evidence_band"] == "THIN"

    def test_evidence_band_independent_of_action(self):
        """Evidence band is evidence-driven even for TRIM/SELL cards."""
        trim_dec = _decision("META", ActionV3.TRIM, ConvictionV3.MEDIUM, AxisBand.STRONG)
        sell_dec = _decision("COIN", ActionV3.SELL, ConvictionV3.LOW, AxisBand.THIN)
        snap = _snap([trim_dec, sell_dec])
        cards = {c["ticker"]: c for c in snap["current_holdings"]}
        assert cards["META"]["evidence_band"] == "STRONG"
        assert cards["COIN"]["evidence_band"] == "THIN"


# ── 2. evidence_text does not over-claim for non-STRONG evidence ──────────────

class TestEvidenceText:
    """evidence_text must not claim 'multiple independent signals' unless STRONG."""

    _STRONG_TEXT = "Multiple independent signals confirm this view."

    def test_strong_evidence_claims_multiple_signals(self):
        """Only STRONG evidence band renders the 'multiple independent signals' text."""
        dec = _decision("AAPL", ActionV3.BUY, ConvictionV3.MEDIUM, AxisBand.STRONG)
        snap = _snap([dec])
        card = snap["current_holdings"][0]
        assert card["evidence_text"] == self._STRONG_TEXT

    def test_thin_evidence_does_not_claim_multiple_signals(self):
        """THIN evidence (regardless of conviction) must not claim multiple signals."""
        dec = _decision("AAPL", ActionV3.BUY, ConvictionV3.HIGH, AxisBand.THIN)
        snap = _snap([dec])
        card = snap["current_holdings"][0]
        assert self._STRONG_TEXT not in card["evidence_text"], (
            "THIN evidence must not claim 'multiple independent signals'"
        )

    def test_ok_evidence_does_not_claim_multiple_signals(self):
        """PARTIAL (OK) evidence must not claim multiple signals."""
        dec = _decision("MSFT", ActionV3.BUY, ConvictionV3.HIGH, AxisBand.OK)
        snap = _snap([dec])
        card = snap["current_holdings"][0]
        assert self._STRONG_TEXT not in card["evidence_text"]


# ── 3. Visible policy caps HIGH-conviction BUY when evidence is OK ────────────

class TestVisiblePolicyGuardrail:
    """Build 3 PR 1: Cap 5 in _compute_conviction applies to the visible decision path."""

    def test_strong_evidence_high_conviction_buy_unchanged(self):
        """Acceptance criterion 3: STRONG evidence + HIGH conviction → HIGH stays HIGH."""
        inp = DecisionInputV3(
            ticker="AAPL",
            evidence_quality=AxisBand.STRONG,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction == ConvictionV3.HIGH, (
            "STRONG evidence + HIGH conviction BUY must remain HIGH"
        )

    def test_ok_evidence_high_conviction_buy_capped_medium(self):
        """Acceptance criterion 4: OK evidence + HIGH upstream → conviction capped MEDIUM."""
        inp = DecisionInputV3(
            ticker="MSFT",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction == ConvictionV3.MEDIUM, (
            "OK evidence must cap HIGH-conviction BUY to MEDIUM in the visible policy"
        )

    def test_ok_evidence_medium_conviction_buy_unchanged(self):
        """OK evidence + MEDIUM upstream → MEDIUM (no change, guardrail only affects HIGH)."""
        inp = DecisionInputV3(
            ticker="GOOGL",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="MEDIUM",
        )
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction == ConvictionV3.MEDIUM

    def test_thin_evidence_buy_conviction_capped_low(self):
        """THIN evidence still caps to LOW via Cap 1 (unchanged)."""
        inp = DecisionInputV3(
            ticker="NVDA",
            evidence_quality=AxisBand.THIN,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
            suppression_reasons={"evidence_quality": "0 trusted dimensions"},
        )
        out = decide(inp)
        assert out.action == ActionV3.HOLD, "THIN evidence blocks BUY"
        assert out.conviction == ConvictionV3.LOW, "THIN evidence caps conviction to LOW"

    def test_sell_trim_not_affected_by_guardrail(self):
        """SELL/TRIM actions are never affected by the BUY conviction guardrail."""
        trim_inp = DecisionInputV3(
            ticker="META",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FULL,
            portfolio_fit=FitBand.OVERWEIGHT,
            risk_band=RiskBand.MEDIUM,
            raw_action="TRIM",
            raw_analyst_action="TRIM",
            upstream_conviction="HIGH",
        )
        trim_out = decide(trim_inp)
        assert trim_out.action == ActionV3.TRIM
        # TRIM caps at MEDIUM via Cap 3, but the BUY guardrail does not add further burden.

    def test_hold_not_affected_by_guardrail(self):
        """HOLD is never downgraded by the BUY conviction guardrail."""
        hold_inp = DecisionInputV3(
            ticker="TST",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.SUPPRESSED,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.NONE,
            raw_action="HOLD",
            upstream_conviction="HIGH",
        )
        hold_out = decide(hold_inp)
        assert hold_out.action == ActionV3.HOLD


# ── 4. Shadow and visible paths now agree for OK-evidence BUY ─────────────────

class TestShadowVisibleAgreement:
    """Acceptance criterion 5: shadow-only guardrail no longer differs from visible path."""

    def _ok_evidence_buy_signals(self) -> dict:
        """Card signals producing OK evidence (2 trusted dims) with HIGH conviction."""
        return dict(
            ticker="MSFT",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            category="tech",
            data_quality_label=None,
            intel_read={
                "trusted_signals": ["earnings", "revenue"],
                "insufficient_data": False,
            },
            thesis_v2=None,
        )

    def _strong_evidence_buy_signals(self) -> dict:
        """Card signals producing STRONG evidence (3 trusted dims) with HIGH conviction."""
        return dict(
            ticker="AAPL",
            v2_visible_action="HOLD",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            category="tech",
            data_quality_label=None,
            intel_read={
                "trusted_signals": ["earnings", "revenue", "margins"],
                "insufficient_data": False,
            },
            thesis_v2=None,
        )

    def test_ok_evidence_shadow_conviction_matches_visible(self):
        """Shadow path produces MEDIUM for OK+HIGH BUY — same as visible policy."""
        result = project_shadow_from_card_signals(**self._ok_evidence_buy_signals())
        assert result is not None
        assert result["v3_shadow_action"] == "BUY"
        assert result["v3_shadow_conviction"] == "MEDIUM", (
            "Shadow conviction must be MEDIUM for OK evidence + HIGH upstream — "
            "same as visible policy (Cap 5 fires before shadow guardrail sees it)"
        )

    def test_ok_evidence_shadow_guardrail_does_not_fire(self):
        """Shadow guardrail does not fire because the visible policy already capped conviction."""
        result = project_shadow_from_card_signals(**self._ok_evidence_buy_signals())
        assert result is not None
        td = result.get("truth_diagnostics") or {}
        gcg = td.get("buy_conviction_guardrail", {})
        assert gcg.get("buy_high_conviction_guardrail_applied") is False, (
            "Shadow guardrail must not fire — policy already capped conviction to MEDIUM"
        )

    def test_strong_evidence_shadow_conviction_high(self):
        """STRONG evidence → visible policy does not cap → shadow guardrail also does not fire."""
        result = project_shadow_from_card_signals(**self._strong_evidence_buy_signals())
        assert result is not None
        assert result["v3_shadow_action"] == "BUY"
        assert result["v3_shadow_conviction"] == "HIGH"
        td = result.get("truth_diagnostics") or {}
        gcg = td.get("buy_conviction_guardrail", {})
        assert gcg.get("buy_high_conviction_guardrail_applied") is False

    def test_visible_and_shadow_conviction_agree_for_ok_evidence(self):
        """End-to-end: visible decide() conviction matches shadow conviction for OK evidence."""
        inp = DecisionInputV3(
            ticker="MSFT",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        visible_out = decide(inp)
        shadow_result = project_shadow_from_card_signals(**self._ok_evidence_buy_signals())
        assert shadow_result is not None
        assert visible_out.conviction.value == shadow_result["v3_shadow_conviction"]


# ── 5. STUB is not in production speculative allowlists ──────────────────────

class TestStubRemovedFromSpeculative:
    """Acceptance criterion 6: STUB must not appear in any production speculative allowlist."""

    _EXPECTED = frozenset({"BTC", "XRP", "RIVN", "KLAR", "BLSH"})

    def test_stub_not_in_adapter_speculative_tickers(self):
        """existing_signal_adapter._SPECULATIVE_TICKERS must not contain STUB."""
        assert "STUB" not in _ADAPTER_SPECULATIVE_TICKERS, (
            "STUB must not be in existing_signal_adapter._SPECULATIVE_TICKERS"
        )

    def test_stub_not_in_governor_speculative_tickers(self):
        """portfolio_governor_lite._SPECULATIVE_TICKERS must not contain STUB."""
        assert "STUB" not in _GOVERNOR_SPECULATIVE_TICKERS, (
            "STUB must not be in portfolio_governor_lite._SPECULATIVE_TICKERS"
        )

    def test_adapter_speculative_tickers_are_real_assets(self):
        """existing_signal_adapter allowlist contains only real higher-risk assets."""
        assert _ADAPTER_SPECULATIVE_TICKERS == self._EXPECTED, (
            f"Adapter speculative allowlist should be {self._EXPECTED}, "
            f"got {_ADAPTER_SPECULATIVE_TICKERS}"
        )

    def test_governor_speculative_tickers_are_real_assets(self):
        """portfolio_governor_lite allowlist contains only real higher-risk assets."""
        assert _GOVERNOR_SPECULATIVE_TICKERS == self._EXPECTED, (
            f"Governor speculative allowlist should be {self._EXPECTED}, "
            f"got {_GOVERNOR_SPECULATIVE_TICKERS}"
        )

    def test_stub_ticker_not_forced_to_blocked_fit(self):
        """STUB no longer receives BLOCKED portfolio fit via the speculative allowlist."""
        inp = build_decision_input_from_card(
            ticker="STUB",
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label="HIGH",
            intel_read={"trusted_signals": ["a", "b", "c"], "insufficient_data": False},
            thesis_v2=None,
        )
        assert inp.portfolio_fit != FitBand.BLOCKED, (
            "STUB should no longer be forced to BLOCKED fit — it was a test artifact"
        )


# ── 6. Action contract remains deterministic ─────────────────────────────────

class TestActionContractUnchanged:
    """Buy/Hold/Trim/Sell action contract must remain deterministic after this PR."""

    def test_strong_evidence_fair_price_produces_buy(self):
        inp = DecisionInputV3(
            ticker="AAPL",
            evidence_quality=AxisBand.STRONG,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        assert out.action == ActionV3.BUY

    def test_thin_evidence_produces_hold(self):
        inp = DecisionInputV3(
            ticker="NVDA",
            evidence_quality=AxisBand.THIN,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        assert out.action == ActionV3.HOLD

    def test_overweight_produces_trim(self):
        inp = DecisionInputV3(
            ticker="META",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FULL,
            portfolio_fit=FitBand.OVERWEIGHT,
            risk_band=RiskBand.MEDIUM,
            raw_action="TRIM",
            raw_analyst_action="TRIM",
            upstream_conviction="MEDIUM",
        )
        out = decide(inp)
        assert out.action == ActionV3.TRIM

    def test_critical_risk_with_sell_signal_produces_sell(self):
        inp = DecisionInputV3(
            ticker="XYZ",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.EXPENSIVE,
            portfolio_fit=FitBand.BREACH,
            risk_band=RiskBand.CRITICAL,
            raw_action="SELL",
            raw_analyst_action="SELL",
            upstream_conviction="LOW",
        )
        out = decide(inp)
        assert out.action == ActionV3.SELL

    def test_action_output_always_in_valid_set(self):
        """All axis combinations produce only BUY/HOLD/TRIM/SELL."""
        _valid = {ActionV3.BUY, ActionV3.HOLD, ActionV3.TRIM, ActionV3.SELL}
        cases = [
            DecisionInputV3(ticker="A", evidence_quality=AxisBand.STRONG, price_context=PriceBand.FAIR, portfolio_fit=FitBand.UNDERWEIGHT, risk_band=RiskBand.NONE, raw_action="BUY", upstream_conviction="HIGH"),
            DecisionInputV3(ticker="B", evidence_quality=AxisBand.OK, price_context=PriceBand.FAIR, portfolio_fit=FitBand.UNDERWEIGHT, risk_band=RiskBand.NONE, raw_action="BUY", upstream_conviction="HIGH"),
            DecisionInputV3(ticker="C", evidence_quality=AxisBand.THIN, price_context=PriceBand.FAIR, portfolio_fit=FitBand.UNDERWEIGHT, risk_band=RiskBand.NONE, raw_action="BUY", upstream_conviction="HIGH"),
            DecisionInputV3(ticker="D", evidence_quality=AxisBand.SUPPRESSED, price_context=PriceBand.SUPPRESSED, portfolio_fit=FitBand.UNKNOWN, risk_band=RiskBand.UNKNOWN),
            DecisionInputV3(ticker="E", evidence_quality=AxisBand.OK, price_context=PriceBand.FULL, portfolio_fit=FitBand.OVERWEIGHT, risk_band=RiskBand.MEDIUM, raw_action="TRIM"),
            DecisionInputV3(ticker="F", evidence_quality=AxisBand.OK, price_context=PriceBand.EXPENSIVE, portfolio_fit=FitBand.BREACH, risk_band=RiskBand.CRITICAL, raw_action="SELL", raw_analyst_action="SELL"),
        ]
        for case in cases:
            out = decide(case)
            assert out.action in _valid, f"{case.ticker}: illegal action {out.action}"

    def test_no_buy_when_evidence_thin(self):
        """Acceptance invariant: THIN evidence never produces BUY."""
        inp = DecisionInputV3(
            ticker="TST",
            evidence_quality=AxisBand.THIN,
            price_context=PriceBand.CHEAP,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            raw_analyst_action="BUY",
            upstream_conviction="HIGH",
        )
        out = decide(inp)
        assert out.action != ActionV3.BUY

    def test_snapshot_output_no_raw_metric_keys(self):
        """Snapshot plain-English fields must not expose raw metric key names."""
        forbidden = {
            "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm",
            "revenue_growth_yoy", "peg_ratio", "p_fcf",
        }
        decisions = [
            _decision("AAPL", ActionV3.BUY, ConvictionV3.HIGH, AxisBand.STRONG),
            _decision("MSFT", ActionV3.HOLD, ConvictionV3.MEDIUM, AxisBand.OK),
            _decision("META", ActionV3.TRIM, ConvictionV3.LOW, AxisBand.THIN),
        ]
        snap = _snap(decisions)
        for card in snap["current_holdings"]:
            for field in ("why_text", "risk_text", "action_text", "evidence_text"):
                text = (card.get(field) or "").lower()
                for key in forbidden:
                    assert key not in text, (
                        f"Raw metric key '{key}' found in {field} for {card['ticker']}"
                    )
