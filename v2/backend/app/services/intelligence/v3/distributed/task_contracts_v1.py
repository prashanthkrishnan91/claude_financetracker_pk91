"""Distributed Run Intel — task/lane/axis taxonomy and pure helpers.

Pure constants + pure functions only (no IO, no LLM, no providers) so every
other module in the package can import this without dragging in dependencies.
Names must stay in sync with the migration 027 CHECK constraints — the SQL
contract test asserts exact parity.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

# ── Session (workflow_version=2) ─────────────────────────────────────────────
WORKFLOW_VERSION_DISTRIBUTED = 2

SESSION_CREATED = "created"
SESSION_RUNNING = "running"
SESSION_COMPLETED = "completed"
SESSION_COMPLETED_WITH_GAPS = "completed_with_gaps"
SESSION_FAILED = "failed"
SESSION_SUPERSEDED = "superseded"

SESSION_TERMINAL_STATES = (
    SESSION_COMPLETED,
    SESSION_COMPLETED_WITH_GAPS,
    SESSION_FAILED,
    SESSION_SUPERSEDED,
)
SESSION_ACTIVE_STATES = (SESSION_CREATED, SESSION_RUNNING)

# current_stage values (presentation ordering, never execution authority)
STAGE_PREPARING = "preparing"
STAGE_COLLECTING = "collecting_evidence"
STAGE_ANALYSIS = "specialist_analysis"
STAGE_DECIDING = "deciding"
STAGE_PUBLISHING = "publishing"
STAGE_DONE = "done"

# ── Ticker state machine ─────────────────────────────────────────────────────
TICKER_PENDING = "pending"
TICKER_EVIDENCE_READY = "evidence_ready"
TICKER_ANALYSIS_COMPLETE = "analysis_complete"
TICKER_DECISION_READY = "decision_ready"
TICKER_DECIDED = "decided"
TICKER_NO_CALL = "no_call"
TICKER_FAILED = "failed"

TICKER_TERMINAL_STATES = (TICKER_DECIDED, TICKER_NO_CALL, TICKER_FAILED)
ALL_TICKER_STATES = (
    TICKER_PENDING,
    TICKER_EVIDENCE_READY,
    TICKER_ANALYSIS_COMPLETE,
    TICKER_DECISION_READY,
    TICKER_DECIDED,
    TICKER_NO_CALL,
    TICKER_FAILED,
)

# ── Asset types ──────────────────────────────────────────────────────────────
ASSET_EQUITY = "equity"
ASSET_ETF = "etf"
ASSET_CRYPTO = "crypto"
ALL_ASSET_TYPES = (ASSET_EQUITY, ASSET_ETF, ASSET_CRYPTO)

# positions.category → asset type (see app/models/position.py category CHECK)
_CATEGORY_TO_ASSET = {
    "crypto": ASSET_CRYPTO,
    "etf": ASSET_ETF,
}


def asset_type_for_category(category: Optional[str]) -> str:
    """Map a positions.category value to an asset type (default equity)."""
    return _CATEGORY_TO_ASSET.get(str(category or "").strip().lower(), ASSET_EQUITY)


# ── Task taxonomy ────────────────────────────────────────────────────────────
TASK_COLLECT_PORTFOLIO_CONTEXT = "collect_portfolio_context"
TASK_COLLECT_MACRO_CONTEXT = "collect_macro_context"
TASK_COLLECT_EVIDENCE_LANE = "collect_evidence_lane"
TASK_BUILD_EVIDENCE_BUNDLE = "build_evidence_bundle"
TASK_SPECIALIST_ANALYSIS = "specialist_analysis"
TASK_REVIEW_CONFLICT = "review_conflict"
TASK_TICKER_DECISION = "ticker_decision"
TASK_PORTFOLIO_JOIN_PUBLISH = "portfolio_join_publish"

ALL_TASK_TYPES = (
    TASK_COLLECT_PORTFOLIO_CONTEXT,
    TASK_COLLECT_MACRO_CONTEXT,
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_BUILD_EVIDENCE_BUNDLE,
    TASK_SPECIALIST_ANALYSIS,
    TASK_REVIEW_CONFLICT,
    TASK_TICKER_DECISION,
    TASK_PORTFOLIO_JOIN_PUBLISH,
)

# ── Task state machine ───────────────────────────────────────────────────────
TASK_BLOCKED = "blocked"
TASK_PENDING = "pending"
TASK_CLAIMED = "claimed"
TASK_SUCCEEDED = "succeeded"
TASK_DEGRADED = "degraded"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"

TASK_TERMINAL_STATES = (TASK_SUCCEEDED, TASK_DEGRADED, TASK_FAILED, TASK_CANCELLED)
ALL_TASK_STATES = (
    TASK_BLOCKED,
    TASK_PENDING,
    TASK_CLAIMED,
    TASK_SUCCEEDED,
    TASK_DEGRADED,
    TASK_FAILED,
    TASK_CANCELLED,
)

# ── Evidence lanes (Stage-5G registry namespace where lanes already exist) ───
LANE_PRICE = "price"
LANE_TECHNICALS = "technicals"
LANE_FUNDAMENTALS = "fundamentals"
LANE_NEWS_SENTIMENT = "news_sentiment"
LANE_SEC_COMPANY_FACTS = "sec_company_facts"
LANE_SEC_CATALYST = "sec_catalyst_sentiment"
LANE_ETF_FUND_DATA = "etf_fund_data"
LANE_CRYPTO_MARKET = "crypto_market"
LANE_MACRO = "macro"

# Required lanes gate the evidence bundle; optional lanes degrade only
# themselves. Grounded in providers that exist in the repo today.
REQUIRED_LANES_BY_ASSET: dict[str, tuple[str, ...]] = {
    ASSET_EQUITY: (LANE_PRICE, LANE_TECHNICALS, LANE_FUNDAMENTALS),
    ASSET_ETF: (LANE_PRICE, LANE_TECHNICALS),
    ASSET_CRYPTO: (LANE_PRICE, LANE_CRYPTO_MARKET),
}
OPTIONAL_LANES_BY_ASSET: dict[str, tuple[str, ...]] = {
    ASSET_EQUITY: (LANE_NEWS_SENTIMENT, LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST),
    ASSET_ETF: (LANE_NEWS_SENTIMENT, LANE_ETF_FUND_DATA),
    ASSET_CRYPTO: (),
}


def lanes_for_asset(asset_type: str) -> list[str]:
    """All lanes (required + optional) collected for an asset type."""
    required = REQUIRED_LANES_BY_ASSET.get(asset_type, ())
    optional = OPTIONAL_LANES_BY_ASSET.get(asset_type, ())
    return [*required, *optional]


def required_lanes_for_asset(asset_type: str) -> list[str]:
    return list(REQUIRED_LANES_BY_ASSET.get(asset_type, ()))


# Lane freshness TTLs in hours (reuse-first; see contract §6). ``0`` means
# always refresh within the run.
LANE_TTL_HOURS: dict[str, float] = {
    LANE_PRICE: 0.25,
    LANE_TECHNICALS: 24.0,
    LANE_FUNDAMENTALS: 24.0,
    LANE_NEWS_SENTIMENT: 1.0,
    LANE_SEC_COMPANY_FACTS: 168.0,
    LANE_SEC_CATALYST: 24.0,
    LANE_ETF_FUND_DATA: 2160.0,
    LANE_CRYPTO_MARKET: 0.25,
    LANE_MACRO: 24.0,
}

# ── Specialist axes ──────────────────────────────────────────────────────────
AXIS_FUNDAMENTAL = "fundamental"
AXIS_TECHNICAL = "technical"
AXIS_SENTIMENT = "sentiment"
AXIS_RISK_FILING = "risk_filing"
AXIS_ETF_EXPOSURE = "etf_exposure"
AXIS_CRYPTO_MARKET = "crypto_market"
AXIS_REVIEW = "review"

ALL_AXES = (
    AXIS_FUNDAMENTAL,
    AXIS_TECHNICAL,
    AXIS_SENTIMENT,
    AXIS_RISK_FILING,
    AXIS_ETF_EXPOSURE,
    AXIS_CRYPTO_MARKET,
    AXIS_REVIEW,
)

# Axes required before a ticker is decision-ready; optional axes degrade only
# themselves (risk_filing only exists when SEC evidence is present; sentiment
# is optional because its backing news lane is optional — a news outage must
# not force NO CALL on an otherwise well-evidenced holding).
REQUIRED_AXES_BY_ASSET: dict[str, tuple[str, ...]] = {
    ASSET_EQUITY: (AXIS_FUNDAMENTAL, AXIS_TECHNICAL),
    ASSET_ETF: (AXIS_TECHNICAL, AXIS_ETF_EXPOSURE),
    ASSET_CRYPTO: (AXIS_CRYPTO_MARKET,),
}
OPTIONAL_AXES_BY_ASSET: dict[str, tuple[str, ...]] = {
    ASSET_EQUITY: (AXIS_SENTIMENT, AXIS_RISK_FILING),
    ASSET_ETF: (AXIS_SENTIMENT,),
    ASSET_CRYPTO: (),
}

# An axis is runnable only when at least one backing lane produced usable
# evidence in the bundle. Empty tuple = always runnable (works from the
# bundle's price/portfolio context alone).
AXIS_BACKING_LANES: dict[str, tuple[str, ...]] = {
    AXIS_FUNDAMENTAL: (LANE_FUNDAMENTALS, LANE_SEC_COMPANY_FACTS),
    AXIS_TECHNICAL: (LANE_TECHNICALS, LANE_PRICE),
    AXIS_SENTIMENT: (LANE_NEWS_SENTIMENT, LANE_SEC_CATALYST),
    AXIS_RISK_FILING: (LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST),
    AXIS_ETF_EXPOSURE: (),
    AXIS_CRYPTO_MARKET: (LANE_CRYPTO_MARKET,),
}

ALLOWED_STANCES = ("positive", "neutral", "negative")


def axes_for_asset(asset_type: str) -> list[str]:
    return [
        *REQUIRED_AXES_BY_ASSET.get(asset_type, ()),
        *OPTIONAL_AXES_BY_ASSET.get(asset_type, ()),
    ]


def required_axes_for_asset(asset_type: str) -> list[str]:
    return list(REQUIRED_AXES_BY_ASSET.get(asset_type, ()))


# ── Priority (execution order only — NEVER scope exclusion) ──────────────────

def compute_ticker_priority(
    *,
    has_current_recommendation: bool,
    evidence_available: bool,
    weight_pct: Optional[float],
) -> int:
    """Smaller number = earlier execution. Every ticker is always included.

    Ordering per contract: missing/failed current recommendation first, then
    missing evidence, then larger portfolio weight. Stable tie-break happens
    at the SQL layer (created_at, then ticker via deterministic insert order).
    """
    priority = 100
    if not has_current_recommendation:
        priority -= 60
    if not evidence_available:
        priority -= 20
    weight = float(weight_pct or 0.0)
    # Weight shifts priority by at most 19 so it can never outrank the
    # missing-recommendation / missing-evidence buckets.
    priority -= min(19, int(weight))
    return max(1, priority)


# ── Fingerprints ─────────────────────────────────────────────────────────────

def stable_fingerprint(payload: Any) -> str:
    """Deterministic sha256 fingerprint of a JSON-serializable payload."""
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def batch_key_for(asset_type: str, axis: str, index: int) -> str:
    """Deterministic batch key for a specialist batch."""
    return f"{asset_type}:{axis}:b{index:03d}"
