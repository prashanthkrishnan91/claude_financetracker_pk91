"""Distributed Run Intel — control plane (create) + status plane (observe).

``create_distributed_session`` is everything ``POST /intel/v3/run`` does:
authenticate (router), adopt-or-create ONE durable session, freeze the
portfolio scope, seed the initial task graph, activate the worker supervisor,
return fast. It performs ZERO provider fetches, ZERO LLM calls, ZERO decision
policy, ZERO snapshot writes — those all live in worker task executors.

``get_session_status`` is the lightweight read-only status plane the frontend
polls. Polling observes work; it never performs or advances work.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from ....financial_truth_baseline_v1 import (
    FinancialTruthReadError,
    run_financial_truth_baseline_strict,
)
from ....portfolio_service import PortfolioService
from ..intel_run_session_store_v1 import get_session
from . import run_task_store_v1 as store
from .task_contracts_v1 import (
    ALL_TICKER_STATES,
    ASSET_CRYPTO,
    ASSET_EQUITY,
    ASSET_ETF,
    SESSION_ACTIVE_STATES,
    SESSION_COMPLETED,
    SESSION_COMPLETED_WITH_GAPS,
    SESSION_CREATED,
    SESSION_FAILED,
    SESSION_RUNNING,
    SESSION_TERMINAL_STATES,
    STAGE_ANALYSIS,
    STAGE_COLLECTING,
    STAGE_DECIDING,
    STAGE_DONE,
    STAGE_PREPARING,
    STAGE_PUBLISHING,
    TASK_COLLECT_MACRO_CONTEXT,
    TASK_COLLECT_PORTFOLIO_CONTEXT,
    TASK_COLLECT_EVIDENCE_LANE,
    TICKER_ANALYSIS_COMPLETE,
    TICKER_DECIDED,
    TICKER_DECISION_READY,
    TICKER_EVIDENCE_READY,
    TICKER_FAILED,
    TICKER_NO_CALL,
    WORKFLOW_VERSION_DISTRIBUTED,
    asset_type_for_category,
    compute_ticker_priority,
    lanes_for_asset,
    required_lanes_for_asset,
)

logger = logging.getLogger(__name__)

SESSIONS_TABLE = "intel_run_sessions"


class SessionOwnershipError(Exception):
    """The session id exists but belongs to another user."""


class SessionScopeError(Exception):
    """The portfolio scope could not be loaded/frozen (honest failure)."""


def _rows(res: Any) -> list[dict[str, Any]]:
    data = getattr(res, "data", None)
    return data if isinstance(data, list) else []


def _now(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# ── Scope freeze (DB reads only) ─────────────────────────────────────────────

def _latest_price_map(price_rows: list[dict[str, Any]]) -> dict[str, float]:
    """First occurrence per ticker in ``price_rows`` — a pure projection of
    the EXACT rows the strict preflight already read (ordered desc by
    ``price_date`` by that same query), never a second price_history read."""
    prices: dict[str, float] = {}
    for row in price_rows:
        ticker = str(row.get("ticker") or "").upper()
        close = _safe_float(row.get("close_price"))
        if ticker and close is not None and ticker not in prices:
            prices[ticker] = close
    return prices


def _load_prior_actions(client: Any, user_id: str) -> dict[str, str]:
    # NOTE: the recommendations column is ``action`` (001_initial_schema.sql)
    # — selecting a nonexistent ``suggested_action`` column 400s in
    # production and silently collapsed prior-action priority (adversarial
    # audit defect D1).
    try:
        res = (
            client.table("recommendations")
            .select("ticker,action,is_active,created_at")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception:
        return {}
    actions: dict[str, str] = {}
    for row in _rows(res):
        ticker = str(row.get("ticker") or "").upper()
        action = str(row.get("action") or "").upper()
        if ticker and action and ticker not in actions:
            actions[ticker] = action
    return actions


def build_frozen_scope_rows(
    positions: list[dict[str, Any]],
    prices: dict[str, float],
    prior_actions: dict[str, str],
) -> list[dict[str, Any]]:
    """Pure: positions + stored prices + prior actions → frozen ticker rows."""
    values: dict[str, Optional[float]] = {}
    cost_bases: dict[str, float] = {}
    for pos in positions:
        ticker = str(pos.get("ticker") or "").strip()
        key = ticker.upper()
        shares = (_safe_float(pos.get("shares")) or 0.0) + (
            _safe_float(pos.get("drip_shares")) or 0.0
        )
        cost = (
            (_safe_float(pos.get("shares")) or 0.0)
            * (_safe_float(pos.get("avg_cost")) or 0.0)
            + (_safe_float(pos.get("drip_cost")) or 0.0)
        )
        cost_bases[key] = cost
        close = prices.get(key)
        values[key] = shares * close if close is not None else None

    total_value = sum(v for v in values.values() if v is not None)
    total_cost = sum(cost_bases.values())

    rows: list[dict[str, Any]] = []
    for pos in sorted(positions, key=lambda p: str(p.get("ticker") or "").upper()):
        ticker = str(pos.get("ticker") or "").strip()
        key = ticker.upper()
        asset_type = asset_type_for_category(pos.get("category"))
        shares = (_safe_float(pos.get("shares")) or 0.0) + (
            _safe_float(pos.get("drip_shares")) or 0.0
        )
        market_value = values.get(key)
        cost_basis = cost_bases.get(key, 0.0)
        if market_value is not None and total_value > 0:
            weight = 100.0 * market_value / total_value
        elif total_cost > 0:
            weight = 100.0 * cost_basis / total_cost
        else:
            weight = None
        gain_pct = None
        if market_value is not None and cost_basis > 0:
            gain_pct = 100.0 * (market_value - cost_basis) / cost_basis
        prior_action = prior_actions.get(key)
        rows.append({
            "ticker": ticker,
            "asset_type": asset_type,
            "quantity": shares,
            "market_value": market_value,
            "portfolio_weight_pct": weight,
            "cost_basis": cost_basis,
            "unrealized_gain_pct": gain_pct,
            "tax_summary": {
                "lt_eligible": bool(pos.get("lt_eligible")),
                "lt_date": str(pos.get("lt_date")) if pos.get("lt_date") else None,
            },
            "prior_action": prior_action,
            "priority": compute_ticker_priority(
                has_current_recommendation=prior_action is not None,
                evidence_available=True,
                weight_pct=weight,
            ),
            "required_lanes": required_lanes_for_asset(asset_type),
        })
    return rows


# ── Portfolio financial-truth preflight (invariants #2-#4) ──────────────────

PREFLIGHT_SCHEMA_VERSION = "run_intel_preflight_v1"

# Truthful, code-specific copy (never one generic sentence for every block).
_PREFLIGHT_MESSAGES: dict[str, str] = {
    "portfolio_truth_unavailable": (
        "Run Intel did not start because the portfolio totals could not be "
        "verified. Refresh or repair the portfolio data, then try again."
    ),
    "portfolio_snapshot_stale": (
        "Run Intel did not start because the portfolio snapshot is still "
        "out of date after an automatic refresh attempt. Refresh the "
        "portfolio snapshot, then try again."
    ),
    "portfolio_reconciliation_failed": (
        "Run Intel did not start because portfolio totals do not reconcile "
        "with recorded positions. Resolve the reconciliation issue, then "
        "try again."
    ),
    "portfolio_refresh_failed": (
        "Run Intel did not start because the portfolio snapshot is stale "
        "and the automatic refresh failed. Refresh the portfolio again."
    ),
    "portfolio_scope_empty": "Add positions before running Intel.",
}


def _preflight_blocked(
    code: str, repair_action: Optional[str], *, snapshot_refreshed: bool = False,
) -> dict[str, Any]:
    return {
        "blocked": True,
        "code": code,
        "message": _PREFLIGHT_MESSAGES[code],
        "repair_action": (repair_action or "See portfolio diagnostics for the exact repair step.")[:300],
        "snapshot_refreshed": snapshot_refreshed,
    }


async def _run_truth_preflight(
    *, client: Any, user_id: str, now: datetime,
) -> dict[str, Any]:
    """Deterministic portfolio financial-truth preflight — ONE strict result
    owns both the verdict and the exact canonical open-position/price rows
    that produced it, so the frozen scope can never diverge from
    reconciliation (no time-of-check/time-of-use gap, no second query).

    Reads ``financial_truth_baseline_v1``'s strict evaluation at most twice,
    invoking the existing canonical portfolio-snapshot refresh at most once
    on staleness/unavailability. Never relaxes thresholds or duplicates
    reconciliation math. A core read failure (distinguishable from a
    legitimate empty result) fails closed rather than reporting an empty or
    healthy portfolio.
    """
    try:
        result = await run_financial_truth_baseline_strict(client, str(user_id))
    except FinancialTruthReadError as exc:
        logger.error("run_intel.preflight_read_failed user=%s tables=%s", user_id, exc.failed_tables)
        return _preflight_blocked("portfolio_truth_unavailable", None)
    except Exception as exc:
        logger.error("run_intel.preflight_read_failed user=%s err=%s", user_id, exc)
        return _preflight_blocked("portfolio_truth_unavailable", None)

    snap = result["snapshot_truth"]
    refreshed = False
    # A missing/non-finite cash_balance means invested value can't be
    # derived — treat it exactly like staleness/unavailability: one refresh
    # attempt, then fail closed if it's still unusable.
    if (
        snap.get("status") != "ok"
        or snap.get("snapshot_is_stale")
        or snap.get("latest_cash_balance") is None
    ):
        try:
            await PortfolioService(user_id=UUID(str(user_id)), client=client).create_snapshot()
            refreshed = True
        except Exception as exc:
            logger.warning("run_intel.preflight_refresh_failed user=%s err=%s", user_id, exc)
            return _preflight_blocked(
                "portfolio_refresh_failed", result["verdict"].get("next_required_fix"),
            )
        # Final strict read — its own open-position/price rows are the ONLY
        # ones the frozen scope may use; a position inserted/removed/changed
        # since the first read is reflected here, never in a stale earlier
        # read.
        try:
            result = await run_financial_truth_baseline_strict(client, str(user_id))
        except Exception as exc:
            logger.error("run_intel.preflight_reread_failed user=%s err=%s", user_id, exc)
            return _preflight_blocked(
                "portfolio_truth_unavailable", None, snapshot_refreshed=True,
            )
        snap = result["snapshot_truth"]

    verdict = result["verdict"]
    recon = result["reconciliation"]
    pos = result["position_derived_truth"]

    if snap.get("status") != "ok":
        return _preflight_blocked(
            "portfolio_truth_unavailable", verdict.get("next_required_fix"),
            snapshot_refreshed=refreshed,
        )
    if snap.get("snapshot_is_stale"):
        return _preflight_blocked(
            "portfolio_snapshot_stale", verdict.get("next_required_fix"),
            snapshot_refreshed=refreshed,
        )
    if snap.get("latest_cash_balance") is None:
        # Refreshed once already (above) — still missing/non-finite means
        # invested value can never be derived; never guess cash as zero.
        return _preflight_blocked(
            "portfolio_truth_unavailable", verdict.get("next_required_fix"),
            snapshot_refreshed=refreshed,
        )
    if not pos.get("open_position_count"):
        return _preflight_blocked(
            "portfolio_scope_empty", "Add or import at least one open position.",
            snapshot_refreshed=refreshed,
        )
    if pos.get("duplicate_active_tickers"):
        # Duplicate active rows are a financial-truth defect, not a valid
        # scope — never silently deduplicated.
        dupes = ", ".join(pos["duplicate_active_tickers"][:5])
        return _preflight_blocked(
            "portfolio_reconciliation_failed",
            f"Remove or close duplicate active positions: {dupes}",
            snapshot_refreshed=refreshed,
        )
    if not pos.get("market_value_feasible"):
        return _preflight_blocked(
            "portfolio_truth_unavailable", verdict.get("next_required_fix"),
            snapshot_refreshed=refreshed,
        )
    if recon.get("reconciliation_status") != "pass":
        return _preflight_blocked(
            "portfolio_reconciliation_failed", verdict.get("next_required_fix"),
            snapshot_refreshed=refreshed,
        )

    return {
        "blocked": False,
        "open_positions": result["_open_positions"],
        "price_rows": result["_price_rows"],
        "summary": {
            "schema_version": PREFLIGHT_SCHEMA_VERSION,
            "status": "passed",
            "checked_at": now.isoformat(),
            "snapshot_at": snap.get("latest_snapshot_at"),
            "snapshot_age_hours": snap.get("snapshot_age_hours"),
            "reconciliation_status": recon.get("reconciliation_status"),
            "snapshot_refreshed": refreshed,
        },
    }


def _preflight_not_created(session_id: str, preflight: dict[str, Any]) -> dict[str, Any]:
    code = preflight["code"]
    # portfolio_scope_empty keeps the pre-existing "no_active_holdings"
    # reason/copy for backward-compatible frontend rendering (the button
    # stays the idle "Add positions" state, not a retry-failure state).
    reason = "no_active_holdings" if code == "portfolio_scope_empty" else code
    return {
        "created": False,
        "run_session_id": session_id,
        "session_status": "not_created",
        "current_stage": None,
        "reason": reason,
        "plain_status": preflight["message"],
        "retryable": code in ("portfolio_refresh_failed", "portfolio_truth_unavailable"),
        "status": "blocked",
        "code": code,
        "message": preflight["message"],
        "repair_action": preflight["repair_action"],
        "provider_calls": 0,
        "llm_calls": 0,
    }


# ── Session creation ─────────────────────────────────────────────────────────

async def create_distributed_session(
    *,
    client: Any,
    user_id: str,
    session_id: str,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Adopt-or-create the durable session and seed its task graph.

    Returns the status payload (same shape as ``get_session_status``) plus
    ``created: bool``. Raises SessionOwnershipError / SessionScopeError for
    the router to map. Never fetches providers, never calls the LLM.
    """
    now_dt = _now(now)

    existing = await asyncio.to_thread(get_session, client, session_id)
    if existing is not None:
        if str(existing.get("user_id")) != str(user_id):
            raise SessionOwnershipError(session_id)
        # Idempotent retry of the same click (or a poll fallback): repair a
        # crashed create (zombie 'created' session with no frozen scope —
        # adversarial-review defect D2), then report state.
        await _repair_unseeded_session(
            client=client, user_id=user_id, session=existing, now=now_dt,
        )
        status = await get_session_status(
            client=client, user_id=user_id, session_id=session_id
        )
        status["created"] = False
        return status

    # One active session per user: a click while a run is active returns the
    # active session instead of creating an overlapping one.
    active = await find_active_session(client=client, user_id=user_id)
    if active is not None and str(active.get("id")) != str(session_id):
        await _repair_unseeded_session(
            client=client, user_id=user_id, session=active, now=now_dt,
        )
        status = await get_session_status(
            client=client, user_id=user_id, session_id=str(active.get("id"))
        )
        status["created"] = False
        status["adopted_active_session"] = True
        return status

    preflight = await _run_truth_preflight(client=client, user_id=user_id, now=now_dt)
    if preflight["blocked"]:
        return _preflight_not_created(session_id, preflight)

    # The frozen scope comes EXCLUSIVELY from the exact rows the passed
    # preflight already read and reconciled against — no second positions/
    # price_history query, no time-of-check/time-of-use gap.
    positions = preflight["open_positions"]
    prices = _latest_price_map(preflight["price_rows"])
    prior_actions = await asyncio.to_thread(_load_prior_actions, client, str(user_id))
    scope_rows = build_frozen_scope_rows(positions, prices, prior_actions)

    session_row = {
        "id": str(session_id),
        "user_id": str(user_id),
        "status": SESSION_CREATED,
        "workflow_version": WORKFLOW_VERSION_DISTRIBUTED,
        "current_stage": STAGE_PREPARING,
        "holdings_scope": [r["ticker"] for r in scope_rows],
        "stale_tickers": [],
        "expected_ticker_job_count": 0,
        "metrics": {"preflight": preflight["summary"]},
        "created_at": now_dt.isoformat(),
        "updated_at": now_dt.isoformat(),
    }
    try:
        await asyncio.to_thread(
            lambda: client.table(SESSIONS_TABLE).insert(session_row).execute()
        )
    except Exception as exc:
        # Duplicate-id race (same click retried) → adopt; a lost
        # active-per-user race (two tabs) → adopt the winner's session;
        # anything else is an explicit retryable failure (e.g. migration 027
        # not applied).
        raced = await asyncio.to_thread(get_session, client, session_id)
        if raced is None or str(raced.get("user_id")) != str(user_id):
            race_winner = await find_active_session(
                client=client, user_id=user_id,
            )
            if race_winner is not None:
                status = await get_session_status(
                    client=client, user_id=user_id,
                    session_id=str(race_winner.get("id")),
                )
                status["created"] = False
                status["adopted_active_session"] = True
                return status
            logger.error(
                "distributed_session.create_failed session=%s err=%s",
                session_id, exc,
            )
            raise SessionScopeError(
                "run_session_create_failed — verify migration "
                "027_intel_run_distributed_tasks.sql is applied"
            ) from exc

    # ── Fail-closed seed + verify + transition (completion item 3) ──────────
    # Seed the graph, then VERIFY the exact expected graph exists before the
    # session may report itself running. Any incomplete shape leaves the
    # session in the explicit retryable 'created' state with the error
    # recorded — a healthy running session with a silently incomplete graph
    # is impossible.
    seed_error: Optional[str] = None
    try:
        await asyncio.to_thread(
            lambda: store.insert_ticker_rows(
                client,
                run_session_id=str(session_id),
                user_id=str(user_id),
                rows=scope_rows,
                now=now_dt,
            )
        )
        await asyncio.to_thread(
            lambda: _seed_initial_tasks(
                client,
                run_session_id=str(session_id),
                user_id=str(user_id),
                scope_rows=scope_rows,
                now=now_dt,
            )
        )
    except Exception as exc:
        seed_error = f"task_graph_seed_failed:{exc}"[:400]

    missing: list[str] = []
    if seed_error is None:
        missing = await asyncio.to_thread(
            lambda: verify_seed_graph(
                client,
                run_session_id=str(session_id),
                scope_rows=scope_rows,
            )
        )
        if missing:
            seed_error = (
                "task_graph_incomplete:" + ",".join(missing[:10])
            )[:400]

    if seed_error is not None:
        await asyncio.to_thread(
            lambda: client.table(SESSIONS_TABLE)
            .update({
                "last_error": seed_error,
                "updated_at": _now().isoformat(),
            })
            .eq("id", str(session_id))
            .execute()
        )
        logger.warning(
            "distributed_session.create_incomplete session=%s err=%s — left "
            "in retryable 'created' state for repair",
            session_id, seed_error,
        )
        status = await get_session_status(
            client=client, user_id=user_id, session_id=session_id
        )
        status["created"] = False
        status["reason"] = "task_graph_incomplete_retryable"
        status["retryable"] = True
        return status

    await asyncio.to_thread(
        lambda: client.table(SESSIONS_TABLE)
        .update({
            "status": SESSION_RUNNING,
            "current_stage": STAGE_COLLECTING,
            "last_error": None,
            "updated_at": _now().isoformat(),
        })
        .eq("id", str(session_id))
        .eq("status", SESSION_CREATED)
        .execute()
    )

    logger.info(
        "distributed_session.created session=%s user=%s tickers=%d",
        session_id, user_id, len(scope_rows),
    )
    status = await get_session_status(
        client=client, user_id=user_id, session_id=session_id
    )
    status["created"] = True
    return status


async def repair_session_graph(
    *,
    client: Any,
    user_id: str,
    session: dict[str, Any],
    now: Optional[datetime] = None,
) -> bool:
    """Repair a partially-seeded 'created' session — WITHOUT ever rebuilding
    the immutable frozen scope from current portfolio state. The durable
    ``intel_run_tickers`` rows (or their absence) are the ONLY scope
    authority: this never re-runs the financial-truth baseline and never
    reloads current positions/prices/prior actions.

    - Frozen ticker rows exist: they are the sole scope authority — only
      missing seed TASKS are created for those exact rows; ``holdings_scope``
      must already match the frozen ticker set exactly, or the session fails
      honestly rather than being repaired from current portfolio data.
    - No frozen ticker rows exist: the crash happened before scope freeze
      ever persisted — fails with ``scope_freeze_incomplete_restart_required``,
      creates no tasks, makes no provider/LLM calls. The user must click Run
      Intel again for a fresh preflight and a new session.
    - Partial/contradictory frozen state (duplicate ticker rows, malformed
      asset type, missing ticker identity, a holdings_scope/ticker-row
      mismatch) fails closed the same way — never repaired.

    Returns True when the session is running with a verified-complete graph.
    Never raises into the adopt path.
    """
    if str(session.get("status") or "") != SESSION_CREATED:
        return str(session.get("status") or "") == SESSION_RUNNING
    session_id = str(session.get("id"))
    now = now or _now()

    async def _fail(reason: str) -> bool:
        await asyncio.to_thread(
            lambda: client.table(SESSIONS_TABLE)
            .update({
                "status": SESSION_FAILED,
                "last_error": reason[:400],
                "updated_at": _now().isoformat(),
            })
            .eq("id", session_id)
            .eq("status", SESSION_CREATED)
            .execute()
        )
        return False

    try:
        frozen_rows = await asyncio.to_thread(
            lambda: store.list_ticker_rows(client, run_session_id=session_id)
        )
        if not frozen_rows:
            return await _fail("scope_freeze_incomplete_restart_required")

        seen: set[str] = set()
        for row in frozen_rows:
            ticker = str(row.get("ticker") or "").strip()
            asset_type = str(row.get("asset_type") or "")
            if not ticker or asset_type not in (ASSET_EQUITY, ASSET_ETF, ASSET_CRYPTO):
                return await _fail(f"frozen_scope_malformed:{ticker or 'missing_ticker'}")
            if ticker in seen:
                return await _fail(f"frozen_scope_duplicate_ticker:{ticker}")
            seen.add(ticker)

        if {str(t) for t in (session.get("holdings_scope") or [])} != seen:
            return await _fail("frozen_scope_holdings_scope_mismatch")

        # Missing seed TASKS only — the frozen ticker rows themselves are
        # never added to, removed from, or modified.
        await asyncio.to_thread(
            lambda: _seed_initial_tasks(
                client,
                run_session_id=session_id,
                user_id=str(user_id),
                scope_rows=frozen_rows,
                now=now,
            )
        )

        missing = await asyncio.to_thread(
            lambda: verify_seed_graph(
                client, run_session_id=session_id, scope_rows=frozen_rows,
            )
        )
        if missing:
            await asyncio.to_thread(
                lambda: client.table(SESSIONS_TABLE)
                .update({
                    "last_error": (
                        "task_graph_incomplete:" + ",".join(missing[:10])
                    )[:400],
                    "updated_at": _now().isoformat(),
                })
                .eq("id", session_id)
                .execute()
            )
            return False
        await asyncio.to_thread(
            lambda: client.table(SESSIONS_TABLE)
            .update({
                "status": SESSION_RUNNING,
                "current_stage": STAGE_COLLECTING,
                "last_error": None,
                "updated_at": _now().isoformat(),
            })
            .eq("id", session_id)
            .eq("status", SESSION_CREATED)
            .execute()
        )
        logger.info(
            "distributed_session.graph_repaired session=%s tickers=%d",
            session_id, len(frozen_rows),
        )
        return True
    except Exception as exc:
        logger.warning(
            "distributed_session.repair_failed session=%s err=%s",
            session_id, exc,
        )
        try:
            await asyncio.to_thread(
                lambda: client.table(SESSIONS_TABLE)
                .update({
                    "last_error": f"repair_failed:{exc}"[:400],
                    "updated_at": _now().isoformat(),
                })
                .eq("id", session_id)
                .execute()
            )
        except Exception:
            pass
        return False


# Backwards-compatible alias used by the adopt paths.
async def _repair_unseeded_session(
    *,
    client: Any,
    user_id: str,
    session: dict[str, Any],
    now: datetime,
) -> None:
    await repair_session_graph(
        client=client, user_id=user_id, session=session, now=now,
    )


def expected_seed_task_keys(
    scope_rows: list[dict[str, Any]],
) -> set[tuple[str, str, str, str]]:
    """The EXACT expected seed graph for a frozen scope: one portfolio-context
    task, one macro-context task, and every (required + optional) collector
    lane for every frozen ticker."""
    keys: set[tuple[str, str, str, str]] = {
        store.logical_task_key(TASK_COLLECT_PORTFOLIO_CONTEXT, None, None, None),
        store.logical_task_key(TASK_COLLECT_MACRO_CONTEXT, None, None, None),
    }
    for row in scope_rows:
        ticker = str(row.get("ticker") or "")
        for lane in lanes_for_asset(str(row.get("asset_type"))):
            keys.add(
                store.logical_task_key(
                    TASK_COLLECT_EVIDENCE_LANE, lane, ticker, None,
                )
            )
    return keys


def verify_seed_graph(
    client: Any,
    *,
    run_session_id: str,
    scope_rows: list[dict[str, Any]],
) -> list[str]:
    """Read back the session graph and return every missing expected logical
    key (empty list == complete). Fail-closed: a read failure surfaces as
    everything-missing (``list_tasks`` degrades to []), so an unverifiable
    graph can never verify."""
    actual = {
        store.logical_task_key(
            str(t.get("task_type")), t.get("lane"),
            t.get("ticker"), t.get("batch_key"),
        )
        for t in store.list_tasks(client, run_session_id=run_session_id)
    }
    missing = [
        ":".join(k) or "portfolio_context"
        for k in sorted(expected_seed_task_keys(scope_rows))
        if k not in actual
    ]
    return missing


def _seed_initial_tasks(
    client: Any,
    *,
    run_session_id: str,
    user_id: str,
    scope_rows: list[dict[str, Any]],
    now: datetime,
) -> None:
    """Seed wave: session-scoped context tasks + every ticker's lane
    collectors. Downstream tasks (bundles/specialists/decisions/publish) are
    created by the scheduler as readiness becomes known."""
    store.create_task(
        client,
        run_session_id=run_session_id,
        user_id=user_id,
        task_type=TASK_COLLECT_PORTFOLIO_CONTEXT,
        priority=1,
        now=now,
    )
    store.create_task(
        client,
        run_session_id=run_session_id,
        user_id=user_id,
        task_type=TASK_COLLECT_MACRO_CONTEXT,
        priority=2,
        now=now,
    )
    for row in scope_rows:
        for lane in lanes_for_asset(str(row.get("asset_type"))):
            store.create_task(
                client,
                run_session_id=run_session_id,
                user_id=user_id,
                task_type=TASK_COLLECT_EVIDENCE_LANE,
                ticker=str(row.get("ticker")),
                lane=lane,
                asset_type=str(row.get("asset_type")),
                priority=int(row.get("priority") or 100),
                now=now,
            )


# ── Status plane ─────────────────────────────────────────────────────────────

async def find_active_session(
    *, client: Any, user_id: str
) -> Optional[dict[str, Any]]:
    """The user's latest non-terminal distributed session (or None)."""
    try:
        res = await asyncio.to_thread(
            lambda: client.table(SESSIONS_TABLE)
            .select("*")
            .eq("user_id", str(user_id))
            .eq("workflow_version", WORKFLOW_VERSION_DISTRIBUTED)
            .in_("status", list(SESSION_ACTIVE_STATES))
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = _rows(res)
        return rows[0] if rows else None
    except Exception as exc:
        logger.warning(
            "distributed_session.find_active_failed user=%s err=%s", user_id, exc,
        )
        return None


def _plain_status(
    session_status: str,
    stage: Optional[str],
    total: int,
    evidence_done: int,
    analysis_done: int,
    decided: int,
) -> str:
    if session_status == SESSION_COMPLETED:
        return "Completed — your recommendations are up to date."
    if session_status == SESSION_COMPLETED_WITH_GAPS:
        return (
            "Completed with gaps — some holdings had limited evidence this run."
        )
    if session_status == SESSION_FAILED:
        return "This run could not finish. You can start a new run."
    if stage == STAGE_PREPARING or session_status == SESSION_CREATED:
        return "Preparing portfolio…"
    if stage == STAGE_COLLECTING:
        return f"Gathering evidence — {evidence_done} of {total} holdings"
    if stage == STAGE_ANALYSIS:
        return f"Specialist analysis — {analysis_done} of {total} holdings"
    if stage == STAGE_DECIDING:
        return f"Finalizing recommendations — {decided} of {total} holdings"
    if stage == STAGE_PUBLISHING:
        return "Finalizing recommendations…"
    return "Working…"


def _evidence_summary_line(metrics: Optional[dict[str, Any]]) -> Optional[str]:
    """Compact, truthful technical-detail line built ONLY from literal
    session counters (contract §6) — never a placeholder when metrics are
    entirely unavailable. A counter's own key is absent (never 0) when
    nothing of that kind ever happened this session — e.g. an immediate
    rerun that reuses every lane never sets ``lanes_refreshed`` at all — so
    the line still renders once EITHER sibling counter in a pair exists,
    treating the other as a genuine zero rather than suppressing the line."""
    metrics = metrics or {}
    parts: list[str] = []
    if "cache_hits" in metrics or "lanes_refreshed" in metrics:
        lanes_reused = int(metrics.get("cache_hits") or 0)
        lanes_refreshed = int(metrics.get("lanes_refreshed") or 0)
        if lanes_reused or lanes_refreshed:
            parts.append(f"Evidence: {lanes_reused} lanes reused, {lanes_refreshed} refreshed.")
    if "llm_reused" in metrics or "llm_calls" in metrics:
        llm_reused = int(metrics.get("llm_reused") or 0)
        llm_calls = int(metrics.get("llm_calls") or 0)
        if llm_reused or llm_calls:
            parts.append(f"Specialist analysis: {llm_reused} reused, {llm_calls} refreshed.")
    return " ".join(parts) if parts else None


async def get_session_status(
    *,
    client: Any,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    """Read-only session status summary. Never advances or performs work."""
    session = await asyncio.to_thread(get_session, client, session_id)
    if session is None:
        return {
            "run_session_id": session_id,
            "session_status": "not_found",
            "retryable": False,
        }
    if str(session.get("user_id")) != str(user_id):
        raise SessionOwnershipError(session_id)

    ticker_rows = await asyncio.to_thread(
        lambda: store.list_ticker_rows(client, run_session_id=session_id)
    )
    task_counts = await asyncio.to_thread(
        lambda: store.count_tasks_by_state(client, run_session_id=session_id)
    )

    by_state: dict[str, int] = {state: 0 for state in ALL_TICKER_STATES}
    for row in ticker_rows:
        state = str(row.get("state") or "")
        by_state[state] = by_state.get(state, 0) + 1

    total = len(ticker_rows)
    evidence_done = sum(
        by_state.get(s, 0)
        for s in (
            TICKER_EVIDENCE_READY, TICKER_ANALYSIS_COMPLETE,
            TICKER_DECISION_READY, TICKER_DECIDED, TICKER_NO_CALL,
        )
    )
    analysis_done = sum(
        by_state.get(s, 0)
        for s in (
            TICKER_ANALYSIS_COMPLETE, TICKER_DECISION_READY,
            TICKER_DECIDED, TICKER_NO_CALL,
        )
    )
    decided = by_state.get(TICKER_DECIDED, 0)
    degraded = by_state.get(TICKER_NO_CALL, 0) + by_state.get(TICKER_FAILED, 0)

    session_status = str(session.get("status") or "")
    stage = session.get("current_stage")
    terminal = session_status in SESSION_TERMINAL_STATES

    result = {
        "run_session_id": str(session.get("id")),
        "session_status": session_status,
        "workflow_version": int(session.get("workflow_version") or 1),
        "current_stage": stage,
        "total_tickers": total,
        "evidence_complete_tickers": evidence_done,
        "analysis_complete_tickers": analysis_done,
        "decision_complete_tickers": decided
        + by_state.get(TICKER_NO_CALL, 0)
        + by_state.get(TICKER_FAILED, 0),
        "decided_tickers": decided,
        "failed_or_degraded_tickers": degraded,
        "task_counts": task_counts,
        "completed_snapshot_id": session.get("completed_snapshot_id"),
        "plain_status": _plain_status(
            session_status, stage, total, evidence_done, analysis_done, decided,
        ),
        "retryable": not terminal,
        "terminal": terminal,
    }
    if session_status in (SESSION_COMPLETED, SESSION_COMPLETED_WITH_GAPS):
        summary_line = _evidence_summary_line(session.get("metrics"))
        if summary_line:
            result["evidence_summary_line"] = summary_line
    return result
