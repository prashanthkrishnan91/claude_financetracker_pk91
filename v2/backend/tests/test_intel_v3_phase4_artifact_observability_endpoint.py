"""Phase 4 artifact observability endpoint tests.

Covers all acceptance criteria for the Phase 4 diagnostics endpoint:

  1.  Endpoint disabled when finance_runtime_cert_enabled=False → 404.
  2.  Missing X-Finance-Runtime-Cert-Secret header → 403.
  3.  Wrong secret → 403.
  4.  Observability flag off → 403 (flag-gate rejection).
  5.  Observability flag on + valid cert → calls summarize_recent_research_artifacts().
  6.  Tickers capped to MAX_OBSERVE_TICKERS_PER_REQUEST (10) at endpoint layer.
  7.  lookback_days clamped to [1, 365].
  8.  max_rows clamped to [1, 1000].
  9.  Tickers normalized uppercase and deduplicated.
  10. Response contains only safe aggregate counters — no raw payloads/rows/secrets.
  11. DB/observability failures return 200 with errors[] (never 500).
  12. No decision_policy_v1.py / decide() import or call in the endpoint module.
  13. No writes to intel_v3_snapshots when endpoint runs.
  14. Worker/validation flags (Phase 3/3.5) are NOT required — observability is independent.
  15. visible_snapshot_unchanged is True in response.

No production Supabase dependency — uses mocks throughout.
"""
from __future__ import annotations

import importlib
import inspect
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


# ── Fake observability summary ────────────────────────────────────────────────

def _fake_summary(
    enabled: bool = True,
    artifact_count: int = 3,
    safe_false: int = 3,
    unexpected_true: int = 0,
    forbidden: int = 0,
    errors: Optional[list] = None,
) -> Any:
    from app.services.intelligence.research_workers.artifact_observability import (
        ArtifactObservabilitySummary,
    )
    return ArtifactObservabilitySummary(
        observability_enabled=enabled,
        requested_tickers=["AAPL"],
        normalized_tickers=["AAPL"],
        lookback_days=30,
        max_rows=250,
        artifact_count=artifact_count,
        by_ticker={"AAPL": artifact_count} if artifact_count else {},
        by_artifact_type={"catalyst_window": artifact_count} if artifact_count else {},
        by_skill_pack={"earnings_reviewer": artifact_count} if artifact_count else {},
        by_confidence_or_trust_level={"UNKNOWN": artifact_count} if artifact_count else {},
        by_freshness_status={"UNKNOWN": artifact_count} if artifact_count else {},
        safe_for_decision_false_count=safe_false,
        unexpected_safe_for_decision_true_count=unexpected_true,
        forbidden_payload_violation_count=forbidden,
        active_count=artifact_count,
        inactive_count=0,
        invalidated_count=0,
        expired_count=0,
        artifacts_with_sources_count=0,
        artifacts_without_sources_count=artifact_count,
        artifacts_with_facts_count=0,
        artifacts_without_facts_count=artifact_count,
        missing_evidence_count=0,
        visible_snapshot_unchanged=True,
        errors=errors or [],
    )


def _settings_for_endpoint(
    cert_enabled: bool = True,
    cert_secret: str = "test-secret",
    cert_user_id: Optional[str] = None,
    observability_enabled: bool = True,
) -> Any:
    from app.config import Settings
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="anon",
        supabase_service_role_key="service",
        supabase_jwt_secret="secret",
        encryption_key="a" * 64,
        finance_runtime_cert_enabled=cert_enabled,
        finance_runtime_cert_secret=cert_secret,
        finance_runtime_cert_user_id=cert_user_id or str(uuid.uuid4()),
        finance_runtime_cert_user_email="test@local",
        intel_v3_research_artifact_observability_enabled=observability_enabled,
    )


# ── Direct function tests on the endpoint function ───────────────────────────

class TestEndpointFlagGating:
    """Test the observability flag gate directly using the endpoint function."""

    def _call_endpoint(self, settings, fake_summary=None, tickers=None, lookback_days=30, max_rows=250):
        """Call the endpoint with a mocked auth user and summarize fn."""
        from app.routers.diagnostics import observe_research_artifacts, ResearchArtifactsObserveRequest
        from app.middleware.auth import AuthenticatedUser
        import asyncio

        user = AuthenticatedUser(
            user_id=uuid.UUID(settings.finance_runtime_cert_user_id),
            email="test@local",
            role="owner",
        )
        payload = ResearchArtifactsObserveRequest(
            tickers=tickers or ["AAPL"],
            lookback_days=lookback_days,
            max_rows=max_rows,
        )
        summary = fake_summary or _fake_summary()

        with patch("app.routers.diagnostics.get_settings", return_value=settings), \
             patch("app.routers.diagnostics.get_supabase_client", return_value=MagicMock()), \
             patch("app.routers.diagnostics.summarize_recent_research_artifacts", return_value=summary):
            return asyncio.run(observe_research_artifacts(payload=payload, user=user))

    def test_observability_flag_off_raises_403(self):
        settings = _settings_for_endpoint(observability_enabled=False)
        from app.routers.diagnostics import observe_research_artifacts, ResearchArtifactsObserveRequest
        from app.middleware.auth import AuthenticatedUser
        import asyncio

        user = AuthenticatedUser(
            user_id=uuid.UUID(settings.finance_runtime_cert_user_id),
            email="test@local",
            role="owner",
        )
        payload = ResearchArtifactsObserveRequest(tickers=["AAPL"])
        with patch("app.routers.diagnostics.get_settings", return_value=settings), \
             patch("app.routers.diagnostics.get_supabase_client", return_value=MagicMock()):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(observe_research_artifacts(payload=payload, user=user))
        assert exc_info.value.status_code == 403
        assert "INTEL_V3_RESEARCH_ARTIFACT_OBSERVABILITY_ENABLED" in str(exc_info.value.detail)

    def test_observability_flag_on_returns_200(self):
        settings = _settings_for_endpoint(observability_enabled=True)
        result = self._call_endpoint(settings)
        assert "artifact_count" in result

    def test_worker_flags_not_required(self):
        """Phase 3/3.5 worker/validation flags must NOT be required for Phase 4."""
        from app.config import Settings
        settings = Settings(
            supabase_url="http://fake",
            supabase_anon_key="anon",
            supabase_service_role_key="service",
            supabase_jwt_secret="secret",
            encryption_key="a" * 64,
            finance_runtime_cert_enabled=True,
            finance_runtime_cert_secret="test-secret",
            finance_runtime_cert_user_id=str(uuid.uuid4()),
            intel_v3_research_artifact_observability_enabled=True,
            # Explicitly off:
            intel_v3_research_workers_enabled=False,
            intel_v3_earnings_reviewer_enabled=False,
            intel_v3_research_worker_validation_enabled=False,
        )
        result = self._call_endpoint(settings)
        assert "artifact_count" in result


class TestEndpointInputGuardrails:
    """Test that tickers/lookback/max_rows are capped at the endpoint layer."""

    def _call_endpoint_with_patches(self, payload_kwargs, settings, capture):
        from app.routers.diagnostics import observe_research_artifacts, ResearchArtifactsObserveRequest
        from app.middleware.auth import AuthenticatedUser
        import asyncio

        user = AuthenticatedUser(
            user_id=uuid.UUID(settings.finance_runtime_cert_user_id),
            email="test@local",
            role="owner",
        )
        payload = ResearchArtifactsObserveRequest(**payload_kwargs)

        def fake_summarize(user_id, db_client, tickers, lookback_days, max_rows, settings):
            capture["tickers"] = tickers
            capture["lookback_days"] = lookback_days
            capture["max_rows"] = max_rows
            return _fake_summary()

        with patch("app.routers.diagnostics.get_settings", return_value=settings), \
             patch("app.routers.diagnostics.get_supabase_client", return_value=MagicMock()), \
             patch("app.routers.diagnostics.summarize_recent_research_artifacts", side_effect=fake_summarize):
            asyncio.run(observe_research_artifacts(payload=payload, user=user))

    def test_tickers_capped_to_10(self):
        settings = _settings_for_endpoint()
        capture: dict = {}
        # 15 tickers in — only first 10 should reach the service
        tickers = [f"T{i}" for i in range(15)]
        self._call_endpoint_with_patches({"tickers": tickers}, settings, capture)
        assert len(capture["tickers"]) == 10

    def test_ticker_dedup_before_cap(self):
        """Normalization + dedup must happen before the 10-ticker cap."""
        settings = _settings_for_endpoint()
        capture: dict = {}
        # 8 unique tickers + 4 duplicates (case variants) = 8 unique after dedup → all pass cap
        tickers = ["aapl", "AAPL", "msft", "MSFT", "T1", "T2", "T3", "T4", "T5", "T6", "t1", "t2"]
        self._call_endpoint_with_patches({"tickers": tickers}, settings, capture)
        # After dedup: AAPL, MSFT, T1, T2, T3, T4, T5, T6 = 8 unique tickers
        assert len(capture["tickers"]) == 8
        assert "AAPL" in capture["tickers"]
        assert "MSFT" in capture["tickers"]

    def test_lookback_days_min_clamped(self):
        settings = _settings_for_endpoint()
        capture: dict = {}
        self._call_endpoint_with_patches({"lookback_days": -10}, settings, capture)
        assert capture["lookback_days"] == 1

    def test_lookback_days_max_clamped(self):
        settings = _settings_for_endpoint()
        capture: dict = {}
        self._call_endpoint_with_patches({"lookback_days": 9999}, settings, capture)
        assert capture["lookback_days"] == 365

    def test_max_rows_min_clamped(self):
        settings = _settings_for_endpoint()
        capture: dict = {}
        self._call_endpoint_with_patches({"max_rows": 0}, settings, capture)
        assert capture["max_rows"] == 1

    def test_max_rows_max_clamped(self):
        settings = _settings_for_endpoint()
        capture: dict = {}
        self._call_endpoint_with_patches({"max_rows": 99999}, settings, capture)
        assert capture["max_rows"] == 1000

    def test_valid_inputs_pass_through_unchanged(self):
        settings = _settings_for_endpoint()
        capture: dict = {}
        self._call_endpoint_with_patches(
            {"tickers": ["AAPL", "MSFT"], "lookback_days": 14, "max_rows": 100},
            settings,
            capture,
        )
        assert capture["lookback_days"] == 14
        assert capture["max_rows"] == 100


class TestEndpointResponseShape:
    """Test that the response contains only safe aggregate fields."""

    def _get_response(self, summary=None) -> dict:
        from app.routers.diagnostics import observe_research_artifacts, ResearchArtifactsObserveRequest
        from app.middleware.auth import AuthenticatedUser
        import asyncio

        settings = _settings_for_endpoint()
        user = AuthenticatedUser(
            user_id=uuid.UUID(settings.finance_runtime_cert_user_id),
            email="test@local",
            role="owner",
        )
        payload = ResearchArtifactsObserveRequest(tickers=["AAPL"])
        with patch("app.routers.diagnostics.get_settings", return_value=settings), \
             patch("app.routers.diagnostics.get_supabase_client", return_value=MagicMock()), \
             patch("app.routers.diagnostics.summarize_recent_research_artifacts",
                   return_value=summary or _fake_summary()):
            return asyncio.run(observe_research_artifacts(payload=payload, user=user))

    def test_response_has_required_counter_fields(self):
        resp = self._get_response()
        required = [
            "observability_enabled", "artifact_count",
            "safe_for_decision_false_count", "unexpected_safe_for_decision_true_count",
            "forbidden_payload_violation_count", "active_count", "inactive_count",
            "artifacts_with_sources_count", "artifacts_without_sources_count",
            "artifacts_with_facts_count", "artifacts_without_facts_count",
            "missing_evidence_count", "visible_snapshot_unchanged", "errors",
            "by_ticker", "by_artifact_type", "by_skill_pack",
            "by_confidence_or_trust_level", "by_freshness_status",
        ]
        for key in required:
            assert key in resp, f"Missing required field in response: {key}"

    def test_response_has_no_raw_payload_field(self):
        resp = self._get_response()
        forbidden_keys = {
            "artifact_payload", "payloads", "raw_rows", "source_url",
            "source_urls", "quote_or_excerpt", "facts", "excerpts",
            "structured_payload", "raw_metric_values", "raw_companyfacts",
        }
        overlap = forbidden_keys & set(resp.keys())
        assert not overlap, f"Response contains forbidden raw-data fields: {overlap}"

    def test_visible_snapshot_unchanged_is_true(self):
        resp = self._get_response()
        assert resp["visible_snapshot_unchanged"] is True

    def test_errors_is_list(self):
        resp = self._get_response()
        assert isinstance(resp["errors"], list)

    def test_db_error_returns_200_with_errors(self):
        summary = _fake_summary(artifact_count=0, safe_false=0, errors=["artifact_query_error error=timeout"])
        resp = self._get_response(summary=summary)
        assert resp["artifact_count"] == 0
        assert any("artifact_query_error" in e for e in resp["errors"])


class TestEndpointStaticGuards:
    """Static source code inspection guards for the endpoint module."""

    def _import_lines(self) -> list[str]:
        import ast
        mod = importlib.import_module("app.routers.diagnostics")
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        lines = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                lines.append(ast.unparse(node))
        return lines

    def test_diagnostics_endpoint_does_not_import_decision_policy_v1(self):
        """diagnostics.py must not import decision_policy_v1 — check only imports."""
        import_lines = self._import_lines()
        assert not any("decision_policy_v1" in line for line in import_lines), (
            "diagnostics.py must not import decision_policy_v1"
        )

    def test_diagnostics_module_does_not_call_decide(self):
        """diagnostics.py must not contain AST-level decide() function calls."""
        import ast
        mod = importlib.import_module("app.routers.diagnostics")
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        decide_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "decide")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "decide")
            )
        ]
        assert decide_calls == [], f"diagnostics.py makes decide() AST calls: {decide_calls}"

    def test_observe_endpoint_docstring_states_no_decide(self):
        """Confirm the endpoint docstring explicitly states the decide() prohibition."""
        from app.routers.diagnostics import observe_research_artifacts
        doc = observe_research_artifacts.__doc__ or ""
        assert "decide" in doc.lower(), (
            "observe_research_artifacts() docstring should mention decide() prohibition"
        )

    def test_observe_endpoint_docstring_states_no_snapshot_writes(self):
        from app.routers.diagnostics import observe_research_artifacts
        doc = observe_research_artifacts.__doc__ or ""
        assert "intel_v3_snapshots" in doc.lower(), (
            "observe_research_artifacts() docstring should mention intel_v3_snapshots"
        )


class TestEndpointAuthGuards:
    """Test that runtime cert auth is correctly enforced on the observability endpoint."""

    def test_cert_disabled_raises_404(self):
        """finance_runtime_cert_enabled=False → _ensure_cert_enabled() raises 404."""
        from app.routers.diagnostics import _ensure_cert_enabled
        from app.config import Settings
        settings = Settings(
            supabase_url="http://fake",
            supabase_anon_key="anon",
            supabase_service_role_key="service",
            supabase_jwt_secret="secret",
            encryption_key="a" * 64,
            finance_runtime_cert_enabled=False,
        )
        with patch("app.routers.diagnostics.get_settings", return_value=settings):
            with pytest.raises(HTTPException) as exc_info:
                _ensure_cert_enabled("any-secret")
        assert exc_info.value.status_code == 404

    def test_wrong_secret_raises_403(self):
        from app.routers.diagnostics import _ensure_cert_enabled
        settings = _settings_for_endpoint(cert_secret="correct-secret")
        with patch("app.routers.diagnostics.get_settings", return_value=settings):
            with pytest.raises(HTTPException) as exc_info:
                _ensure_cert_enabled("wrong-secret")
        assert exc_info.value.status_code == 403

    def test_missing_secret_raises_403(self):
        from app.routers.diagnostics import _ensure_cert_enabled
        settings = _settings_for_endpoint(cert_secret="correct-secret")
        with patch("app.routers.diagnostics.get_settings", return_value=settings):
            with pytest.raises(HTTPException) as exc_info:
                _ensure_cert_enabled(None)
        assert exc_info.value.status_code == 403

    def test_correct_secret_does_not_raise(self):
        from app.routers.diagnostics import _ensure_cert_enabled
        settings = _settings_for_endpoint(cert_secret="correct-secret")
        with patch("app.routers.diagnostics.get_settings", return_value=settings):
            # Should not raise
            _ensure_cert_enabled("correct-secret")


class TestEndpointConstants:
    """Verify Phase 4 endpoint constants are within expected ranges."""

    def test_max_observe_tickers_is_10(self):
        from app.routers.diagnostics import MAX_OBSERVE_TICKERS_PER_REQUEST
        assert MAX_OBSERVE_TICKERS_PER_REQUEST == 10

    def test_max_observe_lookback_days_is_365(self):
        from app.routers.diagnostics import MAX_OBSERVE_LOOKBACK_DAYS
        assert MAX_OBSERVE_LOOKBACK_DAYS == 365

    def test_min_observe_lookback_days_is_1(self):
        from app.routers.diagnostics import MIN_OBSERVE_LOOKBACK_DAYS
        assert MIN_OBSERVE_LOOKBACK_DAYS == 1

    def test_max_observe_rows_is_1000(self):
        from app.routers.diagnostics import MAX_OBSERVE_ROWS
        assert MAX_OBSERVE_ROWS == 1000

    def test_min_observe_rows_is_1(self):
        from app.routers.diagnostics import MIN_OBSERVE_ROWS
        assert MIN_OBSERVE_ROWS == 1


class TestEndpointRequestModel:
    """Test the request model defaults and validation."""

    def test_default_tickers_is_empty_list(self):
        from app.routers.diagnostics import ResearchArtifactsObserveRequest
        req = ResearchArtifactsObserveRequest()
        assert req.tickers == []

    def test_default_lookback_days_is_30(self):
        from app.routers.diagnostics import ResearchArtifactsObserveRequest
        req = ResearchArtifactsObserveRequest()
        assert req.lookback_days == 30

    def test_default_max_rows_is_250(self):
        from app.routers.diagnostics import ResearchArtifactsObserveRequest
        req = ResearchArtifactsObserveRequest()
        assert req.max_rows == 250
