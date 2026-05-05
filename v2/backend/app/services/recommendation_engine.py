"""Recommendation engine — Buy/Sell/Trim/Hold analysis.

Ported from v1 utils/rec_engine.py (v4) with improvements:
- Database-backed instead of in-memory
- Supports persistence and resolution tracking
- Enriched with live prices from the concurrent price engine
- Async-native for non-blocking operation
"""

from __future__ import annotations

import json
import logging
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

from ..database import get_supabase_client
from .http_retry import run_with_retry_sync
from .reasoning_contract import CANONICAL_REASONING_KEYS, normalize_reasoning_payload
from .intelligence.thesis_plain_english import build_thesis_plain_english
from .intelligence.reasoning_v2_plain_english import (
    build_intel_read,
    build_posture_reason,
    is_safe_for_insufficient_data,
)
from .agent_run_status import (
    ACTIVE_RUN_STATUSES,
    assert_db_status,
    normalize_run_status,
)
from ..models.recommendation import (
    AgentInsight,
    AgentRunStatus,
    DecisionLogCreate,
    DecisionLogEntry,
    InsightCard,
    RecommendationResolve,
    StrategyPerformance,
)


# ── Classification constants (from v1) ───────────────────────────────────────

INCOME_FOREVER = {"VYM", "SCHD"}
DCA_ALWAYS = {"VOO", "QQQ", "VTI"}

ACTION_COLORS = {
    "SELL": "red",
    "BUY": "green",
    "TRIM": "orange",
    "HOLD": "blue",
    "REVIEW": "purple",
}


def _normalize_ticker_lookup_key(ticker: Any) -> str:
    """Normalize ticker symbols for tolerant scorecard lookups.

    Keeps user-facing symbols untouched; used only for backend key matching.
    Examples that should match: BRK-B / brk.b / brk b.
    """
    if ticker is None:
        return ""
    return "".join(ch for ch in str(ticker).upper() if ch.isalnum())


def _resolve_thesis_scorecard_for_ticker(
    thesis_map: Any,
    ticker: str,
) -> Optional[dict]:
    """Return best-match thesis_v2 scorecard for a card ticker, if any."""
    if not isinstance(thesis_map, dict) or not thesis_map:
        return None
    direct = thesis_map.get(ticker)
    if isinstance(direct, dict) and direct:
        return direct
    normalized_ticker = _normalize_ticker_lookup_key(ticker)
    if not normalized_ticker:
        return None
    for key, value in thesis_map.items():
        if not (isinstance(value, dict) and value):
            continue
        if _normalize_ticker_lookup_key(key) == normalized_ticker:
            return value
    return None


def _build_thesis_fields_for_card(
    *,
    ticker: str,
    run_id: Any,
    run_lookup: dict[str, dict],
    fallback_run_id: Optional[str] = None,
) -> tuple[Optional[dict], Optional[dict], str]:
    """Resolve thesis_v2 + thesis_plain_english payloads for one card.

    Tries the card's own agent_run_id first. When that run is absent from
    run_lookup or lacks _thesis_v2, falls back to fallback_run_id (the
    latest completed run that has _thesis_v2). This makes the live contract
    resilient to stale run binding, _persist failures, and the small timing
    window between _persist.finally and _update_run writing _thesis_v2.

    Returns (thesis_v2, thesis_plain_english, diagnostic_code).
    Diagnostic codes:
      attached                — primary run used, exact/normalized match
      attached_via_latest_run — fallback run used
      translation_failed      — scorecard found but translator raised
      no_run_id               — card has no agent_run_id
      run_not_found           — agent_run_id not in run_lookup
      allocation_missing_or_invalid — allocation column missing/not a dict
      thesis_map_missing      — _thesis_v2 absent or empty in allocation
      ticker_not_in_thesis_map — no scorecard for this ticker in _thesis_v2
    """
    def _try_run(rid: Any, diag_suffix: str = "") -> tuple[Optional[dict], Optional[dict], str]:
        rid_str = str(rid or "")
        if not rid_str:
            return None, None, "no_run_id"
        run_row = run_lookup.get(rid_str)
        if not run_row:
            return None, None, "run_not_found"
        allocation = run_row.get("allocation")
        if not isinstance(allocation, dict):
            return None, None, "allocation_missing_or_invalid"
        thesis_map = allocation.get("_thesis_v2")
        if not isinstance(thesis_map, dict) or not thesis_map:
            return None, None, "thesis_map_missing"
        scorecard = _resolve_thesis_scorecard_for_ticker(thesis_map, ticker)
        if not (isinstance(scorecard, dict) and scorecard):
            return None, None, "ticker_not_in_thesis_map"
        try:
            return scorecard, build_thesis_plain_english(scorecard), f"attached{diag_suffix}"
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "thesis_plain_english generation skipped ticker=%s run_id=%s err=%s",
                ticker, rid_str, exc,
            )
            return scorecard, None, f"translation_failed{diag_suffix}"

    run_id_str = str(run_id or "")
    primary = _try_run(run_id)
    # If primary succeeded or failed for a terminal reason (no run_id, ticker
    # missing), return immediately — fallback cannot help.
    _TERMINAL_DIAGS = {"attached", "translation_failed", "no_run_id", "ticker_not_in_thesis_map"}
    if primary[2] in _TERMINAL_DIAGS:
        return primary

    # Primary run is missing from run_lookup or has no _thesis_v2.
    # Try the latest completed run that has _thesis_v2 as a fallback.
    if fallback_run_id and str(fallback_run_id) != run_id_str:
        fb = _try_run(fallback_run_id, "_via_latest_run")
        if fb[2].startswith("attached") or fb[2].startswith("translation_failed"):
            logger.info(
                "thesis.fallback_used ticker=%s card_run=%s primary_diag=%s "
                "fallback_run=%s fallback_diag=%s",
                ticker, run_id_str, primary[2], fallback_run_id, fb[2],
            )
            return fb

    return primary

def _build_intel_read_for_card(
    *,
    ticker: str,
    run_id: Any,
    run_lookup: dict[str, dict],
    fallback_run_id: Optional[str] = None,
) -> tuple[Optional[dict], bool]:
    """Resolve intel_read plain-English projection from _reasoning_v2 for one card.

    Reads allocation["_reasoning_v2"][ticker] from the card's own agent_run_id run.
    When that run is absent from run_lookup or lacks _reasoning_v2 entirely, falls
    back to fallback_run_id (latest completed run with _reasoning_v2). Fallback is
    NOT used when the primary run has _reasoning_v2 but the ticker is simply absent
    (caller should treat that as genuinely missing data).
    Returns (intel_read_or_None, is_from_primary_run) — is_from_primary_run=True
    when the data came from the same run as the recommendation (not the fallback).
    Returns (None, False) safely in all failure cases.
    """
    def _try_run(rid: Any) -> tuple[Optional[dict], bool]:
        """Returns (intel_read_or_None, had_reasoning_v2_map).

        had_reasoning_v2_map=True means the run has a _reasoning_v2 map (even if
        this ticker is absent from it). Used to decide whether the fallback is
        appropriate: fallback only when the primary run lacks the map entirely.
        """
        rid_s = str(rid or "")
        if not rid_s:
            return None, False
        row = run_lookup.get(rid_s)
        if not row:
            return None, False
        alloc = row.get("allocation")
        if not isinstance(alloc, dict):
            return None, False
        rmap = alloc.get("_reasoning_v2")
        if not isinstance(rmap, dict) or not rmap:
            return None, False
        ticker_up = str(ticker).strip().upper()
        r2 = rmap.get(ticker_up) or rmap.get(ticker)
        if not isinstance(r2, dict):
            return None, True  # map exists, ticker absent — do not fallback
        try:
            return build_intel_read(r2), True
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "intel_read generation skipped ticker=%s run_id=%s err=%s",
                ticker, rid_s, exc,
            )
            return None, True

    result, had_r2_map = _try_run(run_id)
    if result is not None:
        return result, True  # from primary run
    # Fallback only when primary run had no _reasoning_v2 map at all.
    if had_r2_map:
        return None, True  # primary run owned the map; ticker just absent
    run_id_str = str(run_id or "")
    fb_str = str(fallback_run_id or "")
    if fb_str and fb_str != run_id_str:
        fallback_result, _ = _try_run(fallback_run_id)
        return fallback_result, False  # from fallback run
    return None, False


# DRIP yield estimates (annual %, approximate 2026 values)
DRIP_YIELD: dict[str, float] = {
    "VYM": 2.8, "SCHD": 3.5, "BND": 3.2, "VWO": 2.1, "VXUS": 1.8,
    "VEA": 1.9, "XLE": 3.0, "VTV": 2.2, "VUG": 0.3, "SPY": 1.2,
    "VGT": 0.4, "VHT": 1.4, "VIS": 1.3, "VTI": 1.3, "VOO": 1.2,
    "QQQ": 0.5, "GLD": 0.0, "QCOM": 2.1, "AAPL": 0.5,
    "MSFT": 0.7, "META": 0.4, "COST": 0.7, "WMT": 0.9, "CRM": 0.0,
    "GOOGL": 0.3, "TSM": 1.4, "AMD": 0.0, "NVDA": 0.1, "NFLX": 0.0,
    "BRK-B": 0.0, "RDDT": 0.0, "ALK": 0.0, "SNOW": 0.0, "CAVA": 0.0,
    "RIVN": 0.0, "BMWYY": 4.5, "BTC": 0.0, "XRP": 0.0, "KLAR": 0.0,
    "BLSH": 0.0, "STUB": 0.0,
}

# Aggregate endpoint short-lived cache + in-flight dedupe.
_AGGREGATE_CACHE_TTL_S = 20
_aggregate_cache: dict[str, tuple[datetime, list[InsightCard]]] = {}
_aggregate_inflight: dict[str, tuple[int, asyncio.Task[list[InsightCard]]]] = {}
_aggregate_generation: dict[str, int] = {}
_aggregate_lock = asyncio.Lock()


def invalidate_recommendations_aggregate_cache(
    user_id: UUID | str,
    *,
    reason: str = "mutation",
) -> None:
    """Drop per-user aggregate cache state so next read is fresh.

    Important: never cancel in-flight aggregate tasks. Reads may already be
    awaiting the shared task, and cancelling it propagates ``CancelledError``
    across unrelated request lifecycles.
    """
    key = str(user_id)
    prev_generation = _aggregate_generation.get(key, 0)
    _aggregate_generation[key] = prev_generation + 1
    _aggregate_cache.pop(key, None)
    # Clear the pointer so new readers can start a fresh compute. Existing
    # task continues safely for already-attached readers.
    _aggregate_inflight.pop(key, None)
    logger.info(
        "recommendations.aggregate.invalidated user_id=%s reason=%s generation=%d->%d",
        key,
        reason,
        prev_generation,
        _aggregate_generation[key],
    )


# ── Rec generation (ported from v1 generate_rec) ────────────────────────────

@dataclass
class RecResult:
    """Internal recommendation result before DB persistence."""
    action_label: str      # Human-readable label with emoji
    action: str            # Normalized: BUY, SELL, TRIM, HOLD, REVIEW
    detail: str
    color: str
    urgency: int
    tax_note: str = ""
    drip_note: str = ""


def _classify_action(label: str) -> str:
    """Map emoji action labels to normalized action types."""
    label_upper = label.upper()
    if any(k in label_upper for k in ("SELL", "🔴")):
        return "SELL"
    if any(k in label_upper for k in ("BUY", "ACCUMULATE", "DCA", "🟢", "🔥", "📈")):
        return "BUY"
    if any(k in label_upper for k in ("TRIM", "✂")):
        return "TRIM"
    if any(k in label_upper for k in ("REVIEW", "ALERT", "STOP-LOSS", "🚨")):
        return "REVIEW"
    return "HOLD"


def _tax_note(lt_ready: bool, lt_date: str, pct_gain: float) -> str:
    """Generate a tax-awareness note."""
    if lt_ready:
        if pct_gain > 20:
            tax_saved = pct_gain * 0.17
            return f"LT eligible — save ~{tax_saved:.0f}% vs ST rate"
        return "LT eligible — sell triggers 15-20% cap gains rate"
    return f"ST status until {lt_date} — selling now triggers 37% ordinary income tax"


def _drip_note(
    ticker: str, drip_shares: float, drip_cost: float,
    current_price: Optional[float],
) -> str:
    """Show DRIP compound growth value."""
    if not drip_shares or not current_price:
        return ""
    drip_val = drip_shares * current_price
    drip_gain = drip_val - drip_cost if drip_cost else drip_val
    yield_pct = DRIP_YIELD.get(ticker, 0)
    note = (
        f"DRIP: {drip_shares:.4f} free shares worth ${drip_val:.2f}"
        f" (${drip_gain:+.2f} gain)"
    )
    if yield_pct:
        note += f" · {yield_pct:.1f}% annual yield"
    return note


def generate_rec(
    cat: str,
    ticker: str,
    cost: float,
    target: Optional[float],
    bear: Optional[float],
    bull: Optional[float],
    lt_ready: bool,
    lt_date: str,
    price: Optional[float],
    drip_shares: float = 0.0,
    drip_cost: float = 0.0,
    divs_received: float = 0.0,
) -> RecResult:
    """Generate a recommendation for a single position.

    Full port of v1 rec_engine.py generate_rec() with all decision branches.
    """

    def _make(label: str, detail: str, color: str, urgency: int,
              tax: str = "", drip: str = "") -> RecResult:
        return RecResult(
            action_label=label,
            action=_classify_action(label),
            detail=detail, color=color, urgency=urgency,
            tax_note=tax, drip_note=drip,
        )

    # ── No price yet ─────────────────────────────────────────────────────
    if not price:
        if cat == "SELL":
            label = "SELL NOW" if lt_ready else f"WAIT — SELL {lt_date}"
            return _make(label, "Sell and consolidate into target ETF", "red", 3)
        return _make("HOLD", "Awaiting live price — tap Refresh", "gray", 0)

    # ── No target (SELL positions) ────────────────────────────────────────
    if not target:
        if cat == "SELL":
            label = "SELL NOW — LT eligible" if lt_ready else f"WAIT — SELL {lt_date}"
            tax = _tax_note(lt_ready, lt_date, (price - cost) / cost * 100 if cost else 0)
            return _make(label, "Consolidate into target ETF per plan", "red", 3, tax)
        return _make("HOLD", "No analyst target set", "gray", 0)

    pct = (price - cost) / cost * 100 if cost else 0
    upside = (target - price) / price * 100 if price else 0
    declining = target < cost
    yield_pct = DRIP_YIELD.get(ticker, 0)
    drip_n = _drip_note(ticker, drip_shares, drip_cost, price)
    tax_n = _tax_note(lt_ready, lt_date, pct)

    # ── Income ETFs — never sell ─────────────────────────────────────────
    if ticker in INCOME_FOREVER:
        annual_income = price * (yield_pct / 100) * (drip_shares or 1)
        return _make(
            "HOLD FOREVER — DRIP on",
            f"Compound income machine. Yield: {yield_pct:.1f}%. "
            f"Est. annual income: ${annual_income:.2f}. Never sell.",
            "purple", 0, "", drip_n,
        )

    # ── Core index ETFs — always DCA ────────────────────────────────────
    if ticker in DCA_ALWAYS:
        return _make(
            "DCA ALWAYS",
            f"Core index — add every biweekly deposit. Never sell. {drip_n}",
            "green", 0, "", drip_n,
        )

    # ── SELL-flagged positions ───────────────────────────────────────────
    if cat == "SELL":
        label = "SELL NOW — LT eligible" if lt_ready else f"WAIT — SELL {lt_date}"
        return _make(label, f"Consolidate into target ETF. {tax_n}", "red", 3, tax_n)

    # ── Bear proximity — highest priority for non-crypto ─────────────────
    if bear and price < bear * 1.10 and cat != "Crypto":
        return _make(
            "STOP-LOSS ALERT",
            f"Price ${price:.2f} within 10% of bear case ${bear:,.0f}. "
            f"Review position immediately. {tax_n}",
            "red", 4, tax_n, drip_n,
        )

    # ── Crypto special rules ─────────────────────────────────────────────
    if cat == "Crypto":
        if upside > 25:
            return _make(
                "ACCUMULATE",
                f"{upside:.0f}% upside to target ${target:,.0f}. Long-term hold.",
                "green", 3,
            )
        if upside < -20:
            return _make(
                "TRIM 15%",
                f"{abs(upside):.0f}% above target. Take some off. LT rate applies.",
                "orange", 2, tax_n,
            )
        return _make(
            "HOLD",
            f"{upside:.0f}% to target ${target:,.0f}. Hold position.",
            "blue", 1,
        )

    # ── Declining thesis (analyst target < cost) ─────────────────────────
    if declining:
        if upside > 20:
            return _make(
                "ACCUMULATE",
                f"{upside:.0f}% to analyst target (below cost — declining investment case). {drip_n}",
                "gold", 2, tax_n, drip_n,
            )
        if 5 >= upside > -10:
            if lt_ready:
                return _make(
                    "TRIM 20% (LT)",
                    f"At analyst target. Take partial profits at LT rate. {tax_n}",
                    "orange", 2, tax_n, drip_n,
                )
            return _make(
                "HOLD (ST)",
                f"Near target — wait for LT: {lt_date}. Avoid 37% tax. {tax_n}",
                "gold", 1, tax_n, drip_n,
            )
        if upside <= -10:
            if lt_ready:
                return _make(
                    "TRIM 25% (LT)",
                    f"Above analyst target. Lock gains at LT rate. {tax_n}",
                    "orange", 2, tax_n, drip_n,
                )
            return _make(
                "HOLD (ST)",
                f"Above target — hold until {lt_date} for LT rate. {tax_n}",
                "gold", 1, tax_n, drip_n,
            )
        return _make(
            "HOLD",
            f"Declining investment case — monitor analyst revisions. {drip_n}",
            "gray", 0, tax_n, drip_n,
        )

    # ── Normal thesis — dip buying ───────────────────────────────────────
    if pct < -20 and upside > 20:
        return _make(
            "STRONG BUY",
            f"Down {abs(pct):.0f}% from cost with {upside:.0f}% to target! "
            f"Maximum opportunity. {drip_n}",
            "green", 4, tax_n, drip_n,
        )

    if pct < -15 and upside > 15:
        return _make(
            "BUY THE DIP",
            f"Down {abs(pct):.0f}% from cost. {upside:.0f}% upside. {drip_n}",
            "green", 3, tax_n, drip_n,
        )

    # ── Standard upside zones ────────────────────────────────────────────
    if upside > 40:
        return _make(
            "ACCUMULATE",
            f"{upside:.0f}% upside — add aggressively on any weakness. {drip_n}",
            "green", 3, tax_n, drip_n,
        )

    if upside > 20:
        if yield_pct > 2.0:
            return _make(
                "ACCUMULATE + DRIP",
                f"{upside:.0f}% price upside + {yield_pct:.1f}% dividend yield. {drip_n}",
                "green", 3, tax_n, drip_n,
            )
        return _make(
            "ACCUMULATE",
            f"{upside:.0f}% upside — buy on weakness. {drip_n}",
            "green", 2, tax_n, drip_n,
        )

    # ── At / above target ────────────────────────────────────────────────
    if 5 >= upside > -10:
        if lt_ready:
            return _make(
                "TRIM 20% (LT)",
                f"At analyst target ${target:,.0f}. Sell 20% at LT rate. {tax_n}",
                "orange", 2, tax_n, drip_n,
            )
        return _make(
            "HOLD (ST)",
            f"Near target — hold until {lt_date} for LT cap-gains rate. {tax_n}",
            "gold", 1, tax_n, drip_n,
        )

    if upside <= -10:
        if lt_ready:
            return _make(
                "TRIM 25% (LT)",
                f"{abs(upside):.0f}% above target. Trim 25% at LT rate. {tax_n}",
                "orange", 2, tax_n, drip_n,
            )
        return _make(
            "HOLD (ST)",
            f"{abs(upside):.0f}% above target — wait for LT: {lt_date}. {tax_n}",
            "gold", 1, tax_n, drip_n,
        )

    # ── IPO lockup ───────────────────────────────────────────────────────
    if cat == "IPO" and not lt_ready:
        return _make(
            "HOLD (IPO)",
            f"IPO lockup — hold until LT: {lt_date}. "
            f"Target ${target:,.0f} ({upside:.0f}% upside).",
            "blue", 0, tax_n,
        )

    # ── Normal hold zone (10-20% upside) ─────────────────────────────────
    if upside > 10:
        return _make(
            "HOLD",
            f"{upside:.0f}% upside to target ${target:,.0f}. {drip_n}",
            "blue", 1, tax_n, drip_n,
        )

    # ── Default ──────────────────────────────────────────────────────────
    return _make(
        "HOLD",
        f"Monitoring — {upside:.0f}% to target ${target:,.0f}. {drip_n}",
        "gray", 0, tax_n, drip_n,
    )


# ── Portfolio advisor (LLM-driven) ───────────────────────────────────────────

async def portfolio_advisor(
    portfolio_positions: list[dict[str, Any]],
    macro_summary: str,
    api_key: str,
) -> dict[str, Any]:
    """Generate comprehensive portfolio advice via LLM.

    Takes pre-computed portfolio data and macro context (no fetching inside),
    calls the LLM with the advisor prompt, returns strict JSON format.

    Args:
        portfolio_positions: List of position dicts with ticker, shares, what_changed, etc.
        macro_summary: Current macro context (e.g., inflation, Fed outlook, market sentiment).
        api_key: Anthropic API key for the LLM call.

    Returns:
        Dict with keys: summary, risks, opportunities, cards, top_buys.
        Falls back to safe defaults if LLM fails.
    """
    from .agents.llm import LLMClient

    # Guard: never call the LLM with an empty portfolio
    if not portfolio_positions:
        logger.error("portfolio_advisor: portfolio_positions is empty — skipping LLM call")
        return {
            "summary": "No portfolio positions to analyze.",
            "risks": [],
            "opportunities": [],
            "cards": [],
            "top_buys": [],
        }

    # ── System prompt: role + output schema (no runtime data) ────────────
    system_prompt = """You are a personal portfolio advisor for a long-term retail investor.

Your goal: Provide clear, simple, and actionable investment guidance.

You will receive a JSON object with two keys:
- "macro_summary": string describing current portfolio context
- "portfolio_positions": array of position objects

For each ticker, decide: BUY / HOLD / SELL with high/medium/low confidence.
- SELL = weakening outlook or better opportunities exist
- BUY = strong outlook or worth adding more
- HOLD = neutral or unclear

Keep explanations simple (2 sentences max per ticker).

Return ONLY this valid JSON structure, no other text:
{
  "summary": "2-3 sentence overview in simple language",
  "risks": ["risk1", "risk2"],
  "opportunities": ["opportunity1", "opportunity2"],
  "cards": [
    {
      "ticker": "AAPL",
      "action": "BUY | HOLD | SELL",
      "confidence": "high | medium | low",
      "reasoning": "2 sentences max"
    }
  ],
  "top_buys": ["ticker1", "ticker2", "ticker3"]
}"""

    # ── User message: runtime data as JSON (no template substitution) ─────
    user_payload = json.dumps({
        "macro_summary": macro_summary,
        "portfolio_positions": portfolio_positions,
    })

    # ── Call LLM with fallback handling ──────────────────────────────────
    llm = LLMClient(api_key=api_key)
    result = await llm.ask_json(
        system=system_prompt,
        user=user_payload,
        max_tokens=1024,
    )

    # ── Ensure result matches expected schema ────────────────────────────
    if not result:
        result = {}

    # Safe defaults for missing fields
    return {
        "summary": result.get("summary", "Unable to generate portfolio summary at this time."),
        "risks": result.get("risks", []),
        "opportunities": result.get("opportunities", []),
        "cards": result.get("cards", []),
        "top_buys": result.get("top_buys", []),
    }


# ── Data-quality UX helpers ──────────────────────────────────────────────────


def _derive_confidence_score(conviction: Optional[float]) -> float:
    """Map a conviction score to a data-confidence proxy (0–1).

    Conviction is capped by completeness at write-time, so its magnitude is a
    reliable proxy for how much data backed the recommendation.
    """
    if conviction is None:
        return 0.3
    abs_c = abs(conviction)
    if abs_c >= 0.6:
        return 0.85
    if abs_c >= 0.3:
        return 0.55
    return 0.3


def _derive_quality_label(score: float) -> str:
    if score >= 0.7:
        return "HIGH"
    if score >= 0.45:
        return "MEDIUM"
    return "LOW"


def _derive_sentiment_label(
    rec: dict[str, Any], analyst_verdict: Optional[dict[str, Any]] = None
) -> str:
    """Return Positive/Mixed/Negative/Unavailable from available evidence."""
    if isinstance(analyst_verdict, dict):
        text = " ".join(
            [*(analyst_verdict.get("key_drivers") or []), *(analyst_verdict.get("risks") or [])]
        ).lower()
        if any(k in text for k in ("beat", "growth", "improving", "positive", "strong")):
            return "Positive"
        if any(k in text for k in ("downgrade", "weak", "decline", "negative", "risk")):
            return "Negative"
        if text:
            return "Mixed"

    score = rec.get("sentiment_score")
    try:
        score_f = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_f = None
    if score_f is None:
        return "Unavailable"
    if score_f >= 0.2:
        return "Positive"
    if score_f <= -0.2:
        return "Negative"
    return "Mixed"


def _derive_quality_label_from_evidence(
    *,
    confidence_score: float,
    reason_tags: list[str],
    analyst_fields: dict[str, Any],
    sentiment_label: str,
    rec: dict[str, Any],
) -> str:
    """Map evidence coverage to HIGH/MEDIUM/LOW more faithfully than conviction-only."""
    evidence = 0
    if confidence_score >= 0.7:
        evidence += 2
    elif confidence_score >= 0.45:
        evidence += 1
    if analyst_fields.get("drivers"):
        evidence += 1
    if rec.get("investment_thesis"):
        evidence += 1
    if rec.get("technical_signal"):
        evidence += 1
    if sentiment_label != "Unavailable":
        evidence += 1
    if rec.get("rationale") and "sec filing" in str(rec.get("rationale")).lower():
        evidence += 1
    if "fallback_used" in reason_tags:
        evidence -= 1
    if "low_data" in reason_tags:
        evidence -= 1

    if evidence >= 5:
        return "HIGH"
    if evidence >= 3:
        return "MEDIUM"
    return "LOW"


def _derive_reason_tags(rec: dict) -> list[str]:
    """Extract UX reason tags from existing recommendation fields — no extra queries."""
    tags: list[str] = []
    thesis = (rec.get("investment_thesis") or "").lower()
    detail = (rec.get("detail") or "").lower()

    if any(kw in thesis for kw in ("fallback", "low data confidence", "deterministic")):
        tags.append("fallback_used")
    if any(kw in thesis for kw in ("low confidence", "low data", "watchlist", "missing")):
        tags.append("low_data")
    if any(kw in detail for kw in ("unavailable", "api", "limited data")):
        tags.append("api_failure")
    if not tags:
        conviction = rec.get("conviction_score")
        try:
            if conviction is not None and abs(float(conviction)) < 0.3:
                tags.append("low_conviction")
        except (TypeError, ValueError):
            pass
    return tags


def _extract_analyst_card_fields(
    verdict: Optional[dict], *, analyst_confidence: Optional[float],
) -> dict[str, Any]:
    """Normalise an analyst_verdict JSONB blob into InsightCard fields.

    Tolerant of missing keys / malformed blobs — returns ``None`` for
    every field when the verdict is unusable so the card degrades to
    the legacy (pre-Phase-3) rendering.
    """
    if not isinstance(verdict, dict):
        return {
            "action": None, "conviction": None, "confidence": analyst_confidence,
            "drivers": None, "risks": None, "used_fallback": None,
        }
    action = verdict.get("action")
    conv = verdict.get("conviction")
    conf = verdict.get("confidence")
    drivers = verdict.get("key_drivers")
    risks = verdict.get("risks")
    try:
        conv_f = float(conv) if conv is not None else None
    except (TypeError, ValueError):
        conv_f = None
    try:
        conf_f = float(conf) if conf is not None else analyst_confidence
    except (TypeError, ValueError):
        conf_f = analyst_confidence
    return {
        "action": str(action) if isinstance(action, str) else None,
        "conviction": conv_f,
        "confidence": conf_f,
        "drivers": [str(d) for d in drivers if isinstance(d, str)][:3]
                   if isinstance(drivers, list) else None,
        "risks": [str(r) for r in risks if isinstance(r, str)][:2]
                 if isinstance(risks, list) else None,
        "used_fallback": bool(verdict.get("used_fallback", False)),
    }


def _resolve_card_analysis_source(
    *,
    analyst_verdict: Optional[dict[str, Any]],
    is_fallback: bool,
) -> tuple[str, bool]:
    """Return ``(analysis_source, reused_cached)`` for card serialization.

    Cache reuse is only valid when a verdict explicitly marks itself as
    ``analysis_source='cached_run'`` and is non-fallback. Fallback/template
    cards never qualify as a reusable cache hit.
    """
    explicit_source = None
    if isinstance(analyst_verdict, dict):
        source_raw = analyst_verdict.get("analysis_source")
        if isinstance(source_raw, str):
            explicit_source = source_raw.strip().lower()

    if explicit_source == "cached_run" and not is_fallback:
        return "cached_run", True
    if is_fallback:
        return "deterministic_fallback", False
    return "live_llm", False


STALE_RUN_MAX_AGE_SECONDS = 600

TICKER_SECTOR_MAP: dict[str, str] = {
    # Tech
    "AAPL": "Technology",
    "MSFT": "Technology",
    "NVDA": "Technology",
    "AMD": "Technology",
    "CRM": "Technology",
    "SNOW": "Technology",
    # Communication
    "GOOGL": "Communication Services",
    "META": "Communication Services",
    "NFLX": "Communication Services",
    "RDDT": "Communication Services",
    # Consumer
    "COST": "Consumer",
    "WMT": "Consumer",
    "CAVA": "Consumer",
    # Semis
    "QCOM": "Technology",
    "TSM": "Technology",
    # Financial
    "BRK-B": "Financials",
    # Industrial/Auto
    "ALK": "Industrials / Autos",
    "RIVN": "Industrials / Autos",
    "BMWYY": "Industrials / Autos",
    # ETF
    "VOO": "ETFs / Broad Market",
    "VTI": "ETFs / Broad Market",
    "SPY": "ETFs / Broad Market",
    "QQQ": "ETFs / Broad Market",
    "SCHD": "ETFs / Broad Market",
    "VYM": "ETFs / Broad Market",
    "VXUS": "ETFs / Broad Market",
    "VEA": "ETFs / Broad Market",
    "VWO": "ETFs / Broad Market",
    "BND": "Gold / Bonds / Defensive",
    # Alt
    "GLD": "Gold / Bonds / Defensive",
    "BTC": "Crypto",
    "XRP": "Crypto",
    # Speculative
    "KLAR": "Consumer",
    "BLSH": "Consumer",
    "STUB": "Consumer",
}

STRATEGY_MAP: dict[str, str] = {
    "VOO": "Core index ETFs",
    "VTI": "Core index ETFs",
    "SPY": "Core index ETFs",
    "QQQ": "Core index ETFs",
    "AAPL": "Mega-cap quality growth",
    "MSFT": "Mega-cap quality growth",
    "GOOGL": "Mega-cap quality growth",
    "META": "Mega-cap quality growth",
    "AMZN": "Mega-cap quality growth",
    "NVDA": "Semiconductors / AI infrastructure",
    "AMD": "Semiconductors / AI infrastructure",
    "TSM": "Semiconductors / AI infrastructure",
    "QCOM": "Semiconductors / AI infrastructure",
    "SCHD": "Dividend income",
    "VYM": "Dividend income",
    "VXUS": "International diversification",
    "VEA": "International diversification",
    "VWO": "International diversification",
    "BTC": "Crypto / alternatives",
    "XRP": "Crypto / alternatives",
    "GLD": "Crypto / alternatives",
    "KLAR": "Speculative / IPO / high volatility",
    "BLSH": "Speculative / IPO / high volatility",
    "STUB": "Speculative / IPO / high volatility",
    "RIVN": "Speculative / IPO / high volatility",
}

RISK_BUCKETS = [
    "Concentration risk",
    "Momentum breakdown risk",
    "Speculative risk",
    "Crypto volatility",
    "Single-stock risk",
    "Tax-sensitive trims",
    "Missing fundamental data",
]


def map_ticker_to_sector(ticker: str | None) -> str:
    if not ticker:
        return "Unknown"
    return TICKER_SECTOR_MAP.get(str(ticker).upper(), "Unknown")


def _normalize_action(action: str | None) -> str:
    raw = (action or "").strip().upper()
    if raw == "REDUCE":
        return "TRIM"
    if raw in {"BUY", "HOLD", "TRIM", "SELL"}:
        return raw
    return "HOLD"


# ── Intel posture system (v3) ─────────────────────────────────────────────────
# Deterministic advisor-facing posture buckets decoupled from broker-style
# BUY/HOLD/SELL action, which collapses all tickers into HOLD under insufficient_data.

# Core index ETFs and dividend/income ETFs: always DCA/contribution targets.
_INTEL_ADD_CANDIDATE_TICKERS: frozenset[str] = frozenset({
    "VOO", "VTI", "SPY", "QQQ", "SCHD", "VYM", "BND",
    "VGT", "VHT", "VIS", "VTV", "VUG", "VXUS", "VEA", "VWO", "XLE",
})

# Speculative, crypto, and IPO tickers: always elevated-risk posture.
_INTEL_RISK_WATCH_TICKERS: frozenset[str] = frozenset(
    {"BTC", "XRP", "RIVN", "KLAR", "BLSH", "STUB"}
)


def _derive_intel_posture(
    *,
    ticker: str,
    action: str,
    analyst_action: Optional[str],
    conviction_level: Optional[str],
    technical_signal: Optional[str],
    category: Optional[str],
    intel_read_dict: Optional[dict],
) -> str:
    """Derive investor-facing Intel posture bucket from safe structural signals.

    Deterministic — no IO, LLM calls, or raw metric keys.
    Returns one of: "Add Candidate" | "Watchlist" | "Review" | "Risk Watch" | "Trim Candidate"

    Rules (evaluated in priority order):
    1. TRIM/SELL action → Trim Candidate
    2. Core index / dividend ETFs → Add Candidate (DCA targets regardless of data coverage)
    3. Crypto / speculative tickers → Risk Watch
    4. Bearish technical signal → Risk Watch
    5. BUY action + sufficient data → Add Candidate
    6. MEDIUM+ conviction + sufficient data → Add Candidate
    7. Insufficient data + MEDIUM conviction → Review (some evidence, not yet actionable)
    8. Everything else → Watchlist
    """
    ticker_up = (ticker or "").upper()
    cat_low = (category or "").lower()
    tech = (technical_signal or "").upper()
    conviction = (conviction_level or "LOW").upper()

    insufficient = bool(intel_read_dict and intel_read_dict.get("insufficient_data"))

    # 1. Trim/Sell signal → Trim Candidate
    if action in {"TRIM", "SELL"}:
        return "Trim Candidate"
    if analyst_action and _normalize_action(analyst_action) in {"TRIM", "SELL"}:
        return "Trim Candidate"

    # 2. Core index ETFs / dividend ETFs → Add Candidate (DCA targets)
    if ticker_up in _INTEL_ADD_CANDIDATE_TICKERS or (
        "etf" in cat_low and ticker_up not in _INTEL_RISK_WATCH_TICKERS
    ):
        return "Add Candidate"

    # 3. Crypto / speculative → Risk Watch
    if (
        ticker_up in _INTEL_RISK_WATCH_TICKERS
        or "crypto" in cat_low
        or "speculative" in cat_low
        or "ipo" in cat_low
    ):
        return "Risk Watch"

    # 4. Bearish technical signal → Risk Watch
    if tech in {"SELL", "WEAK", "BEARISH"}:
        return "Risk Watch"

    # 5. BUY action + sufficient data → Add Candidate
    if not insufficient and action == "BUY":
        return "Add Candidate"

    # 5.5. BUY signal present but primary run's data is thin → Review
    # Separates "agent assessed BUY but coverage is insufficient" from
    # "no constructive signal at all". Prevents a genuine BUY assessment
    # from collapsing to Watchlist under insufficient_data.
    if insufficient and action == "BUY":
        return "Review"

    # 6. MEDIUM+ conviction + sufficient data → Add Candidate
    if not insufficient and conviction in {"HIGH", "MEDIUM"}:
        return "Add Candidate"

    # 7. Insufficient data + MEDIUM conviction → Review
    if insufficient and conviction == "MEDIUM":
        return "Review"

    # 8. Everything else → Watchlist
    return "Watchlist"


def _bucket_pct(count: int, total: int) -> float:
    return round((count / total) * 100.0, 1) if total else 0.0


def _first_text(values: list[str]) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def _classify_strategy(card: InsightCard) -> str:
    ticker = (card.ticker or "").upper()
    if ticker in STRATEGY_MAP:
        return STRATEGY_MAP[ticker]

    category = (card.category or "").lower()
    sector = (card.sector or map_ticker_to_sector(ticker) or "").lower()
    if "etf" in category or sector == "etfs / broad market":
        return "Core index ETFs"
    if ticker in {"AAPL", "MSFT", "GOOGL", "META", "AMZN"} or "technology" in sector:
        return "Mega-cap quality growth"
    if ticker in {"NVDA", "AMD", "TSM", "QCOM"} or "semi" in sector:
        return "Semiconductors / AI infrastructure"
    if ticker in {"SCHD", "VYM"} or "dividend" in category:
        return "Dividend income"
    if ticker in {"VXUS", "VEA", "VWO"} or "international" in category:
        return "International diversification"
    if ticker in {"BTC", "XRP"} or "crypto" in category:
        return "Crypto / alternatives"
    if ticker in {"RIVN", "KLAR", "BLSH", "STUB"} or "ipo" in category or "spec" in category:
        return "Speculative / IPO / high volatility"

    momentum = (card.technical_signal or "").upper()
    if momentum in {"SELL", "WEAK", "BEARISH"}:
        return "Turnaround or weak momentum"
    return "Mega-cap quality growth"


def _classify_sector(card: InsightCard) -> str:
    raw = (card.sector or map_ticker_to_sector(card.ticker) or card.category or "Unknown").strip().lower()
    if "technology" in raw or "semi" in raw:
        return "Technology"
    if "communication" in raw:
        return "Communication Services"
    if "consumer" in raw:
        return "Consumer"
    if "financial" in raw:
        return "Financials"
    if "industrial" in raw or "auto" in raw:
        return "Industrials / Autos"
    if "etf" in raw or "broad market" in raw or (card.category or "").lower() == "etf":
        return "ETFs / Broad Market"
    if "crypto" in raw:
        return "Crypto"
    if "bond" in raw or "gold" in raw or "defensive" in raw:
        return "Gold / Bonds / Defensive"
    return "Technology"


def _card_risk_labels(card: InsightCard, weight_pct: float) -> set[str]:
    labels: set[str] = set()
    ticker = (card.ticker or "").upper()
    action = _normalize_action(card.action)
    tech = (card.technical_signal or "").upper()
    quality = (card.data_quality_label or "").upper()
    risks = [r.lower() for r in (card.analyst_risks or card.main_risks or [])]

    if weight_pct >= 12:
        labels.add("Single-stock risk")
    if action in {"TRIM", "SELL"} and (card.tax_note or ""):
        labels.add("Tax-sensitive trims")
    if ticker in {"BTC", "XRP"} or "crypto" in (card.category or "").lower():
        labels.add("Crypto volatility")
    if ticker in {"RIVN", "KLAR", "BLSH", "STUB"} or "speculative" in (card.category or "").lower():
        labels.add("Speculative risk")
    if tech in {"SELL", "WEAK", "BEARISH"} or any("momentum" in r or "breakdown" in r for r in risks):
        labels.add("Momentum breakdown risk")
    if quality == "LOW" or bool(card.analyst_used_fallback) or any("missing" in r for r in risks):
        labels.add("Missing fundamental data")
    return labels


def build_portfolio_intel(cards: list[InsightCard], holdings: Optional[list[dict[str, Any]]] = None, run_metrics: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    total = len(cards)
    counts = {"BUY": 0, "HOLD": 0, "TRIM": 0, "SELL": 0}
    if total == 0:
        return {
            "quality": "LOW",
            "bias": "Neutral",
            "headline": "No active signals available.",
            "executive_summary": "Run agents to generate portfolio intelligence.",
            "action_counts": counts,
            "exposures": {"strategy_buckets": [], "sector_buckets": [], "risk_buckets": []},
            "top_opportunities": [],
            "top_risks": [],
            "trim_candidates": [],
            "deploy_suggestions": [],
            "what_changed": [],
            "watchlist": [],
        }

    per_card: list[dict[str, Any]] = []
    for c in cards:
        action = _normalize_action(c.action)
        counts[action] += 1
    for c in cards:
        weight_pct = _bucket_pct(1, total)
        per_card.append({
            "card": c,
            "ticker": c.ticker,
            "action": _normalize_action(c.action),
            "strategy": _classify_strategy(c),
            "sector": _classify_sector(c),
            "weight_pct": weight_pct,
            "confidence": float(c.analyst_confidence or c.confidence or 0.0),
            "conviction": float(c.analyst_conviction or c.conviction or c.conviction_score or 0.0),
            "thesis": _first_text([
                c.plain_language_explanation or "",
                c.thesis or "",
                c.reasoning_summary or "",
                c.summary or "",
                c.detail or "",
            ]),
            "risk_note": _first_text((c.analyst_risks or c.main_risks or ["Monitor position sizing and trend confirmation."])),
            "drivers": (c.analyst_drivers or c.key_drivers or [])[:3],
            "what_changed": [line for line in (c.what_changed or "").split("\\n") if line.strip()][:2],
            "risk_labels": _card_risk_labels(c, weight_pct),
        })

    def _bucketize(rows: list[dict[str, Any]], key: str, min_display_pct: float = 0.1) -> list[dict[str, Any]]:
        bucket_counts: dict[str, int] = {}
        bucket_tickers: dict[str, list[str]] = {}
        for row in rows:
            name = row[key]
            bucket_counts[name] = bucket_counts.get(name, 0) + 1
            bucket_tickers.setdefault(name, []).append(row["ticker"])
        out = []
        for name, n in sorted(bucket_counts.items(), key=lambda item: item[1], reverse=True):
            pct = _bucket_pct(n, total)
            if pct < min_display_pct:
                continue
            out.append({
                "name": name,
                "percentage": pct,
                "top_tickers": bucket_tickers[name][:3],
                "why_it_matters": f"{name} represents about {pct:.0f}% of current signals, which affects diversification and risk budgeting.",
            })
        return out

    strategy_buckets = _bucketize(per_card, "strategy")
    sector_buckets = _bucketize(per_card, "sector")

    risk_counts: dict[str, int] = {label: 0 for label in RISK_BUCKETS}
    risk_tickers: dict[str, list[str]] = {label: [] for label in RISK_BUCKETS}
    largest_sector = sector_buckets[0]["name"] if sector_buckets else ""
    largest_sector_pct = sector_buckets[0]["percentage"] if sector_buckets else 0.0
    if largest_sector_pct >= 35:
        risk_counts["Concentration risk"] += 1
        risk_tickers["Concentration risk"] = [r["ticker"] for r in per_card if r["sector"] == largest_sector][:3]
    for row in per_card:
        for label in row["risk_labels"]:
            risk_counts[label] = risk_counts.get(label, 0) + 1
            if row["ticker"] not in risk_tickers.setdefault(label, []):
                risk_tickers[label].append(row["ticker"])

    risk_buckets = []
    for label, n in sorted(risk_counts.items(), key=lambda item: item[1], reverse=True):
        if n <= 0:
            continue
        pct = _bucket_pct(n, total)
        risk_buckets.append({
            "name": label,
            "percentage": pct,
            "top_tickers": risk_tickers.get(label, [])[:3],
            "why_it_matters": f"{label} appears in {n} names and can affect drawdown control and deployment timing.",
        })

    enriched = sum(1 for c in cards if (c.analysis_source or "").lower() == "live_llm")
    high_quality = sum(1 for c in cards if (c.data_quality_label or "").upper() == "HIGH" and not bool(c.analyst_used_fallback))
    fallback = sum(1 for c in cards if bool(c.analyst_used_fallback) or (c.analysis_source or "").lower() == "deterministic_fallback")
    ratio = (enriched / total) if total else 0.0
    quality = "HIGH" if ratio >= 0.8 else ("MEDIUM" if ratio >= 0.5 else "LOW")

    bias = "Neutral"
    if counts["BUY"] >= max(1, counts["TRIM"] + counts["SELL"] + 1):
        bias = "Bullish"
    elif counts["TRIM"] + counts["SELL"] > counts["BUY"]:
        bias = "Defensive"

    buy_rows = sorted([r for r in per_card if r["action"] == "BUY"], key=lambda r: (r["confidence"], r["conviction"]), reverse=True)
    trim_rows = sorted([r for r in per_card if r["action"] in {"TRIM", "SELL"}], key=lambda r: (r["confidence"], r["conviction"]), reverse=True)

    def _suggested_use(row: dict[str, Any]) -> str:
        if row["confidence"] >= 0.7:
            return "add"
        if row["confidence"] >= 0.5:
            return "buy on weakness"
        return "watch"

    top_opportunities = [{
        "ticker": r["ticker"],
        "reason": _first_text(r["drivers"] or [r["thesis"]]),
        "confidence": round(r["confidence"], 2),
        "risk_note": r["risk_note"],
        "suggested_use": _suggested_use(r),
    } for r in buy_rows[:5]]

    trim_candidates = [{
        "ticker": r["ticker"],
        "why_trim": r["risk_note"] or "Trim to control concentration and redeploy to stronger buys.",
        "what_to_watch": _first_text(r["what_changed"] or ["Watch momentum and earnings revisions."]),
        "redirect_proceeds_to": [o["ticker"] for o in top_opportunities[:3]],
    } for r in trim_rows[:5]]

    top_risks = [{
        "label": b["name"],
        "tickers": b["top_tickers"],
        "note": b["why_it_matters"],
    } for b in risk_buckets[:5]]

    deploy_suggestions = []
    if top_opportunities:
        deploy_suggestions.append(f"Prioritize staged adds in {', '.join([o['ticker'] for o in top_opportunities[:3]])} instead of adding to the largest existing bucket.")
    if trim_candidates:
        deploy_suggestions.append(f"Use trim proceeds from {', '.join([t['ticker'] for t in trim_candidates[:3]])} to fund highest-conviction BUY ideas.")

    what_changed = []
    for row in per_card:
        for line in row["what_changed"]:
            what_changed.append({"ticker": row["ticker"], "change": line})
    if not what_changed:
        for row in per_card:
            if row["action"] in {"TRIM", "SELL"} and len(what_changed) < 5:
                what_changed.append({"ticker": row["ticker"], "change": "Action downgraded to risk-control posture; monitor momentum and business-case drift."})

    watchlist = []
    for row in per_card:
        if "Momentum breakdown risk" in row["risk_labels"] or "Missing fundamental data" in row["risk_labels"] or row["action"] in {"TRIM", "SELL"}:
            watchlist.append({
                "ticker": row["ticker"],
                "focus": row["risk_note"],
                "trigger": _first_text(row["what_changed"] or ["Review after earnings or a major price move."]),
            })
    watchlist = watchlist[:6]

    top_strategy = strategy_buckets[0] if strategy_buckets else {"name": "mixed allocation", "percentage": 0, "top_tickers": []}
    top_sector = sector_buckets[0] if sector_buckets else {"name": "mixed sectors", "percentage": 0, "top_tickers": []}
    buy_names = ", ".join([r["ticker"] for r in buy_rows[:3]]) or "selective names"
    risk_names = ", ".join([r["ticker"] for r in trim_rows[:3]]) or "higher-volatility names"
    headline = (
        f"Your portfolio is {bias.lower()} with concentration in {top_strategy['name']} and {top_sector['name']} "
        f"(~{top_strategy['percentage']:.0f}% / ~{top_sector['percentage']:.0f}% of signals). "
        f"Best opportunities right now are {buy_names}, while key risks cluster in {risk_names}. "
        f"Use {len(trim_candidates)} trims to fund {len(top_opportunities)} highest-conviction buys instead of adding to concentrated buckets."
    )
    executive_summary = (
        f"Actionable mix is {counts['BUY']} BUY, {counts['HOLD']} HOLD, {counts['TRIM']} TRIM, {counts['SELL']} SELL. "
        f"This means focus new money on diversified high-conviction buys and treat trims as funding sources, "
        f"especially where momentum or data quality has weakened."
    )

    return {
        "quality": quality,
        "bias": bias,
        "headline": headline,
        "executive_summary": executive_summary,
        "action_counts": counts,
        "exposures": {
            "strategy_buckets": strategy_buckets,
            "sector_buckets": sector_buckets,
            "risk_buckets": risk_buckets,
        },
        "top_opportunities": top_opportunities,
        "top_risks": top_risks,
        "trim_candidates": trim_candidates,
        "deploy_suggestions": deploy_suggestions,
        "what_changed": what_changed[:8],
        "watchlist": watchlist,
        "quality_breakdown": {
            "total_cards": total,
            "enriched": enriched,
            "high_quality": high_quality,
            "fallback": fallback,
        },
    }


def compute_portfolio_synthesis(cards: list[InsightCard]) -> dict[str, Any]:
    """Build deterministic portfolio intelligence for the Intel dashboard."""
    intel = build_portfolio_intel(cards)
    action_counts = intel.get("action_counts", {})
    strategy_buckets = intel.get("exposures", {}).get("strategy_buckets", [])
    sector_buckets = intel.get("exposures", {}).get("sector_buckets", [])
    risk_buckets = intel.get("exposures", {}).get("risk_buckets", [])

    return {
        # legacy fields kept for compatibility
        "quality": intel.get("quality"),
        "aggregate_quality": intel.get("quality"),
        "summary": intel.get("headline") or intel.get("executive_summary") or "",
        "counts": action_counts,
        "top_sectors": [b.get("name") for b in sector_buckets[:3]],
        "sector_allocation": {b.get("name"): b.get("percentage") for b in sector_buckets},
        "quality_breakdown": intel.get("quality_breakdown"),
        # richer optional payload
        "portfolio_bias": (intel.get("bias") or "Neutral").lower(),
        "bias": intel.get("bias"),
        "headline": intel.get("headline"),
        "executive_summary": intel.get("executive_summary"),
        "action_counts": action_counts,
        "exposures": {
            "strategy_buckets": strategy_buckets,
            "sector_buckets": sector_buckets,
            "risk_buckets": risk_buckets,
        },
        "key_themes": [f"{b.get('name')} exposure ~{round(float(b.get('percentage') or 0))}%" for b in strategy_buckets[:3]],
        "risk_concentrations": [f"{b.get('name')}: {', '.join(b.get('top_tickers') or [])}" for b in risk_buckets[:3]],
        "overexposure_flags": [f"{b.get('name')} at ~{round(float(b.get('percentage') or 0))}%" for b in strategy_buckets if float(b.get("percentage") or 0) >= 35][:3],
        "rebalancing_suggestions": intel.get("deploy_suggestions", [])[:5],
        "top_opportunities": intel.get("top_opportunities", []),
        "top_risks": intel.get("top_risks", []),
        "trim_candidates": intel.get("trim_candidates", []),
        "deploy_suggestions": intel.get("deploy_suggestions", []),
        "what_changed": intel.get("what_changed", []),
        "watchlist": intel.get("watchlist", []),
    }


def _agent_run_row_to_status(d: dict) -> AgentRunStatus:
    """Map an ``agent_runs`` row into :class:`AgentRunStatus`.

    Handles the Phase 4-6 columns (``portfolio_synthesis``,
    ``run_mode``, ``run_mode_decision``, ``cost_metrics``) gracefully —
    older rows lacking the columns surface as ``None``, letting the UI
    render a clean FULL-mode card without ever seeing missing fields.
    """
    return AgentRunStatus(
        id=d["id"],
        status=normalize_run_status(d.get("status")),
        current_agent=d.get("current_agent"),
        progress_pct=int(d.get("progress_pct") or 0),
        tickers=d.get("tickers") or [],
        deposit_amount=float(d.get("deposit_amount") or 0),
        sale_proceeds=float(d.get("sale_proceeds") or 0),
        allocation=d.get("allocation") or {},
        summary=d.get("summary"),
        error_message=d.get("error_message"),
        started_at=d.get("started_at"),
        finished_at=d.get("finished_at"),
        portfolio_synthesis=d.get("portfolio_synthesis"),
        synthesis_used_fallback=d.get("synthesis_used_fallback"),
        run_mode=d.get("run_mode"),
        run_mode_decision=d.get("run_mode_decision"),
        cost_metrics=d.get("cost_metrics"),
    )


# ── Service class ────────────────────────────────────────────────────────────

class RecommendationService:
    """Generate, manage, and resolve recommendations."""

    def __init__(self, user_id: UUID, price_service=None):
        self.user_id = user_id
        self.client = get_supabase_client()
        self._price_service = price_service
        self._trace_logged = False

    def _db(self, op_name: str, fn):
        return run_with_retry_sync(fn, op_name=op_name)

    async def get_insight_cards(self) -> list[InsightCard]:
        """Get all active recommendations as frontend-ready InsightCards.

        Includes short per-user caching + in-flight coalescing to suppress
        duplicate aggregate calls from concurrent frontend queries.
        """
        key = str(self.user_id)
        generation = _aggregate_generation.get(key, 0)
        now = datetime.now(timezone.utc)
        cached = _aggregate_cache.get(key)
        if cached and (now - cached[0]).total_seconds() <= _AGGREGATE_CACHE_TTL_S:
            logger.info("recommendations.aggregate.cache_hit user_id=%s", self.user_id)
            return cached[1]

        created = False
        async with _aggregate_lock:
            current = _aggregate_inflight.get(key)
            if current is not None and not current[1].done() and current[0] == generation:
                logger.info(
                    "recommendations.aggregate.coalesced user_id=%s generation=%d",
                    self.user_id,
                    generation,
                )
                task = current[1]
            else:
                if current is not None and not current[1].done() and current[0] != generation:
                    logger.info(
                        "recommendations.aggregate.superseded user_id=%s old_generation=%d new_generation=%d",
                        self.user_id,
                        current[0],
                        generation,
                    )
                task = asyncio.create_task(self._compute_insight_cards())
                _aggregate_inflight[key] = (generation, task)
                created = True
                logger.info(
                    "recommendations.aggregate.attached user_id=%s generation=%d created=%s",
                    self.user_id,
                    generation,
                    created,
                )

        # Shield shared compute from HTTP request cancellation.
        try:
            cards = await asyncio.shield(task)
        except asyncio.CancelledError:
            logger.info(
                "recommendations.aggregate.request_cancelled user_id=%s generation=%d",
                self.user_id,
                generation,
            )
            raise

        if created and _aggregate_generation.get(key, 0) == generation:
            _aggregate_cache[key] = (datetime.now(timezone.utc), cards)

        async with _aggregate_lock:
            cur = _aggregate_inflight.get(key)
            if cur is not None and cur[1] is task and task.done():
                _aggregate_inflight.pop(key, None)
        return cards

    async def _compute_insight_cards(self) -> list[InsightCard]:
        """Compute aggregate recommendation cards from DB state."""
        started = datetime.now(timezone.utc)
        logger.info("recommendations.aggregate.start user_id=%s", self.user_id)
        recs = (
            self._db(
                "recommendations.select_active",
                lambda: self.client.table("recommendations")
                .select("*")
                .eq("user_id", str(self.user_id))
                .eq("is_active", True)
                .order("urgency", desc=True)
                .execute(),
            )
        ).data

        positions = {
            p["ticker"]: p
            for p in (
                self._db(
                    "positions.select",
                    lambda: self.client.table("positions")
                    .select("*")
                    .eq("user_id", str(self.user_id))
                    .execute(),
                )
            ).data
        }

        # Phase 3 / 6 — fetch the linked analyst_verdict JSONB for each
        # recommendation so the frontend card can render drivers + risks
        # without a second round-trip per ticker. Degrades gracefully when
        # the column is missing (pre-migration-010 deployments).
        analyst_lookup: dict[tuple[str, str], dict] = {}
        latest_live_llm_by_ticker: dict[str, dict] = {}
        run_lookup: dict[str, dict] = {}
        run_ids_needed = {r.get("agent_run_id") for r in recs if r.get("agent_run_id")}
        tickers_needed = {str(r.get("ticker")) for r in recs if r.get("ticker")}
        if run_ids_needed:
            try:
                ai_rows = (
                    self._db(
                        "agent_insights.select_for_cards",
                        lambda: self.client.table("agent_insights")
                        .select("run_id, ticker, analyst_verdict, analyst_confidence")
                        .eq("user_id", str(self.user_id))
                        .in_("run_id", [str(r) for r in run_ids_needed])
                        .execute(),
                    )
                ).data or []
                for row in ai_rows:
                    run_id = row.get("run_id")
                    ticker = row.get("ticker")
                    if run_id and ticker:
                        analyst_lookup[(str(run_id), ticker)] = row
            except Exception as exc:  # noqa: BLE001 — pre-migration: skip analyst overlay
                logger.debug(
                    "get_insight_cards: analyst_verdict lookup failed (likely "
                    "pre-Phase-3 schema): %s", exc,
                )
            try:
                completed_runs = (
                    self._db(
                        "agent_runs.select_completed_for_card_preference",
                        lambda: self.client.table("agent_runs")
                        .select("id, finished_at")
                        .eq("user_id", str(self.user_id))
                        .eq("status", "completed")
                        .order("finished_at", desc=True)
                        .limit(25)
                        .execute(),
                    )
                ).data or []
                completed_ids = [str(r["id"]) for r in completed_runs if r.get("id")]
                if completed_ids and tickers_needed:
                    candidate_rows = (
                        self._db(
                            "agent_insights.select_latest_completed_for_cards",
                            lambda: self.client.table("agent_insights")
                            .select("run_id, ticker, analyst_verdict, analyst_confidence, created_at")
                            .eq("user_id", str(self.user_id))
                            .in_("run_id", completed_ids)
                            .in_("ticker", list(tickers_needed))
                            .execute(),
                        )
                    ).data or []
                    run_rank = {rid: idx for idx, rid in enumerate(completed_ids)}
                    for row in candidate_rows:
                        ticker = row.get("ticker")
                        if not ticker:
                            continue
                        verdict = row.get("analyst_verdict") or {}
                        source = str((verdict or {}).get("analysis_source") or "").lower()
                        used_fallback = bool((verdict or {}).get("used_fallback", False))
                        gen_version = str((verdict or {}).get("generation_version") or "").lower()
                        # Only accept human_v2 / compact_v1 verdicts as fresh candidates.
                        # Older rows may contain forbidden indicator language — serving
                        # them as "live_llm" is the root cause of stale text in the UI.
                        _VALID_GEN_VERSIONS = {"human_v2", "compact_v1"}
                        if source != "live_llm" or used_fallback:
                            continue
                        if gen_version not in _VALID_GEN_VERSIONS:
                            logger.debug(
                                "get_insight_cards: skipping stale-schema row ticker=%s gen_version=%s",
                                ticker, gen_version,
                            )
                            continue
                        prev = latest_live_llm_by_ticker.get(ticker)
                        if prev is None:
                            latest_live_llm_by_ticker[ticker] = row
                            continue
                        prev_rank = run_rank.get(str(prev.get("run_id")), 10_000)
                        row_rank = run_rank.get(str(row.get("run_id")), 10_000)
                        if row_rank < prev_rank:
                            latest_live_llm_by_ticker[ticker] = row
            except Exception as exc:  # noqa: BLE001
                logger.debug("get_insight_cards: latest completed analyst lookup failed: %s", exc)
            try:
                run_rows = (
                    self._db(
                        "agent_runs.select_for_cards",
                        lambda: self.client.table("agent_runs")
                        .select("id, status, cost_metrics, allocation")
                        .eq("user_id", str(self.user_id))
                        .in_("id", [str(r) for r in run_ids_needed])
                        .execute(),
                    )
                ).data or []
                for row in run_rows:
                    if row.get("id"):
                        run_lookup[str(row["id"])] = row
            except Exception as exc:  # noqa: BLE001
                logger.debug("get_insight_cards: run metadata lookup failed: %s", exc)

        # Fallback contract: find the latest completed runs with _thesis_v2 and
        # _reasoning_v2. Used as fallbacks when a card's agent_run_id run is absent
        # from run_lookup or its allocation lacks these keys. This covers stale
        # run binding (e.g. _persist failed, leaving old recs active) and the small
        # timing window between _persist.finally and _update_run writing to the DB.
        # A single query for the latest 5 runs serves both fallback lookups.
        latest_thesis_run_id: Optional[str] = None
        latest_reasoning_v2_run_id: Optional[str] = None
        if run_ids_needed:
            try:
                latest_for_fallback = (
                    self._db(
                        "agent_runs.select_latest_completed_with_thesis",
                        lambda: self.client.table("agent_runs")
                        .select("id, finished_at, allocation")
                        .eq("user_id", str(self.user_id))
                        .eq("status", "completed")
                        .order("finished_at", desc=True)
                        .limit(5)
                        .execute(),
                    )
                ).data or []
                for fb_row in latest_for_fallback:
                    rid = str(fb_row.get("id") or "")
                    if not rid:
                        continue
                    alloc = fb_row.get("allocation") or {}
                    if not isinstance(alloc, dict):
                        continue
                    if latest_thesis_run_id is None:
                        tmap = alloc.get("_thesis_v2")
                        if isinstance(tmap, dict) and tmap:
                            latest_thesis_run_id = rid
                            if rid not in run_lookup:
                                run_lookup[rid] = fb_row
                    if latest_reasoning_v2_run_id is None:
                        rmap = alloc.get("_reasoning_v2")
                        if isinstance(rmap, dict) and rmap:
                            latest_reasoning_v2_run_id = rid
                            if rid not in run_lookup:
                                run_lookup[rid] = fb_row
                    if latest_thesis_run_id and latest_reasoning_v2_run_id:
                        break
                logger.info(
                    "thesis.contract card_run_ids=%s latest_thesis_run=%s latest_r2_run=%s",
                    sorted(str(r) for r in run_ids_needed),
                    latest_thesis_run_id or "none",
                    latest_reasoning_v2_run_id or "none",
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("get_insight_cards: latest thesis/r2 run lookup failed: %s", exc)

        # Fetch live prices if price service available
        prices: dict[str, float] = {}
        if self._price_service and positions:
            try:
                tickers = list(positions.keys())
                price_results = await self._price_service.fetch_prices(tickers)
                for t, pr in price_results.items():
                    if pr.is_valid:
                        prices[t] = pr.mid_price
            except Exception:
                pass

        cards = []
        normalized_count = degraded_count = skipped_count = 0
        fallback_cards = reused_cached_cards = 0
        thesis_diag_counts: dict[str, int] = {}
        for rec in recs:
            try:
                ticker = rec.get("ticker") or "UNKNOWN"
                pos = positions.get(ticker, {})
                price = prices.get(ticker)
                avg_cost = float(pos.get("avg_cost", 0))
                pnl_pct = round((price - avg_cost) / avg_cost * 100, 2) if price and avg_cost > 0 else None

                conviction = float(rec["conviction_score"]) if rec.get("conviction_score") is not None else None
                data_confidence_score = _derive_confidence_score(conviction)
                analyst_row = analyst_lookup.get((str(rec.get("agent_run_id")), ticker))
                preferred_live_row = latest_live_llm_by_ticker.get(ticker)
                _used_preferred = False
                _current_av_raw = (analyst_row.get("analyst_verdict") or {}) if analyst_row else {}
                _current_av_for_pref = _current_av_raw if isinstance(_current_av_raw, dict) else {}
                # Also prefer the freshest live LLM row when the current verdict was written before
                # Phase-7 memo fields (primary_driver, conviction_level) existed. This closes the
                # page-load vs Run Agents inconsistency: stale rows without primary_driver now
                # get upgraded to the freshest available analyst verdict on page load.
                _lacks_memo_fields = not bool((_current_av_for_pref.get("primary_driver") or "").strip())
                if preferred_live_row and (
                    analyst_row is None
                    or bool(_current_av_for_pref.get("used_fallback", False))
                    or _lacks_memo_fields
                ):
                    analyst_row = preferred_live_row
                    _used_preferred = True
                _analyst_verdict_raw = (analyst_row or {}).get("analyst_verdict") or None
                analyst_verdict = _analyst_verdict_raw if isinstance(_analyst_verdict_raw, dict) else None
                # Determine reasoning_source for observability + frontend badge.
                _av_gen = str((analyst_verdict or {}).get("generation_version") or "").lower()
                _av_src = str((analyst_verdict or {}).get("analysis_source") or "").lower()
                _av_fallback = bool((analyst_verdict or {}).get("used_fallback", False))
                _FRESH_GEN_VERSIONS = {"human_v2", "compact_v1"}
                if analyst_verdict is None:
                    _reasoning_source = "no_analyst_data"
                elif _av_fallback:
                    _reasoning_source = "fallback"
                elif _used_preferred and _av_gen in _FRESH_GEN_VERSIONS:
                    _reasoning_source = "fresh_llm"
                elif not _used_preferred and _av_gen in _FRESH_GEN_VERSIONS and _av_src == "live_llm":
                    _reasoning_source = "fresh_llm"
                elif _av_gen in _FRESH_GEN_VERSIONS and _av_src == "cached_run":
                    _reasoning_source = "cache"
                elif _av_gen not in _FRESH_GEN_VERSIONS and analyst_verdict is not None:
                    _reasoning_source = "stale_db"
                else:
                    _reasoning_source = _av_src or "unknown"
                _reasoning_schema_version = _av_gen if _av_gen else None
                logger.info(
                    "analyst_trace checkpoint=card_assembly ticker=%s reasoning_source=%s "
                    "reasoning_schema_version=%s used_preferred=%s av_gen=%s",
                    ticker, _reasoning_source, _reasoning_schema_version, _used_preferred, _av_gen,
                )
                analyst_conf_raw = (analyst_row or {}).get("analyst_confidence")
                analyst_confidence = (
                    float(analyst_conf_raw) if analyst_conf_raw is not None else None
                )
                analyst_fields = _extract_analyst_card_fields(
                    analyst_verdict, analyst_confidence=analyst_confidence,
                )
                reason_tags = _derive_reason_tags(rec)
                sentiment_label = _derive_sentiment_label(rec, analyst_verdict)
                data_quality_label = _derive_quality_label_from_evidence(
                    confidence_score=data_confidence_score,
                    reason_tags=reason_tags,
                    analyst_fields=analyst_fields,
                    sentiment_label=sentiment_label,
                    rec=rec,
                )
                rec["sentiment_label"] = sentiment_label
                rec["reason_tags"] = reason_tags
                rec["data_quality_label"] = data_quality_label
                reasoning = normalize_reasoning_payload(rec, analyst_verdict=analyst_verdict)
                normalized_count += 1
                if "reasoning_unavailable" in reasoning.get("fallback_flags", []):
                    degraded_count += 1
                is_fallback = bool(analyst_fields["used_fallback"]) or "reasoning_unavailable" in (
                    reasoning.get("fallback_flags") or []
                )
                if is_fallback:
                    fallback_cards += 1
                source, reused_cached = _resolve_card_analysis_source(
                    analyst_verdict=analyst_verdict,
                    is_fallback=is_fallback,
                )
                if reused_cached:
                    reused_cached_cards += 1

                # Intel v2: extract per-ticker thesis scorecard from
                # agent_runs.allocation["_thesis_v2"] and generate the
                # plain-English translation. Tries the card's own
                # agent_run_id first; falls back to the latest completed
                # run with _thesis_v2 when the primary run is absent or
                # lacks the map. Fails safely — both fields remain None
                # if data is unavailable or malformed.
                thesis_v2_dict, thesis_plain_english_dict, thesis_diag = _build_thesis_fields_for_card(
                    ticker=ticker,
                    run_id=rec.get("agent_run_id"),
                    run_lookup=run_lookup,
                    fallback_run_id=latest_thesis_run_id,
                )
                thesis_diag_counts[thesis_diag] = thesis_diag_counts.get(thesis_diag, 0) + 1

                # Intel v2 reasoning_v2 UI: build plain-English intel_read from
                # _reasoning_v2 coverage block. Falls back to the latest run with
                # _reasoning_v2 when the card's own run lacks it. Safe when absent.
                # is_from_primary: True when data came from the recommendation's own
                # run; False when the fallback (latest completed run) was used instead.
                intel_read_dict, intel_read_from_primary = _build_intel_read_for_card(
                    ticker=ticker,
                    run_id=rec.get("agent_run_id"),
                    run_lookup=run_lookup,
                    fallback_run_id=latest_reasoning_v2_run_id,
                )

                # Resolve action/conviction before card construction so we can
                # apply the INSUFFICIENT_DATA consistency gate below.
                _card_action = (rec.get("action") or "HOLD").upper()
                _card_analyst_action = analyst_fields["action"]
                _card_conviction_level = reasoning.get("conviction_level")
                _card_color = ACTION_COLORS.get(_card_action, "gray")
                # Save pre-gate values for posture derivation (see below).
                _pre_gate_action = _card_action
                _pre_gate_analyst_action = _card_analyst_action
                _pre_gate_conviction_level = _card_conviction_level

                # Posture consistency: when reasoning_v2 forces WATCH due to
                # INSUFFICIENT_DATA, the card must not show BUY / HIGH CONVICTION.
                # Downgrade BUY → HOLD. Apply a conservative conviction ladder so
                # cards remain differentiated rather than all flattening to LOW:
                #   HIGH + ≥3 trusted signals → MEDIUM (meaningful partial evidence)
                #   HIGH + <3 trusted signals → LOW  (too sparse for any conviction)
                #   MEDIUM + <2 trusted signals → LOW (very weak coverage)
                #   MEDIUM + ≥2 trusted signals → preserved
                #   LOW → preserved
                # Also replace body copy (ACTION/WHY/ALT VIEW) with conservative
                # watchlist language so the card content matches the badge.
                if intel_read_dict and intel_read_dict.get("insufficient_data"):
                    _n_trusted = len(intel_read_dict.get("trusted_signals") or [])
                    if _card_action == "BUY":
                        _card_action = "HOLD"
                        _card_color = ACTION_COLORS.get("HOLD", "blue")
                    if (_card_analyst_action or "").upper() == "BUY":
                        _card_analyst_action = "HOLD"
                    _cl_upper = (_card_conviction_level or "").upper()
                    if _cl_upper == "HIGH":
                        _card_conviction_level = "MEDIUM" if _n_trusted >= 3 else "LOW"
                    elif _cl_upper == "MEDIUM" and _n_trusted < 2:
                        _card_conviction_level = "LOW"
                    # ACTION always replaced with conservative watchlist language.
                    reasoning["action_reason"] = intel_read_dict.get("conservative_action")
                    # WHY: preserve ticker-specific primary_driver when safe (no forbidden
                    # bullish phrases); fall back to conservative_why when absent or unsafe.
                    if not reasoning.get("primary_driver") or not is_safe_for_insufficient_data(
                        reasoning.get("primary_driver")
                    ):
                        reasoning["primary_driver"] = intel_read_dict.get("conservative_why")
                    # ALT VIEW: preserve ticker-specific differentiation when safe; null when
                    # it contains forbidden bullish phrases or is action-directive language.
                    if reasoning.get("differentiation") and not is_safe_for_insufficient_data(
                        reasoning.get("differentiation")
                    ):
                        reasoning["differentiation"] = None

                # Intel posture system (v3): derive advisor-facing posture bucket
                # using PRE-GATE signals so the display safety gate (BUY→HOLD under
                # insufficient_data) does not suppress the posture bucket.
                #
                # Key consistency fix: when intel_read comes from the FALLBACK run
                # (not the recommendation's own run), its insufficient_data flag may
                # reflect a different run's data quality and must not gate posture.
                # Only the primary run's _reasoning_v2 is authoritative for posture.
                _intel_read_for_posture = intel_read_dict if intel_read_from_primary else None
                intel_posture_label = _derive_intel_posture(
                    ticker=ticker,
                    action=_pre_gate_action,
                    analyst_action=_pre_gate_analyst_action,
                    conviction_level=_pre_gate_conviction_level,
                    technical_signal=rec.get("technical_signal"),
                    category=pos.get("category"),
                    intel_read_dict=_intel_read_for_posture,
                )
                # Inject posture_reason into intel_read_dict so the WhyThisView
                # section explains WHY this posture was assigned (card-specific).
                if intel_read_dict is not None:
                    intel_read_dict["posture_reason"] = build_posture_reason(
                        posture_label=intel_posture_label,
                        trusted_signals=intel_read_dict.get("trusted_signals") or [],
                        incomplete_signals=intel_read_dict.get("incomplete_signals") or [],
                        ticker=ticker,
                        category=pos.get("category") or "",
                    )

                card = InsightCard(
                    id=rec["id"],
                    ticker=ticker,
                    name=pos.get("name", ticker),
                    action=_card_action,
                    detail=rec.get("detail") or "Data-backed recommendation available; AI reasoning is unavailable.",
                    rationale=rec.get("rationale", ""),
                    urgency=int(rec.get("urgency") or 0),
                    color=_card_color,
                    tax_note=rec.get("tax_note", ""),
                    drip_note=rec.get("drip_note", ""),
                    current_price=price,
                    pnl_pct=pnl_pct,
                    category=pos.get("category", "Unknown"),
                    sector=(
                        pos.get("sector")
                        or pos.get("industry")
                        or pos.get("asset_class")
                        or pos.get("category")
                        or map_ticker_to_sector(ticker)
                    ),
                    investment_thesis=rec.get("investment_thesis"),
                    sentiment_score=float(rec["sentiment_score"]) if rec.get("sentiment_score") is not None else None,
                    sentiment_label=sentiment_label,
                    technical_signal=rec.get("technical_signal"),
                    conviction_score=conviction,
                    suggested_allocation=float(rec["suggested_allocation"]) if rec.get("suggested_allocation") is not None else None,
                    agent_run_id=rec.get("agent_run_id"),
                    what_changed=rec.get("what_changed"),
                    data_confidence_score=data_confidence_score,
                    data_quality_label=data_quality_label,
                    reason_tags=reason_tags,
                    analyst_action=_card_analyst_action,
                    analyst_conviction=analyst_fields["conviction"],
                    analyst_confidence=analyst_fields["confidence"],
                    analyst_drivers=analyst_fields["drivers"],
                    analyst_risks=analyst_fields["risks"],
                    analyst_used_fallback=analyst_fields["used_fallback"],
                    summary=reasoning.get("summary"),
                    reasoning_summary=reasoning.get("reasoning_summary"),
                    thesis=reasoning.get("thesis"),
                    why_this_matters=reasoning.get("why_this_matters"),
                    key_drivers=reasoning.get("key_drivers"),
                    main_risks=reasoning.get("main_risks"),
                    confidence=reasoning.get("confidence"),
                    conviction=reasoning.get("conviction"),
                    supporting_evidence=reasoning.get("supporting_evidence"),
                    plain_language_explanation=reasoning.get("plain_language_explanation"),
                    fallback_flags=reasoning.get("fallback_flags"),
                    analysis_source=source,
                    conviction_level=_card_conviction_level,
                    primary_driver=reasoning.get("primary_driver"),
                    risk_flag=reasoning.get("risk_flag"),
                    action_reason=reasoning.get("action_reason"),
                    reasoning_source=_reasoning_source,
                    reasoning_schema_version=_reasoning_schema_version,
                    differentiation=reasoning.get("differentiation"),
                    thesis_v2=thesis_v2_dict,
                    thesis_plain_english=thesis_plain_english_dict,
                    intel_read=intel_read_dict,
                    intel_posture_label=intel_posture_label,
                    intel_filter_bucket=intel_posture_label,
                )
                logger.info(
                    "analyst_trace checkpoint=api_serializer ticker=%s payload=%s",
                    ticker,
                    json.dumps(
                        {
                            "ticker": card.ticker,
                            "action": card.action,
                            "reasoning_source": card.reasoning_source,
                            "reasoning_schema_version": card.reasoning_schema_version,
                            "investment_thesis": card.investment_thesis,
                            "summary": card.summary,
                            "thesis": card.thesis,
                            "plain_language_explanation": card.plain_language_explanation,
                            "primary_driver": card.primary_driver,
                            "risk_flag": card.risk_flag,
                            "action_reason": card.action_reason,
                            "analyst_action": card.analyst_action,
                            "analyst_drivers": card.analyst_drivers,
                            "analyst_risks": card.analyst_risks,
                            "data_quality_label": card.data_quality_label,
                        },
                        default=str,
                    )[:1500],
                )
                cards.append(card)
                if not self._trace_logged:
                    self._trace_logged = True
                    logger.info(
                        "reasoning_contract_trace loaded_keys=%s normalized_keys=%s card_keys=%s",
                        sorted(rec.keys()),
                        list(CANONICAL_REASONING_KEYS),
                        sorted(card.model_dump(exclude_none=True).keys()),
                    )
            except Exception as exc:  # noqa: BLE001
                skipped_count += 1
                logger.warning(
                    "recommendations.aggregate.row_error user_id=%s recommendation_id=%s error_type=%s",
                    self.user_id,
                    rec.get("id"),
                    type(exc).__name__,
                )
                continue

        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        attempted_llm_calls = successful_llm_calls = failed_llm_calls = 0
        llm_enriched_cards = discarded_llm_calls = 0
        for row in run_lookup.values():
            cm = row.get("cost_metrics") or {}
            attempted_llm_calls += int(cm.get("attempted_llm_calls") or cm.get("actual_llm_calls") or cm.get("total_calls") or 0)
            successful_llm_calls += int(cm.get("successful_llm_calls") or 0)
            failed_llm_calls += int(cm.get("failed_llm_calls") or 0)
            llm_enriched_cards += int(cm.get("llm_enriched_cards") or 0)
            discarded_llm_calls += int(cm.get("discarded_llm_calls") or 0)
        logger.info(
            "recommendations.aggregate.done user_id=%s recs=%d cards=%d positions=%d insights=%d normalized=%d degraded=%d skipped=%d elapsed_ms=%d attempted_llm_calls=%d successful_llm_calls=%d failed_llm_calls=%d llm_enriched_cards=%d discarded_llm_calls=%d fallback_cards=%d reused_cached_cards=%d thesis_diag=%s",
            self.user_id,
            len(recs),
            len(cards),
            len(positions),
            len(analyst_lookup),
            normalized_count,
            degraded_count,
            skipped_count,
            elapsed_ms,
            attempted_llm_calls,
            successful_llm_calls,
            failed_llm_calls,
            llm_enriched_cards,
            discarded_llm_calls,
            fallback_cards,
            reused_cached_cards,
            thesis_diag_counts,
        )
        return cards

    async def queue_agent_run(
        self,
        deposit_amount: Optional[float] = None,
        sale_proceeds: float = 0.0,
        allow_completed_reuse: bool = False,
    ) -> tuple[str, bool]:
        """Return ``(job_id, is_new)`` for the agent run to drive.

        Reuses an existing run instead of creating a new one when either:
          * a run is already ``queued`` / ``running`` for this user — prevents
            concurrent executions (SEV-1 single-run lock), OR
          * the most recent ``completed`` run finished less than 2 minutes
            ago — light cache to stop retry storms on re-mount.

        STALE JOB RECOVERY: If a 'running' job is >10 minutes old,
        marks it as 'failed' and creates a new run instead.

        Callers (the router) must only dispatch the background pipeline when
        ``is_new`` is True; reused runs already have their lifecycle handled.
        """
        # ── Lock / cache: reuse recent runs when possible ─────────────────
        try:
            recent = (
                self.client.table("agent_runs")
                .select("id, status, started_at, finished_at, updated_at, created_at")
                .eq("user_id", str(self.user_id))
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            ).data or []
        except Exception:
            recent = []

        if recent:
            last = recent[0]
            status = last.get("status")
            normalized_status = normalize_run_status(status)
            if normalized_status in ACTIVE_RUN_STATUSES:
                if _is_stale_active_run(last):
                    try:
                        self._mark_stale_run_failed(last["id"], old_status=status, reason="stale_timeout")
                        logger.info(
                            "recommendations.queue.stale_active_marked_failed user_id=%s job_id=%s status=%s",
                            self.user_id,
                            last["id"],
                            status,
                        )
                    except Exception as exc:
                        logger.warning("Failed to mark stale active job failed: %s", exc)
                    # fall through to create a new run
                else:
                    logger.info(
                        "recommendations.queue.reuse_active user_id=%s job_id=%s status=%s",
                        self.user_id,
                        last["id"],
                        status,
                    )
                    return last["id"], False

            # 3) Light cache: completed within the last 2 minutes.
            elif normalized_status == "completed" and allow_completed_reuse:
                finished = last.get("finished_at") or last.get("started_at")
                if finished and _within_last(finished, seconds=120):
                    logger.info(
                        "Cache hit — reusing run %s finished at %s",
                        last["id"], finished,
                    )
                    return last["id"], False
            elif normalized_status == "completed":
                logger.info(
                    "Fresh run requested — not reusing completed run %s (allow_completed_reuse=%s)",
                    last["id"],
                    allow_completed_reuse,
                )

        # Default deposit from user row if not supplied
        if deposit_amount is None:
            try:
                row = (
                    self.client.table("users")
                    .select("deposit_amount")
                    .eq("id", str(self.user_id))
                    .single()
                    .execute()
                )
                deposit_amount = float(row.data.get("deposit_amount") or 900.0)
            except Exception:
                deposit_amount = 900.0

        # A fresh run should never serve stale aggregate cards while queued.
        invalidate_recommendations_aggregate_cache(self.user_id, reason="refresh_requested")
        from .agents.job_runner import build_orchestrator
        orch = build_orchestrator(
            user_id=self.user_id,
            deposit_amount=deposit_amount,
            sale_proceeds=sale_proceeds,
        )
        run_id = await orch.create_run()
        logger.info(
            "recommendations.queue.created user_id=%s job_id=%s",
            self.user_id,
            run_id,
        )
        return run_id, True

    async def get_job_status(self, job_id: UUID) -> AgentRunStatus:
        """Fetch the status of an agent run. Used by the UI progress tracker."""
        from fastapi import HTTPException
        logger.info(
            "recommendations.job_status user_id=%s job_id=%s read_only=true",
            self.user_id,
            job_id,
        )
        row = (
            self._db(
                "agent_runs.get_status",
                lambda: self.client.table("agent_runs")
                .select("*")
                .eq("id", str(job_id))
                .eq("user_id", str(self.user_id))
                .single()
                .execute(),
            )
        )
        if not row.data:
            raise HTTPException(status_code=404, detail="Agent run not found")
        raw = row.data
        raw_status = normalize_run_status(raw.get("status"))
        raw["status"] = raw_status
        if raw_status in ACTIVE_RUN_STATUSES and _is_stale_active_run(raw):
            try:
                self._mark_stale_run_failed(str(job_id), old_status=raw_status, reason="stale_timeout")
                raw["status"] = "failed"
                raw["current_agent"] = "failed"
                raw["progress_pct"] = 100
                raw["summary"] = "Previous run got stuck; start a new run."
                raw["error_message"] = (
                    raw.get("error_message")
                    or "LLM failed: stale_timeout (>10m without activity)."
                )
                raw["finished_at"] = datetime.now(timezone.utc).isoformat()
            except Exception as exc:
                logger.warning("Failed stale auto-fail for job %s: %s", job_id, exc)
                raw["status"] = "failed"
                raw["summary"] = "Previous run got stuck; start a new run."
        status = _agent_run_row_to_status(raw)
        cards = await self.get_insight_cards()
        if cards:
            status.portfolio_synthesis = compute_portfolio_synthesis(cards)
        return status

    def _mark_stale_run_failed(
        self,
        run_id: str,
        *,
        old_status: Optional[str] = None,
        reason: str = "stale_timeout",
    ) -> None:
        new_status = assert_db_status("failed")
        logger.warning(
            "agent_runs.status_patch job_id=%s old_status=%s new_status=%s reason=%s",
            run_id,
            old_status or "unknown",
            new_status,
            reason,
        )
        self._db(
            "agent_runs.mark_failed",
            lambda: self.client.table("agent_runs")
            .update({
                "status": new_status,
                "current_agent": "failed",
                "progress_pct": 100,
                "error_message": f"LLM failed: {reason}",
                "summary": "Previous run got stuck; start a new run.",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", str(run_id))
            .eq("user_id", str(self.user_id))
            .execute(),
        )

    async def get_latest_job(self) -> Optional[AgentRunStatus]:
        """Return the most recent agent run for this user, or None if none exists."""
        logger.info("recommendations.latest_job user_id=%s", self.user_id)
        rows = (
            self._db(
                "agent_runs.latest",
                lambda: self.client.table("agent_runs")
                .select("*")
                .eq("user_id", str(self.user_id))
                .order("started_at", desc=True)
                .limit(1)
                .execute(),
            )
        ).data
        if not rows:
            return None
        status = _agent_run_row_to_status(rows[0])
        cards = await self.get_insight_cards()
        if cards:
            status.portfolio_synthesis = compute_portfolio_synthesis(cards)
        return status

    async def get_agent_insights(self, run_id: Optional[UUID] = None) -> list[AgentInsight]:
        """Fetch the per-ticker agent insights for a run.

        If `run_id` is None, returns the insights from the most recent
        completed run for this user.
        """
        if run_id is None:
            latest = (
                self.client.table("agent_runs")
                .select("id")
                .eq("user_id", str(self.user_id))
                .eq("status", "completed")
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            ).data
            if not latest:
                return []
            run_id = latest[0]["id"]

        rows = (
            self.client.table("agent_insights")
            .select("*")
            .eq("run_id", str(run_id))
            .eq("user_id", str(self.user_id))
            .execute()
        ).data or []

        return [
            AgentInsight(
                id=r["id"],
                run_id=r.get("run_id"),
                ticker=r["ticker"],
                investment_thesis=r.get("investment_thesis"),
                sentiment_score=float(r["sentiment_score"]) if r.get("sentiment_score") is not None else None,
                sentiment_label=r.get("sentiment_label"),
                technical_signal=r.get("technical_signal"),
                technical_summary=r.get("technical_summary"),
                fundamental_score=float(r["fundamental_score"]) if r.get("fundamental_score") is not None else None,
                fundamental_summary=r.get("fundamental_summary"),
                conviction_score=float(r["conviction_score"]) if r.get("conviction_score") is not None else None,
                suggested_allocation=float(r["suggested_allocation"]) if r.get("suggested_allocation") is not None else None,
                suggested_action=r.get("suggested_action"),
                created_at=r.get("created_at"),
                what_changed=r.get("what_changed"),
                analyst_verdict=r.get("analyst_verdict"),
                analyst_confidence=(
                    float(r["analyst_confidence"])
                    if r.get("analyst_confidence") is not None else None
                ),
            )
            for r in rows
        ]

    async def resolve(self, rec_id: UUID, resolution: RecommendationResolve) -> dict:
        """Resolve a recommendation — accept, reject, defer, or expire."""
        update = {
            "is_active": False,
            "resolution": resolution.resolution,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }

        result = (
            self.client.table("recommendations")
            .update(update)
            .eq("id", str(rec_id))
            .eq("user_id", str(self.user_id))
            .execute()
        )

        if not result.data:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Recommendation not found")

        if resolution.notes:
            await self.log_decision(DecisionLogCreate(
                recommendation_id=rec_id,
                ticker=result.data[0]["ticker"],
                decision=resolution.resolution,
                notes=resolution.notes,
            ))

        invalidate_recommendations_aggregate_cache(self.user_id, reason="recommendation_resolved")
        return result.data[0]

    async def list_decisions(self, limit: int = 50) -> list[DecisionLogEntry]:
        """List decision log entries."""
        logger.info("recommendations.decisions user_id=%s limit=%d", self.user_id, limit)
        result = (
            self.client.table("decision_log")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data

    async def log_decision(self, entry: DecisionLogCreate) -> DecisionLogEntry:
        """Create a decision log entry with outcome tracking initialised."""
        data = entry.model_dump(mode="json")
        data["user_id"] = str(self.user_id)
        data.setdefault("status", "active")
        result = self.client.table("decision_log").insert(data).execute()
        return result.data[0]

    async def update_outcomes(self) -> list[DecisionLogEntry]:
        """Refresh current_price, return_pct, and status for all active decision log entries.

        Reuses the existing price service. Marks an entry closed when the
        position for that ticker no longer holds any shares.
        """
        entries = (
            self.client.table("decision_log")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("created_at", desc=True)
            .execute()
        ).data or []

        if not entries:
            return []

        # Current share counts per ticker
        positions = {
            p["ticker"]: float(p.get("shares", 0))
            for p in (
                self.client.table("positions")
                .select("ticker, shares")
                .eq("user_id", str(self.user_id))
                .execute()
            ).data or []
        }

        # Fetch live prices for active entries that have an entry price
        active_tickers = {
            e["ticker"] for e in entries
            if e.get("status", "active") == "active" and e.get("price_at_decision")
        }
        prices: dict[str, float] = {}
        if self._price_service and active_tickers:
            try:
                results = await self._price_service.fetch_prices(list(active_tickers))
                for t, pr in results.items():
                    if pr.is_valid:
                        prices[t] = pr.mid_price
            except Exception:
                pass

        updated: list[DecisionLogEntry] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for entry in entries:
            if entry.get("status", "active") == "closed":
                updated.append(entry)
                continue

            ticker = entry["ticker"]
            entry_price = entry.get("price_at_decision")
            patch: dict = {}

            current_price = prices.get(ticker)
            if current_price is not None:
                patch["current_price"] = current_price
                if entry_price and float(entry_price) > 0:
                    patch["return_pct"] = round(
                        (current_price - float(entry_price)) / float(entry_price) * 100, 4
                    )

            # Close the entry when the position has been fully exited
            shares = positions.get(ticker)
            if shares is None or float(shares) == 0:
                patch["status"] = "closed"
                patch["closed_at"] = now_iso

            if patch:
                row = (
                    self.client.table("decision_log")
                    .update(patch)
                    .eq("id", entry["id"])
                    .execute()
                ).data
                updated.append(row[0] if row else entry)
            else:
                updated.append(entry)

        return updated

    async def get_strategy_performance(self) -> list[StrategyPerformance]:
        """Aggregate decision log entries by strategy_tag."""
        entries = (
            self.client.table("decision_log")
            .select("strategy_tag, return_pct, status")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data or []

        groups: dict[str, list] = {}
        for entry in entries:
            tag = entry.get("strategy_tag") or "untagged"
            groups.setdefault(tag, []).append(entry)

        result = []
        for tag, rows in groups.items():
            returns = [float(r["return_pct"]) for r in rows if r.get("return_pct") is not None]
            avg_return = round(sum(returns) / len(returns), 2) if returns else None
            wins = sum(1 for r in returns if r > 0)
            win_rate = round(wins / len(returns) * 100, 1) if returns else None
            result.append(StrategyPerformance(
                strategy_tag=tag,
                avg_return=avg_return,
                win_rate=win_rate,
                total_trades=len(rows),
            ))

        return sorted(result, key=lambda x: x.total_trades, reverse=True)

    async def generate_portfolio_advice(
        self,
        portfolio_positions: list[dict[str, Any]],
        macro_summary: str,
        api_key: str,
    ) -> dict[str, Any]:
        """Generate portfolio advice via the advisor LLM.

        Wraps the standalone portfolio_advisor function with error handling
        and schema validation.
        """
        return await portfolio_advisor(
            portfolio_positions=portfolio_positions,
            macro_summary=macro_summary,
            api_key=api_key,
        )


def _within_last(iso_ts: str, *, seconds: int) -> bool:
    """True if ``iso_ts`` is a timestamp within the last ``seconds`` seconds."""
    if not iso_ts:
        return False
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts) <= timedelta(seconds=seconds)


def _latest_run_activity_ts(row: dict[str, Any]) -> str:
    """Best-effort heartbeat timestamp from an ``agent_runs`` row."""
    return (
        row.get("updated_at")
        or row.get("heartbeat_at")
        or row.get("started_at")
        or row.get("created_at")
        or ""
    )


def _is_stale_active_run(row: dict[str, Any], *, max_age_seconds: int = STALE_RUN_MAX_AGE_SECONDS) -> bool:
    """True when an active run has no heartbeat/update inside max_age_seconds."""
    if row.get("status") not in ACTIVE_RUN_STATUSES:
        return False
    latest = _latest_run_activity_ts(row)
    return not _within_last(latest, seconds=max_age_seconds)


# ── Intel v3 dark-launch shadow compute ──────────────────────────────────────
# Shadow-computes a v3 decision from an existing InsightCard without touching
# any visible behavior. Callable from tests or internal logging only.
# Not wired to any API route or UI surface.

def _v3_shadow_decide(card: InsightCard) -> Optional[Any]:
    """Shadow-compute a v3 decision from an InsightCard for dark-launch validation.

    Returns DecisionOutputV3 or None if the import fails (graceful degradation).
    Does not modify card, raise exceptions, or change any visible behavior.
    """
    try:
        from .intelligence.v3.existing_signal_adapter import build_decision_input_from_card
        from .intelligence.v3.decision_policy_v1 import decide as _v3_decide

        inp = build_decision_input_from_card(
            ticker=card.ticker,
            action=card.action,
            analyst_action=card.analyst_action,
            conviction_level=card.conviction_level,
            technical_signal=card.technical_signal,
            risk_flag=card.risk_flag,
            analyst_risks=card.analyst_risks,
            category=card.category,
            data_quality_label=card.data_quality_label,
            intel_read=card.intel_read,
            thesis_v2=card.thesis_v2,
        )
        return _v3_decide(inp)
    except Exception as exc:  # noqa: BLE001
        logger.debug("v3_shadow_decide skipped ticker=%s err=%s", card.ticker, exc)
        return None
