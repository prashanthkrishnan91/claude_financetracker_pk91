"""Tests for the Stage 3.0b Evidence Refresh Orchestrator v1.

Covers acceptance criteria from the task brief:
  - Production-like stale ages (191.8h / 286.1h) do NOT classify FAST_CERTIFIED.
  - All-fresh evidence classifies FAST_CERTIFIED.
  - Stale refreshable evidence attempts refresh, then runs policy.
  - Failed refresh downgrades to PARTIAL_CERTIFIED / BLOCKED_UNCERTIFIED.
  - LLM/agent refresh budget caps calls.
  - Provider budget caps calls.
  - Repeated identical fresh inputs produce same decisions.
  - Refreshed analyst evidence can change decision ONLY through deterministic
    policy (analyst-refresh injection is gated; v1 path stays not_supported).
  - Stale evidence is surfaced honestly in diagnostics — never silently accepted.
  - No Deploy/Watchtower module is imported or touched.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.intelligence.v3.evidence_freshness_contract_v1 import (
    BANNER_COPY,
    CRITICAL_SOURCES,
    RUN_MODE_BLOCKED_UNCERTIFIED,
    RUN_MODE_FAST_CERTIFIED,
    RUN_MODE_PARTIAL_CERTIFIED,
    RUN_MODE_REFRESH_THEN_RUN,
    SOURCE_AGENT_INSIGHTS,
    SOURCE_PORTFOLIO_SNAPSHOT,
    SOURCE_POSITIONS,
    SOURCE_PRICE_HISTORY,
    SOURCE_PRICE_LATEST,
    SOURCE_RECOMMENDATIONS,
    SOURCE_RESEARCH_ARTIFACTS,
    SOURCE_SLAS,
    STATE_FRESH,
    STATE_HARD_STALE,
    STATE_MISSING,
    STATE_STALE,
    STATE_UNKNOWN,
    TRUST_PARTIAL,
    TRUST_TRUSTED,
    TRUST_UNCERTIFIED,
    classify_run_mode,
    classify_source_state,
)
from app.services.intelligence.v3.evidence_refresh_orchestrator_v1 import (
    MAX_LLM_CALLS_PER_RUN,
    MAX_PROVIDER_CALLS_PER_RUN,
    EvidenceRefreshOrchestrator,
    OrchestratorInputs,
    RefreshBudget,
    build_source_states,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def _iso_ago(hours: float, now: datetime | None = None) -> str:
    base = now or _now()
    return (base - timedelta(hours=hours)).isoformat()


def _fresh_inputs(now: datetime | None = None) -> OrchestratorInputs:
    now = now or _now()
    return OrchestratorInputs(
        evidence_stats={
            "recommendation_timestamps":     [_iso_ago(1.0, now), _iso_ago(2.0, now)],
            "agent_insight_run_timestamps":  [_iso_ago(1.5, now), _iso_ago(3.0, now)],
            "active_position_count":         2,
            "persisted_recommendation_count": 2,
            "persisted_agent_insight_count":  2,
        },
        portfolio_snapshot_at=_iso_ago(0.5, now),
        market_value_certified_ats=[_iso_ago(0.1, now), _iso_ago(0.1, now)],
        tickers=["AAPL", "NVDA"],
        research_artifact_timestamps=[],
        now=now,
    )


def _production_stale_inputs(now: datetime | None = None) -> OrchestratorInputs:
    """Replicates the production state: rec=191.8h, insight=286.1h, 68 stale signals."""
    now = now or _now()
    return OrchestratorInputs(
        evidence_stats={
            "recommendation_timestamps":     [_iso_ago(191.8, now), _iso_ago(160.0, now)],
            "agent_insight_run_timestamps":  [_iso_ago(286.1, now), _iso_ago(250.0, now)],
            "active_position_count":         34,
            "persisted_recommendation_count": 34,
            "persisted_agent_insight_count":  34,
        },
        portfolio_snapshot_at=_iso_ago(192.0, now),
        market_value_certified_ats=[_iso_ago(192.0, now)] * 34,
        tickers=["AAPL", "NVDA", "TSLA"],
        research_artifact_timestamps=[],
        now=now,
    )


# ── classify_source_state ─────────────────────────────────────────────────────

class TestClassifySourceState:
    def test_fresh_when_inside_fresh_window(self):
        st = classify_source_state(
            source=SOURCE_RECOMMENDATIONS,
            timestamps=[_iso_ago(1.0)],
            expected_count=1,
            now=_now(),
        )
        assert st.state == STATE_FRESH
        assert st.is_critical is True

    def test_stale_when_inside_stale_window(self):
        # 191.8h is < 7d stale_hours=168h... wait, 191 > 168, so HARD_STALE.
        # Use 72h instead: > fresh_hours=24, < stale_hours=168 → STALE.
        st = classify_source_state(
            source=SOURCE_RECOMMENDATIONS,
            timestamps=[_iso_ago(72.0)],
            expected_count=1,
            now=_now(),
        )
        assert st.state == STATE_STALE

    def test_hard_stale_beyond_stale_window(self):
        st = classify_source_state(
            source=SOURCE_RECOMMENDATIONS,
            timestamps=[_iso_ago(200.0)],
            expected_count=1,
            now=_now(),
        )
        assert st.state == STATE_HARD_STALE

    def test_missing_when_no_timestamps_but_expected(self):
        st = classify_source_state(
            source=SOURCE_RECOMMENDATIONS,
            timestamps=[],
            expected_count=5,
            now=_now(),
        )
        assert st.state == STATE_MISSING
        assert st.missing_count == 5

    def test_unknown_when_no_timestamps_no_expected(self):
        st = classify_source_state(
            source=SOURCE_RESEARCH_ARTIFACTS,
            timestamps=[],
            expected_count=0,
            now=_now(),
        )
        assert st.state == STATE_UNKNOWN

    def test_worst_observed_bucket_aggregation(self):
        # One fresh + one hard-stale → aggregate HARD_STALE.
        st = classify_source_state(
            source=SOURCE_RECOMMENDATIONS,
            timestamps=[_iso_ago(1.0), _iso_ago(200.0)],
            expected_count=2,
            now=_now(),
        )
        assert st.state == STATE_HARD_STALE
        assert st.fresh_count == 1
        assert st.hard_stale_count == 1

    def test_oldest_and_newest_age_reported(self):
        st = classify_source_state(
            source=SOURCE_RECOMMENDATIONS,
            timestamps=[_iso_ago(1.0), _iso_ago(48.0)],
            expected_count=2,
            now=_now(),
        )
        assert st.oldest_age_hours is not None
        assert st.newest_age_hours is not None
        assert st.oldest_age_hours > st.newest_age_hours


# ── classify_run_mode ─────────────────────────────────────────────────────────

class TestClassifyRunMode:
    def test_all_fresh_yields_fast_certified(self):
        now = _now()
        states = build_source_states(_fresh_inputs(now))
        decision = classify_run_mode(states, refresh_attempted=False)
        assert decision.run_mode == RUN_MODE_FAST_CERTIFIED
        assert decision.trust_status == TRUST_TRUSTED
        assert decision.should_refresh is False

    def test_production_stale_is_not_fast_certified(self):
        """Production case: recs=191.8h, insights=286.1h → must NOT be FAST_CERTIFIED."""
        now = _now()
        states = build_source_states(_production_stale_inputs(now))
        decision = classify_run_mode(states, refresh_attempted=False)
        assert decision.run_mode != RUN_MODE_FAST_CERTIFIED
        # Critical analyst evidence is HARD_STALE (>168h) → BLOCKED pre-refresh.
        assert decision.run_mode == RUN_MODE_BLOCKED_UNCERTIFIED
        assert decision.trust_status == TRUST_UNCERTIFIED
        assert SOURCE_RECOMMENDATIONS in decision.blocked_sources
        assert SOURCE_AGENT_INSIGHTS in decision.blocked_sources

    def test_stale_refreshable_triggers_refresh_then_run(self):
        now = _now()
        inputs = _fresh_inputs(now)
        # Push price evidence into the STALE band (>15m, <4h).
        inputs.market_value_certified_ats = [_iso_ago(1.0, now)] * 2
        states = build_source_states(inputs)
        decision = classify_run_mode(states, refresh_attempted=False)
        assert decision.run_mode == RUN_MODE_REFRESH_THEN_RUN
        assert decision.should_refresh is True
        assert SOURCE_PRICE_LATEST in decision.refresh_targets

    def test_post_refresh_with_failures_downgrades_to_partial(self):
        now = _now()
        inputs = _fresh_inputs(now)
        # Price is now stale, but after a failed refresh classify with refresh_failed_count.
        inputs.market_value_certified_ats = [_iso_ago(1.0, now)] * 2
        states = build_source_states(inputs)
        decision = classify_run_mode(
            states, refresh_attempted=True,
            refresh_successful_count=0, refresh_failed_count=2,
        )
        assert decision.run_mode == RUN_MODE_PARTIAL_CERTIFIED
        assert decision.trust_status == TRUST_PARTIAL

    def test_critical_hard_stale_post_refresh_stays_blocked(self):
        now = _now()
        states = build_source_states(_production_stale_inputs(now))
        decision = classify_run_mode(
            states, refresh_attempted=True,
            refresh_successful_count=0, refresh_failed_count=0,
        )
        assert decision.run_mode == RUN_MODE_BLOCKED_UNCERTIFIED


# ── Orchestrator integration ──────────────────────────────────────────────────

class TestOrchestratorRun:
    def _run(self, orchestrator: EvidenceRefreshOrchestrator):
        return asyncio.get_event_loop().run_until_complete(orchestrator.run())

    @pytest.mark.asyncio
    async def test_all_fresh_does_not_call_provider(self):
        called = {"count": 0}
        async def _refresh(tickers):
            called["count"] += 1
            return {t: {"is_valid": True, "is_stale": False} for t in tickers}

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=_fresh_inputs(),
            price_refresh=_refresh,
        )
        result = await orch.run()
        assert result.run_mode == RUN_MODE_FAST_CERTIFIED
        assert called["count"] == 0
        assert result.attempted_provider_calls == 0
        assert result.attempted_llm_calls == 0

    @pytest.mark.asyncio
    async def test_stale_price_refresh_then_run_success(self):
        now = _now()
        inputs = _fresh_inputs(now)
        inputs.market_value_certified_ats = [_iso_ago(1.0, now)] * 2

        async def _refresh(tickers):
            return {t: {"is_valid": True, "is_stale": False} for t in tickers}

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=_refresh,
        )
        result = await orch.run()
        assert result.run_mode == RUN_MODE_REFRESH_THEN_RUN
        assert result.trust_status == TRUST_TRUSTED
        assert result.successful_provider_calls == 2
        assert result.failed_provider_calls == 0
        assert result.refreshed_source_count >= 1
        # Banner copy is plain-English.
        assert result.banner_copy == BANNER_COPY[RUN_MODE_REFRESH_THEN_RUN]
        assert "stale" in result.banner_copy.lower()

    @pytest.mark.asyncio
    async def test_failed_price_refresh_downgrades_to_partial(self):
        now = _now()
        inputs = _fresh_inputs(now)
        inputs.market_value_certified_ats = [_iso_ago(1.0, now)] * 2

        async def _refresh(tickers):
            # Provider returns invalid results for all tickers.
            return {t: {"is_valid": False, "is_stale": True} for t in tickers}

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=_refresh,
        )
        result = await orch.run()
        assert result.run_mode == RUN_MODE_PARTIAL_CERTIFIED
        assert result.trust_status == TRUST_PARTIAL
        assert result.failed_provider_calls == 2
        assert result.successful_provider_calls == 0

    @pytest.mark.asyncio
    async def test_production_stale_classifies_blocked_without_analyst_refresh(self):
        async def _refresh(tickers):
            return {t: {"is_valid": True, "is_stale": False} for t in tickers}

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=_production_stale_inputs(),
            price_refresh=_refresh,
            analyst_refresh=None,  # v1 boundary
        )
        result = await orch.run()
        # Even after a successful PRICE refresh, the critical analyst evidence
        # remains HARD_STALE → BLOCKED_UNCERTIFIED. Honest behavior.
        assert result.run_mode == RUN_MODE_BLOCKED_UNCERTIFIED
        assert result.trust_status == TRUST_UNCERTIFIED
        assert result.analyst_refresh_supported is False
        assert result.analyst_refresh_status == "not_supported_v1"
        assert SOURCE_RECOMMENDATIONS in result.blocked_sources
        assert SOURCE_AGENT_INSIGHTS in result.blocked_sources
        # Diagnostics surface stale evidence honestly.
        diag = result.to_diagnostics_dict()
        assert diag["hard_stale_source_count"] > 0
        assert diag["analyst_refresh_supported"] is False

    @pytest.mark.asyncio
    async def test_provider_budget_caps_calls(self):
        now = _now()
        inputs = _fresh_inputs(now)
        inputs.market_value_certified_ats = [_iso_ago(1.0, now)] * 100
        inputs.tickers = [f"T{i}" for i in range(100)]

        attempted_tickers: list[str] = []
        async def _refresh(tickers):
            attempted_tickers.extend(tickers)
            return {t: {"is_valid": True, "is_stale": False} for t in tickers}

        budget = RefreshBudget(max_provider_calls=3)
        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=_refresh,
            budget=budget,
        )
        result = await orch.run()
        assert result.attempted_provider_calls <= 3
        assert len(attempted_tickers) <= 3

    @pytest.mark.asyncio
    async def test_llm_budget_caps_analyst_calls(self):
        now = _now()
        inputs = _production_stale_inputs(now)

        async def _analyst(_tickers):
            return None

        # Even though analyst refresh is injected and stale critical sources
        # exist, LLM budget=0 prevents any call from being attempted.
        budget = RefreshBudget(max_llm_calls=0)
        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=None,
            analyst_refresh=_analyst,
            budget=budget,
        )
        result = await orch.run()
        assert result.attempted_llm_calls == 0
        assert result.analyst_refresh_status == "skipped_budget"

    @pytest.mark.asyncio
    async def test_analyst_refresh_not_supported_in_v1_by_default(self):
        now = _now()
        inputs = _production_stale_inputs(now)
        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(),
            inputs=inputs,
            price_refresh=None,
            analyst_refresh=None,
        )
        result = await orch.run()
        # No fabricated freshness — diagnostics report not_supported_v1.
        assert result.analyst_refresh_supported is False
        assert result.analyst_refresh_status == "not_supported_v1"
        assert result.attempted_llm_calls == 0
        assert "analyst_refresh_not_supported_v1" in result.notes

    @pytest.mark.asyncio
    async def test_repeated_identical_fresh_inputs_produce_same_decision(self):
        async def _refresh(tickers):
            return {t: {"is_valid": True, "is_stale": False} for t in tickers}

        inputs = _fresh_inputs()
        orch1 = EvidenceRefreshOrchestrator(
            user_id=uuid4(), inputs=inputs, price_refresh=_refresh,
        )
        orch2 = EvidenceRefreshOrchestrator(
            user_id=uuid4(), inputs=inputs, price_refresh=_refresh,
        )
        r1 = await orch1.run()
        r2 = await orch2.run()
        assert r1.run_mode == r2.run_mode
        assert r1.trust_status == r2.trust_status

    @pytest.mark.asyncio
    async def test_no_fabricated_freshness_after_failed_refresh(self):
        now = _now()
        inputs = _fresh_inputs(now)
        inputs.market_value_certified_ats = [_iso_ago(1.0, now)] * 2
        original_age = inputs.market_value_certified_ats[0]

        async def _refresh(tickers):
            return {t: {"is_valid": False, "is_stale": True} for t in tickers}

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(), inputs=inputs, price_refresh=_refresh,
        )
        result = await orch.run()
        # After-state still reflects the original (un-refreshed) age, not a
        # rewritten "now" timestamp.
        after_price = result.source_states_after[SOURCE_PRICE_LATEST]
        assert after_price.state == STATE_STALE
        # The original timestamp survived the failed refresh.
        assert original_age in (inputs.market_value_certified_ats or [])

    @pytest.mark.asyncio
    async def test_diagnostics_dict_shape(self):
        async def _refresh(tickers):
            return {t: {"is_valid": True, "is_stale": False} for t in tickers}

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(), inputs=_fresh_inputs(), price_refresh=_refresh,
        )
        result = await orch.run()
        d = result.to_diagnostics_dict()
        # Required fields from the snapshot truth contract.
        for key in (
            "run_mode", "trust_status", "banner_copy",
            "source_freshness", "stale_source_count", "missing_source_count",
            "attempted_provider_calls", "successful_provider_calls", "failed_provider_calls",
            "attempted_llm_calls", "successful_llm_calls", "failed_llm_calls",
            "refreshed_source_count", "failed_refresh_count",
            "analyst_refresh_supported", "analyst_refresh_status",
        ):
            assert key in d, f"missing diagnostics key: {key}"
        # source_freshness keyed by source class.
        assert SOURCE_RECOMMENDATIONS in d["source_freshness"]
        assert SOURCE_PRICE_LATEST in d["source_freshness"]


# ── Adversarial / boundary checks ─────────────────────────────────────────────

class TestPolicyAuthorityBoundary:
    def test_orchestrator_does_not_import_decide(self):
        """The orchestrator only refreshes inputs — it must not call decide()."""
        from app.services.intelligence.v3 import evidence_refresh_orchestrator_v1 as mod
        src = open(mod.__file__).read()
        assert "from .decision_policy_v1" not in src
        assert "import decide" not in src
        assert "decision_policy_v1" not in src

    def test_orchestrator_does_not_import_deploy_or_watchtower(self):
        from app.services.intelligence.v3 import evidence_refresh_orchestrator_v1 as mod
        src = open(mod.__file__).read()
        # Module mentions "deploy"/"watchtower" only in disclaimers, never imports.
        import re
        imports = re.findall(r"^\s*(?:from\s+\S+\s+)?import\s+\S+", src, re.MULTILINE)
        for line in imports:
            assert "deploy" not in line.lower(), f"Unexpected deploy import: {line}"
            assert "watchtower" not in line.lower(), f"Unexpected watchtower import: {line}"

    def test_freshness_contract_owns_run_mode_constants(self):
        """Centralized run-mode names; the orchestrator imports them, never re-defines."""
        from app.services.intelligence.v3 import evidence_freshness_contract_v1 as contract
        assert contract.RUN_MODE_FAST_CERTIFIED == "FAST_CERTIFIED"
        assert contract.RUN_MODE_REFRESH_THEN_RUN == "REFRESH_THEN_RUN"
        assert contract.RUN_MODE_PARTIAL_CERTIFIED == "PARTIAL_CERTIFIED"
        assert contract.RUN_MODE_BLOCKED_UNCERTIFIED == "BLOCKED_UNCERTIFIED"


# ── Diagnostics integration ───────────────────────────────────────────────────

class TestSnapshotDiagnosticsIntegration:
    def test_build_diagnostics_with_refresh_payload_overrides_legacy_zeros(self):
        from app.services.intelligence.v3.snapshot_freshness_diagnostics import build_diagnostics

        refresh_diag = {
            "run_mode": RUN_MODE_REFRESH_THEN_RUN,
            "trust_status": TRUST_TRUSTED,
            "banner_copy": BANNER_COPY[RUN_MODE_REFRESH_THEN_RUN],
            "source_freshness": {},
            "attempted_provider_calls": 7,
            "successful_provider_calls": 6,
            "failed_provider_calls": 1,
            "attempted_llm_calls": 0,
            "successful_llm_calls": 0,
            "failed_llm_calls": 0,
            "refreshed_source_count": 1,
            "failed_refresh_count": 0,
            "analyst_refresh_supported": False,
            "analyst_refresh_status": "not_supported_v1",
        }
        current = {"snapshot_id": "snap-A", "action_counts": {"HOLD": 1}, "current_holdings": [{"ticker": "AAPL", "action": "HOLD"}]}
        combined = build_diagnostics(
            evidence_stats={"recommendation_timestamps": [], "agent_insight_run_timestamps": []},
            current_snapshot=current,
            previous_snapshot=None,
            refresh_diagnostics=refresh_diag,
        )
        # legacy aliases reflect the real provider call count, not 0.
        assert combined["live_provider_calls"] == 6
        assert combined["attempted_llm_calls"] == 0
        assert combined["run_mode"] == RUN_MODE_REFRESH_THEN_RUN
        # Evidence mode renamed to certified mode when refresh ran.
        assert "refresh_then_run" in combined["evidence_mode"].lower()

    def test_build_diagnostics_back_compat_without_refresh(self):
        from app.services.intelligence.v3.snapshot_freshness_diagnostics import build_diagnostics
        current = {"snapshot_id": "snap-A", "action_counts": {"HOLD": 1}, "current_holdings": [{"ticker": "AAPL", "action": "HOLD"}]}
        combined = build_diagnostics(
            evidence_stats={"recommendation_timestamps": [], "agent_insight_run_timestamps": []},
            current_snapshot=current,
            previous_snapshot=None,
            refresh_diagnostics=None,
        )
        assert "run_mode" not in combined
        assert combined["evidence_mode"] == "deterministic_policy_over_persisted_evidence"
        assert combined["attempted_llm_calls"] == 0
        assert combined["live_provider_calls"] == 0


# ── Patch tests: partial price refresh + keyless callable + import smoke ─────

class TestPartialPriceRefreshHonesty:
    """Stage 3.0b v1 patch — no fake market freshness on partial success."""

    def _stale_price_inputs(self, now=None):
        now = now or _now()
        # Three tickers, all with stale price evidence (1h old, > 15m fresh window).
        return OrchestratorInputs(
            evidence_stats={
                "recommendation_timestamps":     [_iso_ago(1.0, now)] * 3,
                "agent_insight_run_timestamps":  [_iso_ago(1.5, now)] * 3,
                "active_position_count":         3,
                "persisted_recommendation_count": 3,
                "persisted_agent_insight_count":  3,
            },
            portfolio_snapshot_at=_iso_ago(1.0, now),
            market_value_certified_ats=[
                _iso_ago(1.0, now),  # AAPL — will refresh successfully
                _iso_ago(1.0, now),  # NVDA — will refresh successfully
                _iso_ago(1.0, now),  # XYZ  — will fail to refresh
            ],
            tickers=["AAPL", "NVDA", "XYZ"],
            research_artifact_timestamps=[],
            now=now,
        )

    @pytest.mark.asyncio
    async def test_partial_success_only_refreshes_successful_tickers(self):
        """Failed ticker keeps original stale timestamp; successful ones bump to now."""
        now = _now()
        inputs = self._stale_price_inputs(now)
        original_xyz_ts = inputs.market_value_certified_ats[2]
        original_aapl_ts = inputs.market_value_certified_ats[0]

        async def _refresh(tickers):
            # AAPL + NVDA refresh; XYZ comes back invalid.
            return {
                "AAPL": {"is_valid": True,  "is_stale": False, "source": "alpaca"},
                "NVDA": {"is_valid": True,  "is_stale": False, "source": "alpaca"},
                "XYZ":  {"is_valid": False, "is_stale": False, "source": "none", "error": "no source"},
            }

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(), inputs=inputs, price_refresh=_refresh,
        )
        result = await orch.run()

        # The successful tickers' positions were stamped to now; XYZ kept its
        # original stale timestamp. We assert via the post-refresh classifier
        # by reconstructing post-refresh inputs the same way the orchestrator
        # did, then inspecting the per-source state.
        post = orch._post_refresh_inputs(
            refreshed_sources={"price_latest", "price_history"},
            successful_tickers=["AAPL", "NVDA"],
        )
        # AAPL + NVDA → now; XYZ → original.
        assert post.market_value_certified_ats[0] == now.isoformat()
        assert post.market_value_certified_ats[1] == now.isoformat()
        assert post.market_value_certified_ats[2] == original_xyz_ts
        # And XYZ's original timestamp is genuinely stale (1h vs 15m SLA).
        assert original_xyz_ts == original_aapl_ts  # both started stale

    @pytest.mark.asyncio
    async def test_partial_success_increments_failed_provider_calls(self):
        async def _refresh(tickers):
            return {
                "AAPL": {"is_valid": True,  "is_stale": False, "source": "alpaca"},
                "NVDA": {"is_valid": True,  "is_stale": False, "source": "alpaca"},
                "XYZ":  {"is_valid": False, "is_stale": False, "source": "none", "error": "rate limit"},
            }
        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(), inputs=self._stale_price_inputs(), price_refresh=_refresh,
        )
        result = await orch.run()

        assert result.attempted_provider_calls == 3
        assert result.successful_provider_calls == 2
        assert result.failed_provider_calls == 1
        assert result.failed_refresh_count >= 1
        # `price_refresh_partial_failure_…` lands in notes.
        assert any("price_refresh_partial_failure" in n for n in result.notes)

    @pytest.mark.asyncio
    async def test_partial_success_does_not_become_trusted(self):
        """Mixed outcomes must not show trust=trusted nor mode=FAST_CERTIFIED."""
        async def _refresh(tickers):
            return {
                "AAPL": {"is_valid": True,  "is_stale": False, "source": "alpaca"},
                "NVDA": {"is_valid": True,  "is_stale": False, "source": "alpaca"},
                "XYZ":  {"is_valid": False, "is_stale": False, "source": "none"},
            }
        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(), inputs=self._stale_price_inputs(), price_refresh=_refresh,
        )
        result = await orch.run()
        assert result.run_mode != RUN_MODE_FAST_CERTIFIED
        # Refresh produced some successes and some failures → PARTIAL_CERTIFIED.
        assert result.run_mode == RUN_MODE_PARTIAL_CERTIFIED
        assert result.trust_status != TRUST_TRUSTED

    @pytest.mark.asyncio
    async def test_partial_success_diagnostics_surface_mixed_state(self):
        async def _refresh(tickers):
            return {
                "AAPL": {"is_valid": True,  "is_stale": False, "source": "alpaca"},
                "NVDA": {"is_valid": True,  "is_stale": False, "source": "alpaca"},
                "XYZ":  {"is_valid": False, "is_stale": False, "source": "none"},
            }
        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(), inputs=self._stale_price_inputs(), price_refresh=_refresh,
        )
        result = await orch.run()
        d = result.to_diagnostics_dict()
        # Diagnostics expose the partial failure honestly.
        assert d["failed_refresh_count"] >= 1
        assert d["successful_provider_calls"] == 2
        assert d["failed_provider_calls"] == 1
        # price_latest source is no longer fully fresh — at least one ticker stale.
        price_state = d["source_freshness"]["price_latest"]
        assert price_state["stale_count"] >= 1
        # Banner does not promise certified.
        assert "Fresh certified" not in d["banner_copy"]

    @pytest.mark.asyncio
    async def test_full_failure_keeps_all_original_timestamps(self):
        original = self._stale_price_inputs()
        original_ats = list(original.market_value_certified_ats)

        async def _refresh(tickers):
            return {t: {"is_valid": False, "is_stale": True, "source": "cache"} for t in tickers}

        orch = EvidenceRefreshOrchestrator(
            user_id=uuid4(), inputs=original, price_refresh=_refresh,
        )
        result = await orch.run()
        # No tickers succeeded — post-refresh inputs preserve all originals.
        post = orch._post_refresh_inputs(refreshed_sources=set(), successful_tickers=[])
        assert post.market_value_certified_ats == original_ats
        assert post.portfolio_snapshot_at == original.portfolio_snapshot_at
        # Counts are honest.
        assert result.failed_provider_calls == 3
        assert result.successful_provider_calls == 0


class TestKeylessPriceRefreshCallable:
    """The price refresh callable must work without paid-tier API keys."""

    def test_callable_returned_without_paid_keys(self, monkeypatch):
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service
        # Clear any paid-tier keys from the environment.
        for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FINNHUB_API_KEY", "POLYGON_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        svc = IntelV3Service(user_id=uuid4())
        callable_ = svc._build_price_refresh_callable()
        # Keyless yfinance + CoinGecko are still available — callable must exist.
        assert callable_ is not None
        assert callable(callable_)

    def test_callable_returned_with_partial_keys(self, monkeypatch):
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service
        monkeypatch.setenv("ALPACA_API_KEY", "x")
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        svc = IntelV3Service(user_id=uuid4())
        assert svc._build_price_refresh_callable() is not None


class TestPriceEngineImportPath:
    """Smoke test: _build_price_refresh_callable resolves the real module."""

    def test_real_price_engine_imports_via_orchestrator_helper(self):
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service
        svc = IntelV3Service(user_id=uuid4())
        # The callable returned must be backed by the canonical PriceService
        # at app.services.price_engine. If the import path were wrong this
        # would return None.
        cb = svc._build_price_refresh_callable()
        assert cb is not None
        # And the canonical module path itself imports cleanly.
        from app.services.price_engine import PriceService as _PriceEngine
        assert _PriceEngine is not None
