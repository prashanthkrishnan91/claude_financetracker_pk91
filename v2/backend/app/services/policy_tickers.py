"""Loader for policy ticker configuration — tickers live in config, not code.

All ticker sets/maps used by policy source code are defined in
``app/policy_tickers.json`` (override the path with the POLICY_TICKERS_FILE
env var). Modules import the accessor functions below at import time; the
file is read once and cached.

Fail-loud by design: a missing file or missing key is a configuration error,
not something to silently default (a policy set that quietly becomes empty
would change decision behavior invisibly).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "policy_tickers.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    path = Path(os.getenv("POLICY_TICKERS_FILE") or _DEFAULT_PATH)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def ticker_set(key: str) -> frozenset[str]:
    """Return the configured ticker list ``key`` as an uppercase frozenset."""
    values = _load()[key]
    return frozenset(str(v).upper() for v in values)


def ticker_tuple(key: str) -> tuple[str, ...]:
    """Return the configured ticker list ``key`` as a tuple (order preserved)."""
    return tuple(str(v).upper() for v in _load()[key])


def ticker_map(key: str) -> dict[str, Any]:
    """Return the configured ticker→value mapping ``key`` (tickers uppercased)."""
    return {str(k).upper(): v for k, v in _load()[key].items()}


def benchmark_symbol() -> str:
    """The configured benchmark symbol (e.g. SPY)."""
    return str(_load()["benchmark_symbol"]).upper()
