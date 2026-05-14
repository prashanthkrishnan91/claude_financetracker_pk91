"""Full-portfolio analyst refresh adapter (Stage 3.0c).

Stage 3.0b.6 introduced a per-ticker analyst refresh adapter capped at 6 stale
tickers under deterministic budgets. In production that cap left ~28 of 34
positions HARD_STALE on every Run Intel v3 click, so the snapshot never moved
out of ``BLOCKED_UNCERTIFIED`` even when LLM calls succeeded for the selected 6.

Stage 3.0c replaces that path as the default. The existing ``AgentOrchestrator``
in ``services/agents/orchestrator.py`` already supports a fast full-portfolio
LLM pass (verified in production: recs=34 / cards=34 / insights=34, 35 LLM
calls, ~5s). This adapter wraps that path UNSCOPED:

  * No 6-ticker subset selection.
  * No ``analyst_refresh_tickers`` scope filter passed into ``AgentOrchestrator``
    (so the orchestrator's existing full-portfolio analyst phase, persistence,
    and recommendation-expire steps execute over every active position).
  * Per-ticker accounting is read back from durable rows
    (``agent_insights.run_id`` and ``recommendations.agent_run_id``), so a
    successful refresh is only declared when the database actually has new
    rows attributed to this refresh's ``agent_run_id``.

Contract preserved from the v1 adapter:

  * Signature: ``async __call__(stale_tickers, *, priority_hints, started_at)
    -> AnalystRefreshResult``.
  * Returns the same ``AnalystRefreshResult`` dataclass — the orchestrator
    embeds it in diagnostics unchanged.
  * Never raises into the orchestrator: backend failures degrade to
    ``STATUS_FAILED`` with explicit per-ticker reasons.
  * Visible decision authority stays with ``decide()``; this module never
    writes ``intel_v3_snapshots`` and never sets a final action.

Non-goals (deferred):
  * No new market-data providers.
  * No Deploy / Watchtower / broker / tax wiring.
  * No SQL schema changes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

from .analyst_refresh_adapter_v1 import (
    AnalystRefreshResult,
    REASON_FALLBACK_VERDICT,
    REASON_NO_AGENT_INSIGHT_ROW_FOR_RUN,
    REASON_NO_POST_RUN_EVIDENCE,
    REASON_PERSISTENCE_MISSING,
    REASON_READ_QUERY_FAILED,
    STATUS_FAILED,
    STATUS_NO_STALE,
    STATUS_PARTIAL_SUCCESS,
    STATUS_SKIPPED_BUDGET,
    STATUS_SKIPPED_TIMEOUT,
    STATUS_SUCCEEDED,
    TickerPriorityHint,
    TickerRefreshOutcome,
    prioritize_stale_tickers,
)

logger = logging.getLogger(__name__)


# ── Budgets ───────────────────────────────────────────────────────────────────

# Full-portfolio refresh defaults. Sized so a 34-position personal portfolio
# can refresh in one Run Intel v3 click without the orchestrator capping us
# out. Production evidence from prior runs: ~5s for 35 LLM calls.
DEFAULT_MAX_FULL_PORTFOLIO_TICKERS = 60
DEFAULT_MAX_FULL_PORTFOLIO_LLM_CALLS = 60
DEFAULT_MAX_FULL_PORTFOLIO_REFRESH_SECONDS = 180.0


@dataclass
class FullPortfolioAnalystRefreshBudget:
    """Hard caps for the full-portfolio refresh path.

    These are intentionally generous compared with
    ``AnalystRefreshBudget``: the user clicked Run Intel v3 because they are
    about to invest, and the recommendation_engine's existing full-portfolio
    path already proved 34-ticker LLM passes complete in ~5s.
    """
    max_tickers: int = DEFAULT_MAX_FULL_PORTFOLIO_TICKERS
    max_llm_calls: int = DEFAULT_MAX_FULL_PORTFOLIO_LLM_CALLS
    max_seconds: float = DEFAULT_MAX_FULL_PORTFOLIO_REFRESH_SECONDS


# Signature for the underlying full-portfolio analyst-run backend.
#
#   (user_id, selected_tickers, started_at)
#     -> { ticker: { recommendation_created_at, agent_insight_created_at,
#                    used_fallback, agent_run_id,
#                    insight_run_match, rec_run_match,
#                    insight_row_present, rec_row_present,
#                    failure_reason } | None }
#
# ``None`` for a ticker means no durable evidence was written for this
# refresh's ``agent_run_id`` — the strongest "the refresh did not persist"
# signal. A dict with ``insight_run_match=True`` and ``used_fallback=False`` is
# the canonical success shape.
FullPortfolioRunBackend = Callable[
    [UUID, list[str], datetime],
    Awaitable[dict[str, Optional[dict[str, Any]]]],
]


class FullPortfolioAnalystRefreshAdapter:
    """Drives a full-portfolio analyst refresh under the budget.

    Behavioral contract:
      1. The adapter accepts whatever stale ticker list the orchestrator
         derived — typically every active position whose evidence age exceeds
         the analyst-insight SLA.
      2. It does NOT carve a smaller subset. The whole stale list is passed
         into ``run_backend`` so the AgentOrchestrator's existing
         full-portfolio LLM phase runs as it does today.
      3. ``max_tickers`` / ``max_llm_calls`` only act as upper safety limits —
         they truncate the priority-ordered list when the count exceeds the
         budget rather than silently capping at 6.
      4. Per-ticker success is sourced from real post-run DB rows. A ticker
         that the backend could not persist (or that returned a fallback
         verdict) is reported as failed, with its original stale evidence age
         preserved upstream.
    """

    def __init__(
        self,
        *,
        user_id: UUID,
        run_backend: FullPortfolioRunBackend,
        budget: Optional[FullPortfolioAnalystRefreshBudget] = None,
    ):
        self.user_id = user_id
        self._run_backend = run_backend
        self.budget = budget or FullPortfolioAnalystRefreshBudget()

    async def __call__(
        self,
        stale_tickers: list[str],
        *,
        priority_hints: Optional[list[TickerPriorityHint]] = None,
        started_at: Optional[datetime] = None,
    ) -> AnalystRefreshResult:
        started_at = started_at or datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        notes: list[str] = []

        unique: list[str] = []
        seen: set[str] = set()
        for t in stale_tickers or []:
            up = (t or "").strip().upper()
            if not up or up in seen:
                continue
            seen.add(up)
            unique.append(up)

        if not unique:
            return AnalystRefreshResult(
                status=STATUS_NO_STALE,
                selected_tickers=[],
                deferred_tickers=[],
                per_ticker=[],
                duration_ms=0,
                notes=["full_portfolio_analyst_refresh_no_stale_tickers"],
            )

        if self.budget.max_tickers <= 0 or self.budget.max_llm_calls <= 0:
            return AnalystRefreshResult(
                status=STATUS_SKIPPED_BUDGET,
                selected_tickers=[],
                deferred_tickers=list(unique),
                per_ticker=[],
                duration_ms=int((time.monotonic() - started_monotonic) * 1000),
                budget_exhausted=True,
                notes=["full_portfolio_analyst_refresh_zero_budget"],
            )

        hints_by_ticker: dict[str, TickerPriorityHint] = {}
        for h in priority_hints or []:
            t = h.ticker.strip().upper() if h.ticker else ""
            if t and t in seen:
                hints_by_ticker[t] = h
        full_hints = [
            hints_by_ticker.get(t) or TickerPriorityHint(ticker=t)
            for t in unique
        ]
        ranked = prioritize_stale_tickers(full_hints)

        cap = min(self.budget.max_tickers, self.budget.max_llm_calls, len(ranked))
        selected = [h.ticker for h in ranked[:cap]]
        deferred = [h.ticker for h in ranked[cap:]]
        if deferred:
            notes.append(
                f"full_portfolio_analyst_refresh_deferred_{len(deferred)}_tickers_over_budget"
            )

        elapsed = time.monotonic() - started_monotonic
        if elapsed >= self.budget.max_seconds:
            return AnalystRefreshResult(
                status=STATUS_SKIPPED_TIMEOUT,
                selected_tickers=[],
                deferred_tickers=selected + deferred,
                per_ticker=[],
                duration_ms=int(elapsed * 1000),
                budget_exhausted=True,
                notes=["full_portfolio_analyst_refresh_pre_timeout"],
            )

        try:
            backend_results = await asyncio.wait_for(
                self._run_backend(self.user_id, selected, started_at),
                timeout=max(1.0, self.budget.max_seconds - elapsed),
            )
        except asyncio.TimeoutError:
            return AnalystRefreshResult(
                status=STATUS_SKIPPED_TIMEOUT,
                selected_tickers=selected,
                deferred_tickers=deferred,
                per_ticker=[
                    TickerRefreshOutcome(
                        ticker=t,
                        success=False,
                        error_reason="full_portfolio_analyst_refresh_timeout",
                    )
                    for t in selected
                ],
                duration_ms=int((time.monotonic() - started_monotonic) * 1000),
                budget_exhausted=True,
                notes=notes + ["full_portfolio_analyst_refresh_timeout"],
            )
        except Exception as exc:
            logger.warning(
                "full_portfolio_analyst_refresh.backend_failed user_id=%s error=%s",
                self.user_id, exc,
            )
            return AnalystRefreshResult(
                status=STATUS_FAILED,
                selected_tickers=selected,
                deferred_tickers=deferred,
                per_ticker=[
                    TickerRefreshOutcome(
                        ticker=t,
                        success=False,
                        error_reason=f"backend_error:{type(exc).__name__}",
                    )
                    for t in selected
                ],
                duration_ms=int((time.monotonic() - started_monotonic) * 1000),
                notes=notes
                + [f"full_portfolio_analyst_refresh_error:{type(exc).__name__}"],
            )

        per_ticker: list[TickerRefreshOutcome] = []
        attempted = 0
        successful = 0
        failed = 0
        agent_run_id: Optional[str] = None
        for ticker in selected:
            row = (backend_results or {}).get(ticker)
            attempted += 1
            if not isinstance(row, dict):
                failed += 1
                per_ticker.append(TickerRefreshOutcome(
                    ticker=ticker,
                    success=False,
                    error_reason=REASON_NO_POST_RUN_EVIDENCE,
                    llm_call_count=1,
                    llm_success_count=0,
                ))
                continue
            agent_run_id = agent_run_id or row.get("agent_run_id")
            rec_ts = row.get("recommendation_created_at")
            insight_ts = row.get("agent_insight_created_at")
            used_fallback = bool(row.get("used_fallback", False))
            insight_run_match = bool(row.get("insight_run_match"))
            rec_run_match = bool(row.get("rec_run_match"))
            insight_present = bool(row.get("insight_row_present"))
            backend_reason = row.get("failure_reason")

            if used_fallback:
                failed += 1
                per_ticker.append(TickerRefreshOutcome(
                    ticker=ticker,
                    success=False,
                    error_reason=REASON_FALLBACK_VERDICT,
                    llm_call_count=1,
                    llm_success_count=0,
                ))
                continue
            if not insight_run_match:
                if not insight_present:
                    reason = REASON_NO_AGENT_INSIGHT_ROW_FOR_RUN
                else:
                    reason = REASON_PERSISTENCE_MISSING
                failed += 1
                per_ticker.append(TickerRefreshOutcome(
                    ticker=ticker,
                    success=False,
                    error_reason=backend_reason or reason,
                    llm_call_count=1,
                    llm_success_count=0,
                ))
                continue

            successful += 1
            per_ticker.append(TickerRefreshOutcome(
                ticker=ticker,
                success=True,
                refreshed_recommendation_at=rec_ts if (rec_run_match or rec_ts) else None,
                refreshed_agent_insight_at=insight_ts,
                llm_call_count=1,
                llm_success_count=1,
            ))

        if successful == 0:
            status = STATUS_FAILED
        elif failed == 0:
            status = STATUS_SUCCEEDED
        else:
            status = STATUS_PARTIAL_SUCCESS

        return AnalystRefreshResult(
            status=status,
            selected_tickers=selected,
            deferred_tickers=deferred,
            per_ticker=per_ticker,
            attempted_llm_calls=attempted,
            successful_llm_calls=successful,
            failed_llm_calls=failed,
            duration_ms=int((time.monotonic() - started_monotonic) * 1000),
            budget_exhausted=bool(deferred),
            notes=notes,
            agent_run_id=agent_run_id,
        )


# ── Default backend: unscoped AgentOrchestrator + post-run DB verification ───

async def default_full_portfolio_agent_orchestrator_backend(
    user_id: UUID,
    selected_tickers: list[str],
    started_at: datetime,
) -> dict[str, Optional[dict[str, Any]]]:
    """Run ``AgentOrchestrator`` UNSCOPED on the full stale ticker list.

    Differences vs. the v1 6-ticker backend:
      * ``analyst_refresh_tickers`` is NOT passed — the orchestrator runs the
        same full-portfolio LLM phase + persistence that recommendation_engine
        observes in production (recs=34 / cards=34 / insights=34 in ~5s).
      * The post-run read uses ``agent_run_id`` as the durable primary key.

    Returns ``{ticker: row | None}`` for every selected ticker; ``None`` only
    when neither ``agent_insights`` nor ``recommendations`` returned any row
    for this refresh's ``agent_run_id`` (and no post-``started_at`` fallback
    row for the same ticker).
    """
    # Local imports keep the v3 module graph free of agent-stack symbols at
    # import time and allow tests to stub this function out wholesale.
    from ....config import get_settings
    from ...agents.orchestrator import AgentOrchestrator
    from ...price_engine import PriceService

    settings = get_settings()
    price_service = PriceService(
        finnhub_key=getattr(settings, "finnhub_api_key", "") or "",
        alpaca_key=getattr(settings, "alpaca_api_key", "") or "",
        alpaca_secret=getattr(settings, "alpaca_secret_key", "") or "",
        polygon_key=getattr(settings, "polygon_api_key", "") or "",
    )
    try:
        orch = AgentOrchestrator(
            user_id=user_id,
            price_service=price_service,
            anthropic_api_key=getattr(settings, "anthropic_api_key", "") or "",
            finnhub_key=getattr(settings, "finnhub_api_key", "") or "",
            polygon_key=getattr(settings, "polygon_api_key", "") or "",
            force_recompute=True,
            # NOTE: analyst_refresh_tickers intentionally omitted so the
            # orchestrator runs its full-portfolio analyst phase + persistence.
        )
        run_id = await orch.create_run(tickers=list(selected_tickers))
        try:
            await orch.run(run_id)
        except Exception as run_exc:
            logger.warning(
                "full_portfolio_analyst_refresh.agent_run_failed user_id=%s run_id=%s err=%s",
                user_id, run_id, run_exc,
            )
        return await _read_post_run_evidence(
            user_id, selected_tickers, run_id, started_at,
        )
    finally:
        try:
            await price_service.close()
        except Exception:
            pass


async def _read_post_run_evidence(
    user_id: UUID,
    tickers: list[str],
    agent_run_id: str,
    started_at: datetime,
) -> dict[str, Optional[dict[str, Any]]]:
    """Read per-ticker durable evidence for ``agent_run_id``.

    Persistence contract (``AgentOrchestrator._persist_sync`` +
    ``TickerInsight.to_insight_row``):
      * ``agent_insights.run_id``        == the agent run id
      * ``recommendations.agent_run_id`` == the agent run id
      * ``*.user_id``                    == ``str(user_id)``
      * ``*.ticker``                     == the position's ticker AS STORED.
        The orchestrator does NOT normalise ticker casing — it persists
        ``context["portfolio"]`` tickers verbatim, which may be upper / lower /
        mixed case.

    The durable verification key is therefore ``run_id`` + ``user_id`` — NOT
    the request's ticker strings. A previous version of this readback also
    filtered ``.in_("ticker", tickers)`` with the worker's UPPER-cased ticker
    list; whenever the persisted ticker casing differed, that server-side
    filter silently excluded every row and produced ``no_post_run_evidence``
    for every ticker even though the orchestrator had persisted them. The
    ticker filter is intentionally dropped: ``run_id`` / ``created_at`` already
    scope the result set to this run, and per-ticker mapping below is
    case-insensitive (``_index_latest`` upper-cases its keys).
    """
    from ....database import get_supabase_client

    client = get_supabase_client()
    started_iso = started_at.isoformat()

    def _read_insights() -> tuple[list[dict[str, Any]], list[dict[str, Any]], Optional[str]]:
        run_rows: list[dict[str, Any]] = []
        ticker_rows: list[dict[str, Any]] = []
        err: Optional[str] = None
        try:
            res = (
                client.table("agent_insights")
                .select("ticker,created_at,run_id,analyst_verdict")
                .eq("user_id", str(user_id))
                .eq("run_id", agent_run_id)
                .execute()
            )
            run_rows = res.data or []
        except Exception as exc:
            try:
                res = (
                    client.table("agent_insights")
                    .select("ticker,created_at,run_id")
                    .eq("user_id", str(user_id))
                    .eq("run_id", agent_run_id)
                    .execute()
                )
                run_rows = res.data or []
            except Exception as exc2:
                err = f"agent_insights_read_failed:{type(exc2).__name__}"
                logger.warning(
                    "full_portfolio_analyst_refresh.insights_read_failed user_id=%s "
                    "run_id=%s err=%s",
                    user_id, agent_run_id, exc2,
                )
        try:
            res = (
                client.table("agent_insights")
                .select("ticker,created_at,run_id,analyst_verdict")
                .eq("user_id", str(user_id))
                .gte("created_at", started_iso)
                .execute()
            )
            ticker_rows = res.data or []
        except Exception:
            try:
                res = (
                    client.table("agent_insights")
                    .select("ticker,created_at,run_id")
                    .eq("user_id", str(user_id))
                    .gte("created_at", started_iso)
                    .execute()
                )
                ticker_rows = res.data or []
            except Exception:
                pass
        return run_rows, ticker_rows, err

    def _read_recs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], Optional[str]]:
        run_rows: list[dict[str, Any]] = []
        ticker_rows: list[dict[str, Any]] = []
        err: Optional[str] = None
        try:
            res = (
                client.table("recommendations")
                .select("ticker,created_at,agent_run_id,is_active")
                .eq("user_id", str(user_id))
                .eq("agent_run_id", agent_run_id)
                .execute()
            )
            run_rows = res.data or []
        except Exception as exc:
            err = f"recommendations_read_failed:{type(exc).__name__}"
            logger.warning(
                "full_portfolio_analyst_refresh.recs_read_failed user_id=%s "
                "run_id=%s err=%s",
                user_id, agent_run_id, exc,
            )
        try:
            res = (
                client.table("recommendations")
                .select("ticker,created_at,agent_run_id,is_active")
                .eq("user_id", str(user_id))
                .gte("created_at", started_iso)
                .execute()
            )
            ticker_rows = res.data or []
        except Exception:
            pass
        return run_rows, ticker_rows, err

    insight_run_rows, insight_ticker_rows, insight_err = await asyncio.to_thread(_read_insights)
    rec_run_rows, rec_ticker_rows, rec_err = await asyncio.to_thread(_read_recs)

    def _index_latest(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            t = (row.get("ticker") or "").upper()
            if not t:
                continue
            if (
                t not in out
                or (row.get("created_at") or "") > (out[t].get("created_at") or "")
            ):
                out[t] = row
        return out

    insight_by_run = _index_latest(insight_run_rows)
    insight_by_ts = _index_latest(insight_ticker_rows)
    rec_by_run = _index_latest(rec_run_rows)
    rec_by_ts = _index_latest(rec_ticker_rows)

    out: dict[str, Optional[dict[str, Any]]] = {}
    missing_reason_counts: dict[str, int] = {}
    for ticker in tickers:
        up = ticker.upper()
        insight_run = insight_by_run.get(up)
        insight_ts_row = insight_by_ts.get(up)
        rec_run = rec_by_run.get(up)
        rec_ts_row = rec_by_ts.get(up)

        insight_present = bool(insight_run or insight_ts_row)
        rec_present = bool(rec_run or rec_ts_row)

        insight = insight_run or insight_ts_row
        rec = rec_run or rec_ts_row

        verdict = (
            (insight or {}).get("analyst_verdict") if isinstance(insight, dict) else None
        )
        used_fallback = bool(isinstance(verdict, dict) and verdict.get("used_fallback"))

        failure_reason: Optional[str] = None
        if insight_err and not insight_present:
            failure_reason = REASON_READ_QUERY_FAILED
        elif rec_err and not rec_present and not insight_present:
            failure_reason = REASON_READ_QUERY_FAILED
        elif not insight_present and not rec_present:
            failure_reason = REASON_NO_AGENT_INSIGHT_ROW_FOR_RUN
        elif insight_run is None and insight_ts_row is not None:
            failure_reason = REASON_PERSISTENCE_MISSING
        elif used_fallback:
            failure_reason = REASON_FALLBACK_VERDICT

        if insight is None and rec is None and not insight_err and not rec_err:
            out[ticker] = None
            missing_reason_counts[REASON_NO_POST_RUN_EVIDENCE] = (
                missing_reason_counts.get(REASON_NO_POST_RUN_EVIDENCE, 0) + 1
            )
            continue

        out[ticker] = {
            "agent_insight_created_at":  (insight or {}).get("created_at"),
            "recommendation_created_at": (rec or {}).get("created_at"),
            "used_fallback":             used_fallback,
            "agent_run_id":              agent_run_id,
            "insight_run_match":         insight_run is not None,
            "rec_run_match":             rec_run is not None,
            "insight_row_present":       insight_present,
            "rec_row_present":           rec_present,
            "failure_reason":            failure_reason,
        }
        breakdown_key = failure_reason or "ok"
        missing_reason_counts[breakdown_key] = (
            missing_reason_counts.get(breakdown_key, 0) + 1
        )

    # Strong diagnostic: rows persisted under a *different* recent run_id point
    # at a run-id mismatch rather than a non-persisting orchestrator.
    other_run_ids = sorted({
        str(r.get("run_id"))
        for r in insight_ticker_rows
        if r.get("run_id") and str(r.get("run_id")) != str(agent_run_id)
    })

    logger.info(
        "full_portfolio_analyst_refresh.post_run_readback user_id=%s run_id=%s "
        "selected_ticker_count=%d insights_by_run_id=%d insights_by_created_at=%d "
        "recs_by_run_id=%d recs_by_created_at=%d resolved_ticker_count=%d "
        "missing_reason_breakdown=%s other_recent_run_ids=%s "
        "insight_read_err=%s rec_read_err=%s",
        user_id, agent_run_id, len(tickers),
        len(insight_run_rows), len(insight_ticker_rows),
        len(rec_run_rows), len(rec_ticker_rows),
        sum(1 for v in out.values() if v is not None),
        missing_reason_counts,
        ",".join(other_run_ids) if other_run_ids else "none",
        insight_err, rec_err,
    )
    return out
