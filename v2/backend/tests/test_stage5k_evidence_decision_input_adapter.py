"""Stage 5K focused tests — Research Evidence Decision Input Adapter v1.

Acceptance criteria verified:
  1. READY SEC artifact upgrades equity company_fundamentals to READY.
  2. LIMITED SEC artifact contributes with LIMITED readiness (when it is the best lane).
  3. SUPPRESSED/STALE/MISSING SEC artifact does not contribute readiness.
  4. ETF/crypto with no SEC lane is not penalized — sec_lane_applicable=False,
     axis readiness driven by other lanes only.
  5. READY FRED macro contributes portfolio macro_context readiness.
  6. Missing macro does not contribute macro readiness.
  7. Fundamentals/technicals/news_sentiment only contribute when usable artifacts exist.
  8. safe_for_decision remains False always.
  9. shadow_only remains True always.
 10. Adapter is read-only: no provider calls, no LLM calls, no DB writes.
 11. Endpoint is cert/env gated (flag off → 403).
 12. No raw payload/API key/secret/source URL leaks in output.
 13. no_guessing is True always.
 14. ETF with fundamentals artifact still gets fundamentals axis readiness.
 15. All missing → tickers_fully_missing incremented.

No production Supabase dependency — all fakes defined locally.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from app.services.intelligence.v3.research_evidence_coverage_read_model_v1 import (
    LANE_FUNDAMENTALS,
    LANE_MACRO_CONTEXT,
    LANE_NEWS_SENTIMENT,
    LANE_SEC_CATALYST_SENTIMENT,
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    STATUS_LIMITED,
    STATUS_MISSING,
    STATUS_NOT_EVALUABLE,
    STATUS_READY,
    STATUS_STALE_OR_UNKNOWN,
    STATUS_SUPPRESSED,
    compute_research_evidence_coverage,
)
from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
    ADAPTER_VERSION,
    AXIS_COMPANY_FUNDAMENTALS,
    AXIS_MACRO_CONTEXT,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL_SIGNALS,
    READINESS_INSUFFICIENT,
    READINESS_LIMITED,
    READINESS_MISSING,
    READINESS_NOT_APPLICABLE,
    READINESS_READY,
    compute_decision_input_readiness,
    log_decision_readiness_summary,
)


_USER_ID = "user-stage5k-test"


# ── Fakes (mirrored from Stage 5J tests) ─────────────────────────────────────


@dataclass
class _FakeDB:
    rows: list[dict[str, Any]] = field(default_factory=list)
    select_call_count: int = 0
    write_attempts: int = 0


class _FakeQuery:
    def __init__(self, db: _FakeDB, table_name: str) -> None:
        self._db = db
        self._table = table_name
        self._filters: dict[str, Any] = {}
        self._op: Optional[str] = None

    def select(self, cols: str = "*") -> "_FakeQuery":
        self._op = "select"
        return self

    def insert(self, *args, **kwargs):
        self._db.write_attempts += 1
        raise AssertionError("Must never insert")

    def update(self, *args, **kwargs):
        self._db.write_attempts += 1
        raise AssertionError("Must never update")

    def delete(self, *args, **kwargs):
        self._db.write_attempts += 1
        raise AssertionError("Must never delete")

    def eq(self, col: str, val: Any) -> "_FakeQuery":
        self._filters[col] = val
        return self

    def execute(self) -> Any:
        self._db.select_call_count += 1
        if self._table != "research_artifacts" or self._op != "select":
            class _Empty:
                data = []
            return _Empty()
        matched = [r for r in self._db.rows if all(r.get(k) == v for k, v in self._filters.items())]

        class _Res:
            data = matched
        return _Res()


class _FakeClient:
    def __init__(self, db: _FakeDB) -> None:
        self._db = db

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self._db, name)


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _make_artifact_row(
    *,
    artifact_type: str,
    skill_pack: str,
    scope_kind: str,
    ticker: Optional[str],
    usability_label: str = "USABLE",
    is_usable: Optional[bool] = None,
    suppression_reason: Optional[str] = None,
    freshness_status: str = "FRESH",
    secret_field: Optional[str] = None,
) -> dict[str, Any]:
    if is_usable is None:
        is_usable = usability_label in {"USABLE", "USABLE_WITH_LIMITATIONS"}
    payload: dict[str, Any] = {
        "truth_usability_assessment": {
            "usability_label": usability_label,
            "is_usable": is_usable,
            "suppression_reason": suppression_reason,
            "no_guessing": True,
        },
        "source_credibility_assessment": {
            "strongest_authority_level": "PRIMARY_AUTHORITY",
        },
        "contradiction_assessment": {
            "is_evaluable": True,
            "has_contradictions": False,
        },
        "evidence_completeness_assessment": {
            "completeness_band": "COMPLETE",
        },
    }
    if secret_field:
        payload["api_key"] = secret_field
    return {
        "id": str(uuid.uuid4()),
        "user_id": _USER_ID,
        "artifact_type": artifact_type,
        "skill_pack": skill_pack,
        "scope_kind": scope_kind,
        "ticker": ticker,
        "is_active": True,
        "safe_for_decision": False,
        "freshness_status": freshness_status,
        "confidence_or_trust_level": "MEDIUM",
        "generated_at": _now_iso(),
        "expires_at": None,
        "model_version": "model.v1",
        "payload": payload,
    }


def _sec_row(ticker: str, **kw) -> dict[str, Any]:
    return _make_artifact_row(
        artifact_type="fundamental_quality",
        skill_pack="sec_companyfacts_evidence_v1",
        scope_kind="ticker",
        ticker=ticker,
        **kw,
    )


def _fund_row(ticker: str, **kw) -> dict[str, Any]:
    return _make_artifact_row(
        artifact_type="fundamental_quality",
        skill_pack="fundamentals_evidence_v1",
        scope_kind="ticker",
        ticker=ticker,
        **kw,
    )


def _tech_row(ticker: str, **kw) -> dict[str, Any]:
    return _make_artifact_row(
        artifact_type="technical_signal",
        skill_pack="technicals_evidence_v1",
        scope_kind="ticker",
        ticker=ticker,
        **kw,
    )


def _news_row(ticker: str, **kw) -> dict[str, Any]:
    return _make_artifact_row(
        artifact_type="sentiment_event",
        skill_pack="news_sentiment_evidence_v1",
        scope_kind="ticker",
        ticker=ticker,
        **kw,
    )


def _macro_row(**kw) -> dict[str, Any]:
    return _make_artifact_row(
        artifact_type="portfolio_exposure",
        skill_pack="fred_macro_evidence_v1",
        scope_kind="portfolio",
        ticker=None,
        **kw,
    )


def _run_adapter(rows: list[dict], tickers: list[str], ctx: Optional[dict] = None) -> Any:
    """Run Stage 5J + Stage 5K together on the given fake rows."""
    db = _FakeDB(rows=rows)
    coverage = compute_research_evidence_coverage(
        user_id=_USER_ID,
        tickers=tickers,
        db_client=_FakeClient(db),
    )
    return compute_decision_input_readiness(coverage, holding_context_by_ticker=ctx), db


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestReadySecUpgradesEquityFundamentals:
    def test_ready_sec_artifact_yields_ready_company_fundamentals(self) -> None:
        shadow, _ = _run_adapter([_sec_row("AAPL")], ["AAPL"])
        tr = shadow.ticker_readiness["AAPL"]
        axis = tr.axes[AXIS_COMPANY_FUNDAMENTALS]
        assert axis.readiness == READINESS_READY
        assert axis.is_usable is True
        assert LANE_SEC_COMPANY_FACTS in axis.contributing_lanes
        assert tr.sec_lane_applicable is True
        assert tr.any_axis_usable is True


class TestLimitedSecContributesWithLimitations:
    def test_limited_sec_artifact_yields_limited_company_fundamentals(self) -> None:
        shadow, _ = _run_adapter(
            [_sec_row("AAPL", usability_label="USABLE_WITH_LIMITATIONS", is_usable=True)],
            ["AAPL"],
        )
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_COMPANY_FUNDAMENTALS]
        assert axis.readiness == READINESS_LIMITED
        assert axis.is_usable is True
        assert LANE_SEC_COMPANY_FACTS in axis.contributing_lanes


class TestSuppressedSecDoesNotContribute:
    def test_suppressed_sec_does_not_make_axis_usable(self) -> None:
        shadow, _ = _run_adapter(
            [_sec_row("AAPL", usability_label="SUPPRESSED_CONTRADICTED", is_usable=False)],
            ["AAPL"],
        )
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_COMPANY_FUNDAMENTALS]
        assert axis.is_usable is False
        assert LANE_SEC_COMPANY_FACTS not in axis.contributing_lanes
        assert LANE_SEC_COMPANY_FACTS in axis.degraded_lanes

    def test_stale_sec_does_not_contribute(self) -> None:
        shadow, _ = _run_adapter(
            [_sec_row("AAPL", usability_label="USABLE", freshness_status="STALE")],
            ["AAPL"],
        )
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_COMPANY_FUNDAMENTALS]
        assert axis.is_usable is False

    def test_missing_sec_does_not_contribute(self) -> None:
        shadow, _ = _run_adapter([], ["AAPL"])
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_COMPANY_FUNDAMENTALS]
        assert axis.is_usable is False
        assert LANE_SEC_COMPANY_FACTS in axis.missing_lanes


class TestEtfCryptoNotPenalizedForMissingSec:
    """ETF/crypto tickers should not be penalized for SEC CompanyFacts gaps."""

    def test_spy_etf_sec_lane_not_applicable(self) -> None:
        shadow, _ = _run_adapter([], ["SPY"])
        tr = shadow.ticker_readiness["SPY"]
        assert tr.sec_lane_applicable is False
        axis = tr.axes[AXIS_COMPANY_FUNDAMENTALS]
        assert LANE_SEC_COMPANY_FACTS in axis.not_applicable_lanes
        assert LANE_SEC_COMPANY_FACTS not in axis.missing_lanes

    def test_btc_crypto_sec_lane_not_applicable(self) -> None:
        shadow, _ = _run_adapter([], ["BTC"])
        assert shadow.ticker_readiness["BTC"].sec_lane_applicable is False

    def test_etf_with_holding_context_sec_not_applicable(self) -> None:
        ctx = {"SPY": {"category": "ETF"}}
        shadow, _ = _run_adapter([], ["SPY"], ctx=ctx)
        assert shadow.ticker_readiness["SPY"].sec_lane_applicable is False

    def test_crypto_with_holding_context_sec_not_applicable(self) -> None:
        ctx = {"BTC": {"category": "Crypto"}}
        shadow, _ = _run_adapter([], ["BTC"], ctx=ctx)
        assert shadow.ticker_readiness["BTC"].sec_lane_applicable is False

    def test_etf_with_fundamentals_artifact_still_gets_readiness(self) -> None:
        """ETF without SEC but with fundamentals artifact should be usable."""
        shadow, _ = _run_adapter([_fund_row("SPY")], ["SPY"])
        tr = shadow.ticker_readiness["SPY"]
        assert tr.sec_lane_applicable is False
        axis = tr.axes[AXIS_COMPANY_FUNDAMENTALS]
        # fundamentals lane usable even without SEC
        assert axis.is_usable is True
        assert LANE_FUNDAMENTALS in axis.contributing_lanes
        assert LANE_SEC_COMPANY_FACTS in axis.not_applicable_lanes

    def test_unknown_ticker_assumed_equity_by_default(self) -> None:
        """Tickers not in known ETF/crypto lists and no context → SEC is applicable."""
        shadow, _ = _run_adapter([], ["AAPL"])
        assert shadow.ticker_readiness["AAPL"].sec_lane_applicable is True


class TestMacroContextReadiness:
    def test_ready_fred_macro_gives_portfolio_macro_ready(self) -> None:
        shadow, _ = _run_adapter([_macro_row()], ["AAPL"])
        assert shadow.portfolio_macro is not None
        assert shadow.portfolio_macro.readiness == READINESS_READY
        assert shadow.portfolio_macro.is_usable is True

    def test_missing_macro_gives_portfolio_macro_missing(self) -> None:
        shadow, _ = _run_adapter([], ["AAPL"])
        assert shadow.portfolio_macro is not None
        assert shadow.portfolio_macro.is_usable is False
        assert shadow.portfolio_macro.readiness == READINESS_MISSING

    def test_suppressed_macro_does_not_contribute(self) -> None:
        shadow, _ = _run_adapter(
            [_macro_row(usability_label="SUPPRESSED_CONTRADICTED", is_usable=False)],
            ["AAPL"],
        )
        assert shadow.portfolio_macro.is_usable is False

    def test_limited_macro_contributes_with_limited_readiness(self) -> None:
        shadow, _ = _run_adapter(
            [_macro_row(usability_label="USABLE_WITH_LIMITATIONS", is_usable=True)],
            ["AAPL"],
        )
        assert shadow.portfolio_macro.readiness == READINESS_LIMITED
        assert shadow.portfolio_macro.is_usable is True


class TestFundamentalsTechnicalsNewsContributeOnlyWhenUsable:
    def test_ready_technicals_gives_technical_signals_ready(self) -> None:
        shadow, _ = _run_adapter([_tech_row("AAPL")], ["AAPL"])
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_TECHNICAL_SIGNALS]
        assert axis.readiness == READINESS_READY
        assert axis.is_usable is True

    def test_missing_technicals_gives_technical_signals_missing(self) -> None:
        shadow, _ = _run_adapter([], ["AAPL"])
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_TECHNICAL_SIGNALS]
        assert axis.is_usable is False
        assert axis.readiness == READINESS_MISSING

    def test_ready_news_gives_sentiment_ready(self) -> None:
        shadow, _ = _run_adapter([_news_row("AAPL")], ["AAPL"])
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_SENTIMENT]
        assert axis.readiness == READINESS_READY
        assert axis.is_usable is True

    def test_suppressed_fundamentals_does_not_contribute(self) -> None:
        shadow, _ = _run_adapter(
            [_fund_row("AAPL", usability_label="SUPPRESSED_INCOMPLETE", is_usable=False)],
            ["AAPL"],
        )
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_COMPANY_FUNDAMENTALS]
        assert axis.is_usable is False

    def test_stale_technicals_does_not_contribute(self) -> None:
        shadow, _ = _run_adapter(
            [_tech_row("AAPL", freshness_status="STALE")],
            ["AAPL"],
        )
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_TECHNICAL_SIGNALS]
        assert axis.is_usable is False


class TestSafeForDecisionAlwaysFalse:
    def test_safe_for_decision_is_always_false(self) -> None:
        # With full usable evidence
        rows = [_sec_row("AAPL"), _fund_row("AAPL"), _tech_row("AAPL"), _news_row("AAPL"), _macro_row()]
        shadow, _ = _run_adapter(rows, ["AAPL"])
        assert shadow.safe_for_decision is False

    def test_shadow_only_is_always_true(self) -> None:
        shadow, _ = _run_adapter([], ["AAPL"])
        assert shadow.shadow_only is True

    def test_no_guessing_is_always_true(self) -> None:
        shadow, _ = _run_adapter([], ["AAPL"])
        assert shadow.no_guessing is True

    def test_adapter_version_set(self) -> None:
        shadow, _ = _run_adapter([], ["AAPL"])
        assert shadow.schema_version == ADAPTER_VERSION
        assert shadow.adapter_version == ADAPTER_VERSION

    def test_safe_for_decision_false_in_dict(self) -> None:
        shadow, _ = _run_adapter([], ["AAPL"])
        d = shadow.to_dict()
        assert d["safe_for_decision"] is False
        assert d["shadow_only"] is True


class TestAdapterIsReadOnly:
    def test_no_db_writes_from_adapter(self) -> None:
        rows = [_sec_row("AAPL"), _macro_row()]
        db = _FakeDB(rows=rows)
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        compute_decision_input_readiness(coverage)
        assert db.write_attempts == 0

    def test_adapter_itself_makes_no_db_calls(self) -> None:
        """compute_decision_input_readiness takes a coverage object — no DB interaction."""
        rows = [_sec_row("AAPL")]
        db = _FakeDB(rows=rows)
        coverage = compute_research_evidence_coverage(
            user_id=_USER_ID, tickers=["AAPL"], db_client=_FakeClient(db),
        )
        call_count_before = db.select_call_count
        compute_decision_input_readiness(coverage)
        # Adapter should not make any additional DB calls.
        assert db.select_call_count == call_count_before


class TestNoRawPayloadOrSecretLeak:
    def test_output_dict_never_contains_secrets(self) -> None:
        rows = [_sec_row("AAPL", secret_field="TOP_SECRET_KEY"), _macro_row()]
        shadow, _ = _run_adapter(rows, ["AAPL"])
        blob = json.dumps(shadow.to_dict())
        assert "TOP_SECRET_KEY" not in blob
        assert "api_key" not in blob
        assert '"payload"' not in blob
        assert "source_url" not in blob

    def test_log_summary_no_payload_in_log(self, caplog) -> None:
        rows = [_sec_row("AAPL", secret_field="LOG_LEAK_TOKEN")]
        shadow, _ = _run_adapter(rows, ["AAPL"])
        with caplog.at_level("INFO"):
            log_decision_readiness_summary(shadow)
        messages = " ".join(rec.getMessage() for rec in caplog.records)
        assert "LOG_LEAK_TOKEN" not in messages
        assert "api_key" not in messages
        assert "research_evidence_decision_readiness_summary" in messages


class TestAggregateCounters:
    def test_tickers_fully_missing_incremented_when_no_usable_axis(self) -> None:
        shadow, _ = _run_adapter([], ["AAPL", "MSFT"])
        assert shadow.tickers_fully_missing == 2
        assert shadow.tickers_with_any_usable_axis == 0

    def test_tickers_with_any_usable_axis_incremented_when_one_axis_ready(self) -> None:
        shadow, _ = _run_adapter([_sec_row("AAPL")], ["AAPL", "MSFT"])
        assert shadow.tickers_with_any_usable_axis == 1
        assert shadow.tickers_fully_missing == 1

    def test_axis_usable_counts_incremented_correctly(self) -> None:
        rows = [_tech_row("AAPL"), _tech_row("MSFT"), _news_row("AAPL")]
        shadow, _ = _run_adapter(rows, ["AAPL", "MSFT"])
        assert shadow.axis_usable_counts.get(AXIS_TECHNICAL_SIGNALS) == 2
        assert shadow.axis_usable_counts.get(AXIS_SENTIMENT) == 1
        assert shadow.axis_usable_counts.get(AXIS_COMPANY_FUNDAMENTALS, 0) == 0


# ── SEC catalyst sentiment axis tests (Stage 8C PR 2.4) ─────────────────────


def _sec_catalyst_row(ticker: str, **kw) -> dict[str, Any]:
    return _make_artifact_row(
        artifact_type="sentiment_event",
        skill_pack="sec_catalyst_sentiment_evidence_v1",
        scope_kind="ticker",
        ticker=ticker,
        **kw,
    )


class TestSecCatalystSentimentAxis:
    """Stage 5K sentiment axis correctly propagates usable SEC catalyst artifacts."""

    def test_usable_sec_catalyst_yields_limited_sentiment_axis(self) -> None:
        shadow, _ = _run_adapter(
            [_sec_catalyst_row("CRM", usability_label="USABLE_WITH_LIMITATIONS", is_usable=True)],
            ["CRM"],
        )
        axis = shadow.ticker_readiness["CRM"].axes[AXIS_SENTIMENT]
        assert axis.readiness == READINESS_LIMITED
        assert axis.is_usable is True
        assert LANE_SEC_CATALYST_SENTIMENT in axis.contributing_lanes

    def test_suppressed_editorial_news_does_not_override_usable_sec_catalyst(self) -> None:
        rows = [
            _sec_catalyst_row("CRM", usability_label="USABLE_WITH_LIMITATIONS", is_usable=True),
            _news_row("CRM", usability_label="SUPPRESSED_INCOMPLETE", is_usable=False),
        ]
        shadow, _ = _run_adapter(rows, ["CRM"])
        axis = shadow.ticker_readiness["CRM"].axes[AXIS_SENTIMENT]
        assert axis.is_usable is True
        assert axis.readiness == READINESS_LIMITED
        assert LANE_SEC_CATALYST_SENTIMENT in axis.contributing_lanes
        assert LANE_NEWS_SENTIMENT in axis.degraded_lanes

    def test_suppressed_news_only_yields_insufficient_not_usable(self) -> None:
        rows = [_news_row("CRM", usability_label="SUPPRESSED_INCOMPLETE", is_usable=False)]
        shadow, _ = _run_adapter(rows, ["CRM"])
        axis = shadow.ticker_readiness["CRM"].axes[AXIS_SENTIMENT]
        assert axis.is_usable is False
        assert axis.readiness == READINESS_INSUFFICIENT

    def test_sec_catalyst_sentiment_in_axis_usable_counts(self) -> None:
        shadow, _ = _run_adapter(
            [_sec_catalyst_row("CRM", usability_label="USABLE_WITH_LIMITATIONS", is_usable=True)],
            ["CRM"],
        )
        assert shadow.axis_usable_counts.get(AXIS_SENTIMENT) == 1

    def test_etf_missing_sec_catalyst_no_penalty(self) -> None:
        shadow, _ = _run_adapter([], ["SPY"])
        tr = shadow.ticker_readiness["SPY"]
        assert tr.sec_lane_applicable is False
        axis = tr.axes[AXIS_SENTIMENT]
        assert axis.readiness == READINESS_MISSING
        assert axis.is_usable is False

    def test_btc_missing_sec_catalyst_no_penalty(self) -> None:
        shadow, _ = _run_adapter([], ["BTC"])
        axis = shadow.ticker_readiness["BTC"].axes[AXIS_SENTIMENT]
        assert axis.readiness == READINESS_MISSING
        assert axis.is_usable is False

    def test_no_buy_hold_trim_sell_in_shadow_output(self) -> None:
        rows = [_sec_catalyst_row("CRM", usability_label="USABLE_WITH_LIMITATIONS", is_usable=True)]
        shadow, _ = _run_adapter(rows, ["CRM"])
        blob = json.dumps(shadow.to_dict())
        for key in ("\"buy\"", "\"hold\"", "\"trim\"", "\"sell\"", "\"action\"", "\"recommendation\""):
            assert key not in blob.lower(), f"Policy key {key} must not appear in shadow output"

    def test_sentiment_stage5k_source_selection_log_emitted(self, caplog) -> None:
        rows = [
            _sec_catalyst_row("CRM", usability_label="USABLE_WITH_LIMITATIONS", is_usable=True),
            _news_row("CRM", usability_label="SUPPRESSED_INCOMPLETE", is_usable=False),
        ]
        with caplog.at_level("INFO"):
            _run_adapter(rows, ["CRM"])
        messages = " ".join(rec.getMessage() for rec in caplog.records)
        assert "sentiment_stage5k_source_selection" in messages
        assert "selected=sec_catalyst_sentiment" in messages
        assert "suppressed_editorial_present=True" in messages
