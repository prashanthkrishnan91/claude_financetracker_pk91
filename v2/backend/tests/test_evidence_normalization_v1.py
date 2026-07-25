"""financial_evidence_normalization_v1 — currency labeling + news relevance/
timestamp/dedup filtering (final Run Intel operational-reliability PR,
sections 2-3). Acceptance matrix rows 6-15.
"""
from __future__ import annotations

import time

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


def test_tsm_adr_shaped_reporter_keeps_twd_financials_never_dollar_labeled():
    # Production-shaped TSM fixture: USD quote, TWD financial statements.
    raw = {
        "revenue": 4_400_000_000_000.0,
        "cash": 2_100_000_000_000.0,
        "free_cash_flow": 739_000_000_000.0,
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
    assert out["compact"]["revenue"] == "TWD 4.4 trillion"
    assert out["compact"]["free_cash_flow"] == "TWD 739.0 billion"


def test_unknown_reporting_currency_excludes_monetary_never_guesses():
    raw = {"market_cap": 1_000_000_000.0, "quote_currency": "USD"}  # no financial_currency
    out = normalize_fundamentals(raw)
    assert out["reporting_currency"] is None
    assert "market_cap" not in out["monetary"]
    assert "market_cap" not in out["compact"]
    assert "market_cap" in out["normalization_gaps"]


def test_ratios_and_percentages_remain_dimensionless():
    raw = {"pe": 21.0, "profit_margin": 0.24, "beta": 1.1, "debt_to_equity": 80.0, "financial_currency": "USD"}
    out = normalize_fundamentals(raw)
    assert out["ratios"] == {"pe": 21.0, "profit_margin": 0.24, "beta": 1.1, "debt_to_equity": 80.0}


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
