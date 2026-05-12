"""Deploy v3 read-only plan API — Stage 2.4A.

GET /deploy/v3/plan

Returns a read-only Deploy v3 plan built from the latest Intel v3 snapshot.
Zero LLM calls. Zero provider calls. Zero legacy allocation engine calls.

Contract:
  - Intel v3 remains the only Buy/Hold/Trim/Sell authority.
  - Sizing bundle is not provided; dollar fields are scaffold placeholders.
  - Honest not-ready/scaffold behavior when sizing inputs are absent.
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
    Sizing bundle is not provided — dollar fields are scaffold placeholders only.
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
    plan = build_deploy_plan(deploy_inputs, sizing_bundle=None)

    plan_dict = dataclasses.asdict(plan)

    logger.info(
        "deploy_v3.plan user_id=%s snapshot_id=%s items=%d plan_readiness=%s",
        user.id,
        plan.snapshot_id,
        len(plan.items),
        plan.rollup.plan_readiness_status if plan.rollup else "no_rollup",
    )

    return {
        "plan_status": plan_dict["plan_status"],
        "snapshot_id": plan_dict["snapshot_id"],
        "run_id": plan_dict["run_id"],
        "schema_version": plan_dict["schema_version"],
        "items": plan_dict["items"],
        "guardrail_summary": plan_dict["guardrail_summary"],
        "rollup": plan_dict["rollup"],
        "source": {
            "intel_source": "INTEL_V3",
            "sizing_bundle_provided": False,
            "note": (
                "No sizing bundle provided. "
                "Dollar fields are scaffold placeholders — not executable trade instructions."
            ),
        },
    }
