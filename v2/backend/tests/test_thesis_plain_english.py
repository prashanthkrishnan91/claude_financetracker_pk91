from __future__ import annotations

from app.services.intelligence.thesis_engine import score_thesis
from app.services.intelligence.thesis_plain_english import build_thesis_plain_english


RAW_KEYS = [
    "fcf_margin",
    "roic_ttm",
    "net_debt_to_ebitda",
    "ev_ebitda",
    "ps_ttm",
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


def _flatten_text(summary: dict) -> str:
    return " ".join([summary["headline"], summary["quality_label"], summary["valuation_label"], summary["risk_label"], summary["momentum_label"], summary["data_label"], *summary["caveats"]]).lower()


def test_complete_strong_scorecard_produces_positive_summary():
    card = score_thesis("ACME", _strong_inputs())
    summary = build_thesis_plain_english(card)
    assert summary["headline"] == "Overall investment case looks constructive"
    assert summary["quality_label"] == "Business quality looks strong"
    assert summary["risk_label"] == "Balance sheet risk looks manageable"
    assert summary["data_label"] == "Data coverage looks usable"


def test_partial_scorecard_flags_data_incomplete():
    inputs = _strong_inputs()
    for key in ("ps_ttm", "ps_forward", "p_fcf", "ev_ebitda", "peg", "fcf_yield", "forward_pe", "trailing_pe", "peer_ps_median", "peer_ev_ebitda_median", "own_5y_ps_median"):
        inputs.pop(key)
    card = score_thesis("ACME", inputs)
    summary = build_thesis_plain_english(card)
    assert summary["headline"] == "Signal is mixed with partial data coverage"
    assert summary["data_label"] == "Data is still incomplete"
    assert any("directional read" in caveat.lower() for caveat in summary["caveats"])


def test_insufficient_data_gets_conservative_summary():
    card = score_thesis("ACME", {})
    summary = build_thesis_plain_english(card)
    assert summary["headline"] == "Not enough data for a reliable investment-case read"
    assert summary["data_label"] == "Data is still incomplete"


def test_insufficient_data_with_available_inputs_is_not_universal_fallback_copy():
    # Representative mapper-like payload: enough real fields to produce
    # directional dimension labels, but still insufficient overall.
    inputs = {
        "trailing_pe": 23.0,
        "forward_pe": 20.0,
        "peg": 1.3,
        "ps_ttm": 6.2,
        "ev_ebitda": 16.0,
        "revenue_yoy": 0.14,
        "beta": 1.1,
        "net_debt_to_ebitda": 0.9,
        "return_5d": 0.01,
        "return_30d": 0.03,
        "relative_strength_vs_spy": 1.5,
        "trend_regime_score": 55.0,
        "sma_20_50_signal": 1,
    }
    card = score_thesis("AAPL", inputs)
    assert card.status.value == "INSUFFICIENT_DATA"
    summary = build_thesis_plain_english(card)
    assert summary["quality_label"] != "Business quality data is incomplete"
    assert summary["valuation_label"] != "Valuation data is incomplete"
    assert summary["risk_label"] != "Risk data is incomplete"
    assert summary["momentum_label"] != "Momentum data is incomplete"


def test_serialized_scorecard_dict_matches_object_translation():
    card = score_thesis("ACME", _strong_inputs())
    card_dict = {
        "status": card.status.value,
        "quality": {"score": card.quality.score, "published": card.quality.published},
        "valuation": {"score": card.valuation.score, "published": card.valuation.published},
        "risk": {"score": card.risk.score, "published": card.risk.published},
        "momentum": {"score": card.momentum.score, "published": card.momentum.published},
    }
    assert build_thesis_plain_english(card) == build_thesis_plain_english(card_dict)


def test_output_does_not_expose_raw_metric_names():
    card = score_thesis("ACME", _strong_inputs())
    summary = build_thesis_plain_english(card)
    all_text = _flatten_text(summary)
    for raw_key in RAW_KEYS:
        assert raw_key not in all_text


def test_translation_is_deterministic():
    card = score_thesis("ACME", _strong_inputs())
    a = build_thesis_plain_english(card)
    b = build_thesis_plain_english(card)
    assert a == b
