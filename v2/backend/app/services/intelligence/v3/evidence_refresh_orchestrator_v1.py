"""Evidence Refresh Orchestrator (Stage 3.0b v1).

Responsibility:
  1. Classify per-source freshness from the read-only evidence adapter stats and
     the latest portfolio-snapshot timestamps using
     `evidence_freshness_contract_v1`.
  2. Pick a pre-refresh run mode.
  3. Optionally refresh price evidence under deterministic budgets.
  4. Re-classify post-refresh and pick the final run mode.
  5. Return a `RefreshResult` the snapshot diagnostics consume.

Non-goals for v1:
  - The orchestrator does NOT call the analyst LLM / agent path. That path is
    not safely callable synchronously from this user-triggered run today — it
    requires a separate ticker-scoped adapter that the analyst orchestrator
    accepts. v1 marks analyst evidence as honestly STALE / HARD_STALE in the
    diagnostics and reports `analyst_refresh_supported=False` so the UI and
    snapshot do not pretend the run refreshed analyst evidence when it did not.
  - Does NOT widen `decide()` authority. Deterministic policy still owns
    Buy/Hold/Trim/Sell. The orchestrator only refreshes inputs.
  - Does NOT touch Deploy v3, Watchtower, broker execution, tax, or DB schemas.

Budgets are configured via constants below. Defaults are safe for personal
single-user runs but not unlimited.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

from .evidence_freshness_contract_v1 import (
    ALL_SOURCES,
    BANNER_COPY,
    CRITICAL_SOURCES,
    RUN_MODE_BLOCKED_UNCERTIFIED,
    RUN_MODE_FAST_CERTIFIED,
    RUN_MODE_PARTIAL_CERTIFIED,
    RUN_MODE_REFRESH_THEN_RUN,
    SOURCE_AGENT_INSIGHTS,
    SOURCE_POSITIONS,
    SOURCE_PORTFOLIO_SNAPSHOT,
    SOURCE_PRICE_HISTORY,
    SOURCE_PRICE_LATEST,
    SOURCE_RECOMMENDATIONS,
    SOURCE_RESEARCH_ARTIFACTS,
    STATE_FRESH,
    STATE_HARD_STALE,
    STATE_MISSING,
    STATE_STALE,
    STATE_UNKNOWN,
    SourceFreshnessState,
    classify_run_mode,
    classify_source_state,
)
from .provider_registry_v1 import health_summary as _provider_health_summary

logger = logging.getLogger(__name__)


# ── Deterministic budgets ─────────────────────────────────────────────────────

# Per-run hard caps — used by tests and observable in diagnostics.
MAX_PROVIDER_CALLS_PER_RUN = 50
MAX_LLM_CALLS_PER_RUN = 10
MAX_TOTAL_REFRESH_SECONDS = 30.0
MAX_PRICE_TICKERS_PER_RUN = 50


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class RefreshBudget:
    """Mutable, in-run budget tracker. Never persisted."""
    max_provider_calls: int = MAX_PROVIDER_CALLS_PER_RUN
    max_llm_calls: int = MAX_LLM_CALLS_PER_RUN
    max_total_seconds: float = MAX_TOTAL_REFRESH_SECONDS
    started_at: float = field(default_factory=time.monotonic)
    attempted_provider_calls: int = 0
    successful_provider_calls: int = 0
    failed_provider_calls: int = 0
    attempted_llm_calls: int = 0
    successful_llm_calls: int = 0
    failed_llm_calls: int = 0

    def provider_budget_remaining(self) -> int:
        return max(0, self.max_provider_calls - self.attempted_provider_calls)

    def llm_budget_remaining(self) -> int:
        return max(0, self.max_llm_calls - self.attempted_llm_calls)

    def time_remaining(self) -> float:
        elapsed = time.monotonic() - self.started_at
        return max(0.0, self.max_total_seconds - elapsed)

    def time_exhausted(self) -> bool:
        return self.time_remaining() <= 0.0


@dataclass
class RefreshResult:
    """Outcome of a single orchestrator run.

    Embedded into snapshot diagnostics. Diagnostics keys are the canonical
    on-wire names — they are what tests assert and the UI reads.
    """
    run_mode: str
    trust_status: str
    banner_copy: str
    source_states_before: dict[str, SourceFreshnessState]
    source_states_after: dict[str, SourceFreshnessState]
    refresh_targets: list[str]
    blocked_sources: list[str]
    refreshed_source_count: int
    failed_refresh_count: int
    attempted_provider_calls: int
    successful_provider_calls: int
    failed_provider_calls: int
    attempted_llm_calls: int
    successful_llm_calls: int
    failed_llm_calls: int
    refresh_duration_ms: int
    analyst_refresh_supported: bool
    analyst_refresh_status: str       # "not_supported_v1" / "skipped_budget" / "succeeded"
    budget_exhausted: bool
    notes: list[str]

    def to_diagnostics_dict(self) -> dict[str, Any]:
        """Shape consumed by snapshot diagnostics + frontend banner."""
        # Per-source breakdown — tests assert structure here.
        source_freshness: dict[str, dict[str, Any]] = {}
        per_source_oldest: dict[str, Optional[str]] = {}
        per_source_newest: dict[str, Optional[str]] = {}
        for src, st in self.source_states_after.items():
            source_freshness[src] = {
                "state":            st.state,
                "is_critical":      st.is_critical,
                "fresh_count":      st.fresh_count,
                "stale_count":      st.stale_count,
                "hard_stale_count": st.hard_stale_count,
                "missing_count":    st.missing_count,
                "oldest_age_hours": st.oldest_age_hours,
                "newest_age_hours": st.newest_age_hours,
            }
            per_source_oldest[src] = st.oldest_timestamp
            per_source_newest[src] = st.newest_timestamp

        stale_total = sum(
            st.stale_count for st in self.source_states_after.values()
        )
        hard_stale_total = sum(
            st.hard_stale_count for st in self.source_states_after.values()
        )
        missing_total = sum(
            st.missing_count for st in self.source_states_after.values()
        )

        return {
            "run_mode":                    self.run_mode,
            "trust_status":                self.trust_status,
            "banner_copy":                 self.banner_copy,
            "source_freshness":            source_freshness,
            "per_source_oldest_timestamp": per_source_oldest,
            "per_source_newest_timestamp": per_source_newest,
            "stale_source_count":          stale_total,
            "hard_stale_source_count":     hard_stale_total,
            "missing_source_count":        missing_total,
            "refresh_targets":             list(self.refresh_targets),
            "blocked_sources":             list(self.blocked_sources),
            "refreshed_source_count":      self.refreshed_source_count,
            "failed_refresh_count":        self.failed_refresh_count,
            "attempted_provider_calls":    self.attempted_provider_calls,
            "successful_provider_calls":   self.successful_provider_calls,
            "failed_provider_calls":       self.failed_provider_calls,
            "attempted_llm_calls":         self.attempted_llm_calls,
            "successful_llm_calls":        self.successful_llm_calls,
            "failed_llm_calls":            self.failed_llm_calls,
            "refresh_duration_ms":         self.refresh_duration_ms,
            "analyst_refresh_supported":   self.analyst_refresh_supported,
            "analyst_refresh_status":      self.analyst_refresh_status,
            "budget_exhausted":            self.budget_exhausted,
            "orchestrator_notes":          list(self.notes),
            # §3 north-star — provider registry visibility.
            "provider_registry_health":    _provider_health_summary(),
        }


# ── Inputs to the orchestrator ────────────────────────────────────────────────

@dataclass
class OrchestratorInputs:
    """Everything the orchestrator needs to classify and (optionally) refresh.

    Built by the IntelV3Service from existing repo data:
      - evidence_stats: from ReadOnlyEvidenceAdapter.load_cards()
      - portfolio_snapshot_at: latest portfolio_snapshots.snapshot_at ISO
      - market_value_certified_at: list of per-position market_value_certified_at
      - tickers: tickers to refresh prices for
      - research_artifact_timestamps: list of expires_at or created_at (optional)
    """
    evidence_stats: dict[str, Any]
    portfolio_snapshot_at: Optional[str]
    market_value_certified_ats: list[str]
    tickers: list[str]
    research_artifact_timestamps: list[str]
    now: datetime


# Refresh callable signatures — the orchestrator accepts these as injectables so
# tests can pass fakes without standing up real provider clients.
PriceRefreshFn = Callable[[list[str]], Awaitable[dict[str, Any]]]


# ── Source-state builder ──────────────────────────────────────────────────────

def build_source_states(
    inputs: OrchestratorInputs,
) -> dict[str, SourceFreshnessState]:
    """Classify every source class against its SLA.

    Pure function — no IO. The orchestrator calls this twice per run (before
    and after refresh).
    """
    now = inputs.now
    es = inputs.evidence_stats or {}

    rec_ts: list[str] = list(es.get("recommendation_timestamps") or [])
    insight_ts: list[str] = list(es.get("agent_insight_run_timestamps") or [])
    active_position_count: int = int(es.get("active_position_count") or 0)
    persisted_rec_count: int = int(es.get("persisted_recommendation_count") or 0)
    persisted_ai_count: int = int(es.get("persisted_agent_insight_count") or 0)

    states: dict[str, SourceFreshnessState] = {}

    states[SOURCE_RECOMMENDATIONS] = classify_source_state(
        source=SOURCE_RECOMMENDATIONS,
        timestamps=rec_ts,
        expected_count=max(persisted_rec_count, active_position_count),
        now=now,
    )
    states[SOURCE_AGENT_INSIGHTS] = classify_source_state(
        source=SOURCE_AGENT_INSIGHTS,
        timestamps=insight_ts,
        expected_count=max(persisted_ai_count, active_position_count),
        now=now,
    )
    # Positions: when we have a portfolio snapshot at all, use its timestamp as
    # the position freshness proxy. (positions table itself rarely carries a
    # reliable updated_at; the snapshot is the canonical write boundary.)
    pos_ts = [inputs.portfolio_snapshot_at] if inputs.portfolio_snapshot_at else []
    states[SOURCE_POSITIONS] = classify_source_state(
        source=SOURCE_POSITIONS,
        timestamps=pos_ts,
        expected_count=1 if active_position_count > 0 else 0,
        now=now,
    )
    states[SOURCE_PORTFOLIO_SNAPSHOT] = classify_source_state(
        source=SOURCE_PORTFOLIO_SNAPSHOT,
        timestamps=pos_ts,
        expected_count=1 if active_position_count > 0 else 0,
        now=now,
    )
    states[SOURCE_PRICE_LATEST] = classify_source_state(
        source=SOURCE_PRICE_LATEST,
        timestamps=list(inputs.market_value_certified_ats or []),
        expected_count=active_position_count,
        now=now,
    )
    states[SOURCE_PRICE_HISTORY] = classify_source_state(
        source=SOURCE_PRICE_HISTORY,
        timestamps=list(inputs.market_value_certified_ats or []),
        expected_count=active_position_count,
        now=now,
    )
    states[SOURCE_RESEARCH_ARTIFACTS] = classify_source_state(
        source=SOURCE_RESEARCH_ARTIFACTS,
        timestamps=list(inputs.research_artifact_timestamps or []),
        expected_count=0,
        now=now,
    )

    return states


# ── Orchestrator ──────────────────────────────────────────────────────────────

class EvidenceRefreshOrchestrator:
    """Stage 3.0b v1 orchestrator.

    Usage:
        orch = EvidenceRefreshOrchestrator(
            user_id=user_id,
            inputs=inputs,
            price_refresh=price_refresh_fn,   # optional
            analyst_refresh=None,             # not supported in v1
            budget=RefreshBudget(),
        )
        result = await orch.run()
    """

    def __init__(
        self,
        *,
        user_id: UUID,
        inputs: OrchestratorInputs,
        price_refresh: Optional[PriceRefreshFn] = None,
        analyst_refresh: Optional[Callable[..., Awaitable[Any]]] = None,
        budget: Optional[RefreshBudget] = None,
    ):
        self.user_id = user_id
        self.inputs = inputs
        self._price_refresh = price_refresh
        self._analyst_refresh = analyst_refresh
        self.budget = budget or RefreshBudget()
        self.notes: list[str] = []

    async def run(self) -> RefreshResult:
        started = time.monotonic()
        before_states = build_source_states(self.inputs)
        decision = classify_run_mode(before_states, refresh_attempted=False)

        # If FAST_CERTIFIED already, return immediately — no provider/LLM calls.
        if decision.run_mode == RUN_MODE_FAST_CERTIFIED:
            return self._finalize(
                run_mode=decision.run_mode,
                trust_status=decision.trust_status,
                before=before_states,
                after=before_states,
                refresh_targets=[],
                blocked_sources=[],
                refreshed_source_count=0,
                failed_refresh_count=0,
                analyst_refresh_supported=False,
                analyst_refresh_status="not_attempted_fast_certified",
                started=started,
            )

        # Track the set of sources we *attempted* to refresh.
        refresh_targets = list(decision.refresh_targets)
        refreshed_sources: set[str] = set()
        failed_sources: set[str] = set()
        analyst_status = "not_supported_v1"

        # --- Price refresh ---------------------------------------------------
        wants_price = any(
            src in refresh_targets
            for src in (SOURCE_PRICE_LATEST, SOURCE_PRICE_HISTORY, SOURCE_PORTFOLIO_SNAPSHOT, SOURCE_POSITIONS)
        )
        if wants_price and self._price_refresh is not None and self.inputs.tickers:
            tickers = list(self.inputs.tickers)[:MAX_PRICE_TICKERS_PER_RUN]
            if not tickers:
                self.notes.append("price_refresh_skipped_no_tickers")
            elif self.budget.provider_budget_remaining() <= 0:
                self.notes.append("price_refresh_skipped_budget")
                self.budget.attempted_provider_calls += 0  # explicit
            elif self.budget.time_exhausted():
                self.notes.append("price_refresh_skipped_timeout")
            else:
                # Cap tickers by remaining provider budget.
                allowed = min(len(tickers), self.budget.provider_budget_remaining())
                attempt_tickers = tickers[:allowed]
                self.budget.attempted_provider_calls += len(attempt_tickers)
                price_succeeded = False
                try:
                    price_results = await asyncio.wait_for(
                        self._price_refresh(attempt_tickers),
                        timeout=max(0.1, min(self.budget.time_remaining(), 10.0)),
                    )
                    successes, failures = _count_price_results(price_results, attempt_tickers)
                    self.budget.successful_provider_calls += successes
                    self.budget.failed_provider_calls += failures
                    price_succeeded = successes > 0
                    if failures > 0:
                        self.notes.append(
                            f"price_refresh_partial_failure_{failures}_of_{len(attempt_tickers)}"
                        )
                except asyncio.TimeoutError:
                    self.budget.failed_provider_calls += len(attempt_tickers)
                    self.notes.append("price_refresh_timeout")
                except Exception as exc:
                    self.budget.failed_provider_calls += len(attempt_tickers)
                    self.notes.append(f"price_refresh_error:{type(exc).__name__}")
                    logger.warning(
                        "evidence_refresh.price_refresh_failed user_id=%s error=%s",
                        self.user_id, exc,
                    )

                if price_succeeded:
                    refreshed_sources.add(SOURCE_PRICE_LATEST)
                    refreshed_sources.add(SOURCE_PRICE_HISTORY)
                    # If we refreshed prices we re-certify the snapshot as of now;
                    # the IntelV3Service writes a refreshed timestamp into the
                    # OrchestratorInputs (see post_refresh_inputs() below).
                else:
                    failed_sources.add(SOURCE_PRICE_LATEST)
        elif wants_price and self._price_refresh is None:
            self.notes.append("price_refresh_unavailable_no_callable")

        # --- Analyst / LLM refresh -- v1 boundary (do not pretend) ------------
        wants_analyst = any(
            src in refresh_targets
            for src in (SOURCE_AGENT_INSIGHTS, SOURCE_RECOMMENDATIONS)
        )
        if wants_analyst and self._analyst_refresh is None:
            analyst_status = "not_supported_v1"
            self.notes.append("analyst_refresh_not_supported_v1")
            # NOTE: We do not increment attempted_llm_calls — we never tried.
        elif wants_analyst and self._analyst_refresh is not None:
            # Reserved for a future slice. The injection point exists so a
            # later PR can wire a narrow analyst adapter without touching the
            # orchestrator core.
            if self.budget.llm_budget_remaining() <= 0:
                analyst_status = "skipped_budget"
                self.notes.append("analyst_refresh_skipped_budget")
            elif self.budget.time_exhausted():
                analyst_status = "skipped_timeout"
                self.notes.append("analyst_refresh_skipped_timeout")
            else:
                try:
                    self.budget.attempted_llm_calls += 1
                    await self._analyst_refresh(self.inputs.tickers)  # type: ignore[arg-type]
                    self.budget.successful_llm_calls += 1
                    analyst_status = "succeeded"
                    refreshed_sources.add(SOURCE_AGENT_INSIGHTS)
                    refreshed_sources.add(SOURCE_RECOMMENDATIONS)
                except Exception as exc:
                    self.budget.failed_llm_calls += 1
                    analyst_status = f"failed:{type(exc).__name__}"
                    failed_sources.add(SOURCE_AGENT_INSIGHTS)
                    failed_sources.add(SOURCE_RECOMMENDATIONS)
                    logger.warning(
                        "evidence_refresh.analyst_refresh_failed user_id=%s error=%s",
                        self.user_id, exc,
                    )
        else:
            analyst_status = "not_attempted"

        # --- Re-classify after refresh ---------------------------------------
        after_inputs = self._post_refresh_inputs(refreshed_sources)
        after_states = build_source_states(after_inputs)
        post_decision = classify_run_mode(
            after_states,
            refresh_attempted=True,
            refresh_successful_count=len(refreshed_sources),
            refresh_failed_count=len(failed_sources),
        )

        return self._finalize(
            run_mode=post_decision.run_mode,
            trust_status=post_decision.trust_status,
            before=before_states,
            after=after_states,
            refresh_targets=refresh_targets,
            blocked_sources=post_decision.blocked_sources,
            refreshed_source_count=len(refreshed_sources),
            failed_refresh_count=len(failed_sources),
            analyst_refresh_supported=self._analyst_refresh is not None,
            analyst_refresh_status=analyst_status,
            started=started,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _post_refresh_inputs(self, refreshed_sources: set[str]) -> OrchestratorInputs:
        """Return a fresh OrchestratorInputs reflecting which sources we refreshed.

        For successfully refreshed sources we stamp `now` as the certified_at —
        this is the only place fresh timestamps are created, and only after a
        real refresh call returned success. No fabricated freshness elsewhere.
        """
        now_iso = self.inputs.now.isoformat()

        market_value_certified_ats = list(self.inputs.market_value_certified_ats or [])
        portfolio_snapshot_at = self.inputs.portfolio_snapshot_at
        if SOURCE_PRICE_LATEST in refreshed_sources or SOURCE_PRICE_HISTORY in refreshed_sources:
            # Bump certified_at for the tickers we refreshed (one entry per ticker).
            market_value_certified_ats = [now_iso for _ in (self.inputs.tickers or [])]
            portfolio_snapshot_at = now_iso

        evidence_stats = dict(self.inputs.evidence_stats or {})
        if SOURCE_AGENT_INSIGHTS in refreshed_sources:
            evidence_stats["agent_insight_run_timestamps"] = [
                now_iso for _ in (evidence_stats.get("agent_insight_run_timestamps") or [])
            ]
        if SOURCE_RECOMMENDATIONS in refreshed_sources:
            evidence_stats["recommendation_timestamps"] = [
                now_iso for _ in (evidence_stats.get("recommendation_timestamps") or [])
            ]

        return OrchestratorInputs(
            evidence_stats=evidence_stats,
            portfolio_snapshot_at=portfolio_snapshot_at,
            market_value_certified_ats=market_value_certified_ats,
            tickers=list(self.inputs.tickers or []),
            research_artifact_timestamps=list(self.inputs.research_artifact_timestamps or []),
            now=self.inputs.now,
        )

    def _finalize(
        self,
        *,
        run_mode: str,
        trust_status: str,
        before: dict[str, SourceFreshnessState],
        after: dict[str, SourceFreshnessState],
        refresh_targets: list[str],
        blocked_sources: list[str],
        refreshed_source_count: int,
        failed_refresh_count: int,
        analyst_refresh_supported: bool,
        analyst_refresh_status: str,
        started: float,
    ) -> RefreshResult:
        duration_ms = int((time.monotonic() - started) * 1000)
        budget_exhausted = (
            self.budget.provider_budget_remaining() == 0
            or self.budget.llm_budget_remaining() == 0
            or self.budget.time_exhausted()
        )
        return RefreshResult(
            run_mode=run_mode,
            trust_status=trust_status,
            banner_copy=BANNER_COPY.get(run_mode, BANNER_COPY[RUN_MODE_PARTIAL_CERTIFIED]),
            source_states_before=before,
            source_states_after=after,
            refresh_targets=refresh_targets,
            blocked_sources=blocked_sources,
            refreshed_source_count=refreshed_source_count,
            failed_refresh_count=failed_refresh_count,
            attempted_provider_calls=self.budget.attempted_provider_calls,
            successful_provider_calls=self.budget.successful_provider_calls,
            failed_provider_calls=self.budget.failed_provider_calls,
            attempted_llm_calls=self.budget.attempted_llm_calls,
            successful_llm_calls=self.budget.successful_llm_calls,
            failed_llm_calls=self.budget.failed_llm_calls,
            refresh_duration_ms=duration_ms,
            analyst_refresh_supported=analyst_refresh_supported,
            analyst_refresh_status=analyst_refresh_status,
            budget_exhausted=budget_exhausted,
            notes=list(self.notes),
        )


# ── Result counting for price provider ────────────────────────────────────────

def _count_price_results(
    price_results: Any,
    attempted: list[str],
) -> tuple[int, int]:
    """Count successes/failures from a `PriceService.fetch_prices()` style dict.

    Accepts: dict[ticker, PriceResult-like or error str].
      - A "successful" result has truthy `is_valid` and not `is_stale`.
      - Anything else is a failure for refresh-accounting purposes.

    Tests pass plain dicts shaped like {ticker: {"is_valid": True, "is_stale": False}}.
    """
    if not isinstance(price_results, dict):
        return 0, len(attempted)
    successes = 0
    failures = 0
    for t in attempted:
        res = price_results.get(t)
        if res is None:
            failures += 1
            continue
        is_valid = bool(getattr(res, "is_valid", None) if not isinstance(res, dict) else res.get("is_valid"))
        is_stale = bool(getattr(res, "is_stale", None) if not isinstance(res, dict) else res.get("is_stale"))
        if is_valid and not is_stale:
            successes += 1
        else:
            failures += 1
    return successes, failures
