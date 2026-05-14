"""Certified Intel Run Contract — Stage 3.3 all-or-nothing validation.

Validates that the current DB state satisfies every holding's evidence
requirement before a snapshot can be marked ``worker_certified``.

A snapshot is certified only when ALL of the following hold for EVERY
active position ticker:

  1. An active recommendation exists with a non-null ``agent_run_id``.
  2. The recommendation's ``agent_run_id`` matches an ``agent_insights`` row
     for the same ticker and user (``run_id == agent_run_id``).
  3. The matching agent_run (via ``agent_runs.id``) has ``status=completed``.
  4. ``analyst_verdict`` on the matching insight contains non-empty,
     non-template values for ``primary_driver``, ``action_reason``,
     ``risk_flag``, and ``conviction_level``.
  5. ``primary_driver`` is not a ticker-prefix-only template string
     (i.e. does not start with just the ticker followed by a short clause).
  6. Recommendation timestamp is within the fresh SLA (24 h).
  7. Agent insight timestamp is within the fresh SLA (48 h).

This is a pure async read — no writes, no LLM calls, no side effects.
Never raises into its caller; DB failures degrade to honest failure rows.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# SLA windows (hours) — mirror evidence_freshness_contract_v1.py SOURCE_SLAS
RECOMMENDATION_FRESH_HOURS = 24.0
AGENT_INSIGHT_FRESH_HOURS = 48.0


# ── Per-ticker failure reasons ────────────────────────────────────────────────

FAIL_NO_ACTIVE_RECOMMENDATION = "no_active_recommendation"
FAIL_MISSING_AGENT_RUN_ID = "recommendation_missing_agent_run_id"
FAIL_NO_MATCHING_AGENT_INSIGHT = "no_matching_agent_insight_for_run_id"
FAIL_AGENT_RUN_MISSING = "agent_run_missing"
FAIL_AGENT_RUN_NOT_COMPLETED = "agent_run_not_completed"
FAIL_MISSING_PRIMARY_DRIVER = "missing_primary_driver"
FAIL_MISSING_ACTION_REASON = "missing_action_reason"
FAIL_MISSING_RISK_FLAG = "missing_risk_flag"
FAIL_MISSING_CONVICTION = "missing_conviction_level"
FAIL_TEMPLATE_PRIMARY_DRIVER = "template_primary_driver"
FAIL_STALE_RECOMMENDATION = "stale_recommendation"
FAIL_STALE_AGENT_INSIGHT = "stale_agent_insight"


@dataclass
class TickerCertificationResult:
    ticker: str
    certified: bool
    failure_reason: Optional[str] = None
    agent_run_id: Optional[str] = None
    recommendation_created_at: Optional[str] = None
    agent_insight_created_at: Optional[str] = None
    agent_run_status: Optional[str] = None


@dataclass
class CertifiedIntelRunContractResult:
    """Full outcome of the all-or-nothing per-holding contract check."""
    certified: bool
    total_holding_count: int
    certified_holding_count: int
    failed_holding_count: int
    failed_tickers: list[str]
    failed_tickers_with_reasons: list[dict[str, str]]
    missing_recommendation_count: int
    missing_matching_agent_insight_count: int
    stale_evidence_count: int
    missing_primary_driver_count: int
    missing_action_reason_count: int
    missing_risk_flag_count: int
    template_rationale_count: int
    latest_agent_run_at: Optional[str]
    latest_recommendation_at: Optional[str]
    agent_run_ids_used: list[str]
    certification_errors: list[str]
    per_ticker: list[TickerCertificationResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "certified": self.certified,
            "total_holding_count": self.total_holding_count,
            "certified_holding_count": self.certified_holding_count,
            "failed_holding_count": self.failed_holding_count,
            "failed_tickers": self.failed_tickers,
            "failed_tickers_with_reasons": self.failed_tickers_with_reasons,
            "missing_recommendation_count": self.missing_recommendation_count,
            "missing_matching_agent_insight_count": self.missing_matching_agent_insight_count,
            "stale_evidence_count": self.stale_evidence_count,
            "missing_primary_driver_count": self.missing_primary_driver_count,
            "missing_action_reason_count": self.missing_action_reason_count,
            "missing_risk_flag_count": self.missing_risk_flag_count,
            "template_rationale_count": self.template_rationale_count,
            "latest_agent_run_at": self.latest_agent_run_at,
            "latest_recommendation_at": self.latest_recommendation_at,
            "agent_run_ids_used": self.agent_run_ids_used,
            "certification_errors": self.certification_errors,
        }


def _hours_ago(iso: Any, now: datetime) -> Optional[float]:
    if not iso or not isinstance(iso, str):
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 3600.0


def _is_template_primary_driver(primary_driver: str, ticker: str) -> bool:
    """Return True when primary_driver is a ticker-prefix-only template string.

    A primary_driver of the form "<TICKER> <short clause>" with fewer than
    20 characters beyond the ticker prefix is treated as a template because
    the analyst did not supply meaningful structured rationale — it matches
    the lossy writeback path from pre-Stage-3.2c that re-derived primary_driver
    from the first sentence of investment_thesis with a ticker prefix intact.
    """
    if not primary_driver or not ticker:
        return False
    pd_lower = primary_driver.strip().lower()
    tk_lower = ticker.strip().lower()
    if pd_lower.startswith(tk_lower):
        remainder = pd_lower[len(tk_lower):].strip()
        # Fewer than 20 chars of substantive content after the ticker → template
        if len(remainder) < 20:
            return True
    return False


async def check_certified_intel_run_contract(
    *,
    user_id: UUID,
    client: Any,
    now: Optional[datetime] = None,
) -> CertifiedIntelRunContractResult:
    """Run the all-or-nothing certified intel run contract for a user.

    Reads ``positions``, ``recommendations``, ``agent_insights``, and
    ``agent_runs`` for the user. Never writes. Never raises into the caller —
    DB failures degrade to honest failure rows in the result.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    user_str = str(user_id)
    certification_errors: list[str] = []

    # ── Step 1: fetch active positions ────────────────────────────────────────
    active_tickers: list[str] = []
    try:
        res = await asyncio.to_thread(
            lambda: client.table("positions")
            .select("ticker")
            .eq("user_id", user_str)
            .execute()
        )
        for row in (res.data or []):
            t = row.get("ticker") if isinstance(row, dict) else None
            if t:
                active_tickers.append(str(t))
    except Exception as exc:
        certification_errors.append(f"positions_fetch_failed:{type(exc).__name__}")
        logger.warning(
            "certified_intel_run_contract.positions_fetch_failed user_id=%s err=%s",
            user_id, exc,
        )

    total_holding_count = len(active_tickers)
    if total_holding_count == 0:
        return CertifiedIntelRunContractResult(
            certified=False,
            total_holding_count=0,
            certified_holding_count=0,
            failed_holding_count=0,
            failed_tickers=[],
            failed_tickers_with_reasons=[],
            missing_recommendation_count=0,
            missing_matching_agent_insight_count=0,
            stale_evidence_count=0,
            missing_primary_driver_count=0,
            missing_action_reason_count=0,
            missing_risk_flag_count=0,
            template_rationale_count=0,
            latest_agent_run_at=None,
            latest_recommendation_at=None,
            agent_run_ids_used=[],
            certification_errors=certification_errors + ["no_active_positions"],
        )

    # ── Step 2: fetch active recommendations ─────────────────────────────────
    rec_by_ticker: dict[str, dict[str, Any]] = {}
    try:
        res = await asyncio.to_thread(
            lambda: client.table("recommendations")
            .select("ticker,action,agent_run_id,created_at,is_active")
            .eq("user_id", user_str)
            .eq("is_active", True)
            .execute()
        )
        for row in (res.data or []):
            if not isinstance(row, dict):
                continue
            t = row.get("ticker")
            if not t:
                continue
            existing = rec_by_ticker.get(t)
            if existing is None or (row.get("created_at") or "") > (existing.get("created_at") or ""):
                rec_by_ticker[t] = row
    except Exception as exc:
        certification_errors.append(f"recommendations_fetch_failed:{type(exc).__name__}")
        logger.warning(
            "certified_intel_run_contract.recommendations_fetch_failed user_id=%s err=%s",
            user_id, exc,
        )

    # ── Step 3: gather all agent_run_ids from recommendations ─────────────────
    agent_run_ids_set: set[str] = set()
    for rec in rec_by_ticker.values():
        rid = rec.get("agent_run_id")
        if rid:
            agent_run_ids_set.add(str(rid))

    # ── Step 4: fetch agent_insights by run_id ────────────────────────────────
    insight_by_ticker_run: dict[tuple[str, str], dict[str, Any]] = {}
    if agent_run_ids_set:
        try:
            res = await asyncio.to_thread(
                lambda: client.table("agent_insights")
                .select("ticker,run_id,analyst_verdict,created_at")
                .eq("user_id", user_str)
                .in_("run_id", list(agent_run_ids_set))
                .execute()
            )
            for row in (res.data or []):
                if not isinstance(row, dict):
                    continue
                tk = row.get("ticker")
                rid = row.get("run_id")
                if tk and rid:
                    key = (str(tk), str(rid))
                    existing = insight_by_ticker_run.get(key)
                    if existing is None or (row.get("created_at") or "") > (existing.get("created_at") or ""):
                        insight_by_ticker_run[key] = row
        except Exception as exc:
            certification_errors.append(f"agent_insights_fetch_failed:{type(exc).__name__}")
            logger.warning(
                "certified_intel_run_contract.agent_insights_fetch_failed user_id=%s err=%s",
                user_id, exc,
            )

    # ── Step 5: fetch agent_run statuses ──────────────────────────────────────
    agent_run_status_by_id: dict[str, str] = {}
    agent_run_finished_at_by_id: dict[str, str] = {}
    if agent_run_ids_set:
        try:
            res = await asyncio.to_thread(
                lambda: client.table("agent_runs")
                .select("id,status,finished_at")
                .eq("user_id", user_str)
                .in_("id", list(agent_run_ids_set))
                .execute()
            )
            for row in (res.data or []):
                if not isinstance(row, dict):
                    continue
                rid = row.get("id")
                if rid:
                    agent_run_status_by_id[str(rid)] = str(row.get("status") or "unknown")
                    fa = row.get("finished_at")
                    if fa:
                        agent_run_finished_at_by_id[str(rid)] = str(fa)
        except Exception as exc:
            certification_errors.append(f"agent_runs_fetch_failed:{type(exc).__name__}")
            logger.warning(
                "certified_intel_run_contract.agent_runs_fetch_failed user_id=%s err=%s",
                user_id, exc,
            )

    # ── Step 6: per-ticker validation ─────────────────────────────────────────
    per_ticker_results: list[TickerCertificationResult] = []
    certified_tickers: list[str] = []
    failed_tickers: list[str] = []
    failed_tickers_with_reasons: list[dict[str, str]] = []

    missing_recommendation_count = 0
    missing_matching_agent_insight_count = 0
    stale_evidence_count = 0
    missing_primary_driver_count = 0
    missing_action_reason_count = 0
    missing_risk_flag_count = 0
    template_rationale_count = 0

    latest_recommendation_at: Optional[str] = None
    latest_agent_run_at: Optional[str] = None

    for ticker in active_tickers:
        rec = rec_by_ticker.get(ticker)

        # Check 1: active recommendation exists
        if rec is None:
            missing_recommendation_count += 1
            per_ticker_results.append(TickerCertificationResult(
                ticker=ticker, certified=False,
                failure_reason=FAIL_NO_ACTIVE_RECOMMENDATION,
            ))
            failed_tickers.append(ticker)
            failed_tickers_with_reasons.append({"ticker": ticker, "reason": FAIL_NO_ACTIVE_RECOMMENDATION})
            continue

        rec_created_at = rec.get("created_at")
        if rec_created_at and (latest_recommendation_at is None or rec_created_at > latest_recommendation_at):
            latest_recommendation_at = rec_created_at

        # Check 2: recommendation has agent_run_id
        agent_run_id = rec.get("agent_run_id")
        if not agent_run_id:
            missing_recommendation_count += 1
            per_ticker_results.append(TickerCertificationResult(
                ticker=ticker, certified=False,
                failure_reason=FAIL_MISSING_AGENT_RUN_ID,
                recommendation_created_at=rec_created_at,
            ))
            failed_tickers.append(ticker)
            failed_tickers_with_reasons.append({"ticker": ticker, "reason": FAIL_MISSING_AGENT_RUN_ID})
            continue

        agent_run_id_str = str(agent_run_id)

        # Check 3: matching agent_insight exists
        insight = insight_by_ticker_run.get((ticker, agent_run_id_str))
        if insight is None:
            # Try case-insensitive lookup
            for (tk, rid), row in insight_by_ticker_run.items():
                if tk.upper() == ticker.upper() and rid == agent_run_id_str:
                    insight = row
                    break

        if insight is None:
            missing_matching_agent_insight_count += 1
            per_ticker_results.append(TickerCertificationResult(
                ticker=ticker, certified=False,
                failure_reason=FAIL_NO_MATCHING_AGENT_INSIGHT,
                agent_run_id=agent_run_id_str,
                recommendation_created_at=rec_created_at,
            ))
            failed_tickers.append(ticker)
            failed_tickers_with_reasons.append({
                "ticker": ticker,
                "reason": FAIL_NO_MATCHING_AGENT_INSIGHT,
                "agent_run_id": agent_run_id_str,
            })
            continue

        insight_created_at = insight.get("created_at")
        if insight_created_at and (latest_agent_run_at is None or insight_created_at > latest_agent_run_at):
            latest_agent_run_at = insight_created_at

        # Check 4: agent_run row exists AND status == "completed"
        if agent_run_id_str not in agent_run_status_by_id:
            # No matching row in agent_runs — fail with explicit missing reason
            per_ticker_results.append(TickerCertificationResult(
                ticker=ticker, certified=False,
                failure_reason=FAIL_AGENT_RUN_MISSING,
                agent_run_id=agent_run_id_str,
                recommendation_created_at=rec_created_at,
                agent_insight_created_at=insight_created_at,
                agent_run_status=None,
            ))
            failed_tickers.append(ticker)
            failed_tickers_with_reasons.append({
                "ticker": ticker,
                "reason": FAIL_AGENT_RUN_MISSING,
                "agent_run_id": agent_run_id_str,
            })
            continue

        agent_run_status = agent_run_status_by_id[agent_run_id_str]
        if agent_run_status != "completed":
            per_ticker_results.append(TickerCertificationResult(
                ticker=ticker, certified=False,
                failure_reason=FAIL_AGENT_RUN_NOT_COMPLETED,
                agent_run_id=agent_run_id_str,
                recommendation_created_at=rec_created_at,
                agent_insight_created_at=insight_created_at,
                agent_run_status=agent_run_status,
            ))
            failed_tickers.append(ticker)
            failed_tickers_with_reasons.append({
                "ticker": ticker,
                "reason": FAIL_AGENT_RUN_NOT_COMPLETED,
                "agent_run_status": agent_run_status,
            })
            continue

        # Check 5: analyst_verdict fields
        av = insight.get("analyst_verdict") or {}
        if not isinstance(av, dict):
            av = {}

        primary_driver = av.get("primary_driver") or ""
        action_reason = av.get("action_reason") or av.get("do") or ""
        risk_flag = av.get("risk_flag")  # can be "" (no risk) — we allow empty string
        conviction_level = av.get("conviction_level") or ""

        verdict_fail: Optional[str] = None

        if not primary_driver:
            missing_primary_driver_count += 1
            verdict_fail = FAIL_MISSING_PRIMARY_DRIVER
        elif _is_template_primary_driver(primary_driver, ticker):
            template_rationale_count += 1
            verdict_fail = FAIL_TEMPLATE_PRIMARY_DRIVER
        elif not action_reason:
            missing_action_reason_count += 1
            verdict_fail = FAIL_MISSING_ACTION_REASON
        elif risk_flag is None:
            missing_risk_flag_count += 1
            verdict_fail = FAIL_MISSING_RISK_FLAG
        elif not conviction_level:
            verdict_fail = FAIL_MISSING_CONVICTION

        if verdict_fail:
            per_ticker_results.append(TickerCertificationResult(
                ticker=ticker, certified=False,
                failure_reason=verdict_fail,
                agent_run_id=agent_run_id_str,
                recommendation_created_at=rec_created_at,
                agent_insight_created_at=insight_created_at,
                agent_run_status=agent_run_status,
            ))
            failed_tickers.append(ticker)
            failed_tickers_with_reasons.append({
                "ticker": ticker,
                "reason": verdict_fail,
            })
            continue

        # Check 6: freshness SLA
        rec_age = _hours_ago(rec_created_at, now)
        insight_age = _hours_ago(insight_created_at, now)
        stale_fail: Optional[str] = None

        if rec_age is not None and rec_age > RECOMMENDATION_FRESH_HOURS:
            stale_evidence_count += 1
            stale_fail = FAIL_STALE_RECOMMENDATION
        elif insight_age is not None and insight_age > AGENT_INSIGHT_FRESH_HOURS:
            stale_evidence_count += 1
            stale_fail = FAIL_STALE_AGENT_INSIGHT

        if stale_fail:
            per_ticker_results.append(TickerCertificationResult(
                ticker=ticker, certified=False,
                failure_reason=stale_fail,
                agent_run_id=agent_run_id_str,
                recommendation_created_at=rec_created_at,
                agent_insight_created_at=insight_created_at,
                agent_run_status=agent_run_status,
            ))
            failed_tickers.append(ticker)
            failed_tickers_with_reasons.append({
                "ticker": ticker,
                "reason": stale_fail,
                "age_hours": str(round(rec_age or insight_age or 0, 1)),
            })
            continue

        # All checks passed for this ticker
        certified_tickers.append(ticker)
        per_ticker_results.append(TickerCertificationResult(
            ticker=ticker,
            certified=True,
            agent_run_id=agent_run_id_str,
            recommendation_created_at=rec_created_at,
            agent_insight_created_at=insight_created_at,
            agent_run_status=agent_run_status,
        ))

    certified_holding_count = len(certified_tickers)
    failed_holding_count = len(failed_tickers)
    all_certified = (
        total_holding_count > 0
        and certified_holding_count == total_holding_count
        and failed_holding_count == 0
        and not certification_errors
    )

    # Collect unique agent_run_ids used by certified tickers
    agent_run_ids_used = sorted(
        {r.agent_run_id for r in per_ticker_results if r.certified and r.agent_run_id}
    )

    result = CertifiedIntelRunContractResult(
        certified=all_certified,
        total_holding_count=total_holding_count,
        certified_holding_count=certified_holding_count,
        failed_holding_count=failed_holding_count,
        failed_tickers=failed_tickers,
        failed_tickers_with_reasons=failed_tickers_with_reasons,
        missing_recommendation_count=missing_recommendation_count,
        missing_matching_agent_insight_count=missing_matching_agent_insight_count,
        stale_evidence_count=stale_evidence_count,
        missing_primary_driver_count=missing_primary_driver_count,
        missing_action_reason_count=missing_action_reason_count,
        missing_risk_flag_count=missing_risk_flag_count,
        template_rationale_count=template_rationale_count,
        latest_agent_run_at=latest_agent_run_at,
        latest_recommendation_at=latest_recommendation_at,
        agent_run_ids_used=agent_run_ids_used,
        certification_errors=certification_errors,
        per_ticker=per_ticker_results,
    )

    logger.info(
        "intel_v3_certified_contract_summary user_id=%s "
        "certified=%s total=%d certified=%d failed=%d "
        "missing_rec=%d missing_insight=%d stale=%d "
        "missing_driver=%d missing_action_reason=%d missing_risk_flag=%d template=%d "
        "latest_agent_run_at=%s latest_recommendation_at=%s "
        "agent_run_ids=%s errors=%s",
        user_id,
        all_certified,
        total_holding_count,
        certified_holding_count,
        failed_holding_count,
        missing_recommendation_count,
        missing_matching_agent_insight_count,
        stale_evidence_count,
        missing_primary_driver_count,
        missing_action_reason_count,
        missing_risk_flag_count,
        template_rationale_count,
        latest_agent_run_at,
        latest_recommendation_at,
        ",".join(agent_run_ids_used) if agent_run_ids_used else "none",
        ",".join(certification_errors) if certification_errors else "none",
    )

    return result
