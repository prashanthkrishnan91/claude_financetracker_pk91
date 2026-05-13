"""Tests for the canonical Evidence Artifact contract (Stage 3.0b v1 — §2 of north-star)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.intelligence.v3.evidence_artifact_contract_v1 import (
    AssetType,
    EvidenceArtifact,
    POLICY_AXIS_ACTION,
    POLICY_AXIS_CONTEXT,
    POLICY_AXIS_RISK,
    POLICY_AXIS_SIZING,
    POLICY_AXIS_PORTFOLIO_FIT,
    RateLimitStatus,
    SourceQuality,
    TrustStatus,
    map_agent_insight_row,
    map_portfolio_position,
    map_price_result,
    map_recommendation_row,
    summarize_artifact_set,
)


def _now() -> datetime:
    return datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def _ago(hours: float) -> datetime:
    return _now() - timedelta(hours=hours)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── EvidenceArtifact ──────────────────────────────────────────────────────────

class TestEvidenceArtifact:
    def test_evidence_id_is_deterministic(self):
        a1 = EvidenceArtifact(
            ticker="AAPL",
            asset_type=AssetType.STOCK,
            source_class="market_price",
            source_name="alpaca",
            evidence_type="latest_price",
            value={"mid_price": 200.0},
            produced_at=_now(),
            fetched_at=_now(),
            certified_at=_now(),
            expires_at=None,
            freshness_sla_hours=0.25,
            source_quality=SourceQuality.HIGH,
            confidence=None,
            trust_status=TrustStatus.TRUSTED,
        )
        a2 = EvidenceArtifact(
            ticker="AAPL",
            asset_type=AssetType.STOCK,
            source_class="market_price",
            source_name="alpaca",
            evidence_type="latest_price",
            value={"mid_price": 200.0},
            produced_at=_now(),
            fetched_at=_now(),
            certified_at=_now(),
            expires_at=None,
            freshness_sla_hours=0.25,
            source_quality=SourceQuality.HIGH,
            confidence=None,
            trust_status=TrustStatus.TRUSTED,
        )
        assert a1.evidence_id == a2.evidence_id
        assert len(a1.evidence_id) == 16

    def test_to_dict_serializes_datetimes_and_enums(self):
        art = map_price_result("AAPL", {"is_valid": True, "is_stale": False, "source": "alpaca", "mid_price": 200.0}, now=_now())
        d = art.to_dict()
        assert d["trust_status"] == "trusted"
        assert d["asset_type"] == "stock"
        assert isinstance(d["fetched_at"], str)
        assert "T" in d["fetched_at"]

    def test_is_inside_sla(self):
        art = EvidenceArtifact(
            ticker="AAPL",
            asset_type=AssetType.STOCK,
            source_class="analyst_thesis",
            source_name="agent_orchestrator",
            evidence_type="analyst_verdict",
            value={},
            produced_at=_ago(10), fetched_at=_ago(10), certified_at=_ago(10),
            expires_at=None,
            freshness_sla_hours=48.0,
            source_quality=SourceQuality.MEDIUM,
            confidence=None,
            trust_status=TrustStatus.TRUSTED,
        )
        assert art.is_inside_sla(now=_now()) is True
        # Past SLA window.
        art2 = EvidenceArtifact(**{**art.__dict__, "certified_at": _ago(72)})
        assert art2.is_inside_sla(now=_now()) is False

    def test_unknown_certified_at_is_not_inside_sla(self):
        art = map_price_result("AAPL", {"is_valid": False, "is_stale": True, "source": "alpaca"}, now=_now())
        assert art.is_inside_sla(now=_now()) is False

    def test_with_downgraded_trust_one_step(self):
        art = map_recommendation_row(
            {"ticker": "AAPL", "id": "r1", "action": "BUY", "conviction_score": 0.7,
             "created_at": _iso(_ago(1.0))},
            sla_hours=24.0,
            now=_now(),
        )
        assert art.trust_status == TrustStatus.TRUSTED
        d1 = art.with_downgraded_trust("test_downgrade")
        assert d1.trust_status == TrustStatus.PARTIAL
        d2 = d1.with_downgraded_trust("test_downgrade_2")
        assert d2.trust_status == TrustStatus.UNCERTIFIED
        d3 = d2.with_downgraded_trust("test_idempotent")
        assert d3.trust_status == TrustStatus.UNCERTIFIED
        # Original untouched.
        assert art.trust_status == TrustStatus.TRUSTED


# ── Mappers ───────────────────────────────────────────────────────────────────

class TestRecommendationMapper:
    def test_fresh_row_is_trusted_and_action_axis_only(self):
        art = map_recommendation_row(
            {"ticker": "nvda", "id": "r1", "action": "BUY",
             "created_at": _iso(_ago(1.0)), "conviction_score": 0.8},
            sla_hours=24.0, now=_now(),
        )
        assert art.ticker == "NVDA"
        assert art.source_class == "analyst_thesis"
        assert art.source_name == "recommendations_table"
        assert art.trust_status == TrustStatus.TRUSTED
        assert POLICY_AXIS_ACTION in art.allowed_policy_axis
        # Persisted analyst evidence must never own sizing or portfolio_fit.
        assert POLICY_AXIS_SIZING not in art.allowed_policy_axis
        assert POLICY_AXIS_PORTFOLIO_FIT not in art.allowed_policy_axis

    def test_stale_row_is_partial_or_uncertified(self):
        art = map_recommendation_row(
            {"ticker": "AAPL", "action": "HOLD",
             "created_at": _iso(_ago(72.0))},
            sla_hours=24.0, now=_now(),
        )
        # 72h / 24h SLA = 3x → still PARTIAL (≤ 4× window).
        assert art.trust_status == TrustStatus.PARTIAL

    def test_production_stale_row_is_uncertified(self):
        art = map_recommendation_row(
            {"ticker": "AAPL", "action": "BUY",
             "created_at": _iso(_ago(191.8))},
            sla_hours=24.0, now=_now(),
        )
        assert art.trust_status == TrustStatus.UNCERTIFIED


class TestAgentInsightMapper:
    def test_joined_row_picks_up_run_finished_at(self):
        insight = {"ticker": "AAPL", "analyst_verdict": {
            "action": "BUY", "conviction_level": "HIGH",
            "drivers": ["d1"], "risks": ["r1"], "risk_flag": "MEDIUM",
            "data_quality_label": "HIGH",
        }, "analyst_confidence": 0.7}
        run = {"id": "run-1", "finished_at": _iso(_ago(2.0))}
        art = map_agent_insight_row(insight, run, sla_hours=48.0, now=_now())
        assert art.ticker == "AAPL"
        assert art.value["analyst_action"] == "BUY"
        assert art.trust_status == TrustStatus.TRUSTED
        # Analyst evidence may influence action + risk + context — never sizing.
        assert POLICY_AXIS_ACTION in art.allowed_policy_axis
        assert POLICY_AXIS_RISK in art.allowed_policy_axis
        assert POLICY_AXIS_SIZING not in art.allowed_policy_axis

    def test_production_286h_old_insight_is_uncertified(self):
        insight = {"ticker": "AAPL", "analyst_verdict": {"action": "BUY"}}
        run = {"id": "run-x", "finished_at": _iso(_ago(286.1))}
        art = map_agent_insight_row(insight, run, sla_hours=48.0, now=_now())
        assert art.trust_status == TrustStatus.UNCERTIFIED


class TestPortfolioPositionMapper:
    def test_position_with_certified_at_is_trusted_and_sizing_axis(self):
        art = map_portfolio_position(
            {"ticker": "AAPL", "shares": 10, "avg_cost": 150.0,
             "market_value_usd": 2000.0, "category": "Core",
             "market_value_certified_at": _iso(_ago(0.5))},
            sla_hours=24.0, now=_now(),
        )
        assert art.trust_status == TrustStatus.TRUSTED
        assert POLICY_AXIS_SIZING in art.allowed_policy_axis
        # Portfolio state must never own action axis.
        assert POLICY_AXIS_ACTION not in art.allowed_policy_axis

    def test_missing_certified_at_is_uncertified(self):
        art = map_portfolio_position(
            {"ticker": "AAPL", "shares": 10, "avg_cost": 150.0,
             "market_value_usd": 2000.0, "category": "Core"},
            sla_hours=24.0, now=_now(),
        )
        assert art.trust_status == TrustStatus.UNCERTIFIED


class TestPriceResultMapper:
    def test_valid_fresh_result_is_trusted(self):
        art = map_price_result(
            "AAPL", {"is_valid": True, "is_stale": False, "source": "alpaca", "mid_price": 200.0},
            now=_now(),
        )
        assert art.trust_status == TrustStatus.TRUSTED
        assert art.certified_at == _now()
        assert art.source_quality == SourceQuality.HIGH

    def test_stale_result_is_partial_with_no_certified_at(self):
        art = map_price_result(
            "AAPL", {"is_valid": True, "is_stale": True, "source": "cache(alpaca)", "mid_price": 200.0},
            now=_now(),
        )
        assert art.trust_status == TrustStatus.PARTIAL
        # No fabricated freshness.
        assert art.certified_at is None

    def test_invalid_result_is_uncertified(self):
        art = map_price_result(
            "AAPL", {"is_valid": False, "is_stale": False, "source": "none", "error": "no source"},
            now=_now(),
        )
        assert art.trust_status == TrustStatus.UNCERTIFIED
        assert art.provider_error == "no source"
        assert art.source_quality == SourceQuality.LOW

    def test_rate_limit_error_sets_rate_limit_status(self):
        art = map_price_result(
            "AAPL", {"is_valid": False, "source": "polygon", "error": "rate limit exceeded"},
            now=_now(),
        )
        assert art.rate_limit_status == RateLimitStatus.LIMITED


# ── Aggregation ───────────────────────────────────────────────────────────────

class TestSummarizeArtifactSet:
    def test_per_source_class_counts(self):
        arts = [
            map_recommendation_row(
                {"ticker": "AAPL", "action": "BUY", "created_at": _iso(_ago(1.0))},
                sla_hours=24.0, now=_now(),
            ),
            map_recommendation_row(
                {"ticker": "MSFT", "action": "HOLD", "created_at": _iso(_ago(191.8))},
                sla_hours=24.0, now=_now(),
            ),
            map_price_result("AAPL", {"is_valid": True, "is_stale": False, "source": "alpaca", "mid_price": 200.0}, now=_now()),
        ]
        summary = summarize_artifact_set(arts)
        assert "analyst_thesis" in summary
        assert summary["analyst_thesis"]["count"] == 2
        assert summary["analyst_thesis"]["trusted"] == 1
        assert summary["analyst_thesis"]["uncertified"] == 1
        assert summary["market_price"]["trusted"] == 1
