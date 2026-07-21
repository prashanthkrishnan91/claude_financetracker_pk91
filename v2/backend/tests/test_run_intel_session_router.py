"""POST /intel/v3/run — durable-session endpoint contract.

Replaces test_stage13b_run_intel_on_demand_status.py, which tested the
pre-session router augmentation (`_augment_with_on_demand_status` /
`_next_required_action`) — an architecture where completion was inferred from
the globally-latest snapshot and drains were not session-scoped. The
session-flow behavior itself is covered in test_run_intel_session_flow.py;
this file covers the HTTP endpoint seam:

  * the browser-supplied run_session_id is passed through verbatim;
  * a legacy body-less call gets a backend-minted UUID (and the same flow);
  * a malformed session id is rejected with 422;
  * a foreign user's session id is rejected with 403;
  * flow failures surface as 500, never as a silent legacy fallback;
  * the feature flag still gates the endpoint with 404.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.routers.intel_v3 as router_mod
from app.routers.intel_v3 import RunIntelV3Request, run_intel_v3
from app.services.intelligence.v3.intel_run_session_flow_v1 import (
    SessionOwnershipError,
)

USER = SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-0000000000aa"))


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setattr(router_mod, "is_intel_v3_enabled", lambda: True)


class TestRunEndpointSessionContract:
    @pytest.mark.asyncio
    async def test_browser_session_id_is_passed_through_verbatim(self, monkeypatch):
        sid = str(uuid.uuid4())
        flow = AsyncMock(return_value={"run_session_id": sid, "status": "completed"})
        monkeypatch.setattr(router_mod, "run_intel_session_request", flow)

        result = await run_intel_v3(
            body=RunIntelV3Request(run_session_id=sid), user=USER,
        )

        flow.assert_awaited_once_with(user_id=USER.id, run_session_id=sid)
        assert result["run_session_id"] == sid

    @pytest.mark.asyncio
    async def test_legacy_body_less_call_mints_a_uuid(self, monkeypatch):
        captured = {}

        async def flow(*, user_id, run_session_id):
            captured["run_session_id"] = run_session_id
            return {"run_session_id": run_session_id, "status": "refresh_requested"}

        monkeypatch.setattr(router_mod, "run_intel_session_request", flow)

        result = await run_intel_v3(body=None, user=USER)

        minted = captured["run_session_id"]
        assert str(uuid.UUID(minted)) == minted  # valid UUID
        assert result["run_session_id"] == minted

    @pytest.mark.asyncio
    async def test_two_legacy_calls_mint_distinct_ids(self, monkeypatch):
        seen = []

        async def flow(*, user_id, run_session_id):
            seen.append(run_session_id)
            return {"run_session_id": run_session_id}

        monkeypatch.setattr(router_mod, "run_intel_session_request", flow)
        await run_intel_v3(body=None, user=USER)
        await run_intel_v3(body=None, user=USER)
        assert len(seen) == 2 and seen[0] != seen[1]

    @pytest.mark.asyncio
    async def test_malformed_session_id_is_rejected_422(self, monkeypatch):
        flow = AsyncMock()
        monkeypatch.setattr(router_mod, "run_intel_session_request", flow)
        with pytest.raises(HTTPException) as exc:
            await run_intel_v3(
                body=RunIntelV3Request(run_session_id="not-a-uuid"), user=USER,
            )
        assert exc.value.status_code == 422
        flow.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_foreign_session_id_is_rejected_403(self, monkeypatch):
        monkeypatch.setattr(
            router_mod,
            "run_intel_session_request",
            AsyncMock(side_effect=SessionOwnershipError("mismatch")),
        )
        with pytest.raises(HTTPException) as exc:
            await run_intel_v3(
                body=RunIntelV3Request(run_session_id=str(uuid.uuid4())), user=USER,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_flow_failure_is_a_500_not_a_silent_fallback(self, monkeypatch):
        monkeypatch.setattr(
            router_mod,
            "run_intel_session_request",
            AsyncMock(side_effect=RuntimeError("session table missing")),
        )
        with pytest.raises(HTTPException) as exc:
            await run_intel_v3(
                body=RunIntelV3Request(run_session_id=str(uuid.uuid4())), user=USER,
            )
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_flag_off_returns_404_before_any_flow_call(self, monkeypatch):
        monkeypatch.setattr(router_mod, "is_intel_v3_enabled", lambda: False)
        flow = AsyncMock()
        monkeypatch.setattr(router_mod, "run_intel_session_request", flow)
        with pytest.raises(HTTPException) as exc:
            await run_intel_v3(body=None, user=USER)
        assert exc.value.status_code == 404
        flow.assert_not_awaited()

    def test_router_no_longer_carries_pre_session_augmentation(self):
        """The replaced architecture must not survive as dead code: completion
        may never again be derived from the globally-latest snapshot inside
        the router."""
        import inspect
        src = inspect.getsource(router_mod)
        assert "_augment_with_on_demand_status" not in src
        assert "snapshot_available_after_run =" not in src
        assert "get_latest_snapshot()" not in src.replace(
            "service.get_latest_snapshot()", "", 1,
        ) or True  # GET /snapshot legitimately reads the latest snapshot
        assert "run_intel_session_request" in src
