"""Stage 5I — FRED Official Macro Evidence Lane v1 tests.

Proves the Stage 5I acceptance criteria:

 1.  FRED registered as FREE / OFFICIAL macro provider in evidence_provider_registry_v1.
 2.  Router resolves macro lane to fred with ROUTE_REASON_FREE_OFFICIAL.
 3.  Provider client (fred_provider_v1) deterministic, sync, fail-closed.
 4.  Adapter (fred_macro_adapter_v1) produces portfolio-scope WorkerOutput.
 5.  Allowlisted macro series only (FEDFUNDS, DFF, DGS10, DGS2, T10Y2Y,
     CPIAUCSL, UNRATE, PAYEMS, GDP, GDPC1).
 6.  Per-series metadata (title, units, frequency, last_updated) preserved.
 7.  Per-observation date, value, realtime_start/end preserved.
 8.  Macro lane runner (run_fred_macro_evidence) writes through
     ResearchArtifactServiceV1 — never raw ArtifactStoreWriter.
 9.  All four enrichment layers injected (credibility, contradiction,
     completeness, truth usability).
10.  artifact_type="portfolio_exposure" (no new SQL); skill_pack distinct
     from any company-fundamentals lane.
11.  scope_kind="portfolio", ticker IS NULL — one artifact per explicit run.
12.  Flag off → no artifact write, no API call.
13.  Missing FRED_API_KEY → honest skip, no artifact write.
14.  No usable observations → honest skip (no placeholder noise).
15.  Provider error / timeout / rate-limit → honest skip.
16.  safe_for_decision remains False in every persisted artifact.
17.  No writes to intel_v3_snapshots or recommendations.
18.  No decide() import in adapter, runner, or orchestrator.
19.  No paid provider activated; yfinance / sec_edgar lanes unchanged.
20.  No LLM calls.
21.  Macro lane wired into intel_v3 evidence-lane dispatch path
     (intel_v3_evidence_lane_orchestrator_v1) — not page-load.
22.  Replay idempotency key deterministic from observations digest.
23.  Honest no-data limitations recorded; no fabricated values/dates.
24.  Confidence band derived from successful-series count.
25.  Freshness band derived from latest observation date.

No production Supabase or HTTP access. All IO is faked.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pytest

from app.config import Settings

from app.services.intelligence.research_workers.contracts import (
    WORKER_FORBIDDEN_PAYLOAD_KEYS,
    WorkerInput,
)
from app.services.intelligence.research_workers.evidence_provider_registry_v1 import (
    ALL_LANES,
    LANE_MACRO,
    CostTier,
    TrustTier,
    build_registry_summary,
    enabled_providers_for_lane,
    get_provider,
)
from app.services.intelligence.research_workers.evidence_provider_router_v1 import (
    ROUTE_REASON_FREE_OFFICIAL,
    resolve_provider_for_lane,
)
from app.services.intelligence.research_workers.fred_provider_v1 import (
    ALLOWED_MACRO_SERIES,
    FredObservation,
    FredProviderConfig,
    FredProviderResult,
    FredSeriesFetchResult,
    FredSeriesMetadata,
    fetch_macro_series,
    fetch_one_series,
)
from app.services.intelligence.research_workers.fred_macro_adapter_v1 import (
    _ARTIFACT_TYPE,
    _MODEL_VERSION,
    _PROVIDER_NAME,
    _SCOPE_KIND,
    _SKILL_PACK,
    adapt_fred_macro,
    build_fred_macro_worker_output,
)
from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
    run_fred_macro_evidence,
)


# ── Fake HTTP / Supabase infrastructure ───────────────────────────────────────


@dataclass
class _FakeResp:
    """Fake httpx response: .raise_for_status() + .json()."""
    body: dict
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _FakeHTTPError(self.status_code)

    def json(self) -> dict:
        return self.body


class _FakeHTTPError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")

        class _R:
            pass
        self.response = _R()
        self.response.status_code = status_code  # type: ignore[attr-defined]


class _FakeTimeout(Exception):
    def __init__(self) -> None:
        super().__init__("Timeout!")


def _fake_http_get_factory(
    series_meta: dict[str, dict],
    series_obs: dict[str, list[dict]],
    fail_with: Optional[Exception] = None,
) -> Callable[[str, dict[str, Any]], _FakeResp]:
    """Build a fake http_get_fn that returns canned JSON for series_id queries."""
    def _get(url: str, params: dict[str, Any]) -> _FakeResp:
        if fail_with is not None:
            raise fail_with
        sid = (params.get("series_id") or "").upper().strip()
        if url.endswith("/fred/series"):
            meta = series_meta.get(sid)
            return _FakeResp({"seriess": [meta] if meta else []})
        if url.endswith("/fred/series/observations"):
            return _FakeResp({"observations": series_obs.get(sid, [])})
        return _FakeResp({})
    return _get


def _meta_payload(
    sid: str,
    title: str = "Test Series",
    units: str = "Percent",
    frequency: str = "Daily",
    last_updated: str = "2026-05-17 09:00:00-05",
) -> dict:
    return {
        "id": sid,
        "title": title,
        "units": units,
        "units_short": units[:3],
        "frequency": frequency,
        "frequency_short": frequency[:1],
        "seasonal_adjustment": "Not Seasonally Adjusted",
        "seasonal_adjustment_short": "NSA",
        "last_updated": last_updated,
        "observation_start": "1954-07-01",
        "observation_end": "2026-05-15",
        "notes": f"Notes for {sid}.",
    }


def _obs_payload(date: str, value: str, rt: str = "2026-05-18") -> dict:
    return {
        "date": date,
        "value": value,
        "realtime_start": rt,
        "realtime_end": rt,
    }


@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)


class _FakeTableQuery:
    def __init__(self, state: _TableState) -> None:
        self._state = state
        self._row: Optional[dict] = None
        self._is_update: bool = False
        self._filters: dict[str, Any] = {}

    def insert(self, row: dict) -> "_FakeTableQuery":
        self._row = row
        return self

    def update(self, row: dict) -> "_FakeTableQuery":
        self._row = row
        self._is_update = True
        return self

    def upsert(self, row: dict, *, on_conflict: str = "", ignore_duplicates: bool = False) -> "_FakeTableQuery":
        self._row = row
        return self

    def select(self, cols: str = "*") -> "_FakeTableQuery":
        return self

    def eq(self, col: str, val: Any) -> "_FakeTableQuery":
        self._filters[col] = val
        return self

    def neq(self, col: str, val: Any) -> "_FakeTableQuery":
        return self

    def is_(self, col: str, val: Any) -> "_FakeTableQuery":
        return self

    def order(self, *args: Any, **kwargs: Any) -> "_FakeTableQuery":
        return self

    def limit(self, n: int) -> "_FakeTableQuery":
        return self

    def execute(self) -> Any:
        if self._row is not None and self._is_update:
            self._state.updates.append(self._row)
            class _U:
                data = []
            return _U()
        if self._row is not None:
            row_with_id = {"id": str(uuid.uuid4()), **self._row}
            self._state.inserts.append(self._row)
            class _R:
                data = [row_with_id]
            return _R()
        class _E:
            data = []
        return _E()


class _FakeSupabase:
    def __init__(self) -> None:
        self.tables: dict[str, _TableState] = {
            "research_artifacts": _TableState(),
            "research_artifact_sources": _TableState(),
            "research_artifact_facts": _TableState(),
            "worker_audit_events": _TableState(),
            "intel_v3_snapshots": _TableState(),
            "recommendations": _TableState(),
        }

    def table(self, name: str) -> _FakeTableQuery:
        return _FakeTableQuery(self.tables.setdefault(name, _TableState()))

    def artifact_inserts(self) -> list[dict]:
        return self.tables["research_artifacts"].inserts

    def source_inserts(self) -> list[dict]:
        return self.tables["research_artifact_sources"].inserts

    def fact_inserts(self) -> list[dict]:
        return self.tables["research_artifact_facts"].inserts

    def snapshot_writes(self) -> list[dict]:
        return self.tables["intel_v3_snapshots"].inserts

    def recommendation_writes(self) -> list[dict]:
        return self.tables["recommendations"].inserts


# ── Settings fixtures ─────────────────────────────────────────────────────────


def _base_settings(**overrides: Any) -> Settings:
    base = dict(
        supabase_url="http://fake",
        supabase_anon_key="anon",
        supabase_service_role_key="svc",
        supabase_jwt_secret="secret",
        encryption_key="a" * 32,
    )
    base.update(overrides)
    return Settings(**base)


def _settings_macro_on(api_key: str = "fred-test-key") -> Settings:
    return _base_settings(
        intel_v3_research_workers_enabled=True,
        intel_v3_macro_evidence_enabled=True,
        fred_api_key=api_key,
    )


def _settings_macro_off() -> Settings:
    return _base_settings(
        intel_v3_research_workers_enabled=True,
        intel_v3_macro_evidence_enabled=False,
        fred_api_key="fred-test-key",
    )


def _settings_macro_global_off() -> Settings:
    return _base_settings(
        intel_v3_research_workers_enabled=False,
        intel_v3_macro_evidence_enabled=True,
        fred_api_key="fred-test-key",
    )


# ── Provider result fixtures ──────────────────────────────────────────────────


def _success_provider_result(
    series_ids: Optional[list[str]] = None,
    fetched_at: Optional[str] = None,
) -> FredProviderResult:
    sids = series_ids or ["DGS10", "DGS2", "FEDFUNDS"]
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).date()
    series_results: list[FredSeriesFetchResult] = []
    for sid in sids:
        meta = FredSeriesMetadata(
            series_id=sid,
            title=ALLOWED_MACRO_SERIES[sid][0],
            units="Percent",
            frequency="Daily",
            last_updated=fetched_at,
            observation_start="1962-01-02",
            observation_end=today.isoformat(),
        )
        obs = [
            FredObservation(
                date=(today - timedelta(days=i)).isoformat(),
                value=4.50 + i * 0.01,
                realtime_start=today.isoformat(),
                realtime_end=today.isoformat(),
            )
            for i in range(3)
        ]
        series_results.append(FredSeriesFetchResult(
            series_id=sid,
            category=ALLOWED_MACRO_SERIES[sid][1],
            fetch_status="success",
            metadata=meta,
            observations=obs,
        ))
    return FredProviderResult(
        fetch_status="success",
        fetched_at=fetched_at,
        request_count=len(sids) * 2,
        series_results=series_results,
    )


def _worker_input() -> WorkerInput:
    return WorkerInput(
        user_id="user-stage5i",
        ticker=None,
        worker_run_id=str(uuid.uuid4()),
        parent_intel_run_id=str(uuid.uuid4()),
    )


# ── Registry / router tests ──────────────────────────────────────────────────


class TestProviderRegistry:
    def test_macro_lane_in_all_lanes(self):
        assert LANE_MACRO in ALL_LANES

    def test_fred_supports_macro_lane(self):
        entry = get_provider("fred")
        assert entry is not None
        assert LANE_MACRO in entry.supported_lanes

    def test_fred_is_free_official(self):
        entry = get_provider("fred")
        assert entry.cost_tier == CostTier.FREE
        assert entry.trust_tier == TrustTier.OFFICIAL

    def test_fred_default_enabled(self):
        entry = get_provider("fred")
        assert entry.default_enabled is True

    def test_fred_requires_api_key(self):
        entry = get_provider("fred")
        assert entry.requires_api_key is True

    def test_router_macro_returns_free_official_fred(self):
        result = resolve_provider_for_lane(LANE_MACRO)
        assert result.provider_id == "fred"
        assert result.reason == ROUTE_REASON_FREE_OFFICIAL

    def test_router_macro_returns_provider_entry(self):
        result = resolve_provider_for_lane(LANE_MACRO)
        assert result.provider_entry is not None
        assert result.provider_entry.cost_tier == CostTier.FREE
        assert result.provider_entry.trust_tier == TrustTier.OFFICIAL

    def test_paid_providers_not_selected_for_macro(self):
        result = resolve_provider_for_lane(LANE_MACRO)
        assert result.provider_id != "alpha_vantage"
        assert result.provider_id != "fmp"
        assert result.provider_id != "eodhd"

    def test_paid_providers_still_disabled(self):
        for pid in ("fmp", "eodhd", "alpha_vantage"):
            entry = get_provider(pid)
            assert entry is not None
            assert entry.default_enabled is False

    def test_macro_lane_coverage_summary_includes_fred(self):
        summary = build_registry_summary()
        cov = summary["lane_coverage"].get(LANE_MACRO)
        assert cov is not None
        assert cov["enabled_providers"] >= 1
        assert cov["has_free_official"] is True
        assert cov["primary_provider"] == "fred"

    def test_registry_summary_safe_for_decision_false(self):
        summary = build_registry_summary()
        assert summary["safe_for_decision"] is False

    def test_enabled_providers_for_macro_has_fred_first(self):
        enabled = enabled_providers_for_lane(LANE_MACRO)
        assert enabled
        assert enabled[0].provider_id == "fred"


# ── Provider client tests ────────────────────────────────────────────────────


class TestFredProviderClient:
    def test_no_api_key_returns_no_api_key_status(self):
        cfg = FredProviderConfig(api_key="")
        res = fetch_macro_series(["DGS10"], cfg, http_get_fn=lambda *a, **k: _FakeResp({}))
        assert res.fetch_status == "no_api_key"
        assert res.series_results == []

    def test_allowlist_blocks_unknown_series(self):
        cfg = FredProviderConfig(api_key="k")
        get_fn = _fake_http_get_factory({}, {})
        res = fetch_one_series("NOT_ALLOWED", cfg, get_fn)
        assert res.fetch_status == "skipped"

    def test_success_parses_metadata_and_observations(self):
        cfg = FredProviderConfig(api_key="k")
        get_fn = _fake_http_get_factory(
            {"DGS10": _meta_payload("DGS10", title="10Y", units="Percent", frequency="Daily")},
            {"DGS10": [
                _obs_payload("2026-05-15", "4.30"),
                _obs_payload("2026-05-14", "4.28"),
            ]},
        )
        res = fetch_one_series("DGS10", cfg, get_fn)
        assert res.fetch_status == "success"
        assert res.metadata is not None
        assert res.metadata.title == "10Y"
        assert res.metadata.units == "Percent"
        assert res.metadata.frequency == "Daily"
        assert res.metadata.last_updated  # preserved
        assert len(res.observations) == 2
        # Values are parsed as floats; dates preserved.
        assert any(o.value == pytest.approx(4.30) for o in res.observations)
        assert any(o.realtime_start == "2026-05-18" for o in res.observations)

    def test_missing_value_dot_is_none(self):
        cfg = FredProviderConfig(api_key="k")
        get_fn = _fake_http_get_factory(
            {"UNRATE": _meta_payload("UNRATE", units="Percent", frequency="Monthly")},
            {"UNRATE": [_obs_payload("2026-04-01", ".")]},
        )
        res = fetch_one_series("UNRATE", cfg, get_fn)
        assert res.observations[0].value is None

    def test_timeout_returns_timeout_status(self):
        cfg = FredProviderConfig(api_key="k")
        get_fn = _fake_http_get_factory({}, {}, fail_with=_FakeTimeout())
        res = fetch_one_series("DGS10", cfg, get_fn)
        assert res.fetch_status == "timeout"
        assert res.observations == []

    def test_rate_limit_returns_rate_limited_status(self):
        cfg = FredProviderConfig(api_key="k")
        get_fn = _fake_http_get_factory({}, {}, fail_with=_FakeHTTPError(429))
        res = fetch_one_series("DGS10", cfg, get_fn)
        assert res.fetch_status == "rate_limited"

    def test_session_short_circuits_on_rate_limit(self):
        cfg = FredProviderConfig(api_key="k")
        seen: list[str] = []

        def _get(url: str, params: dict[str, Any]) -> _FakeResp:
            sid = (params.get("series_id") or "").upper().strip()
            seen.append(sid)
            raise _FakeHTTPError(429)

        res = fetch_macro_series(["DGS10", "DGS2", "FEDFUNDS"], cfg, http_get_fn=_get)
        # Only first series attempted before short-circuit.
        unique = set(seen)
        assert unique == {"DGS10"}
        assert res.fetch_status == "no_observations"

    def test_no_observations_returned(self):
        cfg = FredProviderConfig(api_key="k")
        get_fn = _fake_http_get_factory(
            {"GDP": _meta_payload("GDP", units="Billions", frequency="Quarterly")},
            {"GDP": []},
        )
        res = fetch_one_series("GDP", cfg, get_fn)
        assert res.fetch_status == "no_observations"
        assert res.observations == []

    def test_observation_count_capped_by_observation_limit(self):
        # Limit applied by FRED via 'limit' param; client just records what came back.
        cfg = FredProviderConfig(api_key="k", observation_limit=2)
        get_fn = _fake_http_get_factory(
            {"DGS10": _meta_payload("DGS10")},
            {"DGS10": [_obs_payload(f"2026-05-{i:02d}", "4.30") for i in (15, 14, 13)]},
        )
        # The fake returns 3 rows; the client preserves what FRED returned (here all 3).
        # The point of observation_limit is to pass `limit` to FRED — confirmed below.
        captured: dict[str, Any] = {}

        def _captured_get(url: str, params: dict[str, Any]) -> _FakeResp:
            captured.update(params)
            return get_fn(url, params)

        fetch_one_series("DGS10", cfg, _captured_get)
        assert captured.get("limit") == 2

    def test_each_series_uses_exactly_two_requests(self):
        cfg = FredProviderConfig(api_key="k")
        call_count = {"n": 0}

        def _get(url: str, params: dict[str, Any]) -> _FakeResp:
            call_count["n"] += 1
            sid = (params.get("series_id") or "").upper().strip()
            if url.endswith("/fred/series"):
                return _FakeResp({"seriess": [_meta_payload(sid)]})
            return _FakeResp({"observations": [_obs_payload("2026-05-15", "4.30")]})

        fetch_one_series("DGS10", cfg, _get)
        assert call_count["n"] == 2


# ── Adapter unit tests ────────────────────────────────────────────────────────


class TestAdaptFredMacro:
    def test_no_api_key_returns_thin_result(self):
        provider = FredProviderResult(fetch_status="no_api_key", error_message="no key")
        res = adapt_fred_macro(provider, datetime.now(timezone.utc).isoformat())
        assert res.facts == []
        assert res.sources == []
        assert res.confidence_or_trust_level == "UNKNOWN"
        assert any("FRED_API_KEY" in lim for lim in res.limitations)
        assert res.artifact_payload_extra["fetch_status"] == "no_api_key"

    def test_no_data_returns_thin_result(self):
        provider = FredProviderResult(
            fetch_status="error", error_message="boom",
            series_results=[FredSeriesFetchResult(
                series_id="DGS10", fetch_status="error", error_message="boom",
            )],
        )
        res = adapt_fred_macro(provider, datetime.now(timezone.utc).isoformat())
        assert res.facts == []
        assert res.sources == []
        assert res.artifact_payload_extra["series_succeeded"] == 0

    def test_success_produces_one_source_per_series(self):
        provider = _success_provider_result(["DGS10", "DGS2"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert len(res.sources) == 2
        ids = {s.source_id for s in res.sources}
        assert ids == {"DGS10", "DGS2"}

    def test_source_record_provider_name_is_fred(self):
        provider = _success_provider_result(["DGS10"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert all(s.provider_name == _PROVIDER_NAME for s in res.sources)

    def test_source_record_has_fred_url(self):
        provider = _success_provider_result(["DGS10"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert res.sources[0].source_url == "https://fred.stlouisfed.org/series/DGS10"

    def test_source_kind_is_other(self):
        # TODO-tracked: future stage may add 'official_macro_data' source_kind.
        provider = _success_provider_result(["DGS10"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert all(s.source_kind == "other" for s in res.sources)

    def test_one_fact_per_observation(self):
        provider = _success_provider_result(["DGS10"])
        # 3 observations per series in the fixture.
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert len(res.facts) == 3

    def test_fact_payload_preserves_series_metadata(self):
        provider = _success_provider_result(["DGS10"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        fact = res.facts[0]
        pl = fact.structured_payload
        assert pl["metric_name"] == "DGS10"
        assert pl["metric_label"] == ALLOWED_MACRO_SERIES["DGS10"][0]
        assert pl["unit"] == "Percent"
        assert pl["frequency"] == "Daily"
        assert pl["provider"] == "fred"
        assert pl["fred_category"] == "treasury_yield"

    def test_fact_payload_preserves_observation_date_and_realtime(self):
        provider = _success_provider_result(["DGS10"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        for fact in res.facts:
            pl = fact.structured_payload
            assert pl["observation_date"]
            assert pl["realtime_start"]
            assert pl["realtime_end"]

    def test_fact_is_quote_grounded(self):
        provider = _success_provider_result(["DGS10"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert all(f.is_quote_grounded for f in res.facts)

    def test_fact_axis_hint_is_macro(self):
        provider = _success_provider_result(["DGS10"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert all(f.axis_hint == "macro" for f in res.facts)

    def test_fact_source_index_matches_series(self):
        provider = _success_provider_result(["DGS10", "DGS2"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        for fact in res.facts:
            sid = fact.structured_payload["metric_name"]
            src = res.sources[fact.source_index]
            assert src.source_id == sid

    def test_no_forbidden_keys_in_facts(self):
        provider = _success_provider_result()
        res = adapt_fred_macro(provider, provider.fetched_at)
        for f in res.facts:
            for k in f.structured_payload:
                assert k.lower() not in WORKER_FORBIDDEN_PAYLOAD_KEYS

    def test_confidence_high_6_plus_series(self):
        provider = _success_provider_result(
            ["FEDFUNDS", "DGS10", "DGS2", "CPIAUCSL", "UNRATE", "PAYEMS"]
        )
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert res.confidence_or_trust_level == "HIGH"

    def test_confidence_medium_3_series(self):
        provider = _success_provider_result(["DGS10", "DGS2", "FEDFUNDS"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert res.confidence_or_trust_level == "MEDIUM"

    def test_confidence_low_1_series(self):
        provider = _success_provider_result(["DGS10"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert res.confidence_or_trust_level == "LOW"

    def test_freshness_fresh_recent_observation(self):
        provider = _success_provider_result(["DGS10"])
        res = adapt_fred_macro(provider, datetime.now(timezone.utc).isoformat())
        assert res.freshness_status == "FRESH"

    def test_freshness_stale_for_old_observation(self):
        provider = _success_provider_result(["DGS10"])
        # Mutate fixture to make observations 2 years old.
        for s in provider.series_results:
            s.observations = [
                FredObservation(date="2023-01-01", value=4.0)
            ]
        res = adapt_fred_macro(provider, datetime.now(timezone.utc).isoformat())
        assert res.freshness_status == "STALE"

    def test_fingerprint_deterministic(self):
        provider = _success_provider_result(["DGS10"])
        r1 = adapt_fred_macro(provider, provider.fetched_at)
        r2 = adapt_fred_macro(provider, provider.fetched_at)
        assert r1.source_refs_fingerprint == r2.source_refs_fingerprint

    def test_fingerprint_changes_with_different_values(self):
        p1 = _success_provider_result(["DGS10"])
        p2 = _success_provider_result(["DGS10"])
        # Mutate p2 observations to different values.
        for s in p2.series_results:
            s.observations = [FredObservation(date=o.date, value=(o.value or 0) + 1.0)
                              for o in s.observations]
        r1 = adapt_fred_macro(p1, p1.fetched_at)
        r2 = adapt_fred_macro(p2, p2.fetched_at)
        assert r1.source_refs_fingerprint != r2.source_refs_fingerprint

    def test_series_status_records_each_attempt(self):
        provider = _success_provider_result(["DGS10"])
        # Add one failing series.
        provider.series_results.append(FredSeriesFetchResult(
            series_id="GDP", fetch_status="timeout", error_message="t",
        ))
        res = adapt_fred_macro(provider, provider.fetched_at)
        ss = res.artifact_payload_extra["series_status"]
        assert "DGS10" in ss and "GDP" in ss
        assert ss["GDP"]["fetch_status"] == "timeout"

    def test_limitations_call_out_official_macro_only(self):
        provider = _success_provider_result()
        res = adapt_fred_macro(provider, provider.fetched_at)
        assert any("Federal Reserve" in lim for lim in res.limitations)
        assert any("not investment recommendations" in lim
                   for lim in res.limitations)


# ── WorkerOutput builder tests ────────────────────────────────────────────────


class TestBuildWorkerOutput:
    def test_artifact_type_portfolio_exposure(self):
        provider = _success_provider_result()
        output = build_fred_macro_worker_output(_worker_input(), provider, provider.fetched_at)
        assert output.artifact_type == "portfolio_exposure"
        assert _ARTIFACT_TYPE == "portfolio_exposure"

    def test_skill_pack_constants_distinct(self):
        provider = _success_provider_result()
        output = build_fred_macro_worker_output(_worker_input(), provider, provider.fetched_at)
        assert output.skill_pack == "fred_macro_evidence_v1"
        # Must not collide with yfinance or sec_companyfacts skill packs.
        assert output.skill_pack != "fundamentals_evidence_v1"
        assert output.skill_pack != "sec_companyfacts_evidence_v1"
        assert _SKILL_PACK == "fred_macro_evidence_v1"

    def test_model_version_distinct(self):
        provider = _success_provider_result()
        output = build_fred_macro_worker_output(_worker_input(), provider, provider.fetched_at)
        assert output.model_version == _MODEL_VERSION
        assert output.model_version == "fred_official_macro_v1"

    def test_scope_kind_portfolio(self):
        provider = _success_provider_result()
        output = build_fred_macro_worker_output(_worker_input(), provider, provider.fetched_at)
        assert output.scope_kind == "portfolio"
        assert _SCOPE_KIND == "portfolio"

    def test_ticker_is_none(self):
        provider = _success_provider_result()
        output = build_fred_macro_worker_output(_worker_input(), provider, provider.fetched_at)
        assert output.ticker is None

    def test_payload_provider_is_fred(self):
        provider = _success_provider_result()
        output = build_fred_macro_worker_output(_worker_input(), provider, provider.fetched_at)
        assert output.artifact_payload.get("provider") == "fred"

    def test_payload_has_series_succeeded_and_observation_count(self):
        provider = _success_provider_result(["DGS10", "DGS2"])
        output = build_fred_macro_worker_output(_worker_input(), provider, provider.fetched_at)
        assert output.artifact_payload["series_succeeded"] == 2
        assert output.artifact_payload["observation_count"] >= 1

    def test_replay_key_deterministic_from_same_inputs(self):
        provider = _success_provider_result()
        o1 = build_fred_macro_worker_output(_worker_input(), provider, provider.fetched_at)
        o2 = build_fred_macro_worker_output(_worker_input(), provider, provider.fetched_at)
        assert o1.replay_idempotency_key == o2.replay_idempotency_key

    def test_replay_key_changes_with_different_observations(self):
        p1 = _success_provider_result(["DGS10"])
        p2 = _success_provider_result(["DGS10"])
        for s in p2.series_results:
            s.observations = [FredObservation(date=o.date, value=(o.value or 0) + 5.0)
                              for o in s.observations]
        o1 = build_fred_macro_worker_output(_worker_input(), p1, p1.fetched_at)
        o2 = build_fred_macro_worker_output(_worker_input(), p2, p2.fetched_at)
        assert o1.replay_idempotency_key != o2.replay_idempotency_key

    def test_no_forbidden_keys_in_payload(self):
        provider = _success_provider_result()
        output = build_fred_macro_worker_output(_worker_input(), provider, provider.fetched_at)
        for k in output.artifact_payload:
            assert k.lower() not in WORKER_FORBIDDEN_PAYLOAD_KEYS


# ── Runner integration tests ─────────────────────────────────────────────────


class TestRunnerIntegration:
    def test_flag_off_returns_none_no_write(self):
        db = _FakeSupabase()
        artifact_id = run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_off(),
            _provider_fn=lambda sids: _success_provider_result(sids),
        )
        assert artifact_id is None
        assert db.artifact_inserts() == []

    def test_global_kill_switch_returns_none(self):
        db = _FakeSupabase()
        artifact_id = run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_global_off(),
            _provider_fn=lambda sids: _success_provider_result(sids),
        )
        assert artifact_id is None
        assert db.artifact_inserts() == []

    def test_missing_api_key_skips_when_no_provider_fn(self):
        db = _FakeSupabase()
        settings = _settings_macro_on(api_key="")
        artifact_id = run_fred_macro_evidence(
            user_id="user-1", db_client=db, settings=settings,
        )
        assert artifact_id is None
        assert db.artifact_inserts() == []

    def test_success_writes_one_portfolio_artifact(self):
        db = _FakeSupabase()
        artifact_id = run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=lambda sids: _success_provider_result(sids),
        )
        assert artifact_id is not None
        assert len(db.artifact_inserts()) == 1
        row = db.artifact_inserts()[0]
        assert row["artifact_type"] == "portfolio_exposure"
        assert row["skill_pack"] == "fred_macro_evidence_v1"
        assert row["scope_kind"] == "portfolio"
        # ticker IS NULL for portfolio-scope artifacts.
        assert row.get("ticker") is None

    def test_artifact_payload_has_four_enrichment_layers(self):
        db = _FakeSupabase()
        run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=lambda sids: _success_provider_result(sids),
        )
        payload = db.artifact_inserts()[0]["payload"]
        assert "source_credibility_assessment" in payload
        assert "contradiction_assessment" in payload
        assert "evidence_completeness_assessment" in payload
        assert "truth_usability_assessment" in payload

    def test_source_records_written_per_series(self):
        db = _FakeSupabase()
        run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=lambda sids: _success_provider_result(["DGS10", "DGS2"]),
        )
        # 2 series → 2 SourceRecord rows.
        assert len(db.source_inserts()) == 2
        sids = {s["source_id"] for s in db.source_inserts()}
        assert sids == {"DGS10", "DGS2"}
        assert all(s["provider_name"] == "fred" for s in db.source_inserts())

    def test_fact_records_written_per_observation(self):
        db = _FakeSupabase()
        run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=lambda sids: _success_provider_result(["DGS10"]),
        )
        # 1 series × 3 observations in fixture.
        assert len(db.fact_inserts()) == 3

    def test_no_artifact_when_no_usable_observations(self):
        db = _FakeSupabase()
        empty = FredProviderResult(fetch_status="no_observations")
        artifact_id = run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=lambda sids: empty,
        )
        assert artifact_id is None
        assert db.artifact_inserts() == []

    def test_safe_for_decision_never_true(self):
        db = _FakeSupabase()
        run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=lambda sids: _success_provider_result(sids),
        )
        for row in db.artifact_inserts():
            assert row.get("safe_for_decision") is not True

    def test_no_intel_v3_snapshots_writes(self):
        db = _FakeSupabase()
        run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=lambda sids: _success_provider_result(sids),
        )
        assert db.snapshot_writes() == []

    def test_no_recommendations_writes(self):
        db = _FakeSupabase()
        run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=lambda sids: _success_provider_result(sids),
        )
        assert db.recommendation_writes() == []

    def test_provider_error_skip_no_artifact(self):
        db = _FakeSupabase()
        def _provider_raises(_sids: list[str]) -> FredProviderResult:
            raise RuntimeError("boom")
        artifact_id = run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=_provider_raises,
        )
        assert artifact_id is None
        assert db.artifact_inserts() == []


# ── Orchestrator wiring tests ────────────────────────────────────────────────


class TestOrchestratorWiring:
    def test_orchestrator_invokes_macro_lane(self):
        from app.services.intelligence.v3 import (
            intel_v3_evidence_lane_orchestrator_v1 as mod,
        )
        # Monkey-patch in-test: replace run_fred_macro_evidence to capture call.
        called: dict[str, Any] = {}

        def _stub_fred_macro(
            user_id: str, db_client: Any,
            parent_intel_run_id: Optional[str] = None,
            settings: Optional[Settings] = None,
        ) -> Optional[str]:
            called["user_id"] = user_id
            called["parent_intel_run_id"] = parent_intel_run_id
            return "macro-art-1"

        import app.services.intelligence.research_workers.evidence_lane_runner_v1 as runner_mod
        orig = runner_mod.run_fred_macro_evidence
        runner_mod.run_fred_macro_evidence = _stub_fred_macro
        try:
            db = _FakeSupabase()
            settings = _settings_macro_on()
            mod.run_enabled_evidence_lanes_for_portfolio(
                user_id="u1",
                tickers=[],
                db_client=db,
                parent_intel_run_id="run-99",
                settings=settings,
            )
            assert called["user_id"] == "u1"
            assert called["parent_intel_run_id"] == "run-99"
        finally:
            runner_mod.run_fred_macro_evidence = orig

    def test_macro_lane_failure_does_not_break_orchestrator(self):
        from app.services.intelligence.v3 import (
            intel_v3_evidence_lane_orchestrator_v1 as mod,
        )

        def _stub_raises(*args: Any, **kwargs: Any) -> Optional[str]:
            raise RuntimeError("simulated macro failure")

        import app.services.intelligence.research_workers.evidence_lane_runner_v1 as runner_mod
        orig = runner_mod.run_fred_macro_evidence
        runner_mod.run_fred_macro_evidence = _stub_raises
        try:
            db = _FakeSupabase()
            # Should NOT raise — exception is contained.
            result = mod.run_enabled_evidence_lanes_for_portfolio(
                user_id="u1",
                tickers=[],
                db_client=db,
                parent_intel_run_id="run-1",
                settings=_settings_macro_on(),
            )
            assert isinstance(result, dict)
        finally:
            runner_mod.run_fred_macro_evidence = orig

    def test_orchestrator_returns_empty_when_global_flag_off(self):
        from app.services.intelligence.v3 import (
            intel_v3_evidence_lane_orchestrator_v1 as mod,
        )
        db = _FakeSupabase()
        # Global research_workers flag OFF — short circuit happens before macro lane.
        result = mod.run_enabled_evidence_lanes_for_portfolio(
            user_id="u1",
            tickers=["AAPL"],
            db_client=db,
            parent_intel_run_id="run-1",
            settings=_base_settings(intel_v3_research_workers_enabled=False),
        )
        assert result == {}
        assert db.artifact_inserts() == []


# ── Safety / boundary invariants ──────────────────────────────────────────────


class TestSafetyInvariants:
    def test_no_decide_import_in_provider(self):
        import app.services.intelligence.research_workers.fred_provider_v1 as mod
        with open(mod.__file__) as f:
            content = f.read()
        assert "from app.services.intelligence.v3.decision_policy" not in content
        assert "import decide" not in content

    def test_no_decide_import_in_adapter(self):
        import app.services.intelligence.research_workers.fred_macro_adapter_v1 as mod
        with open(mod.__file__) as f:
            content = f.read()
        assert "from app.services.intelligence.v3.decision_policy" not in content
        assert "import decide" not in content

    def test_no_decide_import_in_runner(self):
        import app.services.intelligence.research_workers.evidence_lane_runner_v1 as mod
        with open(mod.__file__) as f:
            content = f.read()
        assert "from app.services.intelligence.v3.decision_policy" not in content
        assert "import decide" not in content

    def test_no_intel_v3_snapshots_in_adapter(self):
        import app.services.intelligence.research_workers.fred_macro_adapter_v1 as mod
        with open(mod.__file__) as f:
            content = f.read()
        assert '"intel_v3_snapshots"' not in content
        assert "'intel_v3_snapshots'" not in content

    def test_no_recommendations_in_adapter(self):
        import app.services.intelligence.research_workers.fred_macro_adapter_v1 as mod
        with open(mod.__file__) as f:
            content = f.read()
        assert '"recommendations"' not in content
        assert "'recommendations'" not in content

    def test_no_artifact_store_writer_bypass_in_runner(self):
        import app.services.intelligence.research_workers.evidence_lane_runner_v1 as mod
        with open(mod.__file__) as f:
            content = f.read()
        # Runner must go through ResearchArtifactServiceV1, not raw writer.
        assert "ArtifactStoreWriter" not in content

    def test_no_llm_or_paid_provider_in_adapter(self):
        import app.services.intelligence.research_workers.fred_macro_adapter_v1 as mod
        with open(mod.__file__) as f:
            content = f.read()
        for forbidden in ("anthropic", "openai", "fmp", "eodhd", "alpha_vantage"):
            assert forbidden not in content.lower(), f"unexpected token: {forbidden}"

    def test_config_flag_defaults_false(self):
        s = _base_settings()
        assert s.intel_v3_macro_evidence_enabled is False
        assert s.fred_api_key is None

    def test_macro_lane_constant_is_string(self):
        assert isinstance(LANE_MACRO, str)
        assert LANE_MACRO == "macro"

    def test_allowed_macro_series_contains_required(self):
        # Stage 5I required series — verify allowlist completeness.
        required = {
            "FEDFUNDS", "DFF", "DGS10", "DGS2",
            "CPIAUCSL", "UNRATE", "PAYEMS",
            "GDP", "GDPC1", "T10Y2Y",
        }
        assert required.issubset(set(ALLOWED_MACRO_SERIES.keys()))

    def test_orchestrator_logs_macro_artifact_id(self):
        import app.services.intelligence.v3.intel_v3_evidence_lane_orchestrator_v1 as mod
        with open(mod.__file__) as f:
            content = f.read()
        assert "macro_artifact_id" in content


# ── Stage 5I patch — provider-aware credibility override ─────────────────────


class TestFredProviderAwareCredibility:
    """The Stage 5I patch adds a narrow provider-aware override that classifies
    FRED macro sources as official authority despite source_kind="other",
    without weakening UNKNOWN handling for generic 'other' sources."""

    def _fred_source(
        self,
        series_id: str = "DGS10",
        url: Optional[str] = None,
        provider: str = "fred",
        source_kind: str = "other",
    ):
        from app.services.intelligence.research_workers.contracts import SourceRecord
        return SourceRecord(
            source_kind=source_kind,
            provider_name=provider,
            provider_version="fred_official_macro_v1",
            source_url=url if url is not None else f"https://fred.stlouisfed.org/series/{series_id}",
            source_id=series_id,
            section_reference="fred_series:treasury_yield",
        )

    def test_fred_source_classifies_as_primary_authority(self):
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
            AuthorityLevel,
        )
        result = assess_artifact_sources([self._fred_source("DGS10")])
        assert result.is_insufficient is False
        assert result.strongest_authority_level == AuthorityLevel.PRIMARY_AUTHORITY.value

    def test_fred_source_authorship_is_official_public_data(self):
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
            SourceAuthorship,
        )
        result = assess_artifact_sources([self._fred_source("UNRATE")])
        per = result.per_source_assessments[0]
        assert per["authorship"] == SourceAuthorship.OFFICIAL_PUBLIC_DATA.value

    def test_fred_source_supports_official_macro_data_claim(self):
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
            CLAIM_OFFICIAL_MACRO_DATA,
        )
        result = assess_artifact_sources([self._fred_source("PAYEMS")])
        assert CLAIM_OFFICIAL_MACRO_DATA in result.claim_categories_any_source_supports

    def test_fred_override_per_source_flag_present(self):
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
        )
        result = assess_artifact_sources([self._fred_source("CPIAUCSL")])
        per = result.per_source_assessments[0]
        assert per["provider_aware_override_applied"] is True
        assert per["provider_aware_override_id"] == "fred_macro_official_v1"

    def test_fred_override_never_supports_buy_sell_or_recommendation(self):
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
        )
        result = assess_artifact_sources([self._fred_source("DGS10")])
        never_supported = result.claim_categories_no_source_can_support
        for bad in (
            "buy_sell_action", "recommendation", "price_target",
            "final_action", "future_performance", "allocation", "conviction",
        ):
            assert bad in never_supported

    def test_fred_override_matches_url_when_source_id_missing(self):
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
            AuthorityLevel,
        )
        src = self._fred_source("DGS2")
        src.source_id = None  # rely on URL match only
        result = assess_artifact_sources([src])
        assert result.strongest_authority_level == AuthorityLevel.PRIMARY_AUTHORITY.value

    def test_fred_override_rejects_unknown_series_id(self):
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
            AuthorityLevel,
        )
        src = self._fred_source("NOT_ALLOWED_SERIES", url="https://example.com/x")
        result = assess_artifact_sources([src])
        # Falls back to plain "other" → UNKNOWN.
        assert result.strongest_authority_level == AuthorityLevel.UNKNOWN.value
        assert result.is_insufficient is True

    def test_generic_other_provider_stays_unknown(self):
        """A different provider with source_kind='other' must remain UNKNOWN."""
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
            AuthorityLevel,
        )
        src = self._fred_source("DGS10", provider="some_unknown_vendor",
                                url="https://other.example.com/series/DGS10")
        result = assess_artifact_sources([src])
        assert result.strongest_authority_level == AuthorityLevel.UNKNOWN.value
        assert result.is_insufficient is True

    def test_fred_match_requires_source_kind_other(self):
        """If a future migration switches source_kind, the 'other' override
        does not over-match."""
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
        )
        src = self._fred_source("DGS10", source_kind="news")
        result = assess_artifact_sources([src])
        per = result.per_source_assessments[0]
        assert per["provider_aware_override_applied"] is False

    def test_fred_provider_name_case_insensitive(self):
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
            AuthorityLevel,
        )
        src = self._fred_source("DGS10", provider="FRED")
        result = assess_artifact_sources([src])
        assert result.strongest_authority_level == AuthorityLevel.PRIMARY_AUTHORITY.value

    def test_fred_artifact_truth_label_is_usable(self):
        """End-to-end: a real FRED artifact written through the runner should
        land on USABLE or USABLE_WITH_LIMITATIONS, never SUPPRESSED_UNKNOWN_SOURCE."""
        from app.services.intelligence.v3.artifact_truth_adapter_v1 import (
            ArtifactUsabilityLabel,
        )
        db = _FakeSupabase()
        run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=lambda sids: _success_provider_result(sids),
        )
        payload = db.artifact_inserts()[0]["payload"]
        usab = payload["truth_usability_assessment"]
        assert usab["usability_label"] in (
            ArtifactUsabilityLabel.USABLE.value,
            ArtifactUsabilityLabel.USABLE_WITH_LIMITATIONS.value,
        ), (
            f"FRED artifact must be truth-usable; got "
            f"{usab['usability_label']} suppression_reason={usab.get('suppression_reason')}"
        )
        cred = payload["source_credibility_assessment"]
        assert cred["is_insufficient"] is False
        assert cred["strongest_authority_level"] == "PRIMARY_AUTHORITY"

    def test_fred_artifact_safe_for_decision_unchanged(self):
        """The override must NOT flip safe_for_decision to True."""
        db = _FakeSupabase()
        run_fred_macro_evidence(
            user_id="user-1", db_client=db,
            settings=_settings_macro_on(),
            _provider_fn=lambda sids: _success_provider_result(sids),
        )
        for row in db.artifact_inserts():
            assert row.get("safe_for_decision") is not True

    def test_empty_sources_still_insufficient(self):
        """Override does not affect the no-sources path."""
        from app.services.intelligence.v3.source_credibility_registry_v1 import (
            assess_artifact_sources,
        )
        result = assess_artifact_sources([])
        assert result.is_insufficient is True
        assert result.has_sources is False

    def test_adapter_limitations_describe_provider_override(self):
        """Adapter's limitations now explain the provider-aware override."""
        provider = _success_provider_result(["DGS10"])
        res = adapt_fred_macro(provider, provider.fetched_at)
        joined = " ".join(res.limitations).lower()
        assert "provider-aware override" in joined or "provider_aware" in joined
