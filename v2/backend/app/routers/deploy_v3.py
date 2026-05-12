"""Deploy v3 read-only plan API — Stage 2.4A + Stage 2.5A sizing source adapter.

GET /deploy/v3/plan

Returns a read-only Deploy v3 plan built from the latest Intel v3 snapshot,
with a certified sizing bundle when persisted data is sufficient.

Zero LLM calls. Zero provider calls. Does not call the legacy allocation engine.

Contract:
  - Intel v3 remains the only Buy/Hold/Trim/Sell authority.
  - Sizing bundle is built from portfolio_snapshots + target_allocations + Settings.
  - If any source is missing, stale, or uncertified, dollar fields remain scaffold.
  - Honest not-ready/scaffold behavior when sizing inputs cannot be certified.
  - Legacy /allocation/plan route is unaffected.

Feature flag: INTEL_V3_VISIBLE_SNAPSHOT_ENABLED
  When disabled, returns 404 (mirrors intel_v3 router behavior).
"""
from __future__ import annotations

import dataclasses
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from ..middleware.auth import AuthenticatedUser, get_current_user
from ..services.deploy.deploy_intel_adapter import build_deploy_inputs_from_snapshot
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
                "Deploy v3 plan requires Intel v3 to be enabled. "
                "Set INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true to enable."
            ),
        )


@router.get("/plan")
async def get_deploy_v3_plan(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get the Deploy v3 plan built from the latest Intel v3 snapshot.

    Read-only. Zero LLM calls. Zero provider calls.
    Does not call the legacy allocation engine.
    Returns 404 if no Intel v3 snapshot exists or flag is disabled.
    Attempts to build a certified sizing bundle from persisted portfolio data.
    Falls back to scaffold/not_ready behavior if sources are unavailable or uncertified.
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

    plan = build_deploy_plan(deploy_inputs, sizing_bundle=sizing_bundle)
    plan_dict = dataclasses.asdict(plan)

    # Build source metadata reflecting readiness gates.
    if sizing_bundle is not None:
        suppression_reasons = [r.value for r in sizing_bundle.get_suppression_reasons()]
        source = {
            "intel_source": "INTEL_V3",
            "sizing_bundle_provided": True,
            "exact_dollar_ready": sizing_bundle.exact_dollar_ready,
            "sizing_values_ready": sizing_bundle.sizing_values_ready,
            "target_allocation_ready": sizing_bundle.target_allocation_ready,
            "policy_ready": sizing_bundle.policy_ready,
            "suppression_reasons": suppression_reasons,
            "cash_source": (
                sizing_bundle.cash.source_label if sizing_bundle.cash else None
            ),
            "portfolio_source": (
                sizing_bundle.portfolio.source_label if sizing_bundle.portfolio else None
            ),
            "note": (
                "Sizing bundle certified. Exact-dollar math evaluated."
                if sizing_bundle.exact_dollar_ready
                else (
                    "Sizing bundle provided from persisted sources. "
                    "Exact-dollar math not yet ready — see suppression_reasons."
                )
            ),
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
            "note": (
                "No sizing bundle provided. "
                "Dollar fields are scaffold placeholders — not executable trade instructions."
            ),
        }

    logger.info(
        "deploy_v3.plan user_id=%s snapshot_id=%s items=%d plan_readiness=%s "
        "sizing_bundle_provided=%s exact_dollar_ready=%s",
        user.id,
        plan.snapshot_id,
        len(plan.items),
        plan.rollup.plan_readiness_status if plan.rollup else "no_rollup",
        sizing_bundle is not None,
        sizing_bundle.exact_dollar_ready if sizing_bundle else False,
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
