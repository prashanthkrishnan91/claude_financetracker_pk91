"""Router-level smoke tests for Stage 11B — current-price-truth-repair endpoint.

Tests cert-gating, feature-flag gating, and dry_run default behavior.
No live DB, no provider calls, no LLM calls.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException


_CERT_USER = SimpleNamespace(id=uuid4(), email="cert@example.com")
_CERT_SECRET = "test-secret-11b"
_CERT_SETTINGS_BASE = SimpleNamespace(
    finance_runtime_cert_enabled=True,
    finance_runtime_cert_secret=_CERT_SECRET,
    finance_runtime_cert_user_id=str(_CERT_USER.id),
    finance_runtime_cert_user_email=_CERT_USER.email,
    current_price_truth_repair_enabled=True,
)


def _cert_settings(repair_enabled: bool = True):
    return SimpleNamespace(
        finance_runtime_cert_enabled=True,
        finance_runtime_cert_secret=_CERT_SECRET,
        finance_runtime_cert_user_id=str(_CERT_USER.id),
        finance_runtime_cert_user_email=_CERT_USER.email,
        current_price_truth_repair_enabled=repair_enabled,
    )


@pytest.mark.asyncio
async def test_cert_disabled_returns_404(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(
            finance_runtime_cert_enabled=False,
            finance_runtime_cert_secret=None,
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(
            request=SimpleNamespace(headers={}), cert_secret=None
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_invalid_cert_secret_returns_403(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: _cert_settings(),
    )
    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(
            request=SimpleNamespace(headers={}), cert_secret="wrong-secret"
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_feature_flag_disabled_returns_403(monkeypatch):
    """When CURRENT_PRICE_TRUTH_REPAIR_ENABLED=false, endpoint returns 403."""
    from app.routers.diagnostics import (
        CurrentPriceTruthRepairRequest,
        current_price_truth_repair,
    )

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: _cert_settings(repair_enabled=False),
    )
    monkeypatch.setattr(
        "app.routers.diagnostics.get_supabase_client",
        lambda: SimpleNamespace(),
    )

    with pytest.raises(HTTPException) as exc:
        await current_price_truth_repair(
            payload=CurrentPriceTruthRepairRequest(),
            user=_CERT_USER,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_dry_run_defaults_to_true(monkeypatch):
    """Endpoint default request has dry_run=True."""
    from app.routers.diagnostics import CurrentPriceTruthRepairRequest

    payload = CurrentPriceTruthRepairRequest()
    assert payload.dry_run is True


@pytest.mark.asyncio
async def test_valid_cert_and_flag_calls_service(monkeypatch):
    """With valid cert + flag enabled, endpoint calls the repair service and returns result."""
    from app.routers.diagnostics import (
        CurrentPriceTruthRepairRequest,
        current_price_truth_repair,
    )

    _fake_result = {
        "diagnostic_version": "current_price_truth_repair_v1",
        "dry_run": True,
        "open_tickers_count": 5,
        "rows_written": 0,
        "safe_to_rerun": True,
        "next_step": "rerun_financial_truth_baseline",
        "per_ticker": [],
    }

    async def _fake_run(**_kwargs):
        return _fake_result

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: _cert_settings(),
    )
    monkeypatch.setattr(
        "app.routers.diagnostics.get_supabase_client",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "app.services.current_price_truth_repair_v1.run_current_price_truth_repair",
        _fake_run,
    )

    result = await current_price_truth_repair(
        payload=CurrentPriceTruthRepairRequest(dry_run=True),
        user=_CERT_USER,
    )
    assert result["diagnostic_version"] == "current_price_truth_repair_v1"
    assert result["dry_run"] is True
    assert result["rows_written"] == 0


@pytest.mark.asyncio
async def test_dry_run_false_passes_to_service(monkeypatch):
    """dry_run=false is passed through to the repair service."""
    from app.routers.diagnostics import (
        CurrentPriceTruthRepairRequest,
        current_price_truth_repair,
    )

    received_dry_run: list[bool] = []

    async def _fake_run(db_client, user_id, dry_run=True):
        received_dry_run.append(dry_run)
        return {"diagnostic_version": "current_price_truth_repair_v1",
                "dry_run": dry_run, "rows_written": 3, "safe_to_rerun": True,
                "next_step": "rerun_financial_truth_baseline",
                "open_tickers_count": 1, "per_ticker": []}

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: _cert_settings(),
    )
    monkeypatch.setattr(
        "app.routers.diagnostics.get_supabase_client",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "app.services.current_price_truth_repair_v1.run_current_price_truth_repair",
        _fake_run,
    )

    await current_price_truth_repair(
        payload=CurrentPriceTruthRepairRequest(dry_run=False),
        user=_CERT_USER,
    )
    assert received_dry_run == [False]
