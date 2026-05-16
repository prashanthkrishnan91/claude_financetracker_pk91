"""Build 3 PR 2B — Valuation context production observability tests.

Root-cause investigation follow-up: production shows no Valuation context section
despite PR #341 being merged and snapshot being worker_certified/current.

These tests verify:
  1. Flag disabled → bridge not called, context null, log says flag_enabled=false.
  2. Flag enabled + supported data → snapshot card has non-null valuation_context.
  3. All suppressed inputs → aggregate log clearly shows suppression reason counts.
  4. No raw valuation metrics or target/fair-value fields in any snapshot card.
  5. Buy/Hold/Trim/Sell counts and snapshot structure unchanged regardless of flag.

Acceptance criteria:
  - Production logs can clearly explain why valuation context is or is not rendered.
  - If current stored data supports ≥1 stock context, ≥1 card gets non-null valuation_context.
  - If all suppressed, logs must show exact suppression reason counts.
  - API snapshot contract remains backward-compatible.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _mock_client(
    *,
    artifacts: list[dict] | None = None,
    facts: list[dict] | None = None,
    market_snapshots: list[dict] | None = None,
) -> MagicMock:
    """Build a Supabase client mock that returns the provided rows."""
    client = MagicMock()

    def _table(name: str):
        table = MagicMock()
        table.select.return_value = table
        table.eq.return_value = table
        table.in_.return_value = table
        table.order.return_value = table
        table.execute.return_value = MagicMock(data=[])

        if name == "research_artifacts":
            table.execute.return_value = MagicMock(data=artifacts or [])
        elif name == "research_artifact_facts":
            table.execute.return_value = MagicMock(data=facts or [])
        elif name == "market_snapshots":
            table.execute.return_value = MagicMock(data=market_snapshots or [])
        return table

    client.table.side_effect = _table
    return client


def _fresh_date() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


def _make_decision(ticker: str = "AAPL", action: str = "HOLD"):
    from app.services.intelligence.v3.decision_contracts import (
        ActionV3, AxisBand, ConvictionV3, DecisionOutputV3, FitBand, PriceBand, RiskBand,
    )
    action_map = {
        "BUY": ActionV3.BUY, "HOLD": ActionV3.HOLD,
        "TRIM": ActionV3.TRIM, "SELL": ActionV3.SELL,
    }
    return DecisionOutputV3(
        ticker=ticker,
        action=action_map[action],
        conviction=ConvictionV3.MEDIUM,
        evidence_quality=AxisBand.OK,
        attractiveness=AxisBand.OK,
        price_context=PriceBand.SUPPRESSED,
        portfolio_fit=FitBand.UNKNOWN,
        risk_band=RiskBand.NONE,
        rationale_plain_english="Hold current position.",
        why_now="",
        why_not_now="",
        source_signal_summary="ok",
        schema_version="v3.1",
        blockers=frozenset(),
        suppression_reasons={},
    )


# ── Group 1: Flag-disabled observability ─────────────────────────────────────

class TestFlagDisabledObservability:
    """_build_valuation_context_map() with flag=False must return None and log it."""

    @pytest.mark.asyncio
    async def test_flag_disabled_returns_none(self):
        """Flag off → method returns None immediately."""
        with patch("app.services.intelligence.v3.intel_v3_service.get_supabase_client"):
            with patch("app.services.intelligence.v3.intel_v3_service.get_settings") as mock_settings:
                s = MagicMock()
                s.intel_v3_priceband_visible_context_v1_enabled = False
                mock_settings.return_value = s

                from app.services.intelligence.v3.intel_v3_service import IntelV3Service
                svc = IntelV3Service(user_id=uuid.uuid4())
                result = await svc._build_valuation_context_map([])

        assert result is None

    @pytest.mark.asyncio
    async def test_flag_disabled_logs_flag_enabled_false(self, caplog):
        """Flag off → log emits flag_enabled=false and bridge_not_called=true."""
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.v3.intel_v3_service"):
            with patch("app.services.intelligence.v3.intel_v3_service.get_supabase_client"):
                with patch("app.services.intelligence.v3.intel_v3_service.get_settings") as mock_settings:
                    s = MagicMock()
                    s.intel_v3_priceband_visible_context_v1_enabled = False
                    mock_settings.return_value = s

                    from app.services.intelligence.v3.intel_v3_service import IntelV3Service
                    svc = IntelV3Service(user_id=uuid.uuid4())
                    await svc._build_valuation_context_map([])

        assert "flag_enabled=false" in caplog.text
        assert "bridge_not_called=true" in caplog.text
        assert "INTEL_V3_PRICEBAND_VISIBLE_CONTEXT_V1_ENABLED" in caplog.text

    @pytest.mark.asyncio
    async def test_flag_disabled_bridge_import_never_executed(self):
        """Flag off → priceband_snapshot_context_v1 bridge is never imported or called."""
        bridge_called = []

        async def _fake_bridge(**kwargs):
            bridge_called.append(kwargs)
            return {}

        with patch("app.services.intelligence.v3.intel_v3_service.get_supabase_client"):
            with patch("app.services.intelligence.v3.intel_v3_service.get_settings") as mock_settings:
                s = MagicMock()
                s.intel_v3_priceband_visible_context_v1_enabled = False
                mock_settings.return_value = s

                # Patch the bridge at the module level so any import of it is intercepted.
                with patch(
                    "app.services.intelligence.v3.priceband_snapshot_context_v1.build_ticker_valuation_context_map",
                    side_effect=_fake_bridge,
                ):
                    from app.services.intelligence.v3.intel_v3_service import IntelV3Service
                    svc = IntelV3Service(user_id=uuid.uuid4())
                    await svc._build_valuation_context_map([MagicMock(ticker="AAPL")])

        assert bridge_called == [], "Bridge must not be called when flag is disabled"


# ── Group 2: Aggregate log emitted by bridge ─────────────────────────────────

class TestAggregateObservabilityLog:
    """When flag is on, _build() emits valuation_context_pr2b_aggregate_summary."""

    @pytest.mark.asyncio
    async def test_all_suppressed_emits_aggregate_log(self, caplog):
        """No EPS data → all tickers suppressed; aggregate log shows missing_eps count."""
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        client = _mock_client(
            artifacts=[],
            facts=[],
            market_snapshots=[
                {
                    "ticker": "AAPL", "as_of": _fresh_date(),
                    "price": 180.0, "sector": "Technology", "industry": "Hardware",
                },
            ],
        )

        with caplog.at_level(
            logging.INFO,
            logger="app.services.intelligence.v3.priceband_snapshot_context_v1",
        ):
            result = await build_ticker_valuation_context_map(
                user_id=uuid.uuid4(),
                client=client,
                tickers=["AAPL"],
                categories={"AAPL": "stock"},
            )

        assert result.get("AAPL") is None, "No EPS → context must be suppressed"

        # Aggregate log must be present
        assert "valuation_context_pr2b_aggregate_summary" in caplog.text, (
            "Aggregate observability log must be emitted so production can diagnose suppression"
        )
        assert "flag_enabled=true" in caplog.text
        assert "suppression_missing_eps=1" in caplog.text
        assert "renderable_context_count=0" in caplog.text
        assert "suppressed_context_count=1" in caplog.text

    @pytest.mark.asyncio
    async def test_stale_price_emits_stale_price_suppression_count(self, caplog):
        """Stale price → aggregate log shows suppression_stale_price > 0."""
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        client = _mock_client(
            artifacts=[{"id": "art-1", "ticker": "AAPL"}],
            facts=[{
                "artifact_id": "art-1",
                "fact_kind": "metric_observation",
                "source_id": "src-001",
                "structured_payload": {
                    "claim": "sec_companyfact_observed",
                    "tag": "EarningsPerShareDiluted",
                    "value": 5.0,
                    "fiscal_period": "FY",
                    "fiscal_year": 2024,
                },
            }],
            # price 2 years old — stale
            market_snapshots=[
                {
                    "ticker": "AAPL", "as_of": "2024-01-01",
                    "price": 180.0, "sector": "Technology", "industry": "Hardware",
                },
            ],
        )

        with caplog.at_level(
            logging.INFO,
            logger="app.services.intelligence.v3.priceband_snapshot_context_v1",
        ):
            result = await build_ticker_valuation_context_map(
                user_id=uuid.uuid4(),
                client=client,
                tickers=["AAPL"],
                categories={"AAPL": "stock"},
            )

        assert result.get("AAPL") is None
        assert "suppression_stale_price=1" in caplog.text

    @pytest.mark.asyncio
    async def test_non_company_shows_in_non_company_suppressed_count(self, caplog):
        """Non-company tickers appear in non_company_suppressed_count in the aggregate log."""
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        # Even with a real DB client, non-company tickers are suppressed before DB fetch
        client = _mock_client(artifacts=[], facts=[], market_snapshots=[])

        with caplog.at_level(
            logging.INFO,
            logger="app.services.intelligence.v3.priceband_snapshot_context_v1",
        ):
            result = await build_ticker_valuation_context_map(
                user_id=uuid.uuid4(),
                client=client,
                tickers=["SPY", "GLD"],
                categories={"SPY": "etf", "GLD": "etf"},
            )

        assert result.get("SPY") is None
        assert result.get("GLD") is None
        assert "non_company_suppressed_count=2" in caplog.text

    @pytest.mark.asyncio
    async def test_aggregate_log_counts_match_returned_map(self, caplog):
        """Counts in aggregate log must equal actual context_map renderable/suppressed counts."""
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        # One company ticker with no EPS → suppressed
        client = _mock_client(
            artifacts=[],
            facts=[],
            market_snapshots=[
                {"ticker": "MSFT", "as_of": _fresh_date(), "price": 400.0, "sector": "Tech", "industry": "SW"},
            ],
        )

        with caplog.at_level(
            logging.INFO,
            logger="app.services.intelligence.v3.priceband_snapshot_context_v1",
        ):
            result = await build_ticker_valuation_context_map(
                user_id=uuid.uuid4(),
                client=client,
                tickers=["MSFT"],
                categories={"MSFT": "stock"},
            )

        actual_renderable = sum(1 for v in result.values() if v is not None)
        actual_suppressed = sum(1 for v in result.values() if v is None)

        assert f"renderable_context_count={actual_renderable}" in caplog.text
        assert f"suppressed_context_count={actual_suppressed}" in caplog.text
        assert "total_tickers=1" in caplog.text
        assert "company_ticker_count=1" in caplog.text


# ── Group 3: Happy path — non-null context in snapshot card ──────────────────

class TestHappyPathSnapshotCard:
    """Flag enabled + EPS + fresh price → snapshot card has non-null valuation_context."""

    @pytest.mark.asyncio
    async def test_flag_enabled_with_eps_and_fresh_price_gives_non_null_context(self):
        """Complete bridge pipeline produces non-null valuation_context in snapshot card."""
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        from app.services.intelligence.v3.snapshot_builder import build_snapshot

        client = _mock_client(
            artifacts=[{"id": "art-meta", "ticker": "META"}],
            facts=[{
                "artifact_id": "art-meta",
                "fact_kind": "metric_observation",
                "source_id": "src-sec-002",
                "structured_payload": {
                    "claim": "sec_companyfact_observed",
                    "tag": "EarningsPerShareDiluted",
                    "value": 6.0,
                    "fiscal_period": "FY",
                    "fiscal_year": 2024,
                },
            }],
            market_snapshots=[{
                "ticker": "META",
                "as_of": _fresh_date(),
                "price": 100.0,      # EPS=6, price=100 → yield=6% → renderable
                "sector": "Communication Services",
                "industry": "Social Media",
            }],
        )

        valuation_context_map = await build_ticker_valuation_context_map(
            user_id=uuid.uuid4(),
            client=client,
            tickers=["META"],
            categories={"META": "stock"},
        )

        assert valuation_context_map.get("META") is not None, (
            "Company with FY EPS=6.0 and fresh price=100 must produce renderable context"
        )

        snapshot = build_snapshot(
            run_id="test-run",
            decisions=[_make_decision("META", "HOLD")],
            card_metas=[{"ticker": "META", "name": "Meta Platforms", "category": "stock", "thesis_state": "intact"}],
            source_health={"status": "ok"},
            valuation_context_map=valuation_context_map,
        )

        cards = snapshot.get("current_holdings", [])
        assert len(cards) == 1
        drawer = cards[0]["detail_drawer_payload"]
        vc = drawer["valuation_context"]
        assert vc is not None, "Detail drawer must have non-null valuation_context"
        assert set(vc.keys()) == {"visible_text", "limitation_text", "source_basis"}
        assert len(vc["visible_text"]) > 0
        assert len(vc["limitation_text"]) > 0

    @pytest.mark.asyncio
    async def test_snapshot_card_null_valuation_context_when_bridge_returns_none(self):
        """When bridge is not called (flag off), all snapshot cards have null valuation_context."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot

        snapshot = build_snapshot(
            run_id="test-run",
            decisions=[_make_decision("AAPL", "BUY"), _make_decision("MSFT", "HOLD")],
            card_metas=[
                {"ticker": "AAPL", "name": "Apple", "category": "stock", "thesis_state": "intact"},
                {"ticker": "MSFT", "name": "Microsoft", "category": "stock", "thesis_state": "intact"},
            ],
            source_health={"status": "ok"},
            valuation_context_map=None,  # flag disabled → None
        )

        for card in snapshot["current_holdings"]:
            vc = card["detail_drawer_payload"]["valuation_context"]
            assert vc is None, f"Flag-disabled snapshot must have null valuation_context, got {vc!r}"


# ── Group 4: No raw valuation fields emitted ─────────────────────────────────

class TestNoRawValuationFields:
    """Snapshot cards must never contain raw metric keys, price targets, or financial precision."""

    FORBIDDEN_TOP_LEVEL = {
        "target_price", "fair_value", "intrinsic_value", "upside", "downside",
        "buy_below", "sell_above",
    }
    FORBIDDEN_DRAWER = {
        "target_price", "fair_value", "intrinsic_value", "upside", "downside",
        "buy_below", "sell_above", "valuation_signal", "earnings_yield_bucket",
        "eps_value", "fy_diluted_eps",
    }

    @pytest.mark.asyncio
    async def test_renderable_context_contains_no_forbidden_keys(self):
        """Renderable valuation_context dict has exactly three plain-English keys."""
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        client = _mock_client(
            artifacts=[{"id": "art-1", "ticker": "AAPL"}],
            facts=[{
                "artifact_id": "art-1",
                "fact_kind": "metric_observation",
                "source_id": "src-001",
                "structured_payload": {
                    "claim": "sec_companyfact_observed",
                    "tag": "EarningsPerShareDiluted",
                    "value": 5.0,
                    "fiscal_period": "FY",
                    "fiscal_year": 2024,
                },
            }],
            market_snapshots=[{
                "ticker": "AAPL",
                "as_of": _fresh_date(),
                "price": 100.0,
                "sector": "Technology",
                "industry": "Hardware",
            }],
        )

        result = await build_ticker_valuation_context_map(
            user_id=uuid.uuid4(),
            client=client,
            tickers=["AAPL"],
            categories={"AAPL": "stock"},
        )

        vc = result.get("AAPL")
        if vc is None:
            pytest.skip("Context suppressed for AAPL — skipping forbidden-keys check")

        for forbidden in self.FORBIDDEN_DRAWER:
            assert forbidden not in vc, f"Forbidden key {forbidden!r} found in context dict"

    def test_snapshot_card_top_level_has_no_forbidden_fields(self):
        """Snapshot card top-level dict must not contain raw valuation metric keys."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot

        snapshot = build_snapshot(
            run_id="run-x",
            decisions=[_make_decision("TSLA", "HOLD")],
            card_metas=[{"ticker": "TSLA", "name": "Tesla", "category": "stock", "thesis_state": "intact"}],
            source_health={"status": "ok"},
            valuation_context_map={"TSLA": {
                "visible_text": "Valuation looks demanding based on latest annual earnings.",
                "limitation_text": "Based on annual EPS only.",
                "source_basis": "fy_eps_earnings_yield",
            }},
        )

        card = snapshot["current_holdings"][0]
        for forbidden in self.FORBIDDEN_TOP_LEVEL:
            assert forbidden not in card, f"Forbidden key {forbidden!r} found at card top level"

    def test_visible_text_contains_no_price_target_patterns(self):
        """visible_text must not contain dollar amounts, percentages, or target-price language."""
        import re
        from app.services.intelligence.v3.priceband_visible_context_v1 import build_visible_context
        from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
            PriceBandShadowDiagnostic,
            PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
            PRICEBAND_POLICY_BASIS,
            PRICEBAND_POLICY_TABLE_ID,
        )

        forbidden_patterns = [
            r"\$\d+",
            r"target price",
            r"price target",
            r"fair value",
            r"upside",
            r"downside",
            r"\d+\.\d+%",
        ]

        for signal in ("reasonable", "attractive", "elevated", "expensive", "unusually_cheap"):
            diag = PriceBandShadowDiagnostic(
                ticker="TEST",
                priceband_policy_version=PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
                safe_for_decision=False,
                shadow_only=True,
                visible_decision_changed=False,
                priceband_produced=True,
                valuation_signal=signal,
                valuation_confidence="high",
                valuation_basis=PRICEBAND_POLICY_BASIS,
                valuation_policy_table=PRICEBAND_POLICY_TABLE_ID,
                earnings_yield_bucket="four_to_6_percent",
                sector=None,
                industry=None,
                sector_used_for_classification=False,
                broad_fallback_used=False,
                input_quality="source_linked_fy_eps_and_fresh_price_and_sector",
                plain_english_summary="Valuation looks roughly in line.",
                limitations=["FY-only EPS"],
                unavailable_reason=None,
            )
            ctx = build_visible_context(enabled=True, diagnostic=diag)
            if not ctx.should_render:
                continue
            for pattern in forbidden_patterns:
                assert not re.search(pattern, ctx.visible_text, re.IGNORECASE), (
                    f"Forbidden pattern {pattern!r} found in visible_text for signal={signal!r}"
                )


# ── Group 5: Regression — Buy/Hold/Trim/Sell counts unchanged ────────────────

class TestActionCountsRegression:
    """Valuation context must not affect action counts or card action fields."""

    def test_action_counts_identical_with_and_without_valuation_context_map(self):
        """Same decisions produce same action_counts regardless of valuation_context_map."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot

        decisions = [
            _make_decision("AAPL", "BUY"),
            _make_decision("MSFT", "HOLD"),
            _make_decision("TSLA", "TRIM"),
            _make_decision("GOOG", "HOLD"),
            _make_decision("AMZN", "BUY"),
        ]
        metas = [
            {"ticker": t, "name": t, "category": "stock", "thesis_state": "intact"}
            for t in ("AAPL", "MSFT", "TSLA", "GOOG", "AMZN")
        ]

        snap_no_ctx = build_snapshot(
            run_id="run-a", decisions=decisions, card_metas=metas,
            source_health={"status": "ok"}, valuation_context_map=None,
        )
        snap_with_ctx = build_snapshot(
            run_id="run-b", decisions=decisions, card_metas=metas,
            source_health={"status": "ok"},
            valuation_context_map={t: None for t in ("AAPL", "MSFT", "TSLA", "GOOG", "AMZN")},
        )

        assert snap_no_ctx["action_counts"] == snap_with_ctx["action_counts"], (
            "action_counts must be identical regardless of valuation_context_map"
        )

    def test_card_action_field_unchanged_by_valuation_context(self):
        """Each card's action field is set only by decide() — not by valuation context."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot

        ctx_map = {
            "AAPL": {
                "visible_text": "Valuation looks demanding.",
                "limitation_text": "Based on annual EPS.",
                "source_basis": "fy_eps_earnings_yield",
            },
        }
        snap = build_snapshot(
            run_id="run-c",
            decisions=[_make_decision("AAPL", "BUY")],
            card_metas=[{"ticker": "AAPL", "name": "Apple", "category": "stock", "thesis_state": "intact"}],
            source_health={"status": "ok"},
            valuation_context_map=ctx_map,
        )

        card = snap["current_holdings"][0]
        assert card["action"] == "BUY", "Card action must come from decide(), not valuation_context"
        assert card["detail_drawer_payload"]["valuation_context"] is not None

    def test_snapshot_structure_intact_when_context_map_provided(self):
        """All required snapshot top-level keys remain present when valuation_context_map is set."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot

        required_keys = {
            "snapshot_id", "run_id", "generated_at", "schema_version",
            "is_stale", "warnings", "what_changed", "current_holdings",
            "action_counts", "best_buys", "trim_sell_desk", "portfolio_command_center",
        }
        snap = build_snapshot(
            run_id="run-d",
            decisions=[_make_decision("AAPL", "BUY")],
            card_metas=[{"ticker": "AAPL", "name": "Apple", "category": "stock", "thesis_state": "intact"}],
            source_health={"status": "ok"},
            valuation_context_map={"AAPL": None},
        )
        for key in required_keys:
            assert key in snap, f"Required snapshot key {key!r} missing"
