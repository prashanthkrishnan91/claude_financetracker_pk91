"""Stage 7 snapshot contract freshness gate tests.

Proves:
  1. A pre-Stage-7 certified snapshot (missing stage7_explanation_contract_version)
     is considered stale/incomplete for Stage 7 explanation UI even if analyst
     evidence is current.
  2. A snapshot with the Stage 7 contract marker and per-card evidence_explanation
     is considered fully current — no republish triggered.
  3. Run Intel (enqueue_run_v3) triggers deterministic republish without analyst
     job enqueue when only the Stage 7 snapshot contract is missing.
  4. Evidence lanes completion (republish_after_analyst_eligibility) triggers
     republish when the active snapshot lacks the Stage 7 explanation contract.
  5. compare_and_republish triggers republish when Stage 7 contract is missing
     even when evidence timestamps show no newer evidence.
  6. Action distribution and Stage 6 decision policy are unchanged by the
     contract version check (determinism contract).
  7. Stage 7 contract is not satisfied by marker alone when cards lack the
     evidence_explanation key in detail_drawer_payload.
  8. Stage 7 contract is satisfied when marker is present and all cards have
     evidence_explanation key (value may be None).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.intelligence.v3.stage7_snapshot_contract_v1 import (
    STAGE7_EXPLANATION_CONTRACT_VERSION,
    get_snapshot_stage7_version,
    is_snapshot_stage7_complete,
    is_snapshot_stage7_current,
)
from app.services.intelligence.v3.watchtower_intel_republisher_v1 import (
    PUBLISH_CERTIFIED_CURRENT,
    PUBLISH_REBUILT_AND_PUBLISHED,
    PUBLISH_SKIPPED_NO_NEW_EVIDENCE,
    compare_and_republish,
    republish_after_analyst_eligibility,
)
from app.services.intelligence.v3.evidence_mapping_version_v1 import EVIDENCE_MAPPING_VERSION


# ── Constants ─────────────────────────────────────────────────────────────────

USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")
NOW = datetime(2026, 5, 20, 8, 0, 0, tzinfo=timezone.utc)
COMMON_TS = "2026-05-20T07:00:00+00:00"  # same timestamp for intel + evidence → not newer


# ── Helpers ───────────────────────────────────────────────────────────────────

_SYNTHETIC_EX = {
    "primary_evidence_status": "LIMITED",
    "technical_signals_status": "MISSING",
    "sentiment_status": "MISSING",
    "conviction_cap_applied": True,
    "conviction_cap_reason": "ok_cap_medium",
    "safe_for_visible_decision": True,
    "safe_for_visible_decision_reason": "",
    "governance_priority": "governance_inactive",
    "corroboration_gap": True,
    "action_blocks": [],
}


def _make_held_card(
    ticker: str = "AAPL",
    *,
    with_explanation_key: bool = True,
    explanation_value: object = _SYNTHETIC_EX,
) -> dict:
    """Build a minimal held card. with_explanation_key controls Stage 7 key presence.

    Stage 7C: with_explanation_key=True now sets a non-None synthetic explanation by
    default, because the v2 contract requires evidence_explanation to be non-None.
    Pass explanation_value=None to test the old None-value case explicitly.
    """
    ddp: dict = {
        "rationale": f"{ticker} solid business.",
        "why_now": "",
        "why_not_now": "",
        "evidence_band": "PARTIAL",
        "schema_version": "v3.1",
    }
    if with_explanation_key:
        ddp["evidence_explanation"] = explanation_value
    return {
        "ticker": ticker,
        "action": "HOLD",
        "conviction": "MEDIUM",
        "detail_drawer_payload": ddp,
    }


def _make_snap_payload(
    *,
    stage7_version: Optional[str] = STAGE7_EXPLANATION_CONTRACT_VERSION,
    mapping_version: Optional[str] = EVIDENCE_MAPPING_VERSION,
    generated_at: str = COMMON_TS,
    snapshot_id: Optional[str] = None,
    holdings: Optional[list] = None,
) -> dict:
    payload: dict[str, Any] = {
        "snapshot_id": snapshot_id or str(uuid.uuid4()),
        "generated_at": generated_at,
        "snapshot_source": "worker_certified",
        "evidence_mapping_version": mapping_version,
    }
    if stage7_version is not None:
        payload["stage7_explanation_contract_version"] = stage7_version
    if holdings is not None:
        payload["current_holdings"] = holdings
    return payload


def _make_client(
    snap_payload: Optional[dict] = None,
    *,
    no_intel_snapshot: bool = False,
    portfolio_snapshot_at: Optional[str] = None,
) -> MagicMock:
    """Minimal Supabase client mock for watchtower republisher tests."""
    client = MagicMock()

    if no_intel_snapshot:
        intel_rows = []
    else:
        # Migration 024: _fetch_latest_intel_snapshot reads flat metadata columns
        # from intel_v3_snapshots (not the payload JSONB). The contract booleans
        # are pre-computed at write time; mirror that here by deriving them from
        # the test payload so stage7-incomplete payloads still trigger republish.
        sp = snap_payload or _make_snap_payload()
        intel_rows = [{
            "source_hash": "hash-test",
            "snapshot_source": sp.get("snapshot_source"),
            "payload_generated_at": sp.get("generated_at"),
            "evidence_mapping_version": sp.get("evidence_mapping_version"),
            "stage7_contract_complete": is_snapshot_stage7_complete(sp),
            "stage8e_contract_complete": True,  # Stage 8E contract not under test here
        }]
    port_at = portfolio_snapshot_at or COMMON_TS
    portfolio_rows = [{"id": str(uuid.uuid4()), "snapshot_at": port_at}]

    def _table(name: str) -> MagicMock:
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        if name == "intel_v3_snapshots":
            chain.execute.return_value = MagicMock(data=intel_rows)
        elif name == "portfolio_snapshots":
            chain.execute.return_value = MagicMock(data=portfolio_rows)
        else:
            chain.execute.return_value = MagicMock(data=[])
        return chain

    client.table = _table
    return client


def _make_republish_callable() -> AsyncMock:
    return AsyncMock(return_value={"snapshot_source": "worker_certified"})


# ── Section 1: is_snapshot_stage7_current (marker-only check) ─────────────────

class TestIsSnapshotStage7Current:
    def test_none_payload_returns_false(self):
        assert is_snapshot_stage7_current(None) is False

    def test_empty_payload_returns_false(self):
        assert is_snapshot_stage7_current({}) is False

    def test_missing_field_returns_false(self):
        """Pre-Stage-7 snapshot has no stage7_explanation_contract_version field."""
        payload = {"snapshot_id": "old", "evidence_mapping_version": EVIDENCE_MAPPING_VERSION}
        assert is_snapshot_stage7_current(payload) is False

    def test_wrong_version_returns_false(self):
        payload = {"stage7_explanation_contract_version": "old_version_v0"}
        assert is_snapshot_stage7_current(payload) is False

    def test_correct_version_returns_true(self):
        payload = {"stage7_explanation_contract_version": STAGE7_EXPLANATION_CONTRACT_VERSION}
        assert is_snapshot_stage7_current(payload) is True

    def test_get_version_none_payload(self):
        assert get_snapshot_stage7_version(None) is None

    def test_get_version_missing_field(self):
        assert get_snapshot_stage7_version({"snapshot_id": "x"}) is None

    def test_get_version_present(self):
        payload = {"stage7_explanation_contract_version": STAGE7_EXPLANATION_CONTRACT_VERSION}
        assert get_snapshot_stage7_version(payload) == STAGE7_EXPLANATION_CONTRACT_VERSION


# ── Section 2: is_snapshot_stage7_complete (marker + payload check) ───────────

class TestIsSnapshotStage7Complete:
    def test_none_payload_returns_false(self):
        assert is_snapshot_stage7_complete(None) is False

    def test_missing_marker_returns_false(self):
        """No marker → False regardless of holdings."""
        payload = {
            "snapshot_id": "old",
            "current_holdings": [_make_held_card(with_explanation_key=True)],
        }
        assert is_snapshot_stage7_complete(payload) is False

    def test_marker_and_no_holdings_returns_true(self):
        """Empty portfolio: marker alone is sufficient."""
        payload = _make_snap_payload(stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION, holdings=[])
        assert is_snapshot_stage7_complete(payload) is True

    def test_marker_and_holdings_missing_explanation_key_returns_false(self):
        """Current marker but cards lack evidence_explanation key → not Stage 7 complete.
        This is the exact production failure condition for snapshot b5b68cb8."""
        cards = [_make_held_card("AAPL", with_explanation_key=False)]
        payload = _make_snap_payload(stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION, holdings=cards)
        assert is_snapshot_stage7_complete(payload) is False

    def test_marker_and_holdings_with_explanation_key_none_returns_false(self):
        """Stage 7C: explanation key present but value=None → incomplete (v2 requires non-None).
        Old v1 snapshots with None will trigger deterministic republish to produce synthetic explanation."""
        cards = [_make_held_card("AAPL", with_explanation_key=True, explanation_value=None)]
        payload = _make_snap_payload(stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION, holdings=cards)
        assert is_snapshot_stage7_complete(payload) is False

    def test_marker_and_holdings_with_synthetic_explanation_returns_true(self):
        """Stage 7C: explanation key present with synthetic dict → complete."""
        cards = [_make_held_card("AAPL", with_explanation_key=True)]  # uses _SYNTHETIC_EX
        payload = _make_snap_payload(stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION, holdings=cards)
        assert is_snapshot_stage7_complete(payload) is True

    def test_marker_and_multiple_holdings_one_missing_key_returns_false(self):
        """Any card missing the key → not complete."""
        cards = [
            _make_held_card("AAPL", with_explanation_key=True),
            _make_held_card("MSFT", with_explanation_key=False),
            _make_held_card("GOOGL", with_explanation_key=True),
        ]
        payload = _make_snap_payload(stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION, holdings=cards)
        assert is_snapshot_stage7_complete(payload) is False

    def test_marker_and_all_holdings_with_key_returns_true(self):
        """All cards have the key → complete."""
        cards = [
            _make_held_card("AAPL", with_explanation_key=True),
            _make_held_card("MSFT", with_explanation_key=True),
        ]
        payload = _make_snap_payload(stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION, holdings=cards)
        assert is_snapshot_stage7_complete(payload) is True

    def test_pre_computed_false_flag_returns_false(self):
        """Slim republisher dict with stage7_explanation_payload_present=False → False."""
        payload = {
            "stage7_explanation_contract_version": STAGE7_EXPLANATION_CONTRACT_VERSION,
            "stage7_explanation_payload_present": False,
        }
        assert is_snapshot_stage7_complete(payload) is False

    def test_pre_computed_true_flag_returns_true(self):
        """Slim republisher dict with stage7_explanation_payload_present=True → True."""
        payload = {
            "stage7_explanation_contract_version": STAGE7_EXPLANATION_CONTRACT_VERSION,
            "stage7_explanation_payload_present": True,
        }
        assert is_snapshot_stage7_complete(payload) is True

    def test_card_without_detail_drawer_payload_not_checked(self):
        """Cards with no detail_drawer_payload are skipped (legacy/malformed card)."""
        cards = [{"ticker": "AAPL", "action": "HOLD"}]  # no detail_drawer_payload
        payload = _make_snap_payload(stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION, holdings=cards)
        assert is_snapshot_stage7_complete(payload) is True


# ── Section 3: compare_and_republish — Stage 7 gate ──────────────────────────

class TestCompareAndRepublishStage7Gate:
    @pytest.mark.asyncio
    async def test_pre_stage7_snapshot_triggers_republish(self):
        """A snapshot missing the Stage 7 marker triggers deterministic rebuild."""
        pre_stage7_payload = _make_snap_payload(stage7_version=None)
        client = _make_client(pre_stage7_payload, portfolio_snapshot_at=COMMON_TS)
        callable_ = _make_republish_callable()

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=callable_
        )

        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        callable_.assert_awaited_once_with(USER_ID)

    @pytest.mark.asyncio
    async def test_marker_present_but_cards_missing_explanation_key_triggers_republish(self):
        """Current marker but cards lack evidence_explanation key → republish.
        This is the exact production scenario for b5b68cb8."""
        cards = [_make_held_card("AAPL", with_explanation_key=False)]
        stage7_payload_missing_key = _make_snap_payload(
            stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION,
            holdings=cards,
        )
        client = _make_client(stage7_payload_missing_key, portfolio_snapshot_at=COMMON_TS)
        callable_ = _make_republish_callable()

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=callable_
        )

        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        callable_.assert_awaited_once_with(USER_ID)

    @pytest.mark.asyncio
    async def test_stage7_complete_snapshot_skips_republish(self):
        """Marker present + all cards have explanation key → no republish."""
        cards = [_make_held_card("AAPL", with_explanation_key=True)]
        stage7_payload = _make_snap_payload(
            stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION,
            holdings=cards,
        )
        client = _make_client(stage7_payload, portfolio_snapshot_at=COMMON_TS)
        callable_ = _make_republish_callable()

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=callable_
        )

        assert result.publish_status == PUBLISH_CERTIFIED_CURRENT
        callable_.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pre_stage7_snapshot_analyst_jobs_zero(self):
        """Deterministic Stage 7 recertification never enqueues analyst LLM jobs."""
        pre_stage7_payload = _make_snap_payload(stage7_version=None)
        client = _make_client(pre_stage7_payload, portfolio_snapshot_at=COMMON_TS)
        callable_ = _make_republish_callable()

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=callable_
        )

        assert result.analyst_jobs_queued == 0

    @pytest.mark.asyncio
    async def test_missing_explanation_key_analyst_jobs_zero(self):
        """Republish for missing explanation key also queues zero analyst jobs."""
        cards = [_make_held_card("AAPL", with_explanation_key=False)]
        payload = _make_snap_payload(stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION, holdings=cards)
        client = _make_client(payload, portfolio_snapshot_at=COMMON_TS)
        callable_ = _make_republish_callable()

        result = await compare_and_republish(
            USER_ID, client, intel_republish_callable=callable_
        )

        assert result.analyst_jobs_queued == 0


# ── Section 4: republish_after_analyst_eligibility — Stage 7 gate ─────────────

class TestRepublishAfterAnalystEligibilityStage7:
    @pytest.mark.asyncio
    async def test_pre_stage7_snapshot_triggers_republish_after_evidence_lanes(self):
        """After evidence lanes complete, pre-Stage-7 snapshot triggers republish."""
        pre_stage7_payload = _make_snap_payload(stage7_version=None)
        client = _make_client(pre_stage7_payload)
        callable_ = _make_republish_callable()
        evidence_ts = NOW - timedelta(hours=1)

        result = await republish_after_analyst_eligibility(
            USER_ID,
            client,
            intel_republish_callable=callable_,
            latest_evidence_at=evidence_ts,
            now=NOW,
        )

        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        callable_.assert_awaited_once_with(USER_ID)

    @pytest.mark.asyncio
    async def test_marker_present_missing_explanation_key_triggers_republish(self):
        """Marker present but cards missing explanation key → republish after evidence lanes."""
        cards = [_make_held_card("AAPL", with_explanation_key=False)]
        payload = _make_snap_payload(stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION, holdings=cards)
        client = _make_client(payload)
        callable_ = _make_republish_callable()
        evidence_ts = NOW - timedelta(hours=1)

        result = await republish_after_analyst_eligibility(
            USER_ID,
            client,
            intel_republish_callable=callable_,
            latest_evidence_at=evidence_ts,
            now=NOW,
        )

        assert result.publish_status == PUBLISH_REBUILT_AND_PUBLISHED
        callable_.assert_awaited_once_with(USER_ID)

    @pytest.mark.asyncio
    async def test_stage7_complete_snapshot_skips_republish_when_no_new_evidence(self):
        """Stage 7 complete snapshot with no newer evidence is skipped."""
        cards = [_make_held_card("AAPL", with_explanation_key=True)]
        stage7_payload = _make_snap_payload(
            stage7_version=STAGE7_EXPLANATION_CONTRACT_VERSION,
            generated_at=NOW.isoformat(),
            holdings=cards,
        )
        client = _make_client(stage7_payload)
        callable_ = _make_republish_callable()
        evidence_ts = NOW - timedelta(hours=2)  # older than snapshot

        result = await republish_after_analyst_eligibility(
            USER_ID,
            client,
            intel_republish_callable=callable_,
            latest_evidence_at=evidence_ts,
            now=NOW,
        )

        assert result.publish_status == PUBLISH_SKIPPED_NO_NEW_EVIDENCE
        callable_.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stage7_republish_analyst_jobs_queued_zero(self):
        """Stage 7 contract republish (any trigger) never enqueues analyst jobs."""
        pre_stage7_payload = _make_snap_payload(stage7_version=None)
        client = _make_client(pre_stage7_payload)
        callable_ = _make_republish_callable()
        evidence_ts = NOW - timedelta(hours=1)

        result = await republish_after_analyst_eligibility(
            USER_ID,
            client,
            intel_republish_callable=callable_,
            latest_evidence_at=evidence_ts,
            now=NOW,
        )

        assert result.analyst_jobs_queued == 0


# ── Section 5: snapshot_builder emits Stage 7 contract version ────────────────

class TestSnapshotBuilderEmitsStage7Contract:
    def test_build_snapshot_includes_stage7_contract_version(self):
        """build_snapshot() must emit stage7_explanation_contract_version."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        from app.services.intelligence.v3.decision_contracts import (
            ActionV3, AxisBand, ConvictionV3, DecisionOutputV3, FitBand, PriceBand, RiskBand,
        )

        decision = DecisionOutputV3(
            ticker="AAPL",
            action=ActionV3.HOLD,
            conviction=ConvictionV3.MEDIUM,
            evidence_quality=AxisBand.OK,
            attractiveness=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.ON_TARGET,
            risk_band=RiskBand.LOW,
            rationale_plain_english="Solid business at fair value.",
            why_now="",
            why_not_now="",
            blockers=[],
            suppression_reasons={},
            source_signal_summary={},
            schema_version="v3.1",
        )
        meta = {"ticker": "AAPL", "name": "Apple Inc.", "category": "stock", "thesis_state": "intact"}

        payload = build_snapshot(
            run_id="test-run-001",
            decisions=[decision],
            card_metas=[meta],
        )

        assert "stage7_explanation_contract_version" in payload
        assert payload["stage7_explanation_contract_version"] == STAGE7_EXPLANATION_CONTRACT_VERSION
        assert is_snapshot_stage7_current(payload) is True

    def test_new_snapshot_is_immediately_stage7_complete(self):
        """A freshly built snapshot satisfies is_snapshot_stage7_complete."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        from app.services.intelligence.v3.decision_contracts import (
            ActionV3, AxisBand, ConvictionV3, DecisionOutputV3, FitBand, PriceBand, RiskBand,
        )

        decision = DecisionOutputV3(
            ticker="MSFT",
            action=ActionV3.BUY,
            conviction=ConvictionV3.HIGH,
            evidence_quality=AxisBand.STRONG,
            attractiveness=AxisBand.OK,
            price_context=PriceBand.FAIR,
            portfolio_fit=FitBand.UNDERWEIGHT,
            risk_band=RiskBand.LOW,
            rationale_plain_english="Strong earnings growth.",
            why_now="Recent pullback.",
            why_not_now="",
            blockers=[],
            suppression_reasons={},
            source_signal_summary={},
            schema_version="v3.1",
        )
        meta = {"ticker": "MSFT", "name": "Microsoft Corp.", "category": "stock", "thesis_state": "intact"}

        payload = build_snapshot(
            run_id="test-run-002",
            decisions=[decision],
            card_metas=[meta],
        )

        # Full contract check: marker + evidence_explanation key in all cards.
        assert is_snapshot_stage7_complete(payload) is True
        for card in payload["current_holdings"]:
            ddp = card.get("detail_drawer_payload", {})
            assert "evidence_explanation" in ddp


# ── Section 6: Action distribution unchanged ─────────────────────────────────

class TestActionDistributionUnchangedByContractCheck:
    def test_contract_check_does_not_alter_action_distribution(self):
        """The Stage 7 contract version check is metadata-only and must not affect
        the BUY/HOLD/TRIM/SELL distribution produced by build_snapshot."""
        from app.services.intelligence.v3.snapshot_builder import build_snapshot
        from app.services.intelligence.v3.decision_contracts import (
            ActionV3, AxisBand, ConvictionV3, DecisionOutputV3, FitBand, PriceBand, RiskBand,
        )

        def _dec(ticker: str, action: ActionV3) -> DecisionOutputV3:
            return DecisionOutputV3(
                ticker=ticker,
                action=action,
                conviction=ConvictionV3.MEDIUM,
                evidence_quality=AxisBand.OK,
                attractiveness=AxisBand.OK,
                price_context=PriceBand.FAIR,
                portfolio_fit=FitBand.ON_TARGET,
                risk_band=RiskBand.LOW,
                rationale_plain_english=f"Rationale for {ticker}.",
                why_now="",
                why_not_now="",
                blockers=[],
                suppression_reasons={},
                source_signal_summary={},
                schema_version="v3.1",
            )

        decisions = [
            _dec("AAPL", ActionV3.HOLD),
            _dec("MSFT", ActionV3.BUY),
            _dec("GOOGL", ActionV3.HOLD),
            _dec("NVDA", ActionV3.TRIM),
        ]
        metas = [
            {"ticker": d.ticker, "name": d.ticker, "category": "stock", "thesis_state": "intact"}
            for d in decisions
        ]

        payload = build_snapshot(run_id="test-run-003", decisions=decisions, card_metas=metas)

        # Full contract satisfied
        assert is_snapshot_stage7_complete(payload) is True
        # Action counts unchanged
        assert payload["action_counts"]["HOLD"] == 2
        assert payload["action_counts"]["BUY"] == 1
        assert payload["action_counts"]["TRIM"] == 1
        assert "SELL" not in payload["action_counts"]
        # No action other than BUY/HOLD/TRIM/SELL
        for card in payload["current_holdings"]:
            assert card["action"] in {"BUY", "HOLD", "TRIM", "SELL"}
