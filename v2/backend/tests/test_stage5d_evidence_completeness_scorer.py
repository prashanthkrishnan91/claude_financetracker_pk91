"""Stage 5D focused tests — Evidence Completeness Scorer v1.

Acceptance criteria verified:
  1.  No sources + no facts → NOT_EVALUABLE, is_evaluable=False, missing=both.
  2.  Source but no facts → THIN, missing=[has_at_least_one_fact].
  3.  Facts but no sources → THIN, missing=[has_at_least_one_source].
  4.  Unknown-only source → cannot be COMPLETE (THIN).
  5.  Editorial/news-only source → cannot be COMPLETE (THIN).
  6.  sec_filing + structured quote-grounded metric fact + period/as_of
      + no contradictions → COMPLETE (strongest allowed band).
  7.  contradiction_count > 0 → prevents COMPLETE (PARTIAL).
  8.  Non-comparable facts (no claim_key/value) → prevents COMPLETE (THIN).
  9.  Missing period/as_of → reflected in missing requirements (PARTIAL).
  10. write_artifact injects evidence_completeness_assessment without
      forbidden keys.
  11. source_credibility_assessment and contradiction_assessment still exist
      after write (prior stages intact).
  12. Idempotent replay still works (completeness not re-added on skip).
  13. Clean replacement still works for ticker scope.
  14. Clean replacement still works for portfolio scope.
  15. No writes to intel_v3_snapshots or recommendations tables.
  16. Assessment is replayable: same inputs → same output.
  17. scorer_version embedded in every assessment.
  18. no_guessing is always True.
  19. vendor_derived source + comparable fact → can reach COMPLETE or PARTIAL.
  20. Missing quote grounding → PARTIAL.
  21. company_authored source + comparable fact → can reach COMPLETE.
  22. Mixed sources with at least one PRIMARY_AUTHORITY → credible path.
  23. per_fact_assessments present with deterministic structure.
  24. not_applicable requirements when fact_count == 0.
  25. contradiction_count is None when contradiction not evaluable.
  26. has_no_detected_contradictions not_applicable when no comparable facts.

No production Supabase dependency — all fakes defined locally.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from app.services.intelligence.research_workers.contracts import (
    AuditEventRecord,
    FactRecord,
    SourceRecord,
    WorkerOutput,
    WORKER_FORBIDDEN_PAYLOAD_KEYS,
    compute_input_fingerprint,
    compute_replay_idempotency_key,
)
from app.services.intelligence.v3.contradiction_detector_v1 import (
    detect_contradictions,
)
from app.services.intelligence.v3.evidence_completeness_scorer_v1 import (
    BAND_COMPLETE,
    BAND_NOT_EVALUABLE,
    BAND_PARTIAL,
    BAND_THIN,
    EVIDENCE_COMPLETENESS_SCORER_VERSION,
    REQ_MISSING,
    REQ_NOT_APPLICABLE,
    REQ_PRESENT,
    EvidenceCompletenessAssessment,
    score_evidence_completeness,
)
from app.services.intelligence.v3.source_credibility_registry_v1 import (
    assess_artifact_sources,
)
from app.services.intelligence.v3.research_artifact_service_v1 import (
    ResearchArtifactServiceV1,
)


# ── Fake Supabase infrastructure ──────────────────────────────────────────────

@dataclass
class _FakeResult:
    data: list[dict]


class _FakeTable:
    def __init__(self, table_name: str, shared: "_FakeDB") -> None:
        self._name = table_name
        self._shared = shared
        self._op: Optional[str] = None
        self._payload: Optional[dict] = None
        self._filters: dict[str, Any] = {}
        self._neg_filters: dict[str, Any] = {}
        self._null_filters: dict[str, bool] = {}
        self._update_patch: Optional[dict] = None
        self._select_cols: Optional[str] = None
        self._limit_n: Optional[int] = None
        self._order_by: Optional[str] = None
        self._order_desc: bool = False

    def select(self, cols: str = "*") -> "_FakeTable":
        self._op = "select"
        self._select_cols = cols
        return self

    def insert(self, row: dict) -> "_FakeTable":
        self._op = "insert"
        self._payload = row
        return self

    def update(self, patch: dict) -> "_FakeTable":
        self._op = "update"
        self._update_patch = patch
        return self

    def eq(self, col: str, val: Any) -> "_FakeTable":
        self._filters[col] = val
        return self

    def neq(self, col: str, val: Any) -> "_FakeTable":
        self._neg_filters[col] = val
        return self

    def is_(self, col: str, val: Any) -> "_FakeTable":
        self._null_filters[col] = (val == "null")
        return self

    def order(self, col: str, desc: bool = False) -> "_FakeTable":
        self._order_by = col
        self._order_desc = desc
        return self

    def limit(self, n: int) -> "_FakeTable":
        self._limit_n = n
        return self

    def execute(self) -> Any:
        db = self._shared
        if self._op == "insert" and self._payload:
            row = dict(self._payload)
            row.setdefault("id", str(uuid.uuid4()))
            db._rows.setdefault(self._name, []).append(row)
            return _FakeResult([row])

        if self._op == "update":
            updated = []
            for row in db._rows.get(self._name, []):
                match = all(row.get(k) == v for k, v in self._filters.items())
                neg_match = all(row.get(k) != v for k, v in self._neg_filters.items())
                null_match = all(
                    (row.get(k) is None) == is_null
                    for k, is_null in self._null_filters.items()
                )
                if match and neg_match and null_match:
                    row.update(self._update_patch or {})
                    updated.append(row)
            return _FakeResult(updated)

        if self._op == "select":
            rows = list(db._rows.get(self._name, []))
            for k, v in self._filters.items():
                rows = [r for r in rows if r.get(k) == v]
            for k, v in self._neg_filters.items():
                rows = [r for r in rows if r.get(k) != v]
            for k, is_null in self._null_filters.items():
                rows = [r for r in rows if (r.get(k) is None) == is_null]
            if self._limit_n:
                rows = rows[:self._limit_n]
            return _FakeResult(rows)

        return _FakeResult([])


class _FakeDB:
    def __init__(self) -> None:
        self._rows: dict[str, list[dict]] = {}
        self._blocked_tables: set[str] = set()

    def table(self, name: str) -> _FakeTable:
        if name in self._blocked_tables:
            raise RuntimeError(f"Table '{name}' is blocked in this test scenario")
        return _FakeTable(name, self)


# ── Builder helpers ───────────────────────────────────────────────────────────

def _make_key(ticker: str = "AAPL", suffix: str = "") -> str:
    return compute_replay_idempotency_key(
        skill_pack="test_worker",
        scope_kind="ticker",
        ticker=ticker + suffix,
        source_refs_fingerprint="fp_5d",
        model_version="v1",
    )


def _make_output(
    *,
    ticker: str = "AAPL",
    idempotency_key: Optional[str] = None,
    facts: Optional[list] = None,
    sources: Optional[list] = None,
    extra_payload: Optional[dict] = None,
    scope_kind: str = "ticker",
) -> WorkerOutput:
    key = idempotency_key or _make_key(ticker)
    return WorkerOutput(
        worker_run_id=str(uuid.uuid4()),
        ticker=ticker if scope_kind == "ticker" else None,
        artifact_type="technical_signal",
        skill_pack="test_worker",
        scope_kind=scope_kind,
        artifact_payload={"evidence_summary": "test", **(extra_payload or {})},
        sources=sources or [],
        facts=facts or [],
        audit_events=[AuditEventRecord(tool_call="test", status="completed")],
        evidence_summary_plain_english="test",
        limitations_or_missing_evidence=[],
        confidence_or_trust_level="MEDIUM",
        freshness_status="FRESH",
        input_fingerprint=compute_input_fingerprint({"ticker": ticker}),
        replay_idempotency_key=key,
    )


def _make_source(source_kind: str = "sec_filing", provider: str = "edgar") -> SourceRecord:
    return SourceRecord(
        source_kind=source_kind,
        provider_name=provider,
        source_published_at="2025-01-01T00:00:00Z",
    )


def _make_comparable_fact(
    *,
    claim_key: str = "revenue",
    value: float = 1.0,
    period: str = "Q1-2025",
    as_of: Optional[str] = None,
    is_quote_grounded: bool = False,
    fact_kind: str = "metric_observation",
    source_index: int = 0,
) -> FactRecord:
    sp: dict[str, Any] = {"claim_key": claim_key, "value": value}
    if period:
        sp["period"] = period
    if as_of:
        sp["as_of"] = as_of
    return FactRecord(
        fact_kind=fact_kind,
        structured_payload=sp,
        period=period,
        as_of=as_of,
        is_quote_grounded=is_quote_grounded,
        source_index=source_index,
    )


def _make_noncomparable_fact() -> FactRecord:
    """A fact with no claim_key/metric_name and no value field."""
    return FactRecord(
        fact_kind="quality_observation",
        structured_payload={"note": "revenue grew substantially"},
    )


def _score(sources, facts):
    """Run the full pipeline (credibility + contradiction → completeness)."""
    cred = assess_artifact_sources(sources)
    contra = detect_contradictions(facts)
    return score_evidence_completeness(sources, facts, cred, contra)


# ── Pure scorer unit tests ────────────────────────────────────────────────────

class TestNotEvaluable:
    def test_no_sources_no_facts(self):
        result = _score([], [])
        assert result.completeness_band == BAND_NOT_EVALUABLE
        assert result.is_evaluable is False
        assert "has_at_least_one_source" in result.missing_requirements
        assert "has_at_least_one_fact" in result.missing_requirements
        assert result.source_count == 0
        assert result.fact_count == 0

    def test_scorer_version_always_set(self):
        result = _score([], [])
        assert result.scorer_version == EVIDENCE_COMPLETENESS_SCORER_VERSION

    def test_no_guessing_always_true(self):
        result = _score([], [])
        assert result.no_guessing is True


class TestThinBand:
    def test_source_but_no_facts(self):
        result = _score([_make_source("sec_filing")], [])
        assert result.completeness_band == BAND_THIN
        assert result.is_evaluable is True
        assert "has_at_least_one_fact" in result.missing_requirements
        assert "has_at_least_one_source" in result.present_requirements

    def test_facts_but_no_sources(self):
        fact = _make_comparable_fact(is_quote_grounded=True)
        result = _score([], [fact])
        assert result.completeness_band == BAND_THIN
        assert result.is_evaluable is True
        assert "has_at_least_one_source" in result.missing_requirements

    def test_unknown_only_source_is_thin(self):
        result = _score([_make_source("other")], [_make_comparable_fact(is_quote_grounded=True)])
        assert result.completeness_band == BAND_THIN
        assert "has_known_or_contextual_source_credibility" in result.missing_requirements

    def test_editorial_news_only_source_is_thin(self):
        result = _score(
            [_make_source("news")],
            [_make_comparable_fact(is_quote_grounded=True)],
        )
        assert result.completeness_band == BAND_THIN

    def test_noncomparable_facts_only_is_thin(self):
        result = _score(
            [_make_source("sec_filing")],
            [_make_noncomparable_fact()],
        )
        assert result.completeness_band == BAND_THIN
        assert "has_comparable_fact_when_claim_is_metric_like" in result.missing_requirements

    def test_unknown_source_cannot_be_complete(self):
        """Hard invariant: unknown-only source never reaches COMPLETE."""
        result = _score(
            [_make_source("other")],
            [_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)],
        )
        assert result.completeness_band != BAND_COMPLETE

    def test_editorial_source_cannot_be_complete(self):
        """Hard invariant: editorial-only source never reaches COMPLETE."""
        result = _score(
            [_make_source("news")],
            [_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)],
        )
        assert result.completeness_band != BAND_COMPLETE


class TestPartialBand:
    def test_contradiction_prevents_complete(self):
        """Two facts with same claim_key/period but different values → contradiction → PARTIAL."""
        sources = [_make_source("sec_filing")]
        facts = [
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={"claim_key": "revenue", "value": 100.0, "period": "Q1-2025"},
                period="Q1-2025",
                is_quote_grounded=True,
            ),
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={"claim_key": "revenue", "value": 200.0, "period": "Q1-2025"},
                period="Q1-2025",
                is_quote_grounded=True,
            ),
        ]
        result = _score(sources, facts)
        assert result.completeness_band == BAND_PARTIAL
        assert "has_no_detected_contradictions" in result.missing_requirements
        assert result.contradiction_count == 1

    def test_missing_period_as_of_is_partial(self):
        """Comparable fact with no period/as_of → PARTIAL with has_time_context in missing."""
        sources = [_make_source("sec_filing")]
        facts = [
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={"claim_key": "revenue", "value": 100.0},
                is_quote_grounded=True,
            )
        ]
        result = _score(sources, facts)
        assert result.completeness_band == BAND_PARTIAL
        assert "has_time_context_period_or_as_of" in result.missing_requirements

    def test_missing_quote_grounding_is_partial(self):
        """No quote-grounded fact → PARTIAL."""
        sources = [_make_source("sec_filing")]
        facts = [_make_comparable_fact(period="Q1-2025", is_quote_grounded=False)]
        result = _score(sources, facts)
        assert result.completeness_band == BAND_PARTIAL
        assert "has_quote_grounded_fact" in result.missing_requirements

    def test_vendor_derived_with_good_structure_can_be_partial_or_complete(self):
        """vendor_derived is above EDITORIAL_CONTEXT; can reach PARTIAL or COMPLETE."""
        sources = [_make_source("vendor_fundamentals")]
        facts = [_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)]
        result = _score(sources, facts)
        assert result.completeness_band in (BAND_PARTIAL, BAND_COMPLETE)


class TestCompleteBand:
    def test_sec_filing_structured_complete(self):
        """Best case: sec_filing + comparable + period + quote-grounded + no contradictions."""
        sources = [_make_source("sec_filing")]
        facts = [_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)]
        result = _score(sources, facts)
        assert result.completeness_band == BAND_COMPLETE
        assert result.is_evaluable is True
        assert result.source_count == 1
        assert result.fact_count == 1
        assert result.quote_grounded_fact_count == 1
        assert result.comparable_fact_count == 1
        assert result.contradiction_count == 0

    def test_company_disclosure_structured_complete(self):
        """company_disclosure (COMPANY_AUTHORED) + good fact → COMPLETE."""
        sources = [_make_source("company_disclosure")]
        facts = [_make_comparable_fact(period="Q2-2025", is_quote_grounded=True)]
        result = _score(sources, facts)
        assert result.completeness_band == BAND_COMPLETE

    def test_transcript_source_complete(self):
        sources = [_make_source("transcript")]
        facts = [_make_comparable_fact(period="Q3-2025", is_quote_grounded=True)]
        result = _score(sources, facts)
        assert result.completeness_band == BAND_COMPLETE

    def test_complete_has_no_missing_requirements(self):
        sources = [_make_source("sec_filing")]
        facts = [_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)]
        result = _score(sources, facts)
        assert result.missing_requirements == []

    def test_complete_all_requirements_present_or_not_applicable(self):
        sources = [_make_source("sec_filing")]
        facts = [_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)]
        result = _score(sources, facts)
        for req in result.present_requirements + result.not_applicable_requirements:
            assert req not in result.missing_requirements


class TestRequirements:
    def test_not_applicable_when_no_facts(self):
        """When fact_count == 0, fact-related requirements are not_applicable."""
        result = _score([_make_source("sec_filing")], [])
        assert "has_structured_claim_key_or_metric_name" in result.not_applicable_requirements
        assert "has_time_context_period_or_as_of" in result.not_applicable_requirements
        assert "has_quote_grounded_fact" in result.not_applicable_requirements
        assert "has_comparable_fact_when_claim_is_metric_like" in result.not_applicable_requirements
        assert "has_no_detected_contradictions" in result.not_applicable_requirements

    def test_not_applicable_credibility_when_no_sources(self):
        fact = _make_comparable_fact(period="Q1-2025", is_quote_grounded=True)
        result = _score([], [fact])
        assert "has_known_or_contextual_source_credibility" in result.not_applicable_requirements

    def test_has_no_detected_contradictions_not_applicable_when_non_comparable(self):
        """When contradiction is not evaluable, requirement is not_applicable."""
        sources = [_make_source("sec_filing")]
        facts = [_make_noncomparable_fact()]
        result = _score(sources, facts)
        assert "has_no_detected_contradictions" in result.not_applicable_requirements

    def test_contradiction_count_none_when_not_evaluable(self):
        sources = [_make_source("sec_filing")]
        facts = []
        result = _score(sources, facts)
        assert result.contradiction_count is None

    def test_contradiction_count_set_when_evaluable(self):
        sources = [_make_source("sec_filing")]
        facts = [_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)]
        result = _score(sources, facts)
        assert result.contradiction_count == 0


class TestPerFactAssessments:
    def test_per_fact_assessments_structure(self):
        sources = [_make_source("sec_filing")]
        facts = [
            _make_comparable_fact(period="Q1-2025", is_quote_grounded=True),
            _make_noncomparable_fact(),
        ]
        result = _score(sources, facts)
        assert len(result.per_fact_assessments) == 2

        pf0 = result.per_fact_assessments[0]
        assert pf0["fact_index"] == 0
        assert pf0["has_claim_key"] is True
        assert pf0["has_value_field"] is True
        assert pf0["is_comparable"] is True
        assert pf0["has_time_context"] is True
        assert pf0["is_quote_grounded"] is True

        pf1 = result.per_fact_assessments[1]
        assert pf1["fact_index"] == 1
        assert pf1["has_claim_key"] is False
        assert pf1["is_comparable"] is False

    def test_per_fact_assessments_no_facts(self):
        result = _score([], [])
        assert result.per_fact_assessments == []

    def test_per_fact_no_forbidden_fields(self):
        """Per-fact assessments must not include fact values or forbidden keys."""
        sources = [_make_source("sec_filing")]
        facts = [_make_comparable_fact(period="Q1-2025", value=42.5)]
        result = _score(sources, facts)
        for pf in result.per_fact_assessments:
            for fk in WORKER_FORBIDDEN_PAYLOAD_KEYS:
                assert fk not in pf


class TestReplayability:
    def test_same_inputs_same_output(self):
        sources = [_make_source("sec_filing")]
        facts = [_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)]
        r1 = _score(sources, facts)
        r2 = _score(sources, facts)
        assert r1.completeness_band == r2.completeness_band
        assert r1.missing_requirements == r2.missing_requirements
        assert r1.present_requirements == r2.present_requirements
        assert r1.per_fact_assessments == r2.per_fact_assessments
        assert r1.scorer_version == r2.scorer_version

    def test_to_dict_keys_present(self):
        result = _score([], [])
        d = result.to_dict()
        for k in (
            "scorer_version", "is_evaluable", "completeness_band",
            "source_count", "fact_count", "quote_grounded_fact_count",
            "comparable_fact_count", "contradiction_count",
            "missing_requirements", "present_requirements",
            "not_applicable_requirements", "per_fact_assessments",
            "limitations", "no_guessing",
        ):
            assert k in d, f"Missing key: {k}"


# ── Integration tests with ResearchArtifactServiceV1 ─────────────────────────

class TestWriteArtifactIntegration:
    def _svc(self, db: _FakeDB, user_id: str = "u1") -> ResearchArtifactServiceV1:
        return ResearchArtifactServiceV1(db, user_id)

    def _get_payload(self, db: _FakeDB) -> Optional[dict]:
        rows = db._rows.get("research_artifacts", [])
        if not rows:
            return None
        return rows[-1].get("payload", {})

    def test_write_injects_evidence_completeness_assessment(self):
        db = _FakeDB()
        svc = self._svc(db)
        output = _make_output(
            sources=[_make_source("sec_filing")],
            facts=[_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)],
        )
        artifact_id = svc.write_artifact(output)
        assert artifact_id is not None
        payload = self._get_payload(db)
        assert payload is not None
        assert "evidence_completeness_assessment" in payload
        ec = payload["evidence_completeness_assessment"]
        assert ec["completeness_band"] == BAND_COMPLETE
        assert ec["scorer_version"] == EVIDENCE_COMPLETENESS_SCORER_VERSION
        assert ec["no_guessing"] is True

    def test_write_no_sources_no_facts_not_evaluable(self):
        db = _FakeDB()
        svc = self._svc(db)
        output = _make_output(sources=[], facts=[])
        artifact_id = svc.write_artifact(output)
        assert artifact_id is not None
        payload = self._get_payload(db)
        ec = payload["evidence_completeness_assessment"]
        assert ec["completeness_band"] == BAND_NOT_EVALUABLE
        assert ec["is_evaluable"] is False
        assert "has_at_least_one_source" in ec["missing_requirements"]
        assert "has_at_least_one_fact" in ec["missing_requirements"]

    def test_source_credibility_still_exists_after_write(self):
        db = _FakeDB()
        svc = self._svc(db)
        output = _make_output(sources=[_make_source("sec_filing")])
        svc.write_artifact(output)
        payload = self._get_payload(db)
        assert "source_credibility_assessment" in payload

    def test_contradiction_assessment_still_exists_after_write(self):
        db = _FakeDB()
        svc = self._svc(db)
        output = _make_output(sources=[_make_source("sec_filing")])
        svc.write_artifact(output)
        payload = self._get_payload(db)
        assert "contradiction_assessment" in payload

    def test_all_three_assessments_present(self):
        db = _FakeDB()
        svc = self._svc(db)
        output = _make_output(
            sources=[_make_source("sec_filing")],
            facts=[_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)],
        )
        svc.write_artifact(output)
        payload = self._get_payload(db)
        assert "source_credibility_assessment" in payload
        assert "contradiction_assessment" in payload
        assert "evidence_completeness_assessment" in payload

    def test_no_forbidden_keys_in_completeness_assessment(self):
        db = _FakeDB()
        svc = self._svc(db)
        output = _make_output(
            sources=[_make_source("sec_filing")],
            facts=[_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)],
        )
        svc.write_artifact(output)
        payload = self._get_payload(db)
        ec = payload["evidence_completeness_assessment"]
        # Recursively check no forbidden keys exist in the assessment.
        from app.services.intelligence.research_workers.contracts import _has_forbidden_key
        assert _has_forbidden_key(ec) is None

    def test_thin_with_news_source(self):
        db = _FakeDB()
        svc = self._svc(db)
        output = _make_output(
            sources=[_make_source("news")],
            facts=[_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)],
        )
        svc.write_artifact(output)
        payload = self._get_payload(db)
        ec = payload["evidence_completeness_assessment"]
        assert ec["completeness_band"] == BAND_THIN

    def test_thin_with_source_but_no_facts(self):
        db = _FakeDB()
        svc = self._svc(db)
        output = _make_output(sources=[_make_source("sec_filing")], facts=[])
        svc.write_artifact(output)
        payload = self._get_payload(db)
        ec = payload["evidence_completeness_assessment"]
        assert ec["completeness_band"] == BAND_THIN
        assert "has_at_least_one_fact" in ec["missing_requirements"]

    def test_partial_when_contradiction_detected(self):
        db = _FakeDB()
        svc = self._svc(db)
        conflicting_facts = [
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={"claim_key": "eps", "value": 1.0, "period": "Q1-2025"},
                period="Q1-2025",
                is_quote_grounded=True,
            ),
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={"claim_key": "eps", "value": 2.5, "period": "Q1-2025"},
                period="Q1-2025",
                is_quote_grounded=True,
            ),
        ]
        output = _make_output(
            sources=[_make_source("sec_filing")],
            facts=conflicting_facts,
        )
        svc.write_artifact(output)
        payload = self._get_payload(db)
        ec = payload["evidence_completeness_assessment"]
        assert ec["completeness_band"] == BAND_PARTIAL
        assert "has_no_detected_contradictions" in ec["missing_requirements"]


class TestIdempotencyAndCleanReplacement:
    def _svc(self, db: _FakeDB, user_id: str = "u1") -> ResearchArtifactServiceV1:
        return ResearchArtifactServiceV1(db, user_id)

    def test_idempotent_replay_still_works(self):
        """Same idempotency key → skip; no second insert."""
        db = _FakeDB()
        svc = self._svc(db)
        output = _make_output(sources=[_make_source("sec_filing")])
        id1 = svc.write_artifact(output)
        id2 = svc.write_artifact(output)
        assert id1 == id2
        assert len(db._rows.get("research_artifacts", [])) == 1

    def test_clean_replacement_ticker_scope(self):
        """Different idempotency key → deactivate old, insert new."""
        db = _FakeDB()
        svc = self._svc(db)
        output1 = _make_output(
            sources=[_make_source("sec_filing")],
            idempotency_key=_make_key("AAPL", "-v1"),
        )
        output2 = _make_output(
            sources=[_make_source("transcript")],
            idempotency_key=_make_key("AAPL", "-v2"),
        )
        id1 = svc.write_artifact(output1)
        id2 = svc.write_artifact(output2)
        assert id1 != id2
        rows = db._rows.get("research_artifacts", [])
        active_rows = [r for r in rows if r.get("is_active")]
        assert len(active_rows) == 1
        assert active_rows[0]["id"] == id2

    def test_clean_replacement_portfolio_scope(self):
        db = _FakeDB()
        svc = self._svc(db)
        k1 = compute_replay_idempotency_key(
            skill_pack="test_worker", scope_kind="portfolio",
            ticker="", source_refs_fingerprint="fp_p1", model_version="v1",
        )
        k2 = compute_replay_idempotency_key(
            skill_pack="test_worker", scope_kind="portfolio",
            ticker="", source_refs_fingerprint="fp_p2", model_version="v1",
        )
        out1 = _make_output(scope_kind="portfolio", idempotency_key=k1)
        out2 = _make_output(scope_kind="portfolio", idempotency_key=k2)
        id1 = svc.write_artifact(out1)
        id2 = svc.write_artifact(out2)
        assert id1 != id2
        rows = db._rows.get("research_artifacts", [])
        active_rows = [r for r in rows if r.get("is_active")]
        assert len(active_rows) == 1
        assert active_rows[0]["id"] == id2


class TestNoForbiddenTableWrites:
    def _svc(self, db: _FakeDB) -> ResearchArtifactServiceV1:
        return ResearchArtifactServiceV1(db, "u1")

    def test_no_writes_to_intel_v3_snapshots(self):
        db = _FakeDB()
        db._blocked_tables.add("intel_v3_snapshots")
        svc = self._svc(db)
        output = _make_output(sources=[_make_source("sec_filing")])
        artifact_id = svc.write_artifact(output)
        assert artifact_id is not None  # Write succeeded without touching blocked table

    def test_no_writes_to_recommendations(self):
        db = _FakeDB()
        db._blocked_tables.add("recommendations")
        svc = self._svc(db)
        output = _make_output(sources=[_make_source("sec_filing")])
        artifact_id = svc.write_artifact(output)
        assert artifact_id is not None


class TestEdgeCases:
    def test_as_of_provides_time_context(self):
        """as_of (not period) should satisfy has_time_context_period_or_as_of."""
        sources = [_make_source("sec_filing")]
        facts = [
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={"claim_key": "price", "value": 150.0, "as_of": "2025-01-15"},
                as_of="2025-01-15",
                is_quote_grounded=True,
            )
        ]
        result = _score(sources, facts)
        assert "has_time_context_period_or_as_of" in result.present_requirements
        assert result.completeness_band == BAND_COMPLETE

    def test_metric_name_accepted_as_claim_key(self):
        """metric_name in structured_payload qualifies as claim key."""
        sources = [_make_source("vendor_fundamentals")]
        facts = [
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={"metric_name": "pe_ratio", "value": 22.5, "period": "TTM"},
                period="TTM",
                is_quote_grounded=True,
            )
        ]
        result = _score(sources, facts)
        assert "has_structured_claim_key_or_metric_name" in result.present_requirements
        assert "has_comparable_fact_when_claim_is_metric_like" in result.present_requirements

    def test_mixed_sources_credibility_uses_strongest(self):
        """If one source is sec_filing and another is news, strongest wins."""
        sources = [_make_source("news"), _make_source("sec_filing")]
        facts = [_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)]
        result = _score(sources, facts)
        # sec_filing is PRIMARY_AUTHORITY → not THIN due to source credibility
        assert result.completeness_band in (BAND_COMPLETE, BAND_PARTIAL)

    def test_limitations_always_populated(self):
        result = _score([], [])
        assert len(result.limitations) >= 1

    def test_limitations_populated_for_complete(self):
        sources = [_make_source("sec_filing")]
        facts = [_make_comparable_fact(period="Q1-2025", is_quote_grounded=True)]
        result = _score(sources, facts)
        assert len(result.limitations) >= 1

    def test_non_comparable_facts_count_in_comparable_fact_count(self):
        """comparable_fact_count comes from contradiction assessment (claims + value)."""
        sources = [_make_source("sec_filing")]
        facts = [
            _make_comparable_fact(period="Q1-2025", is_quote_grounded=True),
            _make_noncomparable_fact(),
        ]
        result = _score(sources, facts)
        assert result.comparable_fact_count == 1
        assert result.fact_count == 2
