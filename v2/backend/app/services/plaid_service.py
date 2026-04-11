"""
Plaid service — sync holdings from Robinhood via Plaid Investments API.

Design (carried from v1 with improvements):
  - Call Plaid at most once per 24h (TTL cache in plaid_sync_log table)
  - Force-pull option for same-day trades
  - Upsert positions in Supabase (Plaid is authoritative for share quantities)
  - Log every sync to plaid_sync_log for audit trail
  - Graceful degradation: if Plaid fails, positions table still has last-known data
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# Plaid ticker → standard ticker normalization
_PLAID_TICKER_MAP: dict[str, str] = {
    "BF.B": "BF-B", "BF.A": "BF-A",
    "BRK.B": "BRK-B", "BRK.A": "BRK-A",
}

# Default 24-hour cache TTL
_CACHE_TTL_HOURS = 24


@dataclass
class SyncResult:
    """Result of a Plaid sync operation."""
    status: str  # success, cached, error
    message: str
    holdings_count: int = 0
    cash_balance: float = 0.0
    positions_updated: int = 0
    positions_created: int = 0
    synced_at: Optional[datetime] = None
    duration_ms: int = 0


@dataclass
class SyncStatus:
    """Current Plaid sync status."""
    synced_at: Optional[datetime] = None
    holdings_count: int = 0
    cash_balance: float = 0.0
    status: str = "never_synced"
    age_hours: float = 0.0

    @property
    def is_fresh(self) -> bool:
        # Only treat as fresh if the last sync was successful — error syncs
        # must not block retries even within the 24h window.
        return (
            self.synced_at is not None
            and self.age_hours < _CACHE_TTL_HOURS
            and self.status == "success"
        )


class PlaidSyncService:
    """Plaid Investments API integration for Robinhood."""

    def __init__(self, user_id: UUID, supabase_client, decrypt_fn=None):
        self.user_id = user_id
        self.client = supabase_client
        self._decrypt = decrypt_fn

    async def get_sync_status(self) -> SyncStatus:
        """Get the current Plaid sync status."""
        result = (
            self.client.table("plaid_sync_log")
            .select("*")
            .eq("user_id", str(self.user_id))
            .order("synced_at", desc=True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return SyncStatus()

        row = result.data[0]
        synced_at = datetime.fromisoformat(row["synced_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - synced_at).total_seconds() / 3600

        return SyncStatus(
            synced_at=synced_at,
            holdings_count=row.get("holdings_count", 0),
            cash_balance=float(row.get("cash_balance", 0)),
            status=row.get("status", "unknown"),
            age_hours=age,
        )

    async def sync_holdings(self, force: bool = False) -> SyncResult:
        """Sync holdings from Plaid.

        Respects 24h TTL unless force=True.
        """
        start = time.time()

        # Check cache unless forced
        if not force:
            status = await self.get_sync_status()
            if status.is_fresh:
                return SyncResult(
                    status="cached",
                    message=f"Synced {status.age_hours:.1f}h ago — use force=true to re-sync",
                    holdings_count=status.holdings_count,
                    cash_balance=status.cash_balance,
                    synced_at=status.synced_at,
                )

        # Get user's Plaid credentials
        user_row = (
            self.client.table("users")
            .select("encrypted_plaid_access_token, encrypted_plaid_client_id, encrypted_plaid_secret, plaid_env")
            .eq("id", str(self.user_id))
            .single()
            .execute()
        )

        if not user_row.data:
            return SyncResult(status="error", message="User not found")

        user = user_row.data
        if not user.get("encrypted_plaid_access_token"):
            return SyncResult(status="error", message="Plaid credentials not configured")

        # Decrypt credentials
        try:
            access_token = self._decrypt(user["encrypted_plaid_access_token"])
            client_id = self._decrypt(user["encrypted_plaid_client_id"])
            secret = self._decrypt(user["encrypted_plaid_secret"])
            plaid_env = user.get("plaid_env", "production")
        except Exception as e:
            return SyncResult(status="error", message=f"Failed to decrypt credentials: {e}")

        # Call Plaid API
        try:
            holdings, cash, account_ids, raw = await self._call_plaid(
                access_token, client_id, secret, plaid_env
            )
        except Exception as e:
            # Log the error
            self._log_sync(status="error", error_message=str(e), duration_ms=int((time.time() - start) * 1000))
            return SyncResult(status="error", message=f"Plaid API error: {e}")

        # Upsert positions
        created, updated = await self._upsert_positions(holdings)

        duration_ms = int((time.time() - start) * 1000)

        # Log successful sync
        self._log_sync(
            status="success",
            account_ids=account_ids,
            holdings_count=len(holdings),
            cash_balance=cash,
            duration_ms=duration_ms,
            raw_response=raw,
        )

        return SyncResult(
            status="success",
            message=f"Synced {len(holdings)} holdings from Robinhood",
            holdings_count=len(holdings),
            cash_balance=cash,
            positions_updated=updated,
            positions_created=created,
            synced_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
        )

    async def _call_plaid(
        self, access_token: str, client_id: str, secret: str, env: str
    ) -> tuple[list[dict], float, list[str], dict]:
        """Call Plaid /investments/holdings/get via direct HTTP.

        Uses httpx instead of the plaid-python SDK to avoid pydantic v2
        composed-schema validation errors (e.g. InvestmentAccount.balances
        mismatch) that occur with certain Plaid API response shapes.

        Returns: (holdings_list, cash_usd, account_ids, raw_response)
        """
        import httpx

        host_map = {
            "sandbox": "https://sandbox.plaid.com",
            "development": "https://production.plaid.com",  # Development retired
            "production": "https://production.plaid.com",
        }
        base_url = host_map.get(env, "https://production.plaid.com")

        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                f"{base_url}/investments/holdings/get",
                json={
                    "client_id": client_id,
                    "secret": secret,
                    "access_token": access_token,
                },
            )
            if not resp.is_success:
                err_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                raise RuntimeError(err_data.get("error_message") or resp.text)
            data = resp.json()

        # Build security_id → ticker map
        security_map: dict[str, dict] = {}
        for sec in data.get("securities", []):
            sid = sec.get("security_id")
            if not sid:
                continue
            ticker = sec.get("ticker_symbol") or ""
            name = sec.get("name") or ""
            sec_type = sec.get("type") or "equity"
            ticker = _PLAID_TICKER_MAP.get(ticker, ticker)
            security_map[sid] = {"ticker": ticker, "name": name, "type": sec_type}

        # Parse holdings
        holdings: list[dict] = []
        for h in data.get("holdings", []):
            sec_info = security_map.get(h.get("security_id") or "", {})
            ticker = sec_info.get("ticker", "")
            if not ticker or ticker == "CUR:USD":
                continue

            raw_qty = h.get("quantity")
            raw_cost = h.get("cost_basis")
            raw_price = h.get("institution_price")

            quantity = float(raw_qty) if raw_qty is not None else 0.0
            cost_basis_per_share = (
                float(raw_cost) / quantity
                if (quantity > 0 and raw_cost is not None)
                else 0.0
            )
            institution_price = float(raw_price) if raw_price is not None else 0.0

            holdings.append({
                "ticker": ticker,
                "name": sec_info.get("name", ticker),
                "quantity": quantity,
                "cost_basis": cost_basis_per_share,
                "institution_price": institution_price,
                "security_type": sec_info.get("type", "equity"),
            })

        # Extract cash balance from accounts
        cash = 0.0
        account_ids: list[str] = []
        for acct in data.get("accounts", []):
            account_ids.append(acct.get("account_id") or "")
            balances = acct.get("balances") or {}
            available = balances.get("available")
            if available is not None:
                cash += float(available)

        raw = {"holdings_count": len(holdings), "accounts": len(data.get("accounts", []))}
        return holdings, cash, account_ids, raw

    async def _upsert_positions(self, holdings: list[dict]) -> tuple[int, int]:
        """Upsert holdings into positions table.

        Plaid is authoritative for share quantities.
        Cost basis is updated only if it differs.
        Returns (created_count, updated_count).
        """
        # Get existing positions
        existing = (
            self.client.table("positions")
            .select("ticker, shares, avg_cost")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data
        existing_map = {r["ticker"]: r for r in existing}

        created = 0
        updated = 0

        for h in holdings:
            ticker = h["ticker"]
            existing_pos = existing_map.get(ticker)

            # Determine category based on security type
            sec_type = h.get("security_type", "equity")
            if sec_type == "cryptocurrency":
                category = "Crypto"
            elif sec_type == "etf":
                category = "ETF"
            else:
                category = "Core"  # Will be refined by bootstrap data if available

            if existing_pos:
                # Update only if shares changed
                if float(existing_pos["shares"]) != h["quantity"]:
                    self.client.table("positions").update({
                        "shares": h["quantity"],
                        "avg_cost": h["cost_basis"],
                        "source": "plaid",
                        "last_synced_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("user_id", str(self.user_id)).eq("ticker", ticker).execute()
                    updated += 1
            else:
                # Create new position
                self.client.table("positions").insert({
                    "user_id": str(self.user_id),
                    "ticker": ticker,
                    "name": h["name"],
                    "category": category,
                    "shares": h["quantity"],
                    "avg_cost": h["cost_basis"],
                    "source": "plaid",
                    "last_synced_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
                created += 1

        return created, updated

    def _log_sync(
        self,
        status: str,
        account_ids: list[str] = None,
        holdings_count: int = 0,
        cash_balance: float = 0,
        duration_ms: int = 0,
        error_message: str = None,
        raw_response: dict = None,
    ):
        """Write to plaid_sync_log table."""
        try:
            self.client.table("plaid_sync_log").insert({
                "user_id": str(self.user_id),
                "account_ids": account_ids or [],
                "holdings_count": holdings_count,
                "cash_balance": cash_balance,
                "status": status,
                "error_message": error_message,
                "duration_ms": duration_ms,
                "raw_response": raw_response,
            }).execute()
        except Exception as e:
            logger.error("Failed to log sync: %s", e)
