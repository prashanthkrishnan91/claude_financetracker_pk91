"""Stage 5B focused tests — Source Credibility Registry v1.

Acceptance criteria verified:
  1. sec_filing → PRIMARY_AUTHORITY, OFFICIAL_REGULATORY authorship.
  2. company_disclosure / press_release → COMPANY_AUTHORED authority.
  3. vendor_fundamentals / vendor_estimates → VENDOR_DERIVED authority.
  4. news → EDITORIAL_CONTEXT authority; cannot support financial_stated_fact.
  5. other / unrecognized → UNKNOWN authority, is_insufficient=True.
  6. No-source artifact → has_sources=False, is_insufficient=True, UNKNOWN.
  7. write_artifact adds source_credibility_assessment to payload without forbidden keys.
  8. Idempotent replay still works (assessment not re-added on skip).
  9. Clean replacement still works for ticker and portfolio scope.
 10. No writes to intel_v3_snapshots or recommendations tables.
 11. Assessment is fully replayable: same sources → same output.
 12. strongest_authority_level respects authority rank ordering.
 13. Registry version is embedded in every assessment.
 14. per_source_assessments contains correct per-source metadata.

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
from app.services.intelligence.v3.research_artifact_service_v1 import (
    ResearchArtifactServiceV1,
)
from app.services.intelligence.v3.source_credibility_registry_v1 import (
    SOURCE_CREDIBILITY_REGISTRY_VERSION,
    AuthorityLevel,
    SourceAuthorship,
    assess_artifact_sources,
    get_source_kind_definition,
    KNOWN_SOURCE_KINDS,
    _CLAIMS_NEVER_SUPPORTED,
)


# ── Fake Supabase client (shared with Stage 5A pattern) ───────────────────────

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
        self._select_cols: Optional[str] = None
        self._limit_n: Optional[int] = None

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
        self._payload = patch
        return self

    def upsert(self, row: dict, **kwargs: Any) -> "_FakeTable":
        self._op = "insert"
        self._payload = row
        return self

    def eq(self, col: str, val: Any) -> "_FakeTable":
        self._filters[col] = val
        return self

    def neq(self, col: str, val: Any) -> "_FakeTable":
        self._neg_filters[col] = val
        return self

    def is_(self, col: str, val: str) -> "_FakeTable":
        if val == "null":
            self._null_filters[col] = True
        return self

    def order(self, col: str, desc: bool = False) -> "_FakeTable":
        return self

    def limit(self, n: int) -> "_FakeTable":
        self._limit_n = n
        return self

    def execute(self) -> Any:
        if self._op == "insert" and self._payload:
            row = dict(self._payload)
            row.setdefault("id", str(uuid.uuid4()))
            self._shared.tables.setdefault(self._name, []).append(row)
            self._shared.inserts.setdefault(self._name, []).append(row)

            class _Res:
                data = [row]
            return _Res()

        if self._op == "update" and self._payload:
            op = _UpdateOp(
                table=self._name,
                patch=dict(self._payload),
                filters={
                    **self._filters,
                    "_neq": self._neg_filters,
                    "_is_null": list(self._null_filters.keys()),
                },
            )
            self._shared.updates.append(op)
            patched: list[dict] = []
            rows = self._shared.tables.get(self._name, [])
            for r in rows:
                if self._matches(r):
                    r = {**r, **self._payload}
                    patched.append(r)
            for r in patched:
                idx = next(i for i, x in enumerate(rows) if x["id"] == r["id"])
                self._shared.tables[self._name][idx] = r

            class _Res2:
                data = patched
            return _Res2()

        if self._op == "select":
            rows = self._shared.tables.get(self._name, [])
            matched = [r for r in rows if self._matches(r)]
            if self._limit_n:
                matched = matched[: self._limit_n]

            class _Res3:
                data = matched
            return _Res3()

        class _Empty:
            data = []
        return _Empty()

    def _matches(self, row: dict) -> bool:
        for k, v in self._filters.items():
            if row.get(k) != v:
                return False
        for k, v in self._neg_filters.items():
            if row.get(k) == v:
                return False
        for k in self._null_filters:
            if row.get(k) is not None:
                return False
        return True


@dataclass
class _FakeDB:
    tables: dict[str, list[dict]] = field(default_factory=dict)
    inserts: dict[str, list[dict]] = field(default_factory=dict)
    updates: list[_UpdateOp] = field(default_factory=list)


class _FakeClient:
    def __init__(self, shared: _FakeDB) -> None:
        self._shared = shared

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(name, self._shared)


# ── Test helpers ──────────────────────────────────────────────────────────────

_USER_ID = "user-stage5b-test"
_TICKER = "MSFT"


def _src(source_kind: str, provider: str = "test_provider") -> SourceRecord:
    return SourceRecord(source_kind=source_kind, provider_name=provider)


def _make_output(
    *,
    key_suffix: str = "v1",
    sources: Optional[list[SourceRecord]] = None,
    ticker: Optional[str] = _TICKER,
    scope_kind: str = "ticker",
    artifact_type: str = "filing_risk",
    payload: Optional[dict] = None,
) -> WorkerOutput:
    idempotency_key = compute_replay_idempotency_key(
        skill_pack="test_pack_5b",
        scope_kind=scope_kind,
        ticker=ticker or "",
        source_refs_fingerprint=f"fp_{key_suffix}",
        model_version="none",
    )
    return WorkerOutput(
        worker_run_id=str(uuid.uuid4()),
        ticker=ticker,
        artifact_type=artifact_type,
        skill_pack="test_pack_5b",
        scope_kind=scope_kind,
        artifact_payload=payload or {"evidence_summary": "test"},
        sources=sources or [],
        facts=[],
        audit_events=[AuditEventRecord(tool_call="test", status="completed")],
        evidence_summary_plain_english="Test.",
        limitations_or_missing_evidence=[],
        confidence_or_trust_level="MEDIUM",
        freshness_status="FRESH",
        input_fingerprint=compute_input_fingerprint({"t": ticker, "s": key_suffix}),
        replay_idempotency_key=idempotency_key,
    )


def _make_service(db: Optional[_FakeDB] = None) -> tuple[ResearchArtifactServiceV1, _FakeDB]:
    if db is None:
        db = _FakeDB()
    return ResearchArtifactServiceV1(_FakeClient(db), _USER_ID), db


def _get_artifact_payload(db: _FakeDB) -> Optional[dict]:
    inserts = db.inserts.get("research_artifacts", [])
    if not inserts:
        return None
    row = inserts[-1]
    payload = row.get("payload")
    if isinstance(payload, str):
        import json
        return json.loads(payload)
    return payload


# ── Registry unit tests ───────────────────────────────────────────────────────

class TestSecFilingClassification:
    def test_authority_level_is_primary(self) -> None:
        defn = get_source_kind_definition("sec_filing")
        assert defn.authority_level == AuthorityLevel.PRIMARY_AUTHORITY

    def test_authorship_is_official_regulatory(self) -> None:
        defn = get_source_kind_definition("sec_filing")
        assert defn.authorship == SourceAuthorship.OFFICIAL_REGULATORY

    def test_supports_financial_stated_fact(self) -> None:
        defn = get_source_kind_definition("sec_filing")
        assert "financial_stated_fact" in defn.claim_categories_supported

    def test_supports_regulatory_disclosure(self) -> None:
        defn = get_source_kind_definition("sec_filing")
        assert "regulatory_disclosure" in defn.claim_categories_supported

    def test_assess_sec_filing_sources(self) -> None:
        result = assess_artifact_sources([_src("sec_filing")])
        assert result.strongest_authority_level == AuthorityLevel.PRIMARY_AUTHORITY.value
        assert result.has_sources is True
        assert result.is_insufficient is False

    def test_registry_version_embedded(self) -> None:
        result = assess_artifact_sources([_src("sec_filing")])
        assert result.registry_version == SOURCE_CREDIBILITY_REGISTRY_VERSION


class TestCompanyAuthoredClassification:
    def test_company_disclosure_authority(self) -> None:
        defn = get_source_kind_definition("company_disclosure")
        assert defn.authority_level == AuthorityLevel.COMPANY_AUTHORED
        assert defn.authorship == SourceAuthorship.COMPANY_AUTHORED

    def test_press_release_authority(self) -> None:
        defn = get_source_kind_definition("press_release")
        assert defn.authority_level == AuthorityLevel.COMPANY_AUTHORED
        assert defn.authorship == SourceAuthorship.COMPANY_AUTHORED

    def test_transcript_authority(self) -> None:
        defn = get_source_kind_definition("transcript")
        assert defn.authority_level == AuthorityLevel.COMPANY_AUTHORED
        assert defn.authorship == SourceAuthorship.COMPANY_AUTHORED

    def test_company_disclosure_supports_company_guidance(self) -> None:
        defn = get_source_kind_definition("company_disclosure")
        assert "company_guidance" in defn.claim_categories_supported

    def test_press_release_does_not_support_financial_stated_fact(self) -> None:
        defn = get_source_kind_definition("press_release")
        assert "financial_stated_fact" not in defn.claim_categories_supported

    def test_assess_company_authored_sources(self) -> None:
        result = assess_artifact_sources([_src("press_release")])
        assert result.strongest_authority_level == AuthorityLevel.COMPANY_AUTHORED.value
        assert result.is_insufficient is False

    def test_assess_mixed_company_sources(self) -> None:
        sources = [_src("company_disclosure"), _src("press_release"), _src("transcript")]
        result = assess_artifact_sources(sources)
        assert result.strongest_authority_level == AuthorityLevel.COMPANY_AUTHORED.value
        assert result.source_count == 3


class TestVendorDerivedClassification:
    def test_vendor_fundamentals_authority(self) -> None:
        defn = get_source_kind_definition("vendor_fundamentals")
        assert defn.authority_level == AuthorityLevel.VENDOR_DERIVED
        assert defn.authorship == SourceAuthorship.THIRD_PARTY_VENDOR

    def test_vendor_estimates_authority(self) -> None:
        defn = get_source_kind_definition("vendor_estimates")
        assert defn.authority_level == AuthorityLevel.VENDOR_DERIVED
        assert defn.authorship == SourceAuthorship.THIRD_PARTY_VENDOR

    def test_vendor_calendar_authority(self) -> None:
        defn = get_source_kind_definition("vendor_calendar")
        assert defn.authority_level == AuthorityLevel.VENDOR_DERIVED

    def test_peer_set_def_authority(self) -> None:
        defn = get_source_kind_definition("peer_set_def")
        assert defn.authority_level == AuthorityLevel.VENDOR_DERIVED

    def test_vendor_fundamentals_supports_vendor_derived_metric(self) -> None:
        defn = get_source_kind_definition("vendor_fundamentals")
        assert "vendor_derived_metric" in defn.claim_categories_supported

    def test_vendor_calendar_supports_earnings_calendar(self) -> None:
        defn = get_source_kind_definition("vendor_calendar")
        assert "earnings_calendar" in defn.claim_categories_supported

    def test_assess_vendor_sources(self) -> None:
        result = assess_artifact_sources([_src("vendor_fundamentals"), _src("vendor_estimates")])
        assert result.strongest_authority_level == AuthorityLevel.VENDOR_DERIVED.value
        assert result.is_insufficient is False
        assert result.source_kind_counts == {"vendor_fundamentals": 1, "vendor_estimates": 1}


class TestNewsEditorialClassification:
    def test_news_authority_is_editorial_context(self) -> None:
        defn = get_source_kind_definition("news")
        assert defn.authority_level == AuthorityLevel.EDITORIAL_CONTEXT

    def test_news_authorship_is_editorial(self) -> None:
        defn = get_source_kind_definition("news")
        assert defn.authorship == SourceAuthorship.EDITORIAL

    def test_news_does_not_support_financial_stated_fact(self) -> None:
        defn = get_source_kind_definition("news")
        assert "financial_stated_fact" not in defn.claim_categories_supported

    def test_news_does_not_support_regulatory_disclosure(self) -> None:
        defn = get_source_kind_definition("news")
        assert "regulatory_disclosure" not in defn.claim_categories_supported

    def test_news_supports_only_editorial_context(self) -> None:
        defn = get_source_kind_definition("news")
        assert defn.claim_categories_supported == frozenset({"editorial_context"})

    def test_assess_news_only_sources(self) -> None:
        result = assess_artifact_sources([_src("news"), _src("news")])
        assert result.strongest_authority_level == AuthorityLevel.EDITORIAL_CONTEXT.value
        assert result.is_insufficient is False

    def test_news_cannot_elevate_to_financial_fact(self) -> None:
        result = assess_artifact_sources([_src("news")])
        assert "financial_stated_fact" not in result.claim_categories_any_source_supports


class TestUnknownClassification:
    def test_other_source_kind_is_unknown(self) -> None:
        defn = get_source_kind_definition("other")
        assert defn.authority_level == AuthorityLevel.UNKNOWN
        assert defn.authorship == SourceAuthorship.UNKNOWN

    def test_unrecognized_source_kind_falls_back_to_other(self) -> None:
        defn = get_source_kind_definition("made_up_source_kind_xyz")
        assert defn.authority_level == AuthorityLevel.UNKNOWN

    def test_other_supports_no_claim_categories(self) -> None:
        defn = get_source_kind_definition("other")
        assert len(defn.claim_categories_supported) == 0

    def test_assess_other_source_is_insufficient(self) -> None:
        result = assess_artifact_sources([_src("other")])
        assert result.strongest_authority_level == AuthorityLevel.UNKNOWN.value
        assert result.is_insufficient is True

    def test_assess_unrecognized_kind_is_insufficient(self) -> None:
        result = assess_artifact_sources([_src("some_future_kind")])
        assert result.is_insufficient is True

    def test_other_is_not_a_known_source_kind(self) -> None:
        assert "other" not in KNOWN_SOURCE_KINDS

    def test_per_source_reflects_unknown_known_flag(self) -> None:
        result = assess_artifact_sources([_src("other")])
        assert result.per_source_assessments[0]["is_known_source_kind"] is False


class TestNoSourceArtifact:
    def test_empty_sources_has_sources_false(self) -> None:
        result = assess_artifact_sources([])
        assert result.has_sources is False

    def test_empty_sources_is_insufficient_true(self) -> None:
        result = assess_artifact_sources([])
        assert result.is_insufficient is True

    def test_empty_sources_unknown_authority(self) -> None:
        result = assess_artifact_sources([])
        assert result.strongest_authority_level == AuthorityLevel.UNKNOWN.value

    def test_empty_sources_zero_count(self) -> None:
        result = assess_artifact_sources([])
        assert result.source_count == 0
        assert result.source_kind_counts == {}

    def test_empty_sources_no_supported_claims(self) -> None:
        result = assess_artifact_sources([])
        assert result.claim_categories_any_source_supports == []

    def test_empty_sources_has_limitation_message(self) -> None:
        result = assess_artifact_sources([])
        assert len(result.aggregate_limitations) > 0
        assert "UNKNOWN" in result.aggregate_limitations[0]

    def test_empty_sources_version_embedded(self) -> None:
        result = assess_artifact_sources([])
        assert result.registry_version == SOURCE_CREDIBILITY_REGISTRY_VERSION


class TestNeverSupportedClaims:
    """All sources must never support decision-authority claim categories."""

    _DECISION_CLAIMS = [
        "future_performance", "price_target", "conviction",
        "allocation", "buy_sell_action", "final_action", "recommendation",
    ]

    @pytest.mark.parametrize("source_kind", [
        "sec_filing", "company_disclosure", "press_release", "transcript",
        "vendor_fundamentals", "vendor_estimates", "vendor_calendar",
        "peer_set_def", "news", "other",
    ])
    def test_no_source_kind_supports_decision_claims(self, source_kind: str) -> None:
        defn = get_source_kind_definition(source_kind)
        for claim in self._DECISION_CLAIMS:
            assert claim not in defn.claim_categories_supported, (
                f"{source_kind} must not support claim '{claim}'"
            )

    @pytest.mark.parametrize("source_kind", [
        "sec_filing", "news", "vendor_estimates",
    ])
    def test_never_supported_in_assessment(self, source_kind: str) -> None:
        result = assess_artifact_sources([_src(source_kind)])
        for claim in self._DECISION_CLAIMS:
            assert claim not in result.claim_categories_any_source_supports, (
                f"Assessment for {source_kind} must not claim support for '{claim}'"
            )
        assert set(self._DECISION_CLAIMS).issubset(
            set(result.claim_categories_no_source_can_support)
        )


class TestAuthorityLevelOrdering:
    def test_sec_filing_beats_news(self) -> None:
        result = assess_artifact_sources([_src("news"), _src("sec_filing")])
        assert result.strongest_authority_level == AuthorityLevel.PRIMARY_AUTHORITY.value

    def test_sec_filing_beats_vendor(self) -> None:
        result = assess_artifact_sources([_src("vendor_fundamentals"), _src("sec_filing")])
        assert result.strongest_authority_level == AuthorityLevel.PRIMARY_AUTHORITY.value

    def test_company_authored_beats_vendor(self) -> None:
        result = assess_artifact_sources([_src("vendor_estimates"), _src("transcript")])
        assert result.strongest_authority_level == AuthorityLevel.COMPANY_AUTHORED.value

    def test_vendor_beats_news(self) -> None:
        result = assess_artifact_sources([_src("news"), _src("vendor_fundamentals")])
        assert result.strongest_authority_level == AuthorityLevel.VENDOR_DERIVED.value

    def test_news_beats_other(self) -> None:
        result = assess_artifact_sources([_src("other"), _src("news")])
        assert result.strongest_authority_level == AuthorityLevel.EDITORIAL_CONTEXT.value


class TestReplayability:
    def test_same_sources_same_output(self) -> None:
        sources = [_src("sec_filing", "edgar"), _src("news", "reuters")]
        r1 = assess_artifact_sources(sources)
        r2 = assess_artifact_sources(sources)
        assert r1.to_dict() == r2.to_dict()

    def test_different_sources_different_output(self) -> None:
        r1 = assess_artifact_sources([_src("sec_filing")])
        r2 = assess_artifact_sources([_src("news")])
        assert r1.strongest_authority_level != r2.strongest_authority_level

    def test_source_kind_counts_accurate(self) -> None:
        sources = [
            _src("sec_filing"), _src("news"), _src("sec_filing"), _src("vendor_estimates"),
        ]
        result = assess_artifact_sources(sources)
        assert result.source_kind_counts == {
            "sec_filing": 2, "news": 1, "vendor_estimates": 1
        }

    def test_per_source_indexed_correctly(self) -> None:
        sources = [_src("sec_filing", "edgar"), _src("news", "bloomberg")]
        result = assess_artifact_sources(sources)
        assert result.per_source_assessments[0]["source_index"] == 0
        assert result.per_source_assessments[0]["source_kind"] == "sec_filing"
        assert result.per_source_assessments[1]["source_index"] == 1
        assert result.per_source_assessments[1]["source_kind"] == "news"

    def test_provider_name_captured_in_per_source(self) -> None:
        result = assess_artifact_sources([_src("sec_filing", "sec_edgar_v2")])
        assert result.per_source_assessments[0]["provider_name"] == "sec_edgar_v2"

    def test_limitations_deduplicated(self) -> None:
        sources = [_src("sec_filing"), _src("sec_filing"), _src("sec_filing")]
        result = assess_artifact_sources(sources)
        assert len(result.aggregate_limitations) == 1


# ── Write-path integration tests ──────────────────────────────────────────────

class TestWriteArtifactAddsCredibilityAssessment:
    def test_written_payload_contains_source_credibility_assessment(self) -> None:
        service, db = _make_service()
        sources = [_src("sec_filing")]
        output = _make_output(sources=sources)
        artifact_id = service.write_artifact(output)
        assert artifact_id is not None
        payload = _get_artifact_payload(db)
        assert payload is not None
        assert "source_credibility_assessment" in payload

    def test_assessment_has_registry_version(self) -> None:
        service, db = _make_service()
        service.write_artifact(_make_output(sources=[_src("sec_filing")]))
        payload = _get_artifact_payload(db)
        sca = payload["source_credibility_assessment"]
        assert sca["registry_version"] == SOURCE_CREDIBILITY_REGISTRY_VERSION

    def test_assessment_no_forbidden_keys(self) -> None:
        from app.services.intelligence.research_workers.contracts import (
            WORKER_FORBIDDEN_PAYLOAD_KEYS,
            _has_forbidden_key,
        )
        service, db = _make_service()
        service.write_artifact(_make_output(sources=[_src("sec_filing")]))
        payload = _get_artifact_payload(db)
        found = _has_forbidden_key(payload)
        assert found is None, f"Forbidden key '{found}' found in payload after credibility injection"

    def test_no_source_artifact_assessment_is_insufficient(self) -> None:
        service, db = _make_service()
        output = _make_output(sources=[])
        service.write_artifact(output)
        payload = _get_artifact_payload(db)
        sca = payload["source_credibility_assessment"]
        assert sca["is_insufficient"] is True
        assert sca["has_sources"] is False
        assert sca["strongest_authority_level"] == AuthorityLevel.UNKNOWN.value

    def test_sec_filing_source_gives_primary_authority_in_written_payload(self) -> None:
        service, db = _make_service()
        service.write_artifact(_make_output(sources=[_src("sec_filing")]))
        payload = _get_artifact_payload(db)
        sca = payload["source_credibility_assessment"]
        assert sca["strongest_authority_level"] == AuthorityLevel.PRIMARY_AUTHORITY.value
        assert sca["is_insufficient"] is False

    def test_news_source_gives_editorial_context_in_written_payload(self) -> None:
        service, db = _make_service()
        service.write_artifact(_make_output(sources=[_src("news")]))
        payload = _get_artifact_payload(db)
        sca = payload["source_credibility_assessment"]
        assert sca["strongest_authority_level"] == AuthorityLevel.EDITORIAL_CONTEXT.value

    def test_original_payload_keys_preserved(self) -> None:
        service, db = _make_service()
        output = _make_output(
            sources=[_src("vendor_fundamentals")],
            payload={"evidence_summary": "quarterly revenue trend"},
        )
        service.write_artifact(output)
        payload = _get_artifact_payload(db)
        assert payload["evidence_summary"] == "quarterly revenue trend"
        assert "source_credibility_assessment" in payload

    def test_assessment_written_artifact_has_per_source_assessments(self) -> None:
        service, db = _make_service()
        service.write_artifact(_make_output(sources=[_src("transcript", "refinitiv")]))
        payload = _get_artifact_payload(db)
        sca = payload["source_credibility_assessment"]
        assert sca["source_count"] == 1
        assert len(sca["per_source_assessments"]) == 1
        assert sca["per_source_assessments"][0]["source_kind"] == "transcript"
        assert sca["per_source_assessments"][0]["authority_level"] == AuthorityLevel.COMPANY_AUTHORED.value


class TestIdempotentReplayWithCredibility:
    def test_idempotent_replay_returns_existing_id(self) -> None:
        service, db = _make_service()
        output = _make_output(key_suffix="idem_v1", sources=[_src("sec_filing")])
        first_id = service.write_artifact(output)
        second_id = service.write_artifact(output)
        assert first_id is not None
        assert first_id == second_id

    def test_idempotent_replay_no_extra_insert(self) -> None:
        service, db = _make_service()
        output = _make_output(key_suffix="idem_v2", sources=[_src("news")])
        service.write_artifact(output)
        first_count = len(db.inserts.get("research_artifacts", []))
        service.write_artifact(output)
        second_count = len(db.inserts.get("research_artifacts", []))
        assert first_count == second_count


class TestCleanReplacementWithCredibility:
    def test_new_write_different_key_deactivates_old_ticker_scope(self) -> None:
        service, db = _make_service()
        first_id = service.write_artifact(
            _make_output(key_suffix="r1", sources=[_src("vendor_estimates")])
        )
        second_id = service.write_artifact(
            _make_output(key_suffix="r2", sources=[_src("sec_filing")])
        )
        assert first_id is not None
        assert second_id is not None
        assert first_id != second_id
        deactivations = [
            u for u in db.updates
            if u.table == "research_artifacts" and u.patch.get("is_active") is False
        ]
        assert len(deactivations) >= 1

    def test_portfolio_scope_clean_replacement(self) -> None:
        service, db = _make_service()
        first_id = service.write_artifact(
            _make_output(
                key_suffix="port_r1", ticker=None, scope_kind="portfolio",
                sources=[_src("vendor_fundamentals")],
            )
        )
        second_id = service.write_artifact(
            _make_output(
                key_suffix="port_r2", ticker=None, scope_kind="portfolio",
                sources=[_src("sec_filing")],
            )
        )
        assert first_id is not None
        assert second_id is not None
        assert first_id != second_id

    def test_second_write_assessment_reflects_new_sources(self) -> None:
        service, db = _make_service()
        service.write_artifact(_make_output(key_suffix="upd_r1", sources=[_src("news")]))
        service.write_artifact(_make_output(key_suffix="upd_r2", sources=[_src("sec_filing")]))
        payload = _get_artifact_payload(db)
        sca = payload["source_credibility_assessment"]
        assert sca["strongest_authority_level"] == AuthorityLevel.PRIMARY_AUTHORITY.value


class TestNoIntelV3SnapshotWrites:
    def test_write_artifact_does_not_touch_intel_v3_snapshots(self) -> None:
        service, db = _make_service()
        service.write_artifact(_make_output(sources=[_src("sec_filing")]))
        assert "intel_v3_snapshots" not in db.inserts
        assert "intel_v3_snapshots" not in db.tables

    def test_write_artifact_does_not_touch_recommendations(self) -> None:
        service, db = _make_service()
        service.write_artifact(_make_output(sources=[_src("vendor_fundamentals")]))
        assert "recommendations" not in db.inserts
        assert "recommendations" not in db.tables


class TestAssessmentToDictSerializable:
    def test_to_dict_keys_present(self) -> None:
        result = assess_artifact_sources([_src("sec_filing")])
        d = result.to_dict()
        expected_keys = {
            "registry_version", "has_sources", "is_insufficient", "source_count",
            "source_kind_counts", "strongest_authority_level", "per_source_assessments",
            "aggregate_limitations", "claim_categories_any_source_supports",
            "claim_categories_no_source_can_support",
        }
        assert expected_keys.issubset(set(d.keys()))

    def test_to_dict_no_enum_objects(self) -> None:
        result = assess_artifact_sources([_src("sec_filing")])
        d = result.to_dict()
        import json
        serialized = json.dumps(d)
        assert "AuthorityLevel" not in serialized
        assert "SourceAuthorship" not in serialized

    def test_no_source_to_dict_valid(self) -> None:
        result = assess_artifact_sources([])
        d = result.to_dict()
        assert d["has_sources"] is False
        assert d["is_insufficient"] is True
        import json
        json.dumps(d)  # must not raise
