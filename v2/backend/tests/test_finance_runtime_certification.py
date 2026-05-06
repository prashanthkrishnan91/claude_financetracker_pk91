from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException


@pytest.mark.asyncio
async def test_cert_disabled_returns_404(monkeypatch):
    from app.routers.diagnostics import certify_finance_runtime, FinanceRuntimeCertRequest

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(finance_runtime_cert_enabled=False, finance_runtime_cert_secret=None),
    )
    with pytest.raises(HTTPException) as exc:
        await certify_finance_runtime(
            payload=FinanceRuntimeCertRequest(mode="read_only_cards"),
            background_tasks=BackgroundTasks(),
            user=SimpleNamespace(id=uuid4()),
            cert_secret=None,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cert_enabled_rejects_wrong_secret(monkeypatch):
    from app.routers.diagnostics import certify_finance_runtime, FinanceRuntimeCertRequest

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(finance_runtime_cert_enabled=True, finance_runtime_cert_secret="topsecret"),
    )
    with pytest.raises(HTTPException) as exc:
        await certify_finance_runtime(
            payload=FinanceRuntimeCertRequest(mode="read_only_cards"),
            background_tasks=BackgroundTasks(),
            user=SimpleNamespace(id=uuid4()),
            cert_secret="wrong",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_read_only_cards_emits_summary(monkeypatch):
    from app.models.recommendation import InsightCard
    from app.routers.diagnostics import certify_finance_runtime, FinanceRuntimeCertRequest

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(finance_runtime_cert_enabled=True, finance_runtime_cert_secret="topsecret"),
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
        cert_secret="topsecret",
    )
    assert out["total_cards"] == 1
    assert out["response_path"] == "page_load"


@pytest.mark.asyncio
async def test_force_and_nonforced_pass_force_flag(monkeypatch):
    from app.routers.diagnostics import certify_finance_runtime, FinanceRuntimeCertRequest

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(finance_runtime_cert_enabled=True, finance_runtime_cert_secret="topsecret"),
    )

    class _Svc:
        async def queue_agent_run(self, **kwargs):
            return str(uuid4()), True

    monkeypatch.setattr("app.routers.diagnostics.RecommendationService", lambda user_id: _Svc())

    bg_force = BackgroundTasks()
    out_force = await certify_finance_runtime(
        payload=FinanceRuntimeCertRequest(mode="force_run_agents"),
        background_tasks=bg_force,
        user=SimpleNamespace(id=uuid4()),
        cert_secret="topsecret",
    )
    assert out_force["force_recompute"] is True

    bg_nonforce = BackgroundTasks()
    out_nonforce = await certify_finance_runtime(
        payload=FinanceRuntimeCertRequest(mode="nonforced_run_agents"),
        background_tasks=bg_nonforce,
        user=SimpleNamespace(id=uuid4()),
        cert_secret="topsecret",
    )
    assert out_nonforce["force_recompute"] is False


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
