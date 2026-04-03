"""
plaid_client.py — Portfolio War Room v11.0
Sole source of truth for holdings quantity via Plaid Investments API.
All credentials loaded from environment variables — nothing hardcoded.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Optional
import plaid
from plaid.api import plaid_api
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.investments_holdings_get_request_options import InvestmentsHoldingsGetRequestOptions

logger = logging.getLogger(__name__)


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class PlaidHolding:
    """Normalised holding from Plaid /investments/holdings/get."""
    ticker: str              # Normalised ticker (BF.B → BF-B)
    quantity: float          # Shares held (authoritative)
    cost_basis: float        # Average cost basis per share
    institution_price: float # Plaid's last known price (fallback only)
    security_id: str         # Plaid internal security ID
    account_id: str          # Plaid account ID
    security_type: str       # equity / etf / mutual fund / crypto
    name: str                # Full security name
    iso_currency: str = "USD"


@dataclass
class PlaidPortfolio:
    """Complete Plaid snapshot — holdings + metadata."""
    holdings: list[PlaidHolding] = field(default_factory=list)
    account_ids: list[str] = field(default_factory=list)
    cash_usd: float = 0.0
    raw_response: dict = field(default_factory=dict)


# ─── Ticker Symbol Normalisation ──────────────────────────────────────────────

_PLAID_TO_YFINANCE_MAP: dict[str, str] = {
    # Plaid uses dot notation; yfinance / Finnhub use dash
    "BF.B":  "BF-B",
    "BF.A":  "BF-A",
    "BRK.B": "BRK-B",
    "BRK.A": "BRK-A",
}

_YFINANCE_TO_PLAID_MAP: dict[str, str] = {v: k for k, v in _PLAID_TO_YFINANCE_MAP.items()}


def normalise_ticker(raw: str) -> str:
    """Convert Plaid ticker notation to yfinance/Finnhub-compatible form."""
    return _PLAID_TO_YFINANCE_MAP.get(raw.upper(), raw.upper())


def plaid_ticker(normalised: str) -> str:
    """Convert yfinance/Finnhub ticker back to Plaid form if needed."""
    return _YFINANCE_TO_PLAID_MAP.get(normalised.upper(), normalised.upper())


# ─── PlaidClient ──────────────────────────────────────────────────────────────

class PlaidClient:
    """
    Thin wrapper around the Plaid Investments API.

    Required environment variables:
        PLAID_CLIENT_ID   — Plaid dashboard client ID
        PLAID_SECRET      — Plaid secret (sandbox / development / production)
        PLAID_ENV         — 'sandbox' | 'development' | 'production'
        PLAID_ACCESS_TOKEN — OAuth access token for the connected institution
    """

    ENV_MAP = {
        "sandbox":     plaid.Environment.Sandbox,
        "development": plaid.Environment.Development,
        "production":  plaid.Environment.Production,
    }

    def __init__(self) -> None:
        self._client_id   = self._require_env("PLAID_CLIENT_ID")
        self._secret      = self._require_env("PLAID_SECRET")
        self._env_name    = os.environ.get("PLAID_ENV", "sandbox").lower()
        self._access_token = self._require_env("PLAID_ACCESS_TOKEN")
        self._client: Optional[plaid_api.PlaidApi] = None

    # ── Environment helpers ───────────────────────────────────────────────────

    @staticmethod
    def _require_env(name: str) -> str:
        val = os.environ.get(name)
        if not val:
            raise EnvironmentError(
                f"Required environment variable '{name}' is not set. "
                f"Add it to your .env file or Streamlit Cloud secrets."
            )
        return val

    def _get_client(self) -> plaid_api.PlaidApi:
        """Lazy-init Plaid API client (re-use across calls)."""
        if self._client is None:
            env = self.ENV_MAP.get(self._env_name, plaid.Environment.Sandbox)
            configuration = plaid.Configuration(
                host=env,
                api_key={
                    "clientId": self._client_id,
                    "secret":   self._secret,
                },
            )
            api_client = plaid.ApiClient(configuration)
            self._client = plaid_api.PlaidApi(api_client)
        return self._client

    # ── Core fetch ────────────────────────────────────────────────────────────

    def get_holdings(self, account_ids: Optional[list[str]] = None) -> PlaidPortfolio:
        """
        Fetch all investment holdings from Plaid.

        Args:
            account_ids: Optional filter to specific Plaid account IDs.
                         If None, fetches all investment accounts.

        Returns:
            PlaidPortfolio with normalised holdings list.

        Raises:
            plaid.ApiException: On Plaid API errors (rate limit, token expired, etc.)
            EnvironmentError: If required env vars are missing.
        """
        client = self._get_client()

        options = None
        if account_ids:
            options = InvestmentsHoldingsGetRequestOptions(account_ids=account_ids)

        request = InvestmentsHoldingsGetRequest(
            access_token=self._access_token,
            options=options,
        )

        try:
            response = client.investments_holdings_get(request)
        except plaid.ApiException as exc:
            logger.error("Plaid API error [%s]: %s", exc.status, exc.body)
            raise

        return self._parse_response(response)

    # ── Response parser ───────────────────────────────────────────────────────

    def _parse_response(self, response) -> PlaidPortfolio:
        """Convert raw Plaid response into a clean PlaidPortfolio."""
        # Build security_id → ticker / name lookup
        security_map: dict[str, dict] = {}
        for sec in response.securities:
            ticker_raw = getattr(sec, "ticker_symbol", None) or ""
            security_map[sec.security_id] = {
                "ticker": normalise_ticker(ticker_raw) if ticker_raw else "",
                "name":   getattr(sec, "name", "") or "",
                "type":   getattr(sec, "type", "equity") or "equity",
            }

        holdings: list[PlaidHolding] = []
        cash_total = 0.0
        account_ids: list[str] = []

        for h in response.holdings:
            sec_info = security_map.get(h.security_id, {})
            ticker = sec_info.get("ticker", "")
            sec_type = sec_info.get("type", "equity").lower()

            # Accumulate cash separately
            if sec_type == "cash" or ticker == "CUR:USD":
                cash_total += float(h.quantity or 0)
                continue

            if not ticker:
                logger.warning("Holding security_id=%s has no ticker — skipped", h.security_id)
                continue

            if h.account_id not in account_ids:
                account_ids.append(h.account_id)

            holdings.append(PlaidHolding(
                ticker=ticker,
                quantity=float(h.quantity or 0),
                cost_basis=float(h.cost_basis or 0) / max(float(h.quantity or 1), 1e-9),
                institution_price=float(h.institution_price or 0),
                security_id=h.security_id,
                account_id=h.account_id,
                security_type=sec_type,
                name=sec_info.get("name", ""),
                iso_currency=getattr(h, "iso_currency_code", "USD") or "USD",
            ))

        logger.info(
            "Plaid: fetched %d holdings across %d accounts. Cash: $%.2f",
            len(holdings), len(account_ids), cash_total
        )

        return PlaidPortfolio(
            holdings=holdings,
            account_ids=account_ids,
            cash_usd=cash_total,
            raw_response=response.to_dict() if hasattr(response, "to_dict") else {},
        )
