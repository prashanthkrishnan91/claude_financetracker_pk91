"""Runnable entrypoint for the Alert Email Delivery Worker v1 (Stage 3E).

Processes pending alert_delivery_outbox rows and delivers via Resend.
By default, delivery is OFF (dry-run safe). Set env vars to enable.

── Railway activation (Stage 3F) ─────────────────────────────────────────────
Run as a SEPARATE Railway service. Do NOT wire into the Watchtower loop — the
two workers are intentionally decoupled so evidence refresh cycles cannot
accidentally trigger sends.

Canonical process type:  PROCESS_TYPE=email_delivery
Backward-compat alias:   PROCESS_TYPE=alert_email_delivery
Both dispatch to this worker via railway.toml.

── Step 1: Dry-run validation (safe — no emails sent) ────────────────────────
Set these env vars on the Railway email-delivery service:

    ALERT_EMAIL_DELIVERY_ENABLED=true
    ALERT_EMAIL_PROVIDER=resend
    ALERT_EMAIL_DRY_RUN=true            (default — must be explicit)
    RESEND_API_KEY=<your_resend_api_key> (required even in dry-run — see note)
    ALERT_EMAIL_FROM=<from_address>
    ALERT_EMAIL_TO=<your_own_email>

Note: RESEND_API_KEY, ALERT_EMAIL_FROM, and ALERT_EMAIL_TO are validated
before scanning rows, even in dry-run mode. This is intentional — it ensures
the worker is fully configured before any outbox rows are processed.
The API key is NOT called in dry-run mode; no Resend requests are made.
A pending Resend domain is acceptable for dry-run (ALERT_EMAIL_DRY_RUN=true);
real sends require the domain to be verified in the Resend dashboard first.

Query pending outbox count before validating:
    SELECT COUNT(*) FROM public.alert_delivery_outbox WHERE status = 'pending';

Run a single dry-run pass:
    cd v2/backend
    python -m app.services.alert.alert_email_delivery_worker_entrypoint

Expected log (no outbox rows yet — scanned=0 is fine):
    alert_email_delivery_summary scanned=0 sent=0 failed=0 skipped=0
        status_update_failed=0 dry_run=True provider=resend

Expected log (with pending rows):
    alert_email_delivery_summary scanned=N sent=0 failed=0 skipped=N
        status_update_failed=0 dry_run=True provider=resend note=rows_would_send

If config is incomplete, the log will show note=config_incomplete:<reason>.
No row is claimed or mutated in dry-run mode.

── Step 2: Real-send activation (later — separate manual step) ───────────────
Only after dry-run is validated AND Resend domain is verified:

    ALERT_EMAIL_DRY_RUN=false           (explicit opt-out from dry-run default)
    RESEND_API_KEY=<your_resend_api_key>
    ALERT_EMAIL_FROM=<verified_sender_address>
    ALERT_EMAIL_TO=<your_own_email_only> (personal use — never a bulk list)

Do NOT flip ALERT_EMAIL_DRY_RUN=false until dry-run has been validated and
the Resend sending domain is verified. Never put secret values in docs, code,
or commit messages.

── Manual single pass ────────────────────────────────────────────────────────
    cd v2/backend
    python -m app.services.alert.alert_email_delivery_worker_entrypoint

── Continuous loop ───────────────────────────────────────────────────────────
    python -m app.services.alert.alert_email_delivery_worker_entrypoint --loop

ALERT_EMAIL_DELIVERY_WORKER_INTERVAL_SECONDS overrides the loop interval
(default 300s / 5 minutes). Invalid or non-positive values fall back to default.
--interval-seconds on the CLI overrides the env var.
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
_MASTER_ENABLED_ENV = "INTEL_BACKGROUND_WORKERS_ENABLED"
_ALLOW_AGGRESSIVE_ENV = "COST_GUARD_ALLOW_AGGRESSIVE_POLLING"
DEFAULT_INTERVAL_SECONDS = 300.0
# Cost guard: minimum safe polling interval for the email delivery worker.
# Clamped unless COST_GUARD_ALLOW_AGGRESSIVE_POLLING=true.
MIN_INTERVAL_SECONDS = 86400.0  # 24 hours


def _is_master_enabled() -> bool:
    """Returns True ONLY when INTEL_BACKGROUND_WORKERS_ENABLED is truthy."""
    raw = (os.getenv(_MASTER_ENABLED_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _is_delivery_enabled() -> bool:
    """Returns True ONLY when ALERT_EMAIL_DELIVERY_ENABLED is a truthy value."""
    raw = (os.getenv(_ENABLED_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _apply_cost_guard_clamp(interval: float) -> float:
    """Clamp interval to MIN_INTERVAL_SECONDS unless aggressive polling is allowed.

    Applied to the final resolved interval regardless of whether the value came
    from the env var or a --interval-seconds CLI argument.
    """
    allow_aggressive = (os.getenv(_ALLOW_AGGRESSIVE_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on"
    )
    if not allow_aggressive and interval < MIN_INTERVAL_SECONDS:
        logger.warning(
            "COST_GUARD alert_email_delivery_worker interval_clamped "
            "requested=%ss min=%ss effective=%ss "
            "set %s=true to allow shorter intervals",
            interval, MIN_INTERVAL_SECONDS, MIN_INTERVAL_SECONDS, _ALLOW_AGGRESSIVE_ENV,
        )
        interval = MIN_INTERVAL_SECONDS
    logger.info(
        "COST_GUARD alert_email_delivery_worker effective_interval_seconds=%s",
        interval,
    )
    return interval


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

    if not _is_master_enabled():
        logger.info(
            "COST_GUARD alert_email_delivery_worker master_disabled — "
            "set %s=true to allow background workers. Exiting cleanly.",
            _MASTER_ENABLED_ENV,
        )
        return 0

    if not _is_delivery_enabled():
        logger.info(
            "COST_GUARD alert_email_delivery_worker not enabled — set %s=true to start "
            "(currently absent or not a truthy value). Exiting cleanly.",
            _ENABLED_ENV,
        )
        return 0

    interval_seconds = (
        args.interval_seconds
        if args.interval_seconds is not None
        else _resolve_interval_seconds()
    )
    interval_seconds = _apply_cost_guard_clamp(interval_seconds)
    return _run(loop=args.loop, interval_seconds=interval_seconds)


if __name__ == "__main__":
    sys.exit(main())
