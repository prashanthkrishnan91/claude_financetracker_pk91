"""Tests for reasoning_v2 plain-English translator.

Coverage:
1.  INSUFFICIENT_DATA + WATCH + valuation/momentum published + quality/growth/risk suppressed.
2.  quality newly publishable (PARTIAL status, constructive posture).
3.  Missing reasoning_v2 (None input) returns None safely.
4.  Raw metric key redaction / leak regression.
5.  Deterministic output (same inputs → identical dict).
6.  No published, no suppressed → safe empty message.
7.  All dimensions published, non-WATCH posture.
8.  Agreement conflict blocker → cautious caveat.
9.  Incomplete-only (no published) → honest message.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.services.intelligence.reasoning_v2_plain_english import build_intel_read

# ── Forbidden raw metric keys (must never appear in output) ──────────────────

_RAW_METRIC_KEYS = [
    "fcf_margin",
    "roic_ttm",
    "p_fcf",
    "fcf_yield",
    "gross_margin",
    "fcf_to_net_income",
    "net_debt_to_ebitda",
    "ev_ebitda",
    "revenue_cagr_3y",
    "max_drawdown_1y",
    "trailing_pe",
    "forward_pe",
    "momentum_score",  # raw ev key — must not appear in output text
    "valuation_score",
    "quality_score",
    "growth_score",
    "risk_score",
]


def _make_r2(
    *,
    posture: str = "WATCH",
    data_status: str = "INSUFFICIENT_DATA",
    published_dimensions: list[str] | None = None,
    suppressed_dimensions: list[str] | None = None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal reasoning_v2 dict for testing."""
    return {
        "action": {"posture": posture},
        "data_quality": {"status": data_status},
        "evidence": {
            "deterministic": {
                "coverage": {
                    "published_dimensions": published_dimensions or [],
                    "suppressed_dimensions": suppressed_dimensions or [],
                    "inputs_used": [],
                    "inputs_missing": [],
                }
            }
        },
        "deploy_signals": {
            "blockers": blockers or [],
        },
    }


# ── Test 1: INSUFFICIENT_DATA + WATCH + valuation/momentum published ─────────


def test_insufficient_data_watch_partial_coverage():
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["momentum_score", "valuation_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["posture_label"] == "on watch"
    assert set(result["trusted_signals"]) == {"recent market behavior", "valuation"}
    assert set(result["incomplete_signals"]) == {"business quality", "growth", "risk"}
    assert "watch" in result["summary"].lower()
    assert "incomplete" in result["summary"].lower() or "still" in result["summary"].lower()
    assert "caveat" in result
    assert "Not enough data" in result["caveat"]
    assert result["title"] == "Why this view?"


def test_insufficient_data_watch_summary_mentions_both():
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["momentum_score", "valuation_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    summary = result["summary"]
    # Both published plain-English labels should appear in summary
    assert "valuation" in summary or "recent market behavior" in summary
    assert "business quality" in summary or "growth" in summary or "risk" in summary


# ── Test 2: quality newly publishable (PARTIAL) ──────────────────────────────


def test_quality_newly_publishable_partial():
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="PARTIAL",
        published_dimensions=["quality_score", "momentum_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=[],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["posture_label"] == "constructive"
    assert "business quality" in result["trusted_signals"]
    assert "recent market behavior" in result["trusted_signals"]
    assert "growth" in result["incomplete_signals"]
    assert "risk" in result["incomplete_signals"]
    # Summary should describe the partial coverage honestly
    assert "caveat" in result
    assert "early signal" in result["caveat"] or "complete" in result["caveat"]


def test_quality_publishable_non_watch_summary():
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="PARTIAL",
        published_dimensions=["quality_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=[],
    )
    result = build_intel_read(r2)
    assert result is not None
    summary = result["summary"]
    assert "business quality" in summary
    assert "growth" in summary or "risk" in summary


# ── Test 3: missing reasoning_v2 returns None ────────────────────────────────


def test_none_input_returns_none():
    assert build_intel_read(None) is None


def test_non_dict_returns_none():
    assert build_intel_read("not a dict") is None  # type: ignore[arg-type]
    assert build_intel_read(42) is None  # type: ignore[arg-type]
    assert build_intel_read([]) is None  # type: ignore[arg-type]


def test_empty_dict_safe():
    result = build_intel_read({})
    assert result is not None
    assert result["posture_label"] == "on watch"
    assert result["trusted_signals"] == []
    assert result["incomplete_signals"] == []


# ── Test 4: raw metric key redaction / leak regression ───────────────────────


def test_no_raw_metric_keys_in_output():
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["momentum_score", "valuation_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    output_str = str(result)
    for raw_key in _RAW_METRIC_KEYS:
        # Keys should not appear as standalone words in output text values
        assert raw_key not in result.get("summary", ""), f"Raw key {raw_key!r} leaked into summary"
        assert raw_key not in result.get("caveat", ""), f"Raw key {raw_key!r} leaked into caveat"
        assert raw_key not in result.get("posture_label", ""), f"Raw key {raw_key!r} leaked into posture_label"
        for sig in result.get("trusted_signals", []):
            assert raw_key not in sig, f"Raw key {raw_key!r} leaked into trusted_signals"
        for sig in result.get("incomplete_signals", []):
            assert raw_key not in sig, f"Raw key {raw_key!r} leaked into incomplete_signals"


def test_no_raw_metric_keys_constructive():
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="PARTIAL",
        published_dimensions=["quality_score", "valuation_score", "momentum_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=[],
    )
    result = build_intel_read(r2)
    assert result is not None
    for raw_key in _RAW_METRIC_KEYS:
        assert raw_key not in result.get("summary", "")
        for sig in result.get("trusted_signals", []):
            assert raw_key not in sig
        for sig in result.get("incomplete_signals", []):
            assert raw_key not in sig


# ── Test 5: deterministic output ─────────────────────────────────────────────


def test_deterministic_output():
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["momentum_score", "valuation_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result1 = build_intel_read(copy.deepcopy(r2))
    result2 = build_intel_read(copy.deepcopy(r2))
    assert result1 == result2


def test_deterministic_partial():
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="PARTIAL",
        published_dimensions=["quality_score", "momentum_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=[],
    )
    for _ in range(3):
        assert build_intel_read(copy.deepcopy(r2)) == build_intel_read(copy.deepcopy(r2))


# ── Test 6: no published, no suppressed → safe empty message ─────────────────


def test_no_coverage_at_all():
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=[],
        suppressed_dimensions=[],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["trusted_signals"] == []
    assert result["incomplete_signals"] == []
    assert len(result["summary"]) > 0
    assert "Not enough evidence" in result["summary"] or "watch" in result["summary"].lower()


# ── Test 7: all dimensions published, non-WATCH ───────────────────────────────


def test_all_published_constructive():
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="READY",
        published_dimensions=[
            "quality_score",
            "valuation_score",
            "growth_score",
            "risk_score",
            "momentum_score",
        ],
        suppressed_dimensions=[],
        blockers=[],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["posture_label"] == "constructive"
    assert result["incomplete_signals"] == []
    assert len(result["trusted_signals"]) == 5
    assert "constructive" in result["summary"]


# ── Test 8: agreement conflict blocker ───────────────────────────────────────


def test_agreement_conflict_caveat():
    r2 = _make_r2(
        posture="WATCH",
        data_status="PARTIAL",
        published_dimensions=["momentum_score"],
        suppressed_dimensions=["quality", "risk"],
        blockers=["agreement_conflict"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert "conflict" in result["caveat"].lower()


# ── Test 9: incomplete-only (no published dimensions) ────────────────────────


def test_incomplete_only_honest_message():
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=[],
        suppressed_dimensions=["quality", "valuation", "growth", "risk", "momentum"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["trusted_signals"] == []
    assert len(result["incomplete_signals"]) == 5
    summary = result["summary"]
    # Should mention incomplete state honestly
    assert "incomplete" in summary.lower() or "not enough" in summary.lower()


# ── Test 10: posture label coverage ──────────────────────────────────────────


@pytest.mark.parametrize("posture,expected_label", [
    ("ACCUMULATE", "constructive"),
    ("HOLD", "neutral"),
    ("TRIM", "cautious"),
    ("AVOID", "cautious"),
    ("WATCH", "on watch"),
    ("UNKNOWN_POSTURE", "on watch"),  # fallback to WATCH
])
def test_posture_label_mapping(posture: str, expected_label: str):
    r2 = _make_r2(posture=posture, data_status="PARTIAL")
    result = build_intel_read(r2)
    assert result is not None
    assert result["posture_label"] == expected_label


# ── Test 11: output schema completeness ──────────────────────────────────────


def test_output_schema_keys():
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score"],
        suppressed_dimensions=["quality", "growth"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    required_keys = {"title", "posture_label", "summary", "trusted_signals", "incomplete_signals", "caveat"}
    assert required_keys.issubset(result.keys())
    assert isinstance(result["trusted_signals"], list)
    assert isinstance(result["incomplete_signals"], list)
    assert isinstance(result["summary"], str)
    assert len(result["summary"]) > 0
