"""Distributed Run Intel — in-process durable worker supervisor.

One lightweight supervisor per backend process:

  * activated by ``POST /intel/v3/run`` (``ensure_supervisor_running``) and by
    app startup crash-recovery (``recover_active_sessions_on_startup``);
  * loops while ANY non-terminal distributed session exists: one idempotent
    scheduler pass per session, then SQL-atomic task claims, then bounded
    concurrent execution;
  * exits completely when no active session remains — zero idle polling,
    zero idle provider/LLM/database activity afterwards;
  * durable across process termination: all state is SQL; leases expire and a
    restarted process resumes exactly where the dead one stopped;
  * safe for multiple replicas later (SKIP LOCKED claim RPC + leases +
    idempotent task identity) — the initial release runs one supervisor.

NOT FastAPI BackgroundTasks. NOT the legacy analyst_refresh worker. This is
the single execution authority for Run Intel.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from .....config import get_settings
from . import run_task_store_v1 as store
from .collectors_v1 import execute_collector_task
from .decision_tasks_v1 import execute_ticker_decision_task
from .evidence_bundle_v1 import build_evidence_bundle
from .publication_v1 import execute_publication_task
from .run_scheduler_v1 import run_scheduler_pass
from .specialist_agents_v1 import (
    execute_conflict_resolution_task,
    execute_specialist_task,
)
from .task_contracts_v1 import (
    SESSION_ACTIVE_STATES,
    TASK_BUILD_EVIDENCE_BUNDLE,
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_COLLECT_MACRO_CONTEXT,
    TASK_COLLECT_PORTFOLIO_CONTEXT,
    TASK_DEGRADED,
    TASK_FAILED,
    TASK_PORTFOLIO_JOIN_PUBLISH,
    TASK_REVIEW_CONFLICT,
    TASK_SPECIALIST_ANALYSIS,
    TASK_SUCCEEDED,
    TASK_TICKER_DECISION,
    WORKFLOW_VERSION_DISTRIBUTED,
)
from .run_task_store_v1 import TASK_FAILED_RETRYABLE

logger = logging.getLogger(__name__)

_COLLECTOR_TASK_TYPES = (
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_COLLECT_MACRO_CONTEXT,
    TASK_COLLECT_PORTFOLIO_CONTEXT,
)

# Supervisor pacing: short sleep between busy passes, longer when a pass did
# nothing, exit after N consecutive SUCCESSFUL zero-session queries.
_BUSY_SLEEP_SECONDS = 0.5
_IDLE_SLEEP_SECONDS = 2.0
_EXIT_AFTER_IDLE_PASSES = 3
# Database-outage backoff (discovery query failed): bounded exponential with
# ±30% jitter. The supervisor is retained for the whole outage.
_OUTAGE_BACKOFF_BASE_SECONDS = 1.0
_OUTAGE_BACKOFF_MAX_SECONDS = 60.0


def _rows(res: Any) -> list[dict[str, Any]]:
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


class ActiveSessionQueryFailed(Exception):
    """The active-session discovery query itself failed (database outage).

    Distinct from "zero active sessions" — the supervisor must NEVER equate
    an unanswerable query with an idle workflow.
    """


def list_active_distributed_sessions(client: Any) -> list[dict[str, Any]]:
    """All users' non-terminal v2 sessions. Legacy (v1) sessions are invisible
    by construction — the filter is on workflow_version.

    Raises :class:`ActiveSessionQueryFailed` on ANY query failure so callers
    can distinguish outage from genuinely-idle.
    """
    try:
        res = (
            client.table("intel_run_sessions")
            .select("*")
            .eq("workflow_version", WORKFLOW_VERSION_DISTRIBUTED)
            .in_("status", list(SESSION_ACTIVE_STATES))
            .execute()
        )
        return _rows(res)
    except Exception as exc:
        raise ActiveSessionQueryFailed(str(exc)) from exc


class WorkerSupervisor:
    """Drives the durable task graph for all active distributed sessions."""

    def __init__(
        self,
        *,
        client: Any = None,
        settings: Any = None,
        llm: Any = None,
        worker_id: Optional[str] = None,
    ):
        self._client = client
        self.settings = settings or get_settings()
        # Explicit override (tests, callers wiring a single fake).
        self._llm = llm
        self._specialist_llm: Any = None
        self.worker_id = worker_id or store.default_worker_id()
        self.metrics_buffer: dict[str, dict[str, int]] = {}

    # ── Lazy dependencies (kept injectable for tests) ────────────────────────
    @property
    def client(self) -> Any:
        if self._client is None:
            from .....database import get_supabase_client
            self._client = get_supabase_client()
        return self._client

    @property
    def specialist_llm(self) -> Any:
        """Standard specialist analysis: configured Haiku model, no fallback.

        A specialist task never auto-escalates to Sonnet — a failed call
        exhausts the durable task retry budget on the same cheap model.
        """
        if self._llm is not None:
            return self._llm
        if self._specialist_llm is None:
            from ....agents.llm import LLMClient
            self._specialist_llm = LLMClient(
                api_key=getattr(self.settings, "anthropic_api_key", "") or "",
                model=getattr(
                    self.settings,
                    "intel_v3_distributed_specialist_model",
                    "claude-haiku-4-5-20251001",
                ),
                fallback_model=None,
            )
        return self._specialist_llm

    def _effective_specialist_batch_cap(self) -> int:
        """Batch size the scheduler chunks specialist tickers into.

        `intel_v3_distributed_max_specialist_batch` stays the unrelated
        architectural ceiling for any model. When the configured specialist
        model is a Haiku model (the normal routing —
        `WorkerSupervisor.specialist_llm`), the narrower
        `intel_v3_distributed_haiku_max_specialist_batch` applies instead so
        compact-JSON Haiku output stays reliable — it can only narrow the
        batch, never widen past the architectural ceiling.
        """
        global_max = int(
            getattr(self.settings, "intel_v3_distributed_max_specialist_batch", 5)
        )
        specialist_model = str(
            getattr(
                self.settings, "intel_v3_distributed_specialist_model",
                "claude-haiku-4-5-20251001",
            ) or ""
        )
        if "haiku" not in specialist_model.lower():
            return max(1, global_max)
        haiku_max = int(
            getattr(
                self.settings,
                "intel_v3_distributed_haiku_max_specialist_batch", 2,
            )
        )
        return max(1, min(haiku_max, global_max))

    # ── One pass ─────────────────────────────────────────────────────────────
    async def run_pass(self) -> dict[str, int]:
        """One full supervisor pass: schedule → claim → execute → flush.

        Raises :class:`ActiveSessionQueryFailed` when discovery itself fails —
        the loop treats that as a database outage (backoff + retain), never as
        an idle workflow.
        """
        stats = {"sessions": 0, "claimed": 0, "executed": 0, "llm_calls": 0}
        sessions = await asyncio.to_thread(
            list_active_distributed_sessions, self.client
        )
        stats["sessions"] = len(sessions)
        if not sessions:
            return stats

        # Crashed-create repair (fail-closed graph contract): a session left
        # in 'created' has an incomplete/unverified graph — compare against
        # the expected graph, create only what's missing, verify, and only
        # then transition it to running. No browser traffic required.
        for session in sessions:
            if str(session.get("status") or "") == "created":
                try:
                    from .session_control_v1 import repair_session_graph

                    repaired = await repair_session_graph(
                        client=self.client,
                        user_id=str(session.get("user_id")),
                        session=session,
                    )
                    if repaired:
                        session["status"] = "running"
                except Exception as exc:
                    logger.warning(
                        "supervisor.session_repair_failed session=%s err=%s",
                        session.get("id"), exc,
                    )

        # Exhausted-claim sweep: a task stuck 'claimed' with an expired lease
        # and zero attempts remaining (worker died on its final attempt) is
        # terminalized so its ticker/session can finish honestly instead of
        # wedging forever.
        await asyncio.to_thread(
            lambda: store.sweep_exhausted_expired_claims(self.client)
        )

        max_batch = self._effective_specialist_batch_cap()
        for session in sessions:
            try:
                await asyncio.to_thread(
                    lambda s=session: run_scheduler_pass(
                        self.client, session=s, max_specialist_batch=max_batch,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "supervisor.scheduler_pass_failed session=%s err=%s",
                    session.get("id"), exc,
                )

        collector_limit = int(
            getattr(
                self.settings,
                "intel_v3_distributed_max_collector_concurrency", 4,
            )
        )
        llm_limit = int(
            getattr(self.settings, "intel_v3_distributed_max_llm_concurrency", 2)
        )
        lease_seconds = int(
            getattr(self.settings, "intel_v3_distributed_task_lease_seconds", 300)
        )
        claim_limit = max(collector_limit + llm_limit + 2, 4)

        claimed = await asyncio.to_thread(
            lambda: store.claim_tasks(
                self.client,
                worker_id=self.worker_id,
                limit=claim_limit,
                lease_seconds=lease_seconds,
            )
        )
        stats["claimed"] = len(claimed)
        if not claimed:
            await self._flush_metrics()
            return stats

        collector_semaphore = asyncio.Semaphore(max(1, collector_limit))
        llm_semaphore = asyncio.Semaphore(max(1, llm_limit))

        async def _run(task: dict[str, Any]) -> None:
            task_type = str(task.get("task_type") or "")
            if task_type in _COLLECTOR_TASK_TYPES:
                async with collector_semaphore:
                    await self._execute_one(task, stats)
            elif task_type == TASK_SPECIALIST_ANALYSIS:
                async with llm_semaphore:
                    await self._execute_one(task, stats)
            else:
                await self._execute_one(task, stats)

        await asyncio.gather(*(_run(task) for task in claimed))
        await self._flush_metrics()
        return stats

    # ── Task dispatch ────────────────────────────────────────────────────────
    async def _execute_one(self, task: dict[str, Any], stats: dict[str, int]) -> None:
        task_type = str(task.get("task_type") or "")
        session_id = str(task.get("run_session_id") or "")
        buffer = self.metrics_buffer.setdefault(session_id, {})
        try:
            if task_type in _COLLECTOR_TASK_TYPES:
                result = await execute_collector_task(
                    self.client, task=task, settings=self.settings,
                )
                buffer["provider_calls"] = (
                    buffer.get("provider_calls", 0) + result.provider_calls
                )
                if result.cache_hit:
                    buffer["cache_hits"] = buffer.get("cache_hits", 0) + 1
                else:
                    buffer["lanes_refreshed"] = buffer.get("lanes_refreshed", 0) + 1
                await asyncio.to_thread(
                    lambda: store.complete_task(
                        self.client,
                        task=task,
                        worker_id=self.worker_id,
                        final_state=result.final_state,
                        output=result.output,
                        output_ref=result.output_ref,
                        error_code=result.error_code,
                        error_detail=result.error_detail,
                    )
                )
            elif task_type == TASK_BUILD_EVIDENCE_BUNDLE:
                await self._execute_bundle(task)
            elif task_type == TASK_SPECIALIST_ANALYSIS:
                outcome = await execute_specialist_task(
                    self.client, task=task, llm=self.specialist_llm,
                )
                buffer["llm_calls"] = buffer.get("llm_calls", 0) + outcome.llm_calls
                buffer["llm_reused"] = (
                    buffer.get("llm_reused", 0) + len(outcome.reused)
                )
                buffer["specialist_repair_calls"] = (
                    buffer.get("specialist_repair_calls", 0) + outcome.repair_calls
                )
                buffer["specialist_truncations"] = (
                    buffer.get("specialist_truncations", 0) + outcome.truncated_calls
                )
                buffer["specialist_quota_failures"] = (
                    buffer.get("specialist_quota_failures", 0)
                    + outcome.quota_or_auth_failures
                )
                if outcome.partial_success:
                    buffer["specialist_partial_successes"] = (
                        buffer.get("specialist_partial_successes", 0) + 1
                    )
                self._buffer_model_metrics(buffer, outcome.models_used)
                stats["llm_calls"] += outcome.llm_calls
                logger.info(
                    "specialist_task.completed session=%s axis=%s requested=%d "
                    "persisted=%d reused=%d malformed=%d llm_calls=%d "
                    "repair_calls=%d truncated_calls=%d quota_or_auth_failures=%d "
                    "model=%s failure=%s",
                    session_id, task.get("lane"), len(outcome.requested_tickers),
                    len(outcome.persisted), len(outcome.reused),
                    len(outcome.malformed), outcome.llm_calls, outcome.repair_calls,
                    outcome.truncated_calls, outcome.quota_or_auth_failures,
                    outcome.models_used[-1] if outcome.models_used else "",
                    outcome.error or "",
                )
                await asyncio.to_thread(
                    lambda: store.complete_task(
                        self.client,
                        task=task,
                        worker_id=self.worker_id,
                        final_state=outcome.final_state,
                        error_code=outcome.error,
                        error_detail=(
                            f"malformed={outcome.malformed} "
                            f"skipped={outcome.skipped_insufficient}"
                            if (outcome.malformed or outcome.skipped_insufficient)
                            else None
                        ),
                    )
                )
            elif task_type == TASK_REVIEW_CONFLICT:
                # Deterministic conflict resolution — ordinary work, no LLM
                # semaphore, zero provider/LLM calls, zero llm_calls/model
                # metrics.
                outcome = await execute_conflict_resolution_task(
                    self.client, task=task,
                )
                if outcome.persisted:
                    buffer["deterministic_conflicts_resolved"] = (
                        buffer.get("deterministic_conflicts_resolved", 0) + 1
                    )
                await asyncio.to_thread(
                    lambda: store.complete_task(
                        self.client,
                        task=task,
                        worker_id=self.worker_id,
                        final_state=outcome.final_state,
                        error_code=outcome.error,
                    )
                )
            elif task_type == TASK_TICKER_DECISION:
                outcome = await execute_ticker_decision_task(
                    self.client, task=task,
                )
                final = (
                    TASK_SUCCEEDED if outcome.error is None
                    else TASK_FAILED_RETRYABLE
                )
                await asyncio.to_thread(
                    lambda: store.complete_task(
                        self.client,
                        task=task,
                        worker_id=self.worker_id,
                        final_state=final,
                        error_code=outcome.error,
                    )
                )
            elif task_type == TASK_PORTFOLIO_JOIN_PUBLISH:
                await self._execute_publication(task)
            else:
                await asyncio.to_thread(
                    lambda: store.complete_task(
                        self.client,
                        task=task,
                        worker_id=self.worker_id,
                        final_state=TASK_FAILED,
                        error_code="unknown_task_type",
                        error_detail=task_type,
                    )
                )
            stats["executed"] += 1
        except Exception as exc:
            logger.warning(
                "supervisor.task_execution_failed task=%s type=%s err=%s",
                task.get("id"), task_type, exc,
            )
            await asyncio.to_thread(
                lambda: store.complete_task(
                    self.client,
                    task=task,
                    worker_id=self.worker_id,
                    final_state=TASK_FAILED_RETRYABLE,
                    error_code="executor_exception",
                    error_detail=f"{type(exc).__name__}: {exc}",
                )
            )

    @staticmethod
    def _buffer_model_metrics(
        buffer: dict[str, int], models_used: list[str],
    ) -> None:
        """Per-session LLM-call counts by model (observability only, never a
        decision input) — reuses the existing metrics buffer/flush seam."""
        for model in models_used:
            key = f"llm_calls_model:{model}"
            buffer[key] = buffer.get(key, 0) + 1

    async def _execute_bundle(self, task: dict[str, Any]) -> None:
        session_id = str(task.get("run_session_id") or "")
        ticker = str(task.get("ticker") or "")

        def _build() -> str:
            from ..intel_run_session_store_v1 import get_session

            # Claim fence (adversarial audit D3): a stale reclaimed bundle
            # worker must not (re)write the ticker's bundle.
            if not store.owns_claim(self.client, task):
                raise RuntimeError("claim_lost")
            session = get_session(self.client, session_id) or {"id": session_id}
            rows = store.list_ticker_rows(self.client, run_session_id=session_id)
            row = next(
                (r for r in rows if str(r.get("ticker")) == ticker), None
            )
            if row is None:
                raise RuntimeError("ticker_row_missing")
            bundle = build_evidence_bundle(
                self.client, session=session, ticker_row=row,
            )
            missing = bundle.get("required_lanes_missing") or []
            return TASK_DEGRADED if missing else TASK_SUCCEEDED

        try:
            final = await asyncio.to_thread(_build)
            error_code = None
        except Exception as exc:
            final = TASK_FAILED_RETRYABLE
            error_code = f"bundle_error:{type(exc).__name__}"
        await asyncio.to_thread(
            lambda: store.complete_task(
                self.client,
                task=task,
                worker_id=self.worker_id,
                final_state=final,
                error_code=error_code,
            )
        )

    async def _execute_publication(self, task: dict[str, Any]) -> None:
        outcome = await execute_publication_task(
            self.client, task=task, settings=self.settings,
        )
        completed = await asyncio.to_thread(
            lambda: store.complete_task(
                self.client,
                task=task,
                worker_id=self.worker_id,
                final_state=outcome.final_state,
                output_ref=outcome.snapshot_row_id,
                error_code=outcome.error,
            )
        )
        # Publication retry budget exhausted → honest terminal session failure.
        if completed and outcome.final_state == TASK_FAILED_RETRYABLE:
            attempts = int(task.get("attempts") or 0)
            max_attempts = int(task.get("max_attempts") or 3)
            if attempts >= max_attempts:
                from .publication_v1 import _mark_session

                await _mark_session(
                    self.client, str(task.get("run_session_id")),
                    status="failed",
                    last_error=(
                        f"publication_retry_budget_exhausted:{outcome.error}"
                    ),
                )

    async def _flush_metrics(self) -> None:
        if not self.metrics_buffer:
            return
        buffered = self.metrics_buffer
        self.metrics_buffer = {}

        def _flush() -> None:
            for session_id, counters in buffered.items():
                if not counters:
                    continue
                try:
                    res = (
                        self.client.table("intel_run_sessions")
                        .select("metrics")
                        .eq("id", session_id)
                        .limit(1)
                        .execute()
                    )
                    rows = _rows(res)
                    metrics = (rows[0].get("metrics") if rows else {}) or {}
                    for key, delta in counters.items():
                        metrics[key] = int(metrics.get(key) or 0) + int(delta)
                    (
                        self.client.table("intel_run_sessions")
                        .update({"metrics": metrics})
                        .eq("id", session_id)
                        .execute()
                    )
                except Exception as exc:
                    logger.debug(
                        "supervisor.metrics_flush_failed session=%s err=%s",
                        session_id, exc,
                    )

        await asyncio.to_thread(_flush)

    # ── Loop ─────────────────────────────────────────────────────────────────
    async def run_until_idle(self) -> None:
        """Loop until a SUCCESSFUL discovery query returns zero sessions.

        Outage contract (completion item 4): a failed active-session query is
        NEVER an idle signal. On query failure the supervisor is retained and
        retries with bounded exponential backoff + jitter, performing zero
        provider/LLM work; the idle-exit counter only advances after a query
        that succeeded AND returned zero active sessions.
        """
        import random

        idle_passes = 0
        outage_failures = 0
        while True:
            try:
                stats = await self.run_pass()
            except ActiveSessionQueryFailed as exc:
                outage_failures += 1
                delay = min(
                    _OUTAGE_BACKOFF_MAX_SECONDS,
                    _OUTAGE_BACKOFF_BASE_SECONDS * (2 ** (outage_failures - 1)),
                )
                delay = delay * (1.0 + random.uniform(-0.3, 0.3))
                logger.warning(
                    "supervisor.discovery_outage worker=%s failures=%d "
                    "retry_in=%.1fs err=%s — retaining supervisor, workflow "
                    "NOT idle",
                    self.worker_id, outage_failures, delay, exc,
                )
                await asyncio.sleep(max(0.05, delay))
                continue
            except Exception as exc:
                # A pass crash after successful discovery: keep the loop, but
                # never let it count toward idle exit.
                logger.error("supervisor.pass_crashed err=%s", exc)
                await asyncio.sleep(_IDLE_SLEEP_SECONDS)
                continue
            outage_failures = 0
            if stats["sessions"] == 0:
                idle_passes += 1
                if idle_passes >= _EXIT_AFTER_IDLE_PASSES:
                    logger.info("supervisor.exiting_idle worker=%s", self.worker_id)
                    return
                await asyncio.sleep(_IDLE_SLEEP_SECONDS)
                continue
            idle_passes = 0
            await asyncio.sleep(
                _BUSY_SLEEP_SECONDS if stats["claimed"] else _IDLE_SLEEP_SECONDS
            )


# ── Process-level singleton ──────────────────────────────────────────────────

_SUPERVISOR_TASK: Optional[asyncio.Task] = None
_SUPERVISOR_LOCK = asyncio.Lock()


async def ensure_supervisor_running(**kwargs: Any) -> bool:
    """Start the singleton supervisor task if not already running.

    Returns True when a supervisor is (now) running. Never raises into the
    caller — Run Intel session creation must not fail because the supervisor
    could not start (a restart / next request recovers it).
    """
    global _SUPERVISOR_TASK
    try:
        async with _SUPERVISOR_LOCK:
            if _SUPERVISOR_TASK is not None and not _SUPERVISOR_TASK.done():
                return True
            supervisor = WorkerSupervisor(**kwargs)

            async def _run() -> None:
                try:
                    await supervisor.run_until_idle()
                except Exception as exc:
                    logger.error("supervisor.crashed err=%s", exc)

            _SUPERVISOR_TASK = asyncio.create_task(_run())
            logger.info(
                "supervisor.started worker=%s", supervisor.worker_id,
            )
            return True
    except Exception as exc:
        logger.error("supervisor.start_failed err=%s", exc)
        return False


def _reset_supervisor_for_testing() -> None:
    global _SUPERVISOR_TASK
    _SUPERVISOR_TASK = None


async def recover_active_sessions_on_startup(client: Any = None) -> bool:
    """Startup supervisor PROBE (crash recovery without browser traffic).

    Starts one supervisor immediately on application startup. Its loop exits
    only after a SUCCESSFUL zero-active-session query — so it survives a
    transient database failure at boot (bounded backoff, zero provider/LLM
    work) and resumes any unfinished distributed sessions without needing a
    single browser request. When the workflow is genuinely idle, the probe's
    first successful queries return zero sessions and it exits within a few
    seconds — no permanent polling cost.
    """
    try:
        logger.info("supervisor.startup_probe_starting")
        # Client stays LAZY (constructed inside the supervisor's first pass):
        # a boot-time client-construction failure then lands in the loop's
        # outage handling instead of aborting recovery entirely.
        if client is not None:
            return await ensure_supervisor_running(client=client)
        return await ensure_supervisor_running()
    except Exception as exc:
        logger.warning("supervisor.startup_recovery_failed err=%s", exc)
        return False
