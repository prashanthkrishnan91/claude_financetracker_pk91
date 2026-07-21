"""Full-portfolio analyst refresh adapter (Stage 3.0c).

Stage 3.0b.6 introduced a per-ticker analyst refresh adapter capped at 6 stale
tickers under deterministic budgets. In production that cap left ~28 of 34
positions HARD_STALE on every Run Intel v3 click, so the snapshot never moved
out of ``BLOCKED_UNCERTIFIED`` even when LLM calls succeeded for the selected 6.

Stage 3.0c replaced the capped path as the default; the durable-sessions
recovery then changed WHAT the backend executes. The default backend now:

  * Scopes the orchestrator's analyst + persist phases to the selected batch
    via ``analyst_refresh_tickers`` (non-scope tickers' rows stay untouched).
  * Calls ``AgentOrchestrator.run_analyst_refresh_only()`` — the genuine
    analyst-only production method. It must NEVER call the full ``run()``
    pipeline: run()'s unconditional Phase 4 portfolio synthesis consumed the
    remaining request deadline AFTER per-ticker analysis had already
    succeeded, timing out this adapter and blanket-failing the batch.
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


# ── Agent-run-outcome-aware failure reasons (Stage 3.2) ──────────────────────
#
# When the worker's post-run readback finds no durable rows, the generic
# ``no_post_run_evidence`` hides WHY the orchestrator did not persist: did
# ``orch.run()`` raise, return ``no_data`` (empty portfolio), end ``failed``, or
# end ``completed`` and still write nothing? These reasons reconcile the
# readback against the actual ``AgentPipelineResult`` so a single production log
# line pinpoints the orchestrator stage that needs the durable fix — instead of
# every non-success collapsing to one opaque bucket.
REASON_AGENT_RUN_RAISED = "agent_run_raised"
REASON_AGENT_RUN_FAILED = "agent_run_failed"
REASON_AGENT_RUN_NO_DATA = "agent_run_no_data"
REASON_AGENT_RUN_COMPLETED_NO_ROWS = "agent_run_completed_no_persisted_rows"


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
        worker_run_id: Optional[str] = None,
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
            # Session batches thread the durable worker_run_id into the
            # backend as the explicit agent-run id (kwarg only when supplied
            # so injected legacy/test backends keep their 3-arg signature).
            if worker_run_id is not None:
                backend_coro = self._run_backend(
                    self.user_id, selected, started_at, run_id=str(worker_run_id),
                )
            else:
                backend_coro = self._run_backend(self.user_id, selected, started_at)
            backend_results = await asyncio.wait_for(
                backend_coro,
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
    run_id: Optional[str] = None,
) -> dict[str, Optional[dict[str, Any]]]:
    """Run ``AgentOrchestrator`` UNSCOPED on the full stale ticker list.

    Differences vs. the v1 6-ticker backend:
      * ``analyst_refresh_tickers`` is NOT passed — the orchestrator runs the
        same full-portfolio LLM phase + persistence that recommendation_engine
        observes in production (recs=34 / cards=34 / insights=34 in ~5s).
      * The post-run read uses ``agent_run_id`` as the durable primary key.
      * The orchestrator's run outcome (``AgentPipelineResult.status`` or the
        raised exception) is captured and threaded into the readback so a
        no-durable-rows ticker gets a SPECIFIC failure reason instead of the
        opaque generic ``no_post_run_evidence``.

    Returns ``{ticker: row}`` for every selected ticker — a verified row when
    durable evidence exists, otherwise an explanatory row whose
    ``failure_reason`` pinpoints the orchestrator stage (raised / failed /
    no_data / completed-but-persisted-nothing). Never marks a no-rows ticker a
    success.
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
            # Scope the orchestrator's analyst + persist phases to the selected
            # batch only.  Non-scope tickers keep their existing rows untouched.
            analyst_refresh_tickers=set(selected_tickers),
        )
        # ``run_id`` (session batches): the caller supplies the durable
        # analyst_refresh_jobs.worker_run_id, so the agent_runs row — and every
        # agent_insights.run_id / recommendations.agent_run_id written for this
        # batch — carries the EXACT id already stamped on the claimed queue
        # rows. Evidence↔job ownership is then a durable SQL equality, never a
        # timestamp inference. Legacy callers omit it (DB-generated id).
        run_id = await orch.create_run(
            tickers=list(selected_tickers), run_id=run_id,
        )
        # Capture the orchestrator's run outcome so the post-run readback can
        # attribute a SPECIFIC failure reason when no durable rows appear —
        # raised / failed / no_data / completed-but-persisted-nothing — instead
        # of the opaque generic "no_post_run_evidence".
        agent_run_status = "unknown"
        agent_run_error: Optional[str] = None
        agent_run_insight_count = 0
        result_insights: list[Any] = []
        try:
            # Analyst-only execution boundary: the Run Intel ticker-refresh
            # path must never enter the full run() pipeline, whose
            # unconditional Phase 4 portfolio synthesis previously consumed
            # the remaining request deadline AFTER per-ticker analysis had
            # succeeded — timing out the adapter and blanket-failing the
            # batch. run_analyst_refresh_only() returns as soon as the
            # selected tickers' evidence is durably persisted.
            result = await orch.run_analyst_refresh_only(
                run_id, tickers=list(selected_tickers),
            )
            agent_run_status = str(getattr(result, "status", "unknown") or "unknown")
            result_insights = list(getattr(result, "insights", None) or [])
            agent_run_insight_count = len(result_insights)
        except Exception as run_exc:
            agent_run_status = "raised"
            agent_run_error = f"{type(run_exc).__name__}: {run_exc}"[:200]
            logger.warning(
                "full_portfolio_analyst_refresh.agent_run_failed user_id=%s run_id=%s err=%s",
                user_id, run_id, run_exc,
            )

        # Extract live AnalystVerdict objects from the orchestrator (Stage 3.2c).
        # ``orch._verdicts`` is populated during the per-ticker LLM analyst phase and
        # holds the full structured verdict (primary_driver / risk_flag / action_reason /
        # differentiation etc.) that ``_persist_sync`` would have written to
        # agent_insights.analyst_verdict.  We convert to dicts here so the explicit
        # writeback writer can store the identical shape, preserving all rationale
        # fields and avoiding the ticker-prefix-only rationale block that fires when
        # primary_driver is re-derived from the first sentence of investment_thesis.
        verdicts_raw = getattr(orch, "_verdicts", None) or {}
        verdicts_dicts: dict[str, Any] = {}
        for tk, v in verdicts_raw.items():
            tk_upper = (tk or "").upper()
            if not tk_upper:
                continue
            if hasattr(v, "to_dict"):
                verdicts_dicts[tk_upper] = v.to_dict()
            elif isinstance(v, dict):
                verdicts_dicts[tk_upper] = v

        # Explicit writeback bridge (Stage 3.2b + Stage 3.2c rationale fix).
        #
        # AgentOrchestrator._persist_sync runs via asyncio.to_thread(self._persist_sync,
        # state) using the orchestrator's instance-level self.db client.  In the Railway
        # worker process that client silently fails inside the thread — the exception is
        # caught by orchestrator.run():567–575, so run() returns completed with insights
        # in memory but zero DB rows (production evidence: agent_run_completed_no_persisted_rows=34).
        #
        # This writer uses a fresh get_supabase_client() — the same pattern as the
        # working _read_post_run_evidence readback — to guarantee durable writes even
        # when the orchestrator's instance client fails in the worker context.
        # Idempotent: skips rows that already exist (for when _persist_sync succeeded).
        # verdicts_dicts carries the live AnalystVerdict payload so primary_driver /
        # risk_flag / action_reason are written faithfully rather than re-derived.
        write_result = None
        if agent_run_status == "completed" and result_insights:
            from .analyst_evidence_writer_v1 import write_analyst_evidence
            # Filter insights to the selected batch only.  AgentPipelineResult.insights
            # contains all positions from state.insights (the full portfolio), but
            # analyst_refresh_tickers scoped the LLM + _persist_sync to the selected
            # batch.  Only those tickers have fresh LLM-backed evidence in this run.
            selected_upper = {t.upper() for t in selected_tickers}
            result_insights_scoped = [
                ins for ins in result_insights
                if (getattr(ins, "ticker", None) or "").upper() in selected_upper
            ]
            write_result = await write_analyst_evidence(
                user_id=user_id,
                agent_run_id=run_id,
                insights=result_insights_scoped,
                started_at=started_at,
                verdicts=verdicts_dicts or None,
                scoped_tickers=list(selected_tickers),
            )
            logger.info(
                "analyst_evidence_writer_persisted_count=%d user_id=%s run_id=%s "
                "insights_written=%d recs_written=%d "
                "already_present_insights=%d already_present_recs=%d write_error=%s "
                "verdicts_available=%d selected_ticker_count=%d",
                write_result.persisted_count,
                user_id,
                run_id,
                write_result.insights_written,
                write_result.recommendations_written,
                write_result.insights_already_present,
                write_result.recommendations_already_present,
                write_result.write_error,
                len(verdicts_dicts),
                len(selected_tickers),
            )

        # Snapshot prewarm is deferred to the worker level (analyst_refresh_worker_v1).
        # The worker triggers prewarm only after the full refresh job set is drained
        # (run_resumable=False), not after every partial batch.  Calling prewarm here
        # (per-batch) allowed worker_certified to be published mid-refresh when the
        # remaining tickers had fresh rows from a previous run — the pre-merge blocker.

        return await _read_post_run_evidence(
            user_id, selected_tickers, run_id, started_at,
            agent_run_status=agent_run_status,
            agent_run_error=agent_run_error,
            agent_run_insight_count=agent_run_insight_count,
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
    *,
    agent_run_status: str = "unknown",
    agent_run_error: Optional[str] = None,
    agent_run_insight_count: int = 0,
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

    ``agent_run_status`` / ``agent_run_error`` come from the just-finished
    ``AgentPipelineResult`` (or the exception ``orch.run()`` raised). When the
    readback finds no durable rows, this lets it attribute a SPECIFIC failure
    reason — ``agent_run_raised`` / ``agent_run_failed`` / ``agent_run_no_data``
    / ``agent_run_completed_no_persisted_rows`` — instead of the opaque generic
    ``no_post_run_evidence``, so the orchestrator-side persistence root cause is
    pinpointed from one production log line. It NEVER turns a no-rows ticker
    into a success — verification still requires a real ``run_id``-matched row.
    """
    from ....database import get_supabase_client

    client = get_supabase_client()
    started_iso = started_at.isoformat()

    def _no_durable_rows_reason() -> str:
        """Specific reason for a ticker with no durable post-run evidence,
        derived from the orchestrator's actual run outcome."""
        if agent_run_status == "raised":
            return f"{REASON_AGENT_RUN_RAISED}:{agent_run_error or 'unknown'}"
        if agent_run_status == "failed":
            return REASON_AGENT_RUN_FAILED
        if agent_run_status == "no_data":
            return REASON_AGENT_RUN_NO_DATA
        if agent_run_status == "completed":
            # The orchestrator ran end-to-end and reported completed, yet no
            # agent_insights / recommendations row exists for this run_id —
            # the persistence step (AgentOrchestrator._persist_sync) is the
            # durable-fix target.
            return REASON_AGENT_RUN_COMPLETED_NO_ROWS
        return REASON_NO_POST_RUN_EVIDENCE

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
            # No durable post-run evidence for this ticker. Return an
            # explanatory row (NOT None / NOT a success) carrying a SPECIFIC
            # reason derived from the orchestrator's actual run outcome, so
            # the worker log + missing-reason breakdown pinpoint whether the
            # orchestrator raised, failed, returned no_data, or completed yet
            # persisted nothing.
            no_rows_reason = _no_durable_rows_reason()
            out[ticker] = {
                "agent_insight_created_at":  None,
                "recommendation_created_at": None,
                "used_fallback":             False,
                "agent_run_id":              agent_run_id,
                "insight_run_match":         False,
                "rec_run_match":             False,
                "insight_row_present":       False,
                "rec_row_present":           False,
                "failure_reason":            no_rows_reason,
            }
            missing_reason_counts[no_rows_reason] = (
                missing_reason_counts.get(no_rows_reason, 0) + 1
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

    # "Resolved" = tickers that actually have a durable post-run row (insight
    # or recommendation). Every out value is now an explanatory dict, so count
    # real evidence presence rather than "is not None".
    resolved_ticker_count = sum(
        1 for v in out.values()
        if isinstance(v, dict) and (
            v.get("insight_row_present") or v.get("rec_row_present")
        )
    )

    logger.info(
        "full_portfolio_analyst_refresh.post_run_readback user_id=%s run_id=%s "
        "agent_run_status=%s agent_run_insight_count=%d "
        "selected_ticker_count=%d insights_by_run_id=%d insights_by_created_at=%d "
        "recs_by_run_id=%d recs_by_created_at=%d resolved_ticker_count=%d "
        "missing_reason_breakdown=%s other_recent_run_ids=%s "
        "insight_read_err=%s rec_read_err=%s agent_run_error=%s",
        user_id, agent_run_id, agent_run_status, agent_run_insight_count,
        len(tickers),
        len(insight_run_rows), len(insight_ticker_rows),
        len(rec_run_rows), len(rec_ticker_rows),
        resolved_ticker_count,
        missing_reason_counts,
        ",".join(other_run_ids) if other_run_ids else "none",
        insight_err, rec_err, agent_run_error,
    )
    return out


# ── Stage 3.2c: deterministic snapshot prewarm ───────────────────────────────

async def trigger_snapshot_prewarm(
    *,
    user_id: UUID,
    worker_run_id: str,
) -> None:
    """Trigger a deterministic Intel v3 snapshot from freshly written evidence.

    Called by ``AnalystRefreshWorker.run_once`` when ``run_resumable=False``
    (all pending refresh jobs drained) — never after a partial batch.  Calling
    prewarm after every batch would allow ``worker_certified`` to be published
    mid-refresh when remaining tickers have fresh rows from a previous run.

    Guarantees:
      * Zero LLM calls — the prewarm only reads persisted evidence and runs
        the deterministic ``decide()`` kernel.
      * Does NOT enqueue another analyst refresh job — the prewarm path skips
        ``_run_refresh_orchestrator`` entirely, so no ``AnalystRefreshRequestSeam``
        is called and no ``analyst_refresh_jobs`` rows are inserted.
      * Does not block or fail the worker on error — failures are logged as
        ``analyst_refresh_snapshot_prewarm_failed`` and swallowed so the worker
        job outcome (succeeded/failed per ticker) is unaffected.
    """
    import time as _time

    prewarm_started = _time.monotonic()
    logger.info(
        "analyst_refresh_snapshot_prewarm_started user_id=%s worker_run_id=%s",
        user_id, worker_run_id,
    )
    try:
        from .intel_v3_service import prewarm_intel_v3_snapshot
        snapshot = await prewarm_intel_v3_snapshot(user_id, prewarm_run_id=worker_run_id)
        duration_ms = int((_time.monotonic() - prewarm_started) * 1000)
        diag = snapshot.get("diagnostics") or {}
        logger.info(
            "analyst_refresh_snapshot_prewarm_completed user_id=%s worker_run_id=%s "
            "prewarm_snapshot_id=%s prewarm_run_mode=%s prewarm_trust_status=%s "
            "prewarm_duration_ms=%d",
            user_id,
            worker_run_id,
            snapshot.get("snapshot_id"),
            diag.get("run_mode"),
            diag.get("trust_status"),
            duration_ms,
        )
    except Exception as exc:
        duration_ms = int((_time.monotonic() - prewarm_started) * 1000)
        logger.warning(
            "analyst_refresh_snapshot_prewarm_failed user_id=%s worker_run_id=%s "
            "prewarm_error=%s prewarm_duration_ms=%d",
            user_id, worker_run_id, exc, duration_ms,
        )


# Backward-compat alias for tests and any existing code that references the
# private name.  New call sites should use ``trigger_snapshot_prewarm``.
_trigger_snapshot_prewarm = trigger_snapshot_prewarm
