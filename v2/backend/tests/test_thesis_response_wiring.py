"""Intel v2 PR-8 — tests for thesis_plain_english wiring into backend response payload.

Verifies:
- thesis_plain_english is present when a thesis_v2 scorecard exists.
- thesis_v2 is preserved unchanged alongside thesis_plain_english.
- Response text never leaks raw finance metric keys.
- Missing / partial / INSUFFICIENT_DATA scorecard does not break the response.
- build_thesis_plain_english is the sole generation path (no LLM calls).
"""

from __future__ import annotations

import copy
import socket
from uuid import uuid4

from app.services.intelligence.thesis_engine import score_thesis
from app.services.intelligence.thesis_plain_english import build_thesis_plain_english


RAW_METRIC_KEYS = [
    "fcf_margin",
    "roic_ttm",
    "net_debt_to_ebitda",
    "ev_ebitda",
    "ps_ttm",
    "gross_margin",
    "interest_coverage",
    "share_count_delta_3y",
    "peg",
    "forward_pe",
    "trailing_pe",
]


def _strong_inputs() -> dict:
    return {
        "roic_ttm": 0.22,
        "gross_margin": 0.72,
        "fcf_margin": 0.26,
        "fcf_to_net_income": 1.20,
        "net_debt_to_ebitda": 0.5,
        "interest_coverage": 18.0,
        "share_count_delta_3y": -0.06,
        "ps_ttm": 4.0,
        "ps_forward": 3.5,
        "p_fcf": 15.0,
        "ev_ebitda": 12.0,
        "peg": 1.0,
        "fcf_yield": 0.06,
        "forward_pe": 18.0,
        "trailing_pe": 22.0,
        "peer_ps_median": 8.0,
        "peer_ev_ebitda_median": 18.0,
        "own_5y_ps_median": 9.0,
        "revenue_cagr_3y": 0.25,
        "revenue_yoy": 0.28,
        "fcf_cagr_3y": 0.30,
        "gross_profit_yoy": 0.26,
        "forward_revenue_growth_est": 0.22,
        "customer_concentration_flag": 0,
        "guidance_cut_count_4q": 0,
        "insider_net_selling_6m": 0.005,
        "beta": 0.80,
        "max_drawdown_1y": -0.10,
        "gaap_nongaap_gap": 0.02,
        "relative_strength_vs_spy": 8.0,
        "trend_regime_score": 75.0,
        "return_5d": 0.03,
        "return_30d": 0.09,
        "sma_20_50_signal": 1,
    }


def _scorecard_to_dict(card) -> dict:
    def _sub(sub) -> dict:
        return {
            "score": sub.score,
            "data_quality": sub.data_quality,
            "inputs_used": list(sub.inputs_used),
            "inputs_missing": list(sub.inputs_missing),
            "published": sub.published,
        }

    return {
        "ticker": card.ticker,
        "status": card.status.value,
        "quality": _sub(card.quality),
        "valuation": _sub(card.valuation),
        "growth": _sub(card.growth),
        "risk": _sub(card.risk),
        "momentum": _sub(card.momentum),
        "conviction_score": card.conviction_score,
        "conviction_band": card.conviction_band.value,
        "blended_data_quality": card.blended_data_quality,
        "inputs_used": list(card.inputs_used),
        "inputs_missing": list(card.inputs_missing),
        "score_version": card.score_version,
    }


def _base_kwargs() -> dict:
    return dict(
        id=uuid4(),
        ticker="AAPL",
        name="Apple Inc.",
        action="HOLD",
        detail="Monitoring",
        rationale="Strong fundamentals",
        urgency=1,
        color="blue",
        tax_note="LT eligible",
        drip_note="",
        category="Core",
    )


def _flatten_text(summary: dict) -> str:
    parts = [
        summary.get("headline", ""),
        summary.get("quality_label", ""),
        summary.get("valuation_label", ""),
        summary.get("risk_label", ""),
        summary.get("momentum_label", ""),
        summary.get("data_label", ""),
        *summary.get("caveats", []),
    ]
    return " ".join(parts).lower()


# ---------------------------------------------------------------------------
# 1. thesis_plain_english is present when scorecard exists
# ---------------------------------------------------------------------------

class TestThesisPlainEnglishPresentWhenScorecardExists:
    def test_insight_card_carries_thesis_plain_english(self):
        from app.models.recommendation import InsightCard
        card = score_thesis("AAPL", _strong_inputs())
        scorecard_dict = _scorecard_to_dict(card)
        plain = build_thesis_plain_english(scorecard_dict)

        insight = InsightCard(**_base_kwargs(), thesis_v2=scorecard_dict, thesis_plain_english=plain)
        assert insight.thesis_plain_english is not None
        assert isinstance(insight.thesis_plain_english, dict)

    def test_thesis_plain_english_has_required_keys(self):
        card = score_thesis("AAPL", _strong_inputs())
        plain = build_thesis_plain_english(_scorecard_to_dict(card))
        required = {"headline", "quality_label", "valuation_label", "risk_label",
                    "momentum_label", "data_label", "caveats"}
        assert required.issubset(set(plain.keys()))

    def test_headline_is_non_empty_string(self):
        card = score_thesis("AAPL", _strong_inputs())
        plain = build_thesis_plain_english(_scorecard_to_dict(card))
        assert isinstance(plain["headline"], str) and plain["headline"]

    def test_caveats_is_a_list(self):
        card = score_thesis("AAPL", _strong_inputs())
        plain = build_thesis_plain_english(_scorecard_to_dict(card))
        assert isinstance(plain["caveats"], list)


# ---------------------------------------------------------------------------
# 2. thesis_v2 is preserved unchanged
# ---------------------------------------------------------------------------

class TestThesisV2PreservedUnchanged:
    def test_build_thesis_plain_english_does_not_mutate_scorecard_dict(self):
        card = score_thesis("AAPL", _strong_inputs())
        scorecard_dict = _scorecard_to_dict(card)
        original = copy.deepcopy(scorecard_dict)
        build_thesis_plain_english(scorecard_dict)
        assert scorecard_dict == original

    def test_insight_card_thesis_v2_equals_original_scorecard(self):
        from app.models.recommendation import InsightCard
        card = score_thesis("AAPL", _strong_inputs())
        scorecard_dict = _scorecard_to_dict(card)
        plain = build_thesis_plain_english(scorecard_dict)
        insight = InsightCard(**_base_kwargs(), thesis_v2=scorecard_dict, thesis_plain_english=plain)
        assert insight.thesis_v2 == scorecard_dict

    def test_thesis_v2_status_key_intact(self):
        from app.models.recommendation import InsightCard
        card = score_thesis("AAPL", _strong_inputs())
        scorecard_dict = _scorecard_to_dict(card)
        plain = build_thesis_plain_english(scorecard_dict)
        insight = InsightCard(**_base_kwargs(), thesis_v2=scorecard_dict, thesis_plain_english=plain)
        assert insight.thesis_v2["status"] == card.status.value

    def test_thesis_v2_conviction_band_key_intact(self):
        from app.models.recommendation import InsightCard
        card = score_thesis("AAPL", _strong_inputs())
        scorecard_dict = _scorecard_to_dict(card)
        plain = build_thesis_plain_english(scorecard_dict)
        insight = InsightCard(**_base_kwargs(), thesis_v2=scorecard_dict, thesis_plain_english=plain)
        assert insight.thesis_v2["conviction_band"] == card.conviction_band.value


# ---------------------------------------------------------------------------
# 3. No raw metric keys in thesis_plain_english text
# ---------------------------------------------------------------------------

class TestNoRawMetricKeysInPlainEnglishText:
    def test_strong_scorecard_no_raw_keys_in_text(self):
        card = score_thesis("AAPL", _strong_inputs())
        plain = build_thesis_plain_english(_scorecard_to_dict(card))
        text = _flatten_text(plain)
        for key in RAW_METRIC_KEYS:
            assert key not in text, f"Raw key '{key}' leaked into thesis_plain_english text"

    def test_partial_scorecard_no_raw_keys_in_text(self):
        inputs = _strong_inputs()
        for key in ("ps_ttm", "ps_forward", "p_fcf", "ev_ebitda", "peg",
                    "fcf_yield", "forward_pe", "trailing_pe",
                    "peer_ps_median", "peer_ev_ebitda_median", "own_5y_ps_median"):
            inputs.pop(key, None)
        card = score_thesis("AAPL", inputs)
        plain = build_thesis_plain_english(_scorecard_to_dict(card))
        text = _flatten_text(plain)
        for key in RAW_METRIC_KEYS:
            assert key not in text, f"Raw key '{key}' leaked in partial text"

    def test_insufficient_data_scorecard_no_raw_keys_in_text(self):
        card = score_thesis("AAPL", {})
        plain = build_thesis_plain_english(_scorecard_to_dict(card))
        text = _flatten_text(plain)
        for key in RAW_METRIC_KEYS:
            assert key not in text, f"Raw key '{key}' leaked in INSUFFICIENT_DATA text"


# ---------------------------------------------------------------------------
# 4. Missing / partial / INSUFFICIENT_DATA does not break the response
# ---------------------------------------------------------------------------

class TestMissingOrPartialScorecardDoesNotBreak:
    def test_none_thesis_v2_gives_none_plain_english_on_card(self):
        from app.models.recommendation import InsightCard
        insight = InsightCard(**_base_kwargs())
        assert insight.thesis_plain_english is None
        assert insight.thesis_v2 is None

    def test_empty_dict_scorecard_returns_translation(self):
        plain = build_thesis_plain_english({})
        assert isinstance(plain, dict)
        assert "headline" in plain

    def test_insufficient_data_scorecard_returns_conservative_summary(self):
        card = score_thesis("AAPL", {})
        plain = build_thesis_plain_english(_scorecard_to_dict(card))
        assert "not enough data" in plain["headline"].lower()

    def test_partial_scorecard_returns_directional_caveat(self):
        inputs = _strong_inputs()
        for key in ("ps_ttm", "ps_forward", "p_fcf", "ev_ebitda", "peg",
                    "fcf_yield", "forward_pe", "trailing_pe",
                    "peer_ps_median", "peer_ev_ebitda_median", "own_5y_ps_median"):
            inputs.pop(key, None)
        card = score_thesis("AAPL", inputs)
        plain = build_thesis_plain_english(_scorecard_to_dict(card))
        assert any("directional read" in c.lower() for c in plain["caveats"])

    def test_insight_card_without_thesis_fields_serializes_cleanly(self):
        from app.models.recommendation import InsightCard
        insight = InsightCard(**_base_kwargs())
        dumped = insight.model_dump(exclude_none=True)
        assert "thesis_v2" not in dumped
        assert "thesis_plain_english" not in dumped

    def test_insight_card_with_both_fields_serializes_correctly(self):
        from app.models.recommendation import InsightCard
        card = score_thesis("AAPL", _strong_inputs())
        scorecard_dict = _scorecard_to_dict(card)
        plain = build_thesis_plain_english(scorecard_dict)
        insight = InsightCard(**_base_kwargs(), thesis_v2=scorecard_dict, thesis_plain_english=plain)
        dumped = insight.model_dump()
        assert dumped["thesis_plain_english"]["headline"] == plain["headline"]


# ---------------------------------------------------------------------------
# 5. No LLM or IO required
# ---------------------------------------------------------------------------

class TestNoLLMOrIORequired:
    def test_translation_is_deterministic(self):
        card = score_thesis("AAPL", _strong_inputs())
        scorecard_dict = _scorecard_to_dict(card)
        a = build_thesis_plain_english(scorecard_dict)
        b = build_thesis_plain_english(scorecard_dict)
        assert a == b

    def test_translation_requires_no_io(self):
        original_socket = socket.socket

        class _BlockingSocket:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("IO not allowed inside build_thesis_plain_english")

        socket.socket = _BlockingSocket  # type: ignore[assignment]
        try:
            card = score_thesis("AAPL", _strong_inputs())
            plain = build_thesis_plain_english(_scorecard_to_dict(card))
            assert plain["headline"]
        finally:
            socket.socket = original_socket
