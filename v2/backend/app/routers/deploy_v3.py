"""Deploy v3 read-only plan API — Stage 2.4A + Stage 2.5A sizing source adapter.

GET /deploy/v3/plan       — Deploy plan from latest Intel v3 snapshot.
GET /deploy/v3/readiness  — Production readiness diagnostic (Stage 2.5D).

Zero LLM calls. Zero provider calls. Does not call the legacy allocation engine.

Contract:
  - Intel v3 remains the only Buy/Hold/Trim/Sell authority.
  - Sizing bundle is built from portfolio_snapshots + target_allocations + Settings.
  - If any source is missing, stale, or uncertified, dollar fields remain scaffold.
  - Honest not-ready/scaffold behavior when sizing inputs cannot be certified.
  - Legacy /allocation/plan route is unaffected.

Feature flag: INTEL_V3_VISIBLE_SNAPSHOT_ENABLED
  When disabled, both endpoints return 404 (mirrors intel_v3 router behavior).
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..services.deploy.deploy_intel_adapter import build_deploy_inputs_from_snapshot
from ..services.deploy.deploy_readiness_diagnostic_v1 import build_readiness_diagnostic
from ..services.deploy.deploy_sizing_source_adapter_v1 import (
    build_sizing_bundle_from_persisted_data,
)
from ..services.deploy.deploy_translation_v1 import build_deploy_plan
from ..services.intelligence.v3.intel_v3_service import IntelV3Service, is_intel_v3_enabled

router = APIRouter(prefix="/deploy/v3", tags=["deploy_v3"])
logger = logging.getLogger(__name__)


def _check_flag() -> None:
    """Raise 404 when the Intel v3 feature flag is disabled."""
    if not is_intel_v3_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Deploy v3 requires Intel v3 to be enabled. "
                "Set INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true to enable."
            ),
        )


@router.get("/readiness")
async def get_deploy_v3_readiness(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get a production readiness diagnostic for the Deploy v3 exact-dollar path.

    Read-only. Zero LLM calls. Zero provider calls. Does not call the legacy allocation engine.
    Reports snapshot status, market value coverage, target allocation status and portfolio
    total, policy configuration presence (no secret values), and a plain-English
    next_required_action.
    Returns 404 if the Intel v3 feature flag is disabled.
    """
    _check_flag()

    try:
        diagnostic = await build_readiness_diagnostic(user_id=user.id)
    except Exception as exc:
        logger.warning("deploy_v3.readiness: diagnostic build failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Readiness diagnostic unavailable.",
        )

    logger.info(
        "deploy_v3.readiness user_id=%s exact_dollar_ready=%s next_action=%r",
        user.id,
        diagnostic.get("exact_dollar_ready"),
        diagnostic.get("next_required_action"),
    )

    return diagnostic


@router.get("/plan")
async def get_deploy_v3_plan(
    user: AuthenticatedUser = Depends(get_current_user),
    cash_to_deploy: Optional[float] = Query(
        default=None,
        ge=0,
        description=(
            "User-entered new-cash planning capital in USD. "
            "When provided and > 0, enables amount-aware BUY sizing relative to "
            "(portfolio_value + cash_to_deploy). "
            "Does not claim broker-verified cash availability. "
            "Omit or pass 0 to use the existing current-gap behavior."
        ),
    ),
):
    """Get the Deploy v3 plan built from the latest Intel v3 snapshot.

    Read-only. Zero LLM calls. Zero provider calls.
    Does not call the legacy allocation engine.
    Returns 404 if no Intel v3 snapshot exists or flag is disabled.
    Attempts to build a certified sizing bundle from persisted portfolio data.
    Falls back to scaffold/not_ready behavior if sources are unavailable or uncertified.

    When cash_to_deploy > 0, enables amount-aware new-cash planning:
    BUY items are sized toward target_weight * (portfolio_value + cash_to_deploy).
    Total BUY dollars are capped at cash_to_deploy. TRIM/SELL use current-gap math.
    User-entered planning capital is NOT broker-verified cash — source metadata
    reflects this clearly (sizing_mode: "new_cash", amount_aware: true).
    """
    _check_flag()

    service = IntelV3Service(user_id=user.id)
    snapshot = await service.get_latest_snapshot()

    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "no_snapshot",
                "message": "No Intel v3 snapshot exists yet. Run Intel v3 first.",
            },
        )

    deploy_inputs = build_deploy_inputs_from_snapshot(snapshot)

    # Attempt to build a certified sizing bundle from persisted data.
    # Falls back to None (scaffold/not_ready) on error or missing sources.
    sizing_bundle = None
    try:
        sizing_bundle = await build_sizing_bundle_from_persisted_data(user_id=user.id)
    except Exception as exc:
        logger.warning(
            "deploy_v3.plan: sizing adapter failed, proceeding without bundle: %s", exc
        )

    # Coerce to float|None — guards against FastAPI Query() objects when called directly in tests.
    _cash_float: Optional[float] = float(cash_to_deploy) if isinstance(cash_to_deploy, (int, float)) else None
    is_amount_aware = bool(_cash_float and _cash_float > 0)
    cash_to_deploy = _cash_float  # normalise for all downstream use
    plan = build_deploy_plan(
        deploy_inputs,
        sizing_bundle=sizing_bundle,
        cash_to_deploy=cash_to_deploy if is_amount_aware else None,
    )
    plan_dict = dataclasses.asdict(plan)

    # Build source metadata reflecting readiness gates and amount-aware mode.
    if sizing_bundle is not None:
        suppression_reasons = [r.value for r in sizing_bundle.get_suppression_reasons()]
        if is_amount_aware:
            note = (
                f"Amount-aware new-cash planning. Sized for user-entered ${cash_to_deploy:.2f} "
                "planning capital (not broker-verified cash). "
                "BUY deltas relative to (portfolio + cash_to_deploy). "
                "Total BUY capped at cash_to_deploy."
            )
        else:
            note = (
                "Sizing bundle certified. Exact-dollar math evaluated."
                if sizing_bundle.exact_dollar_ready
                else (
                    "Sizing bundle provided from persisted sources. "
                    "Exact-dollar math not yet ready — see suppression_reasons."
                )
            )
        source = {
            "intel_source": "INTEL_V3",
            "sizing_bundle_provided": True,
            "exact_dollar_ready": sizing_bundle.exact_dollar_ready,
            "sizing_values_ready": sizing_bundle.sizing_values_ready,
            "target_allocation_ready": sizing_bundle.target_allocation_ready,
            "policy_ready": sizing_bundle.policy_ready,
            "suppression_reasons": suppression_reasons,
            "cash_source": (
                "user_entered_planning_capital" if is_amount_aware
                else (sizing_bundle.cash.source_label if sizing_bundle.cash else None)
            ),
            "portfolio_source": (
                sizing_bundle.portfolio.source_label if sizing_bundle.portfolio else None
            ),
            "amount_aware": is_amount_aware,
            "cash_to_deploy": cash_to_deploy if is_amount_aware else None,
            "sizing_mode": "new_cash" if is_amount_aware else "current_gap",
            "note": note,
        }
    else:
        source = {
            "intel_source": "INTEL_V3",
            "sizing_bundle_provided": False,
            "exact_dollar_ready": False,
            "sizing_values_ready": False,
            "target_allocation_ready": False,
            "policy_ready": False,
            "suppression_reasons": [],
            "amount_aware": False,
            "cash_to_deploy": None,
            "sizing_mode": "current_gap",
            "note": (
                "No sizing bundle provided. "
                "Dollar fields are scaffold placeholders — not executable trade instructions."
            ),
        }

    logger.info(
        "deploy_v3.plan user_id=%s snapshot_id=%s items=%d plan_readiness=%s "
        "sizing_bundle_provided=%s exact_dollar_ready=%s amount_aware=%s cash_to_deploy=%s",
        user.id,
        plan.snapshot_id,
        len(plan.items),
        plan.rollup.plan_readiness_status if plan.rollup else "no_rollup",
        sizing_bundle is not None,
        sizing_bundle.exact_dollar_ready if sizing_bundle else False,
        is_amount_aware,
        cash_to_deploy if is_amount_aware else None,
    )

    return {
        "plan_status": plan_dict["plan_status"],
        "snapshot_id": plan_dict["snapshot_id"],
        "run_id": plan_dict["run_id"],
        "schema_version": plan_dict["schema_version"],
        "items": plan_dict["items"],
        "guardrail_summary": plan_dict["guardrail_summary"],
        "rollup": plan_dict["rollup"],
        "source": source,
    }
