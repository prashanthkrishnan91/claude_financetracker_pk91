"""Stage 5I — FRED (Federal Reserve Economic Data) official macro provider v1.

Synchronous, typed, deterministic HTTP client for the FRED public JSON API.

Endpoints used:
  - https://api.stlouisfed.org/fred/series                series metadata
  - https://api.stlouisfed.org/fred/series/observations   recent observations

Hard constraints enforced by this module:
  - Requires FRED_API_KEY (from Settings.fred_api_key). No anonymous calls.
  - All HTTP calls bounded by timeout_seconds.
  - Total requests per session capped at max_requests_per_session.
  - Per-series fetch returns at most one metadata + one observations response.
  - Recent observations only — pulls the most recent observation_limit values
    (default 12). No full-history pulls.
  - Fail-closed on any error, timeout, rate-limit, or malformed response.
    Always returns FredProviderResult. Never raises.
  - Never fabricates data — returns only what the FRED API provides.
  - Deterministic and testable: inject http_get_fn to avoid real HTTP calls.
  - Never runs outside explicit worker invocation (not on page load).

Dependency: httpx (already in requirements.txt). Only imported when http_get_fn
is None — i.e., not imported in test paths that supply their own fake.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Stage 5I.1 — API-key log redaction ────────────────────────────────────────
# FRED requires api_key as a query parameter (no header-auth alternative). httpx
# (and httpcore) log every request URL at INFO level, which leaked the live
# FRED_API_KEY into Railway logs. We install a logging filter that scrubs the
# key from any log record on the httpx / httpcore loggers, and additionally
# raise their level to WARNING so the request URL line is suppressed entirely.
# High-level FRED progress logs (series_id, observation_count, latest_date) are
# emitted by the runner via this module's own logger and are unaffected.

_API_KEY_QUERY_RE = re.compile(r"(api_key=)[^&\s\"']+", re.IGNORECASE)


class _ApiKeyRedactingFilter(logging.Filter):
    """Scrub api_key query values from log messages and args."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        try:
            if isinstance(record.msg, str) and "api_key=" in record.msg.lower():
                record.msg = _API_KEY_QUERY_RE.sub(r"\1[REDACTED]", record.msg)
            if record.args:
                if isinstance(record.args, tuple):
                    record.args = tuple(
                        _API_KEY_QUERY_RE.sub(r"\1[REDACTED]", a)
                        if isinstance(a, str) else a
                        for a in record.args
                    )
                elif isinstance(record.args, dict):
                    record.args = {
                        k: (_API_KEY_QUERY_RE.sub(r"\1[REDACTED]", v)
                            if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
        except Exception:  # noqa: BLE001 — never break logging
            pass
        return True


_REDACTING_FILTER = _ApiKeyRedactingFilter()


def _install_api_key_log_redaction() -> None:
    """Idempotently install api_key redaction on httpx/httpcore + this module's logger."""
    for name in ("httpx", "httpcore", __name__):
        lg = logging.getLogger(name)
        if not any(isinstance(f, _ApiKeyRedactingFilter) for f in lg.filters):
            lg.addFilter(_REDACTING_FILTER)
    # httpx logs request URLs at INFO. Suppress that line entirely; the runner
    # already emits structured per-series logs that do not include the key.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


_install_api_key_log_redaction()


_SERIES_URL = "https://api.stlouisfed.org/fred/series"
_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
_DEFAULT_TIMEOUT_SECONDS: float = 10.0
_DEFAULT_MAX_REQUESTS_PER_SESSION: int = 32
_DEFAULT_OBSERVATION_LIMIT: int = 12

# Stage 5I — allowlisted macro series. Bounded set; do not expand without review.
# Each entry: series_id → (display_label, lane_category).
ALLOWED_MACRO_SERIES: dict[str, tuple[str, str]] = {
    "FEDFUNDS": ("Federal Funds Effective Rate (monthly)", "policy_rate"),
    "DFF": ("Federal Funds Rate (daily)", "policy_rate"),
    "DGS10": ("10-Year Treasury Constant Maturity Rate", "treasury_yield"),
    "DGS2": ("2-Year Treasury Constant Maturity Rate", "treasury_yield"),
    "T10Y2Y": ("10-Year minus 2-Year Treasury Yield Spread", "yield_curve"),
    "CPIAUCSL": ("Consumer Price Index for All Urban Consumers", "inflation"),
    "UNRATE": ("Civilian Unemployment Rate", "labor_market"),
    "PAYEMS": ("All Employees: Total Nonfarm Payrolls", "labor_market"),
    "GDP": ("Gross Domestic Product (nominal)", "growth"),
    "GDPC1": ("Real Gross Domestic Product", "growth"),
}


@dataclass
class FredSeriesMetadata:
    """Metadata for one FRED series, as returned by /fred/series."""

    series_id: str
    title: Optional[str] = None
    units: Optional[str] = None
    units_short: Optional[str] = None
    frequency: Optional[str] = None
    frequency_short: Optional[str] = None
    seasonal_adjustment: Optional[str] = None
    seasonal_adjustment_short: Optional[str] = None
    last_updated: Optional[str] = None
    observation_start: Optional[str] = None
    observation_end: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class FredObservation:
    """One observation (date, value) from /fred/series/observations.

    value may be None when FRED returns "." (missing observation).
    realtime_start/end reflect the FRED vintage window for this observation.
    """

    date: str                       # YYYY-MM-DD
    value: Optional[float] = None
    realtime_start: Optional[str] = None
    realtime_end: Optional[str] = None


@dataclass
class FredSeriesFetchResult:
    """Per-series outcome inside a FredProviderResult.

    fetch_status values:
      success         — both metadata + observations successfully retrieved.
      partial         — metadata or observations missing/error, but the other succeeded.
      skipped         — series not in allowlist or skipped due to request cap.
      timeout         — HTTP timeout on metadata or observations call.
      rate_limited    — HTTP 429 received from FRED.
      malformed       — JSON parse / unexpected structure.
      error           — any other exception.
      no_observations — both calls returned no usable values.
    """

    series_id: str
    category: Optional[str] = None
    fetch_status: str = "unknown"
    metadata: Optional[FredSeriesMetadata] = None
    observations: list[FredObservation] = field(default_factory=list)
    error_message: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.fetch_status in ("success", "partial") and bool(self.observations)


@dataclass
class FredProviderConfig:
    """Immutable config for one FRED fetch session.

    All values must come from Settings — never from user input at runtime.
    """

    api_key: str
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_requests_per_session: int = _DEFAULT_MAX_REQUESTS_PER_SESSION
    observation_limit: int = _DEFAULT_OBSERVATION_LIMIT


@dataclass
class FredProviderResult:
    """Aggregate result of a FRED macro fetch attempt for the allowlisted series.

    Top-level fetch_status:
      success        — at least one series produced usable observations.
      no_api_key     — api_key not configured; no HTTP call was made.
      rate_limited   — first request returned HTTP 429.
      error          — global error before any series could be fetched.
      no_observations — every series returned without usable values.
    """

    fetch_status: str = "unknown"
    error_message: Optional[str] = None
    fetched_at: str = ""
    request_count: int = 0
    series_results: list[FredSeriesFetchResult] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        return self.fetch_status == "success" and any(
            s.is_success for s in self.series_results
        )

    @property
    def successful_series(self) -> list[FredSeriesFetchResult]:
        return [s for s in self.series_results if s.is_success]


# ── Internal helpers ──────────────────────────────────────────────────────────


def _is_timeout_exc(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "timeout" in str(exc).lower()


def _is_rate_limit_exc(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    return resp is not None and getattr(resp, "status_code", 0) == 429


def _parse_value(raw: Any) -> Optional[float]:
    """FRED uses '.' for missing observations. Return None when missing or non-numeric."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s == "." or s.lower() == "nan":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_series_metadata(series_id: str, body: dict[str, Any]) -> Optional[FredSeriesMetadata]:
    """Parse /fred/series JSON body into FredSeriesMetadata."""
    entries = body.get("seriess") or []
    if not entries or not isinstance(entries[0], dict):
        return None
    entry = entries[0]
    return FredSeriesMetadata(
        series_id=str(entry.get("id") or series_id),
        title=entry.get("title"),
        units=entry.get("units"),
        units_short=entry.get("units_short"),
        frequency=entry.get("frequency"),
        frequency_short=entry.get("frequency_short"),
        seasonal_adjustment=entry.get("seasonal_adjustment"),
        seasonal_adjustment_short=entry.get("seasonal_adjustment_short"),
        last_updated=entry.get("last_updated"),
        observation_start=entry.get("observation_start"),
        observation_end=entry.get("observation_end"),
        notes=entry.get("notes"),
    )


def _parse_observations(body: dict[str, Any]) -> list[FredObservation]:
    """Parse /fred/series/observations JSON body into FredObservation list."""
    rows = body.get("observations") or []
    parsed: list[FredObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = str(row.get("date") or "").strip()
        if not date:
            continue
        parsed.append(FredObservation(
            date=date,
            value=_parse_value(row.get("value")),
            realtime_start=(row.get("realtime_start") or None),
            realtime_end=(row.get("realtime_end") or None),
        ))
    return parsed


# ── Public API ────────────────────────────────────────────────────────────────


def fetch_one_series(
    series_id: str,
    config: FredProviderConfig,
    http_get_fn: Callable[[str, dict[str, Any]], Any],
    observation_limit: Optional[int] = None,
) -> FredSeriesFetchResult:
    """Fetch metadata + recent observations for one FRED series.

    Returns FredSeriesFetchResult — always. Never raises.

    Args:
        series_id:        FRED series identifier (e.g. "DGS10").
        config:           Provider configuration with api_key + limits.
        http_get_fn:      Callable(url, params) → response-like object that exposes
                          .raise_for_status() and .json(). Required (caller decides
                          whether to inject a fake or use a real httpx.Client.get).
        observation_limit: Override for the per-series observation limit.

    The function ALWAYS performs at most two HTTP calls (metadata + observations).
    """
    series_upper = series_id.upper().strip()
    label, category = ALLOWED_MACRO_SERIES.get(
        series_upper, (None, None)
    )
    if label is None:
        return FredSeriesFetchResult(
            series_id=series_upper,
            fetch_status="skipped",
            error_message="series not in Stage 5I allowlist",
        )

    obs_limit = observation_limit if observation_limit is not None else config.observation_limit

    # ── Request 1: series metadata ───────────────────────────────────────────
    meta: Optional[FredSeriesMetadata] = None
    meta_err: Optional[str] = None
    try:
        resp = http_get_fn(_SERIES_URL, {
            "series_id": series_upper,
            "api_key": config.api_key,
            "file_type": "json",
        })
        resp.raise_for_status()
        body = resp.json() or {}
        meta = _parse_series_metadata(series_upper, body)
        if meta is None:
            meta_err = "metadata response missing 'seriess' entry"
    except Exception as exc:  # noqa: BLE001
        if _is_timeout_exc(exc):
            return FredSeriesFetchResult(
                series_id=series_upper, category=category,
                fetch_status="timeout",
                error_message=f"Timeout on series metadata: {exc}",
            )
        if _is_rate_limit_exc(exc):
            return FredSeriesFetchResult(
                series_id=series_upper, category=category,
                fetch_status="rate_limited",
                error_message="Rate limited by FRED (metadata).",
            )
        meta_err = f"metadata fetch error: {exc}"

    # ── Request 2: observations ──────────────────────────────────────────────
    obs: list[FredObservation] = []
    obs_err: Optional[str] = None
    try:
        resp2 = http_get_fn(_OBSERVATIONS_URL, {
            "series_id": series_upper,
            "api_key": config.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": obs_limit,
        })
        resp2.raise_for_status()
        body2 = resp2.json() or {}
        obs = _parse_observations(body2)
    except Exception as exc:  # noqa: BLE001
        if _is_timeout_exc(exc):
            return FredSeriesFetchResult(
                series_id=series_upper, category=category, metadata=meta,
                fetch_status="timeout",
                error_message=f"Timeout on observations: {exc}",
            )
        if _is_rate_limit_exc(exc):
            return FredSeriesFetchResult(
                series_id=series_upper, category=category, metadata=meta,
                fetch_status="rate_limited",
                error_message="Rate limited by FRED (observations).",
            )
        obs_err = f"observations fetch error: {exc}"

    # Sort observations by date ascending so the most recent is last (helps
    # downstream display) but preserve actual API ordering for fingerprinting.
    if obs:
        obs = sorted(obs, key=lambda o: o.date)

    if not obs and meta is None:
        return FredSeriesFetchResult(
            series_id=series_upper, category=category,
            fetch_status="malformed" if (meta_err or obs_err) else "no_observations",
            error_message=meta_err or obs_err or "no metadata, no observations",
        )

    if not obs:
        return FredSeriesFetchResult(
            series_id=series_upper, category=category, metadata=meta,
            fetch_status="no_observations",
            error_message=obs_err or "no observations returned",
        )

    status = "success" if meta is not None and not (meta_err or obs_err) else "partial"
    return FredSeriesFetchResult(
        series_id=series_upper,
        category=category,
        fetch_status=status,
        metadata=meta,
        observations=obs,
        error_message=meta_err or obs_err,
    )


def fetch_macro_series(
    series_ids: list[str],
    config: FredProviderConfig,
    http_get_fn: Optional[Callable[[str, dict[str, Any]], Any]] = None,
) -> FredProviderResult:
    """Fetch metadata + recent observations for the requested allowlisted series.

    Returns FredProviderResult — always. Never raises.

    Args:
        series_ids:  List of FRED series ids to fetch (must be in ALLOWED_MACRO_SERIES).
        config:      Provider configuration with api_key and limits.
        http_get_fn: Optional injectable callable(url, params) → response-like object.
                     If None, an httpx.Client is created internally. Pass a fake for tests.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()

    if not config.api_key or not config.api_key.strip():
        return FredProviderResult(
            fetch_status="no_api_key",
            error_message="FRED_API_KEY not configured. No anonymous FRED calls permitted.",
            fetched_at=fetched_at,
        )

    http_client: Any = None
    _own_client = False
    try:
        if http_get_fn is None:
            import httpx  # deferred import; not needed in test paths
            http_client = httpx.Client(timeout=config.timeout_seconds)
            _own_client = True

            def _real_get(url: str, params: dict[str, Any]) -> Any:
                return http_client.get(url, params=params)

            _get: Callable[[str, dict[str, Any]], Any] = _real_get
        else:
            _get = http_get_fn

        request_budget = config.max_requests_per_session
        series_results: list[FredSeriesFetchResult] = []
        request_count = 0

        for sid in series_ids:
            if request_count + 2 > request_budget:
                series_results.append(FredSeriesFetchResult(
                    series_id=sid.upper().strip(),
                    fetch_status="skipped",
                    error_message="request budget exhausted",
                ))
                continue
            try:
                res = fetch_one_series(sid, config, _get)
            except Exception as exc:  # noqa: BLE001 — defence-in-depth
                res = FredSeriesFetchResult(
                    series_id=sid.upper().strip(),
                    fetch_status="error",
                    error_message=f"unexpected error: {exc}",
                )
            series_results.append(res)
            # Best-effort request count — fetch_one_series performs at most 2 calls.
            request_count += 2

            # Short-circuit on global rate-limit: once we see 429 it will keep happening.
            if res.fetch_status == "rate_limited":
                logger.warning(
                    "fred_provider_rate_limited series=%s remaining_count=%d",
                    res.series_id, len(series_ids) - len(series_results),
                )
                break

        successful = [r for r in series_results if r.is_success]
        if not successful:
            return FredProviderResult(
                fetch_status="no_observations",
                error_message="no allowlisted series produced usable observations",
                fetched_at=fetched_at,
                request_count=request_count,
                series_results=series_results,
            )

        return FredProviderResult(
            fetch_status="success",
            fetched_at=fetched_at,
            request_count=request_count,
            series_results=series_results,
        )
    except Exception as exc:  # noqa: BLE001
        return FredProviderResult(
            fetch_status="error",
            error_message=f"global error: {exc}",
            fetched_at=fetched_at,
        )
    finally:
        if _own_client and http_client is not None:
            try:
                http_client.close()
            except Exception:  # noqa: BLE001
                pass
