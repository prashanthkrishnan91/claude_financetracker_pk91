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

        # PR 3B observability: track trusted-signal mapping across all cards.
        mapped_existing_analyst_signal_count = 0
        trusted_signal_count_distribution: dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 0}

        # Template/fallback phrases that must not count as trusted evidence signals.
        # These are produced by the analyst when data_quality_score < 0.4 (INSUFFICIENT_DATA).
        _FALLBACK_PHRASES: frozenset[str] = frozenset({
            "no clear edge vs alternatives",
            "limited data reduces conviction",
            "hold — no allocation until signal improves",
            "hold - no allocation until signal improves",
            "hold — no allocation until signal improves",
        })

        def _is_real_signal(text: Any) -> bool:
            """Return True when text is a non-empty, non-template analyst string.

            Normalizes trailing punctuation before phrase matching so "No clear edge
            vs alternatives." (with period) matches the stored fallback phrase set.
            """
            if not text or not isinstance(text, str):
                return False
            stripped = text.strip()
            if not stripped:
                return False
            normalized = stripped.rstrip(".!?").strip()
            return bool(normalized) and normalized.lower() not in _FALLBACK_PHRASES

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
            # AnalystVerdict.to_dict() serializes the field as "key_drivers" (matching
            # the dataclass attribute). The fallback writeback path uses "drivers".
            # Read both to support both write paths.
            drivers = av.get("key_drivers") or av.get("drivers") or []
            conviction_level = av.get("conviction_level") or ("MEDIUM" if float(rec.get("conviction_score") or 0) >= 0.66 else "LOW")
            primary_driver = av.get("primary_driver") or av.get("why")
            action_reason = av.get("action_reason") or av.get("do")
            # intel_read is a legacy _reasoning_v2 field — not written by agent_insights.
            # We synthesize a compatible structure below from real analyst_verdict fields.
            legacy_intel_read = av.get("intel_read") if isinstance(av, dict) else None
            thesis_v2 = None
            if not primary_driver:
                missing += 1
            if not ai:
                stale_or_missing_source_count += 1

            # ── Synthesize intel_read from existing analyst_verdict fields ────────
            # Root-cause fix for PR 3B: AnalystVerdict never includes intel_read or
            # data_quality_label, so the adapter was defaulting to "MEDIUM" for every
            # card — inflating all evidence bands to PARTIAL regardless of content.
            #
            # Instead, count genuine analyst evidence dimensions from persisted fields:
            #   primary_driver   → "analyst_primary_driver"
            #   action_reason    → "analyst_action_rationale"
            #   key_drivers list → "analyst_key_drivers"
            #
            # Governance: only source-linked fields from the matched analyst_verdict
            # are counted. action / conviction are NOT counted (must not derive evidence
            # quality from action or conviction per repo invariant). Template/fallback
            # phrases are excluded. Research artifacts remain locked (safe_for_decision=
            # FALSE) and are never consumed here.
            synthetic_trusted_dims: list[str] = []
            analyst_used_fallback = bool(av.get("used_fallback", False))

            if ai and not analyst_used_fallback:
                if _is_real_signal(primary_driver):
                    synthetic_trusted_dims.append("analyst_primary_driver")
                if _is_real_signal(action_reason):
                    synthetic_trusted_dims.append("analyst_action_rationale")
                key_drivers_list = av.get("key_drivers") or []
                if isinstance(key_drivers_list, list) and any(
                    _is_real_signal(d) for d in key_drivers_list
                ):
                    synthetic_trusted_dims.append("analyst_key_drivers")

            # Build the resolved intel_read:
            # Priority: synthetic (from analyst_verdict) > legacy (from _reasoning_v2)
            # When legacy intel_read exists it already has trusted_signals populated.
            resolved_intel_read: Any = None
            if synthetic_trusted_dims:
                resolved_intel_read = {
                    "trusted_signals": synthetic_trusted_dims,
                    "source": "analyst_verdict_synthesis",
                }
                mapped_existing_analyst_signal_count += 1
            elif legacy_intel_read and isinstance(legacy_intel_read, dict):
                resolved_intel_read = legacy_intel_read

            n_trusted = len(synthetic_trusted_dims)
            trusted_signal_count_distribution[min(n_trusted, 3)] = (
                trusted_signal_count_distribution.get(min(n_trusted, 3), 0) + 1
            )

            # data_quality_label: only use the explicit label from analyst_verdict.
            # Do NOT default to "MEDIUM" — that inflated all cards to PARTIAL regardless
            # of whether analyst evidence was actually present.
            resolved_dql = av.get("data_quality_label") or None

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
                data_quality_label=resolved_dql,
                intel_read=resolved_intel_read,
                thesis_v2=thesis_v2,
                analyst_used_fallback=analyst_used_fallback,
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
            # PR 3B: trusted-signal mapping observability.
            # mapped_existing_analyst_signal_count: cards where synthetic intel_read
            # was built from real analyst_verdict fields (evidence upgrade path).
            "mapped_existing_analyst_signal_count": mapped_existing_analyst_signal_count,
            # trusted_signal_count_distribution: histogram of 0/1/2/3 trusted signals
            # per card after synthesis. Proves the fix without per-ticker payloads.
            "trusted_signal_count_distribution": trusted_signal_count_distribution,
            # Research artifact governance: confirmed locked at safe_for_decision=FALSE
            # in production schema (017_research_artifact_store_v1.sql CHECK constraint).
            # No artifacts are consumed for evidence-band uplift in this PR.
            "artifact_decision_safe_count": 0,
            "artifact_suppressed_unsafe_count": 0,
        }
        return cards, stats
