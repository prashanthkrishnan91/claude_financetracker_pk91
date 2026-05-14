"""Analyst Refresh Adapter (Stage 3.0b.6).

Narrow, budgeted bridge between the Evidence Refresh Orchestrator (PR #308) and
the existing ``services/agents/orchestrator.AgentOrchestrator``. The adapter is
intentionally thin:

  - Picks a deterministic priority-sorted subset of stale tickers that fits the
    LLM/ticker budget.
  - Invokes ``AgentOrchestrator.run()`` once, restricted to that ticker subset
    via the optional ``analyst_refresh_tickers`` scope param.
  - Verifies per-ticker success from real DB state (``agent_insights`` /
    ``recommendations``) — no fabricated freshness.
  - Returns an ``AnalystRefreshResult`` the orchestrator embeds in diagnostics.

Non-negotiables enforced here:
  - LLMs/agents refresh evidence only — visible decision authority stays with
    ``decide()`` (the deterministic policy). The adapter never writes
    ``intel_v3_snapshots`` and never sets a final action.
  - No stale LLM/agent output gets stamped fresh unless the refresh actually
    succeeded for that ticker.
  - Per-ticker success/failure accounting based on post-run DB timestamps.
  - Hard budget caps: max analyst tickers, max LLM calls, max total seconds.
  - Owned portfolio only; opportunity / radar tickers are out of scope.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# ── Budgets ───────────────────────────────────────────────────────────────────

# Safe defaults for personal single-user runs. Production values can be tuned by
# environment in a future slice; v1 keeps them deterministic constants so the
# adapter never silently runs an unbudgeted 34-ticker LLM pass.
DEFAULT_MAX_ANALYST_TICKERS_PER_RUN = 6
DEFAULT_MAX_ANALYST_LLM_CALLS_PER_RUN = 6
DEFAULT_MAX_ANALYST_REFRESH_SECONDS = 90.0


# ── Status enum ───────────────────────────────────────────────────────────────

STATUS_NOT_SUPPORTED = "not_supported"
STATUS_NO_STALE = "no_stale"
STATUS_SKIPPED_BUDGET = "skipped_budget"
STATUS_SKIPPED_TIMEOUT = "skipped_timeout"
STATUS_PARTIAL_SUCCESS = "partial_success"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


# ── Priority ordering ─────────────────────────────────────────────────────────

# Lower number wins. BUY/TRIM beat HOLD/SELL/UNKNOWN — these are the actionable
# decisions where stale analyst evidence most directly poisons the user's next
# move. SELL is deprioritized because once an exit decision exists, a refresh
# rarely flips it; HOLD/UNKNOWN are background noise relative to actionables.
_ACTION_PRIORITY: dict[str, int] = {
    "BUY":   0,
    "TRIM":  1,
    "REDUCE": 1,
    "SELL":  2,
    "HOLD":  3,
    "UNKNOWN": 4,
    "":      4,
}


@dataclass(frozen=True)
class TickerPriorityHint:
    """Input shape the orchestrator hands the adapter for each stale ticker.

    All fields are optional; missing data degrades to alphabetical tie-break.
    """
    ticker: str
    prior_action: Optional[str] = None
    weight_pct: Optional[float] = None          # 0..100
    evidence_age_hours: Optional[float] = None


def _priority_sort_key(hint: TickerPriorityHint) -> tuple:
    action_rank = _ACTION_PRIORITY.get(
        (hint.prior_action or "").upper(),
        _ACTION_PRIORITY[""],
    )
    # Higher weight first → negate for ascending sort.
    weight = -float(hint.weight_pct) if hint.weight_pct is not None else 0.0
    # Older evidence first → negate.
    age = -float(hint.evidence_age_hours) if hint.evidence_age_hours is not None else 0.0
    return (action_rank, weight, age, hint.ticker.upper())


def prioritize_stale_tickers(
    hints: list[TickerPriorityHint],
) -> list[TickerPriorityHint]:
    """Deterministic priority sort.

    Order: BUY/TRIM first, then SELL, then HOLD/UNKNOWN. Within each action
    bucket: higher weight first, then older evidence first, then ticker A→Z.
    Pure function — exposed so tests can lock the contract.
    """
    return sorted(hints, key=_priority_sort_key)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class TickerRefreshOutcome:
    """Per-ticker accounting after a refresh attempt.

    ``refreshed_recommendation_at`` / ``refreshed_agent_insight_at`` are populated
    ONLY when the underlying DB row actually shifted to a new ``created_at``
    after the refresh started. Otherwise they stay None — no fabricated stamps.
    """
    ticker: str
    success: bool
    refreshed_recommendation_at: Optional[str] = None
    refreshed_agent_insight_at: Optional[str] = None
    error_reason: Optional[str] = None
    llm_call_count: int = 0       # attempted LLM calls attributable to this ticker
    llm_success_count: int = 0    # successful (non-fallback) LLM calls

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker":                       self.ticker,
            "success":                      self.success,
            "refreshed_recommendation_at":  self.refreshed_recommendation_at,
            "refreshed_agent_insight_at":   self.refreshed_agent_insight_at,
            "error_reason":                 self.error_reason,
            "llm_call_count":               self.llm_call_count,
            "llm_success_count":            self.llm_success_count,
        }


@dataclass
class AnalystRefreshResult:
    """Outcome of one analyst-refresh adapter call.

    The Evidence Refresh Orchestrator embeds the per-ticker outcomes + summary
    counts into its diagnostics so the snapshot + log line + UI banner can be
    honest about what actually refreshed.
    """
    status: str
    selected_tickers: list[str]
    deferred_tickers: list[str]          # priority-eligible but cut by budget
    per_ticker: list[TickerRefreshOutcome]
    attempted_llm_calls: int = 0
    successful_llm_calls: int = 0
    failed_llm_calls: int = 0
    duration_ms: int = 0
    budget_exhausted: bool = False
    notes: list[str] = field(default_factory=list)
    agent_run_id: Optional[str] = None

    # Per-ticker shortcuts ---------------------------------------------------

    def successful_tickers(self) -> list[str]:
        return [o.ticker for o in self.per_ticker if o.success]

    def failed_tickers(self) -> list[str]:
        return [o.ticker for o in self.per_ticker if not o.success]

    # Diagnostic shape -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        per_ticker_dicts: list[dict[str, Any]] = []
        for o in self.per_ticker:
            if hasattr(o, "to_dict"):
                per_ticker_dicts.append(o.to_dict())
            elif isinstance(o, dict):
                per_ticker_dicts.append(dict(o))
        return {
            "status":                self.status,
            "selected_tickers":      list(self.selected_tickers),
            "deferred_tickers":      list(self.deferred_tickers),
            "per_ticker":            per_ticker_dicts,
            "attempted_llm_calls":   self.attempted_llm_calls,
            "successful_llm_calls":  self.successful_llm_calls,
            "failed_llm_calls":      self.failed_llm_calls,
            "duration_ms":           self.duration_ms,
            "budget_exhausted":      self.budget_exhausted,
            "notes":                 list(self.notes),
            "agent_run_id":          self.agent_run_id,
        }


# ── Backend run function — pluggable for tests ────────────────────────────────

# Signature: (user_id, selected_tickers, started_at) -> dict[ticker, dict|None]
# A None entry means no fresh row was found for that ticker post-run (failure).
# A dict entry contains:
#   recommendation_created_at: ISO str | None
#   agent_insight_created_at:  ISO str | None
#   used_fallback:             bool
#   agent_run_id:              str | None
AnalystRunBackend = Callable[
    [UUID, list[str], datetime],
    Awaitable[dict[str, Optional[dict[str, Any]]]],
]


# ── Adapter ───────────────────────────────────────────────────────────────────

@dataclass
class AnalystRefreshBudget:
    max_tickers: int = DEFAULT_MAX_ANALYST_TICKERS_PER_RUN
    max_llm_calls: int = DEFAULT_MAX_ANALYST_LLM_CALLS_PER_RUN
    max_seconds: float = DEFAULT_MAX_ANALYST_REFRESH_SECONDS


class AnalystRefreshAdapter:
    """Narrow analyst-refresh callable injectable into EvidenceRefreshOrchestrator.

    The adapter exposes ``async def __call__(stale_tickers, *, priority_hints,
    started_at) -> AnalystRefreshResult``. The orchestrator hands it the stale
    ticker list plus optional priority hints (prior action, portfolio weight,
    evidence age). The adapter then:

      1. Prioritizes + caps to budget.
      2. Calls the injected ``run_backend`` (default = wraps AgentOrchestrator).
      3. Builds per-ticker outcomes from the returned DB state.
      4. Returns AnalystRefreshResult — never raises into the orchestrator.
    """

    def __init__(
        self,
        *,
        user_id: UUID,
        run_backend: AnalystRunBackend,
        budget: Optional[AnalystRefreshBudget] = None,
    ):
        self.user_id = user_id
        self._run_backend = run_backend
        self.budget = budget or AnalystRefreshBudget()

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

        unique = []
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
                notes=["analyst_refresh_no_stale_tickers"],
            )

        hints_by_ticker: dict[str, TickerPriorityHint] = {}
        for h in priority_hints or []:
            t = h.ticker.strip().upper() if h.ticker else ""
            if t and t in seen:
                hints_by_ticker[t] = h
        # Fill missing hints with bare ticker-only entries so the sort is total.
        full_hints = [
            hints_by_ticker.get(t) or TickerPriorityHint(ticker=t)
            for t in unique
        ]
        ranked = prioritize_stale_tickers(full_hints)

        if self.budget.max_tickers <= 0 or self.budget.max_llm_calls <= 0:
            return AnalystRefreshResult(
                status=STATUS_SKIPPED_BUDGET,
                selected_tickers=[],
                deferred_tickers=[h.ticker for h in ranked],
                per_ticker=[],
                duration_ms=int((time.monotonic() - started_monotonic) * 1000),
                budget_exhausted=True,
                notes=["analyst_refresh_skipped_budget_zero_cap"],
            )

        cap = min(self.budget.max_tickers, self.budget.max_llm_calls, len(ranked))
        selected = [h.ticker for h in ranked[:cap]]
        deferred = [h.ticker for h in ranked[cap:]]
        if deferred:
            notes.append(
                f"analyst_refresh_deferred_{len(deferred)}_tickers_over_budget"
            )

        # Time budget — surface as skipped_timeout if the caller has already
        # consumed the wall-clock window before we make the call.
        elapsed = time.monotonic() - started_monotonic
        if elapsed >= self.budget.max_seconds:
            return AnalystRefreshResult(
                status=STATUS_SKIPPED_TIMEOUT,
                selected_tickers=[],
                deferred_tickers=selected + deferred,
                per_ticker=[],
                duration_ms=int(elapsed * 1000),
                budget_exhausted=True,
                notes=["analyst_refresh_skipped_timeout"],
            )

        # Run the analyst pipeline on the selected subset, under a hard timeout.
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
                        error_reason="analyst_refresh_timeout",
                    )
                    for t in selected
                ],
                duration_ms=int((time.monotonic() - started_monotonic) * 1000),
                budget_exhausted=True,
                notes=notes + ["analyst_refresh_timeout"],
            )
        except Exception as exc:
            logger.warning(
                "analyst_refresh_adapter.backend_failed user_id=%s error=%s",
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
                notes=notes + [f"analyst_refresh_backend_error:{type(exc).__name__}"],
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
                    error_reason="no_post_run_evidence",
                    llm_call_count=1,
                    llm_success_count=0,
                ))
                continue
            agent_run_id = agent_run_id or row.get("agent_run_id")
            rec_ts = row.get("recommendation_created_at")
            insight_ts = row.get("agent_insight_created_at")
            used_fallback = bool(row.get("used_fallback", False))

            # Per-ticker honesty: success requires at least one fresh row
            # whose created_at is post-started_at AND the verdict is not a
            # fallback. Anything else stays stamped as not-refreshed.
            rec_fresh = _is_post(rec_ts, started_at)
            insight_fresh = _is_post(insight_ts, started_at)
            ok = (rec_fresh or insight_fresh) and not used_fallback
            if ok:
                successful += 1
                per_ticker.append(TickerRefreshOutcome(
                    ticker=ticker,
                    success=True,
                    refreshed_recommendation_at=rec_ts if rec_fresh else None,
                    refreshed_agent_insight_at=insight_ts if insight_fresh else None,
                    llm_call_count=1,
                    llm_success_count=1,
                ))
            else:
                failed += 1
                reason = (
                    "used_fallback_verdict" if used_fallback
                    else "no_fresh_row_post_started_at"
                )
                per_ticker.append(TickerRefreshOutcome(
                    ticker=ticker,
                    success=False,
                    error_reason=reason,
                    llm_call_count=1,
                    llm_success_count=0,
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
            budget_exhausted=(len(deferred) > 0),
            notes=notes,
            agent_run_id=agent_run_id,
        )


def _is_post(ts: Any, started_at: datetime) -> bool:
    if not isinstance(ts, str) or not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= started_at


# ── Default backend wrapping the existing AgentOrchestrator ──────────────────

async def default_agent_orchestrator_backend(
    user_id: UUID,
    selected_tickers: list[str],
    started_at: datetime,
) -> dict[str, Optional[dict[str, Any]]]:
    """Run the existing AgentOrchestrator scoped to ``selected_tickers``.

    Reuses the existing durable write paths (``agent_runs``, ``agent_insights``,
    ``recommendations``) — the adapter never opens a parallel analyst result
    store. ``force_recompute=True`` so the orchestrator bypasses TTL cache for
    the selected subset. The scope-filter (``analyst_refresh_tickers``) limits
    LLM calls + persist-time writes to that subset only — other tickers' rows
    are not expired or overwritten.

    Returns ``{ticker: {recommendation_created_at, agent_insight_created_at,
    used_fallback, agent_run_id}}`` or ``{ticker: None}`` when no row is found.
    """
    # Local imports keep the v3 module graph free of agent-stack symbols at
    # import time (and let tests stub this function out wholesale).
    from ....config import get_settings
    from ....database import get_supabase_client
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
            analyst_refresh_tickers=set(selected_tickers),
        )
        run_id = await orch.create_run(tickers=list(selected_tickers))
        try:
            await orch.run(run_id)
        except Exception as run_exc:
            logger.warning(
                "analyst_refresh_adapter.agent_run_failed user_id=%s run_id=%s err=%s",
                user_id, run_id, run_exc,
            )
        return await _read_post_run_evidence(user_id, selected_tickers, run_id, started_at)
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
    """Read per-ticker evidence rows produced by ``agent_run_id``.

    Reads are scoped to this user_id and to rows whose ``created_at`` is on or
    after ``started_at``. This is the deterministic boundary that prevents
    counting older stale rows as "fresh".
    """
    from ....database import get_supabase_client

    client = get_supabase_client()
    started_iso = started_at.isoformat()

    def _read_rows(table: str) -> list[dict[str, Any]]:
        try:
            res = (
                client.table(table)
                .select("ticker,created_at,analyst_verdict")
                if table == "agent_insights"
                else client.table(table).select("ticker,created_at,is_active,agent_run_id")
            )
            res = res.eq("user_id", str(user_id)).in_("ticker", list(tickers)).gte("created_at", started_iso).execute()
            return res.data or []
        except Exception:
            try:
                # Fallback when analyst_verdict column is missing.
                res = client.table(table).select("ticker,created_at").eq("user_id", str(user_id)).in_("ticker", list(tickers)).gte("created_at", started_iso).execute()
                return res.data or []
            except Exception as exc:
                logger.warning(
                    "analyst_refresh_adapter.read_failed table=%s err=%s",
                    table, exc,
                )
                return []

    insight_rows = await asyncio.to_thread(_read_rows, "agent_insights")
    rec_rows = await asyncio.to_thread(_read_rows, "recommendations")

    insight_by_ticker: dict[str, dict[str, Any]] = {}
    for row in insight_rows:
        t = (row.get("ticker") or "").upper()
        if not t:
            continue
        if t not in insight_by_ticker or (row.get("created_at") or "") > (insight_by_ticker[t].get("created_at") or ""):
            insight_by_ticker[t] = row

    rec_by_ticker: dict[str, dict[str, Any]] = {}
    for row in rec_rows:
        t = (row.get("ticker") or "").upper()
        if not t:
            continue
        if t not in rec_by_ticker or (row.get("created_at") or "") > (rec_by_ticker[t].get("created_at") or ""):
            rec_by_ticker[t] = row

    out: dict[str, Optional[dict[str, Any]]] = {}
    for ticker in tickers:
        up = ticker.upper()
        insight = insight_by_ticker.get(up)
        rec = rec_by_ticker.get(up)
        if insight is None and rec is None:
            out[ticker] = None
            continue
        verdict = (insight or {}).get("analyst_verdict") if isinstance(insight, dict) else None
        used_fallback = bool(isinstance(verdict, dict) and verdict.get("used_fallback"))
        out[ticker] = {
            "agent_insight_created_at": (insight or {}).get("created_at"),
            "recommendation_created_at": (rec or {}).get("created_at"),
            "used_fallback": used_fallback,
            "agent_run_id": agent_run_id,
        }
    return out
