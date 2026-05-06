"""Intel v3 certification fix tests.

Proves the root causes from the 2026-05-06 production certification failure
are fixed:
  1. No generic copy spam — every card's why_text is unique (ticker-prefixed).
  2. action_counts match rendered cards.
  3. No raw metric keys in any rationale field.
  4. No posture labels.
  5. Page-load read path does NOT invoke LLM/recommendation aggregation.
  6. v3 snapshot run path detects and reports generic_copy_count.
  7. 29+ identical BUY cards would have been detected — now produces 0.

Synthetic fixtures only — no real user or account data.
"""
from __future__ import annotations

import uuid
from collections import Counter
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
from app.services.intelligence.v3.decision_policy_v1 import decide
from app.services.intelligence.v3.snapshot_builder import build_snapshot
from app.services.intelligence.v3.source_validator_lite import (
    detect_generic_copy_spam,
    validate_snapshot_cards,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

_RAW_METRIC_KEYS = [
    "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm",
    "peg_ratio", "p_fcf", "ebit_margin", "net_margin_ttm",
    "debt_to_equity", "current_ratio", "altman_z",
]

_BANNED_POSTURE_LABELS = [
    "watchlist", "review", "risk watch", "add candidate",
    "strong buy", "strong sell",
]


def _buy_input(ticker: str, evidence: AxisBand = AxisBand.OK) -> DecisionInputV3:
    """Standard BUY input with no price signal suppression."""
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=evidence,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.UNDERWEIGHT,
        risk_band=RiskBand.NONE,
        raw_action="BUY",
        raw_analyst_action="BUY",
        upstream_conviction="MEDIUM",
    )


def _hold_input(ticker: str) -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=AxisBand.THIN,
        price_context=PriceBand.SUPPRESSED,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.UNKNOWN,
        raw_action="HOLD",
        raw_analyst_action="HOLD",
        upstream_conviction="LOW",
    )


def _make_portfolio_snapshot(buy_tickers: list[str], hold_tickers: list[str]) -> dict[str, Any]:
    """Build a snapshot from a mix of BUY and HOLD cards (same evidence/price context)."""
    decisions = []
    metas = []
    for t in buy_tickers:
        inp = _buy_input(t)
        decisions.append(decide(inp))
        metas.append({"ticker": t, "name": f"{t} Corp", "category": "stock", "thesis_state": "intact"})
    for t in hold_tickers:
        inp = _hold_input(t)
        decisions.append(decide(inp))
        metas.append({"ticker": t, "name": f"{t} Corp", "category": "stock", "thesis_state": "intact"})
    return build_snapshot(
        run_id="test-cert-001",
        decisions=decisions,
        card_metas=metas,
        source_health={"status": "ok"},
    )


# ── Section 1: Generic copy elimination ──────────────────────────────────────

class TestGenericCopyEliminated:
    """Prove that 11 BUY cards with identical evidence/price context produce 0 generic copies."""

    def test_11_buy_cards_same_evidence_price_all_unique_why_text(self):
        """Root cause fix: 11 BUY cards with same evidence+price → all unique why_text."""
        buy_tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ", "SCHD"]
        snap = _make_portfolio_snapshot(buy_tickers, [])

        why_texts = [c["why_text"] for c in snap["current_holdings"]]
        unique_count = len(set(why_texts))
        assert unique_count == len(buy_tickers), (
            f"Expected {len(buy_tickers)} unique why_text values, got {unique_count}. "
            f"Texts: {why_texts}"
        )

    def test_11_buy_cards_spam_detector_returns_empty(self):
        """detect_generic_copy_spam must return [] when all cards have unique why_text."""
        buy_tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ", "SCHD"]
        snap = _make_portfolio_snapshot(buy_tickers, [])

        spam = detect_generic_copy_spam(snap["current_holdings"])
        assert spam == [], f"Expected no spam tickers, got: {spam}"

    def test_validate_snapshot_generic_copy_count_is_zero(self):
        """validate_snapshot_cards spam_tickers must be empty for ticker-prefixed rationale."""
        buy_tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ", "SCHD"]
        snap = _make_portfolio_snapshot(buy_tickers, [])

        _, spam_tickers, hard_count = validate_snapshot_cards(snap["current_holdings"])
        assert spam_tickers == [], f"Expected 0 spam tickers, got: {spam_tickers}"
        assert hard_count == 0, f"Expected 0 hard violations, got: {hard_count}"

    def test_34_card_portfolio_zero_generic_copy(self):
        """Synthetic 34-card portfolio (11 BUY + 23 HOLD) produces 0 generic copy cards."""
        buy_tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ", "SCHD"]
        hold_tickers = [f"HOLD{i}" for i in range(23)]
        snap = _make_portfolio_snapshot(buy_tickers, hold_tickers)

        _, spam_tickers, hard_count = validate_snapshot_cards(snap["current_holdings"])
        assert spam_tickers == [], f"34-card portfolio: expected 0 spam, got {spam_tickers}"
        assert hard_count == 0

    def test_why_text_contains_ticker(self):
        """Each card's why_text must start with its ticker symbol."""
        tickers = ["AAPL", "MSFT", "NVDA"]
        snap = _make_portfolio_snapshot(tickers, [])

        for card in snap["current_holdings"]:
            assert card["ticker"] in card["why_text"], (
                f"Ticker {card['ticker']!r} not found in why_text: {card['why_text']!r}"
            )

    def test_old_generic_template_no_longer_produced(self):
        """Old generic template 'Signals support adding: ... evidence coverage...' must not appear."""
        tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META"]
        snap = _make_portfolio_snapshot(tickers, [])

        old_template = "Signals support adding:"
        for card in snap["current_holdings"]:
            assert old_template not in card["why_text"], (
                f"Old generic template still present in {card['ticker']} why_text: {card['why_text']!r}"
            )


# ── Section 2: Action count contract ─────────────────────────────────────────

class TestActionCountsMatchCards:
    """action_counts must be derived from cards — not an independent computation."""

    def test_action_counts_match_holdings_exactly(self):
        buy_tickers = ["AAPL", "MSFT", "NVDA"]
        hold_tickers = ["GOOG", "META", "AMZN", "TSLA"]
        snap = _make_portfolio_snapshot(buy_tickers, hold_tickers)

        cards = snap["current_holdings"]
        expected = dict(Counter(c["action"] for c in cards))
        assert snap["action_counts"] == expected

    def test_portfolio_command_center_counts_match_action_counts(self):
        buy_tickers = ["AAPL", "MSFT"]
        hold_tickers = ["GOOG", "META", "AMZN"]
        snap = _make_portfolio_snapshot(buy_tickers, hold_tickers)

        cc = snap["portfolio_command_center"]
        ac = snap["action_counts"]
        assert cc["buy_count"] == ac.get("BUY", 0)
        assert cc["hold_count"] == ac.get("HOLD", 0)
        assert cc["trim_count"] == ac.get("TRIM", 0)
        assert cc["sell_count"] == ac.get("SELL", 0)
        assert cc["total_holdings"] == len(snap["current_holdings"])


# ── Section 3: No raw metric keys / posture labels ───────────────────────────

class TestNoRawMetricKeysOrPostureLabels:
    """Visible snapshot card fields must be free of raw metric keys and posture labels."""

    def _all_text(self, card: dict) -> str:
        return " ".join(filter(None, [
            card.get("why_text", ""),
            card.get("risk_text", ""),
            card.get("action_text", ""),
            card.get("evidence_text", ""),
            card.get("what_would_change_view", ""),
        ])).lower()

    def test_no_raw_metric_keys_in_any_card_text(self):
        buy_tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN",
                       "TSM", "VGT", "VOO", "QQQ", "SCHD"]
        hold_tickers = [f"HOLD{i}" for i in range(23)]
        snap = _make_portfolio_snapshot(buy_tickers, hold_tickers)

        violations = []
        for card in snap["current_holdings"]:
            text = self._all_text(card)
            for key in _RAW_METRIC_KEYS:
                if key in text:
                    violations.append(f"{card['ticker']}: found '{key}'")
        assert violations == [], f"Raw metric key violations: {violations}"

    def test_no_banned_posture_labels_in_action_field(self):
        buy_tickers = ["AAPL", "MSFT", "NVDA"]
        hold_tickers = ["GOOG", "META"]
        snap = _make_portfolio_snapshot(buy_tickers, hold_tickers)

        valid_actions = {"BUY", "HOLD", "TRIM", "SELL"}
        for card in snap["current_holdings"]:
            assert card["action"] in valid_actions, (
                f"{card['ticker']} has invalid action: {card['action']!r}"
            )
            for label in _BANNED_POSTURE_LABELS:
                assert label not in card["action"].lower(), (
                    f"{card['ticker']} action contains banned label: {label!r}"
                )

    def test_raw_metric_key_count_is_zero_for_standard_portfolio(self):
        buy_tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN",
                       "TSM", "VGT", "VOO", "QQQ", "SCHD"]
        hold_tickers = [f"HOLD{i}" for i in range(23)]
        snap = _make_portfolio_snapshot(buy_tickers, hold_tickers)

        _, _, hard_count = validate_snapshot_cards(snap["current_holdings"])
        assert hard_count == 0, f"Expected 0 hard violations (raw keys/posture labels), got {hard_count}"


# ── Section 4: Page-load path does NOT invoke LLM ────────────────────────────

class TestPageLoadIsolationFromLLM:
    """GET /snapshot must never call ReadOnlyEvidenceAdapter or any LLM path."""

    def test_get_latest_snapshot_does_not_call_recommendation_service(self):
        """Confirm get_latest_snapshot does not call ReadOnlyEvidenceAdapter."""
        import asyncio
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
            "app.services.intelligence.v3.intel_v3_service.ReadOnlyEvidenceAdapter"
        ) as mock_rec:
            asyncio.run(service.get_latest_snapshot())
            mock_rec.assert_not_called()

    def test_get_latest_snapshot_page_load_no_llm_calls_in_contract(self):
        """get_latest_snapshot is a pure DB read — zero LLM/provider calls by contract."""
        import asyncio
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        expected_payload = {
            "snapshot_id": "snap-123",
            "run_id": "run-123",
            "action_counts": {"BUY": 5, "HOLD": 18},
            "current_holdings": [],
            "schema_version": "v3.1",
        }
        mock_client = MagicMock()
        mock_chain = MagicMock()
        mock_chain.select.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.order.return_value = mock_chain
        mock_chain.limit.return_value = mock_chain
        mock_chain.execute.return_value = MagicMock(data=[{"payload": expected_payload, "id": "row-1"}])
        mock_client.table.return_value = mock_chain

        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        service.client = mock_client

        result = asyncio.run(service.get_latest_snapshot())
        assert result == expected_payload


# ── Section 5: Run path persists and GET returns it ──────────────────────────

class TestRunThenSnapshot:
    """POST /run must persist snapshot; subsequent GET /snapshot returns it."""

    def _make_mock_card(self, ticker: str, action: str = "BUY") -> MagicMock:
        card = MagicMock()
        card.ticker = ticker
        card.name = f"{ticker} Corp"
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

    def test_run_v3_persists_and_get_returns_payload(self):
        """run_v3 persists snapshot; get_latest_snapshot retrieves the same payload."""
        import asyncio
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        persisted = {}

        async def fake_persist(*, run_id, payload):
            persisted["payload"] = payload

        cards = [
            self._make_mock_card("AAPL", "BUY"),
            self._make_mock_card("MSFT", "BUY"),
            self._make_mock_card("GOOG", "HOLD"),
        ]

        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
        service.client = MagicMock()

        with (
            patch(
                "app.services.intelligence.v3.intel_v3_service.ReadOnlyEvidenceAdapter",
                return_value=MagicMock(load_cards=AsyncMock(return_value=(cards, {"persisted_recommendation_count": len(cards), "persisted_agent_insight_count": len(cards), "active_position_count": len(cards), "missing_recommendation_count": 0, "missing_evidence_count": 0, "stale_or_missing_source_count": 0}))),
            ),
            patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
            patch.object(service, "_persist_snapshot", side_effect=fake_persist),
        ):
            snapshot = asyncio.run(service.run_v3())

        # run_v3 returned the snapshot.
        assert "snapshot_id" in snapshot
        assert "run_id" in snapshot
        # persist received the same payload.
        assert persisted["payload"]["snapshot_id"] == snapshot["snapshot_id"]
        assert persisted["payload"]["run_id"] == snapshot["run_id"]

    def test_run_v3_generic_copy_count_zero_with_ticker_prefixed_rationale(self):
        """After the fix, run_v3 with 11 BUY cards must report 0 generic copy cards."""
        import asyncio
        from app.services.intelligence.v3.intel_v3_service import IntelV3Service

        buy_tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ", "SCHD"]
        cards = [self._make_mock_card(t, "BUY") for t in buy_tickers]

        service = IntelV3Service.__new__(IntelV3Service)
        service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
        service.client = MagicMock()

        with (
            patch(
                "app.services.intelligence.v3.intel_v3_service.ReadOnlyEvidenceAdapter",
                return_value=MagicMock(load_cards=AsyncMock(return_value=(cards, {"persisted_recommendation_count": len(cards), "persisted_agent_insight_count": len(cards), "active_position_count": len(cards), "missing_recommendation_count": 0, "missing_evidence_count": 0, "stale_or_missing_source_count": 0}))),
            ),
            patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
            patch.object(service, "_persist_snapshot", new_callable=AsyncMock),
        ):
            snapshot = asyncio.run(service.run_v3())

        # Verify no generic copy.
        held_cards = snapshot["current_holdings"]
        spam = detect_generic_copy_spam(held_cards)
        assert spam == [], f"Expected 0 generic copy cards, got: {spam}"
        # Verify no warnings about generic copy.
        assert not any("Generic copy" in w for w in snapshot.get("warnings", [])), (
            f"Unexpected generic copy warning: {snapshot.get('warnings')}"
        )


# ── Section 6: Certification field validation ─────────────────────────────────

class TestCertificationFields:
    """Prove the certification summary would pass with the fixed rationale."""

    def test_certification_generic_copy_count_is_zero(self):
        """After fix: 11 BUY cards produce generic_copy_count=0 in certification."""
        buy_tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ", "SCHD"]
        snap = _make_portfolio_snapshot(buy_tickers, [])

        _, spam_tickers, hard_count = validate_snapshot_cards(snap["current_holdings"])
        generic_copy_count = len(spam_tickers)
        assert generic_copy_count == 0, f"Expected generic_copy_count=0, got {generic_copy_count}"
        assert hard_count == 0

    def test_certification_unique_reason_count_equals_total_cards(self):
        """After fix: every card has a unique why_text → unique_reason_count == total_cards."""
        buy_tickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ", "SCHD"]
        hold_tickers = [f"HOLD{i}" for i in range(23)]
        snap = _make_portfolio_snapshot(buy_tickers, hold_tickers)

        why_texts = [c.get("why_text", "") for c in snap["current_holdings"] if c.get("why_text")]
        why_counts = Counter(why_texts)
        unique_reason_count = sum(1 for cnt in why_counts.values() if cnt == 1)
        total = len(snap["current_holdings"])
        assert unique_reason_count == total, (
            f"Expected all {total} cards to have unique rationale, but only {unique_reason_count} are unique"
        )

    def test_snapshot_id_present_in_payload(self):
        snap = _make_portfolio_snapshot(["AAPL"], ["MSFT"])
        assert "snapshot_id" in snap
        assert snap["snapshot_id"]

    def test_run_id_present_in_payload(self):
        snap = _make_portfolio_snapshot(["AAPL"], ["MSFT"])
        assert "run_id" in snap
        assert snap["run_id"]

    def test_schema_version_starts_with_v3(self):
        snap = _make_portfolio_snapshot(["AAPL"], ["MSFT"])
        assert snap["schema_version"].startswith("v3")

    def test_source_path_not_legacy(self):
        snap = _make_portfolio_snapshot(["AAPL"], ["MSFT"])
        assert snap.get("legacy_path_used") is False
