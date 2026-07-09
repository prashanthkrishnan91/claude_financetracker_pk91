"""Stage 12E.1 — regression tests for the runtime-cert Authorization-header path.

Bug: `_get_runtime_cert_user` called `get_current_user(request)` directly
when an Authorization header was present. `get_current_user`'s `auth` and
`settings` parameters only resolve through FastAPI's own dependency
injection, so a direct call left them as unresolved `Depends(...)` objects
and crashed with:

    AttributeError: 'Depends' object has no attribute 'credentials'

Fix: `get_current_user_from_request` (app/middleware/auth.py) explicitly
resolves the bearer credentials via the same HTTPBearer scheme instance and
the settings singleton, then delegates to get_current_user's unchanged JWT
validation logic.

No allocation/policy changes, no SQL, no provider calls, no LLM calls, no
writes, no paycheck-plan output changes.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException


def _cert_settings(**overrides):
    base = dict(
        finance_runtime_cert_enabled=True,
        finance_runtime_cert_secret="topsecret",
        finance_runtime_cert_user_id=str(uuid4()),
        finance_runtime_cert_user_email="cert@example.com",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeRequest:
    """Minimal stand-in for starlette.Request — only .headers is touched."""

    def __init__(self, headers: dict):
        self.headers = headers


# ── No-Authorization path: unchanged runtime-cert-user fallback ──────────────

@pytest.mark.asyncio
async def test_runtime_cert_user_fallback_when_no_authorization_header(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    settings = _cert_settings()
    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)

    user = await _get_runtime_cert_user(
        request=_FakeRequest(headers={}),
        cert_secret="topsecret",
    )
    assert str(user.id) == settings.finance_runtime_cert_user_id
    assert user.email == settings.finance_runtime_cert_user_email


# ── Authorization-header path: must not crash on unresolved Depends ─────────

@pytest.mark.asyncio
async def test_runtime_cert_user_with_authorization_header_does_not_crash(monkeypatch):
    """Regression guard for the exact Stage 12E.1 failure mode."""
    from app.routers.diagnostics import _get_runtime_cert_user

    settings = _cert_settings()
    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)

    real_user = SimpleNamespace(id=uuid4(), email="real-user@example.com", role="owner")

    async def _fake_get_current_user_from_request(request):
        return real_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_current_user_from_request",
        _fake_get_current_user_from_request,
    )

    try:
        user = await _get_runtime_cert_user(
            request=_FakeRequest(headers={"authorization": "Bearer some-jwt"}),
            cert_secret="topsecret",
        )
    except AttributeError as exc:
        pytest.fail(
            f"_get_runtime_cert_user crashed on the Authorization-header path: {exc}"
        )

    assert user is real_user
    # Explicit regression assertion for the exact historical failure class.
    assert "'Depends' object has no attribute 'credentials'" not in repr(user)


@pytest.mark.asyncio
async def test_get_current_user_from_request_resolves_real_dependencies(monkeypatch):
    """
    Exercises get_current_user_from_request end-to-end (no monkeypatching of
    diagnostics) against a syntactically valid but unverifiable JWT, proving
    it reaches JWT validation instead of crashing on an unresolved Depends
    object.
    """
    from app.middleware.auth import get_current_user_from_request

    settings = SimpleNamespace(supabase_url="https://example.supabase.co")
    monkeypatch.setattr("app.middleware.auth.get_settings", lambda: settings)

    fake_token = jwt.encode({"sub": str(uuid4()), "email": "x@example.com"}, "not-the-real-key", algorithm="HS256")
    request = _FakeRequest(headers={"Authorization": f"Bearer {fake_token}"})

    class _FakeJWKSClient:
        def get_signing_key_from_jwt(self, token):
            raise jwt.InvalidTokenError("no matching key")

    monkeypatch.setattr("app.middleware.auth.get_jwks_client", lambda supabase_url: _FakeJWKSClient())

    with pytest.raises(HTTPException) as exc:
        await get_current_user_from_request(request)

    # A clean 401 (JWT validation ran and rejected the token) — not a 500
    # AttributeError from an unresolved Depends object.
    assert exc.value.status_code == 401
    assert "'Depends' object has no attribute" not in str(exc.value.detail)


# ── Invalid / missing cert secret: unchanged behavior ────────────────────────

@pytest.mark.asyncio
async def test_missing_cert_secret_still_403(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _cert_settings())

    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(request=_FakeRequest(headers={}), cert_secret=None)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_wrong_cert_secret_still_403_even_with_authorization_header(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: _cert_settings())

    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(
            request=_FakeRequest(headers={"authorization": "Bearer whatever"}),
            cert_secret="wrong",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_cert_disabled_still_404_even_with_authorization_header(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(finance_runtime_cert_enabled=False, finance_runtime_cert_secret=None),
    )
    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(
            request=_FakeRequest(headers={"authorization": "Bearer whatever"}),
            cert_secret=None,
        )
    assert exc.value.status_code == 404


# ── Invalid bearer token with a valid cert secret: clean 401, not 500 ────────

@pytest.mark.asyncio
async def test_invalid_bearer_token_with_valid_cert_returns_clean_401_not_500(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    settings = _cert_settings()
    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)
    monkeypatch.setattr("app.middleware.auth.get_settings", lambda: SimpleNamespace(supabase_url="https://example.supabase.co"))

    class _FakeJWKSClient:
        def get_signing_key_from_jwt(self, token):
            raise jwt.InvalidTokenError("bad token")

    monkeypatch.setattr("app.middleware.auth.get_jwks_client", lambda supabase_url: _FakeJWKSClient())

    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(
            request=_FakeRequest(headers={"authorization": "Bearer not-a-real-jwt"}),
            cert_secret="topsecret",
        )
    assert exc.value.status_code == 401
    assert "'Depends' object" not in str(exc.value.detail)


@pytest.mark.asyncio
async def test_missing_bearer_scheme_with_valid_cert_returns_clean_401_not_500(monkeypatch):
    """Authorization header present but malformed (no 'Bearer' scheme) — HTTPBearer(auto_error=False) returns None, get_current_user then raises a clean 401."""
    from app.routers.diagnostics import _get_runtime_cert_user

    settings = _cert_settings()
    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)
    monkeypatch.setattr("app.middleware.auth.get_settings", lambda: SimpleNamespace(supabase_url="https://example.supabase.co"))

    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(
            request=_FakeRequest(headers={"authorization": "NotBearer something"}),
            cert_secret="topsecret",
        )
    assert exc.value.status_code == 401


# ── Stage 12D endpoint reachable through the auth-header path without 500 ────

@pytest.mark.asyncio
async def test_paycheck_plan_preview_endpoint_reachable_via_auth_header_path(monkeypatch):
    """
    Proves the Stage 12E frontend proxy path (Authorization + cert secret
    both present) resolves to a real user without a 500, by chaining
    _get_runtime_cert_user's dependency into the paycheck-plan-preview
    endpoint exactly as FastAPI's DI would.
    """
    from app.routers.diagnostics import _get_runtime_cert_user
    from app.routers.paycheck_plan_preview import PaycheckPlanPreviewRequest, paycheck_plan_preview

    settings = _cert_settings()
    monkeypatch.setattr("app.routers.diagnostics.get_settings", lambda: settings)

    real_user = SimpleNamespace(id=uuid4(), email="real-user@example.com", role="owner")

    async def _fake_get_current_user_from_request(request):
        return real_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_current_user_from_request",
        _fake_get_current_user_from_request,
    )

    user = await _get_runtime_cert_user(
        request=_FakeRequest(headers={"authorization": "Bearer some-jwt"}),
        cert_secret="topsecret",
    )
    assert user is real_user

    async def _fake_run(**kwargs):
        return {
            "diagnostic_version": "allocation_policy_v1",
            "input": {"cash_to_deploy": 2737.5},
            "truth_dependency": {"price_coverage_status": "ok", "missing_price_tickers": [], "stale_price_tickers": []},
            "next_buy_candidates": [],
            "cash_plan": {"allocated_cash": 0.0, "unallocated_cash": 2737.5, "allocation_count": 0},
            "verdict": {"policy_status": "ready", "numeric_plan_trusted": True, "next_required_fix": None},
        }

    monkeypatch.setattr("app.routers.paycheck_plan_preview.get_supabase_client", lambda: SimpleNamespace())
    monkeypatch.setattr("app.services.allocation_policy_v1.run_next_buy_policy_diagnostic", _fake_run)

    result = await paycheck_plan_preview(
        payload=PaycheckPlanPreviewRequest(cash_to_deploy=2737.5),
        user=user,
    )
    assert result["status"] == "ready"
    assert result["recommendations_trusted"] is False
