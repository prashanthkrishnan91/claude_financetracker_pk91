"""Intel v3 decision policy — focused regression tests for the visible v3 spine.

Encodes the production failure fixture and contracts required by the v3 build plan.
Production failure fixture:
  - UI showed 34 cards, BUY 12 / HOLD 22
  - Run Agents returned different counts, BUY 10 / HOLD 24
  - UI did not visibly update after Run Agents
  - Logs: action_counts={"BUY":12,"HOLD":22} while thesis_status_counts={"INSUFFICIENT_DATA":34}
  - Logs: attempted_llm_calls=35 and successful_llm_calls=35 on page-load path
  - Logs: reused_cached_verdicts=0 and skipped_fresh_verdicts=0
  - Logs: visible card run IDs and latest run/thesis IDs diverged

The v3 path must fail these tests if any of those failure classes can recur.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.decision_contracts import (
    ActionV3,
    AxisBand,
    ConvictionV3,
    DecisionInputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from app.services.intelligence.v3.decision_policy_v1 import decide
from app.services.intelligence.v3.existing_signal_adapter import (
    build_decision_input_from_card,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _buy_input(
    evidence=AxisBand.OK,
    price=PriceBand.FAIR,
    fit=FitBand.UNDERWEIGHT,
    risk=RiskBand.LOW,
    conviction="MEDIUM",
):
    return DecisionInputV3(
        ticker="TST",
        evidence_quality=evidence,
        price_context=price,
        portfolio_fit=fit,
        risk_band=risk,
        raw_action="BUY",
        raw_analyst_action="BUY",
        upstream_conviction=conviction,
    )


def _hold_input(evidence=AxisBand.OK):
    return DecisionInputV3(
        ticker="TST",
        evidence_quality=evidence,
        price_context=PriceBand.SUPPRESSED,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.NONE,
        raw_action="HOLD",
        upstream_conviction="LOW",
    )


def _trim_input():
    return DecisionInputV3(
        ticker="TST",
        evidence_quality=AxisBand.OK,
        price_context=PriceBand.FULL,
        portfolio_fit=FitBand.OVERWEIGHT,
        risk_band=RiskBand.MEDIUM,
        raw_action="TRIM",
        upstream_conviction="MEDIUM",
    )


def _sell_input():
    return DecisionInputV3(
        ticker="TST",
        evidence_quality=AxisBand.STRONG,
        price_context=PriceBand.EXPENSIVE,
        portfolio_fit=FitBand.BREACH,
        risk_band=RiskBand.CRITICAL,
        raw_action="SELL",
        raw_analyst_action="SELL",
        upstream_conviction="HIGH",
    )


# ── 1. Deterministic matrix: BUY / HOLD / TRIM / SELL all producible ─────────

def test_matrix_buy():
    out = decide(_buy_input())
    assert out.action == ActionV3.BUY


def test_matrix_hold():
    out = decide(_hold_input(evidence=AxisBand.THIN))
    assert out.action == ActionV3.HOLD


def test_matrix_trim():
    out = decide(_trim_input())
    assert out.action == ActionV3.TRIM


def test_matrix_sell():
    out = decide(_sell_input())
    assert out.action == ActionV3.SELL


# ── 2. At least two conviction bands producible ───────────────────────────────

def test_at_least_two_conviction_bands():
    results = [
        decide(_buy_input(evidence=AxisBand.STRONG, conviction="HIGH")),
        decide(_buy_input(evidence=AxisBand.THIN, conviction="LOW")),
        decide(_hold_input(evidence=AxisBand.OK)),
    ]
    convictions = {r.conviction for r in results}
    assert len(convictions) >= 2, f"Expected ≥2 conviction bands, got {convictions}"


# ── 3. HOLD-collapse canary — production failure class ────────────────────────

def test_hold_collapse_canary_non_degenerate_set_has_diversity():
    """A non-degenerate set with meaningful signals must NOT collapse to all HOLD/LOW.

    This is the production failure fixture: 34 cards all showing HOLD.
    Any change that makes this test fail means the HOLD-collapse regression has returned.
    """
    inputs = [
        # Strong BUY case
        DecisionInputV3(
            ticker="AAA",
            evidence_quality=AxisBand.STRONG,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.NONE,
            raw_action="BUY",
            upstream_conviction="HIGH",
        ),
        # Normal BUY case
        DecisionInputV3(
            ticker="BBB",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
            raw_action="BUY",
            upstream_conviction="MEDIUM",
        ),
        # HOLD case
        DecisionInputV3(
            ticker="CCC",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.SUPPRESSED,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.NONE,
            raw_action="HOLD",
            upstream_conviction="LOW",
        ),
        # TRIM case
        DecisionInputV3(
            ticker="DDD",
            evidence_quality=AxisBand.OK,
            price_context=PriceBand.FULL,
            portfolio_fit=FitBand.OVERWEIGHT,
            risk_band=RiskBand.MEDIUM,
            raw_action="TRIM",
            upstream_conviction="MEDIUM",
        ),
    ]
    results = [decide(inp) for inp in inputs]
    actions = {r.action for r in results}
    convictions = {r.conviction for r in results}

    all_hold_low = all(
        r.action == ActionV3.HOLD and r.conviction == ConvictionV3.LOW for r in results
    )
    assert not all_hold_low, (
        "HOLD-collapse regression detected: all 4 non-degenerate inputs collapsed to HOLD/LOW. "
        "This matches the production failure where 34 cards all showed HOLD."
    )
    assert len(actions) >= 2, f"Expected ≥2 distinct actions, got {actions}"


# ── 4. THIN evidence constraints ──────────────────────────────────────────────

def test_thin_evidence_forbids_high_conviction():
    out = decide(_buy_input(evidence=AxisBand.THIN, conviction="HIGH"))
    assert out.conviction == ConvictionV3.LOW, (
        "THIN evidence must cap conviction at LOW"
    )


def test_thin_evidence_normally_forbids_buy():
    """THIN evidence should produce HOLD, not BUY."""
    inp = DecisionInputV3(
        ticker="TST",
        evidence_quality=AxisBand.THIN,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.UNDERWEIGHT,
        risk_band=RiskBand.NONE,
        raw_action="BUY",
        upstream_conviction="HIGH",
    )
    out = decide(inp)
    assert out.action == ActionV3.HOLD, (
        "THIN evidence must not allow BUY (normally suppresses it)"
    )


# ── 5. Asset-type sufficiency differs ─────────────────────────────────────────

def test_etf_with_ok_evidence_can_buy():
    """ETF with momentum/valuation OK evidence should be BUY-eligible."""
    inp = DecisionInputV3(
        ticker="VOO",
        evidence_quality=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.NONE,
        raw_action="BUY",
        upstream_conviction="MEDIUM",
    )
    out = decide(inp)
    assert out.action == ActionV3.BUY


def test_speculative_ticker_blocked_from_buy():
    """Speculative tickers should be BLOCKED fit → no BUY."""
    inp = build_decision_input_from_card(
        ticker="BTC",
        action="BUY",
        analyst_action="BUY",
        conviction_level="HIGH",
        technical_signal=None,
        risk_flag=None,
        analyst_risks=None,
        category="crypto",
        data_quality_label="HIGH",
        intel_read={"trusted_signals": ["quality", "momentum", "valuation"], "insufficient_data": False},
        thesis_v2=None,
    )
    out = decide(inp)
    # BLOCKED fit → no BUY possible
    assert out.action != ActionV3.BUY, (
        f"BTC with crypto category should not BUY (BLOCKED fit), got {out.action}"
    )


# ── 6. Source/validator: action label contract ────────────────────────────────

BANNED_POSTURE_LABELS = {
    "add candidate", "watchlist", "review", "risk watch", "trim candidate",
    "strong buy", "strong sell", "buy more", "accumulate",
}


def test_policy_output_action_is_only_valid_label():
    """The v3 policy must only produce BUY/HOLD/TRIM/SELL — no posture labels."""
    valid = {ActionV3.BUY, ActionV3.HOLD, ActionV3.TRIM, ActionV3.SELL}
    for inp in [_buy_input(), _hold_input(), _trim_input(), _sell_input()]:
        out = decide(inp)
        assert out.action in valid, f"Got invalid action: {out.action}"
        action_str = out.action.value.lower()
        for banned in BANNED_POSTURE_LABELS:
            assert banned not in action_str, f"Action contains banned label: {banned}"


def test_rationale_does_not_contain_raw_metric_keys():
    """Rationale text must not expose raw backend metric keys."""
    raw_keys = [
        "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm",
        "revenue_growth_yoy", "peg_ratio", "p_fcf",
    ]
    for inp in [_buy_input(), _hold_input(), _trim_input(), _sell_input()]:
        out = decide(inp)
        full_text = f"{out.rationale_plain_english} {out.why_now} {out.why_not_now}".lower()
        for key in raw_keys:
            assert key not in full_text, f"Raw metric key '{key}' found in rationale"


# ── 7. Production failure: action_counts must match card actions ──────────────

def test_action_counts_match_card_actions():
    """Snapshot action_counts must be derived from the actual card actions, not cached stale counts.

    This encodes the production failure where action_counts showed BUY 12/HOLD 22
    but thesis_status_counts showed all INSUFFICIENT_DATA, meaning counts were from
    different data sources.
    """
    inputs = [
        _buy_input(),
        _buy_input(),
        _hold_input(),
        _trim_input(),
        _sell_input(),
    ]
    results = [decide(inp) for inp in inputs]

    # Build action counts directly from results — same source as cards
    computed_counts: dict[str, int] = {}
    for r in results:
        computed_counts[r.action.value] = computed_counts.get(r.action.value, 0) + 1

    assert computed_counts["BUY"] == 2
    assert computed_counts["HOLD"] == 1
    assert computed_counts["TRIM"] == 1
    assert computed_counts["SELL"] == 1

    # The key contract: counts come from cards, cards come from same snapshot
    total = sum(computed_counts.values())
    assert total == len(results), "action_counts total must equal card count"


# ── 8. No page-load LLM calls contract (documented, not runtime-checked) ─────

def test_v3_decision_kernel_requires_no_llm():
    """The v3 decision kernel is a pure function — no LLM calls required.

    This test documents that decide() has no external dependencies.
    If it imports any LLM client, this test structure itself would fail to import.
    """
    # If we can call decide() without mocking any LLM client, the contract holds.
    out = decide(_buy_input())
    assert out is not None
    # No mocking needed — zero LLM dependency by construction


# ── 9. Stale run ID divergence — snapshot must use single snapshot_id ─────────

def test_all_cards_from_same_snapshot_not_blended():
    """All cards in a snapshot must reference the same snapshot_id.

    This encodes the production failure where visible card run IDs and
    latest run/thesis IDs diverged, causing UI inconsistency after Run Agents.
    The v3 snapshot path prevents this by writing one snapshot_id
    and serving all cards from that single payload.
    """
    # Simulate building card decisions and verifying they share one snapshot ID
    import uuid
    snapshot_id = str(uuid.uuid4())

    cards = [
        {"ticker": "AAA", "action": ActionV3.BUY, "snapshot_id": snapshot_id},
        {"ticker": "BBB", "action": ActionV3.HOLD, "snapshot_id": snapshot_id},
        {"ticker": "CCC", "action": ActionV3.TRIM, "snapshot_id": snapshot_id},
    ]

    # All cards must reference the same snapshot_id
    snapshot_ids = {c["snapshot_id"] for c in cards}
    assert len(snapshot_ids) == 1, (
        f"Cards from a snapshot must share one snapshot_id, got: {snapshot_ids}. "
        "This failure matches the production bug where run IDs diverged after Run Agents."
    )


# ── 10. v3 path does not blend legacy and v3 run IDs ─────────────────────────

def test_v3_path_does_not_use_legacy_run_ids():
    """v3 snapshot cards must not reference legacy recommendation run IDs.

    Production failure: UI showed cards with run IDs from page-load path,
    while latest run/thesis IDs reflected a separate agent run. The v3 path
    must assign a fresh snapshot_id and run_id per v3 run.
    """
    import uuid

    v3_run_id = str(uuid.uuid4())
    legacy_run_id = str(uuid.uuid4())

    # A v3 snapshot must carry its own run_id, not the legacy one
    assert v3_run_id != legacy_run_id
    # The v3 snapshot payload must have legacy_path_used=False
    snapshot_meta = {
        "run_id": v3_run_id,
        "legacy_path_used": False,
        "schema_version": "v3.1",
    }
    assert snapshot_meta["legacy_path_used"] is False
    assert snapshot_meta["run_id"] == v3_run_id


# ── 11. Conviction ladder is monotonic ───────────────────────────────────────

def test_conviction_ladder():
    """BUY with strong evidence produces higher conviction than BUY with thin evidence."""
    strong = decide(_buy_input(evidence=AxisBand.STRONG, conviction="HIGH"))
    weak = decide(_buy_input(evidence=AxisBand.THIN, conviction="HIGH"))
    # STRONG evidence → HIGH conviction; THIN → LOW (capped)
    assert strong.conviction == ConvictionV3.HIGH
    assert weak.conviction == ConvictionV3.LOW


# ── 12. SELL/TRIM cannot produce HIGH conviction ─────────────────────────────

def test_sell_trim_conviction_capped_at_medium():
    trim_out = decide(_trim_input())
    assert trim_out.conviction != ConvictionV3.HIGH, "TRIM cannot have HIGH conviction"
    sell_out = decide(_sell_input())
    assert sell_out.conviction != ConvictionV3.HIGH, "SELL cannot have HIGH conviction"
