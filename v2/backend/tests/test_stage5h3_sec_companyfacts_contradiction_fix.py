"""Stage 5H.3 — SEC CompanyFacts contradiction grouping and non-equity guard.

Acceptance criteria:
  1. SEC metric_observation facts from different filings / different periods /
     different durations / different units / different metrics do NOT collide
     in the contradiction detector and therefore do NOT produce false
     SUPPRESSED_CONTRADICTED labels.
  2. Genuine identity collisions (same metric + same unit + same fiscal_year +
     same fiscal_period + same period_start + same period_end + same filed)
     with conflicting values STILL produce a contradiction.
  3. Crypto/ETF/fund holdings are skipped before any SEC EDGAR lookup. No
     placeholder artifact is written and no fabricated SEC-company identity
     is inferred from a colliding ticker symbol.
  4. BTC and XRP are skipped by the conservative known-symbol fallback even
     when no holding_context metadata is supplied.
  5. Tickers with normal company identity continue to write usable artifacts.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

import pytest

from app.config import Settings
from app.services.intelligence.research_workers.contracts import (
    FactRecord,
    WorkerInput,
)
from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
    run_sec_companyfacts_evidence,
)
from app.services.intelligence.research_workers.sec_companyfacts_adapter_v1 import (
    adapt_sec_companyfacts,
)
from app.services.intelligence.research_workers.sec_companyfacts_parser import (
    CompanyFactsParseResult,
    MetricObservation,
    parse_companyfacts,
)
from app.services.intelligence.research_workers.sec_edgar_provider import (
    SecEdgarProviderResult,
    SecFilingRecord,
)
from app.services.intelligence.v3.contradiction_detector_v1 import (
    detect_contradictions,
)


# ── Minimal Fake Supabase (parity with stage5h2 tests) ────────────────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)


class _FakeQuery:
    def __init__(self, state: _TableState) -> None:
        self._state = state
        self._row: Optional[dict] = None
        self._is_update = False
        self._on_conflict: Optional[str] = None

    def insert(self, row): self._row = row; return self
    def update(self, row): self._row = row; self._is_update = True; return self
    def upsert(self, row, *, on_conflict="", ignore_duplicates=False):
        self._row = row; self._on_conflict = on_conflict; return self
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def neq(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        if self._row is not None and self._is_update:
            class _U: data = []
            return _U()
        if self._row is not None:
            rid = str(uuid.uuid4())
            if self._on_conflict is not None:
                self._state.upserts.append(self._row)
            else:
                self._state.inserts.append(self._row)
            class _R: data = [{"id": rid, **self._row}]
            return _R()
        class _E: data = []
        return _E()


class FakeSupabaseClient:
    def __init__(self) -> None:
        self.tables: dict[str, _TableState] = {}

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self.tables.setdefault(name, _TableState()))

    def artifact_inserts(self) -> list[dict]:
        return self.tables.get("research_artifacts", _TableState()).inserts


def _settings_on() -> Settings:
    return Settings(
        supabase_url="http://fake",
        supabase_anon_key="anon",
        supabase_service_role_key="svc",
        supabase_jwt_secret="secret",
        encryption_key="a" * 32,
        intel_v3_research_workers_enabled=True,
        intel_v3_sec_companyfacts_evidence_enabled=True,
        sec_edgar_user_agent="TestApp/1.0 test@example.com",
    )


def _filing(accn: str = "ACC-K", form: str = "10-K", date: str = "2023-11-03") -> SecFilingRecord:
    return SecFilingRecord(
        form_type=form,
        filing_date=date,
        accession_number=accn,
        report_date=date,
        filing_url=f"https://www.sec.gov/Archives/edgar/data/X/{accn}/",
    )


def _sec_fact(
    metric: str,
    value: float,
    unit: str = "USD",
    fy: Optional[int] = 2023,
    fp: Optional[str] = "FY",
    start: Optional[str] = "2022-09-25",
    end: Optional[str] = "2023-09-30",
    filed: str = "2023-11-03",
    accn: str = "ACC-K",
    frame: Optional[str] = None,
) -> FactRecord:
    """Build a FactRecord that mirrors what the SEC adapter emits."""
    return FactRecord(
        fact_kind="metric_observation",
        structured_payload={
            "metric_name": metric,
            "value": value,
            "unit": unit,
            "fiscal_year": fy,
            "fiscal_period": fp,
            "period_start": start,
            "period_end": end,
            "frame": frame,
            "filed": filed,
            "accession_number": accn,
            "provider": "sec_edgar",
        },
        period=f"{fy}-{fp}:{start}..{end}" if start and end else f"{fy}-{fp}",
        as_of=filed,
    )


# ── 1. SEC-specific group key prevents false contradictions ───────────────────

class TestSecSpecificGroupingNoFalseContradictions:
    """The SEC-specific contradiction group key must include unit, fy, fp,
    period_start, period_end, frame and filed so that legitimate distinct
    XBRL observations never collide."""

    def test_different_metrics_never_conflict(self):
        facts = [
            _sec_fact("Revenues", 383_285e6),
            _sec_fact("NetIncomeLoss", 96_995e6),
        ]
        a = detect_contradictions(facts)
        assert a.has_contradictions is False

    def test_different_units_never_conflict(self):
        facts = [
            _sec_fact("EarningsPerShareBasic", 6.16, unit="USD/shares"),
            _sec_fact("EarningsPerShareBasic", 6_000_000.0, unit="USD"),
        ]
        a = detect_contradictions(facts)
        assert a.has_contradictions is False

    def test_different_durations_same_fy_fp_never_conflict(self):
        # Q3 quarterly (3 months) and Q3 YTD (9 months) — same fy, same fp,
        # same filed, same accn, but different XBRL durations.
        facts = [
            _sec_fact("Revenues", 81_797e6, fp="Q3",
                      start="2023-04-02", end="2023-07-01",
                      filed="2023-08-04", accn="ACC-Q3"),
            _sec_fact("Revenues", 244_776e6, fp="Q3",
                      start="2022-09-25", end="2023-07-01",
                      filed="2023-08-04", accn="ACC-Q3"),
        ]
        a = detect_contradictions(facts)
        assert a.has_contradictions is False

    def test_different_filings_different_periods_never_conflict(self):
        facts = [
            _sec_fact("Revenues", 383_285e6, fy=2023, fp="FY",
                      start="2022-09-25", end="2023-09-30",
                      filed="2023-11-03", accn="ACC-K-23"),
            _sec_fact("Revenues", 394_329e6, fy=2022, fp="FY",
                      start="2021-09-25", end="2022-09-24",
                      filed="2022-10-28", accn="ACC-K-22"),
        ]
        a = detect_contradictions(facts)
        assert a.has_contradictions is False

    def test_multi_metric_multi_period_no_false_contradictions(self):
        # Mimics the runtime PR #378 production payload shape: several tags,
        # multiple periods/durations across two filings. None should conflict.
        facts = [
            _sec_fact("Revenues", 383_285e6, fy=2023, fp="FY",
                      start="2022-09-25", end="2023-09-30", filed="2023-11-03", accn="K23"),
            _sec_fact("Revenues", 394_329e6, fy=2022, fp="FY",
                      start="2021-09-25", end="2022-09-24", filed="2022-10-28", accn="K22"),
            _sec_fact("NetIncomeLoss", 96_995e6, fy=2023, fp="FY",
                      start="2022-09-25", end="2023-09-30", filed="2023-11-03", accn="K23"),
            _sec_fact("NetIncomeLoss", 99_803e6, fy=2022, fp="FY",
                      start="2021-09-25", end="2022-09-24", filed="2022-10-28", accn="K22"),
            _sec_fact("Assets", 352_583e6, fy=2023, fp="FY",
                      start=None, end="2023-09-30", filed="2023-11-03", accn="K23"),
            _sec_fact("Assets", 352_755e6, fy=2022, fp="FY",
                      start=None, end="2022-09-24", filed="2022-10-28", accn="K22"),
            _sec_fact("EarningsPerShareBasic", 6.16, unit="USD/shares",
                      fy=2023, fp="FY", start="2022-09-25", end="2023-09-30",
                      filed="2023-11-03", accn="K23"),
        ]
        a = detect_contradictions(facts)
        assert a.has_contradictions is False, (
            f"Expected zero contradictions, got {a.contradiction_count}: "
            f"{a.contradiction_groups}"
        )


# ── 2. Genuine same-identity conflict is still detected ───────────────────────

class TestGenuineConflictStillDetected:
    def test_same_identity_conflicting_values_flagged(self):
        # Same metric, same unit, same fy/fp/start/end/filed; values differ.
        # Two different filings, but identity is otherwise identical → true conflict.
        facts = [
            _sec_fact("Revenues", 383_285e6, fy=2023, fp="FY",
                      start="2022-09-25", end="2023-09-30",
                      filed="2023-11-03", accn="ACC-A"),
            _sec_fact("Revenues", 999_999e6, fy=2023, fp="FY",
                      start="2022-09-25", end="2023-09-30",
                      filed="2023-11-03", accn="ACC-B"),
        ]
        a = detect_contradictions(facts)
        assert a.is_evaluable is True
        assert a.has_contradictions is True
        assert a.contradiction_count >= 1

    def test_same_identity_values_within_tolerance_not_flagged(self):
        # Values within the 1% relative tolerance — not a contradiction.
        facts = [
            _sec_fact("Revenues", 383_285e6),
            _sec_fact("Revenues", 383_285_000_000.0 * 1.005,  # +0.5%
                      accn="ACC-OTHER"),
        ]
        a = detect_contradictions(facts)
        assert a.has_contradictions is False


# ── 3. Non-SEC facts unaffected (no behavior regression) ──────────────────────

class TestNonSecFactsUnaffected:
    def test_yfinance_style_metric_observation_uses_generic_grouping(self):
        # Non-SEC metric_observation facts must still group by the generic
        # (claim_key, fact_kind, period, as_of) key and detect contradictions.
        a = FactRecord(
            fact_kind="metric_observation",
            structured_payload={"metric_name": "trailingPE", "value": 25.0},
            period="2023-Q3", as_of="2023-09-30",
        )
        b = FactRecord(
            fact_kind="metric_observation",
            structured_payload={"metric_name": "trailingPE", "value": 99.0},
            period="2023-Q3", as_of="2023-09-30",
        )
        assessment = detect_contradictions([a, b])
        assert assessment.has_contradictions is True


# ── 4. Non-equity ticker guard skips SEC EDGAR lookup ─────────────────────────

class TestNonEquityGuard:
    """Crypto/ETF/fund holdings must be skipped before any SEC ticker lookup.
    The provider function must never be called for skipped tickers."""

    def _run(self, ticker: str, holding_context: Optional[dict] = None,
             provider_called: Optional[list] = None) -> tuple:
        db = FakeSupabaseClient()

        def _prov(t: str):
            if provider_called is not None:
                provider_called.append(t)
            raise AssertionError(
                f"provider must not be called for skipped ticker {t!r}"
            )

        artifact_id = run_sec_companyfacts_evidence(
            user_id="u",
            ticker=ticker,
            db_client=db,
            settings=_settings_on(),
            holding_context=holding_context,
            _provider_fn=_prov,
        )
        return artifact_id, db

    def test_btc_skipped_by_known_symbol_fallback(self):
        called: list = []
        artifact_id, db = self._run("BTC", provider_called=called)
        assert artifact_id is None
        assert db.artifact_inserts() == []
        assert called == []

    def test_xrp_skipped_by_known_symbol_fallback(self):
        called: list = []
        artifact_id, db = self._run("XRP", provider_called=called)
        assert artifact_id is None
        assert db.artifact_inserts() == []
        assert called == []

    def test_etf_skipped_by_holding_context_category(self):
        called: list = []
        artifact_id, db = self._run(
            "SOMEETF",
            holding_context={"category": "ETF"},
            provider_called=called,
        )
        assert artifact_id is None
        assert called == []

    def test_crypto_skipped_by_holding_context_asset_type(self):
        called: list = []
        artifact_id, db = self._run(
            "RANDOMCOIN",
            holding_context={"asset_type": "Crypto"},
            provider_called=called,
        )
        assert artifact_id is None
        assert called == []

    def test_known_etf_symbol_skipped_without_context(self):
        # SPY/QQQ/etc. are in KNOWN_FUND_OR_ETF_TICKERS
        called: list = []
        artifact_id, db = self._run("SPY", provider_called=called)
        assert artifact_id is None
        assert called == []

    def test_log_emitted_for_skip(self, caplog):
        caplog.set_level(logging.INFO)
        self._run("BTC")
        msgs = [r.getMessage() for r in caplog.records]
        assert any("sec_companyfacts_skip_non_equity" in m and "ticker=BTC" in m
                   for m in msgs), f"missing skip log; got: {msgs}"


# ── 5. Eligible company tickers still produce an artifact ─────────────────────

class TestEligibleTickerStillRuns:
    def test_aapl_eligible_writes_artifact(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        obs = MetricObservation(
            taxonomy="us-gaap", tag="Revenues", label="Revenues",
            value=383_285e6, unit="USD", form="10-K",
            fiscal_year=2023, fiscal_period="FY",
            filed=today, accession_number="ACC-K",
            period_start="2022-09-25", period_end=today,
        )
        cf = CompanyFactsParseResult(
            observations=[obs], parse_status="success", tags_found=["Revenues"],
        )
        provider = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[_filing("ACC-K", "10-K", today)],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=cf,
        )
        db = FakeSupabaseClient()
        artifact_id = run_sec_companyfacts_evidence(
            user_id="u", ticker="AAPL", db_client=db,
            settings=_settings_on(),
            holding_context={"category": "Core"},
            _provider_fn=lambda t: provider,
        )
        assert artifact_id is not None
        payload = db.artifact_inserts()[0]["payload"]
        usability = payload["truth_usability_assessment"]
        assert usability["usability_label"] in ("USABLE", "USABLE_WITH_LIMITATIONS")

    def test_usability_summary_log_emitted(self, caplog):
        caplog.set_level(logging.INFO)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        obs = MetricObservation(
            taxonomy="us-gaap", tag="Revenues", label="Revenues",
            value=383_285e6, unit="USD", form="10-K",
            fiscal_year=2023, fiscal_period="FY",
            filed=today, accession_number="ACC-K",
            period_start="2022-09-25", period_end=today,
        )
        cf = CompanyFactsParseResult(
            observations=[obs], parse_status="success", tags_found=["Revenues"],
        )
        provider = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[_filing("ACC-K", "10-K", today)],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=cf,
        )
        db = FakeSupabaseClient()
        run_sec_companyfacts_evidence(
            user_id="u", ticker="AAPL", db_client=db,
            settings=_settings_on(),
            _provider_fn=lambda t: provider,
        )
        msgs = [r.getMessage() for r in caplog.records]
        assert any("sec_companyfacts_usability_summary" in m and "ticker=AAPL" in m
                   for m in msgs), f"missing usability summary log; got: {msgs}"


# ── 5b. Orchestrator plumbs holding_context_by_ticker → runner ────────────────

class TestOrchestratorPlumbsHoldingContext:
    """Stage 5H.3 patch: explicit-run orchestrator must pass holding_context per
    ticker into run_evidence_lanes_for_ticker so the SEC non-equity guard can
    decide eligibility from actual position metadata, not just symbol fallback."""

    def test_holding_context_passed_per_ticker(self, monkeypatch):
        from app.services.intelligence.v3.intel_v3_evidence_lane_orchestrator_v1 import (
            run_enabled_evidence_lanes_for_portfolio,
        )
        captured: dict[str, Any] = {}

        def fake_runner(user_id, ticker, db_client, parent_intel_run_id=None,
                        holding_context=None, settings=None, **kwargs):
            captured[ticker] = holding_context
            return {}

        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", fake_runner)

        ctx = {
            "AAPL": {"category": "Core"},
            "SCHD": {"category": "ETF"},
            "BTC": {"category": "Crypto"},
        }
        run_enabled_evidence_lanes_for_portfolio(
            user_id="u",
            tickers=["AAPL", "SCHD", "BTC"],
            db_client=FakeSupabaseClient(),
            settings=_settings_on(),
            holding_context_by_ticker=ctx,
        )
        assert captured["AAPL"] == {"category": "Core"}
        assert captured["SCHD"] == {"category": "ETF"}
        assert captured["BTC"] == {"category": "Crypto"}

    def test_missing_context_passes_none(self, monkeypatch):
        from app.services.intelligence.v3.intel_v3_evidence_lane_orchestrator_v1 import (
            run_enabled_evidence_lanes_for_portfolio,
        )
        captured: dict[str, Any] = {}

        def fake_runner(user_id, ticker, db_client, parent_intel_run_id=None,
                        holding_context=None, settings=None, **kwargs):
            captured[ticker] = holding_context
            return {}

        import app.services.intelligence.research_workers.runner as runner_mod
        monkeypatch.setattr(runner_mod, "run_evidence_lanes_for_ticker", fake_runner)

        run_enabled_evidence_lanes_for_portfolio(
            user_id="u", tickers=["AAPL"], db_client=FakeSupabaseClient(),
            settings=_settings_on(),
        )
        assert captured["AAPL"] is None


# ── 5c. Metadata-only crypto / fund tickers (not in static fallback) skipped ──

class TestUnknownSymbolWithMetadataSkipped:
    """Tickers NOT in KNOWN_CRYPTO_TICKERS or KNOWN_FUND_OR_ETF_TICKERS must
    still be skipped when holding_context metadata says Crypto/ETF."""

    def test_unknown_crypto_symbol_with_metadata_skipped(self):
        called: list = []
        db = FakeSupabaseClient()

        def _prov(t):
            called.append(t)
            raise AssertionError("provider must not be called")

        artifact_id = run_sec_companyfacts_evidence(
            user_id="u", ticker="NOTLISTEDCOIN", db_client=db,
            settings=_settings_on(),
            holding_context={"category": "Crypto"},
            _provider_fn=_prov,
        )
        assert artifact_id is None
        assert called == []

    def test_unknown_etf_symbol_with_metadata_skipped(self):
        called: list = []
        db = FakeSupabaseClient()

        def _prov(t):
            called.append(t)
            raise AssertionError("provider must not be called")

        artifact_id = run_sec_companyfacts_evidence(
            user_id="u", ticker="NOTLISTEDETF", db_client=db,
            settings=_settings_on(),
            holding_context={"asset_type": "ETF"},
            _provider_fn=_prov,
        )
        assert artifact_id is None
        assert called == []

    def test_skip_source_log_marks_metadata(self, caplog):
        caplog.set_level(logging.INFO)
        db = FakeSupabaseClient()
        run_sec_companyfacts_evidence(
            user_id="u", ticker="NOTLISTEDCOIN", db_client=db,
            settings=_settings_on(),
            holding_context={"category": "Crypto"},
            _provider_fn=lambda t: (_ for _ in ()).throw(AssertionError("never")),
        )
        msgs = [r.getMessage() for r in caplog.records]
        assert any("sec_companyfacts_skip_non_equity" in m
                   and "skip_source=metadata" in m for m in msgs), msgs

    def test_skip_source_log_marks_symbol_fallback(self, caplog):
        caplog.set_level(logging.INFO)
        db = FakeSupabaseClient()
        run_sec_companyfacts_evidence(
            user_id="u", ticker="BTC", db_client=db,
            settings=_settings_on(),
            holding_context=None,
            _provider_fn=lambda t: (_ for _ in ()).throw(AssertionError("never")),
        )
        msgs = [r.getMessage() for r in caplog.records]
        assert any("sec_companyfacts_skip_non_equity" in m
                   and "skip_source=symbol_fallback" in m for m in msgs), msgs


# ── 6. Parse → adapt → detect on real-shaped XBRL payload ─────────────────────

class TestRuntimeShapeParseAdaptDetect:
    """Reproduces a runtime-shaped multi-metric/multi-period XBRL payload all
    the way through parse → adapt → contradiction detection. The result must
    be ZERO contradictions (matches the PR #378 false-positive runtime case)."""

    def test_multi_metric_multi_period_xbrl_no_false_contradictions(self):
        accn_k23 = "K23"
        accn_q23 = "Q23"
        raw = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {"USD": [
                            {"accn": accn_k23, "fy": 2023, "fp": "FY",
                             "form": "10-K", "filed": "2023-11-03",
                             "start": "2022-09-25", "end": "2023-09-30",
                             "val": 383_285_000_000.0},
                            {"accn": accn_q23, "fy": 2023, "fp": "Q3",
                             "form": "10-Q", "filed": "2023-08-04",
                             "start": "2023-04-02", "end": "2023-07-01",
                             "val": 81_797_000_000.0},
                        ]},
                    },
                    "NetIncomeLoss": {
                        "label": "Net Income",
                        "units": {"USD": [
                            {"accn": accn_k23, "fy": 2023, "fp": "FY",
                             "form": "10-K", "filed": "2023-11-03",
                             "start": "2022-09-25", "end": "2023-09-30",
                             "val": 96_995_000_000.0},
                        ]},
                    },
                    "Assets": {
                        "label": "Assets",
                        "units": {"USD": [
                            {"accn": accn_k23, "fy": 2023, "fp": "FY",
                             "form": "10-K", "filed": "2023-11-03",
                             "end": "2023-09-30",
                             "val": 352_583_000_000.0},
                        ]},
                    },
                    "EarningsPerShareBasic": {
                        "label": "EPS Basic",
                        "units": {"USD/shares": [
                            {"accn": accn_k23, "fy": 2023, "fp": "FY",
                             "form": "10-K", "filed": "2023-11-03",
                             "start": "2022-09-25", "end": "2023-09-30",
                             "val": 6.16},
                        ]},
                    },
                }
            }
        }
        parse_result = parse_companyfacts(
            raw, source_accessions=frozenset({accn_k23, accn_q23}),
        )
        assert parse_result.is_success

        provider = SecEdgarProviderResult(
            ticker="AAPL", cik="0000320193",
            filings=[_filing(accn_k23, "10-K", "2023-11-03"),
                     _filing(accn_q23, "10-Q", "2023-08-04")],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=parse_result,
        )
        adapter_result = adapt_sec_companyfacts(
            provider, "AAPL", datetime.now(timezone.utc).isoformat(),
        )
        assessment = detect_contradictions(adapter_result.facts)
        assert assessment.is_evaluable is True
        assert assessment.has_contradictions is False, (
            "Multi-metric/multi-period real-shape XBRL payload must not produce "
            f"false contradictions; got {assessment.contradiction_count} groups: "
            f"{assessment.contradiction_groups}"
        )
