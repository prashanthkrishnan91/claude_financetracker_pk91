"""Validated loader for decision-influencing policy ticker configuration.

Every ticker set/map that influences policy decisions lives in
``app/policy_tickers.json`` (packaged with the application, so the default
path resolves inside the deployed Railway package). The path may be
overridden with the ``POLICY_TICKERS_FILE`` env var.

Fail-loud by design: a missing file, missing key, malformed shape, unknown
ETF type/role, or duplicate ticker is a configuration error raised at first
access — never a silent empty fallback, because a policy set that quietly
became empty would change decision behavior invisibly.

Provider symbol-translation tables (crypto→Yahoo symbols, CoinGecko IDs,
etc.) intentionally stay in provider code: they route data, they are not
policy membership.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# app/services/policy_tickers.py → app/policy_tickers.json
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "policy_tickers.json"

_REQUIRED_LIST_KEYS = (
    "broad_index_core_preference_order",
    "alternatives_tickers",
    "crypto_tickers",
    "speculative_tickers",
    "kernel_crypto_tickers",
)

# The four ETF groups that collectively form the allocation policy's ETF floor.
_REQUIRED_ETF_GROUPS = (
    "broad_index_etf",
    "dividend_etf",
    "international_etf",
    "sector_etf",
)

# Allowed vocabulary for etf_classifier_map values. Mirrors the constants in
# services/intelligence/v3/etf_intelligence_classifier_v1.py; parity is
# asserted in tests/test_policy_tickers.py.
ALLOWED_ETF_TYPES = frozenset({
    "equity_etf", "sector_etf", "dividend_etf", "international_etf",
    "bond_etf", "commodity_trust", "crypto_etf", "unknown_fund",
})
ALLOWED_ETF_ROLES = frozenset({
    "core_us_equity", "growth_tilt", "dividend_income", "sector_tilt",
    "international_diversifier", "bond_stability", "commodity_hedge",
    "crypto_speculative", "cash_like", "unknown_role",
})


class PolicyTickerConfigError(RuntimeError):
    """Raised when the policy ticker configuration is missing or invalid."""


_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_path: str | None = None


def _normalize_list(key: str, values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        raise PolicyTickerConfigError(
            f"policy_tickers: key '{key}' must be a non-empty list of ticker strings"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not isinstance(v, str) or not v.strip():
            raise PolicyTickerConfigError(
                f"policy_tickers: key '{key}' contains a non-string or empty entry: {v!r}"
            )
        t = v.strip().upper()
        if t in seen:
            raise PolicyTickerConfigError(
                f"policy_tickers: duplicate ticker '{t}' in '{key}'"
            )
        seen.add(t)
        normalized.append(t)
    return normalized


def _validate(raw: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PolicyTickerConfigError(f"policy_tickers: {path} must contain a JSON object")

    cfg: dict[str, Any] = {}

    for key in _REQUIRED_LIST_KEYS:
        if key not in raw:
            raise PolicyTickerConfigError(f"policy_tickers: required key '{key}' missing in {path}")
        cfg[key] = _normalize_list(key, raw[key])

    groups_raw = raw.get("etf_groups")
    if not isinstance(groups_raw, dict):
        raise PolicyTickerConfigError(f"policy_tickers: required key 'etf_groups' missing or not an object in {path}")
    unknown_groups = set(groups_raw) - set(_REQUIRED_ETF_GROUPS)
    missing_groups = set(_REQUIRED_ETF_GROUPS) - set(groups_raw)
    if unknown_groups or missing_groups:
        raise PolicyTickerConfigError(
            "policy_tickers: etf_groups must contain exactly "
            f"{sorted(_REQUIRED_ETF_GROUPS)}; unknown={sorted(unknown_groups)} missing={sorted(missing_groups)}"
        )
    groups: dict[str, list[str]] = {
        g: _normalize_list(f"etf_groups.{g}", groups_raw[g]) for g in _REQUIRED_ETF_GROUPS
    }
    cfg["etf_groups"] = groups

    # Allocation classification sets are first-match — membership must be
    # unambiguous across every classification set.
    classification_sets: list[tuple[str, list[str]]] = [
        *[(f"etf_groups.{g}", tickers) for g, tickers in groups.items()],
        ("alternatives_tickers", cfg["alternatives_tickers"]),
        ("crypto_tickers", cfg["crypto_tickers"]),
        ("speculative_tickers", cfg["speculative_tickers"]),
    ]
    owner: dict[str, str] = {}
    for set_name, tickers in classification_sets:
        for t in tickers:
            if t in owner:
                raise PolicyTickerConfigError(
                    f"policy_tickers: ticker '{t}' appears in both '{owner[t]}' and "
                    f"'{set_name}' — allocation classification must be unambiguous"
                )
            owner[t] = set_name

    # Preference order must be a subset of the broad-index group (ranking is
    # meaningless for tickers the classifier will never place in that group).
    broad = set(groups["broad_index_etf"])
    for t in cfg["broad_index_core_preference_order"]:
        if t not in broad:
            raise PolicyTickerConfigError(
                f"policy_tickers: preference-order ticker '{t}' is not in etf_groups.broad_index_etf"
            )

    cmap_raw = raw.get("etf_classifier_map")
    if not isinstance(cmap_raw, dict) or not cmap_raw:
        raise PolicyTickerConfigError(f"policy_tickers: required key 'etf_classifier_map' missing or not a non-empty object in {path}")
    cmap: dict[str, tuple[str, str]] = {}
    for k, v in cmap_raw.items():
        t = str(k).strip().upper()
        if t in cmap:
            raise PolicyTickerConfigError(f"policy_tickers: duplicate ticker '{t}' in etf_classifier_map")
        if (
            not isinstance(v, list) or len(v) != 2
            or not all(isinstance(x, str) for x in v)
        ):
            raise PolicyTickerConfigError(
                f"policy_tickers: etf_classifier_map['{t}'] must be a [etf_type, etf_role] pair of strings"
            )
        etf_type, etf_role = v[0], v[1]
        if etf_type not in ALLOWED_ETF_TYPES:
            raise PolicyTickerConfigError(
                f"policy_tickers: etf_classifier_map['{t}'] has unknown etf_type '{etf_type}' "
                f"(allowed: {sorted(ALLOWED_ETF_TYPES)})"
            )
        if etf_role not in ALLOWED_ETF_ROLES:
            raise PolicyTickerConfigError(
                f"policy_tickers: etf_classifier_map['{t}'] has unknown etf_role '{etf_role}' "
                f"(allowed: {sorted(ALLOWED_ETF_ROLES)})"
            )
        cmap[t] = (etf_type, etf_role)
    cfg["etf_classifier_map"] = cmap

    return cfg


def _load() -> dict[str, Any]:
    global _cache, _cache_path
    path = os.getenv("POLICY_TICKERS_FILE") or str(_DEFAULT_PATH)
    with _lock:
        if _cache is not None and _cache_path == path:
            return _cache
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError as exc:
            raise PolicyTickerConfigError(
                f"policy_tickers: configuration file not found at {path} "
                "(set POLICY_TICKERS_FILE or restore app/policy_tickers.json)"
            ) from exc
        except json.JSONDecodeError as exc:
            raise PolicyTickerConfigError(f"policy_tickers: {path} is not valid JSON: {exc}") from exc
        cfg = _validate(raw, path)
        _cache = cfg
        _cache_path = path
        return cfg


def reset_cache() -> None:
    """Test hook: force a re-read (e.g. after changing POLICY_TICKERS_FILE)."""
    global _cache, _cache_path
    with _lock:
        _cache = None
        _cache_path = None


def default_config_path() -> Path:
    """The packaged default config path (for deployment-resolution proof)."""
    return _DEFAULT_PATH


def broad_index_core_preference_order() -> tuple[str, ...]:
    """Ordered core-ETF preference (first = most preferred)."""
    return tuple(_load()["broad_index_core_preference_order"])


def etf_group_tickers(group: str) -> frozenset[str]:
    groups = _load()["etf_groups"]
    if group not in groups:
        raise PolicyTickerConfigError(f"policy_tickers: unknown ETF group '{group}'")
    return frozenset(groups[group])


def alternatives_tickers() -> frozenset[str]:
    return frozenset(_load()["alternatives_tickers"])


def crypto_tickers() -> frozenset[str]:
    return frozenset(_load()["crypto_tickers"])


def speculative_tickers() -> frozenset[str]:
    return frozenset(_load()["speculative_tickers"])


def kernel_crypto_tickers() -> frozenset[str]:
    return frozenset(_load()["kernel_crypto_tickers"])


def etf_classifier_map() -> dict[str, tuple[str, str]]:
    return dict(_load()["etf_classifier_map"])
