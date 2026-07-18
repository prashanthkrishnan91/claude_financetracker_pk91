"""Policy ticker config — tickers live in config, not policy source."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.policy_tickers import (
    benchmark_symbol,
    ticker_map,
    ticker_set,
    ticker_tuple,
)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "app" / "policy_tickers.json"


class TestLoader:
    def test_config_file_exists_and_parses(self):
        data = json.loads(_CONFIG_PATH.read_text())
        assert "benchmark_symbol" in data

    def test_ticker_set_uppercases(self):
        assert ticker_set("speculative_tickers") == frozenset(
            {"BTC", "XRP", "RIVN", "KLAR", "BLSH"}
        )

    def test_ticker_tuple_preserves_order(self):
        assert ticker_tuple("stage9k_diagnostic_default_tickers") == ("VTI", "SCHD", "VXUS")

    def test_ticker_map_keys_uppercased(self):
        m = ticker_map("ticker_sector_fallback")
        assert m["AAPL"] == "Technology"
        assert m["BTC"] == "Crypto"

    def test_benchmark_symbol(self):
        assert benchmark_symbol() == "SPY"

    def test_missing_key_fails_loud(self):
        import pytest

        with pytest.raises(KeyError):
            ticker_set("nonexistent_policy_set")


class TestPolicyModulesUseConfig:
    """The policy constants must be sourced from config (behavior-identical)."""

    def test_kernel_crypto_set_matches_config(self):
        from app.services.intelligence.v3.decision_policy_v1 import _KERNEL_CRYPTO_TICKERS

        assert _KERNEL_CRYPTO_TICKERS == ticker_set("kernel_crypto_tickers")

    def test_governor_speculative_set_matches_config(self):
        from app.services.intelligence.v3.portfolio_governor_lite import _SPECULATIVE_TICKERS

        assert _SPECULATIVE_TICKERS == ticker_set("speculative_tickers")

    def test_etf_classifier_map_matches_config(self):
        from app.services.intelligence.v3.etf_intelligence_classifier_v1 import _KNOWN_ETF_MAP

        cfg = ticker_map("etf_classifier_map")
        assert len(_KNOWN_ETF_MAP) == len(cfg) == 55
        assert _KNOWN_ETF_MAP["VOO"] == ("equity_etf", "core_us_equity")

    def test_orchestrator_sets_match_config(self):
        from app.services.agents.orchestrator import AgentOrchestrator

        assert AgentOrchestrator._ETF_TICKERS == ticker_set("orchestrator_etf_tickers")
        assert AgentOrchestrator._CRYPTO_TICKERS == ticker_set("orchestrator_crypto_tickers")
