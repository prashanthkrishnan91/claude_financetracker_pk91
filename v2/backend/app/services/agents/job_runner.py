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
    """Entry point for FastAPI BackgroundTasks — always marks the run terminal.

    Any error in ``build_orchestrator`` (e.g., missing API keys, Supabase
    outage) is caught and written to the ``agent_runs`` row as a ``failed``
    terminal state with a fallback summary. This guarantees the frontend
    poller eventually sees ``status`` in ``(completed, failed)`` and never
    leaves a run stuck in ``running``.
    """
    logger.info("Agent run started — id=%s user=%s", run_id, user_id)
    try:
        orch = build_orchestrator(user_id, deposit_amount, sale_proceeds)
    except Exception as exc:
        logger.exception("Failed to build orchestrator for run %s", run_id)
        await _force_fail_run(run_id, str(exc), user_id=user_id)
        return
    try:
        result = await orch.run(run_id)
        logger.info("Agent run finished — id=%s status=%s", run_id, result.status)
    except Exception as exc:
        # Defensive — orch.run has its own try/except, but if BackgroundTasks
        # ever swallows an error below the orchestrator we still mark the row.
        logger.exception("Unhandled error in agent run %s", run_id)
        await _force_fail_run(run_id, str(exc), user_id=user_id)


async def _force_fail_run(run_id: str, error: str, *, user_id: UUID | None = None) -> None:
    """Mark an agent_runs row failed with a fallback summary, best-effort."""
    from datetime import datetime, timezone
    try:
        db = get_supabase_client()
        query = db.table("agent_runs").update({
            "status": "failed",
            "current_agent": "Failed",
            "progress_pct": 100,
            "error_message": error[:500],
            "summary": "Analysis temporarily unavailable — please retry.",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", run_id)
        if user_id is not None:
            query = query.eq("user_id", str(user_id))
        res = query.select("id").execute()
        matched_rows = len(res.data or [])
        logger.info(
            "agent_run.terminal_update status=failed job_id=%s matched_rows=%d",
            run_id,
            matched_rows,
        )
        if matched_rows == 0:
            raise RuntimeError(f"agent_runs terminal failed update matched zero rows for {run_id}")
    except Exception as exc:
        logger.warning("Failed to mark run %s as failed: %s", run_id, exc)
