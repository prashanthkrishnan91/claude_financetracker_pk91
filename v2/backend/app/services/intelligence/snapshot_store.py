"""Best-effort Supabase persistence for :class:`MarketSnapshot` rows.

The orchestrator calls :func:`persist_snapshots` after building snapshots
for a run. When the ``market_snapshots`` table exists, one row per ticker
is inserted. When the table is missing (migration not yet applied, local
dev without Supabase, etc.) the insert is skipped with a log warning —
the pipeline itself never fails because of a missing persistence layer.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from .market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)

_TABLE = "market_snapshots"
# Module-level flag so we only log the "table missing" warning once per
# process. Once the migration is applied this flips back to True on the
# next successful insert.
_TABLE_AVAILABLE: Optional[bool] = None


def persist_snapshots(
    snapshots: Iterable[MarketSnapshot],
    *,
    run_id: str,
    user_id: str,
    db=None,
) -> int:
    """Insert one row per snapshot into ``market_snapshots``.

    Returns the number of rows inserted (0 when the table is missing or
    the batch is empty). Never raises — Supabase failures are logged and
    swallowed so a broken persistence layer can't break the agent run.
    """
    rows = [s.to_row(run_id=run_id, user_id=user_id) for s in snapshots]
    if not rows:
        return 0

    if db is None:
        try:
            from ...database import get_supabase_client
            db = get_supabase_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning("snapshot_store: Supabase client unavailable: %s", exc)
            return 0

    global _TABLE_AVAILABLE
    if _TABLE_AVAILABLE is False:
        # We already know this deployment doesn't have the migration — skip
        # the insert entirely to save a round-trip per run.
        logger.debug("snapshot_store: table %s marked unavailable — skipping %d rows",
                     _TABLE, len(rows))
        return 0

    try:
        db.table(_TABLE).insert(rows).execute()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "does not exist" in msg or "relation" in msg or "schema cache" in msg:
            # Table-missing failure — flip the flag so subsequent runs
            # don't repeat the round-trip. Logged at WARNING once.
            if _TABLE_AVAILABLE is not False:
                logger.warning(
                    "snapshot_store: table %s missing — run `migrations/"
                    "008_market_snapshots.sql`. Snapshots will not persist "
                    "until applied.",
                    _TABLE,
                )
            _TABLE_AVAILABLE = False
            return 0
        logger.warning("snapshot_store: insert failed (%d rows): %s", len(rows), exc)
        return 0

    _TABLE_AVAILABLE = True
    return len(rows)


def _reset_for_testing() -> None:
    """Test hook — reset the process-wide availability flag."""
    global _TABLE_AVAILABLE
    _TABLE_AVAILABLE = None
