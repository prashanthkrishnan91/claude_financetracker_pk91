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
from datetime import datetime, timezone
from typing import Any, Optional

from .....config import get_settings
from . import run_task_store_v1 as store
from .collectors_v1 import execute_collector_task
from .decision_tasks_v1 import execute_ticker_decision_task
from .evidence_bundle_v1 import build_evidence_bundle
from .publication_v1 import execute_publication_task
from .run_scheduler_v1 import run_scheduler_pass
from .specialist_agents_v1 import execute_review_task, execute_specialist_task
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
# nothing, exit after N consecutive passes with zero active sessions.
_BUSY_SLEEP_SECONDS = 0.5
_IDLE_SLEEP_SECONDS = 2.0
_EXIT_AFTER_IDLE_PASSES = 3


def _rows(res: Any) -> list[dict[str, Any]]:
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


def list_active_distributed_sessions(client: Any) -> list[dict[str, Any]]:
    """All users' non-terminal v2 sessions. Legacy (v1) sessions are invisible
    by construction — the filter is on workflow_version."""
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
        logger.warning("supervisor.list_active_failed err=%s", exc)
        return []


class WorkerSupervisor:
    """Drives the durable task graph for all active distributed sessions."""

    def __init__(
        self,
        *,
        client: Any = None,
        settings: Any = None,
        llm: Any = None,
        worker_id: Optional[str] = None,
        service_factory: Any = None,
    ):
        self._client = client
        self.settings = settings or get_settings()
        self._llm = llm
        self.worker_id = worker_id or store.default_worker_id()
        self.service_factory = service_factory
        self.metrics_buffer: dict[str, dict[str, int]] = {}

    # ── Lazy dependencies (kept injectable for tests) ────────────────────────
    @property
    def client(self) -> Any:
        if self._client is None:
            from .....database import get_supabase_client
            self._client = get_supabase_client()
        return self._client

    @property
    def llm(self) -> Any:
        if self._llm is None:
            from ....agents.llm import LLMClient
            self._llm = LLMClient(
                api_key=getattr(self.settings, "anthropic_api_key", "") or ""
            )
        return self._llm

    # ── One pass ─────────────────────────────────────────────────────────────
    async def run_pass(self) -> dict[str, int]:
        """One full supervisor pass: schedule → claim → execute → flush."""
        stats = {"sessions": 0, "claimed": 0, "executed": 0, "llm_calls": 0}
        sessions = await asyncio.to_thread(
            list_active_distributed_sessions, self.client
        )
        stats["sessions"] = len(sessions)
        if not sessions:
            return stats

        # Defect-D1 sweep: a task stuck 'claimed' with an expired lease and
        # zero attempts remaining (worker died on its final attempt) is
        # terminalized so its ticker/session can finish honestly instead of
        # wedging forever.
        await asyncio.to_thread(
            lambda: store.sweep_exhausted_expired_claims(self.client)
        )

        max_batch = int(
            getattr(self.settings, "intel_v3_distributed_max_specialist_batch", 5)
        )
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
            elif task_type in (TASK_SPECIALIST_ANALYSIS, TASK_REVIEW_CONFLICT):
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
                    self.client, task=task, llm=self.llm,
                )
                buffer["llm_calls"] = buffer.get("llm_calls", 0) + outcome.llm_calls
                buffer["llm_reused"] = (
                    buffer.get("llm_reused", 0) + len(outcome.reused)
                )
                stats["llm_calls"] += outcome.llm_calls
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
                outcome = await execute_review_task(
                    self.client, task=task, llm=self.llm,
                )
                buffer["llm_calls"] = buffer.get("llm_calls", 0) + outcome.llm_calls
                stats["llm_calls"] += outcome.llm_calls
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

    async def _execute_bundle(self, task: dict[str, Any]) -> None:
        session_id = str(task.get("run_session_id") or "")
        ticker = str(task.get("ticker") or "")

        def _build() -> str:
            from ..intel_run_session_store_v1 import get_session

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
        service = None
        if self.service_factory is not None:
            service = self.service_factory(str(task.get("user_id")))
        outcome = await execute_publication_task(
            self.client, task=task, service=service,
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
        idle_passes = 0
        while True:
            try:
                stats = await self.run_pass()
            except Exception as exc:
                logger.error("supervisor.pass_crashed err=%s", exc)
                stats = {"sessions": 0, "claimed": 0}
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
    """One cheap startup query; start the supervisor only if unfinished
    distributed sessions exist (crash recovery). Zero polling when idle."""
    try:
        if client is None:
            from .....database import get_supabase_client
            client = get_supabase_client()
        active = await asyncio.to_thread(
            list_active_distributed_sessions, client
        )
        if not active:
            return False
        logger.info(
            "supervisor.startup_recovery active_sessions=%d", len(active),
        )
        return await ensure_supervisor_running(client=client)
    except Exception as exc:
        logger.warning("supervisor.startup_recovery_failed err=%s", exc)
        return False
