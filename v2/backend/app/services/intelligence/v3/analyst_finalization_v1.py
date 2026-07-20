"""Run Intel v3 recovery — Phase 2 finalization (ticker/finalization split).

Root cause this closes
----------------------
Before this module the bounded on-demand ticker batch invoked
``AgentOrchestrator.run()``, which ran portfolio synthesis (Phase 4) INSIDE the
same request budget as the per-ticker analyst stage. When synthesis outran the
request deadline the whole run was cancelled and the three completed ticker
analyses were discarded — the jobs were marked
``full_portfolio_analyst_refresh_timeout`` and the queue never drained
(production failure after PR #476).

The fix splits the work in two:
  * Phase 1 — each bounded ticker batch runs ``run(run_synthesis=False)``:
    per-ticker analyst + durable persist, no synthesis. Completed ticker jobs
    are credited immediately and can never be reopened by a later
    portfolio-level failure.
  * Phase 2 — this module runs ONE portfolio-synthesis pass, then the existing
    deterministic certification + snapshot-publication path, but ONLY once
    every active-ticker analyst job has succeeded (no due, backoff, or terminal
    blockers remain).

Finalization state is represented with the EXISTING durable job + snapshot
contract — no new schema:
  * "ticker work remains"  → ``count_due_jobs`` reports due/backoff/terminal.
  * "finalization pending" → all ticker jobs done AND no ``worker_certified +
    certified_current`` snapshot exists yet.
  * "finalized"            → a fresh ``worker_certified + certified_current``
    snapshot is published.

A finalization failure preserves every succeeded ticker job, reruns ZERO
per-ticker LLM calls, and returns an explicit retryable state so the next
continuation retries finalization only.

Hard boundary: this module never imports the deterministic Intel v3 decision
policy (``decide``). It orchestrates evidence synthesis + the existing
certification path; it never decides Buy/Hold/Trim/Sell.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

from .analyst_refresh_job_store_v1 import count_due_jobs
from .full_portfolio_analyst_refresh_adapter_v1 import (
    default_finalization_synthesis_backend,
    trigger_snapshot_prewarm,
)
from .watchtower_intel_republisher_v1 import PUBLISH_CERTIFIED_CURRENT

logger = logging.getLogger(__name__)

CERTIFIED_SNAPSHOT_SOURCE = "worker_certified"

# ── Finalization outcomes ─────────────────────────────────────────────────────
FINALIZATION_SKIPPED_NOT_READY = "skipped_not_ready"
FINALIZATION_ALREADY_CERTIFIED = "already_certified"
FINALIZATION_COMPLETED = "completed"
FINALIZATION_FAILED_RETRYABLE = "failed_retryable"


# Injectable seams (production defaults wired here; tests substitute fakes).
SynthesisBackend = Callable[[UUID], Awaitable[dict[str, Any]]]
PrewarmFn = Callable[..., Awaitable[None]]
SnapshotStateReader = Callable[[UUID, Any], Awaitable[Optional[dict[str, Any]]]]


@dataclass
class FinalizationResult:
    """Observable outcome of one finalization attempt."""

    status: str = FINALIZATION_SKIPPED_NOT_READY
    ran_synthesis: bool = False
    synthesis_llm_calls: int = 0
    certified: bool = False
    published_snapshot_id: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ran_synthesis": self.ran_synthesis,
            "synthesis_llm_calls": self.synthesis_llm_calls,
            "certified": self.certified,
            "published_snapshot_id": self.published_snapshot_id,
            "reason": self.reason,
        }


def _is_certified_current(snapshot: Optional[dict[str, Any]]) -> bool:
    return (
        isinstance(snapshot, dict)
        and snapshot.get("snapshot_source") == CERTIFIED_SNAPSHOT_SOURCE
        and snapshot.get("evidence_freshness_state") == PUBLISH_CERTIFIED_CURRENT
    )


async def _default_snapshot_state_reader(
    user_id: UUID, client: Any
) -> Optional[dict[str, Any]]:
    """Read the latest snapshot's provenance via the existing service reader."""
    from .intel_v3_service import IntelV3Service

    service = IntelV3Service(user_id=user_id)
    return await service.get_latest_snapshot()


async def run_finalization_if_ready(
    *,
    user_id: "UUID | str",
    client: Any,
    tickers: Optional[list[str]] = None,
    now: Optional[datetime] = None,
    synthesis_backend: SynthesisBackend = default_finalization_synthesis_backend,
    prewarm: PrewarmFn = trigger_snapshot_prewarm,
    snapshot_state_reader: SnapshotStateReader = _default_snapshot_state_reader,
) -> FinalizationResult:
    """Run the single finalization pass when — and only when — ready.

    Ready = every active-ticker analyst job has succeeded (no due, backoff, or
    terminal job for the scoped tickers) AND no ``worker_certified +
    certified_current`` snapshot exists yet.

    Sequence when ready:
      1. Run portfolio synthesis exactly once (own request budget).
      2. Run the existing certification + snapshot-publication path (prewarm).
      3. Report whether a fresh certified-current snapshot was published.

    Never raises: any error degrades to an explicit
    ``FINALIZATION_FAILED_RETRYABLE`` so the next continuation retries
    finalization only — succeeded ticker jobs are never touched.
    """
    uid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
    now = now or datetime.now(timezone.utc)

    # ── Gate 1: no ticker work may remain ─────────────────────────────────────
    due = count_due_jobs(client, now=now, user_id=str(uid), tickers=tickers or None)
    total_due = int(due.get("total_due", 0))
    backoff = int(due.get("failed_not_yet_due", 0))
    terminal = int(due.get("failed_terminal", 0))
    if total_due > 0 or backoff > 0 or terminal > 0:
        logger.info(
            "intel_v3.finalization_skipped_not_ready user_id=%s total_due=%d "
            "failed_not_yet_due=%d failed_terminal=%d",
            uid, total_due, backoff, terminal,
        )
        return FinalizationResult(
            status=FINALIZATION_SKIPPED_NOT_READY,
            reason=f"ticker_work_remains:due={total_due},backoff={backoff},terminal={terminal}",
        )

    # ── Gate 2: idempotency — already certified-current is a no-op ────────────
    try:
        pre_snapshot = await snapshot_state_reader(uid, client)
    except Exception as exc:
        logger.warning(
            "intel_v3.finalization_snapshot_read_failed user_id=%s err=%s", uid, exc,
        )
        pre_snapshot = None
    if _is_certified_current(pre_snapshot):
        logger.info(
            "intel_v3.finalization_already_certified user_id=%s snapshot_id=%s",
            uid, (pre_snapshot or {}).get("snapshot_id"),
        )
        return FinalizationResult(
            status=FINALIZATION_ALREADY_CERTIFIED,
            certified=True,
            published_snapshot_id=(pre_snapshot or {}).get("snapshot_id"),
        )

    # ── Step 1: run synthesis exactly once (best-effort narrative) ────────────
    # Synthesis produces the portfolio-level narrative. It is NOT what certifies
    # the snapshot — the deterministic certification contract validates the
    # durable per-ticker evidence, independent of synthesis. So a synthesis
    # failure must NOT block certification: we record it and still run the
    # certification + publish step below. This also guarantees every
    # finalization attempt leaves a snapshot row (certified or
    # certification_failed) for the finalization-only retry path to recognise.
    logger.info("intel_v3.finalization_started user_id=%s", uid)
    ran_synthesis = True
    synthesis_llm_calls = 1
    synth_status = "unknown"
    try:
        synth = await synthesis_backend(uid) or {}
        synth_status = str(synth.get("status") or "unknown")
        synthesis_llm_calls = int(synth.get("synthesis_llm_calls", 1) or 0)
        if synth_status not in ("completed", "no_data"):
            logger.warning(
                "intel_v3.finalization_synthesis_failed user_id=%s status=%s "
                "(certification will still run)", uid, synth_status,
            )
    except Exception as exc:
        logger.warning(
            "intel_v3.finalization_synthesis_raised user_id=%s err=%s "
            "(certification will still run)", uid, exc,
        )
        synth_status = f"raised:{type(exc).__name__}"

    # ── Step 2: certification + snapshot publication (existing prewarm path) ──
    worker_run_id = str(uuid.uuid4())
    try:
        await prewarm(user_id=uid, worker_run_id=worker_run_id)
    except Exception as exc:
        logger.warning(
            "intel_v3.finalization_prewarm_raised user_id=%s err=%s", uid, exc,
        )
        return FinalizationResult(
            status=FINALIZATION_FAILED_RETRYABLE,
            ran_synthesis=ran_synthesis,
            synthesis_llm_calls=synthesis_llm_calls,
            reason=f"prewarm_raised:{type(exc).__name__}",
        )

    # ── Step 3: confirm a fresh certified-current snapshot was published ──────
    try:
        post_snapshot = await snapshot_state_reader(uid, client)
    except Exception as exc:
        logger.warning(
            "intel_v3.finalization_post_snapshot_read_failed user_id=%s err=%s", uid, exc,
        )
        post_snapshot = None

    certified = _is_certified_current(post_snapshot)
    published_id = (post_snapshot or {}).get("snapshot_id")
    if certified:
        logger.info(
            "intel_v3.finalization_completed user_id=%s snapshot_id=%s "
            "synthesis_status=%s synthesis_llm_calls=%d",
            uid, published_id, synth_status, synthesis_llm_calls,
        )
        return FinalizationResult(
            status=FINALIZATION_COMPLETED,
            ran_synthesis=ran_synthesis,
            synthesis_llm_calls=synthesis_llm_calls,
            certified=True,
            published_snapshot_id=published_id,
        )

    # Certification did not produce a certified-current snapshot (e.g. a holding
    # failed the evidence contract, or synthesis failed AND that blocked a
    # narrative-dependent certification check). Retryable: ticker jobs stay
    # succeeded; a later continuation retries finalization only.
    logger.warning(
        "intel_v3.finalization_not_certified user_id=%s snapshot_id=%s "
        "snapshot_source=%s freshness=%s synthesis_status=%s",
        uid, published_id,
        (post_snapshot or {}).get("snapshot_source"),
        (post_snapshot or {}).get("evidence_freshness_state"),
        synth_status,
    )
    return FinalizationResult(
        status=FINALIZATION_FAILED_RETRYABLE,
        ran_synthesis=ran_synthesis,
        synthesis_llm_calls=synthesis_llm_calls,
        certified=False,
        published_snapshot_id=published_id,
        reason=f"certification_not_current:synth={synth_status}",
    )
