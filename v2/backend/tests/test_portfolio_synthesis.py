"""Phase 4 — portfolio synthesis acceptance tests.

Gates covered here (tasks/todo.md → Phase 4 acceptance):
  1. Output includes ≥2 cross-ticker themes.
  2. Identifies ≥1 risk concentration.
  3. Differs from per-ticker outputs (true synthesis, not rehash).
  4. Does not fail even when some tickers are INSUFFICIENT_DATA.
"""

from __future__ import annotations

import pytest

from app.services.intelligence.feature_engine import FeatureSet
from app.services.intelligence.market_snapshot import MarketSnapshot
from app.services.intelligence.per_ticker_analyst import AnalystVerdict
from app.services.intelligence.portfolio_synthesis import (
    ALLOWED_BIASES,
    PortfolioSynthesis,
    build_synthesis_inputs,
    deterministic_synthesis,
    synthesize_portfolio,
    validate_synthesis,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


def _snap(ticker, sector="Technology", category="Tech", **kw) -> MarketSnapshot:
    defaults = dict(
        ticker=ticker,
        as_of="2026-04-23T00:00:00+00:00",
        price=100.0,
        price_source="live",
        return_5d=1.0,
        return_30d=5.0,
        volatility_30d=0.25,
        sector=sector,
        industry="",
        category=category,
        data_quality_score=0.8,
        missing_fields=[],
    )
    defaults.update(kw)
    return MarketSnapshot(**defaults)


def _feat(ticker, **kw) -> FeatureSet:
    defaults = dict(
        ticker=ticker,
        as_of="2026-04-23T00:00:00+00:00",
        trend_regime="uptrend",
        momentum_score=0.4,
        volatility_regime="medium",
        relative_strength_30d=2.0,
        relative_strength_label="inline",
        benchmark_symbol="SPY",
        sector="Technology",
        category="Tech",
        data_quality_score=0.8,
    )
    defaults.update(kw)
    return FeatureSet(**defaults)


def _verdict(ticker, action="HOLD", conviction=0.0, sector="Technology",
             drivers=None, risks=None, confidence=0.5) -> AnalystVerdict:
    return AnalystVerdict(
        ticker=ticker,
        action=action,
        conviction=conviction,
        key_drivers=drivers or [f"{ticker} driver"],
        risks=risks or [f"{ticker} risk"],
        confidence=confidence,
    )


def _mixed_portfolio():
    """Helper — 5-ticker portfolio with mixed actions + tech concentration."""
    snapshots = {
        "AAPL": _snap("AAPL", sector="Technology"),
        "MSFT": _snap("MSFT", sector="Technology"),
        "NVDA": _snap("NVDA", sector="Technology"),
        "TSLA": _snap("TSLA", sector="Consumer Cyclical", category="Auto"),
        "BTC":  _snap("BTC",  sector="Crypto", category="Crypto",
                      data_quality_score=0.2),
    }
    features = {
        "AAPL": _feat("AAPL", sector="Technology"),
        "MSFT": _feat("MSFT", sector="Technology"),
        "NVDA": _feat("NVDA", sector="Technology"),
        "TSLA": _feat("TSLA", sector="Consumer Cyclical",
                      trend_regime="downtrend", momentum_score=-0.5),
        "BTC":  _feat("BTC", sector="Crypto", volatility_regime="high"),
    }
    verdicts = {
        "AAPL": _verdict("AAPL", action="BUY", conviction=0.6,
                         sector="Technology", drivers=["earnings beat"]),
        "MSFT": _verdict("MSFT", action="BUY", conviction=0.5,
                         sector="Technology", drivers=["cloud growth"]),
        "NVDA": _verdict("NVDA", action="HOLD", conviction=0.1,
                         sector="Technology"),
        "TSLA": _verdict("TSLA", action="REDUCE", conviction=0.6,
                         sector="Consumer Cyclical", drivers=["demand weak"]),
        "BTC":  _verdict("BTC",  action="INSUFFICIENT_DATA", conviction=0.0),
    }
    positions = [
        {"ticker": "AAPL", "shares": 100, "avg_cost": 140,  "category": "Tech"},
        {"ticker": "MSFT", "shares":  50, "avg_cost": 300,  "category": "Tech"},
        {"ticker": "NVDA", "shares":  40, "avg_cost": 400,  "category": "Tech"},
        {"ticker": "TSLA", "shares":  30, "avg_cost": 280,  "category": "Auto"},
        {"ticker": "BTC",  "shares":   1, "avg_cost": 50000,"category": "Crypto"},
    ]
    return snapshots, features, verdicts, positions


# ── Input builder ──────────────────────────────────────────────────────────


def test_build_synthesis_inputs_shape():
    snaps, feats, verds, poss = _mixed_portfolio()
    payload = build_synthesis_inputs(
        verdicts=verds, snapshots=snaps, features=feats,
        positions=poss, macro={"summary": "m", "regime": "mid-cycle"},
    )
    comp = payload["portfolio_composition"]
    assert "total_value" in comp
    assert comp["ticker_count"] == 5
    assert "Technology" in comp["sector_exposure"]
    assert len(comp["top_positions"]) <= 5
    assert payload["action_summary"]["BUY"] == 2
    assert payload["action_summary"]["REDUCE"] == 1
    assert payload["action_summary"]["INSUFFICIENT_DATA"] == 1
    assert payload["data_quality"]["insufficient_data_tickers"] == ["BTC"]


# ── Validator ──────────────────────────────────────────────────────────────


def test_validate_synthesis_happy_path():
    raw = {
        "portfolio_bias": "bullish",
        "key_themes": ["theme 1", "theme 2"],
        "risk_concentrations": ["risk 1"],
        "overexposure_flags": [],
        "rebalancing_suggestions": ["reallocate X to Y"],
        "summary": "Book tilts bullish.",
    }
    s = validate_synthesis(raw)
    assert s is not None
    assert s.portfolio_bias == "bullish"
    assert s.has_required_signal()


def test_validate_synthesis_unknown_bias_defaults_to_neutral():
    raw = {"portfolio_bias": "super-bull", "key_themes": ["t1", "t2"],
           "risk_concentrations": ["r1"]}
    s = validate_synthesis(raw)
    assert s is not None
    assert s.portfolio_bias == "neutral"


def test_validate_synthesis_coerces_list_types():
    raw = {
        "portfolio_bias": "neutral",
        "key_themes": ["ok", 123, None, "another"],
        "risk_concentrations": "not a list",
        "rebalancing_suggestions": ["s1"],
    }
    s = validate_synthesis(raw)
    assert s is not None
    assert s.key_themes == ["ok", "another"]
    # non-list risk_concentrations → empty list.
    assert s.risk_concentrations == []


def test_validate_synthesis_allowed_biases_matches_spec():
    assert ALLOWED_BIASES == {"bullish", "neutral", "defensive"}


# ── Deterministic fallback ─────────────────────────────────────────────────


def test_deterministic_synthesis_satisfies_phase4_minimums():
    """Gate #1 + #2 — fallback ALWAYS emits ≥2 themes + ≥1 risk concentration."""
    snaps, feats, verds, poss = _mixed_portfolio()
    payload = build_synthesis_inputs(
        verdicts=verds, snapshots=snaps, features=feats,
        positions=poss,
    )
    s = deterministic_synthesis(payload)

    assert s.portfolio_bias in ALLOWED_BIASES
    assert len(s.key_themes) >= 2
    assert len(s.risk_concentrations) >= 1
    assert s.used_fallback is True
    assert s.error == "llm_failed"


def test_deterministic_synthesis_flags_sector_concentration():
    """Technology dominates the mixed portfolio — must appear as risk."""
    snaps, feats, verds, poss = _mixed_portfolio()
    payload = build_synthesis_inputs(
        verdicts=verds, snapshots=snaps, features=feats,
        positions=poss,
    )
    s = deterministic_synthesis(payload)
    combined = " ".join(s.risk_concentrations + s.key_themes).lower()
    assert "tech" in combined or "technology" in combined


def test_deterministic_synthesis_avoids_unknown_sector_summary():
    snapshots = {
        "AAA": _snap("AAA", sector="", category="Momentum"),
        "BBB": _snap("BBB", sector="", category="Momentum"),
    }
    features = {"AAA": _feat("AAA"), "BBB": _feat("BBB")}
    verdicts = {
        "AAA": _verdict("AAA", action="BUY", conviction=0.6, confidence=0.7),
        "BBB": _verdict("BBB", action="HOLD", conviction=0.1, confidence=0.5),
    }
    positions = [
        {"ticker": "AAA", "shares": 10, "avg_cost": 100, "category": "Momentum"},
        {"ticker": "BBB", "shares": 8, "avg_cost": 100, "category": "Momentum"},
    ]
    payload = build_synthesis_inputs(
        verdicts=verdicts, snapshots=snapshots, features=features, positions=positions,
    )
    s = deterministic_synthesis(payload)
    lower_summary = s.summary.lower()
    assert "top sector: unknown" not in lower_summary
    assert "unknown (100% of book)" not in lower_summary
    assert "top exposures:" in lower_summary


def test_deterministic_synthesis_uses_plain_english_summary_copy():
    snaps, feats, verds, poss = _mixed_portfolio()
    payload = build_synthesis_inputs(
        verdicts=verds, snapshots=snaps, features=feats, positions=poss,
    )
    s = deterministic_synthesis(payload)
    lower_summary = s.summary.lower()
    assert "action mix:" in lower_summary
    assert "buy /" in lower_summary
    assert "hold /" in lower_summary
    assert "reduce" in lower_summary
    assert "top exposures:" in lower_summary


def test_deterministic_synthesis_bias_bullish_when_buy_dominates():
    # Three BUYs, one HOLD — bullish bias.
    snaps = {f"T{i}": _snap(f"T{i}") for i in range(4)}
    feats = {f"T{i}": _feat(f"T{i}") for i in range(4)}
    verds = {
        "T0": _verdict("T0", action="BUY", conviction=0.6),
        "T1": _verdict("T1", action="BUY", conviction=0.5),
        "T2": _verdict("T2", action="BUY", conviction=0.4),
        "T3": _verdict("T3", action="HOLD"),
    }
    poss = [{"ticker": f"T{i}", "shares": 10, "avg_cost": 100,
             "category": "Tech"} for i in range(4)]
    payload = build_synthesis_inputs(
        verdicts=verds, snapshots=snaps, features=feats, positions=poss,
    )
    s = deterministic_synthesis(payload)
    assert s.portfolio_bias == "bullish"


def test_deterministic_synthesis_bias_defensive_when_reduces_dominate():
    snaps = {f"T{i}": _snap(f"T{i}") for i in range(4)}
    feats = {f"T{i}": _feat(f"T{i}") for i in range(4)}
    verds = {
        "T0": _verdict("T0", action="REDUCE", conviction=0.6),
        "T1": _verdict("T1", action="REDUCE", conviction=0.5),
        "T2": _verdict("T2", action="HOLD"),
        "T3": _verdict("T3", action="HOLD"),
    }
    poss = [{"ticker": f"T{i}", "shares": 10, "avg_cost": 100,
             "category": "Tech"} for i in range(4)]
    payload = build_synthesis_inputs(
        verdicts=verds, snapshots=snaps, features=feats, positions=poss,
    )
    s = deterministic_synthesis(payload)
    assert s.portfolio_bias == "defensive"


def test_build_synthesis_inputs_maps_known_tickers_and_sets_high_aggregate_quality():
    known = [
        "AAPL", "MSFT", "NVDA", "AMD", "CRM", "SNOW",
        "GOOGL", "META", "NFLX", "RDDT", "COST", "WMT",
        "QCOM", "TSM", "BRK-B", "ALK", "RIVN", "BMWYY",
        "VOO", "VTI", "SPY", "QQQ", "SCHD", "VYM",
        "BND", "VXUS", "VEA", "VWO", "GLD", "BTC",
        "XRP", "KLAR", "BLSH", "STUB",
    ]
    snapshots = {
        t: _snap(t, sector="", category="", data_quality_score=0.9)
        for t in known
    }
    features = {t: _feat(t, sector="", category="") for t in known}
    verdicts = {
        t: _verdict(
            t,
            action="BUY" if i < 10 else ("REDUCE" if i < 13 else "HOLD"),
            conviction=0.75,
            confidence=0.9,
        )
        for i, t in enumerate(known)
    }
    positions = [{"ticker": t, "shares": 1, "avg_cost": 100, "category": ""} for t in known]

    payload = build_synthesis_inputs(
        verdicts=verdicts,
        snapshots=snapshots,
        features=features,
        positions=positions,
    )
    synthesis = deterministic_synthesis(payload)
    sec = payload["portfolio_composition"]["sector_exposure"]
    assert payload["data_quality"]["aggregate_quality"] == "HIGH"
    assert payload["data_quality"]["total_cards"] == 34
    assert "Unknown" not in sec or sec.get("Unknown", 0.0) < 100.0
    assert "Technology" in sec
    assert "Crypto" in sec
    text = " ".join(synthesis.overexposure_flags + synthesis.rebalancing_suggestions + [synthesis.summary]).lower()
    assert "unknown-heavy book" not in text
    assert "trim unknown" not in text


# ── Failure-mode LLM drivers ───────────────────────────────────────────────


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.api_key = "fake"
        self.calls = 0

    async def ask_json(self, *, system, user, max_tokens=1024, normalizer=None):
        self.calls += 1
        if not self.responses:
            return {}
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.mark.asyncio
async def test_synthesize_portfolio_happy_path():
    snaps, feats, verds, poss = _mixed_portfolio()
    llm = _FakeLLM([{
        "portfolio_bias": "neutral",
        "key_themes": [
            "Tech concentration across AAPL/MSFT/NVDA",
            "Auto caution via TSLA REDUCE verdict",
        ],
        "risk_concentrations": ["Single-sector exposure in Technology"],
        "overexposure_flags": [],
        "rebalancing_suggestions": ["Trim TSLA into defensive equity"],
        "summary": "Balanced tech-tilted book with one REDUCE.",
    }])
    s = await synthesize_portfolio(
        verdicts=verds, snapshots=snaps, features=feats, positions=poss,
        llm=llm,
    )
    assert s.portfolio_bias == "neutral"
    assert s.has_required_signal()
    assert s.used_fallback is False
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_synthesize_portfolio_partial_schema_is_augmented_without_fallback():
    """Minor omissions should be repaired without marking deterministic fallback."""
    snaps, feats, verds, poss = _mixed_portfolio()
    llm = _FakeLLM([
        {"portfolio_bias": "bogus", "themes": ["Only one theme"]},
    ])
    s = await synthesize_portfolio(
        verdicts=verds, snapshots=snaps, features=feats, positions=poss,
        llm=llm,
    )
    assert s.used_fallback is False
    assert s.has_required_signal()
    assert s.error == "llm_partial_schema_normalized"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_synthesize_portfolio_survives_all_insufficient_data():
    """Gate #4 — every ticker INSUFFICIENT_DATA must not crash synthesis."""
    snaps = {"X": _snap("X", data_quality_score=0.1),
             "Y": _snap("Y", data_quality_score=0.1)}
    feats = {"X": _feat("X"), "Y": _feat("Y")}
    verds = {
        "X": _verdict("X", action="INSUFFICIENT_DATA", conviction=0.0),
        "Y": _verdict("Y", action="INSUFFICIENT_DATA", conviction=0.0),
    }
    poss = [{"ticker": "X", "shares": 10, "avg_cost": 100, "category": "Tech"},
            {"ticker": "Y", "shares": 10, "avg_cost": 100, "category": "Tech"}]
    llm = _FakeLLM([Exception("oops"), Exception("again")])
    s = await synthesize_portfolio(
        verdicts=verds, snapshots=snaps, features=feats, positions=poss, llm=llm,
    )
    # Deterministic fallback still produces a valid synthesis.
    assert s.portfolio_bias in ALLOWED_BIASES
    assert s.has_required_signal()
    assert s.used_fallback is False
    assert s.error == "llm_partial_schema_normalized"


@pytest.mark.asyncio
async def test_synthesize_portfolio_no_llm_returns_deterministic():
    snaps, feats, verds, poss = _mixed_portfolio()
    s = await synthesize_portfolio(
        verdicts=verds, snapshots=snaps, features=feats, positions=poss,
        llm=None,
    )
    assert s.used_fallback is True
    assert s.has_required_signal()


@pytest.mark.asyncio
async def test_synthesize_portfolio_differs_from_per_ticker_reasoning():
    """Gate #3 — synthesis output must not just rehash the per-ticker drivers."""
    snaps, feats, verds, poss = _mixed_portfolio()
    llm = _FakeLLM([{
        "portfolio_bias": "neutral",
        "key_themes": [
            "Technology sector makes up majority of book",
            "Auto exposure trending weak via TSLA",
        ],
        "risk_concentrations": [
            "Top-3 holdings all in Technology",
        ],
        "overexposure_flags": [],
        "rebalancing_suggestions": [],
        "summary": "Tech-heavy book.",
    }])
    s = await synthesize_portfolio(
        verdicts=verds, snapshots=snaps, features=feats, positions=poss, llm=llm,
    )
    per_ticker_drivers = set()
    for v in verds.values():
        for d in v.key_drivers:
            per_ticker_drivers.add(d.lower())
    for theme in s.key_themes:
        # Each cross-ticker theme must NOT be verbatim-equal to any
        # single per-ticker driver; that's the definition of synthesis.
        assert theme.lower() not in per_ticker_drivers


# ── Orchestrator integration ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_run_portfolio_synthesis(monkeypatch):
    from unittest.mock import MagicMock
    from uuid import uuid4
    from app.services.agents import orchestrator as orch_mod

    mock_db = MagicMock()
    monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)

    orch = orch_mod.AgentOrchestrator(user_id=uuid4(), anthropic_api_key="")
    # No LLM → deterministic fallback path exercised.
    snaps, feats, verds, poss = _mixed_portfolio()
    orch._snapshots = snaps
    orch._features = feats
    orch._verdicts = verds

    context = {"portfolio": poss, "macro": {"summary": "m"}}
    s = await orch._run_portfolio_synthesis(context=context)

    assert isinstance(s, PortfolioSynthesis)
    assert s.has_required_signal()
    assert s.portfolio_bias in ALLOWED_BIASES
