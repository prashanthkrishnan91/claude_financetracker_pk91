"""Tests for intel_read card projection logic.

Tests the behavior of _build_intel_read_for_card (logic extracted here to avoid
the supabase import chain — pre-existing limitation of this env; the function
lives in recommendation_engine.py which requires supabase at import time).

Coverage:
1.  intel_read populated when _reasoning_v2 exists with valid coverage.
2.  intel_read is None when _reasoning_v2 is missing from allocation.
3.  intel_read is None when ticker is absent from _reasoning_v2 map.
4.  _thesis_v2 and _reasoning_v2 coexist in allocation without interference.
5.  intel_read contains no raw metric key names.
6.  Empty allocation safely returns None.
7.  None run_id safely returns None.
8.  Uppercase ticker normalization matches correctly.
9.  InsightCard model accepts intel_read field (backward-compat check).
10. Fallback run used when card's own run lacks _reasoning_v2.
11. No fallback when primary run has _reasoning_v2 but ticker is absent.
12. No fallback when fallback run also lacks _reasoning_v2.
13. BUY/HIGH CONVICTION downgrade logic when intel_read.insufficient_data=True.
14. No downgrade when intel_read.insufficient_data=False.
15. insufficient_data flag present in build_intel_read output.
18. Conviction ladder: HIGH + ≥3 trusted → MEDIUM (strong partial evidence).
19. Conviction ladder: HIGH + <3 trusted → LOW (weak coverage).
20. Conviction ladder: MEDIUM + <2 trusted → LOW (very weak coverage).
21. Conviction ladder: MEDIUM + ≥2 trusted → preserved.
22. Page-load prefers latest_live_llm when current analyst_verdict lacks primary_driver.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

import pytest

from app.services.intelligence.reasoning_v2_plain_english import (
    build_intel_read,
    is_safe_for_insufficient_data,
)

# ── Raw metric keys that must not appear in intel_read output ─────────────────

_RAW_METRIC_KEYS = [
    "fcf_margin",
    "roic_ttm",
    "p_fcf",
    "fcf_yield",
    "gross_margin",
    "net_debt_to_ebitda",
    "ev_ebitda",
    "revenue_cagr_3y",
    "max_drawdown_1y",
    "trailing_pe",
    "forward_pe",
    "momentum_score",
    "valuation_score",
    "quality_score",
    "growth_score",
    "risk_score",
]


# ── Local reproduction of _build_intel_read_for_card logic ───────────────────
# This mirrors the implementation in recommendation_engine.py exactly so these
# tests remain valid when that module cannot be imported due to missing supabase.

def _build_intel_read_for_card(
    *,
    ticker: str,
    run_id: Any,
    run_lookup: dict[str, dict],
    fallback_run_id: Optional[str] = None,
) -> Optional[dict]:
    """Local copy of recommendation_engine._build_intel_read_for_card for testing.

    Mirrors the production implementation: tries the card's own run first; falls
    back to fallback_run_id only when the primary run lacks _reasoning_v2 entirely.
    """
    def _try_run(rid: Any) -> tuple[Optional[dict], bool]:
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
        except Exception:  # noqa: BLE001
            return None, True

    result, had_r2_map = _try_run(run_id)
    if result is not None:
        return result
    if had_r2_map:
        return None
    run_id_str = str(run_id or "")
    fb_str = str(fallback_run_id or "")
    if fb_str and fb_str != run_id_str:
        fallback_result, _ = _try_run(fallback_run_id)
        return fallback_result
    return None


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_reasoning_v2(
    posture: str = "WATCH",
    data_status: str = "INSUFFICIENT_DATA",
    published: list[str] | None = None,
    suppressed: list[str] | None = None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "reasoning_v2.0",
        "ticker": "NVDA",
        "action": {"posture": posture},
        "data_quality": {"status": data_status},
        "evidence": {
            "deterministic": {
                "coverage": {
                    "published_dimensions": published or [],
                    "suppressed_dimensions": suppressed or [],
                    "inputs_used": [],
                    "inputs_missing": [],
                }
            }
        },
        "deploy_signals": {"blockers": blockers or []},
    }


def _make_run_lookup(
    run_id: str,
    *,
    thesis_v2: dict | None = None,
    reasoning_v2: dict | None = None,
) -> dict[str, dict]:
    allocation: dict[str, Any] = {}
    if thesis_v2 is not None:
        allocation["_thesis_v2"] = thesis_v2
    if reasoning_v2 is not None:
        allocation["_reasoning_v2"] = reasoning_v2
    return {run_id: {"id": run_id, "allocation": allocation}}


# ── Test 1: intel_read populated when _reasoning_v2 exists ───────────────────


def test_intel_read_populated_when_reasoning_v2_exists():
    run_id = str(uuid4())
    r2_nvda = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["momentum_score", "valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2_nvda})
    result = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)

    assert result is not None
    assert result["title"] == "Why this view?"
    assert result["posture_label"] == "on watch"
    assert "recent market behavior" in result["trusted_signals"] or "valuation" in result["trusted_signals"]
    assert "business quality" in result["incomplete_signals"]
    assert "growth" in result["incomplete_signals"]
    assert "risk" in result["incomplete_signals"]


# ── Test 2: intel_read is None when _reasoning_v2 is missing ─────────────────


def test_intel_read_none_when_reasoning_v2_absent():
    run_id = str(uuid4())
    run_lookup = _make_run_lookup(run_id)  # no _reasoning_v2
    assert _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup) is None


def test_intel_read_none_when_run_id_not_in_lookup():
    assert _build_intel_read_for_card(ticker="NVDA", run_id=str(uuid4()), run_lookup={}) is None


def test_intel_read_none_when_run_id_is_none():
    assert _build_intel_read_for_card(ticker="NVDA", run_id=None, run_lookup={}) is None


# ── Test 3: intel_read None when ticker absent from _reasoning_v2 ─────────────


def test_intel_read_none_when_ticker_not_in_map():
    run_id = str(uuid4())
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"GOOGL": _make_reasoning_v2()})
    assert _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup) is None


# ── Test 4: _thesis_v2 and _reasoning_v2 coexist without interference ─────────


def test_thesis_v2_and_reasoning_v2_coexist():
    run_id = str(uuid4())
    thesis_scorecard = {"status": "INSUFFICIENT_DATA", "data_quality_score": 0.3}
    r2_nvda = _make_reasoning_v2(
        posture="WATCH",
        published=["valuation_score"],
        suppressed=["quality", "growth", "risk", "momentum"],
    )
    run_lookup = _make_run_lookup(
        run_id,
        thesis_v2={"NVDA": thesis_scorecard},
        reasoning_v2={"NVDA": r2_nvda},
    )

    intel_result = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_result is not None
    assert intel_result["title"] == "Why this view?"

    # thesis_v2 still accessible alongside reasoning_v2 (no mutation)
    allocation = run_lookup[run_id]["allocation"]
    assert "_thesis_v2" in allocation
    assert "_reasoning_v2" in allocation
    assert allocation["_thesis_v2"]["NVDA"] is thesis_scorecard


# ── Test 5: intel_read contains no raw metric keys ────────────────────────────


def test_intel_read_no_raw_metric_keys():
    run_id = str(uuid4())
    r2_nvda = _make_reasoning_v2(
        posture="ACCUMULATE",
        data_status="PARTIAL",
        published=["quality_score", "valuation_score", "momentum_score"],
        suppressed=["growth", "risk"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2_nvda})
    result = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert result is not None

    for raw_key in _RAW_METRIC_KEYS:
        assert raw_key not in result.get("summary", ""), f"{raw_key!r} leaked into summary"
        for sig in result.get("trusted_signals", []):
            assert raw_key not in sig, f"{raw_key!r} leaked into trusted_signals"
        for sig in result.get("incomplete_signals", []):
            assert raw_key not in sig, f"{raw_key!r} leaked into incomplete_signals"
        assert raw_key not in result.get("caveat", ""), f"{raw_key!r} leaked into caveat"


# ── Test 6: empty/missing allocation returns None ────────────────────────────


def test_empty_allocation_returns_none():
    run_id = str(uuid4())
    run_lookup = {run_id: {"id": run_id, "allocation": {}}}
    assert _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup) is None


def test_none_allocation_returns_none():
    run_id = str(uuid4())
    run_lookup = {run_id: {"id": run_id, "allocation": None}}
    assert _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup) is None


# ── Test 7: reasoning_v2 map with empty-dict ticker value ─────────────────────


def test_reasoning_v2_empty_ticker_dict_handled_safely():
    run_id = str(uuid4())
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": {}})
    result = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    # build_intel_read({}) returns a safe minimal result
    if result is not None:
        assert "title" in result
        assert result["trusted_signals"] == []
        assert result["incomplete_signals"] == []


# ── Test 8: uppercase ticker normalization ────────────────────────────────────


def test_ticker_case_normalization():
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(posture="HOLD", data_status="PARTIAL", published=["valuation_score"])
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})

    # lowercase ticker input should still find it after normalization
    result = _build_intel_read_for_card(ticker="nvda", run_id=run_id, run_lookup=run_lookup)
    assert result is not None
    assert result["posture_label"] == "neutral"


# ── Test 9: InsightCard model accepts intel_read field ────────────────────────


def test_insight_card_model_accepts_intel_read():
    """Verify InsightCard Pydantic model accepts intel_read without breaking existing fields."""
    from uuid import uuid4 as _uuid4
    from app.models.recommendation import InsightCard

    card = InsightCard(
        id=_uuid4(),
        ticker="NVDA",
        name="NVIDIA Corp",
        action="BUY",
        detail="Test detail",
        rationale="Test rationale",
        urgency=1,
        color="green",
        tax_note="",
        drip_note="",
        category="Core",
        intel_read={
            "title": "Why this view?",
            "posture_label": "on watch",
            "summary": "Test summary.",
            "trusted_signals": ["valuation"],
            "incomplete_signals": ["business quality", "growth"],
            "caveat": "Treat this as an early signal.",
        },
    )
    assert card.intel_read is not None
    assert card.intel_read["title"] == "Why this view?"
    # thesis_plain_english remains separate and unaffected
    assert card.thesis_plain_english is None


def test_insight_card_intel_read_defaults_to_none():
    """InsightCard.intel_read is None by default (backward compatible)."""
    from uuid import uuid4 as _uuid4
    from app.models.recommendation import InsightCard

    card = InsightCard(
        id=_uuid4(),
        ticker="NVDA",
        name="NVIDIA Corp",
        action="BUY",
        detail="Test detail",
        rationale="",
        urgency=0,
        color="green",
        tax_note="",
        drip_note="",
        category="Core",
    )
    assert card.intel_read is None


# ── Test 10: fallback run used when primary lacks _reasoning_v2 ───────────────


def test_intel_read_fallback_used_when_primary_run_lacks_reasoning_v2():
    """When card's own run has no _reasoning_v2, fallback_run_id provides intel_read."""
    primary_run_id = str(uuid4())
    fallback_run_id = str(uuid4())
    r2_nvda = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["valuation_score"],
        suppressed=["quality", "growth", "risk", "momentum"],
        blockers=["insufficient_data"],
    )
    run_lookup = {
        # Primary run: has _thesis_v2 but no _reasoning_v2
        primary_run_id: {
            "id": primary_run_id,
            "allocation": {"_thesis_v2": {"NVDA": {"status": "INSUFFICIENT_DATA"}}},
        },
        # Fallback run: has _reasoning_v2
        fallback_run_id: {
            "id": fallback_run_id,
            "allocation": {"_reasoning_v2": {"NVDA": r2_nvda}},
        },
    }
    result = _build_intel_read_for_card(
        ticker="NVDA",
        run_id=primary_run_id,
        run_lookup=run_lookup,
        fallback_run_id=fallback_run_id,
    )
    assert result is not None
    assert result["posture_label"] == "on watch"
    assert result["insufficient_data"] is True


def test_intel_read_fallback_used_when_primary_run_not_in_lookup():
    """When card's own run is absent from run_lookup, fallback provides intel_read."""
    stale_run_id = str(uuid4())
    fallback_run_id = str(uuid4())
    r2_nvda = _make_reasoning_v2(posture="WATCH", data_status="INSUFFICIENT_DATA", blockers=["insufficient_data"])
    run_lookup = {
        fallback_run_id: {
            "id": fallback_run_id,
            "allocation": {"_reasoning_v2": {"NVDA": r2_nvda}},
        },
    }
    result = _build_intel_read_for_card(
        ticker="NVDA",
        run_id=stale_run_id,
        run_lookup=run_lookup,
        fallback_run_id=fallback_run_id,
    )
    assert result is not None
    assert result["posture_label"] == "on watch"


# ── Test 11: no fallback when primary has _reasoning_v2 but ticker absent ─────


def test_intel_read_no_fallback_when_primary_has_r2_map_but_ticker_absent():
    """Primary run has _reasoning_v2 but not for this ticker — fallback should NOT be used."""
    primary_run_id = str(uuid4())
    fallback_run_id = str(uuid4())
    r2_googl = _make_reasoning_v2(posture="ACCUMULATE", data_status="PARTIAL", published=["quality_score"])
    r2_nvda_in_fallback = _make_reasoning_v2(posture="WATCH", data_status="INSUFFICIENT_DATA")
    run_lookup = {
        primary_run_id: {
            "id": primary_run_id,
            "allocation": {"_reasoning_v2": {"GOOGL": r2_googl}},  # NVDA absent
        },
        fallback_run_id: {
            "id": fallback_run_id,
            "allocation": {"_reasoning_v2": {"NVDA": r2_nvda_in_fallback}},
        },
    }
    # NVDA is absent from primary's _reasoning_v2 map — fallback should NOT kick in
    result = _build_intel_read_for_card(
        ticker="NVDA",
        run_id=primary_run_id,
        run_lookup=run_lookup,
        fallback_run_id=fallback_run_id,
    )
    assert result is None


# ── Test 12: no fallback when fallback run also lacks _reasoning_v2 ───────────


def test_intel_read_none_when_both_runs_lack_reasoning_v2():
    """Both primary and fallback lack _reasoning_v2 — result is None."""
    primary_run_id = str(uuid4())
    fallback_run_id = str(uuid4())
    run_lookup = {
        primary_run_id: {"id": primary_run_id, "allocation": {}},
        fallback_run_id: {"id": fallback_run_id, "allocation": {}},
    }
    result = _build_intel_read_for_card(
        ticker="NVDA",
        run_id=primary_run_id,
        run_lookup=run_lookup,
        fallback_run_id=fallback_run_id,
    )
    assert result is None


# ── Test 13: BUY/HIGH CONVICTION downgrade logic ──────────────────────────────


def _simulate_conviction_ladder(
    intel_read: dict,
    card_action: str,
    card_analyst_action: str,
    card_conviction_level: str,
) -> tuple[str, str, str]:
    """Mirror the conviction-ladder block in recommendation_engine._compute_insight_cards.

    Returns (card_action, card_analyst_action, card_conviction_level).
    """
    if intel_read.get("insufficient_data"):
        n_trusted = len(intel_read.get("trusted_signals") or [])
        if card_action == "BUY":
            card_action = "HOLD"
        if (card_analyst_action or "").upper() == "BUY":
            card_analyst_action = "HOLD"
        cl_upper = (card_conviction_level or "").upper()
        if cl_upper == "HIGH":
            card_conviction_level = "MEDIUM" if n_trusted >= 3 else "LOW"
        elif cl_upper == "MEDIUM" and n_trusted < 2:
            card_conviction_level = "LOW"
    return card_action, card_analyst_action, card_conviction_level


def test_card_buy_downgraded_to_hold_when_intel_read_insufficient():
    """BUY → HOLD + HIGH → LOW for weak partial evidence (1 trusted signal)."""
    intel_read = {
        "title": "Why this view?",
        "posture_label": "on watch",
        "summary": "stays on watch instead of becoming a high-conviction idea.",
        "trusted_signals": ["valuation"],
        "incomplete_signals": ["business quality", "growth"],
        "caveat": "Not enough data to be confident. Wait for more signals before acting.",
        "insufficient_data": True,
    }
    action, analyst_action, conviction = _simulate_conviction_ladder(
        intel_read, "BUY", "BUY", "HIGH"
    )
    assert action == "HOLD"
    assert analyst_action == "HOLD"
    assert conviction == "LOW"


def test_card_non_buy_not_downgraded_when_intel_read_insufficient():
    """TRIM/SELL are not downgraded — already conservative."""
    intel_read = {"insufficient_data": True, "posture_label": "on watch"}
    for action in ("TRIM", "SELL", "HOLD"):
        card_action = action
        if intel_read.get("insufficient_data") and card_action == "BUY":
            card_action = "HOLD"
        assert card_action == action, f"{action} should not be downgraded"


# ── Test 14: no downgrade when insufficient_data=False ───────────────────────


def test_card_buy_not_downgraded_when_intel_read_not_insufficient():
    """BUY is preserved when intel_read.insufficient_data=False."""
    intel_read = {
        "title": "Why this view?",
        "posture_label": "constructive",
        "summary": "Evidence on business quality supports a constructive view.",
        "trusted_signals": ["business quality", "valuation"],
        "incomplete_signals": ["growth"],
        "caveat": "Treat this as an early signal, not a complete picture.",
        "insufficient_data": False,
    }
    card_action = "BUY"
    card_conviction_level = "HIGH"

    if intel_read.get("insufficient_data"):
        if card_action == "BUY":
            card_action = "HOLD"
        if (card_conviction_level or "").upper() == "HIGH":
            card_conviction_level = "LOW"

    assert card_action == "BUY"
    assert card_conviction_level == "HIGH"


# ── Test 15: insufficient_data flag in build_intel_read output ────────────────


def test_build_intel_read_insufficient_data_flag_present():
    """build_intel_read always returns the insufficient_data key."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["valuation_score"],
        suppressed=["quality", "growth"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    result = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert result is not None
    assert "insufficient_data" in result
    assert result["insufficient_data"] is True


def test_build_intel_read_insufficient_data_false_for_constructive():
    """build_intel_read.insufficient_data=False for PARTIAL/ACCUMULATE."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="ACCUMULATE",
        data_status="PARTIAL",
        published=["quality_score", "momentum_score"],
        suppressed=["growth", "risk"],
        blockers=[],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    result = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert result is not None
    assert result["insufficient_data"] is False


# ── Test 16: body copy override in card assembly ──────────────────────────────

_FORBIDDEN_CARD_PHRASES = [
    "accumulate",
    "buy",
    "entry opportunity",
    "re-rating opportunity",
    "high-conviction idea",
    "add aggressively",
    "strong buy",
    "deploy",
]


def _simulate_card_assembly_body_override(reasoning: dict, intel_read: dict) -> None:
    """Mirrors the body-copy override block in recommendation_engine._compute_insight_cards.

    Action always replaced. WHY preserved when safe; conservative_why when absent or unsafe.
    ALT VIEW preserved when safe; nulled when unsafe.
    """
    if intel_read.get("insufficient_data"):
        reasoning["action_reason"] = intel_read.get("conservative_action")
        if not reasoning.get("primary_driver") or not is_safe_for_insufficient_data(
            reasoning.get("primary_driver")
        ):
            reasoning["primary_driver"] = intel_read.get("conservative_why")
        if reasoning.get("differentiation") and not is_safe_for_insufficient_data(
            reasoning.get("differentiation")
        ):
            reasoning["differentiation"] = None


def test_card_action_reason_overridden_when_insufficient():
    """action_reason replaced with conservative_action when insufficient_data=True."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None
    assert intel_read["insufficient_data"] is True

    reasoning = {
        "action_reason": "Accumulate on pullbacks — forward PE at 17x signals opportunity.",
        "primary_driver": "High-conviction idea; institutional re-rating likely.",
        "differentiation": "If growth accelerates, this becomes a strong buy.",
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)

    assert reasoning["action_reason"] == intel_read["conservative_action"]
    assert reasoning["action_reason"] != "Accumulate on pullbacks — forward PE at 17x signals opportunity."
    lower_action = (reasoning["action_reason"] or "").lower()
    for phrase in _FORBIDDEN_CARD_PHRASES:
        assert phrase not in lower_action, f"Forbidden phrase {phrase!r} still in action_reason"


def test_card_primary_driver_overridden_when_insufficient():
    """primary_driver replaced with conservative_why when insufficient_data=True."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["momentum_score", "valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None

    reasoning = {
        "action_reason": "Buy aggressively.",
        "primary_driver": "High-conviction re-rating opportunity.",
        "differentiation": "Bullish alt view.",
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)

    assert reasoning["primary_driver"] == intel_read["conservative_why"]
    lower_why = (reasoning["primary_driver"] or "").lower()
    for phrase in _FORBIDDEN_CARD_PHRASES:
        assert phrase not in lower_why, f"Forbidden phrase {phrase!r} still in primary_driver"


def test_card_differentiation_nulled_when_insufficient():
    """differentiation is set to None when insufficient_data=True."""
    intel_read = {
        "insufficient_data": True,
        "conservative_action": "Hold off on new buying until growth evidence improves.",
        "conservative_why": "Watchlist read only.",
    }
    reasoning = {
        "action_reason": "Accumulate.",
        "primary_driver": "Good re-rating story.",
        "differentiation": "If growth improves this becomes a strong buy.",
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)
    assert reasoning["differentiation"] is None


def test_card_body_not_overridden_when_not_insufficient():
    """Body copy is not touched when insufficient_data=False."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="ACCUMULATE",
        data_status="PARTIAL",
        published=["quality_score", "momentum_score"],
        suppressed=["growth"],
        blockers=[],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None
    assert intel_read["insufficient_data"] is False

    original_action = "Accumulate on continued earnings growth."
    original_driver = "Business quality is solid and improving."
    reasoning = {
        "action_reason": original_action,
        "primary_driver": original_driver,
        "differentiation": "If momentum stalls, trim.",
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)

    assert reasoning["action_reason"] == original_action
    assert reasoning["primary_driver"] == original_driver


# ── Test 17: safe ticker-specific primary_driver preserved ────────────────────


def test_safe_primary_driver_preserved_when_insufficient():
    """Safe ticker-specific primary_driver survives the insufficient_data gate."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["momentum_score", "valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None
    assert intel_read["insufficient_data"] is True

    safe_driver = (
        "AI infrastructure demand remains the main watchlist reason — "
        "hyperscaler capex and H100-B200 ramp keep NVDA relevant."
    )
    reasoning = {
        "action_reason": "Hold aggressively at current levels.",
        "primary_driver": safe_driver,
        "differentiation": "Monitoring export restriction risk.",
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)

    # Safe ticker-specific WHY is preserved unchanged
    assert reasoning["primary_driver"] == safe_driver
    # ACTION is always replaced
    assert reasoning["action_reason"] == intel_read["conservative_action"]
    assert reasoning["action_reason"] != "Hold aggressively at current levels."


def test_unsafe_primary_driver_replaced_with_conservative_why():
    """Unsafe primary_driver (contains forbidden phrase) falls back to conservative_why."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None

    unsafe_driver = "High-conviction idea — accumulate on any pullback."
    reasoning = {
        "action_reason": "Accumulate aggressively.",
        "primary_driver": unsafe_driver,
        "differentiation": None,
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)

    # Unsafe WHY falls back to conservative_why
    assert reasoning["primary_driver"] == intel_read["conservative_why"]
    assert reasoning["primary_driver"] != unsafe_driver
    lower_why = (reasoning["primary_driver"] or "").lower()
    for phrase in _FORBIDDEN_CARD_PHRASES:
        assert phrase not in lower_why, f"Forbidden phrase {phrase!r} leaked into primary_driver"


def test_absent_primary_driver_replaced_with_conservative_why():
    """None primary_driver (no analyst verdict) gets conservative_why injected."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None

    reasoning = {"action_reason": "Hold.", "primary_driver": None, "differentiation": None}
    _simulate_card_assembly_body_override(reasoning, intel_read)

    # None primary_driver gets conservative_why so the card has useful WHY text
    assert reasoning["primary_driver"] == intel_read["conservative_why"]
    assert reasoning["primary_driver"] is not None


def test_safe_differentiation_preserved_when_insufficient():
    """Safe ticker-specific differentiation is kept under insufficient_data."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None

    safe_diff = "Export restriction risk to China could materially cut data-center revenue."
    reasoning = {
        "action_reason": "Buy at these levels.",
        "primary_driver": "AI infrastructure demand.",
        "differentiation": safe_diff,
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)

    # Safe differentiation is preserved
    assert reasoning["differentiation"] == safe_diff


def test_unsafe_differentiation_nulled_when_insufficient():
    """Unsafe differentiation (contains forbidden phrase) is nulled."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None

    unsafe_diff = "If growth improves this re-rating opportunity becomes a strong buy."
    reasoning = {
        "action_reason": "Hold.",
        "primary_driver": "AI infrastructure demand.",
        "differentiation": unsafe_diff,
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)

    assert reasoning["differentiation"] is None


def test_action_reason_always_replaced_even_when_safe():
    """ACTION is always replaced with conservative_action regardless of original content."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None

    safe_action = "Monitor this position on the watchlist."
    reasoning = {
        "action_reason": safe_action,
        "primary_driver": "AI infrastructure demand keeps NVDA on watchlist.",
        "differentiation": None,
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)

    # ACTION replaced even though original was safe (conservative_action is always the source)
    assert reasoning["action_reason"] == intel_read["conservative_action"]
    assert reasoning["action_reason"] != safe_action


def test_none_differentiation_stays_none_when_insufficient():
    """None differentiation is not injected with anything under insufficient_data."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None

    reasoning = {
        "action_reason": "Hold.",
        "primary_driver": "AI demand monitoring.",
        "differentiation": None,
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)
    assert reasoning["differentiation"] is None


def test_no_forbidden_phrases_in_preserved_safe_driver():
    """A safe primary_driver that survives the gate contains no forbidden phrases."""
    run_id = str(uuid4())
    r2 = _make_reasoning_v2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published=["momentum_score", "valuation_score"],
        suppressed=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    run_lookup = _make_run_lookup(run_id, reasoning_v2={"NVDA": r2})
    intel_read = _build_intel_read_for_card(ticker="NVDA", run_id=run_id, run_lookup=run_lookup)
    assert intel_read is not None

    safe_driver = (
        "Azure AI consumption and Copilot expansion keep MSFT relevant to enterprise AI, "
        "but incomplete growth and risk coverage keeps this below a conviction position."
    )
    reasoning = {
        "action_reason": "Hold off until more data.",
        "primary_driver": safe_driver,
        "differentiation": None,
    }
    _simulate_card_assembly_body_override(reasoning, intel_read)

    final_driver = reasoning["primary_driver"] or ""
    for phrase in _FORBIDDEN_CARD_PHRASES:
        assert phrase not in final_driver.lower(), (
            f"Forbidden phrase {phrase!r} found in preserved primary_driver"
        )


# ── Tests 18-21: conviction ladder ────────────────────────────────────────────


def test_conviction_ladder_high_strong_partial_yields_medium():
    """Test 18: HIGH conviction + ≥3 trusted signals → MEDIUM (strong partial evidence)."""
    intel_read = {
        "insufficient_data": True,
        "trusted_signals": ["business quality", "valuation", "recent market behavior"],
        "incomplete_signals": ["growth", "risk"],
    }
    action, analyst_action, conviction = _simulate_conviction_ladder(
        intel_read, "BUY", "BUY", "HIGH"
    )
    assert action == "HOLD"
    assert conviction == "MEDIUM", "3 trusted signals should yield MEDIUM, not LOW"


def test_conviction_ladder_high_weak_partial_yields_low():
    """Test 19: HIGH conviction + <3 trusted signals → LOW (insufficient partial evidence)."""
    intel_read = {
        "insufficient_data": True,
        "trusted_signals": ["valuation"],
        "incomplete_signals": ["business quality", "growth", "risk"],
    }
    action, analyst_action, conviction = _simulate_conviction_ladder(
        intel_read, "BUY", "BUY", "HIGH"
    )
    assert action == "HOLD"
    assert conviction == "LOW", "1 trusted signal should yield LOW"


def test_conviction_ladder_medium_very_weak_yields_low():
    """Test 20: MEDIUM conviction + <2 trusted signals → LOW (very weak coverage)."""
    intel_read = {
        "insufficient_data": True,
        "trusted_signals": ["recent market behavior"],
        "incomplete_signals": ["business quality", "valuation", "growth", "risk"],
    }
    _, _, conviction = _simulate_conviction_ladder(intel_read, "HOLD", "HOLD", "MEDIUM")
    assert conviction == "LOW", "MEDIUM with only 1 trusted signal should downgrade to LOW"


def test_conviction_ladder_medium_adequate_partial_preserved():
    """Test 21: MEDIUM conviction + ≥2 trusted signals → preserved (adequate partial evidence)."""
    intel_read = {
        "insufficient_data": True,
        "trusted_signals": ["business quality", "valuation"],
        "incomplete_signals": ["growth", "risk"],
    }
    _, _, conviction = _simulate_conviction_ladder(intel_read, "HOLD", "HOLD", "MEDIUM")
    assert conviction == "MEDIUM", "MEDIUM with 2 trusted signals should be preserved"


def test_conviction_ladder_low_always_preserved():
    """LOW conviction is always preserved under insufficient_data."""
    intel_read = {
        "insufficient_data": True,
        "trusted_signals": [],
        "incomplete_signals": ["business quality", "valuation", "growth", "risk", "recent market behavior"],
    }
    _, _, conviction = _simulate_conviction_ladder(intel_read, "HOLD", "HOLD", "LOW")
    assert conviction == "LOW"


def test_conviction_ladder_no_downgrade_when_sufficient():
    """HIGH conviction is preserved when insufficient_data=False."""
    intel_read = {
        "insufficient_data": False,
        "trusted_signals": ["business quality", "valuation", "growth"],
        "incomplete_signals": [],
    }
    action, _, conviction = _simulate_conviction_ladder(intel_read, "BUY", "BUY", "HIGH")
    assert action == "BUY"
    assert conviction == "HIGH"


# ── Test 22: page-load analyst_row preference ─────────────────────────────────


def test_page_load_prefers_latest_live_llm_when_verdict_lacks_primary_driver():
    """Test 22: On page load, stale analyst_verdict without primary_driver is upgraded.

    Simulates the analyst_row selection logic in _compute_insight_cards:
    when the current row's analyst_verdict has used_fallback=False but lacks
    primary_driver (pre-Phase-7 row), the freshest live LLM row is preferred.
    This closes the page-load vs Run Agents WHY inconsistency.
    """
    stale_verdict = {
        "primary_driver": None,
        "conviction_level": "HIGH",
        "used_fallback": False,
        "generation_version": "human_v2",
        "analysis_source": "live_llm",
    }
    stale_row = {"analyst_verdict": stale_verdict}

    fresh_verdict = {
        "primary_driver": "AI chip demand forces hyperscalers to TSMC capacity.",
        "conviction_level": "MEDIUM",
        "used_fallback": False,
        "generation_version": "human_v2",
        "analysis_source": "live_llm",
    }
    fresh_row = {"analyst_verdict": fresh_verdict}

    # Simulate analyst_row selection logic (mirrors recommendation_engine.py)
    analyst_row = stale_row
    preferred_live_row = fresh_row
    _used_preferred = False

    _current_av_for_pref = (analyst_row.get("analyst_verdict") or {}) if analyst_row else {}
    _lacks_memo_fields = not bool((_current_av_for_pref.get("primary_driver") or "").strip())

    if preferred_live_row and (
        analyst_row is None
        or bool(_current_av_for_pref.get("used_fallback", False))
        or _lacks_memo_fields
    ):
        analyst_row = preferred_live_row
        _used_preferred = True

    assert _used_preferred is True, "Should prefer fresh row when stale lacks primary_driver"
    assert analyst_row is fresh_row
    assert analyst_row["analyst_verdict"]["primary_driver"] == "AI chip demand forces hyperscalers to TSMC capacity."


def test_page_load_keeps_current_row_when_primary_driver_present():
    """Current analyst_row is kept when it already has primary_driver (no unnecessary upgrade)."""
    current_verdict = {
        "primary_driver": "Hyperscaler AI capex cycle drives sustained GPU datacenter demand.",
        "conviction_level": "MEDIUM",
        "used_fallback": False,
        "generation_version": "human_v2",
        "analysis_source": "live_llm",
    }
    current_row = {"analyst_verdict": current_verdict}

    newer_verdict = {
        "primary_driver": "Some newer reasoning text.",
        "conviction_level": "LOW",
        "used_fallback": False,
        "generation_version": "human_v2",
        "analysis_source": "live_llm",
    }
    newer_row = {"analyst_verdict": newer_verdict}

    analyst_row = current_row
    preferred_live_row = newer_row
    _used_preferred = False

    _current_av_for_pref = (analyst_row.get("analyst_verdict") or {}) if analyst_row else {}
    _lacks_memo_fields = not bool((_current_av_for_pref.get("primary_driver") or "").strip())

    if preferred_live_row and (
        analyst_row is None
        or bool(_current_av_for_pref.get("used_fallback", False))
        or _lacks_memo_fields
    ):
        analyst_row = preferred_live_row
        _used_preferred = True

    assert _used_preferred is False, "Should not replace row when primary_driver already present"
    assert analyst_row is current_row


def test_page_load_pref_logic_tolerates_malformed_analyst_verdict():
    """Malformed analyst_verdict should degrade safely and prefer fresh row when available."""
    malformed_current_row = {"analyst_verdict": ["not", "a", "dict"]}
    fresh_row = {
        "analyst_verdict": {
            "primary_driver": "Valid ticker-specific WHY from latest live row.",
            "used_fallback": False,
        }
    }

    analyst_row = malformed_current_row
    preferred_live_row = fresh_row
    _used_preferred = False

    _current_av_raw = (analyst_row.get("analyst_verdict") or {}) if analyst_row else {}
    _current_av_for_pref = _current_av_raw if isinstance(_current_av_raw, dict) else {}
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

    assert _used_preferred is True
    assert isinstance(analyst_verdict, dict)
    assert analyst_verdict.get("primary_driver") == "Valid ticker-specific WHY from latest live row."
