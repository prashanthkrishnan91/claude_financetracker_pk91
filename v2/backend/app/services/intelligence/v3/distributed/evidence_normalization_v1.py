"""financial_evidence_normalization_v1 — versioned monetary + news
normalization before durable persistence/LLM exposure. Pure functions only.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# v2: quote vs. statement currency split; EPS is per_share not a ratio;
# NaN/infinite rejected; non-ISO/mixed-case units (GBX/GBp) rejected.
NORMALIZATION_VERSION = "financial_evidence_normalization_v2"
NEWS_NORMALIZATION_VERSION = "financial_evidence_normalization_v2"

_STATEMENT_FIELDS = ("revenue", "free_cash_flow", "operating_cash_flow", "net_income", "total_debt", "cash", "ebitda")  # reporting currency
_QUOTE_MONETARY_FIELDS = ("market_cap", "target_mean_price")  # quote currency
_RATIO_FIELDS = (
    "pe", "forward_pe", "peg", "ps_ttm", "ev_ebitda", "profit_margin", "gross_margin",
    "revenue_growth", "earnings_growth", "debt_to_equity", "return_on_equity", "beta",
    "dividend_yield", "recommendation_mean",
)
_SCALE_LABELS = ((1_000_000_000_000.0, "trillion"), (1_000_000_000.0, "billion"), (1_000_000.0, "million"))
_ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_NON_ISO_CODES = frozenset({"GBX"})  # pence-quoted UK listings — never == GBP

def _valid_iso_currency(value: Any) -> Optional[str]:
    ok = isinstance(value, str) and _ISO_CURRENCY_RE.fullmatch(value) and value not in _NON_ISO_CODES
    return value if ok else None

def _finite(value: Any) -> Optional[float]:
    value = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return value if value is not None and math.isfinite(value) else None

def _format_scaled(value: float, currency: str) -> str:
    magnitude = abs(value)
    for threshold, label in _SCALE_LABELS:
        if magnitude >= threshold:
            return f"{currency} {value / threshold:.1f} {label}"
    return f"{currency} {value:,.2f}"

def normalize_fundamentals(raw: dict[str, Any]) -> dict[str, Any]:
    """Label each field by its real currency domain — never substituted."""
    reporting_currency = _valid_iso_currency(raw.get("financial_currency"))
    quote_currency = _valid_iso_currency(raw.get("quote_currency"))
    monetary: dict[str, Any] = {}
    compact: dict[str, str] = {}
    gaps: list[str] = []

    def _add(field: str, currency: Optional[str], *, unit: Optional[str] = None) -> None:
        value = _finite(raw.get(field))
        if value is None:
            return
        if currency is None:
            gaps.append(field)
            return
        label = f"{currency} {value:,.2f} per share" if unit else _format_scaled(value, currency)
        entry = {"value": value, "currency": currency, "as_reported": True, "source_field": field}
        monetary[field] = {**entry, "unit": unit} if unit else entry
        compact[field] = label

    for field in _STATEMENT_FIELDS:
        _add(field, reporting_currency)
    for field in _QUOTE_MONETARY_FIELDS:
        _add(field, quote_currency)
    _add("eps", quote_currency, unit="per_share")

    ratios = {f: _finite(raw.get(f)) for f in _RATIO_FIELDS if _finite(raw.get(f)) is not None}
    return {
        "schema_version": NORMALIZATION_VERSION,
        "market_price_currency": quote_currency,
        "reporting_currency": reporting_currency,
        "monetary": monetary,
        "ratios": ratios,
        "compact": compact,
        "normalization_gaps": sorted(set(gaps)),
    }

_MAX_ACCEPTED_ARTICLES = 8
_MAX_ARTICLE_AGE_DAYS = 14

def _normalize_news_item(raw: dict[str, Any]) -> dict[str, Any]:
    # Unifies the legacy top-level shape + the nested {content: {...}} shape.
    content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
    provider = content.get("provider") if isinstance(content.get("provider"), dict) else {}
    canonical = content.get("canonicalUrl") if isinstance(content.get("canonicalUrl"), dict) else {}
    return {
        "headline": content.get("title") or raw.get("headline") or raw.get("title"),
        "summary": content.get("summary") or content.get("description") or raw.get("summary"),
        "datetime": content.get("pubDate") or raw.get("datetime") or raw.get("providerPublishTime"),
        "source": provider.get("displayName") or raw.get("source") or raw.get("publisher"),
        "id": raw.get("id") or raw.get("uuid") or content.get("id"),
        "link": canonical.get("url") or raw.get("link"),
        "related_tickers": raw.get("related_tickers") or raw.get("relatedTickers") or content.get("relatedTickers") or [],
    }

def _valid_publication(raw_dt: Any, now: datetime) -> Optional[datetime]:
    # Finite Unix timestamp OR ISO/RFC3339 datetime — never fetch time.
    if isinstance(raw_dt, str):
        try:
            published = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
        except ValueError:
            return None
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
    else:
        try:
            ts = float(raw_dt)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(ts) or ts <= 0:
            return None
        try:
            published = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if published > now + timedelta(hours=1) or published < now - timedelta(days=_MAX_ARTICLE_AGE_DAYS):
        return None
    return published

def _is_relevant(item: dict[str, Any], ticker: str) -> bool:
    # related_tickers, when nonempty, is AUTHORITATIVE — no text override on
    # mismatch. Exact-token text is a fallback only when absent (no fuzzy).
    related = item.get("related_tickers") or []
    if isinstance(related, list) and related:
        return any(str(r).strip().upper() == ticker.upper() for r in related)
    if len(ticker) < 3:
        return False
    text = f"{item.get('headline') or ''} {item.get('summary') or ''}"
    return bool(re.search(r"\b" + re.escape(ticker) + r"\b", text, re.IGNORECASE))

def _identity_key(item: dict[str, Any]) -> Optional[str]:
    for field in ("id", "link"):
        value = item.get(field)
        if value:
            return f"{field}:{value}"
    headline = (item.get("headline") or "").strip().lower()
    return f"headline:{headline}:{item.get('datetime')}" if headline else None

def filter_news_items(
    raw_items: list[dict[str, Any]], ticker: str, now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Normalize both provider shapes, then relevance+timestamp+dedup gate —
    only accepted articles reach durable evidence/prompt. URLs never logged."""
    now = now or datetime.now(timezone.utc)
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    rejected_invalid_timestamp = 0
    rejected_irrelevant = 0
    duplicate = 0
    for raw in raw_items or []:
        item = _normalize_news_item(raw)
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
