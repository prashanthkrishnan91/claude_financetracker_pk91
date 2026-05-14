"""Read-only evidence adapter for Intel v3 run path.

Loads persisted recommendation/card evidence without invoking legacy aggregation
or LLM generation paths.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from ....database import get_supabase_client


class ReadOnlyEvidenceAdapter:
    def __init__(self, user_id: UUID):
        self.user_id = user_id
        self.client = get_supabase_client()

    async def load_cards(self) -> tuple[list[Any], dict[str, Any]]:
        rec_rows = await asyncio.to_thread(
            lambda: self.client.table("recommendations")
            .select("id,ticker,action,technical_signal,conviction_score,agent_run_id,is_active,created_at")
            .eq("user_id", str(self.user_id))
            .eq("is_active", True)
            .execute()
        )
        recs = rec_rows.data or []
        tickers = [r.get("ticker") for r in recs if r.get("ticker")]

        # Per-ticker map of the active recommendation's agent_run_id — used to
        # prefer the matching agent_insight over insights from older runs.
        rec_run_id_by_ticker: dict[str, str] = {}
        for r in recs:
            t = r.get("ticker")
            rid = r.get("agent_run_id")
            if t and rid:
                rec_run_id_by_ticker[t] = str(rid)

        pos_rows = await asyncio.to_thread(
            lambda: self.client.table("positions")
            .select("ticker,name,category")
            .eq("user_id", str(self.user_id))
            .execute()
        )
        positions = {r.get("ticker"): r for r in (pos_rows.data or []) if r.get("ticker")}

        run_rows = await asyncio.to_thread(
            lambda: self.client.table("agent_runs")
            .select("id,finished_at,status,allocation")
            .eq("user_id", str(self.user_id))
            .eq("status", "completed")
            .order("finished_at", desc=True)
            .limit(25)
            .execute()
        )
        completed = run_rows.data or []
        run_ids = [str(r.get("id")) for r in completed if r.get("id")]

        # Build run_id → finished_at for insight timestamp attribution.
        run_finished_at: dict[str, str] = {
            str(r["id"]): r["finished_at"]
            for r in completed
            if r.get("id") and r.get("finished_at")
        }

        # Include rec agent_run_ids that may fall outside the 25 most recent
        # completed runs (rare but correct to cover).
        rec_run_ids_set = set(rec_run_id_by_ticker.values())
        all_query_run_ids = list(dict.fromkeys(
            run_ids + [rid for rid in rec_run_ids_set if rid not in set(run_ids)]
        ))

        # Two-level lookup for agent_insights:
        #   ai_run_lookup: (ticker, run_id) → row  — for matched-run preference
        #   ai_fallback:   ticker → row            — best available (most recent run)
        ai_run_lookup: dict[tuple[str, str], dict] = {}
        ai_fallback: dict[str, dict] = {}

        if all_query_run_ids and tickers:
            ai_rows = await asyncio.to_thread(
                lambda: self.client.table("agent_insights")
                .select("run_id,ticker,analyst_verdict,analyst_confidence,created_at")
                .eq("user_id", str(self.user_id))
                .in_("run_id", all_query_run_ids)
                .in_("ticker", tickers)
                .execute()
            )
            for row in (ai_rows.data or []):
                rid = str(row.get("run_id") or "")
                tk = row.get("ticker") or ""
                if not (rid and tk):
                    continue
                ai_run_lookup[(tk, rid)] = row
                # Fallback: prefer the insight from the most recently finished run.
                prev = ai_fallback.get(tk)
                if prev is None:
                    ai_fallback[tk] = row
                else:
                    cur_ts = run_finished_at.get(rid) or row.get("created_at") or ""
                    prev_rid = str(prev.get("run_id") or "")
                    prev_ts = run_finished_at.get(prev_rid) or prev.get("created_at") or ""
                    if cur_ts > prev_ts:
                        ai_fallback[tk] = row

        # Collect timestamps for freshness diagnostics.
        # recommendation_timestamps: created_at of each active recommendation.
        recommendation_timestamps: list[str] = [
            r["created_at"] for r in recs if r.get("created_at")
        ]

        cards = []
        missing = 0
        stale_or_missing_source_count = 0
        matched_by_run_count = 0
        fallback_by_ticker_count = 0
        missing_insight_for_run_count = 0
        # agent_insight_run_timestamps: finished_at (or created_at) of the
        # matched insight's run — one entry per card that has any insight.
        agent_insight_run_timestamps: list[str] = []

        for rec in recs:
            t = rec.get("ticker") or "UNKNOWN"
            pos = positions.get(t, {})
            rec_agent_run_id = rec_run_id_by_ticker.get(t)

            # Prefer insight from the same run as the active recommendation.
            ai: dict = {}
            if rec_agent_run_id:
                matched = ai_run_lookup.get((t, rec_agent_run_id))
                if matched is not None:
                    ai = matched
                    matched_by_run_count += 1
                else:
                    missing_insight_for_run_count += 1
                    fallback = ai_fallback.get(t)
                    if fallback is not None:
                        ai = fallback
                        fallback_by_ticker_count += 1
            else:
                fallback = ai_fallback.get(t)
                if fallback is not None:
                    ai = fallback
                    fallback_by_ticker_count += 1

            # Attribute insight timestamp to its run's finished_at if known.
            ai_rid = str(ai.get("run_id") or "")
            insight_ts = run_finished_at.get(ai_rid) or ai.get("created_at") or ""
            if ai and insight_ts:
                agent_insight_run_timestamps.append(insight_ts)

            av = ai.get("analyst_verdict") or {}
            risks = av.get("risks") or []
            drivers = av.get("drivers") or []
            conviction_level = av.get("conviction_level") or ("MEDIUM" if float(rec.get("conviction_score") or 0) >= 0.66 else "LOW")
            primary_driver = av.get("primary_driver") or av.get("why")
            action_reason = av.get("action_reason") or av.get("do")
            intel_read = av.get("intel_read") if isinstance(av, dict) else None
            thesis_v2 = None
            if not primary_driver:
                missing += 1
            if not ai:
                stale_or_missing_source_count += 1
            cards.append(SimpleNamespace(
                ticker=t,
                name=pos.get("name") or t,
                action=(rec.get("action") or "HOLD").upper(),
                analyst_action=(av.get("action") or rec.get("action") or "HOLD").upper(),
                conviction_level=conviction_level,
                technical_signal=rec.get("technical_signal"),
                risk_flag=av.get("risk_flag") or "",
                analyst_risks=risks if isinstance(risks, list) else [],
                category=pos.get("category") or "stock",
                data_quality_label=av.get("data_quality_label") or "MEDIUM",
                intel_read=intel_read if isinstance(intel_read, dict) else None,
                thesis_v2=thesis_v2,
                analyst_used_fallback=bool(av.get("used_fallback", False)),
                primary_driver=primary_driver,
                action_reason=action_reason,
                analyst_drivers=drivers if isinstance(drivers, list) else [],
            ))

        stats: dict[str, Any] = {
            "active_position_count": len(positions),
            "persisted_recommendation_count": len(recs),
            "persisted_agent_insight_count": len(ai_fallback),
            "missing_recommendation_count": max(0, len(positions) - len(recs)),
            "missing_evidence_count": missing,
            "stale_or_missing_source_count": stale_or_missing_source_count,
            "generated_legacy_recommendations": False,
            "attempted_llm_calls": 0,
            # Freshness timestamps — used by snapshot_freshness_diagnostics.
            "recommendation_timestamps": recommendation_timestamps,
            "agent_insight_run_timestamps": agent_insight_run_timestamps,
            # Insight-matching diagnostics.
            "matched_agent_insight_by_recommendation_run_count": matched_by_run_count,
            "fallback_agent_insight_by_ticker_count": fallback_by_ticker_count,
            "missing_agent_insight_for_recommendation_run_count": missing_insight_for_run_count,
            "recommendation_agent_run_ids_count": len(rec_run_ids_set),
        }
        return cards, stats
