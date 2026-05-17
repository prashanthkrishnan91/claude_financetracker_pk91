"""Runnable entrypoint for the Alert Email Delivery Worker v1 (Stage 3E).

Processes pending alert_delivery_outbox rows and delivers via Resend.
By default, delivery is OFF (dry-run safe). Set env vars to enable.

── Required env vars for real sends ──────────────────────────────────────────
    ALERT_EMAIL_DELIVERY_ENABLED=true   (must be truthy)
    ALERT_EMAIL_PROVIDER=resend
    RESEND_API_KEY=<your_resend_api_key>
    ALERT_EMAIL_FROM=<from_address>
    ALERT_EMAIL_TO=<recipient_address>
    ALERT_EMAIL_DRY_RUN=false           (default is true — explicit opt-out)

── Manual single pass ────────────────────────────────────────────────────────
    cd v2/backend
    python -m app.services.alert.alert_email_delivery_worker_entrypoint

── Continuous loop ───────────────────────────────────────────────────────────
    python -m app.services.alert.alert_email_delivery_worker_entrypoint --loop

── Railway ───────────────────────────────────────────────────────────────────
Run as a SEPARATE Railway service (do NOT wire into the Watchtower loop).
Set the start command to:

    python -m app.services.alert.alert_email_delivery_worker_entrypoint --loop

ALERT_EMAIL_DELIVERY_WORKER_INTERVAL_SECONDS overrides the loop interval
(default 300s / 5 minutes). Invalid or non-positive values fall back to default.
--interval-seconds on the CLI overrides the env var.

Email delivery is intentionally kept separate from Watchtower candidate generation
so that evidence refresh cycles cannot accidentally trigger sends.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

logger = logging.getLogger("alert_email_delivery_worker_entrypoint")

_ENABLED_ENV = "ALERT_EMAIL_DELIVERY_ENABLED"
_INTERVAL_ENV = "ALERT_EMAIL_DELIVERY_WORKER_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 300.0


def _is_delivery_enabled() -> bool:
    """Returns True ONLY when ALERT_EMAIL_DELIVERY_ENABLED is a truthy value."""
    raw = (os.getenv(_ENABLED_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _resolve_interval_seconds() -> float:
    raw = (os.getenv(_INTERVAL_ENV) or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "alert_email_delivery_worker invalid %s=%r — using default %ss",
            _INTERVAL_ENV, raw, DEFAULT_INTERVAL_SECONDS,
        )
        return DEFAULT_INTERVAL_SECONDS
    if val <= 0:
        logger.warning(
            "alert_email_delivery_worker non-positive %s=%r — using default %ss",
            _INTERVAL_ENV, raw, DEFAULT_INTERVAL_SECONDS,
        )
        return DEFAULT_INTERVAL_SECONDS
    return val


def _run_one_pass() -> dict:
    from .alert_email_delivery_worker_v1 import build_alert_email_delivery_worker
    worker = build_alert_email_delivery_worker()
    return worker.run_delivery_pass()


def _run(*, loop: bool, interval_seconds: float) -> int:
    if not loop:
        result = _run_one_pass()
        logger.info(
            "alert_email_delivery_worker mode=single_run "
            "scanned=%s sent=%s failed=%s skipped=%s dry_run=%s provider=%s",
            result["scanned"], result["sent"], result["failed"],
            result["skipped"], result["dry_run"], result["provider"],
        )
        return 0

    logger.info(
        "alert_email_delivery_worker mode=loop interval_seconds=%s",
        interval_seconds,
    )
    while True:
        try:
            t0 = time.monotonic()
            result = _run_one_pass()
            cycle_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "alert_email_delivery_worker loop_cycle_summary "
                "scanned=%s sent=%s failed=%s skipped=%s dry_run=%s "
                "provider=%s cycle_ms=%d interval_seconds=%s",
                result["scanned"], result["sent"], result["failed"],
                result["skipped"], result["dry_run"], result["provider"],
                cycle_ms, interval_seconds,
            )
        except Exception as exc:
            logger.exception(
                "alert_email_delivery_worker loop pass failed: %s", exc
            )
        time.sleep(interval_seconds)


def main(argv: "list[str] | None" = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Alert Email Delivery Worker — Resend provider (Stage 3E)",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously instead of a single pass.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help=(
            f"Loop interval in seconds. Overrides {_INTERVAL_ENV}; "
            f"default is {DEFAULT_INTERVAL_SECONDS:g}s."
        ),
    )
    args = parser.parse_args(argv)

    if not _is_delivery_enabled():
        logger.info(
            "alert_email_delivery_worker not enabled — set %s=true to start "
            "(currently absent or not a truthy value). Exiting cleanly.",
            _ENABLED_ENV,
        )
        return 0

    interval_seconds = (
        args.interval_seconds
        if args.interval_seconds is not None
        else _resolve_interval_seconds()
    )
    return _run(loop=args.loop, interval_seconds=interval_seconds)


if __name__ == "__main__":
    sys.exit(main())
