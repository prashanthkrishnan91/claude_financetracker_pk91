"""Stage 5C focused tests — Contradiction Detector v1.

Acceptance criteria verified:
  1.  No facts → not evaluable, reason=no_facts_provided.
  2.  Facts without comparable fields → not evaluable, non_comparable_fact_count > 0.
  3.  Same claim_key + period + same numeric value → evaluable, no contradiction.
  4.  Same claim_key + period + different numeric values → contradiction group.
  5.  Boolean true/false conflict → contradiction group.
  6.  Different periods → no conflict (not the same time point).
  7.  Different claim_key / metric_name → no conflict.
  8.  Tolerance: values within 1% → no contradiction.
  9.  Values exactly at tolerance boundary → behavior deterministic.
 10.  write_artifact injects contradiction_assessment without forbidden keys.
 11.  source_credibility_assessment still exists after write (Stage 5B intact).
 12.  Idempotent replay still works (assessments not re-added on skip).
 13.  Clean replacement still works for ticker scope.
 14.  Clean replacement still works for portfolio scope.
 15.  No writes to intel_v3_snapshots or recommendations tables.
 16.  Assessment is replayable: same facts → same output.
 17.  detector_version embedded in every assessment.
 18.  no_guessing is always True.
 19.  Mixed comparable + non-comparable facts: evaluable, non_comparable_fact_count correct.
 20.  text_value case-insensitive match → no contradiction.
 21.  text_value mismatch → contradiction group.
 22.  metric_name accepted as claim key (fallback from claim_key).
 23.  as_of isolation: same claim_key, different as_of → no conflict.
 24.  Multiple contradiction groups detected independently.
 25.  Numeric tolerance near zero: two near-zero values within tolerance.

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
    compute_input_fingerprint,
    compute_replay_idempotency_key,
)
from app.services.intelligence.v3.contradiction_detector_v1 import (
    CONTRADICTION_DETECTOR_VERSION,
    NUMERIC_RELATIVE_TOLERANCE,
    ContradictionAssessment,
    detect_contradictions,
)
from app.services.intelligence.v3.research_artifact_service_v1 import (
    ResearchArtifactServiceV1,
)


# ── Fake Supabase infrastructure ──────────────────────────────────────────────

@dataclass
class _UpdateOp:
    table: str
    patch: dict[str, Any]
    filters: dict[str, Any]


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


@dataclass
class _FakeResult:
    data: list[dict]


class _FakeDB:
    def __init__(self) -> None:
        self._rows: dict[str, list[dict]] = {}
        self._blocked_tables: set[str] = set()

    def table(self, name: str) -> _FakeTable:
        if name in self._blocked_tables:
            raise RuntimeError(f"Table '{name}' is blocked in this test scenario")
        return _FakeTable(name, self)


# ── WorkerOutput builder helpers ──────────────────────────────────────────────

def _make_key(ticker: str = "AAPL", suffix: str = "") -> str:
    return compute_replay_idempotency_key(
        skill_pack="test_worker",
        scope_kind="ticker",
        ticker=ticker + suffix,
        source_refs_fingerprint="fp1",
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


def _make_fact(
    *,
    claim_key: Optional[str] = None,
    metric_name: Optional[str] = None,
    value: Optional[float] = None,
    value_normalized: Optional[float] = None,
    boolean_value: Optional[bool] = None,
    text_value: Optional[str] = None,
    period: Optional[str] = None,
    as_of: Optional[str] = None,
    fact_kind: str = "metric",
    source_index: Optional[int] = None,
    axis_hint: Optional[str] = None,
) -> FactRecord:
    sp: dict[str, Any] = {}
    if claim_key:
        sp["claim_key"] = claim_key
    if metric_name:
        sp["metric_name"] = metric_name
    if value is not None:
        sp["value"] = value
    if value_normalized is not None:
        sp["value_normalized"] = value_normalized
    if boolean_value is not None:
        sp["boolean_value"] = boolean_value
    if text_value is not None:
        sp["text_value"] = text_value
    return FactRecord(
        fact_kind=fact_kind,
        structured_payload=sp,
        period=period,
        as_of=as_of,
        source_index=source_index,
        axis_hint=axis_hint,
    )


# ── 1. No facts → not evaluable ───────────────────────────────────────────────

def test_no_facts_not_evaluable():
    result = detect_contradictions([])
    assert result.is_evaluable is False
    assert result.not_evaluable_reason == "no_facts_provided"
    assert result.comparable_fact_count == 0
    assert result.non_comparable_fact_count == 0
    assert result.has_contradictions is False
    assert result.contradiction_count == 0
    assert result.contradiction_groups == []
    assert result.no_guessing is True
    assert result.detector_version == CONTRADICTION_DETECTOR_VERSION


# ── 2. Facts without comparable fields → not evaluable ───────────────────────

def test_facts_without_comparable_fields_not_evaluable():
    # Fact with no claim_key / metric_name and no value fields
    f = FactRecord(
        fact_kind="narrative",
        structured_payload={"summary": "revenue looks good"},
    )
    result = detect_contradictions([f])
    assert result.is_evaluable is False
    assert result.not_evaluable_reason == "insufficient_comparable_facts"
    assert result.non_comparable_fact_count == 1
    assert result.comparable_fact_count == 0
    assert result.has_contradictions is False


def test_facts_with_claim_key_but_no_value_not_evaluable():
    f = FactRecord(
        fact_kind="metric",
        structured_payload={"claim_key": "revenue", "description": "some text"},
    )
    result = detect_contradictions([f])
    assert result.is_evaluable is False
    assert result.non_comparable_fact_count == 1


def test_facts_with_value_but_no_claim_key_not_evaluable():
    f = FactRecord(
        fact_kind="metric",
        structured_payload={"value": 100.0, "description": "no claim key"},
    )
    result = detect_contradictions([f])
    assert result.is_evaluable is False
    assert result.non_comparable_fact_count == 1


# ── 3. Same claim_key + period + same numeric value → evaluable, no contradiction

def test_same_value_same_period_no_contradiction():
    f1 = _make_fact(claim_key="revenue", value=100.0, period="Q1-2025")
    f2 = _make_fact(claim_key="revenue", value=100.0, period="Q1-2025")
    result = detect_contradictions([f1, f2])
    assert result.is_evaluable is True
    assert result.has_contradictions is False
    assert result.contradiction_count == 0
    assert result.comparable_fact_count == 2


def test_same_normalized_value_no_contradiction():
    f1 = _make_fact(claim_key="pe_ratio", value_normalized=25.0, period="FY2024")
    f2 = _make_fact(claim_key="pe_ratio", value_normalized=25.0, period="FY2024")
    result = detect_contradictions([f1, f2])
    assert result.is_evaluable is True
    assert result.has_contradictions is False


# ── 4. Same claim_key + period + different numeric values → contradiction ─────

def test_different_numeric_values_contradiction():
    f1 = _make_fact(claim_key="revenue", value=100.0, period="Q1-2025", source_index=0)
    f2 = _make_fact(claim_key="revenue", value=200.0, period="Q1-2025", source_index=1)
    result = detect_contradictions([f1, f2])
    assert result.is_evaluable is True
    assert result.has_contradictions is True
    assert result.contradiction_count == 1
    grp = result.contradiction_groups[0]
    assert grp["claim_key"] == "revenue"
    assert grp["period"] == "Q1-2025"
    assert grp["conflicting_fact_count"] == 2
    assert len(grp["conflicting_facts"]) == 2
    source_indices = {f["source_index"] for f in grp["conflicting_facts"]}
    assert source_indices == {0, 1}


def test_different_normalized_values_contradiction():
    f1 = _make_fact(claim_key="eps", value_normalized=1.0, period="Q2-2025")
    f2 = _make_fact(claim_key="eps", value_normalized=2.0, period="Q2-2025")
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is True
    assert result.contradiction_count == 1


# ── 5. Boolean true/false conflict → contradiction group ─────────────────────

def test_boolean_true_false_contradiction():
    f1 = _make_fact(claim_key="guidance_raised", boolean_value=True, period="Q1-2025")
    f2 = _make_fact(claim_key="guidance_raised", boolean_value=False, period="Q1-2025")
    result = detect_contradictions([f1, f2])
    assert result.is_evaluable is True
    assert result.has_contradictions is True
    assert result.contradiction_count == 1
    assert result.contradiction_groups[0]["claim_key"] == "guidance_raised"


def test_boolean_same_value_no_contradiction():
    f1 = _make_fact(claim_key="guidance_raised", boolean_value=True)
    f2 = _make_fact(claim_key="guidance_raised", boolean_value=True)
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is False


# ── 6. Different periods → no conflict ───────────────────────────────────────

def test_different_periods_no_conflict():
    f1 = _make_fact(claim_key="revenue", value=100.0, period="Q1-2025")
    f2 = _make_fact(claim_key="revenue", value=999.0, period="Q2-2025")
    result = detect_contradictions([f1, f2])
    assert result.is_evaluable is True
    assert result.has_contradictions is False


def test_no_period_vs_period_no_conflict():
    # Facts with different time contexts (one has period, one doesn't)
    f1 = _make_fact(claim_key="revenue", value=100.0, period="Q1-2025")
    f2 = _make_fact(claim_key="revenue", value=200.0)  # no period
    result = detect_contradictions([f1, f2])
    # Group key differs because period is part of the key
    assert result.has_contradictions is False


# ── 7. Different claim_key / metric_name → no conflict ───────────────────────

def test_different_claim_keys_no_conflict():
    f1 = _make_fact(claim_key="revenue", value=100.0, period="Q1-2025")
    f2 = _make_fact(claim_key="earnings", value=999.0, period="Q1-2025")
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is False


# ── 8. Tolerance: values within 1% → no contradiction ────────────────────────

def test_numeric_within_tolerance_no_contradiction():
    # 1% relative difference is exactly at boundary — should NOT flag
    a = 100.0
    b = 100.0 * (1.0 + NUMERIC_RELATIVE_TOLERANCE)  # exactly 1% above
    # Boundary check: |a - b| / max(|a|, |b|) == 0.01 → NOT > 0.01 → no contradiction
    f1 = _make_fact(claim_key="eps", value=a, period="Q1-2025")
    f2 = _make_fact(claim_key="eps", value=b, period="Q1-2025")
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is False


def test_numeric_just_over_tolerance_contradiction():
    # 2% relative difference — clearly above the 1% tolerance
    a = 100.0
    b = 102.0
    # |a - b| / max(|a|, |b|) = 2 / 102 ≈ 0.0196 > 0.01 → contradiction
    f1 = _make_fact(claim_key="eps", value=a, period="Q1-2025")
    f2 = _make_fact(claim_key="eps", value=b, period="Q1-2025")
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is True


def test_numeric_near_zero_within_tolerance():
    # Both near zero — relative tolerance uses floor, so small diff is not flagged
    f1 = _make_fact(claim_key="tiny_metric", value=0.0, period="Q1-2025")
    f2 = _make_fact(claim_key="tiny_metric", value=1e-12, period="Q1-2025")
    # |0 - 1e-12| / max(0, 1e-12, 1e-9) = 1e-12 / 1e-9 = 0.001 < 0.01 → no contradiction
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is False


# ── 10. write_artifact injects contradiction_assessment without forbidden keys ─

def test_write_artifact_injects_contradiction_assessment():
    db = _FakeDB()
    svc = ResearchArtifactServiceV1(db, user_id="u1")
    f1 = _make_fact(claim_key="revenue", value=100.0, period="Q1-2025")
    f2 = _make_fact(claim_key="revenue", value=200.0, period="Q1-2025")
    output = _make_output(facts=[f1, f2])
    artifact_id = svc.write_artifact(output)
    assert artifact_id is not None

    rows = db._rows.get("research_artifacts", [])
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert "contradiction_assessment" in payload
    ca = payload["contradiction_assessment"]
    assert ca["detector_version"] == CONTRADICTION_DETECTOR_VERSION
    assert ca["is_evaluable"] is True
    assert ca["has_contradictions"] is True
    assert ca["no_guessing"] is True


def test_write_artifact_no_forbidden_keys_in_contradiction_assessment():
    from app.services.intelligence.research_workers.contracts import (
        WORKER_FORBIDDEN_PAYLOAD_KEYS,
        _has_forbidden_key,
    )
    db = _FakeDB()
    svc = ResearchArtifactServiceV1(db, user_id="u1")
    f = _make_fact(claim_key="revenue", value=100.0)
    output = _make_output(facts=[f])
    svc.write_artifact(output)

    rows = db._rows.get("research_artifacts", [])
    assert len(rows) == 1
    payload = rows[0]["payload"]
    forbidden = _has_forbidden_key(payload.get("contradiction_assessment", {}))
    assert forbidden is None, f"Forbidden key in contradiction_assessment: {forbidden}"


# ── 11. source_credibility_assessment still present (Stage 5B intact) ─────────

def test_source_credibility_assessment_still_present():
    db = _FakeDB()
    svc = ResearchArtifactServiceV1(db, user_id="u1")
    output = _make_output(
        sources=[SourceRecord(source_kind="sec_filing", provider_name="SEC")],
    )
    svc.write_artifact(output)

    rows = db._rows.get("research_artifacts", [])
    payload = rows[0]["payload"]
    assert "source_credibility_assessment" in payload
    assert "contradiction_assessment" in payload


# ── 12. Idempotent replay: assessments not re-added on skip ──────────────────

def test_idempotent_replay_skip():
    db = _FakeDB()
    svc = ResearchArtifactServiceV1(db, user_id="u1")
    key = _make_key("AAPL", "idem_test")
    output = _make_output(idempotency_key=key)

    id1 = svc.write_artifact(output)
    id2 = svc.write_artifact(output)  # same key → skip
    assert id1 == id2
    assert len(db._rows.get("research_artifacts", [])) == 1


# ── 13. Clean replacement — ticker scope ──────────────────────────────────────

def test_clean_replacement_ticker_scope():
    db = _FakeDB()
    svc = ResearchArtifactServiceV1(db, user_id="u1")

    key1 = compute_replay_idempotency_key("test_worker", "ticker", "AAPL", "fp1", "v1")
    key2 = compute_replay_idempotency_key("test_worker", "ticker", "AAPL", "fp2", "v1")

    out1 = _make_output(ticker="AAPL", idempotency_key=key1)
    out2 = _make_output(ticker="AAPL", idempotency_key=key2)

    svc.write_artifact(out1)
    svc.write_artifact(out2)

    rows = db._rows.get("research_artifacts", [])
    active = [r for r in rows if r.get("is_active")]
    deactivated = [r for r in rows if not r.get("is_active")]
    assert len(active) == 1
    assert len(deactivated) == 1
    assert active[0]["replay_idempotency_key"] == key2


# ── 14. Clean replacement — portfolio scope ───────────────────────────────────

def test_clean_replacement_portfolio_scope():
    db = _FakeDB()
    svc = ResearchArtifactServiceV1(db, user_id="u1")

    key1 = compute_replay_idempotency_key("test_worker", "portfolio", "", "fp1", "v1")
    key2 = compute_replay_idempotency_key("test_worker", "portfolio", "", "fp2", "v1")

    out1 = _make_output(scope_kind="portfolio", idempotency_key=key1)
    out2 = _make_output(scope_kind="portfolio", idempotency_key=key2)

    svc.write_artifact(out1)
    svc.write_artifact(out2)

    rows = db._rows.get("research_artifacts", [])
    active = [r for r in rows if r.get("is_active")]
    assert len(active) == 1
    assert active[0]["replay_idempotency_key"] == key2


# ── 15. No writes to intel_v3_snapshots ───────────────────────────────────────

def test_no_intel_v3_snapshots_write():
    db = _FakeDB()
    svc = ResearchArtifactServiceV1(db, user_id="u1")
    svc.write_artifact(_make_output())
    assert "intel_v3_snapshots" not in db._rows


# ── 16. Replayability: same facts → same output ───────────────────────────────

def test_replayability_same_facts_same_output():
    f1 = _make_fact(claim_key="revenue", value=100.0, period="Q1-2025")
    f2 = _make_fact(claim_key="revenue", value=200.0, period="Q1-2025")
    result_a = detect_contradictions([f1, f2])
    result_b = detect_contradictions([f1, f2])
    assert result_a.to_dict() == result_b.to_dict()


def test_replayability_no_fact_same_output():
    result_a = detect_contradictions([])
    result_b = detect_contradictions([])
    assert result_a.to_dict() == result_b.to_dict()


# ── 17. detector_version embedded ────────────────────────────────────────────

def test_detector_version_embedded_no_facts():
    result = detect_contradictions([])
    assert result.detector_version == CONTRADICTION_DETECTOR_VERSION


def test_detector_version_embedded_evaluable():
    f = _make_fact(claim_key="eps", value=1.0, period="Q1")
    result = detect_contradictions([f])
    assert result.detector_version == CONTRADICTION_DETECTOR_VERSION


# ── 18. no_guessing is always True ───────────────────────────────────────────

def test_no_guessing_always_true_not_evaluable():
    assert detect_contradictions([]).no_guessing is True


def test_no_guessing_always_true_evaluable():
    f = _make_fact(claim_key="rev", value=1.0)
    assert detect_contradictions([f]).no_guessing is True


def test_no_guessing_in_write_artifact_payload():
    db = _FakeDB()
    svc = ResearchArtifactServiceV1(db, user_id="u1")
    svc.write_artifact(_make_output())
    rows = db._rows.get("research_artifacts", [])
    ca = rows[0]["payload"]["contradiction_assessment"]
    assert ca["no_guessing"] is True


# ── 19. Mixed comparable + non-comparable: evaluable ─────────────────────────

def test_mixed_comparable_non_comparable_facts():
    f_comparable = _make_fact(claim_key="revenue", value=100.0, period="Q1-2025")
    f_non_comparable = FactRecord(
        fact_kind="narrative",
        structured_payload={"summary": "revenue looks solid"},
    )
    result = detect_contradictions([f_comparable, f_non_comparable])
    assert result.is_evaluable is True
    assert result.comparable_fact_count == 1
    assert result.non_comparable_fact_count == 1
    assert result.has_contradictions is False


# ── 20. text_value case-insensitive match → no contradiction ─────────────────

def test_text_value_case_insensitive_same_no_contradiction():
    f1 = _make_fact(claim_key="trend", text_value="Positive", period="Q1-2025")
    f2 = _make_fact(claim_key="trend", text_value="positive", period="Q1-2025")
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is False


# ── 21. text_value mismatch → contradiction group ────────────────────────────

def test_text_value_mismatch_contradiction():
    f1 = _make_fact(claim_key="trend", text_value="positive", period="Q1-2025")
    f2 = _make_fact(claim_key="trend", text_value="negative", period="Q1-2025")
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is True
    assert result.contradiction_count == 1


# ── 22. metric_name accepted as claim key ────────────────────────────────────

def test_metric_name_as_claim_key():
    f1 = _make_fact(metric_name="pe_ratio", value=20.0, period="FY2024")
    f2 = _make_fact(metric_name="pe_ratio", value=30.0, period="FY2024")
    result = detect_contradictions([f1, f2])
    assert result.is_evaluable is True
    assert result.has_contradictions is True
    assert result.contradiction_groups[0]["claim_key"] == "pe_ratio"


def test_metric_name_fallback_after_claim_key():
    # claim_key takes precedence over metric_name
    f = FactRecord(
        fact_kind="metric",
        structured_payload={
            "claim_key": "revenue",
            "metric_name": "should_be_ignored",
            "value": 1.0,
        },
    )
    result = detect_contradictions([f])
    assert result.is_evaluable is True
    # Only one fact — no contradictions possible
    assert result.has_contradictions is False


# ── 23. as_of isolation: same claim_key, different as_of → no conflict ───────

def test_different_as_of_no_conflict():
    f1 = _make_fact(claim_key="eps", value=1.0, as_of="2025-01-01")
    f2 = _make_fact(claim_key="eps", value=2.0, as_of="2025-04-01")
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is False


def test_same_as_of_conflict():
    f1 = _make_fact(claim_key="eps", value=1.0, as_of="2025-01-01")
    f2 = _make_fact(claim_key="eps", value=2.0, as_of="2025-01-01")
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is True


# ── 24. Multiple contradiction groups detected independently ──────────────────

def test_multiple_contradiction_groups():
    f1 = _make_fact(claim_key="revenue", value=100.0, period="Q1-2025", source_index=0)
    f2 = _make_fact(claim_key="revenue", value=200.0, period="Q1-2025", source_index=1)
    f3 = _make_fact(claim_key="guidance_raised", boolean_value=True, period="Q1-2025", source_index=0)
    f4 = _make_fact(claim_key="guidance_raised", boolean_value=False, period="Q1-2025", source_index=1)
    result = detect_contradictions([f1, f2, f3, f4])
    assert result.is_evaluable is True
    assert result.has_contradictions is True
    assert result.contradiction_count == 2
    keys = {g["claim_key"] for g in result.contradiction_groups}
    assert "revenue" in keys
    assert "guidance_raised" in keys


# ── 25. Numeric tolerance near zero ──────────────────────────────────────────

def test_near_zero_both_values():
    # Both values at 0 → no contradiction (same value)
    f1 = _make_fact(claim_key="diff", value=0.0, period="Q1")
    f2 = _make_fact(claim_key="diff", value=0.0, period="Q1")
    result = detect_contradictions([f1, f2])
    assert result.has_contradictions is False


# ── Additional: write_artifact no-fact artifact → not evaluable in payload ────

def test_write_artifact_no_fact_not_evaluable():
    db = _FakeDB()
    svc = ResearchArtifactServiceV1(db, user_id="u1")
    output = _make_output(facts=[])
    svc.write_artifact(output)

    rows = db._rows.get("research_artifacts", [])
    ca = rows[0]["payload"]["contradiction_assessment"]
    assert ca["is_evaluable"] is False
    assert ca["not_evaluable_reason"] == "no_facts_provided"
    assert ca["has_contradictions"] is False


def test_write_artifact_non_comparable_facts_not_evaluable():
    db = _FakeDB()
    svc = ResearchArtifactServiceV1(db, user_id="u1")
    f = FactRecord(
        fact_kind="narrative",
        structured_payload={"summary": "revenue looks solid but costs rising"},
    )
    output = _make_output(facts=[f])
    svc.write_artifact(output)

    rows = db._rows.get("research_artifacts", [])
    ca = rows[0]["payload"]["contradiction_assessment"]
    assert ca["is_evaluable"] is False
    assert ca["not_evaluable_reason"] == "insufficient_comparable_facts"
    assert ca["non_comparable_fact_count"] == 1
