#!/usr/bin/env python3
"""Cost-guard health diagnostic.

Reports worker enable flags, effective polling intervals, snapshot write guard
state, and approximate generated table row counts (via Supabase service role).

Usage (from repo root):
    python scripts/cost_guard_health.py

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in env or .env file.
Never exposes secret values in output.

Exit codes:
    0 — all cost guards are active (safe mode)
    1 — at least one cost guard is disabled (workers or writes may be running)
"""
from __future__ import annotations

import os
import sys

_TRUTHY = {"1", "true", "yes", "on"}

# ── Generated tables to count ─────────────────────────────────────────────────
_GENERATED_TABLES = [
    "intel_v3_snapshots",
    "market_snapshots",
    "agent_runs",
    "agent_insights",
    "agent_features",
    "recommendations",
    "decision_log",
    "research_artifacts",
    "research_artifact_facts",
    "research_artifact_sources",
    "worker_audit_events",
]


def _flag(env_var: str) -> bool:
    return (os.getenv(env_var) or "").strip().lower() in _TRUTHY


def _effective_interval(
    env_var: str,
    default: float,
    min_val: float,
    allow_aggressive: bool,
) -> tuple[float, bool]:
    """Returns (effective_seconds, was_clamped)."""
    raw = (os.getenv(env_var) or "").strip()
    try:
        configured = float(raw) if raw else default
        if configured <= 0:
            configured = default
    except (TypeError, ValueError):
        configured = default
    if not allow_aggressive and configured < min_val:
        return min_val, True
    return configured, False


def _count_table(client: object, table: str) -> int | str:
    try:
        result = client.table(table).select("id", count="exact").limit(1).execute()
        return result.count if hasattr(result, "count") else "?"
    except Exception as exc:
        return f"error:{exc}"


def main() -> int:
    print("=" * 60)
    print("COST GUARD HEALTH DIAGNOSTIC")
    print("=" * 60)

    # ── 1. Flag summary ───────────────────────────────────────────────────────
    master = _flag("INTEL_BACKGROUND_WORKERS_ENABLED")
    watchtower = _flag("INTEL_V3_WATCHTOWER_ENABLED")
    research = _flag("INTEL_V3_RESEARCH_WORKERS_ENABLED")
    email = _flag("ALERT_EMAIL_DELIVERY_ENABLED")
    snapshot_writes = _flag("INTEL_V3_SNAPSHOT_WRITES_ENABLED")
    allow_aggressive = _flag("COST_GUARD_ALLOW_AGGRESSIVE_POLLING")

    print("\n[WORKER FLAGS]")
    print(f"  INTEL_BACKGROUND_WORKERS_ENABLED  = {master}  (master kill switch)")
    print(f"  INTEL_V3_WATCHTOWER_ENABLED        = {watchtower}")
    print(f"  INTEL_V3_RESEARCH_WORKERS_ENABLED  = {research}  (analyst + research lanes)")
    print(f"  ALERT_EMAIL_DELIVERY_ENABLED       = {email}")

    print("\n[WRITE GUARDS]")
    print(f"  INTEL_V3_SNAPSHOT_WRITES_ENABLED   = {snapshot_writes}")

    print("\n[POLLING OVERRIDE]")
    print(f"  COST_GUARD_ALLOW_AGGRESSIVE_POLLING = {allow_aggressive}")

    # ── 2. Effective polling intervals ────────────────────────────────────────
    print("\n[EFFECTIVE POLLING INTERVALS]")
    wt_secs, wt_clamped = _effective_interval(
        "INTEL_V3_WATCHTOWER_WORKER_INTERVAL_SECONDS",
        default=60.0, min_val=21600.0, allow_aggressive=allow_aggressive,
    )
    ar_secs, ar_clamped = _effective_interval(
        "INTEL_V3_ANALYST_REFRESH_WORKER_INTERVAL_SECONDS",
        default=60.0, min_val=43200.0, allow_aggressive=allow_aggressive,
    )
    em_secs, em_clamped = _effective_interval(
        "ALERT_EMAIL_DELIVERY_WORKER_INTERVAL_SECONDS",
        default=300.0, min_val=86400.0, allow_aggressive=allow_aggressive,
    )
    print(f"  Watchtower:     {wt_secs:,.0f}s ({wt_secs/3600:.1f}h)"
          f"{'  [CLAMPED from configured]' if wt_clamped else ''}")
    print(f"  Analyst/Research: {ar_secs:,.0f}s ({ar_secs/3600:.1f}h)"
          f"{'  [CLAMPED from configured]' if ar_clamped else ''}")
    print(f"  Email delivery: {em_secs:,.0f}s ({em_secs/3600:.1f}h)"
          f"{'  [CLAMPED from configured]' if em_clamped else ''}")

    # ── 3. Table row counts ───────────────────────────────────────────────────
    print("\n[GENERATED TABLE ROW COUNTS]")
    supabase_url = os.getenv("SUPABASE_URL") or os.getenv("supabase_url") or ""
    supabase_key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("supabase_service_role_key")
        or ""
    )
    if not supabase_url or not supabase_key:
        print("  (skipped — SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set)")
    else:
        try:
            from supabase import create_client
            client = create_client(supabase_url, supabase_key)
            for table in _GENERATED_TABLES:
                count = _count_table(client, table)
                print(f"  {table:<40} {count}")
        except ImportError:
            print("  (skipped — supabase package not installed in this environment)")
        except Exception as exc:
            print(f"  (error connecting to Supabase: {exc})")

    # ── 4. Safety assessment ──────────────────────────────────────────────────
    print("\n[SAFETY ASSESSMENT]")
    safe = True
    if master:
        print("  WARNING: INTEL_BACKGROUND_WORKERS_ENABLED=true — workers may start")
        safe = False
    else:
        print("  OK: master kill switch OFF — no background workers will start")

    if snapshot_writes:
        print("  WARNING: INTEL_V3_SNAPSHOT_WRITES_ENABLED=true — snapshots will be written")
        safe = False
    else:
        print("  OK: snapshot writes OFF — intel_v3_snapshots table is protected")

    if allow_aggressive:
        print("  WARNING: COST_GUARD_ALLOW_AGGRESSIVE_POLLING=true — interval clamping bypassed")
        safe = False
    else:
        print("  OK: aggressive polling override OFF — intervals are clamped to safe minimums")

    print()
    if safe:
        print("RESULT: SAFE — all cost guards active")
    else:
        print("RESULT: ACTION REQUIRED — one or more cost guards disabled")
    print("=" * 60)

    return 0 if safe else 1


if __name__ == "__main__":
    sys.exit(main())
