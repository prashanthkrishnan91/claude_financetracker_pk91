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

    async def load_cards(self) -> tuple[list[Any], dict[str, int]]:
        rec_rows = await asyncio.to_thread(
            lambda: self.client.table("recommendations")
            .select("id,ticker,action,technical_signal,conviction_score,agent_run_id,is_active")
            .eq("user_id", str(self.user_id))
            .eq("is_active", True)
            .execute()
        )
        recs = rec_rows.data or []
        tickers = [r.get("ticker") for r in recs if r.get("ticker")]

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

        ai_lookup = {}
        if run_ids and tickers:
            ai_rows = await asyncio.to_thread(
                lambda: self.client.table("agent_insights")
                .select("run_id,ticker,analyst_verdict,analyst_confidence")
                .eq("user_id", str(self.user_id))
                .in_("run_id", run_ids)
                .in_("ticker", tickers)
                .execute()
            )
            for row in (ai_rows.data or []):
                rid = str(row.get("run_id") or "")
                tk = row.get("ticker")
                if rid and tk and (tk not in ai_lookup):
                    ai_lookup[tk] = row

        cards=[]
        missing=0
        stale_or_missing_source_count=0
        for rec in recs:
            t = rec.get("ticker") or "UNKNOWN"
            pos = positions.get(t, {})
            ai = ai_lookup.get(t, {})
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
                analyst_risks=risks if isinstance(risks,list) else [],
                category=pos.get("category") or "stock",
                data_quality_label=av.get("data_quality_label") or "MEDIUM",
                intel_read=intel_read if isinstance(intel_read, dict) else None,
                thesis_v2=thesis_v2,
                analyst_used_fallback=bool(av.get("used_fallback", False)),
                primary_driver=primary_driver,
                action_reason=action_reason,
                analyst_drivers=drivers if isinstance(drivers,list) else [],
            ))

        stats = {
            "active_position_count": len(positions),
            "persisted_recommendation_count": len(recs),
            "persisted_agent_insight_count": len(ai_lookup),
            "missing_recommendation_count": max(0, len(positions) - len(recs)),
            "missing_evidence_count": missing,
            "stale_or_missing_source_count": stale_or_missing_source_count,
            "generated_legacy_recommendations": False,
            "attempted_llm_calls": 0,
        }
        return cards, stats
