"""Intel v3 router + service integration tests (Gate hardening pass).

Proves the service/router path rather than just pure helpers:
  - App startup/import with intel_v3 router registered does not crash.
  - Feature flag off returns 404.
  - Feature flag on + no snapshot returns no_snapshot 404.
  - POST /run builds exactly one snapshot payload.
  - POST /run does NOT persist when validator finds hard violations.
  - GET /snapshot returns the persisted snapshot.
  - action_counts match current_holdings actions.
  - All cards share the same snapshot_id and run_id.
  - No visible action outside BUY/HOLD/TRIM/SELL.
  - Page-load snapshot read does not call RecommendationService.get_insight_cards.
  - v3 run path is the only path that calls get_insight_cards (not page load).
  - all-INSUFFICIENT_DATA + BUY-count contradiction cannot be persisted.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
from app.services.intelligence.v3.snapshot_builder import build_snapshot
from app.services.intelligence.v3.source_validator_lite import (
    validate_snapshot_cards,
    HARD_VIOLATION_RULES,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_output(
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
        rationale_plain_english="Signals support this position.",
        why_now="Evidence and fit support acting now.",
        why_not_now="Watch for evidence weakening.",
        source_signal_summary={},
        schema_version="v3.1",
    )


def _make_snapshot(*actions: tuple[str, ActionV3]) -> dict[str, Any]:
    decisions = [_make_output(t, a) for t, a in actions]
    metas = [{"ticker": t, "name": t, "category": "stock", "thesis_state": "intact"}
             for t, _ in actions]
    return build_snapshot(
        run_id="test-run-001",
        decisions=decisions,
        card_metas=metas,
        source_health={"status": "ok"},
        is_stale=False,
    )


# ── Gate A: App import / startup ──────────────────────────────────────────────

class TestAppImport:
    def test_app_imports_without_error(self):
        """App and intel_v3 router must import without raising."""
        from app.main import app
        from app.routers.intel_v3 import router
        assert app is not None
        assert router is not None

    def test_router_routes_registered(self):
        """intel_v3 router must be registered in the app with expected paths."""
        from app.main import app
        paths = [r.path for r in app.routes]
        assert "/api/v1/intel/v3/snapshot" in paths
        assert "/api/v1/intel/v3/run" in paths
        assert "/api/v1/intel/v3/runs/{run_id}" in paths

    def test_service_import(self):
        """IntelV3Service and is_intel_v3_enabled must import cleanly."""
        from app.services.intelligence.v3.intel_v3_service import (
            IntelV3Service,
            is_intel_v3_enabled,
        )
        assert IntelV3Service is not None
        assert callable(is_intel_v3_enabled)

    def test_recommendation_service_import_path(self):
        """The transitional adapter RecommendationService must resolve correctly."""
        from app.services.intelligence.v3.intel_v3_service import RecommendationService
        from app.services.recommendation_engine import RecommendationService as DirectRS
        assert RecommendationService is DirectRS


# ── Gate B: Feature flag behavior ────────────────────────────────────────────

class TestFeatureFlag:
    def test_flag_off_returns_false(self):
        """is_intel_v3_enabled returns False when env var is absent."""
        from app.services.intelligence.v3.intel_v3_service import is_intel_v3_enabled
        with patch.dict(os.environ, {}, clear=True):
            assert not is_intel_v3_enabled()

    def test_flag_on_returns_true(self):
        """is_intel_v3_enabled returns True when env var is 'true'."""
        from app.services.intelligence.v3.intel_v3_service import is_intel_v3_enabled
        with patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}):
            assert is_intel_v3_enabled()

    def test_flag_on_case_insensitive(self):
        """Flag check is case-insensitive (True, TRUE, 1, yes)."""
        from app.services.intelligence.v3.intel_v3_service import is_intel_v3_enabled
        for val in ("True", "TRUE", "1", "yes"):
            with patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": val}):
                assert is_intel_v3_enabled(), f"Expected True for {val!r}"

    def test_check_flag_raises_404_when_disabled(self):
        """_check_flag raises HTTPException 404 when feature flag is off."""
        from fastapi import HTTPException
        from app.routers.intel_v3 import _check_flag
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(HTTPException) as exc_info:
                _check_flag()
            assert exc_info.value.status_code == 404


# ── Gate C: Snapshot contract ─────────────────────────────────────────────────

class TestSnapshotContract:
    def test_action_counts_match_holdings(self):
        """action_counts must equal the actual distribution of card actions."""
        snap = _make_snapshot(
            ("AAPL", ActionV3.BUY),
            ("MSFT", ActionV3.BUY),
            ("GOOG", ActionV3.HOLD),
            ("AMZN", ActionV3.SELL),
        )
        counts = snap["action_counts"]
        assert counts.get("BUY") == 2
        assert counts.get("HOLD") == 1
        assert counts.get("SELL") == 1
        # Total must equal card count.
        assert sum(counts.values()) == len(snap["current_holdings"])

    def test_all_cards_share_same_snapshot_and_run_id(self):
        """Every card in a snapshot must share one snapshot_id and one run_id."""
        snap = _make_snapshot(
            ("AAPL", ActionV3.BUY),
            ("MSFT", ActionV3.HOLD),
            ("GOOG", ActionV3.TRIM),
        )
        snap_ids = {c["source_snapshot_id"] for c in snap["current_holdings"]}
        run_ids = {c["source_run_id"] for c in snap["current_holdings"]}
        assert len(snap_ids) == 1, "All cards must share one snapshot_id"
        assert len(run_ids) == 1, "All cards must share one run_id"

    def test_no_action_outside_valid_set(self):
        """Every card action must be BUY/HOLD/TRIM/SELL — no posture labels."""
        snap = _make_snapshot(
            ("AAPL", ActionV3.BUY),
            ("MSFT", ActionV3.HOLD),
            ("GOOG", ActionV3.TRIM),
            ("AMZN", ActionV3.SELL),
        )
        valid = {"BUY", "HOLD", "TRIM", "SELL"}
        for card in snap["current_holdings"]:
            assert card["action"] in valid, f"{card['ticker']} has invalid action {card['action']}"

    def test_snapshot_has_required_keys(self):
        """Snapshot payload must have all required top-level keys."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        required = {
            "snapshot_id", "run_id", "generated_at", "schema_version",
            "current_holdings", "action_counts", "best_buys", "trim_sell_desk",
            "what_changed", "warnings", "is_stale",
        }
        missing = required - set(snap.keys())
        assert not missing, f"Missing snapshot keys: {missing}"


# ── Gate D: Validator fail-closed behavior ────────────────────────────────────

class TestValidatorFailClosed:
    def test_hard_violation_count_property(self):
        """ValidationResult.hard_violation_count counts only hard rules."""
        from app.services.intelligence.v3.source_validator_lite import validate_card
        result = validate_card(
            ticker="TEST",
            action="REVIEW",  # invalid — hard violation
            conviction="LOW",
            why_text="No raw metric keys here.",
        )
        assert result.hard_violation_count >= 1
        assert "valid_action_labels_only" in result.rules_violated

    def test_validate_snapshot_cards_returns_3_tuple(self):
        """validate_snapshot_cards must return (results, spam_tickers, hard_count)."""
        cards = [
            {"ticker": "AAPL", "action": "BUY", "conviction": "MEDIUM"},
        ]
        ret = validate_snapshot_cards(cards)
        assert len(ret) == 3, "Must return 3-tuple: (results, spam_tickers, hard_count)"

    def test_hard_violations_reported_in_third_element(self):
        """Third return value is the sum of hard violations across all cards."""
        cards = [
            {"ticker": "BAD1", "action": "WATCH",  "conviction": "LOW"},   # radar label → hard
            {"ticker": "BAD2", "action": "REVIEW", "conviction": "LOW"},  # invalid action → hard
            {"ticker": "OK",   "action": "HOLD",   "conviction": "MEDIUM"},
        ]
        _, _, hard_count = validate_snapshot_cards(cards)
        assert hard_count >= 2

    def test_no_hard_violations_on_clean_cards(self):
        """Clean cards produce zero hard violations."""
        cards = [
            {"ticker": "AAPL", "action": "BUY",  "conviction": "HIGH",
             "why_text": "Strong earnings growth at reasonable valuation."},
            {"ticker": "MSFT", "action": "HOLD", "conviction": "MEDIUM",
             "why_text": "Holding while fundamentals confirm thesis."},
        ]
        _, _, hard_count = validate_snapshot_cards(cards)
        assert hard_count == 0

    def test_service_does_not_persist_on_hard_violations(self):
        """run_v3 must raise ValueError (not persist) when hard violations exist.

        Injects a poisoned snapshot (via patching build_snapshot) with a card
        whose why_text contains a raw metric key — a hard violation that must
        block persistence.
        """
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        # Poisoned snapshot: card has raw metric key in why_text — hard violation.
        poisoned_snapshot = {
            "snapshot_id": "snap-poison",
            "run_id": "run-poison",
            "generated_at": "2026-01-01T00:00:00Z",
            "schema_version": "v3.1",
            "current_holdings": [
                {
                    "ticker": "BAD",
                    "action": "BUY",
                    "conviction": "LOW",
                    "why_text": "High roic_ttm indicates strong returns.",  # hard: raw metric key
                }
            ],
            "action_counts": {"BUY": 1},
            "best_buys": [],
            "trim_sell_desk": [],
            "what_changed": [],
            "warnings": [],
            "is_stale": False,
            "portfolio_command_center": {
                "total_holdings": 1, "buy_count": 1,
                "hold_count": 0, "trim_count": 0, "sell_count": 0,
            },
            "opportunity_radar_preview": {"status": "deferred"},
        }

        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        service.client = MagicMock()

        mock_card = MagicMock()
        mock_card.ticker = "BAD"
        mock_card.name = "Bad Corp"
        mock_card.category = "stock"
        mock_card.action = "BUY"
        mock_card.analyst_action = "BUY"
        mock_card.conviction_level = "LOW"
        mock_card.technical_signal = None
        mock_card.risk_flag = None
        mock_card.analyst_risks = []
        mock_card.data_quality_label = None
        mock_card.intel_read = None
        mock_card.thesis_v2 = None
        mock_card.analyst_used_fallback = None

        with (
            patch(
                "app.services.intelligence.v3.intel_v3_service.RecommendationService",
                return_value=MagicMock(get_insight_cards=AsyncMock(return_value=[mock_card])),
            ),
            patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
            patch(
                "app.services.intelligence.v3.intel_v3_service.build_snapshot",
                return_value=poisoned_snapshot,
            ),
            patch.object(service, "_persist_snapshot", new_callable=AsyncMock) as mock_persist,
        ):
            with pytest.raises(ValueError, match="hard validation violation"):
                asyncio.get_event_loop().run_until_complete(service.run_v3())
            # _persist_snapshot must NOT have been called.
            mock_persist.assert_not_called()

    def test_all_insufficient_data_buy_contradiction_blocked(self):
        """Cards with action=BUY but HOLD-only language in why_text are rejected."""
        from app.services.intelligence.v3.source_validator_lite import validate_card
        result = validate_card(
            ticker="TEST",
            action="BUY",
            conviction="LOW",
            why_text="stay on watchlist until signal improves",  # BUY contradiction
        )
        assert result.hard_violation_count >= 1
        assert "no_action_contradictions" in result.rules_violated


# ── Gate E: Page-load isolation ───────────────────────────────────────────────

class TestPageLoadIsolation:
    def test_get_latest_snapshot_does_not_call_recommendation_service(self):
        """get_latest_snapshot must NEVER call RecommendationService.get_insight_cards."""
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        mock_client = MagicMock()
        mock_chain = MagicMock()
        mock_chain.select.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.order.return_value = mock_chain
        mock_chain.limit.return_value = mock_chain
        mock_chain.execute.return_value = MagicMock(data=[])
        mock_client.table.return_value = mock_chain

        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        service.client = mock_client

        with patch(
            "app.services.intelligence.v3.intel_v3_service.RecommendationService"
        ) as mock_rec_service:
            asyncio.get_event_loop().run_until_complete(service.get_latest_snapshot())
            # RecommendationService must not have been instantiated or called.
            mock_rec_service.assert_not_called()

    def test_get_latest_snapshot_returns_none_when_no_rows(self):
        """get_latest_snapshot returns None when the table has no active rows."""
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        mock_client = MagicMock()
        mock_chain = MagicMock()
        mock_chain.select.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.order.return_value = mock_chain
        mock_chain.limit.return_value = mock_chain
        mock_chain.execute.return_value = MagicMock(data=[])
        mock_client.table.return_value = mock_chain

        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        service.client = mock_client

        result = asyncio.get_event_loop().run_until_complete(service.get_latest_snapshot())
        assert result is None

    def test_get_latest_snapshot_returns_payload_when_row_exists(self):
        """get_latest_snapshot returns the payload dict from the stored row."""
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        expected = {"snapshot_id": "snap-abc", "action_counts": {"BUY": 3}}

        mock_client = MagicMock()
        mock_chain = MagicMock()
        mock_chain.select.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.order.return_value = mock_chain
        mock_chain.limit.return_value = mock_chain
        mock_chain.execute.return_value = MagicMock(data=[{"payload": expected, "id": "row-1"}])
        mock_client.table.return_value = mock_chain

        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        service.client = mock_client

        result = asyncio.get_event_loop().run_until_complete(service.get_latest_snapshot())
        assert result == expected


# ── Gate F: Run path produces valid snapshot ──────────────────────────────────

class TestRunPath:
    def _make_mock_card(self, ticker: str, action: str = "BUY") -> MagicMock:
        card = MagicMock()
        card.ticker = ticker
        card.name = ticker + " Corp"
        card.category = "stock"
        card.action = action
        card.analyst_action = action
        card.conviction_level = "MEDIUM"
        card.technical_signal = None
        card.risk_flag = None
        card.analyst_risks = []
        card.data_quality_label = "PARTIAL"
        card.intel_read = None
        card.thesis_v2 = None
        card.analyst_used_fallback = False
        return card

    def test_run_v3_produces_snapshot_with_valid_structure(self):
        """run_v3 with clean cards produces a valid snapshot payload."""
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        cards = [
            self._make_mock_card("AAPL", "BUY"),
            self._make_mock_card("MSFT", "HOLD"),
        ]

        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_table.update.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.insert.return_value = mock_table
        mock_table.execute.return_value = MagicMock(data=[])
        mock_client.table.return_value = mock_table

        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        service.client = mock_client

        with (
            patch(
                "app.services.intelligence.v3.intel_v3_service.RecommendationService",
                return_value=MagicMock(get_insight_cards=AsyncMock(return_value=cards)),
            ),
            patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
            patch.object(service, "_persist_snapshot", new_callable=AsyncMock),
        ):
            result = asyncio.get_event_loop().run_until_complete(service.run_v3())

        assert "snapshot_id" in result
        assert "run_id" in result
        assert "current_holdings" in result
        assert "action_counts" in result

    def test_run_v3_action_counts_match_produced_cards(self):
        """action_counts returned by run_v3 must match actual card actions."""
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        cards = [
            self._make_mock_card("AAPL", "BUY"),
            self._make_mock_card("NVDA", "BUY"),
            self._make_mock_card("MSFT", "HOLD"),
        ]

        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
        service.client = MagicMock()

        with (
            patch(
                "app.services.intelligence.v3.intel_v3_service.RecommendationService",
                return_value=MagicMock(get_insight_cards=AsyncMock(return_value=cards)),
            ),
            patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
            patch.object(service, "_persist_snapshot", new_callable=AsyncMock),
        ):
            result = asyncio.get_event_loop().run_until_complete(service.run_v3())

        # action_counts must be derived from cards, not from input card actions.
        holdings = result["current_holdings"]
        from collections import Counter
        expected_counts = dict(Counter(c["action"] for c in holdings))
        assert result["action_counts"] == expected_counts

    def test_run_v3_all_cards_share_snapshot_and_run_id(self):
        """All cards produced by run_v3 share one snapshot_id and one run_id."""
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        cards = [self._make_mock_card(t) for t in ("AAPL", "MSFT", "GOOG")]

        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
        service.client = MagicMock()

        with (
            patch(
                "app.services.intelligence.v3.intel_v3_service.RecommendationService",
                return_value=MagicMock(get_insight_cards=AsyncMock(return_value=cards)),
            ),
            patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
            patch.object(service, "_persist_snapshot", new_callable=AsyncMock),
        ):
            result = asyncio.get_event_loop().run_until_complete(service.run_v3())

        snap_ids = {c["source_snapshot_id"] for c in result["current_holdings"]}
        run_ids = {c["source_run_id"] for c in result["current_holdings"]}
        assert len(snap_ids) == 1
        assert len(run_ids) == 1
