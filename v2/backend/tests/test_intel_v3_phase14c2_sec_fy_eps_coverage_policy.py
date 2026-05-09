"""Phase 14C.2 — SEC FY EPS Coverage Selection Policy tests.

Validates the additive FY-priority selection policy added to
sec_companyfacts_parser.py. The policy ensures that the latest annual FY EPS
observation is retained even when generic latest-N slots are filled by
quarterly Q1/Q2/Q3 observations.

Hard invariants verified:
  - FY annual (fp=="FY" OR form=="10-K") retained when not in generic selection.
  - No duplicate observations (deduplication by accession_number).
  - No quarterly annualization (quarterly values not modified).
  - No FY observation fabricated when none exists.
  - Source-link requirement enforced (FY without accession in source_accessions
    is already excluded by existing filtering, not by this policy).
  - Non-EPS tags use unchanged generic latest-N behavior.
  - End-to-end: stored artifact facts include FY EPS when available.

All tests use pure in-memory data — no DB, no HTTP, no external calls.
"""
from __future__ import annotations

from dataclasses import field
from typing import Any

import pytest

from app.services.intelligence.research_workers.sec_companyfacts_parser import (
    CompanyFactsParseResult,
    MetricObservation,
    _EPS_TAGS,
    _MAX_PERIODS_PER_TAG,
    _is_fy_annual_entry,
    parse_companyfacts,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_ACCN_FY = "0000320193-24-000100"   # FY annual accession
_ACCN_Q1 = "0000320193-25-000001"   # Q1 quarterly accession
_ACCN_Q2 = "0000320193-25-000002"   # Q2 quarterly accession
_ACCN_Q3 = "0000320193-25-000003"   # Q3 quarterly accession
_ACCN_REV = "0000320193-24-000200"  # revenue (non-EPS) accession

_ALL_ACCNS: frozenset[str] = frozenset({
    _ACCN_FY, _ACCN_Q1, _ACCN_Q2, _ACCN_Q3, _ACCN_REV,
})


def _cf_entry(
    accn: str,
    val: float,
    form: str,
    filed: str,
    fy: int | None = None,
    fp: str | None = None,
) -> dict:
    """Build a minimal companyfacts unit entry."""
    e: dict[str, Any] = {"accn": accn, "val": val, "form": form, "filed": filed}
    if fy is not None:
        e["fy"] = fy
    if fp is not None:
        e["fp"] = fp
    return e


def _make_eps_companyfacts(tag: str, entries: list[dict]) -> dict:
    return {
        "facts": {
            "us-gaap": {
                tag: {"label": f"{tag} Label", "units": {"USD/shares": entries}},
            }
        }
    }


def _make_revenue_companyfacts(tag: str, entries: list[dict]) -> dict:
    return {
        "facts": {
            "us-gaap": {
                tag: {"label": f"{tag} Label", "units": {"USD": entries}},
            }
        }
    }


def _accns_of(result: CompanyFactsParseResult) -> list[str]:
    return [o.accession_number for o in result.observations]


def _fps_of(result: CompanyFactsParseResult, tag: str) -> list[str | None]:
    return [o.fiscal_period for o in result.observations if o.tag == tag]


# ── _is_fy_annual_entry unit tests ────────────────────────────────────────────

class TestIsFyAnnualEntry:
    """Unit tests for the _is_fy_annual_entry helper."""

    def test_fp_fy_form_10k_is_fy(self) -> None:
        assert _is_fy_annual_entry({"fp": "FY", "form": "10-K"}) is True

    def test_fp_fy_form_10q_is_fy(self) -> None:
        # fp=="FY" is sufficient regardless of form.
        assert _is_fy_annual_entry({"fp": "FY", "form": "10-Q"}) is True

    def test_fp_absent_form_10k_is_fy(self) -> None:
        assert _is_fy_annual_entry({"form": "10-K"}) is True

    def test_fp_q1_is_not_fy(self) -> None:
        assert _is_fy_annual_entry({"fp": "Q1", "form": "10-Q"}) is False

    def test_fp_q2_is_not_fy(self) -> None:
        assert _is_fy_annual_entry({"fp": "Q2", "form": "10-Q"}) is False

    def test_fp_q3_is_not_fy(self) -> None:
        assert _is_fy_annual_entry({"fp": "Q3", "form": "10-Q"}) is False

    def test_fp_absent_form_10q_is_not_fy(self) -> None:
        assert _is_fy_annual_entry({"form": "10-Q"}) is False


# ── Test 1: latest Q1/Q2 in generic slots → FY retained ──────────────────────

class TestFyRetainedWhenGenericSlotsAreQuarterly:
    """Generic latest-2 would keep Q1 and Q2 only; policy must also retain FY.

    Timeline (filed date ascending):
      2024-10-25 → FY 2023 (10-K, fp="FY")   — oldest
      2025-02-10 → Q1 2024 (10-Q, fp="Q1")
      2025-05-08 → Q2 2024 (10-Q, fp="Q2")   — most recently filed
    """

    def _build_result(self, tag: str) -> CompanyFactsParseResult:
        entries = [
            _cf_entry(_ACCN_FY, 5.00, "10-K", "2024-10-25", fy=2023, fp="FY"),
            _cf_entry(_ACCN_Q1, 1.20, "10-Q", "2025-02-10", fy=2024, fp="Q1"),
            _cf_entry(_ACCN_Q2, 1.30, "10-Q", "2025-05-08", fy=2024, fp="Q2"),
        ]
        return parse_companyfacts(
            _make_eps_companyfacts(tag, entries),
            source_accessions=_ALL_ACCNS,
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_fy_observation_is_retained(self, tag: str) -> None:
        result = self._build_result(tag)
        assert _ACCN_FY in _accns_of(result), (
            f"FY annual accession missing for {tag}; got {_accns_of(result)}"
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_generic_latest_two_are_also_retained(self, tag: str) -> None:
        result = self._build_result(tag)
        accns = _accns_of(result)
        # Q2 and Q1 are the generic latest-2 (most recently filed).
        assert _ACCN_Q2 in accns, f"Q2 observation missing for {tag}"
        assert _ACCN_Q1 in accns, f"Q1 observation missing for {tag}"

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_total_three_observations_stored(self, tag: str) -> None:
        result = self._build_result(tag)
        tag_obs = [o for o in result.observations if o.tag == tag]
        assert len(tag_obs) == 3, (
            f"Expected 3 observations (Q2+Q1+FY) for {tag}, got {len(tag_obs)}"
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_fy_eps_added_beyond_generic_limit_count_is_one(self, tag: str) -> None:
        result = self._build_result(tag)
        assert result.fy_eps_added_beyond_generic_limit_count >= 1, (
            f"Expected fy_eps_added_beyond_generic_limit_count >= 1 for {tag}"
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_fy_quarterly_values_not_modified(self, tag: str) -> None:
        result = self._build_result(tag)
        by_accn = {o.accession_number: o for o in result.observations if o.tag == tag}
        assert by_accn[_ACCN_FY].value == 5.00
        assert by_accn[_ACCN_Q1].value == 1.20
        assert by_accn[_ACCN_Q2].value == 1.30


# ── Test 2: FY already in generic latest-N → no duplicate ─────────────────────

class TestNoFyDuplicateWhenFyAlreadyInGenericSelection:
    """When the FY annual is already one of the two most recently filed, do not
    add a duplicate.

    Timeline:
      2024-08-01 → Q3 2024 (10-Q, fp="Q3")
      2024-11-15 → FY 2024 (10-K, fp="FY")  — most recently filed
    """

    def _build_result(self, tag: str) -> CompanyFactsParseResult:
        entries = [
            _cf_entry(_ACCN_Q3, 1.50, "10-Q", "2024-08-01", fy=2024, fp="Q3"),
            _cf_entry(_ACCN_FY, 6.00, "10-K", "2024-11-15", fy=2024, fp="FY"),
        ]
        return parse_companyfacts(
            _make_eps_companyfacts(tag, entries),
            source_accessions=_ALL_ACCNS,
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_no_duplicate_fy_observation(self, tag: str) -> None:
        result = self._build_result(tag)
        fy_obs = [o for o in result.observations if o.tag == tag and o.accession_number == _ACCN_FY]
        assert len(fy_obs) == 1, (
            f"Expected exactly 1 FY observation for {tag}, got {len(fy_obs)}"
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_fy_added_count_is_zero_when_already_present(self, tag: str) -> None:
        result = self._build_result(tag)
        # FY is already in generic latest-2, so the supplemental path is not triggered.
        assert result.fy_eps_added_beyond_generic_limit_count == 0, (
            f"Expected fy_eps_added_beyond_generic_limit_count == 0 for {tag}, "
            f"got {result.fy_eps_added_beyond_generic_limit_count}"
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_two_observations_total(self, tag: str) -> None:
        result = self._build_result(tag)
        tag_obs = [o for o in result.observations if o.tag == tag]
        assert len(tag_obs) == 2


# ── Test 3: EPS tag with only quarterly observations → no FY fabricated ───────

class TestNoFyFabricatedWhenOnlyQuarterlyExist:
    """When only quarterly observations exist, do not fabricate a FY EPS.

    No annualization. No inference. Fail closed.
    """

    def _build_result(self, tag: str) -> CompanyFactsParseResult:
        entries = [
            _cf_entry(_ACCN_Q1, 1.10, "10-Q", "2025-02-01", fy=2024, fp="Q1"),
            _cf_entry(_ACCN_Q2, 1.20, "10-Q", "2025-05-01", fy=2024, fp="Q2"),
            _cf_entry(_ACCN_Q3, 1.30, "10-Q", "2024-08-01", fy=2024, fp="Q3"),
        ]
        return parse_companyfacts(
            _make_eps_companyfacts(tag, entries),
            source_accessions=_ALL_ACCNS,
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_no_fy_observation_fabricated(self, tag: str) -> None:
        result = self._build_result(tag)
        fy_obs = [
            o for o in result.observations
            if o.tag == tag and (o.fiscal_period == "FY" or o.form == "10-K")
        ]
        assert len(fy_obs) == 0, (
            f"FY observation fabricated for {tag}: {fy_obs}"
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_quarterly_values_not_annualized(self, tag: str) -> None:
        result = self._build_result(tag)
        for obs in result.observations:
            if obs.tag != tag:
                continue
            # Values must match exactly what was provided; no annualization.
            assert obs.value in (1.10, 1.20, 1.30), (
                f"Unexpected value {obs.value} for {tag} — looks like annualization"
            )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_fy_added_count_is_zero(self, tag: str) -> None:
        result = self._build_result(tag)
        assert result.fy_eps_added_beyond_generic_limit_count == 0


# ── Test 4: FY exists but missing source link → excluded by existing filter ───

class TestFyWithoutSourceLinkExcluded:
    """FY annual EPS without a matching accession in source_accessions must be
    excluded by the existing source-link filter (candidates loop), not
    surfaced as a FY observation.
    """

    _ACCN_FY_UNLINKED = "0000320193-24-UNLINKED"  # NOT in source_accessions

    def _source_accessions_without_fy(self) -> frozenset[str]:
        # Exclude the FY accession — simulates not having a SourceRecord for it.
        return frozenset({_ACCN_Q1, _ACCN_Q2, _ACCN_Q3, _ACCN_REV})

    def _build_result(self, tag: str) -> CompanyFactsParseResult:
        entries = [
            _cf_entry(self._ACCN_FY_UNLINKED, 5.00, "10-K", "2024-10-25", fy=2023, fp="FY"),
            _cf_entry(_ACCN_Q1, 1.20, "10-Q", "2025-02-10", fy=2024, fp="Q1"),
            _cf_entry(_ACCN_Q2, 1.30, "10-Q", "2025-05-08", fy=2024, fp="Q2"),
        ]
        return parse_companyfacts(
            _make_eps_companyfacts(tag, entries),
            source_accessions=self._source_accessions_without_fy(),
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_unlinked_fy_not_stored(self, tag: str) -> None:
        result = self._build_result(tag)
        assert self._ACCN_FY_UNLINKED not in _accns_of(result), (
            f"Unlinked FY accession was stored for {tag}: {_accns_of(result)}"
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_linked_quarterly_still_stored(self, tag: str) -> None:
        result = self._build_result(tag)
        accns = _accns_of(result)
        assert _ACCN_Q1 in accns or _ACCN_Q2 in accns, (
            f"Quarterly observations missing for {tag}: {accns}"
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_fy_added_count_zero_because_filtered_before_policy(self, tag: str) -> None:
        # The unlinked FY entry never reaches the FY-priority selection loop
        # because the source-link filter excludes it from candidates entirely.
        result = self._build_result(tag)
        assert result.fy_eps_added_beyond_generic_limit_count == 0


# ── Test 5: Non-EPS tags use unchanged generic latest-N behavior ──────────────

class TestNonEpsTagsUnchanged:
    """Non-EPS tags (Revenues, NetIncomeLoss, etc.) must continue to use the
    generic latest-N selection policy without any FY-priority modification.
    """

    def _build_result_revenues(self) -> CompanyFactsParseResult:
        # Three entries — generic latest-2 should keep the two most recently filed.
        entries = [
            _cf_entry(_ACCN_FY, 1_000_000, "10-K", "2024-10-25"),
            _cf_entry(_ACCN_Q1, 300_000, "10-Q", "2025-02-10"),
            _cf_entry(_ACCN_Q2, 320_000, "10-Q", "2025-05-08"),
        ]
        return parse_companyfacts(
            _make_revenue_companyfacts("Revenues", entries),
            source_accessions=_ALL_ACCNS,
        )

    def test_revenues_keeps_only_latest_n(self) -> None:
        result = self._build_result_revenues()
        rev_obs = [o for o in result.observations if o.tag == "Revenues"]
        # Generic latest-2 keeps Q2 (most recent) and Q1 (second most recent).
        assert len(rev_obs) == _MAX_PERIODS_PER_TAG, (
            f"Revenues should keep exactly {_MAX_PERIODS_PER_TAG} observations, "
            f"got {len(rev_obs)}"
        )
        accns = [o.accession_number for o in rev_obs]
        assert _ACCN_Q2 in accns
        assert _ACCN_Q1 in accns
        assert _ACCN_FY not in accns  # FY was oldest → dropped by generic policy

    def test_revenues_fy_added_count_zero(self) -> None:
        result = self._build_result_revenues()
        # FY-priority policy must not trigger for non-EPS tags.
        assert result.fy_eps_added_beyond_generic_limit_count == 0

    def test_net_income_loss_unchanged(self) -> None:
        entries = [
            _cf_entry(_ACCN_FY, 50_000_000, "10-K", "2024-10-25"),
            _cf_entry(_ACCN_Q1, 10_000_000, "10-Q", "2025-02-10"),
            _cf_entry(_ACCN_Q2, 12_000_000, "10-Q", "2025-05-08"),
        ]
        result = parse_companyfacts(
            _make_revenue_companyfacts("NetIncomeLoss", entries),
            source_accessions=_ALL_ACCNS,
        )
        ni_obs = [o for o in result.observations if o.tag == "NetIncomeLoss"]
        assert len(ni_obs) == _MAX_PERIODS_PER_TAG
        assert all(o.accession_number in (_ACCN_Q2, _ACCN_Q1) for o in ni_obs)


# ── Test 6: End-to-end — adapter produces FY EPS in metric_observation facts ──

class TestEndToEndFyEpsInArtifactFacts:
    """Integration-level test: when the parser produces a FY EPS observation
    via the FY-priority policy, the earnings_sec_adapter wires it into a
    metric_observation FactRecord with the correct source link.
    """

    def test_adapter_produces_fy_metric_observation_fact(self) -> None:
        from app.services.intelligence.research_workers.earnings_sec_adapter import (
            adapt_sec_result,
        )
        from app.services.intelligence.research_workers.sec_edgar_provider import (
            SecEdgarProviderResult,
            SecFilingRecord,
        )
        from app.services.intelligence.research_workers.sec_companyfacts_parser import (
            CompanyFactsParseResult,
        )

        # Build a companyfacts parse result that includes FY EPS via the policy:
        # two quarterly entries in generic slots + FY added by policy.
        tag = "EarningsPerShareDiluted"
        cf_raw = _make_eps_companyfacts(tag, [
            _cf_entry(_ACCN_FY, 4.50, "10-K", "2024-10-25", fy=2023, fp="FY"),
            _cf_entry(_ACCN_Q1, 1.10, "10-Q", "2025-02-10", fy=2024, fp="Q1"),
            _cf_entry(_ACCN_Q2, 1.15, "10-Q", "2025-05-08", fy=2024, fp="Q2"),
        ])
        cf_result = parse_companyfacts(cf_raw, source_accessions=_ALL_ACCNS)

        # Confirm FY was added by the policy.
        assert _ACCN_FY in [o.accession_number for o in cf_result.observations if o.tag == tag]
        assert cf_result.fy_eps_added_beyond_generic_limit_count >= 1

        # Build a minimal SecEdgarProviderResult with matching filings.
        filings = [
            SecFilingRecord(
                form_type="10-K",
                filing_date="2024-10-25",
                accession_number=_ACCN_FY,
                report_date="2024-09-28",
                filing_url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000100/0000320193-24-000100-index.htm",
            ),
            SecFilingRecord(
                form_type="10-Q",
                filing_date="2025-02-10",
                accession_number=_ACCN_Q1,
                report_date="2024-12-28",
                filing_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000001/0000320193-25-000001-index.htm",
            ),
            SecFilingRecord(
                form_type="10-Q",
                filing_date="2025-05-08",
                accession_number=_ACCN_Q2,
                report_date="2025-03-29",
                filing_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000002/0000320193-25-000002-index.htm",
            ),
        ]
        sec_result = SecEdgarProviderResult(
            ticker="AAPL",
            cik="0000320193",
            fetch_status="success",
            filings=filings,
            companyfacts_parse_result=cf_result,
        )

        adapter_result = adapt_sec_result(sec_result)

        # Find metric_observation facts with EPS tag.
        fy_metric_facts = [
            f for f in adapter_result.facts
            if f.fact_kind == "metric_observation"
            and f.structured_payload.get("tag") == tag
            and (
                f.structured_payload.get("fiscal_period") == "FY"
                or (
                    f.structured_payload.get("fiscal_period") is None
                    and f.structured_payload.get("form") == "10-K"
                )
            )
        ]
        assert len(fy_metric_facts) == 1, (
            f"Expected 1 FY metric_observation fact, got {len(fy_metric_facts)}"
        )
        fy_fact = fy_metric_facts[0]

        # FY fact must be source-linked (source_index set).
        assert fy_fact.source_index is not None, "FY EPS fact must have source_index"

        # FY value must be unchanged — 4.50 exactly.
        assert fy_fact.structured_payload["value"] == 4.50, (
            f"FY EPS value was modified: {fy_fact.structured_payload['value']}"
        )

        # safe_for_decision is enforced by the writer contract; the fact itself
        # must not carry forbidden payload keys.
        from app.services.intelligence.research_workers.contracts import _has_forbidden_key
        assert not _has_forbidden_key(fy_fact.structured_payload), (
            "FY EPS fact structured_payload contains forbidden keys"
        )

    def test_adapter_total_eps_facts_includes_fy_and_quarterly(self) -> None:
        from app.services.intelligence.research_workers.earnings_sec_adapter import adapt_sec_result
        from app.services.intelligence.research_workers.sec_edgar_provider import (
            SecEdgarProviderResult, SecFilingRecord,
        )

        tag = "EarningsPerShareBasic"
        cf_raw = _make_eps_companyfacts(tag, [
            _cf_entry(_ACCN_FY, 3.80, "10-K", "2024-10-25", fy=2023, fp="FY"),
            _cf_entry(_ACCN_Q1, 0.95, "10-Q", "2025-02-10", fy=2024, fp="Q1"),
            _cf_entry(_ACCN_Q2, 0.98, "10-Q", "2025-05-08", fy=2024, fp="Q2"),
        ])
        cf_result = parse_companyfacts(cf_raw, source_accessions=_ALL_ACCNS)

        filings = [
            SecFilingRecord("10-K", "2024-10-25", _ACCN_FY, "2024-09-28", "https://sec.gov/a"),
            SecFilingRecord("10-Q", "2025-02-10", _ACCN_Q1, "2024-12-28", "https://sec.gov/b"),
            SecFilingRecord("10-Q", "2025-05-08", _ACCN_Q2, "2025-03-29", "https://sec.gov/c"),
        ]
        sec_result = SecEdgarProviderResult(
            ticker="TEST",
            cik="0000000001",
            fetch_status="success",
            filings=filings,
            companyfacts_parse_result=cf_result,
        )

        adapter_result = adapt_sec_result(sec_result)
        eps_facts = [
            f for f in adapter_result.facts
            if f.fact_kind == "metric_observation"
            and f.structured_payload.get("tag") == tag
        ]
        # Should have 3 metric_observation facts: Q2 (generic), Q1 (generic), FY (policy).
        assert len(eps_facts) == 3, (
            f"Expected 3 EPS metric_observation facts, got {len(eps_facts)}: "
            f"{[f.structured_payload.get('accession_number') for f in eps_facts]}"
        )
        accns_in_facts = {f.structured_payload.get("accession_number") for f in eps_facts}
        assert _ACCN_FY in accns_in_facts
        assert _ACCN_Q1 in accns_in_facts
        assert _ACCN_Q2 in accns_in_facts


# ── Test 7: Shape C (fp absent, form 10-K) treated as FY annual ───────────────

class TestShapeCFpAbsentForm10K:
    """Verify that an entry with fp absent and form==10-K is treated as FY annual
    and retained by the policy when generic slots are filled with quarterly entries.
    """

    def _build_result(self, tag: str) -> CompanyFactsParseResult:
        fy_entry = _cf_entry(_ACCN_FY, 4.00, "10-K", "2024-10-25", fy=2023)
        # fp is absent in this entry (Shape C from eps_payload_extractor_v1.py).
        q1_entry = _cf_entry(_ACCN_Q1, 1.00, "10-Q", "2025-02-10", fy=2024, fp="Q1")
        q2_entry = _cf_entry(_ACCN_Q2, 1.05, "10-Q", "2025-05-08", fy=2024, fp="Q2")
        return parse_companyfacts(
            _make_eps_companyfacts(tag, [fy_entry, q1_entry, q2_entry]),
            source_accessions=_ALL_ACCNS,
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_shape_c_fy_retained(self, tag: str) -> None:
        result = self._build_result(tag)
        assert _ACCN_FY in _accns_of(result), (
            f"Shape C FY entry not retained for {tag}: {_accns_of(result)}"
        )

    @pytest.mark.parametrize("tag", sorted(_EPS_TAGS))
    def test_shape_c_fy_observation_has_no_fp(self, tag: str) -> None:
        result = self._build_result(tag)
        fy_obs = next(
            (o for o in result.observations if o.tag == tag and o.accession_number == _ACCN_FY),
            None,
        )
        assert fy_obs is not None
        assert fy_obs.fiscal_period is None, (
            f"Shape C FY entry should have fiscal_period=None, got {fy_obs.fiscal_period}"
        )
        assert fy_obs.form == "10-K"
