"""Stage 5H.2 — SEC CompanyFacts usability classification quality tests.

Acceptance criteria:
  1.  Real company with multiple periods of Revenue/NetIncome/EPS is NOT marked
      SUPPRESSED_CONTRADICTED merely because values differ across periods.
  2.  Same concept + same duration identity + same period + same unit, conflicting
      values → still flagged as contradiction (true contradiction preserved).
  3.  ETFs/funds/crypto/no-CIK/no-companyfacts → no placeholder artifact written.
  4.  ResearchArtifactServiceV1 still enriches valid SEC CompanyFacts artifacts
      with all four layers: credibility, contradiction, completeness, usability.
  5.  At least one valid SEC CompanyFacts artifact can reach USABLE or
      USABLE_WITH_LIMITATIONS when evidence is source-grounded and non-contradicted.
  6.  Quarterly and YTD facts from the same 10-Q filing (different start/end dates)
      are BOTH preserved and do NOT trigger false contradiction because the adapter
      encodes duration identity in the FactRecord.period string.
  7.  True same-duration conflicting values still trigger contradiction.
  8.  Existing Stage 5H run / dispatcher behavior unchanged for real companies.

Root cause fixed (Stage 5H.2 — duration-aware period identity):
  SEC XBRL 10-Q filings report the same metric (e.g., Revenue) for the same
  fy+fp (e.g., fy=2023, fp="Q3") twice — once as the 3-month quarterly figure
  (start=2023-04-02) and once as the 9-month YTD figure (start=2022-09-25).
  Both share the same accn, filed date, fy, and fp.

  Fix: The parser preserves period_start/period_end/frame from each XBRL entry
  and uses exact-identity deduplication (accn, fy, fp, start, end) — true
  duplicates are dropped, distinct durations are kept. The adapter then encodes
  duration in the FactRecord.period string:
    quarterly:  "2023-Q3:2023-04-02..2023-07-01"
    YTD:        "2023-Q3:2022-09-25..2023-07-01"
  Different period strings → different contradiction group keys → no false
  SUPPRESSED_CONTRADICTED for legitimate multi-duration XBRL filings.

No production Supabase access. All DB interactions use FakeSupabaseClient.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

import pytest

from app.services.intelligence.research_workers.sec_edgar_provider import (
    SecEdgarProviderResult,
    SecFilingRecord,
)
from app.services.intelligence.research_workers.sec_companyfacts_parser import (
    CompanyFactsParseResult,
    MetricObservation,
    parse_companyfacts,
)
from app.services.intelligence.research_workers.sec_companyfacts_adapter_v1 import (
    adapt_sec_companyfacts,
    build_sec_companyfacts_worker_output,
)
from app.services.intelligence.research_workers.contracts import WorkerInput
from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
    run_sec_companyfacts_evidence,
)
from app.services.intelligence.v3.contradiction_detector_v1 import detect_contradictions
from app.services.intelligence.v3.research_artifact_service_v1 import (
    ResearchArtifactServiceV1,
)
from app.config import Settings


# ── Fake DB client (shared with test_stage5h) ─────────────────────────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)


class FakeTableQuery:
    def __init__(self, state: _TableState, return_id: Optional[str] = None) -> None:
        self._state = state
        self._return_id = return_id or str(uuid.uuid4())
        self._row: Optional[dict] = None
        self._on_conflict: Optional[str] = None
        self._is_update = False
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

    def fact_inserts(self) -> list[dict]:
        return self.tables["research_artifact_facts"].inserts

    def snapshot_writes(self) -> list[dict]:
        return (
            self.tables["intel_v3_snapshots"].inserts
            + self.tables["intel_v3_snapshots"].upserts
        )


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _obs(
    tag: str = "Revenues",
    value: float = 100_000_000.0,
    unit: str = "USD",
    accession: str = "0000320193-23-000054",
    fiscal_year: int = 2023,
    fiscal_period: str = "FY",
    filed: str = "2023-11-03",
    form: str = "10-K",
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    frame: Optional[str] = None,
) -> MetricObservation:
    return MetricObservation(
        taxonomy="us-gaap",
        tag=tag,
        label=tag,
        value=value,
        unit=unit,
        form=form,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        filed=filed,
        accession_number=accession,
        period_start=period_start,
        period_end=period_end,
        frame=frame,
    )


def _filing(
    accession: str = "0000320193-23-000054",
    form_type: str = "10-K",
    filing_date: str = "2023-11-03",
) -> SecFilingRecord:
    return SecFilingRecord(
        form_type=form_type,
        filing_date=filing_date,
        accession_number=accession,
        report_date="2023-09-30",
        filing_url=f"https://www.sec.gov/Archives/edgar/data/320193/{accession.replace('-', '')}/",
    )


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


def _make_success_provider(
    observations: List[MetricObservation],
    filings: Optional[List[SecFilingRecord]] = None,
    tags_found: Optional[List[str]] = None,
) -> SecEdgarProviderResult:
    if filings is None:
        accns = list({o.accession_number for o in observations})
        filings = [_filing(accession=a) for a in accns]
    if tags_found is None:
        tags_found = sorted({o.tag for o in observations})
    cf = CompanyFactsParseResult(
        observations=observations,
        parse_status="success",
        tags_found=tags_found,
    )
    return SecEdgarProviderResult(
        ticker="AAPL",
        cik="0000320193",
        filings=filings,
        fetch_status="success",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        request_count=3,
        companyfacts_parse_result=cf,
    )


def _worker_input(ticker: str = "AAPL") -> WorkerInput:
    return WorkerInput(
        user_id="user-test",
        ticker=ticker,
        worker_run_id=str(uuid.uuid4()),
    )


# ── 1. Multi-period real company — NOT SUPPRESSED_CONTRADICTED ───────────────

class TestMultiPeriodNotContradicted:
    """A real company with different Revenue/NetIncome/EPS across periods must not
    be flagged SUPPRESSED_CONTRADICTED merely because the values differ."""

    def test_two_periods_different_values_not_contradicted(self):
        """Revenue FY2023 != Revenue FY2022 — different periods, different values.
        Contradiction detector must not flag this."""
        obs1 = _obs(tag="Revenues", value=383_285_000_000.0, fiscal_year=2023,
                    fiscal_period="FY", filed="2023-11-03", accession="ACC-K-23")
        obs2 = _obs(tag="Revenues", value=394_329_000_000.0, fiscal_year=2022,
                    fiscal_period="FY", filed="2022-10-28", accession="ACC-K-22")
        from app.services.intelligence.research_workers.contracts import FactRecord
        facts = [
            FactRecord(
                fact_kind="metric_observation",
                structured_payload={"metric_name": o.tag, "value": o.value, "unit": o.unit},
                period=f"{o.fiscal_year}-{o.fiscal_period}",
                as_of=o.filed,
            )
            for o in [obs1, obs2]
        ]
        assessment = detect_contradictions(facts)
        assert assessment.is_evaluable is True
        assert assessment.has_contradictions is False, (
            "Different fiscal periods must not be flagged as contradictions"
        )

    def test_quarterly_and_annual_revenue_not_contradicted(self):
        """Revenue Q3 2023 (quarterly) vs Revenue FY2023 (annual) — entirely different
        periods. Must not be flagged as contradiction."""
        obs_q3 = _obs(tag="Revenues", value=89_503_000_000.0, fiscal_year=2023,
                      fiscal_period="Q3", filed="2023-08-04", form="10-Q",
                      accession="ACC-Q-23")
        obs_fy = _obs(tag="Revenues", value=383_285_000_000.0, fiscal_year=2023,
                      fiscal_period="FY", filed="2023-11-03", form="10-K",
                      accession="ACC-K-23")
        provider = _make_success_provider([obs_q3, obs_fy])
        result = adapt_sec_companyfacts(provider, "AAPL", datetime.now(timezone.utc).isoformat())
        facts = result.facts
        assessment = detect_contradictions(facts)
        assert not assessment.has_contradictions, (
            "Q3 quarterly vs FY annual must not be a contradiction"
        )

    def test_multi_tag_multi_period_not_contradicted(self):
        """Multiple tags across multiple periods — a rich real-company artifact should
        reach USABLE or USABLE_WITH_LIMITATIONS, not SUPPRESSED_CONTRADICTED."""
        now_filed = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        obs_list = [
            _obs("Revenues", 383_285e6, "USD", "ACC-K-23", 2023, "FY", now_filed, "10-K"),
            _obs("Revenues", 394_329e6, "USD", "ACC-K-22", 2022, "FY", "2022-10-28", "10-K"),
            _obs("NetIncomeLoss", 96_995e6, "USD", "ACC-K-23", 2023, "FY", now_filed, "10-K"),
            _obs("NetIncomeLoss", 99_803e6, "USD", "ACC-K-22", 2022, "FY", "2022-10-28", "10-K"),
        ]
        filings = [
            _filing("ACC-K-23", "10-K", now_filed),
            _filing("ACC-K-22", "10-K", "2022-10-28"),
        ]
        provider = _make_success_provider(obs_list, filings=filings)
        fetched_at = datetime.now(timezone.utc).isoformat()
        result = adapt_sec_companyfacts(provider, "AAPL", fetched_at)
        assessment = detect_contradictions(result.facts)
        assert assessment.has_contradictions is False

    def test_multi_period_artifact_reaches_usable_label(self):
        """A valid SEC CompanyFacts artifact with fresh OFFICIAL sources and no
        contradictions must reach USABLE or USABLE_WITH_LIMITATIONS."""
        now_filed = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        obs_list = [
            _obs("Revenues", 383_285e6, "USD", "ACC-K", 2023, "FY", now_filed, "10-K"),
            _obs("NetIncomeLoss", 96_995e6, "USD", "ACC-K", 2023, "FY", now_filed, "10-K"),
            _obs("Assets", 352_583e6, "USD", "ACC-K", 2023, "FY", now_filed, "10-K"),
            _obs("Liabilities", 290_437e6, "USD", "ACC-K", 2023, "FY", now_filed, "10-K"),
        ]
        filings = [_filing("ACC-K", "10-K", now_filed)]
        provider = _make_success_provider(obs_list, filings=filings)
        db = FakeSupabaseClient()
        wi = _worker_input()
        artifact_id = run_sec_companyfacts_evidence(
            user_id="user-test",
            ticker="AAPL",
            db_client=db,
            settings=_settings_on(),
            _provider_fn=lambda t: provider,
        )
        assert artifact_id is not None
        payload = db.artifact_inserts()[0]["payload"]
        usability = payload.get("truth_usability_assessment", {})
        label = usability.get("usability_label", "")
        assert label in ("USABLE", "USABLE_WITH_LIMITATIONS"), (
            f"Expected USABLE or USABLE_WITH_LIMITATIONS, got {label!r}. "
            "A valid SEC CompanyFacts artifact must not be suppressed."
        )


# ── 2. Parser duration preservation: quarterly AND YTD both kept ──────────────

class TestParserDurationPreservation:
    """Verify the parser preserves BOTH quarterly and YTD observations from the
    same 10-Q filing (they have different start/end XBRL duration identity),
    and that the adapter's duration-aware period strings prevent false contradictions."""

    def _raw_json_with_quarterly_and_ytd(self, accn: str) -> dict:
        """Build a CompanyFacts JSON with Q3 quarterly AND Q3 YTD Revenue entries."""
        return {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {
                            "USD": [
                                # Q3 quarterly (3 months)
                                {
                                    "accn": accn,
                                    "fy": 2023,
                                    "fp": "Q3",
                                    "form": "10-Q",
                                    "filed": "2023-08-04",
                                    "start": "2023-04-02",
                                    "end": "2023-07-01",
                                    "val": 81_797_000_000.0,
                                },
                                # Q3 YTD (9 months): same fy, fp, accn, filed — DIFFERENT start+end
                                {
                                    "accn": accn,
                                    "fy": 2023,
                                    "fp": "Q3",
                                    "form": "10-Q",
                                    "filed": "2023-08-04",
                                    "start": "2022-09-25",  # older start = longer period
                                    "end": "2023-07-01",
                                    "val": 244_776_000_000.0,  # 9-month value, much larger
                                },
                            ]
                        },
                    }
                }
            }
        }

    def test_parser_preserves_both_quarterly_and_ytd_different_durations(self):
        """Both Q3 quarterly and Q3 YTD facts must be preserved — they have different
        XBRL duration identity (different start dates) and are distinct measurements."""
        accn = "ACC-Q3-2023"
        raw = self._raw_json_with_quarterly_and_ytd(accn)
        result = parse_companyfacts(raw, source_accessions=frozenset({accn}))
        assert result.is_success
        revs = [o for o in result.observations if o.tag == "Revenues"]
        assert len(revs) == 2, (
            f"Parser should preserve BOTH quarterly and YTD observations (got {len(revs)}). "
            "Different start/end dates = different XBRL duration dimensions."
        )
        values = {o.value for o in revs}
        assert 81_797_000_000.0 in values, "Quarterly value must be preserved"
        assert 244_776_000_000.0 in values, "YTD value must be preserved"
        # Each observation carries its own duration identity
        starts = {o.period_start for o in revs}
        assert len(starts) == 2, "Both distinct start dates must be preserved"
        assert "2023-04-02" in starts, "Quarterly start date must be present"
        assert "2022-09-25" in starts, "YTD start date must be present"

    def test_two_separate_filings_two_periods_both_kept(self):
        """Observations from DIFFERENT accessions for DIFFERENT periods are both kept."""
        accn_k = "ACC-10K"
        accn_q = "ACC-10Q"
        raw = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {
                            "USD": [
                                {
                                    "accn": accn_k, "fy": 2023, "fp": "FY",
                                    "form": "10-K", "filed": "2023-11-03",
                                    "start": "2022-09-25", "end": "2023-09-30",
                                    "val": 383_285_000_000.0,
                                },
                                {
                                    "accn": accn_q, "fy": 2023, "fp": "Q3",
                                    "form": "10-Q", "filed": "2023-08-04",
                                    "start": "2023-04-02", "end": "2023-07-01",
                                    "val": 81_797_000_000.0,
                                },
                            ]
                        },
                    }
                }
            }
        }
        result = parse_companyfacts(
            raw,
            source_accessions=frozenset({accn_k, accn_q}),
            max_periods_per_tag=2,
        )
        assert result.is_success
        revs = [o for o in result.observations if o.tag == "Revenues"]
        assert len(revs) == 2, (
            "Different accessions for different periods must both be kept"
        )
        values = {o.value for o in revs}
        assert 383_285_000_000.0 in values
        assert 81_797_000_000.0 in values

    def test_adapter_period_strings_differ_for_quarterly_vs_ytd(self):
        """After parse→adapt, Q3 quarterly and Q3 YTD must produce DIFFERENT period strings.
        Different period strings → different group keys → no false contradiction."""
        accn = "ACC-Q3-2023"
        raw = self._raw_json_with_quarterly_and_ytd(accn)
        parse_result = parse_companyfacts(raw, source_accessions=frozenset({accn}))
        assert len([o for o in parse_result.observations if o.tag == "Revenues"]) == 2

        provider = SecEdgarProviderResult(
            ticker="TESTCO",
            cik="0000111111",
            filings=[_filing(accn, "10-Q", "2023-08-04")],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=parse_result,
        )
        adapter_result = adapt_sec_companyfacts(
            provider, "TESTCO", datetime.now(timezone.utc).isoformat()
        )
        rev_facts = [
            f for f in adapter_result.facts
            if f.structured_payload.get("metric_name") == "Revenues"
        ]
        assert len(rev_facts) == 2
        periods = {f.period for f in rev_facts}
        assert len(periods) == 2, (
            "Quarterly and YTD must produce different period strings so the "
            "contradiction detector assigns them to different groups. "
            f"Got: {periods}"
        )
        # Both should contain the duration suffix (start..end)
        for p in periods:
            assert ".." in p, f"Period string should contain duration suffix: {p!r}"

    def test_contradiction_detector_not_triggered_by_quarterly_vs_ytd(self):
        """After parse→adapt, Q3 quarterly and Q3 YTD Revenue facts are BOTH present
        with different period strings — no false contradiction is triggered."""
        accn = "ACC-Q3"
        raw = self._raw_json_with_quarterly_and_ytd(accn)
        result = parse_companyfacts(raw, source_accessions=frozenset({accn}))
        assert result.is_success

        provider = SecEdgarProviderResult(
            ticker="TESTCO",
            cik="0000111111",
            filings=[_filing(accn, "10-Q", "2023-08-04")],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=result,
        )
        adapter_result = adapt_sec_companyfacts(
            provider, "TESTCO", datetime.now(timezone.utc).isoformat()
        )
        # Both quarterly and YTD facts are present
        rev_facts = [
            f for f in adapter_result.facts
            if f.structured_payload.get("metric_name") == "Revenues"
        ]
        assert len(rev_facts) == 2, (
            "Both quarterly and YTD facts must survive parse→adapt"
        )
        contradiction = detect_contradictions(adapter_result.facts)
        assert contradiction.has_contradictions is False, (
            "Quarterly vs YTD in same filing must not trigger contradiction: "
            "they have different duration-aware period strings"
        )

    def test_exact_duplicate_entry_deduplicated(self):
        """True exact-identity duplicate entries (same accn, fy, fp, start, end) are
        deduplicated to one — no contradiction spam from genuinely identical entries."""
        accn = "ACC-DUP"
        raw = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {
                            "USD": [
                                # Exact duplicate (same everything)
                                {
                                    "accn": accn, "fy": 2023, "fp": "FY",
                                    "form": "10-K", "filed": "2023-11-03",
                                    "start": "2022-09-25", "end": "2023-09-30",
                                    "val": 383_285_000_000.0,
                                },
                                {
                                    "accn": accn, "fy": 2023, "fp": "FY",
                                    "form": "10-K", "filed": "2023-11-03",
                                    "start": "2022-09-25", "end": "2023-09-30",
                                    "val": 383_285_000_000.0,  # identical
                                },
                            ]
                        },
                    }
                }
            }
        }
        result = parse_companyfacts(raw, source_accessions=frozenset({accn}))
        revs = [o for o in result.observations if o.tag == "Revenues"]
        assert len(revs) == 1, "Exact duplicates must be deduplicated to one"


# ── 3. True same-duration contradiction still detected ────────────────────────

class TestTrueSameFilingContradictionDetected:
    """Same concept + same duration + same period + same unit with conflicting values
    must still be flagged as a contradiction (genuine data integrity issue)."""

    def test_same_period_same_duration_conflicting_values_flagged(self):
        """Two FactRecords with the SAME duration-aware period string and conflicting
        values must still be detected as a true contradiction."""
        from app.services.intelligence.research_workers.contracts import FactRecord
        # Both have the same period (including duration suffix) and same as_of
        fact_a = FactRecord(
            fact_kind="metric_observation",
            structured_payload={
                "metric_name": "Revenues", "value": 383_285_000_000.0, "unit": "USD",
            },
            period="2023-FY:2022-09-25..2023-09-30",
            as_of="2023-11-03",
        )
        fact_b = FactRecord(
            fact_kind="metric_observation",
            structured_payload={
                "metric_name": "Revenues", "value": 999_999_000_000.0, "unit": "USD",
            },
            period="2023-FY:2022-09-25..2023-09-30",
            as_of="2023-11-03",
        )
        assessment = detect_contradictions([fact_a, fact_b])
        assert assessment.is_evaluable is True
        assert assessment.has_contradictions is True, (
            "Same concept + same duration-aware period + same as_of with "
            "conflicting values must be flagged as contradiction"
        )
        assert assessment.contradiction_count >= 1

    def test_same_duration_different_accessions_same_filed_date_contradicts(self):
        """Two observations from different filings (different accessions) but with
        the same metric, same duration (start+end), and same filed date produce
        the same period string → true contradiction detected through adapt chain."""
        obs_a = _obs(
            tag="Revenues", value=383_285_000_000.0, unit="USD",
            accession="ACC-ORIG", fiscal_year=2023, fiscal_period="FY",
            filed="2023-11-03", form="10-K",
            period_start="2022-09-25", period_end="2023-09-30",
        )
        obs_b = _obs(
            tag="Revenues", value=999_999_000_000.0, unit="USD",
            accession="ACC-CONFLICT",  # different accession
            fiscal_year=2023, fiscal_period="FY",
            filed="2023-11-03",  # same filed date → same as_of
            form="10-K",
            period_start="2022-09-25", period_end="2023-09-30",  # same duration
        )
        filings = [_filing("ACC-ORIG"), _filing("ACC-CONFLICT")]
        provider = _make_success_provider([obs_a, obs_b], filings=filings)
        adapter_result = adapt_sec_companyfacts(
            provider, "TESTCO", datetime.now(timezone.utc).isoformat()
        )
        assessment = detect_contradictions(adapter_result.facts)
        assert assessment.has_contradictions is True, (
            "Same metric + same duration + same filed date from different filings "
            "with conflicting values must be detected as a true contradiction"
        )


# ── 4. ETF/no-CIK/no-companyfacts → no placeholder artifact ──────────────────

class TestNoArtifactForNonCompany:
    """ETFs, funds, crypto, no-CIK, and no-companyfacts tickers must NOT produce
    a placeholder NOT_EVALUABLE artifact in the DB."""

    def _run(self, provider: SecEdgarProviderResult) -> tuple:
        db = FakeSupabaseClient()
        artifact_id = run_sec_companyfacts_evidence(
            user_id="user-test",
            ticker=provider.ticker,
            db_client=db,
            settings=_settings_on(),
            _provider_fn=lambda t: provider,
        )
        return artifact_id, db

    def test_no_cik_returns_none_no_artifact(self):
        provider = SecEdgarProviderResult(
            ticker="SPY", fetch_status="no_cik",
            error_message="ETF — no SEC CIK",
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        artifact_id, db = self._run(provider)
        assert artifact_id is None
        assert db.artifact_inserts() == []

    def test_timeout_returns_none_no_artifact(self):
        provider = SecEdgarProviderResult(
            ticker="BTC-USD", fetch_status="timeout",
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        artifact_id, db = self._run(provider)
        assert artifact_id is None
        assert db.artifact_inserts() == []

    def test_no_companyfacts_returns_none_no_artifact(self):
        provider = SecEdgarProviderResult(
            ticker="QQQ",
            cik="0001067839",
            filings=[_filing()],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=2,
            companyfacts_parse_result=None,  # companyfacts not fetched
        )
        artifact_id, db = self._run(provider)
        assert artifact_id is None
        assert db.artifact_inserts() == []

    def test_parse_no_facts_returns_none_no_artifact(self):
        cf = CompanyFactsParseResult(
            parse_status="no_facts",
            observations=[],
        )
        provider = SecEdgarProviderResult(
            ticker="GLD",
            cik="0001222333",
            filings=[_filing()],
            fetch_status="success",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=cf,
        )
        artifact_id, db = self._run(provider)
        assert artifact_id is None
        assert db.artifact_inserts() == []

    def test_parse_error_returns_none_no_artifact(self):
        cf = CompanyFactsParseResult(
            parse_status="error",
            error_message="malformed JSON",
        )
        provider = SecEdgarProviderResult(
            ticker="CRYPTO", fetch_status="success",
            cik="XXXX",
            filings=[_filing()],
            fetched_at=datetime.now(timezone.utc).isoformat(),
            request_count=3,
            companyfacts_parse_result=cf,
        )
        artifact_id, db = self._run(provider)
        assert artifact_id is None
        assert db.artifact_inserts() == []

    def test_no_snapshot_writes_for_skipped_tickers(self):
        provider = SecEdgarProviderResult(
            ticker="ETH-USD", fetch_status="no_cik",
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        _, db = self._run(provider)
        assert db.snapshot_writes() == []


# ── 5. ResearchArtifactServiceV1 enrichment still present for valid artifacts ─

class TestEnrichmentLayersForValidArtifact:
    """All four enrichment layers must be present in every written artifact."""

    def test_all_four_layers_present(self):
        obs = _obs(tag="Revenues", value=383_285e6, filed=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        provider = _make_success_provider([obs])
        db = FakeSupabaseClient()
        run_sec_companyfacts_evidence(
            user_id="u",
            ticker="AAPL",
            db_client=db,
            settings=_settings_on(),
            _provider_fn=lambda t: provider,
        )
        assert len(db.artifact_inserts()) == 1
        payload = db.artifact_inserts()[0]["payload"]
        assert "source_credibility_assessment" in payload
        assert "contradiction_assessment" in payload
        assert "evidence_completeness_assessment" in payload
        assert "truth_usability_assessment" in payload

    def test_safe_for_decision_never_set(self):
        obs = _obs()
        provider = _make_success_provider([obs])
        db = FakeSupabaseClient()
        run_sec_companyfacts_evidence(
            user_id="u", ticker="AAPL", db_client=db,
            settings=_settings_on(),
            _provider_fn=lambda t: provider,
        )
        for row in db.artifact_inserts():
            assert row.get("safe_for_decision") is not True

    def test_no_intel_v3_snapshots_writes(self):
        obs = _obs()
        provider = _make_success_provider([obs])
        db = FakeSupabaseClient()
        run_sec_companyfacts_evidence(
            user_id="u", ticker="AAPL", db_client=db,
            settings=_settings_on(),
            _provider_fn=lambda t: provider,
        )
        assert db.snapshot_writes() == []

    def test_credibility_assessment_recognizes_sec_edgar_source(self):
        """SEC EDGAR (official source) should produce non-UNKNOWN credibility."""
        obs = _obs(filed=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        provider = _make_success_provider([obs])
        db = FakeSupabaseClient()
        run_sec_companyfacts_evidence(
            user_id="u", ticker="AAPL", db_client=db,
            settings=_settings_on(),
            _provider_fn=lambda t: provider,
        )
        payload = db.artifact_inserts()[0]["payload"]
        cred = payload["source_credibility_assessment"]
        assert cred.get("strongest_authority_level") != "UNKNOWN", (
            "SEC EDGAR source should not be classified as UNKNOWN authority"
        )


# ── 6. Valid artifact can reach USABLE / USABLE_WITH_LIMITATIONS ──────────────

class TestUsabilityForValidCompany:
    """A valid SEC CompanyFacts artifact with OFFICIAL sources and no contradictions
    must be able to reach USABLE or USABLE_WITH_LIMITATIONS."""

    def test_single_period_recent_filing_usable_or_limited(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        obs = _obs(tag="Revenues", value=383_285e6, filed=today)
        provider = _make_success_provider([obs], filings=[_filing("ACC-K", "10-K", today)])
        db = FakeSupabaseClient()
        run_sec_companyfacts_evidence(
            user_id="u", ticker="AAPL", db_client=db,
            settings=_settings_on(),
            _provider_fn=lambda t: provider,
        )
        payload = db.artifact_inserts()[0]["payload"]
        usability = payload["truth_usability_assessment"]
        label = usability["usability_label"]
        assert label in ("USABLE", "USABLE_WITH_LIMITATIONS"), (
            f"Expected USABLE or USABLE_WITH_LIMITATIONS, got {label!r}"
        )
        assert usability["is_usable"] is True

    def test_multiple_tags_high_confidence_not_suppressed(self):
        """A HIGH confidence artifact (8+ tags) with no contradictions must not be
        SUPPRESSED_CONTRADICTED or SUPPRESSED_INCOMPLETE."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tags_and_values = [
            ("Revenues", 383_285e6, "USD"),
            ("NetIncomeLoss", 96_995e6, "USD"),
            ("OperatingIncomeLoss", 114_301e6, "USD"),
            ("Assets", 352_583e6, "USD"),
            ("Liabilities", 290_437e6, "USD"),
            ("StockholdersEquity", 62_146e6, "USD"),
            ("NetCashProvidedByUsedInOperatingActivities", 110_543e6, "USD"),
            ("EarningsPerShareBasic", 6.16, "USD/shares"),
        ]
        obs_list = [
            _obs(tag, val, unit, "ACC-K", 2023, "FY", today, "10-K")
            for tag, val, unit in tags_and_values
        ]
        provider = _make_success_provider(obs_list, filings=[_filing("ACC-K", "10-K", today)])
        db = FakeSupabaseClient()
        run_sec_companyfacts_evidence(
            user_id="u", ticker="AAPL", db_client=db,
            settings=_settings_on(),
            _provider_fn=lambda t: provider,
        )
        payload = db.artifact_inserts()[0]["payload"]
        usability = payload["truth_usability_assessment"]
        assert usability["usability_label"] not in (
            "SUPPRESSED_CONTRADICTED", "SUPPRESSED_INCOMPLETE", "NOT_EVALUABLE"
        ), f"High-confidence non-contradicted artifact got {usability['usability_label']!r}"

    def test_usability_is_usable_true_for_valid_artifact(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        obs = _obs(filed=today)
        provider = _make_success_provider([obs], filings=[_filing("ACC-K", "10-K", today)])
        db = FakeSupabaseClient()
        run_sec_companyfacts_evidence(
            user_id="u", ticker="AAPL", db_client=db,
            settings=_settings_on(),
            _provider_fn=lambda t: provider,
        )
        payload = db.artifact_inserts()[0]["payload"]
        usability = payload["truth_usability_assessment"]
        assert usability["is_usable"] is True
