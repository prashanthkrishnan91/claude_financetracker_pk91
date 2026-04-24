"""Canonical agent run statuses shared across recommendation services.

These values must stay aligned with the Supabase ``agent_runs`` status check
constraint and frontend polling logic.
"""

from __future__ import annotations

from typing import Final

# Supabase-backed statuses that are safe to persist on ``agent_runs.status``.
DB_AGENT_RUN_STATUSES: Final[set[str]] = {
    "queued",
    "running",
    "in_progress",
    "completed",
    "failed",
    "cancelled",
}

ACTIVE_RUN_STATUSES: Final[set[str]] = {"queued", "running", "in_progress"}
TERMINAL_RUN_STATUSES: Final[set[str]] = {"completed", "failed", "cancelled"}


def normalize_run_status(status: str | None) -> str:
    """Normalize unknown/legacy statuses into a safe frontend/backend shape."""
    raw = (status or "").strip().lower()
    if raw in DB_AGENT_RUN_STATUSES:
        return raw
    # Legacy/deprecated values that should behave as failed.
    if raw in {"stale_failed", "no_data"}:
        return "failed"
    # Unknown statuses should never keep polling forever.
    return "failed"


def assert_db_status(status: str) -> str:
    """Validate that a status is supported by the DB constraint."""
    normalized = (status or "").strip().lower()
    if normalized not in DB_AGENT_RUN_STATUSES:
        raise ValueError(f"Unsupported agent_runs.status '{status}'")
    return normalized
