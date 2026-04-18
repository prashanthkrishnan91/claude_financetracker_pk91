"""Personalization engine — READ-ONLY analytics layer.

Learns user behavior from decision_log history and produces a deterministic
profile. Does not use LLM, does not modify decision generation or feedback.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from ..database import get_supabase_client


# ── Constants ─────────────────────────────────────────────────────────────────

# Proxies for high-beta / volatile exposure used in risk scoring
_HIGH_BETA: frozenset[str] = frozenset({
    "NVDA", "QQQ", "AMD", "RIVN", "SNOW", "RDDT", "CAVA",
    "BTC", "XRP", "KLAR", "BLSH", "STUB",
})

_CACHE_TTL_SECONDS = 3_600  # 1 hour


# ── In-memory cache ───────────────────────────────────────────────────────────

# key: str(user_id) → (profile_dict, cached_at)
_cache: dict[str, tuple[dict[str, Any], datetime]] = {}


# ── Public API ────────────────────────────────────────────────────────────────

async def get_user_profile(user_id: str | UUID) -> dict[str, Any]:
    """Return cached profile or recompute if older than TTL."""
    key = str(user_id)
    entry = _cache.get(key)
    if entry is not None:
        profile, cached_at = entry
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < _CACHE_TTL_SECONDS:
            return profile

    profile = await compute_user_profile(user_id)
    _cache[key] = (profile, datetime.now(timezone.utc))
    return profile


async def compute_user_profile(user_id: str | UUID) -> dict[str, Any]:
    """Compute personalization profile from decision_log.

    Reads decision_log and agent_insights — writes nothing.
    """
    uid = str(user_id)
    client = get_supabase_client()

    decisions: list[dict] = (
        client.table("decision_log")
        .select("recommendation_id, ticker, decision, price_at_decision, shares_at_decision, created_at")
        .eq("user_id", uid)
        .order("created_at", desc=False)
        .execute()
    ).data or []

    if not decisions:
        return _empty_profile()

    insights: list[dict] = (
        client.table("agent_insights")
        .select("ticker, suggested_allocation, suggested_action, conviction_score")
        .eq("user_id", uid)
        .execute()
    ).data or []

    compliance_rate = _compliance_rate(decisions)
    risk_score = _risk_score(decisions)
    biases = _biases(decisions, insights)
    behavior_tags = _behavior_tags(decisions, compliance_rate, risk_score)

    return {
        "risk_score": round(risk_score, 4),
        "compliance_rate": round(compliance_rate, 4),
        "biases": biases,
        "behavior_tags": behavior_tags,
    }


# ── Computation helpers ───────────────────────────────────────────────────────

def _empty_profile() -> dict[str, Any]:
    return {
        "risk_score": 0.0,
        "compliance_rate": 0.0,
        "biases": {"overweighted_symbols": []},
        "behavior_tags": [],
    }


def _compliance_rate(decisions: list[dict]) -> float:
    accepted = sum(1 for d in decisions if d.get("decision") == "accepted")
    return accepted / len(decisions)


def _risk_score(decisions: list[dict]) -> float:
    """Ratio of accepted decisions on high-beta symbols vs. total decisions."""
    high_beta_accepts = sum(
        1 for d in decisions
        if d.get("decision") == "accepted"
        and (d.get("ticker") or "").upper() in _HIGH_BETA
    )
    return min(1.0, high_beta_accepts / len(decisions))


def _biases(decisions: list[dict], insights: list[dict]) -> dict[str, Any]:
    """Detect per-symbol over-weighting vs. system suggestions."""
    # Latest suggested_allocation per ticker (last row wins; dollars)
    suggestion: dict[str, float] = {}
    for row in insights:
        ticker = (row.get("ticker") or "").upper()
        alloc = float(row.get("suggested_allocation") or 0)
        if ticker and alloc > 0:
            suggestion[ticker] = alloc
    total_suggested = sum(suggestion.values())

    total_accepted = sum(1 for d in decisions if d.get("decision") == "accepted")

    # Group accepted decisions by ticker to measure user's implicit weight
    accepted_per_ticker: dict[str, int] = {}
    for d in decisions:
        if d.get("decision") != "accepted":
            continue
        ticker = (d.get("ticker") or "").upper()
        if ticker:
            accepted_per_ticker[ticker] = accepted_per_ticker.get(ticker, 0) + 1

    overweighted: list[dict] = []
    for ticker, accept_count in accepted_per_ticker.items():
        if ticker not in suggestion or total_suggested <= 0 or total_accepted <= 0:
            continue

        # share of the user's accepted decisions that went to this ticker
        user_weight = accept_count / total_accepted
        # share of the system's suggested allocation for this ticker
        system_weight = suggestion[ticker] / total_suggested

        bias_score = round(user_weight - system_weight, 4)
        if bias_score > 0.01:
            overweighted.append({
                "symbol": ticker,
                "system_weight": round(system_weight, 4),
                "user_weight": round(user_weight, 4),
                "bias_score": bias_score,
            })

    overweighted.sort(key=lambda x: x["bias_score"], reverse=True)
    return {"overweighted_symbols": overweighted}


def _behavior_tags(
    decisions: list[dict],
    compliance_rate: float,
    risk_score: float,
) -> list[str]:
    tags: list[str] = []

    if compliance_rate > 0.70:
        tags.append("conservative_follower")

    if risk_score > 0.25:
        tags.append("aggressive_overrider")

    # conviction_buyer: same ticker accepted ≥3 times
    accepts_per_ticker: dict[str, int] = {}
    for d in decisions:
        if d.get("decision") == "accepted":
            ticker = (d.get("ticker") or "").upper()
            if ticker:
                accepts_per_ticker[ticker] = accepts_per_ticker.get(ticker, 0) + 1
    if any(count >= 3 for count in accepts_per_ticker.values()):
        tags.append("conviction_buyer")

    # diversifier: accepted decisions are spread across many unique tickers
    accepted_tickers = set(accepts_per_ticker.keys())
    total_accepted = sum(accepts_per_ticker.values())
    if total_accepted > 0:
        diversity_ratio = len(accepted_tickers) / total_accepted
        if diversity_ratio > 0.6 and len(accepted_tickers) >= 5:
            tags.append("diversifier")

    return tags
