"""SystemModeManager tests — mode derivation + load-shedding.

Validates v4 distributed-correctness spec, task #3:
  * NORMAL when every provider is healthy.
  * DEGRADED when a critical provider (finnhub / coingecko) or Polygon is
    open.
  * LIGHTWEIGHT when two or more critical providers are open.
  * Cache is honoured for ``_MODE_CACHE_S`` seconds; ``invalidate_cache``
    forces a recompute.
  * ``batch_size_factor`` matches the spec (1.0 / 0.5 / 0.25).
  * Polygon is opportunistically skipped under DEGRADED+LIGHTWEIGHT.
"""

from __future__ import annotations

import pytest

from app.services.market_data.system_mode import (
    SystemMode,
    SystemModeManager,
)


def _mgr(status_map: dict[str, str]) -> SystemModeManager:
    """Build a manager with a frozen provider-status map."""
    return SystemModeManager(status_provider=lambda: status_map)


def test_normal_when_all_providers_ok():
    mgr = _mgr({"finnhub": "ok", "coingecko": "ok", "polygon": "ok", "yfinance": "ok"})
    state = mgr.current(force_refresh=True)
    assert state.mode == SystemMode.NORMAL
    assert state.open_providers == []
    assert mgr.batch_size_factor() == 1.0
    assert mgr.should_skip_polygon() is False
    assert mgr.should_skip_external_calls() is False


def test_degraded_when_polygon_open():
    mgr = _mgr({"finnhub": "ok", "coingecko": "ok", "polygon": "blocked"})
    state = mgr.current(force_refresh=True)
    assert state.mode == SystemMode.DEGRADED
    assert "polygon" in state.open_providers
    assert mgr.should_skip_polygon() is True
    assert mgr.should_skip_external_calls() is False
    assert mgr.batch_size_factor() == 0.5


def test_degraded_when_one_critical_provider_open():
    mgr = _mgr({"finnhub": "rate_limited", "coingecko": "ok", "polygon": "ok"})
    state = mgr.current(force_refresh=True)
    assert state.mode == SystemMode.DEGRADED
    assert "finnhub" in state.open_providers


def test_lightweight_when_two_critical_providers_open():
    mgr = _mgr({
        "finnhub": "rate_limited",
        "coingecko": "failed",
        "polygon": "ok",
    })
    state = mgr.current(force_refresh=True)
    assert state.mode == SystemMode.LIGHTWEIGHT
    assert set(state.open_providers) >= {"finnhub", "coingecko"}
    assert mgr.should_skip_external_calls() is True
    assert mgr.batch_size_factor() == 0.25


def test_cache_is_honoured_between_computes():
    """Repeated ``current()`` calls serve the cached state without re-deriving."""
    calls = {"n": 0}

    def status():
        calls["n"] += 1
        return {"finnhub": "ok", "coingecko": "ok", "polygon": "ok"}

    mgr = SystemModeManager(status_provider=status)
    mgr.current()  # miss
    mgr.current()  # hit
    mgr.current()  # hit
    assert calls["n"] == 1


def test_invalidate_cache_forces_recompute():
    """``invalidate_cache`` should flush the cached state immediately."""
    status = {"finnhub": "ok", "coingecko": "ok", "polygon": "ok"}
    mgr = SystemModeManager(status_provider=lambda: status)
    state1 = mgr.current()
    assert state1.mode == SystemMode.NORMAL

    # Flip to degraded and invalidate — next current() must see the change.
    status["finnhub"] = "rate_limited"
    mgr.invalidate_cache()
    state2 = mgr.current()
    assert state2.mode == SystemMode.DEGRADED


def test_state_to_dict_is_jsonable():
    """to_dict() output must be serialisable so it can ride in the LLM context."""
    import json

    mgr = _mgr({"finnhub": "ok", "coingecko": "ok", "polygon": "ok"})
    payload = mgr.current(force_refresh=True).to_dict()
    assert payload["mode"] == "NORMAL"
    # Round-trip through json to prove it's safe for LLM / DB serialisation.
    rt = json.loads(json.dumps(payload))
    assert rt["mode"] == "NORMAL"
    assert rt["open_providers"] == []
