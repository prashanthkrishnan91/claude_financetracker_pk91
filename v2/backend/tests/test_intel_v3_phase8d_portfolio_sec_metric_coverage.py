"""Phase 8D — Portfolio SEC Metric Coverage Diagnostics tests.

Acceptance criteria (15 total):

 1. Portfolio tickers with READY snapshot are counted as ready.
 2. Portfolio tickers with PARTIAL snapshot are counted as partial.
 3. Portfolio tickers with artifacts but no source-linked metric facts are
    counted as blocked.
 4. Portfolio tickers with no research artifacts are included and counted
    as missing coverage.
 5. Tickers without coverage include missing_sec_research_artifact.
 6. All returned tickers include decision_consumption_disabled and
    safe_for_decision_db_lock.
 7. Coverage counts are deterministic.
 8. Output order is deterministic.
 9. No raw metric values, structured_payload, source URLs, or raw rows
    are returned.
10. The module does not call SEC providers, LLMs, decide(), IntelV3Service,
    or recommendation_engine (static import guard).
11. safe_for_decision remains False.
12. eligible_for_decision_consumption is absent from decision-producing
    semantics (coverage module has no such field).
13. visible_snapshot_unchanged remains True.
14. Existing Phase 8A, 8B, and 8C tests still pass (enforced by running them).
15. Disabled flag path returns zero/empty/false safely.

Architecture invariants verified by this file:
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER writes to any DB table.
    - NEVER sets safe_for_decision=True.
    - safe_for_decision always False.
    - visible_snapshot_unchanged always True.

All pure-function tests use in-memory fixtures — no Supabase dependency.
"""
from __future__ import annotations

import ast
import copy
import pathlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# ── Imports under test ────────────────────────────────────────────────────────

from app.services.intelligence.research_workers.sec_metric_portfolio_coverage_dry_run import (
    SEC_METRIC_PORTFOLIO_COVERAGE_DRY_RUN_CONTRACT_VERSION,
    PortfolioSecCoverageDryRunResult,
    build_portfolio_sec_coverage_dry_run,
    compute_portfolio_sec_metric_coverage,
)
from app.services.intelligence.research_workers.sec_metric_truth_adapter_dry_run import (
    EXPECTED_BUCKETS,
    SEC_METRIC_BUCKET_MAP,
    run_sec_metric_truth_adapter_dry_run,
)
from app.services.intelligence.research_workers.sec_metric_evidence_snapshot_dry_run import (
    run_sec_metric_evidence_snapshot_dry_run,
)

_ALL_TAGS = list(SEC_METRIC_BUCKET_MAP.keys())
_UID = "u_phase8d"


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _aid() -> str:
    return str(uuid.uuid4())


def _make_artifact(aid: str, ticker: str = "TICK") -> dict:
    return {
        "id": aid,
        "user_id": _UID,
        "ticker": ticker,
        "artifact_type": "catalyst_window",
        "skill_pack": "earnings_reviewer",
        "safe_for_decision": False,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _make_metric_fact(
    artifact_id: str,
    tag: str = "Revenues",
    unit: str = "USD",
    form: str = "10-K",
    source_id: Optional[str] = None,
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "user_id": _UID,
        "artifact_id": artifact_id,
        "fact_kind": "metric_observation",
        "source_id": source_id if source_id is not None else str(uuid.uuid4()),
        "structured_payload": {
            "claim": "sec_companyfact_observed",
            "taxonomy": "us-gaap",
            "tag": tag,
            "label": tag,
            "value": 123456789,
            "unit": unit,
            "form": form,
            "filed": "2024-11-01",
        },
    }


def _full_facts(aid: str) -> list[dict]:
    """One fact per mapped SEC tag — covers all expected buckets."""
    return [_make_metric_fact(aid, tag=t) for t in _ALL_TAGS]


def _facts_without_capex(aid: str) -> list[dict]:
    return [
        _make_metric_fact(aid, tag=t)
        for t in _ALL_TAGS
        if SEC_METRIC_BUCKET_MAP[t] != "capex"
    ]


def _run_phase8b(artifact_rows: list[dict], facts_by_artifact: dict) -> dict:
    """Run Phase 8A + 8B; return Phase 8B by_ticker."""
    adapter = run_sec_metric_truth_adapter_dry_run(
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )
    snapshot = run_sec_metric_evidence_snapshot_dry_run(
        adapter_result=adapter,
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )
    return snapshot.by_ticker


def _coverage(
    portfolio_positions: list[dict],
    snapshot_by_ticker: dict,
    coverage_enabled: bool = True,
) -> PortfolioSecCoverageDryRunResult:
    return build_portfolio_sec_coverage_dry_run(
        portfolio_positions=portfolio_positions,
        snapshot_by_ticker=snapshot_by_ticker,
        coverage_enabled=coverage_enabled,
    )


# ── Settings stub ─────────────────────────────────────────────────────────────

@dataclass
class _StubSettings:
    intel_v3_sec_metric_portfolio_coverage_dry_run_enabled: bool = True


# ── Fake DB client ────────────────────────────────────────────────────────────

class _FakeTable:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._filters: list[tuple] = []

    def select(self, *args):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, vals))
        return self

    def order(self, col, **kwargs):
        return self

    def limit(self, n):
        return self

    def execute(self):
        filtered = self._rows
        for op, col, val in self._filters:
            if op == "eq":
                filtered = [r for r in filtered if str(r.get(col, "")) == str(val)]
            elif op == "in":
                filtered = [r for r in filtered if r.get(col) in val]

        @dataclass
        class _Result:
            data: list[dict]

        return _Result(data=list(filtered))


class _FakeDbClient:
    def __init__(self, tables: dict[str, list[dict]]):
        self._tables = tables

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self._tables.get(name, []))


# =============================================================================
# Contract version constant
# =============================================================================

class TestContractVersion:
    def test_contract_version_exists(self):
        assert SEC_METRIC_PORTFOLIO_COVERAGE_DRY_RUN_CONTRACT_VERSION == "phase8d_v1"

    def test_contract_version_is_string(self):
        assert isinstance(SEC_METRIC_PORTFOLIO_COVERAGE_DRY_RUN_CONTRACT_VERSION, str)


# =============================================================================
# AC 1 — Portfolio tickers with READY snapshot counted as ready
# =============================================================================

class TestReadyTickerCounting:
    def test_ready_ticker_counted_as_ready(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.tickers_ready_for_future_adapter_count == 1
        assert result.tickers_partial_for_future_adapter_count == 0
        assert result.tickers_blocked_for_future_adapter_count == 0

    def test_ready_ticker_readiness_in_by_ticker(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="MSFT")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "MSFT", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.by_ticker["MSFT"]["future_adapter_readiness"] == "READY_DRY_RUN_ONLY"

    def test_ready_ticker_has_research_artifacts(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.by_ticker["AAPL"]["has_research_artifacts"] is True

    def test_ready_counts_in_readiness_counts(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.readiness_counts.get("READY_DRY_RUN_ONLY") == 1
        assert result.readiness_counts.get("PARTIAL_DRY_RUN_ONLY") == 0
        assert result.readiness_counts.get("BLOCKED_DRY_RUN_ONLY") == 0


# =============================================================================
# AC 2 — Portfolio tickers with PARTIAL snapshot counted as partial
# =============================================================================

class TestPartialTickerCounting:
    def test_partial_ticker_counted_as_partial(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "NVDA", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.tickers_partial_for_future_adapter_count == 1
        assert result.tickers_ready_for_future_adapter_count == 0
        assert result.tickers_blocked_for_future_adapter_count == 0

    def test_partial_ticker_readiness_in_by_ticker(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "NVDA", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.by_ticker["NVDA"]["future_adapter_readiness"] == "PARTIAL_DRY_RUN_ONLY"

    def test_partial_ticker_has_source_linked_evidence(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "NVDA", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.by_ticker["NVDA"]["has_source_linked_metric_evidence"] is True
        assert result.tickers_with_source_linked_metric_evidence_count == 1

    def test_partial_missing_capex_in_missing_buckets(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "NVDA", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert "capex" in result.by_ticker["NVDA"]["missing_buckets"]


# =============================================================================
# AC 3 — Tickers with artifacts but no source-linked metric facts → blocked
# =============================================================================

class TestBlockedWithArtifactsNoSourceLinked:
    def test_artifact_no_source_linked_facts_counted_as_blocked(self):
        aid = _aid()
        unlinked = _make_metric_fact(aid, tag="Revenues")
        unlinked["source_id"] = None
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="BLK1")],
            facts_by_artifact={aid: [unlinked]},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "BLK1", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.tickers_blocked_for_future_adapter_count == 1
        assert result.by_ticker["BLK1"]["future_adapter_readiness"] == "BLOCKED_DRY_RUN_ONLY"

    def test_artifact_empty_facts_counted_as_blocked(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="EMPTYBLK")],
            facts_by_artifact={aid: []},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "EMPTYBLK", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.tickers_blocked_for_future_adapter_count == 1
        assert result.by_ticker["EMPTYBLK"]["has_research_artifacts"] is True
        assert result.by_ticker["EMPTYBLK"]["has_source_linked_metric_evidence"] is False

    def test_blocked_with_artifact_not_in_without_coverage_list(self):
        """Tickers with artifacts (but blocked) are NOT in tickers_without_sec_metric_coverage."""
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="HASART")],
            facts_by_artifact={aid: []},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "HASART", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        # Has artifact (even empty), so NOT in the missing coverage list.
        assert "HASART" not in result.tickers_without_sec_metric_coverage
        assert result.tickers_with_research_artifacts_count == 1
        assert result.tickers_without_research_artifacts_count == 0


# =============================================================================
# AC 4 — Portfolio tickers with no research artifacts are included + counted
# =============================================================================

class TestNoArtifactTickers:
    def test_ticker_without_artifacts_included_in_by_ticker(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "UNKNOWN_CO", "category": "Core"}],
            snapshot_by_ticker={},  # no artifacts for this ticker
        )
        assert "UNKNOWN_CO" in result.by_ticker

    def test_ticker_without_artifacts_counted_as_without(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "NOART", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert result.tickers_without_research_artifacts_count == 1
        assert result.tickers_with_research_artifacts_count == 0

    def test_ticker_without_artifacts_readiness_blocked(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "NOART", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert result.by_ticker["NOART"]["future_adapter_readiness"] == "BLOCKED_DRY_RUN_ONLY"

    def test_ticker_without_artifacts_fact_count_zero(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "NOART", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert result.by_ticker["NOART"]["source_linked_metric_fact_count"] == 0
        assert result.by_ticker["NOART"]["has_research_artifacts"] is False
        assert result.by_ticker["NOART"]["has_source_linked_metric_evidence"] is False

    def test_ticker_without_artifacts_all_buckets_missing(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "NOART", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert set(result.by_ticker["NOART"]["missing_buckets"]) == EXPECTED_BUCKETS
        assert result.by_ticker["NOART"]["present_buckets"] == []


# =============================================================================
# AC 5 — Tickers without coverage include missing_sec_research_artifact
# =============================================================================

class TestMissingSecResearchArtifactCode:
    def test_no_artifact_ticker_has_missing_code(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "NOCO", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert "missing_sec_research_artifact" in result.by_ticker["NOCO"]["blocking_reason_codes"]

    def test_ticker_with_artifact_does_not_have_missing_code(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="HASART")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "HASART", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert "missing_sec_research_artifact" not in result.by_ticker["HASART"]["blocking_reason_codes"]

    def test_no_artifact_ticker_in_tickers_without_coverage(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "NOCO", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert "NOCO" in result.tickers_without_sec_metric_coverage

    def test_mixed_portfolio_without_coverage_list_correct(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[
                {"ticker": "AAPL", "category": "Core"},
                {"ticker": "NOART_X", "category": "Core"},
            ],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert "NOART_X" in result.tickers_without_sec_metric_coverage
        assert "AAPL" not in result.tickers_without_sec_metric_coverage


# =============================================================================
# AC 6 — All returned tickers include decision_consumption_disabled and
#         safe_for_decision_db_lock
# =============================================================================

class TestAlwaysBlockingCodes:
    def test_always_blocking_codes_on_ready_ticker(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        codes = result.by_ticker["AAPL"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes

    def test_always_blocking_codes_on_partial_ticker(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "NVDA", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        codes = result.by_ticker["NVDA"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes

    def test_always_blocking_codes_on_no_artifact_ticker(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "NOART", "category": "Core"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["NOART"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes

    def test_always_blocking_codes_on_all_tickers_in_mixed_portfolio(self):
        aid_r = _aid()
        aid_p = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[
                _make_artifact(aid_r, ticker="READY_T"),
                _make_artifact(aid_p, ticker="PARTIAL_T"),
            ],
            facts_by_artifact={
                aid_r: _full_facts(aid_r),
                aid_p: _facts_without_capex(aid_p),
            },
        )
        result = _coverage(
            portfolio_positions=[
                {"ticker": "READY_T", "category": "Core"},
                {"ticker": "PARTIAL_T", "category": "Core"},
                {"ticker": "NOART_T", "category": "Core"},
            ],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        for ticker in ["READY_T", "PARTIAL_T", "NOART_T"]:
            codes = result.by_ticker[ticker]["blocking_reason_codes"]
            assert "decision_consumption_disabled" in codes, f"Missing on {ticker}"
            assert "safe_for_decision_db_lock" in codes, f"Missing on {ticker}"


# =============================================================================
# AC 7 — Coverage counts are deterministic
# =============================================================================

class TestDeterministicCounts:
    def _build_mixed(self):
        aid_r = _aid()
        aid_p = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[
                _make_artifact(aid_r, ticker="READY_D"),
                _make_artifact(aid_p, ticker="PARTIAL_D"),
            ],
            facts_by_artifact={
                aid_r: _full_facts(aid_r),
                aid_p: _facts_without_capex(aid_p),
            },
        )
        positions = [
            {"ticker": "READY_D", "category": "Core"},
            {"ticker": "PARTIAL_D", "category": "Core"},
            {"ticker": "NOART_D", "category": "Core"},
        ]
        return positions, snapshot_by_ticker

    def test_counts_deterministic_across_two_runs(self):
        positions, snap = self._build_mixed()
        r1 = _coverage(positions, snap)
        r2 = _coverage(positions, snap)
        assert r1.tickers_ready_for_future_adapter_count == r2.tickers_ready_for_future_adapter_count
        assert r1.tickers_partial_for_future_adapter_count == r2.tickers_partial_for_future_adapter_count
        assert r1.tickers_blocked_for_future_adapter_count == r2.tickers_blocked_for_future_adapter_count
        assert r1.tickers_without_research_artifacts_count == r2.tickers_without_research_artifacts_count

    def test_counts_sum_to_portfolio_ticker_count(self):
        positions, snap = self._build_mixed()
        r = _coverage(positions, snap)
        total = (
            r.tickers_ready_for_future_adapter_count
            + r.tickers_partial_for_future_adapter_count
            + r.tickers_blocked_for_future_adapter_count
        )
        assert total == r.portfolio_ticker_count

    def test_with_without_research_artifacts_sum_to_portfolio_count(self):
        positions, snap = self._build_mixed()
        r = _coverage(positions, snap)
        assert (
            r.tickers_with_research_artifacts_count + r.tickers_without_research_artifacts_count
            == r.portfolio_ticker_count
        )

    def test_mixed_portfolio_correct_distribution(self):
        positions, snap = self._build_mixed()
        r = _coverage(positions, snap)
        assert r.tickers_ready_for_future_adapter_count == 1
        assert r.tickers_partial_for_future_adapter_count == 1
        assert r.tickers_blocked_for_future_adapter_count == 1  # NOART_D
        assert r.tickers_with_research_artifacts_count == 2
        assert r.tickers_without_research_artifacts_count == 1
        assert r.portfolio_ticker_count == 3


# =============================================================================
# AC 8 — Output order is deterministic
# =============================================================================

class TestDeterministicOrder:
    def test_portfolio_tickers_evaluated_is_sorted(self):
        result = _coverage(
            portfolio_positions=[
                {"ticker": "ZZZ", "category": "Core"},
                {"ticker": "AAA", "category": "Core"},
                {"ticker": "MMM", "category": "Core"},
            ],
            snapshot_by_ticker={},
        )
        tickers = result.portfolio_tickers_evaluated
        assert tickers == sorted(tickers)

    def test_tickers_without_coverage_is_sorted(self):
        result = _coverage(
            portfolio_positions=[
                {"ticker": "ZZZ", "category": "Core"},
                {"ticker": "AAA", "category": "Core"},
            ],
            snapshot_by_ticker={},
        )
        lst = result.tickers_without_sec_metric_coverage
        assert lst == sorted(lst)

    def test_blocking_codes_are_sorted(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "T1", "category": "Core"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["T1"]["blocking_reason_codes"]
        assert codes == sorted(codes)

    def test_present_buckets_sorted(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="READY_S")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "READY_S", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        pb = result.by_ticker["READY_S"]["present_buckets"]
        assert pb == sorted(pb)

    def test_missing_buckets_sorted(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "NOART", "category": "Core"}],
            snapshot_by_ticker={},
        )
        mb = result.by_ticker["NOART"]["missing_buckets"]
        assert mb == sorted(mb)

    def test_reordered_input_produces_same_sorted_output(self):
        positions_ab = [
            {"ticker": "ZZZ", "category": "Core"},
            {"ticker": "AAA", "category": "Core"},
        ]
        positions_ba = [
            {"ticker": "AAA", "category": "Core"},
            {"ticker": "ZZZ", "category": "Core"},
        ]
        r1 = _coverage(positions_ab, {})
        r2 = _coverage(positions_ba, {})
        assert r1.portfolio_tickers_evaluated == r2.portfolio_tickers_evaluated
        assert r1.by_ticker == r2.by_ticker


# =============================================================================
# AC 9 — No raw metric values, structured_payload, source URLs, raw rows
# =============================================================================

class TestNoRawDataExposed:
    def test_result_has_no_structured_payload_field(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "NORAW", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert not hasattr(result, "structured_payload")
        assert not hasattr(result, "raw_metric_values")
        assert not hasattr(result, "source_url")

    def test_by_ticker_has_no_structured_payload(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "NORAW", "category": "Core"}],
            snapshot_by_ticker={},
        )
        ticker_dict = result.by_ticker["NORAW"]
        assert "structured_payload" not in ticker_dict
        assert "raw_metric_values" not in ticker_dict
        assert "source_url" not in ticker_dict
        assert "source_excerpt" not in ticker_dict
        assert "data" not in ticker_dict
        assert "rows" not in ticker_dict

    def test_fact_count_is_integer_not_metric_value(self):
        aid = _aid()
        # Value in the metric fact should never appear as fact_count.
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="NORAW2")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="Revenues")]},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "NORAW2", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        fact_count = result.by_ticker["NORAW2"]["source_linked_metric_fact_count"]
        assert isinstance(fact_count, int)
        # The raw value 123456789 should never appear as the count.
        assert fact_count != 123456789

    def test_result_fields_are_counts_not_raw_values(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "T", "category": "Core"}],
            snapshot_by_ticker={},
        )
        for field_name in [
            "portfolio_ticker_count",
            "tickers_with_research_artifacts_count",
            "tickers_without_research_artifacts_count",
            "tickers_ready_for_future_adapter_count",
            "tickers_partial_for_future_adapter_count",
            "tickers_blocked_for_future_adapter_count",
        ]:
            val = getattr(result, field_name)
            assert isinstance(val, int)
            # None of these should equal a raw metric value like 123456789.
            assert val != 123456789


# =============================================================================
# AC 10 — No forbidden imports in coverage module
# =============================================================================

def _read_module_src(rel_path: str) -> str:
    base = pathlib.Path(__file__).parent.parent
    return (base / rel_path).read_text()


class TestStaticImportGuardsPhase8D:
    """Verify the coverage module and this test file have no forbidden imports."""

    def test_no_decide_in_coverage_module(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_portfolio_coverage_dry_run.py"
        )
        assert "from .decision_policy_v1 import" not in src
        assert "import decide" not in src

    def test_no_intel_v3_service_in_coverage_module(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_portfolio_coverage_dry_run.py"
        )
        assert "import IntelV3Service" not in src
        assert "from .intel_v3_service" not in src
        assert "import recommendation_engine" not in src
        assert "from .recommendation_engine" not in src

    def test_no_db_write_in_coverage_module(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_portfolio_coverage_dry_run.py"
        )
        # Check for supabase-style DB write operations (not Python set/dict .update()).
        assert ".insert(" not in src
        assert ".upsert(" not in src
        # Verify no chained supabase table write: pattern is table(...).update( after .eq/.in_
        # We check the module has no delete or insert operations.
        assert ".delete(" not in src

    def test_no_sec_provider_call_in_coverage_module(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_portfolio_coverage_dry_run.py"
        )
        assert "sec_edgar_provider" not in src
        assert "fetch_company_facts" not in src
        assert "fetch_filings" not in src

    def test_phase8d_test_file_has_no_forbidden_imports(self):
        src = _read_module_src(
            "tests/test_intel_v3_phase8d_portfolio_sec_metric_coverage.py"
        )
        tree = ast.parse(src)
        imported_names: list[str] = []
        imported_froms: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_froms.append(node.module or "")
        all_imports = " ".join(imported_names + imported_froms)
        assert "decision_policy_v1" not in all_imports
        assert "intel_v3_service" not in all_imports
        assert "recommendation_engine" not in all_imports


# =============================================================================
# AC 11 — safe_for_decision remains False
# =============================================================================

class TestSafeForDecisionFalse:
    def test_safe_for_decision_false_on_ready_portfolio(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="SAFE_R")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "SAFE_R", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.safe_for_decision is False

    def test_safe_for_decision_false_on_empty_portfolio(self):
        result = _coverage(portfolio_positions=[], snapshot_by_ticker={})
        assert result.safe_for_decision is False

    def test_safe_for_decision_false_on_all_blocked(self):
        result = _coverage(
            portfolio_positions=[
                {"ticker": "A", "category": "Core"},
                {"ticker": "B", "category": "Core"},
            ],
            snapshot_by_ticker={},
        )
        assert result.safe_for_decision is False

    def test_disabled_result_safe_for_decision_false(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker={},
            coverage_enabled=False,
        )
        # build_portfolio_sec_coverage_dry_run with coverage_enabled=False
        # still runs computation but reports coverage_enabled=False.
        assert result.safe_for_decision is False


# =============================================================================
# AC 12 — eligible_for_decision_consumption absent from decision-producing semantics
# =============================================================================

class TestNoDecisionConsumptionFields:
    def test_coverage_result_has_no_eligible_for_decision_consumption(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "T", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert not hasattr(result, "eligible_for_decision_consumption")
        assert not hasattr(result, "eligible_for_decision_consumption_count")

    def test_by_ticker_has_no_eligible_for_decision_consumption(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "T", "category": "Core"}],
            snapshot_by_ticker={},
        )
        for ticker_data in result.by_ticker.values():
            assert "eligible_for_decision_consumption" not in ticker_data

    def test_coverage_module_src_has_no_decide_import(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/"
            "sec_metric_portfolio_coverage_dry_run.py"
        )
        # Use AST to verify no import of decision_policy_v1.
        tree = ast.parse(src)
        imported_froms: list[str] = []
        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_froms.append(node.module or "")
        all_imports = " ".join(imported_names + imported_froms)
        assert "decision_policy_v1" not in all_imports
        # Verify no function call named "decide" at AST level.
        decide_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(getattr(node, "func", None), ast.Name)
            and node.func.id == "decide"
        ]
        assert len(decide_calls) == 0


# =============================================================================
# AC 13 — visible_snapshot_unchanged remains True
# =============================================================================

class TestVisibleSnapshotUnchanged:
    def test_visible_snapshot_unchanged_true_on_ready_portfolio(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="V")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "V", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert result.visible_snapshot_unchanged is True

    def test_visible_snapshot_unchanged_true_on_empty_portfolio(self):
        result = _coverage(portfolio_positions=[], snapshot_by_ticker={})
        assert result.visible_snapshot_unchanged is True

    def test_visible_snapshot_unchanged_true_on_all_blocked(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "BLK", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert result.visible_snapshot_unchanged is True


# =============================================================================
# AC 15 — Disabled flag path returns zero/empty/false safely
# =============================================================================

class TestDisabledFlagPath:
    def test_disabled_setting_returns_disabled_result(self):
        @dataclass
        class _OffSettings:
            intel_v3_sec_metric_portfolio_coverage_dry_run_enabled: bool = False

        db = _FakeDbClient({"positions": [], "research_artifacts": [], "research_artifact_facts": []})
        result = compute_portfolio_sec_metric_coverage(
            user_id=_UID,
            db_client=db,
            settings=_OffSettings(),
        )
        assert result.coverage_enabled is False
        assert result.safe_for_decision is False
        assert result.visible_snapshot_unchanged is True
        assert result.portfolio_ticker_count == 0
        assert result.portfolio_tickers_evaluated == []
        assert result.tickers_with_research_artifacts_count == 0
        assert result.tickers_without_research_artifacts_count == 0
        assert result.tickers_ready_for_future_adapter_count == 0
        assert result.tickers_partial_for_future_adapter_count == 0
        assert result.tickers_blocked_for_future_adapter_count == 0
        assert result.tickers_without_sec_metric_coverage == []
        assert result.by_ticker == {}
        assert len(result.errors) == 1
        assert "false" in result.errors[0]

    def test_disabled_result_does_not_query_db(self):
        """When disabled, no DB queries are needed — fake client returns empty cleanly."""

        class _NoQueryDb:
            def table(self, name):
                raise AssertionError(f"DB should not be queried when disabled (table={name})")

        @dataclass
        class _OffSettings:
            intel_v3_sec_metric_portfolio_coverage_dry_run_enabled: bool = False

        # Should not raise even though _NoQueryDb would assert on any query.
        result = compute_portfolio_sec_metric_coverage(
            user_id=_UID,
            db_client=_NoQueryDb(),
            settings=_OffSettings(),
        )
        assert result.coverage_enabled is False


# =============================================================================
# DB-reading integration tests (using fake client)
# =============================================================================

class TestComputePortfolioSecMetricCoverage:
    """Test compute_portfolio_sec_metric_coverage with fake DB."""

    def _make_db_with_positions(
        self,
        positions: list[dict],
        artifacts: list[dict] = None,
        facts: list[dict] = None,
    ) -> _FakeDbClient:
        return _FakeDbClient(
            {
                "positions": positions,
                "portfolio_snapshots": [],
                "research_artifacts": artifacts or [],
                "research_artifact_facts": facts or [],
            }
        )

    def test_enabled_returns_coverage_enabled_true(self):
        db = self._make_db_with_positions(
            positions=[{"ticker": "AAPL", "category": "Core", "user_id": _UID}],
        )
        result = compute_portfolio_sec_metric_coverage(
            user_id=_UID,
            db_client=db,
            settings=_StubSettings(),
        )
        assert result.coverage_enabled is True

    def test_enabled_reads_portfolio_tickers(self):
        db = self._make_db_with_positions(
            positions=[
                {"ticker": "AAPL", "category": "Core", "user_id": _UID},
                {"ticker": "MSFT", "category": "Core", "user_id": _UID},
            ],
        )
        result = compute_portfolio_sec_metric_coverage(
            user_id=_UID,
            db_client=db,
            settings=_StubSettings(),
        )
        assert result.portfolio_ticker_count == 2
        assert "AAPL" in result.portfolio_tickers_evaluated
        assert "MSFT" in result.portfolio_tickers_evaluated

    def test_tickers_without_artifacts_reported_as_missing(self):
        db = self._make_db_with_positions(
            positions=[{"ticker": "AAPL", "category": "Core", "user_id": _UID}],
            artifacts=[],  # no artifacts
        )
        result = compute_portfolio_sec_metric_coverage(
            user_id=_UID,
            db_client=db,
            settings=_StubSettings(),
        )
        assert result.tickers_without_research_artifacts_count == 1
        assert "AAPL" in result.tickers_without_sec_metric_coverage
        assert "missing_sec_research_artifact" in result.by_ticker["AAPL"]["blocking_reason_codes"]

    def test_ready_artifact_counted_as_ready(self):
        aid = _aid()
        facts = _full_facts(aid)
        db = self._make_db_with_positions(
            positions=[{"ticker": "AAPL", "category": "Core", "user_id": _UID}],
            artifacts=[dict(_make_artifact(aid, ticker="AAPL"), user_id=_UID)],
            facts=[dict(f, user_id=_UID) for f in facts],
        )
        result = compute_portfolio_sec_metric_coverage(
            user_id=_UID,
            db_client=db,
            settings=_StubSettings(),
        )
        assert result.tickers_ready_for_future_adapter_count == 1
        assert result.by_ticker["AAPL"]["future_adapter_readiness"] == "READY_DRY_RUN_ONLY"

    def test_safe_for_decision_false_from_db_path(self):
        db = self._make_db_with_positions(
            positions=[{"ticker": "AAPL", "category": "Core", "user_id": _UID}],
        )
        result = compute_portfolio_sec_metric_coverage(
            user_id=_UID,
            db_client=db,
            settings=_StubSettings(),
        )
        assert result.safe_for_decision is False

    def test_visible_snapshot_unchanged_from_db_path(self):
        db = self._make_db_with_positions(
            positions=[{"ticker": "AAPL", "category": "Core", "user_id": _UID}],
        )
        result = compute_portfolio_sec_metric_coverage(
            user_id=_UID,
            db_client=db,
            settings=_StubSettings(),
        )
        assert result.visible_snapshot_unchanged is True

    def test_empty_positions_returns_zero_counts(self):
        db = self._make_db_with_positions(positions=[])
        result = compute_portfolio_sec_metric_coverage(
            user_id=_UID,
            db_client=db,
            settings=_StubSettings(),
        )
        assert result.portfolio_ticker_count == 0
        assert result.by_ticker == {}


# =============================================================================
# Asset-type blocking codes (ETF / Crypto categories)
# =============================================================================

class TestAssetTypeBlockingCodes:
    def test_etf_ticker_has_asset_type_codes(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "SPY", "category": "ETF"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["SPY"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" in codes
        assert "likely_fund_or_etf" in codes

    def test_crypto_ticker_has_asset_type_codes(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "BTC", "category": "Crypto"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["BTC"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" in codes
        assert "likely_crypto" in codes

    def test_core_ticker_has_no_asset_type_codes(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["AAPL"]["blocking_reason_codes"]
        assert "asset_type_not_sec_company" not in codes
        assert "likely_crypto" not in codes
        assert "likely_fund_or_etf" not in codes

    def test_blocking_codes_still_sorted_with_asset_type(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "SPY", "category": "ETF"}],
            snapshot_by_ticker={},
        )
        codes = result.by_ticker["SPY"]["blocking_reason_codes"]
        assert codes == sorted(codes)


# =============================================================================
# Edge cases
# =============================================================================

class TestEdgeCases:
    def test_empty_portfolio_returns_safe_defaults(self):
        result = _coverage(portfolio_positions=[], snapshot_by_ticker={})
        assert result.portfolio_ticker_count == 0
        assert result.by_ticker == {}
        assert result.tickers_without_sec_metric_coverage == []
        assert result.safe_for_decision is False
        assert result.visible_snapshot_unchanged is True

    def test_duplicate_portfolio_tickers_deduplicated(self):
        result = _coverage(
            portfolio_positions=[
                {"ticker": "AAPL", "category": "Core"},
                {"ticker": "AAPL", "category": "Core"},
            ],
            snapshot_by_ticker={},
        )
        assert result.portfolio_ticker_count == 1
        assert result.portfolio_tickers_evaluated == ["AAPL"]

    def test_lowercase_ticker_normalized_to_upper(self):
        result = _coverage(
            portfolio_positions=[{"ticker": "aapl", "category": "Core"}],
            snapshot_by_ticker={},
        )
        assert "AAPL" in result.portfolio_tickers_evaluated
        assert "aapl" not in result.portfolio_tickers_evaluated

    def test_snapshot_ticker_not_in_portfolio_not_in_result(self):
        """Tickers in Phase 8B but NOT in portfolio should not appear in coverage."""
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="NOTINPORT")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[{"ticker": "AAPL", "category": "Core"}],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        assert "NOTINPORT" not in result.by_ticker
        assert result.portfolio_tickers_evaluated == ["AAPL"]

    def test_readiness_counts_sum_to_portfolio_ticker_count(self):
        aid = _aid()
        snapshot_by_ticker = _run_phase8b(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        result = _coverage(
            portfolio_positions=[
                {"ticker": "AAPL", "category": "Core"},
                {"ticker": "NOART1", "category": "Core"},
                {"ticker": "NOART2", "category": "ETF"},
            ],
            snapshot_by_ticker=snapshot_by_ticker,
        )
        total = sum(result.readiness_counts.values())
        assert total == result.portfolio_ticker_count == 3
