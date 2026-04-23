"""Staged intelligence pipeline — Phase 1 data-stabilization layer.

Public exports:
    * ``MarketSnapshot`` — per-ticker, per-run data envelope.
    * ``build_market_snapshots`` — pure transform from io_layer bundle to snapshots.
    * ``persist_snapshots`` — best-effort Supabase insert for ``market_snapshots``.
"""

from .market_snapshot import MarketSnapshot, build_market_snapshots
from .snapshot_store import persist_snapshots

__all__ = ["MarketSnapshot", "build_market_snapshots", "persist_snapshots"]
