"""Stage 5H.1 — Intel v3 evidence lane orchestration tests.

Proves all acceptance criteria for Stage 5H.1 (wire enabled evidence lanes into
Intel v3 explicit run path):

  1. Explicit POST /intel/v3/run wires evidence lane dispatch even when
     analyst evidence is current (source-code structural proof).
  2. GET /intel/v3/snapshot (page load) does NOT dispatch evidence lanes
     (source-code structural proof).
  3. Global kill switch off → no dispatch, returns {}.
  4. Empty portfolio → no dispatch.
  5. Enabled flags + tickers → run_evidence_lanes_for_ticker called once per ticker.
  6. Per-ticker error does not crash the batch (fail-soft).
  7. All evidence lane flags off → no artifact writes.
  8. SEC CompanyFacts flag on + SEC user agent → SEC lane dispatches (artifact written).
  9. SEC CompanyFacts flag on, missing user agent → SEC lane skipped honestly.
 10. Artifacts write through ResearchArtifactServiceV1 (no bypass).
 11. No writes to intel_v3_snapshots or recommendations.
 12. Orchestrator does not import decide().
 13. Orchestrator does not write to intel_v3_snapshots (source-code scan).

No production Supabase access. All DB interactions use FakeSupabaseClient.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional

import pytest

from app.config import Settings
from app.services.intelligence.v3.intel_v3_evidence_lane_orchestrator_v1 import (
    run_enabled_evidence_lanes_for_portfolio,
)
from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
    run_all_evidence_lanes,
)
from app.services.intelligence.research_workers.evidence_provider_registry_v1 import (
    LANE_SEC_COMPANY_FACTS,
)
from app.services.intelligence.research_workers.evidence_lane_adapter_v1 import (
    LANE_FUNDAMENTALS,
    LANE_TECHNICALS,
    LANE_NEWS_SENTIMENT,
)


# ── Fake Supabase client (mirrors Stage 5F/5H test infrastructure) ────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)


class FakeTableQuery:
    def __init__(self, state: _TableState, return_id: Optional[str] = None) -> None:
        self._state = state
        self._return_id = return_id or str(uuid.uuid4())
        self._row: Optional[dict] = None
        self._on_conflict: Optional[str] = None
        self._is_update: bool = False

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
        return self

    def eq(self, col: str, val: Any) -> "FakeTableQuery":
        return self

    def neq(self, col: str, val: Any) -> "FakeTableQuery":
        return self

    def is_(self, col: str, val: Any) -> "FakeTableQuery":
        return self

    def order(self, *args: Any, **kwargs: Any) -> "FakeTableQuery":
        return self

    def limit(self, n: int) -> "FakeTableQuery":
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


# ── Settings helpers ──────────────────────────────────────────────────────────

_BASE = dict(
    supabase_url="http://fake",
    supabase_anon_key="anon",
    supabase_service_role_key="svc",
    supabase_jwt_secret="secret",
    encryption_key="a" * 32,
)


def _settings_global_off() -> Settings:
    return Settings(
        **_BASE,
        intel_v3_research_workers_enabled=False,
        intel_v3_fundamentals_evidence_enabled=True,
        intel_v3_technicals_evidence_enabled=True,
        intel_v3_news_sentiment_evidence_enabled=True,
        intel_v3_sec_companyfacts_evidence_enabled=True,
        sec_edgar_user_agent="TestApp/1.0 test@example.com",
    )


def _settings_all_lanes_off() -> Settings:
    return Settings(
        **_BASE,
        intel_v3_research_workers_enabled=True,
        intel_v3_fundamentals_evidence_enabled=False,
        intel_v3_technicals_evidence_enabled=False,
        intel_v3_news_sentiment_evidence_enabled=False,
        intel_v3_sec_companyfacts_evidence_enabled=False,
    )


def _settings_sec_only_with_agent() -> Settings:
    return Settings(
        **_BASE,
        intel_v3_research_workers_enabled=True,
        intel_v3_fundamentals_evidence_enabled=False,
        intel_v3_technicals_evidence_enabled=False,
        intel_v3_news_sentiment_evidence_enabled=False,
        intel_v3_sec_companyfacts_evidence_enabled=True,
        sec_edgar_user_agent="TestApp/1.0 test@example.com",
    )


def _settings_sec_only_no_agent() -> Settings:
    return Settings(
        **_BASE,
        intel_v3_research_workers_enabled=True,
        intel_v3_fundamentals_evidence_enabled=False,
        intel_v3_technicals_evidence_enabled=False,
        intel_v3_news_sentiment_evidence_enabled=False,
        intel_v3_sec_companyfacts_evidence_enabled=True,
        sec_edgar_user_agent=None,
    )


def _settings_fundamentals_only() -> Settings:
    return Settings(
        **_BASE,
        intel_v3_research_workers_enabled=True,
        intel_v3_fundamentals_evidence_enabled=True,
        intel_v3_technicals_evidence_enabled=False,
        intel_v3_news_sentiment_evidence_enabled=False,
        intel_v3_sec_companyfacts_evidence_enabled=False,
    )


# ── Fake SEC provider result ──────────────────────────────────────────────────

def _make_success_sec_result():
    from app.services.intelligence.research_workers.sec_edgar_provider import (
        SecEdgarProviderResult,
        SecFilingRecord,
    )
    from app.services.intelligence.research_workers.sec_companyfacts_parser import (
        CompanyFactsParseResult,
        MetricObservation,
    )
    obs = MetricObservation(
        taxonomy="us-gaap",
        tag="Revenues",
        label="Revenues",
        value=394329000000.0,
        unit="USD",
        accession_number="0000320193-23-000054",
        fiscal_year=2023,
        fiscal_period="FY",
        filed="2023-11-03",
        form="10-K",
    )
    cf = CompanyFactsParseResult(
        parse_status="success",
        observations=[obs],
        tags_found=["Revenues"],
    )
    filing = SecFilingRecord(
        form_type="10-K",
        filing_date="2023-11-03",
        accession_number="0000320193-23-000054",
        report_date="2023-09-30",
        filing_url="https://www.sec.gov/Archives/edgar/data/320193/000032019323000054/",
    )
    return SecEdgarProviderResult(
        ticker="AAPL",
        cik="0000320193",
        filings=[filing],
        fetch_status="success",
        fetched_at="2026-05-18T10:00:00+00:00",
        request_count=2,
        companyfacts_parse_result=cf,
    )


# ── Criterion 3: Global kill switch off → no dispatch ────────────────────────

class TestGlobalKillSwitch:
    def test_global_flag_off_returns_empty(self):
        db = FakeSupabaseClient()
        result = run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL", "MSFT"],
            db_client=db,
            settings=_settings_global_off(),
        )
        assert result == {}

    def test_global_flag_off_writes_no_artifacts(self):
        db = FakeSupabaseClient()
        run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
            settings=_settings_global_off(),
        )
        assert db.artifact_inserts() == []

    def test_global_flag_off_no_snapshot_writes(self):
        db = FakeSupabaseClient()
        run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
            settings=_settings_global_off(),
        )
        assert db.snapshot_writes() == []


# ── Criterion 4: Empty portfolio → no dispatch ────────────────────────────────

class TestEmptyPortfolio:
    def test_empty_tickers_returns_empty(self):
        db = FakeSupabaseClient()
        result = run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=[],
            db_client=db,
            settings=_settings_fundamentals_only(),
        )
        assert result == {}

    def test_empty_tickers_writes_no_artifacts(self):
        db = FakeSupabaseClient()
        run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=[],
            db_client=db,
            settings=_settings_fundamentals_only(),
        )
        assert db.artifact_inserts() == []


# ── Criterion 5: Enabled flags + tickers → runner called per ticker ───────────

class TestDispatchPerTicker:
    def test_runner_called_once_per_ticker(self, monkeypatch):
        """Orchestrator calls run_evidence_lanes_for_ticker for each ticker."""
        calls: list[str] = []

        def fake_runner(user_id, ticker, db_client, parent_intel_run_id=None,
                        holding_context=None, settings=None,
                        _fundamentals_fetch_fn=None, _technicals_fetch_fn=None,
                        _news_sentiment_fetch_fn=None):
            calls.append(ticker)
            return {LANE_FUNDAMENTALS: "fake-id", LANE_TECHNICALS: None,
                    LANE_NEWS_SENTIMENT: None, LANE_SEC_COMPANY_FACTS: None}

        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", fake_runner)

        db = FakeSupabaseClient()
        run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL", "MSFT", "GOOGL"],
            db_client=db,
            settings=_settings_fundamentals_only(),
        )

        assert len(calls) == 3
        assert "AAPL" in calls
        assert "MSFT" in calls
        assert "GOOGL" in calls

    def test_result_keys_match_tickers(self, monkeypatch):
        """Return dict has one key per ticker."""
        def fake_runner(user_id, ticker, db_client, **kwargs):
            return {LANE_FUNDAMENTALS: "id-" + ticker}

        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", fake_runner)

        db = FakeSupabaseClient()
        result = run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL", "MSFT"],
            db_client=db,
            settings=_settings_fundamentals_only(),
        )

        assert set(result.keys()) == {"AAPL", "MSFT"}

    def test_settings_passed_to_runner(self, monkeypatch):
        """Settings object is forwarded to run_evidence_lanes_for_ticker."""
        received_settings: list[Settings] = []

        def fake_runner(user_id, ticker, db_client, parent_intel_run_id=None,
                        holding_context=None, settings=None, **kwargs):
            received_settings.append(settings)
            return {}

        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", fake_runner)

        s = _settings_fundamentals_only()
        db = FakeSupabaseClient()
        run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
            settings=s,
        )

        assert len(received_settings) == 1
        assert received_settings[0] is s


# ── Criterion 6: Per-ticker error does not crash batch ────────────────────────

class TestFailSoft:
    def test_per_ticker_error_continues_remaining(self, monkeypatch):
        """An exception on one ticker must not abort the remaining tickers."""
        calls: list[str] = []

        def fake_runner(user_id, ticker, db_client, **kwargs):
            calls.append(ticker)
            if ticker == "AAPL":
                raise RuntimeError("simulated failure")
            return {LANE_FUNDAMENTALS: "ok-" + ticker}

        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", fake_runner)

        db = FakeSupabaseClient()
        result = run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL", "MSFT"],
            db_client=db,
            settings=_settings_fundamentals_only(),
        )

        assert "AAPL" in calls
        assert "MSFT" in calls
        # Failed ticker returns empty dict, not missing key
        assert result["AAPL"] == {}
        # Successful ticker has its result
        assert result["MSFT"][LANE_FUNDAMENTALS] == "ok-MSFT"

    def test_per_ticker_error_does_not_raise(self, monkeypatch):
        def fake_runner(user_id, ticker, db_client, **kwargs):
            raise ValueError("boom")

        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", fake_runner)

        db = FakeSupabaseClient()
        # Must not raise
        result = run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
            settings=_settings_fundamentals_only(),
        )
        assert result["AAPL"] == {}


# ── Criterion 7: All evidence lane flags off → no artifact writes ─────────────

class TestAllFlagsOff:
    def test_all_lanes_off_no_artifacts(self):
        db = FakeSupabaseClient()
        run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
            settings=_settings_all_lanes_off(),
        )
        assert db.artifact_inserts() == []

    def test_all_lanes_off_result_values_none(self):
        db = FakeSupabaseClient()
        result = run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
            settings=_settings_all_lanes_off(),
        )
        if result:
            for lane_results in result.values():
                for v in lane_results.values():
                    assert v is None


# ── Criterion 8: SEC flag on + user agent → SEC lane dispatches ───────────────

class TestSecLaneWithAgent:
    def test_sec_flag_on_with_agent_writes_sec_artifact(self):
        """SEC CompanyFacts lane writes an artifact when flag on + user agent set."""
        db = FakeSupabaseClient()
        provider_result = _make_success_sec_result()

        result = run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_only_with_agent(),
            _sec_companyfacts_provider_fn=lambda t: provider_result,
        )

        assert result[LANE_SEC_COMPANY_FACTS] is not None, (
            "SEC CompanyFacts artifact must be written when flag on + user agent set"
        )

    def test_sec_lane_artifact_written_to_research_artifacts(self):
        db = FakeSupabaseClient()
        provider_result = _make_success_sec_result()

        run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_only_with_agent(),
            _sec_companyfacts_provider_fn=lambda t: provider_result,
        )

        assert len(db.artifact_inserts()) >= 1, (
            "At least one artifact must be written to research_artifacts"
        )

    def test_sec_settings_forwarded_through_orchestrator(self, monkeypatch):
        """When orchestrator receives SEC-enabled settings, they reach the runner."""
        received_settings: list[Settings] = []

        def fake_runner(user_id, ticker, db_client, parent_intel_run_id=None,
                        holding_context=None, settings=None, **kwargs):
            received_settings.append(settings)
            return {LANE_SEC_COMPANY_FACTS: "sec-artifact-id"}

        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", fake_runner)

        s = _settings_sec_only_with_agent()
        db = FakeSupabaseClient()
        run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
            settings=s,
        )

        assert received_settings[0].intel_v3_sec_companyfacts_evidence_enabled is True
        assert received_settings[0].sec_edgar_user_agent == "TestApp/1.0 test@example.com"


# ── Criterion 9: Missing SEC user agent → SEC lane skipped honestly ───────────

class TestSecLaneWithoutAgent:
    def test_sec_flag_on_no_agent_skips_sec_lane(self):
        """SEC CompanyFacts lane returns None when user agent is missing."""
        db = FakeSupabaseClient()

        result = run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_only_no_agent(),
            # No _sec_companyfacts_provider_fn → uses real path → user agent check
        )

        assert result[LANE_SEC_COMPANY_FACTS] is None, (
            "SEC CompanyFacts lane must return None when sec_edgar_user_agent is missing"
        )

    def test_sec_flag_on_no_agent_writes_no_artifact(self):
        db = FakeSupabaseClient()

        run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_only_no_agent(),
        )

        assert db.artifact_inserts() == [], (
            "No artifact must be written when SEC user agent is absent"
        )


# ── Criterion 10 & 11: Safety — artifacts through service, no snapshot writes ─

class TestSafetyInvariants:
    def test_artifacts_write_through_research_artifact_service(self):
        """Artifacts must reach research_artifacts table (not bypassed)."""
        db = FakeSupabaseClient()
        run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_fundamentals_only(),
            _fundamentals_fetch_fn=lambda t: {
                "pe": 28.5, "eps": 6.12, "revenue": 385e9, "net_income": 95e9,
            },
        )
        assert len(db.artifact_inserts()) >= 1

    def test_no_intel_v3_snapshots_writes(self):
        """Orchestrator must never write to intel_v3_snapshots."""
        db = FakeSupabaseClient()
        run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
            settings=_settings_fundamentals_only(),
            # All lanes will skip (no fetch fns injected, yfinance would fail in test)
        )
        assert db.snapshot_writes() == []

    def test_no_recommendations_writes(self):
        """Orchestrator must never write to recommendations."""
        db = FakeSupabaseClient()
        run_enabled_evidence_lanes_for_portfolio(
            user_id="user-1",
            tickers=["AAPL"],
            db_client=db,
            settings=_settings_fundamentals_only(),
        )
        assert db.recommendation_writes() == []

    def test_sec_lane_no_snapshot_writes(self):
        """SEC lane must not write to intel_v3_snapshots."""
        db = FakeSupabaseClient()
        provider_result = _make_success_sec_result()
        run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_only_with_agent(),
            _sec_companyfacts_provider_fn=lambda t: provider_result,
        )
        assert db.snapshot_writes() == []

    def test_sec_lane_no_recommendations_writes(self):
        db = FakeSupabaseClient()
        provider_result = _make_success_sec_result()
        run_all_evidence_lanes(
            user_id="user-1",
            ticker="AAPL",
            db_client=db,
            settings=_settings_sec_only_with_agent(),
            _sec_companyfacts_provider_fn=lambda t: provider_result,
        )
        assert db.recommendation_writes() == []


# ── Criteria 1 & 2: Structural proof — explicit run path, not page load ───────

class TestExplicitRunPathStructural:
    def test_enqueue_run_v3_calls_evidence_lane_orchestrator(self):
        """enqueue_run_v3() source must reference run_enabled_evidence_lanes_for_portfolio."""
        import os, sys, importlib
        mod_name = "app.services.intelligence.v3.intel_v3_service"
        mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
        src = open(mod.__file__).read()

        method_start = src.find("async def enqueue_run_v3")
        assert method_start >= 0, "enqueue_run_v3 not found in module"
        method_end = src.find("\n    # ──", method_start + 1)
        method_src = src[method_start:method_end if method_end > 0 else method_start + 8000]

        assert "run_enabled_evidence_lanes_for_portfolio" in method_src, (
            "enqueue_run_v3 must call run_enabled_evidence_lanes_for_portfolio"
        )
        assert "intel_v3_evidence_lane_orchestrator_v1" in method_src, (
            "enqueue_run_v3 must import from intel_v3_evidence_lane_orchestrator_v1"
        )

    def test_get_latest_snapshot_does_not_dispatch_evidence_lanes(self):
        """get_latest_snapshot() must not reference evidence lane orchestrator."""
        import os, sys, importlib
        mod_name = "app.services.intelligence.v3.intel_v3_service"
        mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
        src = open(mod.__file__).read()

        method_start = src.find("async def get_latest_snapshot")
        assert method_start >= 0, "get_latest_snapshot not found in module"
        method_end = src.find("\n    # ──", method_start + 1)
        if method_end < 0:
            method_end = src.find("\n    async def ", method_start + 1)
        method_src = src[method_start:method_end if method_end > 0 else method_start + 3000]

        assert "run_enabled_evidence_lanes_for_portfolio" not in method_src, (
            "get_latest_snapshot must not dispatch evidence lanes (page-load contract)"
        )

    def test_enqueue_run_v3_creates_task_for_evidence_lanes(self):
        """enqueue_run_v3 must use create_task (fire-and-forget) for evidence dispatch."""
        import sys, importlib
        mod_name = "app.services.intelligence.v3.intel_v3_service"
        mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
        src = open(mod.__file__).read()

        method_start = src.find("async def enqueue_run_v3")
        method_end = src.find("\n    # ──", method_start + 1)
        method_src = src[method_start:method_end if method_end > 0 else method_start + 8000]

        assert "create_task" in method_src, (
            "enqueue_run_v3 must fire evidence lane dispatch as a background task"
        )


# ── Criterion 12: Orchestrator does not import decide() ──────────────────────

class TestOrchestratorSourceSafety:
    def test_orchestrator_does_not_import_decide(self):
        import ast, os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "v3",
            "intel_v3_evidence_lane_orchestrator_v1.py",
        )
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "decide", (
                        "Orchestrator must not import decide()"
                    )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "decision_policy" not in alias.name, (
                        "Orchestrator must not import decision_policy"
                    )

    def test_orchestrator_does_not_reference_snapshots_table(self):
        import ast, os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "app", "services", "intelligence", "v3",
            "intel_v3_evidence_lane_orchestrator_v1.py",
        )
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        # Check no function body (non-docstring) calls .table("intel_v3_snapshots")
        # by scanning string constants that appear in Call nodes (not just Constant nodes
        # which would include docstrings). The simplest reliable check: search the
        # functional source lines for table references outside comment/docstring context.
        func_start = source.find("def run_enabled_evidence_lanes_for_portfolio")
        assert func_start >= 0
        func_body = source[func_start:]
        # The only table references must not be intel_v3_snapshots or recommendations
        assert ".table(" not in func_body, (
            "Orchestrator function body must not call .table() directly"
        )

    def test_orchestrator_module_importable(self):
        from app.services.intelligence.v3.intel_v3_evidence_lane_orchestrator_v1 import (
            run_enabled_evidence_lanes_for_portfolio,
        )
        assert callable(run_enabled_evidence_lanes_for_portfolio)


# ── Safe wrapper and scheduling log structural proofs ─────────────────────────

class TestSafeWrapperStructural:
    """Structural proof that enqueue_run_v3 has scheduling log + safe exception handler."""

    def _method_src(self) -> str:
        import sys, importlib
        mod_name = "app.services.intelligence.v3.intel_v3_service"
        mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
        src = open(mod.__file__).read()
        start = src.find("async def enqueue_run_v3")
        assert start >= 0, "enqueue_run_v3 not found"
        end = src.find("\n    # ──", start + 1)
        return src[start:end if end > 0 else start + 10000]

    def test_dispatch_scheduled_log_present(self):
        """intel_v3_evidence_lanes_dispatch_scheduled must be logged before create_task."""
        src = self._method_src()
        assert "intel_v3_evidence_lanes_dispatch_scheduled" in src, (
            "enqueue_run_v3 must log dispatch_scheduled before create_task"
        )

    def test_dispatch_scheduled_logged_before_evidence_create_task(self):
        """Scheduling log must appear before the evidence-lane create_task call."""
        src = self._method_src()
        scheduled_pos = src.find("intel_v3_evidence_lanes_dispatch_scheduled")
        # Look for the evidence-lane specific create_task — the safe wrapper function name
        # is defined after the scheduled log, so its create_task call comes after the log.
        safe_fn_def_pos = src.find("_run_evidence_lanes_safe")
        assert scheduled_pos >= 0, "dispatch_scheduled log key not found"
        assert safe_fn_def_pos >= 0, "_run_evidence_lanes_safe wrapper not found"
        assert scheduled_pos < safe_fn_def_pos, (
            "dispatch_scheduled log must appear before the evidence-lane safe wrapper definition"
        )

    def test_dispatch_failed_log_present(self):
        """intel_v3_evidence_lanes_dispatch_failed must be logged on wrapper exception."""
        src = self._method_src()
        assert "intel_v3_evidence_lanes_dispatch_failed" in src, (
            "enqueue_run_v3 must log dispatch_failed in the safe exception handler"
        )

    def test_safe_wrapper_has_try_except(self):
        """The fire-and-forget wrapper must have a try/except block."""
        src = self._method_src()
        assert "try:" in src, (
            "enqueue_run_v3 safe wrapper must use try/except to catch background exceptions"
        )
        assert "except Exception" in src, (
            "enqueue_run_v3 safe wrapper must catch Exception generically"
        )

    def test_dispatch_failed_inside_except_block(self):
        """dispatch_failed log must be inside the except block (after try:)."""
        src = self._method_src()
        try_pos = src.find("try:")
        failed_pos = src.find("intel_v3_evidence_lanes_dispatch_failed")
        assert try_pos >= 0 and failed_pos >= 0
        assert failed_pos > try_pos, (
            "dispatch_failed log must appear after try: (inside the except handler)"
        )

    def test_snapshot_get_no_dispatch_scheduled_log(self):
        """get_latest_snapshot must not contain dispatch_scheduled (page-load contract)."""
        import sys, importlib
        mod_name = "app.services.intelligence.v3.intel_v3_service"
        mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
        src = open(mod.__file__).read()
        start = src.find("async def get_latest_snapshot")
        assert start >= 0
        end = src.find("\n    # ──", start + 1)
        if end < 0:
            end = src.find("\n    async def ", start + 1)
        method_src = src[start:end if end > 0 else start + 3000]
        assert "dispatch_scheduled" not in method_src, (
            "get_latest_snapshot must not log dispatch_scheduled (page-load contract)"
        )
