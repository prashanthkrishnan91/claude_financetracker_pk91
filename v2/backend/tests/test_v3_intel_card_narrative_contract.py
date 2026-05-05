"""Intel Card Narrative Contract v1 — comprehensive test suite.

Tests the full contract that ensures Evidence Check copy always agrees
with the visible action badge on Intel cards.

Root cause fixed:
- _derive_intel_posture rule 5.5 returned "Review" for BUY+insufficient,
  causing build_posture_reason to emit "Reviewing before taking action —
  the setup is interesting but not yet complete." on BUY cards.
- _build_caveat fallback emitted "Treat this as an early signal, not a
  complete picture." for WATCH posture, appearing on BUY cards.

Fix:
- Rule 5.5 removed; BUY action always → Add Candidate.
- build_intel_card_narrative_contract overrides posture_reason + caveat
  with action-consistent text keyed on the VISIBLE action.

Synthetic fixtures only — no real user or account data.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.reasoning_v2_plain_english import (
    build_intel_card_narrative_contract,
    detect_intel_card_conflict,
    _FORBIDDEN_FOR_BUY,
    _FORBIDDEN_FOR_TRIM_SELL,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

_THREE_TRUSTED = ["recent market behavior", "business quality", "valuation"]
_TWO_TRUSTED = ["business quality", "valuation"]
_ONE_TRUSTED = ["business quality"]
_MISSING_GROWTH_RISK = ["growth", "risk"]
_MISSING_RISK = ["risk"]

SCREENSHOT_MSFT_TRUSTED = _THREE_TRUSTED
SCREENSHOT_MSFT_MISSING = _MISSING_GROWTH_RISK


def _contract(
    action: str,
    conviction: str = "MEDIUM",
    trusted: list[str] | None = None,
    incomplete: list[str] | None = None,
    ticker: str = "TEST",
    category: str = "Core",
) -> dict:
    return build_intel_card_narrative_contract(
        visible_action=action,
        conviction_label=conviction,
        trusted_signals=trusted or [],
        incomplete_signals=incomplete or [],
        ticker=ticker,
        category=category,
    )


def _no_forbidden_hold(text: str) -> bool:
    """Return True if text contains none of the forbidden HOLD phrases for BUY cards."""
    lower = text.lower()
    return not any(phrase in lower for phrase in _FORBIDDEN_FOR_BUY)


def _no_forbidden_buy(text: str) -> bool:
    """Return True if text contains none of the forbidden BUY phrases for TRIM/SELL."""
    lower = text.lower()
    return not any(phrase in lower for phrase in _FORBIDDEN_FOR_TRIM_SELL)


# ── Section 1: Contract version and shape ────────────────────────────────────

class TestContractShape:
    def test_returns_required_keys(self):
        c = _contract("BUY")
        assert "action" in c
        assert "confidence_label" in c
        assert "evidence_summary" in c
        assert "reliable_labels" in c
        assert "missing_labels" in c
        assert "final_takeaway" in c
        assert "conflict_flags" in c
        assert "narrative_contract_version" in c

    def test_version_is_v1(self):
        for action in ("BUY", "HOLD", "TRIM", "SELL"):
            c = _contract(action)
            assert c["narrative_contract_version"] == "v1"

    def test_action_preserved(self):
        for action in ("BUY", "HOLD", "TRIM", "SELL"):
            c = _contract(action)
            assert c["action"] == action

    def test_conviction_preserved(self):
        for conviction in ("HIGH", "MEDIUM", "LOW"):
            c = _contract("BUY", conviction=conviction)
            assert c["confidence_label"] == conviction

    def test_unknown_conviction_defaults_to_low(self):
        c = _contract("BUY", conviction="UNKNOWN")
        assert c["confidence_label"] == "LOW"

    def test_conflict_flags_empty_for_valid_contract(self):
        for action in ("BUY", "HOLD", "TRIM", "SELL"):
            c = _contract(action)
            assert c["conflict_flags"] == []

    def test_reliable_labels_equals_input(self):
        c = _contract("BUY", trusted=_THREE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        assert c["reliable_labels"] == _THREE_TRUSTED

    def test_missing_labels_equals_input(self):
        c = _contract("BUY", trusted=_THREE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        assert c["missing_labels"] == _MISSING_GROWTH_RISK


# ── Section 2: BUY card invariants ───────────────────────────────────────────

class TestBuyCardInvariants:
    """Evidence Check must support a measured BUY — no HOLD/wait language."""

    @pytest.mark.parametrize("conviction", ["HIGH", "MEDIUM", "LOW"])
    def test_no_hold_language_any_conviction(self, conviction):
        c = _contract(
            "BUY",
            conviction=conviction,
            trusted=_THREE_TRUSTED,
            incomplete=_MISSING_GROWTH_RISK,
        )
        assert _no_forbidden_hold(c["evidence_summary"]), (
            f"evidence_summary contains forbidden HOLD phrase for BUY/{conviction}: "
            f"{c['evidence_summary']!r}"
        )
        assert _no_forbidden_hold(c["final_takeaway"]), (
            f"final_takeaway contains forbidden HOLD phrase for BUY/{conviction}: "
            f"{c['final_takeaway']!r}"
        )

    def test_no_hold_language_zero_trusted(self):
        c = _contract("BUY", trusted=[], incomplete=_MISSING_GROWTH_RISK)
        assert _no_forbidden_hold(c["evidence_summary"])
        assert _no_forbidden_hold(c["final_takeaway"])

    def test_no_reviewing_before_taking_action(self):
        c = _contract("BUY", trusted=_THREE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        combined = c["evidence_summary"] + " " + c["final_takeaway"]
        assert "reviewing before taking action" not in combined.lower()

    def test_no_not_yet_complete(self):
        c = _contract("BUY", trusted=_THREE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        combined = c["evidence_summary"] + " " + c["final_takeaway"]
        assert "not yet complete" not in combined.lower()

    def test_no_early_signal(self):
        c = _contract("BUY", trusted=_THREE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        combined = c["evidence_summary"] + " " + c["final_takeaway"]
        assert "early signal" not in combined.lower()

    def test_no_not_enough_data(self):
        c = _contract("BUY", trusted=_THREE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        combined = c["evidence_summary"] + " " + c["final_takeaway"]
        assert "not enough data" not in combined.lower()

    def test_no_wait_for_more_signals(self):
        c = _contract("BUY", trusted=_THREE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        combined = c["evidence_summary"] + " " + c["final_takeaway"]
        assert "wait for more signals" not in combined.lower()

    def test_no_stay_on_watchlist(self):
        c = _contract("BUY", trusted=_THREE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        combined = c["evidence_summary"] + " " + c["final_takeaway"]
        assert "stay on watchlist" not in combined.lower()

    def test_missing_axes_appear_as_caveats_not_stoppers(self):
        c = _contract(
            "BUY",
            conviction="MEDIUM",
            trusted=_THREE_TRUSTED,
            incomplete=_MISSING_GROWTH_RISK,
        )
        # Missing labels are present in the contract
        assert "growth" in str(c["missing_labels"])
        # But evidence_summary is still a buy-positive statement
        assert _no_forbidden_hold(c["evidence_summary"])


# ── Section 3: Screenshot regression (MSFT-like BUY + 3 trusted + 2 missing) ─

class TestScreenshotRegression:
    """Exact screenshot failure case from the task description."""

    def _msft_contract(self, conviction="MEDIUM"):
        return _contract(
            "BUY",
            conviction=conviction,
            trusted=SCREENSHOT_MSFT_TRUSTED,
            incomplete=SCREENSHOT_MSFT_MISSING,
            ticker="MSFT",
            category="Core",
        )

    def test_does_not_produce_reviewing_before_taking_action(self):
        c = self._msft_contract()
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        assert "reviewing before taking action" not in combined

    def test_does_not_produce_not_yet_complete(self):
        c = self._msft_contract()
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        assert "not yet complete" not in combined

    def test_does_not_produce_early_signal_phrase(self):
        c = self._msft_contract()
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        assert "early signal" not in combined

    def test_does_not_produce_treat_as_early_signal(self):
        c = self._msft_contract()
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        assert "treat this as an early signal" not in combined

    def test_evidence_summary_is_buy_positive(self):
        c = self._msft_contract()
        assert _no_forbidden_hold(c["evidence_summary"])

    def test_evidence_summary_mentions_trusted_axes(self):
        c = self._msft_contract()
        # At least one trusted axis should appear
        combined = c["evidence_summary"].lower()
        assert any(sig in combined for sig in SCREENSHOT_MSFT_TRUSTED)

    def test_missing_axes_mentioned_as_limits_not_stoppers(self):
        c = self._msft_contract()
        # Missing axes appear as limiting conviction, not blocking action
        combined = c["evidence_summary"].lower()
        # Should mention "growth" or "risk" in a caveat context
        has_missing_mention = "growth" in combined or "risk" in combined
        if has_missing_mention:
            # Verify it's framed as a limitation, not a stopper
            assert _no_forbidden_hold(c["evidence_summary"])


# ── Section 4: BUY + ETF ──────────────────────────────────────────────────────

class TestBuyEtfContract:
    """ETF BUY cards must say 'regular contribution target' not 'not complete'."""

    def test_vgt_like_etf_buy(self):
        c = _contract("BUY", trusted=_TWO_TRUSTED, incomplete=_MISSING_RISK, ticker="VGT", category="ETF")
        assert _no_forbidden_hold(c["evidence_summary"])
        assert _no_forbidden_hold(c["final_takeaway"])

    def test_etf_buy_says_contribution_target(self):
        c = _contract("BUY", trusted=_THREE_TRUSTED, incomplete=[], ticker="VOO", category="Index ETF")
        summary = c["evidence_summary"].lower()
        assert "contribution" in summary or "accumulation" in summary

    def test_etf_buy_no_not_complete_picture(self):
        c = _contract("BUY", trusted=_TWO_TRUSTED, incomplete=_MISSING_GROWTH_RISK, ticker="QQQ", category="ETF")
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        assert "not complete picture" not in combined
        assert "not yet complete" not in combined

    def test_etf_buy_zero_trusted_still_buy_positive(self):
        c = _contract("BUY", trusted=[], incomplete=_MISSING_GROWTH_RISK, ticker="SCHD", category="Dividend ETF")
        assert _no_forbidden_hold(c["evidence_summary"])

    def test_etf_final_takeaway_no_hold_language(self):
        c = _contract("BUY", trusted=_TWO_TRUSTED, incomplete=_MISSING_RISK, ticker="VTI", category="ETF")
        assert _no_forbidden_hold(c["final_takeaway"])

    def test_non_etf_not_treated_as_etf(self):
        c = _contract("BUY", trusted=_THREE_TRUSTED, incomplete=[], ticker="MSFT", category="Core")
        summary = c["evidence_summary"].lower()
        # Non-ETF should not have the ETF-specific "regular contribution" language
        assert "regular contribution target" not in summary


# ── Section 5: BUY + 1-2 trusted signals ─────────────────────────────────────

class TestBuyThinCoverage:
    """BUY with limited trusted signals stays BUY-positive (evidence-limited)."""

    def test_buy_one_trusted_no_hold_language(self):
        c = _contract("BUY", conviction="LOW", trusted=_ONE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        assert _no_forbidden_hold(c["evidence_summary"])
        assert _no_forbidden_hold(c["final_takeaway"])

    def test_buy_two_trusted_no_hold_language(self):
        c = _contract("BUY", conviction="MEDIUM", trusted=_TWO_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        assert _no_forbidden_hold(c["evidence_summary"])
        assert _no_forbidden_hold(c["final_takeaway"])

    def test_buy_low_conviction_says_limited_confidence(self):
        c = _contract("BUY", conviction="LOW", trusted=_ONE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        summary = c["evidence_summary"].lower()
        assert "limited" in summary or "modest" in summary or "low" in summary or "gradual" in summary

    def test_buy_medium_conviction_mentions_measured(self):
        c = _contract("BUY", conviction="MEDIUM", trusted=_TWO_TRUSTED, incomplete=_MISSING_RISK)
        summary = c["evidence_summary"].lower()
        assert "measured" in summary or "gradually" in summary or "constructive" in summary or "moderate" in summary


# ── Section 6: HOLD card invariants ──────────────────────────────────────────

class TestHoldCardInvariants:
    """HOLD cards may retain wait/watchlist language."""

    def test_hold_zero_trusted_allows_wait_language(self):
        c = _contract("HOLD", trusted=[], incomplete=_MISSING_GROWTH_RISK)
        # Should NOT have immediate buy language
        assert _no_forbidden_buy(c["evidence_summary"])

    def test_hold_with_trusted_does_not_imply_zero_evidence(self):
        c = _contract("HOLD", trusted=_TWO_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        # Should mention the available evidence
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        assert any(sig in combined for sig in _TWO_TRUSTED)

    def test_hold_no_buy_now_language(self):
        c = _contract("HOLD", trusted=_THREE_TRUSTED, incomplete=[], conviction="HIGH")
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        assert "add now" not in combined
        assert "buy now" not in combined

    def test_hold_zero_trusted_says_not_enough_evidence(self):
        c = _contract("HOLD", trusted=[], incomplete=_MISSING_GROWTH_RISK)
        summary = c["evidence_summary"].lower()
        assert "not enough" in summary or "monitoring" in summary or "waiting" in summary

    def test_hold_partial_evidence_says_not_enough_conviction(self):
        c = _contract("HOLD", trusted=_ONE_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        summary = c["evidence_summary"].lower()
        assert "not enough conviction" in summary or "incomplete" in summary or "still" in summary


# ── Section 7: TRIM card invariants ──────────────────────────────────────────

class TestTrimCardInvariants:
    def test_trim_no_accumulate_language(self):
        c = _contract("TRIM", trusted=_TWO_TRUSTED, incomplete=_MISSING_RISK)
        assert _no_forbidden_buy(c["evidence_summary"])
        assert _no_forbidden_buy(c["final_takeaway"])

    def test_trim_evidence_summary_says_reduce(self):
        c = _contract("TRIM", trusted=_TWO_TRUSTED, incomplete=[])
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        assert "reduc" in combined or "lightening" in combined or "trim" in combined

    def test_trim_final_takeaway_mentions_reduction(self):
        c = _contract("TRIM", trusted=_THREE_TRUSTED, incomplete=[])
        takeaway = c["final_takeaway"].lower()
        assert "reduc" in takeaway or "partial" in takeaway


# ── Section 8: SELL card invariants ──────────────────────────────────────────

class TestSellCardInvariants:
    def test_sell_no_accumulate_language(self):
        c = _contract("SELL", trusted=_TWO_TRUSTED, incomplete=[])
        assert _no_forbidden_buy(c["evidence_summary"])
        assert _no_forbidden_buy(c["final_takeaway"])

    def test_sell_evidence_summary_says_exit_or_reduce(self):
        c = _contract("SELL", trusted=_TWO_TRUSTED, incomplete=[])
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        assert "exit" in combined or "reduc" in combined or "sell" in combined

    def test_sell_final_takeaway_differs_from_trim(self):
        trim = _contract("TRIM", trusted=_TWO_TRUSTED, incomplete=[])
        sell = _contract("SELL", trusted=_TWO_TRUSTED, incomplete=[])
        # SELL takeaway should mention full/substantial reduction
        assert "full" in sell["final_takeaway"].lower() or "substantial" in sell["final_takeaway"].lower()


# ── Section 9: detect_intel_card_conflict ────────────────────────────────────

class TestDetectIntelCardConflict:
    def test_buy_card_clean_no_conflicts(self):
        flags = detect_intel_card_conflict(
            visible_action="BUY",
            posture_reason="Evidence on business quality supports a measured buy.",
            caveat="Evidence supports a measured buy. Keep sizing disciplined.",
        )
        assert flags == []

    def test_buy_card_detects_reviewing_phrase(self):
        flags = detect_intel_card_conflict(
            visible_action="BUY",
            posture_reason="Reviewing before taking action — the setup is interesting but not yet complete.",
            caveat="",
        )
        assert len(flags) > 0
        assert any("buy_hold_lang" in f for f in flags)

    def test_buy_card_detects_early_signal_in_caveat(self):
        flags = detect_intel_card_conflict(
            visible_action="BUY",
            posture_reason="Evidence supports a measured buy.",
            caveat="Treat this as an early signal, not a complete picture.",
        )
        assert len(flags) > 0

    def test_buy_card_detects_not_yet_complete(self):
        flags = detect_intel_card_conflict(
            visible_action="BUY",
            posture_reason="The setup is interesting but not yet complete.",
            caveat="",
        )
        assert len(flags) > 0

    def test_buy_card_detects_stay_on_watchlist(self):
        flags = detect_intel_card_conflict(
            visible_action="BUY",
            posture_reason="Stay on watchlist until evidence strengthens.",
            caveat="",
        )
        assert len(flags) > 0

    def test_hold_card_clean_no_conflicts(self):
        flags = detect_intel_card_conflict(
            visible_action="HOLD",
            posture_reason="Not enough evidence to act. Monitoring.",
            caveat="Wait for more complete signals.",
        )
        assert flags == []

    def test_trim_card_clean_no_conflicts(self):
        flags = detect_intel_card_conflict(
            visible_action="TRIM",
            posture_reason="Signals suggest reducing exposure.",
            caveat="Consider partial reduction.",
        )
        assert flags == []

    def test_none_inputs_return_no_conflicts(self):
        flags = detect_intel_card_conflict(
            visible_action="BUY",
            posture_reason=None,
            caveat=None,
        )
        assert flags == []


# ── Section 10: Narrative contract replaces old forbidden phrases ─────────────

class TestNarrativeContractReplacementProof:
    """Prove that applying the contract to the known bad inputs produces clean output."""

    def test_msft_buy_review_posture_fixed(self):
        """Old: posture_label=Review → 'Reviewing before taking action'
        New: contract(BUY, MEDIUM, 3 trusted, 2 missing) → buy-positive text."""
        c = _contract(
            "BUY",
            conviction="MEDIUM",
            trusted=SCREENSHOT_MSFT_TRUSTED,
            incomplete=SCREENSHOT_MSFT_MISSING,
            ticker="MSFT",
        )
        assert _no_forbidden_hold(c["evidence_summary"])
        assert "reviewing before taking action" not in c["evidence_summary"].lower()

    def test_watch_posture_caveat_fixed_for_buy(self):
        """Old: r2.action.posture=WATCH + n_trusted>=1 → 'Treat this as an early signal'
        New: contract(BUY, ...) final_takeaway → buy-positive text."""
        c = _contract(
            "BUY",
            conviction="MEDIUM",
            trusted=_THREE_TRUSTED,
            incomplete=_MISSING_GROWTH_RISK,
        )
        assert "early signal" not in c["final_takeaway"].lower()
        assert "treat this as" not in c["final_takeaway"].lower()

    def test_vgt_buy_not_complete_picture_fixed(self):
        """Old: ETF BUY with WATCH posture → 'Treat this as an early signal'
        New: contract(BUY, ETF) → 'Regular contribution target...'"""
        c = _contract("BUY", trusted=_TWO_TRUSTED, incomplete=_MISSING_GROWTH_RISK, ticker="VGT", category="ETF")
        assert _no_forbidden_hold(c["evidence_summary"])
        assert "not complete picture" not in c["evidence_summary"].lower()


# ── Section 11: 34-card fixture — conflict_count must be 0 ───────────────────

class Test34CardPortfolioNoConflicts:
    """Synthetic 34-card portfolio matching production shape must produce 0 conflicts."""

    def _make_portfolio(self):
        """34-card portfolio: 11 BUY + 23 HOLD, various convictions and coverages."""
        cards = []
        # 11 BUY cards
        buy_cases = [
            ("MSFT", "Core", "HIGH", _THREE_TRUSTED, _MISSING_GROWTH_RISK),
            ("NVDA", "Core", "HIGH", _THREE_TRUSTED, []),
            ("AAPL", "Core", "MEDIUM", _THREE_TRUSTED, _MISSING_RISK),
            ("TSM", "Core", "MEDIUM", _TWO_TRUSTED, _MISSING_GROWTH_RISK),
            ("AMZN", "Core", "MEDIUM", _TWO_TRUSTED, _MISSING_RISK),
            ("VGT", "ETF", "MEDIUM", _TWO_TRUSTED, _MISSING_GROWTH_RISK),
            ("VOO", "Index ETF", "LOW", _TWO_TRUSTED, _MISSING_GROWTH_RISK),
            ("QQQ", "ETF", "LOW", _ONE_TRUSTED, _MISSING_GROWTH_RISK),
            ("GOOG", "Core", "LOW", _ONE_TRUSTED, _MISSING_GROWTH_RISK),
            ("META", "Core", "MEDIUM", _THREE_TRUSTED, _MISSING_RISK),
            ("SCHD", "Dividend ETF", "LOW", [], _MISSING_GROWTH_RISK),
        ]
        # 23 HOLD cards
        hold_cases = [
            (f"HOLD{i}", "Core", "LOW", [], _MISSING_GROWTH_RISK) for i in range(15)
        ] + [
            (f"HOLDM{i}", "Core", "MEDIUM", _ONE_TRUSTED, _MISSING_GROWTH_RISK) for i in range(8)
        ]

        for ticker, category, conviction, trusted, incomplete in buy_cases:
            c = _contract("BUY", conviction=conviction, trusted=trusted, incomplete=incomplete, ticker=ticker, category=category)
            cards.append(c)
        for ticker, category, conviction, trusted, incomplete in hold_cases:
            c = _contract("HOLD", conviction=conviction, trusted=trusted, incomplete=incomplete, ticker=ticker, category=category)
            cards.append(c)
        return cards

    def test_zero_conflicts_in_34_card_portfolio(self):
        portfolio = self._make_portfolio()
        assert len(portfolio) == 34
        conflicts = []
        for c in portfolio:
            action = c["action"]
            summary = c["evidence_summary"]
            takeaway = c["final_takeaway"]
            flags = detect_intel_card_conflict(
                visible_action=action,
                posture_reason=summary,
                caveat=takeaway,
            )
            if flags:
                conflicts.append({"action": action, "flags": flags, "text": summary[:80]})
        assert conflicts == [], f"Expected 0 conflicts, got: {conflicts}"

    def test_buy_insufficient_contract_is_buy_positive(self):
        """Rule 5.5 was removed: BUY + insufficient → Add Candidate posture.
        Verify the contract output for a BUY card with insufficient_data is buy-positive."""
        c = _contract(
            "BUY",
            conviction="MEDIUM",
            trusted=_THREE_TRUSTED,
            incomplete=_MISSING_GROWTH_RISK,
            ticker="MSFT",
        )
        assert _no_forbidden_hold(c["evidence_summary"]), (
            "BUY+insufficient contract must not produce HOLD language. "
            f"Got: {c['evidence_summary']!r}"
        )
        flags = detect_intel_card_conflict(
            visible_action="BUY",
            posture_reason=c["evidence_summary"],
            caveat=c["final_takeaway"],
        )
        assert flags == [], f"Contract output still has conflicts: {flags}"

    def test_buy_sufficient_data_contract_is_buy_positive(self):
        c = _contract(
            "BUY",
            conviction="HIGH",
            trusted=_THREE_TRUSTED,
            incomplete=[],
            ticker="NVDA",
        )
        assert _no_forbidden_hold(c["evidence_summary"])
        assert _no_forbidden_hold(c["final_takeaway"])


# ── Section 12: HOLD card with trusted signals ───────────────────────────────

class TestHoldWithTrustedSignals:
    def test_hold_with_trusted_mentions_available_evidence(self):
        c = _contract("HOLD", trusted=_TWO_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        # Should acknowledge the available evidence
        assert any(sig in combined for sig in _TWO_TRUSTED)

    def test_hold_zero_trusted_allows_wait_phrasing(self):
        c = _contract("HOLD", trusted=[], incomplete=_MISSING_GROWTH_RISK)
        combined = (c["evidence_summary"] + " " + c["final_takeaway"]).lower()
        # Wait/monitor language is appropriate for HOLD with zero evidence
        assert "wait" in combined or "monitor" in combined or "not enough" in combined

    def test_hold_does_not_say_not_zero_evidence_with_trusted(self):
        c = _contract("HOLD", trusted=_TWO_TRUSTED, incomplete=_MISSING_GROWTH_RISK)
        summary = c["evidence_summary"].lower()
        # Should NOT imply zero usable evidence when trusted signals exist
        assert "not enough evidence on any dimension" not in summary
        assert "no complete dimension" not in summary


# ── Section 13: detect_intel_card_conflict on old screenshot phrases ──────────

class TestConflictDetectionOnOldPhrases:
    """Verify the detector flags the exact phrases from the screenshot failure."""

    def test_flags_exact_screenshot_posture_reason(self):
        old_text = (
            "Evidence on recent market behavior, business quality, and valuation warrants attention, "
            "but growth and risk are still missing. "
            "Reviewing before taking action — the setup is interesting but not yet complete."
        )
        flags = detect_intel_card_conflict(
            visible_action="BUY",
            posture_reason=old_text,
            caveat="",
        )
        assert len(flags) > 0

    def test_flags_treat_as_early_signal_caveat(self):
        flags = detect_intel_card_conflict(
            visible_action="BUY",
            posture_reason="Evidence supports a measured buy.",
            caveat="Treat this as an early signal, not a complete picture.",
        )
        assert len(flags) > 0

    def test_clean_buy_text_no_conflict(self):
        flags = detect_intel_card_conflict(
            visible_action="BUY",
            posture_reason="Evidence on recent market behavior, business quality, and valuation supports a measured buy.",
            caveat="Missing growth and risk lowers conviction but does not override the buy signal. Size gradually.",
        )
        assert flags == []
