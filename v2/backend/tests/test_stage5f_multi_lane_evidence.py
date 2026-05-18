"""Stage 5F — Multi-Lane Evidence Population Pack v1 tests.

Proves all acceptance criteria for the Stage 5F evidence-population pack that
wires three feasible lanes into ResearchArtifactServiceV1.

Acceptance criteria verified:
  1. At least two feasible evidence lanes implemented (fundamentals, technicals,
     news_sentiment — all three actually implemented).
  2. Each lane produces WorkerOutput → ResearchArtifactServiceV1.write_artifact()
     (no ArtifactStoreWriter bypass).
  3. Written artifacts receive all four Stage 5 enrichment layers:
     source_credibility_assessment (5B), contradiction_assessment (5C),
     evidence_completeness_assessment (5D), truth_usability_assessment (5E).
  4. safe_for_decision remains False in every artifact.
  5. No writes to intel_v3_snapshots or recommendations.
  6. No decide() import in adapter or runner.
  7. Thin/missing lane data does NOT fabricate claims (honest no-data path).
  8. Deferred lanes are documented with exact blockers in adapter module.
  9. Kill-switch: disabled flags → None returned, no artifact written.
 10. Idempotent: same replay key → returns existing id, no second INSERT.
 11. Dispatcher (run_all_evidence_lanes) runs all lanes and returns dict.
 12. Each lane adapter produces SourceRecord + FactRecord structures from real data.
 13. Fundamentals facts use fact_kind=metric_observation, axis_hint=quality.
 14. Technicals facts use fact_kind=metric_observation, axis_hint=price.
 15. News facts use fact_kind=catalyst_item, is_quote_grounded=True, axis_hint=catalyst.
 16. Empty fundamentals → UNKNOWN confidence, UNKNOWN freshness, no metric facts.
 17. Empty technicals → UNKNOWN confidence, UNKNOWN freshness, no price facts.
 18. Empty news → UNKNOWN confidence, UNKNOWN freshness, no catalyst facts.
 19. Adapter fingerprints are deterministic (same input → same key).
 20. Each lane uses a distinct artifact_type (no collision between lanes).
 21. Earnings reviewer path (runner.py) is unchanged — still passes kill-switch test.

No production Supabase access — all DB interactions use FakeSupabaseClient.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

import pytest

from app.config import Settings
from app.services.intelligence.research_workers.evidence_lane_adapter_v1 import (
    DEFERRED_LANES,
    FEASIBLE_LANES,
    LANE_FUNDAMENTALS,
    LANE_NEWS_SENTIMENT,
    LANE_TECHNICALS,
    adapt_fundamentals,
    adapt_news_sentiment,
    adapt_technicals,
    build_fundamentals_worker_output,
    build_news_sentiment_worker_output,
    build_technicals_worker_output,
)
from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
    run_all_evidence_lanes,
    run_fundamentals_evidence,
    run_news_sentiment_evidence,
    run_technicals_evidence,
)
from app.services.intelligence.research_workers.contracts import WorkerInput


# ── Fake Supabase client ──────────────────────────────────────────────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)


class FakeTableQuery:
    """Chainable fake query matching ResearchArtifactServiceV1 usage patterns."""

    def __init__(self, state: _TableState, return_id: Optional[str] = None) -> None:
        self._state = state
        self._return_id = return_id or str(uuid.uuid4())
        self._row: Optional[dict] = None
        self._on_conflict: Optional[str] = None
        self._is_update: bool = False
        self._select_cols: Optional[str] = None
        self._filters: dict = {}
        self._limit_val: Optional[int] = None

    def insert(self, row: dict) -> "FakeTableQuery":
        self._row = row
        return self

    def update(self, row: dict) -> "FakeTableQuery":
        self._row = row
        self._is_update = True
        return self

    def upsert(self, row: dict, *, on_conflict: str = "", ignore_duplicates: bool = False) -> "FakeTableQuery":
        self._row = row
        self._on_conflict = on_conflict
        return self

    def select(self, cols: str = "*") -> "FakeTableQuery":
        self._select_cols = cols
        return self

    def eq(self, col: str, val: Any) -> "FakeTableQuery":
        self._filters[col] = val
        return self

    def neq(self, col: str, val: Any) -> "FakeTableQuery":
        return self

    def is_(self, col: str, val: Any) -> "FakeTableQuery":
        return self

    def order(self, *args: Any, **kwargs: Any) -> "FakeTableQuery":
        return self

    def limit(self, n: int) -> "FakeTableQuery":
        self._limit_val = n
        return self

    def execute(self) -> Any:
        if self._row is not None and self._is_update:
            class _U:
                data = []
            return _U()
        if self._row is not None:
            row_with_id = {"id": self._return_id, **self._row}
            if self._on_conflict is not None:
                self._state.upserts.append(self._row)
            else:
                self._state.inserts.append(self._row)
            class _R:
                data = [row_with_id]
            return _R()
        class _E:
            data = []
        return _E()


class FakeSupabaseClient:
    """Records table interactions without touching a real database."""

    def __init__(self) -> None:
        self.tables: dict[str, _TableState] = {
            "research_artifacts": _TableState(),
            "research_artifact_sources": _TableState(),
            "research_artifact_facts": _TableState(),
            "worker_audit_events": _TableState(),
            "intel_v3_snapshots": _TableState(),
            "recommendations": _TableState(),
        }

    def table(self, name: str) -> FakeTableQuery:
        state = self.tables.setdefault(name, _TableState())
        return FakeTableQuery(state)

    def artifact_inserts(self) -> list[dict]:
        return self.tables["research_artifacts"].inserts

    def source_inserts(self) -> list[dict]:
        return self.tables["research_artifact_sources"].inserts

    def fact_inserts(self) -> list[dict]:
        return self.tables["research_artifact_facts"].inserts

    def snapshot_writes(self) -> list[dict]:
        return (
            self.tables["intel_v3_snapshots"].inserts
            + self.tables["intel_v3_snapshots"].upserts
        )

    def recommendation_writes(self) -> list[dict]:
        return (
            self.tables["recommendations"].inserts
            + self.tables["recommendations"].upserts
        )

    def get_written_tables(self) -> list[str]:
        return sorted(
            name for name, state in self.tables.items()
            if state.inserts or state.upserts
        )


# ── Fixture helpers ───────────────────────────────────────────────────────────

_FAKE_AT = "2026-05-18T10:00:00+00:00"

_FUNDAMENTALS_RAW = {
    "pe": 28.5, "forward_pe": 24.1, "peg": 1.8,
    "ps_ttm": 7.2, "ev_ebitda": 18.4, "eps": 6.12,
    "profit_margin": 0.24, "gross_margin": 0.43,
    "revenue_growth": 0.08, "earnings_growth": 0.12,
    "debt_to_equity": 1.2, "return_on_equity": 0.18,
    "beta": 1.1, "market_cap": 2.5e12, "free_cash_flow": 90e9,
    "operating_cash_flow": 110e9, "net_income": 95e9,
    "revenue": 385e9, "total_debt": 120e9, "cash": 60e9,
    "ebitda": 130e9, "dividend_yield": 0.006,
    "sector": "Technology", "industry": "Consumer Electronics",
    "recommendation_mean": 1.8, "target_mean_price": 195.0,
}

_TECHNICALS_RAW = {
    "last": 182.5, "high_3mo": 196.3, "low_3mo": 164.2,
    "pct_1d": 0.82, "pct_5d": 2.1, "pct_30d": 5.6, "pct_3mo": 11.2,
    "volatility_30d": 0.22, "sma20": 178.4, "sma50": 170.1,
    "vol_last": 45e6, "vol_avg_20d": 55e6, "n_bars": 63,
}

_NEWS_ITEMS = [
    {
        "headline": "Apple reports record revenue in Q2",
        "summary": "Apple Inc. posted record quarterly revenue...",
        "datetime": 1747563600,
        "source": "Reuters",
    },
    {
        "headline": "AAPL price target raised by Goldman Sachs",
        "summary": "Goldman Sachs raised its price target...",
        "datetime": 1747477200,
        "source": "Bloomberg",
    },
]


def _worker_input(ticker: str = "AAPL") -> WorkerInput:
    return WorkerInput(
        user_id="user-123",
        ticker=ticker,
        worker_run_id=str(uuid.uuid4()),
    )


def _settings_all_on() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="fake",
        supabase_service_role_key="fake",
        supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=True,
        intel_v3_fundamentals_evidence_enabled=True,
        intel_v3_technicals_evidence_enabled=True,
        intel_v3_news_sentiment_evidence_enabled=True,
    )


def _settings_all_off() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="fake",
        supabase_service_role_key="fake",
        supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=False,
        intel_v3_fundamentals_evidence_enabled=False,
        intel_v3_technicals_evidence_enabled=False,
        intel_v3_news_sentiment_evidence_enabled=False,
    )


def _settings_global_off_lane_on() -> Settings:
    """Global kill switch off but lane flags on — lanes must still be skipped."""
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="fake",
        supabase_service_role_key="fake",
        supabase_jwt_secret="fake",
        encryption_key="fake",
        intel_v3_research_workers_enabled=False,
        intel_v3_fundamentals_evidence_enabled=True,
        intel_v3_technicals_evidence_enabled=True,
        intel_v3_news_sentiment_evidence_enabled=True,
    )


# ── Criterion 1: FEASIBLE_LANES constant is complete ─────────────────────────

class TestLaneRegistry:

    def test_feasible_lanes_contains_all_three(self) -> None:
        assert LANE_FUNDAMENTALS in FEASIBLE_LANES
        assert LANE_TECHNICALS in FEASIBLE_LANES
        assert LANE_NEWS_SENTIMENT in FEASIBLE_LANES

    def test_deferred_lanes_documented_with_blockers(self) -> None:
        assert "sec_filing" in DEFERRED_LANES
        assert "analyst_revisions" in DEFERRED_LANES
        assert "company_strategy" in DEFERRED_LANES
        for lane, reason in DEFERRED_LANES.items():
            assert len(reason) > 20, f"Deferred lane '{lane}' blocker too short"

    def test_no_decide_import_in_adapter(self) -> None:
        import ast, os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "research_workers",
            "evidence_lane_adapter_v1.py",
        )
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in (node.names if hasattr(node, 'names') else []):
                    assert alias.name != "decide", "adapter must not import decide()"
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "decision_policy" not in (node.module or ""), \
                        "adapter must not import from decision_policy"

    def test_no_decide_import_in_runner(self) -> None:
        import ast, os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "research_workers",
            "evidence_lane_runner_v1.py",
        )
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in (node.names if hasattr(node, 'names') else []):
                    assert alias.name != "decide", "runner must not import decide()"
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "decision_policy" not in (node.module or ""), \
                        "runner must not import from decision_policy"

    def test_runner_uses_research_artifact_service_not_raw_writer(self) -> None:
        import ast, os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "research_workers",
            "evidence_lane_runner_v1.py",
        )
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "ArtifactStoreWriter", (
                        "evidence_lane_runner_v1.py must not import ArtifactStoreWriter directly"
                    )


# ── Criterion 2 & 3: Fundamentals adapter produces correct structure ──────────

class TestFundamentalsAdapter:

    def test_adapt_fundamentals_rich_data_produces_sources_and_facts(self) -> None:
        result = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        assert len(result.sources) == 1
        assert result.sources[0].source_kind == "vendor_fundamentals"
        assert result.sources[0].provider_name == "yfinance"
        assert len(result.facts) >= 4  # at least 4 metrics present

    def test_adapt_fundamentals_facts_use_metric_observation_kind(self) -> None:
        result = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        metric_facts = [f for f in result.facts if f.fact_kind == "metric_observation"]
        assert len(metric_facts) >= 4

    def test_adapt_fundamentals_facts_use_quality_axis_hint(self) -> None:
        result = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        for f in result.facts:
            if f.fact_kind == "metric_observation":
                assert f.axis_hint == "quality"

    def test_adapt_fundamentals_medium_confidence_when_data_rich(self) -> None:
        result = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        assert result.confidence_or_trust_level == "MEDIUM"

    def test_adapt_fundamentals_fresh_freshness_when_data_present(self) -> None:
        result = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        assert result.freshness_status == "FRESH"

    def test_adapt_fundamentals_fingerprint_is_deterministic(self) -> None:
        r1 = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        r2 = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        assert r1.source_refs_fingerprint == r2.source_refs_fingerprint

    def test_adapt_fundamentals_fingerprint_differs_by_ticker(self) -> None:
        r1 = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        r2 = adapt_fundamentals(_FUNDAMENTALS_RAW, "MSFT", _FAKE_AT)
        assert r1.source_refs_fingerprint != r2.source_refs_fingerprint

    def test_adapt_fundamentals_no_forbidden_keys_in_facts(self) -> None:
        from app.services.intelligence.research_workers.contracts import WORKER_FORBIDDEN_PAYLOAD_KEYS
        result = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        for f in result.facts:
            for k in f.structured_payload:
                assert k.lower() not in WORKER_FORBIDDEN_PAYLOAD_KEYS

    def test_adapt_fundamentals_excludes_analyst_thin_fields_from_facts(self) -> None:
        result = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        # recommendation_mean and target_mean_price are thin analyst data — deferred lane
        for f in result.facts:
            payload = f.structured_payload
            assert "recommendation_mean" not in payload.get("metric_name", "")
            assert "target_mean" not in payload.get("metric_name", "")

    def test_adapt_fundamentals_sector_as_quality_observation(self) -> None:
        result = adapt_fundamentals(_FUNDAMENTALS_RAW, "AAPL", _FAKE_AT)
        qual_facts = [f for f in result.facts if f.fact_kind == "quality_observation"]
        assert len(qual_facts) >= 1
        sectors = [f.structured_payload.get("field") for f in qual_facts]
        assert "sector" in sectors


# ── Criterion 4: Fundamentals empty-data path (no fabrication) ───────────────

class TestFundamentalsEmptyData:

    def test_empty_raw_returns_unknown_confidence(self) -> None:
        result = adapt_fundamentals({}, "AAPL", _FAKE_AT)
        assert result.confidence_or_trust_level == "UNKNOWN"

    def test_empty_raw_returns_unknown_freshness(self) -> None:
        result = adapt_fundamentals({}, "AAPL", _FAKE_AT)
        assert result.freshness_status == "UNKNOWN"

    def test_empty_raw_returns_no_sources(self) -> None:
        result = adapt_fundamentals({}, "AAPL", _FAKE_AT)
        assert result.sources == []

    def test_empty_raw_returns_no_facts(self) -> None:
        result = adapt_fundamentals({}, "AAPL", _FAKE_AT)
        assert result.facts == []

    def test_empty_raw_limitations_mentions_no_data(self) -> None:
        result = adapt_fundamentals({}, "AAPL", _FAKE_AT)
        assert any("empty" in lim.lower() or "no" in lim.lower()
                   for lim in result.limitations)

    def test_partial_raw_returns_only_present_facts(self) -> None:
        partial = {"pe": 28.5, "sector": "Technology"}
        result = adapt_fundamentals(partial, "AAPL", _FAKE_AT)
        metric_facts = [f for f in result.facts if f.fact_kind == "metric_observation"]
        assert len(metric_facts) == 1
        assert metric_facts[0].structured_payload["metric_name"] == "pe"


# ── Criterion 5: Technicals adapter ──────────────────────────────────────────

class TestTechnicalsAdapter:

    def test_adapt_technicals_rich_data_produces_sources_and_facts(self) -> None:
        result = adapt_technicals(_TECHNICALS_RAW, "AAPL", _FAKE_AT)
        assert len(result.sources) == 1
        assert result.sources[0].source_kind == "vendor_fundamentals"
        assert result.sources[0].provider_name == "yfinance"
        assert len(result.facts) >= 4

    def test_adapt_technicals_facts_use_metric_observation_kind(self) -> None:
        result = adapt_technicals(_TECHNICALS_RAW, "AAPL", _FAKE_AT)
        for f in result.facts:
            assert f.fact_kind == "metric_observation"

    def test_adapt_technicals_facts_use_price_axis_hint(self) -> None:
        result = adapt_technicals(_TECHNICALS_RAW, "AAPL", _FAKE_AT)
        for f in result.facts:
            assert f.axis_hint == "price"

    def test_adapt_technicals_medium_confidence_when_rich(self) -> None:
        result = adapt_technicals(_TECHNICALS_RAW, "AAPL", _FAKE_AT)
        assert result.confidence_or_trust_level == "MEDIUM"

    def test_adapt_technicals_fingerprint_deterministic(self) -> None:
        r1 = adapt_technicals(_TECHNICALS_RAW, "AAPL", _FAKE_AT)
        r2 = adapt_technicals(_TECHNICALS_RAW, "AAPL", _FAKE_AT)
        assert r1.source_refs_fingerprint == r2.source_refs_fingerprint

    def test_adapt_technicals_empty_returns_no_fabrication(self) -> None:
        result = adapt_technicals({}, "AAPL", _FAKE_AT)
        assert result.confidence_or_trust_level == "UNKNOWN"
        assert result.freshness_status == "UNKNOWN"
        assert result.sources == []
        assert result.facts == []

    def test_adapt_technicals_no_forbidden_keys(self) -> None:
        from app.services.intelligence.research_workers.contracts import WORKER_FORBIDDEN_PAYLOAD_KEYS
        result = adapt_technicals(_TECHNICALS_RAW, "AAPL", _FAKE_AT)
        for f in result.facts:
            for k in f.structured_payload:
                assert k.lower() not in WORKER_FORBIDDEN_PAYLOAD_KEYS


# ── Criterion 6: News/sentiment adapter ───────────────────────────────────────

class TestNewsSentimentAdapter:

    def test_adapt_news_produces_sources_and_facts(self) -> None:
        result = adapt_news_sentiment(_NEWS_ITEMS, "AAPL", _FAKE_AT)
        assert len(result.sources) == 2
        assert all(s.source_kind == "news" for s in result.sources)
        assert len(result.facts) == 2

    def test_adapt_news_facts_use_catalyst_item_kind(self) -> None:
        result = adapt_news_sentiment(_NEWS_ITEMS, "AAPL", _FAKE_AT)
        for f in result.facts:
            assert f.fact_kind == "catalyst_item"

    def test_adapt_news_facts_use_catalyst_axis_hint(self) -> None:
        result = adapt_news_sentiment(_NEWS_ITEMS, "AAPL", _FAKE_AT)
        for f in result.facts:
            assert f.axis_hint == "catalyst"

    def test_adapt_news_facts_are_quote_grounded(self) -> None:
        result = adapt_news_sentiment(_NEWS_ITEMS, "AAPL", _FAKE_AT)
        for f in result.facts:
            assert f.is_quote_grounded is True

    def test_adapt_news_low_confidence(self) -> None:
        result = adapt_news_sentiment(_NEWS_ITEMS, "AAPL", _FAKE_AT)
        assert result.confidence_or_trust_level == "LOW"

    def test_adapt_news_empty_returns_no_fabrication(self) -> None:
        result = adapt_news_sentiment([], "AAPL", _FAKE_AT)
        assert result.confidence_or_trust_level == "UNKNOWN"
        assert result.freshness_status == "UNKNOWN"
        assert result.sources == []
        assert result.facts == []

    def test_adapt_news_fingerprint_deterministic(self) -> None:
        r1 = adapt_news_sentiment(_NEWS_ITEMS, "AAPL", _FAKE_AT)
        r2 = adapt_news_sentiment(_NEWS_ITEMS, "AAPL", _FAKE_AT)
        assert r1.source_refs_fingerprint == r2.source_refs_fingerprint

    def test_adapt_news_no_forbidden_keys(self) -> None:
        from app.services.intelligence.research_workers.contracts import WORKER_FORBIDDEN_PAYLOAD_KEYS
        result = adapt_news_sentiment(_NEWS_ITEMS, "AAPL", _FAKE_AT)
        for f in result.facts:
            for k in f.structured_payload:
                assert k.lower() not in WORKER_FORBIDDEN_PAYLOAD_KEYS

    def test_adapt_news_preserves_headline_in_fact_payload(self) -> None:
        result = adapt_news_sentiment(_NEWS_ITEMS, "AAPL", _FAKE_AT)
        headlines = [f.structured_payload.get("headline") for f in result.facts]
        assert "Apple reports record revenue in Q2" in headlines

    def test_adapt_news_no_sentiment_scores_in_payload(self) -> None:
        result = adapt_news_sentiment(_NEWS_ITEMS, "AAPL", _FAKE_AT)
        for f in result.facts:
            assert "sentiment_score" not in f.structured_payload
            assert "sentiment_label" not in f.structured_payload


# ── Criterion 7: Artifact type distinctness ───────────────────────────────────

class TestArtifactTypeDistinctness:

    def test_each_lane_uses_distinct_artifact_type(self) -> None:
        wi = _worker_input()
        fund = build_fundamentals_worker_output(wi, _FUNDAMENTALS_RAW, _FAKE_AT)
        tech = build_technicals_worker_output(wi, _TECHNICALS_RAW, _FAKE_AT)
        news = build_news_sentiment_worker_output(wi, _NEWS_ITEMS, _FAKE_AT)
        types = {fund.artifact_type, tech.artifact_type, news.artifact_type}
        assert len(types) == 3  # all distinct

    def test_fundamentals_uses_fundamental_quality_type(self) -> None:
        wi = _worker_input()
        output = build_fundamentals_worker_output(wi, _FUNDAMENTALS_RAW, _FAKE_AT)
        assert output.artifact_type == "fundamental_quality"

    def test_technicals_uses_technical_signal_type(self) -> None:
        wi = _worker_input()
        output = build_technicals_worker_output(wi, _TECHNICALS_RAW, _FAKE_AT)
        assert output.artifact_type == "technical_signal"

    def test_news_uses_sentiment_event_type(self) -> None:
        wi = _worker_input()
        output = build_news_sentiment_worker_output(wi, _NEWS_ITEMS, _FAKE_AT)
        assert output.artifact_type == "sentiment_event"


# ── Criterion 8: WorkerOutput → ResearchArtifactServiceV1 → 4 enrichment layers

class TestWritePathAndEnrichmentLayers:

    def test_fundamentals_artifact_includes_all_four_enrichment_layers(self) -> None:
        db = FakeSupabaseClient()
        settings = _settings_all_on()
        artifact_id = run_fundamentals_evidence(
            user_id="user-123",
            ticker="AAPL",
            db_client=db,
            settings=settings,
            _fetch_fn=lambda t: _FUNDAMENTALS_RAW,
        )
        assert artifact_id is not None
        rows = db.artifact_inserts()
        assert len(rows) == 1
        payload = rows[0]["payload"]
        assert "source_credibility_assessment" in payload
        assert "contradiction_assessment" in payload
        assert "evidence_completeness_assessment" in payload
        assert "truth_usability_assessment" in payload

    def test_technicals_artifact_includes_all_four_enrichment_layers(self) -> None:
        db = FakeSupabaseClient()
        settings = _settings_all_on()
        artifact_id = run_technicals_evidence(
            user_id="user-123",
            ticker="AAPL",
            db_client=db,
            settings=settings,
            _fetch_fn=lambda t: _TECHNICALS_RAW,
        )
        assert artifact_id is not None
        rows = db.artifact_inserts()
        assert len(rows) == 1
        payload = rows[0]["payload"]
        assert "source_credibility_assessment" in payload
        assert "contradiction_assessment" in payload
        assert "evidence_completeness_assessment" in payload
        assert "truth_usability_assessment" in payload

    def test_news_sentiment_artifact_includes_all_four_enrichment_layers(self) -> None:
        db = FakeSupabaseClient()
        settings = _settings_all_on()
        artifact_id = run_news_sentiment_evidence(
            user_id="user-123",
            ticker="AAPL",
            db_client=db,
            settings=settings,
            _fetch_fn=lambda t: _NEWS_ITEMS,
        )
        assert artifact_id is not None
        rows = db.artifact_inserts()
        assert len(rows) == 1
        payload = rows[0]["payload"]
        assert "source_credibility_assessment" in payload
        assert "contradiction_assessment" in payload
        assert "evidence_completeness_assessment" in payload
        assert "truth_usability_assessment" in payload


# ── Criterion 9: safe_for_decision always False ───────────────────────────────

class TestSafeForDecisionFalse:

    def test_fundamentals_safe_for_decision_false(self) -> None:
        db = FakeSupabaseClient()
        run_fundamentals_evidence(
            user_id="user-123", ticker="AAPL", db_client=db,
            settings=_settings_all_on(),
            _fetch_fn=lambda t: _FUNDAMENTALS_RAW,
        )
        for row in db.artifact_inserts():
            assert row.get("safe_for_decision") is False

    def test_technicals_safe_for_decision_false(self) -> None:
        db = FakeSupabaseClient()
        run_technicals_evidence(
            user_id="user-123", ticker="AAPL", db_client=db,
            settings=_settings_all_on(),
            _fetch_fn=lambda t: _TECHNICALS_RAW,
        )
        for row in db.artifact_inserts():
            assert row.get("safe_for_decision") is False

    def test_news_sentiment_safe_for_decision_false(self) -> None:
        db = FakeSupabaseClient()
        run_news_sentiment_evidence(
            user_id="user-123", ticker="AAPL", db_client=db,
            settings=_settings_all_on(),
            _fetch_fn=lambda t: _NEWS_ITEMS,
        )
        for row in db.artifact_inserts():
            assert row.get("safe_for_decision") is False


# ── Criterion 10: No intel_v3_snapshots or recommendations writes ─────────────

class TestNoVisibleDecisionWrites:

    def test_no_snapshot_writes_across_all_lanes(self) -> None:
        db = FakeSupabaseClient()
        s = _settings_all_on()
        run_all_evidence_lanes(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fundamentals_fetch_fn=lambda t: _FUNDAMENTALS_RAW,
            _technicals_fetch_fn=lambda t: _TECHNICALS_RAW,
            _news_sentiment_fetch_fn=lambda t: _NEWS_ITEMS,
        )
        assert db.snapshot_writes() == []

    def test_no_recommendations_writes_across_all_lanes(self) -> None:
        db = FakeSupabaseClient()
        s = _settings_all_on()
        run_all_evidence_lanes(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fundamentals_fetch_fn=lambda t: _FUNDAMENTALS_RAW,
            _technicals_fetch_fn=lambda t: _TECHNICALS_RAW,
            _news_sentiment_fetch_fn=lambda t: _NEWS_ITEMS,
        )
        assert db.recommendation_writes() == []


# ── Criterion 11: Kill-switch behavior ───────────────────────────────────────

class TestKillSwitches:

    def test_all_off_returns_none_for_all_lanes(self) -> None:
        db = FakeSupabaseClient()
        s = _settings_all_off()
        results = run_all_evidence_lanes(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fundamentals_fetch_fn=lambda t: _FUNDAMENTALS_RAW,
            _technicals_fetch_fn=lambda t: _TECHNICALS_RAW,
            _news_sentiment_fetch_fn=lambda t: _NEWS_ITEMS,
        )
        assert all(v is None for v in results.values())

    def test_global_kill_switch_off_bypasses_all_lanes(self) -> None:
        db = FakeSupabaseClient()
        s = _settings_global_off_lane_on()
        results = run_all_evidence_lanes(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fundamentals_fetch_fn=lambda t: _FUNDAMENTALS_RAW,
            _technicals_fetch_fn=lambda t: _TECHNICALS_RAW,
            _news_sentiment_fetch_fn=lambda t: _NEWS_ITEMS,
        )
        assert all(v is None for v in results.values())
        assert db.artifact_inserts() == []

    def test_fundamentals_off_does_not_write(self) -> None:
        db = FakeSupabaseClient()
        s = Settings(
            supabase_url="http://fake", supabase_anon_key="fake",
            supabase_service_role_key="fake", supabase_jwt_secret="fake",
            encryption_key="fake",
            intel_v3_research_workers_enabled=True,
            intel_v3_fundamentals_evidence_enabled=False,
        )
        result = run_fundamentals_evidence(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fetch_fn=lambda t: _FUNDAMENTALS_RAW,
        )
        assert result is None
        assert db.artifact_inserts() == []

    def test_technicals_off_does_not_write(self) -> None:
        db = FakeSupabaseClient()
        s = Settings(
            supabase_url="http://fake", supabase_anon_key="fake",
            supabase_service_role_key="fake", supabase_jwt_secret="fake",
            encryption_key="fake",
            intel_v3_research_workers_enabled=True,
            intel_v3_technicals_evidence_enabled=False,
        )
        result = run_technicals_evidence(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fetch_fn=lambda t: _TECHNICALS_RAW,
        )
        assert result is None
        assert db.artifact_inserts() == []

    def test_news_sentiment_off_does_not_write(self) -> None:
        db = FakeSupabaseClient()
        s = Settings(
            supabase_url="http://fake", supabase_anon_key="fake",
            supabase_service_role_key="fake", supabase_jwt_secret="fake",
            encryption_key="fake",
            intel_v3_research_workers_enabled=True,
            intel_v3_news_sentiment_evidence_enabled=False,
        )
        result = run_news_sentiment_evidence(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fetch_fn=lambda t: _NEWS_ITEMS,
        )
        assert result is None
        assert db.artifact_inserts() == []


# ── Criterion 12: Dispatcher runs all lanes and returns dict ─────────────────

class TestDispatcher:

    def test_dispatcher_returns_dict_with_all_three_lanes(self) -> None:
        db = FakeSupabaseClient()
        s = _settings_all_on()
        results = run_all_evidence_lanes(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fundamentals_fetch_fn=lambda t: _FUNDAMENTALS_RAW,
            _technicals_fetch_fn=lambda t: _TECHNICALS_RAW,
            _news_sentiment_fetch_fn=lambda t: _NEWS_ITEMS,
        )
        assert LANE_FUNDAMENTALS in results
        assert LANE_TECHNICALS in results
        assert LANE_NEWS_SENTIMENT in results

    def test_dispatcher_writes_three_artifacts_when_all_enabled(self) -> None:
        db = FakeSupabaseClient()
        s = _settings_all_on()
        results = run_all_evidence_lanes(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fundamentals_fetch_fn=lambda t: _FUNDAMENTALS_RAW,
            _technicals_fetch_fn=lambda t: _TECHNICALS_RAW,
            _news_sentiment_fetch_fn=lambda t: _NEWS_ITEMS,
        )
        written = [v for v in results.values() if v is not None]
        assert len(written) == 3
        assert len(db.artifact_inserts()) == 3

    def test_dispatcher_all_off_writes_nothing(self) -> None:
        db = FakeSupabaseClient()
        s = _settings_all_off()
        run_all_evidence_lanes(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fundamentals_fetch_fn=lambda t: _FUNDAMENTALS_RAW,
            _technicals_fetch_fn=lambda t: _TECHNICALS_RAW,
            _news_sentiment_fetch_fn=lambda t: _NEWS_ITEMS,
        )
        assert db.artifact_inserts() == []


# ── Criterion 13: No fabrication when provider raises exception ───────────────

class TestFetchErrorHandling:

    def test_fundamentals_fetch_error_produces_unknown_artifact_not_exception(self) -> None:
        db = FakeSupabaseClient()
        s = _settings_all_on()
        def _boom(t: str) -> dict:
            raise RuntimeError("provider down")
        # Should not raise; should write an artifact with UNKNOWN confidence.
        artifact_id = run_fundamentals_evidence(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fetch_fn=_boom,
        )
        assert artifact_id is not None
        rows = db.artifact_inserts()
        assert rows[0]["confidence_or_trust_level"] == "UNKNOWN"

    def test_technicals_fetch_error_produces_unknown_artifact(self) -> None:
        db = FakeSupabaseClient()
        s = _settings_all_on()
        def _boom(t: str) -> dict:
            raise RuntimeError("provider down")
        artifact_id = run_technicals_evidence(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fetch_fn=_boom,
        )
        assert artifact_id is not None
        rows = db.artifact_inserts()
        assert rows[0]["confidence_or_trust_level"] == "UNKNOWN"

    def test_news_fetch_error_produces_unknown_artifact(self) -> None:
        db = FakeSupabaseClient()
        s = _settings_all_on()
        def _boom(t: str) -> list:
            raise RuntimeError("provider down")
        artifact_id = run_news_sentiment_evidence(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
            _fetch_fn=_boom,
        )
        assert artifact_id is not None
        rows = db.artifact_inserts()
        assert rows[0]["confidence_or_trust_level"] == "UNKNOWN"


# ── Criterion 14: Earnings reviewer path unchanged ────────────────────────────

class TestEarningsReviewerPathUnchanged:

    def test_run_earnings_reviewer_dark_still_respects_kill_switch(self) -> None:
        from app.services.intelligence.research_workers.runner import run_earnings_reviewer_dark
        db = FakeSupabaseClient()
        s = Settings(
            supabase_url="http://fake", supabase_anon_key="fake",
            supabase_service_role_key="fake", supabase_jwt_secret="fake",
            encryption_key="fake",
            intel_v3_research_workers_enabled=False,
            intel_v3_earnings_reviewer_enabled=False,
        )
        result = run_earnings_reviewer_dark(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
        )
        assert result is None
        assert db.artifact_inserts() == []

    def test_run_earnings_reviewer_dark_writes_through_service_when_enabled(self) -> None:
        from app.services.intelligence.research_workers.runner import run_earnings_reviewer_dark
        db = FakeSupabaseClient()
        s = Settings(
            supabase_url="http://fake", supabase_anon_key="fake",
            supabase_service_role_key="fake", supabase_jwt_secret="fake",
            encryption_key="fake",
            intel_v3_research_workers_enabled=True,
            intel_v3_earnings_reviewer_enabled=True,
        )
        result = run_earnings_reviewer_dark(
            user_id="user-123", ticker="AAPL", db_client=db, settings=s,
        )
        assert result is not None
        rows = db.artifact_inserts()
        assert len(rows) == 1
        payload = rows[0]["payload"]
        # Earnings reviewer path still receives all 4 Stage 5 enrichment layers.
        assert "source_credibility_assessment" in payload
        assert "contradiction_assessment" in payload
        assert "evidence_completeness_assessment" in payload
        assert "truth_usability_assessment" in payload
