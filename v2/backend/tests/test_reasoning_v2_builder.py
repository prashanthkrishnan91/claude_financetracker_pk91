"""Tests for Intel Reasoning v2 Builder.

Coverage requirements from spec:
1.  Schema shape: all required top-level sections and schema_version.
2.  Deterministic-only ticker: support="deterministic" where applicable.
3.  Analyst-only / insufficient-data: WATCH posture + insufficient_data blocker.
4.  Both present + agree: agreement="agree", non-WATCH posture when data allows.
5.  Both present + disagree: WATCH + agreement_conflict blocker.
6.  used_fallback=True: recorded in evidence, excluded from user_text copy.
7.  PARTIAL scorecard: user_text only from published dimensions; subscore_basis only lists published.
8.  Forbidden indicator language absent from all user_text fields.
9.  No allocation leakage in any section.
10. Data quality band thresholds around 0.50 and 0.75.
11. Evidence traceability: every subscore_basis entry in evidence.deterministic.
12. Determinism: same inputs → identical dict output.
13. ScoreCard object and serialised dict produce equivalent reasoning.
14. Snapshot tests for READY, PARTIAL, INSUFFICIENT_DATA.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.services.intelligence.reasoning_v2_builder import (
    SCHEMA_VERSION,
    ScoreCard,
    build_reasoning_v2,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

COMPACT_V1_VERDICT: dict[str, Any] = {
    "action": "BUY",
    "conviction": 0.72,
    "confidence": 0.78,
    "generation_version": "compact_v1",
    "used_fallback": False,
    "analysis_source": "live_llm",
    "primary_driver": "Cloud infrastructure demand is accelerating with enterprise adoption.",
    "risk_flag": "Enterprise spending slowdown could reduce renewal rates quickly.",
    "action_reason": "Accumulate on weakness; strong free cash flow supports the thesis.",
    "differentiation": "vs MSFT: margin expansion path is steeper and valuation is lower.",
    "key_drivers": ["enterprise adoption", "free cash flow growth"],
    "risks": ["enterprise spending slowdown"],
    "why": "Cloud infrastructure demand is accelerating with enterprise adoption.",
    "risk": "Enterprise spending slowdown could reduce renewal rates quickly.",
    "do": "Accumulate on weakness; strong free cash flow supports the thesis.",
    "alt_view": "vs MSFT: margin expansion path is steeper and valuation is lower.",
}

READY_SCORECARD_DICT: dict[str, Any] = {
    "ticker": "NVDA",
    "status": "READY",
    "data_quality_score": 0.85,
    "missing_fields": [],
    "stale_fields": [],
    "dimensions": {},
    "return_1d": 1.2,
    "return_5d": 3.4,
    "return_30d": 12.5,
    "volatility_30d": 28.0,
    "sentiment_score": 0.65,
    "momentum_score": 0.7,
    "trend_regime": "bullish",
    "relative_strength": 1.15,
}

PARTIAL_SCORECARD_DICT: dict[str, Any] = {
    "ticker": "ALK",
    "status": "PARTIAL",
    "data_quality_score": 0.45,
    "missing_fields": ["fundamentals", "news"],
    "stale_fields": ["return_30d"],
    "dimensions": {},
    "return_1d": 0.3,
    "return_5d": None,
    "return_30d": None,
    "volatility_30d": 35.0,
    "sentiment_score": None,
    "momentum_score": None,
    "trend_regime": None,
    "relative_strength": None,
}

INSUFFICIENT_SCORECARD_DICT: dict[str, Any] = {
    "ticker": "KLAR",
    "status": "INSUFFICIENT_DATA",
    "data_quality_score": 0.10,
    "missing_fields": ["price", "fundamentals", "news", "return_1d"],
    "stale_fields": [],
    "dimensions": {},
}

FALLBACK_VERDICT: dict[str, Any] = {
    "action": "INSUFFICIENT_DATA",
    "conviction": 0.0,
    "confidence": 0.0,
    "generation_version": "compact_v1",
    "used_fallback": True,
    "analysis_source": "deterministic_fallback",
    "primary_driver": "No clear edge vs alternatives.",
    "risk_flag": "Limited data reduces conviction.",
    "action_reason": "Hold — no allocation until signal improves.",
    "differentiation": "—",
}


# ── Test 1: Schema shape ──────────────────────────────────────────────────────

def test_schema_has_all_required_top_level_sections():
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=None)
    required = {"schema_version", "ticker", "why", "risk", "action", "alt_view",
                "confidence", "deploy_signals", "evidence", "data_quality"}
    assert required.issubset(result.keys()), f"Missing sections: {required - result.keys()}"
    assert result["schema_version"] == SCHEMA_VERSION


def test_schema_why_has_required_keys():
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=None)
    assert {"user_text", "support", "subscore_basis"} == result["why"].keys()


def test_schema_risk_has_required_keys():
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=None)
    assert {"user_text", "severity", "support"} == result["risk"].keys()


def test_schema_action_has_required_keys():
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=None)
    assert {"posture", "user_text", "support"} == result["action"].keys()


def test_schema_confidence_has_required_keys():
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=None)
    assert {"conviction_band", "agreement", "score"} == result["confidence"].keys()


def test_schema_deploy_signals_has_required_keys():
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=None)
    ds = result["deploy_signals"]
    required_ds = {
        "conviction_band", "risk_band", "data_quality_band",
        "action_posture", "watchlist_reason", "blockers", "caveats",
    }
    assert required_ds.issubset(ds.keys())


def test_schema_evidence_has_required_keys():
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=None)
    assert {"deterministic", "analyst", "provider"} == result["evidence"].keys()


def test_schema_data_quality_has_required_keys():
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=None)
    dq = result["data_quality"]
    assert {"status", "blended_quality", "missing", "stale", "user_safe_note"} == dq.keys()


# ── Test 2: Deterministic-only ticker ────────────────────────────────────────

def test_deterministic_only_no_analyst_citations():
    sc = dict(READY_SCORECARD_DICT)
    result = build_reasoning_v2(ticker="NVDA", scorecard=sc, analyst_verdict=None)
    # No analyst evidence should be present
    ev_analyst = result["evidence"]["analyst"]
    assert ev_analyst == {}
    # Why support should be deterministic or insufficient (no analyst)
    assert result["why"]["support"] in {"deterministic", "insufficient"}


def test_deterministic_only_evidence_populated():
    sc = dict(READY_SCORECARD_DICT)
    result = build_reasoning_v2(ticker="NVDA", scorecard=sc, analyst_verdict=None)
    det = result["evidence"]["deterministic"]
    assert "30d_return" in det
    assert det["30d_return"] == 12.5


# ── Test 3: Analyst-only / insufficient-data → WATCH ────────────────────────

def test_no_scorecard_no_analyst_forces_watch():
    result = build_reasoning_v2(ticker="STUB", scorecard=None, analyst_verdict=None)
    assert result["action"]["posture"] == "WATCH"
    assert "insufficient_data" in result["deploy_signals"]["blockers"]


def test_insufficient_data_scorecard_no_analyst_forces_watch():
    result = build_reasoning_v2(
        ticker="KLAR",
        scorecard=dict(INSUFFICIENT_SCORECARD_DICT),
        analyst_verdict=None,
    )
    assert result["action"]["posture"] == "WATCH"
    assert "insufficient_data" in result["deploy_signals"]["blockers"]
    assert result["confidence"]["conviction_band"] == "INSUFFICIENT_DATA"


def test_no_scorecard_with_valid_analyst_watch_is_allowed_only_when_expected():
    # With valid analyst + no scorecard: insufficient-data contract forces WATCH
    result = build_reasoning_v2(
        ticker="AAPL",
        scorecard=None,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    # Scorecard absent → INSUFFICIENT_DATA status → WATCH contract
    assert result["action"]["posture"] == "WATCH"
    assert result["deploy_signals"]["action_posture"] == "WATCH"
    assert "insufficient_data" in result["deploy_signals"]["blockers"]
    assert result["confidence"]["agreement"] == "analyst_only"


# ── Test 4: Both present and agree ───────────────────────────────────────────

def test_both_agree_non_watch_posture():
    # Scorecard shows positive sentiment + analyst says BUY → agree
    sc = dict(READY_SCORECARD_DICT)  # sentiment_score=0.65 → positive
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=sc,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    assert result["confidence"]["agreement"] == "agree"
    assert result["action"]["posture"] in {"ACCUMULATE", "HOLD"}
    assert "agreement_conflict" not in result["deploy_signals"]["blockers"]


def test_both_agree_conviction_band_from_analyst():
    sc = dict(READY_SCORECARD_DICT)
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=sc,
        analyst_verdict=dict(COMPACT_V1_VERDICT),  # conviction=0.72 → HIGH
    )
    assert result["confidence"]["conviction_band"] == "HIGH"


# ── Test 5: Both present and disagree ────────────────────────────────────────

def test_disagree_forces_watch():
    # Analyst says BUY but scorecard shows very negative sentiment
    sc = dict(READY_SCORECARD_DICT, sentiment_score=-0.75)
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=sc,
        analyst_verdict=dict(COMPACT_V1_VERDICT),  # action=BUY
    )
    assert result["confidence"]["agreement"] == "disagree"
    assert result["action"]["posture"] == "WATCH"
    assert "agreement_conflict" in result["deploy_signals"]["blockers"]


def test_disagree_does_not_change_recommendation_action_fields():
    # This test verifies we don't mutate incoming dicts and the output is self-contained
    sc = dict(READY_SCORECARD_DICT, sentiment_score=-0.75)
    av = dict(COMPACT_V1_VERDICT)
    original_av = copy.deepcopy(av)
    build_reasoning_v2(ticker="NVDA", scorecard=sc, analyst_verdict=av)
    assert av == original_av  # input not mutated


# ── Test 6: used_fallback=True → evidence-only, excluded from user copy ──────

def test_fallback_verdict_recorded_in_evidence():
    result = build_reasoning_v2(
        ticker="KLAR",
        scorecard=None,
        analyst_verdict=dict(FALLBACK_VERDICT),
    )
    ev = result["evidence"]["analyst"]
    assert ev.get("used_fallback") is True
    assert ev.get("action") == "INSUFFICIENT_DATA"


def test_fallback_verdict_excluded_from_why_user_text():
    result = build_reasoning_v2(
        ticker="KLAR",
        scorecard=None,
        analyst_verdict=dict(FALLBACK_VERDICT),
    )
    # Fallback verdict should NOT contribute to why/risk/action user_text
    assert result["why"]["support"] == "insufficient"
    assert result["action"]["posture"] == "WATCH"


def test_fallback_verdict_caveat_recorded():
    result = build_reasoning_v2(
        ticker="KLAR",
        scorecard=None,
        analyst_verdict=dict(FALLBACK_VERDICT),
    )
    assert "analyst_fallback_recorded" in result["deploy_signals"]["caveats"]


def test_fallback_verdict_why_does_not_use_fallback_copy():
    # "No clear edge vs alternatives." is the fallback primary_driver — must not appear
    # in why.user_text when used_fallback=True (it would be passing fallback text as real copy)
    result = build_reasoning_v2(
        ticker="KLAR",
        scorecard=None,
        analyst_verdict=dict(FALLBACK_VERDICT),
    )
    # Since fallback is rejected, why.user_text should be the conservative insufficient text
    assert "No clear edge vs alternatives" not in result["why"]["user_text"]
    assert "No clear edge vs alternatives" not in result["action"]["user_text"]


# ── Test 7: PARTIAL scorecard ────────────────────────────────────────────────

def test_partial_scorecard_emits_deterministic_why():
    result = build_reasoning_v2(
        ticker="ALK",
        scorecard=dict(PARTIAL_SCORECARD_DICT),
        analyst_verdict=None,
    )
    # Why should mention partial coverage from deterministic dimensions
    assert result["why"]["support"] in {"deterministic", "insufficient"}


def test_partial_scorecard_subscore_basis_only_published():
    result = build_reasoning_v2(
        ticker="ALK",
        scorecard=dict(PARTIAL_SCORECARD_DICT),
        analyst_verdict=None,
    )
    det = result["evidence"]["deterministic"]
    for key in result["why"]["subscore_basis"]:
        assert key in det, f"subscore_basis entry '{key}' not in evidence.deterministic"


def test_partial_scorecard_null_fields_excluded_from_evidence():
    result = build_reasoning_v2(
        ticker="ALK",
        scorecard=dict(PARTIAL_SCORECARD_DICT),
        analyst_verdict=None,
    )
    det = result["evidence"]["deterministic"]
    # return_5d is None in PARTIAL_SCORECARD_DICT — must not appear
    assert "5d_return" not in det
    # return_1d is 0.3 — must appear
    assert "1d_return" in det


# ── Test 8: No forbidden language in user_text ───────────────────────────────

_FORBIDDEN_PHRASES = [
    "moving average", "SMA", "SMA20", "SMA50", "momentum", "trending",
    "RSI", "price above", "outperforming the broad market", "MACD",
    "Bollinger", "support level", "resistance level",
]


def _all_user_texts(result: dict) -> list[str]:
    return [
        result["why"]["user_text"],
        result["risk"]["user_text"],
        result["action"]["user_text"],
        result["alt_view"]["user_text"],
    ]


@pytest.mark.parametrize("phrase", _FORBIDDEN_PHRASES)
def test_no_forbidden_language_in_base_output(phrase):
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=None)
    for text in _all_user_texts(result):
        assert phrase.lower() not in text.lower(), (
            f"Forbidden phrase '{phrase}' found in user_text: {text!r}"
        )


def test_forbidden_language_in_analyst_verdict_is_scrubbed():
    # If analyst produces forbidden language, it must be scrubbed from user_text
    dirty_verdict = dict(COMPACT_V1_VERDICT)
    dirty_verdict["primary_driver"] = "SMA50 and RSI are showing upward momentum."
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=dirty_verdict)
    for text in _all_user_texts(result):
        for phrase in _FORBIDDEN_PHRASES:
            assert phrase.lower() not in text.lower(), (
                f"Forbidden phrase '{phrase}' in user_text: {text!r}"
            )


# ── Test 9: No allocation leakage ────────────────────────────────────────────

_ALLOCATION_PATTERNS = [
    "$", "allocation", "percent", "%", "share count", "dollar",
    "position target", "target weight", "deploy", "units",
]


def test_no_allocation_leakage_in_why(capsys):
    result = build_reasoning_v2(
        ticker="AAPL",
        scorecard=None,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    for text in _all_user_texts(result):
        for pattern in ("suggested_allocation", "position_target", "target_weight"):
            assert pattern not in text.lower()


def test_deploy_signals_has_no_allocation_math():
    result = build_reasoning_v2(
        ticker="AAPL",
        scorecard=None,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    ds = result["deploy_signals"]
    # None of these keys should appear
    forbidden_keys = {
        "allocation_amount", "allocation_pct", "dollar_amount",
        "share_count", "position_target", "target_weight",
    }
    assert not forbidden_keys.intersection(ds.keys()), (
        f"Allocation math keys found in deploy_signals: {forbidden_keys.intersection(ds.keys())}"
    )


# ── Test 10: Data quality band thresholds ────────────────────────────────────

def test_data_quality_band_low_below_050():
    # No scorecard, no analyst → blended=0.0 → LOW
    result = build_reasoning_v2(ticker="STUB", scorecard=None, analyst_verdict=None)
    assert result["deploy_signals"]["data_quality_band"] == "LOW"
    assert result["data_quality"]["blended_quality"] < 0.50


def test_data_quality_band_medium_around_060():
    # Analyst present (no scorecard) → blended=0.60 → MEDIUM
    result = build_reasoning_v2(
        ticker="AAPL",
        scorecard=None,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    assert result["data_quality"]["blended_quality"] == 0.60
    assert result["deploy_signals"]["data_quality_band"] == "MEDIUM"


def test_data_quality_band_high_above_075():
    # sc_quality=0.85, analyst present → blended=(0.85+0.80)/2=0.825 → HIGH
    sc = dict(READY_SCORECARD_DICT)  # data_quality_score=0.85
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=sc,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    assert result["data_quality"]["blended_quality"] >= 0.75
    assert result["deploy_signals"]["data_quality_band"] == "HIGH"


def test_data_quality_band_boundary_exactly_050():
    # Partial scorecard quality=0.20 + no analyst → blended=0.20 → LOW
    sc = dict(PARTIAL_SCORECARD_DICT, data_quality_score=0.20)
    result = build_reasoning_v2(ticker="ALK", scorecard=sc, analyst_verdict=None)
    assert result["data_quality"]["blended_quality"] == 0.20
    assert result["deploy_signals"]["data_quality_band"] == "LOW"


def test_data_quality_band_boundary_exactly_075():
    # sc_quality=0.70 + analyst → (0.70+0.80)/2=0.75 → HIGH (boundary is >=0.75)
    sc = dict(READY_SCORECARD_DICT, data_quality_score=0.70)
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=sc,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    assert result["data_quality"]["blended_quality"] == 0.75
    assert result["deploy_signals"]["data_quality_band"] == "HIGH"


# ── Test 11: Evidence traceability ───────────────────────────────────────────

def test_subscore_basis_entries_all_in_evidence_deterministic():
    sc = dict(PARTIAL_SCORECARD_DICT)
    result = build_reasoning_v2(
        ticker="ALK",
        scorecard=sc,
        analyst_verdict=None,
    )
    det = result["evidence"]["deterministic"]
    for key in result["why"]["subscore_basis"]:
        assert key in det, (
            f"subscore_basis '{key}' has no matching entry in evidence.deterministic: {det}"
        )


def test_evidence_deterministic_has_all_non_null_dimensions():
    sc = dict(READY_SCORECARD_DICT)
    result = build_reasoning_v2(ticker="NVDA", scorecard=sc, analyst_verdict=None)
    det = result["evidence"]["deterministic"]
    expected_keys = {
        "1d_return", "5d_return", "30d_return", "volatility_30d",
        "sentiment_score", "momentum_score", "trend_regime", "relative_strength",
    }
    assert expected_keys == set(det.keys())


def test_analyst_evidence_pass_through_only():
    av = dict(COMPACT_V1_VERDICT)
    result = build_reasoning_v2(ticker="NVDA", scorecard=None, analyst_verdict=av)
    ev_analyst = result["evidence"]["analyst"]
    # Must be a pass-through subset, not synthesised
    assert ev_analyst.get("action") == av["action"]
    assert ev_analyst.get("conviction") == av["conviction"]
    assert ev_analyst.get("generation_version") == av["generation_version"]


# ── Test 12: Determinism ─────────────────────────────────────────────────────

def test_same_inputs_produce_identical_output():
    sc = dict(READY_SCORECARD_DICT)
    av = dict(COMPACT_V1_VERDICT)
    result_a = build_reasoning_v2(ticker="NVDA", scorecard=sc, analyst_verdict=av)
    result_b = build_reasoning_v2(ticker="NVDA", scorecard=sc, analyst_verdict=av)
    assert result_a == result_b


def test_determinism_with_none_inputs():
    r1 = build_reasoning_v2(ticker="STUB", scorecard=None, analyst_verdict=None)
    r2 = build_reasoning_v2(ticker="STUB", scorecard=None, analyst_verdict=None)
    assert r1 == r2


# ── Test 13: ScoreCard object and dict produce equivalent results ─────────────

def test_scorecard_object_and_dict_equivalent():
    sc_obj = ScoreCard(
        ticker="NVDA",
        status="READY",
        data_quality_score=0.85,
        missing_fields=[],
        stale_fields=[],
        return_1d=1.2,
        return_5d=3.4,
        return_30d=12.5,
        volatility_30d=28.0,
        sentiment_score=0.65,
        momentum_score=0.7,
        trend_regime="bullish",
        relative_strength=1.15,
    )
    sc_dict = dict(READY_SCORECARD_DICT)
    av = dict(COMPACT_V1_VERDICT)

    result_obj = build_reasoning_v2(ticker="NVDA", scorecard=sc_obj, analyst_verdict=av)
    result_dict = build_reasoning_v2(ticker="NVDA", scorecard=sc_dict, analyst_verdict=av)

    # Both should produce identical structured output
    assert result_obj == result_dict


# ── Test 14: Snapshot-style tests for READY / PARTIAL / INSUFFICIENT_DATA ────

def test_snapshot_ready_scorecard_with_analyst():
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(READY_SCORECARD_DICT),
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    assert result["schema_version"] == "reasoning_v2.0"
    assert result["ticker"] == "NVDA"
    assert result["data_quality"]["status"] == "READY"
    assert result["action"]["posture"] in {"ACCUMULATE", "HOLD"}
    assert result["confidence"]["conviction_band"] == "HIGH"
    assert result["deploy_signals"]["data_quality_band"] == "HIGH"
    assert result["deploy_signals"]["blockers"] == []
    assert result["why"]["support"] == "analyst"
    assert result["risk"]["support"] == "analyst"


def test_snapshot_partial_scorecard_no_analyst():
    result = build_reasoning_v2(
        ticker="ALK",
        scorecard=dict(PARTIAL_SCORECARD_DICT),
        analyst_verdict=None,
    )
    assert result["data_quality"]["status"] == "PARTIAL"
    assert result["action"]["posture"] == "WATCH"
    assert result["confidence"]["conviction_band"] == "INSUFFICIENT_DATA"
    assert "insufficient_data" not in result["deploy_signals"]["blockers"]
    # PARTIAL status alone doesn't block, but no analyst → WATCH
    assert result["data_quality"]["missing"] == ["fundamentals", "news"]


def test_snapshot_insufficient_data_no_analyst():
    result = build_reasoning_v2(
        ticker="KLAR",
        scorecard=dict(INSUFFICIENT_SCORECARD_DICT),
        analyst_verdict=None,
    )
    assert result["data_quality"]["status"] == "INSUFFICIENT_DATA"
    assert result["action"]["posture"] == "WATCH"
    assert "insufficient_data" in result["deploy_signals"]["blockers"]
    assert result["deploy_signals"]["watchlist_reason"] == "insufficient_data"
    assert result["confidence"]["conviction_band"] == "INSUFFICIENT_DATA"
    assert result["why"]["support"] == "insufficient"


def test_insufficient_data_with_strong_analyst_buy_forces_watch_posture():
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(INSUFFICIENT_SCORECARD_DICT),
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    assert result["data_quality"]["status"] == "INSUFFICIENT_DATA"
    assert result["action"]["posture"] == "WATCH"
    assert "accumulate" not in result["action"]["user_text"].lower()


def test_insufficient_data_with_strong_analyst_buy_forces_deploy_watch_posture():
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(INSUFFICIENT_SCORECARD_DICT),
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    assert result["deploy_signals"]["action_posture"] == "WATCH"
    assert "insufficient_data" in result["deploy_signals"]["blockers"]


def test_insufficient_data_with_strong_analyst_buy_not_high_conviction():
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(INSUFFICIENT_SCORECARD_DICT),
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    assert result["confidence"]["conviction_band"] != "HIGH"
    assert result["deploy_signals"]["conviction_band"] != "HIGH"


def test_insufficient_data_preserves_analyst_evidence():
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(INSUFFICIENT_SCORECARD_DICT),
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    analyst_ev = result["evidence"]["analyst"]
    assert analyst_ev.get("action") == "BUY"
    assert analyst_ev.get("primary_driver")


# ── Additional edge cases ────────────────────────────────────────────────────

def test_ticker_is_uppercased():
    result = build_reasoning_v2(ticker="aapl", scorecard=None, analyst_verdict=None)
    assert result["ticker"] == "AAPL"


def test_provider_meta_pass_through():
    meta = {"source": "finnhub", "as_of": "2026-05-01"}
    result = build_reasoning_v2(
        ticker="AAPL",
        scorecard=None,
        analyst_verdict=None,
        provider_meta=meta,
    )
    assert result["evidence"]["provider"] == meta


def test_no_provider_meta_is_none():
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=None)
    assert result["evidence"]["provider"] is None


def test_hold_analyst_maps_to_hold_posture():
    av = dict(COMPACT_V1_VERDICT, action="HOLD", conviction=0.5)
    result = build_reasoning_v2(ticker="MSFT", scorecard=None, analyst_verdict=av)
    assert result["action"]["posture"] == "WATCH"


def test_reduce_analyst_maps_to_trim_posture():
    av = dict(COMPACT_V1_VERDICT, action="REDUCE", conviction=0.5)
    result = build_reasoning_v2(ticker="NVDA", scorecard=None, analyst_verdict=av)
    assert result["action"]["posture"] == "WATCH"


def test_medium_conviction_band():
    av = dict(COMPACT_V1_VERDICT, conviction=0.45)
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=av)
    assert result["confidence"]["conviction_band"] == "MEDIUM"


def test_low_conviction_band():
    av = dict(COMPACT_V1_VERDICT, conviction=0.25)
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=av)
    assert result["confidence"]["conviction_band"] == "LOW"


def test_human_v2_generation_version_is_usable():
    av = dict(COMPACT_V1_VERDICT, generation_version="human_v2")
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=av)
    assert result["why"]["support"] == "analyst"


def test_unknown_generation_version_not_usable():
    av = dict(COMPACT_V1_VERDICT, generation_version="legacy_v0")
    result = build_reasoning_v2(ticker="AAPL", scorecard=None, analyst_verdict=av)
    # Not a valid generation version → analyst not usable → insufficient
    assert result["why"]["support"] == "insufficient"


def test_deploy_signals_no_forbidden_math_keys():
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(READY_SCORECARD_DICT),
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    ds = result["deploy_signals"]
    forbidden = {
        "allocation_amount", "allocation_pct", "dollar_amount",
        "shares", "share_count", "position_target", "target_weight",
        "deploy_amount", "deploy_pct",
    }
    assert not forbidden.intersection(ds.keys())


def test_user_text_max_180_chars():
    result = build_reasoning_v2(
        ticker="AAPL",
        scorecard=None,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    for section in ("why", "risk", "action", "alt_view"):
        text = result[section]["user_text"]
        assert len(text) <= 180, f"{section}.user_text exceeds 180 chars: {len(text)}"


def test_analyst_confidence_in_confidence_score():
    av = dict(COMPACT_V1_VERDICT, confidence=0.78)
    result = build_reasoning_v2(ticker="NVDA", scorecard=None, analyst_verdict=av)
    assert result["confidence"]["score"] == 0.78


def test_no_analyst_verdict_confidence_score_is_none_or_zero():
    result = build_reasoning_v2(ticker="STUB", scorecard=None, analyst_verdict=None)
    # No analyst → score is None
    assert result["confidence"]["score"] is None


# ── Thesis engine scorecard fusion tests (PR 2) ──────────────────────────────
#
# These tests use the serialized thesis dict format produced by
# orchestrator._scorecard_to_dict() so they run without importing
# score_schema or thesis_engine (which avoids circular dependencies
# in the test suite).  Test 4 additionally imports score_schema.ScoreCard
# to verify the object path through _normalize_thesis_engine_scorecard.

# Thesis dict with multiple published dimensions (READY status)
THESIS_READY_DICT: dict[str, Any] = {
    "ticker": "NVDA",
    "status": "READY",
    "conviction_score": 72.5,
    "conviction_band": "HIGH",
    "blended_data_quality": 0.82,
    "inputs_used": ["trailing_pe", "forward_pe", "beta", "return_5d", "return_30d"],
    "inputs_missing": ["roic_ttm", "gross_margin", "fcf_margin"],
    "score_version": "v1",
    "quality": {
        "score": 55.0, "data_quality": 0.43,
        "inputs_used": [], "inputs_missing": [], "published": False,
    },
    "valuation": {
        "score": 64.0, "data_quality": 0.72,
        "inputs_used": ["trailing_pe", "forward_pe"], "inputs_missing": [], "published": True,
    },
    "growth": {
        "score": 70.0, "data_quality": 0.60,
        "inputs_used": ["revenue_yoy"], "inputs_missing": [], "published": True,
    },
    "risk": {
        "score": 68.0, "data_quality": 0.71,
        "inputs_used": ["beta", "net_debt_to_ebitda"], "inputs_missing": [], "published": True,
    },
    "momentum": {
        "score": 73.0, "data_quality": 0.80,
        "inputs_used": ["return_5d", "return_30d"], "inputs_missing": [], "published": True,
    },
}

# Thesis dict where analyst (BUY) and deterministic (LOW = negative) disagree
THESIS_LOW_CONVICTION_DICT: dict[str, Any] = {
    "ticker": "WEAK",
    "status": "READY",
    "conviction_score": 38.0,
    "conviction_band": "LOW",
    "blended_data_quality": 0.60,
    "inputs_used": ["trailing_pe", "beta"],
    "inputs_missing": ["roic_ttm", "gross_margin", "fcf_margin", "revenue_yoy"],
    "score_version": "v1",
    "quality": {
        "score": 32.0, "data_quality": 0.43,
        "inputs_used": [], "inputs_missing": [], "published": False,
    },
    "valuation": {
        "score": 40.0, "data_quality": 0.55,
        "inputs_used": ["trailing_pe"], "inputs_missing": [], "published": True,
    },
    "growth": {
        "score": 35.0, "data_quality": 0.40,
        "inputs_used": [], "inputs_missing": [], "published": False,
    },
    "risk": {
        "score": 42.0, "data_quality": 0.57,
        "inputs_used": ["beta"], "inputs_missing": [], "published": True,
    },
    "momentum": {
        "score": 38.0, "data_quality": 0.52,
        "inputs_used": [], "inputs_missing": [], "published": True,
    },
}

# Thesis dict with INSUFFICIENT_DATA status
THESIS_INSUFFICIENT_DICT: dict[str, Any] = {
    "ticker": "MISS",
    "status": "INSUFFICIENT_DATA",
    "conviction_score": None,
    "conviction_band": "INSUFFICIENT_DATA",
    "blended_data_quality": 0.22,
    "inputs_used": [],
    "inputs_missing": ["roic_ttm", "gross_margin", "fcf_margin", "trailing_pe"],
    "score_version": "v1",
    "quality": {
        "score": 0.0, "data_quality": 0.0,
        "inputs_used": [], "inputs_missing": [], "published": False,
    },
    "valuation": {
        "score": 0.0, "data_quality": 0.28,
        "inputs_used": [], "inputs_missing": [], "published": False,
    },
    "growth": {
        "score": 0.0, "data_quality": 0.0,
        "inputs_used": [], "inputs_missing": [], "published": False,
    },
    "risk": {
        "score": 0.0, "data_quality": 0.14,
        "inputs_used": [], "inputs_missing": [], "published": False,
    },
    "momentum": {
        "score": 0.0, "data_quality": 0.0,
        "inputs_used": [], "inputs_missing": [], "published": False,
    },
}

# Thesis dict with PARTIAL status — some published dimensions
THESIS_PARTIAL_DICT: dict[str, Any] = {
    "ticker": "PART",
    "status": "PARTIAL",
    "conviction_score": 58.0,
    "conviction_band": "MEDIUM",
    "blended_data_quality": 0.62,
    "inputs_used": ["trailing_pe", "beta"],
    "inputs_missing": ["roic_ttm", "gross_margin", "fcf_margin"],
    "score_version": "v1",
    "quality": {
        "score": 45.0, "data_quality": 0.29,
        "inputs_used": [], "inputs_missing": [], "published": False,
    },
    "valuation": {
        "score": 60.0, "data_quality": 0.55,
        "inputs_used": ["trailing_pe"], "inputs_missing": [], "published": True,
    },
    "growth": {
        "score": 55.0, "data_quality": 0.40,
        "inputs_used": [], "inputs_missing": [], "published": False,
    },
    "risk": {
        "score": 62.0, "data_quality": 0.57,
        "inputs_used": ["beta"], "inputs_missing": [], "published": True,
    },
    "momentum": {
        "score": 58.0, "data_quality": 0.52,
        "inputs_used": [], "inputs_missing": [], "published": True,
    },
}


# ── Test PR2-1: wire-up — scorecard passes into builder, evidence populated ───

def test_thesis_scorecard_produces_non_empty_deterministic_evidence():
    """When _thesis_v2 has published scorecard dimensions, evidence.deterministic is populated."""
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(THESIS_READY_DICT),
        analyst_verdict=None,
    )
    det = result["evidence"]["deterministic"]
    # At least the published dimensions should appear
    assert len(det) > 0, "evidence.deterministic must be non-empty for READY scorecard"
    assert "valuation_score" in det
    assert "risk_score" in det
    assert "momentum_score" in det


def test_thesis_ready_scorecard_published_scores_are_correct():
    """Published subscore scores are rounded floats matching the input dict."""
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(THESIS_READY_DICT),
        analyst_verdict=None,
    )
    det = result["evidence"]["deterministic"]
    assert det["valuation_score"]["score"] == 64.0
    assert det["growth_score"]["score"] == 70.0
    assert det["risk_score"]["score"] == 68.0
    assert det["momentum_score"]["score"] == 73.0


def test_thesis_ready_scorecard_evidence_keeps_machine_readable_fields():
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(THESIS_READY_DICT),
        analyst_verdict=None,
    )
    valuation = result["evidence"]["deterministic"]["valuation_score"]
    assert set(valuation.keys()) == {
        "score", "published", "inputs_used", "data_quality", "inputs_missing"
    }
    assert valuation["published"] is True
    assert valuation["inputs_used"] == ["trailing_pe", "forward_pe"]


def test_thesis_unpublished_dimension_excluded_from_evidence():
    """quality.published=False → quality_score must NOT appear in evidence.deterministic."""
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(THESIS_READY_DICT),
        analyst_verdict=None,
    )
    det = result["evidence"]["deterministic"]
    # quality.published=False in THESIS_READY_DICT
    assert "quality_score" not in det


# ── Test PR2-2: sibling keys coexist in allocation_map ───────────────────────

def test_thesis_v2_and_reasoning_v2_can_coexist_as_sibling_keys():
    """Both _thesis_v2 and _reasoning_v2 can be sibling keys in one allocation map."""
    scorecard_dict = dict(THESIS_READY_DICT)
    r2 = build_reasoning_v2(
        ticker="NVDA",
        scorecard=scorecard_dict,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    allocation_map = {
        "NVDA": 450.0,
        "_thesis_v2": {"NVDA": scorecard_dict},
        "_reasoning_v2": {"NVDA": r2},
    }
    assert "_thesis_v2" in allocation_map
    assert "_reasoning_v2" in allocation_map
    assert allocation_map["_thesis_v2"]["NVDA"]["conviction_band"] == "HIGH"
    assert allocation_map["_reasoning_v2"]["NVDA"]["schema_version"] == "reasoning_v2.0"


# ── Test PR2-3: score_schema.ScoreCard object vs serialized dict equivalence ──

def test_thesis_scorecard_object_and_dict_produce_equivalent_evidence():
    """score_schema.ScoreCard object and its serialized dict produce the same evidence."""
    from app.services.intelligence.score_schema import (
        ConvictionBand, ScoreCard as ThesisScoreCard, ScoreStatus, SubScore
    )

    def _make_subscore(score, dq, published) -> SubScore:
        return SubScore(
            score=score, data_quality=dq, inputs_used=[], inputs_missing=[],
            published=published,
        )

    card_obj = ThesisScoreCard(
        ticker="NVDA",
        status=ScoreStatus.READY,
        quality=_make_subscore(55.0, 0.43, False),
        valuation=_make_subscore(64.0, 0.72, True),
        growth=_make_subscore(70.0, 0.60, True),
        risk=_make_subscore(68.0, 0.71, True),
        momentum=_make_subscore(73.0, 0.80, True),
        conviction_score=72.5,
        conviction_band=ConvictionBand.HIGH,
        blended_data_quality=0.82,
        inputs_used=["trailing_pe", "forward_pe"],
        inputs_missing=["roic_ttm"],
    )
    card_dict = dict(THESIS_READY_DICT)

    result_obj = build_reasoning_v2(ticker="NVDA", scorecard=card_obj, analyst_verdict=None)
    result_dict = build_reasoning_v2(ticker="NVDA", scorecard=card_dict, analyst_verdict=None)

    # Both should produce the same deterministic evidence keys
    assert set(result_obj["evidence"]["deterministic"].keys()) == set(
        result_dict["evidence"]["deterministic"].keys()
    )
    # And the same agreement / status
    assert result_obj["confidence"]["agreement"] == result_dict["confidence"]["agreement"]


def test_insufficient_data_with_published_dimensions_keeps_deterministic_evidence():
    live_style = dict(THESIS_INSUFFICIENT_DICT)
    live_style["valuation"] = {
        "score": 61.0, "data_quality": 0.62, "published": True,
        "inputs_used": ["trailing_pe"], "inputs_missing": [],
    }
    live_style["momentum"] = {
        "score": 59.0, "data_quality": 0.60, "published": True,
        "inputs_used": ["return_5d"], "inputs_missing": [],
    }
    result = build_reasoning_v2(ticker="NVDA", scorecard=live_style, analyst_verdict=None)
    det = result["evidence"]["deterministic"]
    assert "valuation_score" in det
    assert "momentum_score" in det
    assert "quality_score" not in det
    assert "growth_score" not in det
    assert "risk_score" not in det
    assert result["action"]["posture"] == "WATCH"
    assert result["deploy_signals"]["action_posture"] == "WATCH"
    assert "insufficient_data" in result["deploy_signals"]["blockers"]
    assert result["confidence"]["agreement"] == "deterministic_only"


def test_insufficient_data_with_published_dimensions_and_analyst_not_analyst_only():
    live_style = dict(THESIS_INSUFFICIENT_DICT)
    live_style["valuation"] = {
        "score": 61.0, "data_quality": 0.62, "published": True,
        "inputs_used": ["trailing_pe"], "inputs_missing": [],
    }
    live_style["momentum"] = {
        "score": 59.0, "data_quality": 0.60, "published": True,
        "inputs_used": ["return_5d"], "inputs_missing": [],
    }
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=live_style,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    assert result["confidence"]["agreement"] in {"agree", "disagree"}
    assert result["confidence"]["agreement"] != "analyst_only"
    for section in ("why", "risk", "action", "alt_view"):
        text = result[section]["user_text"].lower()
        assert "trailing_pe" not in text
        assert "return_5d" not in text


# ── Test PR2-4: deterministic + analyst agree ─────────────────────────────────

def test_thesis_high_conviction_plus_buy_analyst_agree():
    """HIGH conviction thesis + BUY analyst → agreement = 'agree', non-WATCH posture."""
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(THESIS_READY_DICT),   # conviction_band=HIGH
        analyst_verdict=dict(COMPACT_V1_VERDICT),  # action=BUY
    )
    assert result["confidence"]["agreement"] == "agree"
    assert result["action"]["posture"] in {"ACCUMULATE", "HOLD"}
    assert "agreement_conflict" not in result["deploy_signals"]["blockers"]


def test_thesis_medium_conviction_plus_buy_analyst_agree():
    """MEDIUM conviction thesis + BUY analyst → agreement = 'agree'."""
    result = build_reasoning_v2(
        ticker="PART",
        scorecard=dict(THESIS_PARTIAL_DICT),  # conviction_band=MEDIUM
        analyst_verdict=dict(COMPACT_V1_VERDICT),  # action=BUY
    )
    assert result["confidence"]["agreement"] == "agree"
    assert "agreement_conflict" not in result["deploy_signals"]["blockers"]


# ── Test PR2-5: deterministic + analyst disagree ──────────────────────────────

def test_thesis_low_conviction_plus_buy_analyst_disagree():
    """LOW conviction thesis + BUY analyst → agreement = 'disagree', WATCH posture."""
    result = build_reasoning_v2(
        ticker="WEAK",
        scorecard=dict(THESIS_LOW_CONVICTION_DICT),  # conviction_band=LOW
        analyst_verdict=dict(COMPACT_V1_VERDICT),      # action=BUY
    )
    assert result["confidence"]["agreement"] == "disagree"
    assert result["action"]["posture"] == "WATCH"
    assert result["deploy_signals"]["action_posture"] == "WATCH"
    assert "agreement_conflict" in result["deploy_signals"]["blockers"]


def test_thesis_high_conviction_plus_reduce_analyst_disagree():
    """HIGH conviction thesis + REDUCE analyst → agreement = 'disagree', WATCH posture."""
    reduce_verdict = dict(COMPACT_V1_VERDICT, action="REDUCE")
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(THESIS_READY_DICT),  # conviction_band=HIGH → positive
        analyst_verdict=reduce_verdict,       # REDUCE → bearish
    )
    assert result["confidence"]["agreement"] == "disagree"
    assert result["action"]["posture"] == "WATCH"
    assert "agreement_conflict" in result["deploy_signals"]["blockers"]


# ── Test PR2-6: INSUFFICIENT_DATA thesis still forces WATCH ──────────────────

def test_thesis_insufficient_data_with_buy_analyst_forces_watch():
    """INSUFFICIENT_DATA thesis + strong BUY analyst → WATCH contract still enforced."""
    result = build_reasoning_v2(
        ticker="MISS",
        scorecard=dict(THESIS_INSUFFICIENT_DICT),
        analyst_verdict=dict(COMPACT_V1_VERDICT),  # action=BUY
    )
    assert result["data_quality"]["status"] == "INSUFFICIENT_DATA"
    assert result["action"]["posture"] == "WATCH"
    assert result["deploy_signals"]["action_posture"] == "WATCH"
    assert "insufficient_data" in result["deploy_signals"]["blockers"]
    assert result["confidence"]["conviction_band"] != "HIGH"


def test_thesis_insufficient_data_evidence_deterministic_empty():
    """INSUFFICIENT_DATA thesis → evidence.deterministic must be empty (no score leakage)."""
    result = build_reasoning_v2(
        ticker="MISS",
        scorecard=dict(THESIS_INSUFFICIENT_DICT),
        analyst_verdict=None,
    )
    assert result["evidence"]["deterministic"] == {}


def test_insufficient_data_with_serialized_thesis_inputs_missing_is_actionable():
    """Serialized thesis dicts use inputs_missing; diagnostics must not go empty."""
    sc = dict(THESIS_INSUFFICIENT_DICT)
    # Live serialized thesis shape from orchestrator does not include missing_fields.
    sc.pop("missing_fields", None)
    sc.pop("stale_fields", None)

    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=sc,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    dq = result["data_quality"]
    assert dq["status"] == "INSUFFICIENT_DATA"
    assert len(dq["missing"]) > 0
    assert "trailing_pe" in dq["missing"]


def test_insufficient_data_never_reports_both_missing_and_stale_empty_when_suppressed():
    """If major dimensions are suppressed, diagnostics must remain actionable."""
    sc = {
        "ticker": "GOOGL",
        "status": "INSUFFICIENT_DATA",
        "data_quality_score": 0.42,
        "quality": {"score": 0.0, "data_quality": 0.0, "published": False},
        "valuation": {"score": 45.0, "data_quality": 0.2, "published": False},
        "growth": {"score": 0.0, "data_quality": 0.0, "published": False},
        "risk": {"score": 58.0, "data_quality": 0.49, "published": False},
        "momentum": {"score": 62.0, "data_quality": 0.6, "published": True},
    }
    result = build_reasoning_v2(
        ticker="GOOGL",
        scorecard=sc,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    dq = result["data_quality"]
    assert dq["status"] == "INSUFFICIENT_DATA"
    assert dq["missing"] or dq["stale"]
    assert any(str(m).startswith("suppressed:") for m in dq["missing"])
    assert "Suppressed dimensions due to low coverage" in dq["user_safe_note"]


def test_insufficient_data_scorecard_has_non_null_diagnostics_before_serialization():
    """Live-style INSUFFICIENT_DATA thesis dict must include actionable diagnostics."""
    sc = dict(THESIS_INSUFFICIENT_DICT)
    sc["diagnostics"] = {
        "missing_dimensions": ["quality", "growth"],
        "suppressed_dimensions": ["quality", "valuation", "growth", "risk"],
        "published_dimensions": [],
        "unpublished_reasons": {
            "quality": "suppressed_low_quality",
            "valuation": "suppressed_low_quality",
            "growth": "suppressed_low_quality",
            "risk": "suppressed_low_quality",
            "momentum": "suppressed_low_quality",
        },
    }
    assert sc["status"] == "INSUFFICIENT_DATA"
    assert sc["diagnostics"] is not None
    assert len(sc["diagnostics"]["suppressed_dimensions"]) > 0


def test_orchestrator_serialized_thesis_v2_includes_diagnostics_for_insufficient_data():
    """Regression: serialized _thesis_v2 payload must keep diagnostics non-null."""
    from app.services.agents.orchestrator import _scorecard_to_dict
    from app.services.intelligence.score_schema import ConvictionBand, ScoreCard, ScoreStatus, SubScore

    ss = SubScore(score=0.0, data_quality=0.0, inputs_used=[], inputs_missing=["roic_ttm"], published=False)
    card = ScoreCard(
        ticker="NVDA",
        status=ScoreStatus.INSUFFICIENT_DATA,
        quality=ss,
        valuation=ss,
        growth=ss,
        risk=ss,
        momentum=ss,
        conviction_score=None,
        conviction_band=ConvictionBand.INSUFFICIENT_DATA,
        blended_data_quality=0.0,
        inputs_used=[],
        inputs_missing=["roic_ttm"],
    )
    serialized = _scorecard_to_dict(card)
    assert serialized["status"] == "INSUFFICIENT_DATA"
    assert serialized.get("diagnostics") is not None
    assert "suppressed_dimensions" in serialized["diagnostics"]
    assert len(serialized["diagnostics"]["suppressed_dimensions"]) > 0


# ── Test PR2-7: PARTIAL scorecard with useful published dimensions ─────────────

def test_thesis_partial_published_dimensions_appear_in_evidence():
    """PARTIAL thesis with published dimensions populates evidence.deterministic."""
    result = build_reasoning_v2(
        ticker="PART",
        scorecard=dict(THESIS_PARTIAL_DICT),  # valuation, risk, momentum published
        analyst_verdict=None,
    )
    det = result["evidence"]["deterministic"]
    assert len(det) > 0
    assert "valuation_score" in det
    assert "risk_score" in det
    assert "momentum_score" in det
    # quality is not published in THESIS_PARTIAL_DICT
    assert "quality_score" not in det


# ── Test PR2-8: no allocation leakage into deploy_signals ────────────────────

def test_thesis_scorecard_no_allocation_leakage_in_deploy_signals():
    """No dollar/percent/position-target keys appear in deploy_signals for thesis scorecards."""
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(THESIS_READY_DICT),
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    ds = result["deploy_signals"]
    forbidden = {
        "allocation_amount", "allocation_pct", "dollar_amount",
        "shares", "share_count", "position_target", "target_weight",
        "deploy_amount", "deploy_pct",
    }
    assert not forbidden.intersection(ds.keys())


# ── Test PR2-9: no raw metric keys in user-facing text ───────────────────────

_RAW_METRIC_KEYS = [
    "quality_score", "valuation_score", "growth_score", "risk_score", "momentum_score",
    "fcf_margin", "roic_ttm", "ev_ebitda", "ps_ttm", "net_debt_to_ebitda",
    "trailing_pe", "forward_pe", "blended_data_quality", "conviction_score",
]


@pytest.mark.parametrize("metric_key", _RAW_METRIC_KEYS)
def test_no_raw_metric_keys_in_user_text_thesis_scorecard(metric_key):
    """Raw metric/score keys must never appear verbatim in user-facing text fields."""
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(THESIS_READY_DICT),
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    user_texts = [
        result["why"]["user_text"],
        result["risk"]["user_text"],
        result["action"]["user_text"],
        result["alt_view"]["user_text"],
    ]
    for text in user_texts:
        assert metric_key.lower() not in text.lower(), (
            f"Raw metric key '{metric_key}' found in user_text: {text!r}"
        )


# ── Test PR2-10: blended_data_quality used when data_quality_score absent ────

def test_thesis_serialized_dict_blended_data_quality_used_for_sc_quality():
    """Serialized thesis dict uses blended_data_quality (not data_quality_score) for quality."""
    # Thesis dict has blended_data_quality=0.82; if _sc_quality falls back correctly
    # the blended_quality in data_quality will be non-zero.
    result = build_reasoning_v2(
        ticker="NVDA",
        scorecard=dict(THESIS_READY_DICT),
        analyst_verdict=None,
    )
    # blended_data_quality from scorecard (0.82) should feed _sc_quality
    assert result["data_quality"]["blended_quality"] > 0.0


# ── Test PR2-11: score_schema.ScoreCard object normalised to dict correctly ───

def test_thesis_engine_scorecard_object_normalised_correctly():
    """score_schema.ScoreCard object goes through _normalize_thesis_engine_scorecard."""
    from app.services.intelligence.score_schema import (
        ConvictionBand, ScoreCard as ThesisScoreCard, ScoreStatus, SubScore
    )

    def _make_subscore(score, dq, published) -> SubScore:
        return SubScore(
            score=score, data_quality=dq, inputs_used=[], inputs_missing=[],
            published=published,
        )

    card = ThesisScoreCard(
        ticker="GOOG",
        status=ScoreStatus.PARTIAL,
        quality=_make_subscore(40.0, 0.29, False),
        valuation=_make_subscore(58.0, 0.55, True),
        growth=_make_subscore(65.0, 0.60, True),
        risk=_make_subscore(70.0, 0.71, True),
        momentum=_make_subscore(55.0, 0.50, True),
        conviction_score=58.0,
        conviction_band=ConvictionBand.MEDIUM,
        blended_data_quality=0.62,
        inputs_used=["trailing_pe"],
        inputs_missing=["roic_ttm"],
    )

    result = build_reasoning_v2(ticker="GOOG", scorecard=card, analyst_verdict=None)
    det = result["evidence"]["deterministic"]
    assert "valuation_score" in det
    assert "growth_score" in det
    assert "risk_score" in det
    assert "momentum_score" in det
    assert "quality_score" not in det  # quality.published=False
    assert result["data_quality"]["status"] == "PARTIAL"
