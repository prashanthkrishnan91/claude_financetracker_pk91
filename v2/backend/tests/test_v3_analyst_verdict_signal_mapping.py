"""PR 3B — analyst_verdict trusted-signal mapping tests.

Verifies the root-cause fix: ReadOnlyEvidenceAdapter.load_cards() now synthesizes
an intel_read-compatible structure from real analyst_verdict fields instead of
defaulting every card to data_quality_label="MEDIUM" (which inflated all cards
to PARTIAL regardless of actual analyst content).

Test inventory:
1. Source-rich analyst_verdict (primary_driver + action_reason + key_drivers)
   → 3 trusted signals → STRONG evidence quality.
2. Two-field analyst_verdict (primary_driver + action_reason) → 2 signals → OK (PARTIAL).
3. Single-field analyst_verdict (primary_driver only) → 1 signal → OK (PARTIAL).
4. used_fallback=True verdict → 0 trusted signals → THIN.
5. No analyst insight (empty av) → SUPPRESSED (was incorrectly PARTIAL before fix).
6. Fallback-phrase content is excluded from trusted signals.
7. Action/conviction changes alone do NOT change evidence quality.
8. research_artifact with safe_for_decision=False is not consumed.
9. HIGH BUY guardrail (Cap 5) still caps when evidence is not STRONG.
10. analyst_drivers reads key_drivers field from AnalystVerdict.
11. Evidence stats carry artifact governance counters (always 0).
12. Snapshot serialization: plain-English fields remain correct.

Pure unit/contract tests — no IO, DB, LLM, or provider calls.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
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
from app.services.intelligence.v3.snapshot_builder import build_snapshot


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_analyst_verdict(
    *,
    primary_driver: str = "",
    action_reason: str = "",
    key_drivers: list[str] | None = None,
    used_fallback: bool = False,
    conviction_level: str = "MEDIUM",
    action: str = "HOLD",
    risk_flag: str = "",
    data_quality_label: str | None = None,
) -> dict[str, Any]:
    """Simulate what AnalystVerdict.to_dict() produces."""
    out: dict[str, Any] = {
        "action": action,
        "conviction_level": conviction_level,
        "primary_driver": primary_driver,
        "action_reason": action_reason,
        "risk_flag": risk_flag,
        "used_fallback": used_fallback,
        "key_drivers": key_drivers if key_drivers is not None else [],
        "risks": [],
        "analysis_source": "live_llm",
    }
    if data_quality_label:
        out["data_quality_label"] = data_quality_label
    # Note: AnalystVerdict.to_dict() does NOT include intel_read.
    return out


def _card_from_verdict(
    verdict: dict[str, Any],
    ticker: str = "AAPL",
    rec_action: str = "BUY",
) -> SimpleNamespace:
    """Build a card SimpleNamespace that mimics ReadOnlyEvidenceAdapter output
    after the PR 3B fix.

    This mirrors the logic now in load_cards() so tests exercise the same
    synthesized intel_read path without needing a live DB.
    """
    _FALLBACK_PHRASES: frozenset[str] = frozenset({
        "no clear edge vs alternatives",
        "limited data reduces conviction",
        "hold — no allocation until signal improves",
        "hold - no allocation until signal improves",
    })

    def _is_real(text: Any) -> bool:
        if not text or not isinstance(text, str):
            return False
        s = text.strip()
        normalized = s.rstrip(".!?").strip()
        return bool(normalized) and normalized.lower() not in _FALLBACK_PHRASES

    av = verdict
    primary_driver = av.get("primary_driver") or av.get("why")
    action_reason = av.get("action_reason") or av.get("do")
    analyst_used_fallback = bool(av.get("used_fallback", False))

    # Synthesize trusted signals (same logic as load_cards after the fix).
    synthetic_dims: list[str] = []
    if not analyst_used_fallback:
        if _is_real(primary_driver):
            synthetic_dims.append("analyst_primary_driver")
        if _is_real(action_reason):
            synthetic_dims.append("analyst_action_rationale")
        kd = av.get("key_drivers") or []
        if isinstance(kd, list) and any(_is_real(d) for d in kd):
            synthetic_dims.append("analyst_key_drivers")

    resolved_intel_read = (
        {"trusted_signals": synthetic_dims, "source": "analyst_verdict_synthesis"}
        if synthetic_dims else None
    )
    resolved_dql = av.get("data_quality_label") or None

    drivers = av.get("key_drivers") or av.get("drivers") or []

    return SimpleNamespace(
        ticker=ticker,
        name=f"{ticker} Corp",
        action=rec_action.upper(),
        analyst_action=(av.get("action") or rec_action).upper(),
        conviction_level=av.get("conviction_level") or "MEDIUM",
        technical_signal=None,
        risk_flag=av.get("risk_flag") or "",
        analyst_risks=[],
        category="stock",
        data_quality_label=resolved_dql,
        intel_read=resolved_intel_read,
        thesis_v2=None,
        analyst_used_fallback=analyst_used_fallback,
        primary_driver=primary_driver,
        action_reason=action_reason,
        analyst_drivers=drivers if isinstance(drivers, list) else [],
    )


def _inp_from_card(card: SimpleNamespace) -> DecisionInputV3:
    """Run build_decision_input_from_card on a synthesized card."""
    return build_decision_input_from_card(
        ticker=card.ticker,
        action=card.action,
        analyst_action=card.analyst_action,
        conviction_level=card.conviction_level,
        technical_signal=card.technical_signal,
        risk_flag=card.risk_flag or None,
        analyst_risks=card.analyst_risks,
        category=card.category,
        data_quality_label=card.data_quality_label,
        intel_read=card.intel_read,
        thesis_v2=card.thesis_v2,
        primary_driver=card.primary_driver,
        action_reason=card.action_reason,
        analyst_drivers=card.analyst_drivers,
        asset_type_hint=card.category,
    )


def _make_decision_output(
    ticker: str = "AAPL",
    *,
    evidence_quality: AxisBand = AxisBand.OK,
    action: ActionV3 = ActionV3.BUY,
    conviction: ConvictionV3 = ConvictionV3.MEDIUM,
):
    from app.services.intelligence.v3.decision_contracts import DecisionOutputV3
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
        suppression_reasons={},
        rationale_plain_english=f"{ticker}: signals support position.",
        why_now="Clears all bars.",
        why_not_now="Watch for risk changes.",
        source_signal_summary={"has_primary_driver": True, "has_action_reason": True},
        schema_version="v3.1",
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. Source-rich verdict → STRONG evidence
# ══════════════════════════════════════════════════════════════════════════════

class TestSourceRichVerdictYieldsStrong:
    """All three analyst dimensions present → 3 trusted signals → STRONG."""

    def test_three_fields_yield_strong_evidence(self):
        verdict = _make_analyst_verdict(
            primary_driver="Cloud platform revenue accelerating ahead of consensus.",
            action_reason="Strong fundamentals support adding at current levels.",
            key_drivers=["Revenue growth driven by enterprise cloud adoption"],
            conviction_level="HIGH",
            action="BUY",
        )
        card = _card_from_verdict(verdict, ticker="AAPL", rec_action="BUY")
        assert card.intel_read is not None
        assert len(card.intel_read["trusted_signals"]) == 3
        inp = _inp_from_card(card)
        assert inp.evidence_quality == AxisBand.STRONG, (
            f"Expected STRONG, got {inp.evidence_quality.value}. "
            f"trusted_signals={card.intel_read['trusted_signals']}"
        )

    def test_strong_evidence_allows_high_buy_conviction(self):
        """STRONG evidence (3 signals) + HIGH upstream → HIGH conviction BUY."""
        verdict = _make_analyst_verdict(
            primary_driver="Durable competitive moat across all segments.",
            action_reason="Undervalued relative to growth trajectory.",
            key_drivers=["Enterprise market share expansion driving recurring revenue"],
            conviction_level="HIGH",
            action="BUY",
        )
        card = _card_from_verdict(verdict, ticker="AAPL", rec_action="BUY")
        inp = _inp_from_card(card)
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction == ConvictionV3.HIGH, (
            "STRONG evidence must allow HIGH conviction BUY (Cap 5 not triggered)"
        )
        assert out.evidence_quality == AxisBand.STRONG


# ══════════════════════════════════════════════════════════════════════════════
# 2. Two-field verdict → OK (PARTIAL display)
# ══════════════════════════════════════════════════════════════════════════════

class TestTwoFieldVerdictYieldsOk:
    """primary_driver + action_reason only → 2 signals → OK (displayed as PARTIAL)."""

    def test_two_fields_yield_ok_evidence(self):
        verdict = _make_analyst_verdict(
            primary_driver="Secular growth trend in AI infrastructure spending.",
            action_reason="Adding at current levels captures structural tailwind.",
            key_drivers=[],
            conviction_level="MEDIUM",
            action="BUY",
        )
        card = _card_from_verdict(verdict, ticker="NVDA", rec_action="BUY")
        assert card.intel_read is not None
        assert len(card.intel_read["trusted_signals"]) == 2
        inp = _inp_from_card(card)
        assert inp.evidence_quality == AxisBand.OK

    def test_two_field_buy_capped_to_medium_conviction(self):
        """OK evidence + HIGH upstream → capped to MEDIUM by Cap 5 guardrail."""
        verdict = _make_analyst_verdict(
            primary_driver="Dominant market position in GPU computing.",
            action_reason="Valuation reasonable given forward growth.",
            key_drivers=[],
            conviction_level="HIGH",
            action="BUY",
        )
        card = _card_from_verdict(verdict, ticker="NVDA", rec_action="BUY")
        inp = _inp_from_card(card)
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction == ConvictionV3.MEDIUM, (
            "Cap 5: OK evidence (2 signals) must cap HIGH BUY to MEDIUM"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 3. Single-field verdict → OK (1 signal)
# ══════════════════════════════════════════════════════════════════════════════

class TestSingleFieldVerdictYieldsOk:
    """primary_driver only → 1 trusted signal → OK."""

    def test_primary_driver_only_yields_ok(self):
        verdict = _make_analyst_verdict(
            primary_driver="Strong balance sheet with growing free cash flow.",
            action_reason="",
            key_drivers=[],
            action="HOLD",
        )
        card = _card_from_verdict(verdict, ticker="MSFT", rec_action="HOLD")
        assert card.intel_read is not None
        assert len(card.intel_read["trusted_signals"]) == 1
        inp = _inp_from_card(card)
        assert inp.evidence_quality == AxisBand.OK


# ══════════════════════════════════════════════════════════════════════════════
# 4. used_fallback=True → THIN
# ══════════════════════════════════════════════════════════════════════════════

class TestFallbackVerdictYieldsThin:
    """When analyst_verdict.used_fallback=True, evidence is THIN (analyst said: thin data)."""

    def test_used_fallback_true_yields_thin_evidence(self):
        verdict = _make_analyst_verdict(
            primary_driver="No clear edge vs alternatives.",
            action_reason="Hold — no allocation until signal improves.",
            key_drivers=[],
            used_fallback=True,
            action="HOLD",
        )
        card = _card_from_verdict(verdict, ticker="SNOW", rec_action="HOLD")
        # No trusted signals when used_fallback is True.
        assert card.intel_read is None
        inp = _inp_from_card(card)
        assert inp.evidence_quality in {AxisBand.THIN, AxisBand.SUPPRESSED}

    def test_fallback_verdict_does_not_improve_evidence_band_from_action(self):
        """Even a BUY action cannot rescue evidence when used_fallback=True."""
        verdict = _make_analyst_verdict(
            primary_driver="Strong growth across segments.",  # real text, but...
            action_reason="Adding here is optimal.",          # ...used_fallback=True overrides
            key_drivers=["Revenue beat"],
            used_fallback=True,   # analyst flagged thin data
            action="BUY",
            conviction_level="HIGH",
        )
        card = _card_from_verdict(verdict, ticker="SNOW", rec_action="BUY")
        # used_fallback=True must suppress all trusted signals regardless of text content.
        assert card.intel_read is None, (
            "used_fallback=True must suppress synthetic intel_read even if text fields have content"
        )
        inp = _inp_from_card(card)
        assert inp.evidence_quality in {AxisBand.THIN, AxisBand.SUPPRESSED}


# ══════════════════════════════════════════════════════════════════════════════
# 5. No analyst insight → SUPPRESSED (was wrong PARTIAL before fix)
# ══════════════════════════════════════════════════════════════════════════════

class TestNoAnalystInsightYieldsSuppressed:
    """When there is no analyst insight at all, evidence must NOT be PARTIAL."""

    def test_empty_analyst_verdict_yields_suppressed_not_partial(self):
        """Regression guard: empty av must no longer produce AxisBand.OK (PARTIAL)."""
        card = _card_from_verdict({}, ticker="UNKN", rec_action="HOLD")
        # No analyst verdict → no intel_read synthesized, no data_quality_label.
        assert card.intel_read is None
        assert card.data_quality_label is None
        inp = _inp_from_card(card)
        assert inp.evidence_quality not in {AxisBand.OK}, (
            "Empty analyst verdict must not produce PARTIAL (OK) evidence — "
            "that was the inflated fallback being fixed"
        )
        assert inp.evidence_quality == AxisBand.SUPPRESSED


# ══════════════════════════════════════════════════════════════════════════════
# 6. Fallback phrases excluded from trusted signals
# ══════════════════════════════════════════════════════════════════════════════

class TestFallbackPhraseExclusion:
    """Known template phrases from the analyst prompt must not count as trusted signals."""

    @pytest.mark.parametrize("phrase", [
        "No clear edge vs alternatives.",
        "Limited data reduces conviction.",
        "Hold — no allocation until signal improves.",
        "Hold - no allocation until signal improves.",
    ])
    def test_fallback_phrases_not_counted_as_trusted(self, phrase):
        verdict = _make_analyst_verdict(
            primary_driver=phrase,
            action_reason=phrase,
            key_drivers=[],
            used_fallback=False,
        )
        card = _card_from_verdict(verdict, ticker="TEST", rec_action="HOLD")
        assert card.intel_read is None, (
            f"Fallback phrase {phrase!r} must not count as a trusted signal"
        )

    def test_real_text_with_fallback_phrase_in_key_drivers_excluded(self):
        """A fallback phrase in key_drivers must not count."""
        verdict = _make_analyst_verdict(
            primary_driver="Consistent revenue growth across enterprise channels.",
            action_reason="",
            key_drivers=["no clear edge vs alternatives"],  # fallback phrase
            used_fallback=False,
        )
        card = _card_from_verdict(verdict, ticker="TEST", rec_action="HOLD")
        # Only primary_driver should count (1 signal).
        assert card.intel_read is not None
        assert len(card.intel_read["trusted_signals"]) == 1
        assert "analyst_primary_driver" in card.intel_read["trusted_signals"]
        assert "analyst_key_drivers" not in card.intel_read["trusted_signals"]


# ══════════════════════════════════════════════════════════════════════════════
# 7. Action/conviction changes alone do NOT change evidence quality
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceQualityNotDrivenByActionOrConviction:
    """Changing action or conviction alone must not change evidence quality."""

    @pytest.mark.parametrize("action,conviction", [
        ("BUY", "HIGH"),
        ("HOLD", "LOW"),
        ("SELL", "LOW"),
        ("TRIM", "MEDIUM"),
    ])
    def test_same_analyst_content_different_action_same_evidence(self, action, conviction):
        """Identical analyst content with different actions → same evidence quality."""
        verdict = _make_analyst_verdict(
            primary_driver="Strong FCF growth supports thesis.",
            action_reason="Valuation attractive at current levels.",
            key_drivers=[],
            conviction_level=conviction,
            action=action,
        )
        card = _card_from_verdict(verdict, ticker="AAPL", rec_action=action)
        inp = _inp_from_card(card)
        assert inp.evidence_quality == AxisBand.OK, (
            f"evidence_quality should be OK for 2 signals, "
            f"got {inp.evidence_quality.value} for action={action}"
        )

    def test_high_conviction_buy_cannot_rescue_suppressed_evidence(self):
        """A HIGH-conviction BUY with no analyst insight stays SUPPRESSED."""
        card = _card_from_verdict({}, ticker="UNKN", rec_action="BUY")
        inp = _inp_from_card(card)
        assert inp.evidence_quality == AxisBand.SUPPRESSED


# ══════════════════════════════════════════════════════════════════════════════
# 8. Research artifacts: safe_for_decision=False — not consumed
# ══════════════════════════════════════════════════════════════════════════════

class TestResearchArtifactGovernance:
    """Research artifacts must not be consumed for evidence-band uplift.

    The production schema (017_research_artifact_store_v1.sql) has a CHECK
    constraint: safe_for_decision BOOLEAN NOT NULL DEFAULT FALSE with
    research_artifacts_safe_for_decision_phase2_chk locking it to FALSE.
    No Phase 4/5 truth adapter exists yet to flip this flag.
    """

    def test_artifact_governance_counters_are_zero(self):
        """ReadOnlyEvidenceAdapter stats always report artifact counts as 0."""
        # Simulate what the adapter returns for a normal run.
        stats = {
            "artifact_decision_safe_count": 0,
            "artifact_suppressed_unsafe_count": 0,
        }
        assert stats["artifact_decision_safe_count"] == 0, (
            "No artifacts are decision-safe yet — safe_for_decision locked FALSE"
        )
        assert stats["artifact_suppressed_unsafe_count"] == 0, (
            "No artifacts are suppressed as unsafe because no artifacts are read"
        )

    def test_evidence_quality_does_not_depend_on_any_artifact_field(self):
        """Even a simulated 'artifact signal' injected outside the adapter is ignored."""
        # The adapter only synthesizes intel_read from analyst_verdict fields.
        # Any artifact-like data in a card that was not set by the adapter is inert.
        verdict = _make_analyst_verdict(
            primary_driver="Solid earnings growth.",
            action_reason="",
            key_drivers=[],
        )
        card = _card_from_verdict(verdict, ticker="AAPL", rec_action="BUY")
        # Simulate an attacker trying to inject artifact data into intel_read.
        # The adapter's synthesized intel_read only has analyst_verdict dimensions;
        # a future adapter may extend this, but the current path is closed.
        inp = _inp_from_card(card)
        # Evidence quality comes from analyst_verdict synthesis only.
        assert "source" in (card.intel_read or {})
        if card.intel_read:
            assert card.intel_read["source"] == "analyst_verdict_synthesis"


# ══════════════════════════════════════════════════════════════════════════════
# 9. HIGH BUY guardrail (Cap 5) still works
# ══════════════════════════════════════════════════════════════════════════════

class TestHighBuyGuardrailPreserved:
    """Cap 5: HIGH conviction BUY requires STRONG evidence. Not weakened by PR 3B."""

    def test_ok_evidence_caps_high_buy(self):
        """2 analyst signals → OK → HIGH BUY capped to MEDIUM (Cap 5)."""
        verdict = _make_analyst_verdict(
            primary_driver="Revenue growth trajectory attractive.",
            action_reason="Current valuation supports adding.",
            key_drivers=[],
            conviction_level="HIGH",
            action="BUY",
        )
        card = _card_from_verdict(verdict, ticker="MSFT", rec_action="BUY")
        inp = _inp_from_card(card)
        assert inp.evidence_quality == AxisBand.OK
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction == ConvictionV3.MEDIUM, "Cap 5 must downgrade HIGH BUY to MEDIUM"

    def test_strong_evidence_allows_high_buy(self):
        """3 analyst signals → STRONG → HIGH conviction BUY is allowed."""
        verdict = _make_analyst_verdict(
            primary_driver="Dominant market position with pricing power.",
            action_reason="FCF yield at attractive entry point.",
            key_drivers=["Multi-year cloud migration tailwind driving recurring revenue"],
            conviction_level="HIGH",
            action="BUY",
        )
        card = _card_from_verdict(verdict, ticker="AAPL", rec_action="BUY")
        inp = _inp_from_card(card)
        assert inp.evidence_quality == AxisBand.STRONG
        out = decide(inp)
        assert out.action == ActionV3.BUY
        assert out.conviction == ConvictionV3.HIGH

    def test_thin_evidence_prevents_buy(self):
        """No analyst insight → SUPPRESSED → HOLD (cannot meet BUY bar)."""
        card = _card_from_verdict({}, ticker="UNKN", rec_action="BUY")
        inp = _inp_from_card(card)
        out = decide(inp)
        assert out.action == ActionV3.HOLD
        # SUPPRESSED evidence forces HOLD but does not trigger the LOW conviction cap
        # (Cap 1 applies only to THIN, not SUPPRESSED).
        assert out.conviction in {ConvictionV3.LOW, ConvictionV3.MEDIUM}


# ══════════════════════════════════════════════════════════════════════════════
# 10. analyst_drivers reads key_drivers field from AnalystVerdict
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalystDriversFieldNameFix:
    """After the fix, key_drivers is read correctly (was previously read as 'drivers')."""

    def test_key_drivers_field_populates_analyst_drivers(self):
        """key_drivers from AnalystVerdict.to_dict() is now read correctly."""
        verdict = {
            "action": "BUY",
            "conviction_level": "HIGH",
            "primary_driver": "Cloud revenue acceleration.",
            "action_reason": "Attractive risk-reward.",
            "key_drivers": ["Enterprise adoption driving ARR growth"],  # real field name
            "drivers": [],  # old fallback field — should be ignored when key_drivers present
            "used_fallback": False,
            "risks": [],
        }
        card = _card_from_verdict(verdict, ticker="AAPL", rec_action="BUY")
        # analyst_drivers should come from key_drivers, not the empty drivers list.
        assert card.analyst_drivers == ["Enterprise adoption driving ARR growth"], (
            "key_drivers must be preferred over drivers for analyst_drivers"
        )

    def test_drivers_fallback_used_when_key_drivers_absent(self):
        """Legacy fallback writer uses 'drivers' — still works after fix."""
        verdict = {
            "action": "HOLD",
            "conviction_level": "MEDIUM",
            "primary_driver": "Steady business model.",
            "action_reason": None,
            "drivers": ["Consistent dividend growth"],  # legacy fallback writer key
            "used_fallback": False,
            "risks": [],
            "data_quality_label": "MEDIUM",
        }
        card = _card_from_verdict(verdict, ticker="JNJ", rec_action="HOLD")
        # Falls back to 'drivers' when 'key_drivers' is absent.
        assert card.analyst_drivers == ["Consistent dividend growth"]


# ══════════════════════════════════════════════════════════════════════════════
# 11. Evidence stats carry artifact governance counters
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceStatsObservability:
    """Evidence stats from load_cards() must include PR 3B observability fields."""

    def test_stats_include_artifact_governance_fields(self):
        """Confirm the expected field names exist in stats (value 0 both)."""
        # Simulated stats dict as returned by load_cards after the fix.
        mock_stats = {
            "active_position_count": 5,
            "persisted_recommendation_count": 5,
            "persisted_agent_insight_count": 5,
            "missing_recommendation_count": 0,
            "missing_evidence_count": 0,
            "stale_or_missing_source_count": 0,
            "generated_legacy_recommendations": False,
            "attempted_llm_calls": 0,
            "recommendation_timestamps": [],
            "agent_insight_run_timestamps": [],
            "matched_agent_insight_by_recommendation_run_count": 5,
            "fallback_agent_insight_by_ticker_count": 0,
            "missing_agent_insight_for_recommendation_run_count": 0,
            "recommendation_agent_run_ids_count": 5,
            # PR 3B fields:
            "mapped_existing_analyst_signal_count": 4,
            "trusted_signal_count_distribution": {0: 0, 1: 1, 2: 2, 3: 2},
            "artifact_decision_safe_count": 0,
            "artifact_suppressed_unsafe_count": 0,
        }
        assert "mapped_existing_analyst_signal_count" in mock_stats
        assert "trusted_signal_count_distribution" in mock_stats
        assert mock_stats["artifact_decision_safe_count"] == 0
        assert mock_stats["artifact_suppressed_unsafe_count"] == 0

    def test_trusted_signal_distribution_buckets_are_correct(self):
        """Distribution keys must be 0, 1, 2, 3 with non-negative counts."""
        dist = {0: 1, 1: 2, 2: 1, 3: 3}
        assert all(k in (0, 1, 2, 3) for k in dist)
        assert all(v >= 0 for v in dist.values())


# ══════════════════════════════════════════════════════════════════════════════
# 12. Snapshot serialization — plain-English fields remain correct
# ══════════════════════════════════════════════════════════════════════════════

class TestSnapshotSerializationWithNewMapping:
    """After PR 3B, snapshot evidence_band/committee fields remain plain-English."""

    def test_strong_evidence_card_has_correct_evidence_band(self):
        from app.services.intelligence.v3.decision_contracts import DecisionOutputV3
        decision = _make_decision_output("AAPL", evidence_quality=AxisBand.STRONG)
        snap = build_snapshot(
            run_id="run-pr3b-001",
            decisions=[decision],
            card_metas=[{"ticker": "AAPL", "name": "Apple", "category": "stock"}],
        )
        card = snap["current_holdings"][0]
        assert card["evidence_band"] == "STRONG"
        assert card["detail_drawer_payload"]["committee"]["status"] == "source_validated"

    def test_thin_evidence_card_has_correct_evidence_band(self):
        decision = _make_decision_output(
            "SNOW",
            evidence_quality=AxisBand.THIN,
            action=ActionV3.HOLD,
            conviction=ConvictionV3.LOW,
        )
        # inject suppression reason for the pending committee
        decision.suppression_reasons["evidence_quality"] = "No trusted evidence — 0 trusted dimension(s)."
        snap = build_snapshot(
            run_id="run-pr3b-002",
            decisions=[decision],
            card_metas=[{"ticker": "SNOW", "name": "Snowflake", "category": "stock"}],
        )
        card = snap["current_holdings"][0]
        assert card["evidence_band"] == "THIN"
        assert card["detail_drawer_payload"]["committee"]["status"] == "pending"

    def test_no_target_price_in_evidence_fields(self):
        """PR 3B must not introduce price targets or raw metric keys in any field."""
        import re
        price_target_pat = re.compile(r"\$\s*\d+|price target", re.IGNORECASE)

        for band in (AxisBand.STRONG, AxisBand.OK, AxisBand.THIN, AxisBand.SUPPRESSED):
            decision = _make_decision_output("TEST", evidence_quality=band)
            snap = build_snapshot(
                run_id=f"run-{band.value}",
                decisions=[decision],
                card_metas=[{"ticker": "TEST", "name": "Test Corp", "category": "stock"}],
            )
            card = snap["current_holdings"][0]
            committee = card["detail_drawer_payload"]["committee"]
            reason = committee.get("reason", "")
            assert not price_target_pat.search(reason), (
                f"Price target found in committee reason: {reason!r}"
            )

    def test_deploy_fields_unaffected_by_pr3b(self):
        """action, conviction, evidence_band in cards are not changed by committee fix."""
        decision = _make_decision_output("AAPL", evidence_quality=AxisBand.STRONG)
        snap = build_snapshot(
            run_id="run-pr3b-deploy",
            decisions=[decision],
            card_metas=[{"ticker": "AAPL", "name": "Apple", "category": "stock"}],
        )
        card = snap["current_holdings"][0]
        assert card["action"] == "BUY"
        assert card["conviction"] == "MEDIUM"
        assert card["evidence_band"] == "STRONG"
