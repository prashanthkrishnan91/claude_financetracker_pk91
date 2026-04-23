"""Best-effort Supabase persistence for :class:`FeatureSet` rows.

Mirrors the contract of :mod:`snapshot_store` — missing table logs once
and no-ops, so a deployment where migration 009 hasn't been applied
keeps running and the feature engine still produces in-memory features
that downstream stages can consume.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from .feature_engine import FeatureSet

logger = logging.getLogger(__name__)

_TABLE = "agent_features"
_TABLE_AVAILABLE: Optional[bool] = None


def persist_features(
    features: Iterable[FeatureSet],
    *,
    run_id: str,
    user_id: str,
    db=None,
) -> int:
    """Insert one row per feature set. Returns rows inserted (0 on no-op/failure).

    Never raises — Supabase failures are logged and swallowed so a
    broken persistence layer can't break the agent run.
    """
    rows = [f.to_row(run_id=run_id, user_id=user_id) for f in features]
    if not rows:
        return 0

    if db is None:
        try:
            from ...database import get_supabase_client
            db = get_supabase_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning("feature_store: Supabase client unavailable: %s", exc)
            return 0

    global _TABLE_AVAILABLE
    if _TABLE_AVAILABLE is False:
        logger.debug(
            "feature_store: table %s marked unavailable — skipping %d rows",
            _TABLE, len(rows),
        )
        return 0

    try:
        db.table(_TABLE).insert(rows).execute()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "does not exist" in msg or "relation" in msg or "schema cache" in msg:
            if _TABLE_AVAILABLE is not False:
                logger.warning(
                    "feature_store: table %s missing — run `migrations/"
                    "009_agent_features.sql`. Features will not persist "
                    "until applied.",
                    _TABLE,
                )
            _TABLE_AVAILABLE = False
            return 0
        logger.warning("feature_store: insert failed (%d rows): %s", len(rows), exc)
        return 0

    _TABLE_AVAILABLE = True
    return len(rows)


def _reset_for_testing() -> None:
    """Test hook — reset the process-wide availability flag."""
    global _TABLE_AVAILABLE
    _TABLE_AVAILABLE = None
