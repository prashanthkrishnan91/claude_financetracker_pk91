"""Exponential-backoff retry tests for data_sources provider helpers.

Validates v3 stability-layer guarantees:
  * 429 is transient → retry up to 3 times with backoff + jitter.
  * 5xx is transient → same retry behaviour.
  * 403 is non-transient → NEVER retry (hard-blocked key / plan issue).
  * Network exceptions between attempts are retried (transient by default).
  * Jittered delay stays within the documented ±30% band.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_is_transient_status_classification():
    from app.services.agents.data_sources import _is_transient_status

    assert _is_transient_status(429) is True
    assert _is_transient_status(500) is True
    assert _is_transient_status(502) is True
    assert _is_transient_status(599) is True
    assert _is_transient_status(403) is False
    assert _is_transient_status(404) is False
    assert _is_transient_status(400) is False
    assert _is_transient_status(200) is False


def test_jittered_delay_within_band():
    from app.services.agents.data_sources import (
        _jittered_delay,
        _BACKOFF_MAX_DELAY_S,
    )

    # 30% jitter band around 1s → [0.7, 1.3]; clamped to 8s max.
    for _ in range(50):
        d = _jittered_delay(1.0)
        assert 0.7 <= d <= 1.3
    # Large base → clamped at ceiling.
    assert _jittered_delay(20.0) <= _BACKOFF_MAX_DELAY_S


@pytest.mark.asyncio
async def test_retry_http_retries_on_5xx_then_succeeds(monkeypatch):
    from app.services.agents import data_sources as ds

    ds.reset_breakers()
    monkeypatch.setattr(ds, "_BACKOFF_BASE_DELAYS_S", (0.001, 0.001, 0.001))
    attempt = 0

    class _Resp:
        def __init__(self, status): self.status_code = status

    async def perform():
        nonlocal attempt
        attempt += 1
        if attempt < 3:
            return _Resp(503)
        return _Resp(200)

    out = await ds._retry_http("finnhub", "AAPL", perform)
    assert out is not None
    assert out.status_code == 200
    assert attempt == 3


@pytest.mark.asyncio
async def test_retry_http_does_not_retry_on_403(monkeypatch):
    from app.services.agents import data_sources as ds

    ds.reset_breakers()
    monkeypatch.setattr(ds, "_BACKOFF_BASE_DELAYS_S", (0.001, 0.001, 0.001))
    attempt = 0

    class _Resp:
        status_code = 403

    async def perform():
        nonlocal attempt
        attempt += 1
        return _Resp()

    out = await ds._retry_http("finnhub", "AAPL", perform)
    assert out is not None
    assert out.status_code == 403
    # Hard block → exactly ONE call, no retries.
    assert attempt == 1


@pytest.mark.asyncio
async def test_retry_http_retries_network_exceptions(monkeypatch):
    from app.services.agents import data_sources as ds

    ds.reset_breakers()
    monkeypatch.setattr(ds, "_BACKOFF_BASE_DELAYS_S", (0.001, 0.001, 0.001))
    attempt = 0

    class _Resp:
        status_code = 200

    async def perform():
        nonlocal attempt
        attempt += 1
        if attempt < 2:
            raise RuntimeError("temporary connection reset")
        return _Resp()

    out = await ds._retry_http("coingecko", "BTC", perform)
    assert out is not None
    assert out.status_code == 200
    assert attempt == 2


@pytest.mark.asyncio
async def test_retry_http_gives_up_after_max_attempts_on_429(monkeypatch):
    from app.services.agents import data_sources as ds

    ds.reset_breakers()
    monkeypatch.setattr(ds, "_BACKOFF_BASE_DELAYS_S", (0.001, 0.001, 0.001))
    attempt = 0

    class _Resp:
        status_code = 429

    async def perform():
        nonlocal attempt
        attempt += 1
        return _Resp()

    out = await ds._retry_http("finnhub", "AAPL", perform)
    assert out is not None
    assert out.status_code == 429
    # Initial attempt + 3 retries per the backoff schedule.
    assert attempt == 4


@pytest.mark.asyncio
async def test_coingecko_returns_empty_on_403_without_retry(monkeypatch):
    """403 on CoinGecko should NOT trigger retries at the HTTP layer."""
    from app.services.agents import data_sources as ds

    ds.reset_breakers()
    monkeypatch.setattr(ds, "_BACKOFF_BASE_DELAYS_S", (0.001, 0.001, 0.001))
    call_count = 0

    class _Resp:
        status_code = 403

        def json(self):
            return {}

    async def fake_get(*args, **kwargs):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return _Resp()

    client = MagicMock()
    client.get = fake_get

    out = await ds.fetch_coingecko_market(client, "BTC")
    assert out == {}
    assert call_count == 1  # hard-block → no retries
    assert ds._BREAKERS["coingecko"].failures == 1

    ds.reset_breakers()
