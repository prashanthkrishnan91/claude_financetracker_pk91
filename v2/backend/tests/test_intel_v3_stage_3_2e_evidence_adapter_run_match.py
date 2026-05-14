"""Stage 3.2e — ReadOnlyEvidenceAdapter run-id-matched insight selection tests.

Acceptance criteria:
  1. Fresh active recommendation with agent_run_id=new_run + stale insight
     under old_run + fresh insight under new_run → adapter selects new_run
     insight and reports fresh analyst timestamp (not old_run timestamp).
  2. When matched-run insight is missing, adapter falls back to best available
     insight and increments fallback/missing diagnostic counts.
  3. primary_driver / risk_flag / action_reason from the matching fresh insight
     are surfaced into the card (not from the older run's lossy fields).
  4. Fresh rec (0h) + matching insight (0h) → both agent_insight_run_timestamps
     and recommendation_timestamps are recent (within the same window).
  5. adapter.load_cards() makes zero LLM calls.
  6. Existing Stage 3.2c/3.2d tests pass (non-regression).
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch, call
from uuid import uuid4


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uid():
    return uuid4()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def _hrs_ago(h: float, base: Optional[datetime] = None) -> datetime:
    return (base or _now()) - timedelta(hours=h)


def _make_mock_client(
    *,
    recs: list[dict],
    positions: list[dict],
    agent_runs: list[dict],
    agent_insights: list[dict],
) -> MagicMock:
    """Build a mock Supabase client that returns canned data per table."""
    client = MagicMock()

    def _table(name):
        tbl = MagicMock()
        if name == "recommendations":
            tbl.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
                SimpleNamespace(data=recs)
            )
        elif name == "positions":
            tbl.select.return_value.eq.return_value.execute.return_value = (
                SimpleNamespace(data=positions)
            )
        elif name == "agent_runs":
            tbl.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = (
                SimpleNamespace(data=agent_runs)
            )
        elif name == "agent_insights":
            # select(...).eq(...).in_(...).in_(...).execute()
            tbl.select.return_value.eq.return_value.in_.return_value.in_.return_value.execute.return_value = (
                SimpleNamespace(data=agent_insights)
            )
        return tbl

    client.table.side_effect = _table
    return client


def _run_load_cards(client) -> tuple[list[Any], dict[str, Any]]:
    from app.services.intelligence.v3.read_only_evidence_adapter import ReadOnlyEvidenceAdapter
    adapter = ReadOnlyEvidenceAdapter.__new__(ReadOnlyEvidenceAdapter)
    adapter.user_id = _uid()
    adapter.client = client

    return asyncio.get_event_loop().run_until_complete(adapter.load_cards())


# ── Test 1: matched-run insight selected over older same-ticker insight ────────

class TestRunIdMatchedInsightPreferred(unittest.TestCase):
    """Reproduces production bug: fresh rec paired with old insight when new exists."""

    def test_selects_new_run_insight_over_old_run_insight(self):
        now = _now()
        old_run_id = str(uuid4())
        new_run_id = str(uuid4())

        recs = [{
            "id": str(uuid4()),
            "ticker": "AAPL",
            "action": "BUY",
            "technical_signal": "BUY",
            "conviction_score": 0.85,
            "agent_run_id": new_run_id,  # fresh run
            "is_active": True,
            "created_at": _iso(now),
        }]
        positions = [{"ticker": "AAPL", "name": "Apple Inc.", "category": "stock"}]
        agent_runs = [
            {"id": new_run_id, "finished_at": _iso(now), "status": "completed", "allocation": None},
            {"id": old_run_id, "finished_at": _iso(_hrs_ago(10.3 * 24, now)), "status": "completed", "allocation": None},
        ]
        # Both insights exist — old and new. DB may return in any order.
        agent_insights = [
            {
                "run_id": old_run_id,
                "ticker": "AAPL",
                "analyst_verdict": {
                    "action": "BUY",
                    "primary_driver": "AAPL: signals support adding.",
                    "action_reason": None,
                    "risk_flag": "",
                    "conviction_level": "HIGH",
                    "data_quality_label": "MEDIUM",
                    "used_fallback": False,
                },
                "analyst_confidence": 0.7,
                "created_at": _iso(_hrs_ago(10.3 * 24, now)),
            },
            {
                "run_id": new_run_id,
                "ticker": "AAPL",
                "analyst_verdict": {
                    "action": "BUY",
                    "primary_driver": "Services segment growing 15% YoY with expanding margins.",
                    "action_reason": "Add on dips; services multiple not yet priced in.",
                    "risk_flag": "Watch for app store regulatory headwinds.",
                    "conviction_level": "HIGH",
                    "data_quality_label": "HIGH",
                    "used_fallback": False,
                },
                "analyst_confidence": 0.85,
                "created_at": _iso(now),
            },
        ]

        client = _make_mock_client(
            recs=recs, positions=positions,
            agent_runs=agent_runs, agent_insights=agent_insights,
        )
        cards, stats = _run_load_cards(client)

        self.assertEqual(len(cards), 1)
        card = cards[0]

        # Must use the new_run insight's structured fields
        self.assertEqual(card.primary_driver, "Services segment growing 15% YoY with expanding margins.")
        self.assertEqual(card.action_reason, "Add on dips; services multiple not yet priced in.")
        self.assertEqual(card.risk_flag, "Watch for app store regulatory headwinds.")

    def test_analyst_timestamp_reflects_new_run_not_old_run(self):
        """agent_insight_run_timestamps must use the matched run's finished_at."""
        now = _now()
        old_run_id = str(uuid4())
        new_run_id = str(uuid4())

        recs = [{
            "id": str(uuid4()), "ticker": "AAPL", "action": "BUY",
            "technical_signal": "BUY", "conviction_score": 0.85,
            "agent_run_id": new_run_id, "is_active": True, "created_at": _iso(now),
        }]
        positions = [{"ticker": "AAPL", "name": "Apple Inc.", "category": "stock"}]
        agent_runs = [
            {"id": new_run_id, "finished_at": _iso(now), "status": "completed", "allocation": None},
            {"id": old_run_id, "finished_at": _iso(_hrs_ago(10.3 * 24, now)), "status": "completed", "allocation": None},
        ]
        agent_insights = [
            {"run_id": old_run_id, "ticker": "AAPL", "analyst_verdict": {}, "analyst_confidence": 0.5, "created_at": _iso(_hrs_ago(10.3 * 24, now))},
            {"run_id": new_run_id, "ticker": "AAPL", "analyst_verdict": {"primary_driver": "Margin expansion"}, "analyst_confidence": 0.85, "created_at": _iso(now)},
        ]

        client = _make_mock_client(
            recs=recs, positions=positions,
            agent_runs=agent_runs, agent_insights=agent_insights,
        )
        cards, stats = _run_load_cards(client)

        # Should have exactly one timestamp — from the new (fresh) run
        self.assertEqual(len(stats["agent_insight_run_timestamps"]), 1)
        ts = stats["agent_insight_run_timestamps"][0]
        # The timestamp should be now's ISO, not old_run's
        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age_hours = (now - ts_dt).total_seconds() / 3600
        self.assertLess(age_hours, 1.0, f"Analyst timestamp should be fresh, got age={age_hours:.1f}h")

    def test_diagnostic_matched_count_incremented(self):
        now = _now()
        new_run_id = str(uuid4())

        recs = [{
            "id": str(uuid4()), "ticker": "AAPL", "action": "BUY",
            "technical_signal": "BUY", "conviction_score": 0.8,
            "agent_run_id": new_run_id, "is_active": True, "created_at": _iso(now),
        }]
        positions = [{"ticker": "AAPL", "name": "Apple Inc.", "category": "stock"}]
        agent_runs = [{"id": new_run_id, "finished_at": _iso(now), "status": "completed", "allocation": None}]
        agent_insights = [{
            "run_id": new_run_id, "ticker": "AAPL",
            "analyst_verdict": {"primary_driver": "Strong growth"},
            "analyst_confidence": 0.8, "created_at": _iso(now),
        }]

        client = _make_mock_client(
            recs=recs, positions=positions,
            agent_runs=agent_runs, agent_insights=agent_insights,
        )
        _, stats = _run_load_cards(client)

        self.assertEqual(stats["matched_agent_insight_by_recommendation_run_count"], 1)
        self.assertEqual(stats["fallback_agent_insight_by_ticker_count"], 0)
        self.assertEqual(stats["missing_agent_insight_for_recommendation_run_count"], 0)
        self.assertEqual(stats["recommendation_agent_run_ids_count"], 1)


# ── Test 2: fallback when matched-run insight is missing ─────────────────────

class TestFallbackWhenMatchedRunInsightMissing(unittest.TestCase):
    """When the rec's run_id has no insight, fall back honestly."""

    def test_fallback_used_when_matched_run_has_no_insight(self):
        now = _now()
        new_run_id = str(uuid4())
        old_run_id = str(uuid4())

        recs = [{
            "id": str(uuid4()), "ticker": "MSFT", "action": "BUY",
            "technical_signal": "BUY", "conviction_score": 0.75,
            "agent_run_id": new_run_id, "is_active": True, "created_at": _iso(now),
        }]
        positions = [{"ticker": "MSFT", "name": "Microsoft", "category": "stock"}]
        agent_runs = [
            {"id": new_run_id, "finished_at": _iso(now), "status": "completed", "allocation": None},
            {"id": old_run_id, "finished_at": _iso(_hrs_ago(5, now)), "status": "completed", "allocation": None},
        ]
        # Only old_run has an insight for MSFT — new_run has none
        agent_insights = [{
            "run_id": old_run_id, "ticker": "MSFT",
            "analyst_verdict": {"primary_driver": "Cloud growth thesis", "conviction_level": "MEDIUM"},
            "analyst_confidence": 0.7, "created_at": _iso(_hrs_ago(5, now)),
        }]

        client = _make_mock_client(
            recs=recs, positions=positions,
            agent_runs=agent_runs, agent_insights=agent_insights,
        )
        cards, stats = _run_load_cards(client)

        # Should fall back to old_run insight (honest degradation)
        card = cards[0]
        self.assertEqual(card.primary_driver, "Cloud growth thesis")
        self.assertEqual(stats["missing_agent_insight_for_recommendation_run_count"], 1)
        self.assertEqual(stats["fallback_agent_insight_by_ticker_count"], 1)
        self.assertEqual(stats["matched_agent_insight_by_recommendation_run_count"], 0)

    def test_missing_insight_entirely_degrades_gracefully(self):
        now = _now()
        new_run_id = str(uuid4())

        recs = [{
            "id": str(uuid4()), "ticker": "NVDA", "action": "BUY",
            "technical_signal": "BUY", "conviction_score": 0.9,
            "agent_run_id": new_run_id, "is_active": True, "created_at": _iso(now),
        }]
        positions = [{"ticker": "NVDA", "name": "NVIDIA", "category": "stock"}]
        agent_runs = [{"id": new_run_id, "finished_at": _iso(now), "status": "completed", "allocation": None}]
        agent_insights = []  # no insights at all

        client = _make_mock_client(
            recs=recs, positions=positions,
            agent_runs=agent_runs, agent_insights=agent_insights,
        )
        cards, stats = _run_load_cards(client)

        card = cards[0]
        self.assertIsNone(card.primary_driver)
        self.assertEqual(stats["missing_evidence_count"], 1)
        self.assertEqual(stats["stale_or_missing_source_count"], 1)
        self.assertEqual(stats["missing_agent_insight_for_recommendation_run_count"], 1)
        self.assertEqual(stats["fallback_agent_insight_by_ticker_count"], 0)


# ── Test 3: rationale fields from matched fresh insight surface correctly ─────

class TestRationaleFieldsFromMatchedInsight(unittest.TestCase):
    """primary_driver / action_reason / risk_flag come from the matched run's insight."""

    def test_all_three_rationale_fields_from_matched_insight(self):
        now = _now()
        run_id = str(uuid4())

        recs = [{
            "id": str(uuid4()), "ticker": "GOOGL", "action": "BUY",
            "technical_signal": "BUY", "conviction_score": 0.8,
            "agent_run_id": run_id, "is_active": True, "created_at": _iso(now),
        }]
        positions = [{"ticker": "GOOGL", "name": "Alphabet", "category": "stock"}]
        agent_runs = [{"id": run_id, "finished_at": _iso(now), "status": "completed", "allocation": None}]
        agent_insights = [{
            "run_id": run_id, "ticker": "GOOGL",
            "analyst_verdict": {
                "action": "BUY",
                "primary_driver": "AI search monetisation accelerating ahead of consensus.",
                "action_reason": "Accumulate; valuation discount to peers unjustified.",
                "risk_flag": "Antitrust settlement risk remains elevated.",
                "conviction_level": "HIGH",
                "data_quality_label": "HIGH",
                "used_fallback": False,
            },
            "analyst_confidence": 0.82,
            "created_at": _iso(now),
        }]

        client = _make_mock_client(
            recs=recs, positions=positions,
            agent_runs=agent_runs, agent_insights=agent_insights,
        )
        cards, stats = _run_load_cards(client)

        card = cards[0]
        self.assertEqual(card.primary_driver, "AI search monetisation accelerating ahead of consensus.")
        self.assertEqual(card.action_reason, "Accumulate; valuation discount to peers unjustified.")
        self.assertEqual(card.risk_flag, "Antitrust settlement risk remains elevated.")
        self.assertEqual(card.conviction_level, "HIGH")
        self.assertFalse(card.analyst_used_fallback)

    def test_both_freshness_ages_near_zero_with_matched_insight(self):
        """rec 0h + matched insight 0h → both timestamp lists are fresh."""
        now = _now()
        run_id = str(uuid4())

        recs = [{
            "id": str(uuid4()), "ticker": "META", "action": "BUY",
            "technical_signal": "BUY", "conviction_score": 0.78,
            "agent_run_id": run_id, "is_active": True, "created_at": _iso(now),
        }]
        positions = [{"ticker": "META", "name": "Meta Platforms", "category": "stock"}]
        agent_runs = [{"id": run_id, "finished_at": _iso(now), "status": "completed", "allocation": None}]
        agent_insights = [{
            "run_id": run_id, "ticker": "META",
            "analyst_verdict": {"primary_driver": "Ad revenue rebound", "conviction_level": "HIGH"},
            "analyst_confidence": 0.78,
            "created_at": _iso(now),
        }]

        client = _make_mock_client(
            recs=recs, positions=positions,
            agent_runs=agent_runs, agent_insights=agent_insights,
        )
        _, stats = _run_load_cards(client)

        def _max_age_hours(timestamps):
            if not timestamps:
                return float("inf")
            ages = []
            for ts in timestamps:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ages.append((now - dt).total_seconds() / 3600)
            return max(ages)

        rec_age = _max_age_hours(stats["recommendation_timestamps"])
        insight_age = _max_age_hours(stats["agent_insight_run_timestamps"])
        self.assertLess(rec_age, 1.0, f"Rec age should be <1h, got {rec_age:.2f}h")
        self.assertLess(insight_age, 1.0, f"Insight age should be <1h, got {insight_age:.2f}h")


# ── Test 4: zero LLM calls ────────────────────────────────────────────────────

class TestZeroLLMCalls(unittest.TestCase):
    """load_cards() never invokes any LLM or analyst orchestrator."""

    def test_load_cards_zero_llm_calls(self):
        now = _now()
        run_id = str(uuid4())

        recs = [{
            "id": str(uuid4()), "ticker": "AMZN", "action": "BUY",
            "technical_signal": "BUY", "conviction_score": 0.7,
            "agent_run_id": run_id, "is_active": True, "created_at": _iso(now),
        }]
        positions = [{"ticker": "AMZN", "name": "Amazon", "category": "stock"}]
        agent_runs = [{"id": run_id, "finished_at": _iso(now), "status": "completed", "allocation": None}]
        agent_insights = [{
            "run_id": run_id, "ticker": "AMZN",
            "analyst_verdict": {"primary_driver": "AWS cloud growth"},
            "analyst_confidence": 0.7,
            "created_at": _iso(now),
        }]

        client = _make_mock_client(
            recs=recs, positions=positions,
            agent_runs=agent_runs, agent_insights=agent_insights,
        )
        _, stats = _run_load_cards(client)

        self.assertEqual(stats["attempted_llm_calls"], 0)


# ── Test 5: multi-ticker — correct pairing across all tickers ─────────────────

class TestMultiTickerRunIdMatching(unittest.TestCase):
    """Each ticker's rec is paired with its own run's insight, not another's."""

    def test_each_ticker_paired_with_its_own_run_insight(self):
        now = _now()
        run_a = str(uuid4())  # AAPL's run
        run_b = str(uuid4())  # MSFT's run (different run, older)
        shared_old_run = str(uuid4())  # both have a stale insight here

        recs = [
            {"id": str(uuid4()), "ticker": "AAPL", "action": "BUY", "technical_signal": "BUY",
             "conviction_score": 0.8, "agent_run_id": run_a, "is_active": True, "created_at": _iso(now)},
            {"id": str(uuid4()), "ticker": "MSFT", "action": "HOLD", "technical_signal": "HOLD",
             "conviction_score": 0.5, "agent_run_id": run_b, "is_active": True, "created_at": _iso(_hrs_ago(1, now))},
        ]
        positions = [
            {"ticker": "AAPL", "name": "Apple Inc.", "category": "stock"},
            {"ticker": "MSFT", "name": "Microsoft", "category": "stock"},
        ]
        agent_runs = [
            {"id": run_a, "finished_at": _iso(now), "status": "completed", "allocation": None},
            {"id": run_b, "finished_at": _iso(_hrs_ago(2, now)), "status": "completed", "allocation": None},
            {"id": shared_old_run, "finished_at": _iso(_hrs_ago(50, now)), "status": "completed", "allocation": None},
        ]
        agent_insights = [
            # AAPL has fresh insight under run_a and stale under shared_old_run
            {"run_id": run_a, "ticker": "AAPL", "analyst_verdict": {"primary_driver": "AAPL fresh insight"}, "analyst_confidence": 0.8, "created_at": _iso(now)},
            {"run_id": shared_old_run, "ticker": "AAPL", "analyst_verdict": {"primary_driver": "AAPL old insight"}, "analyst_confidence": 0.5, "created_at": _iso(_hrs_ago(50, now))},
            # MSFT has insight under run_b and stale under shared_old_run
            {"run_id": run_b, "ticker": "MSFT", "analyst_verdict": {"primary_driver": "MSFT correct insight"}, "analyst_confidence": 0.6, "created_at": _iso(_hrs_ago(2, now))},
            {"run_id": shared_old_run, "ticker": "MSFT", "analyst_verdict": {"primary_driver": "MSFT old insight"}, "analyst_confidence": 0.4, "created_at": _iso(_hrs_ago(50, now))},
        ]

        client = _make_mock_client(
            recs=recs, positions=positions,
            agent_runs=agent_runs, agent_insights=agent_insights,
        )
        cards, stats = _run_load_cards(client)

        card_map = {c.ticker: c for c in cards}
        self.assertEqual(card_map["AAPL"].primary_driver, "AAPL fresh insight")
        self.assertEqual(card_map["MSFT"].primary_driver, "MSFT correct insight")
        self.assertEqual(stats["matched_agent_insight_by_recommendation_run_count"], 2)
        self.assertEqual(stats["fallback_agent_insight_by_ticker_count"], 0)


# ── Test 6: rec with no agent_run_id falls back honestly ─────────────────────

class TestRecWithoutAgentRunId(unittest.TestCase):
    """Rec with null agent_run_id uses best available fallback insight."""

    def test_null_agent_run_id_uses_fallback(self):
        now = _now()
        run_id = str(uuid4())

        recs = [{
            "id": str(uuid4()), "ticker": "TSLA", "action": "HOLD",
            "technical_signal": "HOLD", "conviction_score": 0.4,
            "agent_run_id": None,  # no run id
            "is_active": True, "created_at": _iso(now),
        }]
        positions = [{"ticker": "TSLA", "name": "Tesla", "category": "stock"}]
        agent_runs = [{"id": run_id, "finished_at": _iso(_hrs_ago(3, now)), "status": "completed", "allocation": None}]
        agent_insights = [{
            "run_id": run_id, "ticker": "TSLA",
            "analyst_verdict": {"primary_driver": "EV demand softening"},
            "analyst_confidence": 0.5,
            "created_at": _iso(_hrs_ago(3, now)),
        }]

        client = _make_mock_client(
            recs=recs, positions=positions,
            agent_runs=agent_runs, agent_insights=agent_insights,
        )
        cards, stats = _run_load_cards(client)

        card = cards[0]
        self.assertEqual(card.primary_driver, "EV demand softening")
        self.assertEqual(stats["fallback_agent_insight_by_ticker_count"], 1)
        self.assertEqual(stats["matched_agent_insight_by_recommendation_run_count"], 0)
        self.assertEqual(stats["missing_agent_insight_for_recommendation_run_count"], 0)


# ── Test 7: non-regression — diagnostic keys always present ──────────────────

class TestDiagnosticKeysAlwaysPresent(unittest.TestCase):
    """All four new diagnostic keys exist in stats even with zero data."""

    def test_diagnostic_keys_present_with_no_recs(self):
        client = _make_mock_client(recs=[], positions=[], agent_runs=[], agent_insights=[])
        _, stats = _run_load_cards(client)

        for key in (
            "matched_agent_insight_by_recommendation_run_count",
            "fallback_agent_insight_by_ticker_count",
            "missing_agent_insight_for_recommendation_run_count",
            "recommendation_agent_run_ids_count",
        ):
            self.assertIn(key, stats, f"Missing diagnostic key: {key}")
            self.assertEqual(stats[key], 0)


if __name__ == "__main__":
    unittest.main()
