"""Stage 3 Evidence Depth — backend contract tests.

Verifies:
1. Source-rich stock (3+ trusted_signals) reaches OK/STRONG evidence quality.
2. Thin-evidence stock (0 trusted_signals) stays THIN and logs suppression reason.
3. Action/conviction changes alone do NOT change evidence quality.
4. HIGH BUY conviction guardrail still caps when evidence is not STRONG (Cap 5).
5. source_pack committee status is "source_validated" when evidence is OK+.
6. source_pack committee status is "pending" when evidence is THIN/SUPPRESSED.
7. snapshot_builder emits correct source_pack counts.
8. Snapshot serialization of source-pack / committee fields.

Pure backend unit/contract tests — no IO, DB, LLM, or provider calls.
All fixtures are synthetic; no real user or account data.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.decision_contracts import (
    ActionV3,
    AxisBand,
    ConvictionV3,
    DecisionInputV3,
    DecisionOutputV3,
    FitBand,
    PriceBand,
    RiskBand,
)
from app.services.intelligence.v3.decision_policy_v1 import decide
from app.services.intelligence.v3.existing_signal_adapter import (
    build_decision_input_from_card,
)
from app.services.intelligence.v3.snapshot_builder import (
    _build_source_pack_status,
    build_snapshot,
)


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _intel_read(n_trusted: int) -> dict:
    signals = ["business quality", "valuation", "growth", "momentum", "risk"][:n_trusted]
    return {"insufficient_data": False, "trusted_signals": signals, "insufficient_signals": []}


def _thin_intel_read() -> dict:
    return {"insufficient_data": True, "trusted_signals": [], "insufficient_signals": []}


def _make_decision(
    ticker: str = "AAPL",
    *,
    evidence_quality: AxisBand = AxisBand.OK,
    action: ActionV3 = ActionV3.BUY,
    conviction: ConvictionV3 = ConvictionV3.MEDIUM,
    suppression_reasons: dict | None = None,
) -> DecisionOutputV3:
    return DecisionOutputV3(
        ticker=ticker,
        action=action,
        conviction=conviction,
        evidence_quality=evidence_quality,
        attractiveness=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.UNDERWEIGHT,
        risk_band=RiskBand.NONE,
        blockers=[],
        suppression_reasons=suppression_reasons or {},
        rationale_plain_english=f"{ticker}: signals support this position.",
        why_now=f"{ticker} clears all bars.",
        why_not_now=f"Watch {ticker} for risk changes.",
        source_signal_summary={"has_primary_driver": True, "has_action_reason": True},
        schema_version="v3.1",
    )


def _make_card_meta(ticker: str) -> dict:
    return {"ticker": ticker, "name": f"{ticker} Corp", "category": "stock"}


# ══════════════════════════════════════════════════════════════════════════════
# 1. Source-rich stock reaches OK or STRONG evidence quality through real mapping
# ══════════════════════════════════════════════════════════════════════════════

class TestSourceRichEvidenceQuality:
    """Source-linked evidence (3+ trusted_signals) produces STRONG; 1-2 produces OK."""

    def test_three_trusted_signals_yields_strong_evidence(self):
        inp = build_decision_input_from_card(
            ticker="AAPL",
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=_intel_read(3),
            thesis_v2=None,
            primary_driver="Sustained revenue growth across segments.",
        )
        assert inp.evidence_quality == AxisBand.STRONG
        assert "evidence_quality" not in inp.suppression_reasons

    def test_two_trusted_signals_yields_ok_evidence(self):
        inp = build_decision_input_from_card(
            ticker="MSFT",
            action="BUY",
            analyst_action="BUY",
            conviction_level="MEDIUM",
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=_intel_read(2),
            thesis_v2=None,
        )
        assert inp.evidence_quality == AxisBand.OK
        assert "evidence_quality" not in inp.suppression_reasons

    def test_one_trusted_signal_yields_ok_evidence(self):
        inp = build_decision_input_from_card(
            ticker="NVDA",
            action="HOLD",
            analyst_action="HOLD",
            conviction_level="LOW",
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=_intel_read(1),
            thesis_v2=None,
        )
        assert inp.evidence_quality == AxisBand.OK


# ══════════════════════════════════════════════════════════════════════════════
# 2. Thin-evidence stock stays THIN and suppression reason is logged
# ══════════════════════════════════════════════════════════════════════════════

class TestThinEvidenceStaysPartial:
    """Zero trusted signals keeps evidence THIN; suppression reason is recorded."""

    def test_zero_trusted_signals_yields_thin(self):
        inp = build_decision_input_from_card(
            ticker="SNOW",
            action="HOLD",
            analyst_action="HOLD",
            conviction_level="LOW",
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=_thin_intel_read(),
            thesis_v2=None,
        )
        assert inp.evidence_quality == AxisBand.THIN
        assert "evidence_quality" in inp.suppression_reasons
        assert "0 trusted" in inp.suppression_reasons["evidence_quality"].lower()

    def test_no_intel_read_no_quality_label_yields_suppressed(self):
        inp = build_decision_input_from_card(
            ticker="UNKN",
            action=None,
            analyst_action=None,
            conviction_level=None,
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=None,
            thesis_v2=None,
        )
        assert inp.evidence_quality == AxisBand.SUPPRESSED
        assert "evidence_quality" in inp.suppression_reasons

    def test_thin_evidence_decision_is_hold_not_buy(self):
        """Thin evidence → HOLD (cannot meet BUY bar)."""
        inp = build_decision_input_from_card(
            ticker="SNOW",
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=_thin_intel_read(),
            thesis_v2=None,
        )
        out = decide(inp)
        assert out.action == ActionV3.HOLD
        assert "Insufficient evidence to act." in out.blockers

    def test_thin_evidence_suppression_reason_logged_in_decision(self):
        """Suppression reason from thin evidence is preserved on DecisionOutputV3."""
        inp = build_decision_input_from_card(
            ticker="SNOW",
            action="HOLD",
            analyst_action="HOLD",
            conviction_level="LOW",
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=_thin_intel_read(),
            thesis_v2=None,
        )
        out = decide(inp)
        assert "evidence_quality" in out.suppression_reasons
        reason = out.suppression_reasons["evidence_quality"]
        assert reason  # non-empty — honest reason logged


# ══════════════════════════════════════════════════════════════════════════════
# 3. Action/conviction changes alone do NOT change evidence quality
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceQualityNotDrivenByAction:
    """Changing raw_action or upstream_conviction must not alter evidence quality."""

    @pytest.mark.parametrize("action,conviction", [
        ("BUY", "HIGH"),
        ("HOLD", "LOW"),
        ("SELL", "LOW"),
        ("TRIM", "MEDIUM"),
    ])
    def test_same_intel_read_different_action_same_evidence_quality(self, action, conviction):
        inp = build_decision_input_from_card(
            ticker="AAPL",
            action=action,
            analyst_action=action,
            conviction_level=conviction,
            technical_signal=None,
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=_intel_read(2),
            thesis_v2=None,
        )
        # Evidence quality must be OK regardless of action or conviction.
        assert inp.evidence_quality == AxisBand.OK, (
            f"evidence_quality changed for action={action}, conviction={conviction}"
        )

    def test_no_intel_read_action_doesnt_rescue_evidence(self):
        """Even a HIGH conviction BUY cannot rescue SUPPRESSED evidence quality."""
        inp = build_decision_input_from_card(
            ticker="AAPL",
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=None,
            thesis_v2=None,
        )
        assert inp.evidence_quality == AxisBand.SUPPRESSED


# ══════════════════════════════════════════════════════════════════════════════
# 4. HIGH BUY conviction guardrail (Cap 5) — STRONG evidence required
# ══════════════════════════════════════════════════════════════════════════════

class TestHighBuyGuardrail:
    """Cap 5: BUY + HIGH upstream conviction is capped to MEDIUM when evidence is OK (not STRONG)."""

    def test_ok_evidence_caps_high_buy_to_medium(self):
        """OK evidence (1-2 trusted signals) + HIGH upstream → MEDIUM conviction (not HIGH)."""
        inp = build_decision_input_from_card(
            ticker="MSFT",
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=_intel_read(2),
            thesis_v2=None,
        )
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction == ConvictionV3.MEDIUM, (
            "Cap 5: HIGH conviction BUY with OK evidence must be capped to MEDIUM"
        )

    def test_strong_evidence_allows_high_buy(self):
        """STRONG evidence (3+ trusted signals) + HIGH upstream → HIGH conviction allowed."""
        inp = build_decision_input_from_card(
            ticker="AAPL",
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=_intel_read(3),
            thesis_v2=None,
        )
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction == ConvictionV3.HIGH, (
            "STRONG evidence (3+ trusted signals) must allow HIGH conviction BUY"
        )

    def test_thin_evidence_caps_conviction_to_low(self):
        """THIN evidence always caps conviction to LOW (Cap 1)."""
        inp = build_decision_input_from_card(
            ticker="UNKN",
            action="BUY",
            analyst_action="BUY",
            conviction_level="HIGH",
            technical_signal="BULLISH",
            risk_flag=None,
            analyst_risks=None,
            category="stock",
            data_quality_label=None,
            intel_read=_thin_intel_read(),
            thesis_v2=None,
        )
        out = decide(inp)
        # Thin evidence → HOLD (BUY bar not met), conviction is LOW
        assert out.conviction == ConvictionV3.LOW


# ══════════════════════════════════════════════════════════════════════════════
# 5 & 6. source_pack committee status: source_validated vs pending
# ══════════════════════════════════════════════════════════════════════════════

class TestSourcePackCommitteeStatus:
    """_build_source_pack_status() returns correct status from evidence quality."""

    def test_ok_evidence_yields_source_validated(self):
        decision = _make_decision("AAPL", evidence_quality=AxisBand.OK)
        result = _build_source_pack_status(decision)
        assert result["status"] == "source_validated"
        assert "reason" not in result

    def test_strong_evidence_yields_source_validated(self):
        decision = _make_decision("AAPL", evidence_quality=AxisBand.STRONG)
        result = _build_source_pack_status(decision)
        assert result["status"] == "source_validated"

    def test_thin_evidence_yields_pending(self):
        decision = _make_decision(
            "SNOW",
            evidence_quality=AxisBand.THIN,
            suppression_reasons={"evidence_quality": "No trusted evidence — 0 trusted dimension(s)."},
        )
        result = _build_source_pack_status(decision)
        assert result["status"] == "pending"
        assert result.get("reason"), "pending status must carry a reason"
        assert "0 trusted" in result["reason"].lower()

    def test_suppressed_evidence_yields_pending(self):
        decision = _make_decision(
            "UNKN",
            evidence_quality=AxisBand.SUPPRESSED,
            suppression_reasons={"evidence_quality": "No intel_read or data_quality_label available."},
        )
        result = _build_source_pack_status(decision)
        assert result["status"] == "pending"
        assert result.get("reason")

    def test_pending_with_no_reason_gets_default_reason(self):
        """When suppression_reasons is empty for a thin card, a default reason is used."""
        decision = _make_decision("THIN", evidence_quality=AxisBand.THIN)
        result = _build_source_pack_status(decision)
        assert result["status"] == "pending"
        assert result.get("reason"), "pending must always carry a reason string"

    def test_source_validated_never_carries_reason(self):
        """source_validated status must not carry a reason field."""
        for band in (AxisBand.OK, AxisBand.STRONG):
            decision = _make_decision("GOOD", evidence_quality=band)
            result = _build_source_pack_status(decision)
            assert "reason" not in result, (
                f"{band.value} evidence must not carry a reason in source_validated status"
            )


# ══════════════════════════════════════════════════════════════════════════════
# 7. Snapshot builder emits correct source_pack counts
# ══════════════════════════════════════════════════════════════════════════════

class TestSnapshotSourcePackCounts:
    """build_snapshot() correctly tallies source_pack_validated/pending counts."""

    def _build_mixed_snapshot(self) -> dict:
        decisions = [
            _make_decision("AAPL", evidence_quality=AxisBand.STRONG),  # source_validated
            _make_decision("MSFT", evidence_quality=AxisBand.OK),      # source_validated
            _make_decision("SNOW", evidence_quality=AxisBand.THIN,
                           suppression_reasons={"evidence_quality": "No trusted evidence — 0 trusted dimension(s)."}),  # pending
            _make_decision("UNKN", evidence_quality=AxisBand.SUPPRESSED,
                           suppression_reasons={"evidence_quality": "No intel_read or data_quality_label available."}),  # pending
        ]
        metas = [_make_card_meta(t) for t in ["AAPL", "MSFT", "SNOW", "UNKN"]]
        return build_snapshot(run_id="run-test-001", decisions=decisions, card_metas=metas)

    def test_source_pack_validated_count_is_correct(self):
        snap = self._build_mixed_snapshot()
        assert snap["source_pack_validated_count"] == 2

    def test_source_pack_pending_count_is_correct(self):
        snap = self._build_mixed_snapshot()
        assert snap["source_pack_pending_count"] == 2

    def test_counts_sum_to_total_holdings(self):
        snap = self._build_mixed_snapshot()
        total = snap["source_pack_validated_count"] + snap["source_pack_pending_count"]
        assert total == len(snap["current_holdings"])

    def test_all_validated_snapshot(self):
        """All OK/STRONG evidence → all source_pack_validated."""
        decisions = [
            _make_decision("A", evidence_quality=AxisBand.STRONG),
            _make_decision("B", evidence_quality=AxisBand.OK),
        ]
        metas = [_make_card_meta(t) for t in ["A", "B"]]
        snap = build_snapshot(run_id="run-all-ok", decisions=decisions, card_metas=metas)
        assert snap["source_pack_validated_count"] == 2
        assert snap["source_pack_pending_count"] == 0

    def test_all_pending_snapshot(self):
        """All THIN/SUPPRESSED evidence → all source_pack_pending."""
        decisions = [
            _make_decision("X", evidence_quality=AxisBand.THIN,
                           suppression_reasons={"evidence_quality": "No trusted evidence — 0 trusted dimension(s)."}),
            _make_decision("Y", evidence_quality=AxisBand.SUPPRESSED,
                           suppression_reasons={"evidence_quality": "No intel_read or data_quality_label available."}),
        ]
        metas = [_make_card_meta(t) for t in ["X", "Y"]]
        snap = build_snapshot(run_id="run-all-thin", decisions=decisions, card_metas=metas)
        assert snap["source_pack_validated_count"] == 0
        assert snap["source_pack_pending_count"] == 2


# ══════════════════════════════════════════════════════════════════════════════
# 8. Snapshot serialization — committee field shape and content
# ══════════════════════════════════════════════════════════════════════════════

class TestSnapshotCommitteeSerialization:
    """Snapshot held-card committee payloads are correctly shaped."""

    def test_source_validated_card_committee_shape(self):
        decision = _make_decision("AAPL", evidence_quality=AxisBand.STRONG)
        snap = build_snapshot(
            run_id="run-ser-001",
            decisions=[decision],
            card_metas=[_make_card_meta("AAPL")],
        )
        card = snap["current_holdings"][0]
        committee = card["detail_drawer_payload"]["committee"]
        assert committee["status"] == "source_validated"
        assert "reason" not in committee

    def test_pending_card_committee_shape(self):
        decision = _make_decision(
            "SNOW",
            evidence_quality=AxisBand.THIN,
            suppression_reasons={"evidence_quality": "No trusted evidence — 0 trusted dimension(s)."},
        )
        snap = build_snapshot(
            run_id="run-ser-002",
            decisions=[decision],
            card_metas=[_make_card_meta("SNOW")],
        )
        card = snap["current_holdings"][0]
        committee = card["detail_drawer_payload"]["committee"]
        assert committee["status"] == "pending"
        assert "reason" in committee
        assert committee["reason"]

    def test_committee_field_always_present(self):
        """Every held card must have a committee field regardless of evidence quality."""
        for band in (AxisBand.OK, AxisBand.STRONG, AxisBand.THIN, AxisBand.SUPPRESSED):
            decision = _make_decision("TEST", evidence_quality=band)
            snap = build_snapshot(
                run_id=f"run-{band.value}",
                decisions=[decision],
                card_metas=[_make_card_meta("TEST")],
            )
            card = snap["current_holdings"][0]
            assert "committee" in card["detail_drawer_payload"], (
                f"committee field missing for evidence_quality={band.value}"
            )
            assert "status" in card["detail_drawer_payload"]["committee"]

    def test_no_target_price_in_committee_reason(self):
        """committee reason text must not contain price targets."""
        import re
        price_target_pat = re.compile(r"\$\s*\d+|price target", re.IGNORECASE)

        decision = _make_decision(
            "SNOW",
            evidence_quality=AxisBand.THIN,
            suppression_reasons={"evidence_quality": "No trusted evidence — 0 trusted dimension(s)."},
        )
        snap = build_snapshot(
            run_id="run-no-price", decisions=[decision], card_metas=[_make_card_meta("SNOW")]
        )
        reason = snap["current_holdings"][0]["detail_drawer_payload"]["committee"].get("reason", "")
        assert not price_target_pat.search(reason), f"Price target found in committee reason: {reason!r}"

    def test_deploy_fields_untouched_by_committee_change(self):
        """action and conviction are NOT affected by committee status computation."""
        decision = _make_decision(
            "AAPL",
            evidence_quality=AxisBand.STRONG,
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
        )
        snap = build_snapshot(
            run_id="run-deploy-safe",
            decisions=[decision],
            card_metas=[_make_card_meta("AAPL")],
        )
        card = snap["current_holdings"][0]
        assert card["action"] == "BUY"
        assert card["conviction"] == "HIGH"
        assert card["evidence_band"] == "STRONG"
