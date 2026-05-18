"""Stage 5A focused tests — Research Artifact Store substrate and writer scaffolding.

Acceptance criteria verified here:
  1. Idempotent write: same replay_idempotency_key → no duplicate, existing id returned.
  2. Clean replacement: different key, same (ticker, type, skill_pack) → old deactivated.
  3. Provenance/source fields preserved: source_kind, provider_name, fetched_at.
  4. Freshness/as_of/fetched_at handling: freshness_status, expires_at propagated.
  5. artifact_schema_version always set ('artifact.v1').
  6. Replay/run identity stored: worker_run_id, parent_intel_run_id.
  7. Forbidden-key rejection: ValueError on forbidden keys in payload/facts.
  8. Artifact writer cannot emit or override Buy/Hold/Trim/Sell decisions.
  9. Stage 5A artifact_type values accepted (technical_signal, sentiment_event,
     company_strategy, journal_pattern + existing types).
 10. query_active_artifacts returns safe fields only; DB errors return [].
 11. No mutation of visible Intel v3 decisions (intel_v3_snapshots not touched).

No production Supabase dependency — all fakes defined locally.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


# ── Fake infrastructure ───────────────────────────────────────────────────────

@dataclass
class _Row:
    data: dict[str, Any]


@dataclass
class _UpdateOp:
    """Recorded UPDATE operation for assertion."""
    table: str
    patch: dict[str, Any]
    filters: dict[str, Any]


class _FakeTable:
    """Minimal Supabase table fake: tracks inserts, selects, updates."""

    def __init__(
        self,
        table_name: str,
        shared: "_FakeDB",
    ) -> None:
        self._name = table_name
        self._shared = shared
        self._op: Optional[str] = None  # select | insert | update
        self._payload: Optional[dict] = None
        self._filters: dict[str, Any] = {}
        self._neg_filters: dict[str, Any] = {}
        self._null_filters: dict[str, bool] = {}  # columns that must be None (IS NULL)
        self._select_cols: Optional[str] = None
        self._limit_n: Optional[int] = None
        self._order_col: Optional[str] = None
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
        self._payload = patch
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
        self._order_col = col
        self._order_desc = desc
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
            update_op = _UpdateOp(
                table=self._name,
                patch=dict(self._payload),
                filters={
                    **self._filters,
                    "_neq": self._neg_filters,
                    "_is_null": list(self._null_filters.keys()),
                },
            )
            self._shared.updates.append(update_op)
            # Apply to in-memory rows.
            patched = []
            rows = self._shared.tables.get(self._name, [])
            for row in rows:
                if self._matches(row):
                    row = {**row, **self._payload}
                    patched.append(row)
            for row in patched:
                idx = next(
                    i for i, r in enumerate(rows) if r["id"] == row["id"]
                )
                self._shared.tables[self._name][idx] = row

            class _Res:
                data = patched
            return _Res()

        if self._op == "select":
            rows = self._shared.tables.get(self._name, [])
            matched = [r for r in rows if self._matches(r)]
            if self._limit_n:
                matched = matched[: self._limit_n]

            class _Res:
                data = matched
            return _Res()

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
    """Shared in-memory store across all FakeTable instances."""
    tables: dict[str, list[dict]] = field(default_factory=dict)
    inserts: dict[str, list[dict]] = field(default_factory=dict)
    updates: list[_UpdateOp] = field(default_factory=list)


class _FakeClient:
    """Fake Supabase client — returns a FakeTable per table() call."""

    def __init__(self, shared: "_FakeDB") -> None:
        self._shared = shared

    def table(self, name: str) -> "_FakeTable":
        return _FakeTable(name, self._shared)


# ── Helpers ───────────────────────────────────────────────────────────────────

_USER_ID = "user-stage5a-test"
_TICKER = "AAPL"


def _make_key(suffix: str = "v1") -> str:
    return compute_replay_idempotency_key(
        skill_pack="test_pack",
        scope_kind="ticker",
        ticker=_TICKER,
        source_refs_fingerprint=f"fp_{suffix}",
        model_version="none",
    )


def _make_output(
    *,
    artifact_type: str = "filing_risk",
    skill_pack: str = "test_pack",
    ticker: Optional[str] = _TICKER,
    scope_kind: str = "ticker",
    key_suffix: str = "v1",
    payload: Optional[dict] = None,
    sources: Optional[list[SourceRecord]] = None,
    facts: Optional[list[FactRecord]] = None,
    freshness_status: str = "FRESH",
    confidence_or_trust_level: str = "MEDIUM",
    expires_at: Optional[str] = None,
    parent_intel_run_id: Optional[str] = None,
) -> WorkerOutput:
    idempotency_key = compute_replay_idempotency_key(
        skill_pack=skill_pack,
        scope_kind=scope_kind,
        ticker=ticker or "",
        source_refs_fingerprint=f"fp_{key_suffix}",
        model_version="none",
    )
    input_fp = compute_input_fingerprint({"ticker": ticker, "suffix": key_suffix})
    return WorkerOutput(
        worker_run_id=str(uuid.uuid4()),
        ticker=ticker,
        artifact_type=artifact_type,
        skill_pack=skill_pack,
        scope_kind=scope_kind,
        artifact_payload=payload or {"evidence_summary": "test evidence"},
        sources=sources or [],
        facts=facts or [],
        audit_events=[AuditEventRecord(tool_call="test_run", status="completed")],
        evidence_summary_plain_english="Test evidence summary.",
        limitations_or_missing_evidence=[],
        confidence_or_trust_level=confidence_or_trust_level,
        freshness_status=freshness_status,
        input_fingerprint=input_fp,
        replay_idempotency_key=idempotency_key,
        expires_at=expires_at,
        parent_intel_run_id=parent_intel_run_id,
    )


def _make_service(db: Optional[_FakeDB] = None) -> tuple[ResearchArtifactServiceV1, _FakeDB]:
    if db is None:
        db = _FakeDB()
    client = _FakeClient(db)
    service = ResearchArtifactServiceV1(client, _USER_ID)
    return service, db


# ── Tests: Idempotency ────────────────────────────────────────────────────────

class TestIdempotentWrite:
    def test_same_key_returns_existing_id_no_second_insert(self) -> None:
        """Identical replay_idempotency_key → skip, return existing artifact_id."""
        service, db = _make_service()
        output = _make_output(key_suffix="v1")

        first_id = service.write_artifact(output)
        assert first_id is not None

        first_insert_count = len(db.inserts.get("research_artifacts", []))

        second_id = service.write_artifact(output)
        second_insert_count = len(db.inserts.get("research_artifacts", []))

        assert second_id == first_id, "Idempotent write must return same artifact_id"
        assert second_insert_count == first_insert_count, "No new row inserted on idempotent replay"

    def test_different_key_produces_new_artifact(self) -> None:
        """Different source fingerprint → different idempotency key → new artifact."""
        service, db = _make_service()

        first_id = service.write_artifact(_make_output(key_suffix="v1"))
        second_id = service.write_artifact(_make_output(key_suffix="v2"))

        assert first_id is not None
        assert second_id is not None
        assert first_id != second_id


# ── Tests: Clean Replacement ──────────────────────────────────────────────────

class TestCleanReplacement:
    def test_new_write_deactivates_previous_active_for_same_ticker_type_pack(self) -> None:
        """Writing a new artifact for same (ticker, type, pack) deactivates the old one."""
        service, db = _make_service()

        # First write: creates active artifact.
        first_output = _make_output(key_suffix="v1")
        first_id = service.write_artifact(first_output)
        assert first_id is not None

        # Second write: different key (evidence updated), same ticker/type/pack.
        second_output = _make_output(key_suffix="v2")
        second_id = service.write_artifact(second_output)
        assert second_id is not None
        assert second_id != first_id

        # Verify a deactivation UPDATE was issued.
        deactivation_updates = [
            u for u in db.updates
            if u.table == "research_artifacts"
            and u.patch.get("is_active") is False
            and u.patch.get("invalidation_reason") == "superseded_by_new_write"
        ]
        assert len(deactivation_updates) >= 1, "Previous artifact must be deactivated"

        # Verify deactivation filter targeted the right ticker/type/pack.
        u = deactivation_updates[0]
        assert u.filters.get("ticker") == _TICKER
        assert u.filters.get("artifact_type") == "filing_risk"
        assert u.filters.get("skill_pack") == "test_pack"

    def test_clean_replacement_sets_invalidated_at(self) -> None:
        """Deactivated artifact gets invalidated_at timestamp."""
        service, db = _make_service()
        service.write_artifact(_make_output(key_suffix="v1"))
        service.write_artifact(_make_output(key_suffix="v2"))

        updates = [
            u for u in db.updates
            if u.patch.get("invalidation_reason") == "superseded_by_new_write"
        ]
        assert updates, "Expected a deactivation update"
        assert updates[0].patch.get("invalidated_at") is not None

    def test_idempotent_replay_does_not_deactivate(self) -> None:
        """Idempotent replay (same key) must NOT trigger a deactivation update."""
        service, db = _make_service()
        output = _make_output(key_suffix="v1")

        service.write_artifact(output)
        deactivations_before = len([u for u in db.updates if u.patch.get("is_active") is False])

        service.write_artifact(output)  # idempotent replay
        deactivations_after = len([u for u in db.updates if u.patch.get("is_active") is False])

        assert deactivations_after == deactivations_before, (
            "Idempotent replay must not trigger deactivation"
        )

    def test_different_artifact_type_not_deactivated(self) -> None:
        """Clean replacement is scoped per artifact_type: a technical_signal write must not
        deactivate an existing filing_risk artifact on the same ticker."""
        service, db = _make_service()

        filing_output = _make_output(artifact_type="filing_risk", key_suffix="filing_v1")
        technical_output = _make_output(artifact_type="technical_signal", key_suffix="tech_v1")

        service.write_artifact(filing_output)
        service.write_artifact(technical_output)

        # The filing_risk artifact must still be active in the in-memory table after both writes.
        filing_arts = [
            r for r in db.tables.get("research_artifacts", [])
            if r.get("artifact_type") == "filing_risk"
        ]
        assert len(filing_arts) == 1, "Expected exactly one filing_risk artifact"
        assert filing_arts[0].get("is_active") is True, (
            "filing_risk artifact must remain active when a technical_signal is written"
        )

        # Additionally: any deactivation UPDATE triggered by the technical_signal write
        # must target 'technical_signal', not 'filing_risk'.
        # (The filing_risk write itself may issue a deactivation UPDATE for prior filing_risk
        # rows, which is correct; we only verify that technical_signal's deactivation is scoped.)
        technical_deactivation_targets = {
            u.filters.get("artifact_type")
            for u in db.updates
            if u.patch.get("is_active") is False
            and u.filters.get("_neq", {}).get("replay_idempotency_key")
               == technical_output.replay_idempotency_key
        }
        assert "filing_risk" not in technical_deactivation_targets, (
            "Deactivation triggered by technical_signal write must not target filing_risk"
        )


# ── Tests: Scope-aware clean replacement ─────────────────────────────────────

class TestScopeAwareCleanReplacement:
    def test_portfolio_scope_clean_replacement(self) -> None:
        """Portfolio-scope artifact (ticker=None, scope_kind='portfolio') is deactivated
        when a new artifact for the same lane is written with a different key."""
        service, db = _make_service()

        first = _make_output(
            artifact_type="portfolio_exposure",
            scope_kind="portfolio",
            ticker=None,
            key_suffix="port_v1",
        )
        first_id = service.write_artifact(first)
        assert first_id is not None

        second = _make_output(
            artifact_type="portfolio_exposure",
            scope_kind="portfolio",
            ticker=None,
            key_suffix="port_v2",
        )
        second_id = service.write_artifact(second)
        assert second_id is not None
        assert second_id != first_id

        # First portfolio artifact must be deactivated in-memory.
        portfolio_arts = [
            r for r in db.tables.get("research_artifacts", [])
            if r.get("scope_kind") == "portfolio"
        ]
        active = [r for r in portfolio_arts if r.get("is_active")]
        inactive = [r for r in portfolio_arts if not r.get("is_active")]
        assert len(active) == 1, "Exactly one active portfolio artifact expected"
        assert len(inactive) == 1, "First portfolio artifact must be deactivated"
        assert active[0]["id"] == second_id

    def test_portfolio_scope_deactivation_filter_uses_is_null(self) -> None:
        """Deactivation UPDATE for portfolio-scope must use IS NULL (not eq) for ticker."""
        service, db = _make_service()

        service.write_artifact(
            _make_output(
                artifact_type="portfolio_exposure",
                scope_kind="portfolio",
                ticker=None,
                key_suffix="port_v1",
            )
        )
        service.write_artifact(
            _make_output(
                artifact_type="portfolio_exposure",
                scope_kind="portfolio",
                ticker=None,
                key_suffix="port_v2",
            )
        )

        portfolio_deactivations = [
            u for u in db.updates
            if u.patch.get("is_active") is False
            and u.filters.get("scope_kind") == "portfolio"
        ]
        assert portfolio_deactivations, "Expected a deactivation UPDATE for portfolio scope"
        u = portfolio_deactivations[0]
        assert "ticker" in u.filters.get("_is_null", []), (
            "Portfolio deactivation must use IS NULL for ticker, not eq"
        )
        assert "ticker" not in u.filters, (
            "Portfolio deactivation must not use .eq('ticker', ...) — ticker is NULL"
        )

    def test_deactivation_filter_includes_scope_kind(self) -> None:
        """Every deactivation UPDATE must include scope_kind in its filter."""
        service, db = _make_service()
        service.write_artifact(_make_output(key_suffix="sk_v1"))
        service.write_artifact(_make_output(key_suffix="sk_v2"))

        deactivations = [u for u in db.updates if u.patch.get("is_active") is False]
        assert deactivations, "Expected at least one deactivation update"
        for u in deactivations:
            assert "scope_kind" in u.filters, (
                "Every deactivation UPDATE must filter by scope_kind"
            )

    def test_ticker_scope_deactivation_uses_eq_ticker(self) -> None:
        """Deactivation for ticker-scope must use .eq('ticker', ticker) — not IS NULL."""
        service, db = _make_service()
        service.write_artifact(_make_output(key_suffix="t_v1"))
        service.write_artifact(_make_output(key_suffix="t_v2"))

        ticker_deactivations = [
            u for u in db.updates
            if u.patch.get("is_active") is False
            and u.filters.get("scope_kind") == "ticker"
        ]
        assert ticker_deactivations, "Expected a deactivation UPDATE for ticker scope"
        u = ticker_deactivations[0]
        assert u.filters.get("ticker") == _TICKER, (
            "Ticker-scope deactivation must use .eq('ticker', ticker)"
        )
        assert "ticker" not in u.filters.get("_is_null", []), (
            "Ticker-scope deactivation must not use IS NULL for ticker"
        )

    def test_portfolio_scope_does_not_deactivate_ticker_scope_artifacts(self) -> None:
        """Writing a portfolio-scope artifact must not deactivate ticker-scope artifacts."""
        service, db = _make_service()

        ticker_art = _make_output(
            artifact_type="portfolio_exposure",
            scope_kind="ticker",
            ticker=_TICKER,
            key_suffix="tick_v1",
        )
        ticker_id = service.write_artifact(ticker_art)
        assert ticker_id is not None

        _make_output(
            artifact_type="portfolio_exposure",
            scope_kind="portfolio",
            ticker=None,
            key_suffix="port_v1",
        )
        service.write_artifact(
            _make_output(
                artifact_type="portfolio_exposure",
                scope_kind="portfolio",
                ticker=None,
                key_suffix="port_v1",
            )
        )

        # The ticker-scope artifact must still be active.
        ticker_rows = [
            r for r in db.tables.get("research_artifacts", [])
            if r.get("scope_kind") == "ticker" and r.get("artifact_type") == "portfolio_exposure"
        ]
        assert len(ticker_rows) == 1
        assert ticker_rows[0].get("is_active") is True, (
            "ticker-scope artifact must not be deactivated by a portfolio-scope write"
        )


# ── Tests: Provenance / source fields ────────────────────────────────────────

class TestProvenancePreservation:
    def test_source_fields_written(self) -> None:
        """Source fields (source_kind, provider_name, source_url) are persisted."""
        service, db = _make_service()
        sources = [
            SourceRecord(
                source_kind="sec_filing",
                provider_name="sec_edgar",
                source_url="https://example.com/filing/acc123",
                source_id="acc123",
                provider_version="v1",
                source_hash="sha256_abc",
            )
        ]
        output = _make_output(sources=sources)
        artifact_id = service.write_artifact(output)
        assert artifact_id is not None

        inserted_sources = db.inserts.get("research_artifact_sources", [])
        assert len(inserted_sources) == 1
        src = inserted_sources[0]
        assert src["source_kind"] == "sec_filing"
        assert src["provider_name"] == "sec_edgar"
        assert src["source_url"] == "https://example.com/filing/acc123"
        assert src["source_id"] == "acc123"
        assert src["artifact_id"] == artifact_id
        assert src["user_id"] == _USER_ID

    def test_multiple_sources_all_written(self) -> None:
        """Multiple source records are all persisted under the same artifact."""
        service, db = _make_service()
        sources = [
            SourceRecord(source_kind="sec_filing", provider_name="edgar", source_url="url_1"),
            SourceRecord(source_kind="transcript", provider_name="transcript_co", source_url="url_2"),
        ]
        artifact_id = service.write_artifact(_make_output(sources=sources))
        assert artifact_id is not None

        inserted = db.inserts.get("research_artifact_sources", [])
        assert len(inserted) == 2
        artifact_ids = {s["artifact_id"] for s in inserted}
        assert artifact_ids == {artifact_id}


# ── Tests: Freshness / timestamps ────────────────────────────────────────────

class TestFreshnessFields:
    def test_freshness_status_propagated(self) -> None:
        """freshness_status from WorkerOutput is written to the artifact row."""
        service, db = _make_service()
        service.write_artifact(_make_output(freshness_status="FRESH"))

        arts = db.inserts.get("research_artifacts", [])
        assert arts, "Expected one inserted artifact"
        assert arts[0].get("freshness_status") == "FRESH"

    def test_stale_freshness_status_propagated(self) -> None:
        service, db = _make_service()
        service.write_artifact(_make_output(freshness_status="STALE", key_suffix="stale"))

        arts = db.inserts.get("research_artifacts", [])
        assert arts[0].get("freshness_status") == "STALE"

    def test_expires_at_propagated_when_set(self) -> None:
        """expires_at from WorkerOutput propagates to the artifact row."""
        service, db = _make_service()
        exp = "2026-12-31T00:00:00+00:00"
        service.write_artifact(_make_output(expires_at=exp, key_suffix="exp"))

        arts = db.inserts.get("research_artifacts", [])
        assert arts[0].get("expires_at") == exp

    def test_expires_at_omitted_when_none(self) -> None:
        """No expires_at key written when WorkerOutput.expires_at is None."""
        service, db = _make_service()
        service.write_artifact(_make_output(expires_at=None))

        arts = db.inserts.get("research_artifacts", [])
        # Key should be absent (not None) since writer only sets it when present.
        assert "expires_at" not in arts[0] or arts[0].get("expires_at") is None

    def test_fact_as_of_field_propagated(self) -> None:
        """FactRecord.as_of is persisted to research_artifact_facts."""
        service, db = _make_service()
        facts = [
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={"revenue_growth_pct": 12.5},
                as_of="2026-Q1",
                is_quote_grounded=False,
            )
        ]
        service.write_artifact(_make_output(facts=facts))

        inserted_facts = db.inserts.get("research_artifact_facts", [])
        assert len(inserted_facts) == 1
        assert inserted_facts[0].get("as_of") == "2026-Q1"


# ── Tests: Schema version ─────────────────────────────────────────────────────

class TestSchemaVersion:
    def test_artifact_schema_version_always_set(self) -> None:
        """artifact_schema_version is always written as 'artifact.v1'."""
        service, db = _make_service()
        service.write_artifact(_make_output())

        arts = db.inserts.get("research_artifacts", [])
        assert arts, "Expected one inserted artifact"
        assert arts[0].get("artifact_schema_version") == "artifact.v1"

    def test_safe_for_decision_always_false(self) -> None:
        """safe_for_decision is always False — writer never sets it True."""
        service, db = _make_service()
        service.write_artifact(_make_output())

        arts = db.inserts.get("research_artifacts", [])
        assert arts[0].get("safe_for_decision") is False


# ── Tests: Replay / run identity ─────────────────────────────────────────────

class TestReplayIdentity:
    def test_worker_run_id_stored(self) -> None:
        """worker_run_id from WorkerOutput is persisted to the artifact row."""
        service, db = _make_service()
        run_id = str(uuid.uuid4())
        output = _make_output()
        output.worker_run_id = run_id

        service.write_artifact(output)
        arts = db.inserts.get("research_artifacts", [])
        assert arts[0].get("worker_run_id") == run_id

    def test_parent_intel_run_id_stored_when_set(self) -> None:
        """parent_intel_run_id is stored in the artifact row when provided."""
        service, db = _make_service()
        parent_run = str(uuid.uuid4())

        service.write_artifact(
            _make_output(parent_intel_run_id=parent_run, key_suffix="parent")
        )
        arts = db.inserts.get("research_artifacts", [])
        assert arts[0].get("parent_intel_run_id") == parent_run

    def test_replay_idempotency_key_stored(self) -> None:
        """replay_idempotency_key is persisted for downstream replay verification."""
        service, db = _make_service()
        output = _make_output(key_suffix="replay_test")

        service.write_artifact(output)
        arts = db.inserts.get("research_artifacts", [])
        assert arts[0].get("replay_idempotency_key") == output.replay_idempotency_key

    def test_input_fingerprint_stored(self) -> None:
        """input_fingerprint is persisted for audit trail."""
        service, db = _make_service()
        output = _make_output(key_suffix="fp_test")

        service.write_artifact(output)
        arts = db.inserts.get("research_artifacts", [])
        assert arts[0].get("input_fingerprint") == output.input_fingerprint


# ── Tests: Forbidden key rejection ───────────────────────────────────────────

class TestForbiddenKeyRejection:
    def test_payload_with_final_action_returns_none(self) -> None:
        """Payload containing 'final_action' is rejected; write returns None."""
        service, db = _make_service()

        with pytest.raises(ValueError, match="final_action"):
            _make_output(payload={"final_action": "BUY"})

    def test_payload_with_buy_key_returns_none(self) -> None:
        """Payload containing 'buy' key is rejected."""
        service, db = _make_service()

        with pytest.raises(ValueError, match="buy"):
            _make_output(payload={"buy": True})

    def test_payload_with_nested_sell_rejected(self) -> None:
        """Nested forbidden key is caught by WorkerOutput validation."""
        with pytest.raises(ValueError):
            _make_output(payload={"details": {"sell": "strong"}})

    def test_clean_payload_accepted(self) -> None:
        """Clean payload without forbidden keys is accepted."""
        service, db = _make_service()
        output = _make_output(payload={
            "revenue_growth_pct": 12.5,
            "evidence_quality": "MEDIUM",
            "analyst_rating_change": "upgrade",
        })
        artifact_id = service.write_artifact(output)
        assert artifact_id is not None

    def test_fact_with_forbidden_key_raises_at_construction(self) -> None:
        """FactRecord with forbidden key raises ValueError at construction time."""
        with pytest.raises(ValueError, match="action"):
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={"action": "BUY"},
            )


# ── Tests: No Intel v3 decision mutation ─────────────────────────────────────

class TestNoDecisionMutation:
    def test_intel_v3_snapshots_table_never_written(self) -> None:
        """Service never inserts or updates intel_v3_snapshots."""
        service, db = _make_service()
        service.write_artifact(_make_output())

        assert "intel_v3_snapshots" not in db.inserts, (
            "research_artifact_service must never write to intel_v3_snapshots"
        )
        snapshot_updates = [u for u in db.updates if u.table == "intel_v3_snapshots"]
        assert not snapshot_updates, (
            "research_artifact_service must never update intel_v3_snapshots"
        )

    def test_recommendations_table_never_written(self) -> None:
        """Service never touches the recommendations table."""
        service, db = _make_service()
        service.write_artifact(_make_output())

        assert "recommendations" not in db.inserts
        rec_updates = [u for u in db.updates if u.table == "recommendations"]
        assert not rec_updates

    def test_write_does_not_import_decide(self) -> None:
        """research_artifact_service_v1 must not have an import of decision_policy_v1."""
        import ast
        import app.services.intelligence.v3.research_artifact_service_v1 as svc_mod
        src = open(svc_mod.__file__).read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    assert "decision_policy_v1" not in (name or ""), (
                        f"research_artifact_service_v1 must not import decision_policy_v1, "
                        f"found import: {name}"
                    )
                    assert "decide" not in (name or ""), (
                        f"research_artifact_service_v1 must not import decide, "
                        f"found import: {name}"
                    )


# ── Tests: Stage 5A artifact_type values ─────────────────────────────────────

class TestStage5AArtifactTypes:
    """Verify that Stage 5A artifact_type values are accepted by WorkerOutput."""

    @pytest.mark.parametrize("artifact_type", [
        "technical_signal",
        "sentiment_event",
        "company_strategy",
        "journal_pattern",
    ])
    def test_stage5a_artifact_type_accepted_by_writer(self, artifact_type: str) -> None:
        """Stage 5A artifact_type values pass WorkerOutput construction and write."""
        service, db = _make_service()
        output = _make_output(
            artifact_type=artifact_type,
            key_suffix=f"{artifact_type}_v1",
        )
        artifact_id = service.write_artifact(output)
        assert artifact_id is not None

        arts = db.inserts.get("research_artifacts", [])
        assert any(a["artifact_type"] == artifact_type for a in arts)

    @pytest.mark.parametrize("artifact_type", [
        "filing_risk",
        "catalyst_window",
        "valuation_context",
        "fundamental_quality",
        "capital_allocation",
        "risk_red_team",
        "analyst_revisions",
        "news_event",
        "hidden_gem_candidate",
    ])
    def test_existing_artifact_type_still_accepted(self, artifact_type: str) -> None:
        """Existing artifact_type values (from 017) are still accepted."""
        service, db = _make_service()
        output = _make_output(
            artifact_type=artifact_type,
            key_suffix=f"{artifact_type}_v1",
        )
        artifact_id = service.write_artifact(output)
        assert artifact_id is not None


# ── Tests: query_active_artifacts ────────────────────────────────────────────

class TestQueryActiveArtifacts:
    def test_returns_active_artifacts_for_user(self) -> None:
        """query_active_artifacts returns artifacts written by this user."""
        service, db = _make_service()
        service.write_artifact(_make_output(key_suffix="q1"))

        results = service.query_active_artifacts(ticker=_TICKER)
        assert len(results) >= 1

    def test_filters_by_artifact_type(self) -> None:
        """artifact_type filter is applied correctly."""
        service, db = _make_service()
        service.write_artifact(_make_output(artifact_type="filing_risk", key_suffix="filing_q"))
        service.write_artifact(_make_output(artifact_type="technical_signal", key_suffix="tech_q"))

        results = service.query_active_artifacts(artifact_type="filing_risk")
        assert all(r["artifact_type"] == "filing_risk" for r in results)

    def test_db_error_returns_empty_list(self) -> None:
        """DB errors in query return [] without raising."""

        class _BrokenClient:
            def table(self, name: str):
                class _Broken:
                    def select(self, *a): return self
                    def eq(self, *a): return self
                    def order(self, *a, **kw): return self
                    def limit(self, *a): return self
                    def execute(self):
                        raise RuntimeError("DB unavailable")
                return _Broken()

        svc = ResearchArtifactServiceV1(_BrokenClient(), _USER_ID)
        results = svc.query_active_artifacts()
        assert results == []

    def test_write_failure_returns_none(self) -> None:
        """DB failure on insert returns None without raising."""

        class _InsertFailClient:
            def table(self, name: str):
                class _Q:
                    def select(self, *a): return self
                    def insert(self, *a): return self
                    def update(self, *a): return self
                    def eq(self, *a): return self
                    def neq(self, *a): return self
                    def order(self, *a, **kw): return self
                    def limit(self, *a): return self
                    def execute(self):
                        if name == "research_artifacts":
                            raise RuntimeError("connection reset")
                        class _E:
                            data = []
                        return _E()
                return _Q()

        svc = ResearchArtifactServiceV1(_InsertFailClient(), _USER_ID)
        result = svc.write_artifact(_make_output())
        assert result is None


# ── Tests: Fail-closed deactivation ──────────────────────────────────────────

class TestFailClosedDeactivation:
    def test_deactivation_failure_returns_none_no_insert(self) -> None:
        """If deactivation (UPDATE) fails, write returns None without inserting."""
        inserts_attempted: list[dict] = []

        class _Q:
            def __init__(self, name: str) -> None:
                self._name = name
                self._op: Optional[str] = None
                self._row: Optional[dict] = None

            def select(self, *a: Any) -> "_Q": self._op = "select"; return self
            def insert(self, row: dict) -> "_Q":
                self._op = "insert"; self._row = row; return self
            def update(self, *a: Any) -> "_Q": self._op = "update"; return self
            def eq(self, *a: Any) -> "_Q": return self
            def neq(self, *a: Any) -> "_Q": return self
            def order(self, *a: Any, **kw: Any) -> "_Q": return self
            def limit(self, *a: Any) -> "_Q": return self

            def execute(self) -> Any:
                if self._op == "update" and self._name == "research_artifacts":
                    raise RuntimeError("DB connection lost during deactivation")
                if self._op == "insert" and self._name == "research_artifacts":
                    row = dict(self._row or {})
                    row.setdefault("id", str(uuid.uuid4()))
                    inserts_attempted.append(row)
                    class _Res:
                        data = [row]
                    return _Res()
                class _E:
                    data = []
                return _E()

        class _DeactivateFailClient:
            def table(self, name: str) -> _Q:
                return _Q(name)

        svc = ResearchArtifactServiceV1(_DeactivateFailClient(), _USER_ID)
        result = svc.write_artifact(_make_output())

        assert result is None, "Deactivation failure must cause write to return None"
        assert len(inserts_attempted) == 0, (
            "No artifact insert must occur when deactivation fails (fail-closed)"
        )

    def test_deactivation_success_allows_insert(self) -> None:
        """When deactivation succeeds, write proceeds and returns an artifact_id."""
        service, db = _make_service()
        # First write seeds an existing active row.
        first_id = service.write_artifact(_make_output(key_suffix="v1"))
        assert first_id is not None

        # Second write with different key must deactivate v1 and insert v2.
        second_id = service.write_artifact(_make_output(key_suffix="v2"))
        assert second_id is not None
        assert second_id != first_id


# ── Tests: User-scoped replay idempotency ────────────────────────────────────

class TestUserScopedIdempotency:
    def test_same_key_different_users_both_succeed(self) -> None:
        """Two users sharing the same replay_idempotency_key must each get their own artifact."""
        db = _FakeDB()
        client = _FakeClient(db)

        svc_a = ResearchArtifactServiceV1(client, "user-alpha")
        svc_b = ResearchArtifactServiceV1(client, "user-beta")

        # Build both outputs with identical idempotency key.
        output_a = _make_output(key_suffix="shared")
        shared_key = output_a.replay_idempotency_key

        fp_b = compute_input_fingerprint({"ticker": _TICKER, "user": "beta"})
        output_b = WorkerOutput(
            worker_run_id=str(uuid.uuid4()),
            ticker=_TICKER,
            artifact_type="filing_risk",
            skill_pack="test_pack",
            scope_kind="ticker",
            artifact_payload={"evidence_summary": "beta evidence"},
            sources=[],
            facts=[],
            audit_events=[AuditEventRecord(tool_call="test_run", status="completed")],
            evidence_summary_plain_english="Beta evidence.",
            limitations_or_missing_evidence=[],
            confidence_or_trust_level="MEDIUM",
            freshness_status="FRESH",
            input_fingerprint=fp_b,
            replay_idempotency_key=shared_key,
        )

        id_a = svc_a.write_artifact(output_a)
        id_b = svc_b.write_artifact(output_b)

        assert id_a is not None, "User alpha write must succeed"
        assert id_b is not None, "User beta write must succeed with same key"
        assert id_a != id_b, "Each user must get a distinct artifact_id"

    def test_same_user_same_key_idempotent(self) -> None:
        """Same user + same key = idempotent skip (existing id returned)."""
        service, db = _make_service()
        output = _make_output(key_suffix="idem")

        first_id = service.write_artifact(output)
        second_id = service.write_artifact(output)

        assert first_id == second_id
        assert len(db.inserts.get("research_artifacts", [])) == 1


# ── Tests: fetched_at provenance ─────────────────────────────────────────────

class TestFetchedAtProvenance:
    def test_explicit_fetched_at_written_to_source_row(self) -> None:
        """SourceRecord.fetched_at is written to research_artifact_sources when set."""
        service, db = _make_service()
        fetched = "2026-01-15T12:00:00+00:00"
        sources = [
            SourceRecord(
                source_kind="sec_filing",
                provider_name="edgar",
                fetched_at=fetched,
            )
        ]
        artifact_id = service.write_artifact(_make_output(sources=sources, key_suffix="fa1"))
        assert artifact_id is not None

        inserted_sources = db.inserts.get("research_artifact_sources", [])
        assert len(inserted_sources) == 1
        assert inserted_sources[0].get("fetched_at") == fetched

    def test_omitted_fetched_at_not_in_source_row(self) -> None:
        """When SourceRecord.fetched_at is None, fetched_at key is absent (DB uses DEFAULT)."""
        service, db = _make_service()
        sources = [SourceRecord(source_kind="transcript", provider_name="transcripts_co")]
        service.write_artifact(_make_output(sources=sources, key_suffix="fa2"))

        inserted_sources = db.inserts.get("research_artifact_sources", [])
        assert len(inserted_sources) == 1
        assert "fetched_at" not in inserted_sources[0], (
            "fetched_at must not be written when SourceRecord.fetched_at is None"
        )

    def test_fetched_at_not_written_for_other_source_fields_absent(self) -> None:
        """fetched_at is independent of other optional source fields."""
        service, db = _make_service()
        fetched = "2026-03-01T08:30:00+00:00"
        sources = [
            SourceRecord(
                source_kind="vendor_fundamentals",
                provider_name="market_data",
                fetched_at=fetched,
                source_url=None,   # other optional fields absent
                source_hash=None,
            )
        ]
        service.write_artifact(_make_output(sources=sources, key_suffix="fa3"))

        inserted_sources = db.inserts.get("research_artifact_sources", [])
        assert inserted_sources[0].get("fetched_at") == fetched
        assert "source_url" not in inserted_sources[0]
        assert "source_hash" not in inserted_sources[0]


# ── Tests: SQL migration 023 content ─────────────────────────────────────────

class TestMigration023Content:
    def _load_sql(self) -> str:
        import pathlib
        sql_path = (
            pathlib.Path(__file__).parent.parent.parent
            / "database"
            / "023_research_artifact_store_stage5a_extend.sql"
        )
        return sql_path.read_text()

    def test_migration_023_contains_active_lane_uniqueness_index(self) -> None:
        """Migration 023 must declare the active evidence-lane uniqueness index."""
        content = self._load_sql()
        assert "uq_research_artifacts_active_lane" in content
        assert "COALESCE" in content
        assert "is_active = TRUE" in content or "is_active=TRUE" in content

    def test_migration_023_contains_user_scoped_replay_index(self) -> None:
        """Migration 023 must declare the user-scoped replay idempotency index."""
        content = self._load_sql()
        assert "uq_research_artifacts_replay_user_active" in content
        assert "user_id" in content

    def test_migration_023_drops_global_replay_index(self) -> None:
        """Migration 023 must drop the global replay index from 017."""
        content = self._load_sql()
        assert "uq_research_artifacts_replay_active" in content
        assert "DROP INDEX" in content

    def test_migration_023_contains_duplicate_guard(self) -> None:
        """Migration 023 must have a loud-fail duplicate guard before creating the lane index."""
        content = self._load_sql()
        assert "RAISE EXCEPTION" in content
        assert "duplicate" in content.lower() or "unique_violation" in content
