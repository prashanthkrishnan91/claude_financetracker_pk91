"""Router-level tests for Stage 12B — next-buy-policy-diagnostic endpoint.

Tests cert-gating, input validation, and service delegation.
No live DB, no provider calls, no LLM calls.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException


_CERT_USER = SimpleNamespace(id=uuid4(), email="cert@example.com")
_CERT_SECRET = "test-secret-12b"


def _cert_settings():
    return SimpleNamespace(
        finance_runtime_cert_enabled=True,
        finance_runtime_cert_secret=_CERT_SECRET,
        finance_runtime_cert_user_id=str(_CERT_USER.id),
        finance_runtime_cert_user_email=_CERT_USER.email,
    )


def _disabled_cert_settings():
    return SimpleNamespace(
        finance_runtime_cert_enabled=False,
        finance_runtime_cert_secret=None,
    )


_FAKE_RESULT = {
    "diagnostic_version": "allocation_policy_v1",
    "generated_at": "2026-06-23T00:00:00Z",
    "input": {"cash_to_deploy": 500.0, "min_trade_amount": 25.0, "max_positions": 5},
    "truth_dependency": {
        "truth_status": "certified",
        "reconciliation_status": "pass",
        "snapshot_portfolio_value": 10000.0,
        "position_derived_market_value": 10000.0,
        "price_coverage_status": "ok",
        "missing_price_tickers": [],
        "stale_price_tickers": [],
        "can_run_policy": True,
        "blockers": [],
    },
    "current_portfolio": {
        "total_market_value": 10000.0,
        "open_position_count": 2,
        "per_ticker": [],
        "group_weights": {},
        "etf_total_weight_pct": 75.0,
    },
    "generated_policy": {
        "policy_version": "conservative_profile_policy_v1",
        "etf_floor_pct": 40.0,
        "current_etf_pct": 75.0,
        "etf_floor_met": True,
        "group_targets": {},
        "caps": {},
        "intel_v3_overlay_used": False,
        "intel_v3_overlay_warning": "intel_v3_snapshot_unavailable",
        "warnings": [],
    },
    "target_vs_current": {"by_group": {}, "by_ticker": {}},
    "next_buy_candidates": [
        {
            "ticker": "VOO",
            "dollar_amount": 500.0,
            "current_weight_pct": 30.0,
            "target_or_cap_weight_pct": 25.0,
            "gap_pct": 5.0,
            "gap_dollars": 500.0,
            "classification": "broad_index_etf",
            "conviction": "neutral",
            "confidence": "policy_only",
            "reason_codes": ["etf_floor_not_met"],
            "is_unknown_ticker": False,
        }
    ],
    "cash_plan": {
        "cash_to_deploy": 500.0,
        "allocated_cash": 500.0,
        "unallocated_cash": 0.0,
        "allocation_count": 1,
        "no_buy_reason": None,
    },
    "verdict": {
        "policy_status": "ready",
        "recommendations_trusted": False,
        "numeric_plan_trusted": True,
        "next_required_fix": "No immediate fix required — policy is ready",
    },
}


# ── Cert-gating tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cert_disabled_returns_404(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: _disabled_cert_settings(),
    )
    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(
            request=SimpleNamespace(headers={}), cert_secret=None
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_wrong_cert_secret_returns_403(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: _cert_settings(),
    )
    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(
            request=SimpleNamespace(headers={}), cert_secret="wrong"
        )
    assert exc.value.status_code == 403


# ── Input validation tests ────────────────────────────────────────────────────

def test_cash_to_deploy_required():
    """Request model requires cash_to_deploy."""
    from app.routers.diagnostics import NextBuyPolicyDiagnosticRequest
    import pydantic

    with pytest.raises((pydantic.ValidationError, Exception)):
        NextBuyPolicyDiagnosticRequest()  # missing cash_to_deploy


def test_cash_to_deploy_must_be_positive():
    """cash_to_deploy must be > 0."""
    from app.routers.diagnostics import NextBuyPolicyDiagnosticRequest
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        NextBuyPolicyDiagnosticRequest(cash_to_deploy=0.0)

    with pytest.raises(pydantic.ValidationError):
        NextBuyPolicyDiagnosticRequest(cash_to_deploy=-100.0)


def test_defaults():
    """max_positions defaults to 5, min_trade_amount to 25."""
    from app.routers.diagnostics import NextBuyPolicyDiagnosticRequest

    req = NextBuyPolicyDiagnosticRequest(cash_to_deploy=500.0)
    assert req.max_positions == 5
    assert req.min_trade_amount == 25.0


# ── Service delegation tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_cert_calls_service(monkeypatch):
    """With valid cert, endpoint calls the policy service and returns result."""
    from app.routers.diagnostics import (
        NextBuyPolicyDiagnosticRequest,
        next_buy_policy_diagnostic,
    )

    async def _fake_run(**_kwargs):
        return _FAKE_RESULT

    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _cert_settings())
    monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "app.services.allocation_policy_v1.run_next_buy_policy_diagnostic",
        _fake_run,
    )

    result = await next_buy_policy_diagnostic(
        payload=NextBuyPolicyDiagnosticRequest(cash_to_deploy=500.0),
        user=_CERT_USER,
    )
    assert result["diagnostic_version"] == "allocation_policy_v1"
    assert result["verdict"]["recommendations_trusted"] is False


@pytest.mark.asyncio
async def test_cash_and_params_passed_to_service(monkeypatch):
    """cash_to_deploy, max_positions, min_trade_amount are passed to the service."""
    from app.routers.diagnostics import (
        NextBuyPolicyDiagnosticRequest,
        next_buy_policy_diagnostic,
    )

    received: dict = {}

    async def _fake_run(db_client, user_id, cash_to_deploy, max_positions, min_trade_amount):
        received.update({
            "cash": cash_to_deploy,
            "max_positions": max_positions,
            "min_trade": min_trade_amount,
        })
        return _FAKE_RESULT

    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _cert_settings())
    monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "app.services.allocation_policy_v1.run_next_buy_policy_diagnostic",
        _fake_run,
    )

    await next_buy_policy_diagnostic(
        payload=NextBuyPolicyDiagnosticRequest(
            cash_to_deploy=1000.0, max_positions=3, min_trade_amount=50.0
        ),
        user=_CERT_USER,
    )
    assert received["cash"] == 1000.0
    assert received["max_positions"] == 3
    assert received["min_trade"] == 50.0


@pytest.mark.asyncio
async def test_no_writes_in_endpoint(monkeypatch):
    """Endpoint must not write to DB — pass-through to service with no mutations."""
    from app.routers.diagnostics import (
        NextBuyPolicyDiagnosticRequest,
        next_buy_policy_diagnostic,
    )

    async def _no_write_run(**kwargs):
        # Return valid response — confirms endpoint itself makes no additional writes
        return _FAKE_RESULT

    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _cert_settings())
    monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "app.services.allocation_policy_v1.run_next_buy_policy_diagnostic",
        _no_write_run,
    )

    result = await next_buy_policy_diagnostic(
        payload=NextBuyPolicyDiagnosticRequest(cash_to_deploy=500.0),
        user=_CERT_USER,
    )
    # Endpoint just delegates and returns — no extra mutation
    assert result == _FAKE_RESULT


@pytest.mark.asyncio
async def test_policy_status_blocked_response_passthrough(monkeypatch):
    """When service returns blocked status, endpoint passes it through unmodified."""
    from app.routers.diagnostics import (
        NextBuyPolicyDiagnosticRequest,
        next_buy_policy_diagnostic,
    )

    blocked_result = {**_FAKE_RESULT}
    blocked_result["verdict"] = {
        **_FAKE_RESULT["verdict"],
        "policy_status": "blocked",
        "numeric_plan_trusted": False,
    }
    blocked_result["cash_plan"] = {**_FAKE_RESULT["cash_plan"], "allocation_count": 0,
                                    "no_buy_reason": "policy_blocked: reconciliation_blocked"}

    async def _fake_blocked(**_kwargs):
        return blocked_result

    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _cert_settings())
    monkeypatch.setattr("app.routers.diagnostics.get_supabase_client", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "app.services.allocation_policy_v1.run_next_buy_policy_diagnostic",
        _fake_blocked,
    )

    result = await next_buy_policy_diagnostic(
        payload=NextBuyPolicyDiagnosticRequest(cash_to_deploy=500.0),
        user=_CERT_USER,
    )
    assert result["verdict"]["policy_status"] == "blocked"
    assert result["cash_plan"]["allocation_count"] == 0
