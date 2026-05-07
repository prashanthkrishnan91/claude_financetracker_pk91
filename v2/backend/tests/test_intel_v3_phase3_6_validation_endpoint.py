"""Phase 3.6 validation endpoint tests.

Covers all 15 acceptance criteria from the Phase 3.6 task spec:

  1.  Endpoint/callable is disabled by default (finance_runtime_cert_enabled=False → 404).
  2.  Unauthorized request (no secret header) is rejected.
  3.  Wrong/missing secret is rejected (403).
  4.  Runtime cert gate off is rejected (404).
  5.  Validation flag off is rejected (403).
  6.  Global worker flag off is rejected (403).
  7.  Earnings Reviewer flag off is rejected (403).
  8.  With all gates enabled and valid secret, calls run_validation().
  9.  Tickers capped to 3 at the endpoint layer (max_tickers=3 passed to harness).
  10. Response does not include raw payload/fact/source data.
  11. No decision_policy_v1.py / decide() import or call in endpoint module.
  12. No recommendation_engine / get_insight_cards / _compute_insight_cards import
      or call in endpoint or validation harness.
  13. No writes to intel_v3_snapshots when the endpoint runs.
  14. DB/write failures return safe summary (200 with errors), not 500.
  15. Existing Phase 3 and Phase 3.5 test suites still pass (run separately).

No production Supabase dependency — uses mocks/FakeSupabaseClient throughout.
"""
from __future__ import annotations

import importlib
import inspect
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ── FakeSupabaseClient ────────────────────────────────────────────────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)


class FakeTableQuery:
    def __init__(self, state: _TableState, return_id: Optional[str] = None) -> None:
        self._state = state
        self._return_id = return_id or str(uuid.uuid4())
        self._row: Optional[dict] = None
        self._on_conflict: Optional[str] = None
        self._ignore_duplicates: bool = False
        self._filters: dict = {}

    def insert(self, row: dict) -> "FakeTableQuery":
        self._row = row
        return self

    def upsert(self, row: dict, *, on_conflict: str = "", ignore_duplicates: bool = False) -> "FakeTableQuery":
        self._row = row
        self._on_conflict = on_conflict
        self._ignore_duplicates = ignore_duplicates
        return self

    def select(self, cols: str = "*") -> "FakeTableQuery":
        return self

    def eq(self, col: str, val: Any) -> "FakeTableQuery":
        self._filters[col] = val
        return self

    def order(self, *args, **kwargs) -> "FakeTableQuery":
        return self

    def limit(self, n: int) -> "FakeTableQuery":
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

        class _EmptyResult:
            data = []
        return _EmptyResult()


class FakeSupabaseClient:
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

def _cert_disabled_settings() -> SimpleNamespace:
    return SimpleNamespace(
        finance_runtime_cert_enabled=False,
        finance_runtime_cert_secret=None,
        finance_runtime_cert_user_id=None,
        finance_runtime_cert_user_email=None,
        intel_v3_research_worker_validation_enabled=False,
        intel_v3_research_workers_enabled=False,
        intel_v3_earnings_reviewer_enabled=False,
        intel_v3_research_worker_validation_info_logs_enabled=False,
    )


def _cert_enabled_no_flags() -> SimpleNamespace:
    """Cert gate open, all Phase 3/3.5 flags off."""
    return SimpleNamespace(
        finance_runtime_cert_enabled=True,
        finance_runtime_cert_secret="test-secret-xyz",
        finance_runtime_cert_user_id=str(uuid.uuid4()),
        finance_runtime_cert_user_email="cert@example.com",
        intel_v3_research_worker_validation_enabled=False,
        intel_v3_research_workers_enabled=False,
        intel_v3_earnings_reviewer_enabled=False,
        intel_v3_research_worker_validation_info_logs_enabled=False,
    )


def _all_gates_on() -> SimpleNamespace:
    """All gates open — endpoint should proceed."""
    return SimpleNamespace(
        finance_runtime_cert_enabled=True,
        finance_runtime_cert_secret="test-secret-xyz",
        finance_runtime_cert_user_id=str(uuid.uuid4()),
        finance_runtime_cert_user_email="cert@example.com",
        intel_v3_research_worker_validation_enabled=True,
        intel_v3_research_workers_enabled=True,
        intel_v3_earnings_reviewer_enabled=True,
        intel_v3_research_worker_validation_info_logs_enabled=False,
    )


def _valid_user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


# ── Helper to build a fake ValidationSummary ─────────────────────────────────

def _fake_summary(
    written: int = 1,
    errors: Optional[list[str]] = None,
) -> Any:
    from app.services.intelligence.research_workers.validation_harness import ValidationSummary
    return ValidationSummary(
        validation_enabled=True,
        requested_tickers=["AAPL"],
        normalized_tickers=["AAPL"],
        attempted_count=1,
        written_count=written,
        skipped_count=0,
        failed_count=0,
        artifact_ids=["fake-artifact-id"],
        safe_for_decision_false_count=written,
        unexpected_safe_for_decision_true_count=0,
        forbidden_payload_violation_count=0,
        visible_snapshot_unchanged=True,
        tables_touched=["research_artifacts", "worker_audit_events"],
        worker_run_ids=[],
        errors=errors or [],
    )


# ════════════════════════════════════════════════════════════════════════════
# Criterion 1 & 4: Endpoint disabled / cert gate off → 404
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36CertGateDisabled:
    """Criteria 1, 4: finance_runtime_cert_enabled=False → 404."""

    @pytest.mark.asyncio
    async def test_cert_disabled_raises_404(self, monkeypatch):
        from app.routers.diagnostics import _get_runtime_cert_user
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _cert_disabled_settings())
        with pytest.raises(HTTPException) as exc:
            await _get_runtime_cert_user(
                request=SimpleNamespace(headers={}),
                cert_secret=None,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cert_disabled_with_secret_still_404(self, monkeypatch):
        """Even providing a secret does not help if the gate is off."""
        from app.routers.diagnostics import _get_runtime_cert_user
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _cert_disabled_settings())
        with pytest.raises(HTTPException) as exc:
            await _get_runtime_cert_user(
                request=SimpleNamespace(headers={}),
                cert_secret="test-secret-xyz",
            )
        assert exc.value.status_code == 404

    def test_endpoint_uses_runtime_cert_user_dependency(self):
        """Verify the endpoint declares _get_runtime_cert_user as its auth dependency."""
        from app.routers.diagnostics import _get_runtime_cert_user, validate_research_workers_dark_run
        sig = inspect.signature(validate_research_workers_dark_run)
        user_param = sig.parameters.get("user")
        assert user_param is not None, "endpoint must have a 'user' parameter"
        dep = user_param.default
        # FastAPI Depends objects expose the dependency callable
        assert dep.dependency is _get_runtime_cert_user, (
            "user param must depend on _get_runtime_cert_user"
        )


# ════════════════════════════════════════════════════════════════════════════
# Criteria 2 & 3: Missing or wrong secret → 403
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36SecretRejection:
    """Criteria 2, 3: missing or wrong X-Finance-Runtime-Cert-Secret → 403."""

    @pytest.mark.asyncio
    async def test_missing_secret_header_rejected(self, monkeypatch):
        from app.routers.diagnostics import _get_runtime_cert_user
        monkeypatch.setattr(
            "app.routers.diagnostics.get_settings",
            lambda: SimpleNamespace(
                finance_runtime_cert_enabled=True,
                finance_runtime_cert_secret="correct-secret",
                finance_runtime_cert_user_id=str(uuid.uuid4()),
                finance_runtime_cert_user_email="cert@example.com",
            ),
        )
        with pytest.raises(HTTPException) as exc:
            await _get_runtime_cert_user(
                request=SimpleNamespace(headers={}),
                cert_secret=None,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_wrong_secret_rejected(self, monkeypatch):
        from app.routers.diagnostics import _get_runtime_cert_user
        monkeypatch.setattr(
            "app.routers.diagnostics.get_settings",
            lambda: SimpleNamespace(
                finance_runtime_cert_enabled=True,
                finance_runtime_cert_secret="correct-secret",
                finance_runtime_cert_user_id=str(uuid.uuid4()),
                finance_runtime_cert_user_email="cert@example.com",
            ),
        )
        with pytest.raises(HTTPException) as exc:
            await _get_runtime_cert_user(
                request=SimpleNamespace(headers={}),
                cert_secret="wrong-secret",
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_string_secret_rejected(self, monkeypatch):
        from app.routers.diagnostics import _get_runtime_cert_user
        monkeypatch.setattr(
            "app.routers.diagnostics.get_settings",
            lambda: SimpleNamespace(
                finance_runtime_cert_enabled=True,
                finance_runtime_cert_secret="correct-secret",
                finance_runtime_cert_user_id=str(uuid.uuid4()),
                finance_runtime_cert_user_email="cert@example.com",
            ),
        )
        with pytest.raises(HTTPException) as exc:
            await _get_runtime_cert_user(
                request=SimpleNamespace(headers={}),
                cert_secret="",
            )
        assert exc.value.status_code == 403


# ════════════════════════════════════════════════════════════════════════════
# Criterion 5: Validation flag off → 403
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36ValidationFlagGate:
    """Criterion 5: intel_v3_research_worker_validation_enabled=False → 403."""

    @pytest.mark.asyncio
    async def test_validation_flag_off_rejected(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        settings = _cert_enabled_no_flags()
        settings.intel_v3_research_worker_validation_enabled = False
        settings.intel_v3_research_workers_enabled = True
        settings.intel_v3_earnings_reviewer_enabled = True
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)
        with pytest.raises(HTTPException) as exc:
            await validate_research_workers_dark_run(
                payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
                user=_valid_user(),
            )
        assert exc.value.status_code == 403
        assert "INTEL_V3_RESEARCH_WORKER_VALIDATION_ENABLED" in exc.value.detail

    @pytest.mark.asyncio
    async def test_validation_flag_off_does_not_call_harness(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        settings = _cert_enabled_no_flags()
        settings.intel_v3_research_worker_validation_enabled = False
        settings.intel_v3_research_workers_enabled = True
        settings.intel_v3_earnings_reviewer_enabled = True
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)
        called = []
        monkeypatch.setattr(
            "app.routers.diagnostics.run_validation",
            lambda **kw: called.append(kw),
        )
        with pytest.raises(HTTPException):
            await validate_research_workers_dark_run(
                payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
                user=_valid_user(),
            )
        assert called == [], "run_validation must not be called when flag is off"


# ════════════════════════════════════════════════════════════════════════════
# Criterion 6: Global worker flag off → 403
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36GlobalWorkerFlagGate:
    """Criterion 6: intel_v3_research_workers_enabled=False → 403."""

    @pytest.mark.asyncio
    async def test_global_worker_flag_off_rejected(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        settings = _cert_enabled_no_flags()
        settings.intel_v3_research_worker_validation_enabled = True
        settings.intel_v3_research_workers_enabled = False
        settings.intel_v3_earnings_reviewer_enabled = True
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)
        with pytest.raises(HTTPException) as exc:
            await validate_research_workers_dark_run(
                payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
                user=_valid_user(),
            )
        assert exc.value.status_code == 403
        assert "INTEL_V3_RESEARCH_WORKERS_ENABLED" in exc.value.detail

    @pytest.mark.asyncio
    async def test_global_worker_flag_off_does_not_call_harness(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        settings = _cert_enabled_no_flags()
        settings.intel_v3_research_worker_validation_enabled = True
        settings.intel_v3_research_workers_enabled = False
        settings.intel_v3_earnings_reviewer_enabled = True
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)
        called = []
        monkeypatch.setattr(
            "app.routers.diagnostics.run_validation",
            lambda **kw: called.append(kw),
        )
        with pytest.raises(HTTPException):
            await validate_research_workers_dark_run(
                payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
                user=_valid_user(),
            )
        assert called == []


# ════════════════════════════════════════════════════════════════════════════
# Criterion 7: Earnings Reviewer flag off → 403
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36EarningsReviewerFlagGate:
    """Criterion 7: intel_v3_earnings_reviewer_enabled=False → 403."""

    @pytest.mark.asyncio
    async def test_earnings_reviewer_flag_off_rejected(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        settings = _cert_enabled_no_flags()
        settings.intel_v3_research_worker_validation_enabled = True
        settings.intel_v3_research_workers_enabled = True
        settings.intel_v3_earnings_reviewer_enabled = False
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)
        with pytest.raises(HTTPException) as exc:
            await validate_research_workers_dark_run(
                payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
                user=_valid_user(),
            )
        assert exc.value.status_code == 403
        assert "INTEL_V3_EARNINGS_REVIEWER_ENABLED" in exc.value.detail

    @pytest.mark.asyncio
    async def test_all_flags_off_still_rejected(self, monkeypatch):
        """When all three Phase 3/3.5 flags are off, first missing flag wins."""
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _cert_enabled_no_flags())
        with pytest.raises(HTTPException) as exc:
            await validate_research_workers_dark_run(
                payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
                user=_valid_user(),
            )
        assert exc.value.status_code == 403


# ════════════════════════════════════════════════════════════════════════════
# Criterion 8: All gates on → calls run_validation()
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36AllGatesEnabled:
    """Criterion 8: all gates enabled + valid secret → run_validation() called."""

    @pytest.mark.asyncio
    async def test_calls_run_validation_when_all_gates_on(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        settings = _all_gates_on()
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: FakeSupabaseClient())
        captured: list[dict] = []

        def _fake_run_validation(**kwargs):
            captured.append(kwargs)
            return _fake_summary()

        monkeypatch.setattr("app.routers.diagnostics.run_validation", _fake_run_validation)
        result = await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
            user=_valid_user(),
        )
        assert len(captured) == 1, "run_validation must be called exactly once"
        assert "user_id" in captured[0]
        assert "tickers" in captured[0]
        assert result["attempted_count"] == 1

    @pytest.mark.asyncio
    async def test_user_id_passed_to_run_validation(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _all_gates_on())
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: FakeSupabaseClient())
        user = _valid_user()
        captured: list[dict] = []

        def _fake_run_validation(**kwargs):
            captured.append(kwargs)
            return _fake_summary()

        monkeypatch.setattr("app.routers.diagnostics.run_validation", _fake_run_validation)
        await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
            user=user,
        )
        assert captured[0]["user_id"] == str(user.id)


# ════════════════════════════════════════════════════════════════════════════
# Criterion 9: Ticker cap of 3 at endpoint layer
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36TickerCap:
    """Criterion 9: tickers capped to 3 at endpoint layer (max_tickers=3)."""

    @pytest.mark.asyncio
    async def test_max_tickers_3_passed_to_harness(self, monkeypatch):
        from app.routers.diagnostics import (
            MAX_VALIDATE_TICKERS_PER_REQUEST,
            ResearchWorkersValidateRequest,
            validate_research_workers_dark_run,
        )
        assert MAX_VALIDATE_TICKERS_PER_REQUEST == 3, "endpoint cap must be 3"
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _all_gates_on())
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: FakeSupabaseClient())
        captured: list[dict] = []

        def _fake_run_validation(**kwargs):
            captured.append(kwargs)
            return _fake_summary()

        monkeypatch.setattr("app.routers.diagnostics.run_validation", _fake_run_validation)
        await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(tickers=["AAPL", "MSFT", "NVDA", "GOOG", "AMZN"]),
            user=_valid_user(),
        )
        assert captured[0]["max_tickers"] == 3

    @pytest.mark.asyncio
    async def test_endpoint_enforces_cap_even_with_many_tickers(self, monkeypatch):
        """Providing 10 tickers: harness caps to 3 because max_tickers=3 passed."""
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        from app.services.intelligence.research_workers.validation_harness import ValidationSummary
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _all_gates_on())
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: FakeSupabaseClient())

        # Let run_validation actually execute (with FakeSupabaseClient) to confirm cap
        monkeypatch.setattr(
            "app.routers.diagnostics.get_settings",
            lambda: _all_gates_on(),
        )
        from app.config import Settings
        settings_obj = Settings(
            supabase_url="http://fake",
            supabase_anon_key="fake",
            supabase_service_role_key="fake",
            supabase_jwt_secret="fake",
            encryption_key="fake",
            intel_v3_research_worker_validation_enabled=True,
            intel_v3_research_workers_enabled=True,
            intel_v3_earnings_reviewer_enabled=True,
        )
        fake_client = FakeSupabaseClient()

        from app.services.intelligence.research_workers.validation_harness import run_validation as real_rv

        def _real_run_validation(**kwargs):
            kwargs["db_client"] = fake_client
            kwargs["settings"] = settings_obj
            return real_rv(**kwargs)

        monkeypatch.setattr("app.routers.diagnostics.run_validation", _real_run_validation)
        result = await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(
                tickers=["AAPL", "MSFT", "NVDA", "GOOG", "AMZN", "META", "TSLA", "NFLX", "AMD", "INTC"]
            ),
            user=_valid_user(),
        )
        # harness normalized and capped to 3
        assert result["attempted_count"] <= 3
        assert len(result["normalized_tickers"]) <= 3


# ════════════════════════════════════════════════════════════════════════════
# Criterion 10: Response does not include raw payload/fact/source data
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36ResponseShape:
    """Criterion 10: response keys are exactly the allowed compact summary set."""

    ALLOWED_KEYS = {
        "requested_tickers",
        "normalized_tickers",
        "attempted_count",
        "written_count",
        "skipped_count",
        "failed_count",
        "artifact_ids",
        "safe_for_decision_false_count",
        "unexpected_safe_for_decision_true_count",
        "forbidden_payload_violation_count",
        "visible_snapshot_unchanged",
        "errors",
        "tables_touched",
    }

    FORBIDDEN_RESPONSE_KEYS = {
        "artifact_payload",
        "facts_payload",
        "facts",
        "sources",
        "quote_or_excerpt",
        "structured_payload",
        "raw_db_rows",
        "user_secrets",
        "source_excerpts",
        "payload",
        "holding_context",
    }

    @pytest.mark.asyncio
    async def test_response_contains_only_allowed_keys(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _all_gates_on())
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: FakeSupabaseClient())
        monkeypatch.setattr("app.routers.diagnostics.run_validation", lambda **kw: _fake_summary())
        result = await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
            user=_valid_user(),
        )
        assert isinstance(result, dict)
        assert set(result.keys()) == self.ALLOWED_KEYS, (
            f"Unexpected keys: {set(result.keys()) - self.ALLOWED_KEYS}; "
            f"Missing keys: {self.ALLOWED_KEYS - set(result.keys())}"
        )

    @pytest.mark.asyncio
    async def test_response_does_not_contain_forbidden_keys(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _all_gates_on())
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: FakeSupabaseClient())
        monkeypatch.setattr("app.routers.diagnostics.run_validation", lambda **kw: _fake_summary())
        result = await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
            user=_valid_user(),
        )
        for key in self.FORBIDDEN_RESPONSE_KEYS:
            assert key not in result, f"Forbidden key '{key}' must not appear in response"


# ════════════════════════════════════════════════════════════════════════════
# Criterion 11: No decision_policy_v1 / decide() import or call
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36NoDecideDependency:
    """Criterion 11: diagnostics.py and validation_harness.py must not import or call decide()."""

    def _ast_decide_calls(self, src: str) -> list:
        """Return list of AST Call nodes calling 'decide'."""
        import ast
        tree = ast.parse(src)
        return [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "decide")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "decide")
            )
        ]

    def test_diagnostics_module_does_not_call_decide(self):
        """AST check: no call to decide() in diagnostics.py."""
        import app.routers.diagnostics as diag_mod
        src = inspect.getsource(diag_mod)
        assert self._ast_decide_calls(src) == [], (
            "diagnostics.py must not call decide()"
        )

    def test_diagnostics_module_does_not_import_decision_policy(self):
        """No import of decision_policy_v1 in diagnostics.py."""
        import re
        import app.routers.diagnostics as diag_mod
        src = inspect.getsource(diag_mod)
        assert not re.search(r"^\s*(import|from)\s+.*decision_policy_v1", src, re.MULTILINE), (
            "diagnostics.py must not import decision_policy_v1"
        )

    def test_validation_harness_does_not_call_decide(self):
        """AST check: no call to decide() in validation_harness.py."""
        import app.services.intelligence.research_workers.validation_harness as harness_mod
        src = inspect.getsource(harness_mod)
        assert self._ast_decide_calls(src) == [], (
            "validation_harness.py must not call decide()"
        )

    def test_validation_harness_does_not_import_decision_policy(self):
        """No import of decision_policy_v1 in validation_harness.py."""
        import re
        import app.services.intelligence.research_workers.validation_harness as harness_mod
        src = inspect.getsource(harness_mod)
        assert not re.search(r"^\s*(import|from)\s+.*decision_policy_v1", src, re.MULTILINE), (
            "validation_harness.py must not import decision_policy_v1"
        )

    def test_diagnostics_module_imports_do_not_include_decide(self):
        diag = importlib.import_module("app.routers.diagnostics")
        for name in dir(diag):
            if name == "decide":
                obj = getattr(diag, name)
                assert not callable(obj) or not inspect.isfunction(obj), (
                    "decide() must not be importable from diagnostics module"
                )


# ════════════════════════════════════════════════════════════════════════════
# Criterion 12: No recommendation_engine / get_insight_cards import or call
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36NoRecommendationEngineInHarness:
    """Criterion 12: validation_harness.py must not import/call recommendation_engine."""

    def test_validation_harness_does_not_import_recommendation_engine(self):
        import app.services.intelligence.research_workers.validation_harness as harness_mod
        src = inspect.getsource(harness_mod)
        assert "recommendation_engine" not in src
        assert "get_insight_cards" not in src
        assert "_compute_insight_cards" not in src

    def test_validation_harness_does_not_import_intel_v3_service(self):
        import app.services.intelligence.research_workers.validation_harness as harness_mod
        src = inspect.getsource(harness_mod)
        assert "IntelV3Service" not in src

    def test_endpoint_does_not_call_get_insight_cards(self):
        import app.routers.diagnostics as diag_mod
        src = inspect.getsource(diag_mod)
        # The endpoint may import RecommendationService for existing /certify,
        # but the validate endpoint must not call get_insight_cards or _compute_insight_cards
        validate_fn_src = inspect.getsource(diag_mod.validate_research_workers_dark_run)
        assert "get_insight_cards" not in validate_fn_src
        assert "_compute_insight_cards" not in validate_fn_src


# ════════════════════════════════════════════════════════════════════════════
# Criterion 13: No writes to intel_v3_snapshots
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36NoSnapshotWrites:
    """Criterion 13: endpoint must not write to intel_v3_snapshots."""

    @pytest.mark.asyncio
    async def test_no_intel_v3_snapshots_writes(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        from app.config import Settings
        settings_obj = Settings(
            supabase_url="http://fake",
            supabase_anon_key="fake",
            supabase_service_role_key="fake",
            supabase_jwt_secret="fake",
            encryption_key="fake",
            intel_v3_research_worker_validation_enabled=True,
            intel_v3_research_workers_enabled=True,
            intel_v3_earnings_reviewer_enabled=True,
        )
        fake_client = FakeSupabaseClient()
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _all_gates_on())
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: fake_client)

        from app.services.intelligence.research_workers.validation_harness import run_validation as real_rv

        def _run_with_real_settings(**kwargs):
            kwargs["db_client"] = fake_client
            kwargs["settings"] = settings_obj
            return real_rv(**kwargs)

        monkeypatch.setattr("app.routers.diagnostics.run_validation", _run_with_real_settings)
        await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
            user=_valid_user(),
        )
        assert fake_client.snapshot_writes() == [], (
            "intel_v3_snapshots must not be written during dark-run validation"
        )

    @pytest.mark.asyncio
    async def test_visible_snapshot_unchanged_is_true(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _all_gates_on())
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: FakeSupabaseClient())
        monkeypatch.setattr("app.routers.diagnostics.run_validation", lambda **kw: _fake_summary())
        result = await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
            user=_valid_user(),
        )
        assert result["visible_snapshot_unchanged"] is True


# ════════════════════════════════════════════════════════════════════════════
# Criterion 14: DB/write failures return safe summary, not 500
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36DBFailureSafety:
    """Criterion 14: when run_validation returns errors, endpoint returns 200 with summary."""

    @pytest.mark.asyncio
    async def test_harness_errors_return_200_not_500(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        from app.services.intelligence.research_workers.validation_harness import ValidationSummary
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _all_gates_on())
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: FakeSupabaseClient())
        error_summary = ValidationSummary(
            validation_enabled=True,
            requested_tickers=["AAPL"],
            normalized_tickers=["AAPL"],
            attempted_count=1,
            written_count=0,
            skipped_count=0,
            failed_count=1,
            artifact_ids=[],
            safe_for_decision_false_count=0,
            unexpected_safe_for_decision_true_count=0,
            forbidden_payload_violation_count=0,
            visible_snapshot_unchanged=True,
            tables_touched=[],
            worker_run_ids=[],
            errors=["write_error ticker=AAPL error=Connection refused"],
        )
        monkeypatch.setattr("app.routers.diagnostics.run_validation", lambda **kw: error_summary)
        # Should return a dict (200), not raise HTTPException (500)
        result = await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
            user=_valid_user(),
        )
        assert isinstance(result, dict)
        assert result["failed_count"] == 1
        assert result["written_count"] == 0
        assert len(result["errors"]) == 1
        assert "write_error" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_all_failures_still_returns_valid_summary_shape(self, monkeypatch):
        """Even a total failure scenario returns the compact summary shape."""
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        from app.services.intelligence.research_workers.validation_harness import ValidationSummary
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _all_gates_on())
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: FakeSupabaseClient())
        failure_summary = ValidationSummary(
            validation_enabled=True,
            requested_tickers=["AAPL", "MSFT"],
            normalized_tickers=["AAPL", "MSFT"],
            attempted_count=2,
            written_count=0,
            skipped_count=0,
            failed_count=2,
            artifact_ids=[],
            safe_for_decision_false_count=0,
            unexpected_safe_for_decision_true_count=0,
            forbidden_payload_violation_count=0,
            visible_snapshot_unchanged=True,
            tables_touched=[],
            worker_run_ids=[],
            errors=["write_error ticker=AAPL", "write_error ticker=MSFT"],
        )
        monkeypatch.setattr("app.routers.diagnostics.run_validation", lambda **kw: failure_summary)
        result = await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(tickers=["AAPL", "MSFT"]),
            user=_valid_user(),
        )
        assert result["attempted_count"] == 2
        assert result["written_count"] == 0
        assert result["failed_count"] == 2
        assert result["visible_snapshot_unchanged"] is True
        assert len(result["errors"]) == 2


# ════════════════════════════════════════════════════════════════════════════
# Additional: endpoint constant and module-level guard
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36Constants:

    def test_max_validate_tickers_constant_is_3(self):
        from app.routers.diagnostics import MAX_VALIDATE_TICKERS_PER_REQUEST
        assert MAX_VALIDATE_TICKERS_PER_REQUEST == 3

    def test_endpoint_function_is_async(self):
        from app.routers.diagnostics import validate_research_workers_dark_run
        assert inspect.iscoroutinefunction(validate_research_workers_dark_run), (
            "validate_research_workers_dark_run must be an async function"
        )

    def test_endpoint_is_not_imported_from_intel_v3_router(self):
        """Verify the new endpoint did not land in intel_v3.py (wrong module)."""
        import app.routers.intel_v3 as intel_v3_mod
        src = inspect.getsource(intel_v3_mod)
        assert "research-workers/validate" not in src
        assert "validate_research_workers_dark_run" not in src

    def test_run_validation_is_imported_in_diagnostics(self):
        """Verify run_validation is the harness function, not something else."""
        from app.routers.diagnostics import run_validation
        from app.services.intelligence.research_workers.validation_harness import run_validation as harness_rv
        assert run_validation is harness_rv


# ════════════════════════════════════════════════════════════════════════════
# Additional: safe_for_decision never set to True
# ════════════════════════════════════════════════════════════════════════════

class TestPhase36SafeForDecisionInvariant:
    """safe_for_decision must never be true in any artifact written."""

    @pytest.mark.asyncio
    async def test_unexpected_safe_true_count_is_zero_on_success(self, monkeypatch):
        from app.routers.diagnostics import ResearchWorkersValidateRequest, validate_research_workers_dark_run
        monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _all_gates_on())
        monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: FakeSupabaseClient())
        monkeypatch.setattr("app.routers.diagnostics.run_validation", lambda **kw: _fake_summary())
        result = await validate_research_workers_dark_run(
            payload=ResearchWorkersValidateRequest(tickers=["AAPL"]),
            user=_valid_user(),
        )
        assert result["unexpected_safe_for_decision_true_count"] == 0
        assert result["safe_for_decision_false_count"] == result["written_count"]
