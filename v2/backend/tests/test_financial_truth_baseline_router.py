"""Router-level smoke tests for Stage 11A — financial-truth-baseline endpoint.

Tests cert-gating behavior and verifies the endpoint calls the service correctly.
No live DB, no provider calls, no LLM calls.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException


_CERT_USER = SimpleNamespace(id=uuid4(), email="cert@example.com")
_CERT_SECRET = "test-secret"
_CERT_SETTINGS = SimpleNamespace(
    finance_runtime_cert_enabled=True,
    finance_runtime_cert_secret=_CERT_SECRET,
    finance_runtime_cert_user_id=str(_CERT_USER.id),
    finance_runtime_cert_user_email=_CERT_USER.email,
)


@pytest.mark.asyncio
async def test_cert_disabled_returns_404(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(finance_runtime_cert_enabled=False, finance_runtime_cert_secret=None),
    )
    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(request=SimpleNamespace(headers={}), cert_secret=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_invalid_cert_secret_returns_403(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _CERT_SETTINGS)
    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(request=SimpleNamespace(headers={}), cert_secret="wrong")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_valid_cert_calls_service_and_returns_result(monkeypatch):
    from app.routers.diagnostics import FinancialTruthBaselineRequest, financial_truth_baseline

    _fake_result = {
        "diagnostic_version": "financial_truth_baseline_v1",
        "verdict": {"truth_status": "certified"},
    }

    async def _fake_run(**_kwargs):
        return _fake_result

    monkeypatch.setattr(
        "app.services.financial_truth_baseline_v1.run_financial_truth_baseline",
        _fake_run,
    )
    monkeypatch.setattr(
        "app.routers.diagnostics.get_supabase_client",
        lambda: SimpleNamespace(),
    )

    result = await financial_truth_baseline(
        payload=FinancialTruthBaselineRequest(),
        user=_CERT_USER,
    )
    assert result["diagnostic_version"] == "financial_truth_baseline_v1"
    assert result["verdict"]["truth_status"] == "certified"
