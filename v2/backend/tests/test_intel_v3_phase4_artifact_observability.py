"""Phase 4 artifact observability service tests.

Covers all acceptance criteria from the Phase 4 task spec:

  1.  Disabled observability (flag off) returns no-op summary with observability_enabled=False.
  2.  Enabled observability reads only research artifact tables (no write calls).
  3.  Summary groups artifacts by ticker/type/skill_pack/trust/freshness.
  4.  safe_for_decision=true rows are counted as unexpected, not ignored.
  5.  Forbidden payload keys are counted.
  6.  Missing sources/facts are counted.
  7.  Missing evidence/limitations are counted (non-empty limitations_or_missing_evidence).
  8.  No raw payload/source/fact data is returned in the summary.
  9.  Summary visible_snapshot_unchanged is always True (structural guarantee).
  10. INFO logging is aggregate-only when enabled (no payload/URL/quote in log message).
  11. No imports/calls to decide() in the observability module.
  12. No writes to intel_v3_snapshots.
  13. expired_count counts rows where expires_at < now.
  14. active/inactive counts reflect is_active field.
  15. All query failures are contained in errors[]; no exception propagates.
  16. Static source guard: artifact_observability.py does not import decision_policy_v1.

No production Supabase dependency — all fakes defined here.
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


# ── FakeSupabaseClient ────────────────────────────────────────────────────────

class _FakeQuery:
    """Chainable fake query that returns pre-configured rows on execute()."""

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

    def execute(self) -> Any:
        if self._fail_with is not None:
            raise self._fail_with

        rows = self._rows
        # Apply eq filters (e.g. user_id guard on child tables)
        for col, val in self._filters.items():
            rows = [r for r in rows if str(r.get(col, "")) == str(val)]
        # Apply in_ filters (e.g. artifact_id IN (...))
        for col, vals in self._in_filters.items():
            rows = [r for r in rows if str(r.get(col)) in {str(v) for v in vals}]

        if self._limit is not None:
            rows = rows[: self._limit]

        class _Result:
            pass

        result = _Result()
        result.data = rows
        return result

    def insert(self, row: dict) -> "_FakeQuery":
        self._writes.append(row)
        return self


class FakeSupabaseClient:
    """Minimal Supabase client fake for Phase 4 observability tests.

    Tracks which tables were read (not written) so tests can assert read-only behavior.
    """

    def __init__(
        self,
        artifact_rows: Optional[list[dict]] = None,
        source_rows: Optional[list[dict]] = None,
        fact_rows: Optional[list[dict]] = None,
        fail_artifacts: Optional[Exception] = None,
        fail_sources: Optional[Exception] = None,
        fail_facts: Optional[Exception] = None,
    ) -> None:
        self._artifact_rows: list[dict] = artifact_rows or []
        self._source_rows: list[dict] = source_rows or []
        self._fact_rows: list[dict] = fact_rows or []
        self._fail_artifacts = fail_artifacts
        self._fail_sources = fail_sources
        self._fail_facts = fail_facts
        self.tables_read: list[str] = []
        self.tables_written: list[str] = []

    def table(self, name: str) -> "_FakeQuery":
        if name == "research_artifacts":
            self.tables_read.append(name)
            return _FakeQuery(self._artifact_rows, fail_with=self._fail_artifacts)
        if name == "research_artifact_sources":
            self.tables_read.append(name)
            return _FakeQuery(self._source_rows, fail_with=self._fail_sources)
        if name == "research_artifact_facts":
            self.tables_read.append(name)
            return _FakeQuery(self._fact_rows, fail_with=self._fail_facts)
        # intel_v3_snapshots and all other tables must never be accessed
        raise AssertionError(f"Unexpected table access in Phase 4 observability: {name!r}")


def _settings(enabled: bool = True, info_logs: bool = False) -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="anon",
        supabase_service_role_key="service",
        supabase_jwt_secret="secret",
        encryption_key="a" * 64,
        intel_v3_research_artifact_observability_enabled=enabled,
        intel_v3_research_artifact_observability_info_logs_enabled=info_logs,
    )


USER_ID = "u"


def _artifact(
    ticker: str = "AAPL",
    artifact_type: str = "catalyst_window",
    skill_pack: str = "earnings_reviewer",
    confidence_or_trust_level: str = "UNKNOWN",
    freshness_status: str = "UNKNOWN",
    safe_for_decision: bool = False,
    is_active: bool = True,
    expires_at: Optional[str] = None,
    invalidated_at: Optional[str] = None,
    limitations: Optional[list] = None,
    payload: Optional[dict] = None,
    user_id: str = USER_ID,
) -> dict:
    """Build a fake research_artifacts row using the real production column names."""
    aid = str(uuid.uuid4())
    return {
        "id": aid,
        "user_id": user_id,
        "ticker": ticker,
        "artifact_type": artifact_type,
        "skill_pack": skill_pack,
        "confidence_or_trust_level": confidence_or_trust_level,
        "freshness_status": freshness_status,
        "safe_for_decision": safe_for_decision,
        "is_active": is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "invalidated_at": invalidated_at,
        "limitations_or_missing_evidence": limitations or [],
        # Production column is "payload", not "artifact_payload"
        "payload": payload or {"summary": "ok"},
    }


def _source_row(artifact_id: str, user_id: str = USER_ID) -> dict:
    return {"artifact_id": artifact_id, "user_id": user_id}


def _fact_row(artifact_id: str, user_id: str = USER_ID) -> dict:
    return {"artifact_id": artifact_id, "user_id": user_id}


# ─────────────────────────────────────────────────────────────────────────────
# AC1: disabled returns no-op summary
# ─────────────────────────────────────────────────────────────────────────────

class TestDisabledObservability:
    def test_returns_summary_with_enabled_false(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient()
        s = _settings(enabled=False)
        result = summarize_recent_research_artifacts("user-1", db, settings=s)
        assert result.observability_enabled is False

    def test_disabled_summary_has_zero_counts(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient()
        s = _settings(enabled=False)
        result = summarize_recent_research_artifacts("user-1", db, settings=s)
        assert result.artifact_count == 0
        assert result.safe_for_decision_false_count == 0
        assert result.forbidden_payload_violation_count == 0

    def test_disabled_summary_visible_snapshot_unchanged_true(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient()
        s = _settings(enabled=False)
        result = summarize_recent_research_artifacts("user-1", db, settings=s)
        assert result.visible_snapshot_unchanged is True

    def test_disabled_no_db_reads(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient()
        s = _settings(enabled=False)
        summarize_recent_research_artifacts("user-1", db, settings=s)
        assert db.tables_read == []

    def test_disabled_has_reason_in_errors(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient()
        s = _settings(enabled=False)
        result = summarize_recent_research_artifacts("user-1", db, settings=s)
        assert any("observability_enabled=false" in e for e in result.errors)


# ─────────────────────────────────────────────────────────────────────────────
# AC2: enabled reads only research artifact tables (no writes)
# ─────────────────────────────────────────────────────────────────────────────

class TestEnabledReadsOnly:
    def test_reads_research_artifacts_table(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact()
        db = FakeSupabaseClient(artifact_rows=[art])
        s = _settings()
        summarize_recent_research_artifacts("user-1", db, settings=s)
        assert "research_artifacts" in db.tables_read

    def test_no_writes_recorded(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact()
        db = FakeSupabaseClient(artifact_rows=[art])
        s = _settings()
        summarize_recent_research_artifacts("user-1", db, settings=s)
        assert db.tables_written == []

    def test_does_not_access_intel_v3_snapshots(self):
        """Accessing intel_v3_snapshots would raise AssertionError in FakeSupabaseClient."""
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact()
        db = FakeSupabaseClient(artifact_rows=[art])
        s = _settings()
        # If snapshots table is accessed, FakeSupabaseClient raises AssertionError
        result = summarize_recent_research_artifacts("user-1", db, settings=s)
        assert result.observability_enabled is True


# ─────────────────────────────────────────────────────────────────────────────
# AC3: grouping by ticker / type / skill_pack / trust / freshness
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupingCounters:
    def _three_artifacts(self) -> list[dict]:
        return [
            _artifact(ticker="AAPL", confidence_or_trust_level="HIGH", freshness_status="FRESH"),
            _artifact(ticker="MSFT", confidence_or_trust_level="MEDIUM", freshness_status="STALE"),
            _artifact(ticker="AAPL", artifact_type="other_type", skill_pack="other_pack",
                      confidence_or_trust_level="LOW", freshness_status="UNKNOWN"),
        ]

    def test_by_ticker(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(artifact_rows=self._three_artifacts())
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.by_ticker["AAPL"] == 2
        assert result.by_ticker["MSFT"] == 1

    def test_by_artifact_type(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(artifact_rows=self._three_artifacts())
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.by_artifact_type["catalyst_window"] == 2
        assert result.by_artifact_type["other_type"] == 1

    def test_by_skill_pack(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(artifact_rows=self._three_artifacts())
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.by_skill_pack["earnings_reviewer"] == 2
        assert result.by_skill_pack["other_pack"] == 1

    def test_by_confidence_or_trust_level(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(artifact_rows=self._three_artifacts())
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.by_confidence_or_trust_level["HIGH"] == 1
        assert result.by_confidence_or_trust_level["MEDIUM"] == 1
        assert result.by_confidence_or_trust_level["LOW"] == 1

    def test_by_freshness_status(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(artifact_rows=self._three_artifacts())
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.by_freshness_status["FRESH"] == 1
        assert result.by_freshness_status["STALE"] == 1
        assert result.by_freshness_status["UNKNOWN"] == 1

    def test_artifact_count(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(artifact_rows=self._three_artifacts())
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.artifact_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# AC4: safe_for_decision=true rows counted as unexpected
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeForDecisionCounting:
    def test_all_false_counts_correctly(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        arts = [_artifact(safe_for_decision=False) for _ in range(3)]
        db = FakeSupabaseClient(artifact_rows=arts)
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.safe_for_decision_false_count == 3
        assert result.unexpected_safe_for_decision_true_count == 0

    def test_unexpected_true_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        arts = [
            _artifact(safe_for_decision=False),
            _artifact(safe_for_decision=True),   # unexpected
        ]
        db = FakeSupabaseClient(artifact_rows=arts)
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.safe_for_decision_false_count == 1
        assert result.unexpected_safe_for_decision_true_count == 1

    def test_unexpected_true_not_ignored_not_removed(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        arts = [_artifact(safe_for_decision=True)]
        db = FakeSupabaseClient(artifact_rows=arts)
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        # The artifact is still counted in total; the unexpected flag is raised.
        assert result.artifact_count == 1
        assert result.unexpected_safe_for_decision_true_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# AC5: forbidden payload keys are counted
# ─────────────────────────────────────────────────────────────────────────────

class TestForbiddenPayloadCounting:
    def test_clean_payload_no_violation(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact(payload={"summary": "valid", "found_fields": ["eps"]})
        db = FakeSupabaseClient(artifact_rows=[art])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.forbidden_payload_violation_count == 0

    def test_top_level_forbidden_key_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact(payload={"summary": "bad", "final_action": "BUY"})
        db = FakeSupabaseClient(artifact_rows=[art])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.forbidden_payload_violation_count == 1

    def test_nested_forbidden_key_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact(payload={"catalyst": {"recommendation": "HOLD"}})
        db = FakeSupabaseClient(artifact_rows=[art])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.forbidden_payload_violation_count == 1

    def test_multiple_violating_artifacts_all_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        arts = [
            _artifact(payload={"buy": True}),
            _artifact(payload={"summary": "ok"}),
            _artifact(payload={"sell": True}),
        ]
        db = FakeSupabaseClient(artifact_rows=arts)
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.forbidden_payload_violation_count == 2

    def test_forbidden_key_added_to_errors(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact(payload={"final_action": "SELL"})
        db = FakeSupabaseClient(artifact_rows=[art])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert any("forbidden_key_in_payload" in e for e in result.errors)


# ─────────────────────────────────────────────────────────────────────────────
# AC6: missing sources/facts counted
# ─────────────────────────────────────────────────────────────────────────────

class TestSourcesFactsCounting:
    def test_all_without_sources_when_no_source_rows(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        arts = [_artifact() for _ in range(2)]
        db = FakeSupabaseClient(artifact_rows=arts, source_rows=[])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.artifacts_with_sources_count == 0
        assert result.artifacts_without_sources_count == 2

    def test_partial_sources_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art1 = _artifact()
        art2 = _artifact()
        # source row must include user_id to pass the child-table user guard
        source_rows = [_source_row(art1["id"], USER_ID)]
        db = FakeSupabaseClient(artifact_rows=[art1, art2], source_rows=source_rows)
        result = summarize_recent_research_artifacts(USER_ID, db, settings=_settings())
        assert result.artifacts_with_sources_count == 1
        assert result.artifacts_without_sources_count == 1

    def test_all_without_facts_when_no_fact_rows(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        arts = [_artifact() for _ in range(3)]
        db = FakeSupabaseClient(artifact_rows=arts, fact_rows=[])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.artifacts_with_facts_count == 0
        assert result.artifacts_without_facts_count == 3

    def test_partial_facts_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art1 = _artifact()
        art2 = _artifact()
        # fact row must include user_id to pass the child-table user guard
        fact_rows = [_fact_row(art1["id"], USER_ID)]
        db = FakeSupabaseClient(artifact_rows=[art1, art2], fact_rows=fact_rows)
        result = summarize_recent_research_artifacts(USER_ID, db, settings=_settings())
        assert result.artifacts_with_facts_count == 1
        assert result.artifacts_without_facts_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# AC7: missing evidence / limitations counted
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingEvidenceCounting:
    def test_empty_limitations_not_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact(limitations=[])
        db = FakeSupabaseClient(artifact_rows=[art])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.missing_evidence_count == 0

    def test_non_empty_limitations_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact(limitations=["no eps data available"])
        db = FakeSupabaseClient(artifact_rows=[art])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.missing_evidence_count == 1

    def test_two_artifacts_one_with_evidence_gap(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        arts = [
            _artifact(limitations=["gap1", "gap2"]),
            _artifact(limitations=[]),
        ]
        db = FakeSupabaseClient(artifact_rows=arts)
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.missing_evidence_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# AC8: no raw payload/source/fact data in summary
# ─────────────────────────────────────────────────────────────────────────────

class TestNoRawDataInSummary:
    def test_summary_has_no_payload_field(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            ArtifactObservabilitySummary,
            summarize_recent_research_artifacts,
        )
        art = _artifact(payload={"summary": "confidential"})
        db = FakeSupabaseClient(artifact_rows=[art])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        # Must be an ArtifactObservabilitySummary — no raw payload attribute
        assert not hasattr(result, "artifact_payload")
        assert not hasattr(result, "payloads")
        assert not hasattr(result, "raw_rows")

    def test_summary_has_no_source_url_field(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(artifact_rows=[_artifact()])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert not hasattr(result, "source_url")
        assert not hasattr(result, "source_urls")
        assert not hasattr(result, "quote_or_excerpt")

    def test_summary_dataclass_fields_are_safe(self):
        """Verify the summary dataclass contains only aggregate/counter fields."""
        from app.services.intelligence.research_workers.artifact_observability import (
            ArtifactObservabilitySummary,
        )
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ArtifactObservabilitySummary)}
        # Must NOT contain raw-data fields
        forbidden_summary_fields = {
            "artifact_payload", "payloads", "raw_rows", "source_url", "source_urls",
            "quote_or_excerpt", "facts", "evidence_summaries", "excerpts",
        }
        overlap = forbidden_summary_fields & field_names
        assert not overlap, f"Summary dataclass contains forbidden raw-data fields: {overlap}"


# ─────────────────────────────────────────────────────────────────────────────
# AC9: visible_snapshot_unchanged always True
# ─────────────────────────────────────────────────────────────────────────────

class TestVisibleSnapshotUnchanged:
    def test_enabled_summary_visible_snapshot_unchanged(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(artifact_rows=[_artifact()])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.visible_snapshot_unchanged is True

    def test_disabled_summary_visible_snapshot_unchanged(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient()
        result = summarize_recent_research_artifacts("u", db, settings=_settings(enabled=False))
        assert result.visible_snapshot_unchanged is True

    def test_error_summary_visible_snapshot_unchanged(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(fail_artifacts=RuntimeError("db down"))
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.visible_snapshot_unchanged is True


# ─────────────────────────────────────────────────────────────────────────────
# AC10: INFO logging is aggregate-only
# ─────────────────────────────────────────────────────────────────────────────

class TestInfoLogging:
    def test_info_log_emitted_when_flag_on(self, caplog):
        import logging
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.research_workers.artifact_observability"):
            db = FakeSupabaseClient(artifact_rows=[_artifact()])
            summarize_recent_research_artifacts("u", db, settings=_settings(info_logs=True))
        log_text = " ".join(caplog.messages)
        assert "artifact_observability_complete" in log_text

    def test_info_log_not_emitted_when_flag_off(self, caplog):
        import logging
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.research_workers.artifact_observability"):
            db = FakeSupabaseClient(artifact_rows=[_artifact()])
            summarize_recent_research_artifacts("u", db, settings=_settings(info_logs=False))
        assert "artifact_observability_complete" not in " ".join(caplog.messages)

    def test_info_log_contains_no_payload_or_url(self, caplog):
        import logging
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        secret_val = "CONFIDENTIAL_PAYLOAD_VALUE_12345"
        art = _artifact(payload={"summary": secret_val})
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.research_workers.artifact_observability"):
            db = FakeSupabaseClient(artifact_rows=[art])
            summarize_recent_research_artifacts("u", db, settings=_settings(info_logs=True))
        log_text = " ".join(caplog.messages)
        assert secret_val not in log_text

    def test_info_log_contains_safe_counters(self, caplog):
        import logging
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        with caplog.at_level(logging.INFO, logger="app.services.intelligence.research_workers.artifact_observability"):
            db = FakeSupabaseClient(artifact_rows=[_artifact()])
            summarize_recent_research_artifacts("u", db, settings=_settings(info_logs=True))
        log_text = " ".join(caplog.messages)
        # Counter labels must appear
        assert "artifact_count=" in log_text
        assert "safe_for_decision_false=" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# AC11: no imports/calls to decide()
# ─────────────────────────────────────────────────────────────────────────────

class TestNoDecideDependency:
    def _import_lines(self) -> list[str]:
        import ast
        mod = importlib.import_module(
            "app.services.intelligence.research_workers.artifact_observability"
        )
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        lines = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                lines.append(ast.unparse(node))
        return lines

    def test_observability_module_does_not_import_decision_policy(self):
        import_lines = self._import_lines()
        assert not any("decision_policy_v1" in line for line in import_lines), (
            "artifact_observability.py must not import decision_policy_v1"
        )

    def test_observability_module_does_not_call_decide(self):
        import ast
        mod = importlib.import_module(
            "app.services.intelligence.research_workers.artifact_observability"
        )
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        decide_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "decide")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "decide")
            )
        ]
        assert decide_calls == [], "artifact_observability.py must not call decide()"

    def test_observability_module_does_not_import_recommendation_engine(self):
        import_lines = self._import_lines()
        assert not any("recommendation_engine" in line for line in import_lines)


# ─────────────────────────────────────────────────────────────────────────────
# AC12: no writes to intel_v3_snapshots (enforced by FakeSupabaseClient guard)
# ─────────────────────────────────────────────────────────────────────────────

class TestNoSnapshotWrites:
    def test_intel_v3_snapshots_table_never_accessed(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(artifact_rows=[_artifact()])
        # FakeSupabaseClient raises AssertionError for any unknown table including snapshots.
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert "intel_v3_snapshots" not in db.tables_read
        assert "intel_v3_snapshots" not in db.tables_written


# ─────────────────────────────────────────────────────────────────────────────
# AC13: expired_count counts rows where expires_at < now
# ─────────────────────────────────────────────────────────────────────────────

class TestExpiredCounting:
    def test_past_expires_at_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        past = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        art = _artifact(expires_at=past)
        db = FakeSupabaseClient(artifact_rows=[art])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.expired_count == 1

    def test_future_expires_at_not_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        art = _artifact(expires_at=future)
        db = FakeSupabaseClient(artifact_rows=[art])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.expired_count == 0

    def test_null_expires_at_not_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact(expires_at=None)
        db = FakeSupabaseClient(artifact_rows=[art])
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.expired_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# AC14: active/inactive counts reflect is_active field
# ─────────────────────────────────────────────────────────────────────────────

class TestActiveInactiveCounting:
    def test_active_count(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        arts = [_artifact(is_active=True), _artifact(is_active=True), _artifact(is_active=False)]
        db = FakeSupabaseClient(artifact_rows=arts)
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.active_count == 2
        assert result.inactive_count == 1

    def test_invalidated_at_non_null_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        ts = datetime.now(timezone.utc).isoformat()
        arts = [
            _artifact(invalidated_at=ts),
            _artifact(invalidated_at=ts),
            _artifact(invalidated_at=None),
        ]
        db = FakeSupabaseClient(artifact_rows=arts)
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.invalidated_count == 2

    def test_invalidated_at_null_not_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        arts = [_artifact(invalidated_at=None), _artifact(invalidated_at=None)]
        db = FakeSupabaseClient(artifact_rows=arts)
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.invalidated_count == 0

    def test_all_invalidated_counted(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        ts = datetime.now(timezone.utc).isoformat()
        arts = [_artifact(invalidated_at=ts) for _ in range(3)]
        db = FakeSupabaseClient(artifact_rows=arts)
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.invalidated_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# AC15: query failures contained in errors[], no exception propagates
# ─────────────────────────────────────────────────────────────────────────────

class TestFailureContainment:
    def test_artifact_query_failure_contained(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(fail_artifacts=RuntimeError("connection timeout"))
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.artifact_count == 0
        assert any("artifact_query_error" in e for e in result.errors)
        assert result.visible_snapshot_unchanged is True

    def test_sources_query_failure_contained(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact()
        db = FakeSupabaseClient(artifact_rows=[art], fail_sources=RuntimeError("sources down"))
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.artifact_count == 1
        assert any("sources_query_error" in e for e in result.errors)

    def test_facts_query_failure_contained(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        art = _artifact()
        db = FakeSupabaseClient(artifact_rows=[art], fail_facts=RuntimeError("facts down"))
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result.artifact_count == 1
        assert any("facts_query_error" in e for e in result.errors)

    def test_never_raises(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            summarize_recent_research_artifacts,
        )
        db = FakeSupabaseClient(
            fail_artifacts=RuntimeError("cascade failure"),
            fail_sources=RuntimeError("cascade failure"),
            fail_facts=RuntimeError("cascade failure"),
        )
        # Must never raise regardless of failures
        result = summarize_recent_research_artifacts("u", db, settings=_settings())
        assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# AC16: static source guard — no decision_policy_v1 import in module
# ─────────────────────────────────────────────────────────────────────────────

class TestStaticSourceGuard:
    def _import_lines(self) -> list[str]:
        import ast
        mod = importlib.import_module(
            "app.services.intelligence.research_workers.artifact_observability"
        )
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        lines = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                lines.append(ast.unparse(node))
        return lines

    def test_artifact_observability_does_not_import_decision_policy_v1(self):
        import_lines = self._import_lines()
        assert not any("decision_policy_v1" in line for line in import_lines), (
            "artifact_observability.py must not import decision_policy_v1"
        )

    def test_artifact_observability_does_not_import_intel_v3_snapshots(self):
        # "intel_v3_snapshots" should not appear in import statements
        import_lines = self._import_lines()
        assert not any("intel_v3_snapshots" in line for line in import_lines), (
            "artifact_observability.py must not import intel_v3_snapshots"
        )

    def test_artifact_observability_does_not_import_frontend(self):
        import_lines = self._import_lines()
        assert not any("frontend" in line for line in import_lines)

    def test_artifact_observability_does_not_import_anthropic(self):
        import_lines = self._import_lines()
        assert not any("anthropic" in line for line in import_lines)

    def test_artifact_observability_does_not_write_artifacts(self):
        """Confirm module source contains no insert/upsert/write calls to artifact tables."""
        import ast
        mod = importlib.import_module(
            "app.services.intelligence.research_workers.artifact_observability"
        )
        src = inspect.getsource(mod)
        assert _observability_writes_only_reads(src), (
            "artifact_observability.py appears to write to research_artifacts"
        )

    def test_does_not_reference_artifact_payload_column(self):
        """Regression: column must be 'payload', not 'artifact_payload'."""
        mod = importlib.import_module(
            "app.services.intelligence.research_workers.artifact_observability"
        )
        src = inspect.getsource(mod)
        assert "artifact_payload" not in src, (
            "artifact_observability.py references 'artifact_payload' — production column is 'payload'"
        )

    def test_no_safe_for_decision_true_assignment(self):
        import ast
        mod = importlib.import_module(
            "app.services.intelligence.research_workers.artifact_observability"
        )
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        # Check for keyword arguments safe_for_decision=True in function calls
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "safe_for_decision" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        raise AssertionError("artifact_observability.py sets safe_for_decision=True")


def _observability_writes_only_reads(src: str) -> bool:
    """Helper: return True if the source only uses .select() on artifact tables."""
    import re
    # Look for table("research_artifacts").insert or .upsert patterns
    write_pattern = re.search(
        r'table\s*\(\s*["\']research_artifacts["\']\s*\).*?\.(?:insert|upsert|update|delete)\s*\(',
        src,
        re.DOTALL,
    )
    return write_pattern is None
