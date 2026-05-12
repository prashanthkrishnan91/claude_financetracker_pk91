"""Deploy Stage 2.5A — certified sizing source adapter v1.

Reads only existing persisted app data to build a DeploySizingInputBundle.
No providers, no live price fetch, no LLM calls.

Sources inspected:
  1. portfolio_snapshots: latest row for total_equity, cash_balance, and
     per-position market values (market_value_usd field in positions_data,
     if stored). Cost basis (shares * avg_cost) is NEVER promoted to
     certified market value.
  2. target_allocations: user-defined target weights (target_pct column).
  3. Settings: deploy_minimum_trade_usd + deploy_rounding_policy for
     policy certification.

Certification rules (deterministic):
  Staleness: snapshot older than STALE_THRESHOLD_HOURS is STALE — not certified.
  Position market values: only CERTIFIED if market_value_usd is explicitly
    present in positions_data AND the snapshot is fresh.
  Target allocations: CERTIFIED if present and valid (explicit user-defined).
  Policy: CERTIFIED if both settings fields are set and valid; UNSUPPORTED otherwise.

Fail-safe invariants:
  - Returns None if no portfolio snapshot exists.
  - Any DB error returns None (router falls back to scaffold/not_ready).
  - Never promotes cost basis to certified market value.
  - Never fabricates target allocations or policy.
  - Intel v3 Buy/Hold/Trim/Sell authority is not changed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from ..deploy.deploy_policy_bridge import build_policy_from_config
from ..deploy.deploy_sizing_contracts import (
    DeployCashInput,
    DeployPositionSizingInput,
    DeployPortfolioSizingInput,
    DeploySizingInputBundle,
    DeploySizingTrustStatus,
    DeployTargetAllocationInput,
)
from ..deploy.deploy_target_allocation_bridge import certify_target_allocation

logger = logging.getLogger(__name__)

# Snapshots older than this threshold are treated as STALE.
STALE_THRESHOLD_HOURS: float = 24.0

_SOURCE_PORTFOLIO_SNAPSHOT = "portfolio_snapshots"
_SOURCE_TARGET_ALLOCATIONS = "target_allocations_table"

# Sentinel used to distinguish "caller passed None" from "caller omitted arg".
_POLICY_UNSET = object()


async def build_sizing_bundle_from_persisted_data(
    user_id: UUID,
    db_client: Optional[Any] = None,
    _policy_config: Any = _POLICY_UNSET,
) -> Optional[DeploySizingInputBundle]:
    """Build a DeploySizingInputBundle from persisted app data.

    Returns None if no portfolio snapshot exists. The caller must treat
    None as "no sizing bundle" and preserve not_ready/scaffold behavior.

    Args:
        user_id: Authenticated user's UUID.
        db_client: Optional injected DB client (created internally if None).
        _policy_config: Optional explicit policy config dict for testing.
            If omitted (default sentinel), reads from Settings.
            Pass None or {} to force UNSUPPORTED policy.

    No providers, no live price fetch, no LLM calls. Read-only DB access.
    """
    if db_client is None:
        from ...database import get_supabase_client
        db_client = get_supabase_client()

    snapshot_row = await _read_latest_portfolio_snapshot(user_id, db_client)
    if snapshot_row is None:
        logger.debug("sizing_adapter: no portfolio snapshot for user_id=%s", user_id)
        return None

    snapshot_id = snapshot_row.get("id", "unknown")
    snapshot_at_raw = snapshot_row.get("snapshot_at")
    age_hours = _compute_age_hours(snapshot_at_raw)
    is_stale = age_hours is None or age_hours > STALE_THRESHOLD_HOURS
    source_label = f"{_SOURCE_PORTFOLIO_SNAPSHOT}:{str(snapshot_id)[:8]}"
    snapshot_trust = (
        DeploySizingTrustStatus.STALE if is_stale
        else DeploySizingTrustStatus.CERTIFIED
    )

    logger.debug(
        "sizing_adapter: snapshot_id=%s age_hours=%s stale=%s",
        snapshot_id, age_hours, is_stale,
    )

    # Cash.
    cash_raw = snapshot_row.get("cash_balance")
    cash = DeployCashInput(
        available_cash_usd=float(cash_raw) if cash_raw is not None else None,
        trust_status=snapshot_trust,
        source_label=source_label,
    )

    # Portfolio total.
    equity_raw = snapshot_row.get("total_equity")
    portfolio = DeployPortfolioSizingInput(
        total_portfolio_value_usd=float(equity_raw) if equity_raw is not None else None,
        trust_status=snapshot_trust,
        source_label=source_label,
    )

    # Per-position inputs.
    # Cost basis (shares * avg_cost) is NEVER promoted to certified market value.
    # Positions are certified only when market_value_usd is explicitly present
    # in positions_data AND the snapshot is fresh.
    positions: Dict[str, DeployPositionSizingInput] = {}
    total_equity_float = float(equity_raw) if equity_raw else 0.0
    for pos_entry in (snapshot_row.get("positions_data") or []):
        ticker = str(pos_entry.get("ticker") or "").strip().upper()
        if not ticker:
            continue

        market_value_raw = pos_entry.get("market_value_usd")
        if market_value_raw is not None and not is_stale:
            mkt_val = float(market_value_raw)
            weight = (mkt_val / total_equity_float) if total_equity_float > 0 else None
            pos_trust = DeploySizingTrustStatus.CERTIFIED
            pos_source = source_label
        else:
            # No market_value_usd stored, or snapshot is stale.
            # Do NOT use cost basis as a proxy for market value.
            mkt_val = None
            weight = None
            pos_trust = (
                DeploySizingTrustStatus.STALE if is_stale
                else DeploySizingTrustStatus.MISSING
            )
            pos_source = "not_provided"

        positions[ticker] = DeployPositionSizingInput(
            ticker=ticker,
            current_market_value_usd=mkt_val,
            current_weight=weight,
            trust_status=pos_trust,
            source_label=pos_source,
        )

    # Target allocations from target_allocations table.
    target_allocations = await _read_certified_target_allocations(user_id, db_client)

    # Policy from explicit config (tests) or Settings (production).
    if _policy_config is _POLICY_UNSET:
        policy = _build_policy_from_settings()
    else:
        policy = build_policy_from_config(_policy_config if _policy_config else None)

    bundle = DeploySizingInputBundle(
        cash=cash,
        portfolio=portfolio,
        positions=positions,
        target_allocations=target_allocations,
        policy=policy,
    )

    logger.debug(
        "sizing_adapter: bundle built sizing_values_ready=%s target_alloc_ready=%s "
        "policy_ready=%s exact_ready=%s suppression=%s",
        bundle.sizing_values_ready,
        bundle.target_allocation_ready,
        bundle.policy_ready,
        bundle.exact_dollar_ready,
        [r.value for r in bundle.get_suppression_reasons()],
    )

    return bundle


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _read_latest_portfolio_snapshot(
    user_id: UUID,
    db_client: Any,
) -> Optional[Dict[str, Any]]:
    """Return the latest portfolio_snapshots row for the user, or None."""
    try:
        result = await asyncio.to_thread(
            lambda: db_client.table("portfolio_snapshots")
            .select("id, snapshot_at, total_equity, cash_balance, positions_data")
            .eq("user_id", str(user_id))
            .order("snapshot_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception as exc:
        logger.warning("sizing_adapter: portfolio_snapshots read error: %s", exc)
        return None


async def _read_certified_target_allocations(
    user_id: UUID,
    db_client: Any,
) -> Dict[str, Any]:
    """Return a dict of ticker → certified DeployTargetAllocationInput."""
    try:
        result = await asyncio.to_thread(
            lambda: db_client.table("target_allocations")
            .select("ticker, target_pct")
            .eq("user_id", str(user_id))
            .execute()
        )
        rows = result.data or []
    except Exception as exc:
        logger.warning("sizing_adapter: target_allocations read error: %s", exc)
        return {}

    target_allocations: Dict[str, Any] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        target_pct = row.get("target_pct")
        if not ticker or target_pct is None:
            continue

        # Duplicate ticker rows indicate conflicting data — mark CONFLICTING so the
        # portfolio-level readiness gate sees an explicit suppression reason rather
        # than silently accepting the last-write value.
        if ticker in target_allocations:
            logger.warning(
                "sizing_adapter: duplicate target_allocation row for ticker=%s "
                "— marking CONFLICTING to suppress exact-dollar readiness",
                ticker,
            )
            target_allocations[ticker] = DeployTargetAllocationInput(
                ticker=ticker,
                trust_status=DeploySizingTrustStatus.CONFLICTING,
            )
            continue

        try:
            weight = float(target_pct) / 100.0
            ta = certify_target_allocation(
                ticker=ticker,
                target_weight=weight,
                source_label=_SOURCE_TARGET_ALLOCATIONS,
            )
            target_allocations[ticker] = ta
        except (ValueError, TypeError) as exc:
            logger.debug(
                "sizing_adapter: target_allocation cert failed ticker=%s: %s", ticker, exc
            )

    return target_allocations


def _build_policy_from_settings() -> Any:
    """Return CERTIFIED policy from Settings, or UNSUPPORTED if not configured."""
    try:
        from ...config import get_settings
        settings = get_settings()
        min_trade = getattr(settings, "deploy_minimum_trade_usd", None)
        rounding = getattr(settings, "deploy_rounding_policy", None)
        if min_trade is not None and rounding is not None:
            return build_policy_from_config(
                {"minimum_trade_usd": min_trade, "rounding_policy": rounding}
            )
    except Exception as exc:
        logger.debug("sizing_adapter: policy config read error: %s", exc)
    return build_policy_from_config(None)


def _compute_age_hours(snapshot_at_raw: Any) -> Optional[float]:
    """Return snapshot age in hours, or None on parse error."""
    if snapshot_at_raw is None:
        return None
    try:
        if isinstance(snapshot_at_raw, datetime):
            ts = snapshot_at_raw
        else:
            ts = datetime.fromisoformat(str(snapshot_at_raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        return delta.total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None
