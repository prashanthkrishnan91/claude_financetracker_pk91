"""Tests for Intel v3 snapshot freshness diagnostics.

Acceptance criteria:
  1. Identical persisted evidence produces changed_decision_count=0.
  2. A changed ticker action versus prior snapshot appears in changed_decisions.
  3. Missing timestamps return null/unknown, not fake freshness.
  4. attempted_llm_calls and live_provider_calls are always 0.
  5. Evidence age is computed correctly from known timestamps.
  6. No previous snapshot → previous_snapshot_id and previous_action_counts are None.
  7. Stale evidence count uses the documented 72-hour threshold.
  8. build_diagnostics combines freshness + diff correctly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services.intelligence.v3.snapshot_freshness_diagnostics import (
    STALE_EVIDENCE_THRESHOLD_HOURS,
    build_decision_diff,
    build_diagnostics,
    build_evidence_freshness,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot(action_map: dict[str, str], snapshot_id: str = "snap-A") -> dict[str, Any]:
    """Build a minimal snapshot dict with the given ticker→action mapping."""
    holdings = [{"ticker": t, "action": a} for t, a in action_map.items()]
    counts: dict[str, int] = {}
    for a in action_map.values():
        counts[a] = counts.get(a, 0) + 1
    return {
        "snapshot_id": snapshot_id,
        "action_counts": counts,
        "current_holdings": holdings,
    }


# ── build_evidence_freshness ──────────────────────────────────────────────────

class TestBuildEvidenceFreshness:
    def test_returns_zero_llm_and_provider_calls(self):
        """attempted_llm_calls and live_provider_calls must always be 0."""
        stats: dict[str, Any] = {
            "persisted_recommendation_count": 5,
            "persisted_agent_insight_count": 4,
            "active_position_count": 5,
            "missing_evidence_count": 1,
            "recommendation_timestamps": [],
            "agent_insight_run_timestamps": [],
        }
        result = build_evidence_freshness(stats)
        assert result["attempted_llm_calls"] == 0
        assert result["live_provider_calls"] == 0

    def test_evidence_mode_is_deterministic_policy(self):
        stats: dict[str, Any] = {"recommendation_timestamps": [], "agent_insight_run_timestamps": []}
        result = build_evidence_freshness(stats)
        assert result["evidence_mode"] == "deterministic_policy_over_persisted_evidence"

    def test_missing_timestamps_return_null_not_fake(self):
        """When no timestamps are available, age fields are None — no fabrication."""
        stats: dict[str, Any] = {
            "recommendation_timestamps": [],
            "agent_insight_run_timestamps": [],
        }
        result = build_evidence_freshness(stats)
        assert result["max_recommendation_age_hours"] is None
        assert result["max_agent_insight_age_hours"] is None
        assert result["oldest_source_timestamp"] is None
        assert result["newest_source_timestamp"] is None

    def test_age_computed_correctly_from_known_timestamp(self):
        """Given a known timestamp 48h ago, age should be approximately 48.0 hours."""
        now = _now()
        ts_48h_ago = _iso(now - timedelta(hours=48))
        stats: dict[str, Any] = {
            "recommendation_timestamps": [ts_48h_ago],
            "agent_insight_run_timestamps": [],
        }
        result = build_evidence_freshness(stats, now=now)
        assert result["max_recommendation_age_hours"] == pytest.approx(48.0, abs=0.1)

    def test_age_uses_oldest_timestamp_not_newest(self):
        """max age should be based on the oldest (worst) timestamp."""
        now = _now()
        ts_10h = _iso(now - timedelta(hours=10))
        ts_50h = _iso(now - timedelta(hours=50))
        stats: dict[str, Any] = {
            "recommendation_timestamps": [ts_10h, ts_50h],
            "agent_insight_run_timestamps": [],
        }
        result = build_evidence_freshness(stats, now=now)
        assert result["max_recommendation_age_hours"] == pytest.approx(50.0, abs=0.1)

    def test_stale_evidence_count_uses_72_hour_threshold(self):
        """Items older than STALE_EVIDENCE_THRESHOLD_HOURS (72h) count as stale."""
        assert STALE_EVIDENCE_THRESHOLD_HOURS == 72.0
        now = _now()
        ts_fresh = _iso(now - timedelta(hours=48))   # fresh
        ts_stale = _iso(now - timedelta(hours=96))   # stale
        stats: dict[str, Any] = {
            "recommendation_timestamps": [ts_fresh, ts_stale],
            "agent_insight_run_timestamps": [],
        }
        result = build_evidence_freshness(stats, now=now)
        assert result["stale_evidence_count"] == 1

    def test_no_stale_when_all_fresh(self):
        now = _now()
        ts_recent = _iso(now - timedelta(hours=24))
        stats: dict[str, Any] = {
            "recommendation_timestamps": [ts_recent],
            "agent_insight_run_timestamps": [ts_recent],
        }
        result = build_evidence_freshness(stats, now=now)
        assert result["stale_evidence_count"] == 0

    def test_invalid_timestamp_strings_are_ignored_gracefully(self):
        """Malformed timestamps should not crash; those entries are skipped."""
        stats: dict[str, Any] = {
            "recommendation_timestamps": ["not-a-date", None, ""],
            "agent_insight_run_timestamps": [],
        }
        result = build_evidence_freshness(stats)
        assert result["max_recommendation_age_hours"] is None
        assert result["oldest_source_timestamp"] is None

    def test_counts_passed_through_from_stats(self):
        stats: dict[str, Any] = {
            "persisted_recommendation_count": 10,
            "persisted_agent_insight_count": 8,
            "active_position_count": 12,
            "missing_evidence_count": 3,
            "recommendation_timestamps": [],
            "agent_insight_run_timestamps": [],
        }
        result = build_evidence_freshness(stats)
        assert result["recommendation_count"] == 10
        assert result["agent_insight_count"] == 8
        assert result["position_count"] == 12
        assert result["missing_evidence_count"] == 3

    def test_insight_age_from_run_timestamps(self):
        """agent_insight_run_timestamps drive max_agent_insight_age_hours."""
        now = _now()
        ts_30h = _iso(now - timedelta(hours=30))
        stats: dict[str, Any] = {
            "recommendation_timestamps": [],
            "agent_insight_run_timestamps": [ts_30h],
        }
        result = build_evidence_freshness(stats, now=now)
        assert result["max_agent_insight_age_hours"] == pytest.approx(30.0, abs=0.1)
        assert result["max_recommendation_age_hours"] is None


# ── build_decision_diff ───────────────────────────────────────────────────────

class TestBuildDecisionDiff:
    def test_identical_evidence_produces_zero_changed_count(self):
        """Same actions in both snapshots → changed_decision_count=0."""
        snap = _snapshot({"AAPL": "BUY", "MSFT": "HOLD", "NVDA": "TRIM"}, "snap-A")
        prev = _snapshot({"AAPL": "BUY", "MSFT": "HOLD", "NVDA": "TRIM"}, "snap-prev")
        result = build_decision_diff(snap, prev)
        assert result["changed_decision_count"] == 0
        assert result["changed_decisions"] == []

    def test_changed_ticker_action_appears_in_changed_decisions(self):
        """When AAPL changes from HOLD to BUY, it must appear in changed_decisions."""
        current = _snapshot({"AAPL": "BUY", "MSFT": "HOLD"}, "snap-B")
        previous = _snapshot({"AAPL": "HOLD", "MSFT": "HOLD"}, "snap-A")
        result = build_decision_diff(current, previous)
        assert result["changed_decision_count"] == 1
        assert len(result["changed_decisions"]) == 1
        changed = result["changed_decisions"][0]
        assert changed["ticker"] == "AAPL"
        assert changed["previous_action"] == "HOLD"
        assert changed["current_action"] == "BUY"

    def test_multiple_changed_tickers_all_appear(self):
        current = _snapshot({"AAPL": "BUY", "MSFT": "TRIM", "NVDA": "HOLD"}, "snap-C")
        previous = _snapshot({"AAPL": "HOLD", "MSFT": "HOLD", "NVDA": "HOLD"}, "snap-B")
        result = build_decision_diff(current, previous)
        assert result["changed_decision_count"] == 2
        tickers_changed = {d["ticker"] for d in result["changed_decisions"]}
        assert tickers_changed == {"AAPL", "MSFT"}

    def test_no_previous_snapshot_returns_null_id_and_counts(self):
        """When previous_snapshot is None, previous_snapshot_id and previous_action_counts are None."""
        current = _snapshot({"AAPL": "BUY"}, "snap-first")
        result = build_decision_diff(current, None)
        assert result["previous_snapshot_id"] is None
        assert result["previous_action_counts"] is None
        assert result["changed_decision_count"] == 0
        assert result["changed_decisions"] == []

    def test_previous_snapshot_id_propagated(self):
        current = _snapshot({"AAPL": "BUY"}, "snap-new")
        previous = _snapshot({"AAPL": "BUY"}, "snap-old-id")
        result = build_decision_diff(current, previous)
        assert result["previous_snapshot_id"] == "snap-old-id"

    def test_unchanged_count_is_common_unchanged_tickers(self):
        """unchanged_decision_count = tickers in both with identical action."""
        current = _snapshot({"AAPL": "BUY", "MSFT": "HOLD", "GOOG": "BUY"}, "snap-C")
        previous = _snapshot({"AAPL": "HOLD", "MSFT": "HOLD", "GOOG": "BUY"}, "snap-B")
        result = build_decision_diff(current, previous)
        # AAPL changed, MSFT unchanged, GOOG unchanged → unchanged=2, changed=1
        assert result["unchanged_decision_count"] == 2
        assert result["changed_decision_count"] == 1

    def test_ticker_only_in_current_not_counted_as_changed(self):
        """New tickers (not in previous) are not counted in changed_decisions."""
        current = _snapshot({"AAPL": "BUY", "NEW": "HOLD"}, "snap-C")
        previous = _snapshot({"AAPL": "BUY"}, "snap-B")
        result = build_decision_diff(current, previous)
        assert result["changed_decision_count"] == 0
        assert result["unchanged_decision_count"] == 1  # only AAPL is common

    def test_current_action_counts_always_present(self):
        current = _snapshot({"AAPL": "BUY", "MSFT": "HOLD"}, "snap-X")
        result = build_decision_diff(current, None)
        assert result["current_action_counts"] == {"BUY": 1, "HOLD": 1}

    def test_previous_action_counts_when_previous_exists(self):
        current = _snapshot({"AAPL": "BUY"}, "snap-new")
        previous = _snapshot({"AAPL": "HOLD", "MSFT": "HOLD"}, "snap-old")
        result = build_decision_diff(current, previous)
        assert result["previous_action_counts"] == {"HOLD": 2}


# ── build_diagnostics (integration) ──────────────────────────────────────────

class TestBuildDiagnostics:
    def test_combines_freshness_and_diff(self):
        """build_diagnostics returns merged freshness + diff fields."""
        now = _now()
        ts = _iso(now - timedelta(hours=12))
        stats: dict[str, Any] = {
            "persisted_recommendation_count": 3,
            "persisted_agent_insight_count": 2,
            "active_position_count": 3,
            "missing_evidence_count": 0,
            "recommendation_timestamps": [ts],
            "agent_insight_run_timestamps": [],
        }
        current = _snapshot({"AAPL": "BUY", "MSFT": "HOLD"}, "snap-new")
        previous = _snapshot({"AAPL": "BUY", "MSFT": "HOLD"}, "snap-old")
        result = build_diagnostics(
            evidence_stats=stats,
            current_snapshot=current,
            previous_snapshot=previous,
            now=now,
        )
        # Freshness fields
        assert result["attempted_llm_calls"] == 0
        assert result["live_provider_calls"] == 0
        assert result["evidence_mode"] == "deterministic_policy_over_persisted_evidence"
        assert result["max_recommendation_age_hours"] == pytest.approx(12.0, abs=0.1)
        # Diff fields
        assert result["changed_decision_count"] == 0
        assert result["previous_snapshot_id"] == "snap-old"

    def test_no_previous_snapshot_in_combined(self):
        stats: dict[str, Any] = {
            "recommendation_timestamps": [],
            "agent_insight_run_timestamps": [],
        }
        current = _snapshot({"AAPL": "BUY"}, "snap-first")
        result = build_diagnostics(
            evidence_stats=stats,
            current_snapshot=current,
            previous_snapshot=None,
        )
        assert result["previous_snapshot_id"] is None
        assert result["previous_action_counts"] is None
        assert result["attempted_llm_calls"] == 0
