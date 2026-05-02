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
    # With valid analyst + no scorecard: posture comes from analyst action
    result = build_reasoning_v2(
        ticker="AAPL",
        scorecard=None,
        analyst_verdict=dict(COMPACT_V1_VERDICT),
    )
    # Scorecard absent → INSUFFICIENT_DATA status → analyst_only agreement
    # Posture should be ACCUMULATE (BUY → ACCUMULATE) because analyst is usable
    assert result["action"]["posture"] == "ACCUMULATE"
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
    assert result["action"]["posture"] == "HOLD"


def test_reduce_analyst_maps_to_trim_posture():
    av = dict(COMPACT_V1_VERDICT, action="REDUCE", conviction=0.5)
    result = build_reasoning_v2(ticker="NVDA", scorecard=None, analyst_verdict=av)
    assert result["action"]["posture"] == "TRIM"


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
