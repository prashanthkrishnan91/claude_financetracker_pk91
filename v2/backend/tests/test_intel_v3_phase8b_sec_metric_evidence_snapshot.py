"""Phase 8B — SEC Metric Evidence Snapshot Dry Run tests.

Acceptance criteria:

1.  Ticker with all required groups becomes READY_DRY_RUN_ONLY.
2.  Ticker missing capex becomes PARTIAL_DRY_RUN_ONLY with missing_bucket_capex.
3.  Ticker with no source-linked mapped metric facts becomes BLOCKED_DRY_RUN_ONLY.
4.  Every ticker includes decision_consumption_disabled and safe_for_decision_db_lock.
5.  Present and missing buckets are deterministic sorted lists.
6.  Present and missing bucket groups are deterministic sorted lists.
7.  Forms and units are aggregate counts only.
8.  No raw metric values, structured_payload, source URLs, or raw rows are returned.
9.  Phase 8A fields remain unchanged.
10. Existing safe_for_decision and eligible_for_decision_consumption invariants unchanged.
11. visible_snapshot_unchanged remains True.
12. No decide(), IntelV3Service, recommendation_engine, or frontend imports introduced.
13. Disabled flag path returns zero/empty/false defaults safely.

Additional:
14. BLOCKED_DRY_RUN_ONLY when ticker has no source-linked facts.
15. Bucket group presence/absence detection is correct.
16. Income_statement_core readiness requires revenue + at least one of
    operating_income/net_income/eps.
17. cash_flow_core readiness requires both operating_cash_flow and capex.
18. balance_sheet_core readiness requires all four: assets, liabilities, equity, cash.
19. tickers_blocked_from_decision_count always equals tickers_evaluated_count.
20. snapshot_safe_for_decision is always False.
21. Phase 8B kill-switch: flag=False → all Phase 8B fields are zero/empty/False.
22. Phase 8B requires Phase 8A flag to also be True.
23. Static guard: no decide()/IntelV3Service imports in snapshot module.

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
    run_sec_metric_truth_adapter_dry_run,
)
from app.services.intelligence.research_workers.sec_metric_evidence_snapshot_dry_run import (
    BUCKET_GROUPS,
    run_sec_metric_evidence_snapshot_dry_run,
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


def _both_enabled(**overrides) -> Settings:
    return _base_settings(
        intel_v3_sec_metric_truth_adapter_dry_run_enabled=True,
        intel_v3_sec_metric_evidence_snapshot_dry_run_enabled=True,
        **overrides,
    )


def _8a_only(**overrides) -> Settings:
    return _base_settings(
        intel_v3_sec_metric_truth_adapter_dry_run_enabled=True,
        intel_v3_sec_metric_evidence_snapshot_dry_run_enabled=False,
        **overrides,
    )


def _both_disabled(**overrides) -> Settings:
    return _base_settings(
        intel_v3_sec_metric_truth_adapter_dry_run_enabled=False,
        intel_v3_sec_metric_evidence_snapshot_dry_run_enabled=False,
        **overrides,
    )


_UID = "u1"


# ── Artifact/fact helpers (same pattern as Phase 8A tests) ───────────────────

def _make_artifact(aid: str = None, ticker: str = "AAPL") -> dict:
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


# ── FakeDB (same structure as Phase 8A test infra) ────────────────────────────

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


# ── Observability helper ──────────────────────────────────────────────────────

def _obs(db, settings=None):
    from app.services.intelligence.research_workers.artifact_observability import (
        summarize_recent_research_artifacts,
    )
    return summarize_recent_research_artifacts(
        user_id=_UID,
        db_client=db,
        settings=settings or _both_enabled(),
    )


# ── Full-bucket fact set helpers ──────────────────────────────────────────────

_ALL_TAGS = list(SEC_METRIC_BUCKET_MAP.keys())

def _full_fact_set(aid: str) -> list[dict]:
    """Return one fact per SEC tag — covers all 10 expected buckets."""
    return [_make_metric_fact(aid, tag=t) for t in _ALL_TAGS]


def _facts_without_capex(aid: str) -> list[dict]:
    """All buckets except capex."""
    return [
        _make_metric_fact(aid, tag=t)
        for t in _ALL_TAGS
        if SEC_METRIC_BUCKET_MAP[t] != "capex"
    ]


# ── Helpers for direct unit function call ────────────────────────────────────

def _adapter_and_snapshot(artifact_rows, facts_by_artifact):
    """Run Phase 8A then Phase 8B, return snapshot result."""
    adapter = run_sec_metric_truth_adapter_dry_run(
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )
    return run_sec_metric_evidence_snapshot_dry_run(
        adapter_result=adapter,
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )


# =============================================================================
# AC 1: Ticker with all required groups → READY_DRY_RUN_ONLY
# =============================================================================

class TestReadyDryRunOnly:
    def test_all_buckets_present_gives_ready(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        snap = result.by_ticker["AAPL"]
        assert snap["future_adapter_readiness"] == "READY_DRY_RUN_ONLY"

    def test_ready_ticker_increments_ready_count(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="MSFT")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        assert result.tickers_ready_for_future_adapter_count == 1

    def test_income_statement_partial_sufficient_for_ready(self):
        """revenue + net_income (not all income tags) should still pass income group."""
        aid = str(uuid.uuid4())
        # All balance sheet + cash flow + revenue + net_income (not operating_income or eps).
        tags_needed = [
            "Revenues", "NetIncomeLoss",
            "NetCashProvidedByUsedInOperatingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "CashAndCashEquivalentsAtCarryingValue", "Assets",
            "Liabilities", "StockholdersEquity",
        ]
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="TEST")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag=t) for t in tags_needed]},
        )
        snap = result.by_ticker["TEST"]
        assert snap["future_adapter_readiness"] == "READY_DRY_RUN_ONLY"

    def test_revenue_alone_not_ready(self):
        """revenue without any income_statement_optional is not ready."""
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="SOLO")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="Revenues")]},
        )
        snap = result.by_ticker["SOLO"]
        assert snap["future_adapter_readiness"] != "READY_DRY_RUN_ONLY"


# =============================================================================
# AC 2: Ticker missing capex → PARTIAL_DRY_RUN_ONLY with missing_bucket_capex
# =============================================================================

class TestPartialDryRunOnly:
    def test_missing_capex_gives_partial(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        snap = result.by_ticker["NVDA"]
        assert snap["future_adapter_readiness"] == "PARTIAL_DRY_RUN_ONLY"

    def test_missing_capex_has_missing_bucket_capex_code(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        snap = result.by_ticker["NVDA"]
        assert "missing_bucket_capex" in snap["blocking_reason_codes"]

    def test_partial_ticker_not_counted_in_ready_count(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        assert result.tickers_ready_for_future_adapter_count == 0

    def test_single_mapped_fact_is_partial(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="X")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="Assets")]},
        )
        snap = result.by_ticker["X"]
        assert snap["future_adapter_readiness"] == "PARTIAL_DRY_RUN_ONLY"


# =============================================================================
# AC 3: Ticker with no source-linked mapped facts → BLOCKED_DRY_RUN_ONLY
# =============================================================================

class TestBlockedDryRunOnly:
    def test_no_source_linked_facts_gives_blocked(self):
        aid = str(uuid.uuid4())
        # Fact with no source_id — not source-linked.
        fact = _make_metric_fact(aid, tag="Revenues", source_id=None)
        fact["source_id"] = None
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="ZZZZ")],
            facts_by_artifact={aid: [fact]},
        )
        # ZZZZ is in artifact_rows → appears as BLOCKED.
        assert "ZZZZ" in result.by_ticker
        assert result.by_ticker["ZZZZ"]["future_adapter_readiness"] == "BLOCKED_DRY_RUN_ONLY"

    def test_ticker_with_no_facts_appears_as_blocked(self):
        """Ticker in artifact_rows but with no facts → BLOCKED_DRY_RUN_ONLY."""
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="EMPTY")],
            facts_by_artifact={aid: []},
        )
        assert "EMPTY" in result.by_ticker
        assert result.by_ticker["EMPTY"]["future_adapter_readiness"] == "BLOCKED_DRY_RUN_ONLY"
        assert result.tickers_evaluated_count == 1

    def test_blocked_ticker_has_zero_source_linked_fact_count(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED")],
            facts_by_artifact={aid: []},
        )
        assert result.by_ticker["BLOCKED"]["source_linked_metric_fact_count"] == 0

    def test_blocked_ticker_has_empty_present_buckets(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED")],
            facts_by_artifact={aid: []},
        )
        assert result.by_ticker["BLOCKED"]["present_buckets"] == []

    def test_blocked_ticker_has_all_expected_buckets_missing(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED")],
            facts_by_artifact={aid: []},
        )
        assert set(result.by_ticker["BLOCKED"]["missing_buckets"]) == EXPECTED_BUCKETS
        assert result.by_ticker["BLOCKED"]["missing_buckets"] == sorted(EXPECTED_BUCKETS)

    def test_blocked_ticker_has_empty_present_groups(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED")],
            facts_by_artifact={aid: []},
        )
        assert result.by_ticker["BLOCKED"]["present_bucket_groups"] == []

    def test_blocked_ticker_has_all_groups_missing(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED")],
            facts_by_artifact={aid: []},
        )
        assert set(result.by_ticker["BLOCKED"]["missing_bucket_groups"]) == set(BUCKET_GROUPS.keys())

    def test_blocked_ticker_has_empty_forms_and_units(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED")],
            facts_by_artifact={aid: []},
        )
        assert result.by_ticker["BLOCKED"]["forms"] == {}
        assert result.by_ticker["BLOCKED"]["units"] == {}

    def test_blocked_ticker_has_always_blocking_codes(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED")],
            facts_by_artifact={aid: []},
        )
        codes = result.by_ticker["BLOCKED"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes

    def test_blocked_ticker_has_missing_bucket_codes_for_all_buckets(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED")],
            facts_by_artifact={aid: []},
        )
        codes = result.by_ticker["BLOCKED"]["blocking_reason_codes"]
        for b in EXPECTED_BUCKETS:
            assert f"missing_bucket_{b}" in codes

    def test_blocked_ticker_not_counted_in_source_linked_evidence(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED")],
            facts_by_artifact={aid: []},
        )
        assert result.tickers_with_any_source_linked_evidence_count == 0

    def test_blocked_ticker_counted_in_evaluated_count(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED")],
            facts_by_artifact={aid: []},
        )
        assert result.tickers_evaluated_count == 1

    def test_empty_input_gives_zero_tickers(self):
        result = _adapter_and_snapshot(
            artifact_rows=[],
            facts_by_artifact={},
        )
        assert result.tickers_evaluated_count == 0
        assert result.by_ticker == {}

    def test_mixed_blocked_and_partial_tickers(self):
        """Blocked ticker (no facts) alongside a partial ticker (some facts)."""
        aid_blocked = str(uuid.uuid4())
        aid_partial = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[
                _make_artifact(aid_blocked, ticker="BLOCKED"),
                _make_artifact(aid_partial, ticker="NVDA"),
            ],
            facts_by_artifact={
                aid_blocked: [],
                aid_partial: [_make_metric_fact(aid_partial, tag="Assets")],
            },
        )
        assert result.by_ticker["BLOCKED"]["future_adapter_readiness"] == "BLOCKED_DRY_RUN_ONLY"
        assert result.by_ticker["NVDA"]["future_adapter_readiness"] == "PARTIAL_DRY_RUN_ONLY"
        assert result.tickers_evaluated_count == 2
        assert result.tickers_with_any_source_linked_evidence_count == 1
        assert result.tickers_blocked_from_decision_count == 2


# =============================================================================
# AC 4: Every ticker includes decision_consumption_disabled + safe_for_decision_db_lock
# =============================================================================

class TestAlwaysBlockingCodes:
    def test_ready_ticker_has_always_blocking_codes(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        codes = result.by_ticker["AAPL"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes

    def test_partial_ticker_has_always_blocking_codes(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        codes = result.by_ticker["NVDA"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes

    def test_ready_ticker_with_no_missing_buckets_has_only_always_codes(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="MSFT")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        codes = result.by_ticker["MSFT"]["blocking_reason_codes"]
        # No missing_bucket_* codes — only the two always-present codes.
        missing_bucket_codes = [c for c in codes if c.startswith("missing_bucket_")]
        assert missing_bucket_codes == []
        assert len(codes) == 2


# =============================================================================
# AC 5: Present and missing buckets are deterministic sorted lists
# =============================================================================

class TestBucketsSorted:
    def test_present_buckets_are_sorted(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        present = result.by_ticker["AAPL"]["present_buckets"]
        assert present == sorted(present)

    def test_missing_buckets_are_sorted(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        missing = result.by_ticker["NVDA"]["missing_buckets"]
        assert missing == sorted(missing)

    def test_present_plus_missing_equals_all_expected_buckets(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        snap = result.by_ticker["NVDA"]
        all_b = set(snap["present_buckets"]) | set(snap["missing_buckets"])
        assert all_b == EXPECTED_BUCKETS

    def test_full_coverage_gives_empty_missing_buckets(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        assert result.by_ticker["AAPL"]["missing_buckets"] == []

    def test_full_coverage_present_buckets_is_all_expected(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        assert set(result.by_ticker["AAPL"]["present_buckets"]) == EXPECTED_BUCKETS


# =============================================================================
# AC 6: Present and missing bucket groups are deterministic sorted lists
# =============================================================================

class TestBucketGroupsSorted:
    def test_present_bucket_groups_sorted(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        groups = result.by_ticker["AAPL"]["present_bucket_groups"]
        assert groups == sorted(groups)

    def test_missing_bucket_groups_sorted(self):
        aid = str(uuid.uuid4())
        # Only one fact — no groups fully present.
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="X")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="Assets")]},
        )
        groups = result.by_ticker["X"]["missing_bucket_groups"]
        assert groups == sorted(groups)

    def test_full_coverage_gives_all_three_groups_present(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        assert set(result.by_ticker["AAPL"]["present_bucket_groups"]) == set(BUCKET_GROUPS.keys())
        assert result.by_ticker["AAPL"]["missing_bucket_groups"] == []

    def test_missing_capex_still_has_cash_flow_core_in_present_if_ocf_present(self):
        """cash_flow_core is 'present' if any of its buckets is covered."""
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        # operating_cash_flow IS present → cash_flow_core group is "present".
        assert "cash_flow_core" in result.by_ticker["NVDA"]["present_bucket_groups"]

    def test_no_income_facts_cash_flow_core_missing(self):
        """Only balance sheet facts → income_statement_core and cash_flow_core missing."""
        aid = str(uuid.uuid4())
        balance_tags = [
            "CashAndCashEquivalentsAtCarryingValue", "Assets", "Liabilities", "StockholdersEquity"
        ]
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="BS")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag=t) for t in balance_tags]},
        )
        snap = result.by_ticker["BS"]
        assert "income_statement_core" in snap["missing_bucket_groups"]
        assert "cash_flow_core" in snap["missing_bucket_groups"]
        assert "balance_sheet_core" in snap["present_bucket_groups"]


# =============================================================================
# AC 7: Forms and units are aggregate counts only
# =============================================================================

class TestFormsAndUnitsAggregateOnly:
    def test_forms_are_int_counts(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: [
                _make_metric_fact(aid, tag="Revenues", form="10-K"),
                _make_metric_fact(aid, tag="Assets", form="10-Q"),
            ]},
        )
        forms = result.by_ticker["AAPL"]["forms"]
        assert forms["10-K"] == 1
        assert forms["10-Q"] == 1
        for v in forms.values():
            assert isinstance(v, int)

    def test_units_are_int_counts(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: [
                _make_metric_fact(aid, tag="Revenues", unit="USD"),
                _make_metric_fact(aid, tag="EarningsPerShareBasic", unit="USD/shares"),
            ]},
        )
        units = result.by_ticker["AAPL"]["units"]
        assert units["USD"] == 1
        assert units["USD/shares"] == 1
        for v in units.values():
            assert isinstance(v, int)

    def test_forms_and_units_not_raw_metric_values(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: [_make_metric_fact(aid, value=9_999_999_999)]},
        )
        snap = result.by_ticker["AAPL"]
        for v in snap["forms"].values():
            assert v != 9_999_999_999
        for v in snap["units"].values():
            assert v != 9_999_999_999

    def test_forms_per_ticker_are_independent(self):
        aid1 = str(uuid.uuid4())
        aid2 = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[
                _make_artifact(aid1, ticker="AAPL"),
                _make_artifact(aid2, ticker="MSFT"),
            ],
            facts_by_artifact={
                aid1: [_make_metric_fact(aid1, tag="Revenues", form="10-K")],
                aid2: [_make_metric_fact(aid2, tag="Assets", form="10-Q")],
            },
        )
        assert result.by_ticker["AAPL"]["forms"] == {"10-K": 1}
        assert result.by_ticker["MSFT"]["forms"] == {"10-Q": 1}


# =============================================================================
# AC 8: No raw metric values, structured_payload, source URLs, or raw rows returned
# =============================================================================

class TestNoRawDataReturned:
    def test_by_ticker_values_contain_no_structured_payload(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: [_make_metric_fact(aid)]},
        )
        snap = result.by_ticker["AAPL"]
        assert "structured_payload" not in snap
        assert "raw_metric_values" not in snap

    def test_by_ticker_values_contain_no_source_url(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: [_make_metric_fact(aid)]},
        )
        snap = result.by_ticker["AAPL"]
        assert "source_url" not in snap
        assert "source_excerpt" not in snap

    def test_result_has_no_raw_fields(self):
        aid = str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: [_make_metric_fact(aid)]},
        )
        assert not hasattr(result, "structured_payload")
        assert not hasattr(result, "raw_metric_values")
        assert not hasattr(result, "source_url")


# =============================================================================
# AC 9: Phase 8A fields remain unchanged after Phase 8B is added
# =============================================================================

class TestPhase8AFieldsUnchanged:
    def test_phase8a_adapter_fields_still_populated_via_observability(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, tag="Revenues")],
        )
        result = _obs(db)
        assert result.sec_metric_truth_adapter_dry_run_enabled is True
        assert result.sec_metric_truth_adapter_source_linked_metric_fact_count == 1
        assert result.sec_metric_truth_adapter_by_bucket.get("revenue", 0) == 1
        assert result.sec_metric_truth_adapter_by_ticker.get("AAPL", 0) == 1

    def test_phase8a_missing_buckets_still_correct(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            source_rows=[_make_source(aid)],
            fact_rows=_facts_without_capex(aid),
        )
        result = _obs(db)
        # NVDA should show capex as missing in Phase 8A field.
        assert "capex" in result.sec_metric_truth_adapter_missing_buckets_by_ticker.get("NVDA", [])

    def test_phase8a_visible_snapshot_unchanged_still_true(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid)],
        )
        result = _obs(db)
        assert result.sec_metric_truth_adapter_visible_snapshot_unchanged is True

    def test_phase8a_safe_for_decision_still_false(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid)],
        )
        result = _obs(db)
        assert result.sec_metric_truth_adapter_dry_run_safe_for_decision is False


# =============================================================================
# AC 10: safe_for_decision and eligible_for_decision_consumption invariants
# =============================================================================

class TestSafeForDecisionInvariant:
    def test_snapshot_safe_for_decision_always_false(self):
        aid = str(uuid.uuid4())
        adapter = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        result = run_sec_metric_evidence_snapshot_dry_run(
            adapter_result=adapter,
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        assert result.snapshot_safe_for_decision is False

    def test_observability_safe_for_decision_false(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid)],
        )
        result = _obs(db)
        assert result.sec_metric_evidence_snapshot_safe_for_decision is False

    def test_eligible_for_decision_consumption_zero(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid)],
        )
        result = _obs(db)
        assert result.eligible_for_decision_consumption_count == 0


# =============================================================================
# AC 11: visible_snapshot_unchanged always True
# =============================================================================

class TestVisibleSnapshotUnchanged:
    def test_snapshot_visible_snapshot_unchanged_always_true(self):
        aid = str(uuid.uuid4())
        adapter = run_sec_metric_truth_adapter_dry_run(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
        )
        result = run_sec_metric_evidence_snapshot_dry_run(
            adapter_result=adapter,
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_fact_set(aid)},
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
        assert result.sec_metric_evidence_snapshot_visible_snapshot_unchanged is True


# =============================================================================
# AC 12: Static guard — no forbidden imports in snapshot module
# =============================================================================

def _read_src(rel_path: str) -> str:
    import pathlib
    base = pathlib.Path(__file__).parent.parent
    return (base / rel_path).read_text()


class TestStaticImportGuards:
    def test_no_decide_import_in_snapshot_module(self):
        src = _read_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_evidence_snapshot_dry_run.py"
        )
        assert "from .decision_policy_v1 import" not in src
        assert "import decide" not in src
        assert "from app.services.intelligence.v3.decision_policy_v1" not in src

    def test_no_intel_v3_service_import_in_snapshot_module(self):
        src = _read_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_evidence_snapshot_dry_run.py"
        )
        assert "import IntelV3Service" not in src
        assert "from .intel_v3_service" not in src
        assert "import recommendation_engine" not in src

    def test_no_db_write_in_snapshot_module(self):
        src = _read_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_evidence_snapshot_dry_run.py"
        )
        assert 'table("intel_v3_snapshots")' not in src
        assert ".insert(" not in src
        assert ".update(" not in src
        assert ".upsert(" not in src


# =============================================================================
# AC 13: Kill switch — disabled flag path returns zero/empty/false defaults
# =============================================================================

class TestKillSwitch:
    def test_8b_flag_false_gives_empty_snapshot(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, tag="Revenues")],
        )
        result = _obs(db, settings=_8a_only())
        assert result.sec_metric_evidence_snapshot_dry_run_enabled is False
        assert result.sec_metric_evidence_snapshot_tickers_evaluated_count == 0
        assert result.sec_metric_evidence_snapshot_by_ticker == {}
        assert result.sec_metric_evidence_snapshot_safe_for_decision is False
        assert result.sec_metric_evidence_snapshot_visible_snapshot_unchanged is True

    def test_8b_flag_false_preserves_8a_fields(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, tag="Revenues")],
        )
        result = _obs(db, settings=_8a_only())
        # Phase 8A should still work.
        assert result.sec_metric_truth_adapter_dry_run_enabled is True
        assert result.sec_metric_truth_adapter_source_linked_metric_fact_count == 1

    def test_both_flags_false_gives_all_empty(self):
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, tag="Revenues")],
        )
        result = _obs(db, settings=_both_disabled())
        assert result.sec_metric_truth_adapter_dry_run_enabled is False
        assert result.sec_metric_evidence_snapshot_dry_run_enabled is False
        assert result.sec_metric_evidence_snapshot_by_ticker == {}

    def test_8b_requires_8a_to_be_running(self):
        """Phase 8B never runs when Phase 8A flag is off (dry_run_result is None)."""
        aid = str(uuid.uuid4())
        db = _FakeDB(
            artifact_rows=[_make_artifact(aid)],
            source_rows=[_make_source(aid)],
            fact_rows=[_make_metric_fact(aid, tag="Revenues")],
        )
        # 8A off, 8B on — 8B cannot run because dry_run_result is None.
        settings = _base_settings(
            intel_v3_sec_metric_truth_adapter_dry_run_enabled=False,
            intel_v3_sec_metric_evidence_snapshot_dry_run_enabled=True,
        )
        result = _obs(db, settings=settings)
        assert result.sec_metric_evidence_snapshot_dry_run_enabled is False


# =============================================================================
# AC 19: tickers_blocked_from_decision_count always equals tickers_evaluated_count
# =============================================================================

class TestBlockedCountInvariant:
    def test_blocked_count_equals_evaluated_count_ready_tickers(self):
        aids = [str(uuid.uuid4()) for _ in range(3)]
        tickers = ["AAPL", "MSFT", "GOOG"]
        artifact_rows = [_make_artifact(a, t) for a, t in zip(aids, tickers)]
        facts_by_artifact = {a: _full_fact_set(a) for a in aids}
        result = _adapter_and_snapshot(artifact_rows, facts_by_artifact)
        assert result.tickers_blocked_from_decision_count == result.tickers_evaluated_count
        assert result.tickers_evaluated_count == 3

    def test_blocked_count_equals_evaluated_count_mixed(self):
        aid1, aid2 = str(uuid.uuid4()), str(uuid.uuid4())
        result = _adapter_and_snapshot(
            artifact_rows=[
                _make_artifact(aid1, "AAPL"),
                _make_artifact(aid2, "NVDA"),
            ],
            facts_by_artifact={
                aid1: _full_fact_set(aid1),
                aid2: _facts_without_capex(aid2),
            },
        )
        assert result.tickers_blocked_from_decision_count == result.tickers_evaluated_count


# =============================================================================
# Additional: AAPL/MSFT READY, NVDA PARTIAL (expected production outcome)
# =============================================================================

class TestProductionExpectedOutcome:
    def test_aapl_msft_ready_nvda_partial(self):
        """Mirrors expected Phase 8A production data: NVDA missing capex."""
        aids = {t: str(uuid.uuid4()) for t in ["AAPL", "MSFT", "NVDA"]}
        artifact_rows = [_make_artifact(aids[t], ticker=t) for t in aids]
        facts_by_artifact = {
            aids["AAPL"]: _full_fact_set(aids["AAPL"]),
            aids["MSFT"]: _full_fact_set(aids["MSFT"]),
            aids["NVDA"]: _facts_without_capex(aids["NVDA"]),
        }
        result = _adapter_and_snapshot(artifact_rows, facts_by_artifact)

        assert result.by_ticker["AAPL"]["future_adapter_readiness"] == "READY_DRY_RUN_ONLY"
        assert result.by_ticker["MSFT"]["future_adapter_readiness"] == "READY_DRY_RUN_ONLY"
        assert result.by_ticker["NVDA"]["future_adapter_readiness"] == "PARTIAL_DRY_RUN_ONLY"
        assert "missing_bucket_capex" in result.by_ticker["NVDA"]["blocking_reason_codes"]
        assert result.tickers_ready_for_future_adapter_count == 2
        assert result.tickers_blocked_from_decision_count == 3

    def test_all_three_tickers_have_always_blocking_codes(self):
        aids = {t: str(uuid.uuid4()) for t in ["AAPL", "MSFT", "NVDA"]}
        artifact_rows = [_make_artifact(aids[t], ticker=t) for t in aids]
        facts_by_artifact = {
            aids["AAPL"]: _full_fact_set(aids["AAPL"]),
            aids["MSFT"]: _full_fact_set(aids["MSFT"]),
            aids["NVDA"]: _facts_without_capex(aids["NVDA"]),
        }
        result = _adapter_and_snapshot(artifact_rows, facts_by_artifact)
        for ticker in ["AAPL", "MSFT", "NVDA"]:
            codes = result.by_ticker[ticker]["blocking_reason_codes"]
            assert "decision_consumption_disabled" in codes
            assert "safe_for_decision_db_lock" in codes


# =============================================================================
# Additional: ArtifactObservabilitySummary has Phase 8B fields with correct defaults
# =============================================================================

class TestSummaryDataclassPhase8BFields:
    def test_all_phase8b_fields_exist_on_dataclass(self):
        import dataclasses
        from app.services.intelligence.research_workers.artifact_observability import (
            ArtifactObservabilitySummary,
        )
        names = {f.name for f in dataclasses.fields(ArtifactObservabilitySummary)}
        required = {
            "sec_metric_evidence_snapshot_dry_run_enabled",
            "sec_metric_evidence_snapshot_safe_for_decision",
            "sec_metric_evidence_snapshot_visible_snapshot_unchanged",
            "sec_metric_evidence_snapshot_tickers_evaluated_count",
            "sec_metric_evidence_snapshot_tickers_with_any_source_linked_evidence_count",
            "sec_metric_evidence_snapshot_tickers_ready_for_future_adapter_count",
            "sec_metric_evidence_snapshot_tickers_blocked_from_decision_count",
            "sec_metric_evidence_snapshot_by_ticker",
        }
        for name in required:
            assert name in names, f"Missing Phase 8B field: {name}"

    def test_phase8b_defaults_backward_compatible(self):
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
        assert s.sec_metric_evidence_snapshot_dry_run_enabled is False
        assert s.sec_metric_evidence_snapshot_safe_for_decision is False
        assert s.sec_metric_evidence_snapshot_visible_snapshot_unchanged is True
        assert s.sec_metric_evidence_snapshot_tickers_evaluated_count == 0
        assert s.sec_metric_evidence_snapshot_tickers_with_any_source_linked_evidence_count == 0
        assert s.sec_metric_evidence_snapshot_tickers_ready_for_future_adapter_count == 0
        assert s.sec_metric_evidence_snapshot_tickers_blocked_from_decision_count == 0
        assert s.sec_metric_evidence_snapshot_by_ticker == {}
