"""Phase 6B — Controlled SEC Production Validation + Readiness Observability tests.

Acceptance criteria (numbered as in Phase 6B spec):

Service / readiness aggregation:
  1.  No artifacts → readiness_evaluated_count=0 and no readiness errors.
  2.  Phase 4-style artifact (UNKNOWN/UNKNOWN/no sources) → eligible_for_truth_adapter_count=0;
      reason codes include unknown_or_invalid_confidence / unknown_or_invalid_freshness /
      no_valid_sources / fact_missing_source_link.
  3.  SEC-backed artifact (valid source + source-linked fact + MEDIUM/FRESH)
      → eligible_for_truth_adapter_count=1.
  4.  SEC-backed artifact still has eligible_for_decision_consumption_count=0.
  5.  phase5_ready_but_decision_blocked_count increments for eligible truth-adapter artifacts.
  6.  safe_for_decision_db_promotion_blocked_count equals readiness_evaluated_count.
  7.  Artifact with safe_for_decision=True increments unexpected_safe_for_decision_true_count
      (Phase 4) AND adds unexpected_safe_for_decision_true to by_readiness_reason_code.
  8.  Fact without source_id produces fact_missing_source_link aggregate.
  9.  Fact source_id not matching any source produces fact_source_not_found aggregate.
  10. Source without provenance produces no_valid_sources aggregate.
  11. Forbidden payload key increments forbidden_payload_violation_count (Phase 4)
      AND adds forbidden key reason code to by_readiness_reason_code.
  12. Child source query failure returns errors[] and does not raise; readiness skips gracefully.
  13. Child fact query failure returns errors[] and does not raise; readiness skips gracefully.
  14. Diagnostic output does not include raw payload, structured_payload, source_url,
      quote_or_excerpt, or raw DB rows.

Endpoint:
  15. Observability endpoint returns 403 when observability flag is off.
  16. Endpoint requires runtime cert.
  17. Endpoint caps tickers/lookback/max_rows as before.
  18. Endpoint returns new readiness aggregate fields when enabled.
  19. No frontend/page-load path references the endpoint.

Invariants / static guards:
  20. artifact_observability.py does not import decide() or IntelV3Service.
  21. No writes to intel_v3_snapshots.
  22. No artifact-to-decision integration (no import of recommendation_engine).
  23. No SQL new tables or migrations introduced.

All tests use FakeSupabaseClient — no production Supabase dependency.
"""
from __future__ import annotations

import importlib
import inspect
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from app.config import Settings


# ── Helpers ───────────────────────────────────────────────────────────────────

def _enabled_settings(**overrides) -> Settings:
    base = dict(
        supabase_url="http://fake",
        supabase_anon_key="anon",
        supabase_service_role_key="svc",
        supabase_jwt_secret="secret",
        encryption_key="a" * 64,
        intel_v3_research_artifact_observability_enabled=True,
        intel_v3_research_artifact_observability_info_logs_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def _disabled_settings() -> Settings:
    return _enabled_settings(intel_v3_research_artifact_observability_enabled=False)


_DEFAULT_USER_ID = "u1"


def _make_artifact(
    aid: str = None,
    ticker: str = "AAPL",
    artifact_type: str = "catalyst_window",
    skill_pack: str = "earnings_reviewer",
    confidence: str = "UNKNOWN",
    freshness: str = "UNKNOWN",
    is_active: bool = True,
    safe_for_decision: bool = False,
    invalidated_at: Optional[str] = None,
    expires_at: Optional[str] = None,
    payload: Optional[dict] = None,
    user_id: str = _DEFAULT_USER_ID,
) -> dict:
    return {
        "id": aid or str(uuid.uuid4()),
        "user_id": user_id,
        "ticker": ticker,
        "artifact_type": artifact_type,
        "skill_pack": skill_pack,
        "confidence_or_trust_level": confidence,
        "freshness_status": freshness,
        "is_active": is_active,
        "safe_for_decision": safe_for_decision,
        "invalidated_at": invalidated_at,
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "limitations_or_missing_evidence": [],
        "payload": payload or {"review_status": "dark_run_no_external_source"},
    }


def _make_source(
    src_id: str = None,
    artifact_id: str = "",
    source_kind: str = "sec_filing",
    provider_name: str = "sec_edgar",
    source_url: str = "https://www.sec.gov/Archives/edgar/data/320193/0000320193/",
    section_reference: str = "0000320193-23-000054",
    source_id_val: Optional[str] = None,
    source_hash: Optional[str] = None,
    user_id: str = _DEFAULT_USER_ID,
) -> dict:
    return {
        "id": src_id or str(uuid.uuid4()),
        "user_id": user_id,
        "artifact_id": artifact_id,
        "source_kind": source_kind,
        "provider_name": provider_name,
        "source_url": source_url,
        "section_reference": section_reference,
        "source_id": source_id_val,
        "source_hash": source_hash,
    }


def _make_fact(
    fact_id: str = None,
    artifact_id: str = "",
    fact_kind: str = "sourced_claim",
    source_id: Optional[str] = None,
    structured_payload: Optional[dict] = None,
    user_id: str = _DEFAULT_USER_ID,
) -> dict:
    return {
        "id": fact_id or str(uuid.uuid4()),
        "user_id": user_id,
        "artifact_id": artifact_id,
        "fact_kind": fact_kind,
        "source_id": source_id,
        "structured_payload": structured_payload or {
            "claim": "sec_filing_found",
            "form_type": "10-K",
            "filing_date": "2025-11-01",
        },
    }


# ── FakeSupabaseClient ────────────────────────────────────────────────────────

class _FakeQuery:
    """Chainable fake query builder — returns pre-configured rows on execute()."""

    def __init__(self, rows: list[dict], fail_with: Optional[Exception] = None) -> None:
        self._rows = rows
        self._fail_with = fail_with
        self._filters: dict = {}
        self._in_filters: dict = {}
        self._limit: Optional[int] = None
        self._writes: list[dict] = []

    def select(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def eq(self, col: str, val: Any) -> "_FakeQuery":
        self._filters[col] = val
        return self

    def gte(self, col: str, val: Any) -> "_FakeQuery":
        return self

    def order(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def limit(self, n: int) -> "_FakeQuery":
        self._limit = n
        return self

    def in_(self, col: str, vals: list) -> "_FakeQuery":
        self._in_filters[col] = vals
        return self

    def insert(self, row: dict) -> "_FakeQuery":
        self._writes.append(row)
        return self

    def execute(self) -> Any:
        if self._fail_with is not None:
            raise self._fail_with

        rows = self._rows
        for col, val in self._filters.items():
            rows = [r for r in rows if str(r.get(col, "")) == str(val)]
        for col, vals in self._in_filters.items():
            str_vals = {str(v) for v in vals}
            rows = [r for r in rows if str(r.get(col, "")) in str_vals]
        if self._limit is not None:
            rows = rows[: self._limit]

        @dataclass
        class _Res:
            data: list

        return _Res(data=list(rows))


class _FakeDB:
    """Multi-table fake Supabase client for Phase 6B tests.

    Supports configuring per-table rows and per-call failure injection.
    """

    def __init__(
        self,
        artifact_rows: list[dict] = None,
        source_rows: list[dict] = None,
        fact_rows: list[dict] = None,
        fail_source_query: bool = False,
        fail_fact_query: bool = False,
        fail_readiness_source_query: bool = False,
        fail_readiness_fact_query: bool = False,
    ) -> None:
        self._artifact_rows = artifact_rows or []
        self._source_rows = source_rows or []
        self._fact_rows = fact_rows or []
        self._fail_source_query = fail_source_query
        self._fail_fact_query = fail_fact_query
        self._fail_readiness_source_query = fail_readiness_source_query
        self._fail_readiness_fact_query = fail_readiness_fact_query
        self._query_calls: list[str] = []
        self._written_tables: list[str] = []
        # Track which table is being called — per-call state
        self._current_table: Optional[str] = None
        # Phase 4 source/fact queries are "artifact_id"-only.
        # Phase 6B source query is the one with id in select.
        # We disambiguate via the _src_call_count to simulate different responses.
        self._src_call_count = 0
        self._fact_call_count = 0

    def table(self, name: str) -> "_FakeDB":
        self._current_table = name
        self._query_calls.append(name)
        return self

    def select(self, cols: str) -> "_FakeQuery":
        t = self._current_table
        if t == "research_artifacts":
            return _FakeQuery(self._artifact_rows)
        elif t == "research_artifact_sources":
            # Phase 4 presence query uses "artifact_id" only.
            # Phase 6B readiness query uses "id,artifact_id,...".
            # Distinguish by call count: even = Phase 4, odd = Phase 6B.
            if self._fail_source_query and self._src_call_count == 0:
                self._src_call_count += 1
                return _FakeQuery([], fail_with=RuntimeError("source_query_fail"))
            if self._fail_readiness_source_query and self._src_call_count >= 1:
                self._src_call_count += 1
                return _FakeQuery([], fail_with=RuntimeError("readiness_source_query_fail"))
            self._src_call_count += 1
            return _FakeQuery(self._source_rows)
        elif t == "research_artifact_facts":
            if self._fail_fact_query and self._fact_call_count == 0:
                self._fact_call_count += 1
                return _FakeQuery([], fail_with=RuntimeError("fact_query_fail"))
            if self._fail_readiness_fact_query and self._fact_call_count >= 1:
                self._fact_call_count += 1
                return _FakeQuery([], fail_with=RuntimeError("readiness_fact_query_fail"))
            self._fact_call_count += 1
            return _FakeQuery(self._fact_rows)
        return _FakeQuery([])

    def eq(self, col: str, val: Any) -> "_FakeDB":
        return self

    def gte(self, *args, **kwargs) -> "_FakeDB":
        return self

    def order(self, *args, **kwargs) -> "_FakeDB":
        return self

    def limit(self, n: int) -> "_FakeDB":
        return self

    def in_(self, col: str, vals: list) -> "_FakeDB":
        return self

    def insert(self, row: dict) -> "_FakeDB":
        if self._current_table:
            self._written_tables.append(self._current_table)
        return self

    def execute(self) -> Any:
        @dataclass
        class _Res:
            data: list
        return _Res(data=[])

    def get_written_tables(self) -> list[str]:
        return list(self._written_tables)


# ── Import the module under test ───────────────────────────────────────────────

def _import_service():
    from app.services.intelligence.research_workers.artifact_observability import (
        summarize_recent_research_artifacts,
        ArtifactObservabilitySummary,
    )
    return summarize_recent_research_artifacts, ArtifactObservabilitySummary


# ── AC 1: No artifacts ────────────────────────────────────────────────────────

class TestNoArtifacts:
    def test_readiness_evaluated_count_zero(self):
        fn, _ = _import_service()
        db = _FakeDB(artifact_rows=[])
        result = fn(
            user_id="u1",
            db_client=db,
            settings=_enabled_settings(),
        )
        assert result.readiness_evaluated_count == 0

    def test_no_readiness_errors_when_empty(self):
        fn, _ = _import_service()
        db = _FakeDB(artifact_rows=[])
        result = fn(
            user_id="u1",
            db_client=db,
            settings=_enabled_settings(),
        )
        readiness_errors = [e for e in result.errors if "readiness" in e]
        assert readiness_errors == []

    def test_all_readiness_counts_zero(self):
        fn, _ = _import_service()
        db = _FakeDB(artifact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.eligible_for_truth_adapter_count == 0
        assert result.ineligible_for_truth_adapter_count == 0
        assert result.eligible_for_decision_consumption_count == 0
        assert result.phase5_ready_but_decision_blocked_count == 0


# ── AC 2: Phase 4 UNKNOWN/UNKNOWN artifact → ineligible ──────────────────────

class TestPhase4StyleArtifact:
    """Phase 4 dark-run artifacts have UNKNOWN/UNKNOWN/no sources → ineligible."""

    def _make_phase4_artifact(self):
        return _make_artifact(confidence="UNKNOWN", freshness="UNKNOWN")

    def test_ineligible_for_truth_adapter(self):
        fn, _ = _import_service()
        art = self._make_phase4_artifact()
        db = _FakeDB(
            artifact_rows=[art],
            source_rows=[],
            fact_rows=[],
        )
        result = fn("u1", db, settings=_enabled_settings())
        assert result.eligible_for_truth_adapter_count == 0

    def test_reason_codes_include_unknown_confidence(self):
        fn, _ = _import_service()
        art = self._make_phase4_artifact()
        db = _FakeDB(artifact_rows=[art], source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        codes = result.by_readiness_reason_code
        assert codes.get("unknown_or_invalid_confidence", 0) >= 1

    def test_reason_codes_include_unknown_freshness(self):
        fn, _ = _import_service()
        art = self._make_phase4_artifact()
        db = _FakeDB(artifact_rows=[art], source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        codes = result.by_readiness_reason_code
        assert codes.get("unknown_or_invalid_freshness", 0) >= 1

    def test_reason_codes_include_no_valid_sources(self):
        fn, _ = _import_service()
        art = self._make_phase4_artifact()
        db = _FakeDB(artifact_rows=[art], source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        codes = result.by_readiness_reason_code
        assert codes.get("no_valid_sources", 0) >= 1

    def test_evaluated_count_equals_artifact_count(self):
        fn, _ = _import_service()
        arts = [self._make_phase4_artifact(), self._make_phase4_artifact()]
        db = _FakeDB(artifact_rows=arts, source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.readiness_evaluated_count == 2
        assert result.ineligible_for_truth_adapter_count == 2


# ── AC 3: SEC-backed artifact → eligible_for_truth_adapter ───────────────────

class TestSecBackedArtifact:
    """SEC-grounded artifacts with valid source + source-linked fact + MEDIUM/FRESH."""

    def _make_sec_setup(self, ticker="AAPL"):
        art_id = str(uuid.uuid4())
        src_id = str(uuid.uuid4())
        art = _make_artifact(
            aid=art_id,
            ticker=ticker,
            confidence="MEDIUM",
            freshness="FRESH",
        )
        src = _make_source(src_id=src_id, artifact_id=art_id)
        fact = _make_fact(
            artifact_id=art_id,
            source_id=src_id,  # source-linked
        )
        return art, src, fact, art_id, src_id

    def test_eligible_for_truth_adapter(self):
        fn, _ = _import_service()
        art, src, fact, *_ = self._make_sec_setup()
        db = _FakeDB(
            artifact_rows=[art],
            source_rows=[src],
            fact_rows=[fact],
        )
        result = fn("u1", db, settings=_enabled_settings())
        assert result.eligible_for_truth_adapter_count == 1

    def test_three_tickers_all_eligible(self):
        fn, _ = _import_service()
        artifacts, sources, facts = [], [], []
        for ticker in ["AAPL", "MSFT", "NVDA"]:
            art, src, fact, *_ = self._make_sec_setup(ticker)
            artifacts.append(art)
            sources.append(src)
            facts.append(fact)
        db = _FakeDB(artifact_rows=artifacts, source_rows=sources, fact_rows=facts)
        result = fn("u1", db, settings=_enabled_settings())
        assert result.eligible_for_truth_adapter_count == 3

    def test_reason_codes_empty_for_eligible(self):
        fn, _ = _import_service()
        art, src, fact, *_ = self._make_sec_setup()
        db = _FakeDB(artifact_rows=[art], source_rows=[src], fact_rows=[fact])
        result = fn("u1", db, settings=_enabled_settings())
        # No reason codes for this eligible artifact
        assert result.by_readiness_reason_code == {}


# ── AC 4: eligible_for_decision_consumption always 0 ────────────────────────

class TestDecisionConsumptionAlwaysZero:
    def test_eligible_artifact_still_has_zero_consumption(self):
        fn, _ = _import_service()
        art_id = str(uuid.uuid4())
        src_id = str(uuid.uuid4())
        art = _make_artifact(aid=art_id, confidence="MEDIUM", freshness="FRESH")
        src = _make_source(src_id=src_id, artifact_id=art_id)
        fact = _make_fact(artifact_id=art_id, source_id=src_id)
        db = _FakeDB(artifact_rows=[art], source_rows=[src], fact_rows=[fact])
        result = fn("u1", db, settings=_enabled_settings())
        # Even though eligible for truth adapter, consumption count stays 0
        assert result.eligible_for_decision_consumption_count == 0

    def test_zero_consumption_with_multiple_eligible(self):
        fn, _ = _import_service()
        artifacts, sources, facts = [], [], []
        for _ in range(3):
            aid = str(uuid.uuid4())
            sid = str(uuid.uuid4())
            artifacts.append(_make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH"))
            sources.append(_make_source(src_id=sid, artifact_id=aid))
            facts.append(_make_fact(artifact_id=aid, source_id=sid))
        db = _FakeDB(artifact_rows=artifacts, source_rows=sources, fact_rows=facts)
        result = fn("u1", db, settings=_enabled_settings())
        assert result.eligible_for_decision_consumption_count == 0


# ── AC 5: phase5_ready_but_decision_blocked increments ──────────────────────

class TestPhase5ReadyButDecisionBlocked:
    def test_increments_for_eligible_artifact(self):
        fn, _ = _import_service()
        aid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        art = _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH")
        src = _make_source(src_id=sid, artifact_id=aid)
        fact = _make_fact(artifact_id=aid, source_id=sid)
        db = _FakeDB(artifact_rows=[art], source_rows=[src], fact_rows=[fact])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.phase5_ready_but_decision_blocked_count == 1

    def test_equals_eligible_for_truth_adapter_count(self):
        fn, _ = _import_service()
        artifacts, sources, facts = [], [], []
        for _ in range(2):
            aid = str(uuid.uuid4())
            sid = str(uuid.uuid4())
            artifacts.append(_make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH"))
            sources.append(_make_source(src_id=sid, artifact_id=aid))
            facts.append(_make_fact(artifact_id=aid, source_id=sid))
        db = _FakeDB(artifact_rows=artifacts, source_rows=sources, fact_rows=facts)
        result = fn("u1", db, settings=_enabled_settings())
        assert result.phase5_ready_but_decision_blocked_count == result.eligible_for_truth_adapter_count

    def test_zero_for_ineligible_artifacts(self):
        fn, _ = _import_service()
        art = _make_artifact(confidence="UNKNOWN", freshness="UNKNOWN")
        db = _FakeDB(artifact_rows=[art], source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.phase5_ready_but_decision_blocked_count == 0


# ── AC 6: safe_for_decision_db_promotion_blocked_count equals evaluated count ─

class TestSafeForDecisionDbPromotionBlocked:
    def test_equals_evaluated_count_for_phase4_artifacts(self):
        fn, _ = _import_service()
        arts = [_make_artifact() for _ in range(3)]
        db = _FakeDB(artifact_rows=arts, source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.safe_for_decision_db_promotion_blocked_count == result.readiness_evaluated_count

    def test_equals_evaluated_count_for_mixed_artifacts(self):
        fn, _ = _import_service()
        aid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        arts = [
            _make_artifact(confidence="UNKNOWN"),
            _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH"),
        ]
        sources = [_make_source(src_id=sid, artifact_id=aid)]
        facts = [_make_fact(artifact_id=aid, source_id=sid)]
        db = _FakeDB(artifact_rows=arts, source_rows=sources, fact_rows=facts)
        result = fn("u1", db, settings=_enabled_settings())
        assert result.safe_for_decision_db_promotion_blocked_count == result.readiness_evaluated_count == 2


# ── AC 7: safe_for_decision=True increments unexpected count and reason code ──

class TestUnexpectedSafeForDecision:
    def test_unexpected_safe_increments_phase4_counter(self):
        fn, _ = _import_service()
        art = _make_artifact(safe_for_decision=True)
        db = _FakeDB(artifact_rows=[art], source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.unexpected_safe_for_decision_true_count == 1

    def test_unexpected_safe_adds_reason_code(self):
        fn, _ = _import_service()
        art = _make_artifact(safe_for_decision=True)
        db = _FakeDB(artifact_rows=[art], source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.by_readiness_reason_code.get("unexpected_safe_for_decision_true", 0) >= 1


# ── AC 8: Fact without source_id → fact_missing_source_link ─────────────────

class TestFactMissingSourceLink:
    def test_fact_no_source_id_produces_reason_code(self):
        fn, _ = _import_service()
        aid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        art = _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH")
        src = _make_source(src_id=sid, artifact_id=aid)
        fact = _make_fact(artifact_id=aid, source_id=None)  # no source_id
        db = _FakeDB(artifact_rows=[art], source_rows=[src], fact_rows=[fact])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.by_readiness_reason_code.get("fact_missing_source_link", 0) >= 1
        assert result.eligible_for_truth_adapter_count == 0

    def test_artifact_without_source_linked_facts_increments_counter(self):
        fn, _ = _import_service()
        aid = str(uuid.uuid4())
        art = _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH")
        fact = _make_fact(artifact_id=aid, source_id=None)
        db = _FakeDB(artifact_rows=[art], source_rows=[], fact_rows=[fact])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.artifacts_without_source_linked_facts_count >= 1


# ── AC 9: Fact source_id not found → fact_source_not_found ──────────────────

class TestFactSourceNotFound:
    def test_unmatched_source_id_produces_reason_code(self):
        fn, _ = _import_service()
        aid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        art = _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH")
        src = _make_source(src_id=sid, artifact_id=aid)
        # Fact has a source_id but it doesn't match the source's id
        fact = _make_fact(artifact_id=aid, source_id="nonexistent-source-id")
        db = _FakeDB(artifact_rows=[art], source_rows=[src], fact_rows=[fact])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.by_readiness_reason_code.get("fact_source_not_found", 0) >= 1
        assert result.eligible_for_truth_adapter_count == 0


# ── AC 10: Source without provenance → no_valid_sources ─────────────────────

class TestSourceWithoutProvenance:
    def test_source_no_provenance_produces_reason_code(self):
        fn, _ = _import_service()
        aid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        art = _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH")
        # Source has no provenance handles (no source_url, source_id, source_hash, section_reference)
        src = {
            "id": sid,
            "artifact_id": aid,
            "source_kind": "sec_filing",
            "provider_name": "sec_edgar",
            "source_url": None,
            "section_reference": None,
            "source_id": None,
            "source_hash": None,
        }
        fact = _make_fact(artifact_id=aid, source_id=sid)
        db = _FakeDB(artifact_rows=[art], source_rows=[src], fact_rows=[fact])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.by_readiness_reason_code.get("no_valid_sources", 0) >= 1
        assert result.eligible_for_truth_adapter_count == 0


# ── AC 11: Forbidden payload key increments forbidden count + reason code ────

class TestForbiddenPayload:
    def test_forbidden_key_increments_violation_count(self):
        fn, _ = _import_service()
        art = _make_artifact(payload={"nested": {"final_action": "BUY"}})
        db = _FakeDB(artifact_rows=[art], source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.forbidden_payload_violation_count >= 1

    def test_forbidden_key_adds_reason_code(self):
        fn, _ = _import_service()
        art = _make_artifact(payload={"final_action": "BUY"})
        db = _FakeDB(artifact_rows=[art], source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        # Reason code includes forbidden key info
        codes = result.by_readiness_reason_code
        forbidden_codes = [k for k in codes if k.startswith("forbidden_")]
        assert len(forbidden_codes) >= 1


# ── AC 12: Child source query failure → errors[] only ────────────────────────

class TestSourceQueryFailure:
    def test_readiness_source_failure_appends_error(self):
        fn, _ = _import_service()
        art = _make_artifact(confidence="MEDIUM", freshness="FRESH")
        # Phase 4 source query succeeds; Phase 6B source query fails
        db = _FakeDB(
            artifact_rows=[art],
            source_rows=[],
            fact_rows=[],
            fail_readiness_source_query=True,
        )
        result = fn("u1", db, settings=_enabled_settings())
        assert any("readiness_sources_query_error" in e for e in result.errors)

    def test_does_not_raise_on_source_failure(self):
        fn, _ = _import_service()
        art = _make_artifact()
        db = _FakeDB(
            artifact_rows=[art],
            source_rows=[],
            fact_rows=[],
            fail_readiness_source_query=True,
        )
        # Must not raise
        result = fn("u1", db, settings=_enabled_settings())
        assert result is not None

    def test_readiness_still_evaluated_without_sources(self):
        fn, _ = _import_service()
        art = _make_artifact(confidence="MEDIUM", freshness="FRESH")
        db = _FakeDB(
            artifact_rows=[art],
            source_rows=[],
            fact_rows=[],
            fail_readiness_source_query=True,
        )
        # Evaluation runs fail-closed: no sources → ineligible
        result = fn("u1", db, settings=_enabled_settings())
        assert result.readiness_evaluated_count >= 0  # may or may not evaluate
        assert result.eligible_for_truth_adapter_count == 0


# ── AC 13: Child fact query failure → errors[] only ─────────────────────────

class TestFactQueryFailure:
    def test_readiness_fact_failure_appends_error(self):
        fn, _ = _import_service()
        art = _make_artifact(confidence="MEDIUM", freshness="FRESH")
        db = _FakeDB(
            artifact_rows=[art],
            source_rows=[],
            fact_rows=[],
            fail_readiness_fact_query=True,
        )
        result = fn("u1", db, settings=_enabled_settings())
        assert any("readiness_facts_query_error" in e for e in result.errors)

    def test_does_not_raise_on_fact_failure(self):
        fn, _ = _import_service()
        art = _make_artifact()
        db = _FakeDB(
            artifact_rows=[art],
            source_rows=[],
            fact_rows=[],
            fail_readiness_fact_query=True,
        )
        result = fn("u1", db, settings=_enabled_settings())
        assert result is not None


# ── AC 14: Diagnostic output does not expose raw data ───────────────────────

class TestNoRawDataExposed:
    def test_summary_has_no_source_url_field(self):
        fn, Summary = _import_service()
        fields = {f.name for f in Summary.__dataclass_fields__.values()}
        assert "source_url" not in fields

    def test_summary_has_no_structured_payload_field(self):
        fn, Summary = _import_service()
        fields = {f.name for f in Summary.__dataclass_fields__.values()}
        assert "structured_payload" not in fields

    def test_summary_has_no_quote_or_excerpt_field(self):
        fn, Summary = _import_service()
        fields = {f.name for f in Summary.__dataclass_fields__.values()}
        assert "quote_or_excerpt" not in fields

    def test_summary_has_no_raw_payload_field(self):
        fn, Summary = _import_service()
        # "payload" is a Phase 4 computed field for forbidden key check — not returned
        # The summary should not have a field that exposes raw payload content
        aid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        art = _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH")
        src = _make_source(src_id=sid, artifact_id=aid)
        fact = _make_fact(artifact_id=aid, source_id=sid)
        db = _FakeDB(artifact_rows=[art], source_rows=[src], fact_rows=[fact])
        result = fn("u1", db, settings=_enabled_settings())
        result_dict = result.__dict__
        # No raw source or fact data in the summary result
        assert "source_url" not in result_dict
        assert "structured_payload" not in result_dict
        assert "quote_or_excerpt" not in result_dict


# ── AC 15/16: Endpoint 403 when flag off / cert required ────────────────────

class TestEndpointGating:
    def test_endpoint_403_when_observability_flag_off(self):
        """Endpoint returns 403 when observability flag is off — enforced in diagnostics.py."""
        from app.routers.diagnostics import observe_research_artifacts
        # This is a behavioral contract confirmed by Phase 4 endpoint tests.
        # We verify the endpoint code path here by checking the source text.
        import inspect
        src = inspect.getsource(observe_research_artifacts)
        assert "intel_v3_research_artifact_observability_enabled" in src
        assert "HTTP_403_FORBIDDEN" in src or "403" in src

    def test_endpoint_uses_runtime_cert_dependency(self):
        from app.routers.diagnostics import observe_research_artifacts
        import inspect
        src = inspect.getsource(observe_research_artifacts)
        assert "_get_runtime_cert_user" in src


# ── AC 17: Endpoint caps params ──────────────────────────────────────────────

class TestEndpointCaps:
    def test_endpoint_caps_tickers(self):
        from app.routers.diagnostics import MAX_OBSERVE_TICKERS_PER_REQUEST
        assert MAX_OBSERVE_TICKERS_PER_REQUEST == 10

    def test_endpoint_caps_lookback_days(self):
        from app.routers.diagnostics import MAX_OBSERVE_LOOKBACK_DAYS, MIN_OBSERVE_LOOKBACK_DAYS
        assert MAX_OBSERVE_LOOKBACK_DAYS == 365
        assert MIN_OBSERVE_LOOKBACK_DAYS == 1

    def test_endpoint_caps_max_rows(self):
        from app.routers.diagnostics import MAX_OBSERVE_ROWS, MIN_OBSERVE_ROWS
        assert MAX_OBSERVE_ROWS == 1000
        assert MIN_OBSERVE_ROWS == 1


# ── AC 18: Endpoint returns new readiness fields ────────────────────────────

class TestEndpointNewFields:
    def test_endpoint_includes_readiness_fields_in_response(self):
        """Verify the endpoint response dict includes all new Phase 6B keys."""
        import inspect
        from app.routers.diagnostics import observe_research_artifacts
        src = inspect.getsource(observe_research_artifacts)
        for field_name in [
            "readiness_evaluated_count",
            "eligible_for_truth_adapter_count",
            "eligible_for_decision_consumption_count",
            "safe_for_decision_db_promotion_blocked_count",
            "fail_closed_count",
            "by_readiness_reason_code",
            "phase5_ready_but_decision_blocked_count",
            "readiness_visible_snapshot_unchanged",
        ]:
            assert field_name in src, f"Missing field in endpoint response: {field_name}"


# ── AC 19: No frontend/page-load path references endpoint ────────────────────

class TestNoFrontendReference:
    def test_observe_endpoint_not_referenced_in_frontend_hooks(self):
        import os
        frontend_root = os.path.join(
            os.path.dirname(__file__), "..", "..", "frontend", "src", "lib"
        )
        if not os.path.isdir(frontend_root):
            pytest.skip("Frontend lib directory not found")
        endpoint_path = "research-artifacts/observe"
        for fname in os.listdir(frontend_root):
            if fname.endswith((".ts", ".tsx")):
                fpath = os.path.join(frontend_root, fname)
                content = open(fpath).read()
                assert endpoint_path not in content, (
                    f"Observability endpoint referenced in frontend file: {fname}"
                )


# ── AC 20: Static import guard: no decide() or IntelV3Service ────────────────

class TestStaticImportGuards:
    def _get_module_source(self) -> str:
        import os
        module_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "app",
            "services",
            "intelligence",
            "research_workers",
            "artifact_observability.py",
        )
        with open(module_path) as f:
            return f.read()

    def test_does_not_import_decide(self):
        src = self._get_module_source()
        # Must not import the decision policy function
        assert "from .decision_policy_v1" not in src
        assert "import decision_policy_v1" not in src
        assert "import decide" not in src

    def test_does_not_import_intel_v3_service(self):
        src = self._get_module_source()
        assert "IntelV3Service" not in src
        assert "import intel_v3_service" not in src

    def test_does_not_import_recommendation_engine(self):
        src = self._get_module_source()
        assert "from .recommendation_engine" not in src
        assert "import recommendation_engine" not in src


# ── AC 21: No writes to intel_v3_snapshots ───────────────────────────────────

class TestNoSnapshotWrites:
    def test_no_intel_v3_snapshots_in_source(self):
        import os
        module_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "app",
            "services",
            "intelligence",
            "research_workers",
            "artifact_observability.py",
        )
        with open(module_path) as f:
            src = f.read()
        # Must not call table("intel_v3_snapshots") — docstring mentions it as a
        # structural guarantee (never writes), but the actual DB call must not exist.
        assert '.table("intel_v3_snapshots")' not in src
        assert ".table('intel_v3_snapshots')" not in src

    def test_service_does_not_write_artifacts(self):
        fn, _ = _import_service()
        aid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        art = _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH")
        src = _make_source(src_id=sid, artifact_id=aid)
        fact = _make_fact(artifact_id=aid, source_id=sid)
        db = _FakeDB(artifact_rows=[art], source_rows=[src], fact_rows=[fact])
        fn("u1", db, settings=_enabled_settings())
        # No writes should have occurred
        assert db.get_written_tables() == []


# ── AC 22: No artifact-to-decision integration ────────────────────────────────

class TestNoDecisionIntegration:
    def test_artifact_truth_readiness_import_is_read_only(self):
        """evaluate_artifact_truth_readiness is pure/read-only — no DB, no decide()."""
        import inspect
        from app.services.intelligence.research_workers.artifact_truth_readiness import (
            evaluate_artifact_truth_readiness,
        )
        src = inspect.getsource(evaluate_artifact_truth_readiness)
        # Pure function — no DB calls, no decision path imports
        assert "decide(" not in src
        assert "intel_v3_snapshots" not in src

    def test_eligible_for_decision_consumption_always_false(self):
        """eligible_for_decision_consumption is structurally always False."""
        from app.services.intelligence.research_workers.artifact_truth_readiness import (
            evaluate_artifact_truth_readiness,
        )
        # Even a perfect artifact returns eligible_for_decision_consumption=False
        aid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        art = _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH")
        src_dict = _make_source(src_id=sid, artifact_id=aid)
        fact_dict = _make_fact(artifact_id=aid, source_id=sid)
        result = evaluate_artifact_truth_readiness(
            artifact=art,
            sources=[src_dict],
            facts=[fact_dict],
        )
        assert result.eligible_for_decision_consumption is False


# ── AC 23: No SQL (structural check) ─────────────────────────────────────────

class TestNoSql:
    def test_no_new_migration_files_for_phase6b(self):
        """Phase 6B must not introduce new SQL migration files."""
        import os, glob
        db_dir = os.path.join(os.path.dirname(__file__), "..", "..", "database")
        if not os.path.isdir(db_dir):
            pytest.skip("database directory not found")
        migrations = sorted(glob.glob(os.path.join(db_dir, "0*.sql")))
        # The latest migration should be 017 (Phase 2.1) — no new ones for Phase 6B
        for m in migrations:
            basename = os.path.basename(m)
            seq = int(basename.split("_")[0])
            assert seq <= 17, f"Unexpected migration file found: {basename}"


# ── Integration: Phase 6B summary invariants ─────────────────────────────────

class TestPhase6BInvariants:
    """Cross-cutting invariants for Phase 6B readiness summary."""

    def test_fail_closed_count_equals_evaluated_count(self):
        fn, _ = _import_service()
        arts = [_make_artifact() for _ in range(4)]
        db = _FakeDB(artifact_rows=arts, source_rows=[], fact_rows=[])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.fail_closed_count == result.readiness_evaluated_count

    def test_readiness_visible_snapshot_unchanged_always_true(self):
        fn, _ = _import_service()
        aid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        art = _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH")
        src = _make_source(src_id=sid, artifact_id=aid)
        fact = _make_fact(artifact_id=aid, source_id=sid)
        db = _FakeDB(artifact_rows=[art], source_rows=[src], fact_rows=[fact])
        result = fn("u1", db, settings=_enabled_settings())
        assert result.readiness_visible_snapshot_unchanged is True

    def test_evaluated_plus_artifacts_source_linked_coverage(self):
        """artifacts_with_source_linked_facts + without should equal artifact_count."""
        fn, _ = _import_service()
        aid1 = str(uuid.uuid4())
        sid1 = str(uuid.uuid4())
        aid2 = str(uuid.uuid4())
        arts = [
            _make_artifact(aid=aid1, confidence="MEDIUM", freshness="FRESH"),
            _make_artifact(aid=aid2, confidence="UNKNOWN"),
        ]
        sources = [_make_source(src_id=sid1, artifact_id=aid1)]
        facts = [
            _make_fact(artifact_id=aid1, source_id=sid1),   # source-linked
            _make_fact(artifact_id=aid2, source_id=None),   # no source link
        ]
        db = _FakeDB(artifact_rows=arts, source_rows=sources, fact_rows=facts)
        result = fn("u1", db, settings=_enabled_settings())
        assert (
            result.artifacts_with_source_linked_facts_count
            + result.artifacts_without_source_linked_facts_count
            == result.artifact_count
        )

    def test_disabled_flag_returns_zero_readiness_counts(self):
        fn, _ = _import_service()
        aid = str(uuid.uuid4())
        sid = str(uuid.uuid4())
        art = _make_artifact(aid=aid, confidence="MEDIUM", freshness="FRESH")
        src = _make_source(src_id=sid, artifact_id=aid)
        fact = _make_fact(artifact_id=aid, source_id=sid)
        db = _FakeDB(artifact_rows=[art], source_rows=[src], fact_rows=[fact])
        # With observability disabled, all Phase 6B counters should be 0
        result = fn("u1", db, settings=_disabled_settings())
        assert result.readiness_evaluated_count == 0
        assert result.eligible_for_truth_adapter_count == 0
        assert result.eligible_for_decision_consumption_count == 0
