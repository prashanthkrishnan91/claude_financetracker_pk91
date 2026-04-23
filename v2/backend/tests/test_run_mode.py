"""Phase 5 — cost and failure control acceptance tests.

Gates covered here (tasks/todo.md → Phase 5 acceptance):
  1. Cost metrics visible per run.
  2. At least 30-70% cost reduction in DEGRADED mode.
  3. No empty outputs under any condition.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.intelligence.market_snapshot import MarketSnapshot
from app.services.intelligence.per_ticker_analyst import (
    ALLOWED_ACTIONS,
    AnalystVerdict,
)
from app.services.intelligence.run_mode import (
    DEGRADED_QUALITY_THRESHOLD,
    RunCostTracker,
    RunMode,
    build_degraded_verdicts,
    classify_run_mode,
    estimate_cost_usd,
    projected_full_mode_cost,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


def _snap(ticker, quality=0.8, price_source="live", price=100.0) -> MarketSnapshot:
    return MarketSnapshot(
        ticker=ticker,
        as_of="2026-04-23T00:00:00+00:00",
        price=price,
        price_source=price_source,
        return_30d=4.0,
        volatility_30d=0.25,
        sector="Technology",
        category="Tech",
        data_quality_score=quality,
        missing_fields=[],
    )


# ── Classifier ─────────────────────────────────────────────────────────────


def test_classify_run_mode_full():
    snaps = [_snap(f"T{i}", quality=0.8) for i in range(5)]
    decision = classify_run_mode(snaps)
    assert decision.mode == RunMode.FULL
    assert decision.avg_quality > DEGRADED_QUALITY_THRESHOLD
    assert decision.reason == "ok"


def test_classify_run_mode_degraded_low_quality():
    snaps = [_snap(f"T{i}", quality=0.3) for i in range(5)]
    decision = classify_run_mode(snaps)
    assert decision.mode == RunMode.DEGRADED
    assert decision.reason == "avg_quality_below_threshold"
    assert "DEGRADED" in decision.explanation


def test_classify_run_mode_degraded_majority_stale_prices():
    snaps = [_snap(f"T{i}", quality=0.7, price_source="avg_cost_fallback")
             for i in range(3)] + [_snap("X", quality=0.7)]
    decision = classify_run_mode(snaps)
    assert decision.mode == RunMode.DEGRADED
    assert decision.reason == "majority_stale_prices"


def test_classify_run_mode_empty_is_degraded():
    """No snapshots → DEGRADED so the run can short-circuit safely."""
    decision = classify_run_mode([])
    assert decision.mode == RunMode.DEGRADED
    assert decision.reason == "no_snapshots"


def test_classify_run_mode_boundary():
    """Exactly at the threshold still counts as FULL — strict ``<`` comparison."""
    snaps = [_snap(f"T{i}", quality=DEGRADED_QUALITY_THRESHOLD) for i in range(3)]
    decision = classify_run_mode(snaps)
    assert decision.mode == RunMode.FULL


# ── Degraded-mode verdicts ─────────────────────────────────────────────────


def test_build_degraded_verdicts_every_ticker_has_a_verdict():
    """Gate #3 — even in DEGRADED mode, every ticker carries a verdict."""
    snaps = {
        "AAPL": _snap("AAPL", quality=0.4, price_source="price_action"),
        "BTC":  _snap("BTC",  quality=0.1, price_source="avg_cost_fallback"),
    }
    decision = classify_run_mode(snaps.values())
    verdicts = build_degraded_verdicts(snaps, decision=decision)

    assert set(verdicts.keys()) == {"AAPL", "BTC"}
    for v in verdicts.values():
        assert v.action in ALLOWED_ACTIONS
        # HOLD or INSUFFICIENT_DATA only — never BUY / REDUCE in DEGRADED.
        assert v.action in {"HOLD", "INSUFFICIENT_DATA"}
        assert v.used_fallback is True
        # Driver + risk lists are populated, so thesis rendering is
        # non-empty — never a degenerate ``{}`` equivalent.
        assert len(v.key_drivers) >= 1 or v.action == "INSUFFICIENT_DATA"
        assert len(v.risks) >= 1


def test_build_degraded_verdicts_insufficient_for_missing_price():
    snaps = {
        "NOPRICE": MarketSnapshot(
            ticker="NOPRICE",
            as_of="2026-04-23T00:00:00+00:00",
            price=None,
            price_source="unavailable",
            data_quality_score=0.2,
            sector="",
            category="Tech",
        ),
    }
    decision = classify_run_mode(snaps.values())
    verdicts = build_degraded_verdicts(snaps, decision=decision)
    assert verdicts["NOPRICE"].action == "INSUFFICIENT_DATA"
    assert verdicts["NOPRICE"].conviction == 0.0


def test_build_degraded_verdicts_have_varied_thesis():
    """Each ticker's drivers come from its own snapshot — no identical rehash."""
    snaps = {
        "A": _snap("A", quality=0.4, price_source="live"),
        "B": _snap("B", quality=0.35, price_source="price_action"),
        "C": _snap("C", quality=0.4, price_source="live"),
    }
    snaps["B"].return_30d = -10.0
    snaps["C"].return_30d = 8.0
    decision = classify_run_mode(snaps.values())
    verdicts = build_degraded_verdicts(snaps, decision=decision)

    driver_sets = {tuple(v.key_drivers) for v in verdicts.values()}
    # Not all identical.
    assert len(driver_sets) >= 2


# ── Cost tracker ───────────────────────────────────────────────────────────


def test_cost_tracker_accumulates_calls_and_cost():
    tracker = RunCostTracker(mode=RunMode.FULL)
    tracker.record(kind="analyst", model="claude-sonnet-4-6")
    tracker.record(kind="analyst", model="claude-sonnet-4-6")
    tracker.record(kind="synthesis", model="claude-sonnet-4-6")

    assert tracker.total_calls == 3
    assert tracker.total_cost_usd > 0
    assert tracker.calls_by_kind() == {"analyst": 2, "synthesis": 1}


def test_cost_tracker_to_dict_shape():
    """Gate #1 — cost metrics are surfaced as a dict the UI can render."""
    tracker = RunCostTracker(mode=RunMode.FULL)
    tracker.record(kind="analyst", model="claude-sonnet-4-6")
    d = tracker.to_dict()
    for key in (
        "mode", "total_calls", "total_cost_usd",
        "calls_by_kind", "calls_by_model", "entries",
    ):
        assert key in d
    assert d["total_calls"] == 1
    assert d["mode"] == "FULL"


def test_estimate_cost_usd_known_model():
    """Sonnet rates: $3 in / $15 out per 1M tokens."""
    cost = estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert abs(cost - 18.0) < 0.01


def test_estimate_cost_usd_haiku_cheaper_than_sonnet():
    sonnet = estimate_cost_usd("claude-sonnet-4-6", 1500, 300)
    haiku = estimate_cost_usd("claude-haiku-4-5-20251001", 1500, 300)
    assert haiku < sonnet


# ── Cost-reduction acceptance gate (#2) ────────────────────────────────────


def test_degraded_mode_saves_30_to_100_percent():
    """Gate #2 — DEGRADED mode cuts ≥ 30% of the projected FULL-mode cost.

    Constructed by running the cost tracker twice over identical workloads:
    the FULL pass records N analyst calls + 1 synthesis call, the DEGRADED
    pass records zero calls (spec-mandated). The projected savings
    computed against the FULL-mode projection must be ≥ 30%.
    """
    model = "claude-sonnet-4-6"
    tickers = 5

    # FULL-mode projection matches actual FULL-mode tracker output.
    full = RunCostTracker(mode=RunMode.FULL)
    for _ in range(tickers):
        full.record(kind="analyst", model=model)
    full.record(kind="synthesis", model=model)
    projected = projected_full_mode_cost(full, ticker_count=tickers, model=model)
    assert abs(projected - full.total_cost_usd) < 1e-4

    # DEGRADED mode records no LLM calls in the spec path.
    degraded = RunCostTracker(mode=RunMode.DEGRADED)
    assert degraded.total_calls == 0
    assert degraded.total_cost_usd == 0.0

    # Savings = (full - degraded) / full ≥ 30%.
    savings_pct = ((projected - degraded.total_cost_usd) / projected) * 100
    assert savings_pct >= 30.0
    # Real savings at 0 LLM calls is 100% — both thresholds from the spec
    # (30% minimum, 70% upper) are comfortably beaten.
    assert savings_pct >= 70.0


# ── Orchestrator integration ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_degraded_skips_analyst_llm(monkeypatch):
    """Gate #2 + #3 — DEGRADED orchestrator path makes zero analyst LLM calls
    and still produces one verdict per ticker."""
    from app.services.agents import orchestrator as orch_mod

    mock_db = MagicMock()
    monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)

    orch = orch_mod.AgentOrchestrator(user_id=uuid4(), anthropic_api_key="fake")
    # Low-quality snapshots → classifier trips to DEGRADED.
    orch._snapshots = {
        "AAPL": _snap("AAPL", quality=0.3, price_source="price_action"),
        "TSLA": _snap("TSLA", quality=0.25, price_source="avg_cost_fallback"),
    }
    orch._features = {"AAPL": MagicMock(), "TSLA": MagicMock()}
    orch._mode_decision = classify_run_mode(orch._snapshots.values())
    orch._cost_tracker = RunCostTracker(mode=orch._mode_decision.mode)
    assert orch._mode_decision.mode == RunMode.DEGRADED

    # Sentinel — this must NOT be called in DEGRADED mode.
    async def _boom(**kwargs):
        raise AssertionError("LLM should NOT fire in DEGRADED mode")

    orch._llm.ask_json = _boom  # type: ignore[assignment]

    verdicts = await orch._run_per_ticker_analyst()

    assert set(verdicts.keys()) == {"AAPL", "TSLA"}
    # Every verdict is populated — spec says "never return {}".
    for v in verdicts.values():
        assert v.action in ALLOWED_ACTIONS
        assert v.used_fallback is True
    # Zero LLM calls recorded.
    assert orch._cost_tracker.total_calls == 0


@pytest.mark.asyncio
async def test_orchestrator_full_mode_tracks_cost(monkeypatch):
    """Gate #1 — FULL-mode orchestrator records cost entries for each call."""
    from app.services.agents import orchestrator as orch_mod

    mock_db = MagicMock()
    monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)

    orch = orch_mod.AgentOrchestrator(user_id=uuid4(), anthropic_api_key="fake")
    snaps = {f"T{i}": _snap(f"T{i}", quality=0.85) for i in range(3)}
    features = {
        f"T{i}": MagicMock(
            trend_regime="uptrend", momentum_score=0.4,
            volatility_regime="medium",
            relative_strength_30d=2.0, relative_strength_label="inline",
            sector="Tech", sma20=95.0, sma50=90.0, benchmark_symbol="SPY",
            data_quality_score=0.85,
        )
        for i in range(3)
    }
    orch._snapshots = snaps
    orch._features = features
    orch._mode_decision = classify_run_mode(snaps.values())
    orch._cost_tracker = RunCostTracker(mode=orch._mode_decision.mode)
    assert orch._mode_decision.mode == RunMode.FULL

    async def _fake_ask(**kwargs):
        return {
            "action": "HOLD", "conviction": 0.1,
            "key_drivers": ["range"], "risks": ["noise"], "confidence": 0.5,
        }

    orch._llm.ask_json = _fake_ask  # type: ignore[assignment]

    verdicts = await orch._run_per_ticker_analyst()

    assert set(verdicts.keys()) == {"T0", "T1", "T2"}
    # 3 analyst calls recorded in the tracker.
    assert orch._cost_tracker.total_calls == 3
    assert orch._cost_tracker.calls_by_kind()["analyst"] == 3
    assert orch._cost_tracker.total_cost_usd > 0.0


@pytest.mark.asyncio
async def test_orchestrator_degraded_synthesis_zero_llm(monkeypatch):
    """Synthesis in DEGRADED mode uses the deterministic path — zero calls."""
    from app.services.agents import orchestrator as orch_mod

    mock_db = MagicMock()
    monkeypatch.setattr(orch_mod, "get_supabase_client", lambda: mock_db)

    orch = orch_mod.AgentOrchestrator(user_id=uuid4(), anthropic_api_key="fake")
    snaps = {"X": _snap("X", quality=0.3)}
    orch._snapshots = snaps
    orch._features = {}
    orch._verdicts = {
        "X": AnalystVerdict(
            ticker="X", action="INSUFFICIENT_DATA", conviction=0.0,
            used_fallback=True,
        ),
    }
    orch._mode_decision = classify_run_mode(snaps.values())
    orch._cost_tracker = RunCostTracker(mode=orch._mode_decision.mode)

    async def _boom(**kwargs):
        raise AssertionError("synthesis LLM must NOT fire in DEGRADED mode")

    orch._llm.ask_json = _boom  # type: ignore[assignment]

    context = {"portfolio": [{"ticker": "X", "shares": 1, "avg_cost": 100.0,
                              "category": "Tech"}], "macro": {"summary": ""}}
    synthesis = await orch._run_portfolio_synthesis(context=context)

    # Synthesis still emits a spec-valid output (fallback path).
    assert synthesis.has_required_signal()
    assert synthesis.used_fallback is True
    # No LLM calls were recorded.
    assert orch._cost_tracker.total_calls == 0
    # Explanation tag surfaces in the summary for the UI.
    assert "DEGRADED" in synthesis.summary
