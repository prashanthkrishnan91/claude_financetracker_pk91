"""Tests for the Plaid sync service — SyncResult, SyncStatus, ticker normalization,
and the httpx-based _call_plaid implementation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.plaid_service import (
    PlaidSyncService,
    SyncResult,
    SyncStatus,
    _CACHE_TTL_HOURS,
    _PLAID_TICKER_MAP,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_plaid_response(
    holdings=None,
    securities=None,
    accounts=None,
) -> dict:
    """Minimal Plaid /investments/holdings/get JSON response."""
    if securities is None:
        securities = [
            {
                "security_id": "sec-aapl",
                "ticker_symbol": "AAPL",
                "name": "Apple Inc.",
                "type": "equity",
            },
            {
                "security_id": "sec-nvda",
                "ticker_symbol": "NVDA",
                "name": "NVIDIA Corporation",
                "type": "equity",
            },
        ]
    if holdings is None:
        holdings = [
            {
                "security_id": "sec-aapl",
                "account_id": "acct-1",
                "quantity": 10.5,
                "cost_basis": 1890.00,
                "institution_price": 185.50,
            },
            {
                "security_id": "sec-nvda",
                "account_id": "acct-1",
                "quantity": 5.0,
                "cost_basis": 3000.00,
                "institution_price": 620.00,
            },
        ]
    if accounts is None:
        accounts = [
            {
                "account_id": "acct-1",
                "balances": {"available": 1042.17, "current": 1042.17},
            }
        ]
    return {"securities": securities, "holdings": holdings, "accounts": accounts}


def _make_service(user_id=None) -> PlaidSyncService:
    uid = user_id or uuid4()
    mock_client = MagicMock()
    decrypt_fn = lambda v: f"decrypted-{v}"
    return PlaidSyncService(uid, mock_client, decrypt_fn)


# ── SyncResult ────────────────────────────────────────────────────────────────

class TestSyncResult:
    def test_success(self):
        r = SyncResult(
            status="success",
            message="Synced 39 holdings from Robinhood",
            holdings_count=39,
            cash_balance=1042.17,
            positions_updated=3,
            positions_created=2,
            synced_at=datetime.now(timezone.utc),
            duration_ms=1230,
        )
        assert r.status == "success"
        assert r.holdings_count == 39

    def test_cached(self):
        r = SyncResult(
            status="cached",
            message="Synced 2.3h ago — use force=true to re-sync",
        )
        assert r.status == "cached"
        assert r.holdings_count == 0

    def test_error(self):
        r = SyncResult(status="error", message="Plaid API error: timeout")
        assert r.status == "error"


# ── SyncStatus ────────────────────────────────────────────────────────────────

class TestSyncStatus:
    def test_fresh_status(self):
        s = SyncStatus(
            synced_at=datetime.now(timezone.utc),
            holdings_count=39,
            cash_balance=1042.17,
            status="success",
            age_hours=2.5,
        )
        assert s.is_fresh

    def test_stale_status(self):
        s = SyncStatus(age_hours=25.0)
        assert not s.is_fresh

    def test_default_never_synced(self):
        s = SyncStatus()
        assert s.synced_at is None
        assert s.status == "never_synced"
        assert not s.is_fresh

    def test_cache_ttl_boundary(self):
        """Exactly at TTL is NOT fresh."""
        s = SyncStatus(age_hours=_CACHE_TTL_HOURS)
        assert not s.is_fresh

    def test_just_under_ttl(self):
        s = SyncStatus(synced_at=datetime.now(timezone.utc), age_hours=_CACHE_TTL_HOURS - 0.1, status="success")
        assert s.is_fresh

    def test_error_sync_not_fresh(self):
        """A previous error sync should not block retries (is_fresh = False)."""
        s = SyncStatus(
            synced_at=datetime.now(timezone.utc),
            status="error",
            age_hours=1.0,
        )
        assert not s.is_fresh


# ── Ticker normalization ──────────────────────────────────────────────────────

class TestPlaidTickerNormalization:
    def test_berkshire_normalization(self):
        assert _PLAID_TICKER_MAP.get("BRK.B") == "BRK-B"
        assert _PLAID_TICKER_MAP.get("BRK.A") == "BRK-A"

    def test_brown_forman_normalization(self):
        assert _PLAID_TICKER_MAP.get("BF.B") == "BF-B"
        assert _PLAID_TICKER_MAP.get("BF.A") == "BF-A"

    def test_normal_ticker_not_mapped(self):
        assert _PLAID_TICKER_MAP.get("NVDA") is None
        assert _PLAID_TICKER_MAP.get("AAPL") is None


# ── _call_plaid (httpx path) ──────────────────────────────────────────────────

class TestCallPlaid:
    """Unit tests for PlaidSyncService._call_plaid using mocked httpx."""

    def _make_http_response(self, data: dict, status_code: int = 200):
        """Build a minimal mock httpx.Response."""
        mock = MagicMock()
        mock.is_success = status_code < 400
        mock.status_code = status_code
        mock.headers = {"content-type": "application/json"}
        mock.json.return_value = data
        mock.text = json.dumps(data)
        return mock

    @pytest.mark.asyncio
    async def test_success_two_holdings(self):
        svc = _make_service()
        data = _make_plaid_response()

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            holdings, cash, account_ids, raw = await svc._call_plaid(
                "tok", "cid", "sec", "production"
            )

        assert len(holdings) == 2
        assert holdings[0]["ticker"] == "AAPL"
        assert holdings[0]["quantity"] == 10.5
        # cost_basis per share = 1890 / 10.5 = 180.0
        assert abs(holdings[0]["cost_basis"] - 180.0) < 0.01
        assert holdings[0]["institution_price"] == 185.50
        assert holdings[1]["ticker"] == "NVDA"
        assert cash == pytest.approx(1042.17)
        assert "acct-1" in account_ids

    @pytest.mark.asyncio
    async def test_sandbox_url(self):
        """Sandbox env should call sandbox.plaid.com."""
        svc = _make_service()
        data = _make_plaid_response(holdings=[], accounts=[])

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            await svc._call_plaid("tok", "cid", "sec", "sandbox")

        call_args = mock_client.post.call_args
        assert "sandbox.plaid.com" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_production_url(self):
        """Production env should call production.plaid.com."""
        svc = _make_service()
        data = _make_plaid_response(holdings=[], accounts=[])

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            await svc._call_plaid("tok", "cid", "sec", "production")

        call_args = mock_client.post.call_args
        assert "production.plaid.com" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_development_maps_to_production(self):
        """Development is retired; maps to production.plaid.com."""
        svc = _make_service()
        data = _make_plaid_response(holdings=[], accounts=[])

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            await svc._call_plaid("tok", "cid", "sec", "development")

        call_args = mock_client.post.call_args
        assert "production.plaid.com" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_plaid_error_response_raises(self):
        """A non-2xx response should raise RuntimeError with Plaid error_message."""
        svc = _make_service()
        error_body = {"error_code": "INVALID_ACCESS_TOKEN", "error_message": "access token is invalid"}

        mock_http_resp = self._make_http_response(error_body, status_code=400)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            with pytest.raises(RuntimeError, match="access token is invalid"):
                await svc._call_plaid("bad-tok", "cid", "sec", "production")

    @pytest.mark.asyncio
    async def test_cur_usd_holding_skipped(self):
        """CUR:USD cash positions must not appear in holdings list."""
        svc = _make_service()
        data = _make_plaid_response(
            securities=[
                {"security_id": "sec-cash", "ticker_symbol": "CUR:USD", "name": "USD Cash", "type": "cash"},
                {"security_id": "sec-aapl", "ticker_symbol": "AAPL", "name": "Apple Inc.", "type": "equity"},
            ],
            holdings=[
                {"security_id": "sec-cash", "account_id": "acct-1", "quantity": 500.0, "cost_basis": 500.0, "institution_price": 1.0},
                {"security_id": "sec-aapl", "account_id": "acct-1", "quantity": 3.0, "cost_basis": 600.0, "institution_price": 200.0},
            ],
            accounts=[{"account_id": "acct-1", "balances": {"available": 100.0}}],
        )

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            holdings, cash, _, _ = await svc._call_plaid("tok", "cid", "sec", "production")

        tickers = [h["ticker"] for h in holdings]
        assert "CUR:USD" not in tickers
        assert "AAPL" in tickers
        assert len(holdings) == 1

    @pytest.mark.asyncio
    async def test_holding_without_ticker_skipped(self):
        """Holdings with no ticker symbol are silently skipped."""
        svc = _make_service()
        data = _make_plaid_response(
            securities=[
                {"security_id": "sec-noticker", "ticker_symbol": None, "name": "Unknown Asset", "type": "equity"},
                {"security_id": "sec-aapl", "ticker_symbol": "AAPL", "name": "Apple Inc.", "type": "equity"},
            ],
            holdings=[
                {"security_id": "sec-noticker", "account_id": "acct-1", "quantity": 1.0, "cost_basis": 10.0, "institution_price": 10.0},
                {"security_id": "sec-aapl", "account_id": "acct-1", "quantity": 2.0, "cost_basis": 360.0, "institution_price": 180.0},
            ],
            accounts=[{"account_id": "acct-1", "balances": {"available": 0.0}}],
        )

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            holdings, _, _, _ = await svc._call_plaid("tok", "cid", "sec", "production")

        assert len(holdings) == 1
        assert holdings[0]["ticker"] == "AAPL"

    @pytest.mark.asyncio
    async def test_none_quantity_defaults_to_zero(self):
        """None quantity should default to 0.0, not crash."""
        svc = _make_service()
        data = _make_plaid_response(
            securities=[{"security_id": "sec-aapl", "ticker_symbol": "AAPL", "name": "Apple", "type": "equity"}],
            holdings=[{"security_id": "sec-aapl", "account_id": "acct-1", "quantity": None, "cost_basis": None, "institution_price": None}],
            accounts=[{"account_id": "acct-1", "balances": {"available": None}}],
        )

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            holdings, cash, _, _ = await svc._call_plaid("tok", "cid", "sec", "production")

        # Zero-quantity holdings are still included (not filtered here)
        assert holdings[0]["quantity"] == 0.0
        assert holdings[0]["cost_basis"] == 0.0
        assert holdings[0]["institution_price"] == 0.0
        assert cash == 0.0

    @pytest.mark.asyncio
    async def test_ticker_normalization_brk(self):
        """BRK.B is normalised to BRK-B."""
        svc = _make_service()
        data = _make_plaid_response(
            securities=[{"security_id": "sec-brk", "ticker_symbol": "BRK.B", "name": "Berkshire B", "type": "equity"}],
            holdings=[{"security_id": "sec-brk", "account_id": "acct-1", "quantity": 2.0, "cost_basis": 700.0, "institution_price": 360.0}],
            accounts=[{"account_id": "acct-1", "balances": {"available": 0.0}}],
        )

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            holdings, _, _, _ = await svc._call_plaid("tok", "cid", "sec", "production")

        assert holdings[0]["ticker"] == "BRK-B"

    @pytest.mark.asyncio
    async def test_multi_account_cash_sum(self):
        """Cash is summed across all accounts."""
        svc = _make_service()
        data = _make_plaid_response(
            holdings=[],
            accounts=[
                {"account_id": "acct-1", "balances": {"available": 500.0}},
                {"account_id": "acct-2", "balances": {"available": 250.75}},
            ],
        )

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            _, cash, account_ids, _ = await svc._call_plaid("tok", "cid", "sec", "production")

        assert cash == pytest.approx(750.75)
        assert set(account_ids) == {"acct-1", "acct-2"}

    @pytest.mark.asyncio
    async def test_account_with_none_balance(self):
        """None available balance should not raise — treated as 0."""
        svc = _make_service()
        data = _make_plaid_response(
            holdings=[],
            accounts=[
                {"account_id": "acct-1", "balances": {"available": None}},
            ],
        )

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            _, cash, _, _ = await svc._call_plaid("tok", "cid", "sec", "production")

        assert cash == 0.0

    @pytest.mark.asyncio
    async def test_missing_balances_key(self):
        """Account without balances key should not crash."""
        svc = _make_service()
        data = _make_plaid_response(
            holdings=[],
            accounts=[{"account_id": "acct-1"}],  # No 'balances' key
        )

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            _, cash, _, _ = await svc._call_plaid("tok", "cid", "sec", "production")

        assert cash == 0.0

    @pytest.mark.asyncio
    async def test_crypto_security_type(self):
        """Crypto holdings should have security_type='cryptocurrency'."""
        svc = _make_service()
        data = _make_plaid_response(
            securities=[{"security_id": "sec-btc", "ticker_symbol": "BTC", "name": "Bitcoin", "type": "cryptocurrency"}],
            holdings=[{"security_id": "sec-btc", "account_id": "acct-1", "quantity": 0.5, "cost_basis": 20000.0, "institution_price": 45000.0}],
            accounts=[{"account_id": "acct-1", "balances": {"available": 0.0}}],
        )

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            holdings, _, _, _ = await svc._call_plaid("tok", "cid", "sec", "production")

        assert holdings[0]["security_type"] == "cryptocurrency"

    @pytest.mark.asyncio
    async def test_cost_basis_per_share_calculation(self):
        """cost_basis in returned holding must be per-share, not total."""
        svc = _make_service()
        # 15 shares, total cost_basis = $3000 → per-share = $200
        data = _make_plaid_response(
            securities=[{"security_id": "sec-x", "ticker_symbol": "SPY", "name": "S&P 500 ETF", "type": "etf"}],
            holdings=[{"security_id": "sec-x", "account_id": "acct-1", "quantity": 15.0, "cost_basis": 3000.0, "institution_price": 520.0}],
            accounts=[{"account_id": "acct-1", "balances": {"available": 0.0}}],
        )

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            holdings, _, _, _ = await svc._call_plaid("tok", "cid", "sec", "production")

        assert abs(holdings[0]["cost_basis"] - 200.0) < 0.001

    @pytest.mark.asyncio
    async def test_request_body_structure(self):
        """Verifies the JSON body sent to Plaid contains required fields."""
        svc = _make_service()
        data = _make_plaid_response(holdings=[], accounts=[])

        mock_http_resp = self._make_http_response(data)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_http_resp)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_ctx):
            await svc._call_plaid("my-access-token", "my-client-id", "my-secret", "production")

        call_kwargs = mock_client.post.call_args[1]
        body = call_kwargs["json"]
        assert body["access_token"] == "my-access-token"
        assert body["client_id"] == "my-client-id"
        assert body["secret"] == "my-secret"


# ── sync_holdings (integration-style unit test) ────────────────────────────────

class TestSyncHoldings:
    """Test sync_holdings with mocked Supabase and mocked _call_plaid."""

    def _make_svc_with_credentials(self, user_id=None):
        uid = user_id or uuid4()
        mock_db = MagicMock()

        # Simulate no existing sync log (never synced)
        mock_db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []

        # Simulate user with encrypted credentials
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "encrypted_plaid_access_token": "enc_tok",
            "encrypted_plaid_client_id": "enc_cid",
            "encrypted_plaid_secret": "enc_sec",
            "plaid_env": "production",
        }

        # Simulate no existing positions
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        # Silence insert / update calls
        mock_db.table.return_value.insert.return_value.execute.return_value.data = [{}]
        mock_db.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{}]

        decrypt_fn = lambda v: v.replace("enc_", "")
        return PlaidSyncService(uid, mock_db, decrypt_fn), mock_db

    @pytest.mark.asyncio
    async def test_never_synced_triggers_api_call(self):
        """First sync (no cache) should call Plaid API."""
        svc, _ = self._make_svc_with_credentials()

        plaid_data = _make_plaid_response()
        with patch.object(svc, "_call_plaid", new=AsyncMock(return_value=(
            [{"ticker": "AAPL", "name": "Apple", "quantity": 10.0, "cost_basis": 180.0, "institution_price": 185.0, "security_type": "equity"}],
            1000.0,
            ["acct-1"],
            {},
        ))):
            result = await svc.sync_holdings(force=False)

        assert result.status == "success"
        assert result.holdings_count == 1

    @pytest.mark.asyncio
    async def test_missing_credentials_returns_error(self):
        """If user has no access token, sync must return error without calling Plaid."""
        uid = uuid4()
        mock_db = MagicMock()

        # No previous sync log
        mock_db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []

        # User row exists but has no access token
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "encrypted_plaid_access_token": None,
            "encrypted_plaid_client_id": None,
            "encrypted_plaid_secret": None,
            "plaid_env": "production",
        }

        svc = PlaidSyncService(uid, mock_db, lambda v: v)
        result = await svc.sync_holdings()

        assert result.status == "error"
        assert "not configured" in result.message.lower()

    @pytest.mark.asyncio
    async def test_cached_sync_returns_without_api_call(self):
        """A fresh cache should short-circuit before calling Plaid API."""
        uid = uuid4()
        mock_db = MagicMock()

        now_iso = datetime.now(timezone.utc).isoformat()

        # Simulate a recent successful sync (1 hour ago)
        mock_db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
            {
                "synced_at": now_iso,
                "holdings_count": 15,
                "cash_balance": 500.0,
                "status": "success",
            }
        ]

        svc = PlaidSyncService(uid, mock_db, lambda v: v)

        with patch.object(svc, "_call_plaid", new=AsyncMock()) as mock_call:
            result = await svc.sync_holdings(force=False)

        mock_call.assert_not_called()
        assert result.status == "cached"

    @pytest.mark.asyncio
    async def test_force_bypasses_cache(self):
        """force=True should call Plaid even if cache is fresh."""
        svc, mock_db = self._make_svc_with_credentials()

        with patch.object(svc, "_call_plaid", new=AsyncMock(return_value=(
            [], 0.0, [], {}
        ))):
            with patch.object(svc, "_log_sync"):
                result = await svc.sync_holdings(force=True)

        # Even with empty holdings, it ran (not cached)
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_plaid_api_error_logged_and_returned(self):
        """Plaid API failure should log the error and return error SyncResult."""
        svc, mock_db = self._make_svc_with_credentials()

        with patch.object(svc, "_call_plaid", new=AsyncMock(side_effect=RuntimeError("INVALID_ACCESS_TOKEN"))):
            with patch.object(svc, "_log_sync") as mock_log:
                result = await svc.sync_holdings(force=True)

        assert result.status == "error"
        assert "INVALID_ACCESS_TOKEN" in result.message
        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["status"] == "error"
