"""Stage 5F — Multi-lane evidence population adapter (pure, no IO).

Converts raw data from existing yfinance sync providers into WorkerOutput
objects suitable for ResearchArtifactServiceV1.write_artifact().

Three feasible lanes are implemented — all backed by existing sync providers:

  FUNDAMENTALS  → fundamental_quality artifact (yfinance fundamentals)
  TECHNICALS    → technical_signal artifact   (yfinance price history)
  NEWS_SENTIMENT → sentiment_event artifact   (yfinance news headlines)

Deferred lanes (documented):
  SEC_FILING   — already handled by earnings_reviewer (catalyst_window).
                 A separate filing_risk adapter would need dedicated XBRL parsing
                 work beyond what is available here. Deferred to Stage 5G.
  ANALYST_REVISIONS — yfinance provides only recommendation_mean + target_mean_price
                 (two scalars). Insufficient depth for a dedicated analyst_revisions
                 artifact without a richer consensus provider. Deferred to Stage 5G.

What this module NEVER does:
  - Calls any external network API.
  - Imports or calls decide().
  - Writes to intel_v3_snapshots or any visible-decision table.
  - Produces forbidden payload keys.
  - Sets safe_for_decision = True.
  - Fabricates data not present in the raw provider dict.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from .contracts import (
    AuditEventRecord,
    FactRecord,
    SourceRecord,
    WorkerInput,
    WorkerOutput,
    compute_input_fingerprint,
    compute_replay_idempotency_key,
)

# ── Lane identifiers ──────────────────────────────────────────────────────────

LANE_FUNDAMENTALS = "fundamentals"
LANE_TECHNICALS = "technicals"
LANE_NEWS_SENTIMENT = "news_sentiment"

FEASIBLE_LANES: frozenset[str] = frozenset({
    LANE_FUNDAMENTALS,
    LANE_TECHNICALS,
    LANE_NEWS_SENTIMENT,
})

DEFERRED_LANES: dict[str, str] = {
    "sec_filing": (
        "Already handled by earnings_reviewer (catalyst_window). "
        "Separate filing_risk adapter requires XBRL parsing work beyond current scope. "
        "Deferred to Stage 5G."
    ),
    "analyst_revisions": (
        "yfinance provides only recommendation_mean + target_mean_price — "
        "insufficient depth for a dedicated analyst_revisions artifact "
        "without a richer consensus provider. Deferred to Stage 5G."
    ),
    "company_strategy": (
        "No dedicated guidance/commentary extractor exists in the repo. "
        "Deferred to Stage 5G after analyst_revisions provider is established."
    ),
}

# ── Artifact type and skill pack constants ────────────────────────────────────

_FUNDAMENTALS_ARTIFACT_TYPE = "fundamental_quality"
_FUNDAMENTALS_SKILL_PACK = "fundamentals_evidence_v1"
_FUNDAMENTALS_MODEL_VERSION = "yfinance_fundamentals_sync_v1"

_TECHNICALS_ARTIFACT_TYPE = "technical_signal"
_TECHNICALS_SKILL_PACK = "technicals_evidence_v1"
_TECHNICALS_MODEL_VERSION = "yfinance_price_history_sync_v2"

_NEWS_SENTIMENT_ARTIFACT_TYPE = "sentiment_event"
_NEWS_SENTIMENT_SKILL_PACK = "news_sentiment_evidence_v1"
_NEWS_SENTIMENT_MODEL_VERSION = "yfinance_news_sync_v1"

_SCOPE_KIND = "ticker"

# ── Freshness window ──────────────────────────────────────────────────────────

# News items older than this are STALE (seconds).
_NEWS_FRESH_WINDOW_SECONDS = 7 * 24 * 3600  # 7 days


# ── Internal adapter result type ──────────────────────────────────────────────

@dataclass
class _LaneAdapterResult:
    """Intermediate adapter result; plugs directly into WorkerOutput fields."""
    sources: list[SourceRecord] = field(default_factory=list)
    facts: list[FactRecord] = field(default_factory=list)
    confidence_or_trust_level: str = "UNKNOWN"
    freshness_status: str = "UNKNOWN"
    source_refs_fingerprint: str = "no_data"
    summary: str = ""
    limitations: list[str] = field(default_factory=list)
    source_window_start: Optional[str] = None
    source_window_end: Optional[str] = None
    expires_at: Optional[str] = None
    artifact_payload_extra: dict[str, Any] = field(default_factory=dict)


# ── Public lane adapter functions ─────────────────────────────────────────────

def adapt_fundamentals(
    raw: dict[str, Any],
    ticker: str,
    fetched_at: str,
) -> _LaneAdapterResult:
    """Convert yfinance fundamentals dict → adapter result.

    Honest thin-evidence: if raw is empty, returns UNKNOWN/no-data with
    a limitation recorded. Never fabricates values.

    Args:
        raw:        Output of fetch_yfinance_fundamentals_sync(ticker).
        ticker:     Upper-cased ticker symbol.
        fetched_at: ISO 8601 fetch timestamp (used as source_published_at).
    """
    if not raw:
        return _LaneAdapterResult(
            source_refs_fingerprint="fundamentals_no_data",
            summary=f"Fundamentals for {ticker}: no data returned by yfinance provider.",
            limitations=["yfinance returned empty fundamentals — no metrics available."],
        )

    # Build a single SourceRecord for the yfinance fundamentals fetch.
    source = SourceRecord(
        source_kind="vendor_fundamentals",
        provider_name="yfinance",
        provider_version=_FUNDAMENTALS_MODEL_VERSION,
        source_published_at=fetched_at,
        fetched_at=fetched_at,
        section_reference="yfinance.Ticker.info",
    )
    sources = [source]

    # Determine which numeric metrics are present (non-None).
    _NUMERIC_FIELDS = [
        "pe", "forward_pe", "peg", "ps_ttm", "ev_ebitda",
        "eps", "profit_margin", "gross_margin",
        "revenue_growth", "earnings_growth",
        "debt_to_equity", "return_on_equity",
        "beta", "market_cap", "free_cash_flow",
        "operating_cash_flow", "net_income", "revenue",
        "total_debt", "cash", "ebitda", "dividend_yield",
    ]
    present_metrics = [f for f in _NUMERIC_FIELDS if raw.get(f) is not None]
    missing_metrics = [f for f in _NUMERIC_FIELDS if raw.get(f) is None]

    facts: list[FactRecord] = []
    for metric_name in present_metrics:
        facts.append(FactRecord(
            fact_kind="metric_observation",
            structured_payload={
                "metric_name": metric_name,
                "value": raw[metric_name],
                "provider": "yfinance",
            },
            axis_hint="quality",
            as_of=fetched_at,
            is_quote_grounded=False,
            source_index=0,
        ))

    # Sector/industry as quality observation facts.
    for qual_field in ("sector", "industry"):
        val = raw.get(qual_field)
        if val:
            facts.append(FactRecord(
                fact_kind="quality_observation",
                structured_payload={
                    "field": qual_field,
                    "value": val,
                    "provider": "yfinance",
                },
                axis_hint="quality",
                as_of=fetched_at,
                is_quote_grounded=False,
                source_index=0,
            ))

    # Deterministic fingerprint from sorted present metric names.
    fp_data = {"ticker": ticker, "provider": "yfinance", "present_metrics": sorted(present_metrics)}
    source_refs_fingerprint = hashlib.sha256(
        json.dumps(fp_data, sort_keys=True).encode()
    ).hexdigest()[:32]

    confidence = "MEDIUM" if len(present_metrics) >= 4 else ("LOW" if present_metrics else "UNKNOWN")
    freshness = "FRESH" if present_metrics else "UNKNOWN"

    limitations: list[str] = [
        "Fundamentals sourced from yfinance — delayed/cached data, not real-time.",
        "analyst recommendation_mean and target_mean_price excluded "
        "(thin analyst consensus — deferred to analyst_revisions lane in Stage 5G).",
    ]
    if missing_metrics:
        limitations.append(
            f"{len(missing_metrics)} metric(s) not returned by yfinance: "
            + ", ".join(missing_metrics[:6])
            + ("..." if len(missing_metrics) > 6 else "")
        )

    summary = (
        f"Fundamentals for {ticker}: {len(present_metrics)} metric(s) retrieved "
        f"via yfinance (confidence={confidence}, freshness={freshness}). "
        f"Sector={raw.get('sector') or 'n/a'}, industry={raw.get('industry') or 'n/a'}. "
        f"No analyst estimates or forward guidance."
    )

    return _LaneAdapterResult(
        sources=sources,
        facts=facts,
        confidence_or_trust_level=confidence,
        freshness_status=freshness,
        source_refs_fingerprint=source_refs_fingerprint,
        summary=summary,
        limitations=limitations,
        source_window_start=fetched_at,
        source_window_end=fetched_at,
        artifact_payload_extra={
            "present_metric_count": len(present_metrics),
            "missing_metric_count": len(missing_metrics),
            "sector": raw.get("sector") or None,
            "industry": raw.get("industry") or None,
        },
    )


def adapt_technicals(
    raw: dict[str, Any],
    ticker: str,
    fetched_at: str,
) -> _LaneAdapterResult:
    """Convert yfinance price history dict → adapter result.

    Args:
        raw:        Output of fetch_yfinance_history_sync(ticker).
        ticker:     Upper-cased ticker symbol.
        fetched_at: ISO 8601 fetch timestamp.
    """
    if not raw:
        return _LaneAdapterResult(
            source_refs_fingerprint="technicals_no_data",
            summary=f"Technicals for {ticker}: no price history returned by yfinance.",
            limitations=["yfinance returned empty price history — no technical metrics available."],
        )

    # source_kind="other": no dedicated price/technical kind exists in the DB enum
    # (sec_filing, transcript, vendor_calendar, news, vendor_fundamentals,
    # vendor_estimates, peer_set_def, press_release, company_disclosure, other).
    # "other" is the honest choice; "vendor_fundamentals" would misclassify
    # price history as a fundamentals data feed.
    source = SourceRecord(
        source_kind="other",
        provider_name="yfinance",
        provider_version=_TECHNICALS_MODEL_VERSION,
        source_published_at=fetched_at,
        fetched_at=fetched_at,
        section_reference="yfinance.Ticker.history(period=3mo)",
    )
    sources = [source]

    _PRICE_FIELDS = [
        "last", "high_3mo", "low_3mo",
        "pct_1d", "pct_5d", "pct_30d", "pct_3mo",
        "sma20", "sma50", "vol_last", "vol_avg_20d",
        "volatility_30d", "n_bars",
    ]
    present_fields = [f for f in _PRICE_FIELDS if raw.get(f) is not None]
    missing_fields = [f for f in _PRICE_FIELDS if raw.get(f) is None]

    facts: list[FactRecord] = []
    for field_name in present_fields:
        facts.append(FactRecord(
            fact_kind="metric_observation",
            structured_payload={
                "metric_name": field_name,
                "value": raw[field_name],
                "provider": "yfinance",
            },
            axis_hint="price",
            as_of=fetched_at,
            is_quote_grounded=False,
            source_index=0,
        ))

    fp_data = {"ticker": ticker, "provider": "yfinance", "present_fields": sorted(present_fields)}
    source_refs_fingerprint = hashlib.sha256(
        json.dumps(fp_data, sort_keys=True).encode()
    ).hexdigest()[:32]

    confidence = "MEDIUM" if len(present_fields) >= 4 else ("LOW" if present_fields else "UNKNOWN")
    freshness = "FRESH" if present_fields else "UNKNOWN"

    limitations = [
        "Price history from yfinance — covers 3-month window only.",
        "No options data, no order book, no real-time feed.",
    ]
    if missing_fields:
        limitations.append(
            f"{len(missing_fields)} field(s) not returned by yfinance: "
            + ", ".join(missing_fields[:6])
            + ("..." if len(missing_fields) > 6 else "")
        )

    summary = (
        f"Technicals for {ticker}: {len(present_fields)} price metric(s) from yfinance "
        f"3-month history (n_bars={raw.get('n_bars')}, last={raw.get('last')}, "
        f"pct_30d={raw.get('pct_30d')}, sma20={raw.get('sma20')}, "
        f"confidence={confidence}, freshness={freshness})."
    )

    return _LaneAdapterResult(
        sources=sources,
        facts=facts,
        confidence_or_trust_level=confidence,
        freshness_status=freshness,
        source_refs_fingerprint=source_refs_fingerprint,
        summary=summary,
        limitations=limitations,
        source_window_start=fetched_at,
        source_window_end=fetched_at,
        artifact_payload_extra={
            "present_field_count": len(present_fields),
            "missing_field_count": len(missing_fields),
            "n_bars": raw.get("n_bars"),
        },
    )


def adapt_news_sentiment(
    items: list[dict[str, Any]],
    ticker: str,
    fetched_at: str,
) -> _LaneAdapterResult:
    """Convert yfinance news item list → adapter result.

    Each item has: headline, summary, datetime (unix epoch), source.
    Produces one SourceRecord per news item and one catalyst_item FactRecord per item.

    Args:
        items:      Output of fetch_yfinance_news_sync(ticker).
        ticker:     Upper-cased ticker symbol.
        fetched_at: ISO 8601 fetch timestamp.
    """
    if not items:
        return _LaneAdapterResult(
            source_refs_fingerprint="news_no_items",
            summary=f"News/sentiment for {ticker}: no news items returned by yfinance.",
            limitations=["yfinance returned no news items — sentiment evidence unavailable."],
        )

    now_epoch = datetime.now(timezone.utc).timestamp()
    sources: list[SourceRecord] = []
    facts: list[FactRecord] = []

    freshest_epoch: Optional[float] = None

    for i, item in enumerate(items):
        headline = (item.get("headline") or "").strip()
        if not headline:
            continue
        item_epoch = item.get("datetime") or 0
        if item_epoch and (freshest_epoch is None or item_epoch > freshest_epoch):
            freshest_epoch = float(item_epoch)

        pub_at: Optional[str] = None
        if item_epoch:
            try:
                pub_at = datetime.fromtimestamp(float(item_epoch), tz=timezone.utc).isoformat()
            except Exception:
                pub_at = None

        src = SourceRecord(
            source_kind="news",
            provider_name="yfinance",
            provider_version=_NEWS_SENTIMENT_MODEL_VERSION,
            source_published_at=pub_at,
            fetched_at=fetched_at,
            section_reference=item.get("source") or "yfinance",
        )
        sources.append(src)

        # FactRecord payload: headline + source label only; no scores, no LLM labels.
        facts.append(FactRecord(
            fact_kind="catalyst_item",
            structured_payload={
                "headline": headline,
                "source_label": item.get("source") or "yfinance",
                "item_index": i,
            },
            axis_hint="catalyst",
            as_of=pub_at or fetched_at,
            is_quote_grounded=True,  # headline is a direct quote from the news item
            source_index=len(sources) - 1,
        ))

    if not sources:
        return _LaneAdapterResult(
            source_refs_fingerprint="news_no_valid_headlines",
            summary=f"News/sentiment for {ticker}: items present but no valid headlines found.",
            limitations=["News items present but all lacked valid headlines — no facts produced."],
        )

    # Freshness based on most recent item.
    freshness = "UNKNOWN"
    if freshest_epoch:
        age_seconds = now_epoch - freshest_epoch
        freshness = "FRESH" if age_seconds < _NEWS_FRESH_WINDOW_SECONDS else "STALE"

    # Fingerprint from headline set (sorted for determinism).
    headlines_sorted = sorted(
        f.structured_payload["headline"] for f in facts
        if f.structured_payload.get("headline")
    )
    fp_data = {"ticker": ticker, "provider": "yfinance", "headlines": headlines_sorted[:10]}
    source_refs_fingerprint = hashlib.sha256(
        json.dumps(fp_data, sort_keys=True).encode()
    ).hexdigest()[:32]

    limitations = [
        "News headlines only — no LLM sentiment scoring in this lane.",
        "yfinance news may have up to several hours of delay.",
        "No transcript, earnings call, or press-release text extraction.",
    ]

    summary = (
        f"News/sentiment for {ticker}: {len(facts)} headline(s) from yfinance "
        f"(freshness={freshness}). Headline evidence only — no sentiment scoring."
    )

    return _LaneAdapterResult(
        sources=sources,
        facts=facts,
        confidence_or_trust_level="LOW",  # headlines without LLM scoring are LOW confidence
        freshness_status=freshness,
        source_refs_fingerprint=source_refs_fingerprint,
        summary=summary,
        limitations=limitations,
        source_window_start=fetched_at,
        source_window_end=fetched_at,
        artifact_payload_extra={
            "headline_count": len(facts),
        },
    )


# ── WorkerOutput builder ──────────────────────────────────────────────────────

def build_worker_output(
    worker_input: WorkerInput,
    adapter_result: _LaneAdapterResult,
    artifact_type: str,
    skill_pack: str,
    model_version: str,
) -> WorkerOutput:
    """Assemble a WorkerOutput from a lane adapter result.

    This is the shared builder — all three lanes funnel through here.
    """
    ticker = (worker_input.ticker or "").upper().strip()

    fp_data: dict[str, Any] = {
        "skill_pack": skill_pack,
        "ticker": ticker,
        "model_version": model_version,
        "phase": "stage5f_multi_lane",
    }
    if worker_input.holding_context:
        fp_data["context_keys"] = sorted(worker_input.holding_context.keys())
    input_fingerprint = compute_input_fingerprint(fp_data)

    replay_key = compute_replay_idempotency_key(
        skill_pack=skill_pack,
        scope_kind=_SCOPE_KIND,
        ticker=ticker,
        source_refs_fingerprint=adapter_result.source_refs_fingerprint,
        model_version=model_version,
    )

    payload: dict[str, Any] = {
        "lane": skill_pack,
        "reviewed_ticker": ticker,
        "worker_phase": "stage5f_multi_lane_evidence",
        "provider": "yfinance",
    }
    payload.update(adapter_result.artifact_payload_extra)

    audit_events: list[AuditEventRecord] = [
        AuditEventRecord(
            tool_call=f"{skill_pack}_run",
            status="completed",
            model_id=None,
            model_version=model_version,
            cost_estimate_usd=0.0,
            latency_ms=0,
        )
    ]

    return WorkerOutput(
        worker_run_id=worker_input.worker_run_id,
        ticker=ticker,
        artifact_type=artifact_type,
        skill_pack=skill_pack,
        scope_kind=_SCOPE_KIND,
        artifact_payload=payload,
        sources=adapter_result.sources,
        facts=adapter_result.facts,
        audit_events=audit_events,
        evidence_summary_plain_english=adapter_result.summary,
        limitations_or_missing_evidence=adapter_result.limitations,
        confidence_or_trust_level=adapter_result.confidence_or_trust_level,
        freshness_status=adapter_result.freshness_status,
        input_fingerprint=input_fingerprint,
        replay_idempotency_key=replay_key,
        source_window_start=adapter_result.source_window_start,
        source_window_end=adapter_result.source_window_end,
        expires_at=adapter_result.expires_at,
        parent_intel_run_id=worker_input.parent_intel_run_id,
        generated_by_model=None,
        model_version=model_version,
    )


# ── Per-lane WorkerOutput builders ────────────────────────────────────────────

def build_fundamentals_worker_output(
    worker_input: WorkerInput,
    raw: dict[str, Any],
    fetched_at: str,
) -> WorkerOutput:
    ticker = (worker_input.ticker or "").upper().strip()
    result = adapt_fundamentals(raw, ticker, fetched_at)
    return build_worker_output(
        worker_input, result,
        artifact_type=_FUNDAMENTALS_ARTIFACT_TYPE,
        skill_pack=_FUNDAMENTALS_SKILL_PACK,
        model_version=_FUNDAMENTALS_MODEL_VERSION,
    )


def build_technicals_worker_output(
    worker_input: WorkerInput,
    raw: dict[str, Any],
    fetched_at: str,
) -> WorkerOutput:
    ticker = (worker_input.ticker or "").upper().strip()
    result = adapt_technicals(raw, ticker, fetched_at)
    return build_worker_output(
        worker_input, result,
        artifact_type=_TECHNICALS_ARTIFACT_TYPE,
        skill_pack=_TECHNICALS_SKILL_PACK,
        model_version=_TECHNICALS_MODEL_VERSION,
    )


def build_news_sentiment_worker_output(
    worker_input: WorkerInput,
    items: list[dict[str, Any]],
    fetched_at: str,
) -> WorkerOutput:
    ticker = (worker_input.ticker or "").upper().strip()
    result = adapt_news_sentiment(items, ticker, fetched_at)
    return build_worker_output(
        worker_input, result,
        artifact_type=_NEWS_SENTIMENT_ARTIFACT_TYPE,
        skill_pack=_NEWS_SENTIMENT_SKILL_PACK,
        model_version=_NEWS_SENTIMENT_MODEL_VERSION,
    )
