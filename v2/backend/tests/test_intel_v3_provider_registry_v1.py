"""Tests for the Provider Registry (Stage 3.0b v1 — §3 of north-star)."""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.provider_registry_v1 import (
    FAILURE_CIRCUIT_BREAK,
    FAILURE_DEGRADE,
    SC_ANALYST_THESIS,
    SC_CRYPTO,
    SC_FILINGS,
    SC_MARKET_PRICE,
    SC_PORTFOLIO_STATE,
    SOURCE_CLASSES,
    enabled_providers,
    get_provider,
    health_summary,
    list_providers,
    providers_for_source_class,
)


class TestRegistrySeed:
    def test_known_providers_seeded(self):
        ids = {p.provider_id for p in list_providers()}
        for expected in (
            "yfinance", "alpaca", "finnhub", "polygon", "coingecko",
            "sec_edgar", "sec_companyfacts", "agent_orchestrator",
            "claude_analyst", "research_workers", "portfolio_service",
        ):
            assert expected in ids

    def test_get_provider_returns_record_or_none(self):
        assert get_provider("alpaca") is not None
        assert get_provider("nope") is None

    def test_source_classes_are_known(self):
        for p in list_providers():
            for sc in p.source_classes:
                assert sc in SOURCE_CLASSES, f"{p.provider_id} declared unknown source_class {sc}"


class TestProvidersForSourceClass:
    def test_market_price_has_multiple_providers_ordered_by_priority(self):
        provs = providers_for_source_class(SC_MARKET_PRICE)
        ids = [p.provider_id for p in provs]
        assert "alpaca" in ids and "yfinance" in ids and "finnhub" in ids and "polygon" in ids
        # alpaca should come before yfinance (priority 10 vs 20).
        assert ids.index("alpaca") < ids.index("yfinance")

    def test_crypto_has_coingecko(self):
        provs = providers_for_source_class(SC_CRYPTO)
        assert any(p.provider_id == "coingecko" for p in provs)

    def test_filings_has_sec_providers(self):
        provs = providers_for_source_class(SC_FILINGS)
        ids = {p.provider_id for p in provs}
        assert "sec_edgar" in ids
        assert "research_workers" in ids

    def test_portfolio_state_is_internal_only(self):
        provs = providers_for_source_class(SC_PORTFOLIO_STATE)
        ids = {p.provider_id for p in provs}
        assert ids == {"portfolio_service"}


class TestEnvGating:
    def test_keyless_provider_always_enabled(self):
        yf = get_provider("yfinance")
        assert yf is not None
        assert yf.is_enabled(env={}) is True

    def test_env_gated_provider_disabled_without_key(self):
        alpaca = get_provider("alpaca")
        assert alpaca is not None
        assert alpaca.is_enabled(env={}) is False
        assert alpaca.is_enabled(env={"ALPACA_API_KEY": "xyz"}) is True

    def test_empty_string_env_does_not_enable(self):
        alpaca = get_provider("alpaca")
        assert alpaca.is_enabled(env={"ALPACA_API_KEY": "   "}) is False

    def test_enabled_providers_filters_correctly(self):
        env = {"ALPACA_API_KEY": "x"}
        ids = {p.provider_id for p in enabled_providers(env=env)}
        # keyless providers always enabled; alpaca enabled; finnhub/polygon/anthropic not.
        assert "yfinance" in ids
        assert "alpaca" in ids
        assert "finnhub" not in ids
        assert "agent_orchestrator" not in ids


class TestHealthSummary:
    def test_health_summary_shape(self):
        env = {"ALPACA_API_KEY": "x", "ANTHROPIC_API_KEY": "y"}
        h = health_summary(env=env)
        assert "by_source_class" in h
        assert "disabled_providers" in h
        assert h["total_providers"] > 0
        # market_price has fallback_provider when at least one is enabled.
        assert h["by_source_class"][SC_MARKET_PRICE]["fallback_provider"] is not None
        # analyst_thesis has fallback when ANTHROPIC_API_KEY is set.
        assert h["by_source_class"][SC_ANALYST_THESIS]["fallback_provider"] is not None

    def test_health_summary_no_env_keys_still_shows_keyless_providers(self):
        h = health_summary(env={})
        assert h["by_source_class"][SC_CRYPTO]["fallback_provider"] == "coingecko"
        # market_price falls back to yfinance (keyless) when alpaca/polygon/finnhub disabled.
        assert h["by_source_class"][SC_MARKET_PRICE]["fallback_provider"] == "yfinance"

    def test_analyst_thesis_blocked_without_anthropic_key(self):
        h = health_summary(env={})
        assert h["by_source_class"][SC_ANALYST_THESIS]["fallback_provider"] is None
        # The disabled list names the env var that would enable it.
        disabled_ids = {p["provider_id"]: p for p in h["disabled_providers"]}
        assert "agent_orchestrator" in disabled_ids
        assert disabled_ids["agent_orchestrator"]["env_var_name"] == "ANTHROPIC_API_KEY"


# ── Orchestrator integration ──────────────────────────────────────────────────

class TestOrchestratorEmitsRegistryHealth:
    @pytest.mark.asyncio
    async def test_diagnostics_include_provider_registry_health(self):
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4
        from app.services.intelligence.v3.evidence_refresh_orchestrator_v1 import (
            EvidenceRefreshOrchestrator, OrchestratorInputs,
        )

        now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
        inputs = OrchestratorInputs(
            evidence_stats={
                "recommendation_timestamps": [(now - timedelta(hours=1)).isoformat()],
                "agent_insight_run_timestamps": [(now - timedelta(hours=1)).isoformat()],
                "active_position_count": 1,
                "persisted_recommendation_count": 1,
                "persisted_agent_insight_count": 1,
            },
            portfolio_snapshot_at=(now - timedelta(hours=1)).isoformat(),
            market_value_certified_ats=[(now - timedelta(minutes=5)).isoformat()],
            tickers=["AAPL"],
            research_artifact_timestamps=[],
            now=now,
        )
        orch = EvidenceRefreshOrchestrator(user_id=uuid4(), inputs=inputs)
        result = await orch.run()
        d = result.to_diagnostics_dict()
        assert "provider_registry_health" in d
        h = d["provider_registry_health"]
        assert "by_source_class" in h
        assert "disabled_providers" in h
