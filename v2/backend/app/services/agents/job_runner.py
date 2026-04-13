"""Background job runner — fetches API keys, builds an orchestrator, runs it.

Separated from the router so the BackgroundTasks callback has zero FastAPI
dependencies and is easy to unit-test.
"""

from __future__ import annotations

import logging
from uuid import UUID

from ...config import get_settings
from ...database import get_supabase_client
from ..crypto_service import decrypt_value
from .orchestrator import AgentOrchestrator

logger = logging.getLogger(__name__)


def _user_keys(user_id: UUID) -> dict[str, str]:
    """Pull per-user API keys from the users table, falling back to env."""
    settings = get_settings()
    out = {
        "anthropic": settings.anthropic_api_key or "",
        "finnhub": settings.finnhub_api_key or "",
        "polygon": settings.polygon_api_key or "",
        "alpaca_key": settings.alpaca_api_key or "",
        "alpaca_secret": settings.alpaca_secret_key or "",
    }
    try:
        db = get_supabase_client()
        row = (
            db.table("users")
            .select(
                "encrypted_anthropic_api_key, encrypted_finnhub_api_key, "
                "encrypted_polygon_api_key, encrypted_alpaca_api_key, "
                "encrypted_alpaca_secret_key"
            )
            .eq("id", str(user_id))
            .single()
            .execute()
        )
        if not row.data:
            return out
        for src, dst in (
            ("encrypted_anthropic_api_key", "anthropic"),
            ("encrypted_finnhub_api_key", "finnhub"),
            ("encrypted_polygon_api_key", "polygon"),
            ("encrypted_alpaca_api_key", "alpaca_key"),
            ("encrypted_alpaca_secret_key", "alpaca_secret"),
        ):
            enc = row.data.get(src)
            if enc:
                try:
                    out[dst] = decrypt_value(enc)
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("Failed to fetch user API keys: %s", exc)
    return out


def _make_price_service(keys: dict[str, str]):
    from ..price_engine import PriceService
    return PriceService(
        finnhub_key=keys.get("finnhub", ""),
        alpaca_key=keys.get("alpaca_key", ""),
        alpaca_secret=keys.get("alpaca_secret", ""),
        polygon_key=keys.get("polygon", ""),
    )


def build_orchestrator(
    user_id: UUID,
    deposit_amount: float,
    sale_proceeds: float,
) -> AgentOrchestrator:
    keys = _user_keys(user_id)
    return AgentOrchestrator(
        user_id=user_id,
        deposit_amount=deposit_amount,
        sale_proceeds=sale_proceeds,
        price_service=_make_price_service(keys),
        anthropic_api_key=keys.get("anthropic", ""),
        finnhub_key=keys.get("finnhub", ""),
        polygon_key=keys.get("polygon", ""),
    )


async def run_agent_pipeline(
    user_id: UUID,
    run_id: str,
    deposit_amount: float,
    sale_proceeds: float,
) -> None:
    """Entry point for FastAPI BackgroundTasks."""
    orch = build_orchestrator(user_id, deposit_amount, sale_proceeds)
    await orch.run(run_id)
