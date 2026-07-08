"""Stage 12D — Paycheck plan preview read model.

Product-facing, cert-gated, read-only wrapper around the Stage 12C
next-buy-policy diagnostic (``app.services.allocation_policy_v1``). Converts
the full diagnostic payload into a concise, frontend-safe preview without
duplicating any allocation math.

Read-only. No writes. No provider calls. No LLM calls. No recommendation rows.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..database import get_supabase_client
from ..middleware.auth import AuthenticatedUser
from .diagnostics import _get_runtime_cert_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/advisor/paycheck-plan", tags=["advisor"])

PREVIEW_VERSION = "paycheck_plan_preview_v1"

_ADVICE_CAVEAT = (
    "This is deterministic allocation guidance, not personalized investment advice."
)

# Plain-English text for known reason codes. Group-underweight codes are
# dynamic (f"{group}_group_underweight") and handled separately.
_REASON_CODE_TEXT: dict[str, str] = {
    "core_etf_preference": "Preferred as a core broad-market ETF",
    "preferred_vti_over_spy": "Chosen ahead of SPY under the core ETF preference order",
    "etf_floor_not_met": "Overall ETF allocation floor is not yet met",
    "positive_gap": "Below its target allocation weight",
}


class PaycheckPlanPreviewRequest(BaseModel):
    """Stage 12D — paycheck plan preview request.

    Mirrors the Stage 12C diagnostic's input contract so preview and
    diagnostic stay in lockstep for the same cash amount.
    """
    cash_to_deploy: float = Field(..., gt=0, description="Cash available to deploy (must be > 0)")
    max_positions: int = Field(default=5, ge=1, le=20)
    min_trade_amount: float = Field(default=25.0, ge=1.0)


def _reason_text(code: str) -> str:
    if code in _REASON_CODE_TEXT:
        return _REASON_CODE_TEXT[code]
    if code.endswith("_group_underweight"):
        return "This asset group is underweight versus its target"
    return code.replace("_", " ").capitalize()


def _build_planned_buys(candidates: list[dict]) -> list[dict]:
    planned_buys = []
    for c in candidates:
        reason_codes = c.get("reason_codes") or []
        reason_texts = [_reason_text(code) for code in reason_codes] or ["Below its target allocation weight"]
        planned_buys.append({
            "ticker": c["ticker"],
            "amount": round(c.get("dollar_amount", 0.0), 2),
            "reason": "; ".join(dict.fromkeys(reason_texts)),
            "reason_codes": reason_codes,
        })
    return planned_buys


def _build_caveats(
    trusted: bool,
    preview_status: str,
    missing_price_tickers: list[str],
    stale_price_tickers: list[str],
) -> list[str]:
    caveats = [_ADVICE_CAVEAT]
    if not trusted:
        caveats.append(
            "The numeric plan is not yet fully trusted — treat these figures as directional only."
        )
    if preview_status != "ready":
        caveats.append(
            "No investable buy plan is confirmed until underlying portfolio data is fully refreshed."
        )
    if missing_price_tickers:
        caveats.append("Some holdings are missing recent price data.")
    if stale_price_tickers:
        caveats.append("Some holdings have stale price data.")
    return caveats


def build_paycheck_plan_preview(diagnostic: dict[str, Any]) -> dict[str, Any]:
    """Convert a Stage 12C next-buy-policy diagnostic into a concise preview.

    Pure mapping — no allocation math is recomputed here.
    """
    verdict = diagnostic.get("verdict", {})
    cash_plan = diagnostic.get("cash_plan", {})
    truth_dependency = diagnostic.get("truth_dependency", {})

    policy_status = verdict.get("policy_status", "blocked")
    trusted = bool(verdict.get("numeric_plan_trusted", False))

    if policy_status == "blocked":
        preview_status = "blocked"
    elif policy_status == "degraded" or not trusted:
        preview_status = "degraded"
    else:
        preview_status = "ready"

    candidates = diagnostic.get("next_buy_candidates", []) if preview_status == "ready" else []
    planned_buys = _build_planned_buys(candidates)

    next_required_fix = verdict.get("next_required_fix")
    if preview_status == "ready" and trusted:
        next_required_fix = None

    return {
        "preview_version": PREVIEW_VERSION,
        "cash_to_deploy": diagnostic.get("input", {}).get("cash_to_deploy"),
        "trusted": trusted,
        "status": preview_status,
        "planned_buys": planned_buys,
        "allocation_summary": {
            "allocated_cash": cash_plan.get("allocated_cash", 0.0),
            "unallocated_cash": cash_plan.get("unallocated_cash", 0.0),
            "allocation_count": cash_plan.get("allocation_count", 0),
        },
        "data_freshness_status": truth_dependency.get("price_coverage_status", "unknown"),
        "caveats": _build_caveats(
            trusted,
            preview_status,
            truth_dependency.get("missing_price_tickers", []),
            truth_dependency.get("stale_price_tickers", []),
        ),
        "next_required_fix": next_required_fix,
        "recommendations_trusted": False,
        "source_diagnostic_version": diagnostic.get("diagnostic_version", "unknown"),
    }


@router.post("/preview")
async def paycheck_plan_preview(
    payload: PaycheckPlanPreviewRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Stage 12D — concise, frontend-safe paycheck plan preview.

    Wraps the Stage 12C next-buy-policy diagnostic. Reuses its allocation
    policy logic verbatim; performs no additional allocation math.

    Invariants:
    - Read-only — no writes to any table
    - No live provider calls
    - No LLM calls
    - No recommendation rows created
    - Cert-gated (X-Finance-Runtime-Cert-Secret required)
    - recommendations_trusted is always False
    """
    from ..services.allocation_policy_v1 import run_next_buy_policy_diagnostic

    db_client = get_supabase_client()
    diagnostic = await run_next_buy_policy_diagnostic(
        db_client=db_client,
        user_id=str(user.id),
        cash_to_deploy=payload.cash_to_deploy,
        max_positions=payload.max_positions,
        min_trade_amount=payload.min_trade_amount,
    )

    preview = build_paycheck_plan_preview(diagnostic)

    logger.info(
        "paycheck_plan_preview user=%s cash=%.2f status=%s trusted=%s buys=%d",
        getattr(user, "email", "unknown"),
        payload.cash_to_deploy,
        preview["status"],
        preview["trusted"],
        len(preview["planned_buys"]),
    )

    return preview
