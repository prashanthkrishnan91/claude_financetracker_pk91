"""Analyst refresh-request seam (Intel v3 Stage 3.1).

The synchronous Run Intel v3 HTTP request must return a trusted snapshot
quickly. It must NOT perform any analyst / LLM / full-portfolio research inside
the click. Stages 3.0b.6 and 3.0c wired the LLM ``AgentOrchestrator`` directly
into the synchronous orchestrator, which made the request attempt comprehensive
live research — architecturally wrong for the product goal.

This module is the replacement seam. It plugs into the Evidence Refresh
Orchestrator's existing ``analyst_refresh`` injection point with the same
callable contract, but does **zero** LLM / provider work. When analyst evidence
(``recommendations`` / ``agent_insights``) is stale, the seam:

  * records / logs that an analyst refresh is *required* for the affected
    tickers, and
  * returns an honest result the orchestrator consumes: no tickers refreshed,
    the stale tickers reported as ``deferred`` (so the run mode degrades to
    PARTIAL_CERTIFIED / BLOCKED_UNCERTIFIED, never fake FAST_CERTIFIED),
    ``attempted_llm_calls = 0``.

What this seam is NOT:
  * It is not a background worker or scheduler. It does not run a job and does
    not claim one is running. A future continuous Intelligence Plane PR will
    consume these requests.
  * It does not widen ``decide()`` authority. Deterministic policy still owns
    the visible Buy/Hold/Trim/Sell action.
  * It does not write ``intel_v3_snapshots`` and does not touch Deploy /
    Watchtower / broker / tax / DB schemas.

The LLM adapters (``AnalystRefreshAdapter`` / ``FullPortfolioAnalystRefreshAdapter``)
are retained in the repo for that future background plane — they are simply no
longer wired into the synchronous request path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# Status surfaced in snapshot diagnostics as ``analyst_refresh_status``.
STATUS_REFRESH_REQUESTED = "refresh_requested"
STATUS_NO_STALE = "no_stale"


@dataclass
class AnalystRefreshRequestResult:
    """Outcome of one seam invocation.

    Shape mirrors the dict the Evidence Refresh Orchestrator already consumes
    from the LLM analyst adapters (``status`` / ``selected_tickers`` /
    ``deferred_tickers`` / ``per_ticker`` / ``attempted_llm_calls`` / ...), so
    the orchestrator needs no changes to accept the seam.
    """

    status: str
    requested_tickers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # Fixed: a request seam never refreshes or calls an LLM in-request.
    selected_tickers: list[str] = field(default_factory=list)
    per_ticker: list[dict[str, Any]] = field(default_factory=list)
    attempted_llm_calls: int = 0
    successful_llm_calls: int = 0
    failed_llm_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            # The stale tickers are reported as deferred so the orchestrator's
            # post-refresh classification keeps them stale/uncertified rather
            # than silently certifying the run.
            "deferred_tickers": list(self.requested_tickers),
            "requested_tickers": list(self.requested_tickers),
            "selected_tickers": list(self.selected_tickers),
            "per_ticker": list(self.per_ticker),
            "attempted_llm_calls": self.attempted_llm_calls,
            "successful_llm_calls": self.successful_llm_calls,
            "failed_llm_calls": self.failed_llm_calls,
            "notes": list(self.notes),
        }


class AnalystRefreshRequestSeam:
    """Non-LLM analyst refresh-request seam for the synchronous Run Intel v3 path.

    Callable contract matches the LLM analyst adapters so it can be injected
    into ``EvidenceRefreshOrchestrator(analyst_refresh=...)`` unchanged:

        async __call__(stale_tickers, *, priority_hints=None, started_at=None)
            -> AnalystRefreshRequestResult
    """

    def __init__(self, *, user_id: UUID):
        self.user_id = user_id

    async def __call__(
        self,
        stale_tickers: list[str],
        *,
        priority_hints: Optional[list[Any]] = None,
        started_at: Optional[datetime] = None,
    ) -> AnalystRefreshRequestResult:
        requested = []
        seen: set[str] = set()
        for raw in stale_tickers or []:
            t = str(raw or "").upper()
            if t and t not in seen:
                seen.add(t)
                requested.append(t)

        if not requested:
            return AnalystRefreshRequestResult(
                status=STATUS_NO_STALE,
                requested_tickers=[],
                notes=["analyst_refresh_request_seam_no_stale_tickers"],
            )

        # Record the request. This is the seam's only side effect — a log line.
        # It does NOT enqueue a durable job; a future Intelligence Plane PR owns
        # consumption. The log is the honest, auditable record that the
        # synchronous request identified stale analyst evidence and declined to
        # run an in-request LLM refresh.
        logger.info(
            "intel_v3.analyst_refresh_requested user_id=%s ticker_count=%d "
            "tickers=%s in_request_llm_refresh=false reason=synchronous_path_no_llm",
            self.user_id,
            len(requested),
            ",".join(requested),
        )

        return AnalystRefreshRequestResult(
            status=STATUS_REFRESH_REQUESTED,
            requested_tickers=requested,
            notes=["analyst_refresh_requested_not_run_in_request"],
        )
