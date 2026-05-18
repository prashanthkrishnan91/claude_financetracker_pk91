"""Stage 5E0 — Research Worker Contract Reconciliation tests.

Proves all acceptance criteria for the bridge PR that routes existing research
worker write paths through the enriched Stage 5A–5D artifact quality pipeline.

Acceptance criteria verified:
  1. run_earnings_reviewer_dark writes through ResearchArtifactServiceV1 (not
     raw ArtifactStoreWriter). The artifact payload now includes all three
     Stage 5 quality assessments.
  2. Written artifacts include source_credibility_assessment (Stage 5B).
  3. Written artifacts include contradiction_assessment (Stage 5C).
  4. Written artifacts include evidence_completeness_assessment (Stage 5D).
  5. validation_harness.run_validation() still reports
     safe_for_decision_false_count == written_count.
  6. Diagnostics validation response uses compact summary only (tested via
     ValidationSummary shape — no payload/facts/raw rows exposed).
  7. No writes to intel_v3_snapshots or recommendations tables.
  8. No import/call to decide() from the runner path (static boundary check).
  9. safe_for_decision remains False in every written artifact row.
 10. Idempotent rerun does not duplicate artifacts (returns existing id, no
     second INSERT).
 11. runner.py no longer imports ArtifactStoreWriter directly (it uses the
     enriched service path).

No production Supabase access — all DB interactions use FakeSupabaseClient.
"""
from __future__ import annotations

import importlib
import inspect
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from app.config import Settings
from app.services.intelligence.research_workers import earnings_reviewer
from app.services.intelligence.research_workers.contracts import WorkerInput
from app.services.intelligence.research_workers.runner import run_earnings_reviewer_dark
from app.services.intelligence.research_workers.validation_harness import (
    ValidationSummary,
    run_validation,
)


# ── Fake Supabase client ──────────────────────────────────────────────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)


class FakeTableQuery:
    """Chainable fake query supporting all operations used by ResearchArtifactServiceV1."""

    def __init__(self, state: _TableState, return_id: Optional[str] = None) -> None:
        self._state = state
        self._return_id = return_id or str(uuid.uuid4())
        self._row: Optional[dict] = None
        self._on_conflict: Optional[str] = None
        self._is_update: bool = False
        self._select_cols: Optional[str] = None
        self._filters: dict = {}
        self._limit_val: Optional[int] = None

    def insert(self, row: dict) -> "FakeTableQuery":
        self._row = row
        return self

    def update(self, row: dict) -> "FakeTableQuery":
        self._row = row
        self._is_update = True
        return self

    def upsert(self, row: dict, *, on_conflict: str = "", ignore_duplicates: bool = False) -> "FakeTableQuery":
        self._row = row
        self._on_conflict = on_conflict
        return self

    def select(self, cols: str = "*") -> "FakeTableQuery":
        self._select_cols = cols
        return self

    def eq(self, col: str, val: Any) -> "FakeTableQuery":
        self._filters[col] = val
        return self

    def neq(self, col: str, val: Any) -> "FakeTableQuery":
        return self

    def is_(self, col: str, val: Any) -> "FakeTableQuery":
        return self

    def order(self, *args: Any, **kwargs: Any) -> "FakeTableQuery":
        return self

    def limit(self, n: int) -> "FakeTableQuery":
        self._limit_val = n
        return self

    def execute(self) -> Any:
        if self._row is not None and self._is_update:
            class _U:
                data = []
            return _U()
        if self._row is not None:
            row_with_id = {"id": self._return_id, **self._row}
            if self._on_conflict is not None:
                self._state.upserts.append(self._row)
            else:
                self._state.inserts.append(self._row)
            class _R:
                data = [row_with_id]
            return _R()
        class _E:
            data = []
        return _E()


class FakeSupabaseClient:
    """Records table interactions without touching a real database."""

    def __init__(self) -> None:
        self.tables: dict[str, _TableState] = {
            "research_artifacts": _TableState(),
            "research_artifact_sources": _TableState(),
            "research_artifact_facts": _TableState(),
            "worker_audit_events": _TableState(),
            "intel_v3_snapshots": _TableState(),
        }

    def table(self, name: str) -> FakeTableQuery:
        state = self.tables.setdefault(name, _TableState())
        return FakeTableQuery(state)

    def artifact_inserts(self) -> list[dict]:
        return self.tables["research_artifacts"].inserts

    def audit_inserts(self) -> list[dict]:
        return self.tables["worker_audit_events"].inserts

    def snapshot_writes(self) -> list[dict]:
        return (
            self.tables["intel_v3_snapshots"].inserts
            + self.tables["intel_v3_snapshots"].upserts
        )

    def get_written_tables(self) -> list[str]:
        return sorted(
            name for name, state in self.tables.items()
            if state.inserts or state.upserts
        )


# ── Settings helpers ──────────────────────────────────────────────────────────

def _settings_all_on() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="fake",
        supabase_service_role_key="fake",
        supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_research_worker_validation_enabled=True,
        intel_v3_research_worker_validation_info_logs_enabled=False,
    )


# ── Criterion 1: runner uses ResearchArtifactServiceV1, not raw ArtifactStoreWriter ─

class TestRunnerUsesEnrichedServicePath:

    def test_runner_does_not_import_artifact_store_writer_directly(self) -> None:
        """runner.py must not import ArtifactStoreWriter as a top-level dependency.

        The enriched write path belongs in ResearchArtifactServiceV1. The runner
        delegates to the service, which internally uses ArtifactStoreWriter — that
        is fine. What must NOT happen is runner.py constructing ArtifactStoreWriter
        directly (which would bypass Stage 5B–5D enrichment).
        """
        import ast
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "research_workers", "runner.py"
        )
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        # ArtifactStoreWriter must not appear as a direct import name in runner.py.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "ArtifactStoreWriter", (
                        "runner.py must not import ArtifactStoreWriter directly — "
                        "use ResearchArtifactServiceV1 to ensure Stage 5B–5D enrichment."
                    )

    def test_runner_imports_research_artifact_service(self) -> None:
        """runner.py must import ResearchArtifactServiceV1 as the write path."""
        import ast
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "research_workers", "runner.py"
        )
        with open(path) as f:
            source = f.read()
        assert "ResearchArtifactServiceV1" in source, (
            "runner.py must import and use ResearchArtifactServiceV1 for enriched writes."
        )


# ── Criteria 2–4: artifact payload includes all three Stage 5 assessments ─────

class TestEnrichedArtifactAssessments:

    def _run_and_get_payload(self, ticker: str = "AAPL") -> dict:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker=ticker,
            db_client=client,
            settings=_settings_all_on(),
        )
        rows = client.artifact_inserts()
        assert rows, f"Expected at least one artifact INSERT for {ticker}"
        return rows[0].get("payload", {})

    def test_artifact_payload_includes_source_credibility_assessment(self) -> None:
        """Stage 5B: source_credibility_assessment must be in the written payload."""
        payload = self._run_and_get_payload("AAPL")
        assert "source_credibility_assessment" in payload, (
            "Stage 5B: source_credibility_assessment missing from artifact payload. "
            "runner must use ResearchArtifactServiceV1 write path."
        )

    def test_artifact_payload_includes_contradiction_assessment(self) -> None:
        """Stage 5C: contradiction_assessment must be in the written payload."""
        payload = self._run_and_get_payload("MSFT")
        assert "contradiction_assessment" in payload, (
            "Stage 5C: contradiction_assessment missing from artifact payload. "
            "runner must use ResearchArtifactServiceV1 write path."
        )

    def test_artifact_payload_includes_evidence_completeness_assessment(self) -> None:
        """Stage 5D: evidence_completeness_assessment must be in the written payload."""
        payload = self._run_and_get_payload("NVDA")
        assert "evidence_completeness_assessment" in payload, (
            "Stage 5D: evidence_completeness_assessment missing from artifact payload. "
            "runner must use ResearchArtifactServiceV1 write path."
        )

    def test_all_three_assessments_present_in_single_write(self) -> None:
        """All three Stage 5B–5D assessments must be present in every artifact."""
        payload = self._run_and_get_payload("TSLA")
        for key in (
            "source_credibility_assessment",
            "contradiction_assessment",
            "evidence_completeness_assessment",
        ):
            assert key in payload, (
                f"Assessment key '{key}' missing from artifact payload."
            )

    def test_source_credibility_assessment_has_expected_fields(self) -> None:
        """Stage 5B assessment shape: must include is_insufficient and strongest_authority_level."""
        payload = self._run_and_get_payload("GOOG")
        credibility = payload.get("source_credibility_assessment", {})
        assert "is_insufficient" in credibility, (
            "source_credibility_assessment must include is_insufficient field."
        )
        assert "strongest_authority_level" in credibility, (
            "source_credibility_assessment must include strongest_authority_level field."
        )

    def test_contradiction_assessment_has_expected_fields(self) -> None:
        """Stage 5C assessment shape: must include has_contradictions and is_evaluable."""
        payload = self._run_and_get_payload("AMZN")
        contradiction = payload.get("contradiction_assessment", {})
        assert "has_contradictions" in contradiction, (
            "contradiction_assessment must include has_contradictions field."
        )
        assert "is_evaluable" in contradiction, (
            "contradiction_assessment must include is_evaluable field."
        )

    def test_evidence_completeness_assessment_has_expected_fields(self) -> None:
        """Stage 5D assessment shape: must include completeness_band and is_evaluable."""
        payload = self._run_and_get_payload("META")
        completeness = payload.get("evidence_completeness_assessment", {})
        assert "completeness_band" in completeness, (
            "evidence_completeness_assessment must include completeness_band field."
        )
        assert "is_evaluable" in completeness, (
            "evidence_completeness_assessment must include is_evaluable field."
        )

    def test_dark_run_no_source_credibility_is_insufficient(self) -> None:
        """Phase 3 dark-run produces no sources → credibility must be is_insufficient=True."""
        payload = self._run_and_get_payload("AAPL")
        credibility = payload.get("source_credibility_assessment", {})
        assert credibility.get("is_insufficient") is True, (
            "Phase 3 dark-run artifact (no sources) must have is_insufficient=True in "
            "source_credibility_assessment."
        )

    def test_dark_run_completeness_band_is_not_complete(self) -> None:
        """Phase 3 dark-run (no sources) must not receive COMPLETE completeness band."""
        payload = self._run_and_get_payload("AAPL")
        completeness = payload.get("evidence_completeness_assessment", {})
        band = completeness.get("completeness_band", "")
        assert band != "COMPLETE", (
            f"Phase 3 dark-run (no external sources) must not be COMPLETE, got {band!r}."
        )


# ── Criterion 5: validation harness safe_for_decision_false_count still correct ─

class TestValidationHarnessCountsStillCorrect:

    def test_safe_for_decision_false_count_equals_written_count(self) -> None:
        """safe_for_decision_false_count must equal written_count after enriched write."""
        client = FakeSupabaseClient()
        summary = run_validation(
            tickers=["AAPL", "MSFT"],
            user_id=str(uuid.uuid4()),
            db_client=client,
            settings=_settings_all_on(),
            max_tickers=5,
        )
        assert summary.validation_enabled is True
        assert summary.written_count > 0, "Expected at least one artifact written"
        assert summary.safe_for_decision_false_count == summary.written_count, (
            f"safe_for_decision_false_count ({summary.safe_for_decision_false_count}) "
            f"must equal written_count ({summary.written_count})."
        )

    def test_unexpected_safe_for_decision_true_count_is_zero(self) -> None:
        """No artifact must set safe_for_decision=True."""
        client = FakeSupabaseClient()
        summary = run_validation(
            tickers=["NVDA"],
            user_id=str(uuid.uuid4()),
            db_client=client,
            settings=_settings_all_on(),
        )
        assert summary.unexpected_safe_for_decision_true_count == 0


# ── Criterion 6: diagnostics validation summary is compact ───────────────────

class TestDiagnosticsValidationSummaryCompact:

    def test_validation_summary_has_no_payload_field(self) -> None:
        """ValidationSummary must not expose artifact payloads."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ValidationSummary)}
        assert "payload" not in field_names, (
            "ValidationSummary must never include an artifact payload field."
        )

    def test_validation_summary_has_no_facts_field(self) -> None:
        """ValidationSummary must not expose raw fact rows."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ValidationSummary)}
        assert "facts" not in field_names

    def test_validation_summary_has_no_quotes_field(self) -> None:
        """ValidationSummary must not expose source quotes or excerpts."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ValidationSummary)}
        for disallowed in ("quotes", "quote_or_excerpt", "sources", "source_urls"):
            assert disallowed not in field_names, (
                f"ValidationSummary must not expose {disallowed!r}."
            )


# ── Criterion 7: no writes to intel_v3_snapshots or recommendations ──────────

class TestNoForbiddenTableWrites:

    def test_runner_does_not_write_to_intel_v3_snapshots(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        assert client.snapshot_writes() == [], (
            "run_earnings_reviewer_dark must NEVER write to intel_v3_snapshots."
        )

    def test_validation_harness_does_not_write_to_intel_v3_snapshots(self) -> None:
        client = FakeSupabaseClient()
        run_validation(
            tickers=["AAPL"],
            user_id=str(uuid.uuid4()),
            db_client=client,
            settings=_settings_all_on(),
        )
        assert client.snapshot_writes() == [], (
            "run_validation must NEVER write to intel_v3_snapshots."
        )

    def test_written_tables_are_only_artifact_tables(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="MSFT",
            db_client=client,
            settings=_settings_all_on(),
        )
        allowed = {
            "research_artifacts",
            "research_artifact_sources",
            "research_artifact_facts",
            "worker_audit_events",
        }
        for name, state in client.tables.items():
            if name not in allowed:
                assert state.inserts == [] and state.upserts == [], (
                    f"Unexpected writes to table '{name}'."
                )


# ── Criterion 8: no decide() import in runner path ───────────────────────────

class TestNoDecideImport:

    def test_runner_does_not_import_decide(self) -> None:
        """runner.py must not import decide() from decision_policy_v1."""
        import ast
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "research_workers", "runner.py"
        )
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "decision_policy_v1" not in module, (
                    f"runner.py must never import from decision_policy_v1, found: {module}"
                )
                for alias in node.names:
                    assert alias.name != "decide", (
                        "runner.py must never import decide() — "
                        "visible Intel v3 decision authority stays in the deterministic policy."
                    )

    def test_research_artifact_service_does_not_import_decide(self) -> None:
        """ResearchArtifactServiceV1 must not import decide() from decision_policy_v1."""
        import ast
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "v3",
            "research_artifact_service_v1.py"
        )
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "decision_policy_v1" not in module
                for alias in node.names:
                    assert alias.name != "decide"


# ── Criterion 9: safe_for_decision always False ───────────────────────────────

class TestSafeForDecisionAlwaysFalse:

    def test_every_artifact_row_has_safe_for_decision_false(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        rows = client.artifact_inserts()
        assert rows, "Expected at least one artifact INSERT"
        for row in rows:
            assert row.get("safe_for_decision") is False, (
                f"safe_for_decision must be False in every written row, got: "
                f"{row.get('safe_for_decision')!r}"
            )


# ── Criterion 10: idempotent rerun does not duplicate artifacts ───────────────

class TestIdempotentRerun:

    def test_second_run_same_ticker_returns_same_artifact_id(self) -> None:
        """ResearchArtifactServiceV1 idempotency: identical run → same artifact_id.

        The Phase 3 dark-run produces the same replay_idempotency_key on every
        call for the same ticker (deterministic inputs, no external source).
        The service's idempotency check returns the existing id without a second
        INSERT.
        """
        user_id = str(uuid.uuid4())

        # First run — insert is expected.
        client_1 = FakeSupabaseClient()
        id_1 = run_earnings_reviewer_dark(
            user_id=user_id,
            ticker="AAPL",
            db_client=client_1,
            settings=_settings_all_on(),
        )
        assert id_1 is not None, "First run must return an artifact_id"

        # Use the existing_id returned by first run as the fake response for second run.
        # We build a second client where the SELECT returns the existing row so the
        # idempotency check fires.
        class _IdempotentFakeTableQuery(FakeTableQuery):
            """SELECT returns the existing artifact_id to trigger idempotency skip."""

            def __init__(self, state: _TableState, existing_id: str) -> None:
                super().__init__(state)
                self._existing_id = existing_id
                self._hit_count = 0

            def execute(self) -> Any:
                if self._select_cols is not None and self._row is None:
                    # First SELECT from ResearchArtifactServiceV1 returns existing row.
                    if self._hit_count == 0:
                        self._hit_count += 1
                        class _Found:
                            def __init__(self, eid: str) -> None:
                                self.data = [{"id": eid}]
                        return _Found(self._existing_id)
                    return super().execute()
                return super().execute()

        class _IdempotentFakeClient(FakeSupabaseClient):
            def __init__(self, existing_id: str) -> None:
                super().__init__()
                self._existing_id = existing_id

            def table(self, name: str) -> Any:
                state = self.tables.setdefault(name, _TableState())
                if name == "research_artifacts":
                    return _IdempotentFakeTableQuery(state, self._existing_id)
                return FakeTableQuery(state)

        client_2 = _IdempotentFakeClient(existing_id=id_1)
        id_2 = run_earnings_reviewer_dark(
            user_id=user_id,
            ticker="AAPL",
            db_client=client_2,
            settings=_settings_all_on(),
        )
        assert id_2 == id_1, (
            f"Idempotent rerun must return the same artifact_id. "
            f"First: {id_1!r}, Second: {id_2!r}"
        )
        # No second INSERT should have been issued.
        assert client_2.artifact_inserts() == [], (
            "Second run with same idempotency key must not INSERT a duplicate artifact."
        )
