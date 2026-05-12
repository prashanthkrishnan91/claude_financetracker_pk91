"""Deploy Stage 2.5D — production readiness diagnostic v1.

Read-only. No providers, no live price calls, no LLM, no broker,
no legacy allocation engine.

Inspects persisted app data (portfolio_snapshots, target_allocations, Settings)
and reports exactly why exact-dollar readiness is or is not met, with a
plain-English next_required_action.

Policy env var presence is reported per-var (configured: yes/no). Values
are never exposed. Policy status distinguishes: certified, missing_minimum_trade,
missing_rounding_policy, invalid_policy_config, unsupported_policy.
Intel v3 Buy/Hold/Trim/Sell authority is not changed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from .deploy_sizing_contracts import DeploySizingTrustStatus
from .deploy_sizing_source_adapter_v1 import (
    STALE_THRESHOLD_HOURS,
    build_sizing_bundle_from_persisted_data,
)

logger = logging.getLogger(__name__)

# Sentinel: "use Settings" — do not pass an explicit policy config.
_DIAG_POLICY_UNSET = object()


async def build_readiness_diagnostic(
    user_id: UUID,
    db_client: Optional[Any] = None,
    _policy_config: Any = _DIAG_POLICY_UNSET,
    _policy_presence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a production readiness diagnostic for the Deploy v3 exact-dollar path.

    Returns a structured dict with:
    - Readiness gate booleans (exact_dollar_ready, sizing_values_ready, etc.)
    - Snapshot presence, age, and stale/fresh status
    - Market value coverage per position ticker
    - Target allocation rows, missing/conflicting tickers, portfolio total %
    - Policy configuration presence per env var (no values exposed), policy_status
    - Suppression reasons from the sizing bundle
    - Plain-English next_required_action

    Args:
        _policy_presence: Test-only injection. When None, reads Settings to check
            which deploy policy env vars are present. Pass a dict with
            ``minimum_trade_present`` and ``rounding_policy_present`` keys to
            override for tests. Ignored when the bundle has CERTIFIED policy.

    No providers. No live price calls. No LLM. No broker. No legacy allocation engine.
    """
    if db_client is None:
        from ...database import get_supabase_client
        db_client = get_supabase_client()

    snap_meta = await _read_snapshot_metadata(user_id, db_client)

    bundle = None
    try:
        if _policy_config is _DIAG_POLICY_UNSET:
            bundle = await build_sizing_bundle_from_persisted_data(
                user_id=user_id, db_client=db_client
            )
        else:
            bundle = await build_sizing_bundle_from_persisted_data(
                user_id=user_id, db_client=db_client, _policy_config=_policy_config
            )
    except Exception as exc:
        logger.warning("readiness_diagnostic: adapter error: %s", exc)

    return _build_response(snap_meta, bundle, policy_presence=_policy_presence)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _read_policy_presence() -> Dict[str, Any]:
    """Check which deploy policy env vars are set, without exposing their values."""
    try:
        from ...config import get_settings
        settings = get_settings()
        min_trade = getattr(settings, "deploy_minimum_trade_usd", None)
        rounding = getattr(settings, "deploy_rounding_policy", None)
        return {
            "minimum_trade_present": min_trade is not None,
            "rounding_policy_present": rounding is not None,
        }
    except Exception:
        return {"minimum_trade_present": False, "rounding_policy_present": False}


def _policy_section_from_bundle(
    bundle: Any,
    policy_presence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the policy diagnostic section.

    When bundle has CERTIFIED policy, derives presence from the bundle
    (both vars are known-present and valid). When UNSUPPORTED, reads
    Settings (or uses the injected override) to distinguish which var
    is missing and why.
    """
    if (
        bundle is not None
        and bundle.policy is not None
        and bundle.policy.trust_status == DeploySizingTrustStatus.CERTIFIED
    ):
        return {
            "minimum_trade_configured": True,
            "rounding_policy_configured": True,
            "policy_valid": True,
            "policy_status": "certified",
        }

    # UNSUPPORTED or None — read Settings to get per-var presence.
    presence = policy_presence if policy_presence is not None else _read_policy_presence()
    min_p = bool(presence.get("minimum_trade_present", False))
    rounding_p = bool(presence.get("rounding_policy_present", False))

    if not min_p and not rounding_p:
        policy_status = "unsupported_policy"
    elif not min_p:
        policy_status = "missing_minimum_trade"
    elif not rounding_p:
        policy_status = "missing_rounding_policy"
    else:
        # Both vars present but bundle not CERTIFIED — values are invalid.
        policy_status = "invalid_policy_config"

    return {
        "minimum_trade_configured": min_p,
        "rounding_policy_configured": rounding_p,
        "policy_valid": False,
        "policy_status": policy_status,
    }


async def _read_snapshot_metadata(
    user_id: UUID,
    db_client: Any,
) -> Optional[Dict[str, Any]]:
    """Return minimal snapshot metadata (id, timestamp, age), or None if absent."""
    try:
        result = await asyncio.to_thread(
            lambda: db_client.table("portfolio_snapshots")
            .select("id, snapshot_at")
            .eq("user_id", str(user_id))
            .order("snapshot_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        row = rows[0]
        snapshot_at_raw = row.get("snapshot_at")
        age_hrs = _age_hours(snapshot_at_raw)
        is_stale = age_hrs is None or age_hrs > STALE_THRESHOLD_HOURS
        return {
            "snapshot_id": str(row.get("id", "")),
            "snapshot_at": str(snapshot_at_raw) if snapshot_at_raw else None,
            "age_hours": round(age_hrs, 2) if age_hrs is not None else None,
            "is_stale": is_stale,
        }
    except Exception as exc:
        logger.warning("readiness_diagnostic: snapshot metadata read error: %s", exc)
        return None


def _age_hours(snapshot_at_raw: Any) -> Optional[float]:
    """Return snapshot age in hours from now, or None on parse error."""
    if snapshot_at_raw is None:
        return None
    try:
        if isinstance(snapshot_at_raw, datetime):
            ts = snapshot_at_raw
        else:
            ts = datetime.fromisoformat(str(snapshot_at_raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None


def _build_response(
    snap_meta: Optional[Dict[str, Any]],
    bundle: Any,  # Optional[DeploySizingInputBundle]
    policy_presence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the diagnostic response from snapshot metadata and sizing bundle."""

    # --- Snapshot section ---
    if snap_meta is None:
        snapshot_section: Dict[str, Any] = {
            "present": False,
            "snapshot_id": None,
            "snapshot_at": None,
            "age_hours": None,
            "status": "missing",
        }
    else:
        snapshot_section = {
            "present": True,
            "snapshot_id": snap_meta["snapshot_id"],
            "snapshot_at": snap_meta["snapshot_at"],
            "age_hours": snap_meta["age_hours"],
            "status": "stale" if snap_meta["is_stale"] else "fresh",
        }

    if bundle is None:
        policy_section = _policy_section_from_bundle(None, policy_presence)
        return {
            "exact_dollar_ready": False,
            "sizing_values_ready": False,
            "target_allocation_ready": False,
            "policy_ready": False,
            "snapshot": snapshot_section,
            "market_values": {
                "all_positions_have_market_value": False,
                "uncertified_tickers": [],
                "position_count": 0,
            },
            "target_allocations": {
                "unique_tickers_in_db": 0,
                "missing_tickers": [],
                "conflicting_tickers": [],
                "target_total_pct": None,
                "target_total_in_range": None,
            },
            "policy": policy_section,
            "suppression_reasons": [],
            "next_required_action": "Create a fresh portfolio snapshot to begin.",
        }

    # --- Market values section ---
    uncertified: List[str] = sorted(
        ticker
        for ticker, pos in bundle.positions.items()
        if pos.trust_status != DeploySizingTrustStatus.CERTIFIED
        or pos.current_market_value_usd is None
    )
    market_values_section: Dict[str, Any] = {
        "all_positions_have_market_value": len(uncertified) == 0,
        "uncertified_tickers": uncertified,
        "position_count": len(bundle.positions),
    }

    # --- Target allocations section ---
    conflicting: List[str] = sorted(
        ticker
        for ticker, ta in bundle.target_allocations.items()
        if ta.trust_status == DeploySizingTrustStatus.CONFLICTING
    )
    missing: List[str] = sorted(
        ticker
        for ticker in bundle.positions
        if ticker not in bundle.target_allocations
        or not bundle.target_allocations[ticker].is_ready_for_math
    )
    certified_weights = [
        bundle.target_allocations[t].target_weight
        for t in bundle.positions
        if t in bundle.target_allocations
        and bundle.target_allocations[t].is_ready_for_math
        and bundle.target_allocations[t].target_weight is not None
    ]
    if certified_weights:
        raw_total = sum(certified_weights) * 100.0
        target_total_pct: Optional[float] = round(raw_total, 2)
        target_total_in_range: Optional[bool] = 98.0 <= raw_total <= 102.0
    else:
        target_total_pct = None
        target_total_in_range = None

    target_alloc_section: Dict[str, Any] = {
        "unique_tickers_in_db": len(bundle.target_allocations),
        "missing_tickers": missing,
        "conflicting_tickers": conflicting,
        "target_total_pct": target_total_pct,
        "target_total_in_range": target_total_in_range,
    }

    # --- Policy section (no secret values) ---
    policy_section = _policy_section_from_bundle(bundle, policy_presence)

    suppression_reasons = [r.value for r in bundle.get_suppression_reasons()]

    next_action = _next_required_action(
        snapshot_section, market_values_section, target_alloc_section, policy_section
    )

    return {
        "exact_dollar_ready": bundle.exact_dollar_ready,
        "sizing_values_ready": bundle.sizing_values_ready,
        "target_allocation_ready": bundle.target_allocation_ready,
        "policy_ready": bundle.policy_ready,
        "snapshot": snapshot_section,
        "market_values": market_values_section,
        "target_allocations": target_alloc_section,
        "policy": policy_section,
        "suppression_reasons": suppression_reasons,
        "next_required_action": next_action,
    }


def _next_required_action(
    snapshot: Dict[str, Any],
    market_values: Dict[str, Any],
    target_alloc: Dict[str, Any],
    policy: Dict[str, Any],
) -> str:
    """Return the single highest-priority next action in plain English."""
    if snapshot["status"] == "missing":
        return "Create a fresh portfolio snapshot to begin."
    if snapshot["status"] == "stale":
        return (
            "Create a fresh portfolio snapshot — "
            "the current one is more than 24 hours old."
        )
    if not market_values["all_positions_have_market_value"]:
        tickers = ", ".join(market_values["uncertified_tickers"])
        return (
            f"Create a fresh snapshot with valid prices to populate "
            f"market values for: {tickers}."
        )
    if target_alloc["conflicting_tickers"]:
        tickers = ", ".join(target_alloc["conflicting_tickers"])
        return f"Remove duplicate target allocation rows for: {tickers}."
    if target_alloc["missing_tickers"]:
        tickers = ", ".join(target_alloc["missing_tickers"])
        return (
            f"Add target allocations for: {tickers}. "
            "Portfolio total must be between 98% and 102%."
        )
    total = target_alloc.get("target_total_pct")
    if total is not None and not target_alloc["target_total_in_range"]:
        if total < 98.0:
            return (
                f"Adjust target allocations — current total is {total:.1f}%, "
                "must be at least 98%."
            )
        return (
            f"Adjust target allocations — current total is {total:.1f}%, "
            "must not exceed 102%."
        )
    if not policy["policy_valid"]:
        status = policy.get("policy_status", "")
        if status == "missing_minimum_trade":
            return "Set DEPLOY_MINIMUM_TRADE_USD environment variable."
        if status == "missing_rounding_policy":
            return "Set DEPLOY_ROUNDING_POLICY environment variable."
        if status == "invalid_policy_config":
            return (
                "Fix deploy policy env vars — "
                "both are set but the configuration is invalid."
            )
        return (
            "Set DEPLOY_MINIMUM_TRADE_USD and DEPLOY_ROUNDING_POLICY "
            "environment variables."
        )
    return "Exact-dollar path is ready. All readiness gates pass."
