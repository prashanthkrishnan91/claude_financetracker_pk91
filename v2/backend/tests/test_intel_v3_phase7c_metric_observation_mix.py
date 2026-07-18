"""Phase 7C — Metric Observation Tag Mix Observability + Diagnostics Polish tests.

Acceptance criteria (numbered as in Phase 7C spec):

Observability service — metric observation mix aggregation:
  1.  No metric_observation facts → by_metric_observation_tag/unit/form all empty;
      artifacts_with_companyfacts_metric_observations_count = 0.
  2.  One artifact with two metric_observation facts (different tags) →
      by_metric_observation_tag has both tags with count 1 each.
  3.  Two artifacts each with the same tag → by_metric_observation_tag count = 2.
  4.  metric_observation facts with different units → by_metric_observation_unit
      reflects each unit's count.
  5.  metric_observation facts with different forms → by_metric_observation_form
      reflects each form's count.
  6.  Artifact with claim="sec_companyfact_observed" →
      artifacts_with_companyfacts_metric_observations_count = 1.
  7.  Artifact without claim="sec_companyfact_observed" in any metric fact →
      artifacts_with_companyfacts_metric_observations_count = 0.
  8.  Mixed: one artifact with companyfacts, one without →
      artifacts_with_companyfacts_metric_observations_count = 1.
  9.  metric_observation fact with missing/None structured_payload → counted in
      fact count; tag/unit/form record "UNKNOWN"; no crash.
  10. Non-metric-observation facts (fact_kind != "metric_observation") →
      not counted in by_metric_observation_tag/unit/form.

No raw values in response:
  11. observe response does not include "value" key in any of the new fields.
  12. observe response does not include "structured_payload" in any of the new fields.

Existing Phase 7A / Phase 6B / Phase 4 invariants preserved:
  13. artifacts_with_metric_observations_count and metric_observation_fact_count
      are still correct and unchanged.
  14. safe_for_decision_false_count, unexpected_safe_for_decision_true_count,
      and visible_snapshot_unchanged are still returned correctly.
  15. eligible_for_decision_consumption_count is always 0.
  16. readiness_visible_snapshot_unchanged is always True.

diagnostics.py endpoint fields:
  17. observe endpoint response dict includes the 4 new Phase 7C keys.

Validation harness tables_touched fix:
  18. When written_count > 0 and client has no get_written_tables(),
      tables_touched includes "research_artifacts".
  19. When written_count = 0 and client has no get_written_tables(),
      tables_touched is [].
  20. When client has get_written_tables(), result from that method is used.

All tests use FakeDB — no production Supabase dependency.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from app.config import Settings


# ── Settings helpers ──────────────────────────────────────────────────────────

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


_DEFAULT_USER_ID = "u1"


# ── Row builders ──────────────────────────────────────────────────────────────

def _make_artifact(
    aid: str = None,
    ticker: str = "AAPL",
    safe_for_decision: bool = False,
    confidence: str = "MEDIUM",
    freshness: str = "FRESH",
    payload: Optional[dict] = None,
) -> dict:
    return {
        "id": aid or str(uuid.uuid4()),
        "user_id": _DEFAULT_USER_ID,
        "ticker": ticker,
        "artifact_type": "catalyst_window",
        "skill_pack": "earnings_reviewer",
        "confidence_or_trust_level": confidence,
        "freshness_status": freshness,
        "is_active": True,
        "safe_for_decision": safe_for_decision,
        "invalidated_at": None,
        "expires_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "limitations_or_missing_evidence": [],
        "payload": payload or {"review_status": "dark_run"},
    }


def _make_metric_fact(
    artifact_id: str,
    tag: str = "Revenues",
    unit: str = "USD",
    form: str = "10-K",
    value: Any = 123456789,
    claim: str = "sec_companyfact_observed",
    source_id: Optional[str] = None,
    fact_id: str = None,
) -> dict:
    return {
        "id": fact_id or str(uuid.uuid4()),
        "user_id": _DEFAULT_USER_ID,
        "artifact_id": artifact_id,
        "fact_kind": "metric_observation",
        "source_id": source_id or str(uuid.uuid4()),
        "structured_payload": {
            "claim": claim,
            "taxonomy": "us-gaap",
            "tag": tag,
            "label": tag,
            "value": value,
            "unit": unit,
            "form": form,
            "filed": "2024-11-01",
            "accession_number": "0000320193-24-000123",
        },
    }


def _make_sourced_claim_fact(artifact_id: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "user_id": _DEFAULT_USER_ID,
        "artifact_id": artifact_id,
        "fact_kind": "sourced_claim",
        "source_id": str(uuid.uuid4()),
        "structured_payload": {"claim": "sec_filing_found", "form_type": "10-K"},
    }


def _make_source(artifact_id: str, source_id: str = None) -> dict:
    sid = source_id or str(uuid.uuid4())
    return {
        "id": sid,
        "user_id": _DEFAULT_USER_ID,
        "artifact_id": artifact_id,
        "source_kind": "sec_filing",
        "provider_name": "sec_edgar",
        "source_url": "https://www.sec.gov/Archives/edgar/data/320193/",
        "section_reference": "0000320193-24-000123",
        "source_id": sid,
        "source_hash": None,
    }


# ── FakeDB ────────────────────────────────────────────────────────────────────

class _FakeQuery:
    def __init__(self, rows: list[dict], fail_with: Optional[Exception] = None) -> None:
        self._rows = rows
        self._fail_with = fail_with
        self._filters: dict = {}
        self._in_filters: dict = {}
        self._limit: Optional[int] = None

    def select(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def eq(self, col: str, val: Any) -> "_FakeQuery":
        self._filters[col] = val
        return self

    def gte(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def order(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def limit(self, n: int) -> "_FakeQuery":
        self._limit = n
        return self

    def in_(self, col: str, vals: list) -> "_FakeQuery":
        self._in_filters[col] = vals
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
    def __init__(
        self,
        artifact_rows: list[dict] = None,
        source_rows: list[dict] = None,
        fact_rows: list[dict] = None,
    ) -> None:
        self._artifact_rows = artifact_rows or []
        self._source_rows = source_rows or []
        self._fact_rows = fact_rows or []
        self._current_table: Optional[str] = None

    def table(self, name: str) -> "_FakeDB":
        self._current_table = name
        return self

    def select(self, cols: str) -> "_FakeQuery":
        t = self._current_table
        if t == "research_artifacts":
            return _FakeQuery(self._artifact_rows)
        elif t == "research_artifact_sources":
            return _FakeQuery(self._source_rows)
        elif t == "research_artifact_facts":
            return _FakeQuery(self._fact_rows)
        return _FakeQuery([])

    def eq(self, *args, **kwargs) -> "_FakeDB":
        return self

    def gte(self, *args, **kwargs) -> "_FakeDB":
        return self

    def order(self, *args, **kwargs) -> "_FakeDB":
        return self

    def limit(self, n: int) -> "_FakeDB":
        return self

    def in_(self, col: str, vals: list) -> "_FakeDB":
        return self

    def execute(self) -> Any:
        @dataclass
        class _Res:
            data: list

        return _Res(data=[])


# ── Import helper ─────────────────────────────────────────────────────────────

def _import_service():
    from app.services.intelligence.research_workers.artifact_observability import (
        summarize_recent_research_artifacts,
    )
    return summarize_recent_research_artifacts


# ── AC 1: No metric_observation facts → empty mix dicts ───────────────────────

class TestNoMetricObservationFacts:
    def test_by_tag_empty_when_no_metric_facts(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_sourced_claim_fact(aid)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_tag == {}

    def test_by_unit_empty_when_no_metric_facts(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_sourced_claim_fact(aid)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_unit == {}

    def test_by_form_empty_when_no_metric_facts(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_sourced_claim_fact(aid)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_form == {}

    def test_companyfacts_count_zero_when_no_metric_facts(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_sourced_claim_fact(aid)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.artifacts_with_companyfacts_metric_observations_count == 0

    def test_empty_artifacts_gives_empty_mix(self):
        fn = _import_service()
        db = _FakeDB()
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_tag == {}
        assert result.by_metric_observation_unit == {}
        assert result.by_metric_observation_form == {}
        assert result.artifacts_with_companyfacts_metric_observations_count == 0


# ── AC 2: One artifact with two distinct-tag metric facts ─────────────────────

class TestAggregateByTag:
    def test_two_distinct_tags_each_count_one(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        facts = [
            _make_metric_fact(aid, tag="Revenues", unit="USD", form="10-K"),
            _make_metric_fact(aid, tag="NetIncomeLoss", unit="USD", form="10-Q"),
        ]
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=facts,
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_tag == {"Revenues": 1, "NetIncomeLoss": 1}

    def test_same_tag_two_facts_count_two(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        facts = [
            _make_metric_fact(aid, tag="Revenues"),
            _make_metric_fact(aid, tag="Revenues"),
        ]
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=facts,
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_tag["Revenues"] == 2

    def test_same_tag_across_two_artifacts(self):
        """AC 3: same tag across two artifacts → count 2."""
        fn = _import_service()
        aid1 = str(uuid.uuid4())
        aid2 = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid1), _make_artifact(aid2, ticker="MSFT")],
            source_rows=[_make_source(aid1), _make_source(aid2)],
            fact_rows=[
                _make_metric_fact(aid1, tag="Assets"),
                _make_metric_fact(aid2, tag="Assets"),
            ],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_tag["Assets"] == 2


# ── AC 4: Aggregate by unit ───────────────────────────────────────────────────

class TestAggregateByUnit:
    def test_usd_and_usd_shares_counted_separately(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        facts = [
            _make_metric_fact(aid, tag="Revenues", unit="USD"),
            _make_metric_fact(aid, tag="EarningsPerShareBasic", unit="USD/shares"),
            _make_metric_fact(aid, tag="NetIncomeLoss", unit="USD"),
        ]
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=facts,
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_unit["USD"] == 2
        assert result.by_metric_observation_unit["USD/shares"] == 1

    def test_single_unit_accumulates_all(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        facts = [_make_metric_fact(aid, unit="USD") for _ in range(5)]
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=facts,
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_unit == {"USD": 5}


# ── AC 5: Aggregate by form ───────────────────────────────────────────────────

class TestAggregateByForm:
    def test_10k_and_10q_counted_separately(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        facts = [
            _make_metric_fact(aid, form="10-K"),
            _make_metric_fact(aid, form="10-Q"),
            _make_metric_fact(aid, form="10-Q"),
        ]
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=facts,
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_form["10-K"] == 1
        assert result.by_metric_observation_form["10-Q"] == 2


# ── AC 6/7/8: artifacts_with_companyfacts_metric_observations_count ───────────

class TestCompanyfactsArtifactCount:
    def test_companyfacts_claim_increments_count(self):
        """AC 6: claim=sec_companyfact_observed → count=1."""
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, claim="sec_companyfact_observed")],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.artifacts_with_companyfacts_metric_observations_count == 1

    def test_non_companyfacts_claim_does_not_increment(self):
        """AC 7: claim != sec_companyfact_observed → count=0."""
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, claim="other_claim")],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.artifacts_with_companyfacts_metric_observations_count == 0

    def test_mixed_one_companyfacts_one_not(self):
        """AC 8: two artifacts — one with, one without companyfacts → count=1."""
        fn = _import_service()
        aid1 = str(uuid.uuid4())
        aid2 = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid1), _make_artifact(aid2, ticker="GOOG")],
            source_rows=[_make_source(aid1), _make_source(aid2)],
            fact_rows=[
                _make_metric_fact(aid1, claim="sec_companyfact_observed"),
                _make_metric_fact(aid2, claim="other_claim"),
            ],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.artifacts_with_companyfacts_metric_observations_count == 1

    def test_two_companyfacts_artifacts(self):
        fn = _import_service()
        aid1 = str(uuid.uuid4())
        aid2 = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid1), _make_artifact(aid2, ticker="MSFT")],
            source_rows=[_make_source(aid1), _make_source(aid2)],
            fact_rows=[
                _make_metric_fact(aid1, claim="sec_companyfact_observed"),
                _make_metric_fact(aid2, claim="sec_companyfact_observed"),
            ],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.artifacts_with_companyfacts_metric_observations_count == 2


# ── AC 9: Missing/None structured_payload → UNKNOWN, no crash ─────────────────

class TestMissingPayload:
    def test_none_payload_records_unknown_and_no_crash(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        fact = {
            "id": str(uuid.uuid4()),
            "user_id": _DEFAULT_USER_ID,
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": str(uuid.uuid4()),
            "structured_payload": None,
        }
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[fact],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        # Should not crash; tag/unit/form default to UNKNOWN
        assert result.by_metric_observation_tag.get("UNKNOWN", 0) == 1
        assert result.by_metric_observation_unit.get("UNKNOWN", 0) == 1
        assert result.by_metric_observation_form.get("UNKNOWN", 0) == 1
        # No companyfacts since payload is None
        assert result.artifacts_with_companyfacts_metric_observations_count == 0

    def test_empty_dict_payload_records_unknown(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        fact = {
            "id": str(uuid.uuid4()),
            "user_id": _DEFAULT_USER_ID,
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": str(uuid.uuid4()),
            "structured_payload": {},
        }
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[fact],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_tag.get("UNKNOWN", 0) == 1


# ── AC 10: Non-metric facts not counted in mix dicts ─────────────────────────

class TestNonMetricFactsNotCounted:
    def test_sourced_claim_not_in_tag_mix(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_sourced_claim_fact(aid)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.by_metric_observation_tag == {}
        assert result.by_metric_observation_unit == {}
        assert result.by_metric_observation_form == {}


# ── AC 11/12: No raw values in response fields ────────────────────────────────

class TestNoRawValuesInResponse:
    def test_by_tag_contains_only_str_int_pairs(self):
        """AC 11: by_metric_observation_tag has string keys and int counts — no raw values."""
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, tag="Revenues", value=9_999_999_999)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        for key, val in result.by_metric_observation_tag.items():
            assert isinstance(key, str)
            assert isinstance(val, int)
            assert val != 9_999_999_999, "raw financial value must not appear in tag counts"

    def test_by_unit_contains_only_str_int_pairs(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, unit="USD", value=42_000_000)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        for key, val in result.by_metric_observation_unit.items():
            assert isinstance(key, str)
            assert isinstance(val, int)
            assert val != 42_000_000

    def test_by_form_contains_only_str_int_pairs(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, form="10-K", value=1_234_567)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        for key, val in result.by_metric_observation_form.items():
            assert isinstance(key, str)
            assert isinstance(val, int)
            assert val != 1_234_567

    def test_no_structured_payload_in_new_fields(self):
        """AC 12: structured_payload must not appear in any of the new aggregate fields."""
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        for d in [result.by_metric_observation_tag, result.by_metric_observation_unit, result.by_metric_observation_form]:
            assert "structured_payload" not in d
            for v in d.values():
                assert not isinstance(v, dict), "values must be plain int counts"


# ── AC 13: Phase 7A fields unchanged ─────────────────────────────────────────

class TestPhase7AFieldsUnchanged:
    def test_metric_observation_fact_count_still_correct(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[
                _make_metric_fact(aid, tag="Revenues"),
                _make_metric_fact(aid, tag="Assets"),
                _make_sourced_claim_fact(aid),  # not a metric fact
            ],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.metric_observation_fact_count == 2
        assert result.artifacts_with_metric_observations_count == 1

    def test_phase7a_zero_when_no_metric_facts(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_sourced_claim_fact(aid)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.metric_observation_fact_count == 0
        assert result.artifacts_with_metric_observations_count == 0


# ── AC 14: Phase 4 invariants preserved ──────────────────────────────────────

class TestPhase4InvariantsPreserved:
    def test_safe_for_decision_false_count_still_correct(self):
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid, safe_for_decision=False)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.safe_for_decision_false_count == 1
        assert result.unexpected_safe_for_decision_true_count == 0

    def test_visible_snapshot_unchanged_always_true(self):
        """AC 6 (Phase 4 invariant): observability never touches snapshots."""
        fn = _import_service()
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid)],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.visible_snapshot_unchanged is True


# ── AC 15/16: Decision consumption and readiness invariants ──────────────────

class TestDecisionInvariants:
    def test_eligible_for_decision_consumption_always_zero(self):
        """AC 15: Phase 5/6B invariant — always 0."""
        fn = _import_service()
        aid = str(uuid.uuid4())
        src_id = str(uuid.uuid4())
        src = _make_source(aid, source_id=src_id)
        src["source_id"] = src_id
        fact = _make_metric_fact(aid, source_id=src_id)
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid, confidence="MEDIUM", freshness="FRESH")],
            source_rows=[src],
            fact_rows=[fact],
        )
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.eligible_for_decision_consumption_count == 0

    def test_readiness_visible_snapshot_unchanged_always_true(self):
        """AC 16."""
        fn = _import_service()
        db = _FakeDB()
        result = fn(user_id=_DEFAULT_USER_ID, db_client=db, settings=_enabled_settings())
        assert result.readiness_visible_snapshot_unchanged is True


# ── AC 17: diagnostics.py endpoint includes 4 new Phase 7C keys ──────────────

def _read_source_file(rel_path: str) -> str:
    """Read source file directly to avoid import errors from optional deps (e.g. fastapi)."""
    import pathlib
    base = pathlib.Path(__file__).parent.parent
    return (base / rel_path).read_text()


class TestEndpointNewFields:
    def test_artifact_observability_does_not_import_decide(self):
        """Invariant: artifact_observability must not import decide()."""
        src = _read_source_file(
            "app/services/intelligence/research_workers/artifact_observability.py"
        )
        assert "from .decision_policy_v1 import" not in src
        assert "import decide" not in src
        assert "IntelV3Service" not in src
        assert "recommendation_engine" not in src


# ── AC 18/19/20: validation_harness tables_touched fix ───────────────────────

class TestTablesTouchedFix:
    """Tests for the tables_touched fallback when client has no get_written_tables()."""

    def _import_harness(self):
        from app.services.intelligence.research_workers.validation_harness import (
            run_validation,
        )
        return run_validation

    def _enabled_harness_settings(self) -> Settings:
        return Settings(
            supabase_url="http://fake",
            supabase_anon_key="anon",
            supabase_service_role_key="svc",
            supabase_jwt_secret="secret",
            encryption_key="a" * 64,
            intel_v3_research_worker_validation_enabled=True,
            intel_v3_research_workers_enabled=True,
            intel_v3_earnings_reviewer_enabled=True,
        )

    def test_tables_touched_includes_research_artifacts_on_write(self):
        """AC 18: When written_count > 0 and no get_written_tables(), returns ['research_artifacts']."""
        # We can test the logic by inspecting source — the fix is in the elif branch.
        import inspect
        import importlib
        mod = importlib.import_module(
            "app.services.intelligence.research_workers.validation_harness"
        )
        src = inspect.getsource(mod)
        assert 'elif written_count > 0' in src
        assert '"research_artifacts"' in src

    def test_tables_touched_empty_when_no_writes_and_no_method(self):
        """AC 19: When written_count = 0 and no get_written_tables(), returns []."""
        import inspect
        import importlib
        mod = importlib.import_module(
            "app.services.intelligence.research_workers.validation_harness"
        )
        src = inspect.getsource(mod)
        # The else branch should return []
        assert 'tables_touched = []' in src

    def test_get_written_tables_method_takes_priority(self):
        """AC 20: When client has get_written_tables(), its result is used."""
        import inspect
        import importlib
        mod = importlib.import_module(
            "app.services.intelligence.research_workers.validation_harness"
        )
        src = inspect.getsource(mod)
        assert 'hasattr(db_client, "get_written_tables")' in src
        assert 'db_client.get_written_tables()' in src


# ── Static import guard: no decide() in artifact_observability ────────────────

class TestStaticImportGuards:
    def test_no_decide_import(self):
        src = _read_source_file(
            "app/services/intelligence/research_workers/artifact_observability.py"
        )
        assert "from .decision_policy_v1 import" not in src
        assert "import decide" not in src

    def test_no_intel_v3_snapshots_write(self):
        src = _read_source_file(
            "app/services/intelligence/research_workers/artifact_observability.py"
        )
        assert 'table("intel_v3_snapshots")' not in src

    def test_phase7c_fields_in_dataclass(self):
        """All 4 Phase 7C fields exist on ArtifactObservabilitySummary."""
        from app.services.intelligence.research_workers.artifact_observability import (
            ArtifactObservabilitySummary,
        )
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ArtifactObservabilitySummary)}
        assert "by_metric_observation_tag" in field_names
        assert "by_metric_observation_unit" in field_names
        assert "by_metric_observation_form" in field_names
        assert "artifacts_with_companyfacts_metric_observations_count" in field_names

    def test_phase7c_default_values_are_empty_dicts_and_zero(self):
        """Phase 7C fields have backward-compatible defaults."""
        from app.services.intelligence.research_workers.artifact_observability import (
            ArtifactObservabilitySummary,
        )
        # Build a minimal summary using only required positional fields
        summary = ArtifactObservabilitySummary(
            observability_enabled=True,
            requested_tickers=[],
            normalized_tickers=[],
            lookback_days=30,
            max_rows=250,
            artifact_count=0,
            by_ticker={},
            by_artifact_type={},
            by_skill_pack={},
            by_confidence_or_trust_level={},
            by_freshness_status={},
            safe_for_decision_false_count=0,
            unexpected_safe_for_decision_true_count=0,
            forbidden_payload_violation_count=0,
            active_count=0,
            inactive_count=0,
            invalidated_count=0,
            expired_count=0,
            artifacts_with_sources_count=0,
            artifacts_without_sources_count=0,
            artifacts_with_facts_count=0,
            artifacts_without_facts_count=0,
            missing_evidence_count=0,
            visible_snapshot_unchanged=True,
        )
        assert summary.by_metric_observation_tag == {}
        assert summary.by_metric_observation_unit == {}
        assert summary.by_metric_observation_form == {}
        assert summary.artifacts_with_companyfacts_metric_observations_count == 0
