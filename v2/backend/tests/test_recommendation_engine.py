"""Tests for the recommendation engine — generate_rec decision tree, helpers, RecResult."""

from __future__ import annotations

import pytest

from app.services.recommendation_engine import (
    RecResult,
    generate_rec,
    _classify_action,
    _tax_note,
    _drip_note,
    INCOME_FOREVER,
    DCA_ALWAYS,
    DRIP_YIELD,
    ACTION_COLORS,
    _normalize_ticker_lookup_key,
    _resolve_thesis_scorecard_for_ticker,
    _build_thesis_fields_for_card,
)


# ── _classify_action tests ──────────────────────────────────────────────────


class TestClassifyAction:
    def test_sell_keyword(self):
        assert _classify_action("SELL NOW") == "SELL"

    def test_sell_emoji(self):
        assert _classify_action("🔴 Exit") == "SELL"

    def test_buy_keyword(self):
        assert _classify_action("BUY THE DIP") == "BUY"

    def test_accumulate_keyword(self):
        assert _classify_action("ACCUMULATE") == "BUY"

    def test_dca_keyword(self):
        assert _classify_action("DCA ALWAYS") == "BUY"

    def test_buy_emoji_green(self):
        assert _classify_action("🟢 Add") == "BUY"

    def test_buy_emoji_fire(self):
        assert _classify_action("🔥 Strong") == "BUY"

    def test_buy_emoji_chart(self):
        assert _classify_action("📈 Rising") == "BUY"

    def test_trim_keyword(self):
        assert _classify_action("TRIM 20%") == "TRIM"

    def test_trim_emoji(self):
        assert _classify_action("✂ Cut") == "TRIM"

    def test_review_keyword(self):
        assert _classify_action("REVIEW position") == "REVIEW"

    def test_review_emoji(self):
        assert _classify_action("🚨 Alert") == "REVIEW"

    def test_hold_default(self):
        assert _classify_action("HOLD") == "HOLD"

    def test_unknown_defaults_hold(self):
        assert _classify_action("something random") == "HOLD"

    def test_case_insensitive(self):
        assert _classify_action("sell now") == "SELL"
        assert _classify_action("Buy dip") == "BUY"


# ── _tax_note tests ─────────────────────────────────────────────────────────


class TestTaxNote:
    def test_lt_ready_high_gain(self):
        note = _tax_note(lt_ready=True, lt_date="2025-06-01", pct_gain=30)
        assert "LT eligible" in note
        assert "save" in note.lower()

    def test_lt_ready_low_gain(self):
        note = _tax_note(lt_ready=True, lt_date="2025-06-01", pct_gain=10)
        assert "LT eligible" in note
        assert "15-20%" in note

    def test_st_status(self):
        note = _tax_note(lt_ready=False, lt_date="2025-12-15", pct_gain=5)
        assert "ST status" in note
        assert "2025-12-15" in note
        assert "37%" in note


# ── _drip_note tests ────────────────────────────────────────────────────────


class TestDripNote:
    def test_no_drip_shares(self):
        assert _drip_note("VYM", 0, 0, 100.0) == ""

    def test_no_price(self):
        assert _drip_note("VYM", 5.0, 400.0, None) == ""

    def test_drip_with_yield(self):
        note = _drip_note("VYM", 2.0, 180.0, 110.0)
        assert "DRIP" in note
        assert "2.0000 free shares" in note
        assert "$220.00" in note  # 2 * 110
        assert "2.8%" in note  # VYM yield

    def test_drip_without_yield(self):
        note = _drip_note("AMD", 1.0, 100.0, 150.0)
        assert "DRIP" in note
        assert "1.0000 free shares" in note
        assert "annual yield" not in note  # AMD yield is 0


# ── RecResult dataclass tests ───────────────────────────────────────────────


class TestRecResult:
    def test_construction(self):
        r = RecResult(
            action_label="BUY",
            action="BUY",
            detail="Test detail",
            color="green",
            urgency=2,
        )
        assert r.action_label == "BUY"
        assert r.action == "BUY"
        assert r.tax_note == ""
        assert r.drip_note == ""

    def test_with_notes(self):
        r = RecResult(
            action_label="TRIM",
            action="TRIM",
            detail="Detail",
            color="orange",
            urgency=1,
            tax_note="LT eligible",
            drip_note="DRIP: 3 shares",
        )
        assert r.tax_note == "LT eligible"
        assert r.drip_note == "DRIP: 3 shares"


# ── Constants tests ─────────────────────────────────────────────────────────


class TestConstants:
    def test_income_forever_tickers(self):
        assert "VYM" in INCOME_FOREVER
        assert "SCHD" in INCOME_FOREVER

    def test_dca_always_tickers(self):
        assert "VOO" in DCA_ALWAYS
        assert "QQQ" in DCA_ALWAYS
        assert "VTI" in DCA_ALWAYS

    def test_drip_yield_coverage(self):
        # All 41 tickers should have yield entries
        assert len(DRIP_YIELD) >= 40
        assert DRIP_YIELD["VYM"] > 0
        assert DRIP_YIELD["BTC"] == 0.0

    def test_action_colors(self):
        assert ACTION_COLORS["SELL"] == "red"
        assert ACTION_COLORS["BUY"] == "green"
        assert ACTION_COLORS["TRIM"] == "orange"
        assert ACTION_COLORS["HOLD"] == "blue"
        assert ACTION_COLORS["REVIEW"] == "purple"


class TestThesisTickerLookupNormalization:
    def test_normalize_ticker_lookup_key_strips_case_and_separators(self):
        assert _normalize_ticker_lookup_key("brk-b") == "BRKB"
        assert _normalize_ticker_lookup_key("BRK.B") == "BRKB"
        assert _normalize_ticker_lookup_key(" brk b ") == "BRKB"

    def test_resolve_scorecard_direct_key_match(self):
        scorecard = {"status": "PARTIAL"}
        thesis_map = {"AAPL": scorecard}
        assert _resolve_thesis_scorecard_for_ticker(thesis_map, "AAPL") == scorecard

    def test_resolve_scorecard_normalized_key_match(self):
        scorecard = {"status": "PARTIAL"}
        thesis_map = {"brk.b": scorecard}
        assert _resolve_thesis_scorecard_for_ticker(thesis_map, "BRK-B") == scorecard

    def test_resolve_scorecard_missing_or_malformed_map_safely_returns_none(self):
        assert _resolve_thesis_scorecard_for_ticker({}, "AAPL") is None
        assert _resolve_thesis_scorecard_for_ticker({"AAPL": None}, "AAPL") is None
        assert _resolve_thesis_scorecard_for_ticker(None, "AAPL") is None


class TestThesisPlainEnglishCardWiring:
    def test_emits_plain_english_when_exact_ticker_exists(self):
        run_lookup = {
            "run-1": {"allocation": {"_thesis_v2": {"AAPL": {"status": "PARTIAL"}}}}
        }
        thesis_v2, plain, diag = _build_thesis_fields_for_card(
            ticker="AAPL", run_id="run-1", run_lookup=run_lookup
        )
        assert diag == "attached"
        assert thesis_v2 == {"status": "PARTIAL"}
        assert isinstance(plain, dict)
        assert plain.get("headline")

    def test_emits_plain_english_when_key_differs_by_safe_normalization(self):
        run_lookup = {
            "run-1": {"allocation": {"_thesis_v2": {" brk.b ": {"status": "PARTIAL"}}}}
        }
        thesis_v2, plain, diag = _build_thesis_fields_for_card(
            ticker="BRK-B", run_id="run-1", run_lookup=run_lookup
        )
        assert diag == "attached"
        assert thesis_v2 == {"status": "PARTIAL"}
        assert isinstance(plain, dict)

    def test_missing_thesis_map_leaves_fields_none(self):
        run_lookup = {"run-1": {"allocation": {}}}
        thesis_v2, plain, diag = _build_thesis_fields_for_card(
            ticker="AAPL", run_id="run-1", run_lookup=run_lookup
        )
        assert diag == "thesis_map_missing"
        assert thesis_v2 is None
        assert plain is None


class TestLiveStyleSerializedThesisContract:
    def test_live_style_serialized_thesis_v2_keeps_directional_labels_per_ticker(self):
        run_lookup = {
            "run-live": {
                "allocation": {
                    "_thesis_v2": {
                        "GOOGL": {
                            "status": "INSUFFICIENT_DATA",
                            "quality": {"score": 74.0, "published": True},
                            "valuation": {"score": 58.0, "published": True},
                            "risk": {"score": 71.0, "published": True},
                            "momentum": {"score": 55.0, "published": True},
                        },
                        "META": {
                            "status": "INSUFFICIENT_DATA",
                            "quality": {"score": 78.0, "published": True},
                            "valuation": {"score": 42.0, "published": True},
                            "risk": {"score": 49.0, "published": True},
                            "momentum": {"score": 67.0, "published": True},
                        },
                        "NVDA": {
                            "status": "INSUFFICIENT_DATA",
                            "quality": {"score": None, "published": False},
                            "valuation": {"score": 81.0, "published": True},
                            "risk": {"score": 52.0, "published": True},
                            "momentum": {"score": 73.0, "published": True},
                        },
                    }
                }
            }
        }

        googl_v2, googl_plain, googl_diag = _build_thesis_fields_for_card(
            ticker="GOOGL", run_id="run-live", run_lookup=run_lookup
        )
        meta_v2, meta_plain, meta_diag = _build_thesis_fields_for_card(
            ticker="META", run_id="run-live", run_lookup=run_lookup
        )
        nvda_v2, nvda_plain, nvda_diag = _build_thesis_fields_for_card(
            ticker="NVDA", run_id="run-live", run_lookup=run_lookup
        )

        assert googl_diag == meta_diag == nvda_diag == "attached"
        assert googl_v2 and meta_v2 and nvda_v2

        assert googl_plain["quality_label"] == "Business quality looks strong"
        assert meta_plain["quality_label"] == "Business quality looks strong"
        assert nvda_plain["quality_label"] == "Business quality signal is limited"
        assert googl_plain["valuation_label"] != meta_plain["valuation_label"]
        assert googl_plain["momentum_label"] != nvda_plain["momentum_label"]
        assert googl_plain["headline"] == "Not enough data for a reliable investment-case read"


class TestThesisRunFallbackBehavior:
    """Verify that _build_thesis_fields_for_card falls back to the latest
    completed run when a card's own agent_run_id run lacks _thesis_v2.

    This covers: stale run binding (_persist failure leaving old recs active),
    and the timing window between _persist.finally and _update_run writing
    _thesis_v2 to agent_runs.allocation.
    """

    _VARIED_THESIS_MAP = {
        "GOOGL": {
            "status": "INSUFFICIENT_DATA",
            "quality": {"score": 74.0, "published": True},
            "valuation": {"score": 58.0, "published": True},
            "risk": {"score": 71.0, "published": True},
            "momentum": {"score": 55.0, "published": True},
        },
        "META": {
            "status": "INSUFFICIENT_DATA",
            "quality": {"score": 78.0, "published": True},
            "valuation": {"score": 42.0, "published": True},
            "risk": {"score": 49.0, "published": True},
            "momentum": {"score": 67.0, "published": True},
        },
        "NVDA": {
            "status": "INSUFFICIENT_DATA",
            "quality": {"score": None, "published": False},
            "valuation": {"score": 81.0, "published": True},
            "risk": {"score": 52.0, "published": True},
            "momentum": {"score": 73.0, "published": True},
        },
    }

    def test_old_run_lacks_thesis_map_falls_back_to_newer_run(self):
        """Card's own run has no _thesis_v2; fallback run has varied map."""
        run_lookup = {
            "old-run": {"allocation": {}},  # no _thesis_v2
            "new-run": {"allocation": {"_thesis_v2": self._VARIED_THESIS_MAP}},
        }
        v2, plain, diag = _build_thesis_fields_for_card(
            ticker="GOOGL",
            run_id="old-run",
            run_lookup=run_lookup,
            fallback_run_id="new-run",
        )
        assert diag == "attached_via_latest_run", diag
        assert isinstance(plain, dict)
        assert plain["quality_label"] == "Business quality looks strong"

    def test_fallback_run_provides_varied_labels_per_ticker(self):
        """Different tickers get different Business read lines from the same fallback run."""
        run_lookup = {
            "stale-run": {"allocation": {}},
            "latest-run": {"allocation": {"_thesis_v2": self._VARIED_THESIS_MAP}},
        }
        _, googl_plain, googl_diag = _build_thesis_fields_for_card(
            ticker="GOOGL", run_id="stale-run", run_lookup=run_lookup, fallback_run_id="latest-run"
        )
        _, meta_plain, meta_diag = _build_thesis_fields_for_card(
            ticker="META", run_id="stale-run", run_lookup=run_lookup, fallback_run_id="latest-run"
        )
        _, nvda_plain, nvda_diag = _build_thesis_fields_for_card(
            ticker="NVDA", run_id="stale-run", run_lookup=run_lookup, fallback_run_id="latest-run"
        )
        assert googl_diag == meta_diag == nvda_diag == "attached_via_latest_run"
        # Valuation differs: GOOGL 58 (neutral) vs META 42 (cautious)
        assert googl_plain["valuation_label"] != meta_plain["valuation_label"]
        # Quality differs: NVDA unpublished vs GOOGL published strong
        assert nvda_plain["quality_label"] != googl_plain["quality_label"]

    def test_both_runs_lack_thesis_returns_none_safely(self):
        """Neither primary nor fallback run has _thesis_v2 — safe None return."""
        run_lookup = {
            "run-a": {"allocation": {}},
            "run-b": {"allocation": {"some_key": "value"}},
        }
        v2, plain, diag = _build_thesis_fields_for_card(
            ticker="AAPL",
            run_id="run-a",
            run_lookup=run_lookup,
            fallback_run_id="run-b",
        )
        assert v2 is None
        assert plain is None
        assert diag in ("thesis_map_missing", "run_not_found")

    def test_run_not_in_lookup_falls_back_to_latest(self):
        """Card's run_id not in run_lookup at all — fallback serves thesis."""
        run_lookup = {
            "latest-run": {"allocation": {"_thesis_v2": {"AAPL": {"status": "PARTIAL"}}}},
        }
        v2, plain, diag = _build_thesis_fields_for_card(
            ticker="AAPL",
            run_id="missing-run",
            run_lookup=run_lookup,
            fallback_run_id="latest-run",
        )
        assert diag == "attached_via_latest_run"
        assert isinstance(plain, dict)

    def test_primary_run_has_thesis_no_fallback_needed(self):
        """When card's own run has valid _thesis_v2, fallback run is not used."""
        run_lookup = {
            "primary-run": {"allocation": {"_thesis_v2": {"AAPL": {
                "status": "PARTIAL",
                "quality": {"score": 80.0, "published": True},
                "valuation": {"score": 55.0, "published": True},
                "risk": {"score": 60.0, "published": True},
                "momentum": {"score": 70.0, "published": True},
            }}}},
            "other-run": {"allocation": {"_thesis_v2": {"AAPL": {"status": "INSUFFICIENT_DATA"}}}},
        }
        v2, plain, diag = _build_thesis_fields_for_card(
            ticker="AAPL",
            run_id="primary-run",
            run_lookup=run_lookup,
            fallback_run_id="other-run",
        )
        # Primary succeeds — diag must be "attached" (not via fallback)
        assert diag == "attached"
        assert v2 is not None

    def test_active_run_reuse_no_fallback_same_run_id(self):
        """When fallback_run_id equals the primary run_id, no duplicate lookup."""
        run_lookup = {
            "run-1": {"allocation": {}},
        }
        v2, plain, diag = _build_thesis_fields_for_card(
            ticker="AAPL",
            run_id="run-1",
            run_lookup=run_lookup,
            fallback_run_id="run-1",  # same as primary — must not loop
        )
        assert v2 is None
        assert diag == "thesis_map_missing"

    def test_no_fallback_run_id_degrades_gracefully(self):
        """When fallback_run_id is None, behaviour is identical to old contract."""
        run_lookup = {
            "run-x": {"allocation": {}},
        }
        v2, plain, diag = _build_thesis_fields_for_card(
            ticker="AAPL",
            run_id="run-x",
            run_lookup=run_lookup,
            fallback_run_id=None,
        )
        assert v2 is None
        assert diag == "thesis_map_missing"


# ── generate_rec decision tree tests ────────────────────────────────────────


class TestGenerateRecNoPrice:
    """Branch: price is None or 0."""

    def test_no_price_sell_category_lt_ready(self):
        rec = generate_rec(cat="SELL", ticker="BLSH", cost=10, target=None,
                           bear=None, bull=None, lt_ready=True, lt_date="2025-01-01",
                           price=None)
        assert rec.action == "SELL"
        assert "SELL NOW" in rec.action_label

    def test_no_price_sell_category_not_lt(self):
        rec = generate_rec(cat="SELL", ticker="BLSH", cost=10, target=None,
                           bear=None, bull=None, lt_ready=False, lt_date="2025-06-01",
                           price=None)
        assert rec.action == "SELL"
        assert "WAIT" in rec.action_label
        assert "2025-06-01" in rec.action_label

    def test_no_price_non_sell(self):
        rec = generate_rec(cat="Core", ticker="AAPL", cost=100, target=150,
                           bear=80, bull=200, lt_ready=True, lt_date="2025-01-01",
                           price=None)
        assert rec.action == "HOLD"
        assert "Awaiting" in rec.detail


class TestGenerateRecNoTarget:
    """Branch: target is None and price exists."""

    def test_sell_category_lt_ready(self):
        rec = generate_rec(cat="SELL", ticker="STUB", cost=50, target=None,
                           bear=None, bull=None, lt_ready=True, lt_date="2025-01-01",
                           price=60)
        assert rec.action == "SELL"
        assert "LT eligible" in rec.action_label

    def test_sell_category_not_lt(self):
        rec = generate_rec(cat="SELL", ticker="STUB", cost=50, target=None,
                           bear=None, bull=None, lt_ready=False, lt_date="2025-06-01",
                           price=60)
        assert rec.action == "SELL"
        assert "WAIT" in rec.action_label

    def test_non_sell_no_target(self):
        rec = generate_rec(cat="Core", ticker="AAPL", cost=100, target=None,
                           bear=None, bull=None, lt_ready=True, lt_date="2025-01-01",
                           price=110)
        assert rec.action == "HOLD"
        assert "No analyst target" in rec.detail


class TestGenerateRecIncomeForever:
    """Branch: ticker in INCOME_FOREVER (VYM, SCHD)."""

    def test_vym_hold_forever(self):
        rec = generate_rec(cat="ETF", ticker="VYM", cost=100, target=120,
                           bear=80, bull=150, lt_ready=True, lt_date="2024-01-01",
                           price=110, drip_shares=5.0, drip_cost=400.0)
        assert rec.action == "HOLD"
        assert "HOLD FOREVER" in rec.action_label
        assert "Never sell" in rec.detail
        assert rec.drip_note != ""

    def test_schd_hold_forever(self):
        rec = generate_rec(cat="ETF", ticker="SCHD", cost=70, target=85,
                           bear=55, bull=100, lt_ready=True, lt_date="2024-01-01",
                           price=75)
        assert rec.action == "HOLD"
        assert "income machine" in rec.detail.lower()


class TestGenerateRecDCAAlways:
    """Branch: ticker in DCA_ALWAYS (VOO, QQQ, VTI)."""

    def test_voo_dca(self):
        rec = generate_rec(cat="Core", ticker="VOO", cost=400, target=500,
                           bear=350, bull=550, lt_ready=True, lt_date="2024-01-01",
                           price=450)
        assert rec.action == "BUY"
        assert "DCA ALWAYS" in rec.action_label
        assert "Never sell" in rec.detail

    def test_qqq_dca(self):
        rec = generate_rec(cat="Core", ticker="QQQ", cost=350, target=450,
                           bear=300, bull=500, lt_ready=True, lt_date="2024-01-01",
                           price=380)
        assert rec.action == "BUY"
        assert "DCA" in rec.action_label


class TestGenerateRecSellCategory:
    """Branch: cat == 'SELL' with price and target."""

    def test_sell_lt_ready(self):
        rec = generate_rec(cat="SELL", ticker="KLAR", cost=10, target=15,
                           bear=5, bull=20, lt_ready=True, lt_date="2024-01-01",
                           price=12)
        assert rec.action == "SELL"
        assert "LT eligible" in rec.action_label

    def test_sell_not_lt(self):
        rec = generate_rec(cat="SELL", ticker="KLAR", cost=10, target=15,
                           bear=5, bull=20, lt_ready=False, lt_date="2025-09-01",
                           price=12)
        assert rec.action == "SELL"
        assert "WAIT" in rec.action_label


class TestGenerateRecBearProximity:
    """Branch: price < bear * 1.10 and non-crypto."""

    def test_within_10pct_of_bear(self):
        # bear=90, 10% above = 99. price=95 < 99 → trigger
        rec = generate_rec(cat="Core", ticker="AAPL", cost=100, target=150,
                           bear=90, bull=200, lt_ready=True, lt_date="2024-01-01",
                           price=95)
        assert rec.action == "REVIEW"
        assert "STOP-LOSS" in rec.action_label
        assert "10%" in rec.detail

    def test_crypto_exempt_from_bear(self):
        # Same scenario but crypto — should NOT trigger bear alert
        rec = generate_rec(cat="Crypto", ticker="BTC", cost=40000, target=100000,
                           bear=30000, bull=150000, lt_ready=True, lt_date="2024-01-01",
                           price=32000)
        assert "STOP-LOSS" not in rec.action_label


class TestGenerateRecCrypto:
    """Branch: cat == 'Crypto'."""

    def test_crypto_high_upside(self):
        # upside > 25%
        rec = generate_rec(cat="Crypto", ticker="BTC", cost=40000, target=100000,
                           bear=30000, bull=150000, lt_ready=True, lt_date="2024-01-01",
                           price=60000)
        assert rec.action == "BUY"
        assert "ACCUMULATE" in rec.action_label

    def test_crypto_above_target(self):
        # upside < -20% → need price well above target
        rec = generate_rec(cat="Crypto", ticker="BTC", cost=40000, target=80000,
                           bear=30000, bull=150000, lt_ready=True, lt_date="2024-01-01",
                           price=110000)
        assert rec.action == "TRIM"
        assert "TRIM" in rec.action_label

    def test_crypto_hold_zone(self):
        # upside between -20% and 25%
        rec = generate_rec(cat="Crypto", ticker="XRP", cost=1.0, target=2.0,
                           bear=0.5, bull=3.0, lt_ready=True, lt_date="2024-01-01",
                           price=1.8)
        assert rec.action == "HOLD"


class TestGenerateRecDecliningThesis:
    """Branch: target < cost (declining)."""

    def test_declining_high_upside(self):
        # target=80 < cost=100, upside > 20%
        rec = generate_rec(cat="Core", ticker="SNOW", cost=100, target=80,
                           bear=50, bull=120, lt_ready=True, lt_date="2024-01-01",
                           price=60)
        assert rec.action == "BUY"
        assert "declining investment case" in rec.detail.lower()

    def test_declining_at_target_lt_ready(self):
        # target=90 < cost=100, upside between -10 and 5, lt_ready
        rec = generate_rec(cat="Core", ticker="SNOW", cost=100, target=90,
                           bear=50, bull=120, lt_ready=True, lt_date="2024-01-01",
                           price=88)
        assert rec.action == "TRIM"
        assert "LT" in rec.action_label

    def test_declining_at_target_not_lt(self):
        # target=90 < cost=100, upside between -10 and 5, not lt_ready
        rec = generate_rec(cat="Core", ticker="SNOW", cost=100, target=90,
                           bear=50, bull=120, lt_ready=False, lt_date="2025-09-01",
                           price=88)
        assert rec.action == "HOLD"
        assert "ST" in rec.action_label

    def test_declining_above_target_lt_ready(self):
        # target=80 < cost=100, upside <= -10, lt_ready
        rec = generate_rec(cat="Core", ticker="SNOW", cost=100, target=80,
                           bear=50, bull=120, lt_ready=True, lt_date="2024-01-01",
                           price=100)
        assert rec.action == "TRIM"
        assert "25%" in rec.action_label

    def test_declining_above_target_not_lt(self):
        # target=80 < cost=100, upside <= -10, not lt_ready
        rec = generate_rec(cat="Core", ticker="SNOW", cost=100, target=80,
                           bear=50, bull=120, lt_ready=False, lt_date="2025-09-01",
                           price=100)
        assert rec.action == "HOLD"
        assert "ST" in rec.action_label


class TestGenerateRecDipBuying:
    """Branch: normal thesis dip buying."""

    def test_strong_buy(self):
        # pct < -20 and upside > 20 → STRONG BUY
        rec = generate_rec(cat="Core", ticker="AAPL", cost=200, target=250,
                           bear=100, bull=300, lt_ready=True, lt_date="2024-01-01",
                           price=150)
        assert rec.action == "BUY"
        assert "STRONG BUY" in rec.action_label

    def test_buy_the_dip(self):
        # pct < -15 and upside > 15 → BUY THE DIP
        rec = generate_rec(cat="Core", ticker="MSFT", cost=400, target=450,
                           bear=300, bull=550, lt_ready=True, lt_date="2024-01-01",
                           price=335)
        assert rec.action == "BUY"
        assert "DIP" in rec.action_label


class TestGenerateRecUpsideZones:
    """Branch: standard upside zones."""

    def test_upside_above_40(self):
        rec = generate_rec(cat="Core", ticker="NVDA", cost=500, target=900,
                           bear=400, bull=1000, lt_ready=True, lt_date="2024-01-01",
                           price=600)
        assert rec.action == "BUY"
        assert "ACCUMULATE" in rec.action_label
        assert "aggressively" in rec.detail

    def test_upside_20_40_with_high_yield(self):
        # upside > 20, yield > 2.0
        rec = generate_rec(cat="ETF", ticker="XLE", cost=80, target=120,
                           bear=60, bull=150, lt_ready=True, lt_date="2024-01-01",
                           price=95)
        assert rec.action == "BUY"
        assert "DRIP" in rec.action_label

    def test_upside_20_40_low_yield(self):
        rec = generate_rec(cat="Core", ticker="NFLX", cost=500, target=800,
                           bear=400, bull=1000, lt_ready=True, lt_date="2024-01-01",
                           price=620)
        assert rec.action == "BUY"
        assert "ACCUMULATE" in rec.action_label


class TestGenerateRecAtAboveTarget:
    """Branch: at or above analyst target."""

    def test_at_target_lt_ready(self):
        # upside between -10 and 5, lt_ready → TRIM 20%
        rec = generate_rec(cat="Core", ticker="COST", cost=500, target=600,
                           bear=400, bull=700, lt_ready=True, lt_date="2024-01-01",
                           price=590)
        assert rec.action == "TRIM"
        assert "20%" in rec.action_label

    def test_at_target_not_lt(self):
        # upside between -10 and 5, not lt → HOLD (ST)
        rec = generate_rec(cat="Core", ticker="COST", cost=500, target=600,
                           bear=400, bull=700, lt_ready=False, lt_date="2025-09-01",
                           price=590)
        assert rec.action == "HOLD"
        assert "ST" in rec.action_label

    def test_above_target_lt_ready(self):
        # upside <= -10, lt_ready → TRIM 25%
        rec = generate_rec(cat="Core", ticker="COST", cost=500, target=600,
                           bear=400, bull=700, lt_ready=True, lt_date="2024-01-01",
                           price=700)
        assert rec.action == "TRIM"
        assert "25%" in rec.action_label

    def test_above_target_not_lt(self):
        # upside <= -10, not lt → HOLD (ST)
        rec = generate_rec(cat="Core", ticker="COST", cost=500, target=600,
                           bear=400, bull=700, lt_ready=False, lt_date="2025-09-01",
                           price=700)
        assert rec.action == "HOLD"
        assert "ST" in rec.action_label


class TestGenerateRecHoldZone:
    """Branch: 10-20% upside — normal hold."""

    def test_hold_zone(self):
        # upside ~15%
        rec = generate_rec(cat="Core", ticker="GOOGL", cost=150, target=200,
                           bear=120, bull=250, lt_ready=True, lt_date="2024-01-01",
                           price=175)
        assert rec.action == "HOLD"
        assert "upside" in rec.detail.lower()


class TestGenerateRecDefault:
    """Branch: default hold (5-10% upside)."""

    def test_default_hold(self):
        # upside ~8%
        rec = generate_rec(cat="Core", ticker="GOOGL", cost=150, target=200,
                           bear=120, bull=250, lt_ready=True, lt_date="2024-01-01",
                           price=185)
        assert rec.action == "HOLD"


class TestGenerateRecEdgeCases:
    """Edge cases and boundary conditions."""

    def test_zero_cost_no_division_error(self):
        rec = generate_rec(cat="Core", ticker="AAPL", cost=0, target=150,
                           bear=80, bull=200, lt_ready=True, lt_date="2024-01-01",
                           price=100)
        assert rec.action in ("BUY", "SELL", "TRIM", "HOLD", "REVIEW")

    def test_zero_price_treated_as_no_price(self):
        rec = generate_rec(cat="Core", ticker="AAPL", cost=100, target=150,
                           bear=80, bull=200, lt_ready=True, lt_date="2024-01-01",
                           price=0)
        assert rec.action == "HOLD"
        assert "Awaiting" in rec.detail

    def test_all_actions_are_valid(self):
        """Verify that every possible action is in the valid set."""
        valid_actions = {"BUY", "SELL", "TRIM", "HOLD", "REVIEW"}
        scenarios = [
            ("SELL", "STUB", 10, None, None, None, True, "", None),
            ("Core", "AAPL", 100, 150, 80, 200, True, "", 120),
            ("ETF", "VYM", 100, 120, 80, 150, True, "", 110),
            ("Core", "VOO", 400, 500, 350, 550, True, "", 450),
            ("Crypto", "BTC", 40000, 100000, 30000, 150000, True, "", 60000),
        ]
        for cat, ticker, cost, target, bear, bull, lt, lt_date, price in scenarios:
            rec = generate_rec(cat=cat, ticker=ticker, cost=cost, target=target,
                               bear=bear, bull=bull, lt_ready=lt, lt_date=lt_date,
                               price=price)
            assert rec.action in valid_actions, f"Invalid action '{rec.action}' for {ticker}"

    def test_drip_data_flows_through(self):
        rec = generate_rec(cat="ETF", ticker="VYM", cost=100, target=120,
                           bear=80, bull=150, lt_ready=True, lt_date="2024-01-01",
                           price=110, drip_shares=5.0, drip_cost=450.0)
        assert rec.drip_note != ""
        assert "DRIP" in rec.drip_note

    def test_urgency_range(self):
        """All urgency values should be 0-4."""
        scenarios = [
            ("Core", "AAPL", 200, 250, 100, 300, True, "", 150),  # STRONG BUY → 4
            ("Core", "AAPL", 100, 150, 90, 200, True, "", 95),    # STOP-LOSS → 4
            ("Core", "AAPL", 100, 150, 80, 200, True, "", 140),   # HOLD → 0-1
        ]
        for cat, ticker, cost, target, bear, bull, lt, lt_date, price in scenarios:
            rec = generate_rec(cat=cat, ticker=ticker, cost=cost, target=target,
                               bear=bear, bull=bull, lt_ready=lt, lt_date=lt_date,
                               price=price)
            assert 0 <= rec.urgency <= 4, f"Urgency {rec.urgency} out of range for {ticker}"

    def test_sell_category_overrides_bear_proximity(self):
        """SELL category should be checked before bear proximity."""
        rec = generate_rec(cat="SELL", ticker="KLAR", cost=10, target=15,
                           bear=12, bull=20, lt_ready=True, lt_date="2024-01-01",
                           price=11)
        assert rec.action == "SELL"
        assert "STOP-LOSS" not in rec.action_label

    def test_income_forever_overrides_standard_logic(self):
        """VYM should HOLD FOREVER even if far above target."""
        rec = generate_rec(cat="ETF", ticker="VYM", cost=80, target=90,
                           bear=60, bull=120, lt_ready=True, lt_date="2024-01-01",
                           price=200)
        assert "HOLD FOREVER" in rec.action_label

    def test_dca_always_overrides_trim(self):
        """VOO should DCA even if above target."""
        rec = generate_rec(cat="Core", ticker="VOO", cost=300, target=400,
                           bear=250, bull=500, lt_ready=True, lt_date="2024-01-01",
                           price=500)
        assert "DCA" in rec.action_label


class TestPortfolioIntelSynthesis:
    def _card(self, ticker: str, action: str, category: str = "Core", sector: str | None = None, technical: str | None = None):
        from uuid import uuid4
        from app.models.recommendation import InsightCard

        return InsightCard(
            id=uuid4(),
            ticker=ticker,
            name=ticker,
            action=action,
            detail=f"{ticker} detail",
            rationale="",
            urgency=2,
            color="blue",
            tax_note="LT eligible" if action in {"TRIM", "SELL"} else "",
            drip_note="",
            category=category,
            sector=sector,
            technical_signal=technical,
            analyst_drivers=[f"{ticker} driver"],
            analyst_risks=[f"{ticker} risk"],
            analyst_confidence=0.72 if action == "BUY" else 0.55,
            analysis_source="live_llm",
            data_quality_label="HIGH",
        )

    def test_portfolio_intel_uses_strategy_and_sector_buckets(self):
        from app.services.recommendation_engine import compute_portfolio_synthesis

        cards = [
            self._card("VOO", "HOLD", category="ETF", sector="ETFs / Broad Market"),
            self._card("MSFT", "BUY", sector="Technology"),
            self._card("NVDA", "BUY", sector="Technology"),
            self._card("BTC", "TRIM", category="Crypto", sector="Crypto"),
        ]
        synthesis = compute_portfolio_synthesis(cards)

        assert synthesis["exposures"]["strategy_buckets"]
        names = {b["name"] for b in synthesis["exposures"]["strategy_buckets"]}
        assert "Core index ETFs" in names
        assert "Semiconductors / AI infrastructure" in names or "Mega-cap quality growth" in names
        assert "Core ~" not in synthesis["summary"]
        assert "ETF ~" not in synthesis["summary"]

    def test_portfolio_intel_opportunities_and_trims_present(self):
        from app.services.recommendation_engine import compute_portfolio_synthesis

        cards = [
            self._card("MSFT", "BUY", sector="Technology"),
            self._card("AAPL", "BUY", sector="Technology"),
            self._card("RIVN", "TRIM", category="Speculative", sector="Industrials / Autos", technical="SELL"),
            self._card("BTC", "SELL", category="Crypto", sector="Crypto", technical="SELL"),
        ]
        synthesis = compute_portfolio_synthesis(cards)

        assert synthesis["top_opportunities"]
        assert {row["ticker"] for row in synthesis["top_opportunities"]} & {"MSFT", "AAPL"}
        assert synthesis["trim_candidates"]
        assert {row["ticker"] for row in synthesis["trim_candidates"]} & {"RIVN", "BTC"}

    def test_portfolio_intel_summary_mentions_tickers_and_not_unknown_100(self):
        from app.services.recommendation_engine import compute_portfolio_synthesis

        cards = [
            self._card("MSFT", "BUY", sector="Technology"),
            self._card("NVDA", "BUY", sector="Technology"),
            self._card("RIVN", "TRIM", sector="Industrials / Autos", technical="SELL"),
        ]
        synthesis = compute_portfolio_synthesis(cards)
        summary = (synthesis.get("summary") or "").lower()

        assert any(t in summary for t in ["msft", "nvda", "rivn"])
        assert "unknown 100%" not in summary
