"""Alert Trigger Policy v1 — unit tests.

Evidence band note: snapshot_builder._EVIDENCE_QUALITY_TO_BAND maps AxisBand.OK →
"PARTIAL". Production snapshot cards carry evidence_band="PARTIAL" for what the axis
layer considers "OK" quality. The policy accepts STRONG and PARTIAL as actionable;
THIN, SUPPRESSED, blank, and unknown bands are non-actionable.

Covers:
  1.  No candidates when no prior snapshot exists (conservative baseline)
  2.  New BUY candidate when action changes HOLD → BUY (PARTIAL evidence)
  3.  New TRIM candidate when action changes HOLD → TRIM (PARTIAL evidence)
  4.  New SELL candidate when action changes HOLD → SELL (STRONG evidence)
  5.  No candidate when action stays HOLD in both snapshots
  6.  Candidate deduplicated: same snapshot_id produces same dedupe_key
  7.  Weak/THIN evidence suppresses actionable candidate (suppression, not candidate)
  8.  Weak/SUPPRESSED evidence suppresses actionable candidate
  9.  Strong evidence + BUY → candidate created
  10. Snoozed feedback suppresses repeated candidate (within cooldown window)
  11. Snoozed feedback with explicit cooldown_until suppresses
  12. Snoozed feedback beyond cooldown window does NOT suppress
  13. Ignored feedback suppresses within 7-day cooldown
  14. Ignored feedback beyond cooldown does NOT suppress
  15. not_relevant feedback suppresses within 7-day cooldown
  16. too_risky feedback suppresses within 7-day cooldown
  17. Executed feedback suppresses indefinitely
  18. user_note feedback does NOT suppress
  19. skipped feedback does NOT suppress
  20. Conviction upgrade BUY LOW→MEDIUM with PARTIAL evidence creates candidate
  21. Conviction upgrade BUY LOW→HIGH with STRONG evidence creates candidate
  22. Conviction upgrade with THIN evidence does NOT create candidate
  23. Conviction stays same → no conviction_upgrade candidate
  24. Conviction downgrade → no conviction_upgrade candidate
  25. User isolation: feedback for user B does not suppress candidate for user A
  26. Ticker isolation: feedback for MSFT does not suppress AAPL candidate
  27. Action isolation: executed BUY feedback does not suppress TRIM candidate
  28. No mutation of input card lists or feedback rows
  29. Empty current_holdings → empty result
  30. New ticker in current not in prior → creates candidate (prior snapshot exists)
  31. PARTIAL evidence + HOLD→BUY creates candidate (production-shape card)
  32. PARTIAL evidence + BUY conviction upgrade LOW→MEDIUM creates candidate
  33. SUPPRESSED evidence suppresses BUY candidate
  34. Missing/blank evidence_band suppresses BUY candidate
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services.alert.alert_trigger_policy_v1 import (
    POLICY_VERSION,
    AlertPolicyResult,
    evaluate_snapshot_for_alert_candidates,
)

# ── Test helpers ────────────────────────────────────────────────────────────────

_USER_A = str(uuid.uuid4())
_USER_B = str(uuid.uuid4())
_SNAP_ID = str(uuid.uuid4())
_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def _card(
    ticker: str = "AAPL",
    action: str = "BUY",
    conviction: str = "MEDIUM",
    evidence_band: str = "PARTIAL",  # matches snapshot_builder AxisBand.OK → "PARTIAL"
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "action": action,
        "conviction": conviction,
        "evidence_band": evidence_band,
    }


def _feedback_row(
    user_id: str = _USER_A,
    ticker: str = "AAPL",
    feedback_type: str = "ignored",
    action_type: str | None = "BUY",
    created_at: datetime | None = None,
    cooldown_until: datetime | None = None,
) -> dict[str, Any]:
    if created_at is None:
        created_at = _NOW - timedelta(days=1)  # 1 day ago by default
    row: dict[str, Any] = {
        "user_id": user_id,
        "ticker": ticker,
        "feedback_type": feedback_type,
        "action_type": action_type,
        "created_at": created_at.isoformat(),
    }
    if cooldown_until is not None:
        row["cooldown_until"] = cooldown_until.isoformat()
    return row


def _evaluate(
    current_cards: list[dict],
    prior_cards: list[dict] | None,
    feedback: list[dict] | None = None,
    user_id: str = _USER_A,
    snapshot_id: str = _SNAP_ID,
) -> AlertPolicyResult:
    return evaluate_snapshot_for_alert_candidates(
        user_id=user_id,
        current_snapshot_cards=current_cards,
        prior_snapshot_cards=prior_cards,
        feedback_rows=feedback or [],
        snapshot_id=snapshot_id,
        now=_NOW,
    )


# ── Test 1: No prior snapshot → no candidates (conservative) ───────────────────

def test_no_prior_snapshot_produces_no_candidates():
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=None,
    )
    assert result.candidates == []
    assert result.suppressions == []
    assert result.evaluated_ticker_count == 1
    assert result.policy_version == POLICY_VERSION


# ── Tests 2–4: New actionable action ──────────────────────────────────────────

def test_buy_candidate_when_action_changes_hold_to_buy():
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", conviction="MEDIUM", evidence_band="PARTIAL")],
        prior_cards=[_card("AAPL", action="HOLD")],
    )
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.ticker == "AAPL"
    assert c.candidate_type == "new_actionable_action"
    assert c.action_type == "BUY"
    assert c.source_area == "intel"
    assert c.reason_code == "action_became_buy"
    assert c.policy_version == POLICY_VERSION
    assert "BUY" in c.plain_english_reason or "buy" in c.plain_english_reason.lower()


def test_trim_candidate_when_action_changes_hold_to_trim():
    result = _evaluate(
        current_cards=[_card("MSFT", action="TRIM", conviction="MEDIUM", evidence_band="PARTIAL")],
        prior_cards=[_card("MSFT", action="HOLD")],
    )
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.ticker == "MSFT"
    assert c.action_type == "TRIM"
    assert c.reason_code == "action_became_trim"
    assert c.severity == "normal"


def test_sell_candidate_when_action_changes_hold_to_sell():
    result = _evaluate(
        current_cards=[_card("TSLA", action="SELL", conviction="MEDIUM", evidence_band="STRONG")],
        prior_cards=[_card("TSLA", action="HOLD")],
    )
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.action_type == "SELL"
    assert c.severity == "high"


# ── Test 5: HOLD stays HOLD → no candidate ────────────────────────────────────

def test_no_candidate_when_hold_stays_hold():
    result = _evaluate(
        current_cards=[_card("AAPL", action="HOLD")],
        prior_cards=[_card("AAPL", action="HOLD")],
    )
    assert result.candidates == []
    assert result.suppressions == []


# ── Test 6: Same snapshot_id → same dedupe_key ────────────────────────────────

def test_same_snapshot_id_produces_same_dedupe_key():
    snap_id = str(uuid.uuid4())
    r1 = evaluate_snapshot_for_alert_candidates(
        user_id=_USER_A,
        current_snapshot_cards=[_card("AAPL", action="BUY")],
        prior_snapshot_cards=[_card("AAPL", action="HOLD")],
        feedback_rows=[],
        snapshot_id=snap_id,
        now=_NOW,
    )
    r2 = evaluate_snapshot_for_alert_candidates(
        user_id=_USER_A,
        current_snapshot_cards=[_card("AAPL", action="BUY")],
        prior_snapshot_cards=[_card("AAPL", action="HOLD")],
        feedback_rows=[],
        snapshot_id=snap_id,
        now=_NOW,
    )
    assert len(r1.candidates) == 1
    assert len(r2.candidates) == 1
    assert r1.candidates[0].dedupe_key == r2.candidates[0].dedupe_key


# ── Tests 7–8: Weak evidence suppresses candidates ────────────────────────────

def test_thin_evidence_suppresses_buy_candidate():
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", evidence_band="THIN")],
        prior_cards=[_card("AAPL", action="HOLD")],
    )
    assert result.candidates == []
    assert len(result.suppressions) == 1
    sup = result.suppressions[0]
    assert sup.ticker == "AAPL"
    assert "THIN" in sup.suppression_reason
    assert "STRONG or PARTIAL" in sup.suppression_reason


def test_suppressed_evidence_suppresses_trim_candidate():
    result = _evaluate(
        current_cards=[_card("MSFT", action="TRIM", evidence_band="SUPPRESSED")],
        prior_cards=[_card("MSFT", action="HOLD")],
    )
    assert result.candidates == []
    assert len(result.suppressions) == 1
    assert "SUPPRESSED" in result.suppressions[0].suppression_reason


# ── Test 9: Strong evidence + BUY → candidate created ────────────────────────

def test_strong_evidence_buy_creates_candidate():
    result = _evaluate(
        current_cards=[_card("NVDA", action="BUY", conviction="HIGH", evidence_band="STRONG")],
        prior_cards=[_card("NVDA", action="HOLD")],
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].severity == "high"  # HIGH conviction BUY → high severity


# ── Tests 10–12: Snoozed feedback suppression ─────────────────────────────────

def test_snoozed_feedback_within_default_cooldown_suppresses():
    fb = _feedback_row(
        feedback_type="snoozed",
        action_type="BUY",
        created_at=_NOW - timedelta(days=3),  # 3 days ago < 14-day window
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb],
    )
    assert result.candidates == []
    assert len(result.suppressions) == 1
    assert "snoozed" in result.suppressions[0].suppression_reason.lower()


def test_snoozed_with_explicit_cooldown_until_suppresses():
    future = _NOW + timedelta(days=5)
    fb = _feedback_row(
        feedback_type="snoozed",
        action_type="BUY",
        created_at=_NOW - timedelta(days=20),  # old, but cooldown_until in future
        cooldown_until=future,
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb],
    )
    assert result.candidates == []
    assert len(result.suppressions) == 1


def test_snoozed_beyond_default_cooldown_does_not_suppress():
    fb = _feedback_row(
        feedback_type="snoozed",
        action_type="BUY",
        created_at=_NOW - timedelta(days=20),  # 20 days ago > 14-day window
        cooldown_until=None,
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb],
    )
    assert len(result.candidates) == 1


# ── Tests 13–16: Cooldown-based feedback types ────────────────────────────────

def test_ignored_within_7_days_suppresses():
    fb = _feedback_row(
        feedback_type="ignored",
        created_at=_NOW - timedelta(days=4),
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb],
    )
    assert result.candidates == []


def test_ignored_beyond_7_days_does_not_suppress():
    fb = _feedback_row(
        feedback_type="ignored",
        created_at=_NOW - timedelta(days=8),
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb],
    )
    assert len(result.candidates) == 1


def test_not_relevant_within_7_days_suppresses():
    fb = _feedback_row(
        feedback_type="not_relevant",
        created_at=_NOW - timedelta(days=2),
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb],
    )
    assert result.candidates == []


def test_too_risky_within_7_days_suppresses():
    fb = _feedback_row(
        feedback_type="too_risky",
        created_at=_NOW - timedelta(days=5),
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb],
    )
    assert result.candidates == []


# ── Test 17: Executed suppresses indefinitely ─────────────────────────────────

def test_executed_suppresses_indefinitely():
    # executed 100 days ago — still suppresses
    fb = _feedback_row(
        feedback_type="executed",
        action_type="BUY",
        created_at=_NOW - timedelta(days=100),
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb],
    )
    assert result.candidates == []
    assert "executed" in result.suppressions[0].suppression_reason.lower()


# ── Tests 18–19: Non-suppressing feedback types ───────────────────────────────

def test_user_note_does_not_suppress():
    fb = _feedback_row(
        feedback_type="user_note",
        created_at=_NOW - timedelta(days=1),
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb],
    )
    assert len(result.candidates) == 1


def test_skipped_does_not_suppress():
    fb = _feedback_row(
        feedback_type="skipped",
        created_at=_NOW - timedelta(days=1),
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb],
    )
    assert len(result.candidates) == 1


# ── Tests 20–23: Conviction upgrade ───────────────────────────────────────────

def test_conviction_upgrade_low_to_medium_partial_evidence():
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", conviction="MEDIUM", evidence_band="PARTIAL")],
        prior_cards=[_card("AAPL", action="BUY", conviction="LOW", evidence_band="PARTIAL")],
    )
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.candidate_type == "conviction_upgrade"
    assert c.reason_code == "buy_conviction_upgraded"
    assert "LOW" in c.plain_english_reason
    assert "MEDIUM" in c.plain_english_reason


def test_conviction_upgrade_low_to_high_strong_evidence():
    result = _evaluate(
        current_cards=[_card("MSFT", action="BUY", conviction="HIGH", evidence_band="STRONG")],
        prior_cards=[_card("MSFT", action="BUY", conviction="LOW", evidence_band="STRONG")],
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].candidate_type == "conviction_upgrade"


def test_conviction_upgrade_thin_evidence_does_not_create_candidate():
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", conviction="MEDIUM", evidence_band="THIN")],
        prior_cards=[_card("AAPL", action="BUY", conviction="LOW", evidence_band="THIN")],
    )
    # THIN evidence → the card hits the weak-evidence suppression path before
    # conviction upgrade logic is reached.
    assert result.candidates == []


def test_same_conviction_no_upgrade_candidate():
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", conviction="MEDIUM", evidence_band="PARTIAL")],
        prior_cards=[_card("AAPL", action="BUY", conviction="MEDIUM", evidence_band="PARTIAL")],
    )
    # Action unchanged (BUY→BUY) and conviction unchanged → no candidate
    assert result.candidates == []


def test_conviction_downgrade_no_candidate():
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", conviction="LOW", evidence_band="PARTIAL")],
        prior_cards=[_card("AAPL", action="BUY", conviction="HIGH", evidence_band="PARTIAL")],
    )
    assert result.candidates == []


# ── Tests 25–27: Isolation ────────────────────────────────────────────────────

def test_user_isolation_feedback_for_user_b_does_not_suppress_user_a():
    fb_user_b = _feedback_row(
        user_id=_USER_B,
        ticker="AAPL",
        feedback_type="ignored",
        created_at=_NOW - timedelta(days=1),
    )
    result = evaluate_snapshot_for_alert_candidates(
        user_id=_USER_A,
        current_snapshot_cards=[_card("AAPL", action="BUY")],
        prior_snapshot_cards=[_card("AAPL", action="HOLD")],
        feedback_rows=[fb_user_b],
        snapshot_id=_SNAP_ID,
        now=_NOW,
    )
    assert len(result.candidates) == 1


def test_ticker_isolation_msft_feedback_does_not_suppress_aapl():
    fb_msft = _feedback_row(
        ticker="MSFT",
        feedback_type="ignored",
        created_at=_NOW - timedelta(days=1),
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb_msft],
    )
    assert len(result.candidates) == 1


def test_action_isolation_executed_buy_does_not_suppress_trim():
    # User executed a BUY for AAPL — should not suppress a TRIM candidate
    fb_buy = _feedback_row(
        ticker="AAPL",
        feedback_type="executed",
        action_type="BUY",
        created_at=_NOW - timedelta(days=1),
    )
    result = _evaluate(
        current_cards=[_card("AAPL", action="TRIM", evidence_band="PARTIAL")],
        prior_cards=[_card("AAPL", action="HOLD")],
        feedback=[fb_buy],
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].action_type == "TRIM"


# ── Test 28: No mutation of inputs ────────────────────────────────────────────

def test_inputs_not_mutated():
    current = [_card("AAPL", action="BUY")]
    prior = [_card("AAPL", action="HOLD")]
    feedback = [_feedback_row(feedback_type="ignored")]

    current_copy = copy.deepcopy(current)
    prior_copy = copy.deepcopy(prior)
    feedback_copy = copy.deepcopy(feedback)

    _evaluate(current, prior, feedback)

    assert current == current_copy
    assert prior == prior_copy
    assert feedback == feedback_copy


# ── Test 29: Empty current holdings ──────────────────────────────────────────

def test_empty_current_holdings_returns_empty_result():
    result = _evaluate(
        current_cards=[],
        prior_cards=[_card("AAPL", action="HOLD")],
    )
    assert result.candidates == []
    assert result.suppressions == []
    assert result.evaluated_ticker_count == 0


# ── Test 30: New ticker in current not in prior ───────────────────────────────

def test_new_ticker_in_current_creates_candidate():
    result = _evaluate(
        current_cards=[
            _card("AAPL", action="HOLD"),
            _card("NVDA", action="BUY", conviction="HIGH", evidence_band="STRONG"),
        ],
        prior_cards=[
            _card("AAPL", action="HOLD"),
            # NVDA not in prior
        ],
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].ticker == "NVDA"
    assert result.candidates[0].candidate_type == "new_actionable_action"


# ── Additional: severity mapping ──────────────────────────────────────────────

def test_severity_sell_is_high():
    result = _evaluate(
        current_cards=[_card("AAPL", action="SELL", conviction="MEDIUM", evidence_band="PARTIAL")],
        prior_cards=[_card("AAPL", action="HOLD")],
    )
    assert result.candidates[0].severity == "high"


def test_severity_buy_medium_conviction_is_normal():
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", conviction="MEDIUM", evidence_band="PARTIAL")],
        prior_cards=[_card("AAPL", action="HOLD")],
    )
    assert result.candidates[0].severity == "normal"


def test_severity_buy_low_conviction_is_low():
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", conviction="LOW", evidence_band="PARTIAL")],
        prior_cards=[_card("AAPL", action="HOLD")],
    )
    assert result.candidates[0].severity == "low"


# ── Additional: source fields populated ──────────────────────────────────────

def test_source_snapshot_id_populated_in_candidate():
    snap = str(uuid.uuid4())
    result = evaluate_snapshot_for_alert_candidates(
        user_id=_USER_A,
        current_snapshot_cards=[_card("AAPL", action="BUY")],
        prior_snapshot_cards=[_card("AAPL", action="HOLD")],
        feedback_rows=[],
        snapshot_id=snap,
        now=_NOW,
    )
    assert result.candidates[0].source_snapshot_id == snap


# ── Additional: no mutation of Intel/Deploy/Watchtower data ──────────────────

def test_no_intel_source_queried_during_policy_evaluation():
    """Policy module has no DB imports — purely functional, no IO."""
    import inspect
    import app.services.alert.alert_trigger_policy_v1 as module

    source = inspect.getsource(module)
    # Must not import the supabase client or any DB-touching utilities
    assert "get_supabase_client" not in source
    assert "supabase" not in source.lower() or "supabase" not in source


# ── Tests 31–34: Production-shape evidence band coverage ─────────────────────
# snapshot_builder._EVIDENCE_QUALITY_TO_BAND: AxisBand.OK → "PARTIAL"
# Real production cards carry evidence_band="PARTIAL" for AxisBand.OK quality.

def test_partial_evidence_hold_to_buy_creates_candidate():
    """PARTIAL = AxisBand.OK; confirms production-shape cards produce candidates."""
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", conviction="MEDIUM", evidence_band="PARTIAL")],
        prior_cards=[_card("AAPL", action="HOLD")],
    )
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.action_type == "BUY"
    assert c.candidate_type == "new_actionable_action"


def test_partial_evidence_conviction_upgrade_creates_candidate():
    """PARTIAL evidence does not block conviction_upgrade for sustained BUY."""
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", conviction="MEDIUM", evidence_band="PARTIAL")],
        prior_cards=[_card("AAPL", action="BUY", conviction="LOW", evidence_band="PARTIAL")],
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].candidate_type == "conviction_upgrade"


def test_suppressed_band_suppresses_buy_candidate():
    result = _evaluate(
        current_cards=[_card("AAPL", action="BUY", evidence_band="SUPPRESSED")],
        prior_cards=[_card("AAPL", action="HOLD")],
    )
    assert result.candidates == []
    assert len(result.suppressions) == 1
    assert "SUPPRESSED" in result.suppressions[0].suppression_reason


def test_missing_evidence_band_suppresses_buy_candidate():
    card = {"ticker": "AAPL", "action": "BUY", "conviction": "MEDIUM"}  # no evidence_band key
    result = _evaluate(
        current_cards=[card],
        prior_cards=[_card("AAPL", action="HOLD")],
    )
    assert result.candidates == []
    assert len(result.suppressions) == 1
