"""Snapshot freshness diagnostics for Intel v3.

Pure module — no IO, no DB, no LLM calls.

Provides two entry points:
  build_evidence_freshness(evidence_stats) → freshness metadata dict
  build_decision_diff(current_snapshot, previous_snapshot) → diff dict

Timestamps are sourced from the evidence adapter stats:
  - recommendation_timestamps: recommendations.created_at (per active ticker rec)
  - agent_insight_run_timestamps: agent_runs.finished_at for each ticker's insight run

If a timestamp is unavailable, the relevant field returns None rather than a guess.
Stale threshold: 72 hours (conservative; documented here as the single source of truth).

Stage 3.0b.6: the banner age summary now reports recommendation evidence and
agent-insight evidence separately so the UI never claims "Oldest evidence: 8d"
when agent insight evidence is actually ~12d stale. The plain-English summary
is built here so the frontend never has to guess which age applies.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

# Conservative stale threshold. Exposed so tests can assert the value.
STALE_EVIDENCE_THRESHOLD_HOURS: float = 72.0


def _parse_iso(ts: Any) -> Optional[datetime]:
    """Parse an ISO timestamp string to a timezone-aware datetime. Returns None on failure."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def build_evidence_freshness(
    evidence_stats: dict[str, Any],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Compute evidence freshness metadata from evidence adapter stats.

    Returns a dict with source mode, call counts, evidence counts, and age fields.
    All age/timestamp fields are None when timestamps are unavailable — no fabrication.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    rec_timestamps = evidence_stats.get("recommendation_timestamps") or []
    insight_timestamps = evidence_stats.get("agent_insight_run_timestamps") or []

    rec_dts = [dt for ts in rec_timestamps if (dt := _parse_iso(ts)) is not None]
    insight_dts = [dt for ts in insight_timestamps if (dt := _parse_iso(ts)) is not None]

    all_dts = rec_dts + insight_dts

    oldest_source_timestamp: Optional[str] = None
    newest_source_timestamp: Optional[str] = None
    max_recommendation_age_hours: Optional[float] = None
    max_agent_insight_age_hours: Optional[float] = None
    stale_evidence_count = 0

    if all_dts:
        oldest_source_timestamp = min(all_dts).isoformat()
        newest_source_timestamp = max(all_dts).isoformat()

    if rec_dts:
        oldest_rec = min(rec_dts)
        max_recommendation_age_hours = round(
            (now - oldest_rec).total_seconds() / 3600, 1
        )

    if insight_dts:
        oldest_insight = min(insight_dts)
        max_agent_insight_age_hours = round(
            (now - oldest_insight).total_seconds() / 3600, 1
        )

    if all_dts:
        stale_evidence_count = sum(
            1 for dt in all_dts
            if (now - dt).total_seconds() / 3600 > STALE_EVIDENCE_THRESHOLD_HOURS
        )

    return {
        "evidence_mode": "deterministic_policy_over_persisted_evidence",
        "attempted_llm_calls": 0,
        "live_provider_calls": 0,
        "recommendation_count": evidence_stats.get("persisted_recommendation_count", 0),
        "agent_insight_count": evidence_stats.get("persisted_agent_insight_count", 0),
        "position_count": evidence_stats.get("active_position_count", 0),
        "missing_evidence_count": evidence_stats.get("missing_evidence_count", 0),
        "stale_evidence_count": stale_evidence_count,
        "max_recommendation_age_hours": max_recommendation_age_hours,
        "max_agent_insight_age_hours": max_agent_insight_age_hours,
        "oldest_source_timestamp": oldest_source_timestamp,
        "newest_source_timestamp": newest_source_timestamp,
        # Plain-English per-source age summary (Stage 3.0b.6). Single source
        # of truth for the banner line — both ages reported separately when
        # either is available; "Evidence age: unknown." when both are None.
        "banner_age_summary": _format_banner_age_summary(
            max_recommendation_age_hours,
            max_agent_insight_age_hours,
        ),
    }


def _format_banner_age_summary(
    max_recommendation_age_hours: Optional[float],
    max_agent_insight_age_hours: Optional[float],
) -> str:
    """Plain-English age summary for both analyst sources.

    Examples (per task brief):
      - "Analyst evidence: 12.0 days old. Recommendation evidence: 8.0 days old."
      - "Recommendation evidence: 8.0 days old."  (analyst age missing)
      - "Evidence age: unknown."                  (both ages missing)
    """
    if max_recommendation_age_hours is None and max_agent_insight_age_hours is None:
        return "Evidence age: unknown."
    parts: list[str] = []
    if max_agent_insight_age_hours is not None:
        parts.append(f"Analyst evidence: {_human_age(max_agent_insight_age_hours)} old.")
    if max_recommendation_age_hours is not None:
        parts.append(f"Recommendation evidence: {_human_age(max_recommendation_age_hours)} old.")
    return " ".join(parts)


def _human_age(hours: float) -> str:
    if hours < 24.0:
        return f"{hours:.1f}h"
    return f"{hours / 24.0:.1f} days"


def build_decision_diff(
    current_snapshot: dict[str, Any],
    previous_snapshot: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Compute prior-vs-current decision diff.

    Compares actions for tickers present in both snapshots.
    Tickers added or removed between runs are not counted in changed_decision_count
    (only tickers present in both runs are comparable).
    Returns previous_snapshot_id=None and previous_action_counts=None when there
    is no previous snapshot — callers must not infer a fake prior state.
    """
    current_action_counts: dict[str, int] = current_snapshot.get("action_counts", {})
    current_cards: dict[str, str] = {
        c["ticker"]: c["action"]
        for c in current_snapshot.get("current_holdings", [])
        if c.get("ticker") and c.get("action")
    }

    if previous_snapshot is None:
        return {
            "previous_snapshot_id": None,
            "previous_action_counts": None,
            "current_action_counts": current_action_counts,
            "changed_decision_count": 0,
            "changed_decisions": [],
            "unchanged_decision_count": len(current_cards),
        }

    previous_snapshot_id: Optional[str] = previous_snapshot.get("snapshot_id")
    previous_action_counts: dict[str, int] = previous_snapshot.get("action_counts", {})
    prev_cards: dict[str, str] = {
        c["ticker"]: c["action"]
        for c in previous_snapshot.get("current_holdings", [])
        if c.get("ticker") and c.get("action")
    }

    changed_decisions = []
    unchanged_count = 0

    common_tickers = sorted(set(current_cards) & set(prev_cards))
    for ticker in common_tickers:
        prev_action = prev_cards[ticker]
        curr_action = current_cards[ticker]
        if prev_action != curr_action:
            changed_decisions.append({
                "ticker": ticker,
                "previous_action": prev_action,
                "current_action": curr_action,
            })
        else:
            unchanged_count += 1

    return {
        "previous_snapshot_id": previous_snapshot_id,
        "previous_action_counts": previous_action_counts,
        "current_action_counts": current_action_counts,
        "changed_decision_count": len(changed_decisions),
        "changed_decisions": changed_decisions,
        "unchanged_decision_count": unchanged_count,
    }


def build_diagnostics(
    *,
    evidence_stats: dict[str, Any],
    current_snapshot: dict[str, Any],
    previous_snapshot: Optional[dict[str, Any]],
    refresh_diagnostics: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Combine freshness metadata, decision diff, and refresh orchestrator state.

    `refresh_diagnostics` is the dict produced by
    `EvidenceRefreshOrchestrator.run().to_diagnostics_dict()`. When provided its
    fields override the legacy `attempted_llm_calls` / `live_provider_calls`
    placeholders (legacy=0) with the real counts from the orchestrator, and add
    run_mode / source_freshness / refresh_counts to the snapshot.

    When `refresh_diagnostics` is None the legacy diagnostics shape is preserved
    so callers that haven't migrated still work.
    """
    freshness = build_evidence_freshness(evidence_stats, now=now)
    diff = build_decision_diff(current_snapshot, previous_snapshot)
    combined: dict[str, Any] = {**freshness, **diff}

    if refresh_diagnostics:
        # The orchestrator owns the truth for these fields once it has run.
        # Map legacy aliases onto the new authoritative values so existing log
        # parsers / tests still see `attempted_llm_calls` and
        # `live_provider_calls` populated correctly.
        combined.update(refresh_diagnostics)
        combined["attempted_llm_calls"] = refresh_diagnostics.get(
            "attempted_llm_calls", combined.get("attempted_llm_calls", 0)
        )
        # `live_provider_calls` is the legacy alias for successful provider
        # calls — keep both keys in the diagnostics dict to avoid breaking
        # earlier parsers.
        combined["live_provider_calls"] = refresh_diagnostics.get(
            "successful_provider_calls", combined.get("live_provider_calls", 0)
        )
        # The classic `evidence_mode` field becomes informative — when the
        # orchestrator ran we set it to the post-refresh certified mode so
        # downstream UI never reports "deterministic_policy_over_persisted_evidence"
        # for a run that actually attempted refresh.
        run_mode = refresh_diagnostics.get("run_mode")
        if run_mode:
            combined["evidence_mode"] = f"deterministic_policy_over_{run_mode.lower()}_evidence"
    return combined
