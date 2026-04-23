"""Staged intelligence pipeline — Phase 1 data-stabilization layer.

Public exports:
    * ``MarketSnapshot`` — per-ticker, per-run data envelope.
    * ``build_market_snapshots`` — pure transform from io_layer bundle to snapshots.
    * ``persist_snapshots`` — best-effort Supabase insert for ``market_snapshots``.
"""

from .benchmark import fetch_benchmark_price_action
from .feature_engine import FeatureSet, build_features
from .feature_store import persist_features
from .market_snapshot import MarketSnapshot, build_market_snapshots
from .per_ticker_analyst import (
    ALLOWED_ACTIONS,
    AnalystVerdict,
    action_to_suggested_action,
    analyze_portfolio,
    analyze_ticker,
    format_thesis,
    insufficient_data_verdict,
)
from .portfolio_synthesis import (
    ALLOWED_BIASES,
    PortfolioSynthesis,
    deterministic_synthesis,
    synthesize_portfolio,
)
from .run_mode import (
    DEGRADED_QUALITY_THRESHOLD,
    CostEntry,
    ModeDecision,
    RunCostTracker,
    RunMode,
    build_full_mode_verdicts,
    build_degraded_verdicts,
    classify_run_mode,
    estimate_cost_usd,
    projected_full_mode_cost,
)
from .snapshot_store import persist_snapshots

__all__ = [
    "MarketSnapshot",
    "build_market_snapshots",
    "persist_snapshots",
    "FeatureSet",
    "build_features",
    "persist_features",
    "fetch_benchmark_price_action",
    "AnalystVerdict",
    "ALLOWED_ACTIONS",
    "analyze_portfolio",
    "analyze_ticker",
    "action_to_suggested_action",
    "format_thesis",
    "insufficient_data_verdict",
    "PortfolioSynthesis",
    "ALLOWED_BIASES",
    "synthesize_portfolio",
    "deterministic_synthesis",
    "RunMode",
    "ModeDecision",
    "DEGRADED_QUALITY_THRESHOLD",
    "classify_run_mode",
    "build_full_mode_verdicts",
    "build_degraded_verdicts",
    "RunCostTracker",
    "CostEntry",
    "estimate_cost_usd",
    "projected_full_mode_cost",
]
