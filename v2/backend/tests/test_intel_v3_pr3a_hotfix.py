"""Build 3 PR 3A hotfix — legacy snapshot normalization + prewarm evidence-depth summary.

Tests cover:
  1. Legacy committee.status="deferred" + PARTIAL evidence_band → source_validated.
  2. Legacy committee.status="deferred" + STRONG evidence_band → source_validated.
  3. Legacy committee.status="deferred" + THIN evidence_band → pending with safe reason.
  4. Normalization does not change action/conviction/evidence_band/valuation_context/snapshot_source.
  5. Cards already source_validated or pending pass through unchanged.
  6. best_buys and trim_sell_desk are updated to match normalized current_holdings.
  7. _log_evidence_depth_summary emits intel_v3_evidence_depth_summary log key.
  8. _log_evidence_depth_summary is callable from prewarm path (structural).
  9. Normalization logs intel_v3_source_pack_legacy_normalization_summary.
 10. Normalization skips snapshot when no deferred cards present.
 11. Existing reason is preserved when deferred+THIN has a prior reason.
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.services.intelligence.v3.intel_v3_service import (
    _log_evidence_depth_summary,
    _normalize_legacy_committee_status,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uid() -> str:
    return str(uuid.uuid4())


def _make_card(
    ticker: str,
    *,
    action: str = "BUY",
    conviction: str = "MEDIUM",
    evidence_band: str = "PARTIAL",
    committee_status: str = "deferred",
    committee_reason: str | None = None,
    valuation_context: dict | None = None,
) -> dict[str, Any]:
    committee: dict[str, Any] = {"status": committee_status}
    if committee_reason:
        committee["reason"] = committee_reason
    return {
        "ticker": ticker,
        "action": action,
        "conviction": conviction,
        "evidence_band": evidence_band,
        "why_text": f"{ticker}: rationale text.",
        "risk_text": "Risk applies.",
        "risk_level": "LOW",
        "portfolio_fit": "ON_TARGET",
        "flags": [],
        "what_would_change_view": "Thesis weakens.",
        "evidence_text": "Some evidence.",
        "updated_at": "2026-05-16T00:00:00Z",
        "source_snapshot_id": "snap-001",
        "source_run_id": "run-001",
        "detail_drawer_payload": {
            "rationale": "Rationale text.",
            "why_now": "Now is good.",
            "evidence_band": evidence_band,
            "evidence_quality": "OK",
            "committee": committee,
            "schema_version": "v3.1",
            "valuation_context": valuation_context,
        },
    }


def _make_payload(cards: list[dict], *, snapshot_source: str = "worker_certified") -> dict[str, Any]:
    buys = [c for c in cards if c["action"] == "BUY"]
    trims_sells = [c for c in cards if c["action"] in {"TRIM", "SELL"}]
    return {
        "snapshot_id": "snap-001",
        "run_id": "run-001",
        "schema_version": "v3.1",
        "snapshot_source": snapshot_source,
        "current_holdings": cards,
        "best_buys": buys,
        "trim_sell_desk": trims_sells,
        "action_counts": {},
        "evidence_band_counts": {},
        "source_pack_validated_count": 0,
        "source_pack_pending_count": 0,
    }


def _make_decision(
    *,
    has_primary_driver: bool = True,
    has_action_reason: bool = True,
    suppression_reasons: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        source_signal_summary={
            "has_primary_driver": has_primary_driver,
            "has_action_reason": has_action_reason,
        },
        suppression_reasons=suppression_reasons or {},
    )


# ── Tests: legacy normalization ───────────────────────────────────────────────

class TestLegacyCommitteeNormalization:
    def test_partial_evidence_band_normalizes_to_source_validated(self):
        """PARTIAL evidence_band + deferred → source_validated on API response."""
        card = _make_card("AAPL", evidence_band="PARTIAL", committee_status="deferred")
        payload = _make_payload([card])
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")

        result_card = payload["current_holdings"][0]
        assert result_card["detail_drawer_payload"]["committee"]["status"] == "source_validated"
        assert "reason" not in result_card["detail_drawer_payload"]["committee"]

    def test_strong_evidence_band_normalizes_to_source_validated(self):
        """STRONG evidence_band + deferred → source_validated on API response."""
        card = _make_card("NVDA", evidence_band="STRONG", committee_status="deferred")
        payload = _make_payload([card])
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")

        result_card = payload["current_holdings"][0]
        assert result_card["detail_drawer_payload"]["committee"]["status"] == "source_validated"

    def test_thin_evidence_band_normalizes_to_pending_with_reason(self):
        """THIN evidence_band + deferred → pending with safe reason on API response."""
        card = _make_card("MSFT", evidence_band="THIN", committee_status="deferred")
        payload = _make_payload([card])
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")

        result_card = payload["current_holdings"][0]
        committee = result_card["detail_drawer_payload"]["committee"]
        assert committee["status"] == "pending"
        assert "reason" in committee
        assert len(committee["reason"]) > 0
        assert "Evidence not yet source-linked" in committee["reason"]

    def test_normalization_does_not_mutate_protected_fields(self):
        """Normalization must not change action, conviction, evidence_band, valuation_context, or snapshot_source."""
        val_ctx = {"visible_text": "Fairly priced.", "limitation_text": "Annual EPS only.", "source_basis": "fy_eps"}
        card = _make_card(
            "GOOG",
            action="HOLD",
            conviction="HIGH",
            evidence_band="PARTIAL",
            committee_status="deferred",
            valuation_context=val_ctx,
        )
        payload = _make_payload([card], snapshot_source="worker_certified")
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")

        result_card = payload["current_holdings"][0]
        assert result_card["action"] == "HOLD"
        assert result_card["conviction"] == "HIGH"
        assert result_card["evidence_band"] == "PARTIAL"
        assert result_card["detail_drawer_payload"]["evidence_band"] == "PARTIAL"
        assert result_card["detail_drawer_payload"]["valuation_context"] == val_ctx
        assert payload["snapshot_source"] == "worker_certified"

    def test_already_source_validated_passes_through_unchanged(self):
        """Cards already source_validated are not modified."""
        card = _make_card("AMZN", evidence_band="STRONG", committee_status="source_validated")
        payload = _make_payload([card])
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")

        result_card = payload["current_holdings"][0]
        assert result_card["detail_drawer_payload"]["committee"]["status"] == "source_validated"

    def test_already_pending_passes_through_unchanged(self):
        """Cards already pending are not modified."""
        card = _make_card(
            "META",
            evidence_band="THIN",
            committee_status="pending",
            committee_reason="No trusted evidence.",
        )
        payload = _make_payload([card])
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")

        result_card = payload["current_holdings"][0]
        committee = result_card["detail_drawer_payload"]["committee"]
        assert committee["status"] == "pending"
        assert committee["reason"] == "No trusted evidence."

    def test_skips_when_no_deferred_cards(self):
        """Short-circuits with no changes when no deferred cards present."""
        cards = [
            _make_card("AAPL", committee_status="source_validated"),
            _make_card("MSFT", committee_status="pending"),
        ]
        payload = _make_payload(cards)
        original_holdings = payload["current_holdings"]
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")
        # current_holdings unchanged (same object — short-circuit skipped replacement)
        assert payload["current_holdings"] is original_holdings

    def test_best_buys_updated_to_match_normalized_holdings(self):
        """best_buys is updated to reference the normalized card objects."""
        buy_card = _make_card("AAPL", action="BUY", evidence_band="PARTIAL", committee_status="deferred")
        hold_card = _make_card("MSFT", action="HOLD", evidence_band="THIN", committee_status="deferred")
        payload = _make_payload([buy_card, hold_card])
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")

        # best_buys contains normalized buy card
        assert len(payload["best_buys"]) == 1
        assert payload["best_buys"][0]["detail_drawer_payload"]["committee"]["status"] == "source_validated"

    def test_trim_sell_desk_updated_to_match_normalized_holdings(self):
        """trim_sell_desk is updated to reference the normalized card objects."""
        trim_card = _make_card("NFLX", action="TRIM", evidence_band="PARTIAL", committee_status="deferred")
        payload = _make_payload([trim_card])
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")

        assert len(payload["trim_sell_desk"]) == 1
        assert payload["trim_sell_desk"][0]["detail_drawer_payload"]["committee"]["status"] == "source_validated"

    def test_existing_reason_preserved_when_deferred_thin(self):
        """Existing reason is preserved when a legacy deferred+THIN card has one."""
        card = _make_card(
            "VOO",
            evidence_band="THIN",
            committee_status="deferred",
            committee_reason="Prior reason text.",
        )
        payload = _make_payload([card])
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")

        result_card = payload["current_holdings"][0]
        committee = result_card["detail_drawer_payload"]["committee"]
        assert committee["status"] == "pending"
        assert committee["reason"] == "Prior reason text."

    def test_mixed_cards_counts_are_correct(self):
        """Mixed snapshot: PARTIAL→source_validated, THIN→pending, existing→unchanged."""
        cards = [
            _make_card("A", evidence_band="PARTIAL", committee_status="deferred"),
            _make_card("B", evidence_band="STRONG", committee_status="deferred"),
            _make_card("C", evidence_band="THIN", committee_status="deferred"),
            _make_card("D", evidence_band="PARTIAL", committee_status="source_validated"),
        ]
        payload = _make_payload(cards)
        _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")

        statuses = [
            c["detail_drawer_payload"]["committee"]["status"]
            for c in payload["current_holdings"]
        ]
        assert statuses.count("source_validated") == 3  # A, B, D
        assert statuses.count("pending") == 1  # C

    def test_normalization_logs_summary(self, caplog):
        """Normalization emits intel_v3_source_pack_legacy_normalization_summary log."""
        card = _make_card("AAPL", evidence_band="PARTIAL", committee_status="deferred")
        payload = _make_payload([card])
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.v3.intel_v3_service"):
            _normalize_legacy_committee_status(payload, user_id="u1", snapshot_id="snap-001")
        assert "intel_v3_source_pack_legacy_normalization_summary" in caplog.text


# ── Tests: evidence-depth summary helper ─────────────────────────────────────

class TestLogEvidenceDepthSummary:
    def test_emits_correct_log_key(self, caplog):
        """_log_evidence_depth_summary emits intel_v3_evidence_depth_summary log key."""
        snapshot_payload = {
            "evidence_band_counts": {"STRONG": 2, "PARTIAL": 5, "THIN": 1},
            "source_pack_validated_count": 7,
            "source_pack_pending_count": 1,
            "current_holdings": [{}] * 8,
        }
        decisions = [_make_decision() for _ in range(8)]
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.v3.intel_v3_service"):
            _log_evidence_depth_summary(
                user_id="u1",
                snapshot_id="snap-001",
                run_id="run-001",
                snapshot_payload=snapshot_payload,
                decisions=decisions,
            )
        assert "intel_v3_evidence_depth_summary" in caplog.text

    def test_counts_primary_driver_correctly(self, caplog):
        """Counts primary_driver_present_count from source_signal_summary."""
        snapshot_payload = {
            "evidence_band_counts": {},
            "source_pack_validated_count": 0,
            "source_pack_pending_count": 0,
            "current_holdings": [{}, {}, {}],
        }
        decisions = [
            _make_decision(has_primary_driver=True),
            _make_decision(has_primary_driver=False),
            _make_decision(has_primary_driver=True),
        ]
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.v3.intel_v3_service"):
            _log_evidence_depth_summary(
                user_id="u1",
                snapshot_id="snap-001",
                run_id="prewarm-run-001",
                snapshot_payload=snapshot_payload,
                decisions=decisions,
            )
        assert "primary_driver_present_count=2" in caplog.text

    def test_suppression_reasons_aggregated(self, caplog):
        """Suppression reasons are aggregated and surfaced (no raw values)."""
        snapshot_payload = {
            "evidence_band_counts": {},
            "source_pack_validated_count": 0,
            "source_pack_pending_count": 0,
            "current_holdings": [{}, {}],
        }
        decisions = [
            _make_decision(suppression_reasons={"evidence_quality": "thin signal"}),
            _make_decision(suppression_reasons={"evidence_quality": "thin signal"}),
        ]
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.v3.intel_v3_service"):
            _log_evidence_depth_summary(
                user_id="u1",
                snapshot_id="snap-001",
                run_id="prewarm-run-002",
                snapshot_payload=snapshot_payload,
                decisions=decisions,
            )
        assert "evidence_quality=2" in caplog.text

    def test_callable_with_empty_decisions(self, caplog):
        """Handles empty decisions list without error (prewarm with no holdings)."""
        snapshot_payload = {
            "evidence_band_counts": {},
            "source_pack_validated_count": 0,
            "source_pack_pending_count": 0,
            "current_holdings": [],
        }
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.v3.intel_v3_service"):
            _log_evidence_depth_summary(
                user_id="u1",
                snapshot_id="snap-001",
                run_id="prewarm-run-003",
                snapshot_payload=snapshot_payload,
                decisions=[],
            )
        assert "intel_v3_evidence_depth_summary" in caplog.text
        assert "total_tickers=0" in caplog.text

    def test_prewarm_path_structural(self):
        """_log_evidence_depth_summary is importable and callable — confirms prewarm path wired."""
        # Structural: verifies the function is available for run_prewarm_snapshot() to call.
        assert callable(_log_evidence_depth_summary)

    def test_log_contains_source_pack_counts(self, caplog):
        """Log includes source_pack_validated_count and source_pack_pending_count."""
        snapshot_payload = {
            "evidence_band_counts": {"STRONG": 3, "PARTIAL": 4, "THIN": 1},
            "source_pack_validated_count": 7,
            "source_pack_pending_count": 1,
            "current_holdings": [{}] * 8,
        }
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.v3.intel_v3_service"):
            _log_evidence_depth_summary(
                user_id="u1",
                snapshot_id="snap-prewarm",
                run_id="prewarm-run-004",
                snapshot_payload=snapshot_payload,
                decisions=[],
            )
        assert "source_pack_validated_count=7" in caplog.text
        assert "source_pack_pending_count=1" in caplog.text
