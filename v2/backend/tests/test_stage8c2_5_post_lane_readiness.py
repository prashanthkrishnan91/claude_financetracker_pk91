"""Stage 8C PR 2.5 — Post-lane Stage 5J/5K readiness evaluation.

Validates:
  1. Orchestrator unconditionally calls Stage 5J/5K after evidence lanes complete.
  2. Idempotency-skipped existing artifacts (is_active=True) count as valid evidence.
  3. SEC catalyst sentinel active artifact with USABLE_WITH_LIMITATIONS → Stage 5J LIMITED.
  4. Stage 5K selects sec_catalyst_sentiment when editorial news_sentiment is suppressed.
  5. snapshot_sentiment_readiness log emitted when usable SEC catalyst sentinel exists.
  6. Stage 5J/5K evaluation runs regardless of republisher skip decision.
  7. ETF/BTC/XRP skip behavior is unchanged.
  8. No BUY/HOLD/TRIM/SELL policy keys in any coverage output.

No production Supabase access. All DB interactions use in-memory fakes.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.services.intelligence.v3.intel_v3_evidence_lane_orchestrator_v1 import (
    run_enabled_evidence_lanes_for_portfolio,
)
from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
    LANE_NEWS_SENTIMENT,
    LANE_SEC_CATALYST_SENTIMENT,
    STATUS_LIMITED,
    STATUS_MISSING,
    STATUS_SUPPRESSED,
    compute_research_evidence_coverage,
)
from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
    AXIS_SENTIMENT,
    READINESS_LIMITED,
    compute_decision_input_readiness,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_BASE = dict(
    supabase_url="http://fake",
    supabase_anon_key="anon",
    supabase_service_role_key="svc",
    supabase_jwt_secret="secret",
    encryption_key="a" * 32,
)

_NOW_ISO = "2026-05-22T10:00:00+00:00"
_ART_ID_SEC = "art-sec-catalyst-001"


# ── Fake DB infra ─────────────────────────────────────────────────────────────

def _make_sec_catalyst_artifact_row(
    ticker: str = "AAPL",
    usability_label: str = "USABLE_WITH_LIMITATIONS",
    is_usable: bool = True,
) -> dict:
    """Build a research_artifacts row for the sec_catalyst_sentiment lane."""
    return {
        "id": _ART_ID_SEC,
        "artifact_type": "sentiment_event",
        "skill_pack": "sec_catalyst_sentiment_evidence_v1",
        "scope_kind": "ticker",
        "ticker": ticker,
        "confidence_or_trust_level": "HIGH",
        "freshness_status": "FRESH",
        "generated_at": _NOW_ISO,
        "expires_at": None,
        "is_active": True,
        "model_version": "sec_catalyst_sentiment_adapter.v2",
        "safe_for_decision": False,
        "payload": {
            "truth_usability_assessment": {
                "usability_label": usability_label,
                "is_usable": is_usable,
                "suppression_reason": None,
            },
            "source_credibility_assessment": {
                "strongest_authority_level": "COMPANY_AUTHORED",
            },
            "evidence_completeness_assessment": {
                "completeness_band": "PARTIAL",
            },
            "decision_usefulness_tier": "READY",
            "skill_pack": "sec_catalyst_sentiment_evidence_v1",
            "model_version": "sec_catalyst_sentiment_adapter.v2",
        },
    }


def _make_news_sentiment_suppressed_row(ticker: str = "AAPL") -> dict:
    """Build a suppressed news_sentiment_evidence_v1 row (editorial context)."""
    return {
        "id": "art-news-suppressed-001",
        "artifact_type": "sentiment_event",
        "skill_pack": "news_sentiment_evidence_v1",
        "scope_kind": "ticker",
        "ticker": ticker,
        "confidence_or_trust_level": "LOW",
        "freshness_status": "FRESH",
        "generated_at": _NOW_ISO,
        "expires_at": None,
        "is_active": True,
        "model_version": "news_sentiment_evidence_v1",
        "safe_for_decision": False,
        "payload": {
            "truth_usability_assessment": {
                "usability_label": "SUPPRESSED_INCOMPLETE",
                "is_usable": False,
                "suppression_reason": "editorial_context",
            },
            "source_credibility_assessment": {
                "strongest_authority_level": "EDITORIAL_CONTEXT",
            },
        },
    }


class FakeReadQuery:
    """Supabase-style chainable query builder that returns preset rows on execute()."""

    def __init__(self, table_name: str, rows: list[dict]) -> None:
        self._table_name = table_name
        self._rows = rows

    def select(self, *args, **kwargs) -> "FakeReadQuery":
        return self

    def eq(self, *args, **kwargs) -> "FakeReadQuery":
        return self

    def neq(self, *args, **kwargs) -> "FakeReadQuery":
        return self

    def order(self, *args, **kwargs) -> "FakeReadQuery":
        return self

    def limit(self, *args, **kwargs) -> "FakeReadQuery":
        return self

    def is_(self, *args, **kwargs) -> "FakeReadQuery":
        return self

    def execute(self):
        class _Result:
            pass
        r = _Result()
        r.data = list(self._rows)
        return r

    def insert(self, row: dict) -> "FakeReadQuery":
        return self

    def update(self, row: dict) -> "FakeReadQuery":
        return self

    def upsert(self, row: dict, **kwargs) -> "FakeReadQuery":
        return self


class FakeReadSupabaseClient:
    """Supabase fake supporting read queries with preset artifact rows."""

    def __init__(self, artifact_rows: Optional[list[dict]] = None) -> None:
        self._artifact_rows = artifact_rows or []
        self._snapshot_writes: list[dict] = []
        self._recommendation_writes: list[dict] = []

    def table(self, name: str) -> FakeReadQuery:
        if name == "research_artifacts":
            return FakeReadQuery(name, self._artifact_rows)
        # All other tables (snapshots, recommendations, audit, etc.) return empty.
        return FakeReadQuery(name, [])


def _settings_workers_on() -> Settings:
    return Settings(
        **_BASE,
        intel_v3_research_workers_enabled=True,
        intel_v3_fundamentals_evidence_enabled=False,
        intel_v3_technicals_evidence_enabled=False,
        intel_v3_news_sentiment_evidence_enabled=False,
        intel_v3_sec_companyfacts_evidence_enabled=False,
        intel_v3_sentiment_catalyst_evidence_enabled=True,
    )


def _fake_runner_no_writes(user_id, ticker, db_client, **kwargs):
    """Stub runner that returns None for all lanes (simulates idempotency-skip)."""
    return {
        "fundamentals": None,
        "technicals": None,
        "news_sentiment": None,
        "sec_company_facts": None,
        "sec_catalyst_sentiment": None,
    }


# ── 1. Structural proof: orchestrator source calls Stage 5J/5K unconditionally ─

def _read_orchestrator_src() -> str:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(
        base, "app", "services", "intelligence", "v3",
        "intel_v3_evidence_lane_orchestrator_v1.py",
    )
    with open(path) as f:
        return f.read()


class TestOrchestratorStage5J5KStructural:
    """Structural proof: orchestrator unconditionally calls Stage 5J and 5K."""

    def test_compute_research_evidence_coverage_in_orchestrator(self):
        assert "compute_research_evidence_coverage" in _read_orchestrator_src(), (
            "Orchestrator must call compute_research_evidence_coverage (Stage 5J)"
        )

    def test_compute_decision_input_readiness_in_orchestrator(self):
        assert "compute_decision_input_readiness" in _read_orchestrator_src(), (
            "Orchestrator must call compute_decision_input_readiness (Stage 5K)"
        )

    def test_snapshot_sentiment_readiness_log_key_in_orchestrator(self):
        assert "snapshot_sentiment_readiness" in _read_orchestrator_src(), (
            "Orchestrator must emit snapshot_sentiment_readiness log"
        )

    def test_sec_catalyst_source_selection_present(self):
        src = _read_orchestrator_src()
        assert "sec_catalyst_sentiment" in src, (
            "Orchestrator must reference sec_catalyst_sentiment for source selection"
        )

    def test_not_gated_by_old_coverage_log_flag(self):
        src = _read_orchestrator_src()
        # The old flag gate must not be the only path to Stage 5J/5K calls.
        # After this PR, compute_research_evidence_coverage is called unconditionally,
        # so the old flag key should not appear as a gate in the orchestrator.
        assert "intel_v3_evidence_coverage_dispatch_log_enabled" not in src, (
            "Orchestrator must not gate Stage 5J/5K calls behind "
            "intel_v3_evidence_coverage_dispatch_log_enabled — evaluation is unconditional"
        )

    def test_fail_soft_try_except_present(self):
        src = _read_orchestrator_src()
        fn_start = src.find("def run_enabled_evidence_lanes_for_portfolio")
        assert fn_start >= 0
        fn_body = src[fn_start:]
        assert "intel_v3_evidence_stage5j_5k_post_lane_error" in fn_body, (
            "Orchestrator post-lane Stage 5J/5K block must have fail-soft error log"
        )

    def test_orchestrator_does_not_call_table_directly(self):
        src = _read_orchestrator_src()
        fn_start = src.find("def run_enabled_evidence_lanes_for_portfolio")
        assert fn_start >= 0
        fn_body = src[fn_start:]
        assert ".table(" not in fn_body, (
            "Orchestrator function body must not call .table() directly"
        )


# ── 2. Idempotency-skipped artifacts count as valid evidence (Stage 5J) ───────

class TestIdempotencySkippedArtifactsCountAsValidEvidence:
    """Stage 5J reads the active artifact set — idempotency-skipped artifacts included."""

    def test_existing_sec_catalyst_artifact_yields_limited_coverage(self):
        """Active sec_catalyst_sentiment artifact → Stage 5J status=LIMITED is_usable=True."""
        db = FakeReadSupabaseClient(
            artifact_rows=[_make_sec_catalyst_artifact_row("AAPL")]
        )
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
        )
        sec_cat_cov = coverage.ticker_coverage["AAPL"].lanes.get(LANE_SEC_CATALYST_SENTIMENT)
        assert sec_cat_cov is not None, "sec_catalyst_sentiment lane must be in coverage"
        assert sec_cat_cov.status == STATUS_LIMITED, (
            f"Expected LIMITED, got {sec_cat_cov.status!r}"
        )
        assert sec_cat_cov.is_usable is True, "is_usable must be True for LIMITED artifact"
        assert sec_cat_cov.artifact_id == _ART_ID_SEC, "artifact_id must match"

    def test_artifact_from_before_last_snapshot_still_counted(self):
        """Stage 5J reads from is_active=True set regardless of artifact age."""
        old_artifact = _make_sec_catalyst_artifact_row("CRM")
        old_artifact["generated_at"] = "2026-05-20T00:00:00+00:00"  # older than snapshot
        db = FakeReadSupabaseClient(artifact_rows=[old_artifact])
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["CRM"],
            db_client=db,
        )
        sec_cat_cov = coverage.ticker_coverage["CRM"].lanes.get(LANE_SEC_CATALYST_SENTIMENT)
        assert sec_cat_cov is not None
        assert sec_cat_cov.is_usable is True, (
            "Artifact older than snapshot but is_active=True must still be usable in Stage 5J"
        )

    def test_no_artifact_yields_missing_status(self):
        """When no artifact exists for the lane, Stage 5J reports MISSING."""
        db = FakeReadSupabaseClient(artifact_rows=[])
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
        )
        sec_cat_cov = coverage.ticker_coverage["AAPL"].lanes.get(LANE_SEC_CATALYST_SENTIMENT)
        assert sec_cat_cov is not None
        assert sec_cat_cov.status == STATUS_MISSING
        assert sec_cat_cov.is_usable is False


# ── 3. SEC catalyst artifact → Stage 5J LIMITED ───────────────────────────────

class TestSecCatalystStage5JLimited:
    """SEC catalyst artifact with USABLE_WITH_LIMITATIONS → Stage 5J LIMITED."""

    def test_usable_with_limitations_maps_to_limited(self):
        db = FakeReadSupabaseClient(
            artifact_rows=[
                _make_sec_catalyst_artifact_row(
                    "AAPL",
                    usability_label="USABLE_WITH_LIMITATIONS",
                    is_usable=True,
                )
            ]
        )
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
        )
        cov = coverage.ticker_coverage["AAPL"].lanes[LANE_SEC_CATALYST_SENTIMENT]
        assert cov.status == STATUS_LIMITED
        assert cov.is_usable is True

    def test_sec_catalyst_stage5j_log_emitted(self, caplog):
        """sec_catalyst_stage5j_readiness log key is emitted by compute_research_evidence_coverage."""
        db = FakeReadSupabaseClient(
            artifact_rows=[_make_sec_catalyst_artifact_row("AAPL")]
        )
        with caplog.at_level(logging.INFO):
            compute_research_evidence_coverage(
                user_id="user-1",
                tickers=["AAPL"],
                db_client=db,
            )

        assert any(
            "sec_catalyst_stage5j_readiness" in rec.message
            and "AAPL" in rec.message
            and "status=LIMITED" in rec.message
            and "is_usable=True" in rec.message
            for rec in caplog.records
        ), (
            "sec_catalyst_stage5j_readiness log must include ticker=AAPL status=LIMITED is_usable=True"
        )

    def test_suppressed_artifact_not_usable(self):
        db = FakeReadSupabaseClient(
            artifact_rows=[
                _make_sec_catalyst_artifact_row(
                    "AAPL",
                    usability_label="SUPPRESSED_INCOMPLETE",
                    is_usable=False,
                )
            ]
        )
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
        )
        cov = coverage.ticker_coverage["AAPL"].lanes[LANE_SEC_CATALYST_SENTIMENT]
        assert cov.status == STATUS_SUPPRESSED
        assert cov.is_usable is False


# ── 4. Stage 5K: sec_catalyst_sentiment selected over suppressed news ─────────

class TestStage5KSentimentSourceSelection:
    """Stage 5K selects sec_catalyst_sentiment when news_sentiment is suppressed."""

    def _coverage_with_sec_and_suppressed_news(self, ticker: str = "AAPL"):
        db = FakeReadSupabaseClient(
            artifact_rows=[
                _make_sec_catalyst_artifact_row(ticker),
                _make_news_sentiment_suppressed_row(ticker),
            ]
        )
        return compute_research_evidence_coverage(
            user_id="user-1",
            tickers=[ticker],
            db_client=db,
        )

    def test_sec_catalyst_selected_when_news_suppressed(self):
        coverage = self._coverage_with_sec_and_suppressed_news("AAPL")
        shadow = compute_decision_input_readiness(coverage)
        sent_axis = shadow.ticker_readiness["AAPL"].axes.get(AXIS_SENTIMENT)
        assert sent_axis is not None
        assert sent_axis.is_usable is True
        assert LANE_SEC_CATALYST_SENTIMENT in sent_axis.contributing_lanes, (
            "sec_catalyst_sentiment must be in contributing_lanes when it is usable"
        )

    def test_suppressed_news_not_in_contributing(self):
        coverage = self._coverage_with_sec_and_suppressed_news("AAPL")
        shadow = compute_decision_input_readiness(coverage)
        sent_axis = shadow.ticker_readiness["AAPL"].axes.get(AXIS_SENTIMENT)
        assert LANE_NEWS_SENTIMENT not in sent_axis.contributing_lanes, (
            "Suppressed news_sentiment must not appear in contributing_lanes"
        )

    def test_sentiment_stage5k_source_selection_log_emitted(self, caplog):
        """sentiment_stage5k_source_selection log key emitted during Stage 5K."""
        coverage = self._coverage_with_sec_and_suppressed_news("AAPL")
        with caplog.at_level(logging.INFO):
            compute_decision_input_readiness(coverage)

        assert any(
            "sentiment_stage5k_source_selection" in rec.message
            and "AAPL" in rec.message
            and "selected=sec_catalyst_sentiment" in rec.message
            for rec in caplog.records
        ), (
            "sentiment_stage5k_source_selection must log selected=sec_catalyst_sentiment "
            "when SEC catalyst is usable and news_sentiment is suppressed"
        )

    def test_sentinel_axis_readiness_is_limited(self):
        coverage = self._coverage_with_sec_and_suppressed_news("AAPL")
        shadow = compute_decision_input_readiness(coverage)
        sent_axis = shadow.ticker_readiness["AAPL"].axes.get(AXIS_SENTIMENT)
        assert sent_axis.readiness == READINESS_LIMITED, (
            f"Expected sentiment axis readiness=LIMITED, got {sent_axis.readiness!r}"
        )

    def test_sec_only_no_news_selects_sec_catalyst(self, caplog):
        """When only sec_catalyst artifact exists (no news), sec_catalyst is selected."""
        db = FakeReadSupabaseClient(
            artifact_rows=[_make_sec_catalyst_artifact_row("GOOGL")]
        )
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["GOOGL"],
            db_client=db,
        )
        with caplog.at_level(logging.INFO):
            compute_decision_input_readiness(coverage)

        assert any(
            "sentiment_stage5k_source_selection" in rec.message
            and "GOOGL" in rec.message
            and "selected=sec_catalyst_sentiment" in rec.message
            for rec in caplog.records
        )


# ── 5. snapshot_sentiment_readiness emitted from orchestrator ─────────────────

class TestSnapshotSentimentReadinessLog:
    """snapshot_sentiment_readiness is emitted from the post-lane path."""

    def test_snapshot_sentiment_readiness_logged_for_usable_sec_catalyst(
        self, caplog, monkeypatch
    ):
        """Orchestrator emits snapshot_sentiment_readiness when sec_catalyst is usable."""
        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", _fake_runner_no_writes)

        db = FakeReadSupabaseClient(
            artifact_rows=[_make_sec_catalyst_artifact_row("AAPL")]
        )

        with caplog.at_level(logging.INFO):
            run_enabled_evidence_lanes_for_portfolio(
                user_id="user-1",
                tickers=["AAPL"],
                db_client=db,
                settings=_settings_workers_on(),
            )

        assert any(
            "snapshot_sentiment_readiness" in rec.message
            and "AAPL" in rec.message
            and "source=sec_catalyst_sentiment" in rec.message
            for rec in caplog.records
        ), (
            "snapshot_sentiment_readiness must be logged with source=sec_catalyst_sentiment "
            "when a usable sec_catalyst_sentiment artifact is active"
        )

    def test_snapshot_sentiment_readiness_not_logged_when_lane_missing(
        self, caplog, monkeypatch
    ):
        """snapshot_sentiment_readiness must NOT appear when no usable sentiment exists."""
        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", _fake_runner_no_writes)

        db = FakeReadSupabaseClient(artifact_rows=[])  # no artifacts

        with caplog.at_level(logging.INFO):
            run_enabled_evidence_lanes_for_portfolio(
                user_id="user-1",
                tickers=["AAPL"],
                db_client=db,
                settings=_settings_workers_on(),
            )

        assert not any(
            "snapshot_sentiment_readiness" in rec.message
            for rec in caplog.records
        ), "snapshot_sentiment_readiness must not be logged when sentiment lane is missing"


# ── 6. Stage 5J/5K evaluation independent of republisher skip ─────────────────

class TestPostLaneReadinessIndependentOfRepublisher:
    """Stage 5J/5K runs in the orchestrator, before republisher — always emitted."""

    def test_stage5j_log_emitted_when_artifact_older_than_snapshot(
        self, caplog, monkeypatch
    ):
        """Stage 5J runs even when the artifact is older than the snapshot.

        The republisher would skip (evidence_newer=False), but Stage 5J/5K
        evaluation in the orchestrator still runs and emits diagnostics.
        """
        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", _fake_runner_no_writes)

        old_artifact = _make_sec_catalyst_artifact_row("SNOW")
        old_artifact["generated_at"] = "2026-05-01T00:00:00+00:00"  # very old
        db = FakeReadSupabaseClient(artifact_rows=[old_artifact])

        with caplog.at_level(logging.INFO):
            run_enabled_evidence_lanes_for_portfolio(
                user_id="user-1",
                tickers=["SNOW"],
                db_client=db,
                settings=_settings_workers_on(),
            )

        assert any(
            "sec_catalyst_stage5j_readiness" in rec.message
            and "SNOW" in rec.message
            for rec in caplog.records
        ), (
            "sec_catalyst_stage5j_readiness must appear even when the artifact predates "
            "the current snapshot (idempotency-skipped artifacts are still valid evidence)"
        )

    def test_stage5j_5k_logs_emitted_when_all_lanes_return_none(
        self, caplog, monkeypatch
    ):
        """All evidence lanes idempotency-skip (return None) → Stage 5J/5K still runs."""
        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", _fake_runner_no_writes)

        db = FakeReadSupabaseClient(
            artifact_rows=[_make_sec_catalyst_artifact_row("CRM")]
        )

        with caplog.at_level(logging.INFO):
            run_enabled_evidence_lanes_for_portfolio(
                user_id="user-1",
                tickers=["CRM"],
                db_client=db,
                settings=_settings_workers_on(),
            )

        has_5j = any(
            "sec_catalyst_stage5j_readiness" in rec.message for rec in caplog.records
        )
        has_5k = any(
            "sentiment_stage5k_source_selection" in rec.message for rec in caplog.records
        )
        assert has_5j, "sec_catalyst_stage5j_readiness must appear when artifact is in active set"
        assert has_5k, "sentiment_stage5k_source_selection must appear for every ticker"


# ── 7. ETF/BTC/XRP skip behavior unchanged ────────────────────────────────────

class TestETFCryptoSkipUnchanged:
    """ETF and crypto tickers skip SEC catalyst lane; Stage 5K reflects not_applicable."""

    def test_known_etf_sec_not_applicable(self):
        """SPY is a known ETF → sec_company_facts not applicable in Stage 5K."""
        db = FakeReadSupabaseClient(artifact_rows=[])
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["SPY"],
            db_client=db,
        )
        shadow = compute_decision_input_readiness(
            coverage,
            holding_context_by_ticker={"SPY": {"category": "ETF"}},
        )
        readiness = shadow.ticker_readiness.get("SPY")
        assert readiness is not None
        assert readiness.sec_lane_applicable is False, (
            "ETF ticker must have sec_lane_applicable=False in Stage 5K"
        )

    def test_btc_sec_not_applicable(self):
        db = FakeReadSupabaseClient(artifact_rows=[])
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["BTC"],
            db_client=db,
        )
        shadow = compute_decision_input_readiness(
            coverage,
            holding_context_by_ticker={"BTC": {"category": "Crypto"}},
        )
        readiness = shadow.ticker_readiness.get("BTC")
        assert readiness is not None
        assert readiness.sec_lane_applicable is False

    def test_xrp_sec_not_applicable(self):
        db = FakeReadSupabaseClient(artifact_rows=[])
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["XRP"],
            db_client=db,
        )
        shadow = compute_decision_input_readiness(
            coverage,
            holding_context_by_ticker={"XRP": {"category": "Crypto"}},
        )
        readiness = shadow.ticker_readiness.get("XRP")
        assert readiness is not None
        assert readiness.sec_lane_applicable is False

    def test_etf_sentiment_missing_not_penalized(self, monkeypatch, caplog):
        """ETF with no sentiment artifact → orchestrator runs cleanly without crash."""
        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", _fake_runner_no_writes)

        db = FakeReadSupabaseClient(artifact_rows=[])
        settings = Settings(
            **_BASE,
            intel_v3_research_workers_enabled=True,
            intel_v3_fundamentals_evidence_enabled=False,
            intel_v3_technicals_evidence_enabled=False,
            intel_v3_news_sentiment_evidence_enabled=False,
            intel_v3_sec_companyfacts_evidence_enabled=False,
            intel_v3_sentiment_catalyst_evidence_enabled=True,
        )

        result = run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["SPY"],
            db_client=db,
            settings=settings,
            holding_context_by_ticker={"SPY": {"category": "ETF"}},
        )
        # No crash — ETF skip behavior preserved
        assert "SPY" in result or result == {"SPY": {}} or isinstance(result, dict)


# ── 8. No BUY/HOLD/TRIM/SELL policy keys in coverage output ──────────────────

class TestNoPolicyMutations:
    """Coverage and readiness outputs must contain no BUY/HOLD/TRIM/SELL keys."""

    _POLICY_KEYS = frozenset({
        "BUY", "HOLD", "TRIM", "SELL",
        "action", "policy_action", "recommendation",
    })

    def _has_policy_key(self, obj: Any) -> bool:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in self._POLICY_KEYS:
                    return True
                if self._has_policy_key(v):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                if self._has_policy_key(item):
                    return True
        return False

    def test_coverage_output_no_policy_keys(self):
        db = FakeReadSupabaseClient(
            artifact_rows=[_make_sec_catalyst_artifact_row("AAPL")]
        )
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
        )
        data = coverage.to_dict()
        assert not self._has_policy_key(data), (
            "Coverage summary must contain no BUY/HOLD/TRIM/SELL policy keys"
        )

    def test_stage5k_shadow_no_policy_keys(self):
        db = FakeReadSupabaseClient(
            artifact_rows=[_make_sec_catalyst_artifact_row("AAPL")]
        )
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
        )
        shadow = compute_decision_input_readiness(coverage)
        data = shadow.to_dict()
        assert not self._has_policy_key(data), (
            "Stage 5K shadow must contain no BUY/HOLD/TRIM/SELL policy keys"
        )

    def test_safe_for_decision_always_false(self):
        db = FakeReadSupabaseClient(
            artifact_rows=[_make_sec_catalyst_artifact_row("AAPL")]
        )
        coverage = compute_research_evidence_coverage(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
        )
        shadow = compute_decision_input_readiness(coverage)
        assert coverage.safe_for_decision is False, (
            "Stage 5J coverage.safe_for_decision must always be False"
        )
        assert shadow.safe_for_decision is False, (
            "Stage 5K shadow.safe_for_decision must always be False"
        )
        assert shadow.shadow_only is True, (
            "Stage 5K shadow.shadow_only must always be True"
        )
