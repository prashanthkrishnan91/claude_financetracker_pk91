"""Stage 6 — Evidence Activation Regression Tests.

End-to-end pipeline regression harness: DB fixture artifacts → Stage 5J coverage
read model → Stage 5K decision input adapter → Stage 6 governance shadow.

Root cause documented: Production shows tickers_with_any_usable_axis=0 /
tickers_fully_missing=34 because the per-ticker evidence lane flags
(INTEL_V3_FUNDAMENTALS_EVIDENCE_ENABLED, INTEL_V3_TECHNICALS_EVIDENCE_ENABLED,
INTEL_V3_NEWS_SENTIMENT_EVIDENCE_ENABLED, INTEL_V3_SEC_COMPANYFACTS_EVIDENCE_ENABLED)
all default to False in Railway — no artifacts have been written for any per-ticker
lane. Only the FRED macro lane was explicitly activated, which is why macro_context
is READY while all per-ticker axes show 0 usable.

These tests lock the correct pipeline behavior:
  - When usable artifacts exist, the pipeline must show >0 usable axes.
  - When artifacts are absent, diagnostics must show missing_reason="no_active_artifact".
  - STALE/SUPPRESSED artifacts must remain non-usable.
  - ETF/crypto missing SEC is not penalized.
  - FRED macro remains portfolio-scope READY; per-ticker axes are independent.
  - Flag-off governance is a complete no-op.
  - No raw payloads/secrets escape in to_dict() output.

No production Supabase dependency — all DB interaction uses local fakes.
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
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    STATUS_LIMITED,
    STATUS_MISSING,
    STATUS_READY,
    STATUS_STALE_OR_UNKNOWN,
    STATUS_SUPPRESSED,
    compute_research_evidence_coverage,
)
from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
    AXIS_COMPANY_FUNDAMENTALS,
    AXIS_MACRO_CONTEXT,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL_SIGNALS,
    READINESS_MISSING,
    READINESS_NOT_APPLICABLE,
    READINESS_READY,
    READINESS_LIMITED,
    compute_decision_input_readiness,
)
from app.services.intelligence.v3.intel_v3_evidence_aware_governance_v1 import (
    apply_evidence_governance,
    compute_portfolio_governance_summary,
)
from app.services.intelligence.v3.decision_contracts import (
    AxisBand,
    DecisionInputV3,
    FitBand,
    PriceBand,
    RiskBand,
)

_USER_ID = "user-stage6-regression-test"


# ── Fake DB infrastructure ─────────────────────────────────────────────────────


@dataclass
class _FakeDB:
    rows: list[dict[str, Any]] = field(default_factory=list)
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

    def insert(self, *a, **kw):
        self._db.write_attempts += 1
        raise AssertionError("Pipeline must never insert")

    def update(self, *a, **kw):
        self._db.write_attempts += 1
        raise AssertionError("Pipeline must never update")

    def delete(self, *a, **kw):
        self._db.write_attempts += 1
        raise AssertionError("Pipeline must never delete")

    def eq(self, col: str, val: Any) -> "_FakeQuery":
        self._filters[col] = val
        return self

    def execute(self) -> Any:
        if self._table != "research_artifacts" or self._op != "select":
            class _Empty:
                data: list = []
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


# ── Fixture artifact builders ──────────────────────────────────────────────────


def _now_iso(offset_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat()


def _make_row(
    *,
    artifact_type: str,
    skill_pack: str,
    scope_kind: str,
    ticker: Optional[str],
    usability_label: str = "USABLE",
    is_usable: Optional[bool] = None,
    freshness_status: str = "FRESH",
    suppression_reason: Optional[str] = None,
    secret_field: Optional[str] = None,
    is_active: bool = True,
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
        "source_credibility_assessment": {"strongest_authority_level": "PRIMARY_AUTHORITY"},
        "contradiction_assessment": {"is_evaluable": True, "has_contradictions": False},
        "evidence_completeness_assessment": {"completeness_band": "COMPLETE"},
    }
    if secret_field:
        payload["_secret"] = secret_field
    return {
        "id": str(uuid.uuid4()),
        "user_id": _USER_ID,
        "artifact_type": artifact_type,
        "skill_pack": skill_pack,
        "scope_kind": scope_kind,
        "ticker": ticker,
        "is_active": is_active,
        "safe_for_decision": False,
        "freshness_status": freshness_status,
        "confidence_or_trust_level": "MEDIUM",
        "generated_at": _now_iso(),
        "expires_at": None,
        "model_version": "model.v1",
        "payload": payload,
    }


def _sec_row(ticker: str, **kw) -> dict[str, Any]:
    return _make_row(artifact_type="fundamental_quality", skill_pack="sec_companyfacts_evidence_v1",
                     scope_kind="ticker", ticker=ticker, **kw)


def _fund_row(ticker: str, **kw) -> dict[str, Any]:
    return _make_row(artifact_type="fundamental_quality", skill_pack="fundamentals_evidence_v1",
                     scope_kind="ticker", ticker=ticker, **kw)


def _tech_row(ticker: str, **kw) -> dict[str, Any]:
    return _make_row(artifact_type="technical_signal", skill_pack="technicals_evidence_v1",
                     scope_kind="ticker", ticker=ticker, **kw)


def _news_row(ticker: str, **kw) -> dict[str, Any]:
    return _make_row(artifact_type="sentiment_event", skill_pack="news_sentiment_evidence_v1",
                     scope_kind="ticker", ticker=ticker, **kw)


def _macro_row(**kw) -> dict[str, Any]:
    return _make_row(artifact_type="portfolio_exposure", skill_pack="fred_macro_evidence_v1",
                     scope_kind="portfolio", ticker=None, **kw)


def _run_5j_5k(rows: list[dict], tickers: list[str], ctx: Optional[dict] = None):
    """Run Stage 5J + Stage 5K together over the given fixture rows."""
    db = _FakeDB(rows=rows)
    coverage = compute_research_evidence_coverage(
        user_id=_USER_ID, tickers=tickers, db_client=_FakeClient(db),
    )
    shadow = compute_decision_input_readiness(coverage, holding_context_by_ticker=ctx)
    return coverage, shadow, db


def _hold_inp(ticker: str = "AAPL") -> DecisionInputV3:
    return DecisionInputV3(
        ticker=ticker,
        evidence_quality=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.MEDIUM,
        raw_action="HOLD",
        raw_analyst_action="HOLD",
        upstream_conviction="MEDIUM",
        suppression_reasons={},
    )


# ── 1. Full pipeline: USABLE FRESH artifact → Stage 6 shows >0 usable axes ────


class TestSecFreshUsableArtifactReachesStage6:
    """SEC CompanyFacts USABLE FRESH → READY company_fundamentals → Stage 6 usable axis."""

    def test_nonzero_usable_axes_when_sec_artifact_exists(self) -> None:
        _, shadow, _ = _run_5j_5k([_sec_row("AAPL")], ["AAPL"])
        assert shadow.tickers_with_any_usable_axis == 1
        assert shadow.tickers_fully_missing == 0
        assert shadow.axis_usable_counts.get(AXIS_COMPANY_FUNDAMENTALS) == 1

    def test_sec_artifact_company_fundamentals_readiness_is_ready(self) -> None:
        _, shadow, _ = _run_5j_5k([_sec_row("AAPL")], ["AAPL"])
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_COMPANY_FUNDAMENTALS]
        assert axis.readiness == READINESS_READY
        assert axis.is_usable is True
        assert LANE_SEC_COMPANY_FACTS in axis.contributing_lanes

    def test_limited_sec_artifact_company_fundamentals_readiness_is_limited(self) -> None:
        _, shadow, _ = _run_5j_5k(
            [_sec_row("AAPL", usability_label="USABLE_WITH_LIMITATIONS", is_usable=True)],
            ["AAPL"],
        )
        axis = shadow.ticker_readiness["AAPL"].axes[AXIS_COMPANY_FUNDAMENTALS]
        assert axis.readiness == READINESS_LIMITED
        assert axis.is_usable is True
        assert shadow.axis_usable_counts.get(AXIS_COMPANY_FUNDAMENTALS) == 1


class TestFundamentalsOnlyReachesStage6:
    """yfinance fundamentals USABLE FRESH (no SEC) → READY company_fundamentals."""

    def test_nonzero_usable_axes_with_fundamentals_artifact(self) -> None:
        _, shadow, _ = _run_5j_5k([_fund_row("MSFT")], ["MSFT"])
        assert shadow.tickers_with_any_usable_axis == 1
        assert shadow.tickers_fully_missing == 0
        assert shadow.axis_usable_counts.get(AXIS_COMPANY_FUNDAMENTALS) == 1

    def test_fundamentals_contributes_to_company_fundamentals_axis(self) -> None:
        _, shadow, _ = _run_5j_5k([_fund_row("MSFT")], ["MSFT"])
        axis = shadow.ticker_readiness["MSFT"].axes[AXIS_COMPANY_FUNDAMENTALS]
        assert axis.readiness == READINESS_READY
        assert LANE_FUNDAMENTALS in axis.contributing_lanes


class TestTechnicalsAndSentimentReachStage6:
    """Technicals USABLE FRESH → usable technical_signals axis."""

    def test_technicals_artifact_shows_usable_technical_signals(self) -> None:
        _, shadow, _ = _run_5j_5k([_tech_row("NVDA")], ["NVDA"])
        assert shadow.axis_usable_counts.get(AXIS_TECHNICAL_SIGNALS) == 1
        axis = shadow.ticker_readiness["NVDA"].axes[AXIS_TECHNICAL_SIGNALS]
        assert axis.readiness == READINESS_READY

    def test_news_sentiment_artifact_shows_usable_sentiment(self) -> None:
        _, shadow, _ = _run_5j_5k([_news_row("GOOGL", freshness_status="FRESH")], ["GOOGL"])
        assert shadow.axis_usable_counts.get(AXIS_SENTIMENT) == 1

    def test_technicals_plus_sec_makes_company_fundamentals_and_technical_both_usable(self) -> None:
        _, shadow, _ = _run_5j_5k([_sec_row("AAPL"), _tech_row("AAPL")], ["AAPL"])
        assert shadow.axis_usable_counts.get(AXIS_COMPANY_FUNDAMENTALS) == 1
        assert shadow.axis_usable_counts.get(AXIS_TECHNICAL_SIGNALS) == 1
        assert shadow.tickers_with_any_usable_axis == 1


# ── 2. All lanes missing → tickers_fully_missing ──────────────────────────────


class TestAllLanesMissingShowsFullyMissing:
    """No artifacts → all tickers fully missing — this is the current production state."""

    def test_no_artifacts_gives_zero_usable_axes(self) -> None:
        _, shadow, _ = _run_5j_5k([], ["AAPL", "MSFT"])
        assert shadow.tickers_with_any_usable_axis == 0
        assert shadow.tickers_fully_missing == 2
        assert shadow.axis_usable_counts.get(AXIS_COMPANY_FUNDAMENTALS, 0) == 0
        assert shadow.axis_usable_counts.get(AXIS_TECHNICAL_SIGNALS, 0) == 0
        assert shadow.axis_usable_counts.get(AXIS_SENTIMENT, 0) == 0

    def test_missing_coverage_lanes_have_no_active_artifact_reason(self) -> None:
        coverage, _, _ = _run_5j_5k([], ["AAPL"])
        for lane in (LANE_SEC_COMPANY_FACTS, LANE_FUNDAMENTALS, LANE_TECHNICALS, LANE_NEWS_SENTIMENT):
            cov = coverage.ticker_coverage["AAPL"].lanes[lane]
            assert cov.status == STATUS_MISSING
            assert cov.missing_reason == "no_active_artifact", (
                f"Lane {lane}: expected missing_reason='no_active_artifact', got {cov.missing_reason!r}. "
                "This is the production state when evidence workers have never been run."
            )

    def test_macro_missing_also_has_no_active_artifact_reason(self) -> None:
        coverage, _, _ = _run_5j_5k([], ["AAPL"])
        assert coverage.portfolio_macro_coverage.status == STATUS_MISSING
        assert coverage.portfolio_macro_coverage.missing_reason == "no_active_artifact"


# ── 3. FRED macro: READY and portfolio-level — does not count as ticker usable ──


class TestFredMacroPortfolioScopeOnly:
    """FRED macro READY must not count toward per-ticker usable axes."""

    def test_macro_ready_but_ticker_still_fully_missing(self) -> None:
        _, shadow, _ = _run_5j_5k([_macro_row()], ["AAPL"])
        # Macro READY.
        assert shadow.portfolio_macro.readiness == READINESS_READY  # type: ignore[union-attr]
        # Ticker has no per-ticker artifacts → still fully missing.
        assert shadow.tickers_fully_missing == 1
        assert shadow.tickers_with_any_usable_axis == 0

    def test_macro_plus_sec_makes_ticker_usable(self) -> None:
        _, shadow, _ = _run_5j_5k([_macro_row(), _sec_row("AAPL")], ["AAPL"])
        assert shadow.portfolio_macro.readiness == READINESS_READY  # type: ignore[union-attr]
        assert shadow.tickers_with_any_usable_axis == 1

    def test_macro_not_included_in_ticker_lane_coverage(self) -> None:
        coverage, _, _ = _run_5j_5k([_macro_row()], ["AAPL"])
        ticker_lanes = coverage.ticker_coverage["AAPL"].lanes
        assert LANE_MACRO_CONTEXT not in ticker_lanes


# ── 4. STALE/SUPPRESSED artifacts remain non-usable ───────────────────────────


class TestDegradedArtifactsRemainNonUsable:
    """STALE freshness and SUPPRESSED usability must not reach usable status."""

    def test_stale_sec_artifact_does_not_show_usable(self) -> None:
        _, shadow, _ = _run_5j_5k([_sec_row("AAPL", freshness_status="STALE")], ["AAPL"])
        assert shadow.tickers_fully_missing == 1
        assert shadow.axis_usable_counts.get(AXIS_COMPANY_FUNDAMENTALS, 0) == 0

    def test_stale_artifact_has_freshness_stale_reason(self) -> None:
        coverage, _, _ = _run_5j_5k([_sec_row("AAPL", freshness_status="STALE")], ["AAPL"])
        cov = coverage.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS]
        assert cov.status == STATUS_STALE_OR_UNKNOWN
        assert cov.missing_reason == "freshness_stale"
        assert cov.is_usable is False

    def test_unknown_freshness_artifact_has_freshness_unknown_reason(self) -> None:
        coverage, _, _ = _run_5j_5k([_sec_row("AAPL", freshness_status="UNKNOWN")], ["AAPL"])
        cov = coverage.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS]
        assert cov.status == STATUS_STALE_OR_UNKNOWN
        assert cov.missing_reason == "freshness_unknown"
        assert cov.is_usable is False

    def test_suppressed_artifact_does_not_show_usable(self) -> None:
        _, shadow, _ = _run_5j_5k(
            [_sec_row("AAPL", usability_label="SUPPRESSED_CONTRADICTED",
                      is_usable=False, suppression_reason="material_contradiction_detected")],
            ["AAPL"],
        )
        assert shadow.tickers_fully_missing == 1
        assert shadow.axis_usable_counts.get(AXIS_COMPANY_FUNDAMENTALS, 0) == 0

    def test_suppressed_artifact_has_usability_suppressed_reason(self) -> None:
        coverage, _, _ = _run_5j_5k(
            [_sec_row("AAPL", usability_label="SUPPRESSED_CONTRADICTED", is_usable=False)],
            ["AAPL"],
        )
        cov = coverage.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS]
        assert cov.status == STATUS_SUPPRESSED
        assert cov.missing_reason == "usability_suppressed"
        assert cov.is_usable is False

    def test_news_sentiment_stale_does_not_count(self) -> None:
        _, shadow, _ = _run_5j_5k([_news_row("AAPL", freshness_status="STALE")], ["AAPL"])
        assert shadow.axis_usable_counts.get(AXIS_SENTIMENT, 0) == 0

    def test_missing_technicals_sentiment_stay_missing_without_artifacts(self) -> None:
        # Only SEC artifact — technicals and sentiment remain missing.
        _, shadow, _ = _run_5j_5k([_sec_row("AAPL")], ["AAPL"])
        tech = shadow.ticker_readiness["AAPL"].axes[AXIS_TECHNICAL_SIGNALS]
        sent = shadow.ticker_readiness["AAPL"].axes[AXIS_SENTIMENT]
        assert tech.is_usable is False
        assert sent.is_usable is False
        assert LANE_TECHNICALS in tech.missing_lanes
        assert LANE_NEWS_SENTIMENT in sent.missing_lanes


# ── 5. ETF/crypto: SEC not_applicable not penalized ───────────────────────────


class TestEtfCryptoSecNotApplicable:
    """ETF/crypto missing SEC CompanyFacts must not be counted as a missing failure."""

    def test_spy_etf_sec_not_applicable_with_no_sec_artifact(self) -> None:
        ctx = {"SPY": {"category": "ETF"}}
        _, shadow, _ = _run_5j_5k([], ["SPY"], ctx=ctx)
        tr = shadow.ticker_readiness["SPY"]
        assert tr.sec_lane_applicable is False
        axis = tr.axes[AXIS_COMPANY_FUNDAMENTALS]
        # SEC lane not_applicable — axis readiness driven by fundamentals lane only.
        assert LANE_SEC_COMPANY_FACTS in axis.not_applicable_lanes

    def test_spy_with_fundamentals_artifact_gets_company_fundamentals_ready(self) -> None:
        ctx = {"SPY": {"category": "ETF"}}
        _, shadow, _ = _run_5j_5k([_fund_row("SPY")], ["SPY"], ctx=ctx)
        tr = shadow.ticker_readiness["SPY"]
        assert tr.sec_lane_applicable is False
        axis = tr.axes[AXIS_COMPANY_FUNDAMENTALS]
        assert axis.readiness == READINESS_READY
        assert LANE_FUNDAMENTALS in axis.contributing_lanes

    def test_known_crypto_ticker_sec_not_applicable(self) -> None:
        _, shadow, _ = _run_5j_5k([], ["BTC"])
        tr = shadow.ticker_readiness["BTC"]
        assert tr.sec_lane_applicable is False


# ── 6. Stage 6 governance: usable evidence enables non-THIN governance ─────────


class TestStage6GovernanceWithUsableEvidence:
    """Stage 6 governance: READY fundamentals axis → non-THIN governed quality."""

    def _make_ready_ticker_readiness(self, ticker: str = "AAPL"):
        """Build Stage 5K shadow with READY company_fundamentals for ticker."""
        from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
            AxisReadinessSignal, LaneReadinessContribution, TickerDecisionReadiness,
        )
        fund_axis = AxisReadinessSignal(
            axis_name=AXIS_COMPANY_FUNDAMENTALS,
            readiness=READINESS_READY,
            is_usable=True,
            contributing_lanes=[LANE_SEC_COMPANY_FACTS],
            degraded_lanes=[],
            missing_lanes=[],
            not_applicable_lanes=[],
            lane_contributions=[
                LaneReadinessContribution(
                    lane=LANE_SEC_COMPANY_FACTS,
                    coverage_status=STATUS_READY,
                    is_usable=True,
                    is_applicable=True,
                )
            ],
        )
        tech_axis = AxisReadinessSignal(
            axis_name=AXIS_TECHNICAL_SIGNALS,
            readiness=READINESS_READY,
            is_usable=True,
            contributing_lanes=[LANE_TECHNICALS],
            degraded_lanes=[],
            missing_lanes=[],
            not_applicable_lanes=[],
            lane_contributions=[
                LaneReadinessContribution(
                    lane=LANE_TECHNICALS,
                    coverage_status=STATUS_READY,
                    is_usable=True,
                    is_applicable=True,
                )
            ],
        )
        missing_sent = AxisReadinessSignal(
            axis_name=AXIS_SENTIMENT,
            readiness=READINESS_MISSING,
            is_usable=False,
            contributing_lanes=[],
            degraded_lanes=[],
            missing_lanes=[LANE_NEWS_SENTIMENT],
            not_applicable_lanes=[],
            lane_contributions=[],
        )
        return TickerDecisionReadiness(
            ticker=ticker,
            sec_lane_applicable=True,
            axes={
                AXIS_COMPANY_FUNDAMENTALS: fund_axis,
                AXIS_TECHNICAL_SIGNALS: tech_axis,
                AXIS_SENTIMENT: missing_sent,
            },
            any_axis_usable=True,
            usable_axis_count=2,
        )

    def test_ready_fundamentals_with_corroboration_yields_strong_band(self) -> None:
        inp = _hold_inp("AAPL")
        tr = self._make_ready_ticker_readiness("AAPL")
        result = apply_evidence_governance(inp, tr, None, flag_enabled=True)
        assert result.governance_applied is True
        assert result.governed_evidence_quality == "STRONG"
        assert result.supported_axis_count == 2

    def test_safe_for_visible_decision_true_when_fundamentals_ready(self) -> None:
        inp = _hold_inp("AAPL")
        tr = self._make_ready_ticker_readiness("AAPL")
        result = apply_evidence_governance(inp, tr, None, flag_enabled=True)
        assert result.safe_for_visible_decision is True

    def test_all_missing_readiness_yields_thin_and_buy_blocked(self) -> None:
        from app.services.intelligence.v3.research_evidence_decision_input_adapter_v1 import (
            AxisReadinessSignal, TickerDecisionReadiness,
        )
        def _missing(axis_name, lane):
            return AxisReadinessSignal(
                axis_name=axis_name, readiness=READINESS_MISSING, is_usable=False,
                contributing_lanes=[], degraded_lanes=[], missing_lanes=[lane],
                not_applicable_lanes=[], lane_contributions=[],
            )
        tr = TickerDecisionReadiness(
            ticker="AAPL",
            sec_lane_applicable=True,
            axes={
                AXIS_COMPANY_FUNDAMENTALS: _missing(AXIS_COMPANY_FUNDAMENTALS, LANE_FUNDAMENTALS),
                AXIS_TECHNICAL_SIGNALS: _missing(AXIS_TECHNICAL_SIGNALS, LANE_TECHNICALS),
                AXIS_SENTIMENT: _missing(AXIS_SENTIMENT, LANE_NEWS_SENTIMENT),
            },
            any_axis_usable=False,
            usable_axis_count=0,
        )
        inp = _hold_inp("AAPL")
        result = apply_evidence_governance(inp, tr, None, flag_enabled=True)
        assert result.governed_evidence_quality == "THIN"
        assert "buy_blocked_missing_evidence" in result.action_blocks_applied
        assert result.safe_for_visible_decision is False


# ── 7. Flag-off: governance is complete no-op ──────────────────────────────────


class TestFlagOffIsCompleteNoop:
    """With flag_enabled=False, governance_applied is False and inp is unchanged."""

    def test_flag_off_governance_applied_false(self) -> None:
        inp = _hold_inp("AAPL")
        original_quality = inp.evidence_quality
        result = apply_evidence_governance(inp, None, None, flag_enabled=False)
        assert result.governance_applied is False
        assert inp.evidence_quality == original_quality

    def test_flag_off_does_not_change_any_inp_field(self) -> None:
        inp = _hold_inp("AAPL")
        original_quality = inp.evidence_quality
        apply_evidence_governance(inp, None, None, flag_enabled=False)
        assert inp.evidence_quality == original_quality
        assert not inp.suppression_reasons  # not mutated by macro advisory when flag off


# ── 8. Usable artifact in to_dict: no raw payload or secrets ──────────────────


class TestNoRawPayloadOrSecretsInPipelineOutput:
    """The full pipeline must never leak raw payloads, source URLs, or secrets."""

    def test_lane_coverage_to_dict_no_payload_key(self) -> None:
        coverage, _, _ = _run_5j_5k(
            [_sec_row("AAPL", secret_field="SHOULD_NOT_APPEAR")],
            ["AAPL"],
        )
        blob = json.dumps(coverage.to_dict())
        assert "SHOULD_NOT_APPEAR" not in blob
        assert '"payload"' not in blob
        assert '"_secret"' not in blob

    def test_stage5k_shadow_to_dict_no_payload(self) -> None:
        _, shadow, _ = _run_5j_5k(
            [_sec_row("AAPL", secret_field="ANOTHER_SECRET")],
            ["AAPL"],
        )
        blob = json.dumps(shadow.to_dict())
        assert "ANOTHER_SECRET" not in blob

    def test_missing_reason_present_in_lane_coverage_dict(self) -> None:
        coverage, _, _ = _run_5j_5k([], ["AAPL"])
        cov_dict = coverage.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS].to_dict()
        assert "missing_reason" in cov_dict
        assert cov_dict["missing_reason"] == "no_active_artifact"

    def test_ready_lane_missing_reason_is_none(self) -> None:
        coverage, _, _ = _run_5j_5k([_sec_row("AAPL")], ["AAPL"])
        cov_dict = coverage.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS].to_dict()
        assert cov_dict["missing_reason"] is None

    def test_stale_lane_missing_reason_is_freshness_stale(self) -> None:
        coverage, _, _ = _run_5j_5k([_sec_row("AAPL", freshness_status="STALE")], ["AAPL"])
        cov_dict = coverage.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS].to_dict()
        assert cov_dict["missing_reason"] == "freshness_stale"

    def test_suppressed_lane_missing_reason_is_usability_suppressed(self) -> None:
        coverage, _, _ = _run_5j_5k(
            [_sec_row("AAPL", usability_label="SUPPRESSED_CONTRADICTED", is_usable=False)],
            ["AAPL"],
        )
        cov_dict = coverage.ticker_coverage["AAPL"].lanes[LANE_SEC_COMPANY_FACTS].to_dict()
        assert cov_dict["missing_reason"] == "usability_suppressed"


# ── 9. Pipeline is read-only ──────────────────────────────────────────────────


class TestPipelineIsReadOnly:
    def test_no_db_writes_during_full_pipeline(self) -> None:
        coverage, shadow, db = _run_5j_5k(
            [_sec_row("AAPL"), _macro_row()], ["AAPL"],
        )
        assert db.write_attempts == 0
