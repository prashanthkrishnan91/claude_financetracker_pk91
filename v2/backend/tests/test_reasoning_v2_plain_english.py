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

from app.services.intelligence.reasoning_v2_plain_english import (
    build_intel_read,
    is_safe_for_insufficient_data,
)

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
    assert "Not enough data" not in result["caveat"]
    assert "early signal" in result["caveat"].lower()
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
    assert "measured buy" in result["caveat"].lower() or "early signal" in result["caveat"].lower()


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
    required_keys = {
        "title", "posture_label", "summary", "trusted_signals",
        "incomplete_signals", "caveat", "insufficient_data",
        "conservative_action", "conservative_why", "bottom_line",
    }
    assert required_keys.issubset(result.keys())
    assert isinstance(result["trusted_signals"], list)
    assert isinstance(result["incomplete_signals"], list)
    assert isinstance(result["summary"], str)
    assert len(result["summary"]) > 0


# ── Test 12: insufficient_data flag ──────────────────────────────────────────


def test_insufficient_data_flag_true_when_status_insufficient():
    """insufficient_data=True when data_status==INSUFFICIENT_DATA."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score"],
        suppressed_dimensions=["quality", "growth"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["insufficient_data"] is True


def test_insufficient_data_flag_true_via_blocker_only():
    """insufficient_data=True when blocker contains 'insufficient_data' even if status is not INSUFFICIENT_DATA."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="PARTIAL",
        published_dimensions=["valuation_score"],
        suppressed_dimensions=["quality"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["insufficient_data"] is True


def test_insufficient_data_flag_false_when_partial_constructive():
    """insufficient_data=False when data is PARTIAL but posture is ACCUMULATE."""
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="PARTIAL",
        published_dimensions=["quality_score", "momentum_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=[],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["insufficient_data"] is False


def test_insufficient_data_flag_false_when_ready():
    """insufficient_data=False when data is READY."""
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="READY",
        published_dimensions=["quality_score", "valuation_score", "momentum_score"],
        suppressed_dimensions=[],
        blockers=[],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["insufficient_data"] is False


def test_insufficient_data_flag_false_for_agreement_conflict_watch():
    """insufficient_data=False for WATCH caused by agreement_conflict (not data gap)."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="PARTIAL",
        published_dimensions=["momentum_score"],
        suppressed_dimensions=["quality", "risk"],
        blockers=["agreement_conflict"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["insufficient_data"] is False


# ── Test 13: conservative_action and conservative_why fields ─────────────────


_FORBIDDEN_PHRASES = [
    "accumulate",
    "buy",
    "entry opportunity",
    "re-rating opportunity",
    "high-conviction idea",
    "add aggressively",
    "strong buy",
    "deploy",
]


def _assert_no_forbidden(text: str | None, label: str) -> None:
    if not text:
        return
    lower = text.lower()
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in lower, f"Forbidden phrase {phrase!r} found in {label}: {text!r}"


def test_conservative_action_present_when_insufficient_data():
    """conservative_action is non-None when insufficient_data=True."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score", "momentum_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["conservative_action"] is not None
    assert len(result["conservative_action"]) > 0


def test_conservative_why_present_when_insufficient_data():
    """conservative_why is non-None when insufficient_data=True."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score"],
        suppressed_dimensions=["quality", "growth"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["conservative_why"] is not None
    assert len(result["conservative_why"]) > 0


def test_conservative_fields_none_when_not_insufficient():
    """conservative_action and conservative_why are None when insufficient_data=False."""
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="PARTIAL",
        published_dimensions=["quality_score", "momentum_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=[],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["insufficient_data"] is False
    assert result["conservative_action"] is None
    assert result["conservative_why"] is None


def test_conservative_action_no_forbidden_phrases_with_incomplete_signals():
    """conservative_action does not contain forbidden bullish phrases."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    _assert_no_forbidden(result["conservative_action"], "conservative_action")
    _assert_no_forbidden(result["conservative_why"], "conservative_why")
    assert "watchlist" in (result["conservative_action"] or "").lower() or "watch" in (result["conservative_action"] or "").lower()


def test_conservative_action_no_forbidden_phrases_no_signals():
    """conservative_action degrades gracefully with no signals and no forbidden phrases."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=[],
        suppressed_dimensions=[],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    _assert_no_forbidden(result["conservative_action"], "conservative_action")
    _assert_no_forbidden(result["conservative_why"], "conservative_why")
    assert result["conservative_action"] is not None
    assert result["conservative_why"] is not None


def test_conservative_why_mentions_trusted_and_incomplete_signals():
    """conservative_why names both trusted and incomplete plain-English signals."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score", "momentum_score"],
        suppressed_dimensions=["quality", "growth"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    why = result["conservative_why"] or ""
    assert "valuation" in why or "recent market behavior" in why
    assert "business quality" in why or "growth" in why


def test_no_forbidden_phrases_in_any_insufficient_data_output():
    """No field in build_intel_read output contains forbidden bullish phrases when insufficient_data."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score", "momentum_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["insufficient_data"] is True
    _assert_no_forbidden(result["summary"], "summary")
    _assert_no_forbidden(result["caveat"], "caveat")
    _assert_no_forbidden(result["conservative_action"], "conservative_action")
    _assert_no_forbidden(result["conservative_why"], "conservative_why")
    _assert_no_forbidden(result.get("bottom_line"), "bottom_line")
    for sig in result["trusted_signals"]:
        _assert_no_forbidden(sig, "trusted_signal")
    for sig in result["incomplete_signals"]:
        _assert_no_forbidden(sig, "incomplete_signal")


# ── Test 14: bottom_line field ────────────────────────────────────────────────


def test_bottom_line_present_when_insufficient_data():
    """bottom_line is non-None when insufficient_data=True."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score", "momentum_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["insufficient_data"] is True
    assert result["bottom_line"] is not None
    assert len(result["bottom_line"]) > 0


def test_bottom_line_none_when_not_insufficient():
    """bottom_line is None when insufficient_data=False."""
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="PARTIAL",
        published_dimensions=["quality_score", "momentum_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=[],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["insufficient_data"] is False
    assert result["bottom_line"] is None


def test_bottom_line_differs_from_conservative_why():
    """bottom_line and conservative_why are not identical strings — WHY THIS VIEW and WHY are complementary."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score", "momentum_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["bottom_line"] != result["conservative_why"]


def test_conservative_why_differs_from_summary():
    """conservative_why (WHY) and summary (WHY THIS VIEW body) are not identical strings."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score", "momentum_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert result["conservative_why"] != result["summary"]


def test_conservative_why_is_concise():
    """conservative_why fits in a compact card row — must be under 220 chars."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score", "momentum_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    why = result["conservative_why"] or ""
    assert len(why) < 220, f"conservative_why too long ({len(why)} chars): {why!r}"


def test_conservative_action_starts_with_watchlist():
    """conservative_action now starts with watchlist language."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    action = result["conservative_action"] or ""
    assert "watchlist" in action.lower() or "watch" in action.lower()


def test_bottom_line_mentions_missing_signals():
    """bottom_line names incomplete signals so WHY THIS VIEW is specific."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score", "momentum_score"],
        suppressed_dimensions=["quality", "growth"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    bottom_line = result["bottom_line"] or ""
    assert "business quality" in bottom_line or "growth" in bottom_line


def test_bottom_line_no_raw_metric_keys():
    """bottom_line must not contain raw metric key names."""
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score", "momentum_score"],
        suppressed_dimensions=["quality", "growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    bottom_line = result.get("bottom_line") or ""
    for raw_key in _RAW_METRIC_KEYS:
        assert raw_key not in bottom_line, f"Raw key {raw_key!r} leaked into bottom_line"


# ── Test 15: is_safe_for_insufficient_data sanitizer ─────────────────────────


_ALL_FORBIDDEN = [
    "accumulate",
    "buy",
    "entry opportunity",
    "re-rating opportunity",
    "high-conviction idea",
    "add aggressively",
    "strong buy",
    "deploy",
]


def test_is_safe_returns_true_for_none():
    """None text is safe — no forbidden content."""
    assert is_safe_for_insufficient_data(None) is True


def test_is_safe_returns_true_for_empty_string():
    """Empty string is safe."""
    assert is_safe_for_insufficient_data("") is True


def test_is_safe_returns_true_for_safe_evidence_text():
    """Evidence-oriented watchlist copy that contains no forbidden phrases is safe."""
    safe_texts = [
        "AI infrastructure demand remains the main watchlist reason — hyperscaler capex keeps NVDA relevant.",
        "Azure AI consumption and Copilot expansion keep MSFT worth monitoring.",
        "Advanced-node foundry exposure makes TSM relevant to AI infrastructure demand.",
        "Export restriction risk to China could cut data-center revenue outlook.",
        "Evidence on valuation and recent market behavior is present, but growth is still incomplete.",
        "Stay on watchlist. Recheck after growth and risk evidence improves.",
        "Interesting setup, but growth and risk are still missing — not enough complete evidence.",
    ]
    for text in safe_texts:
        assert is_safe_for_insufficient_data(text) is True, f"Safe text incorrectly flagged: {text!r}"


@pytest.mark.parametrize("phrase", _ALL_FORBIDDEN)
def test_is_safe_returns_false_for_each_forbidden_phrase(phrase: str):
    """is_safe_for_insufficient_data returns False for each forbidden bullish phrase."""
    text_with_phrase = f"This is an interesting setup — {phrase} on dips."
    assert is_safe_for_insufficient_data(text_with_phrase) is False, (
        f"Forbidden phrase {phrase!r} not detected"
    )


def test_is_safe_case_insensitive_uppercase():
    """Forbidden phrase detection is case-insensitive for uppercase variants."""
    assert is_safe_for_insufficient_data("ACCUMULATE on pullbacks.") is False
    assert is_safe_for_insufficient_data("STRONG BUY signal detected.") is False
    assert is_safe_for_insufficient_data("This is a BUY opportunity.") is False


def test_is_safe_case_insensitive_mixed_case():
    """Forbidden phrase detection is case-insensitive for mixed-case variants."""
    assert is_safe_for_insufficient_data("Accumulate On Pullbacks.") is False
    assert is_safe_for_insufficient_data("High-Conviction Idea for this quarter.") is False


def test_is_safe_deterministic():
    """is_safe_for_insufficient_data is deterministic — same input always same output."""
    safe_text = "AI infrastructure demand keeps NVDA on watchlist."
    unsafe_text = "Accumulate on pullbacks — entry opportunity at these levels."
    for _ in range(5):
        assert is_safe_for_insufficient_data(safe_text) is True
        assert is_safe_for_insufficient_data(unsafe_text) is False


def test_is_safe_substring_match():
    """Forbidden phrase matched as substring within longer text."""
    assert is_safe_for_insufficient_data("This is a high-conviction idea based on fundamentals.") is False
    assert is_safe_for_insufficient_data("Looking at this re-rating opportunity carefully.") is False


def test_is_safe_does_not_block_non_forbidden_financial_terms():
    """Common financial terms that are not in forbidden list are allowed."""
    allowed = [
        "The position is on watchlist for monitoring.",
        "Risk of export restrictions to China remains elevated.",
        "Growth coverage is still incomplete — not enough evidence.",
        "Trim this position if valuation extends further.",
        "Neutral stance — hold the current position.",
        "The data-center capex cycle may slow in 2026.",
    ]
    for text in allowed:
        assert is_safe_for_insufficient_data(text) is True, f"Falsely blocked: {text!r}"


def test_is_safe_allows_buyback_and_repurchase_context():
    assert is_safe_for_insufficient_data("Company expanded its share buyback program this quarter.") is True
    assert is_safe_for_insufficient_data("Management approved a repurchase program extension.") is True
    assert is_safe_for_insufficient_data("Share repurchase pace remains steady versus last year.") is True


@pytest.mark.parametrize(
    "text",
    [
        "This is a buy now setup.",
        "A clear buy signal is forming.",
        "Stock looks buy-ready after this pullback.",
        "Consider add shares on weakness.",
        "We can accumulate shares at these levels.",
        "This becomes an entry point after earnings.",
        "Could be an entry opportunity if volatility settles.",
        "Time to deploy capital aggressively here.",
    ],
)
def test_is_safe_blocks_action_directive_variants(text: str):
    assert is_safe_for_insufficient_data(text) is False

def test_bottom_line_avoids_generic_interesting_setup_phrase():
    r2 = _make_r2(
        posture="WATCH",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    bottom_line = (result.get("bottom_line") or "").lower()
    assert "interesting setup" not in bottom_line
    assert "valuation" in bottom_line
    assert "growth" in bottom_line or "risk" in bottom_line


def test_buy_with_trusted_signals_avoids_global_wait_language():
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["quality_score", "valuation_score", "momentum_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    caveat = result["caveat"].lower()
    assert "not enough data to be confident" not in caveat
    assert "wait for more signals" not in caveat
    assert "measured buy" in caveat
    assert set(result["incomplete_signals"]) == {"growth", "risk"}


@pytest.mark.parametrize("published_dimensions", [["valuation_score"], ["valuation_score", "momentum_score"]])
def test_buy_with_one_or_two_trusted_signals_stays_action_consistent(published_dimensions: list[str]):
    r2 = _make_r2(
        posture="ACCUMULATE",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=published_dimensions,
        suppressed_dimensions=["growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert "measured buy" in result["caveat"].lower()


def test_hold_with_zero_trusted_signals_keeps_wait_copy():
    r2 = _make_r2(
        posture="HOLD",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=[],
        suppressed_dimensions=["quality", "valuation", "growth", "risk", "momentum"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    assert "wait for more signals" in result["caveat"].lower()


def test_hold_with_trusted_signals_does_not_imply_zero_evidence():
    r2 = _make_r2(
        posture="HOLD",
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    caveat = result["caveat"].lower()
    assert "wait for more signals" not in caveat
    assert "usable evidence" in caveat


@pytest.mark.parametrize("posture", ["TRIM", "AVOID"])
def test_trim_sell_family_copy_stays_risk_aware(posture: str):
    r2 = _make_r2(
        posture=posture,
        data_status="INSUFFICIENT_DATA",
        published_dimensions=["valuation_score", "momentum_score"],
        suppressed_dimensions=["growth", "risk"],
        blockers=["insufficient_data"],
    )
    result = build_intel_read(r2)
    assert result is not None
    caveat = result["caveat"].lower()
    assert "cautious stance" in caveat
    assert "wait for more signals" not in caveat
