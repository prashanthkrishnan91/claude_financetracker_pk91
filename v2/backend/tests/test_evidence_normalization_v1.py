"""financial_evidence_normalization_v1 — currency labeling + news relevance/
timestamp/dedup filtering (final Run Intel operational-reliability PR,
sections 2-3). Acceptance matrix rows 6-15.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from app.services.intelligence.v3.distributed.evidence_normalization_v1 import (
    filter_news_items,
    normalize_fundamentals,
)
from app.services.intelligence.v3.distributed.collectors_v1 import (
    _collect_fundamentals,
    _collect_news,
)


def _now_ts() -> float:
    return time.time()


# ── Currency normalization ───────────────────────────────────────────────────

def test_usd_reporter_keeps_usd_labels_no_conversion():
    raw = {"market_cap": 3_450_000_000_000.0, "financial_currency": "USD", "quote_currency": "USD", "pe": 30.0}
    out = normalize_fundamentals(raw)
    assert out["reporting_currency"] == "USD"
    assert out["market_price_currency"] == "USD"
    assert out["monetary"]["market_cap"]["currency"] == "USD"
    assert out["monetary"]["market_cap"]["value"] == 3_450_000_000_000.0
    assert out["compact"]["market_cap"].startswith("USD")


def test_tsm_adr_shaped_reporter_splits_quote_vs_statement_currency():
    # Production-shaped TSM fixture: USD quote (market cap/target/EPS), TWD
    # financial statements (revenue/cash/free cash flow) — same normalized
    # output, no conversion, never mislabeled either direction.
    raw = {
        "revenue": 4_400_000_000_000.0,
        "cash": 2_100_000_000_000.0,
        "free_cash_flow": 739_000_000_000.0,
        "market_cap": 750_000_000_000.0,
        "target_mean_price": 210.0,
        "eps": 6.5,
        "financial_currency": "TWD",
        "quote_currency": "USD",
        "pe": 24.0,
    }
    out = normalize_fundamentals(raw)
    assert out["market_price_currency"] == "USD"
    assert out["reporting_currency"] == "TWD"
    for field in ("revenue", "cash", "free_cash_flow"):
        assert out["monetary"][field]["currency"] == "TWD"
        assert out["compact"][field].startswith("TWD")
        assert "$" not in out["compact"][field]
    for field in ("market_cap", "target_mean_price", "eps"):
        assert out["monetary"][field]["currency"] == "USD"
        assert not out["compact"][field].startswith("TWD")
    assert out["compact"]["revenue"] == "TWD 4.4 trillion"
    assert out["compact"]["free_cash_flow"] == "TWD 739.0 billion"
    assert out["monetary"]["eps"]["unit"] == "per_share"
    assert out["compact"]["eps"] == "USD 6.50 per share"


def test_unknown_statement_currency_excludes_statement_fields_only():
    raw = {"revenue": 1_000_000_000.0, "market_cap": 2_000_000_000.0, "quote_currency": "USD"}
    out = normalize_fundamentals(raw)
    assert out["reporting_currency"] is None
    assert "revenue" not in out["monetary"]
    assert "revenue" in out["normalization_gaps"]
    # Quote-domain field still resolves — never blocked by the other domain.
    assert out["monetary"]["market_cap"]["currency"] == "USD"


def test_unknown_quote_currency_excludes_quote_fields_only():
    raw = {"revenue": 1_000_000_000.0, "market_cap": 2_000_000_000.0, "eps": 1.5, "financial_currency": "TWD"}
    out = normalize_fundamentals(raw)
    assert out["market_price_currency"] is None
    assert "market_cap" not in out["monetary"]
    assert "eps" not in out["monetary"]
    assert {"market_cap", "eps"} <= set(out["normalization_gaps"])
    assert out["monetary"]["revenue"]["currency"] == "TWD"


def test_nan_and_infinite_values_rejected():
    raw = {
        "revenue": float("nan"), "cash": float("inf"), "ebitda": float("-inf"),
        "pe": float("nan"), "financial_currency": "USD", "quote_currency": "USD",
    }
    out = normalize_fundamentals(raw)
    assert out["monetary"] == {}
    assert out["ratios"] == {}


def test_gbp_pence_units_never_silently_converted_to_gbp():
    for bad_unit in ("GBp", "GBX", "gbp"):
        raw = {"revenue": 1_000_000_000.0, "financial_currency": bad_unit}
        out = normalize_fundamentals(raw)
        assert out["reporting_currency"] is None, bad_unit
        assert "revenue" in out["normalization_gaps"], bad_unit


def test_ratios_and_percentages_remain_dimensionless():
    raw = {"pe": 21.0, "profit_margin": 0.24, "beta": 1.1, "debt_to_equity": 80.0, "financial_currency": "USD"}
    out = normalize_fundamentals(raw)
    assert out["ratios"] == {"pe": 21.0, "profit_margin": 0.24, "beta": 1.1, "debt_to_equity": 80.0}
    assert "eps" not in out["ratios"]


# ── News relevance / timestamp / dedup ───────────────────────────────────────

def test_valid_related_ticker_article_accepted_with_real_utc_time():
    items = [{"headline": "Update", "source": "yfinance", "datetime": _now_ts(), "related_tickers": ["AAPL"]}]
    out = filter_news_items(items, "AAPL")
    assert out["accepted_count"] == 1
    assert out["items"][0]["published_at"]


def test_zero_future_and_stale_timestamps_rejected_never_replaced():
    items = [
        {"headline": "AAPL zero", "datetime": 0, "related_tickers": ["AAPL"]},
        {"headline": "AAPL future", "datetime": _now_ts() + 999_999, "related_tickers": ["AAPL"]},
        {"headline": "AAPL stale", "datetime": _now_ts() - 86400 * 60, "related_tickers": ["AAPL"]},
    ]
    out = filter_news_items(items, "AAPL")
    assert out["accepted_count"] == 0
    assert out["rejected_invalid_timestamp_count"] == 3


def test_unrelated_company_in_blsh_result_rejected():
    items = [{"headline": "Unrelated Co posts earnings", "datetime": _now_ts(), "related_tickers": []}]
    out = filter_news_items(items, "BLSH")
    assert out["accepted_count"] == 0
    assert out["rejected_irrelevant_count"] == 1


def test_googl_options_article_in_nflx_result_rejected():
    items = [{"headline": "GOOGL options volume surges", "datetime": _now_ts(), "related_tickers": ["GOOGL"]}]
    out = filter_news_items(items, "NFLX")
    assert out["accepted_count"] == 0
    assert out["rejected_irrelevant_count"] == 1


def test_duplicate_url_and_id_collapse_to_one_item():
    ts = _now_ts()
    items = [
        {"headline": "AAPL A", "datetime": ts, "id": "abc", "related_tickers": ["AAPL"]},
        {"headline": "AAPL A dup", "datetime": ts, "id": "abc", "related_tickers": ["AAPL"]},
    ]
    out = filter_news_items(items, "AAPL")
    assert out["accepted_count"] == 1
    assert out["duplicate_count"] == 1


def test_one_relevant_one_irrelevant_in_same_response():
    ts = _now_ts()
    items = [
        {"headline": "AAPL launches product", "datetime": ts, "related_tickers": ["AAPL"]},
        {"headline": "Unrelated headline", "datetime": ts, "related_tickers": []},
    ]
    out = filter_news_items(items, "AAPL")
    assert out["accepted_count"] == 1
    assert out["rejected_irrelevant_count"] == 1


def test_no_accepted_articles_yields_honest_degraded_lane():
    items = [{"headline": "irrelevant", "datetime": _now_ts(), "related_tickers": []}]
    out = filter_news_items(items, "AAPL")
    assert out["items"] == []
    assert out["accepted_count"] == 0


# ── Collector wiring ──────────────────────────────────────────────────────────

import pytest


@pytest.mark.asyncio
async def test_collector_fundamentals_wires_normalized_projection(monkeypatch):
    import app.services.intelligence.v3.distributed.collectors_v1 as collectors

    async def _fake_fetch(_ticker):
        return {"revenue": 4_400_000_000_000.0, "financial_currency": "TWD", "quote_currency": "USD", "pe": 24.0}

    monkeypatch.setattr(collectors, "fetch_fundamentals", _fake_fetch)
    result = await _collect_fundamentals("TSM")
    assert result.output["normalized"]["reporting_currency"] == "TWD"
    assert result.output["normalized"]["compact"]["revenue"] == "TWD 4.4 trillion"


@pytest.mark.asyncio
async def test_collector_news_only_persists_accepted_articles(monkeypatch):
    import app.services.intelligence.v3.distributed.collectors_v1 as collectors

    async def _fake_fetch(_ticker):
        return [
            {"headline": "AAPL update", "datetime": _now_ts(), "related_tickers": ["AAPL"]},
            {"headline": "Unrelated", "datetime": _now_ts(), "related_tickers": []},
            {"headline": "AAPL old", "datetime": 0, "related_tickers": ["AAPL"]},
        ]

    monkeypatch.setattr(collectors, "fetch_yfinance_news", _fake_fetch)
    result = await _collect_news("AAPL")
    assert len(result.output["items"]) == 1
    assert result.output["items"][0]["headline"] == "AAPL update"
    assert result.output["rejected_irrelevant_count"] == 1
    assert result.output["rejected_invalid_timestamp_count"] == 1


def test_nested_yfinance_content_shape_extracts_and_retains_publication_date():
    ts = _now_ts()
    items = [{
        "id": "abc123",
        "content": {
            "title": "AAPL unveils new product",
            "summary": "Details on the announcement.",
            "pubDate": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "provider": {"displayName": "Reuters"},
            "canonicalUrl": {"url": "https://example.com/aapl-article"},
        },
        "related_tickers": ["AAPL"],
    }]
    out = filter_news_items(items, "AAPL")
    assert out["accepted_count"] == 1
    assert out["items"][0]["headline"] == "AAPL unveils new product"
    assert out["items"][0]["source"] == "Reuters"
    # The nested pubDate is retained as a real publication time, never zero.
    assert out["items"][0]["published_at"] is not None


def test_nested_shape_mismatched_related_ticker_rejects_despite_text_match():
    items = [{
        "content": {
            "title": "NFLX mentions GOOGL antitrust case",
            "pubDate": _now_ts(),
        },
        "related_tickers": ["GOOGL"],
    }]
    out = filter_news_items(items, "NFLX")
    assert out["accepted_count"] == 0
    assert out["rejected_irrelevant_count"] == 1


def test_metadata_absent_exact_safe_ticker_match_works():
    items = [{"headline": "AAPL raises guidance", "datetime": _now_ts()}]
    out = filter_news_items(items, "AAPL")
    assert out["accepted_count"] == 1


def test_ambiguous_short_ticker_text_alone_does_not_match():
    items = [{"headline": "F posts strong quarter", "datetime": _now_ts()}]
    out = filter_news_items(items, "F")
    assert out["accepted_count"] == 0
    assert out["rejected_irrelevant_count"] == 1


def test_numeric_and_iso_timestamps_normalize_identically():
    ts = _now_ts()
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    numeric_out = filter_news_items(
        [{"headline": "AAPL numeric", "datetime": ts, "related_tickers": ["AAPL"]}], "AAPL",
    )
    iso_out = filter_news_items(
        [{"headline": "AAPL iso", "datetime": iso, "related_tickers": ["AAPL"]}], "AAPL",
    )
    assert numeric_out["accepted_count"] == 1
    assert iso_out["accepted_count"] == 1


def test_duplicate_canonical_url_collapses_deterministically():
    ts = _now_ts()
    items = [
        {"content": {"title": "AAPL A", "pubDate": ts, "canonicalUrl": {"url": "https://x/a"}}, "related_tickers": ["AAPL"]},
        {"content": {"title": "AAPL A (mirror)", "pubDate": ts, "canonicalUrl": {"url": "https://x/a"}}, "related_tickers": ["AAPL"]},
    ]
    out = filter_news_items(items, "AAPL")
    assert out["accepted_count"] == 1
    assert out["duplicate_count"] == 1
