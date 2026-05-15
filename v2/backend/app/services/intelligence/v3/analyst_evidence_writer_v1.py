"""Explicit analyst evidence writeback for the background worker (Stage 3.2b).

Root-cause context
------------------
``AgentOrchestrator._persist_sync`` is dispatched via
``asyncio.to_thread(self._persist_sync, state)`` using the orchestrator's
instance-level ``self.db`` client (created at ``__init__`` time).  In the
Railway worker process that client silently fails inside the thread — the
exception is caught by the try/except at ``orchestrator.run():567–575``, so
``run()`` returns ``status="completed"`` with 34 insights in memory but zero
rows in ``agent_insights`` / ``recommendations``.  The existing
``_read_post_run_evidence`` readback uses a fresh ``get_supabase_client()``
call and works fine, confirming the thread/process isolation issue is specific
to the orchestrator's instance client.

This writer is the explicit fallback bridge:
  1. Called after ``orch.run()`` returns ``status="completed"`` with non-empty
     insights, before the post-run readback.
  2. Uses a fresh ``get_supabase_client()`` — NOT the orchestrator's instance
     client — so the same isolation issue cannot silently suppress the writes.
  3. Checks which rows already exist for this ``agent_run_id`` (idempotency)
     and only inserts the missing ones.
  4. Writes only from actual ``AgentPipelineResult.insights`` fields — no
     fabricated verdicts or LLM content.
  5. Does NOT replace ``AgentOrchestrator._persist_sync``; that path still runs
     first and may succeed.  This writer is the guaranteed safety net for when
     it does not.

Hard boundaries
---------------
* Never fabricates analyst verdicts or LLM-generated content.
* Never marks a job succeeded when the write itself fails.
* Never writes ``intel_v3_snapshots`` or any visible decision field.
* Never imports the deterministic Intel v3 decision policy (``decide``).
* ``used_fallback=False`` in the derived ``analyst_verdict`` is honest: the LLM
  DID run and returned insights — the only failure was silent DB-write loss.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from ....database import get_supabase_client

logger = logging.getLogger(__name__)


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class AnalystEvidenceWriteResult:
    """Outcome of one explicit writeback pass."""
    insights_written: int = 0
    recommendations_written: int = 0
    insights_already_present: int = 0
    recommendations_already_present: int = 0
    write_error: Optional[str] = None

    @property
    def persisted_count(self) -> int:
        return self.insights_written + self.recommendations_written

    def to_dict(self) -> dict[str, Any]:
        return {
            "insights_written": self.insights_written,
            "recommendations_written": self.recommendations_written,
            "insights_already_present": self.insights_already_present,
            "recommendations_already_present": self.recommendations_already_present,
            "write_error": self.write_error,
            "persisted_count": self.persisted_count,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _derive_conviction_level(conviction_score: Optional[float]) -> str:
    score = float(conviction_score or 0.0)
    if score >= 0.7:
        return "HIGH"
    if score >= 0.4:
        return "MEDIUM"
    return "LOW"


def _round_score(v: Optional[float]) -> Optional[float]:
    return round(v, 2) if v is not None else None


def _build_analyst_verdict_from_insight(
    insight: Any,
    *,
    verdict_dict: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build ``analyst_verdict`` from the live ``AnalystVerdict`` dict when available.

    When ``verdict_dict`` is provided (the ``AnalystVerdict.to_dict()`` from the
    LLM run), use it directly so ``primary_driver`` / ``risk_flag`` /
    ``action_reason`` / ``differentiation`` and all other structured fields are
    preserved faithfully — identical to what ``AgentOrchestrator._persist_sync``
    would have written had the DB client not failed.

    Falls back to deriving ``primary_driver`` from ``TickerInsight.investment_thesis``
    only when the real verdict is unavailable (e.g. the orchestrator did not
    populate ``_verdicts`` for this ticker or the caller did not pass one).
    """
    if verdict_dict is not None and isinstance(verdict_dict, dict):
        out = dict(verdict_dict)
        # Stamp the write path so readers can distinguish explicit-writeback rows
        # from rows the orchestrator's own persist step wrote.
        out["analysis_source"] = out.get("analysis_source") or "explicit_writeback"
        # used_fallback stays as-is from the verdict: the LLM DID run.
        return out

    # ── Fallback: derive from TickerInsight only (verdict unavailable) ────────
    action = (getattr(insight, "suggested_action", None) or "HOLD").upper()
    conviction_score = getattr(insight, "conviction_score", None)
    investment_thesis = getattr(insight, "investment_thesis", None) or ""
    sentiment_label = getattr(insight, "sentiment_label", None)
    technical_signal = getattr(insight, "technical_signal", None)

    primary_driver: Optional[str] = None
    if investment_thesis:
        first = investment_thesis.split(".")[0].strip()
        if len(first) > 10:
            primary_driver = first[:200]

    return {
        "action": action,
        "conviction_level": _derive_conviction_level(conviction_score),
        "primary_driver": primary_driver,
        "action_reason": None,
        "why": None,
        "do": None,
        "risk_flag": "",
        "drivers": [],
        "risks": [],
        "data_quality_label": "MEDIUM",
        "used_fallback": False,
        "analysis_source": "explicit_writeback",
        "sentiment_label": sentiment_label,
        "technical_signal": technical_signal,
    }


# ── Synchronous inner writer (runs in asyncio.to_thread) ─────────────────────

def _write_sync(
    user_id: UUID,
    agent_run_id: str,
    insights: list[Any],
    *,
    now_iso: str,
    verdicts: Optional[dict[str, Any]] = None,
    scoped_tickers: Optional[list[str]] = None,
) -> AnalystEvidenceWriteResult:
    """Write missing ``agent_insights`` and ``recommendations`` rows.

    Uses a fresh ``get_supabase_client()`` — not the orchestrator's instance
    client — so thread/process-level connection issues that silenced
    ``_persist_sync`` cannot affect this path.

    Idempotency contract
    --------------------
    * ``agent_insights``: ``UNIQUE(run_id, ticker)`` — we check existing rows
      first and only insert the missing tickers.  No existing row is overwritten.
    * ``recommendations``: no unique constraint on ``(user_id, ticker,
      agent_run_id)`` — we check existing rows first and only insert missing
      ones.  If the legacy path partially succeeded, we fill the gaps only.
    """
    result = AnalystEvidenceWriteResult()
    client = get_supabase_client()
    user_str = str(user_id)

    # ── Step 1: which agent_insights rows already exist for this run? ─────────
    existing_insight_tickers: set[str] = set()
    try:
        res = (
            client.table("agent_insights")
            .select("ticker")
            .eq("user_id", user_str)
            .eq("run_id", agent_run_id)
            .execute()
        )
        for row in res.data or []:
            t = (row.get("ticker") or "").upper()
            if t:
                existing_insight_tickers.add(t)
    except Exception as exc:
        logger.warning(
            "analyst_evidence_writer.insight_check_failed user_id=%s run_id=%s err=%s",
            user_id, agent_run_id, exc,
        )

    # ── Step 2: build and insert missing agent_insights rows ─────────────────
    insight_rows: list[dict[str, Any]] = []
    for insight in insights:
        ticker = (getattr(insight, "ticker", None) or "").strip()
        if not ticker:
            continue
        if ticker.upper() in existing_insight_tickers:
            result.insights_already_present += 1
            continue
        row: dict[str, Any] = {
            "run_id": agent_run_id,
            "user_id": user_str,
            "ticker": ticker,
            "investment_thesis": getattr(insight, "investment_thesis", None) or "",
            "sentiment_score": _round_score(getattr(insight, "sentiment_score", None)),
            "sentiment_label": getattr(insight, "sentiment_label", None),
            "technical_signal": getattr(insight, "technical_signal", None),
            "technical_summary": getattr(insight, "technical_summary", None) or "",
            "fundamental_score": _round_score(getattr(insight, "fundamental_score", None)),
            "fundamental_summary": getattr(insight, "fundamental_summary", None) or "",
            "conviction_score": _round_score(getattr(insight, "conviction_score", None)),
            "suggested_allocation": round(
                float(getattr(insight, "suggested_allocation", 0) or 0), 2
            ),
            "suggested_action": (
                getattr(insight, "suggested_action", None) or "HOLD"
            ).upper(),
            "analyst_verdict": _build_analyst_verdict_from_insight(
                insight,
                verdict_dict=(verdicts or {}).get(ticker.upper()),
            ),
            "analyst_confidence": _round_score(
                getattr(insight, "conviction_score", None)
            ),
            "created_at": now_iso,
        }
        insight_rows.append(row)

    if insight_rows:
        try:
            client.table("agent_insights").insert(insight_rows).execute()
            result.insights_written = len(insight_rows)
        except Exception as exc:
            logger.warning(
                "analyst_evidence_writer.insight_insert_failed user_id=%s run_id=%s "
                "count=%d err=%s",
                user_id, agent_run_id, len(insight_rows), exc,
            )
            result.write_error = f"insight_insert:{type(exc).__name__}"

    # ── Step 3: which recommendations rows already exist for this run? ────────
    existing_rec_tickers: set[str] = set()
    try:
        res = (
            client.table("recommendations")
            .select("ticker")
            .eq("user_id", user_str)
            .eq("agent_run_id", agent_run_id)
            .execute()
        )
        for row in res.data or []:
            t = (row.get("ticker") or "").upper()
            if t:
                existing_rec_tickers.add(t)
    except Exception as exc:
        logger.warning(
            "analyst_evidence_writer.rec_check_failed user_id=%s run_id=%s err=%s",
            user_id, agent_run_id, exc,
        )

    # ── Step 4: build and insert missing recommendations rows ─────────────────
    rec_rows: list[dict[str, Any]] = []
    for insight in insights:
        ticker = (getattr(insight, "ticker", None) or "").strip()
        if not ticker:
            continue
        if ticker.upper() in existing_rec_tickers:
            result.recommendations_already_present += 1
            continue
        action = (getattr(insight, "suggested_action", None) or "HOLD").upper()
        thesis = (
            getattr(insight, "investment_thesis", None)
            or f"{action} signal from portfolio agent."
        )
        rec_rows.append({
            "user_id": user_str,
            "ticker": ticker,
            "action": action,
            "detail": thesis[:600],
            "is_active": True,
            "agent_run_id": agent_run_id,
            "investment_thesis": thesis,
            "sentiment_score": _round_score(getattr(insight, "sentiment_score", None)),
            "technical_signal": getattr(insight, "technical_signal", None),
            "conviction_score": _round_score(getattr(insight, "conviction_score", None)),
            "suggested_allocation": round(
                float(getattr(insight, "suggested_allocation", 0) or 0), 2
            ),
            "created_at": now_iso,
        })

    if rec_rows:
        try:
            for i in range(0, len(rec_rows), 50):
                client.table("recommendations").insert(
                    rec_rows[i : i + 50]
                ).execute()
            result.recommendations_written = len(rec_rows)
        except Exception as exc:
            logger.warning(
                "analyst_evidence_writer.rec_insert_failed user_id=%s run_id=%s "
                "count=%d err=%s",
                user_id, agent_run_id, len(rec_rows), exc,
            )
            if result.write_error is None:
                result.write_error = f"rec_insert:{type(exc).__name__}"

    # ── Step 5: expire old active recommendations not from this run ───────────
    # Only when we wrote new rows — skipped on a no-op pass to avoid touching
    # DB state.  When scoped_tickers is provided (bounded batch run), only
    # expire prior active recs for those tickers; other tickers' existing
    # recommendations are left untouched so their certification evidence survives.
    if result.recommendations_written > 0:
        try:
            q = client.table("recommendations").update({
                "is_active": False,
                "resolution": "expired",
                "resolved_at": now_iso,
            }).eq("user_id", user_str).eq("is_active", True)
            if scoped_tickers:
                q = q.in_("ticker", list(scoped_tickers))
            q.neq("agent_run_id", agent_run_id).execute()
        except Exception as exc:
            logger.warning(
                "analyst_evidence_writer.expire_failed user_id=%s run_id=%s err=%s",
                user_id, agent_run_id, exc,
            )

    return result


# ── Public async entry point ──────────────────────────────────────────────────

async def write_analyst_evidence(
    *,
    user_id: UUID,
    agent_run_id: str,
    insights: list[Any],
    started_at: Optional[datetime] = None,
    verdicts: Optional[dict[str, Any]] = None,
    scoped_tickers: Optional[list[str]] = None,
) -> AnalystEvidenceWriteResult:
    """Write durable analyst evidence rows for a completed agent run.

    Called when ``orch.run()`` returned ``status="completed"`` with non-empty
    insights but ``_persist_sync`` may have failed silently.  Uses a fresh DB
    client, not the orchestrator's instance client.

    ``verdicts`` — optional dict of ticker (upper-cased) → ``AnalystVerdict.to_dict()``
    output from the just-finished orchestrator run.  When provided, the writer
    stores the full structured verdict (``primary_driver`` / ``risk_flag`` /
    ``action_reason`` / ``differentiation`` etc.) instead of re-deriving a
    minimal version from ``TickerInsight`` fields.

    ``scoped_tickers`` — when provided (bounded batch worker run), rec expiry in
    Step 5 is limited to those tickers only.  Other tickers' active recommendations
    are left untouched so their certification evidence survives the partial pass.
    For a full-portfolio run (``scoped_tickers=None``), the legacy behaviour is
    preserved: all active recs not from this run are expired.

    Only writes rows for insights in the provided list — no fabrication.
    Idempotent: safe to call for the same ``agent_run_id`` when some or all
    rows already exist (e.g. the legacy path partially succeeded).
    """
    if not insights:
        return AnalystEvidenceWriteResult()
    now_iso = (started_at or datetime.now(timezone.utc)).isoformat()
    try:
        return await asyncio.to_thread(
            _write_sync,
            user_id,
            agent_run_id,
            list(insights),
            now_iso=now_iso,
            verdicts=verdicts,
            scoped_tickers=scoped_tickers,
        )
    except Exception as exc:
        logger.warning(
            "analyst_evidence_writer.failed user_id=%s run_id=%s err=%s",
            user_id, agent_run_id, exc,
        )
        return AnalystEvidenceWriteResult(
            write_error=f"writer_error:{type(exc).__name__}"
        )
