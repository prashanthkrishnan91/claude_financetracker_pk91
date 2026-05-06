from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient


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
async def test_cert_enabled_rejects_wrong_secret(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(
            finance_runtime_cert_enabled=True,
            finance_runtime_cert_secret="topsecret",
            finance_runtime_cert_user_id=str(uuid4()),
            finance_runtime_cert_user_email="cert@example.com",
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(request=SimpleNamespace(headers={}), cert_secret="wrong")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_read_only_cards_emits_summary(monkeypatch):
    from app.models.recommendation import InsightCard
    from app.routers.diagnostics import FinanceRuntimeCertRequest, certify_finance_runtime

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(
            finance_runtime_cert_enabled=True,
            finance_runtime_cert_secret="topsecret",
            finance_runtime_cert_user_id=str(uuid4()),
            finance_runtime_cert_user_email="cert@example.com",
        ),
    )

    class _Svc:
        async def get_insight_cards(self):
            return [
                InsightCard(
                    id=uuid4(), ticker="AAPL", name="Apple", action="HOLD", detail="Hold for now", rationale="Balanced", urgency=1,
                    color="blue", tax_note="", drip_note="", category="Core", thesis_v2={"status": "partial"}
                )
            ]

    monkeypatch.setattr("app.routers.diagnostics.RecommendationService", lambda user_id: _Svc())
    out = await certify_finance_runtime(
        payload=FinanceRuntimeCertRequest(mode="read_only_cards"),
        background_tasks=BackgroundTasks(),
        user=SimpleNamespace(id=uuid4()),
    )
    assert out["total_cards"] == 1
    assert out["response_path"] == "page_load"


@pytest.mark.asyncio
async def test_force_and_nonforced_pass_force_flag(monkeypatch):
    from app.routers.diagnostics import FinanceRuntimeCertRequest, certify_finance_runtime

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(
            finance_runtime_cert_enabled=True,
            finance_runtime_cert_secret="topsecret",
            finance_runtime_cert_user_id=str(uuid4()),
            finance_runtime_cert_user_email="cert@example.com",
        ),
    )

    class _Svc:
        async def queue_agent_run(self, **kwargs):
            return str(uuid4()), True

    monkeypatch.setattr("app.routers.diagnostics.RecommendationService", lambda user_id: _Svc())

    out_force = await certify_finance_runtime(
        payload=FinanceRuntimeCertRequest(mode="force_run_agents"),
        background_tasks=BackgroundTasks(),
        user=SimpleNamespace(id=uuid4()),
    )
    assert out_force["force_recompute"] is True

    out_nonforce = await certify_finance_runtime(
        payload=FinanceRuntimeCertRequest(mode="nonforced_run_agents"),
        background_tasks=BackgroundTasks(),
        user=SimpleNamespace(id=uuid4()),
    )
    assert out_nonforce["force_recompute"] is False
    assert "/api/v1/diagnostics/finance-intel/jobs/" in out_force["poll"]["job_status"]
    assert "/api/v1/diagnostics/finance-intel/jobs/" in out_nonforce["poll"]["job_status"]


def test_cert_status_logic_read_only_fail_on_conflict():
    from app.routers.diagnostics import _status_for_mode

    status, reasons = _status_for_mode(
        "read_only_cards",
        1,
        {},
        {
            "conflict_count_after_sanitize": 1,
            "buy_cards_with_hold_language_count_after_sanitize": 0,
            "hold_cards_with_buy_language_count_after_sanitize": 0,
            "trim_sell_cards_with_buy_language_count_after_sanitize": 0,
        },
        {},
    )
    assert status == "FAIL"
    assert "narrative_conflicts_detected" in reasons


@pytest.mark.asyncio
async def test_cert_secret_requires_configured_cert_user(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(
            finance_runtime_cert_enabled=True,
            finance_runtime_cert_secret="topsecret",
            finance_runtime_cert_user_id=None,
            finance_runtime_cert_user_email=None,
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(
            request=SimpleNamespace(headers={}),
            cert_secret="topsecret",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_diagnostics_job_status_uses_cert_secret_and_cert_user(monkeypatch):
    from app.routers.diagnostics import get_cert_job_status

    cert_user_id = uuid4()
    called = {}

    class _Svc:
        def __init__(self, user_id):
            called["user_id"] = user_id

        async def get_job_status(self, job_id):
            called["job_id"] = job_id
            return {"id": str(job_id), "status": "queued"}

    monkeypatch.setattr("app.routers.diagnostics.RecommendationService", _Svc)
    out = await get_cert_job_status(job_id=uuid4(), cert_user=SimpleNamespace(id=cert_user_id))
    assert out["status"] == "queued"
    assert called["user_id"] == cert_user_id


@pytest.mark.asyncio
async def test_cert_secret_guard_missing_or_bad_secret_403(monkeypatch):
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(
            finance_runtime_cert_enabled=True,
            finance_runtime_cert_secret="topsecret",
            finance_runtime_cert_user_id=str(uuid4()),
            finance_runtime_cert_user_email="cert@example.com",
        ),
    )
    with pytest.raises(HTTPException) as missing_exc:
        await _get_runtime_cert_user(request=SimpleNamespace(headers={}), cert_secret=None)
    assert missing_exc.value.status_code == 403

    with pytest.raises(HTTPException) as bad_exc:
        await _get_runtime_cert_user(request=SimpleNamespace(headers={}), cert_secret="bad")
    assert bad_exc.value.status_code == 403


def test_recommendations_job_status_route_still_requires_normal_auth(monkeypatch):
    from app.main import app
    from app.routers.recommendations import get_current_user

    app.dependency_overrides = {}
    client = TestClient(app)
    response = client.get(f"/api/v1/recommendations/jobs/{uuid4()}")
    assert response.status_code in (401, 403)

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    allowed = client.get(f"/api/v1/recommendations/jobs/{uuid4()}")
    assert allowed.status_code != response.status_code
    app.dependency_overrides = {}
