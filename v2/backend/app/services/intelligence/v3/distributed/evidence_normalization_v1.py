"""financial_evidence_normalization_v1 — versioned monetary + news evidence
normalization applied before durable persistence or LLM/specialist exposure.

Pure functions only: no IO, no providers, no LLM calls. Bumping either
version constant invalidates cross-session reuse of the affected lane's
pre-normalization outputs (evidence_bundle_v1's fingerprint covers the
normalized shape, never the raw provider dict).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

NORMALIZATION_VERSION = "financial_evidence_normalization_v1"
NEWS_NORMALIZATION_VERSION = "financial_evidence_normalization_v1"

_MONETARY_FIELDS = (
    "market_cap", "free_cash_flow", "operating_cash_flow", "net_income",
    "revenue", "total_debt", "cash", "ebitda", "target_mean_price",
)
_RATIO_FIELDS = (
    "pe", "forward_pe", "peg", "ps_ttm", "ev_ebitda", "eps",
    "profit_margin", "gross_margin", "revenue_growth", "earnings_growth",
    "debt_to_equity", "return_on_equity", "beta", "dividend_yield",
    "recommendation_mean",
)
_SCALE_LABELS = (
    (1_000_000_000_000.0, "trillion"),
    (1_000_000_000.0, "billion"),
    (1_000_000.0, "million"),
)


def _valid_iso_currency(value: Any) -> Optional[str]:
    return value.upper() if isinstance(value, str) and len(value) == 3 and value.isalpha() else None


def _format_scaled(value: float, currency: str) -> str:
    magnitude = abs(value)
    for threshold, label in _SCALE_LABELS:
        if magnitude >= threshold:
            return f"{currency} {value / threshold:.1f} {label}"
    return f"{currency} {value:,.2f}"


def normalize_fundamentals(raw: dict[str, Any]) -> dict[str, Any]:
    """Separate market-price currency from reporting currency, label every
    monetary metric with its verified ISO-4217 reporting currency, leave
    ratios/percentages dimensionless, and never guess a currency. An ADR's
    quote currency (USD) is never applied to its TWD/EUR/JPY financials."""
    reporting_currency = _valid_iso_currency(raw.get("financial_currency"))
    market_price_currency = _valid_iso_currency(raw.get("quote_currency")) or reporting_currency

    monetary: dict[str, Any] = {}
    compact: dict[str, str] = {}
    gaps: list[str] = []
    for field in _MONETARY_FIELDS:
        value = raw.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value:
            continue
        if reporting_currency is None:
            gaps.append(field)
            continue
        monetary[field] = {
            "value": float(value), "currency": reporting_currency,
            "as_reported": True, "source_field": field,
        }
        compact[field] = _format_scaled(float(value), reporting_currency)

    ratios = {
        field: raw[field] for field in _RATIO_FIELDS
        if isinstance(raw.get(field), (int, float)) and not isinstance(raw.get(field), bool)
    }

    return {
        "schema_version": NORMALIZATION_VERSION,
        "market_price_currency": market_price_currency,
        "reporting_currency": reporting_currency,
        "monetary": monetary,
        "ratios": ratios,
        "compact": compact,
        "normalization_gaps": gaps,
    }

# ── News relevance / timestamp / freshness (section 3) ──────────────────────
_MAX_ACCEPTED_ARTICLES = 8
_MAX_ARTICLE_AGE_DAYS = 14
_TOKEN_PATTERN_CACHE: dict[str, "re.Pattern[str]"] = {}


def _ticker_token_pattern(ticker: str) -> "re.Pattern[str]":
    pattern = _TOKEN_PATTERN_CACHE.get(ticker)
    if pattern is None:
        pattern = re.compile(r"\b" + re.escape(ticker) + r"\b", re.IGNORECASE)
        _TOKEN_PATTERN_CACHE[ticker] = pattern
    return pattern


def _valid_publication(raw_dt: Any, now: datetime) -> Optional[datetime]:
    try:
        ts = float(raw_dt)
    except (TypeError, ValueError):
        return None
    if ts != ts or ts in (float("inf"), float("-inf")) or ts <= 0:
        return None
    try:
        published = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    if published > now + timedelta(hours=1):
        return None
    if published < now - timedelta(days=_MAX_ARTICLE_AGE_DAYS):
        return None
    return published


def _is_relevant(item: dict[str, Any], ticker: str) -> bool:
    related = item.get("related_tickers") or []
    if isinstance(related, list) and any(
        str(r).strip().upper() == ticker.upper() for r in related
    ):
        return True
    text = f"{item.get('headline') or ''} {item.get('summary') or ''}"
    return bool(_ticker_token_pattern(ticker).search(text))


def _identity_key(item: dict[str, Any]) -> Optional[str]:
    for field in ("id", "link"):
        value = item.get(field)
        if value:
            return f"{field}:{value}"
    headline = (item.get("headline") or "").strip().lower()
    return f"headline:{headline}:{item.get('datetime')}" if headline else None


def filter_news_items(
    items: list[dict[str, Any]], ticker: str, now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Deterministic ticker relevance + valid-timestamp + dedup gate. Only
    accepted articles may enter durable lane evidence, the bundle, and the
    sentiment specialist prompt — a fetch-time timestamp never substitutes
    for an invalid publication timestamp."""
    now = now or datetime.now(timezone.utc)
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    rejected_invalid_timestamp = 0
    rejected_irrelevant = 0
    duplicate = 0
    for item in items or []:
        published = _valid_publication(item.get("datetime"), now)
        if published is None:
            rejected_invalid_timestamp += 1
            continue
        if not _is_relevant(item, ticker):
            rejected_irrelevant += 1
            continue
        key = _identity_key(item)
        if key is None or key in seen:
            duplicate += 1
            continue
        seen.add(key)
        accepted.append({
            "headline": item.get("headline"), "source": item.get("source"),
            "datetime": item.get("datetime"), "published_at": published.isoformat(),
        })
    accepted.sort(key=lambda a: a["published_at"], reverse=True)
    accepted = accepted[:_MAX_ACCEPTED_ARTICLES]
    return {
        "schema_version": NEWS_NORMALIZATION_VERSION,
        "items": accepted,
        "accepted_count": len(accepted),
        "rejected_invalid_timestamp_count": rejected_invalid_timestamp,
        "rejected_irrelevant_count": rejected_irrelevant,
        "duplicate_count": duplicate,
        "oldest_accepted_at": accepted[-1]["published_at"] if accepted else None,
        "newest_accepted_at": accepted[0]["published_at"] if accepted else None,
    }
