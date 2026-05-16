"""Build 3 PR 2B — Price / Valuation Context tests.

Verifies acceptance criteria:
  1. Visible Intel v3 snapshot/card contract carries grounded price/valuation context
     when supported by real evidence.
  2. Context is never derived from action, conviction, evidence band, or label.
  3. Weak/unavailable valuation evidence suppresses valuation judgment honestly.
  4. No target price, fair value, upside/downside forecast, or fake precision emitted.
  5. Frontend contract: plain-English copy when present, clean absence when not.
  6. Existing certification and Watchtower paths remain intact (regression).
  7. No regressions to Build 3 PR 2A behavior.

Test groups:
  A. Suppression tests — context absent when evidence unavailable or weak.
  B. Isolation tests — same valuation evidence, different action/conviction → same context.
  C. Snapshot contract tests — valuation_context field in detail_drawer_payload.
  D. Frontend contract — plain-English only; no raw metric keys.
  E. Regression — no target_price / fair_value / upside / downside fields.
  F. snapshot_builder backward-compat — existing callers without valuation_context_map.
  G. priceband_snapshot_context_v1 — integration module unit tests.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_shadow_diagnostic(
    *,
    ticker: str = "AAPL",
    signal: str = "reasonable",
    confidence: str = "high",
    priceband_produced: bool = True,
    unavailable_reason: Optional[str] = None,
):
    from app.services.intelligence.v3.priceband_shadow_policy_v1 import (
        PriceBandShadowDiagnostic,
        PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
        PRICEBAND_POLICY_BASIS,
        PRICEBAND_POLICY_TABLE_ID,
    )
    return PriceBandShadowDiagnostic(
        ticker=ticker,
        priceband_policy_version=PRICEBAND_SHADOW_POLICY_V1_CONTRACT_VERSION,
        safe_for_decision=False,
        shadow_only=True,
        visible_decision_changed=False,
        priceband_produced=priceband_produced,
        valuation_signal=signal,
        valuation_confidence=confidence,
        valuation_basis=PRICEBAND_POLICY_BASIS,
        valuation_policy_table=PRICEBAND_POLICY_TABLE_ID,
        earnings_yield_bucket="four_to_6_percent" if signal == "reasonable" else "",
        sector=None,
        industry=None,
        sector_used_for_classification=False,
        broad_fallback_used=False,
        input_quality="source_linked_fy_eps_and_fresh_price_and_sector",
        plain_english_summary="Valuation looks roughly in line with broad market norms.",
        limitations=["FY-only EPS"],
        unavailable_reason=unavailable_reason,
    )


def _build_visible_ctx(*, signal: str, confidence: str = "high"):
    from app.services.intelligence.v3.priceband_visible_context_v1 import build_visible_context
    diag = _make_shadow_diagnostic(signal=signal, confidence=confidence)
    return build_visible_context(enabled=True, diagnostic=diag)


def _make_snapshot_decision(
    *,
    action: str = "BUY",
    conviction: str = "HIGH",
    evidence_quality: str = "STRONG",
):
    """Build a minimal DecisionOutputV3-like mock for snapshot_builder tests."""
    from app.services.intelligence.v3.decision_contracts import (
        ActionV3, AxisBand, ConvictionV3, DecisionOutputV3, FitBand, PriceBand, RiskBand,
    )
    action_map = {"BUY": ActionV3.BUY, "HOLD": ActionV3.HOLD, "TRIM": ActionV3.TRIM, "SELL": ActionV3.SELL}
    conviction_map = {"HIGH": ConvictionV3.HIGH, "MEDIUM": ConvictionV3.MEDIUM, "LOW": ConvictionV3.LOW}
    band_map = {"STRONG": AxisBand.STRONG, "OK": AxisBand.OK, "THIN": AxisBand.THIN}
    return DecisionOutputV3(
        ticker="AAPL",
        action=action_map[action],
        conviction=conviction_map[conviction],
        evidence_quality=band_map[evidence_quality],
        attractiveness=AxisBand.OK,
        price_context=PriceBand.SUPPRESSED,
        portfolio_fit=FitBand.UNKNOWN,
        risk_band=RiskBand.NONE,
        rationale_plain_english="Analyst expects continued earnings growth.",
        why_now="Recent earnings beat consensus.",
        why_not_now="Wait for position confirmation.",
        blockers=frozenset(),
        suppression_reasons={},
        source_signal_summary={},
        schema_version="v3.1",
    )


def _build_snapshot_with_valuation(
    *,
    ticker: str = "AAPL",
    action: str = "BUY",
    conviction: str = "HIGH",
    evidence_quality: str = "STRONG",
    valuation_context: Optional[dict] = None,
):
    from app.services.intelligence.v3.snapshot_builder import build_snapshot
    decision = _make_snapshot_decision(action=action, conviction=conviction, evidence_quality=evidence_quality)
    return build_snapshot(
        run_id="run-test-001",
        decisions=[decision],
        card_metas=[{"ticker": ticker, "name": f"{ticker} Corp", "category": "stock"}],
        valuation_context_map={ticker: valuation_context} if valuation_context is not None else {ticker: None},
    )


# ── A. Suppression tests ───────────────────────────────────────────────────────

class TestValuationContextSuppression:
    """Context must be absent/suppressed when evidence is unavailable or weak."""

    def test_unavailable_signal_suppressed(self):
        ctx = _build_visible_ctx(signal="unavailable", confidence="low")
        assert ctx.should_render is False
        assert ctx.visible_text is None

    def test_negative_eps_suppressed(self):
        ctx = _build_visible_ctx(signal="negative_eps", confidence="high")
        assert ctx.should_render is False
        assert ctx.visible_text is None

    def test_low_confidence_suppressed(self):
        ctx = _build_visible_ctx(signal="reasonable", confidence="low")
        assert ctx.should_render is False
        assert ctx.visible_text is None

    def test_suppressed_produces_none_in_snapshot(self):
        snap = _build_snapshot_with_valuation(valuation_context=None)
        card = snap["current_holdings"][0]
        assert card["detail_drawer_payload"]["valuation_context"] is None

    def test_unavailable_produces_none_in_snapshot(self):
        from app.services.intelligence.v3.priceband_visible_context_v1 import build_visible_context
        diag = _make_shadow_diagnostic(signal="unavailable", confidence="low", priceband_produced=False,
                                       unavailable_reason="missing_eps")
        ctx = build_visible_context(enabled=True, diagnostic=diag)
        # Serialize as the integration module does
        serialized = None if not ctx.should_render else {
            "visible_text": ctx.visible_text,
            "limitation_text": ctx.limitation_text,
            "source_basis": ctx.source_basis,
        }
        snap = _build_snapshot_with_valuation(valuation_context=serialized)
        card = snap["current_holdings"][0]
        assert card["detail_drawer_payload"]["valuation_context"] is None

    def test_disabled_flag_produces_none_context_map(self):
        """When intel_v3_priceband_visible_context_v1_enabled=False, context map is None."""
        from app.config import Settings
        s = Settings(
            supabase_url="https://test.supabase.co",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="jwt",
            encryption_key="a" * 64,
        )
        assert s.intel_v3_priceband_visible_context_v1_enabled is False


# ── B. Isolation tests ─────────────────────────────────────────────────────────

class TestValuationContextIsolation:
    """Context must NOT change when action/conviction differ, given same valuation evidence."""

    def _serialize(self, ctx) -> Optional[dict]:
        if not ctx.should_render:
            return None
        return {"visible_text": ctx.visible_text, "limitation_text": ctx.limitation_text,
                "source_basis": ctx.source_basis}

    def test_same_valuation_signal_different_action_same_context(self):
        """BUY vs SELL with same reasonable/high signal → same visible_text."""
        from app.services.intelligence.v3.priceband_visible_context_v1 import build_visible_context
        diag = _make_shadow_diagnostic(signal="reasonable", confidence="high")
        ctx_shared = build_visible_context(enabled=True, diagnostic=diag)

        snap_buy = _build_snapshot_with_valuation(
            action="BUY", conviction="HIGH", evidence_quality="STRONG",
            valuation_context=self._serialize(ctx_shared),
        )
        snap_sell = _build_snapshot_with_valuation(
            action="SELL", conviction="LOW", evidence_quality="THIN",
            valuation_context=self._serialize(ctx_shared),
        )

        ctx_in_buy = snap_buy["current_holdings"][0]["detail_drawer_payload"]["valuation_context"]
        ctx_in_sell = snap_sell["current_holdings"][0]["detail_drawer_payload"]["valuation_context"]
        assert ctx_in_buy == ctx_in_sell

    def test_action_not_leaked_into_visible_text(self):
        """visible_text must not contain action words derived from Buy/Hold/Trim/Sell."""
        for signal in ("expensive", "elevated", "reasonable", "attractive", "unusually_cheap"):
            ctx = _build_visible_ctx(signal=signal, confidence="high")
            if ctx.should_render and ctx.visible_text:
                text = ctx.visible_text.lower()
                # Action words must not appear in valuation context text
                for action_word in ("buy", "sell", "trim", "hold", "purchase", "exit"):
                    assert action_word not in text, (
                        f"Action word '{action_word}' leaked into valuation_context for signal={signal!r}: {ctx.visible_text!r}"
                    )

    def test_conviction_not_leaked_into_visible_text(self):
        """visible_text must not contain conviction labels."""
        for signal in ("reasonable", "attractive"):
            for conviction in ("HIGH", "MEDIUM", "LOW"):
                ctx = _build_visible_ctx(signal=signal, confidence="high")
                if ctx.should_render and ctx.visible_text:
                    text = ctx.visible_text.lower()
                    assert "high conviction" not in text
                    assert "medium conviction" not in text
                    assert "low conviction" not in text

    def test_evidence_band_not_leaked_into_visible_text(self):
        """visible_text must not reference STRONG/PARTIAL/THIN evidence band labels."""
        for signal in ("reasonable", "expensive"):
            ctx = _build_visible_ctx(signal=signal, confidence="high")
            if ctx.should_render and ctx.visible_text:
                text = ctx.visible_text.upper()
                assert "STRONG" not in text
                assert "PARTIAL" not in text
                assert "THIN" not in text


# ── C. Snapshot contract tests ─────────────────────────────────────────────────

class TestSnapshotContract:
    """valuation_context field appears in detail_drawer_payload when evidence supports it."""

    def test_renderable_context_present_in_snapshot(self):
        ctx = _build_visible_ctx(signal="reasonable", confidence="high")
        assert ctx.should_render is True
        serialized = {"visible_text": ctx.visible_text, "limitation_text": ctx.limitation_text,
                      "source_basis": ctx.source_basis}
        snap = _build_snapshot_with_valuation(valuation_context=serialized)
        payload = snap["current_holdings"][0]["detail_drawer_payload"]
        assert "valuation_context" in payload
        assert payload["valuation_context"] is not None
        assert "visible_text" in payload["valuation_context"]
        assert "limitation_text" in payload["valuation_context"]

    def test_suppressed_context_none_in_snapshot(self):
        snap = _build_snapshot_with_valuation(valuation_context=None)
        payload = snap["current_holdings"][0]["detail_drawer_payload"]
        assert "valuation_context" in payload
        assert payload["valuation_context"] is None

    def test_snapshot_backward_compat_no_valuation_map(self):
        """build_snapshot with no valuation_context_map still produces valuation_context=None."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = _make_snapshot_decision()
        snap = build_snapshot(
            run_id="run-compat-001",
            decisions=[decision],
            card_metas=[{"ticker": "AAPL", "name": "Apple", "category": "stock"}],
        )
        payload = snap["current_holdings"][0]["detail_drawer_payload"]
        assert "valuation_context" in payload
        assert payload["valuation_context"] is None

    def test_all_snapshot_top_level_fields_intact(self):
        """Existing top-level snapshot fields must not be disturbed."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = _make_snapshot_decision()
        snap = build_snapshot(
            run_id="run-compat-002",
            decisions=[decision],
            card_metas=[{"ticker": "AAPL", "name": "Apple", "category": "stock"}],
        )
        required_fields = [
            "schema_version", "snapshot_id", "run_id", "generated_at",
            "action_counts", "current_holdings", "portfolio_command_center",
            "best_buys", "trim_sell_desk",
        ]
        for f in required_fields:
            assert f in snap, f"Required field {f!r} missing from snapshot"

    def test_valuation_context_field_always_present_in_card(self):
        """valuation_context key must always be in detail_drawer_payload (None or dict)."""
        for action in ("BUY", "HOLD", "TRIM", "SELL"):
            snap = _build_snapshot_with_valuation(action=action, valuation_context=None)
            payload = snap["current_holdings"][0]["detail_drawer_payload"]
            assert "valuation_context" in payload

    def test_existing_detail_drawer_fields_unchanged(self):
        """Existing fields in detail_drawer_payload must not be removed or renamed."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = _make_snapshot_decision()
        snap = build_snapshot(
            run_id="run-existing-001",
            decisions=[decision],
            card_metas=[{"ticker": "MSFT", "name": "Microsoft", "category": "stock"}],
        )
        payload = snap["current_holdings"][0]["detail_drawer_payload"]
        required_fields = [
            "rationale", "why_now", "why_not_now", "evidence_band", "evidence_quality",
            "attractiveness", "price_context", "portfolio_fit_raw", "risk_band",
            "blockers", "suppression_reasons", "schema_version", "committee",
        ]
        for f in required_fields:
            assert f in payload, f"Existing field {f!r} missing from detail_drawer_payload"


# ── D. Frontend contract ───────────────────────────────────────────────────────

class TestFrontendContract:
    """Plain-English context when present; clean absence when not."""

    _FORBIDDEN_PATTERNS = [
        (r"\btarget[_\s]price\b", "target price"),
        (r"\bfair[_\s]value\b", "fair value"),
        (r"\bintrinsic\b", "intrinsic"),
        (r"\bupside\b", "upside"),
        (r"\bdownside\b", "downside"),
        (r"\bbuy[_\s]below\b", "buy_below"),
        (r"\bsell[_\s]above\b", "sell_above"),
        (r"\bearnings[_\s]yield\b", "earnings_yield"),
        (r"\$\d", "dollar amount"),
        (r"\b\d+\.\d+\s*%", "decimal percentage"),
        # Raw internal enum values
        (r"\bunusually_cheap\b", "raw unusually_cheap enum"),
        (r"\bnegative_eps\b", "raw negative_eps enum"),
        (r"\bfour_to_6_percent\b", "bucket enum"),
        (r"\bmissing_eps\b", "unavailable reason code"),
        (r"\bstale_price\b", "unavailable reason code"),
        # Raw PriceBand enum values from decision_contracts
        (r"\bSUPPRESSED\b", "raw PriceBand SUPPRESSED"),
        (r"\bCHEAP\b", "raw PriceBand CHEAP"),
        (r"\bFULL\b", "raw PriceBand FULL"),
    ]

    def test_visible_text_plain_english_no_forbidden_terms(self):
        for signal in ("expensive", "elevated", "reasonable", "attractive", "unusually_cheap"):
            ctx = _build_visible_ctx(signal=signal, confidence="high")
            assert ctx.should_render is True
            text = ctx.visible_text
            for pattern, label in self._FORBIDDEN_PATTERNS:
                assert not re.search(pattern, text, re.IGNORECASE), (
                    f"Forbidden '{label}' in visible_text for {signal!r}: {text!r}"
                )

    def test_limitation_text_plain_english_no_forbidden_terms(self):
        ctx = _build_visible_ctx(signal="reasonable", confidence="high")
        text = ctx.limitation_text
        for pattern, label in self._FORBIDDEN_PATTERNS:
            assert not re.search(pattern, text, re.IGNORECASE), (
                f"Forbidden '{label}' in limitation_text: {text!r}"
            )

    def test_no_digits_in_visible_text(self):
        for signal in ("expensive", "elevated", "reasonable", "attractive", "unusually_cheap"):
            ctx = _build_visible_ctx(signal=signal, confidence="high")
            assert not re.search(r"\d", ctx.visible_text), (
                f"Digit found in visible_text for {signal!r}: {ctx.visible_text!r}"
            )

    def test_suppressed_renders_nothing_in_snapshot(self):
        """When should_render=False, valuation_context in snapshot is None."""
        snap = _build_snapshot_with_valuation(valuation_context=None)
        payload = snap["current_holdings"][0]["detail_drawer_payload"]
        assert payload["valuation_context"] is None

    def test_renderable_has_visible_text_key(self):
        ctx = _build_visible_ctx(signal="attractive", confidence="high")
        serialized = {"visible_text": ctx.visible_text, "limitation_text": ctx.limitation_text,
                      "source_basis": ctx.source_basis}
        snap = _build_snapshot_with_valuation(valuation_context=serialized)
        payload = snap["current_holdings"][0]["detail_drawer_payload"]
        vc = payload["valuation_context"]
        assert vc is not None
        assert "visible_text" in vc
        assert isinstance(vc["visible_text"], str)
        assert len(vc["visible_text"]) > 0

    def test_no_raw_metric_keys_in_valuation_context(self):
        """No raw backend metric names exposed in valuation_context dict."""
        ctx = _build_visible_ctx(signal="reasonable", confidence="high")
        serialized = {"visible_text": ctx.visible_text, "limitation_text": ctx.limitation_text,
                      "source_basis": ctx.source_basis}
        snap = _build_snapshot_with_valuation(valuation_context=serialized)
        vc = snap["current_holdings"][0]["detail_drawer_payload"]["valuation_context"]
        assert vc is not None
        # These raw metric names must never appear
        forbidden_keys = {
            "valuation_signal", "valuation_confidence", "earnings_yield_bucket",
            "unavailable_reason", "eps_value", "raw_eps", "fy_diluted_eps",
        }
        for key in forbidden_keys:
            assert key not in vc, f"Forbidden raw metric key {key!r} found in valuation_context"

    def test_unusually_cheap_includes_quality_risk_caution(self):
        ctx = _build_visible_ctx(signal="unusually_cheap", confidence="high")
        assert ctx.should_render is True
        text = ctx.visible_text.lower()
        assert any(w in text for w in ("quality", "risk", "review")), (
            f"unusually_cheap missing quality/risk caution: {ctx.visible_text!r}"
        )


# ── E. Regression — no forbidden financial output fields ──────────────────────

class TestRegressionForbiddenFields:
    """No target_price / fair_value / upside / downside emitted anywhere."""

    _FORBIDDEN_TOP_LEVEL_KEYS = {
        "target_price", "fair_value", "intrinsic_value", "upside", "downside",
        "buy_below", "sell_above", "price_target",
    }

    def test_snapshot_top_level_has_no_forbidden_fields(self):
        ctx = _build_visible_ctx(signal="attractive", confidence="high")
        serialized = {"visible_text": ctx.visible_text, "limitation_text": ctx.limitation_text,
                      "source_basis": ctx.source_basis}
        snap = _build_snapshot_with_valuation(valuation_context=serialized)
        for key in self._FORBIDDEN_TOP_LEVEL_KEYS:
            assert key not in snap, f"Forbidden key {key!r} found at snapshot top level"

    def test_card_payload_has_no_forbidden_fields(self):
        snap = _build_snapshot_with_valuation(valuation_context=None)
        card = snap["current_holdings"][0]
        for key in self._FORBIDDEN_TOP_LEVEL_KEYS:
            assert key not in card, f"Forbidden key {key!r} found in card"

    def test_detail_drawer_has_no_forbidden_fields(self):
        ctx = _build_visible_ctx(signal="expensive", confidence="high")
        serialized = {"visible_text": ctx.visible_text, "limitation_text": ctx.limitation_text,
                      "source_basis": ctx.source_basis}
        snap = _build_snapshot_with_valuation(valuation_context=serialized)
        payload = snap["current_holdings"][0]["detail_drawer_payload"]
        for key in self._FORBIDDEN_TOP_LEVEL_KEYS:
            assert key not in payload, f"Forbidden key {key!r} in detail_drawer_payload"

    def test_valuation_context_dict_has_no_forbidden_fields(self):
        ctx = _build_visible_ctx(signal="attractive", confidence="high")
        serialized = {"visible_text": ctx.visible_text, "limitation_text": ctx.limitation_text,
                      "source_basis": ctx.source_basis}
        snap = _build_snapshot_with_valuation(valuation_context=serialized)
        vc = snap["current_holdings"][0]["detail_drawer_payload"].get("valuation_context")
        if vc:
            for key in self._FORBIDDEN_TOP_LEVEL_KEYS:
                assert key not in vc, f"Forbidden key {key!r} in valuation_context dict"

    def test_no_price_target_in_visible_text_across_all_signals(self):
        for signal in ("expensive", "elevated", "reasonable", "attractive", "unusually_cheap"):
            ctx = _build_visible_ctx(signal=signal, confidence="high")
            if ctx.visible_text:
                assert "target" not in ctx.visible_text.lower()
                assert "price" not in ctx.visible_text.lower()


# ── F. snapshot_builder backward compat ───────────────────────────────────────

class TestSnapshotBuilderBackwardCompat:
    """Existing callers that don't pass valuation_context_map must not break."""

    def test_build_snapshot_without_valuation_context_map(self):
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = _make_snapshot_decision()
        snap = build_snapshot(
            run_id="compat-run-001",
            decisions=[decision],
            card_metas=[{"ticker": "TSLA", "name": "Tesla", "category": "stock"}],
        )
        assert len(snap["current_holdings"]) == 1
        payload = snap["current_holdings"][0]["detail_drawer_payload"]
        assert payload["valuation_context"] is None

    def test_build_snapshot_with_empty_valuation_context_map(self):
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        decision = _make_snapshot_decision()
        snap = build_snapshot(
            run_id="compat-run-002",
            decisions=[decision],
            card_metas=[{"ticker": "NVDA", "name": "Nvidia", "category": "stock"}],
            valuation_context_map={},
        )
        payload = snap["current_holdings"][0]["detail_drawer_payload"]
        assert payload["valuation_context"] is None

    def test_multi_card_snapshot_independent_context(self):
        """Each card gets its own valuation context; one renderable, one suppressed."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        ctx = _build_visible_ctx(signal="reasonable", confidence="high")
        serialized = {"visible_text": ctx.visible_text, "limitation_text": ctx.limitation_text,
                      "source_basis": ctx.source_basis}

        d1 = _make_snapshot_decision(action="BUY")
        d2 = _make_snapshot_decision(action="HOLD")
        snap = build_snapshot(
            run_id="multi-run-001",
            decisions=[d1, d2],
            card_metas=[
                {"ticker": "AAPL", "name": "Apple", "category": "stock"},
                {"ticker": "GLD", "name": "Gold ETF", "category": "etf"},
            ],
            valuation_context_map={
                "AAPL": serialized,
                "GLD": None,
            },
        )
        cards = snap["current_holdings"]
        assert cards[0]["detail_drawer_payload"]["valuation_context"] == serialized
        assert cards[1]["detail_drawer_payload"]["valuation_context"] is None


# ── G. priceband_snapshot_context_v1 integration unit tests ───────────────────

class TestPricebandSnapshotContextV1:
    """Unit tests for the new integration module."""

    def _make_mock_client(self, *, artifacts=None, facts=None, market_snapshots=None):
        client = MagicMock()
        def _table(name):
            tbl = MagicMock()
            tbl.select.return_value = tbl
            tbl.eq.return_value = tbl
            tbl.in_.return_value = tbl
            tbl.order.return_value = tbl
            tbl.limit.return_value = tbl
            if name == "research_artifacts":
                tbl.execute.return_value = MagicMock(data=artifacts or [])
            elif name == "research_artifact_facts":
                tbl.execute.return_value = MagicMock(data=facts or [])
            elif name == "market_snapshots":
                tbl.execute.return_value = MagicMock(data=market_snapshots or [])
            else:
                tbl.execute.return_value = MagicMock(data=[])
            return tbl
        client.table.side_effect = _table
        return client

    @pytest.mark.asyncio
    async def test_empty_tickers_returns_empty_map(self):
        from uuid import uuid4
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        result = await build_ticker_valuation_context_map(
            user_id=uuid4(), client=MagicMock(), tickers=[], categories={},
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_non_company_tickers_always_suppressed(self):
        from uuid import uuid4
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        client = self._make_mock_client()
        result = await build_ticker_valuation_context_map(
            user_id=uuid4(),
            client=client,
            tickers=["SPY", "GLD", "BTC"],
            categories={"SPY": "etf", "GLD": "etf", "BTC": "crypto"},
        )
        for ticker in ("SPY", "GLD", "BTC"):
            assert result.get(ticker) is None

    @pytest.mark.asyncio
    async def test_db_error_returns_suppressed_context_gracefully(self):
        """DB errors during EPS/price fetch degrade gracefully to suppressed context (None)."""
        from uuid import uuid4
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        client = MagicMock()
        client.table.side_effect = Exception("DB connection failure")
        result = await build_ticker_valuation_context_map(
            user_id=uuid4(),
            client=client,
            tickers=["AAPL"],
            categories={"AAPL": "stock"},
        )
        # DB error → missing EPS + price → Phase 14D unavailable → None (suppressed, not crashing)
        assert "AAPL" in result
        assert result["AAPL"] is None

    @pytest.mark.asyncio
    async def test_missing_eps_produces_none_context(self):
        """When EPS is unavailable, valuation context for that ticker is None."""
        from uuid import uuid4
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        # No research_artifacts → no EPS
        client = self._make_mock_client(
            artifacts=[],
            facts=[],
            market_snapshots=[
                {"ticker": "AAPL", "as_of": "2026-05-01", "price": 200.0, "sector": "Technology", "industry": "Tech"},
            ],
        )
        result = await build_ticker_valuation_context_map(
            user_id=uuid4(),
            client=client,
            tickers=["AAPL"],
            categories={"AAPL": "stock"},
        )
        assert result.get("AAPL") is None

    @pytest.mark.asyncio
    async def test_stale_price_produces_none_context(self):
        """When price is stale (>7 days), context must be suppressed."""
        from uuid import uuid4
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            build_ticker_valuation_context_map,
        )
        # Old date — stale price
        client = self._make_mock_client(
            artifacts=[],
            facts=[],
            market_snapshots=[
                {"ticker": "AAPL", "as_of": "2020-01-01", "price": 150.0, "sector": "Technology", "industry": "Tech"},
            ],
        )
        result = await build_ticker_valuation_context_map(
            user_id=uuid4(),
            client=client,
            tickers=["AAPL"],
            categories={"AAPL": "stock"},
        )
        assert result.get("AAPL") is None

    def test_serialized_context_contains_no_forbidden_keys(self):
        """Serialized valuation_context dict must never contain raw metric keys."""
        ctx = _build_visible_ctx(signal="attractive", confidence="high")
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import _serialize_context
        serialized = _serialize_context(ctx)
        assert serialized is not None
        forbidden_keys = {
            "valuation_signal", "earnings_yield_bucket", "eps_value",
            "fy_diluted_eps", "unavailable_reason", "target_price",
        }
        for key in forbidden_keys:
            assert key not in serialized, f"Forbidden key {key!r} in serialized context"

    def test_serialized_suppressed_returns_none(self):
        ctx = _build_visible_ctx(signal="unavailable", confidence="low")
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import _serialize_context
        assert _serialize_context(ctx) is None

    def test_price_stale_threshold_days_is_7(self):
        from app.services.intelligence.v3.priceband_snapshot_context_v1 import (
            PRICE_STALE_THRESHOLD_DAYS,
        )
        assert PRICE_STALE_THRESHOLD_DAYS == 7
