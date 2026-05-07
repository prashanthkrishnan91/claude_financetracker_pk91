"""Phase 3 research worker tests.

Covers all 10 acceptance criteria from the Phase 3 task spec:

  1. Worker is disabled by default (both flags False → returns None).
  2. Global research workers flag off → no artifact writes.
  3. Earnings Reviewer flag off → no artifact writes.
  4. When enabled, writes only to the four research artifact tables.
  5. safe_for_decision is always False in artifact writes.
  6. Payload does not contain forbidden decision-authority keys.
  7. Worker does not import or call decision_policy_v1.decide().
  8. Visible Intel v3 snapshot certification tests still pass.
  9. No legacy recommendation_engine aggregation is reintroduced.
 10. Failure path records/returns safe audit info without raising.

DB writer tests use a FakeSupabaseClient — no production Supabase access required.
"""
from __future__ import annotations

import inspect
import importlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import patch

import pytest

from app.config import Settings
from app.services.intelligence.research_workers.contracts import (
    FORBIDDEN_PAYLOAD_KEYS,
    AuditEventRecord,
    FactRecord,
    SourceRecord,
    WorkerInput,
    WorkerOutput,
    _has_forbidden_key,
    compute_replay_idempotency_key,
    validate_payload,
)
from app.services.intelligence.research_workers import earnings_reviewer
from app.services.intelligence.research_workers.artifact_store_writer import (
    ArtifactStoreWriter,
)
from app.services.intelligence.research_workers.runner import (
    run_earnings_reviewer_dark,
)


# ── Fake Supabase client ──────────────────────────────────────────────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)


class FakeTableQuery:
    """Chainable fake Supabase table query that records calls."""

    def __init__(self, state: _TableState, return_id: Optional[str] = None) -> None:
        self._state = state
        self._return_id = return_id or str(uuid.uuid4())
        self._row: Optional[dict] = None
        self._on_conflict: Optional[str] = None
        self._ignore_duplicates: bool = False
        self._filters: dict = {}
        self._limit_val: Optional[int] = None
        self._select_cols: Optional[str] = None

    def insert(self, row: dict) -> "FakeTableQuery":
        self._row = row
        return self

    def upsert(self, row: dict, *, on_conflict: str = "", ignore_duplicates: bool = False) -> "FakeTableQuery":
        self._row = row
        self._on_conflict = on_conflict
        self._ignore_duplicates = ignore_duplicates
        return self

    def select(self, cols: str = "*") -> "FakeTableQuery":
        self._select_cols = cols
        return self

    def eq(self, col: str, val: Any) -> "FakeTableQuery":
        self._filters[col] = val
        return self

    def order(self, *args, **kwargs) -> "FakeTableQuery":
        return self

    def limit(self, n: int) -> "FakeTableQuery":
        self._limit_val = n
        return self

    def execute(self) -> Any:
        if self._row is not None:
            row_with_id = {"id": self._return_id, **self._row}
            if self._on_conflict is not None:
                self._state.upserts.append(self._row)
            else:
                self._state.inserts.append(self._row)

            class _Result:
                data = [row_with_id]
            return _Result()

        # SELECT path (for idempotency fetch-existing logic).
        class _EmptyResult:
            data = []
        return _EmptyResult()


class FakeSupabaseClient:
    """Records all table calls without touching a real database."""

    def __init__(self) -> None:
        self.tables: dict[str, _TableState] = {
            "research_artifacts": _TableState(),
            "research_artifact_sources": _TableState(),
            "research_artifact_facts": _TableState(),
            "worker_audit_events": _TableState(),
            "intel_v3_snapshots": _TableState(),  # should NEVER be written
        }

    def table(self, name: str) -> FakeTableQuery:
        state = self.tables.setdefault(name, _TableState())
        return FakeTableQuery(state)

    def artifact_inserts(self) -> list[dict]:
        return self.tables["research_artifacts"].upserts

    def source_inserts(self) -> list[dict]:
        return self.tables["research_artifact_sources"].inserts

    def fact_inserts(self) -> list[dict]:
        return self.tables["research_artifact_facts"].inserts

    def audit_inserts(self) -> list[dict]:
        return self.tables["worker_audit_events"].inserts

    def snapshot_writes(self) -> list[dict]:
        return (
            self.tables["intel_v3_snapshots"].inserts
            + self.tables["intel_v3_snapshots"].upserts
        )


# ── Settings helpers ──────────────────────────────────────────────────────────

def _settings_all_off() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="fake",
        supabase_service_role_key="fake",
        supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=False,
        intel_v3_earnings_reviewer_enabled=False,
    )


def _settings_all_on() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="fake",
        supabase_service_role_key="fake",
        supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
    )


def _settings_global_off() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="fake",
        supabase_service_role_key="fake",
        supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=False,
        intel_v3_earnings_reviewer_enabled=True,
    )


def _settings_worker_off() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="fake",
        supabase_service_role_key="fake",
        supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=False,
    )


# ── Criterion 1: disabled by default ─────────────────────────────────────────

class TestWorkerDisabledByDefault:

    def test_default_settings_both_flags_false(self) -> None:
        """Settings model defaults both research worker flags to False."""
        from app.config import Settings
        s = Settings(
            supabase_url="http://fake",
            supabase_anon_key="fake",
            supabase_service_role_key="fake",
            supabase_jwt_secret="fake",
            encryption_key="fake",
        )
        assert s.intel_v3_research_workers_enabled is False
        assert s.intel_v3_earnings_reviewer_enabled is False

    def test_runner_returns_none_when_both_flags_off(self) -> None:
        client = FakeSupabaseClient()
        result = run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_off(),
        )
        assert result is None


# ── Criterion 2: global flag off → no DB writes ───────────────────────────────

class TestGlobalFlagOff:

    def test_no_writes_when_global_flag_off(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="MSFT",
            db_client=client,
            settings=_settings_global_off(),
        )
        assert client.artifact_inserts() == []
        assert client.audit_inserts() == []

    def test_returns_none_when_global_flag_off(self) -> None:
        client = FakeSupabaseClient()
        result = run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="MSFT",
            db_client=client,
            settings=_settings_global_off(),
        )
        assert result is None


# ── Criterion 3: earnings reviewer flag off → no DB writes ───────────────────

class TestEarningsReviewerFlagOff:

    def test_no_writes_when_worker_flag_off(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="GOOG",
            db_client=client,
            settings=_settings_worker_off(),
        )
        assert client.artifact_inserts() == []
        assert client.audit_inserts() == []

    def test_returns_none_when_worker_flag_off(self) -> None:
        client = FakeSupabaseClient()
        result = run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="GOOG",
            db_client=client,
            settings=_settings_worker_off(),
        )
        assert result is None


# ── Criterion 4: when enabled, writes only to the four artifact tables ────────

class TestWritesOnlyToArtifactTables:

    def test_writes_to_research_artifacts(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        assert len(client.artifact_inserts()) == 1

    def test_writes_audit_events(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        assert len(client.audit_inserts()) >= 1

    def test_no_writes_to_intel_v3_snapshots(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        assert client.snapshot_writes() == [], (
            "Worker must NEVER write to intel_v3_snapshots"
        )

    def test_no_writes_to_unexpected_tables(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="NVDA",
            db_client=client,
            settings=_settings_all_on(),
        )
        allowed = {
            "research_artifacts",
            "research_artifact_sources",
            "research_artifact_facts",
            "worker_audit_events",
        }
        for table_name, state in client.tables.items():
            if table_name not in allowed:
                assert state.inserts == [] and state.upserts == [], (
                    f"Unexpected writes to table '{table_name}'"
                )

    def test_returns_artifact_id_string_when_enabled(self) -> None:
        client = FakeSupabaseClient()
        result = run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="TSLA",
            db_client=client,
            settings=_settings_all_on(),
        )
        assert result is not None
        # The fake client always returns a UUID string as id.
        assert isinstance(result, str)


# ── Criterion 5: safe_for_decision is always False ───────────────────────────

class TestSafeForDecisionAlwaysFalse:

    def test_artifact_row_has_safe_for_decision_false(self) -> None:
        client = FakeSupabaseClient()
        run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=client,
            settings=_settings_all_on(),
        )
        rows = client.artifact_inserts()
        assert rows, "Expected at least one artifact row"
        for row in rows:
            assert row.get("safe_for_decision") is False, (
                f"safe_for_decision must be False, got {row.get('safe_for_decision')!r}"
            )

    def test_worker_output_has_no_safe_for_decision_field(self) -> None:
        """WorkerOutput dataclass has no safe_for_decision attribute.

        Workers must not express this field at all — the writer hard-codes False.
        """
        worker_input = WorkerInput(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            worker_run_id=str(uuid.uuid4()),
        )
        output = earnings_reviewer.run(worker_input)
        assert not hasattr(output, "safe_for_decision"), (
            "WorkerOutput must not carry safe_for_decision — it is not a worker field"
        )


# ── Criterion 6: payload contains no forbidden keys ──────────────────────────

class TestNoForbiddenPayloadKeys:

    def _get_artifact_payload(self, ticker: str = "AAPL") -> dict:
        worker_input = WorkerInput(
            user_id=str(uuid.uuid4()),
            ticker=ticker,
            worker_run_id=str(uuid.uuid4()),
        )
        output = earnings_reviewer.run(worker_input)
        return output.artifact_payload

    def _get_fact_payloads(self, ticker: str = "AAPL") -> list[dict]:
        worker_input = WorkerInput(
            user_id=str(uuid.uuid4()),
            ticker=ticker,
            worker_run_id=str(uuid.uuid4()),
        )
        output = earnings_reviewer.run(worker_input)
        return [f.structured_payload for f in output.facts]

    def test_artifact_payload_has_no_forbidden_keys(self) -> None:
        payload = self._get_artifact_payload()
        found = _has_forbidden_key(payload)
        assert found is None, f"Forbidden key '{found}' in artifact payload"

    def test_fact_payloads_have_no_forbidden_keys(self) -> None:
        for fact_payload in self._get_fact_payloads():
            found = _has_forbidden_key(fact_payload)
            assert found is None, f"Forbidden key '{found}' in fact payload"

    @pytest.mark.parametrize("forbidden_key", sorted(FORBIDDEN_PAYLOAD_KEYS))
    def test_validate_payload_rejects_top_level_forbidden_key(self, forbidden_key: str) -> None:
        with pytest.raises(ValueError, match="Forbidden key"):
            validate_payload({forbidden_key: "anything"})

    @pytest.mark.parametrize("forbidden_key", ["FINAL_ACTION", "Buy", "HOLD", "Sell"])
    def test_validate_payload_rejects_case_insensitive_forbidden_key(self, forbidden_key: str) -> None:
        with pytest.raises(ValueError, match="Forbidden key"):
            validate_payload({forbidden_key: "anything"})

    def test_validate_payload_rejects_nested_forbidden_key(self) -> None:
        with pytest.raises(ValueError, match="Forbidden key"):
            validate_payload({"context": {"final_action": "BUY"}})

    def test_validate_payload_rejects_deeply_nested_forbidden_key(self) -> None:
        with pytest.raises(ValueError, match="Forbidden key"):
            validate_payload({"a": {"b": {"c": {"recommendation": {"FINAL_ACTION": "x"}}}}})

    def test_validate_payload_accepts_valid_payload(self) -> None:
        validate_payload({"review_status": "ok", "found_fields": ["eps"]})

    def test_fact_record_rejects_forbidden_key_at_construction(self) -> None:
        with pytest.raises(ValueError, match="Forbidden key"):
            FactRecord(
                fact_kind="sourced_claim",
                structured_payload={"final_action": "BUY"},
            )


# ── Criterion 7: worker does not import or call decide() ─────────────────────

def _read_worker_source(filename: str) -> str:
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(
        base, "app", "services", "intelligence", "research_workers", filename
    )
    with open(path) as f:
        return f.read()


class TestNoDecideDependency:

    def test_earnings_reviewer_has_no_import_of_decision_policy(self) -> None:
        source = _read_worker_source("earnings_reviewer.py")
        # Check for import statements only — not docstring mentions.
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        assert not any("decision_policy_v1" in l for l in import_lines), (
            "earnings_reviewer.py must not import decision_policy_v1"
        )

    def test_earnings_reviewer_has_no_decide_call(self) -> None:
        source = _read_worker_source("earnings_reviewer.py")
        # Remove docstrings/comments for the check.
        import ast
        tree = ast.parse(source)
        # If we can parse it cleanly, check only Call nodes for 'decide'.
        decide_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "decide")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "decide")
            )
        ]
        assert decide_calls == [], (
            "earnings_reviewer.py must not call decide()"
        )

    def test_runner_does_not_import_decision_policy(self) -> None:
        source = _read_worker_source("runner.py")
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        assert not any("decision_policy_v1" in l for l in import_lines), (
            "runner.py must not import decision_policy_v1"
        )

    def test_artifact_store_writer_does_not_import_decision_policy(self) -> None:
        source = _read_worker_source("artifact_store_writer.py")
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        assert not any("decision_policy_v1" in l for l in import_lines), (
            "artifact_store_writer.py must not import decision_policy_v1"
        )


# ── Criterion 8: visible Intel v3 snapshot certification unchanged ────────────

def _read_v3_source(filename: str) -> str:
    """Read a v3 source file directly — avoids importing supabase in test env."""
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "app", "services", "intelligence", "v3", filename)
    with open(path) as f:
        return f.read()


class TestVisibleIntelV3CertificationUnchanged:

    def test_intel_v3_service_source_unchanged(self) -> None:
        """Static source guard: intel_v3_service.py still forbids legacy aggregation."""
        source = _read_v3_source("intel_v3_service.py")
        assert "get_insight_cards" not in source
        assert "_compute_insight_cards" not in source
        assert "recommendation_engine" not in source

    def test_read_only_evidence_adapter_source_unchanged(self) -> None:
        source = _read_v3_source("read_only_evidence_adapter.py")
        assert "recommendation_engine" not in source
        assert "get_insight_cards" not in source
        assert "anthropic" not in source
        assert "openai" not in source

    def test_snapshot_log_constants_present(self) -> None:
        """Key log strings that the certification pipeline depends on must remain."""
        source = _read_v3_source("intel_v3_service.py")
        assert "intel_v3_snapshot_certification_summary" in source
        assert "source_path=intel_v3_snapshot" in source
        assert "attempted_llm_calls=0" in source
        assert "page_load_llm_calls=0" in source
        assert "generated_legacy_recommendations=false" in source

    def test_decision_policy_has_no_research_worker_dependency(self) -> None:
        """decide() has no dependency on research artifacts or workers."""
        source = _read_v3_source("decision_policy_v1.py")
        assert "research_artifact" not in source
        assert "research_workers" not in source
        assert "anthropic" not in source


# ── Criterion 9: no legacy recommendation_engine re-coupling ─────────────────

class TestNoLegacyAggregationRecoupling:

    def _all_worker_sources(self) -> list[str]:
        modules = [
            "app.services.intelligence.research_workers.contracts",
            "app.services.intelligence.research_workers.earnings_reviewer",
            "app.services.intelligence.research_workers.artifact_store_writer",
            "app.services.intelligence.research_workers.runner",
        ]
        sources = []
        for mod_name in modules:
            mod = importlib.import_module(mod_name)
            sources.append(inspect.getsource(mod))
        return sources

    def test_no_recommendation_engine_in_worker_modules(self) -> None:
        for src in self._all_worker_sources():
            assert "recommendation_engine" not in src, (
                "research_workers must not reference recommendation_engine"
            )

    def test_no_get_insight_cards_in_worker_modules(self) -> None:
        for src in self._all_worker_sources():
            assert "get_insight_cards" not in src

    def test_no_compute_insight_cards_in_worker_modules(self) -> None:
        for src in self._all_worker_sources():
            assert "_compute_insight_cards" not in src


# ── Criterion 10: failure path is safe and audited ───────────────────────────

class TestFailurePathSafe:

    def test_writer_returns_none_on_db_error(self) -> None:
        """ArtifactStoreWriter.write() returns None instead of raising on DB error."""

        class BrokenClient:
            def table(self, name: str):
                class BrokenQuery:
                    def upsert(self, *a, **kw): return self
                    def insert(self, *a, **kw): return self
                    def select(self, *a, **kw): return self
                    def eq(self, *a, **kw): return self
                    def order(self, *a, **kw): return self
                    def limit(self, *a, **kw): return self
                    def execute(self):
                        raise RuntimeError("simulated DB failure")
                return BrokenQuery()

        worker_input = WorkerInput(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            worker_run_id=str(uuid.uuid4()),
        )
        output = earnings_reviewer.run(worker_input)
        writer = ArtifactStoreWriter(supabase_client=BrokenClient(), user_id=worker_input.user_id)
        result = writer.write(output)
        assert result is None, "write() must return None on DB error, not raise"

    def test_runner_returns_none_on_db_error(self) -> None:
        """runner.run_earnings_reviewer_dark() returns None on DB failure."""

        class BrokenClient:
            def table(self, name: str):
                class BrokenQuery:
                    def upsert(self, *a, **kw): return self
                    def insert(self, *a, **kw): return self
                    def select(self, *a, **kw): return self
                    def eq(self, *a, **kw): return self
                    def order(self, *a, **kw): return self
                    def limit(self, *a, **kw): return self
                    def execute(self):
                        raise RuntimeError("simulated DB failure")
                return BrokenQuery()

        result = run_earnings_reviewer_dark(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=BrokenClient(),
            settings=_settings_all_on(),
        )
        assert result is None

    def test_runner_does_not_propagate_exception_to_caller(self) -> None:
        """No exception escapes runner — failure is silent (logged) from caller PoV."""

        class ExplodingClient:
            def table(self, name: str):
                raise ValueError("unexpected explosion")

        try:
            run_earnings_reviewer_dark(
                user_id=str(uuid.uuid4()),
                ticker="AAPL",
                db_client=ExplodingClient(),
                settings=_settings_all_on(),
            )
        except Exception as exc:
            pytest.fail(f"runner raised unexpectedly: {exc}")


# ── Bonus: idempotency key stability ─────────────────────────────────────────

class TestIdempotencyKey:

    def test_same_inputs_produce_same_replay_key(self) -> None:
        key1 = compute_replay_idempotency_key("earnings_reviewer", "ticker", "AAPL", "no_src", "none")
        key2 = compute_replay_idempotency_key("earnings_reviewer", "ticker", "AAPL", "no_src", "none")
        assert key1 == key2

    def test_different_tickers_produce_different_keys(self) -> None:
        key_aapl = compute_replay_idempotency_key("earnings_reviewer", "ticker", "AAPL", "no_src", "none")
        key_msft = compute_replay_idempotency_key("earnings_reviewer", "ticker", "MSFT", "no_src", "none")
        assert key_aapl != key_msft

    def test_worker_produces_deterministic_replay_key(self) -> None:
        user_id = str(uuid.uuid4())
        ticker = "NVDA"
        input1 = WorkerInput(user_id=user_id, ticker=ticker, worker_run_id="run-a")
        input2 = WorkerInput(user_id=user_id, ticker=ticker, worker_run_id="run-b")
        out1 = earnings_reviewer.run(input1)
        out2 = earnings_reviewer.run(input2)
        assert out1.replay_idempotency_key == out2.replay_idempotency_key, (
            "Same ticker → same replay key regardless of worker_run_id"
        )

    def test_holding_context_keys_affect_fingerprint_but_not_replay_key(self) -> None:
        user_id = str(uuid.uuid4())
        ticker = "AAPL"
        input_no_ctx = WorkerInput(user_id=user_id, ticker=ticker, worker_run_id="r1")
        input_with_ctx = WorkerInput(
            user_id=user_id, ticker=ticker, worker_run_id="r2",
            holding_context={"primary_driver": "strong growth"}
        )
        out_no_ctx = earnings_reviewer.run(input_no_ctx)
        out_with_ctx = earnings_reviewer.run(input_with_ctx)
        # Replay key must be the same (same ticker, same source fingerprint).
        assert out_no_ctx.replay_idempotency_key == out_with_ctx.replay_idempotency_key
        # But input fingerprints differ (holding context keys are included).
        assert out_no_ctx.input_fingerprint != out_with_ctx.input_fingerprint


# ── Bonus: worker output content validation ───────────────────────────────────

class TestWorkerOutputContent:

    def test_earnings_reviewer_output_has_expected_structure(self) -> None:
        wi = WorkerInput(user_id=str(uuid.uuid4()), ticker="tsla", worker_run_id=str(uuid.uuid4()))
        output = earnings_reviewer.run(wi)
        assert output.ticker == "TSLA"  # normalized to uppercase
        assert output.artifact_type == "catalyst_window"
        assert output.skill_pack == "earnings_reviewer"
        assert output.scope_kind == "ticker"
        assert output.confidence_or_trust_level == "UNKNOWN"
        assert output.freshness_status == "UNKNOWN"
        assert len(output.limitations_or_missing_evidence) > 0
        assert output.evidence_summary_plain_english is not None

    def test_no_external_sources_in_dark_run(self) -> None:
        wi = WorkerInput(user_id=str(uuid.uuid4()), ticker="AAPL", worker_run_id=str(uuid.uuid4()))
        output = earnings_reviewer.run(wi)
        assert output.sources == [], "Phase 3 dark-run must have no external sources"

    def test_worker_acknowledges_missing_fields(self) -> None:
        wi = WorkerInput(user_id=str(uuid.uuid4()), ticker="AAPL", worker_run_id=str(uuid.uuid4()))
        output = earnings_reviewer.run(wi)
        assert any("missing" in lim.lower() for lim in output.limitations_or_missing_evidence)

    def test_holding_context_with_existing_fields(self) -> None:
        wi = WorkerInput(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            worker_run_id=str(uuid.uuid4()),
            holding_context={"earnings_date_next": "2026-07-30", "eps_actual_last": 1.53},
        )
        output = earnings_reviewer.run(wi)
        payload = output.artifact_payload
        assert "earnings_date_next" in payload.get("found_fields", [])
        assert "eps_actual_last" in payload.get("found_fields", [])
