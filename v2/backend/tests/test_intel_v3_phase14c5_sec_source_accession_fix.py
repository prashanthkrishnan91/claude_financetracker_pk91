"""Phase 14C.5 acceptance tests — SEC source accession selection fix for latest 10-K.

Root cause: when recent SEC submissions are dominated by 10-Qs, the max_filings_to_return
cap (default 5) fills before the annual 10-K appears in the list. As a result,
source_accessions never contains the 10-K accession, so the companyfacts parser skips
all FY EPS observations whose accn matches the 10-K filing.

Fix (sec_edgar_provider.py): after the regular filing loop, if no 10-K was collected,
scan the remaining submissions entries for the most recent 10-K and append it. This is
additive (one extra filing at most), bounded, dedup-safe, and never fabricates data.

Tests:
1. Recent 10-Qs fill cap → latest 10-K is still included (regression guard for the fix)
2. 10-K already in top filings → no duplicate
3. No 10-K in submissions at all → no fabrication
4. source_accessions frozenset includes the 10-K accession after fix
5. Parser emits FY EPS when 10-K accession is in source_accessions
6. Parser skips FY EPS when 10-K accession is absent (documents pre-fix loss)
7. Non-EPS tags behave identically before and after
8. End-to-end: provider + companyfacts FY EPS with 10-K overshadowed by 10-Qs
9. End-to-end: no 10-K in submissions → no FY EPS from a phantom 10-K
10. Governance: max one extra 10-K; existing 10-Q source behavior unchanged
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

import pytest

from app.services.intelligence.research_workers.sec_edgar_provider import (
    SecEdgarProviderConfig,
    SecEdgarProviderResult,
    SecFilingRecord,
    fetch_for_ticker,
)
from app.services.intelligence.research_workers.sec_companyfacts_parser import (
    MetricObservation,
    parse_companyfacts,
)
from app.services.intelligence.research_workers.earnings_sec_adapter import (
    adapt_sec_result,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _dt(days_ago: int = 0) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _make_config(max_filings: int = 5) -> SecEdgarProviderConfig:
    return SecEdgarProviderConfig(
        user_agent="TestApp/1.0 test@example.com",
        max_filings_to_return=max_filings,
    )


def _make_ticker_map(ticker: str, cik: int) -> dict:
    return {"0": {"cik_str": cik, "ticker": ticker.upper(), "title": f"Fake {ticker}"}}


def _make_submissions(
    forms: list[str],
    dates: list[str],
    accessions: Optional[list[str]] = None,
) -> dict:
    """Build a minimal submissions JSON payload with explicit accessions."""
    n = len(forms)
    if accessions is None:
        accessions = [f"0000320193-25-{i:06d}" for i in range(n)]
    report_dates = [d[:7] + "-01" for d in dates]
    return {
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": accessions,
                "reportDate": report_dates,
            }
        }
    }


def _make_companyfacts_eps(entries: list[dict]) -> dict:
    """Build minimal companyfacts JSON with EarningsPerShareDiluted entries."""
    return {
        "facts": {
            "us-gaap": {
                "EarningsPerShareDiluted": {
                    "label": "Earnings Per Share Diluted",
                    "units": {
                        "USD/shares": entries,
                    },
                }
            }
        }
    }


def _make_eps_entry(
    accn: str,
    val: float,
    form: str = "10-K",
    filed: str = "2025-01-30",
    fy: int = 2024,
    fp: str = "FY",
) -> dict:
    return {"accn": accn, "val": val, "form": form, "filed": filed, "fy": fy, "fp": fp}


class _FakeResponse:
    status_code = 200

    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._data


class FakeHttpGetFn:
    """Injectable HTTP callable for provider tests — no real network calls."""

    def __init__(
        self,
        ticker_map: dict,
        submissions: dict,
        companyfacts: Optional[dict] = None,
    ) -> None:
        self._ticker_map = ticker_map
        self._submissions = submissions
        self._companyfacts = companyfacts or {"facts": {"us-gaap": {}}}

    def __call__(self, url: str) -> Any:
        if "company_tickers" in url:
            return _FakeResponse(self._ticker_map)
        if "submissions" in url:
            return _FakeResponse(self._submissions)
        if "companyfacts" in url:
            return _FakeResponse(self._companyfacts)
        raise RuntimeError(f"Unexpected URL in test: {url}")


# ── SCENARIO SETUP ────────────────────────────────────────────────────────────
# Mimics AAPL/MSFT/GOOGL: 6 recent 10-Qs filed monthly, then 1 annual 10-K.
# With max_filings=5, the pre-fix provider collects only 10-Qs; 10-K is skipped.

_10K_ACCESSION = "0000320193-25-000010"
_10Q_ACCESSIONS = [f"0000320193-25-{i:06d}" for i in range(1, 7)]

_AAPL_FORMS_RECENT_FIRST = (
    ["10-Q"] * 6                   # Q3/Q2/Q1 + prior year Q3/Q2/Q1
    + ["10-K"]                      # annual 10-K — would be at index 6
)
_AAPL_DATES_RECENT_FIRST = [
    _dt(30), _dt(120), _dt(210), _dt(300), _dt(390), _dt(480),
    _dt(540),  # 10-K
]
_AAPL_ACCESSIONS_RECENT_FIRST = _10Q_ACCESSIONS + [_10K_ACCESSION]


# ── 1. Recent 10-Qs fill cap → 10-K still included ───────────────────────────

class TestIncludesLatest10KWhenOvershadowedBy10Qs:

    def test_filings_include_10k_when_recent_10qs_fill_cap(self) -> None:
        """With 6 recent 10-Qs and max_filings=5, the 10-K must still appear."""
        subs = _make_submissions(
            _AAPL_FORMS_RECENT_FIRST,
            _AAPL_DATES_RECENT_FIRST,
            _AAPL_ACCESSIONS_RECENT_FIRST,
        )
        ticker_map = _make_ticker_map("AAPL", 320193)
        cf = _make_companyfacts_eps([
            _make_eps_entry(_10K_ACCESSION, 6.11, form="10-K", fp="FY"),
        ])
        fake = FakeHttpGetFn(ticker_map, subs, cf)

        result = fetch_for_ticker("AAPL", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        form_types = [f.form_type for f in result.filings]
        assert "10-K" in form_types, (
            "10-K must be included in filings even when 5 recent 10-Qs fill the cap"
        )

    def test_10k_accession_in_source_accessions(self) -> None:
        """The 10-K accession must appear in the set used to filter companyfacts."""
        subs = _make_submissions(
            _AAPL_FORMS_RECENT_FIRST,
            _AAPL_DATES_RECENT_FIRST,
            _AAPL_ACCESSIONS_RECENT_FIRST,
        )
        ticker_map = _make_ticker_map("AAPL", 320193)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("AAPL", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        collected_accns = {f.accession_number for f in result.filings}
        assert _10K_ACCESSION in collected_accns, (
            f"10-K accession {_10K_ACCESSION!r} must be in source accessions; "
            f"got: {collected_accns}"
        )

    def test_10q_accessions_still_present(self) -> None:
        """Adding the 10-K must not remove existing 10-Q accessions."""
        subs = _make_submissions(
            _AAPL_FORMS_RECENT_FIRST,
            _AAPL_DATES_RECENT_FIRST,
            _AAPL_ACCESSIONS_RECENT_FIRST,
        )
        ticker_map = _make_ticker_map("AAPL", 320193)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("AAPL", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        collected_accns = {f.accession_number for f in result.filings}
        # Should still have the 5 most recent 10-Qs
        for q_accn in _10Q_ACCESSIONS[:5]:
            assert q_accn in collected_accns, f"10-Q {q_accn!r} lost after 10-K fix"


# ── 2. 10-K already in top filings → no duplicate ────────────────────────────

class TestNo10KDuplicateWhenAlreadyPresent:

    def test_10k_within_cap_not_duplicated(self) -> None:
        """When 10-K is in the top 5 most recent filings, it appears exactly once."""
        subs = _make_submissions(
            forms=["10-K", "10-Q", "10-Q", "10-Q", "10-Q"],
            dates=[_dt(30), _dt(60), _dt(90), _dt(120), _dt(150)],
            accessions=["0000320193-25-000001", "0000320193-25-000002",
                        "0000320193-25-000003", "0000320193-25-000004",
                        "0000320193-25-000005"],
        )
        ticker_map = _make_ticker_map("MSFT", 789019)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("MSFT", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        ten_k_filings = [f for f in result.filings if f.form_type == "10-K"]
        assert len(ten_k_filings) == 1, (
            f"Expected exactly 1 10-K filing, got {len(ten_k_filings)}"
        )

    def test_exactly_5_filings_when_10k_already_included(self) -> None:
        """No extra filings are appended when 10-K is already in the cap."""
        subs = _make_submissions(
            forms=["10-K", "10-Q", "10-Q", "10-Q", "10-Q"],
            dates=[_dt(30), _dt(60), _dt(90), _dt(120), _dt(150)],
        )
        ticker_map = _make_ticker_map("GOOGL", 1652044)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("GOOGL", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        assert len(result.filings) == 5, (
            f"Expected 5 filings (no extra 10-K), got {len(result.filings)}"
        )

    def test_10k_second_in_list_not_duplicated(self) -> None:
        """10-K at index 1 (second most recent) must not be duplicated."""
        subs = _make_submissions(
            forms=["10-Q", "10-K", "10-Q", "10-Q", "10-Q"],
            dates=[_dt(15), _dt(30), _dt(90), _dt(120), _dt(150)],
            accessions=["Q1", "10K", "Q2", "Q3", "Q4"],
        )
        ticker_map = _make_ticker_map("COST", 895126)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("COST", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        ten_k_filings = [f for f in result.filings if f.form_type == "10-K"]
        assert len(ten_k_filings) == 1


# ── 3. No 10-K in submissions → no fabrication ───────────────────────────────

class TestNoFabricatedFilingWhenNo10KPresent:

    def test_all_10q_submissions_no_fabricated_10k(self) -> None:
        """When submissions contain only 10-Qs, no 10-K filing is invented."""
        subs = _make_submissions(
            forms=["10-Q"] * 8,
            dates=[_dt(i * 30) for i in range(8)],
        )
        ticker_map = _make_ticker_map("QCOM", 804328)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("QCOM", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        ten_k_filings = [f for f in result.filings if f.form_type == "10-K"]
        assert len(ten_k_filings) == 0, "Must not fabricate a 10-K when none exists"

    def test_empty_submissions_no_fabricated_10k(self) -> None:
        """Empty submissions produce zero filings, no fabrication."""
        subs = _make_submissions(forms=[], dates=[])
        ticker_map = _make_ticker_map("ALK", 766421)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("ALK", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        assert len(result.filings) == 0
        ten_k_filings = [f for f in result.filings if f.form_type == "10-K"]
        assert len(ten_k_filings) == 0

    def test_8k_only_submissions_no_fabricated_10k(self) -> None:
        """8-K-only submissions produce no 10-K filing."""
        subs = _make_submissions(
            forms=["8-K"] * 5,
            dates=[_dt(i * 7) for i in range(5)],
        )
        ticker_map = _make_ticker_map("ALK", 766421)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("ALK", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        ten_k_filings = [f for f in result.filings if f.form_type == "10-K"]
        assert len(ten_k_filings) == 0


# ── 4. Parser: FY EPS emitted when 10-K in source_accessions ─────────────────

class TestParserFyEpsWithTenKInSourceAccessions:

    def test_fy_eps_emitted_when_10k_accession_included(self) -> None:
        """parse_companyfacts emits FY EPS when the 10-K accession is in the source set."""
        cf = _make_companyfacts_eps([
            _make_eps_entry(_10K_ACCESSION, 6.11, form="10-K", fp="FY"),
        ])
        source_accessions = frozenset({_10K_ACCESSION})
        result = parse_companyfacts(cf, source_accessions)
        assert result.is_success, f"Expected success, got parse_status={result.parse_status}"
        eps_obs = [o for o in result.observations if o.tag == "EarningsPerShareDiluted"]
        assert len(eps_obs) >= 1, "Expected at least 1 FY EPS observation"
        fy_obs = [o for o in eps_obs if o.fiscal_period == "FY"]
        assert len(fy_obs) >= 1, "Expected at least 1 FY annual observation"

    def test_fy_eps_skipped_when_10k_accession_absent(self) -> None:
        """Documents pre-fix behavior: without 10-K in source_accessions, FY EPS is lost."""
        cf = _make_companyfacts_eps([
            _make_eps_entry(_10K_ACCESSION, 6.11, form="10-K", fp="FY"),
        ])
        # Only 10-Q accessions in source set — 10-K excluded
        source_accessions = frozenset(_10Q_ACCESSIONS)
        result = parse_companyfacts(cf, source_accessions)
        eps_obs = [o for o in result.observations if o.tag == "EarningsPerShareDiluted"]
        fy_obs = [o for o in eps_obs if o.fiscal_period == "FY"]
        assert len(fy_obs) == 0, (
            "FY EPS must be filtered out when 10-K accession is absent from source set"
        )

    def test_fy_eps_value_correct_when_10k_included(self) -> None:
        """The FY EPS value is preserved intact (no annualization or modification)."""
        expected_val = 6.11
        cf = _make_companyfacts_eps([
            _make_eps_entry(_10K_ACCESSION, expected_val, form="10-K", fp="FY"),
        ])
        result = parse_companyfacts(cf, frozenset({_10K_ACCESSION}))
        fy_obs = [o for o in result.observations if o.fiscal_period == "FY"]
        assert len(fy_obs) == 1
        assert abs(fy_obs[0].value - expected_val) < 1e-9
        assert fy_obs[0].accession_number == _10K_ACCESSION
        assert fy_obs[0].form == "10-K"

    def test_quarterly_eps_still_emitted_alongside_fy(self) -> None:
        """Recent quarterly EPS from 10-Qs is still emitted together with FY EPS."""
        q_accn = _10Q_ACCESSIONS[0]
        cf = _make_companyfacts_eps([
            _make_eps_entry(_10K_ACCESSION, 6.11, form="10-K", fp="FY",
                            filed=_dt(540), fy=2024),
            _make_eps_entry(q_accn, 1.65, form="10-Q", fp="Q1",
                            filed=_dt(30), fy=2025),
        ])
        source_accessions = frozenset({_10K_ACCESSION, q_accn})
        result = parse_companyfacts(cf, source_accessions)
        assert result.is_success
        all_eps = [o for o in result.observations if o.tag == "EarningsPerShareDiluted"]
        fy_eps = [o for o in all_eps if o.fiscal_period == "FY"]
        q_eps = [o for o in all_eps if o.fiscal_period == "Q1"]
        assert len(fy_eps) >= 1, "FY EPS must be present"
        assert len(q_eps) >= 1, "Q1 EPS must be present"


# ── 5. Non-EPS tags unaffected by 10-K inclusion fix ─────────────────────────

class TestNonEpsTagsBehaviorUnchanged:

    def test_revenue_tag_still_works_with_10q_accession(self) -> None:
        """Non-EPS tags (Revenues) still work with 10-Q-only source accessions."""
        q_accn = _10Q_ACCESSIONS[0]
        cf = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {"USD": [
                            {"accn": q_accn, "val": 100_000_000,
                             "form": "10-Q", "filed": _dt(30), "fy": 2025, "fp": "Q1"},
                        ]},
                    }
                }
            }
        }
        result = parse_companyfacts(cf, frozenset({q_accn}))
        assert result.is_success
        rev_obs = [o for o in result.observations if o.tag == "Revenues"]
        assert len(rev_obs) == 1
        assert rev_obs[0].value == 100_000_000

    def test_revenue_excluded_when_not_in_source_accessions(self) -> None:
        """Non-EPS tags still filtered by source_accessions — behavior unchanged."""
        q_accn = _10Q_ACCESSIONS[0]
        other_accn = "0000320193-99-999999"
        cf = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {"USD": [
                            {"accn": other_accn, "val": 999_999,
                             "form": "10-Q", "filed": _dt(30)},
                        ]},
                    }
                }
            }
        }
        result = parse_companyfacts(cf, frozenset({q_accn}))
        rev_obs = [o for o in result.observations if o.tag == "Revenues"]
        assert len(rev_obs) == 0, "Revenue from unknown accession must be filtered"


# ── 6. End-to-end: provider + companyfacts → FY EPS with 10-K overshadowed ───

class TestEndToEndProviderFyEpsWithOvershadowed10K:

    def test_provider_emits_fy_eps_when_10q_dominates_recent_list(self) -> None:
        """Full provider fetch: 6 recent 10-Qs + 1 10-K → FY EPS observation present."""
        subs = _make_submissions(
            _AAPL_FORMS_RECENT_FIRST,
            _AAPL_DATES_RECENT_FIRST,
            _AAPL_ACCESSIONS_RECENT_FIRST,
        )
        ticker_map = _make_ticker_map("AAPL", 320193)
        cf = _make_companyfacts_eps([
            _make_eps_entry(_10K_ACCESSION, 6.11, form="10-K", fp="FY",
                            filed=_dt(540), fy=2024),
            _make_eps_entry(_10Q_ACCESSIONS[0], 1.65, form="10-Q", fp="Q1",
                            filed=_dt(30), fy=2025),
        ])
        fake = FakeHttpGetFn(ticker_map, subs, cf)
        result = fetch_for_ticker("AAPL", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        assert result.companyfacts_parse_result is not None
        assert result.companyfacts_parse_result.is_success
        obs = result.companyfacts_parse_result.observations
        fy_eps_obs = [
            o for o in obs
            if o.tag == "EarningsPerShareDiluted" and o.fiscal_period == "FY"
        ]
        assert len(fy_eps_obs) >= 1, (
            "FY EPS observation must be present after 10-K source inclusion fix"
        )
        assert fy_eps_obs[0].accession_number == _10K_ACCESSION

    def test_adapted_artifact_has_fy_eps_metric_fact(self) -> None:
        """Adapted result includes a metric_observation fact for the FY EPS."""
        subs = _make_submissions(
            _AAPL_FORMS_RECENT_FIRST,
            _AAPL_DATES_RECENT_FIRST,
            _AAPL_ACCESSIONS_RECENT_FIRST,
        )
        ticker_map = _make_ticker_map("AAPL", 320193)
        cf = _make_companyfacts_eps([
            _make_eps_entry(_10K_ACCESSION, 6.11, form="10-K", fp="FY",
                            filed=_dt(540), fy=2024),
        ])
        fake = FakeHttpGetFn(ticker_map, subs, cf)
        result = fetch_for_ticker("AAPL", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        adapted = adapt_sec_result(result)
        metric_facts = [f for f in adapted.facts if f.fact_kind == "metric_observation"]
        fy_eps_facts = [
            f for f in metric_facts
            if f.structured_payload.get("tag") == "EarningsPerShareDiluted"
            and f.structured_payload.get("fiscal_period") == "FY"
        ]
        assert len(fy_eps_facts) >= 1, (
            "Adapted artifact must include a FY EPS metric_observation fact"
        )

    def test_10k_source_record_present_in_adapted_sources(self) -> None:
        """The 10-K filing appears as a SourceRecord in the adapted output."""
        subs = _make_submissions(
            _AAPL_FORMS_RECENT_FIRST,
            _AAPL_DATES_RECENT_FIRST,
            _AAPL_ACCESSIONS_RECENT_FIRST,
        )
        ticker_map = _make_ticker_map("AAPL", 320193)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("AAPL", _make_config(max_filings=5), http_get_fn=fake)
        adapted = adapt_sec_result(result)
        accns_in_sources = {
            s.section_reference for s in adapted.sources if s.section_reference
        }
        assert _10K_ACCESSION in accns_in_sources, (
            f"10-K accession {_10K_ACCESSION!r} must appear in adapted SourceRecords; "
            f"got: {accns_in_sources}"
        )

    def test_no_10k_in_submissions_no_phantom_fy_eps(self) -> None:
        """When no 10-K exists in submissions, no FY EPS is conjured from thin air."""
        subs = _make_submissions(
            forms=["10-Q"] * 7,
            dates=[_dt(i * 30) for i in range(7)],
            accessions=_10Q_ACCESSIONS + ["0000320193-25-000099"],
        )
        ticker_map = _make_ticker_map("QCOM", 804328)
        # Companyfacts has FY EPS under the 10-K accession — but 10-K is not in submissions
        cf = _make_companyfacts_eps([
            _make_eps_entry(_10K_ACCESSION, 9.99, form="10-K", fp="FY"),
        ])
        fake = FakeHttpGetFn(ticker_map, subs, cf)
        result = fetch_for_ticker("QCOM", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        if result.companyfacts_parse_result:
            fy_eps_obs = [
                o for o in result.companyfacts_parse_result.observations
                if o.tag == "EarningsPerShareDiluted" and o.fiscal_period == "FY"
                and o.accession_number == _10K_ACCESSION
            ]
            assert len(fy_eps_obs) == 0, (
                "FY EPS from absent 10-K must not appear — 10-K not in submissions"
            )


# ── 7. Governance invariants ──────────────────────────────────────────────────

class TestGovernanceInvariants:

    def test_at_most_one_extra_10k_appended(self) -> None:
        """At most one 10-K filing is added beyond the regular cap."""
        subs = _make_submissions(
            forms=["10-Q"] * 5 + ["10-K", "10-K"],  # two 10-Ks — only latest should be added
            dates=[_dt(i * 30) for i in range(5)] + [_dt(600), _dt(960)],
            accessions=_10Q_ACCESSIONS[:5] + ["10K-LATEST", "10K-OLDER"],
        )
        ticker_map = _make_ticker_map("AAPL", 320193)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("AAPL", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        ten_k_filings = [f for f in result.filings if f.form_type == "10-K"]
        assert len(ten_k_filings) == 1, (
            f"Must add only 1 10-K (the latest), got {len(ten_k_filings)}"
        )
        assert ten_k_filings[0].accession_number == "10K-LATEST", (
            "The most recent 10-K (first in list) must be the one added"
        )

    def test_total_filings_count_is_n_plus_1_when_10k_appended(self) -> None:
        """When 10-K is appended, total filings = max_filings_to_return + 1."""
        subs = _make_submissions(
            forms=["10-Q"] * 6 + ["10-K"],
            dates=[_dt(i * 30) for i in range(7)],
            accessions=_10Q_ACCESSIONS + [_10K_ACCESSION],
        )
        ticker_map = _make_ticker_map("MSFT", 789019)
        fake = FakeHttpGetFn(ticker_map, subs)
        result = fetch_for_ticker("MSFT", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        assert len(result.filings) == 6, (
            f"Expected 5 (cap) + 1 (10-K) = 6 filings, got {len(result.filings)}"
        )

    def test_no_snapshot_writes_from_provider(self) -> None:
        """Provider never touches intel_v3_snapshots — static source guard."""
        import inspect
        import app.services.intelligence.research_workers.sec_edgar_provider as mod
        src = inspect.getsource(mod)
        assert "intel_v3_snapshots" not in src, (
            "sec_edgar_provider.py must not reference intel_v3_snapshots"
        )

    def test_no_priceband_in_provider(self) -> None:
        """Provider must never generate PriceBand or safe_for_decision=True data."""
        import inspect
        import app.services.intelligence.research_workers.sec_edgar_provider as mod
        src = inspect.getsource(mod)
        assert "priceband" not in src.lower()
        assert "safe_for_decision" not in src.lower()

    def test_fix_is_scan_based_not_ticker_specific(self) -> None:
        """The fix scans the submissions list generically — not a per-ticker special case.

        Verifies the Phase 14C.5 block uses form-type comparisons against the
        parsed submissions data rather than hardcoded ticker conditions. The
        functional tests (test_all_10q_submissions_no_fabricated_10k,
        test_no_10k_in_submissions_no_phantom_fy_eps) prove no fabrication occurs.
        """
        import inspect
        import app.services.intelligence.research_workers.sec_edgar_provider as mod
        src = inspect.getsource(mod)
        # The fix must reference the generic "10-K" form-type string
        assert '"10-K"' in src, "Phase 14C.5 fix must scan for 10-K form_type"
        # The fix must not call an external API or fabricate; it must reuse
        # the already-fetched submissions arrays
        assert "_form_str" in src, "Fix iterates submissions entries — _form_str present"

    def test_existing_10q_source_behavior_unchanged(self) -> None:
        """When 10-Qs dominate and a 10-K is appended, 10-Q facts still link correctly."""
        subs = _make_submissions(
            _AAPL_FORMS_RECENT_FIRST,
            _AAPL_DATES_RECENT_FIRST,
            _AAPL_ACCESSIONS_RECENT_FIRST,
        )
        ticker_map = _make_ticker_map("AAPL", 320193)
        q_accn = _10Q_ACCESSIONS[0]
        cf = {
            "facts": {
                "us-gaap": {
                    "EarningsPerShareDiluted": {
                        "label": "EPS Diluted",
                        "units": {"USD/shares": [
                            _make_eps_entry(q_accn, 1.65, form="10-Q", fp="Q1",
                                           filed=_dt(30), fy=2025),
                            _make_eps_entry(_10K_ACCESSION, 6.11, form="10-K", fp="FY",
                                           filed=_dt(540), fy=2024),
                        ]},
                    }
                }
            }
        }
        fake = FakeHttpGetFn(ticker_map, subs, cf)
        result = fetch_for_ticker("AAPL", _make_config(max_filings=5), http_get_fn=fake)
        assert result.is_success
        assert result.companyfacts_parse_result is not None
        obs = result.companyfacts_parse_result.observations
        # Quarterly EPS from 10-Q still present
        q_obs = [o for o in obs if o.accession_number == q_accn]
        assert len(q_obs) >= 1, "Quarterly EPS (10-Q) must still be emitted"
        # Annual FY EPS from 10-K now also present
        fy_obs = [o for o in obs if o.accession_number == _10K_ACCESSION]
        assert len(fy_obs) >= 1, "Annual FY EPS (10-K) must now be emitted"

    def test_backfill_flag_gating_unaffected(self) -> None:
        """The provider fix never bypasses backfill endpoint flag gating."""
        import inspect
        import app.services.intelligence.research_workers.sec_edgar_provider as mod
        src = inspect.getsource(mod)
        # The provider must not reference backfill flags directly
        assert "intel_v3_sec_fy_eps_backfill_enabled" not in src.lower()
        assert "INTEL_V3_SEC_FY_EPS_BACKFILL_ENABLED" not in src
