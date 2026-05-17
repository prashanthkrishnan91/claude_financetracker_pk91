"""Portfolio Intelligence Platform v2 — FastAPI Application.

Entry point: uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import action_feedback, ai, alert_candidates, allocation, analytics, auth, decision_logs, decisions, deposits, deploy_v3, diagnostics, drip, intel_v3, portfolio, positions, prices, recommendations, sync


def _configure_yfinance_cache() -> None:
    """Set a writable yfinance tz-cache path to suppress noisy warnings."""
    cache_dir = Path(os.getenv("YFINANCE_CACHE_DIR", "/tmp/yfinance-cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        import yfinance as yf  # lazy optional dependency

        set_cache = getattr(yf, "set_tz_cache_location", None)
        if callable(set_cache):
            set_cache(str(cache_dir))
    except Exception:
        # Keep startup resilient even when yfinance is unavailable.
        return


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle events."""
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    logger = logging.getLogger("app")
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Debug mode: {settings.debug}")
    _configure_yfinance_cache()

    yield

    logger.info("Shutting down")


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Personal portfolio intelligence platform — "
            "real-time pricing, tax-optimized recommendations, DRIP analytics."
        ),
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # CORS — allow_credentials cannot be True when allow_origins=["*"]
    # allow_origin_regex covers all Vercel preview & production deployments
    # without needing to enumerate individual URLs in env vars.
    if settings.cors_allow_all:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_origin_regex=r"https://[a-zA-Z0-9\-]+\.vercel\.app",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Register routers
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(portfolio.router, prefix="/api/v1")
    app.include_router(positions.router, prefix="/api/v1")
    app.include_router(prices.router, prefix="/api/v1")
    app.include_router(recommendations.router, prefix="/api/v1")
    app.include_router(sync.router, prefix="/api/v1")
    app.include_router(deposits.router, prefix="/api/v1")
    app.include_router(drip.router, prefix="/api/v1")
    app.include_router(ai.router, prefix="/api/v1")
    app.include_router(decisions.router, prefix="/api/v1")
    app.include_router(decision_logs.router, prefix="/api/v1")
    app.include_router(analytics.router, prefix="/api/v1")
    app.include_router(allocation.router, prefix="/api/v1")
    app.include_router(diagnostics.router, prefix="/api/v1")
    app.include_router(intel_v3.router, prefix="/api/v1")
    app.include_router(deploy_v3.router, prefix="/api/v1")
    app.include_router(action_feedback.router, prefix="/api/v1")
    app.include_router(alert_candidates.router, prefix="/api/v1")

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "version": settings.app_version}

    return app


app = create_app()
