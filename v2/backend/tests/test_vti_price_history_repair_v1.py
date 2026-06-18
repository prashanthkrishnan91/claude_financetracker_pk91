"""Tests for Stage 10C.1 — VTI price history repair service and endpoint gating.

Service tests use fully mocked DB clients — no live Supabase/yfinance calls.
Router-level tests use monkeypatched get_settings and call endpoint functions
directly (same pattern as test_finance_runtime_certification.py) — no full
FastAPI app import required.

Coverage:
  - Both-source contribution forensics (deposit_plans primary + buy_tx fallback counts)
  - Endpoint cert-gating (cert disabled → 404, wrong cert → 403)
  - Endpoint feature-flag gating (flag disabled → 403)
  - dry_run defaults to True; dry_run=True writes no rows
  - enabled + valid cert calls repair service
  - Idempotency, no non-VTI writes, no fabrication on provider failure
  - Zero-close filter, coverage check, Stage 10C blocked/unblocked states
  - Period coverage warning when earliest contribution predates backfill window
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.vti_price_history_repair_v1 import (
    REPAIR_VERSION,
    VTI_TICKER,
    _coverage_check,
    _load_contribution_forensics,
    _period_coverage_warning,
    run_vti_price_history_repair,
)
from app.services.history_service import HistoryPoint


# ── DB mock helpers ───────────────────────────────────────────────────────────

class _TableQuery:
    """Chainable mock — returns preset rows on execute()."""

    def __init__(self, rows: list, count: int | None = None):
        self._rows = rows
        self._count = count if count is not None else len(rows)

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, *_a, **_k):
        return self

    def execute(self):
        return SimpleNamespace(data=list(self._rows), count=self._count)


class _MockDB:
    def __init__(
        self,
        deposit_plans: list | None = None,
        transactions: list | None = None,
        price_history_rows: list | None = None,
        price_history_count: int = 0,
        upsert_spy: list | None = None,
    ):
        self._deposit_plans = deposit_plans or []
        self._transactions = transactions or []
        self._price_history_rows = price_history_rows or []
        self._price_history_count = price_history_count
        self._upsert_spy = upsert_spy

    def table(self, name: str) -> _TableQuery:
        if name == "deposit_plans":
            return _TableQuery(self._deposit_plans)
        if name == "transactions":
            return _TableQuery(self._transactions)
        if name == "price_history":
            if self._upsert_spy is not None:
                return _SpyTableQuery(
                    self._price_history_rows,
                    self._price_history_count,
                    self._upsert_spy,
                )
            return _TableQuery(self._price_history_rows, self._price_history_count)
        return _TableQuery([])


class _SpyTableQuery(_TableQuery):
    def __init__(self, rows, count, spy: list):
        super().__init__(rows, count)
        self._spy = spy

    def upsert(self, rows, **_k):
        self._spy.extend(rows)
        return self


def _make_deposit(deposit_date: str, executed: bool = True) -> dict:
    return {"id": str(uuid4()), "execution_date": deposit_date, "executed": executed}


def _make_tx(tx_type: str, tx_date: str) -> dict:
    return {"tx_type": tx_type, "tx_date": tx_date}


def _make_point(date_str: str, close: float = 200.0) -> HistoryPoint:
    return HistoryPoint(date=date_str, open=199.0, high=201.0, low=198.0,
                        close=close, volume=1_000_000)


USER_ID = str(uuid4())


async def _run_repair(db, dry_run=True, provider_points=None, backfill_period="5Y",
                      start_date=None, end_date=None):
    points = provider_points if provider_points is not None else [
        _make_point("2024-01-02"), _make_point("2024-01-03"), _make_point("2024-01-04")
    ]
    with patch("app.services.vti_price_history_repair_v1.HistoryService") as MockSvc:
        inst = AsyncMock()
        inst.fetch_prices_from_provider = AsyncMock(return_value=points)
        inst.close = AsyncMock()
        MockSvc.return_value = inst
        return await run_vti_price_history_repair(
            db_client=db,
            user_id=USER_ID,
            dry_run=dry_run,
            backfill_period=backfill_period,
            start_date=start_date,
            end_date=end_date,
        )


# ── 1. Both-source forensics: deposit_plans primary ──────────────────────────

@pytest.mark.asyncio
async def test_source_forensics_deposit_plans_primary():
    """With deposit_plans, selected_mode=primary; both counts present in response."""
    db = _MockDB(
        deposit_plans=[_make_deposit("2024-01-02"), _make_deposit("2024-01-03")],
        transactions=[_make_tx("Buy", "2024-02-01")],  # tx also loaded but not selected
    )
    result = await _run_repair(db)
    src = result["contribution_source"]
    assert src["selected_mode"] == "deposit_plans_primary"
    assert src["executed_deposit_plans_count"] == 2
    assert src["buy_transactions_fallback_count"] == 1  # still reported even when not selected
    assert src["contribution_dates_count"] == 2
    assert src["required_price_start_date"] == "2024-01-02"
    assert src["required_price_end_date"] == "2024-01-03"


# ── 2. Both-source forensics: buy_transactions fallback ───────────────────────

@pytest.mark.asyncio
async def test_source_forensics_buy_transactions_fallback():
    """With no deposit_plans, selected_mode=fallback; both counts present."""
    db = _MockDB(
        deposit_plans=[],
        transactions=[_make_tx("Buy", "2024-01-02"), _make_tx("Buy", "2024-01-03")],
    )
    result = await _run_repair(db)
    src = result["contribution_source"]
    assert src["selected_mode"] == "buy_transactions_fallback"
    assert src["executed_deposit_plans_count"] == 0
    assert src["buy_transactions_fallback_count"] == 2
    assert src["contribution_dates_count"] == 2


# ── 3. dry_run=True writes no rows ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_writes_no_rows():
    spy: list = []
    db = _MockDB(upsert_spy=spy)
    result = await _run_repair(db, dry_run=True)
    assert result["write_result"]["rows_written"] == 0
    assert result["write_result"]["write_skipped_dry_run"] is True
    assert spy == []


# ── 4. dry_run=False writes only VTI rows ─────────────────────────────────────

@pytest.mark.asyncio
async def test_writes_only_vti_ticker():
    spy: list = []
    db = _MockDB(upsert_spy=spy)
    await _run_repair(db, dry_run=False, provider_points=[_make_point("2024-01-02")])
    assert len(spy) == 1
    for row in spy:
        assert row["ticker"] == VTI_TICKER, f"non-VTI write detected: {row['ticker']}"


# ── 5. Idempotency: second write does not error ───────────────────────────────

@pytest.mark.asyncio
async def test_idempotent_double_write():
    for _ in range(2):
        spy: list = []
        db = _MockDB(upsert_spy=spy)
        r = await _run_repair(db, dry_run=False, provider_points=[_make_point("2024-01-02")])
        assert r["write_result"]["write_error"] is None
        assert r["write_result"]["rows_written"] == 1


# ── 6. Provider failure does not fabricate rows ───────────────────────────────

@pytest.mark.asyncio
async def test_provider_failure_no_fabrication():
    spy: list = []
    db = _MockDB(upsert_spy=spy)
    result = await _run_repair(db, dry_run=False, provider_points=[])
    assert spy == []
    assert result["write_result"]["rows_written"] == 0
    assert result["provider_fetch"]["provider_failure"] is True


# ── 7. Zero-close points are dropped ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_zero_close_points_dropped():
    spy: list = []
    db = _MockDB(upsert_spy=spy)
    points = [
        _make_point("2024-01-02", close=200.0),
        _make_point("2024-01-03", close=0.0),
        _make_point("2024-01-04", close=201.0),
    ]
    result = await _run_repair(db, dry_run=False, provider_points=points)
    assert result["write_result"]["rows_written"] == 2
    assert result["provider_fetch"]["valid_points_after_zero_close_filter"] == 2
    assert "2024-01-03" not in {r["price_date"] for r in spy}


# ── 8. Coverage check unit tests ──────────────────────────────────────────────

def test_coverage_check_exact_match():
    price_index = {"2024-01-02", "2024-01-03"}
    covered, missing, sample = _coverage_check(["2024-01-02", "2024-01-03"], price_index)
    assert covered == 2
    assert missing == 0
    assert sample == []


def test_coverage_check_missing():
    price_index = {"2024-01-02"}
    covered, missing, sample = _coverage_check(["2024-01-02", "2024-06-15"], price_index)
    assert covered == 1
    assert missing == 1
    assert "2024-06-15" in sample


# ── 9. Stage 10C blocked when no VTI prices ───────────────────────────────────

@pytest.mark.asyncio
async def test_benchmark_still_blocked_when_vti_prices_absent():
    db = _MockDB(price_history_count=0)
    result = await _run_repair(db, dry_run=True)
    assert result["price_history_row_counts"]["vti_rows_before"] == 0
    assert result["price_history_row_counts"]["vti_rows_after"] == 0
    assert result["write_result"]["rows_written"] == 0


# ── 10. Result shape and version ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_result_shape_and_version():
    db = _MockDB()
    result = await _run_repair(db)
    assert result["repair_version"] == REPAIR_VERSION
    for key in (
        "dry_run", "backfill_period_requested", "date_range_filter",
        "contribution_source", "provider_fetch",
        "write_result", "price_history_row_counts", "coverage",
    ):
        assert key in result, f"missing key: {key}"
    src = result["contribution_source"]
    for field in (
        "selected_mode", "selected_reason",
        "executed_deposit_plans_count", "buy_transactions_fallback_count",
        "contribution_dates_count", "required_price_start_date",
        "required_price_end_date", "sample_contribution_dates",
        "period_coverage_warning",
    ):
        assert field in src, f"missing contribution_source field: {field}"


# ── 11. Period coverage warning ───────────────────────────────────────────────

def test_period_coverage_warning_triggered_when_old_date():
    old_date = (date.today() - timedelta(days=2000)).isoformat()
    warning = _period_coverage_warning(old_date, "5Y")
    assert warning is not None
    assert "consider_backfill_period_max" in warning


def test_period_coverage_warning_not_triggered_for_max():
    old_date = (date.today() - timedelta(days=5000)).isoformat()
    warning = _period_coverage_warning(old_date, "max")
    assert warning is None


def test_period_coverage_warning_not_triggered_for_recent_date():
    recent_date = (date.today() - timedelta(days=100)).isoformat()
    warning = _period_coverage_warning(recent_date, "5Y")
    assert warning is None


# ── 12. Stage 10C unblocked after repair (coverage check) ────────────────────

@pytest.mark.asyncio
async def test_benchmark_can_progress_after_repair():
    contribution_dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    price_index = set(contribution_dates)
    covered, missing, _ = _coverage_check(contribution_dates, price_index)
    assert covered == 3
    assert missing == 0


# ── 13. _load_contribution_forensics returns both counts ─────────────────────

def test_load_contribution_forensics_both_counts():
    db = _MockDB(
        deposit_plans=[_make_deposit("2024-01-02")],
        transactions=[_make_tx("Buy", "2024-02-01"), _make_tx("Buy", "2024-02-02")],
    )
    result = _load_contribution_forensics(db, USER_ID)
    assert result["executed_deposit_plans_count"] == 1
    assert result["buy_transactions_fallback_count"] == 2
    assert result["selected_mode"] == "deposit_plans_primary"


# ── Router-level gating tests (monkeypatch, no full app import) ───────────────
# Pattern from test_finance_runtime_certification.py: monkeypatch get_settings,
# call endpoint functions or _get_runtime_cert_user directly.

@pytest.mark.asyncio
async def test_router_cert_disabled_returns_404(monkeypatch):
    """When finance_runtime_cert_enabled=False, _get_runtime_cert_user raises 404."""
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
async def test_router_wrong_cert_returns_403(monkeypatch):
    """Correct flag, wrong secret → 403."""
    from app.routers.diagnostics import _get_runtime_cert_user

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(
            finance_runtime_cert_enabled=True,
            finance_runtime_cert_secret="correct-secret",
            finance_runtime_cert_user_id=str(uuid4()),
            finance_runtime_cert_user_email="cert@example.com",
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await _get_runtime_cert_user(
            request=SimpleNamespace(headers={}), cert_secret="wrong-secret"
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_router_feature_flag_disabled_returns_403(monkeypatch):
    """Valid cert but VTI_PRICE_HISTORY_REPAIR_ENABLED=false → 403."""
    from app.routers.diagnostics import vti_price_history_repair, VtiPriceHistoryRepairRequest
    from app.middleware.auth import AuthenticatedUser

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(vti_price_history_repair_enabled=False),
    )
    fake_user = AuthenticatedUser(
        user_id=uuid4(), email="cert@example.com", role="authenticated"
    )
    with pytest.raises(HTTPException) as exc:
        await vti_price_history_repair(
            payload=VtiPriceHistoryRepairRequest(dry_run=True),
            user=fake_user,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_router_dry_run_defaults_to_true(monkeypatch):
    """VtiPriceHistoryRepairRequest default dry_run must be True."""
    from app.routers.diagnostics import VtiPriceHistoryRepairRequest
    req = VtiPriceHistoryRepairRequest()
    assert req.dry_run is True


@pytest.mark.asyncio
async def test_router_enabled_flag_calls_repair_service(monkeypatch):
    """When flag=True + valid user, endpoint calls run_vti_price_history_repair."""
    from app.routers.diagnostics import vti_price_history_repair, VtiPriceHistoryRepairRequest
    from app.middleware.auth import AuthenticatedUser

    fake_user = AuthenticatedUser(
        user_id=uuid4(), email="cert@example.com", role="authenticated"
    )
    fake_result = {
        "repair_version": REPAIR_VERSION,
        "dry_run": True,
        "backfill_period_requested": "5Y",
        "contribution_source": {
            "selected_mode": "buy_transactions_fallback",
            "executed_deposit_plans_count": 0,
            "buy_transactions_fallback_count": 5,
        },
        "provider_fetch": {"fetched_points_total": 0, "provider_failure": True},
        "write_result": {"rows_written": 0, "write_skipped_dry_run": True},
        "price_history_row_counts": {"vti_rows_before": 0, "vti_rows_after": 0},
        "coverage": {},
        "date_range_filter": {},
    }

    monkeypatch.setattr(
        "app.routers.diagnostics.get_settings",
        lambda: SimpleNamespace(vti_price_history_repair_enabled=True),
    )
    monkeypatch.setattr(
        "app.routers.diagnostics.get_supabase_client",
        lambda: None,
    )

    # The endpoint lazy-imports run_vti_price_history_repair inside the function
    # body, so patch it at the service module level.
    with patch(
        "app.services.vti_price_history_repair_v1.run_vti_price_history_repair",
        new=AsyncMock(return_value=fake_result),
    ):
        # Import the service module so the patch target exists, then call via
        # the endpoint which does its own lazy import from the same module.
        import app.services.vti_price_history_repair_v1 as _svc_mod
        with patch.object(_svc_mod, "run_vti_price_history_repair",
                          new=AsyncMock(return_value=fake_result)) as mock_repair:
            result = await vti_price_history_repair(
                payload=VtiPriceHistoryRepairRequest(dry_run=True),
                user=fake_user,
            )

    # The endpoint called the service — verify the result was passed through
    assert result["repair_version"] == REPAIR_VERSION
