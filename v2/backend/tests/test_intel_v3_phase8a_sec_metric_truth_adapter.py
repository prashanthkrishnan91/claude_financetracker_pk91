"""Phase 8A — SEC Metric Truth Adapter Dry Run tests.

Acceptance criteria:

Unit tests for sec_metric_truth_adapter_dry_run module:
  1.  Observed SEC tags map to the intended normalized buckets.
  2.  Revenue aliases (Revenues, RevenueFromContractWithCustomerExcludingAssessedTax)
      both map to "revenue".
  3.  EPS basic and diluted both map to "eps".
  4.  Operating cash flow and capex map separately.
  5.  Balance sheet tags map separately to cash/assets/liabilities/equity.
  6.  Unmapped tags are counted as unmapped, not silently dropped.
  7.  Missing tag/unit/form uses UNKNOWN safely — no crash.
  8.  Facts without source_id are excluded from source-linked adapter counts.
  9.  Non-metric facts (fact_kind != "metric_observation") are ignored.
  10. Raw metric values and structured_payload are not returned.

Integration tests via summarize_recent_research_artifacts (observability):
  11. Existing Phase 7C fields remain unchanged.
  12. safe_for_decision remains False / decision consumption remains zero.
  13. visible_snapshot_unchanged remains True.
  14. No decide(), IntelV3Service, recommendation_engine, or frontend imports.

Static source guards:
  15. sec_metric_truth_adapter_dry_run.py has no decide() / IntelV3Service imports.
  16. diagnostics.py observe endpoint includes all 12 Phase 8A keys.
  17. Phase 8A summary fields have backward-compatible defaults.

Kill-switch:
  18. When flag is False (default), all Phase 8A fields are zero/empty/False.
  19. When flag is True, dry-run runs and populates fields correctly.

All tests use FakeDB — no production Supabase dependency.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from app.config import Settings
from app.services.intelligence.research_workers.sec_metric_truth_adapter_dry_run import (
    SEC_METRIC_BUCKET_MAP,
    EXPECTED_BUCKETS,
    SecMetricTruthAdapterDryRunResult,
    run_sec_metric_truth_adapter_dry_run,
)


# ── Settings helpers ──────────────────────────────────────────────────────────

def _base_settings(**overrides) -> Settings:
    base = dict(
        supabase_url="http://fake",
        supabase_anon_key="anon",
        supabase_service_role_key="svc",
        supabase_jwt_secret="secret",
        encryption_key="a" * 64,
        intel_v3_research_artifact_observability_enabled=True,
        intel_v3_research_artifact_observability_info_logs_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def _dry_run_enabled_settings(**overrides) -> Settings:
    return _base_settings(
        intel_v3_sec_metric_truth_adapter_dry_run_enabled=True,
        **overrides,
    )


def _dry_run_disabled_settings(**overrides) -> Settings:
    return _base_settings(
        intel_v3_sec_metric_truth_adapter_dry_run_enabled=False,
        **overrides,
    )


_UID = "u1"


# ── Fact/artifact helpers ─────────────────────────────────────────────────────

def _make_artifact(
    aid: str = None,
    ticker: str = "AAPL",
) -> dict:
    return {
        "id": aid or str(uuid.uuid4()),
        "user_id": _UID,
        "ticker": ticker,
        "artifact_type": "catalyst_window",
        "skill_pack": "earnings_reviewer",
        "confidence_or_trust_level": "MEDIUM",
        "freshness_status": "FRESH",
        "is_active": True,
        "safe_for_decision": False,
        "invalidated_at": None,
        "expires_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "limitations_or_missing_evidence": [],
        "payload": {"review_status": "dark_run"},
    }


def _make_metric_fact(
    artifact_id: str,
    tag: str = "Revenues",
    unit: str = "USD",
    form: str = "10-K",
    value: Any = 123456789,
    claim: str = "sec_companyfact_observed",
    source_id: Optional[str] = None,
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "user_id": _UID,
        "artifact_id": artifact_id,
        "fact_kind": "metric_observation",
        "source_id": source_id if source_id is not None else str(uuid.uuid4()),
        "structured_payload": {
            "claim": claim,
            "taxonomy": "us-gaap",
            "tag": tag,
            "label": tag,
            "value": value,
            "unit": unit,
            "form": form,
            "filed": "2024-11-01",
            "accession_number": "0000320193-24-000123",
        },
    }


def _make_non_metric_fact(artifact_id: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "user_id": _UID,
        "artifact_id": artifact_id,
        "fact_kind": "sourced_claim",
        "source_id": str(uuid.uuid4()),
        "structured_payload": {"claim": "sec_filing_found", "form_type": "10-K"},
    }


def _make_source(artifact_id: str, source_id: str = None) -> dict:
    sid = source_id or str(uuid.uuid4())
    return {
        "id": sid,
        "user_id": _UID,
        "artifact_id": artifact_id,
        "source_kind": "sec_filing",
        "provider_name": "sec_edgar",
        "source_url": "https://www.sec.gov/Archives/edgar/data/320193/",
        "section_reference": "0000320193-24-000123",
        "source_id": sid,
        "source_hash": None,
    }


# ── FakeDB (mirrors Phase 7C test infra) ─────────────────────────────────────

class _FakeQuery:
    def __init__(self, rows: list[dict], fail_with: Optional[Exception] = None) -> None:
        self._rows = rows
        self._fail_with = fail_with
        self._filters: dict = {}
        self._in_filters: dict = {}
        self._limit: Optional[int] = None

    def select(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def eq(self, col: str, val: Any) -> "_FakeQuery":
        self._filters[col] = val
        return self

    def gte(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def order(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def limit(self, n: int) -> "_FakeQuery":
        self._limit = n
        return self

    def in_(self, col: str, vals: list) -> "_FakeQuery":
        self._in_filters[col] = vals
        return self

    def execute(self) -> Any:
        if self._fail_with is not None:
            raise self._fail_with
        rows = self._rows
        for col, val in self._filters.items():
            rows = [r for r in rows if str(r.get(col, "")) == str(val)]
        for col, vals in self._in_filters.items():
            str_vals = {str(v) for v in vals}
            rows = [r for r in rows if str(r.get(col, "")) in str_vals]
        if self._limit is not None:
            rows = rows[: self._limit]

        @dataclass
        class _Res:
            data: list

        return _Res(data=list(rows))


class _FakeDB:
    def __init__(
        self,
        artifact_rows: list[dict] = None,
        source_rows: list[dict] = None,
        fact_rows: list[dict] = None,
    ) -> None:
        self._artifact_rows = artifact_rows or []
        self._source_rows = source_rows or []
        self._fact_rows = fact_rows or []
        self._current_table: Optional[str] = None

    def table(self, name: str) -> "_FakeDB":
        self._current_table = name
        return self

    def select(self, cols: str) -> "_FakeQuery":
        t = self._current_table
        if t == "research_artifacts":
            return _FakeQuery(self._artifact_rows)
        elif t == "research_artifact_sources":
            return _FakeQuery(self._source_rows)
        elif t == "research_artifact_facts":
            return _FakeQuery(self._fact_rows)
        return _FakeQuery([])

    def eq(self, *args, **kwargs) -> "_FakeDB":
        return self

    def gte(self, *args, **kwargs) -> "_FakeDB":
        return self

    def order(self, *args, **kwargs) -> "_FakeDB":
        return self

    def limit(self, n: int) -> "_FakeDB":
        return self

    def in_(self, col: str, vals: list) -> "_FakeDB":
        return self

    def execute(self) -> Any:
        @dataclass
        class _Res:
            data: list

        return _Res(data=[])


# ── Helper: call observability service ───────────────────────────────────────

def _obs(db, settings=None):
    from app.services.intelligence.research_workers.artifact_observability import (
        summarize_recent_research_artifacts,
    )
    return summarize_recent_research_artifacts(
        user_id=_UID,
        db_client=db,
        settings=settings or _dry_run_enabled_settings(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC 1: SEC tags map to correct normalized buckets
# ─────────────────────────────────────────────────────────────────────────────

class TestBucketMapping:
    def test_all_12_tags_are_in_map(self):
        expected_tags = {
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "NetIncomeLoss",
            "OperatingIncomeLoss",
            "EarningsPerShareBasic",
            "EarningsPerShareDiluted",
            "NetCashProvidedByUsedInOperatingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "CashAndCashEquivalentsAtCarryingValue",
            "Assets",
            "Liabilities",
            "StockholdersEquity",
        }
        assert expected_tags == set(SEC_METRIC_BUCKET_MAP.keys())

    def test_10_expected_buckets(self):
        assert EXPECTED_BUCKETS == {
            "revenue", "net_income", "operating_income", "eps",
            "operating_cash_flow", "capex", "cash", "assets",
            "liabilities", "equity",
        }

    def test_each_tag_maps_to_expected_bucket(self):
        checks = [
            ("Revenues", "revenue"),
            ("RevenueFromContractWithCustomerExcludingAssessedTax", "revenue"),
            ("NetIncomeLoss", "net_income"),
            ("OperatingIncomeLoss", "operating_income"),
            ("EarningsPerShareBasic", "eps"),
            ("EarningsPerShareDiluted", "eps"),
            ("NetCashProvidedByUsedInOperatingActivities", "operating_cash_flow"),
            ("PaymentsToAcquirePropertyPlantAndEquipment", "capex"),
            ("CashAndCashEquivalentsAtCarryingValue", "cash"),
            ("Assets", "assets"),
            ("Liabilities", "liabilities"),
            ("StockholdersEquity", "equity"),
        ]
        for tag, bucket in checks:
            assert SEC_METRIC_BUCKET_MAP[tag] == bucket, f"{tag} should map to {bucket}"


# ─────────────────────────────────────────────────────────────────────────────
# AC 2: Revenue aliases both map to "revenue"
# ─────────────────────────────────────────────────────────────────────────────

class TestRevenueAliases:
    def test_revenues_maps_to_revenue_bucket(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="Revenues")]},
        )
        assert result.by_bucket.get("revenue", 0) == 1

    def test_revenue_from_contract_maps_to_revenue_bucket(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [
                    _make_metric_fact(
                        aid,
                        tag="RevenueFromContractWithCustomerExcludingAssessedTax",
                    )
                ]
            },
        )
        assert result.by_bucket.get("revenue", 0) == 1

    def test_both_revenue_aliases_accumulate_in_same_bucket(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [
                    _make_metric_fact(aid, tag="Revenues"),
                    _make_metric_fact(
                        aid,
                        tag="RevenueFromContractWithCustomerExcludingAssessedTax",
                    ),
                ]
            },
        )
        assert result.by_bucket["revenue"] == 2
        assert result.by_tag["Revenues"] == 1
        assert result.by_tag["RevenueFromContractWithCustomerExcludingAssessedTax"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# AC 3: EPS basic and diluted both map to "eps"
# ─────────────────────────────────────────────────────────────────────────────

class TestEpsAliases:
    def test_eps_basic_maps_to_eps(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [_make_metric_fact(aid, tag="EarningsPerShareBasic", unit="USD/shares")]
            },
        )
        assert result.by_bucket.get("eps", 0) == 1

    def test_eps_diluted_maps_to_eps(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [_make_metric_fact(aid, tag="EarningsPerShareDiluted", unit="USD/shares")]
            },
        )
        assert result.by_bucket.get("eps", 0) == 1

    def test_both_eps_aliases_sum_in_eps_bucket(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [
                    _make_metric_fact(aid, tag="EarningsPerShareBasic", unit="USD/shares"),
                    _make_metric_fact(aid, tag="EarningsPerShareDiluted", unit="USD/shares"),
                ]
            },
        )
        assert result.by_bucket["eps"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# AC 4: Operating cash flow and capex map separately
# ─────────────────────────────────────────────────────────────────────────────

class TestCashFlowAndCapex:
    def test_operating_cash_flow_maps_separately(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [
                    _make_metric_fact(
                        aid,
                        tag="NetCashProvidedByUsedInOperatingActivities",
                    )
                ]
            },
        )
        assert result.by_bucket.get("operating_cash_flow", 0) == 1
        assert result.by_bucket.get("capex", 0) == 0

    def test_capex_maps_separately(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [
                    _make_metric_fact(
                        aid,
                        tag="PaymentsToAcquirePropertyPlantAndEquipment",
                    )
                ]
            },
        )
        assert result.by_bucket.get("capex", 0) == 1
        assert result.by_bucket.get("operating_cash_flow", 0) == 0

    def test_both_cash_flow_and_capex_distinct(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [
                    _make_metric_fact(
                        aid,
                        tag="NetCashProvidedByUsedInOperatingActivities",
                    ),
                    _make_metric_fact(
                        aid,
                        tag="PaymentsToAcquirePropertyPlantAndEquipment",
                    ),
                ]
            },
        )
        assert result.by_bucket["operating_cash_flow"] == 1
        assert result.by_bucket["capex"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# AC 5: Balance sheet tags map separately to cash/assets/liabilities/equity
# ─────────────────────────────────────────────────────────────────────────────

class TestBalanceSheetBuckets:
    def test_cash_tag_maps_to_cash_bucket(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [_make_metric_fact(aid, tag="CashAndCashEquivalentsAtCarryingValue")]
            },
        )
        assert result.by_bucket.get("cash", 0) == 1

    def test_assets_maps_to_assets(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="Assets")]},
        )
        assert result.by_bucket.get("assets", 0) == 1

    def test_liabilities_maps_to_liabilities(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="Liabilities")]},
        )
        assert result.by_bucket.get("liabilities", 0) == 1

    def test_equity_maps_to_equity(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="StockholdersEquity")]},
        )
        assert result.by_bucket.get("equity", 0) == 1

    def test_all_four_balance_sheet_buckets_distinct(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [
                    _make_metric_fact(aid, tag="CashAndCashEquivalentsAtCarryingValue"),
                    _make_metric_fact(aid, tag="Assets"),
                    _make_metric_fact(aid, tag="Liabilities"),
                    _make_metric_fact(aid, tag="StockholdersEquity"),
                ]
            },
        )
        for bucket in ("cash", "assets", "liabilities", "equity"):
            assert result.by_bucket[bucket] == 1


# ─────────────────────────────────────────────────────────────────────────────
# AC 6: Unmapped tags counted as unmapped, not dropped
# ─────────────────────────────────────────────────────────────────────────────

class TestUnmappedTags:
    def test_unknown_tag_counted_as_unmapped(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [_make_metric_fact(aid, tag="SomeUnknownTag")]
            },
        )
        assert result.unmapped_metric_fact_count == 1
        assert result.by_tag.get("SomeUnknownTag", 0) == 1
        # Unmapped tag must NOT appear in by_bucket
        assert "SomeUnknownTag" not in result.by_bucket

    def test_unmapped_tag_still_counts_in_source_linked_total(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [_make_metric_fact(aid, tag="GoodwillAndIntangibleAssetsNet")]
            },
        )
        assert result.source_linked_metric_fact_count == 1
        assert result.unmapped_metric_fact_count == 1

    def test_mixed_mapped_and_unmapped(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [
                    _make_metric_fact(aid, tag="Revenues"),
                    _make_metric_fact(aid, tag="WeirdTag"),
                ]
            },
        )
        assert result.source_linked_metric_fact_count == 2
        assert result.unmapped_metric_fact_count == 1
        assert result.by_bucket.get("revenue", 0) == 1


# ─────────────────────────────────────────────────────────────────────────────
# AC 7: Missing tag/unit/form uses UNKNOWN safely — no crash
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingFieldsFallback:
    def test_none_tag_uses_unknown(self):
        aid = str(uuid.uuid4())
        fact = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": str(uuid.uuid4()),
            "structured_payload": {
                "claim": "sec_companyfact_observed",
                "tag": None,
                "unit": "USD",
                "form": "10-K",
            },
        }
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [fact]},
        )
        assert result.by_tag.get("UNKNOWN", 0) == 1
        assert result.unmapped_metric_fact_count == 1

    def test_none_unit_uses_unknown(self):
        aid = str(uuid.uuid4())
        fact = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": str(uuid.uuid4()),
            "structured_payload": {
                "claim": "sec_companyfact_observed",
                "tag": "Revenues",
                "unit": None,
                "form": "10-K",
            },
        }
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [fact]},
        )
        assert result.by_unit.get("UNKNOWN", 0) == 1

    def test_none_form_uses_unknown(self):
        aid = str(uuid.uuid4())
        fact = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": str(uuid.uuid4()),
            "structured_payload": {
                "claim": "sec_companyfact_observed",
                "tag": "Revenues",
                "unit": "USD",
                "form": None,
            },
        }
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [fact]},
        )
        assert result.by_form.get("UNKNOWN", 0) == 1

    def test_none_structured_payload_does_not_crash(self):
        aid = str(uuid.uuid4())
        fact = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": str(uuid.uuid4()),
            "structured_payload": None,
        }
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [fact]},
        )
        # None payload has claim=None → not sec_companyfact_observed → excluded
        assert result.source_linked_metric_fact_count == 0

    def test_empty_dict_payload_excluded(self):
        aid = str(uuid.uuid4())
        fact = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": str(uuid.uuid4()),
            "structured_payload": {},
        }
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [fact]},
        )
        # empty dict → claim is None → not sec_companyfact_observed → excluded
        assert result.source_linked_metric_fact_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# AC 8: Facts without source_id excluded from source-linked counts
# ─────────────────────────────────────────────────────────────────────────────

class TestSourceLinkedRequirement:
    def test_fact_with_no_source_id_excluded(self):
        aid = str(uuid.uuid4())
        fact = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": None,
            "structured_payload": {
                "claim": "sec_companyfact_observed",
                "tag": "Revenues",
                "unit": "USD",
                "form": "10-K",
            },
        }
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [fact]},
        )
        assert result.source_linked_metric_fact_count == 0
        assert result.by_ticker == {}
        assert result.by_bucket == {}
        assert result.by_tag == {}

    def test_fact_with_empty_source_id_excluded(self):
        aid = str(uuid.uuid4())
        fact = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": "",
            "structured_payload": {
                "claim": "sec_companyfact_observed",
                "tag": "Assets",
                "unit": "USD",
                "form": "10-Q",
            },
        }
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [fact]},
        )
        assert result.source_linked_metric_fact_count == 0

    def test_only_source_linked_facts_counted(self):
        aid = str(uuid.uuid4())
        linked = _make_metric_fact(aid, tag="Revenues", source_id=str(uuid.uuid4()))
        unlinked = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": None,
            "structured_payload": {
                "claim": "sec_companyfact_observed",
                "tag": "Assets",
                "unit": "USD",
                "form": "10-Q",
            },
        }
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [linked, unlinked]},
        )
        assert result.source_linked_metric_fact_count == 1
        assert result.by_bucket.get("revenue", 0) == 1
        assert "assets" not in result.by_bucket


# ─────────────────────────────────────────────────────────────────────────────
# AC 9: Non-metric facts are ignored
# ─────────────────────────────────────────────────────────────────────────────

class TestNonMetricFactsIgnored:
    def test_sourced_claim_fact_not_counted(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [_make_non_metric_fact(aid)]},
        )
        assert result.source_linked_metric_fact_count == 0
        assert result.by_bucket == {}
        assert result.by_tag == {}

    def test_non_companyfacts_claim_excluded(self):
        aid = str(uuid.uuid4())
        fact = _make_metric_fact(aid, claim="other_claim")
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [fact]},
        )
        assert result.source_linked_metric_fact_count == 0

    def test_only_metric_observation_kind_counted(self):
        aid = str(uuid.uuid4())
        fact = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "sourced_claim",
            "source_id": str(uuid.uuid4()),
            "structured_payload": {
                "claim": "sec_companyfact_observed",
                "tag": "Revenues",
                "unit": "USD",
                "form": "10-K",
            },
        }
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [fact]},
        )
        assert result.source_linked_metric_fact_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# AC 10: Raw metric values and structured_payload not returned
# ─────────────────────────────────────────────────────────────────────────────

class TestNoRawValuesReturned:
    def test_by_bucket_values_are_int_counts(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [_make_metric_fact(aid, tag="Revenues", value=9_999_999_999)]
            },
        )
        for k, v in result.by_bucket.items():
            assert isinstance(k, str)
            assert isinstance(v, int)
            assert v != 9_999_999_999

    def test_by_tag_values_are_int_counts(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [_make_metric_fact(aid, tag="Assets", value=1_234_567)]
            },
        )
        for v in result.by_tag.values():
            assert isinstance(v, int)
            assert v != 1_234_567

    def test_result_has_no_structured_payload_field(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [_make_metric_fact(aid, tag="NetIncomeLoss")]
            },
        )
        assert not hasattr(result, "structured_payload")
        assert not hasattr(result, "raw_metric_values")

    def test_result_has_no_source_url_field(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={
                aid: [_make_metric_fact(aid)]
            },
        )
        assert not hasattr(result, "source_url")
        assert not hasattr(result, "source_excerpt")


# ─────────────────────────────────────────────────────────────────────────────
# AC 11: Existing Phase 7C observability fields unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestPhase7CFieldsUnchanged:
    def test_by_metric_observation_tag_still_populated(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, tag="Revenues")],
        )
        result = _obs(db)
        assert result.by_metric_observation_tag.get("Revenues", 0) == 1

    def test_by_metric_observation_unit_still_populated(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, unit="USD")],
        )
        result = _obs(db)
        assert "USD" in result.by_metric_observation_unit

    def test_by_metric_observation_form_still_populated(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, form="10-K")],
        )
        result = _obs(db)
        assert "10-K" in result.by_metric_observation_form

    def test_artifacts_with_companyfacts_count_still_correct(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, claim="sec_companyfact_observed")],
        )
        result = _obs(db)
        assert result.artifacts_with_companyfacts_metric_observations_count == 1

    def test_metric_observation_fact_count_still_correct(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[
                _make_metric_fact(aid, tag="Revenues"),
                _make_metric_fact(aid, tag="Assets"),
                _make_non_metric_fact(aid),
            ],
        )
        result = _obs(db)
        assert result.metric_observation_fact_count == 2
        assert result.artifacts_with_metric_observations_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# AC 12: safe_for_decision remains False / decision consumption zero
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeForDecisionInvariant:
    def test_dry_run_safe_for_decision_always_false(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [_make_metric_fact(aid)]},
        )
        assert result.dry_run_safe_for_decision is False

    def test_observability_safe_for_decision_field_false(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid, )],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid)],
        )
        result = _obs(db)
        assert result.sec_metric_truth_adapter_dry_run_safe_for_decision is False
        assert result.safe_for_decision_false_count == 1
        assert result.unexpected_safe_for_decision_true_count == 0

    def test_eligible_for_decision_consumption_always_zero(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid)],
        )
        result = _obs(db)
        assert result.eligible_for_decision_consumption_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# AC 13: visible_snapshot_unchanged always True
# ─────────────────────────────────────────────────────────────────────────────

class TestVisibleSnapshotUnchanged:
    def test_dry_run_visible_snapshot_unchanged_always_true(self):
        aid = str(uuid.uuid4())
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid)],
            facts_by_artifact={aid: [_make_metric_fact(aid)]},
        )
        assert result.visible_snapshot_unchanged is True

    def test_observability_visible_snapshot_unchanged_true(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid)],
        )
        result = _obs(db)
        assert result.visible_snapshot_unchanged is True
        assert result.sec_metric_truth_adapter_visible_snapshot_unchanged is True
        assert result.readiness_visible_snapshot_unchanged is True


# ─────────────────────────────────────────────────────────────────────────────
# AC 14 / Static guard: no forbidden imports in dry-run module
# ─────────────────────────────────────────────────────────────────────────────

def _read_src(rel_path: str) -> str:
    import pathlib
    base = pathlib.Path(__file__).parent.parent
    return (base / rel_path).read_text()


class TestStaticImportGuards:
    def test_no_decide_import_in_dry_run_module(self):
        src = _read_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_truth_adapter_dry_run.py"
        )
        assert "from .decision_policy_v1 import" not in src
        assert "import decide" not in src
        # Must not import from decision_policy_v1 — docstring mentions it as forbidden.
        assert "from app.services.intelligence.v3.decision_policy_v1" not in src

    def test_no_intel_v3_service_import_in_dry_run_module(self):
        src = _read_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_truth_adapter_dry_run.py"
        )
        assert "import IntelV3Service" not in src
        assert "from .intel_v3_service" not in src
        assert "import recommendation_engine" not in src
        assert "from .recommendation_engine" not in src

    def test_no_db_write_in_dry_run_module(self):
        src = _read_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_truth_adapter_dry_run.py"
        )
        assert 'table("intel_v3_snapshots")' not in src
        assert 'table("research_artifacts")' not in src
        assert ".insert(" not in src
        assert ".update(" not in src
        assert ".upsert(" not in src

    def test_no_decide_import_in_artifact_observability(self):
        src = _read_src(
            "app/services/intelligence/research_workers/artifact_observability.py"
        )
        assert "from .decision_policy_v1 import" not in src
        assert "import decide" not in src
        assert "IntelV3Service" not in src
        assert "recommendation_engine" not in src


# ─────────────────────────────────────────────────────────────────────────────
# AC 16: ArtifactObservabilitySummary has all 12 Phase 8A fields with defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestSummaryDataclassFields:
    def test_all_phase8a_fields_exist_on_dataclass(self):
        import dataclasses
        from app.services.intelligence.research_workers.artifact_observability import (
            ArtifactObservabilitySummary,
        )
        names = {f.name for f in dataclasses.fields(ArtifactObservabilitySummary)}
        required = {
            "sec_metric_truth_adapter_dry_run_enabled",
            "sec_metric_truth_adapter_dry_run_safe_for_decision",
            "sec_metric_truth_adapter_artifacts_evaluated_count",
            "sec_metric_truth_adapter_source_linked_metric_fact_count",
            "sec_metric_truth_adapter_unmapped_metric_fact_count",
            "sec_metric_truth_adapter_by_ticker",
            "sec_metric_truth_adapter_by_bucket",
            "sec_metric_truth_adapter_by_tag",
            "sec_metric_truth_adapter_by_unit",
            "sec_metric_truth_adapter_by_form",
            "sec_metric_truth_adapter_missing_buckets_by_ticker",
            "sec_metric_truth_adapter_visible_snapshot_unchanged",
        }
        for name in required:
            assert name in names, f"Missing field: {name}"

    def test_phase8a_defaults_are_backward_compatible(self):
        from app.services.intelligence.research_workers.artifact_observability import (
            ArtifactObservabilitySummary,
        )
        s = ArtifactObservabilitySummary(
            observability_enabled=True,
            requested_tickers=[],
            normalized_tickers=[],
            lookback_days=30,
            max_rows=250,
            artifact_count=0,
            by_ticker={},
            by_artifact_type={},
            by_skill_pack={},
            by_confidence_or_trust_level={},
            by_freshness_status={},
            safe_for_decision_false_count=0,
            unexpected_safe_for_decision_true_count=0,
            forbidden_payload_violation_count=0,
            active_count=0,
            inactive_count=0,
            invalidated_count=0,
            expired_count=0,
            artifacts_with_sources_count=0,
            artifacts_without_sources_count=0,
            artifacts_with_facts_count=0,
            artifacts_without_facts_count=0,
            missing_evidence_count=0,
            visible_snapshot_unchanged=True,
        )
        assert s.sec_metric_truth_adapter_dry_run_enabled is False
        assert s.sec_metric_truth_adapter_dry_run_safe_for_decision is False
        assert s.sec_metric_truth_adapter_artifacts_evaluated_count == 0
        assert s.sec_metric_truth_adapter_source_linked_metric_fact_count == 0
        assert s.sec_metric_truth_adapter_unmapped_metric_fact_count == 0
        assert s.sec_metric_truth_adapter_by_ticker == {}
        assert s.sec_metric_truth_adapter_by_bucket == {}
        assert s.sec_metric_truth_adapter_by_tag == {}
        assert s.sec_metric_truth_adapter_by_unit == {}
        assert s.sec_metric_truth_adapter_by_form == {}
        assert s.sec_metric_truth_adapter_missing_buckets_by_ticker == {}
        assert s.sec_metric_truth_adapter_visible_snapshot_unchanged is True


# ─────────────────────────────────────────────────────────────────────────────
# AC 17 (Kill-switch): flag=False → Phase 8A fields zero/empty/False
# ─────────────────────────────────────────────────────────────────────────────

class TestKillSwitch:
    def test_flag_false_disables_dry_run(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, tag="Revenues")],
        )
        result = _obs(db, settings=_dry_run_disabled_settings())
        assert result.sec_metric_truth_adapter_dry_run_enabled is False
        assert result.sec_metric_truth_adapter_source_linked_metric_fact_count == 0
        assert result.sec_metric_truth_adapter_by_bucket == {}
        assert result.sec_metric_truth_adapter_by_ticker == {}

    def test_flag_false_preserves_phase7c_fields(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, tag="Revenues")],
        )
        result = _obs(db, settings=_dry_run_disabled_settings())
        # Phase 7C fields must still be populated regardless of Phase 8A flag.
        assert result.by_metric_observation_tag.get("Revenues", 0) == 1

    def test_flag_true_enables_dry_run(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, tag="Revenues")],
        )
        result = _obs(db, settings=_dry_run_enabled_settings())
        assert result.sec_metric_truth_adapter_dry_run_enabled is True
        assert result.sec_metric_truth_adapter_source_linked_metric_fact_count == 1
        assert result.sec_metric_truth_adapter_by_bucket.get("revenue", 0) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Multi-ticker / missing_buckets_by_ticker tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingBuckets:
    def test_missing_buckets_present_when_not_all_buckets_covered(self):
        aid = str(uuid.uuid4())
        # Only one tag (Revenues → revenue), so 9 buckets missing.
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="Revenues")]},
        )
        assert "AAPL" in result.missing_buckets_by_ticker
        missing = result.missing_buckets_by_ticker["AAPL"]
        assert "revenue" not in missing
        assert "net_income" in missing
        assert len(missing) == 9

    def test_no_missing_buckets_when_all_covered(self):
        aid = str(uuid.uuid4())
        tags = list(SEC_METRIC_BUCKET_MAP.keys())
        facts = [_make_metric_fact(aid, tag=t) for t in tags]
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: facts},
        )
        assert "NVDA" not in result.missing_buckets_by_ticker

    def test_missing_buckets_per_ticker_independent(self):
        aid1 = str(uuid.uuid4())
        aid2 = str(uuid.uuid4())
        # AAPL has revenue only; MSFT has assets only.
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[
                _make_artifact(aid1, ticker="AAPL"),
                _make_artifact(aid2, ticker="MSFT"),
            ],
            facts_by_artifact={
                aid1: [_make_metric_fact(aid1, tag="Revenues")],
                aid2: [_make_metric_fact(aid2, tag="Assets")],
            },
        )
        aapl_missing = result.missing_buckets_by_ticker.get("AAPL", [])
        msft_missing = result.missing_buckets_by_ticker.get("MSFT", [])
        assert "revenue" not in aapl_missing
        assert "assets" in aapl_missing
        assert "assets" not in msft_missing
        assert "revenue" in msft_missing


# ─────────────────────────────────────────────────────────────────────────────
# Invariant: dry_run_safe_for_decision always False even when input is manipulated
# ─────────────────────────────────────────────────────────────────────────────

class TestHardInvariants:
    def test_dry_run_safe_for_decision_false_on_empty_input(self):
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[],
            facts_by_artifact={},
        )
        assert result.dry_run_safe_for_decision is False

    def test_dry_run_visible_snapshot_unchanged_true_on_empty_input(self):
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[],
            facts_by_artifact={},
        )
        assert result.visible_snapshot_unchanged is True

    def test_artifacts_evaluated_count_matches_input(self):
        arts = [_make_artifact(str(uuid.uuid4()), ticker=t) for t in ["AAPL", "MSFT"]]
        result = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=arts,
            facts_by_artifact={},
        )
        assert result.artifacts_evaluated_count == 2
