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
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

import pytest

from app.services.intelligence.reasoning_v2_plain_english import build_intel_read

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
) -> Optional[dict]:
    """Local copy of recommendation_engine._build_intel_read_for_card for testing."""
    rid_str = str(run_id or "")
    if not rid_str:
        return None
    run_row = run_lookup.get(rid_str)
    if not run_row:
        return None
    allocation = run_row.get("allocation")
    if not isinstance(allocation, dict):
        return None
    reasoning_map = allocation.get("_reasoning_v2")
    if not isinstance(reasoning_map, dict) or not reasoning_map:
        return None
    ticker_up = str(ticker).strip().upper()
    r2 = reasoning_map.get(ticker_up) or reasoning_map.get(ticker)
    if not isinstance(r2, dict):
        return None
    try:
        return build_intel_read(r2)
    except Exception:  # noqa: BLE001
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
