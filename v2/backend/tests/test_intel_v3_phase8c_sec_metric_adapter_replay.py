"""Phase 8C — Adapter Contract Hardening + Fixture Replay tests.

Acceptance criteria (19 total):

 1. READY fixture remains READY_DRY_RUN_ONLY.
 2. Missing capex fixture remains PARTIAL_DRY_RUN_ONLY with missing_bucket_capex.
 3. Artifact-without-source-linked-metric fixture becomes BLOCKED_DRY_RUN_ONLY.
 4. Non-metric-only fixture becomes BLOCKED_DRY_RUN_ONLY.
 5. Unlinked metric facts are excluded from source-linked counts.
 6. Unmapped tags are counted and do not create fake buckets.
 7. Missing tag/unit/form becomes UNKNOWN safely.
 8. Mixed 10-K/10-Q forms are counted correctly.
 9. Duplicate facts increase fact counts but do not create duplicate bucket names.
10. Mixed READY/PARTIAL/BLOCKED portfolio replay is deterministic.
11. Reordered input facts produce equivalent sorted output.
12. Blocking reason code order is deterministic.
13. Raw metric values, structured_payload, source URLs, and raw rows absent from
    returned diagnostics.
14. Phase 8A fields remain unchanged (adapter contract version present).
15. Phase 8B fields remain unchanged (snapshot contract version present).
16. safe_for_decision and eligible_for_decision_consumption invariants unchanged.
17. visible_snapshot_unchanged remains True.
18. No decide(), IntelV3Service, recommendation_engine, or frontend imports.
19. Existing Phase 8A and Phase 8B tests still pass (enforced by running them).

Architecture invariants verified by this file:
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER writes to any DB table.
    - NEVER sets safe_for_decision=True.
    - dry_run_safe_for_decision / snapshot_safe_for_decision always False.
    - visible_snapshot_unchanged always True.

All fixtures are pure in-memory — no Supabase dependency.
"""
from __future__ import annotations

import copy
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.intelligence.research_workers.sec_metric_truth_adapter_dry_run import (
    EXPECTED_BUCKETS,
    SEC_METRIC_BUCKET_MAP,
    SEC_METRIC_TRUTH_ADAPTER_DRY_RUN_CONTRACT_VERSION,
    run_sec_metric_truth_adapter_dry_run,
)
from app.services.intelligence.research_workers.sec_metric_evidence_snapshot_dry_run import (
    BUCKET_GROUPS,
    SEC_METRIC_EVIDENCE_SNAPSHOT_DRY_RUN_CONTRACT_VERSION,
    run_sec_metric_evidence_snapshot_dry_run,
)

_UID = "u_replay"
_ALL_TAGS = list(SEC_METRIC_BUCKET_MAP.keys())


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _aid() -> str:
    return str(uuid.uuid4())


def _make_artifact(
    aid: str = None,
    ticker: str = "TICK",
    confidence: str = "MEDIUM",
    freshness: str = "FRESH",
) -> dict:
    return {
        "id": aid or _aid(),
        "user_id": _UID,
        "ticker": ticker,
        "artifact_type": "catalyst_window",
        "skill_pack": "earnings_reviewer",
        "confidence_or_trust_level": confidence,
        "freshness_status": freshness,
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


def _full_facts(aid: str) -> list[dict]:
    """One fact per mapped SEC tag — covers all 10 expected buckets."""
    return [_make_metric_fact(aid, tag=t) for t in _ALL_TAGS]


def _facts_without_capex(aid: str) -> list[dict]:
    return [
        _make_metric_fact(aid, tag=t)
        for t in _ALL_TAGS
        if SEC_METRIC_BUCKET_MAP[t] != "capex"
    ]


def _run(artifact_rows, facts_by_artifact):
    """Run Phase 8A then Phase 8B; return (adapter_result, snapshot_result)."""
    adapter = run_sec_metric_truth_adapter_dry_run(
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )
    snapshot = run_sec_metric_evidence_snapshot_dry_run(
        adapter_result=adapter,
        artifact_rows=artifact_rows,
        facts_by_artifact=facts_by_artifact,
    )
    return adapter, snapshot


# ── Contract version constants ────────────────────────────────────────────────

class TestContractVersionConstants:
    """AC 14 / AC 15 — contract version constants exist and are stable strings."""

    def test_adapter_contract_version_exists(self):
        assert SEC_METRIC_TRUTH_ADAPTER_DRY_RUN_CONTRACT_VERSION == "phase8a_v1"

    def test_snapshot_contract_version_exists(self):
        assert SEC_METRIC_EVIDENCE_SNAPSHOT_DRY_RUN_CONTRACT_VERSION == "phase8b_v1"

    def test_contract_versions_are_strings(self):
        assert isinstance(SEC_METRIC_TRUTH_ADAPTER_DRY_RUN_CONTRACT_VERSION, str)
        assert isinstance(SEC_METRIC_EVIDENCE_SNAPSHOT_DRY_RUN_CONTRACT_VERSION, str)


# =============================================================================
# AC 1 — READY fixture remains READY_DRY_RUN_ONLY
# =============================================================================

class TestReadyFixture:
    def test_all_buckets_present_is_ready(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="READY")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        assert snap.by_ticker["READY"]["future_adapter_readiness"] == "READY_DRY_RUN_ONLY"

    def test_ready_fixture_has_no_missing_buckets(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="READY")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        assert snap.by_ticker["READY"]["missing_buckets"] == []

    def test_ready_fixture_safe_for_decision_false(self):
        aid = _aid()
        adapter, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="READY")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        assert adapter.dry_run_safe_for_decision is False
        assert snap.snapshot_safe_for_decision is False

    def test_ready_fixture_visible_snapshot_unchanged_true(self):
        aid = _aid()
        adapter, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="READY")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        assert adapter.visible_snapshot_unchanged is True
        assert snap.visible_snapshot_unchanged is True

    def test_ready_fixture_has_always_blocking_codes(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="READY")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        codes = snap.by_ticker["READY"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes

    def test_ready_fixture_tickers_counted(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="READY")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        assert snap.tickers_ready_for_future_adapter_count == 1
        assert snap.tickers_blocked_from_decision_count == 1
        assert snap.tickers_evaluated_count == 1


# =============================================================================
# AC 2 — Missing capex fixture → PARTIAL_DRY_RUN_ONLY with missing_bucket_capex
# =============================================================================

class TestPartialFixtureMissingCapex:
    def test_missing_capex_is_partial(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="PARTIAL")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        assert snap.by_ticker["PARTIAL"]["future_adapter_readiness"] == "PARTIAL_DRY_RUN_ONLY"

    def test_missing_capex_has_code(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="PARTIAL")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        assert "missing_bucket_capex" in snap.by_ticker["PARTIAL"]["blocking_reason_codes"]

    def test_partial_fixture_not_counted_as_ready(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="PARTIAL")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        assert snap.tickers_ready_for_future_adapter_count == 0

    def test_partial_fixture_still_has_always_blocking_codes(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="PARTIAL")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        codes = snap.by_ticker["PARTIAL"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes

    def test_partial_fixture_capex_in_missing_buckets(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="PARTIAL")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        assert "capex" in snap.by_ticker["PARTIAL"]["missing_buckets"]
        assert "capex" not in snap.by_ticker["PARTIAL"]["present_buckets"]

    def test_partial_safe_for_decision_false(self):
        aid = _aid()
        adapter, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="PARTIAL")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        assert adapter.dry_run_safe_for_decision is False
        assert snap.snapshot_safe_for_decision is False


# =============================================================================
# AC 3 — Artifact rows but no source-linked metric facts → BLOCKED_DRY_RUN_ONLY
# =============================================================================

class TestBlockedFixtureNoSourceLinked:
    def test_artifact_without_source_linked_facts_is_blocked(self):
        aid = _aid()
        unlinked_fact = _make_metric_fact(aid, tag="Revenues")
        unlinked_fact["source_id"] = None
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED1")],
            facts_by_artifact={aid: [unlinked_fact]},
        )
        assert snap.by_ticker["BLOCKED1"]["future_adapter_readiness"] == "BLOCKED_DRY_RUN_ONLY"

    def test_blocked_no_source_linked_fact_count_zero(self):
        aid = _aid()
        unlinked_fact = _make_metric_fact(aid, tag="Assets")
        unlinked_fact["source_id"] = None
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED1")],
            facts_by_artifact={aid: [unlinked_fact]},
        )
        assert snap.by_ticker["BLOCKED1"]["source_linked_metric_fact_count"] == 0

    def test_blocked_no_source_linked_has_always_blocking_codes(self):
        aid = _aid()
        unlinked_fact = _make_metric_fact(aid, tag="Revenues")
        unlinked_fact["source_id"] = None
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="BLOCKED1")],
            facts_by_artifact={aid: [unlinked_fact]},
        )
        codes = snap.by_ticker["BLOCKED1"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes

    def test_blocked_empty_facts_is_blocked(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="EMPTYBLOCKED")],
            facts_by_artifact={aid: []},
        )
        assert snap.by_ticker["EMPTYBLOCKED"]["future_adapter_readiness"] == "BLOCKED_DRY_RUN_ONLY"


# =============================================================================
# AC 4 — Non-metric facts only → BLOCKED_DRY_RUN_ONLY
# =============================================================================

class TestBlockedFixtureNonMetricOnly:
    def test_non_metric_facts_only_gives_blocked(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="NONMETRIC")],
            facts_by_artifact={aid: [_make_non_metric_fact(aid), _make_non_metric_fact(aid)]},
        )
        assert snap.by_ticker["NONMETRIC"]["future_adapter_readiness"] == "BLOCKED_DRY_RUN_ONLY"

    def test_non_metric_facts_excluded_from_source_linked_count(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="NONMETRIC")],
            facts_by_artifact={aid: [_make_non_metric_fact(aid)]},
        )
        assert adapter.source_linked_metric_fact_count == 0

    def test_non_metric_facts_not_in_by_bucket(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="NONMETRIC")],
            facts_by_artifact={aid: [_make_non_metric_fact(aid)]},
        )
        assert adapter.by_bucket == {}

    def test_non_metric_only_has_always_blocking_codes(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="NONMETRIC")],
            facts_by_artifact={aid: [_make_non_metric_fact(aid)]},
        )
        codes = snap.by_ticker["NONMETRIC"]["blocking_reason_codes"]
        assert "decision_consumption_disabled" in codes
        assert "safe_for_decision_db_lock" in codes


# =============================================================================
# AC 5 — Unlinked metric facts excluded from source-linked counts
# =============================================================================

class TestUnlinkedFactsExcluded:
    def test_unlinked_fact_not_counted(self):
        aid = _aid()
        linked = _make_metric_fact(aid, tag="Revenues")
        unlinked = _make_metric_fact(aid, tag="Assets")
        unlinked["source_id"] = None
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="MIX")],
            facts_by_artifact={aid: [linked, unlinked]},
        )
        assert adapter.source_linked_metric_fact_count == 1
        assert adapter.by_bucket.get("revenue", 0) == 1
        assert "assets" not in adapter.by_bucket

    def test_empty_string_source_id_excluded(self):
        aid = _aid()
        fact = _make_metric_fact(aid, tag="Assets")
        fact["source_id"] = ""
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="EMPTYID")],
            facts_by_artifact={aid: [fact]},
        )
        assert adapter.source_linked_metric_fact_count == 0
        assert adapter.by_ticker == {}

    def test_whitespace_only_source_id_excluded(self):
        aid = _aid()
        fact = _make_metric_fact(aid, tag="Assets")
        fact["source_id"] = "   "
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="WSID")],
            facts_by_artifact={aid: [fact]},
        )
        assert adapter.source_linked_metric_fact_count == 0


# =============================================================================
# AC 6 — Unmapped tags counted and do not create fake buckets
# =============================================================================

class TestUnmappedTagsHandling:
    def test_unmapped_tag_counted_not_dropped(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="UNMAPPED")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="SomeObscureXBRLTag")]},
        )
        assert adapter.unmapped_metric_fact_count == 1
        assert adapter.by_tag.get("SomeObscureXBRLTag", 0) == 1

    def test_unmapped_tag_does_not_appear_in_by_bucket(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="UNMAPPED")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="WeirdTag")]},
        )
        assert "WeirdTag" not in adapter.by_bucket

    def test_unmapped_tag_does_not_create_bucket_in_snapshot(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="UNMAPPED")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="NonExistentTag")]},
        )
        all_reported_buckets = (
            set(snap.by_ticker["UNMAPPED"]["present_buckets"])
            | set(snap.by_ticker["UNMAPPED"]["missing_buckets"])
        )
        assert all_reported_buckets == EXPECTED_BUCKETS

    def test_mixed_mapped_and_unmapped_counts_correctly(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="MIXED")],
            facts_by_artifact={aid: [
                _make_metric_fact(aid, tag="Revenues"),
                _make_metric_fact(aid, tag="UnknownTag1"),
                _make_metric_fact(aid, tag="UnknownTag2"),
            ]},
        )
        assert adapter.source_linked_metric_fact_count == 3
        assert adapter.unmapped_metric_fact_count == 2
        assert adapter.by_bucket.get("revenue", 0) == 1

    def test_multiple_unmapped_tags_each_counted_separately(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="MULTI_UNM")],
            facts_by_artifact={aid: [
                _make_metric_fact(aid, tag="TagA"),
                _make_metric_fact(aid, tag="TagB"),
                _make_metric_fact(aid, tag="TagA"),
            ]},
        )
        assert adapter.unmapped_metric_fact_count == 3
        assert adapter.by_tag.get("TagA", 0) == 2
        assert adapter.by_tag.get("TagB", 0) == 1


# =============================================================================
# AC 7 — Missing tag/unit/form becomes UNKNOWN safely
# =============================================================================

class TestMissingFieldsUnknown:
    def test_none_tag_becomes_unknown(self):
        aid = _aid()
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
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="NOTAG")],
            facts_by_artifact={aid: [fact]},
        )
        assert adapter.by_tag.get("UNKNOWN", 0) == 1
        assert adapter.unmapped_metric_fact_count == 1

    def test_none_unit_becomes_unknown(self):
        aid = _aid()
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
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="NOUNIT")],
            facts_by_artifact={aid: [fact]},
        )
        assert adapter.by_unit.get("UNKNOWN", 0) == 1

    def test_none_form_becomes_unknown(self):
        aid = _aid()
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
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="NOFORM")],
            facts_by_artifact={aid: [fact]},
        )
        assert adapter.by_form.get("UNKNOWN", 0) == 1

    def test_all_three_missing_becomes_unknown_no_crash(self):
        aid = _aid()
        fact = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": str(uuid.uuid4()),
            "structured_payload": {
                "claim": "sec_companyfact_observed",
                "tag": None,
                "unit": None,
                "form": None,
            },
        }
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="ALLUNKNOWN")],
            facts_by_artifact={aid: [fact]},
        )
        assert adapter.by_tag.get("UNKNOWN", 0) == 1
        assert adapter.by_unit.get("UNKNOWN", 0) == 1
        assert adapter.by_form.get("UNKNOWN", 0) == 1

    def test_unknown_tag_snapshot_counts_correctly(self):
        aid = _aid()
        fact = {
            "id": str(uuid.uuid4()),
            "artifact_id": aid,
            "fact_kind": "metric_observation",
            "source_id": str(uuid.uuid4()),
            "structured_payload": {
                "claim": "sec_companyfact_observed",
                "tag": None,
                "unit": None,
                "form": None,
            },
        }
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="ALLUNKNOWN")],
            facts_by_artifact={aid: [fact]},
        )
        assert snap.by_ticker["ALLUNKNOWN"]["units"].get("UNKNOWN", 0) == 1
        assert snap.by_ticker["ALLUNKNOWN"]["forms"].get("UNKNOWN", 0) == 1


# =============================================================================
# AC 8 — Mixed 10-K and 10-Q forms counted correctly
# =============================================================================

class TestMixedForms:
    def test_10k_and_10q_counted_separately(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="FORMS")],
            facts_by_artifact={aid: [
                _make_metric_fact(aid, tag="Revenues", form="10-K"),
                _make_metric_fact(aid, tag="NetIncomeLoss", form="10-K"),
                _make_metric_fact(aid, tag="Assets", form="10-Q"),
            ]},
        )
        forms = snap.by_ticker["FORMS"]["forms"]
        assert forms.get("10-K", 0) == 2
        assert forms.get("10-Q", 0) == 1

    def test_forms_are_counts_not_raw_values(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="FORMS")],
            facts_by_artifact={aid: [
                _make_metric_fact(aid, form="10-K", value=9_999_999_999),
                _make_metric_fact(aid, form="10-Q", value=8_888_888),
            ]},
        )
        for v in snap.by_ticker["FORMS"]["forms"].values():
            assert isinstance(v, int)
            assert v not in (9_999_999_999, 8_888_888)

    def test_10k_and_10q_per_ticker_independent(self):
        aid1, aid2 = _aid(), _aid()
        _, snap = _run(
            artifact_rows=[
                _make_artifact(aid1, ticker="T1"),
                _make_artifact(aid2, ticker="T2"),
            ],
            facts_by_artifact={
                aid1: [_make_metric_fact(aid1, form="10-K")],
                aid2: [_make_metric_fact(aid2, form="10-Q")],
            },
        )
        assert snap.by_ticker["T1"]["forms"] == {"10-K": 1}
        assert snap.by_ticker["T2"]["forms"] == {"10-Q": 1}

    def test_adapter_by_form_totals_all_tickers(self):
        aid1, aid2 = _aid(), _aid()
        adapter, _ = _run(
            artifact_rows=[
                _make_artifact(aid1, ticker="T1"),
                _make_artifact(aid2, ticker="T2"),
            ],
            facts_by_artifact={
                aid1: [_make_metric_fact(aid1, form="10-K")],
                aid2: [_make_metric_fact(aid2, form="10-Q")],
            },
        )
        assert adapter.by_form.get("10-K", 0) == 1
        assert adapter.by_form.get("10-Q", 0) == 1


# =============================================================================
# AC 9 — Duplicate facts increase counts but do not create duplicate bucket names
# =============================================================================

class TestDuplicateFacts:
    def test_duplicate_facts_increase_count(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="DUP")],
            facts_by_artifact={aid: [
                _make_metric_fact(aid, tag="Revenues"),
                _make_metric_fact(aid, tag="Revenues"),
                _make_metric_fact(aid, tag="Revenues"),
            ]},
        )
        assert adapter.by_bucket.get("revenue", 0) == 3
        assert adapter.source_linked_metric_fact_count == 3

    def test_duplicate_facts_do_not_create_duplicate_bucket_names(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="DUP")],
            facts_by_artifact={aid: [
                _make_metric_fact(aid, tag="Revenues"),
                _make_metric_fact(aid, tag="Revenues"),
            ]},
        )
        present = snap.by_ticker["DUP"]["present_buckets"]
        assert present.count("revenue") == 1

    def test_duplicate_facts_do_not_change_readiness_beyond_truth(self):
        """Duplicating facts should not flip PARTIAL to READY."""
        aid = _aid()
        # All buckets except capex, but duplicated many times.
        facts = _facts_without_capex(aid) * 5
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="DUP_PARTIAL")],
            facts_by_artifact={aid: facts},
        )
        assert snap.by_ticker["DUP_PARTIAL"]["future_adapter_readiness"] == "PARTIAL_DRY_RUN_ONLY"
        assert "capex" in snap.by_ticker["DUP_PARTIAL"]["missing_buckets"]

    def test_duplicate_same_tag_counted_in_by_tag(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="DUPTAG")],
            facts_by_artifact={aid: [
                _make_metric_fact(aid, tag="Assets"),
                _make_metric_fact(aid, tag="Assets"),
            ]},
        )
        assert adapter.by_tag.get("Assets", 0) == 2

    def test_duplicate_unmapped_tags_counted_correctly(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="DUPUNMAPPED")],
            facts_by_artifact={aid: [
                _make_metric_fact(aid, tag="SomeTag"),
                _make_metric_fact(aid, tag="SomeTag"),
                _make_metric_fact(aid, tag="SomeTag"),
            ]},
        )
        assert adapter.unmapped_metric_fact_count == 3
        assert adapter.by_tag.get("SomeTag", 0) == 3


# =============================================================================
# AC 10 — Mixed READY/PARTIAL/BLOCKED portfolio replay is deterministic
# =============================================================================

class TestMixedPortfolioReplay:
    def _build_portfolio(self):
        aids = {t: _aid() for t in ["READY_CO", "PARTIAL_CO", "BLOCKED_CO"]}
        artifact_rows = [
            _make_artifact(aids["READY_CO"], ticker="READY_CO"),
            _make_artifact(aids["PARTIAL_CO"], ticker="PARTIAL_CO"),
            _make_artifact(aids["BLOCKED_CO"], ticker="BLOCKED_CO"),
        ]
        facts_by_artifact = {
            aids["READY_CO"]: _full_facts(aids["READY_CO"]),
            aids["PARTIAL_CO"]: _facts_without_capex(aids["PARTIAL_CO"]),
            aids["BLOCKED_CO"]: [],
        }
        return artifact_rows, facts_by_artifact

    def test_mixed_portfolio_correct_readiness(self):
        rows, facts = self._build_portfolio()
        _, snap = _run(rows, facts)
        assert snap.by_ticker["READY_CO"]["future_adapter_readiness"] == "READY_DRY_RUN_ONLY"
        assert snap.by_ticker["PARTIAL_CO"]["future_adapter_readiness"] == "PARTIAL_DRY_RUN_ONLY"
        assert snap.by_ticker["BLOCKED_CO"]["future_adapter_readiness"] == "BLOCKED_DRY_RUN_ONLY"

    def test_mixed_portfolio_counts(self):
        rows, facts = self._build_portfolio()
        _, snap = _run(rows, facts)
        assert snap.tickers_evaluated_count == 3
        assert snap.tickers_ready_for_future_adapter_count == 1
        assert snap.tickers_with_any_source_linked_evidence_count == 2
        assert snap.tickers_blocked_from_decision_count == 3

    def test_mixed_portfolio_safe_for_decision_false(self):
        rows, facts = self._build_portfolio()
        adapter, snap = _run(rows, facts)
        assert adapter.dry_run_safe_for_decision is False
        assert snap.snapshot_safe_for_decision is False

    def test_mixed_portfolio_visible_snapshot_unchanged_true(self):
        rows, facts = self._build_portfolio()
        adapter, snap = _run(rows, facts)
        assert adapter.visible_snapshot_unchanged is True
        assert snap.visible_snapshot_unchanged is True

    def test_mixed_portfolio_all_tickers_have_always_blocking_codes(self):
        rows, facts = self._build_portfolio()
        _, snap = _run(rows, facts)
        for ticker in ["READY_CO", "PARTIAL_CO", "BLOCKED_CO"]:
            codes = snap.by_ticker[ticker]["blocking_reason_codes"]
            assert "decision_consumption_disabled" in codes
            assert "safe_for_decision_db_lock" in codes

    def test_mixed_portfolio_deterministic_run_twice(self):
        rows, facts = self._build_portfolio()
        _, snap1 = _run(rows, facts)
        _, snap2 = _run(rows, facts)
        assert snap1.by_ticker == snap2.by_ticker
        assert snap1.tickers_evaluated_count == snap2.tickers_evaluated_count
        assert snap1.tickers_ready_for_future_adapter_count == snap2.tickers_ready_for_future_adapter_count


# =============================================================================
# AC 11 — Reordered input facts produce equivalent sorted output
# =============================================================================

class TestInputOrderDeterminism:
    def test_reordered_facts_same_present_buckets(self):
        aid = _aid()
        facts_ordered = _full_facts(aid)
        facts_shuffled = copy.copy(facts_ordered)
        random.shuffle(facts_shuffled)
        _, snap_ordered = _run(
            artifact_rows=[_make_artifact(aid, ticker="ORDER")],
            facts_by_artifact={aid: facts_ordered},
        )
        # Need a fresh aid for the shuffled run to avoid state bleed.
        aid2 = _aid()
        facts_shuffled2 = [dict(f, artifact_id=aid2, id=str(uuid.uuid4())) for f in facts_shuffled]
        _, snap_shuffled = _run(
            artifact_rows=[_make_artifact(aid2, ticker="ORDER")],
            facts_by_artifact={aid2: facts_shuffled2},
        )
        assert snap_ordered.by_ticker["ORDER"]["present_buckets"] == snap_shuffled.by_ticker["ORDER"]["present_buckets"]
        assert snap_ordered.by_ticker["ORDER"]["missing_buckets"] == snap_shuffled.by_ticker["ORDER"]["missing_buckets"]

    def test_reordered_facts_same_readiness(self):
        aid = _aid()
        facts = _facts_without_capex(aid)
        shuffled = copy.copy(facts)
        random.shuffle(shuffled)
        _, snap1 = _run(
            artifact_rows=[_make_artifact(aid, ticker="ORD2")],
            facts_by_artifact={aid: facts},
        )
        aid2 = _aid()
        shuffled2 = [dict(f, artifact_id=aid2, id=str(uuid.uuid4())) for f in shuffled]
        _, snap2 = _run(
            artifact_rows=[_make_artifact(aid2, ticker="ORD2")],
            facts_by_artifact={aid2: shuffled2},
        )
        assert snap1.by_ticker["ORD2"]["future_adapter_readiness"] == snap2.by_ticker["ORD2"]["future_adapter_readiness"]

    def test_reordered_artifact_rows_same_sorted_tickers(self):
        """Swapping artifact row order does not change per-ticker output."""
        aids = {t: _aid() for t in ["ZZZ", "AAA"]}
        rows_ab = [
            _make_artifact(aids["ZZZ"], ticker="ZZZ"),
            _make_artifact(aids["AAA"], ticker="AAA"),
        ]
        rows_ba = [
            _make_artifact(aids["AAA"], ticker="AAA"),
            _make_artifact(aids["ZZZ"], ticker="ZZZ"),
        ]
        facts = {
            aids["ZZZ"]: [_make_metric_fact(aids["ZZZ"], tag="Revenues")],
            aids["AAA"]: [_make_metric_fact(aids["AAA"], tag="Assets")],
        }
        _, snap_ab = _run(rows_ab, facts)
        _, snap_ba = _run(rows_ba, facts)
        assert snap_ab.by_ticker.get("ZZZ") == snap_ba.by_ticker.get("ZZZ")
        assert snap_ab.by_ticker.get("AAA") == snap_ba.by_ticker.get("AAA")

    def test_same_input_twice_is_identical(self):
        aid = _aid()
        rows = [_make_artifact(aid, ticker="IDEM")]
        facts = {aid: _full_facts(aid)}
        adapter1, snap1 = _run(rows, facts)
        adapter2, snap2 = _run(rows, facts)
        assert adapter1.by_bucket == adapter2.by_bucket
        assert adapter1.source_linked_metric_fact_count == adapter2.source_linked_metric_fact_count
        assert snap1.by_ticker == snap2.by_ticker


# =============================================================================
# AC 12 — Blocking reason code order is deterministic (sorted)
# =============================================================================

class TestBlockingCodeOrder:
    def test_blocking_codes_are_sorted(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="BORD")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        codes = snap.by_ticker["BORD"]["blocking_reason_codes"]
        assert codes == sorted(codes)

    def test_ready_ticker_blocking_codes_sorted(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="RORD")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        codes = snap.by_ticker["RORD"]["blocking_reason_codes"]
        assert codes == sorted(codes)

    def test_blocked_ticker_blocking_codes_sorted(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="BLORD")],
            facts_by_artifact={aid: []},
        )
        codes = snap.by_ticker["BLORD"]["blocking_reason_codes"]
        assert codes == sorted(codes)

    def test_blocking_codes_deterministic_across_runs(self):
        aid = _aid()
        rows = [_make_artifact(aid, ticker="DETCODES")]
        facts = {aid: _facts_without_capex(aid)}
        _, snap1 = _run(rows, facts)
        _, snap2 = _run(rows, facts)
        assert snap1.by_ticker["DETCODES"]["blocking_reason_codes"] == snap2.by_ticker["DETCODES"]["blocking_reason_codes"]


# =============================================================================
# AC 13 — No raw metric values, structured_payload, source URLs, raw rows
# =============================================================================

class TestNoRawDataExposed:
    def test_adapter_result_has_no_structured_payload_field(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="NORAW")],
            facts_by_artifact={aid: [_make_metric_fact(aid, value=9_999_888_777)]},
        )
        assert not hasattr(adapter, "structured_payload")
        assert not hasattr(adapter, "raw_metric_values")
        assert not hasattr(adapter, "source_url")

    def test_adapter_by_bucket_values_are_counts_not_metric_values(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="NORAW")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="Revenues", value=9_876_543_210)]},
        )
        for v in adapter.by_bucket.values():
            assert isinstance(v, int)
            assert v != 9_876_543_210

    def test_adapter_by_tag_values_are_counts(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="NORAW")],
            facts_by_artifact={aid: [_make_metric_fact(aid, tag="Assets", value=1_000_000_000)]},
        )
        for v in adapter.by_tag.values():
            assert isinstance(v, int)
            assert v != 1_000_000_000

    def test_snapshot_ticker_dict_has_no_structured_payload(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="NORAWSNAP")],
            facts_by_artifact={aid: [_make_metric_fact(aid, value=555_555_555)]},
        )
        ticker_dict = snap.by_ticker["NORAWSNAP"]
        assert "structured_payload" not in ticker_dict
        assert "raw_metric_values" not in ticker_dict
        assert "source_url" not in ticker_dict
        assert "source_excerpt" not in ticker_dict

    def test_snapshot_forms_and_units_not_raw_values(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="NORAWUNITS")],
            facts_by_artifact={aid: [_make_metric_fact(aid, value=99_999_999)]},
        )
        ticker_dict = snap.by_ticker["NORAWUNITS"]
        for v in ticker_dict["forms"].values():
            assert v != 99_999_999
        for v in ticker_dict["units"].values():
            assert v != 99_999_999

    def test_no_raw_rows_in_any_returned_field(self):
        """Ensure returned dicts contain no 'data', 'rows', or 'payload' keys."""
        aid = _aid()
        adapter, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="NORES")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        for attr in ("by_ticker", "by_bucket", "by_tag", "by_unit", "by_form"):
            val = getattr(adapter, attr)
            assert "data" not in val
            assert "rows" not in val
        ticker_dict = snap.by_ticker.get("NORES", {})
        for bad_key in ("data", "rows", "payload", "structured_payload", "source_url"):
            assert bad_key not in ticker_dict


# =============================================================================
# AC 14 / AC 15 — Phase 8A and Phase 8B fields unchanged via adapter contract
# =============================================================================

class TestPhase8AAndBFieldsUnchanged:
    def test_phase8a_fields_populated_correctly(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        # Phase 8A field contract.
        assert adapter.dry_run_enabled is True
        assert adapter.dry_run_safe_for_decision is False
        assert adapter.visible_snapshot_unchanged is True
        assert adapter.artifacts_evaluated_count == 1
        assert adapter.source_linked_metric_fact_count == len(_ALL_TAGS)
        assert adapter.unmapped_metric_fact_count == 0
        assert isinstance(adapter.by_ticker, dict)
        assert isinstance(adapter.by_bucket, dict)
        assert isinstance(adapter.by_tag, dict)
        assert isinstance(adapter.by_unit, dict)
        assert isinstance(adapter.by_form, dict)
        assert isinstance(adapter.missing_buckets_by_ticker, dict)

    def test_phase8b_fields_populated_correctly(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="MSFT")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        # Phase 8B field contract.
        assert snap.snapshot_enabled is True
        assert snap.snapshot_safe_for_decision is False
        assert snap.visible_snapshot_unchanged is True
        assert snap.tickers_evaluated_count == 1
        assert snap.tickers_blocked_from_decision_count == snap.tickers_evaluated_count
        assert isinstance(snap.by_ticker, dict)

    def test_phase8a_missing_buckets_by_ticker_correct(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="NVDA")],
            facts_by_artifact={aid: _facts_without_capex(aid)},
        )
        assert "capex" in adapter.missing_buckets_by_ticker.get("NVDA", [])
        assert "NVDA" in adapter.missing_buckets_by_ticker

    def test_phase8b_by_ticker_has_expected_keys(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="AAPL")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        ticker_dict = snap.by_ticker["AAPL"]
        required_keys = {
            "source_linked_metric_fact_count",
            "present_buckets",
            "missing_buckets",
            "present_bucket_groups",
            "missing_bucket_groups",
            "forms",
            "units",
            "future_adapter_readiness",
            "blocking_reason_codes",
        }
        assert required_keys.issubset(set(ticker_dict.keys()))


# =============================================================================
# AC 16 — safe_for_decision and eligible_for_decision_consumption invariants
# =============================================================================

class TestSafeForDecisionInvariant:
    def test_adapter_safe_for_decision_always_false_on_ready(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="SAFE_R")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        assert adapter.dry_run_safe_for_decision is False

    def test_snapshot_safe_for_decision_always_false_on_ready(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="SAFE_R")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        assert snap.snapshot_safe_for_decision is False

    def test_adapter_safe_for_decision_always_false_on_empty(self):
        adapter, _ = _run(artifact_rows=[], facts_by_artifact={})
        assert adapter.dry_run_safe_for_decision is False

    def test_snapshot_safe_for_decision_always_false_on_empty(self):
        _, snap = _run(artifact_rows=[], facts_by_artifact={})
        assert snap.snapshot_safe_for_decision is False

    def test_tickers_blocked_from_decision_equals_tickers_evaluated(self):
        aids = {t: _aid() for t in ["A", "B", "C"]}
        rows = [_make_artifact(aids[t], ticker=t) for t in aids]
        facts = {
            aids["A"]: _full_facts(aids["A"]),
            aids["B"]: _facts_without_capex(aids["B"]),
            aids["C"]: [],
        }
        _, snap = _run(rows, facts)
        assert snap.tickers_blocked_from_decision_count == snap.tickers_evaluated_count == 3

    def test_older_unknown_artifact_does_not_become_decision_consumable(self):
        """UNKNOWN confidence/freshness older artifacts still blocked."""
        aid_old = _aid()
        aid_new = _aid()
        rows = [
            _make_artifact(aid_old, ticker="AAPL", confidence="UNKNOWN", freshness="UNKNOWN"),
            _make_artifact(aid_new, ticker="AAPL", confidence="MEDIUM", freshness="FRESH"),
        ]
        facts = {
            aid_old: [],
            aid_new: _full_facts(aid_new),
        }
        adapter, snap = _run(rows, facts)
        assert adapter.dry_run_safe_for_decision is False
        assert snap.snapshot_safe_for_decision is False
        assert snap.tickers_blocked_from_decision_count == snap.tickers_evaluated_count


# =============================================================================
# AC 17 — visible_snapshot_unchanged always True
# =============================================================================

class TestVisibleSnapshotUnchanged:
    def test_adapter_visible_snapshot_unchanged_always_true(self):
        aid = _aid()
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="V")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        assert adapter.visible_snapshot_unchanged is True

    def test_snapshot_visible_snapshot_unchanged_always_true(self):
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="V")],
            facts_by_artifact={aid: _full_facts(aid)},
        )
        assert snap.visible_snapshot_unchanged is True

    def test_visible_snapshot_unchanged_true_on_empty_input(self):
        adapter, snap = _run(artifact_rows=[], facts_by_artifact={})
        assert adapter.visible_snapshot_unchanged is True
        assert snap.visible_snapshot_unchanged is True

    def test_visible_snapshot_unchanged_true_on_blocked_ticker(self):
        aid = _aid()
        adapter, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="BLK")],
            facts_by_artifact={aid: []},
        )
        assert adapter.visible_snapshot_unchanged is True
        assert snap.visible_snapshot_unchanged is True


# =============================================================================
# AC 18 — No decide(), IntelV3Service, recommendation_engine, or frontend imports
# =============================================================================

def _read_module_src(rel_path: str) -> str:
    import pathlib
    base = pathlib.Path(__file__).parent.parent
    return (base / rel_path).read_text()


class TestStaticImportGuardsPhase8C:
    """Phase 8C — verify phase8c test file itself has no forbidden imports."""

    def test_no_decide_in_adapter_module(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/sec_metric_truth_adapter_dry_run.py"
        )
        assert "from .decision_policy_v1 import" not in src
        assert "import decide" not in src

    def test_no_decide_in_snapshot_module(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/sec_metric_evidence_snapshot_dry_run.py"
        )
        assert "from .decision_policy_v1 import" not in src
        assert "import decide" not in src

    def test_no_intel_v3_service_in_adapter_module(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/sec_metric_truth_adapter_dry_run.py"
        )
        # Check for actual import statements, not docstring mentions.
        assert "import IntelV3Service" not in src
        assert "from .intel_v3_service" not in src
        assert "import recommendation_engine" not in src
        assert "from .recommendation_engine" not in src

    def test_no_intel_v3_service_in_snapshot_module(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/sec_metric_evidence_snapshot_dry_run.py"
        )
        # Check for actual import statements, not docstring mentions.
        assert "import IntelV3Service" not in src
        assert "from .intel_v3_service" not in src
        assert "import recommendation_engine" not in src
        assert "from .recommendation_engine" not in src

    def test_phase8c_test_file_has_no_forbidden_imports(self):
        import ast
        import pathlib
        src = _read_module_src("tests/test_intel_v3_phase8c_sec_metric_adapter_replay.py")
        # Parse imports from the AST — avoids false positives from string literals.
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
        # Verify positive: correct imports present.
        assert "run_sec_metric_truth_adapter_dry_run" in src
        assert "run_sec_metric_evidence_snapshot_dry_run" in src

    def test_no_db_write_in_adapter_module(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/sec_metric_truth_adapter_dry_run.py"
        )
        assert ".insert(" not in src
        assert ".update(" not in src
        assert ".upsert(" not in src

    def test_no_db_write_in_snapshot_module(self):
        src = _read_module_src(
            "app/services/intelligence/research_workers/sec_metric_evidence_snapshot_dry_run.py"
        )
        assert ".insert(" not in src
        assert ".update(" not in src
        assert ".upsert(" not in src


# =============================================================================
# Supplementary: older UNKNOWN artifacts alongside newer MEDIUM/FRESH (AC 16 ext)
# =============================================================================

class TestOlderUnknownArtifacts:
    """Older UNKNOWN confidence/freshness artifacts do not become decision-consumable."""

    def test_two_artifacts_same_ticker_older_unknown_still_blocked(self):
        """Ticker with both old UNKNOWN and new MEDIUM/FRESH artifacts still blocked."""
        aid_old = _aid()
        aid_new = _aid()
        rows = [
            _make_artifact(aid_old, ticker="MSFT", confidence="UNKNOWN", freshness="UNKNOWN"),
            _make_artifact(aid_new, ticker="MSFT", confidence="MEDIUM", freshness="FRESH"),
        ]
        # Old artifact: no facts. New artifact: all facts.
        facts = {
            aid_old: [],
            aid_new: _full_facts(aid_new),
        }
        adapter, snap = _run(rows, facts)
        # Adapter should count facts from new artifact only.
        assert adapter.by_ticker.get("MSFT", 0) == len(_ALL_TAGS)
        # Snapshot should be READY (from new artifact's facts) but still blocked.
        assert snap.by_ticker["MSFT"]["future_adapter_readiness"] == "READY_DRY_RUN_ONLY"
        assert snap.snapshot_safe_for_decision is False
        assert adapter.dry_run_safe_for_decision is False

    def test_older_unknown_artifact_with_all_facts_still_blocked(self):
        """UNKNOWN confidence artifact with all facts: readiness may be READY
        (adapter does not filter by confidence), but safe_for_decision stays False."""
        aid = _aid()
        rows = [_make_artifact(aid, ticker="OLD_FULL", confidence="UNKNOWN", freshness="UNKNOWN")]
        facts = {aid: _full_facts(aid)}
        adapter, snap = _run(rows, facts)
        # safe_for_decision must be False regardless of artifact confidence.
        assert adapter.dry_run_safe_for_decision is False
        assert snap.snapshot_safe_for_decision is False
        assert snap.visible_snapshot_unchanged is True


# =============================================================================
# Supplementary: edge cases
# =============================================================================

class TestEdgeCases:
    def test_empty_artifact_list_returns_safe_defaults(self):
        adapter, snap = _run(artifact_rows=[], facts_by_artifact={})
        assert adapter.dry_run_enabled is True
        assert adapter.dry_run_safe_for_decision is False
        assert adapter.artifacts_evaluated_count == 0
        assert adapter.source_linked_metric_fact_count == 0
        assert adapter.by_ticker == {}
        assert snap.tickers_evaluated_count == 0
        assert snap.by_ticker == {}

    def test_artifact_with_no_matching_facts_entry_gives_blocked(self):
        """Artifact ID present in rows but no entry in facts_by_artifact."""
        aid = _aid()
        _, snap = _run(
            artifact_rows=[_make_artifact(aid, ticker="NOFACTENTRY")],
            facts_by_artifact={},  # no entry at all
        )
        assert snap.by_ticker["NOFACTENTRY"]["future_adapter_readiness"] == "BLOCKED_DRY_RUN_ONLY"

    def test_non_companyfacts_claim_excluded(self):
        aid = _aid()
        fact = _make_metric_fact(aid, claim="other_provider_claim")
        adapter, _ = _run(
            artifact_rows=[_make_artifact(aid, ticker="BADCLAIM")],
            facts_by_artifact={aid: [fact]},
        )
        assert adapter.source_linked_metric_fact_count == 0

    def test_present_plus_missing_always_equals_expected_buckets(self):
        for tag in _ALL_TAGS:
            aid = _aid()
            _, snap = _run(
                artifact_rows=[_make_artifact(aid, ticker="T")],
                facts_by_artifact={aid: [_make_metric_fact(aid, tag=tag)]},
            )
            d = snap.by_ticker["T"]
            assert set(d["present_buckets"]) | set(d["missing_buckets"]) == EXPECTED_BUCKETS

    def test_tickers_blocked_count_always_equals_evaluated(self):
        for n_tickers in range(1, 5):
            aids = [_aid() for _ in range(n_tickers)]
            rows = [_make_artifact(a, ticker=f"T{i}") for i, a in enumerate(aids)]
            facts = {a: _full_facts(a) if i % 2 == 0 else [] for i, a in enumerate(aids)}
            _, snap = _run(rows, facts)
            assert snap.tickers_blocked_from_decision_count == snap.tickers_evaluated_count == n_tickers
