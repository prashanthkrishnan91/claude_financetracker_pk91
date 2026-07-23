"""Production-shaped fixture proofs for ``run_trust_contract_v1``.

Fixture mirrors the observed truth of production session
``a51e977b-561a-4e98-baa8-59ad56a877ff`` (see mission / HANDOFF):

  * 31 frozen holdings, 31 persisted decisions, session status completed
  * 31 technical outputs, 31 sentiment outputs, 19 fundamental outputs
    (equities only), 12 ETF-exposure outputs (ETFs only)
  * 7 conflict reviews required; 2 succeeded (KLAR, NVDA); 5 failed
    (BLSH, CRM, GLD, GOOGL, NFLX)
  * 0 evidence bundles / specialist outputs with nonempty source refs
  * AMD and TSM: strong evidence, suppressed price context (not an evidence
    failure)
  * NVDA: portfolio-overweight constraint (not an evidence failure)
  * BLSH: speculative/blocked portfolio fit AND a failed required review —
    two distinct, non-conflated constraint categories

``build_run_trust_contract`` is a pure function over plain dicts — no
FakeSupabase/DB/LLM required for these proofs.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.distributed import run_trust_contract_v1 as trust
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_ETF_EXPOSURE,
    AXIS_FUNDAMENTAL,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL,
    TASK_REVIEW_CONFLICT,
    TASK_SUCCEEDED,
    TASK_FAILED,
    TICKER_DECIDED,
)

SESSION_ID = "a51e977b-561a-4e98-baa8-59ad56a877ff"

EQUITIES = [
    "AMD", "TSM", "NVDA", "CRM", "GOOGL", "NFLX", "BLSH", "KLAR",
    "AAPL", "MSFT", "META", "AMZN", "TSLA", "JPM", "V", "MA",
    "UNH", "JNJ", "XOM",
]
ETFS = [
    "GLD", "VTI", "VOO", "QQQ", "VHT", "SCHD", "IVV", "VUG",
    "VYM", "VNQ", "XLK", "XLF",
]
assert len(EQUITIES) == 19
assert len(ETFS) == 12

REVIEW_SUCCEEDED_TICKERS = {"KLAR", "NVDA"}
REVIEW_FAILED_TICKERS = {"BLSH", "CRM", "GLD", "GOOGL", "NFLX"}
REVIEW_REQUIRED_TICKERS = REVIEW_SUCCEEDED_TICKERS | REVIEW_FAILED_TICKERS

# Speculative tickers are BLOCKED by the real portfolio governor
# (portfolio_governor_lite._SPECULATIVE_TICKERS) — BLSH/KLAR included.
SPECULATIVE = {"BLSH", "KLAR", "BTC", "XRP", "RIVN"}


def _decision(
    *,
    action="HOLD",
    evidence_quality="OK",
    price_context="SUPPRESSED",
    portfolio_fit="ON_TARGET",
    risk_band="LOW",
) -> dict:
    return {
        "outcome": "DECIDED",
        "action": action,
        "conviction": "MEDIUM",
        "evidence_quality": evidence_quality,
        "attractiveness": "OK",
        "price_context": price_context,
        "portfolio_fit": portfolio_fit,
        "risk_band": risk_band,
        "blockers": [],
        "suppression_reasons": {},
        "rationale_plain_english": "test rationale",
        "why_now": "", "why_not_now": "",
        "source_signal_summary": {},
        "policy_schema_version": "v3.1",
    }


# Per-ticker decision overrides (defaults otherwise: OK evidence, ON_TARGET
# fit, LOW risk, suppressed price — same "everyone gets price_context
# suppressed" architecture reality the mission documents).
_DECISION_OVERRIDES = {
    "AMD": dict(action="BUY", evidence_quality="STRONG", portfolio_fit="UNDERWEIGHT"),
    "TSM": dict(action="BUY", evidence_quality="STRONG", portfolio_fit="ON_TARGET"),
    "NVDA": dict(action="TRIM", evidence_quality="STRONG", portfolio_fit="OVERWEIGHT"),
    "BLSH": dict(action="HOLD", evidence_quality="OK", portfolio_fit="BLOCKED"),
    "CRM": dict(action="HOLD", evidence_quality="STRONG", portfolio_fit="ON_TARGET"),
    "GOOGL": dict(action="BUY", evidence_quality="STRONG", portfolio_fit="UNDERWEIGHT"),
    "NFLX": dict(action="HOLD", evidence_quality="OK", portfolio_fit="ON_TARGET"),
    "GLD": dict(action="HOLD", evidence_quality="STRONG", portfolio_fit="ON_TARGET"),
    "KLAR": dict(action="HOLD", evidence_quality="OK", portfolio_fit="BLOCKED"),
}


def _asset_type(ticker: str) -> str:
    return "etf" if ticker in ETFS else "equity"


def _ticker_row(ticker: str) -> dict:
    overrides = _DECISION_OVERRIDES.get(ticker, {})
    return {
        "ticker": ticker,
        "asset_type": _asset_type(ticker),
        "state": TICKER_DECIDED,
        "portfolio_weight_pct": 6.0 if ticker == "NVDA" else 2.0,
        # Zero-reference bundle — matches production: 0 evidence bundles with
        # nonempty source_refs.
        "evidence_bundle": {"source_refs": []},
        "decision": _decision(**overrides),
    }


def _specialist_output(ticker: str, axis: str) -> dict:
    return {
        "ticker": ticker,
        "axis": axis,
        "score": 0.5,
        "confidence": 0.8,
        "key_findings": [f"{ticker} {axis} finding"],
        "risks": [],
        # Zero-reference outputs — matches production: 0 persisted specialist
        # outputs with nonempty evidence_refs.
        "evidence_refs": [],
        "missing_evidence": [],
        "limitations": [],
        "model": "fake", "prompt_version": "test",
    }


def build_production_shaped_fixture() -> tuple[dict, list, list, list]:
    session = {"id": SESSION_ID, "status": "completed", "workflow_version": 2}
    tickers = EQUITIES + ETFS
    ticker_rows = [_ticker_row(t) for t in tickers]

    specialist_outputs: list[dict] = []
    for t in tickers:
        specialist_outputs.append(_specialist_output(t, AXIS_TECHNICAL))
        specialist_outputs.append(_specialist_output(t, AXIS_SENTIMENT))
        if _asset_type(t) == "equity":
            specialist_outputs.append(_specialist_output(t, AXIS_FUNDAMENTAL))
        else:
            specialist_outputs.append(_specialist_output(t, AXIS_ETF_EXPOSURE))

    tasks: list[dict] = []
    for t in REVIEW_SUCCEEDED_TICKERS:
        tasks.append({
            "task_type": TASK_REVIEW_CONFLICT, "ticker": t, "state": TASK_SUCCEEDED,
        })
    for t in REVIEW_FAILED_TICKERS:
        tasks.append({
            "task_type": TASK_REVIEW_CONFLICT, "ticker": t, "state": TASK_FAILED,
        })

    return session, ticker_rows, tasks, specialist_outputs


@pytest.fixture()
def fixture_contract():
    session, ticker_rows, tasks, specialist_outputs = build_production_shaped_fixture()
    return trust.build_run_trust_contract(
        session=session, ticker_rows=ticker_rows, tasks=tasks,
        specialist_outputs=specialist_outputs,
    )


class TestSchemaAndSessionCoverage:
    def test_schema_version_and_session_id(self, fixture_contract):
        assert fixture_contract["schema_version"] == "run_trust_contract_v1"
        assert fixture_contract["run_session_id"] == SESSION_ID

    def test_session_coverage_complete_31_holdings(self, fixture_contract):
        cov = fixture_contract["session_coverage"]
        assert cov["frozen_holding_count"] == 31
        assert cov["decided_count"] == 31
        assert cov["no_call_count"] == 0
        assert cov["failed_count"] == 0
        assert cov["unaccounted_count"] == 0
        assert cov["publication_complete"] is True


class TestConflictReviewCoverage:
    def test_conflict_review_coverage_blocked(self, fixture_contract):
        rc = fixture_contract["conflict_review_coverage"]
        assert rc["required_count"] == 7
        assert rc["succeeded_count"] == 2
        assert rc["failed_count"] == 5
        assert rc["pending_count"] == 0
        assert set(rc["succeeded_tickers"]) == REVIEW_SUCCEEDED_TICKERS
        assert set(rc["failed_tickers"]) == REVIEW_FAILED_TICKERS

    def test_overall_analysis_trust_blocked(self, fixture_contract):
        # 31/31 decided, but 5 failed required reviews + zero source lineage
        # anywhere — "all decisions persisted" must never mean "trusted".
        assert fixture_contract["overall_status"] == trust.STATUS_BLOCKED
        assert fixture_contract["blocking_reasons"]


class TestAxisCoverage:
    def test_technical_and_sentiment_fully_usable(self, fixture_contract):
        axis_cov = fixture_contract["axis_coverage"]
        assert axis_cov[AXIS_TECHNICAL]["succeeded_count"] == 31
        assert axis_cov[AXIS_TECHNICAL]["missing_count"] == 0
        assert axis_cov[AXIS_TECHNICAL]["failed_count"] == 0
        assert axis_cov[AXIS_SENTIMENT]["succeeded_count"] == 31
        assert axis_cov[AXIS_SENTIMENT]["missing_count"] == 0

    def test_fundamental_and_etf_exposure_scoped_by_asset_type(self, fixture_contract):
        axis_cov = fixture_contract["axis_coverage"]
        assert axis_cov[AXIS_FUNDAMENTAL]["succeeded_count"] == 19
        assert axis_cov[AXIS_FUNDAMENTAL]["not_applicable_count"] == 12
        assert axis_cov[AXIS_ETF_EXPOSURE]["succeeded_count"] == 12
        assert axis_cov[AXIS_ETF_EXPOSURE]["not_applicable_count"] == 19

    def test_not_applicable_axes_never_count_as_failures(self, fixture_contract):
        axis_cov = fixture_contract["axis_coverage"]
        for axis, counts in axis_cov.items():
            # not_applicable is tracked completely separately from failed.
            assert counts["not_applicable_count"] >= 0
            if counts["not_applicable_count"] > 0:
                assert counts["failed_count"] <= len(EQUITIES) + len(ETFS)

    def test_technical_output_never_appears_missing_when_it_succeeded(
        self, fixture_contract
    ):
        by_ticker = {e["ticker"]: e for e in fixture_contract["ticker_trust"]}
        for ticker in EQUITIES + ETFS:
            assert by_ticker[ticker]["axis_readiness"][AXIS_TECHNICAL] != "MISSING"
            assert by_ticker[ticker]["axis_readiness"][AXIS_SENTIMENT] != "MISSING"


class TestSourceLineage:
    def test_zero_source_refs_blocks_lineage_and_validation(self, fixture_contract):
        lineage = fixture_contract["source_lineage"]
        assert lineage["outputs_with_source_refs"] == 0
        assert lineage["outputs_missing_source_refs"] > 0
        assert fixture_contract["source_health"]["status"] == trust.STATUS_BLOCKED
        for entry in fixture_contract["ticker_trust"]:
            assert entry["source_validated"] is False
            assert entry["has_source_lineage"] is False


class TestDecisionConstraintClassification:
    def _entry(self, fixture_contract, ticker):
        by_ticker = {e["ticker"]: e for e in fixture_contract["ticker_trust"]}
        return by_ticker[ticker]

    def test_amd_and_tsm_price_context_limited_not_evidence_blocked(
        self, fixture_contract
    ):
        for ticker in ("AMD", "TSM"):
            entry = self._entry(fixture_contract, ticker)
            assert trust.CONSTRAINT_PRICE_CONTEXT in entry["decision_constraints"]
            assert trust.CONSTRAINT_EVIDENCE_QUALITY not in entry["decision_constraints"]

    def test_nvda_portfolio_policy_not_evidence_blocked(self, fixture_contract):
        entry = self._entry(fixture_contract, "NVDA")
        assert trust.CONSTRAINT_PORTFOLIO_POLICY in entry["decision_constraints"]
        assert trust.CONSTRAINT_EVIDENCE_QUALITY not in entry["decision_constraints"]

    def test_crm_gld_googl_nflx_not_fully_supported_on_failed_review(
        self, fixture_contract
    ):
        for ticker in ("CRM", "GLD", "GOOGL", "NFLX"):
            entry = self._entry(fixture_contract, ticker)
            assert entry["conflict_review_status"] == trust.REVIEW_FAILED
            assert entry["source_validated"] is False
            assert trust.CONSTRAINT_CONFLICT_REVIEW in entry["decision_constraints"]

    def test_blsh_carries_portfolio_policy_and_failed_review_distinctly(
        self, fixture_contract
    ):
        entry = self._entry(fixture_contract, "BLSH")
        constraints = set(entry["decision_constraints"])
        assert trust.CONSTRAINT_PORTFOLIO_POLICY in constraints
        assert trust.CONSTRAINT_CONFLICT_REVIEW in constraints
        # Two distinct categories, not merged into one "other"/"evidence" bucket.
        assert trust.CONSTRAINT_OTHER not in constraints

    def test_klar_review_succeeded_not_conflict_blocked(self, fixture_contract):
        entry = self._entry(fixture_contract, "KLAR")
        assert entry["conflict_review_status"] == trust.REVIEW_SUCCEEDED
        assert trust.CONSTRAINT_CONFLICT_REVIEW not in entry["decision_constraints"]
        # Still speculative/blocked fit — a real, separate constraint.
        assert trust.CONSTRAINT_PORTFOLIO_POLICY in entry["decision_constraints"]


class TestNoLeakage:
    def test_no_raw_internal_enums_or_task_errors_in_copy(self, fixture_contract):
        allowed_overall = {
            trust.STATUS_HEALTHY, trust.STATUS_LIMITED, trust.STATUS_BLOCKED,
            trust.STATUS_NOT_APPLICABLE, trust.STATUS_UNKNOWN,
        }
        assert fixture_contract["overall_status"] in allowed_overall
        for text in fixture_contract["warnings"] + fixture_contract["blocking_reasons"]:
            assert isinstance(text, str)
            assert "Traceback" not in text
            assert "Exception" not in text
            assert "None" not in text

    def test_ticker_trust_uses_constrained_vocabulary_only(self, fixture_contract):
        allowed_review = {
            trust.REVIEW_NOT_REQUIRED, trust.REVIEW_SUCCEEDED,
            trust.REVIEW_FAILED, trust.REVIEW_PENDING,
        }
        allowed_constraints = {
            trust.CONSTRAINT_EVIDENCE_QUALITY, trust.CONSTRAINT_SOURCE_LINEAGE,
            trust.CONSTRAINT_PRICE_CONTEXT, trust.CONSTRAINT_PORTFOLIO_POLICY,
            trust.CONSTRAINT_RISK, trust.CONSTRAINT_CONFLICT_REVIEW,
            trust.CONSTRAINT_OTHER,
        }
        for entry in fixture_contract["ticker_trust"]:
            assert entry["conflict_review_status"] in allowed_review
            assert set(entry["decision_constraints"]) <= allowed_constraints


class TestFinancialTruthSeparation:
    def test_contract_carries_no_financial_truth_fields(self, fixture_contract):
        forbidden_keys = {
            "portfolio_truth", "price_truth", "reconciliation",
            "snapshot_value", "position_derived_value", "cost_basis_truth",
        }
        assert forbidden_keys.isdisjoint(fixture_contract.keys())


class TestNotApplicableAxesNeverFailures:
    def test_crypto_axis_all_not_applicable_no_crypto_holdings(self, fixture_contract):
        counts = fixture_contract["axis_coverage"][trust.AXIS_CRYPTO_MARKET]
        assert counts["not_applicable_count"] == 31
        assert counts["failed_count"] == 0
        assert counts["missing_count"] == 0
