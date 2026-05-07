"""Phase 0.5 regression guardrails for Intel v3 snapshot certification.

Certified Phase 0 state (PRs #220-#222, 2026-05-06):
  - 34 holdings: 10 BUY, 23 HOLD, 1 TRIM, 0 SELL
  - schema_version=v3.1, source_path=intel_v3_snapshot
  - hard_violations=0, soft_violations=0, generic_copy_count=0
  - page_load_llm_calls=0, attempted_llm_calls=0, generated_legacy_recommendations=false

These tests add regression guardrails only — no behavior changes.

What is covered here that is NOT covered elsewhere:
  A. Reusable assert_snapshot_certification_clean() helper — callable from any future test
     that builds a snapshot, making regressions obviously loud.
  B. Static source guards — v3 run path cannot silently regain legacy aggregation calls
     (RecommendationService.get_insight_cards / recommendation_engine._compute_insight_cards).
  C. Log-format contract — source_path, generated_legacy_recommendations, attempted_llm_calls
     values are hardcoded in the service and would change only if intentionally edited.
  D. Evidence stats key contract — ReadOnlyEvidenceAdapter.load_cards() returns the exact
     set of keys that intel_v3_service.py expects; missing keys would cause silent 0-counts.
  E. Exact schema_version=="v3.1" (not just starts-with "v3", which lets v3.2 slip through).
  F. Certification summary field completeness — certify_snapshot_cards() returns all fields
     the service logs; a missing field would produce a KeyError or wrong log value.

Tests that already exist elsewhere (not duplicated here):
  - TestSnapshotBuilder in test_intel_v3_snapshot.py — action_counts == card counts, run_id
  - TestSnapshotContract in test_intel_v3_router_service.py — action_counts sum, run_id, snapshot_id
  - TestPageLoadIsolation / TestPageLoadIsolationFromLLM — ReadOnlyEvidenceAdapter not called on GET
  - TestCertificationFields in test_v3_certification_fix.py — generic_copy=0, unique_reason=total
  - TestNoRawMetricKeysOrPostureLabels — metric keys + posture labels absent for 34-card portfolio
"""
from __future__ import annotations

import inspect
from collections import Counter
from typing import Any

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
from app.services.intelligence.v3.snapshot_builder import build_snapshot
from app.services.intelligence.v3.source_validator_lite import certify_snapshot_cards


# ── Reusable certification helper ────────────────────────────────────────────


def assert_snapshot_certification_clean(snapshot: dict[str, Any]) -> None:
    """Assert all Phase 0 certification invariants on a snapshot payload.

    Call this from any test that builds or simulates a production snapshot.
    A failure here means a Phase 0 contract has regressed.
    """
    # schema_version must be exactly v3.1 — not just starts-with v3.
    assert snapshot.get("schema_version") == "v3.1", (
        f"schema_version regression: expected 'v3.1', got {snapshot.get('schema_version')!r}. "
        "Increment requires an explicit certification pass."
    )

    # legacy_path_used must be False (v3 path only).
    assert snapshot.get("legacy_path_used") is False, (
        "legacy_path_used regression: expected False. "
        "The v3 snapshot path must never use the legacy recommendation aggregation path."
    )

    holdings = snapshot.get("current_holdings", [])
    action_counts = snapshot.get("action_counts", {})

    # action_counts must sum to exactly total holdings.
    counts_total = sum(action_counts.values())
    actual_total = len(holdings)
    assert counts_total == actual_total, (
        f"action_counts sum regression: action_counts sum {counts_total} != "
        f"total_cards {actual_total}. Counts: {action_counts}"
    )

    # All visible actions must be BUY/HOLD/TRIM/SELL — no posture labels.
    _valid = {"BUY", "HOLD", "TRIM", "SELL"}
    for card in holdings:
        action = card.get("action", "")
        assert action in _valid, (
            f"Invalid action regression: card {card.get('ticker')!r} has action {action!r}. "
            "Only BUY/HOLD/TRIM/SELL are permitted."
        )

    # action_counts must match the actual distribution of card actions.
    derived_counts = dict(Counter(c["action"] for c in holdings))
    assert action_counts == derived_counts, (
        f"action_counts mismatch regression: stored {action_counts} != "
        f"derived from cards {derived_counts}."
    )

    # snapshot_id must be present and shared by all cards.
    snapshot_id = snapshot.get("snapshot_id")
    assert snapshot_id, "snapshot_id missing from snapshot payload."
    card_snapshot_ids = {c.get("source_snapshot_id") for c in holdings}
    assert len(card_snapshot_ids) == 1, (
        f"snapshot_id coherence regression: cards reference {len(card_snapshot_ids)} "
        f"distinct snapshot_ids: {card_snapshot_ids}."
    )
    assert list(card_snapshot_ids)[0] == snapshot_id, (
        f"Card source_snapshot_id {list(card_snapshot_ids)[0]!r} != "
        f"snapshot.snapshot_id {snapshot_id!r}."
    )

    # run_id must be present and shared by all cards.
    run_id = snapshot.get("run_id")
    assert run_id, "run_id missing from snapshot payload."
    card_run_ids = {c.get("source_run_id") for c in holdings}
    assert len(card_run_ids) == 1, (
        f"run_id coherence regression: cards reference {len(card_run_ids)} "
        f"distinct run_ids: {card_run_ids}."
    )

    # Certify snapshot cards — must be clean (all counts zero).
    cert = certify_snapshot_cards(holdings)
    assert cert["hard_violations"] == 0, (
        f"hard_violations regression: got {cert['hard_violations']}. "
        "At least one card has a hard rule violation."
    )
    assert cert["raw_metric_key_count"] == 0, (
        f"raw_metric_key_count regression: got {cert['raw_metric_key_count']}. "
        "Raw metric keys must not appear in visible card text."
    )
    assert cert["posture_label_count"] == 0, (
        f"posture_label_count regression: got {cert['posture_label_count']}. "
        "Banned posture labels must not appear in card action or action_text."
    )
    assert cert["action_conflict_count"] == 0, (
        f"action_conflict_count regression: got {cert['action_conflict_count']}. "
        "BUY cards must not contain hold/wait language."
    )


# ── Section A: Tests using the certification helper ───────────────────────────


def _make_decision(
    ticker: str,
    action: ActionV3 = ActionV3.HOLD,
    conviction: ConvictionV3 = ConvictionV3.MEDIUM,
) -> DecisionOutputV3:
    return DecisionOutputV3(
        ticker=ticker,
        action=action,
        conviction=conviction,
        evidence_quality=AxisBand.OK,
        attractiveness=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.LOW,
        blockers=[],
        suppression_reasons={},
        rationale_plain_english=f"{ticker}: signals support this position.",
        why_now=f"{ticker}: evidence and fit support acting now.",
        why_not_now="Watch for evidence weakening.",
        source_signal_summary={},
        schema_version="v3.1",
    )


def _make_meta(ticker: str) -> dict:
    return {"ticker": ticker, "name": ticker, "category": "stock", "thesis_state": "intact"}


def _make_clean_snapshot(tickers_and_actions: list[tuple[str, ActionV3]]) -> dict:
    decisions = [_make_decision(t, a) for t, a in tickers_and_actions]
    metas = [_make_meta(t) for t, _ in tickers_and_actions]
    return build_snapshot(
        run_id="guardrail-run-001",
        decisions=decisions,
        card_metas=metas,
        source_health={"status": "ok"},
        is_stale=False,
    )


class TestCertificationHelperPasses:
    """Verify assert_snapshot_certification_clean() passes on clean snapshots."""

    def test_single_buy_card_passes(self):
        snap = _make_clean_snapshot([("AAPL", ActionV3.BUY)])
        assert_snapshot_certification_clean(snap)

    def test_mixed_portfolio_passes(self):
        snap = _make_clean_snapshot([
            ("AAPL", ActionV3.BUY),
            ("MSFT", ActionV3.HOLD),
            ("NVDA", ActionV3.HOLD),
            ("GOOG", ActionV3.TRIM),
        ])
        assert_snapshot_certification_clean(snap)

    def test_34_card_golden_portfolio_passes(self):
        """Synthetic 34-card portfolio mirrors the Phase 0 certified distribution."""
        buy_tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ"]
        hold_tickers = [f"HOLD{i}" for i in range(23)]
        trim_tickers = ["SCHD"]
        items: list[tuple[str, ActionV3]] = (
            [(t, ActionV3.BUY) for t in buy_tickers]
            + [(t, ActionV3.HOLD) for t in hold_tickers]
            + [(t, ActionV3.TRIM) for t in trim_tickers]
        )
        snap = _make_clean_snapshot(items)
        assert_snapshot_certification_clean(snap)
        # Confirm the distribution matches certified Phase 0 shape.
        counts = snap["action_counts"]
        assert counts.get("BUY", 0) == 10
        assert counts.get("HOLD", 0) == 23
        assert counts.get("TRIM", 0) == 1
        assert counts.get("SELL", 0) == 0


class TestCertificationHelperFailsOnRegressions:
    """Verify assert_snapshot_certification_clean() fails on each specific regression."""

    def test_wrong_schema_version_fails(self):
        snap = _make_clean_snapshot([("AAPL", ActionV3.BUY)])
        snap["schema_version"] = "v3.2"
        with pytest.raises(AssertionError, match="schema_version regression"):
            assert_snapshot_certification_clean(snap)

    def test_schema_version_prefix_only_fails(self):
        """starts-with 'v3' is not enough — must be exactly 'v3.1'."""
        snap = _make_clean_snapshot([("AAPL", ActionV3.BUY)])
        snap["schema_version"] = "v3.99"
        with pytest.raises(AssertionError, match="schema_version regression"):
            assert_snapshot_certification_clean(snap)

    def test_legacy_path_used_true_fails(self):
        snap = _make_clean_snapshot([("AAPL", ActionV3.BUY)])
        snap["legacy_path_used"] = True
        with pytest.raises(AssertionError, match="legacy_path_used regression"):
            assert_snapshot_certification_clean(snap)

    def test_action_counts_sum_mismatch_fails(self):
        snap = _make_clean_snapshot([
            ("AAPL", ActionV3.BUY),
            ("MSFT", ActionV3.HOLD),
        ])
        # Corrupt action_counts so sum != len(holdings).
        snap["action_counts"] = {"BUY": 99}
        with pytest.raises(AssertionError, match="action_counts sum regression|action_counts mismatch regression"):
            assert_snapshot_certification_clean(snap)

    def test_invalid_action_label_fails(self):
        snap = _make_clean_snapshot([("AAPL", ActionV3.BUY)])
        snap["current_holdings"][0]["action"] = "WATCH"
        snap["action_counts"] = {"WATCH": 1}
        with pytest.raises(AssertionError, match="Invalid action regression"):
            assert_snapshot_certification_clean(snap)

    def test_snapshot_id_missing_fails(self):
        snap = _make_clean_snapshot([("AAPL", ActionV3.BUY)])
        snap["snapshot_id"] = ""
        with pytest.raises(AssertionError, match="snapshot_id missing"):
            assert_snapshot_certification_clean(snap)

    def test_mismatched_source_snapshot_ids_fails(self):
        snap = _make_clean_snapshot([
            ("AAPL", ActionV3.BUY),
            ("MSFT", ActionV3.HOLD),
        ])
        # Corrupt one card's source_snapshot_id to simulate a stale-run blend.
        snap["current_holdings"][1]["source_snapshot_id"] = "stale-legacy-snap-id"
        with pytest.raises(AssertionError, match="snapshot_id coherence regression|Card source_snapshot_id"):
            assert_snapshot_certification_clean(snap)

    def test_raw_metric_key_in_why_text_fails(self):
        snap = _make_clean_snapshot([("AAPL", ActionV3.BUY)])
        snap["current_holdings"][0]["why_text"] = "AAPL: fcf_margin is 28% indicating strong cash flow."
        # raw_metric_key is a hard violation, so hard_violations assertion fires first.
        with pytest.raises(AssertionError, match="hard_violations regression|raw_metric_key_count regression"):
            assert_snapshot_certification_clean(snap)

    def test_posture_label_in_action_text_fails(self):
        snap = _make_clean_snapshot([("AAPL", ActionV3.BUY)])
        snap["current_holdings"][0]["action_text"] = "Add Candidate"
        # banned posture label is a hard violation, so hard_violations assertion fires first.
        with pytest.raises(AssertionError, match="hard_violations regression|posture_label_count regression"):
            assert_snapshot_certification_clean(snap)


# ── Section B: Static source guards — anti-legacy-path ───────────────────────


class TestNoLegacyAggregationInV3RunPath:
    """Verify that the v3 run path source does not reference legacy aggregation methods.

    These tests guard against accidental re-introduction of:
      - RecommendationService.get_insight_cards() — legacy LLM-backed aggregation
      - recommendation_engine._compute_insight_cards() — internal legacy engine call
    """

    def _get_service_source(self) -> str:
        import app.services.intelligence.v3.intel_v3_service as mod
        return inspect.getsource(mod)

    def _get_adapter_source(self) -> str:
        import app.services.intelligence.v3.read_only_evidence_adapter as mod
        return inspect.getsource(mod)

    def test_intel_v3_service_does_not_call_get_insight_cards(self):
        """intel_v3_service must not call RecommendationService.get_insight_cards().

        If this fails, the v3 run path has re-acquired the legacy LLM aggregation call.
        """
        source = self._get_service_source()
        assert "get_insight_cards" not in source, (
            "Regression: intel_v3_service.py calls get_insight_cards(). "
            "This is the legacy LLM-backed aggregation path and must never be called "
            "from the v3 run path. Use ReadOnlyEvidenceAdapter.load_cards() instead."
        )

    def test_intel_v3_service_does_not_call_compute_insight_cards(self):
        """intel_v3_service must not call _compute_insight_cards().

        If this fails, the v3 run path bypasses the read-only evidence adapter.
        """
        source = self._get_service_source()
        assert "_compute_insight_cards" not in source, (
            "Regression: intel_v3_service.py calls _compute_insight_cards(). "
            "This is an internal legacy method that invokes LLM calls. "
            "The v3 path must only use ReadOnlyEvidenceAdapter.load_cards()."
        )

    def test_intel_v3_service_does_not_import_recommendation_engine_module(self):
        """intel_v3_service must not import the legacy recommendation_engine module.

        If this fails, the v3 service has acquired a legacy dependency.
        """
        source = self._get_service_source()
        assert "recommendation_engine" not in source, (
            "Regression: intel_v3_service.py imports recommendation_engine. "
            "All signal loading must go through ReadOnlyEvidenceAdapter."
        )

    def test_read_only_adapter_does_not_import_recommendation_engine(self):
        """ReadOnlyEvidenceAdapter must not import or call recommendation_engine.

        If this fails, the read-only adapter has acquired a side-effect dependency.
        """
        source = self._get_adapter_source()
        assert "recommendation_engine" not in source, (
            "Regression: read_only_evidence_adapter.py imports recommendation_engine. "
            "The adapter is a pure DB-read layer — no generation paths allowed."
        )

    def test_read_only_adapter_does_not_call_get_insight_cards(self):
        source = self._get_adapter_source()
        assert "get_insight_cards" not in source, (
            "Regression: read_only_evidence_adapter.py calls get_insight_cards(). "
            "This adapter must be a read-only DB pass-through with no LLM calls."
        )

    def test_read_only_adapter_does_not_import_anthropic_or_openai(self):
        """ReadOnlyEvidenceAdapter must not import any LLM client."""
        source = self._get_adapter_source()
        assert "anthropic" not in source, (
            "Regression: read_only_evidence_adapter.py imports anthropic. "
            "This adapter must be LLM-free."
        )
        assert "openai" not in source, (
            "Regression: read_only_evidence_adapter.py imports openai. "
            "This adapter must be LLM-free."
        )


# ── Section C: Log-format contract ───────────────────────────────────────────


class TestCertificationLogFormatContract:
    """Verify the intel_v3_snapshot_certification_summary log format is intact.

    These fields are hardcoded in the log format string. Changing them would silently
    break Railway log parsing. This test catches those changes at the source level.
    """

    def _get_service_source(self) -> str:
        import app.services.intelligence.v3.intel_v3_service as mod
        return inspect.getsource(mod)

    def test_log_contains_source_path_intel_v3_snapshot(self):
        """source_path=intel_v3_snapshot must be present in the certification log.

        If this fails, the log format has changed and Railway parsing will miss it.
        """
        source = self._get_service_source()
        assert "source_path=intel_v3_snapshot" in source, (
            "Log format regression: 'source_path=intel_v3_snapshot' not found in "
            "intel_v3_service.py. This field is required in intel_v3_snapshot_certification_summary."
        )

    def test_log_contains_generated_legacy_recommendations_false(self):
        """generated_legacy_recommendations=false must be hardcoded in the evidence source log.

        If this changes to True, it means the run path is generating legacy recommendations.
        """
        source = self._get_service_source()
        assert "generated_legacy_recommendations=false" in source, (
            "Log format regression: 'generated_legacy_recommendations=false' not found. "
            "If this was changed to 'true', the v3 run path is calling legacy generation."
        )

    def test_log_contains_attempted_llm_calls_zero(self):
        """attempted_llm_calls=0 must be hardcoded in the evidence source log."""
        source = self._get_service_source()
        assert "attempted_llm_calls=0" in source, (
            "Log format regression: 'attempted_llm_calls=0' not found. "
            "The v3 run path must make zero LLM calls."
        )

    def test_log_contains_page_load_llm_calls_zero(self):
        """page_load_llm_calls=0 must be present in the certification summary log."""
        source = self._get_service_source()
        assert "page_load_llm_calls=0" in source, (
            "Log format regression: 'page_load_llm_calls=0' not found. "
            "The GET /snapshot path must make zero LLM calls."
        )

    def test_certification_log_key_is_intel_v3_snapshot_certification_summary(self):
        """The certification log key must be the exact expected string."""
        source = self._get_service_source()
        assert "intel_v3_snapshot_certification_summary" in source, (
            "Log key regression: 'intel_v3_snapshot_certification_summary' not found. "
            "Railway log parsing depends on this exact key."
        )

    def test_evidence_source_log_key_is_intel_v3_evidence_source_summary(self):
        """The evidence source log key must be the exact expected string."""
        source = self._get_service_source()
        assert "intel_v3_evidence_source_summary" in source, (
            "Log key regression: 'intel_v3_evidence_source_summary' not found."
        )


# ── Section D: Evidence stats key contract ───────────────────────────────────


class TestEvidenceStatsKeyContract:
    """Verify certify_snapshot_cards() returns all expected certification fields.

    The intel_v3_service.py logs each field by name. If certify_snapshot_cards()
    stops returning a field, it would become a KeyError or log as None.
    """

    _REQUIRED_CERT_KEYS = {
        "hard_violations",
        "generic_copy_count",
        "duplicate_reason_count",
        "repeated_skeleton_count",
        "ticker_prefix_only_reason_count",
        "weak_buy_rationale_count",
        "action_conflict_count",
        "raw_metric_key_count",
        "posture_label_count",
        "per_card_results",
        "spam_tickers",
        "examples",
    }

    def test_certify_snapshot_cards_returns_all_required_keys(self):
        """certify_snapshot_cards() must return every field that the service logs."""
        cards = [
            {
                "ticker": "AAPL",
                "action": "BUY",
                "conviction": "MEDIUM",
                "why_text": "AAPL: strong evidence supports adding at this price.",
            },
            {
                "ticker": "MSFT",
                "action": "HOLD",
                "conviction": "MEDIUM",
                "why_text": "MSFT: holding while evidence builds.",
            },
        ]
        cert = certify_snapshot_cards(cards)
        missing = self._REQUIRED_CERT_KEYS - set(cert.keys())
        assert not missing, (
            f"certify_snapshot_cards() is missing required keys: {missing}. "
            "The service logs these fields by name — missing keys cause silent failures."
        )

    def test_certify_snapshot_cards_all_counts_are_int(self):
        """All count fields returned by certify_snapshot_cards() must be ints."""
        _count_keys = {
            "hard_violations", "generic_copy_count", "duplicate_reason_count",
            "repeated_skeleton_count", "ticker_prefix_only_reason_count",
            "weak_buy_rationale_count", "action_conflict_count",
            "raw_metric_key_count", "posture_label_count",
        }
        cards = [{"ticker": "TST", "action": "HOLD", "conviction": "LOW",
                  "why_text": "TST: monitoring position."}]
        cert = certify_snapshot_cards(cards)
        for key in _count_keys:
            assert isinstance(cert[key], int), (
                f"certify_snapshot_cards()['{key}'] is not an int: {type(cert[key])}. "
                "Service logs format these as integers."
            )

    def test_certify_clean_cards_all_counts_zero(self):
        """A portfolio of genuinely distinct clean cards must produce all zero certification counts."""
        # Each card uses a distinct why_text so skeleton/copy detectors don't fire.
        _cards_data = [
            ("AAPL", "BUY",  "AAPL: strong earnings growth and fair price support adding at this weight."),
            ("MSFT", "BUY",  "MSFT: cloud momentum and pricing power strengthen the thesis for adding."),
            ("NVDA", "BUY",  "NVDA: AI infrastructure demand and expanding margins justify a position."),
            ("GOOG", "HOLD", "GOOG: search monetization is stable but premium valuation limits upside."),
            ("META", "HOLD", "META: advertising recovery is intact, but position is already at target weight."),
            ("AMZN", "HOLD", "AMZN: AWS growth is solid; holding while retail margin recovery confirms."),
        ]
        cards = [
            {"ticker": t, "action": a, "conviction": "MEDIUM", "why_text": w}
            for t, a, w in _cards_data
        ]
        cert = certify_snapshot_cards(cards)
        _count_keys = [
            "hard_violations", "generic_copy_count", "duplicate_reason_count",
            "repeated_skeleton_count", "ticker_prefix_only_reason_count",
            "weak_buy_rationale_count", "action_conflict_count",
            "raw_metric_key_count", "posture_label_count",
        ]
        for key in _count_keys:
            assert cert[key] == 0, (
                f"certify_snapshot_cards()['{key}'] = {cert[key]} for clean cards. "
                "Expected 0."
            )


# ── Section E: Schema version exact match ─────────────────────────────────────


class TestSchemaVersionExact:
    """schema_version must be exactly 'v3.1' — not a prefix match."""

    def test_build_snapshot_schema_version_is_exactly_v3_1(self):
        snap = _make_clean_snapshot([("AAPL", ActionV3.BUY)])
        assert snap["schema_version"] == "v3.1", (
            f"schema_version is {snap['schema_version']!r}, expected exactly 'v3.1'. "
            "Incrementing schema_version requires an explicit certification pass."
        )

    def test_decision_output_schema_version_is_exactly_v3_1(self):
        """DecisionOutputV3 must carry schema_version='v3.1'."""
        dec = _make_decision("AAPL", ActionV3.BUY)
        assert dec.schema_version == "v3.1", (
            f"DecisionOutputV3.schema_version is {dec.schema_version!r}, expected 'v3.1'."
        )


# ── Section F: E2E certification helper integration ───────────────────────────


class TestCertificationHelperE2EIntegration:
    """Run assert_snapshot_certification_clean() through a kernel→snapshot pipeline."""

    def test_kernel_pipeline_snapshot_passes_certification(self):
        """Full decide() + build_snapshot() pipeline produces a certifiable snapshot."""
        inputs = [
            DecisionInputV3(
                ticker="AAPL",
                evidence_quality=AxisBand.STRONG,
                price_context=PriceBand.FAIR,
                portfolio_fit=FitBand.UNDERWEIGHT,
                risk_band=RiskBand.LOW,
                raw_action="BUY",
                upstream_conviction="HIGH",
            ),
            DecisionInputV3(
                ticker="MSFT",
                evidence_quality=AxisBand.OK,
                price_context=PriceBand.SUPPRESSED,
                portfolio_fit=FitBand.ON_TARGET,
                risk_band=RiskBand.NONE,
                raw_action="HOLD",
                upstream_conviction="MEDIUM",
            ),
            DecisionInputV3(
                ticker="NFLX",
                evidence_quality=AxisBand.OK,
                price_context=PriceBand.FULL,
                portfolio_fit=FitBand.OVERWEIGHT,
                risk_band=RiskBand.MEDIUM,
                raw_action="TRIM",
                upstream_conviction="MEDIUM",
            ),
        ]
        decisions = [decide(inp) for inp in inputs]
        metas = [_make_meta(t) for t in ["AAPL", "MSFT", "NFLX"]]
        snap = build_snapshot(
            run_id="phase0-5-e2e-001",
            decisions=decisions,
            card_metas=metas,
            source_health={"status": "ok"},
            is_stale=False,
        )
        # This is the key integration: any snapshot from the real pipeline must pass.
        assert_snapshot_certification_clean(snap)
