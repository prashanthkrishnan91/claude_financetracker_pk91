"""Policy ticker configuration — validation and exact parity with historic values.

The consolidation PR moved decision-influencing ticker membership out of
policy source code into ``app/policy_tickers.json``. These tests prove:

1. The packaged default config resolves inside the deployed package structure.
2. Loaded values are EXACTLY the values the code previously hardcoded
   (membership and, for the preference order, ordering) — no behavior change.
3. The loader fails loudly (never a silent empty fallback) on missing file,
   missing keys, malformed shapes, unknown ETF type/role vocabulary,
   duplicate tickers, and ambiguous cross-set membership.
4. Case normalization and the optional override path work.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import policy_tickers
from app.services.policy_tickers import PolicyTickerConfigError


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    policy_tickers.reset_cache()
    yield
    monkeypatch.delenv("POLICY_TICKERS_FILE", raising=False)
    policy_tickers.reset_cache()


def _write_config(tmp_path: Path, mutate=None) -> Path:
    with open(policy_tickers.default_config_path(), "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if mutate:
        mutate(raw)
    p = tmp_path / "policy_tickers.json"
    p.write_text(json.dumps(raw))
    return p


class TestDefaultPathResolution:
    def test_default_config_ships_inside_app_package(self):
        path = policy_tickers.default_config_path()
        assert path.name == "policy_tickers.json"
        assert path.parent.name == "app", "config must live in the deployed app package"
        assert path.is_file(), "packaged default config must exist at the resolved path"

    def test_default_load_succeeds_without_env_override(self, monkeypatch):
        monkeypatch.delenv("POLICY_TICKERS_FILE", raising=False)
        assert policy_tickers.broad_index_core_preference_order()


class TestExactParityWithHistoricHardcodedValues:
    """Every assertion below is the literal set/order the code shipped with."""

    def test_broad_index_core_preference_order_exact(self):
        assert policy_tickers.broad_index_core_preference_order() == ("VTI", "VOO", "SPY", "QQQ")

    def test_etf_groups_exact(self):
        assert policy_tickers.etf_group_tickers("broad_index_etf") == frozenset({"VOO", "VTI", "SPY", "QQQ"})
        assert policy_tickers.etf_group_tickers("dividend_etf") == frozenset({"VYM", "SCHD"})
        assert policy_tickers.etf_group_tickers("international_etf") == frozenset({"VXUS"})
        assert policy_tickers.etf_group_tickers("sector_etf") == frozenset({"VGT", "VHT", "VIS", "XLE"})

    def test_alternatives_crypto_speculative_exact(self):
        assert policy_tickers.alternatives_tickers() == frozenset({"GLD"})
        assert policy_tickers.crypto_tickers() == frozenset(
            {"BTC", "XRP", "ETH", "SOL", "DOGE", "ADA", "AVAX", "MATIC", "DOT", "LTC"}
        )
        assert policy_tickers.speculative_tickers() == frozenset({"STUB", "KLAR", "BLSH", "RDDT"})

    def test_kernel_crypto_tickers_exact(self):
        assert policy_tickers.kernel_crypto_tickers() == frozenset(
            {"BTC", "ETH", "XRP", "SOL", "BNB", "ADA", "DOGE"}
        )

    def test_etf_classifier_map_exact_sample_and_size(self):
        cmap = policy_tickers.etf_classifier_map()
        assert len(cmap) == 55
        assert cmap["VOO"] == ("equity_etf", "core_us_equity")
        assert cmap["QQQ"] == ("equity_etf", "growth_tilt")
        assert cmap["VGT"] == ("sector_etf", "growth_tilt")
        assert cmap["SCHD"] == ("dividend_etf", "dividend_income")
        assert cmap["XLE"] == ("sector_etf", "sector_tilt")
        assert cmap["VXUS"] == ("international_etf", "international_diversifier")
        assert cmap["BND"] == ("bond_etf", "bond_stability")
        assert cmap["SHY"] == ("bond_etf", "cash_like")
        assert cmap["GLD"] == ("commodity_trust", "commodity_hedge")
        assert cmap["IBIT"] == ("crypto_etf", "crypto_speculative")


class TestConsumersReadFromConfig:
    def test_allocation_policy_module_constants_match_config(self):
        from app.services import allocation_policy_v1 as ap

        assert ap.BROAD_INDEX_CORE_PREFERENCE_ORDER == ["VTI", "VOO", "SPY", "QQQ"]
        assert ap._CORE_ETF_PREFERENCE_RANK == {"VTI": 4, "VOO": 3, "SPY": 2, "QQQ": 1}
        assert ap._BROAD_ETF_TICKERS == frozenset({"VOO", "VTI", "SPY", "QQQ"})
        assert ap._DIVIDEND_ETF_TICKERS == frozenset({"VYM", "SCHD"})
        assert ap._INTERNATIONAL_ETF_TICKERS == frozenset({"VXUS"})
        assert ap._SECTOR_ETF_TICKERS == frozenset({"VGT", "VHT", "VIS", "XLE"})
        assert ap._ALTERNATIVES_TICKERS == frozenset({"GLD"})
        assert ap._CRYPTO_TICKERS == frozenset(
            {"BTC", "XRP", "ETH", "SOL", "DOGE", "ADA", "AVAX", "MATIC", "DOT", "LTC"}
        )
        assert ap._SPECULATIVE_TICKERS == frozenset({"STUB", "KLAR", "BLSH", "RDDT"})

    def test_allocation_policy_classification_behavior_unchanged(self):
        from app.services.allocation_policy_v1 import classify_ticker

        assert classify_ticker("VTI") == ("broad_index_etf", False)
        assert classify_ticker("SCHD") == ("dividend_etf", False)
        assert classify_ticker("VXUS") == ("international_etf", False)
        assert classify_ticker("XLE") == ("sector_etf", False)
        assert classify_ticker("GLD") == ("alternatives", False)
        assert classify_ticker("BTC") == ("crypto", False)
        assert classify_ticker("KLAR") == ("speculative", False)
        group, unknown = classify_ticker("AAPL")
        assert group == "individual_stock"

    def test_decision_policy_kernel_crypto_constant_matches_config(self):
        from app.services.intelligence.v3 import decision_policy_v1 as dp

        assert dp._KERNEL_CRYPTO_TICKERS == frozenset(
            {"BTC", "ETH", "XRP", "SOL", "BNB", "ADA", "DOGE"}
        )

    def test_etf_classifier_known_map_matches_config_and_vocabulary(self):
        from app.services.intelligence.v3 import etf_intelligence_classifier_v1 as ec

        assert ec._KNOWN_ETF_MAP == policy_tickers.etf_classifier_map()
        # Config vocabulary must stay in lock-step with the module constants.
        module_types = {v for k, v in vars(ec).items() if k.startswith("ETF_TYPE_")}
        module_roles = {v for k, v in vars(ec).items() if k.startswith("ETF_ROLE_")}
        assert policy_tickers.ALLOWED_ETF_TYPES == frozenset(module_types)
        assert policy_tickers.ALLOWED_ETF_ROLES == frozenset(module_roles)
        for etf_type, etf_role in ec._KNOWN_ETF_MAP.values():
            assert etf_type in module_types
            assert etf_role in module_roles


class TestFailLoudValidation:
    def test_missing_file_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(tmp_path / "nope.json"))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="not found"):
            policy_tickers.crypto_tickers()

    def test_invalid_json_raises(self, monkeypatch, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="not valid JSON"):
            policy_tickers.crypto_tickers()

    def test_missing_required_key_raises(self, monkeypatch, tmp_path):
        p = _write_config(tmp_path, lambda raw: raw.pop("crypto_tickers"))
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="crypto_tickers"):
            policy_tickers.crypto_tickers()

    def test_empty_list_raises_not_silent_empty_set(self, monkeypatch, tmp_path):
        p = _write_config(tmp_path, lambda raw: raw.__setitem__("speculative_tickers", []))
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="non-empty"):
            policy_tickers.speculative_tickers()

    def test_duplicate_ticker_within_list_raises(self, monkeypatch, tmp_path):
        p = _write_config(
            tmp_path, lambda raw: raw.__setitem__("crypto_tickers", ["BTC", "btc"])
        )
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="duplicate ticker 'BTC'"):
            policy_tickers.crypto_tickers()

    def test_cross_set_ambiguous_membership_raises(self, monkeypatch, tmp_path):
        def mutate(raw):
            raw["speculative_tickers"] = raw["speculative_tickers"] + ["GLD"]

        p = _write_config(tmp_path, mutate)
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="unambiguous"):
            policy_tickers.speculative_tickers()

    def test_unknown_etf_group_raises(self, monkeypatch, tmp_path):
        def mutate(raw):
            raw["etf_groups"]["bond_etf_group"] = ["BND"]

        p = _write_config(tmp_path, mutate)
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="etf_groups"):
            policy_tickers.etf_group_tickers("broad_index_etf")

    def test_preference_order_ticker_outside_broad_group_raises(self, monkeypatch, tmp_path):
        def mutate(raw):
            raw["broad_index_core_preference_order"] = ["VTI", "SCHD"]

        p = _write_config(tmp_path, mutate)
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="broad_index_etf"):
            policy_tickers.broad_index_core_preference_order()

    def test_unknown_etf_type_raises(self, monkeypatch, tmp_path):
        def mutate(raw):
            raw["etf_classifier_map"]["VOO"] = ["mystery_fund", "core_us_equity"]

        p = _write_config(tmp_path, mutate)
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="unknown etf_type"):
            policy_tickers.etf_classifier_map()

    def test_unknown_etf_role_raises(self, monkeypatch, tmp_path):
        def mutate(raw):
            raw["etf_classifier_map"]["VOO"] = ["equity_etf", "moonshot"]

        p = _write_config(tmp_path, mutate)
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="unknown etf_role"):
            policy_tickers.etf_classifier_map()

    def test_malformed_classifier_pair_raises(self, monkeypatch, tmp_path):
        def mutate(raw):
            raw["etf_classifier_map"]["VOO"] = ["equity_etf"]

        p = _write_config(tmp_path, mutate)
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        with pytest.raises(PolicyTickerConfigError, match="pair"):
            policy_tickers.etf_classifier_map()


class TestNormalizationAndOverride:
    def test_case_normalization(self, monkeypatch, tmp_path):
        p = _write_config(
            tmp_path, lambda raw: raw.__setitem__("alternatives_tickers", ["gld"])
        )
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        assert policy_tickers.alternatives_tickers() == frozenset({"GLD"})

    def test_override_path_used_when_set(self, monkeypatch, tmp_path):
        def mutate(raw):
            raw["speculative_tickers"] = ["TESTONLY"]

        p = _write_config(tmp_path, mutate)
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        assert policy_tickers.speculative_tickers() == frozenset({"TESTONLY"})

    def test_ordering_preserved_from_file(self, monkeypatch, tmp_path):
        def mutate(raw):
            raw["broad_index_core_preference_order"] = ["QQQ", "SPY", "VOO", "VTI"]

        p = _write_config(tmp_path, mutate)
        monkeypatch.setenv("POLICY_TICKERS_FILE", str(p))
        policy_tickers.reset_cache()
        assert policy_tickers.broad_index_core_preference_order() == ("QQQ", "SPY", "VOO", "VTI")
