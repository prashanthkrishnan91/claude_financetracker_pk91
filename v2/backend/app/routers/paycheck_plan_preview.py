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
    "evidence_fresh_and_constructive": "Passed evidence freshness, confidence, and concentration checks",
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
    stock_candidates_status: str | None = None,
) -> list[str]:
    caveats = [_ADVICE_CAVEAT]
    if not trusted:
        caveats.append(
            "The numeric plan is not yet fully trusted — treat these figures as directional only."
        )
    if preview_status == "blocked":
        caveats.append(
            "No investable buy plan is confirmed until underlying portfolio data is fully refreshed."
        )
    elif preview_status == "degraded":
        caveats.append(
            "This plan is preserved from the last calculable run but is degraded, not fully "
            "certified — some non-fatal price/provider limitations remain (see below)."
        )
    if missing_price_tickers:
        caveats.append("Some holdings are missing recent price data.")
    if stale_price_tickers:
        caveats.append(
            "Some holdings have stale price data — none of them are eligible to receive new "
            "cash (allocation_policy_v1 excludes both missing and stale prices from candidacy)."
        )
    if stock_candidates_status == "blocked_insufficient_evidence":
        caveats.append(
            "Individual stocks were not included in this plan — evidence data did not pass "
            "freshness or confidence checks. This plan covers ETF allocation only."
        )
    elif stock_candidates_status == "blocked_by_policy_caps":
        caveats.append(
            "Individual stocks were not included in this plan — held positions are already "
            "at or above their concentration cap."
        )
    return caveats


# ── Consolidation: plain-English explanation buckets for the Advisor view ────
# Pure presentation over the diagnostic's existing fields — no allocation math.
# Raw reason codes are preserved alongside every translation for expandable
# technical detail in the UI.

_EVIDENCE_GATE_TEXT: dict[str, str] = {
    "evidence_missing_for_ticker": "No certified Intel evidence exists for this ticker yet.",
    "evidence_stale": "Its Intel evidence is older than the 24-hour freshness window.",
    "evidence_freshness_unknown": "Its Intel evidence has no usable timestamp, so freshness cannot be verified.",
    "evidence_signal_not_constructive": "Its Intel action is HOLD — only BUY evidence makes a stock eligible for new cash.",
    "evidence_confidence_insufficient": "Its Intel evidence band is too thin to support new cash.",
    "evidence_has_blocking_gaps": "Its Intel evidence carries blocking data-quality flags.",
}


def _policy_block_text(code: str) -> str:
    if code == "individual_stock_group_above_target":
        return "The individual-stock sleeve is already at or above its policy target."
    if code.startswith("at_or_above_") and code.endswith("cap_20pct"):
        return "This position is already at or above its per-position concentration cap."
    if code.startswith("at_or_above_"):
        return "This position is already at or above its concentration cap."
    if code.startswith("etf_group_") and code.endswith("_already_above_target"):
        return "Its ETF group is already above its target allocation."
    if code == "crypto_group_at_or_above_cap":
        return "The crypto group is already at or above its policy cap."
    if code == "alternatives_group_at_or_above_cap":
        return "The alternatives group is already at or above its policy cap."
    if code == "no_price_available":
        return "No trusted current price is available for this ticker."
    if code == "stale_price_not_eligible_for_new_cash":
        return "This ticker's price is stale, so it cannot receive new cash right now."
    return code.replace("_", " ").capitalize()


def _policy_block_bucket(code: str) -> str:
    if code == "no_price_available":
        return "missing_truth_blocked"
    if code == "stale_price_not_eligible_for_new_cash":
        return "stale_price_blocked"
    if code.startswith("at_or_above_") and "group" not in code:
        return "concentration_blocked"
    return "group_cap_blocked"


# Codes whose plain-English "not selected" entry is built authoritatively by
# the dedicated stale/missing-price loops below (using truth_dependency's own
# ticker lists, which is the single source of truth for price-coverage
# status) — never also appended here from ticker_gaps.policy_ineligibility_reason,
# or the same ticker would appear twice in `not_selected`.
_PRICE_TRUTH_CODES = frozenset({"no_price_available", "stale_price_not_eligible_for_new_cash"})


def _evidence_summary_for_candidate(candidate: dict) -> dict | None:
    """Evidence action/band for stock candidates (from Stage 13A diagnostic fields)."""
    if candidate.get("asset_type") != "equity":
        return None
    label = candidate.get("confidence_label")
    band = {"high_confidence_evidence": "STRONG", "moderate_confidence_evidence": "PARTIAL"}.get(label)
    if band is None:
        return None
    # Only BUY-evidence stocks pass the Stage 13A gate, so action is BUY by construction.
    return {"action": "BUY", "evidence_band": band}


def _policy_role_for_etf(reason_codes: list[str]) -> str | None:
    if "etf_floor_not_met" in reason_codes:
        return "Fills the 40% ETF allocation floor"
    if "core_etf_preference" in reason_codes:
        return "Core broad-market ETF under the preference order"
    for code in reason_codes:
        if code.endswith("_group_underweight"):
            return "Brings an underweight ETF group toward its target"
    return None


def build_plan_explanations(diagnostic: dict[str, Any]) -> dict[str, Any]:
    """Explain every selected and blocked result in plain English.

    Pure mapping over the diagnostic's existing per-ticker gate fields.
    Buckets: selected / evidence_eligible_policy_blocked / evidence_blocked /
    concentration_blocked / group_cap_blocked / etf_floor_driven (as a role on
    selected ETFs) / stale_price_blocked / missing_truth_blocked /
    below_minimum_trade / max_positions_reached.
    """
    inputs = diagnostic.get("input", {})
    cash_to_deploy = inputs.get("cash_to_deploy") or 0.0
    min_trade = inputs.get("min_trade_amount")
    max_positions = inputs.get("max_positions")
    cash_plan = diagnostic.get("cash_plan", {})
    stock_candidates = diagnostic.get("stock_candidates", {})
    ticker_gaps = (diagnostic.get("target_vs_current") or {}).get("by_ticker", {}) or {}
    truth = diagnostic.get("truth_dependency", {})
    candidates = diagnostic.get("next_buy_candidates", []) or []

    selected: list[dict] = []
    selected_tickers: set[str] = set()
    for c in candidates:
        reason_codes = list(c.get("reason_codes") or [])
        amount = round(c.get("dollar_amount", 0.0), 2)
        entry = {
            "ticker": c.get("ticker"),
            "asset_type": c.get("asset_type", "unknown"),
            "amount": amount,
            "percent_of_deployable_cash": round(100.0 * amount / cash_to_deploy, 2) if cash_to_deploy else None,
            "reasons": [_reason_text(code) for code in reason_codes] or ["Below its target allocation weight"],
            "evidence": _evidence_summary_for_candidate(c),
            "policy_role": _policy_role_for_etf(reason_codes) if c.get("asset_type") != "equity" else None,
            "raw_codes": reason_codes,
        }
        selected.append(entry)
        if entry["ticker"]:
            selected_tickers.add(entry["ticker"])

    not_selected: list[dict] = []

    evidence_eligible_policy_blocked = set(
        stock_candidates.get("evidence_eligible_but_policy_blocked_tickers") or []
    )
    policy_block_codes = stock_candidates.get("policy_block_reason_codes") or {}

    for ticker, tg in sorted(ticker_gaps.items()):
        if ticker in selected_tickers:
            continue
        policy_code = tg.get("policy_ineligibility_reason")
        gate_codes = list(tg.get("evidence_gate_codes") or [])
        gate_passed = tg.get("evidence_gate_passed")
        is_stock = tg.get("group") == "individual_stock"

        if ticker in evidence_eligible_policy_blocked:
            code = policy_block_codes.get(ticker) or policy_code or "policy_blocked"
            not_selected.append({
                "ticker": ticker,
                "bucket": "evidence_eligible_policy_blocked",
                "plain_english": (
                    f"{ticker} passed Intel evidence but is blocked by policy: "
                    f"{_policy_block_text(code)}"
                ),
                "raw_codes": [code],
            })
            continue
        if is_stock and gate_passed is False and gate_codes:
            texts = [_EVIDENCE_GATE_TEXT.get(g, g.replace("_", " ").capitalize()) for g in gate_codes]
            not_selected.append({
                "ticker": ticker,
                "bucket": "evidence_blocked",
                "plain_english": f"{ticker} is not eligible: " + " ".join(texts),
                "raw_codes": gate_codes + ([policy_code] if policy_code else []),
            })
            continue
        if policy_code and policy_code in _PRICE_TRUTH_CODES:
            # Deferred to the dedicated stale/missing-price loops below —
            # they are the authoritative source for these two codes.
            continue
        if policy_code:
            not_selected.append({
                "ticker": ticker,
                "bucket": _policy_block_bucket(policy_code),
                "plain_english": f"{ticker}: {_policy_block_text(policy_code)}",
                "raw_codes": [policy_code],
            })
            continue
        if tg.get("eligible_for_buy") and (tg.get("gap_pct") or 0) > 0:
            # Eligible but received no dollars — the allocator stopped first.
            if max_positions is not None and cash_plan.get("allocation_count") == max_positions:
                bucket, text = "max_positions_reached", (
                    f"{ticker} is eligible but the plan already holds the maximum of "
                    f"{max_positions} positions."
                )
            else:
                bucket, text = "below_minimum_trade", (
                    f"{ticker} is eligible but the remaining cash is below the "
                    f"${min_trade:.0f} minimum trade size." if min_trade is not None
                    else f"{ticker} is eligible but remaining cash is below the minimum trade size."
                )
            not_selected.append({
                "ticker": ticker,
                "bucket": bucket,
                "plain_english": text,
                "raw_codes": [cash_plan.get("no_buy_reason")] if cash_plan.get("no_buy_reason") else [],
            })

    for ticker in truth.get("stale_price_tickers") or []:
        if ticker not in selected_tickers:
            not_selected.append({
                "ticker": ticker,
                "bucket": "stale_price_blocked",
                "plain_english": f"{ticker} has stale price data, so it cannot receive new cash.",
                "raw_codes": ["stale_price"],
            })
    for ticker in truth.get("missing_price_tickers") or []:
        if ticker not in selected_tickers:
            not_selected.append({
                "ticker": ticker,
                "bucket": "missing_truth_blocked",
                "plain_english": f"{ticker} is missing current price data, so it cannot receive new cash.",
                "raw_codes": ["missing_price"],
            })

    plan_notes: list[str] = []
    stock_status = stock_candidates.get("status")
    any_stock_selected = any(e.get("asset_type") == "equity" for e in selected)
    if selected and not any_stock_selected:
        if stock_status == "blocked_insufficient_evidence":
            plan_notes.append(
                "This plan is ETF-only because no individual stock passed Intel evidence "
                "freshness and confidence checks."
            )
        elif stock_status == "blocked_by_policy_caps":
            n = len(evidence_eligible_policy_blocked)
            if n:
                plan_notes.append(
                    f"This plan is ETF-only: {n} stock{'s' if n != 1 else ''} passed Intel "
                    "evidence, but the individual-stock sleeve is already above its policy target."
                )
            else:
                plan_notes.append(
                    "This plan is ETF-only because held stocks are at or above their "
                    "policy concentration caps."
                )
        elif stock_status == "no_stock_positions_held":
            plan_notes.append("This plan is ETF-only because the portfolio holds no individual stocks.")
    if any("etf_floor_not_met" in (c.get("reason_codes") or []) for c in candidates):
        plan_notes.append("Your ETF allocation is below its 40% floor, so ETF purchases come first.")
    no_buy_reason = cash_plan.get("no_buy_reason")
    if no_buy_reason == "min_trade_amount_not_met_for_any_candidate":
        plan_notes.append("No candidate could receive at least the minimum trade amount, so no buys are planned.")
    elif no_buy_reason == "no_eligible_buy_candidates":
        plan_notes.append("No holding is currently eligible for new cash under the policy and evidence gates.")

    return {"selected": selected, "not_selected": not_selected, "plan_notes": plan_notes}


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

    # Only a genuinely blocked truth (no computable portfolio value,
    # reconciliation beyond tolerance, or no total portfolio value to weight
    # against — see allocation_policy_v1.run_next_buy_policy_diagnostic's
    # can_run_policy gate) empties the plan. "degraded" already means the
    # diagnostic ran the full allocator against certified-enough truth, so a
    # stale/missing price on some OTHER (non-candidate) holding must never
    # erase an already-calculated, priced buy plan.
    candidates = [] if preview_status == "blocked" else (diagnostic.get("next_buy_candidates", []) or [])

    # Defense-in-depth (product-recovery Blocker 1): a ticker with a missing
    # OR stale price is never eligible to receive new cash — that gate now
    # lives canonically at the allocation-policy boundary
    # (`allocation_policy_v1._compute_gaps`'s `policy_ineligibility_reason`),
    # not here. This router never re-filters or recomputes the diagnostic's
    # own selection/cash-plan math. It DOES independently verify the
    # resulting invariant before preserving a degraded plan — no selected
    # candidate may itself appear in `missing_price_tickers`/
    # `stale_price_tickers` — and suppresses the plan entirely rather than
    # ever displaying unsafe dollar guidance if that invariant is violated
    # (e.g. by a stale/hand-built diagnostic payload or a future allocator
    # regression).
    unsafe_price_tickers = set(truth_dependency.get("missing_price_tickers") or []) | set(
        truth_dependency.get("stale_price_tickers") or []
    )
    invariant_violated = any(c.get("ticker") in unsafe_price_tickers for c in candidates)
    if invariant_violated:
        preview_status = "blocked"
        candidates = []

    planned_buys = _build_planned_buys(candidates)

    next_required_fix = verdict.get("next_required_fix")
    if preview_status == "ready" and trusted:
        next_required_fix = None
    if invariant_violated:
        next_required_fix = (
            "Safety check failed: a recommended ticker's price is not current. "
            "Plan suppressed pending price-truth repair."
        )

    return {
        "preview_version": PREVIEW_VERSION,
        "cash_to_deploy": diagnostic.get("input", {}).get("cash_to_deploy"),
        "generated_at": diagnostic.get("generated_at"),
        "trusted": trusted,
        "status": preview_status,
        "planned_buys": planned_buys,
        # Additive (consolidation): plain-English selected/blocked explanations
        # for the Advisor cash-plan section. Presentation-only mapping — the
        # Stage 12D keys above are unchanged. Mirrors the same candidates as
        # planned_buys above (empty only when blocked) so the cash-plan
        # section never shows real dollar buys with no matching explanation.
        "explanations": (
            build_plan_explanations({**diagnostic, "next_buy_candidates": []})
            if preview_status == "blocked"
            else build_plan_explanations(diagnostic)
        ),
        # When the defense-in-depth invariant fires, the cash-plan totals must
        # also reflect zero allocation — never show a nonzero allocated_cash
        # figure alongside an empty planned_buys list.
        "allocation_summary": (
            {
                "allocated_cash": 0.0,
                "unallocated_cash": (
                    diagnostic.get("input", {}).get("cash_to_deploy")
                    or cash_plan.get("cash_to_deploy", 0.0)
                ),
                "allocation_count": 0,
            }
            if invariant_violated
            else {
                "allocated_cash": cash_plan.get("allocated_cash", 0.0),
                "unallocated_cash": cash_plan.get("unallocated_cash", 0.0),
                "allocation_count": cash_plan.get("allocation_count", 0),
            }
        ),
        "data_freshness_status": truth_dependency.get("price_coverage_status", "unknown"),
        "caveats": _build_caveats(
            trusted,
            preview_status,
            truth_dependency.get("missing_price_tickers", []),
            truth_dependency.get("stale_price_tickers", []),
            diagnostic.get("stock_candidates", {}).get("status"),
        ),
        "next_required_fix": next_required_fix,
        "recommendations_trusted": False,
        "source_diagnostic_version": diagnostic.get("diagnostic_version", "unknown"),
    }


def _summarize_price_truth_repair(repair_result: dict[str, Any] | None) -> dict[str, Any]:
    """Plain-English-friendly summary of the pre-diagnostic price-truth repair.

    ``status``:
      * ``refreshed``   — repair ran and every stale/missing ticker it
                          attempted was fetched successfully (or there was
                          nothing to fetch).
      * ``partial``      — repair ran but at least one ticker's provider
                          fetch failed or was unsupported.
      * ``unavailable``  — the repair call itself could not be completed
                          (see current_price_truth_repair_v1's own contract:
                          it does not raise, so this is a defensive fallback).
    """
    if not repair_result:
        return {
            "status": "unavailable",
            "attempted": 0,
            "succeeded": 0,
            "written": 0,
            "unsupported": 0,
            "provider_errors": 0,
        }
    attempted = int(repair_result.get("attempted_fetch_count") or 0)
    succeeded = int(repair_result.get("successful_fetch_count") or 0)
    unsupported = int(repair_result.get("unsupported_count") or 0)
    provider_errors = int(repair_result.get("provider_error_count") or 0)
    status = "refreshed" if (attempted == 0 or succeeded == attempted) else "partial"
    return {
        "status": status,
        "attempted": attempted,
        "succeeded": succeeded,
        "written": int(repair_result.get("rows_written") or 0),
        "unsupported": unsupported,
        "provider_errors": provider_errors,
    }


@router.post("/preview")
async def paycheck_plan_preview(
    payload: PaycheckPlanPreviewRequest,
    user: AuthenticatedUser = Depends(_get_runtime_cert_user),
) -> dict:
    """Stage 12D — concise, frontend-safe paycheck plan preview.

    Wraps the Stage 12C next-buy-policy diagnostic. Reuses its allocation
    policy logic verbatim; performs no additional allocation math.

    Deploy Cash is an explicit, user-triggered action, so — unlike page-load
    reads — it is allowed to refresh price truth before computing the plan:
    it first runs the existing ``current_price_truth_repair_v1`` service
    (``dry_run=False``) for stale/missing open-position prices, then runs the
    allocation diagnostic against the refreshed ``price_history`` rows. The
    repair only ever writes to ``price_history`` (Yahoo Finance / CoinGecko;
    bounded concurrency) — no LLM calls, no recommendation/snapshot writes.

    Invariants:
    - No writes to any table other than price_history (via the repair step)
    - No LLM calls
    - No recommendation rows created
    - Cert-gated (X-Finance-Runtime-Cert-Secret required)
    - recommendations_trusted is always False
    """
    from ..services.allocation_policy_v1 import run_next_buy_policy_diagnostic
    from ..services.current_price_truth_repair_v1 import run_current_price_truth_repair

    db_client = get_supabase_client()

    repair_result: dict[str, Any] | None = None
    try:
        repair_result = await run_current_price_truth_repair(
            db_client=db_client, user_id=str(user.id), dry_run=False,
        )
    except Exception as exc:
        logger.warning(
            "paycheck_plan_preview price_truth_repair_failed user=%s error=%s",
            getattr(user, "email", "unknown"), exc,
        )

    # The allocation diagnostic must run AFTER the repair attempt above, not
    # before, so it reads whatever refreshed price_history rows the repair
    # just wrote.
    diagnostic = await run_next_buy_policy_diagnostic(
        db_client=db_client,
        user_id=str(user.id),
        cash_to_deploy=payload.cash_to_deploy,
        max_positions=payload.max_positions,
        min_trade_amount=payload.min_trade_amount,
    )

    preview = build_paycheck_plan_preview(diagnostic)
    preview["price_truth_repair"] = _summarize_price_truth_repair(repair_result)

    logger.info(
        "paycheck_plan_preview user=%s cash=%.2f status=%s trusted=%s buys=%d",
        getattr(user, "email", "unknown"),
        payload.cash_to_deploy,
        preview["status"],
        preview["trusted"],
        len(preview["planned_buys"]),
    )

    return preview
