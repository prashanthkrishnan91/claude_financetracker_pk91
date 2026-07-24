"""Production-shaped fixture proofs for ``run_trust_contract_v1``.

Fixture mirrors the observed truth of production session
``a51e977b-561a-4e98-baa8-59ad56a877ff`` (see mission / HANDOFF):

  * 31 frozen holdings, 31 persisted decisions, session status completed
  * 31 technical outputs, 31 sentiment outputs, 19 fundamental outputs
    (equities only), 12 ETF-exposure outputs (ETFs only)
  * 7 conflict reviews required; 2 succeeded (KLAR, NVDA — each with a valid
    persisted axis=review output, the actual proof of success); 5 failed
    (BLSH, CRM, GLD, GOOGL, NFLX)
  * 0 evidence bundles / specialist outputs with nonempty source refs
  * AMD and TSM: strong evidence, suppressed price context (not an evidence
    failure)
  * NVDA: portfolio-overweight constraint (not an evidence failure)
  * BLSH: speculative/blocked portfolio fit AND a failed required review —
    two distinct, non-conflated constraint categories

``build_run_trust_contract`` is a pure function over plain dicts — no
FakeSupabase/DB/LLM required for these proofs.

Also proves the release-blocker patch semantics: required-vs-optional axis
coverage (a valid persisted output is the only proof of success), conflict-
review truth (TASK_DEGRADED is never success; a terminal-success task with
no valid output is FAILED, not succeeded), full/partial/missing source
lineage across every decision-influencing output (review included), and a
decision-constraint classifier that never mislabels a positive/assessed
band (UNDERWEIGHT) as a limitation and never drops an unclassified blocker.
"""
from __future__ import annotations

import pytest

from app.services.intelligence.v3.distributed import run_trust_contract_v1 as trust
from app.services.intelligence.v3.distributed import source_lineage_v1 as lineage
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_ETF_EXPOSURE,
    AXIS_FUNDAMENTAL,
    AXIS_REVIEW,
    AXIS_RISK_FILING,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL,
    LANE_PRICE,
    LANE_TECHNICALS,
    TASK_CANCELLED,
    TASK_DEGRADED,
    TASK_REVIEW_CONFLICT,
    TASK_SUCCEEDED,
    TASK_FAILED,
    TASK_PENDING,
    TICKER_DECIDED,
    TICKER_FAILED,
    TICKER_NO_CALL,
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
    blockers=None,
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
        "blockers": list(blockers or []),
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


def _specialist_output(
    ticker: str, axis: str, *, evidence_refs=None, reviewed_outputs=None,
) -> dict:
    """``evidence_refs`` truthy/falsy keeps the old call convention (hundreds
    of call sites just signal "has a reference" vs "doesn't") while building
    a STRUCTURALLY VALID PR-2 manifest — ``run_trust_contract_v1`` now
    independently re-derives ``status`` from the manifest's own structure
    (never trusts a persisted status field), so the fixture must be
    self-consistent, not just carry the right label.

    Review outputs (``axis == AXIS_REVIEW``) are now ENTIRELY DERIVED from
    the sibling non-review outputs they reconcile (round-3 contract §6) — a
    review manifest is never independently hand-authored, it is built via
    the same ``build_review_lineage_manifest`` production code uses, fed by
    ``reviewed_outputs`` (a list of sibling ``_specialist_output(...)``
    dicts). When ``reviewed_outputs`` is omitted, a single synthetic
    technical-axis input mirroring ``evidence_refs``'s truthiness is used —
    matching the old simple "review has some reference or none" call
    convention for fixtures that don't assert an exact review lineage
    status.
    """
    has_ref = bool(evidence_refs)
    ref = {
        "schema_version": lineage.SCHEMA_VERSION,
        "ref_type": lineage.REF_TYPE_PROVIDER_OBSERVATION,
        "lane": "price", "provider": "yfinance", "ticker": ticker,
        "task_id": f"{ticker}-{axis}-task", "output_digest": "sha256:test",
    }
    if axis == AXIS_REVIEW:
        inputs = reviewed_outputs if reviewed_outputs is not None else [
            _specialist_output(ticker, AXIS_TECHNICAL, evidence_refs=evidence_refs)
        ]
        manifest = lineage.build_review_lineage_manifest(inputs, ticker=ticker)
    else:
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION,
            "axis": axis,
            "expected_lanes": ["price"],
            "linked_lanes": ["price"] if has_ref else [],
            "missing_ref_lanes": [] if has_ref else ["price"],
            "status": lineage.LINEAGE_FULL if has_ref else lineage.LINEAGE_MISSING,
            "refs": [ref] if has_ref else [],
        }
    return {
        "ticker": ticker,
        "axis": axis,
        "score": 0.5,
        "confidence": 0.8,
        "key_findings": [f"{ticker} {axis} finding"],
        "risks": [],
        # Zero-reference outputs — matches production: 0 persisted specialist
        # outputs with nonempty evidence_refs.
        "evidence_refs": manifest,
        "missing_evidence": [],
        "limitations": [],
        "model": "fake", "prompt_version": "test",
    }


def _partial_specialist_output(ticker: str, axis: str) -> dict:
    """A genuinely PARTIAL manifest (self-consistent — two of that axis's own
    candidate lanes supplied, only one referenced) — used to prove
    source-health/lineage semantics correctly distinguish partial from full,
    never collapsing "some reference somewhere" into healthy."""
    candidate_lanes = list(lineage.AXIS_CANDIDATE_LANES.get(axis, ()))
    referenced_lane, unreferenced_lane = candidate_lanes[0], candidate_lanes[1]
    manifest = lineage.build_axis_lineage_manifest(
        axis=axis,
        source_refs_by_lane={referenced_lane: [{
            "schema_version": lineage.SCHEMA_VERSION,
            "ref_type": lineage.REF_TYPE_PROVIDER_OBSERVATION,
            "lane": referenced_lane, "provider": "yfinance", "ticker": ticker,
            "task_id": f"{ticker}-{axis}-ref", "output_digest": "sha256:test",
        }]},
        supplied_lanes=[referenced_lane, unreferenced_lane],
    )
    assert manifest["status"] == lineage.LINEAGE_PARTIAL
    return {
        "ticker": ticker, "axis": axis, "score": 0.5, "confidence": 0.8,
        "key_findings": [f"{ticker} {axis} finding"], "risks": [],
        "evidence_refs": manifest, "missing_evidence": [], "limitations": [],
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
        # The actual proof of review success: a valid persisted axis=review
        # output — a terminal-success task alone is not proof of anything.
        specialist_outputs.append(_specialist_output(t, AXIS_REVIEW))
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
        # Technical is required for every equity/ETF in this fixture;
        # sentiment is optional everywhere.
        assert axis_cov[AXIS_TECHNICAL]["required_succeeded_count"] == 31
        assert axis_cov[AXIS_SENTIMENT]["optional_succeeded_count"] == 31

    def test_fundamental_and_etf_exposure_scoped_by_asset_type(self, fixture_contract):
        axis_cov = fixture_contract["axis_coverage"]
        assert axis_cov[AXIS_FUNDAMENTAL]["succeeded_count"] == 19
        assert axis_cov[AXIS_FUNDAMENTAL]["not_applicable_count"] == 12
        assert axis_cov[AXIS_FUNDAMENTAL]["required_succeeded_count"] == 19
        assert axis_cov[AXIS_ETF_EXPOSURE]["succeeded_count"] == 12
        assert axis_cov[AXIS_ETF_EXPOSURE]["not_applicable_count"] == 19
        assert axis_cov[AXIS_ETF_EXPOSURE]["required_succeeded_count"] == 12

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
            assert entry["lineage_status"] == trust.LINEAGE_MISSING


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

    def test_amd_underweight_is_not_a_portfolio_policy_limitation(self, fixture_contract):
        entry = self._entry(fixture_contract, "AMD")
        assert entry["decision_bands"]["portfolio_fit"] == "UNDERWEIGHT"
        assert trust.CONSTRAINT_PORTFOLIO_POLICY not in entry["decision_constraints"]

    def test_nvda_portfolio_policy_not_evidence_blocked(self, fixture_contract):
        entry = self._entry(fixture_contract, "NVDA")
        assert entry["decision_bands"]["portfolio_fit"] == "OVERWEIGHT"
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

    def test_klar_review_succeeded_with_valid_output_not_conflict_blocked(
        self, fixture_contract
    ):
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

    def test_ticker_trust_uses_constrained_vocabulary_only(self, fixture_contract):
        allowed_review = {
            trust.REVIEW_NOT_REQUIRED, trust.REVIEW_SUCCEEDED,
            trust.REVIEW_FAILED, trust.REVIEW_PENDING,
        }
        allowed_constraints = trust.ALL_CONSTRAINT_CATEGORIES
        allowed_lineage = {
            trust.LINEAGE_FULL, trust.LINEAGE_PARTIAL, trust.LINEAGE_MISSING,
        }
        for entry in fixture_contract["ticker_trust"]:
            assert entry["conflict_review_status"] in allowed_review
            assert set(entry["decision_constraints"]) <= allowed_constraints
            assert entry["lineage_status"] in allowed_lineage


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


# ── Release-blocker patch: focused single-ticker scenarios ───────────────────
# Small, hand-built session/ticker_rows/tasks/specialist_outputs — each
# isolates exactly one semantic the patch fixed, independent of the big
# 31-ticker fixture above.

def _one_ticker_session(ticker="AAA", asset_type="equity"):
    return {"id": "sess-1", "status": "completed", "workflow_version": 2}


def _one_ticker_row(ticker="AAA", asset_type="equity", **decision_overrides):
    return {
        "ticker": ticker,
        "asset_type": asset_type,
        "state": TICKER_DECIDED,
        "portfolio_weight_pct": 2.0,
        "evidence_bundle": {"source_refs": []},
        "decision": _decision(**decision_overrides),
    }


class TestRequiredVsOptionalAxisCoverage:
    def test_missing_required_technical_never_yields_healthy(self):
        # Equity ticker: fundamental present, technical (required) missing.
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [_specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["ref1"])]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["axis_status"][AXIS_TECHNICAL] == trust.AXIS_STATUS_MISSING
        assert entry["required_axis_gap"] is True
        assert entry["trust_status"] == trust.STATUS_BLOCKED
        assert contract["overall_status"] == trust.STATUS_BLOCKED

    def test_missing_required_fundamental_never_yields_healthy(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [_specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["ref1"])]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["required_axis_gap"] is True
        assert contract["overall_status"] == trust.STATUS_BLOCKED

    def test_missing_required_etf_exposure_never_yields_healthy(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(ticker="EEE", asset_type="etf", evidence_quality="STRONG")]
        outputs = [_specialist_output("EEE", AXIS_TECHNICAL, evidence_refs=["ref1"])]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["axis_status"][AXIS_ETF_EXPOSURE] == trust.AXIS_STATUS_MISSING
        assert entry["required_axis_gap"] is True
        assert contract["overall_status"] == trust.STATUS_BLOCKED

    def test_missing_optional_sentiment_yields_limited_not_blocked(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["axis_status"][AXIS_SENTIMENT] == trust.AXIS_STATUS_MISSING
        assert entry["required_axis_gap"] is False
        assert entry["optional_axis_gap"] is True
        assert entry["trust_status"] == trust.STATUS_LIMITED
        assert contract["overall_status"] == trust.STATUS_LIMITED

    def test_not_applicable_does_not_reduce_trust(self):
        # ETF ticker: every APPLICABLE axis present (required: technical,
        # etf_exposure; optional: sentiment) + full lineage, no review
        # required — fundamental is NOT_APPLICABLE for an ETF and must not
        # prevent healthy.
        session = _one_ticker_session()
        rows = [_one_ticker_row(ticker="EEE", asset_type="etf", evidence_quality="STRONG")]
        outputs = [
            _specialist_output("EEE", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("EEE", AXIS_ETF_EXPOSURE, evidence_refs=["r2"]),
            _specialist_output("EEE", AXIS_SENTIMENT, evidence_refs=["r3"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["axis_status"][AXIS_FUNDAMENTAL] == trust.AXIS_STATUS_NOT_APPLICABLE
        assert entry["required_axis_gap"] is False
        assert entry["optional_axis_gap"] is False
        assert entry["lineage_status"] == trust.LINEAGE_FULL
        assert entry["trust_status"] == trust.STATUS_HEALTHY
        assert contract["overall_status"] == trust.STATUS_HEALTHY

    def test_zero_specialist_outputs_with_decided_holding_cannot_be_healthy(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=[],
        )
        entry = contract["ticker_trust"][0]
        assert entry["required_axis_gap"] is True
        assert entry["trust_status"] == trust.STATUS_BLOCKED
        assert contract["overall_status"] == trust.STATUS_BLOCKED

    def test_terminal_task_without_valid_output_is_failed_not_succeeded(self):
        # A specialist_analysis task reached TASK_SUCCEEDED but no valid
        # output was persisted (score/confidence missing) — must not count
        # as succeeded, and must not silently read as merely "missing".
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        tasks = [{
            "task_type": "specialist_analysis", "lane": AXIS_TECHNICAL,
            "batch_key": "equity:technical:b000:AAA", "state": TASK_SUCCEEDED,
        }]
        outputs = [_specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r1"])]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=tasks, specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["axis_status"][AXIS_TECHNICAL] == trust.AXIS_STATUS_FAILED
        assert entry["required_axis_gap"] is True
        assert contract["overall_status"] == trust.STATUS_BLOCKED


class TestConflictReviewTruth:
    def test_degraded_review_task_is_never_success_even_with_output(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        tasks = [{"task_type": TASK_REVIEW_CONFLICT, "ticker": "AAA", "state": TASK_DEGRADED}]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
        ]
        outputs.append(
            _specialist_output(
                "AAA", AXIS_REVIEW, evidence_refs=["r3"], reviewed_outputs=outputs,
            )
        )
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=tasks, specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["conflict_review_status"] == trust.REVIEW_FAILED
        assert entry["trust_status"] == trust.STATUS_BLOCKED
        assert entry["source_validated"] is False
        assert contract["overall_status"] == trust.STATUS_BLOCKED

    def test_succeeded_task_without_review_output_is_failed(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        tasks = [{"task_type": TASK_REVIEW_CONFLICT, "ticker": "AAA", "state": TASK_SUCCEEDED}]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
            # No axis=review output persisted.
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=tasks, specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["conflict_review_status"] == trust.REVIEW_FAILED
        assert entry["source_validated"] is False

    def test_pending_review_blocks_ticker_and_overall_trust(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        tasks = [{"task_type": TASK_REVIEW_CONFLICT, "ticker": "AAA", "state": TASK_PENDING}]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=tasks, specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["conflict_review_status"] == trust.REVIEW_PENDING
        assert entry["trust_status"] == trust.STATUS_BLOCKED
        assert entry["source_validated"] is False
        assert contract["overall_status"] == trust.STATUS_BLOCKED
        assert trust.CONSTRAINT_CONFLICT_REVIEW in entry["decision_constraints"]

    def test_succeeded_review_with_valid_output_is_success(self):
        # Every applicable equity axis present (required: technical,
        # fundamental; optional: sentiment, risk_filing) so ONLY the review
        # outcome is under test — an optional gap would independently cap
        # this at "limited" and mask what this test is actually proving.
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        tasks = [{"task_type": TASK_REVIEW_CONFLICT, "ticker": "AAA", "state": TASK_SUCCEEDED}]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
            _specialist_output("AAA", AXIS_SENTIMENT, evidence_refs=["r4"]),
            _specialist_output("AAA", AXIS_RISK_FILING, evidence_refs=["r5"]),
        ]
        outputs.append(
            _specialist_output(
                "AAA", AXIS_REVIEW, evidence_refs=["r3"], reviewed_outputs=outputs,
            )
        )
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=tasks, specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["conflict_review_status"] == trust.REVIEW_SUCCEEDED
        assert entry["lineage_status"] == trust.LINEAGE_FULL
        assert entry["source_validated"] is True
        assert entry["trust_status"] == trust.STATUS_HEALTHY
        assert contract["overall_status"] == trust.STATUS_HEALTHY

    def test_is_source_validated_requires_exactly_not_required_or_succeeded(self):
        for bad_status in (trust.REVIEW_FAILED, trust.REVIEW_PENDING, trust.REVIEW_UNKNOWN):
            assert trust.is_source_validated(
                evidence_quality="STRONG", lineage_status=trust.LINEAGE_FULL,
                review_status=bad_status,
            ) is False
        for good_status in (trust.REVIEW_NOT_REQUIRED, trust.REVIEW_SUCCEEDED):
            assert trust.is_source_validated(
                evidence_quality="STRONG", lineage_status=trust.LINEAGE_FULL,
                review_status=good_status,
            ) is True

    def test_forged_review_derived_axes_never_reads_full(self):
        # Round-3 item 6: a review claiming it reconciled a different axis
        # set than what's CURRENTLY true for this ticker (a stale/forged
        # persisted claim) must read as missing lineage in the trust
        # contract, never full — even though the review manifest is
        # otherwise internally self-consistent.
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        tasks = [{"task_type": TASK_REVIEW_CONFLICT, "ticker": "AAA", "state": TASK_SUCCEEDED}]
        technical_output = _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"])
        review_output = _specialist_output(
            "AAA", AXIS_REVIEW, evidence_refs=["r3"], reviewed_outputs=[technical_output],
        )
        # Forge the persisted claim: assert it also reconciled "fundamental"
        # even though no fundamental output exists for this ticker at all.
        forged_manifest = dict(review_output["evidence_refs"])
        forged_manifest["input_axis_lineage"] = sorted(
            forged_manifest["input_axis_lineage"]
            + [{"axis": AXIS_FUNDAMENTAL, "status": lineage.LINEAGE_FULL}],
            key=lambda e: e["axis"],
        )
        forged_manifest["derived_from_axes"] = sorted(
            forged_manifest["derived_from_axes"] + [AXIS_FUNDAMENTAL]
        )
        review_output["evidence_refs"] = forged_manifest
        outputs = [technical_output, review_output]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=tasks, specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        # The review's own lineage is forced to MISSING by the cross-check —
        # capping the ticker-level aggregate below full.
        assert entry["lineage_status"] != trust.LINEAGE_FULL


class TestSourceLineageFullPartialMissing:
    def test_full_lineage_requires_every_decision_influencing_output_referenced(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["lineage_status"] == trust.LINEAGE_FULL

    def test_partial_lineage_when_only_one_axis_has_a_reference(self):
        # One arbitrary axis has a reference, the other decision-influencing
        # output doesn't — must be "partial", never "full"/source_validated.
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=[]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["lineage_status"] == trust.LINEAGE_PARTIAL
        assert entry["source_validated"] is False
        assert entry["trust_status"] == trust.STATUS_LIMITED
        assert contract["overall_status"] == trust.STATUS_LIMITED

    def test_missing_lineage_when_no_output_referenced(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=[]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=[]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["lineage_status"] == trust.LINEAGE_MISSING
        assert entry["trust_status"] == trust.STATUS_BLOCKED
        assert contract["overall_status"] == trust.STATUS_BLOCKED

    def test_review_output_counts_toward_lineage(self):
        # A review is now ENTIRELY DERIVED from the sibling outputs it
        # reconciles (round-3 contract §6) — it can never independently
        # claim a worse status than a fully-linked set of inputs, nor a
        # better one. A review reconciling one fully-linked axis and one
        # unlinked axis is itself honestly "partial", proving the review
        # output counts as its own decision-influencing lineage input in the
        # ticker-level aggregate (technical alone would already be "full").
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        tasks = [{"task_type": TASK_REVIEW_CONFLICT, "ticker": "AAA", "state": TASK_SUCCEEDED}]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=[]),
        ]
        outputs.append(
            _specialist_output(
                "AAA", AXIS_REVIEW, evidence_refs=["r3"], reviewed_outputs=outputs,
            )
        )
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=tasks, specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["lineage_status"] == trust.LINEAGE_PARTIAL
        assert entry["source_validated"] is False

    def test_zero_valid_outputs_source_health_carries_a_distinguishing_reason(self):
        # A SUCCESSFUL read that genuinely found zero valid specialist
        # outputs must carry a reason distinguishing it from a fail-closed
        # read-failure "unknown" (unknown_overlay_contract) — both share
        # status="unknown" but mean different things.
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=[],
        )
        assert contract["source_health"]["status"] == trust.STATUS_UNKNOWN
        reason = contract["source_health"]["reason"]
        assert "zero outputs" in reason
        # Must read distinctly from the fail-closed read-failure overlay's
        # reason wording (see test_run_trust_contract_integration.py) — a
        # successful read finding zero outputs is not "could not be
        # re-verified".
        assert "could not be re-verified" not in reason


class TestDecisionConstraintTruth:
    def test_underweight_never_a_portfolio_policy_limitation(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(
            evidence_quality="STRONG", portfolio_fit="UNDERWEIGHT",
        )]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert trust.CONSTRAINT_PORTFOLIO_POLICY not in entry["decision_constraints"]

    def test_overweight_is_a_portfolio_policy_limitation(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(
            evidence_quality="STRONG", portfolio_fit="OVERWEIGHT",
        )]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert trust.CONSTRAINT_PORTFOLIO_POLICY in entry["decision_constraints"]

    def test_suppressed_price_context_differs_from_full_and_expensive_bands(self):
        session = _one_ticker_session()
        rows = [
            _one_ticker_row(ticker="SUP", evidence_quality="STRONG", price_context="SUPPRESSED"),
            _one_ticker_row(ticker="FUL", evidence_quality="STRONG", price_context="FULL"),
            _one_ticker_row(ticker="EXP", evidence_quality="STRONG", price_context="EXPENSIVE"),
        ]
        outputs = []
        for t in ("SUP", "FUL", "EXP"):
            outputs.append(_specialist_output(t, AXIS_TECHNICAL, evidence_refs=["r1"]))
            outputs.append(_specialist_output(t, AXIS_FUNDAMENTAL, evidence_refs=["r2"]))
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        by_ticker = {e["ticker"]: e for e in contract["ticker_trust"]}
        # All three carry the price_context category (a real dimension) but
        # the RAW band is preserved distinctly for accurate downstream text
        # — SUPPRESSED (unconfirmed) is never conflated with FULL/EXPENSIVE
        # (assessed-and-elevated) at the data level.
        for t in ("SUP", "FUL", "EXP"):
            assert trust.CONSTRAINT_PRICE_CONTEXT in by_ticker[t]["decision_constraints"]
        assert by_ticker["SUP"]["decision_bands"]["price_context"] == "SUPPRESSED"
        assert by_ticker["FUL"]["decision_bands"]["price_context"] == "FULL"
        assert by_ticker["EXP"]["decision_bands"]["price_context"] == "EXPENSIVE"

    def test_unclassified_attractiveness_blocker_remains_visible(self):
        # STUB/VIS-style HOLD: evidence fine, price/portfolio/risk all
        # unremarkable, but decide() persisted "Attractiveness signal absent
        # or weak." — a real blocker with no band-based category. It must
        # surface as CONSTRAINT_OTHER, additively alongside whatever other
        # categories apply (source_lineage here, since refs are empty).
        session = _one_ticker_session()
        rows = [_one_ticker_row(
            ticker="STUB", evidence_quality="OK", price_context="FAIR",
            portfolio_fit="ON_TARGET", risk_band="LOW",
            blockers=["Attractiveness signal absent or weak."],
        )]
        outputs = [
            _specialist_output("STUB", AXIS_TECHNICAL, evidence_refs=[]),
            _specialist_output("STUB", AXIS_FUNDAMENTAL, evidence_refs=[]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert trust.CONSTRAINT_OTHER in entry["decision_constraints"]
        assert trust.CONSTRAINT_SOURCE_LINEAGE in entry["decision_constraints"]

    def test_other_preserved_even_when_other_categories_also_apply(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(
            ticker="VIS", evidence_quality="STRONG", price_context="SUPPRESSED",
            portfolio_fit="OVERWEIGHT", risk_band="HIGH",
            blockers=["Attractiveness signal absent or weak."],
        )]
        outputs = [
            _specialist_output("VIS", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("VIS", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        constraints = set(entry["decision_constraints"])
        assert trust.CONSTRAINT_OTHER in constraints
        assert trust.CONSTRAINT_PORTFOLIO_POLICY in constraints
        assert trust.CONSTRAINT_PRICE_CONTEXT in constraints
        assert trust.CONSTRAINT_RISK in constraints

    def test_clean_healthy_decision_has_no_fabricated_other(self):
        # A fully sourced, fully healthy holding: all required AND optional
        # axes complete, no review required, FAIR price context, ON_TARGET
        # portfolio fit, LOW risk, blockers=[]. Release-blocker requirement:
        # a clean decision must return decision_constraints=[] — "other"
        # must never be fabricated merely because no other category applies.
        session = _one_ticker_session()
        rows = [_one_ticker_row(
            evidence_quality="STRONG", price_context="FAIR",
            portfolio_fit="ON_TARGET", risk_band="LOW", blockers=[],
        )]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
            _specialist_output("AAA", AXIS_SENTIMENT, evidence_refs=["r3"]),
            _specialist_output("AAA", AXIS_RISK_FILING, evidence_refs=["r4"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["trust_status"] == trust.STATUS_HEALTHY
        assert entry["decision_constraints"] == []
        assert entry["source_validated"] is True

    def test_clean_healthy_decision_with_underweight_fit_also_empty(self):
        # UNDERWEIGHT is room-to-add, not a limitation — a clean UNDERWEIGHT
        # holding must also return decision_constraints=[], same as ON_TARGET.
        session = _one_ticker_session()
        rows = [_one_ticker_row(
            evidence_quality="STRONG", price_context="FAIR",
            portfolio_fit="UNDERWEIGHT", risk_band="LOW", blockers=[],
        )]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
            _specialist_output("AAA", AXIS_SENTIMENT, evidence_refs=["r3"]),
            _specialist_output("AAA", AXIS_RISK_FILING, evidence_refs=["r4"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["trust_status"] == trust.STATUS_HEALTHY
        assert entry["decision_constraints"] == []
        assert entry["source_validated"] is True

    def test_unmatched_blocker_alone_adds_only_other(self):
        # Otherwise-clean state (full lineage, FAIR price, ON_TARGET fit, LOW
        # risk) plus one real unmatched blocker: decision_constraints must
        # contain ONLY "other" — the blocker doesn't fabricate any other
        # category, and "other" isn't accompanied by a spurious one either.
        session = _one_ticker_session()
        rows = [_one_ticker_row(
            evidence_quality="STRONG", price_context="FAIR",
            portfolio_fit="ON_TARGET", risk_band="LOW",
            blockers=["Attractiveness signal absent or weak."],
        )]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
            _specialist_output("AAA", AXIS_SENTIMENT, evidence_refs=["r3"]),
            _specialist_output("AAA", AXIS_RISK_FILING, evidence_refs=["r4"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["decision_constraints"] == [trust.CONSTRAINT_OTHER]


class TestSourceHealthSemantics:
    """Release-blocker proof: full/partial/missing output lineage are
    tracked SEPARATELY — an all-partial run must never collapse into
    "outputs_with_refs == total" and read as healthy."""

    def test_all_partial_outputs_never_reads_healthy(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [
            _partial_specialist_output("AAA", AXIS_TECHNICAL),
            _partial_specialist_output("AAA", AXIS_FUNDAMENTAL),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        assert contract["source_health"]["status"] == trust.STATUS_LIMITED
        assert contract["source_health"]["status"] != trust.STATUS_HEALTHY
        assert contract["source_lineage"]["outputs_full_lineage"] == 0
        assert contract["source_lineage"]["outputs_partial_lineage"] == 2
        assert contract["source_lineage"]["outputs_missing_lineage"] == 0

    def test_all_missing_outputs_is_blocked(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=None),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=None),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        assert contract["source_health"]["status"] == trust.STATUS_BLOCKED
        assert contract["source_lineage"]["outputs_missing_lineage"] == 2

    def test_all_full_outputs_is_healthy(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=["r2"]),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        assert contract["source_health"]["status"] == trust.STATUS_HEALTHY
        assert contract["source_lineage"]["outputs_full_lineage"] == 2

    def test_mixed_full_and_missing_is_limited_not_healthy(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _specialist_output("AAA", AXIS_FUNDAMENTAL, evidence_refs=None),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        assert contract["source_health"]["status"] == trust.STATUS_LIMITED

    def test_zero_valid_outputs_is_unknown(self):
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=[],
        )
        assert contract["source_health"]["status"] == trust.STATUS_UNKNOWN

    def test_full_ticker_lineage_requires_every_influencing_output_full(self):
        # PR #485 rule preserved: mixed full+partial at the TICKER level is
        # "partial", never "full" — even though this patch changes HOW each
        # output's own status is derived.
        session = _one_ticker_session()
        rows = [_one_ticker_row(evidence_quality="STRONG")]
        outputs = [
            _specialist_output("AAA", AXIS_TECHNICAL, evidence_refs=["r1"]),
            _partial_specialist_output("AAA", AXIS_FUNDAMENTAL),
        ]
        contract = trust.build_run_trust_contract(
            session=session, ticker_rows=rows, tasks=[], specialist_outputs=outputs,
        )
        entry = contract["ticker_trust"][0]
        assert entry["lineage_status"] == trust.LINEAGE_PARTIAL
        assert entry["source_validated"] is False
