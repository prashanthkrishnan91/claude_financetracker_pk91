"""Phase 3 — per-ticker analyst acceptance tests.

Gates covered here (tasks/todo.md → Phase 3 acceptance):
  1. No empty {} responses reach the pipeline.
  2. At least 3 different actions across a mixed portfolio.
  3. No identical reasoning across all tickers.
  4. Every ticker has an entry in the verdict map.
  5. Failure rate < 10% when the LLM is healthy (retries + INSUFFICIENT_DATA
     fallback keep the rate bounded).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.intelligence.feature_engine import FeatureSet
from app.services.intelligence.market_snapshot import MarketSnapshot
from app.services.intelligence.per_ticker_analyst import (
    ALLOWED_ACTIONS,
    AnalystVerdict,
    action_to_suggested_action,
    analyze_portfolio,
    analyze_ticker,
    build_analyst_inputs,
    format_thesis,
    insufficient_data_verdict,
    validate_verdict,
)


# ── Validation unit tests ───────────────────────────────────────────────────


def test_validate_verdict_happy_path():
    raw = {
        "action": "BUY",
        "conviction": 0.65,
        "key_drivers": ["earnings beat", "momentum up"],
        "risks": ["macro slowdown"],
        "confidence": 0.7,
    }
    v = validate_verdict(raw, ticker="AAPL")
    assert v is not None
    assert v.action == "BUY"
    assert v.conviction == 0.65
    assert v.key_drivers == ["earnings beat", "momentum up"]
    assert v.risks == ["macro slowdown"]
    assert v.confidence == 0.7
    assert v.used_fallback is False


def test_validate_verdict_rejects_unknown_action():
    raw = {"action": "STRONG_BUY", "conviction": 0.9, "confidence": 0.8}
    assert validate_verdict(raw, ticker="AAPL") is None


def test_validate_verdict_rejects_non_dict():
    assert validate_verdict("nope", ticker="AAPL") is None
    assert validate_verdict(None, ticker="AAPL") is None
    assert validate_verdict([], ticker="AAPL") is None


def test_validate_verdict_clamps_conviction_and_confidence():
    raw = {"action": "BUY", "conviction": 1.5, "confidence": -0.3}
    v = validate_verdict(raw, ticker="X")
    assert v is not None
    assert v.conviction == 1.0
    assert v.confidence == 0.0


def test_validate_verdict_truncates_lists():
    raw = {
        "action": "HOLD",
        "conviction": 0.2,
        "key_drivers": ["a", "b", "c", "d", "e"],
        "risks": ["r1", "r2", "r3"],
        "confidence": 0.3,
    }
    v = validate_verdict(raw, ticker="X")
    assert len(v.key_drivers) == 3
    assert len(v.risks) == 2


def test_validate_verdict_insufficient_data_zeroes_conviction():
    raw = {"action": "INSUFFICIENT_DATA", "conviction": 0.7, "confidence": 0.5}
    v = validate_verdict(raw, ticker="X")
    assert v.action == "INSUFFICIENT_DATA"
    assert v.conviction == 0.0


def test_allowed_actions_matches_spec():
    assert ALLOWED_ACTIONS == {"BUY", "HOLD", "REDUCE", "INSUFFICIENT_DATA"}


def test_action_to_suggested_action_mapping():
    assert action_to_suggested_action("BUY") == "BUY"
    assert action_to_suggested_action("REDUCE") == "TRIM"
    assert action_to_suggested_action("HOLD") == "HOLD"
    assert action_to_suggested_action("INSUFFICIENT_DATA") == "HOLD"


def test_format_thesis_cites_drivers_and_risks():
    v = AnalystVerdict(
        ticker="AAPL", action="BUY", conviction=0.7,
        key_drivers=["earnings beat", "sector rotation"],
        risks=["macro headwinds"],
        confidence=0.7,
    )
    thesis = format_thesis(v)
    assert "BUY" in thesis
    assert "earnings beat" in thesis
    assert "macro headwinds" in thesis


# ── Snapshots + features fixtures ─────────────────────────────────────────


def _snap(ticker, **overrides) -> MarketSnapshot:
    defaults = dict(
        ticker=ticker,
        as_of="2026-04-23T00:00:00+00:00",
        price=100.0,
        price_source="live",
        return_5d=1.0,
        return_30d=5.0,
        volatility_30d=0.25,
        sector="Technology",
        industry="Software",
        category="Tech",
        fundamentals={"pe": 28},
        sentiment_label="neutral",
        sentiment_score=0.0,
        news_count=2,
        recent_headlines=["h1", "h2"],
        data_quality_score=0.8,
        missing_fields=[],
    )
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


def _feat(ticker, **overrides) -> FeatureSet:
    defaults = dict(
        ticker=ticker,
        as_of="2026-04-23T00:00:00+00:00",
        trend_regime="uptrend",
        momentum_score=0.4,
        volatility_regime="medium",
        relative_strength_30d=2.0,
        relative_strength_label="inline",
        benchmark_symbol="SPY",
        benchmark_return_30d=4.0,
        sector="Technology",
        industry="Software",
        category="Tech",
        data_quality_score=0.8,
        price=100.0,
        sma20=95.0,
        sma50=90.0,
        return_5d=1.0,
        return_30d=5.0,
        volatility_30d=0.25,
    )
    defaults.update(overrides)
    return FeatureSet(**defaults)


# ── Analyst LLM integration with fake client ──────────────────────────────


class FakeLLM:
    """Test double that returns canned responses per call."""

    def __init__(self, responses):
        # responses: list of dicts or exceptions to return in order
        self.responses = list(responses)
        self.api_key = "fake-key"
        self.calls = []

    async def ask_json(self, *, system, user, max_tokens=1024):
        self.calls.append((system[:40], user[:40], max_tokens))
        if not self.responses:
            return {}
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.mark.asyncio
async def test_analyze_ticker_happy_path():
    llm = FakeLLM([
        {
            "action": "BUY",
            "conviction": 0.6,
            "key_drivers": ["earnings", "momentum"],
            "risks": ["rates"],
            "confidence": 0.7,
        }
    ])
    v = await analyze_ticker(
        snapshot=_snap("AAPL"),
        feature_set=_feat("AAPL"),
        llm=llm,
    )
    assert v.action == "BUY"
    assert v.conviction == 0.6
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_analyze_ticker_retries_on_invalid_and_succeeds():
    llm = FakeLLM([
        {"action": "WHATEVER"},  # invalid
        {
            "action": "HOLD",
            "conviction": 0.2,
            "key_drivers": ["thin signal"],
            "risks": ["data gaps"],
            "confidence": 0.4,
        },
    ])
    v = await analyze_ticker(
        snapshot=_snap("AAPL"),
        feature_set=_feat("AAPL"),
        llm=llm,
    )
    assert v.action == "HOLD"
    assert len(llm.calls) == 2
    assert v.used_fallback is False


@pytest.mark.asyncio
async def test_analyze_ticker_falls_back_on_two_failures():
    """Gate #1 — never return {}, always a validated verdict."""
    llm = FakeLLM([{"action": "nope"}, {"conviction": "garbage"}])
    v = await analyze_ticker(
        snapshot=_snap("AAPL"),
        feature_set=_feat("AAPL"),
        llm=llm,
    )
    assert v.action == "INSUFFICIENT_DATA"
    assert v.conviction == 0.0
    assert v.used_fallback is True
    # The spec says "retry once on invalid JSON" — exactly 2 calls total.
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_analyze_ticker_bypasses_llm_when_quality_low():
    """Thin-data guard skips the LLM entirely."""
    llm = FakeLLM([])  # would be empty anyway — asserts no call
    snap = _snap("NVDA", data_quality_score=0.1)
    v = await analyze_ticker(
        snapshot=snap, feature_set=_feat("NVDA"), llm=llm,
    )
    assert v.action == "INSUFFICIENT_DATA"
    assert v.used_fallback is True
    assert len(llm.calls) == 0


@pytest.mark.asyncio
async def test_analyze_ticker_swallows_llm_exceptions():
    llm = FakeLLM([RuntimeError("boom"), RuntimeError("boom again")])
    v = await analyze_ticker(
        snapshot=_snap("AAPL"),
        feature_set=_feat("AAPL"),
        llm=llm,
    )
    assert v.action == "INSUFFICIENT_DATA"
    assert v.used_fallback is True


# ── Portfolio-wide parallel analyst ────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_portfolio_every_ticker_has_a_verdict():
    """Gate #4 — every input ticker appears in the output."""
    llm = FakeLLM([
        {"action": "BUY", "conviction": 0.7, "key_drivers": ["d1"],
         "risks": ["r1"], "confidence": 0.8},
        {"action": "HOLD", "conviction": 0.2, "key_drivers": ["range"],
         "risks": ["noise"], "confidence": 0.5},
        {"action": "REDUCE", "conviction": 0.6, "key_drivers": ["breakdown"],
         "risks": ["volatility"], "confidence": 0.6},
    ])
    snapshots = {
        "AAPL": _snap("AAPL"),
        "TSLA": _snap("TSLA", return_30d=-10, volatility_30d=0.55),
        "BTC":  _snap("BTC",  return_30d=25, volatility_30d=0.80),
    }
    features = {
        "AAPL": _feat("AAPL"),
        "TSLA": _feat("TSLA", trend_regime="downtrend", momentum_score=-0.5),
        "BTC":  _feat("BTC",  trend_regime="uptrend", momentum_score=0.9,
                      volatility_regime="high"),
    }
    verdicts = await analyze_portfolio(
        snapshots=snapshots, features=features, llm=llm, max_concurrency=3,
    )
    assert set(verdicts.keys()) == {"AAPL", "TSLA", "BTC"}
    # Gate #2 — three distinct actions.
    assert {v.action for v in verdicts.values()} == {"BUY", "HOLD", "REDUCE"}
    # Gate #3 — drivers differ.
    all_drivers = {tuple(v.key_drivers) for v in verdicts.values()}
    assert len(all_drivers) == 3


@pytest.mark.asyncio
async def test_analyze_portfolio_missing_feature_set_returns_insufficient_data():
    llm = FakeLLM([
        {"action": "BUY", "conviction": 0.7, "key_drivers": ["d1"],
         "risks": ["r1"], "confidence": 0.8},
    ])
    snapshots = {"AAPL": _snap("AAPL"), "GHOST": _snap("GHOST")}
    features = {"AAPL": _feat("AAPL")}  # no feature set for GHOST
    verdicts = await analyze_portfolio(
        snapshots=snapshots, features=features, llm=llm,
    )
    assert verdicts["GHOST"].action == "INSUFFICIENT_DATA"
    assert verdicts["GHOST"].used_fallback is True


@pytest.mark.asyncio
async def test_analyze_portfolio_failure_rate_under_threshold():
    """Gate #5 — with one bad ticker among many, failure rate stays below 10%."""
    # 11 tickers — 1 receives malformed responses twice, others succeed.
    good = {
        "action": "HOLD", "conviction": 0.2, "key_drivers": ["d"],
        "risks": ["r"], "confidence": 0.4,
    }
    bad = {"action": "WHO_KNOWS"}
    # For parallel gather, order isn't guaranteed to match the dict
    # iteration, so supply enough of each. 10 good responses + 2 bad to
    # guarantee one ticker hits both bad responses in the retry path.
    responses = [bad, bad] + [good] * 20
    llm = FakeLLM(responses)

    snapshots = {f"T{i}": _snap(f"T{i}") for i in range(11)}
    features = {f"T{i}": _feat(f"T{i}") for i in range(11)}

    verdicts = await analyze_portfolio(
        snapshots=snapshots, features=features, llm=llm, max_concurrency=5,
    )
    fallback_rate = sum(1 for v in verdicts.values() if v.used_fallback) / len(verdicts)
    # 1 forced fallback in 11 → ~9.1%. Assert under the spec's 10% threshold
    # — the test is deterministic because only one ticker sees the two
    # consecutive ``bad`` responses at the head of the FakeLLM queue.
    assert fallback_rate < 0.10, fallback_rate
    assert len(verdicts) == 11


# ── Input construction ─────────────────────────────────────────────────────


def test_build_analyst_inputs_omits_bulky_fields():
    snap = _snap(
        "AAPL",
        recent_headlines=["h1", "h2", "h3", "h4", "h5"],
        fundamentals={"pe": 28, "forward_pe": 25},
    )
    payload = build_analyst_inputs(snapshot=snap, feature_set=_feat("AAPL"))
    # Top-3 headlines only
    assert len(payload["snapshot"]["recent_headlines"]) == 3
    # Compact fundamentals pass through
    assert payload["snapshot"]["fundamentals"] == {"pe": 28, "forward_pe": 25}
    assert "ticker" in payload
    assert "features" in payload


# ── Orchestrator wiring ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_applies_verdicts_to_insights(monkeypatch):
    """End-to-end: verdicts project into TickerInsight fields correctly."""
    from app.services.agents import orchestrator as orch_mod
    from app.services.agents.state import AgentState, TickerInsight

    mock_db = MagicMock()
    monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)

    orch = orch_mod.AgentOrchestrator(user_id=uuid4())
    state = AgentState(
        user_id="u1", run_id="r1", tickers=["AAPL", "TSLA", "VOO"],
        deposit_amount=900.0,
    )
    state.insights = {
        "AAPL": TickerInsight(ticker="AAPL", suggested_action="HOLD"),
        "TSLA": TickerInsight(ticker="TSLA", suggested_action="HOLD"),
        "VOO":  TickerInsight(ticker="VOO",  suggested_action="HOLD"),
    }
    verdicts = {
        "AAPL": AnalystVerdict(ticker="AAPL", action="BUY", conviction=0.7,
                               key_drivers=["earnings"], risks=["rates"],
                               confidence=0.7),
        "TSLA": AnalystVerdict(ticker="TSLA", action="REDUCE", conviction=0.5,
                               key_drivers=["breakdown"], risks=["vol"],
                               confidence=0.6),
        "VOO":  AnalystVerdict(ticker="VOO", action="HOLD", conviction=0.0,
                               key_drivers=["benchmark"], risks=[],
                               confidence=0.5),
    }
    orch._apply_verdicts_to_insights(state, verdicts)

    assert state.insights["AAPL"].suggested_action == "BUY"
    assert state.insights["AAPL"].conviction_score == 0.7
    assert "earnings" in state.insights["AAPL"].investment_thesis

    assert state.insights["TSLA"].suggested_action == "TRIM"  # REDUCE → TRIM
    assert state.insights["TSLA"].conviction_score == -0.5

    assert state.insights["VOO"].suggested_action == "HOLD"
    assert state.insights["VOO"].conviction_score == 0.0
    # BUY ticker receives the cash allocation; REDUCE/HOLD get $0.
    assert state.insights["AAPL"].suggested_allocation == 900.0
    assert state.insights["TSLA"].suggested_allocation == 0.0
    assert state.insights["VOO"].suggested_allocation == 0.0
