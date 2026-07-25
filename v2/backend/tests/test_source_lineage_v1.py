"""PR 2 — versioned source-reference lineage.

Proves the full producer → bundle → axis → review → trust chain generates
genuine, structural source lineage instead of the PR-1 "0 of N" honest-empty
state, per ``docs/ai/RUN_INTEL_DISTRIBUTED_WORKFLOW.md`` §PR-2 contract.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.intelligence.v3.distributed import (
    run_task_store_v1 as store,
    session_control_v1 as control,
    source_lineage_v1 as lineage,
)
from app.services.intelligence.v3.distributed.collectors_v1 import (
    execute_collector_task,
)
from app.services.intelligence.v3.distributed.evidence_bundle_v1 import (
    build_evidence_bundle,
)
from app.services.intelligence.v3.distributed.run_scheduler_v1 import (
    run_scheduler_pass,
)
from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
    PROMPT_VERSION,
    execute_review_task,
    execute_specialist_task,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_CRYPTO_MARKET,
    AXIS_ETF_EXPOSURE,
    AXIS_FUNDAMENTAL,
    AXIS_REVIEW,
    AXIS_RISK_FILING,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL,
    LANE_CRYPTO_MARKET,
    LANE_ETF_FUND_DATA,
    LANE_FUNDAMENTALS,
    LANE_NEWS_SENTIMENT,
    LANE_PRICE,
    LANE_SEC_CATALYST,
    LANE_SEC_COMPANY_FACTS,
    LANE_TECHNICALS,
    TASK_COLLECT_EVIDENCE_LANE,
    TASK_DEGRADED,
    TASK_REVIEW_CONFLICT,
    TASK_SPECIALIST_ANALYSIS,
    TASK_SUCCEEDED,
)
from tests.distributed_run_intel_test_utils import (
    FakeLLM,
    FakeSupabase,
    ProviderRecorder,
    claim_task_row,
    make_settings,
    patch_providers,
    seed_position,
)

USER = str(uuid.uuid4())


# ── Pure module unit tests ───────────────────────────────────────────────────

def test_provider_observation_ref_from_usable_output():
    ref = lineage.make_provider_observation_ref(
        lane=LANE_PRICE, ticker="AAPL", task_id="task-1",
        output={"price": 101.5, "source": "yfinance", "as_of": "2026-07-24T00:00:00+00:00"},
    )
    assert ref is not None
    assert lineage.is_valid_reference(ref)
    assert ref["ref_type"] == lineage.REF_TYPE_PROVIDER_OBSERVATION
    assert ref["provider"] == "yfinance"
    assert ref["lane"] == LANE_PRICE
    assert ref["ticker"] == "AAPL"
    assert ref["task_id"] == "task-1"
    assert ref["output_digest"]


def test_provider_observation_ref_from_coingecko_output():
    ref = lineage.make_provider_observation_ref(
        lane=LANE_CRYPTO_MARKET, ticker="BTC", task_id="task-2",
        output={"price_usd": 30000.0, "market_cap_rank": 1, "source": "coingecko",
                 "as_of": "2026-07-24T00:00:00+00:00"},
    )
    assert ref is not None
    assert ref["provider"] == "coingecko"
    assert lineage.is_valid_reference(ref)


def test_degraded_no_data_output_creates_no_reference():
    # news_sentiment degraded (no items) — never a fabricated reference.
    ref = lineage.make_provider_observation_ref(
        lane=LANE_NEWS_SENTIMENT, ticker="AAPL", task_id="task-3",
        output={"items": [], "as_of": "2026-07-24T00:00:00+00:00"},
    )
    assert ref is None


def test_output_with_no_provider_creates_no_reference():
    ref = lineage.make_provider_observation_ref(
        lane=LANE_PRICE, ticker="AAPL", task_id="task-4",
        output={"price": 101.5, "as_of": "2026-07-24T00:00:00+00:00"},
    )
    assert ref is None


def test_research_artifact_source_ref_requires_a_real_source_row():
    ref = lineage.make_research_artifact_source_ref(
        lane=LANE_SEC_COMPANY_FACTS, ticker="AAPL", artifact_id="art-1",
        source_row={"id": "src-1", "provider_name": "sec_edgar", "source_kind": "sec_filing"},
    )
    assert ref is not None
    assert lineage.is_valid_reference(ref)
    assert ref["ref_type"] == lineage.REF_TYPE_RESEARCH_ARTIFACT_SOURCE
    assert ref["artifact_id"] == "art-1"
    assert ref["artifact_source_id"] == "src-1"


def test_artifact_id_alone_with_no_source_row_is_not_a_valid_reference():
    ref = lineage.make_research_artifact_source_ref(
        lane=LANE_SEC_COMPANY_FACTS, ticker="AAPL", artifact_id="art-1", source_row={},
    )
    assert ref is None
    # A bare opaque artifact-id string (the pre-PR-2 shape) never validates.
    assert lineage.is_valid_reference("art-1") is False
    assert lineage.is_valid_reference({"artifact_id": "art-1"}) is False


def test_legacy_opaque_string_and_malformed_dicts_never_validate():
    assert lineage.is_valid_reference("some-opaque-ref") is False
    assert lineage.is_valid_reference(None) is False
    assert lineage.is_valid_reference({}) is False
    assert lineage.is_valid_reference({"schema_version": "other_v1"}) is False


def test_dedupe_references_is_deterministic_and_partitioned():
    ref_a = lineage.make_provider_observation_ref(
        lane=LANE_PRICE, ticker="AAPL", task_id="t1",
        output={"price": 1, "source": "yfinance", "as_of": "x"},
    )
    ref_b = lineage.make_provider_observation_ref(
        lane=LANE_PRICE, ticker="AAPL", task_id="t1",
        output={"price": 1, "source": "yfinance", "as_of": "x"},
    )
    combined_1 = lineage.dedupe_references([ref_a, ref_b, "legacy-opaque"])
    combined_2 = lineage.dedupe_references([ref_b, ref_a])
    assert len(combined_1) == 1
    assert combined_1 == combined_2


def test_axis_lineage_manifest_partial_when_one_supplied_lane_unreferenced():
    price_ref = lineage.make_provider_observation_ref(
        lane=LANE_PRICE, ticker="AAA", task_id="t1",
        output={"price": 1, "source": "yfinance", "as_of": "x"},
    )
    manifest = lineage.build_axis_lineage_manifest(
        axis=AXIS_TECHNICAL,
        source_refs_by_lane={LANE_PRICE: [price_ref]},
        supplied_lanes=[LANE_PRICE, LANE_TECHNICALS],
    )
    assert manifest["status"] == lineage.LINEAGE_PARTIAL
    assert manifest["linked_lanes"] == [LANE_PRICE]
    assert manifest["missing_ref_lanes"] == [LANE_TECHNICALS]


def test_axis_lineage_manifest_full_when_every_supplied_lane_referenced():
    price_ref = lineage.make_provider_observation_ref(
        lane=LANE_PRICE, ticker="AAA", task_id="t1",
        output={"price": 1, "source": "yfinance", "as_of": "x"},
    )
    tech_ref = lineage.make_provider_observation_ref(
        lane=LANE_TECHNICALS, ticker="AAA", task_id="t2",
        output={"last": 1, "source": "yfinance", "as_of": "x"},
    )
    manifest = lineage.build_axis_lineage_manifest(
        axis=AXIS_TECHNICAL,
        source_refs_by_lane={LANE_PRICE: [price_ref], LANE_TECHNICALS: [tech_ref]},
        supplied_lanes=[LANE_PRICE, LANE_TECHNICALS],
    )
    assert manifest["status"] == lineage.LINEAGE_FULL


def test_axis_lineage_manifest_missing_when_no_candidate_lane_usable():
    manifest = lineage.build_axis_lineage_manifest(
        axis=AXIS_TECHNICAL, source_refs_by_lane={}, supplied_lanes=[],
    )
    assert manifest["status"] == lineage.LINEAGE_MISSING
    assert manifest["expected_lanes"] == []


def test_parse_axis_manifest_rejects_legacy_and_malformed():
    assert lineage.parse_axis_manifest(["legacy", "opaque"]) is None
    assert lineage.parse_axis_manifest([]) is None
    assert lineage.parse_axis_manifest(None) is None
    assert lineage.parse_axis_manifest({"status": "full"}) is None  # no schema_version


class TestStrictManifestValidationNeverFull:
    """Explicit proofs that a manifest claiming ``full`` while its own
    structure disagrees is always rejected (derived to ``missing``) — the
    persisted ``status`` field is NEVER trusted at face value."""

    def test_full_status_with_empty_refs_is_missing(self):
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            "expected_lanes": [LANE_PRICE], "linked_lanes": [LANE_PRICE],
            "missing_ref_lanes": [], "status": lineage.LINEAGE_FULL, "refs": [],
        }
        assert lineage.parse_axis_manifest(manifest) is None
        assert lineage.output_lineage_status(manifest) == lineage.LINEAGE_MISSING

    def test_full_status_with_unrelated_lane_reference_is_missing(self):
        ref = lineage.make_provider_observation_ref(
            lane=LANE_TECHNICALS, ticker="AAA", task_id="t1",
            output={"last": 1, "source": "yfinance", "as_of": "x"},
        )
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            "expected_lanes": [LANE_PRICE], "linked_lanes": [LANE_PRICE],
            "missing_ref_lanes": [], "status": lineage.LINEAGE_FULL,
            "refs": [ref],  # ref's own lane (technicals) isn't in linked_lanes
        }
        assert lineage.parse_axis_manifest(manifest, expected_ticker="AAA") is None

    def test_full_status_with_invalid_reference_is_missing(self):
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            "expected_lanes": [LANE_PRICE], "linked_lanes": [LANE_PRICE],
            "missing_ref_lanes": [], "status": lineage.LINEAGE_FULL,
            "refs": ["not-a-structured-reference"],
        }
        assert lineage.parse_axis_manifest(manifest) is None

    def test_full_status_with_wrong_ticker_reference_is_missing(self):
        ref = lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="ZZZ", task_id="t1",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            "expected_lanes": [LANE_PRICE], "linked_lanes": [LANE_PRICE],
            "missing_ref_lanes": [], "status": lineage.LINEAGE_FULL, "refs": [ref],
        }
        assert lineage.parse_axis_manifest(manifest, expected_ticker="AAA") is None
        # No expected_ticker given — still structurally valid (ticker check
        # only applies when the caller asks for it).
        assert lineage.parse_axis_manifest(manifest) is not None

    def test_full_status_with_missing_expected_lane_is_missing(self):
        ref = lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t1",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            # technicals is claimed expected but never appears in linked OR
            # missing — union(linked, missing) != expected.
            "expected_lanes": [LANE_PRICE, LANE_TECHNICALS],
            "linked_lanes": [LANE_PRICE], "missing_ref_lanes": [],
            "status": lineage.LINEAGE_FULL, "refs": [ref],
        }
        assert lineage.parse_axis_manifest(manifest, expected_ticker="AAA") is None

    def test_review_full_status_with_nonempty_missing_ref_axes_is_missing(self):
        ref = lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t1",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_REVIEW,
            "derived_from_axes": [AXIS_TECHNICAL, AXIS_FUNDAMENTAL],
            "missing_ref_axes": [AXIS_FUNDAMENTAL],
            "status": lineage.LINEAGE_FULL,
            "refs": [ref],
        }
        assert lineage.parse_axis_manifest(manifest, expected_ticker="AAA") is None
        assert lineage.output_lineage_status(
            manifest, expected_axis=AXIS_REVIEW, expected_ticker="AAA",
        ) == lineage.LINEAGE_MISSING

    def test_wrong_expected_axis_is_missing(self):
        ref = lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t1",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            "expected_lanes": [LANE_PRICE], "linked_lanes": [LANE_PRICE],
            "missing_ref_lanes": [], "status": lineage.LINEAGE_FULL, "refs": [ref],
        }
        assert lineage.parse_axis_manifest(manifest, expected_axis=AXIS_FUNDAMENTAL) is None


class TestAxisCandidateLaneEnforcement:
    """Round-3 item 1: every lane a manifest touches must belong to THAT
    axis's own ``AXIS_CANDIDATE_LANES`` set, never the wider
    ``SUPPORTED_LANES`` — a technical manifest citing fundamentals/SEC/news
    lanes (or vice versa) must fail closed, never read as full lineage."""

    def test_self_consistent_technical_manifest_sourced_only_by_fundamentals_is_missing(self):
        ref = lineage.make_provider_observation_ref(
            lane=LANE_FUNDAMENTALS, ticker="AAA", task_id="t1",
            output={"pe": 10, "source": "yfinance", "as_of": "x"},
        )
        # Self-consistent BY ITS OWN (wrong) lane set — the fundamentals lane
        # is not in AXIS_CANDIDATE_LANES[AXIS_TECHNICAL] at all, so this must
        # fail closed regardless of internal consistency.
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            "expected_lanes": [LANE_FUNDAMENTALS],
            "linked_lanes": [LANE_FUNDAMENTALS], "missing_ref_lanes": [],
            "status": lineage.LINEAGE_FULL, "refs": [ref],
        }
        assert lineage.parse_axis_manifest(manifest, expected_axis=AXIS_TECHNICAL) is None
        assert lineage.output_lineage_status(
            manifest, expected_axis=AXIS_TECHNICAL, expected_ticker="AAA",
        ) == lineage.LINEAGE_MISSING

    def test_self_consistent_fundamental_manifest_sourced_only_by_technicals_is_missing(self):
        ref = lineage.make_provider_observation_ref(
            lane=LANE_TECHNICALS, ticker="AAA", task_id="t1",
            output={"sma_50": 1, "source": "yfinance", "as_of": "x"},
        )
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_FUNDAMENTAL,
            "expected_lanes": [LANE_TECHNICALS],
            "linked_lanes": [LANE_TECHNICALS], "missing_ref_lanes": [],
            "status": lineage.LINEAGE_FULL, "refs": [ref],
        }
        assert lineage.parse_axis_manifest(manifest, expected_axis=AXIS_FUNDAMENTAL) is None

    def test_sentiment_manifest_with_technicals_and_fundamentals_is_missing(self):
        ref_tech = lineage.make_provider_observation_ref(
            lane=LANE_TECHNICALS, ticker="AAA", task_id="t1",
            output={"sma_50": 1, "source": "yfinance", "as_of": "x"},
        )
        ref_fund = lineage.make_provider_observation_ref(
            lane=LANE_FUNDAMENTALS, ticker="AAA", task_id="t2",
            output={"pe": 10, "source": "yfinance", "as_of": "x"},
        )
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_SENTIMENT,
            "expected_lanes": [LANE_FUNDAMENTALS, LANE_TECHNICALS],
            "linked_lanes": [LANE_FUNDAMENTALS, LANE_TECHNICALS],
            "missing_ref_lanes": [], "status": lineage.LINEAGE_FULL,
            "refs": [ref_tech, ref_fund],
        }
        assert lineage.parse_axis_manifest(manifest, expected_axis=AXIS_SENTIMENT) is None

    def test_malformed_lane_arrays_never_raise(self):
        malformed_variants = [
            {"expected_lanes": "not-a-list"},
            {"expected_lanes": [{"nested": "dict"}]},
            {"expected_lanes": [["nested", "list"]]},
            {"expected_lanes": None},
            {"expected_lanes": [LANE_PRICE, LANE_PRICE]},  # duplicate
            {"linked_lanes": "not-a-list"},
            {"linked_lanes": [123]},
            {"missing_ref_lanes": {"a": 1}},
        ]
        base = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            "expected_lanes": [LANE_PRICE], "linked_lanes": [LANE_PRICE],
            "missing_ref_lanes": [], "status": lineage.LINEAGE_MISSING, "refs": [],
        }
        for override in malformed_variants:
            manifest = {**base, **override}
            # Must never raise — always fails closed to None.
            assert lineage.parse_axis_manifest(manifest, expected_axis=AXIS_TECHNICAL) is None

    def test_too_many_persisted_refs_fails_closed(self):
        refs = [
            lineage.make_provider_observation_ref(
                lane=LANE_PRICE, ticker="AAA", task_id=f"t{i}",
                output={"price": i, "source": "yfinance", "as_of": "x"},
            )
            for i in range(lineage.MAX_REFS_PER_MANIFEST + 1)
        ]
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            "expected_lanes": [LANE_PRICE], "linked_lanes": [LANE_PRICE],
            "missing_ref_lanes": [], "status": lineage.LINEAGE_FULL, "refs": refs,
        }
        assert lineage.parse_axis_manifest(manifest, expected_axis=AXIS_TECHNICAL) is None

    def test_axis_ticker_mismatch_remains_missing(self):
        ref = lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="ZZZ", task_id="t1",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            "expected_lanes": [LANE_PRICE], "linked_lanes": [LANE_PRICE],
            "missing_ref_lanes": [], "status": lineage.LINEAGE_FULL, "refs": [ref],
        }
        assert lineage.output_lineage_status(
            manifest, expected_axis=AXIS_TECHNICAL, expected_ticker="AAA",
        ) == lineage.LINEAGE_MISSING


class TestTruncatedRefCountValidation:
    """Round-3 item 1: ``truncated_ref_count`` must be validated as a
    bounded nonnegative int — booleans, negatives, non-ints and unreasonably
    large values are all rejected, never silently coerced."""

    def _manifest(self, truncated_ref_count):
        return {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_TECHNICAL,
            "expected_lanes": [LANE_PRICE], "linked_lanes": [], "missing_ref_lanes": [LANE_PRICE],
            "status": lineage.LINEAGE_MISSING, "refs": [],
            "truncated_ref_count": truncated_ref_count,
        }

    @pytest.mark.parametrize("bad_value", [True, False, -1, 1.5, "3", [], {}, 10_001])
    def test_invalid_truncated_ref_count_rejected(self, bad_value):
        manifest = self._manifest(bad_value)
        assert lineage.parse_axis_manifest(manifest, expected_axis=AXIS_TECHNICAL) is None

    def test_valid_truncated_ref_count_accepted(self):
        manifest = self._manifest(3)
        parsed = lineage.parse_axis_manifest(manifest, expected_axis=AXIS_TECHNICAL)
        assert parsed is not None
        assert parsed["truncated_ref_count"] == 3

    def test_absent_truncated_ref_count_defaults_to_zero(self):
        manifest = self._manifest(None)
        del manifest["truncated_ref_count"]
        parsed = lineage.parse_axis_manifest(manifest, expected_axis=AXIS_TECHNICAL)
        assert parsed is not None
        assert parsed["truncated_ref_count"] == 0


class TestForgedReviewManifestFailsClosed:
    """Round-3 item 6: a review manifest's lineage claims must be
    independently re-derivable and match the CURRENT valid non-review
    outputs for the same ticker — a review claiming a different axis set or
    different per-axis statuses than what is currently true is stale/forged
    and must read as missing lineage, never full."""

    def _full_axis_output(self, axis, ticker):
        manifest = lineage.build_axis_lineage_manifest(
            axis=axis,
            source_refs_by_lane={LANE_PRICE: [lineage.make_provider_observation_ref(
                lane=LANE_PRICE, ticker=ticker, task_id=f"{ticker}-{axis}",
                output={"price": 1, "source": "yfinance", "as_of": "x"},
            )]},
            supplied_lanes=[LANE_PRICE],
        )
        return {"axis": axis, "evidence_refs": manifest, "score": 0.5, "confidence": 0.8}

    def test_review_claiming_extra_axis_not_currently_present_is_missing(self):
        technical_output = self._full_axis_output(AXIS_TECHNICAL, "AAA")
        # Review honestly reconciles only "technical" now...
        honest_review = lineage.build_review_lineage_manifest(
            [technical_output], ticker="AAA",
        )
        # ...but is then persisted as though it ALSO reconciled fundamental
        # (forged claim: input_axis_lineage lists an axis that isn't in the
        # CURRENT non-review outputs at all).
        forged = dict(honest_review)
        forged["input_axis_lineage"] = sorted(
            honest_review["input_axis_lineage"] + [{"axis": AXIS_FUNDAMENTAL, "status": lineage.LINEAGE_FULL}],
            key=lambda e: e["axis"],
        )
        forged["derived_from_axes"] = sorted(honest_review["derived_from_axes"] + [AXIS_FUNDAMENTAL])
        result = lineage.validate_review_against_current_outputs(
            forged, ticker="AAA", current_non_review_outputs=[technical_output],
        )
        assert result is None

    def test_review_claiming_better_status_than_current_truth_is_missing(self):
        technical_output = self._full_axis_output(AXIS_TECHNICAL, "AAA")
        # Downgrade the CURRENT technical output to missing (no reference) —
        # simulating the axis output changing after the review was written.
        stale_manifest = lineage.build_axis_lineage_manifest(
            axis=AXIS_TECHNICAL, source_refs_by_lane={}, supplied_lanes=[LANE_PRICE],
        )
        current_technical_output = {
            "axis": AXIS_TECHNICAL, "evidence_refs": stale_manifest,
            "score": 0.5, "confidence": 0.8,
        }
        # The persisted review still claims "technical: full" (stale/forged
        # relative to the ticker's CURRENT truth).
        stale_review = lineage.build_review_lineage_manifest(
            [technical_output], ticker="AAA",
        )
        result = lineage.validate_review_against_current_outputs(
            stale_review, ticker="AAA",
            current_non_review_outputs=[current_technical_output],
        )
        assert result is None

    def test_review_matching_current_truth_exactly_validates(self):
        technical_output = self._full_axis_output(AXIS_TECHNICAL, "AAA")
        review = lineage.build_review_lineage_manifest(
            [technical_output], ticker="AAA",
        )
        result = lineage.validate_review_against_current_outputs(
            review, ticker="AAA", current_non_review_outputs=[technical_output],
        )
        assert result is not None
        assert result["status"] == lineage.LINEAGE_FULL

    def test_review_manifest_missing_input_axis_lineage_is_rejected(self):
        # A hand-crafted review manifest lacking input_axis_lineage entirely
        # (pre-round-3 shape, or a tampered write) must fail structural
        # validation — never trusted merely for having plausible
        # derived_from_axes/status fields.
        manifest = {
            "schema_version": lineage.SCHEMA_VERSION, "axis": AXIS_REVIEW,
            "derived_from_axes": [AXIS_TECHNICAL], "missing_ref_axes": [],
            "status": lineage.LINEAGE_FULL, "refs": [
                lineage.make_provider_observation_ref(
                    lane=LANE_PRICE, ticker="AAA", task_id="t1",
                    output={"price": 1, "source": "yfinance", "as_of": "x"},
                ),
            ],
        }
        assert lineage.parse_axis_manifest(
            manifest, expected_axis=AXIS_REVIEW, expected_ticker="AAA",
        ) is None


def test_review_lineage_manifest_full_only_when_every_input_full():
    full_manifest = lineage.build_axis_lineage_manifest(
        axis=AXIS_TECHNICAL,
        source_refs_by_lane={LANE_PRICE: [lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t1",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )]},
        supplied_lanes=[LANE_PRICE],
    )
    # A genuinely PARTIAL manifest (self-consistent — one supplied lane
    # referenced, one not) — flipping only the "status" label of an
    # otherwise-full manifest would now be rejected as malformed (structural
    # status is independently re-derived, never trusted).
    partial_manifest = lineage.build_axis_lineage_manifest(
        axis=AXIS_FUNDAMENTAL,
        source_refs_by_lane={LANE_PRICE: [lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t2",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )]},
        supplied_lanes=[LANE_PRICE, LANE_FUNDAMENTALS],
    )
    assert partial_manifest["status"] == lineage.LINEAGE_PARTIAL

    review = lineage.build_review_lineage_manifest(
        [
            {"axis": AXIS_TECHNICAL, "evidence_refs": full_manifest},
            {"axis": AXIS_FUNDAMENTAL, "evidence_refs": partial_manifest},
        ],
        ticker="AAA",
    )
    assert review["status"] == lineage.LINEAGE_PARTIAL
    assert AXIS_FUNDAMENTAL in review["missing_ref_axes"]
    assert review["derived_from_axes"] == [AXIS_FUNDAMENTAL, AXIS_TECHNICAL]
    assert review["refs"]  # inherits the technical axis's valid reference

    review_all_full = lineage.build_review_lineage_manifest(
        [{"axis": AXIS_TECHNICAL, "evidence_refs": full_manifest}], ticker="AAA",
    )
    assert review_all_full["status"] == lineage.LINEAGE_FULL
    assert review_all_full["missing_ref_axes"] == []


def test_review_never_writes_evidence_refs_empty_when_sourced_inputs_exist():
    full_manifest = lineage.build_axis_lineage_manifest(
        axis=AXIS_TECHNICAL,
        source_refs_by_lane={LANE_PRICE: [lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t1",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )]},
        supplied_lanes=[LANE_PRICE],
    )
    review = lineage.build_review_lineage_manifest(
        [{"axis": AXIS_TECHNICAL, "evidence_refs": full_manifest}], ticker="AAA",
    )
    assert review["refs"] != []


def test_review_input_fingerprint_is_deterministic_and_nonempty():
    inputs = [{
        "axis": AXIS_TECHNICAL, "stance": "positive", "score": 0.5,
        "confidence": 0.8, "key_findings": ["f"], "risks": [],
        "lineage_status": lineage.LINEAGE_FULL, "linked_lanes": [LANE_PRICE],
        "missing_ref_lanes": [], "evidence_sources": [],
    }]
    fp1 = lineage.review_input_fingerprint(inputs, ticker="AAA", prompt_version="v2")
    fp2 = lineage.review_input_fingerprint(inputs, ticker="AAA", prompt_version="v2")
    assert fp1 and fp1 == fp2
    fp3 = lineage.review_input_fingerprint(inputs, ticker="BBB", prompt_version="v2")
    assert fp3 != fp1  # ticker is part of the fingerprint


# ── Bundle-level integration: direct lanes via real collectors ──────────────

async def _bundle_for(
    client: FakeSupabase, monkeypatch, ticker: str, *, category: str = "Core",
    recorder: ProviderRecorder | None = None,
) -> dict:
    seed_position(client, USER, ticker, category=category)
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    recorder = recorder or ProviderRecorder()
    patch_providers(monkeypatch, recorder)
    settings = make_settings()
    session = client.rows("intel_run_sessions")[0]
    for task in list(client.rows("intel_run_tasks")):
        if task["task_type"] != TASK_COLLECT_EVIDENCE_LANE:
            continue
        result = await execute_collector_task(client, task=task, settings=settings)
        client.table("intel_run_tasks").update({
            "state": result.final_state
            if result.final_state in (TASK_SUCCEEDED, TASK_DEGRADED)
            else TASK_DEGRADED,
            "output": result.output,
            "output_ref": result.output_ref,
            "completed_at": "2026-07-24T00:00:00+00:00",
        }).eq("id", task["id"]).execute()
    row = next(r for r in client.rows("intel_run_tickers") if r["ticker"] == ticker.upper())
    return build_evidence_bundle(client, session=session, ticker_row=row), recorder


class TestBundleDirectLaneLineage:
    @pytest.mark.asyncio
    async def test_price_technicals_fundamentals_news_are_source_linked(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        bundle, _ = await _bundle_for(client, monkeypatch, "AAPL")
        refs_by_lane = bundle["source_refs_by_lane"]
        for lane in (LANE_PRICE, LANE_TECHNICALS, LANE_FUNDAMENTALS, LANE_NEWS_SENTIMENT):
            assert lane in refs_by_lane, f"{lane} missing from source_refs_by_lane"
            assert refs_by_lane[lane], f"{lane} has no valid references"
            for ref in refs_by_lane[lane]:
                assert lineage.is_valid_reference(ref)
                assert ref["provider"] == "yfinance"
        assert bundle["source_ref_gaps"] == []
        assert bundle["quality"]["source_linked_lane_count"] == len(refs_by_lane)
        assert bundle["quality"]["source_ref_count"] == len(bundle["source_refs"])
        assert bundle["source_refs"] == lineage.dedupe_references(bundle["source_refs"])

    @pytest.mark.asyncio
    async def test_crypto_market_lane_is_source_linked(self, monkeypatch):
        client = FakeSupabase()
        bundle, _ = await _bundle_for(client, monkeypatch, "BTC", category="Crypto")
        refs_by_lane = bundle["source_refs_by_lane"]
        assert LANE_CRYPTO_MARKET in refs_by_lane
        for ref in refs_by_lane[LANE_CRYPTO_MARKET]:
            assert ref["provider"] == "coingecko"

    @pytest.mark.asyncio
    async def test_degraded_news_lane_produces_no_reference_and_records_gap(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        # ProviderRecorder always returns 3 news items — force a degraded
        # (no-data) outcome by monkeypatching the collector's news fetch to
        # return nothing, mirroring a genuine "no news" provider response.
        recorder = ProviderRecorder()
        bundle, _ = await _bundle_for(client, monkeypatch, "AAPL", recorder=recorder)
        # Sanity: the happy-path recorder DOES produce news — rebuild with an
        # explicit empty-news recorder to prove the negative.
        client2 = FakeSupabase()

        async def _no_news(ticker, limit=6):
            return []

        seed_position(client2, USER, "MSFT", category="Core")
        session_id = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client2, user_id=USER, session_id=session_id,
        )
        recorder2 = ProviderRecorder()
        patch_providers(monkeypatch, recorder2)
        monkeypatch.setattr(
            "app.services.intelligence.v3.distributed.collectors_v1.fetch_yfinance_news",
            _no_news,
        )
        settings = make_settings()
        session = client2.rows("intel_run_sessions")[0]
        for task in list(client2.rows("intel_run_tasks")):
            if task["task_type"] != TASK_COLLECT_EVIDENCE_LANE:
                continue
            result = await execute_collector_task(client2, task=task, settings=settings)
            client2.table("intel_run_tasks").update({
                "state": result.final_state
                if result.final_state in (TASK_SUCCEEDED, TASK_DEGRADED)
                else TASK_DEGRADED,
                "output": result.output,
                "output_ref": result.output_ref,
                "completed_at": "2026-07-24T00:00:00+00:00",
            }).eq("id", task["id"]).execute()
        row = next(r for r in client2.rows("intel_run_tickers") if r["ticker"] == "MSFT")
        bundle2 = build_evidence_bundle(client2, session=session, ticker_row=row)
        assert LANE_NEWS_SENTIMENT not in bundle2["source_refs_by_lane"]
        assert LANE_NEWS_SENTIMENT not in bundle2["usable_lanes"]

    @pytest.mark.asyncio
    async def test_ttl_reused_lane_keeps_honest_identity_zero_provider_calls(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        seed_position(client, USER, "AAPL", category="Core")
        recorder = ProviderRecorder()
        patch_providers(monkeypatch, recorder)
        settings = make_settings()

        session_id_1 = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id_1,
        )
        session_1 = next(
            s for s in client.rows("intel_run_sessions") if s["id"] == session_id_1
        )
        for task in list(client.rows("intel_run_tasks")):
            if task["task_type"] != TASK_COLLECT_EVIDENCE_LANE or task["lane"] != LANE_TECHNICALS:
                continue
            result = await execute_collector_task(client, task=task, settings=settings)
            client.table("intel_run_tasks").update({
                "state": result.final_state, "output": result.output,
                "completed_at": "2026-07-24T00:00:00+00:00",
            }).eq("id", task["id"]).execute()
        calls_after_first = len(recorder.calls)
        assert calls_after_first > 0

        # A second session (TTL not expired — technicals TTL is 24h) reuses
        # the cached lane output with ZERO additional provider calls, and
        # the resulting reference still carries the honest provider/lane.
        for row in client.rows("intel_run_sessions"):
            if row.get("status") in ("created", "running"):
                row["status"] = "completed"
        session_id_2 = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id_2,
        )
        session_2 = next(
            s for s in client.rows("intel_run_sessions") if s["id"] == session_id_2
        )
        tech_task_2 = next(
            t for t in client.rows("intel_run_tasks")
            if t["run_session_id"] == session_id_2 and t["lane"] == LANE_TECHNICALS
        )
        result_2 = await execute_collector_task(client, task=tech_task_2, settings=settings)
        assert result_2.cache_hit is True
        assert len(recorder.calls) == calls_after_first  # zero new provider calls
        client.table("intel_run_tasks").update({
            "state": result_2.final_state, "output": result_2.output,
            "completed_at": "2026-07-24T00:00:00+00:00",
        }).eq("id", tech_task_2["id"]).execute()
        row_2 = next(
            r for r in client.rows("intel_run_tickers")
            if r["run_session_id"] == session_id_2 and r["ticker"] == "AAPL"
        )
        bundle_2 = build_evidence_bundle(client, session=session_2, ticker_row=row_2)
        tech_refs = bundle_2["source_refs_by_lane"].get(LANE_TECHNICALS)
        assert tech_refs and tech_refs[0]["provider"] == "yfinance"

    @pytest.mark.asyncio
    async def test_fingerprint_stable_across_sessions_but_sensitive_to_evidence(
        self, monkeypatch,
    ):
        client_a = FakeSupabase()
        bundle_a, _ = await _bundle_for(client_a, monkeypatch, "AAPL")

        client_b = FakeSupabase()
        bundle_b, _ = await _bundle_for(client_b, monkeypatch, "AAPL")
        # Different sessions/tasks (fresh UUIDs), identical evidence.
        assert bundle_a["run_session_id"] != bundle_b["run_session_id"]
        assert bundle_a["input_fingerprint"] == bundle_b["input_fingerprint"]

        client_c = FakeSupabase()
        recorder_c = ProviderRecorder(fail_fundamentals={"AAPL"})
        bundle_c, _ = await _bundle_for(
            client_c, monkeypatch, "AAPL", recorder=recorder_c,
        )
        assert bundle_c["input_fingerprint"] != bundle_a["input_fingerprint"]


class TestArtifactBackedLaneLineage:
    def _seed_ticker_and_session(self, client: FakeSupabase, ticker: str, asset_type: str = "equity"):
        session_id = str(uuid.uuid4())
        client.table("intel_run_sessions").insert({
            "id": session_id, "user_id": USER, "status": "running",
            "workflow_version": 2,
        }).execute()
        store.insert_ticker_rows(
            client, run_session_id=session_id, user_id=USER,
            rows=[{"ticker": ticker, "asset_type": asset_type}],
        )
        return session_id

    def _insert_lane_task(
        self, client: FakeSupabase, *, session_id, ticker, lane, asset_type,
        state, output,
    ):
        client.table("intel_run_tasks").insert({
            "id": str(uuid.uuid4()), "run_session_id": session_id, "user_id": USER,
            "task_type": TASK_COLLECT_EVIDENCE_LANE, "lane": lane, "ticker": ticker,
            "asset_type": asset_type, "state": state, "output": output,
        }).execute()

    def _seed_artifact(
        self, client: FakeSupabase, *, artifact_id, ticker, user_id=USER,
        payload=None, is_active=True, scope_kind="ticker", invalidated_at=None,
    ):
        # Must match the SEC CompanyFacts adapter's own artifact contract
        # (``_ARTIFACT_TYPE``/``_SKILL_PACK``/``_SCOPE_KIND``) and carry a
        # genuinely SUBSTANTIVE payload (``observation_count > 0``) by
        # default — evidence_bundle_v1 derives its contract/substantive
        # checks from these exact adapter constants, never a guessed value.
        client.table("research_artifacts").insert({
            "id": artifact_id, "user_id": user_id, "ticker": ticker,
            "artifact_type": "fundamental_quality",
            "skill_pack": "sec_companyfacts_evidence_v1",
            "scope_kind": scope_kind,
            "payload": payload if payload is not None else {
                "observation_count": 3, "fact": "value",
            },
            "is_active": is_active,
            "invalidated_at": invalidated_at,
        }).execute()

    def test_artifact_with_source_rows_creates_research_artifact_source_refs(self):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        self._seed_artifact(client, artifact_id=artifact_id, ticker="AAPL")
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        client.table("research_artifact_sources").insert({
            "id": str(uuid.uuid4()), "artifact_id": artifact_id, "user_id": USER,
            "provider_name": "sec_edgar", "source_kind": "sec_filing",
            "source_id": "0001234567-26-000001",
        }).execute()
        row = client.rows("intel_run_tickers")[0]
        bundle = build_evidence_bundle(
            client, session={"id": session_id}, ticker_row=row,
        )
        refs = bundle["source_refs_by_lane"].get(LANE_SEC_COMPANY_FACTS)
        assert refs and len(refs) == 1
        assert refs[0]["ref_type"] == lineage.REF_TYPE_RESEARCH_ARTIFACT_SOURCE
        assert refs[0]["provider"] == "sec_edgar"
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_ref_gaps"]
        assert bundle["sec"][LANE_SEC_COMPANY_FACTS]["payload"]

    def test_succeeded_task_with_missing_artifact_parent_is_degraded_not_a_gap(self):
        # A missing parent has NO usable evidence to have a citation gap
        # over — it is excluded from usable_lanes entirely and surfaces as a
        # DEGRADED lane with a bounded reason (round-3 contract §3), never a
        # source_ref_gap (that would wrongly imply otherwise-valid evidence
        # is merely uncited).
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        # No research_artifacts row at all for this id.
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        row = client.rows("intel_run_tickers")[0]
        bundle = build_evidence_bundle(
            client, session={"id": session_id}, ticker_row=row,
        )
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_refs_by_lane"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_ref_gaps"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["usable_lanes"]
        assert LANE_SEC_COMPANY_FACTS in bundle["degraded_lanes"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["sec"]

    def test_wrong_user_artifact_never_leaks_provenance(self):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        other_user = str(uuid.uuid4())
        self._seed_artifact(
            client, artifact_id=artifact_id, ticker="AAPL", user_id=other_user,
        )
        client.table("research_artifact_sources").insert({
            "id": str(uuid.uuid4()), "artifact_id": artifact_id, "user_id": other_user,
            "provider_name": "sec_edgar", "source_kind": "sec_filing",
        }).execute()
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        row = client.rows("intel_run_tickers")[0]
        bundle = build_evidence_bundle(
            client, session={"id": session_id}, ticker_row=row,
        )
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_refs_by_lane"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_ref_gaps"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["usable_lanes"]
        assert LANE_SEC_COMPANY_FACTS in bundle["degraded_lanes"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["sec"]

    def test_wrong_ticker_artifact_never_leaks_provenance(self):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        self._seed_artifact(client, artifact_id=artifact_id, ticker="MSFT")
        client.table("research_artifact_sources").insert({
            "id": str(uuid.uuid4()), "artifact_id": artifact_id, "user_id": USER,
            "provider_name": "sec_edgar", "source_kind": "sec_filing",
        }).execute()
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        row = client.rows("intel_run_tickers")[0]
        bundle = build_evidence_bundle(
            client, session={"id": session_id}, ticker_row=row,
        )
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_refs_by_lane"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_ref_gaps"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["usable_lanes"]
        assert LANE_SEC_COMPANY_FACTS in bundle["degraded_lanes"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["sec"]

    def test_empty_artifact_payload_is_degraded_not_a_gap(self):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        self._seed_artifact(client, artifact_id=artifact_id, ticker="AAPL", payload={})
        client.table("research_artifact_sources").insert({
            "id": str(uuid.uuid4()), "artifact_id": artifact_id, "user_id": USER,
            "provider_name": "sec_edgar", "source_kind": "sec_filing",
        }).execute()
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        row = client.rows("intel_run_tickers")[0]
        bundle = build_evidence_bundle(
            client, session={"id": session_id}, ticker_row=row,
        )
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_refs_by_lane"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_ref_gaps"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["usable_lanes"]
        assert LANE_SEC_COMPANY_FACTS in bundle["degraded_lanes"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["sec"]

    def test_zero_observation_count_payload_is_degraded_not_substantive(self):
        # observation_count=0 is exactly the "thin"/no-data shape the adapter
        # itself already treats as not worth persisting substance for — a
        # nonempty dict alone must never be mistaken for real evidence.
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        self._seed_artifact(
            client, artifact_id=artifact_id, ticker="AAPL",
            payload={"observation_count": 0},
        )
        client.table("research_artifact_sources").insert({
            "id": str(uuid.uuid4()), "artifact_id": artifact_id, "user_id": USER,
            "provider_name": "sec_edgar", "source_kind": "sec_filing",
        }).execute()
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        row = client.rows("intel_run_tickers")[0]
        bundle = build_evidence_bundle(
            client, session={"id": session_id}, ticker_row=row,
        )
        assert LANE_SEC_COMPANY_FACTS not in bundle["usable_lanes"]
        assert LANE_SEC_COMPANY_FACTS in bundle["degraded_lanes"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["sec"]

    def test_invalidated_artifact_rejected_even_if_is_active_true(self):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        self._seed_artifact(
            client, artifact_id=artifact_id, ticker="AAPL",
            is_active=True, invalidated_at="2026-07-01T00:00:00+00:00",
        )
        client.table("research_artifact_sources").insert({
            "id": str(uuid.uuid4()), "artifact_id": artifact_id, "user_id": USER,
            "provider_name": "sec_edgar", "source_kind": "sec_filing",
        }).execute()
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        row = client.rows("intel_run_tickers")[0]
        bundle = build_evidence_bundle(
            client, session={"id": session_id}, ticker_row=row,
        )
        assert LANE_SEC_COMPANY_FACTS not in bundle["usable_lanes"]
        assert LANE_SEC_COMPANY_FACTS in bundle["degraded_lanes"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["sec"]

    def test_wrong_skill_pack_artifact_rejected(self):
        # A payload/type/scope that superficially matches but comes from a
        # DIFFERENT adapter's skill pack must never be accepted as this
        # lane's evidence — the contract is the FULL (type, skill_pack,
        # scope_kind) tuple, not any one field alone.
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        client.table("research_artifacts").insert({
            "id": artifact_id, "user_id": USER, "ticker": "AAPL",
            "artifact_type": "fundamental_quality",
            "skill_pack": "some_other_skill_pack_v1",
            "scope_kind": "ticker",
            "payload": {"observation_count": 3},
            "is_active": True,
        }).execute()
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        row = client.rows("intel_run_tickers")[0]
        bundle = build_evidence_bundle(
            client, session={"id": session_id}, ticker_row=row,
        )
        assert LANE_SEC_COMPANY_FACTS not in bundle["usable_lanes"]
        assert LANE_SEC_COMPANY_FACTS in bundle["degraded_lanes"]
        assert LANE_SEC_COMPANY_FACTS not in bundle["sec"]

    def _bundle_with_artifact(
        self, *, artifact_id, source_id="0001-26-000001", provider_name="sec_edgar",
    ):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        self._seed_artifact(client, artifact_id=artifact_id, ticker="AAPL")
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        client.table("research_artifact_sources").insert({
            "id": str(uuid.uuid4()), "artifact_id": artifact_id, "user_id": USER,
            "provider_name": provider_name, "source_kind": "sec_filing",
            "source_id": source_id,
        }).execute()
        row = client.rows("intel_run_tickers")[0]
        return build_evidence_bundle(client, session={"id": session_id}, ticker_row=row)

    def test_internal_artifact_id_change_alone_does_not_change_fingerprint(self):
        # Round-3 item 4 (full-bundle integration, not just a pure
        # reference-projection test): replacing an internal artifact/task
        # storage row with the SAME external evidence and SAME external
        # source must never change the analytical fingerprint.
        bundle_a = self._bundle_with_artifact(artifact_id=str(uuid.uuid4()))
        bundle_b = self._bundle_with_artifact(artifact_id=str(uuid.uuid4()))
        assert bundle_a["input_fingerprint"] == bundle_b["input_fingerprint"]

    def test_external_source_identity_change_alters_fingerprint(self):
        bundle_a = self._bundle_with_artifact(
            artifact_id=str(uuid.uuid4()), source_id="0001-26-000001",
        )
        bundle_b = self._bundle_with_artifact(
            artifact_id=str(uuid.uuid4()), source_id="0009-26-000099",
        )
        assert bundle_a["input_fingerprint"] != bundle_b["input_fingerprint"]

    def test_external_provider_identity_change_alters_fingerprint(self):
        bundle_a = self._bundle_with_artifact(
            artifact_id=str(uuid.uuid4()), provider_name="sec_edgar",
        )
        bundle_b = self._bundle_with_artifact(
            artifact_id=str(uuid.uuid4()), provider_name="sec_edgar_v2",
        )
        assert bundle_a["input_fingerprint"] != bundle_b["input_fingerprint"]

    def test_artifact_with_no_source_rows_is_a_gap_not_a_reference(self):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        self._seed_artifact(client, artifact_id=artifact_id, ticker="AAPL")
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        # No research_artifact_sources rows inserted for this artifact.
        row = client.rows("intel_run_tickers")[0]
        bundle = build_evidence_bundle(
            client, session={"id": session_id}, ticker_row=row,
        )
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_refs_by_lane"]
        assert LANE_SEC_COMPANY_FACTS in bundle["source_ref_gaps"]
        # The evidence itself (sec summary payload) must still be usable —
        # a lineage gap never erases otherwise-usable evidence — the
        # SUBSTANTIVE artifact payload (owned + ticker-scoped + active) IS
        # present in the bundle summary even without source rows.
        assert LANE_SEC_COMPANY_FACTS in bundle["usable_lanes"]
        assert bundle["sec"][LANE_SEC_COMPANY_FACTS]["payload"]

    def test_artifact_source_read_failure_fails_closed_without_crashing(self):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        self._seed_artifact(client, artifact_id=artifact_id, ticker="AAPL")
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        client.table("research_artifact_sources").insert({
            "id": str(uuid.uuid4()), "artifact_id": artifact_id, "user_id": USER,
            "provider_name": "sec_edgar", "source_kind": "sec_filing",
        }).execute()

        class _FailingClient:
            def __init__(self, inner):
                self._inner = inner

            def table(self, name):
                if name == "research_artifact_sources":
                    raise RuntimeError("simulated read failure")
                return self._inner.table(name)

            def rows(self, name):
                return self._inner.rows(name)

        failing_client = _FailingClient(client)
        row = client.rows("intel_run_tickers")[0]
        # Must not raise — construction fails closed for lineage only.
        bundle = build_evidence_bundle(
            failing_client, session={"id": session_id}, ticker_row=row,
        )
        assert LANE_SEC_COMPANY_FACTS not in bundle["source_refs_by_lane"]
        assert LANE_SEC_COMPANY_FACTS in bundle["source_ref_gaps"]
        assert LANE_SEC_COMPANY_FACTS in bundle["usable_lanes"]


class TestSentimentCatalystSuppliedLanes:
    """Round-3 item 2: AXIS_SENTIMENT carries SEC catalyst evidence in its
    own compact ``catalysts`` list (not the ``sec`` dict) — a substantive
    catalyst artifact there must count as LANE_SEC_CATALYST being supplied,
    exactly as it would via the ``sec`` dict path for other axes."""

    def _catalyst_summary(self):
        return {
            "artifact_id": "internal-artifact-uuid-123",
            "artifact_type": "sec_catalyst_sentiment",
            "skill_pack": "sec_catalyst_sentiment_v1",
            "generated_at": "2026-07-24T00:00:00+00:00",
            "freshness_status": "fresh",
            "trust_level": "high",
            "payload": {"catalyst_count": 2, "catalysts": [{"kind": "8-K"}]},
        }

    def _bundle(self):
        return {
            "ticker": "AAPL", "asset_type": "equity",
            "portfolio_context": {}, "missing_lanes": [], "degraded_lanes": [],
            "market": {"price": 100.0},
            "sentiment": {"headline_count": 3},
            "catalysts": [self._catalyst_summary()],
            "source_refs_by_lane": {},
        }

    def test_catalyst_evidence_is_supplied_and_expected_for_sentiment(self):
        from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
            axis_evidence_context,
        )

        context = axis_evidence_context(self._bundle(), AXIS_SENTIMENT)
        assert LANE_SEC_CATALYST in context["supplied_lanes"]
        assert LANE_SEC_CATALYST in context["manifest"]["expected_lanes"]

    def test_catalyst_without_reference_is_partial(self):
        from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
            axis_evidence_context,
        )

        context = axis_evidence_context(self._bundle(), AXIS_SENTIMENT)
        assert context["manifest"]["status"] in (
            lineage.LINEAGE_PARTIAL, lineage.LINEAGE_MISSING,
        )
        assert LANE_SEC_CATALYST in context["manifest"]["missing_ref_lanes"]

    def test_catalyst_with_reference_and_all_other_lanes_linked_is_full(self):
        from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
            axis_evidence_context,
        )

        bundle = self._bundle()
        bundle["source_refs_by_lane"] = {
            LANE_PRICE: [lineage.make_provider_observation_ref(
                lane=LANE_PRICE, ticker="AAPL", task_id="t0",
                output={"price": 100.0, "source": "yfinance", "as_of": "x"},
            )],
            LANE_NEWS_SENTIMENT: [lineage.make_provider_observation_ref(
                lane=LANE_NEWS_SENTIMENT, ticker="AAPL", task_id="t1",
                output={"headline_count": 3, "source": "yfinance", "as_of": "x"},
            )],
            LANE_SEC_CATALYST: [lineage.make_research_artifact_source_ref(
                lane=LANE_SEC_CATALYST, ticker="AAPL", artifact_id="art-1",
                source_row={
                    "id": "src-1", "artifact_id": "art-1",
                    "provider_name": "sec_edgar", "source_kind": "sec_filing",
                    "source_id": "0001-26-000001",
                },
            )],
        }
        context = axis_evidence_context(bundle, AXIS_SENTIMENT)
        assert context["manifest"]["status"] == lineage.LINEAGE_FULL

    def test_payload_only_strips_internal_ids_from_catalysts_list(self):
        from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
            _compact_bundle_for_axis,
        )

        compact = _compact_bundle_for_axis(self._bundle(), AXIS_SENTIMENT)
        catalysts = compact["catalysts"]
        assert catalysts
        for item in catalysts:
            assert "artifact_id" not in item
            assert "artifact_type" not in item
            assert "skill_pack" not in item
            assert item["payload"]


class TestAxisSuppliedLanesContract:
    """Round-3 item 2: every nonempty external-evidence field in each axis's
    compact prompt maps to exactly one AXIS_CANDIDATE_LANES entry, and no
    absent field ever creates a supplied lane."""

    def _full_bundle(self):
        return {
            "ticker": "AAPL", "asset_type": "equity",
            "portfolio_context": {}, "missing_lanes": [], "degraded_lanes": [],
            "market": {"price": 100.0},
            "technical": {"sma_50": 1.0},
            "fundamental": {
                "pe": 10.0, "debt_to_equity": 1.0, "total_debt": 1.0,
                "cash": 1.0, "beta": 1.0,
            },
            "valuation": {"pe": 10.0},
            "sentiment": {"headline_count": 3},
            "sec": {
                LANE_SEC_COMPANY_FACTS: {
                    "artifact_id": "a1", "generated_at": "x", "trust_level": "high",
                    "payload": {"observation_count": 3},
                },
                LANE_SEC_CATALYST: {
                    "artifact_id": "a2", "generated_at": "x", "trust_level": "high",
                    "payload": {"catalyst_count": 1},
                },
            },
            "catalysts": [{
                "artifact_id": "a2", "generated_at": "x", "trust_level": "high",
                "payload": {"catalyst_count": 1},
            }],
            "asset_specific": {
                "etf_fund_data": {
                    "artifact_id": "a3", "generated_at": "x", "trust_level": "high",
                    "payload": {"holdings_count": 5},
                },
                "crypto_market": {"market_cap_rank": 1},
            },
            "source_refs_by_lane": {},
        }

    @pytest.mark.parametrize("axis,expected_subset", [
        (AXIS_FUNDAMENTAL, {LANE_PRICE, LANE_FUNDAMENTALS, LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST}),
        (AXIS_TECHNICAL, {LANE_PRICE, LANE_TECHNICALS}),
        (AXIS_SENTIMENT, {LANE_PRICE, LANE_NEWS_SENTIMENT, LANE_SEC_CATALYST}),
        (AXIS_RISK_FILING, {LANE_PRICE, LANE_FUNDAMENTALS, LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST}),
        (AXIS_ETF_EXPOSURE, {LANE_PRICE, LANE_TECHNICALS, LANE_ETF_FUND_DATA, LANE_FUNDAMENTALS}),
        (AXIS_CRYPTO_MARKET, {LANE_PRICE, LANE_CRYPTO_MARKET, LANE_TECHNICALS}),
    ])
    def test_fully_populated_bundle_supplies_exactly_its_axis_candidate_lanes(
        self, axis, expected_subset,
    ):
        from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
            axis_evidence_context,
        )

        context = axis_evidence_context(self._full_bundle(), axis)
        assert set(context["supplied_lanes"]) == expected_subset
        assert set(context["supplied_lanes"]) <= set(
            lineage.AXIS_CANDIDATE_LANES.get(axis, ())
        )

    @pytest.mark.parametrize("axis", [
        AXIS_FUNDAMENTAL, AXIS_TECHNICAL, AXIS_SENTIMENT,
        AXIS_RISK_FILING, AXIS_ETF_EXPOSURE, AXIS_CRYPTO_MARKET,
    ])
    def test_empty_bundle_supplies_no_lanes(self, axis):
        from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
            axis_evidence_context,
        )

        empty_bundle = {
            "ticker": "AAPL", "asset_type": "equity",
            "portfolio_context": {}, "missing_lanes": [], "degraded_lanes": [],
            "market": {}, "source_refs_by_lane": {},
        }
        context = axis_evidence_context(empty_bundle, axis)
        assert context["supplied_lanes"] == []


# ── Axis-scoped specialist lineage + reuse + review ─────────────────────────

async def _ready_bundle_session(
    client: FakeSupabase, monkeypatch, tickers: list[str],
    categories: dict[str, str] | None = None,
) -> tuple[str, dict]:
    categories = categories or {}
    for ticker in tickers:
        seed_position(client, USER, ticker, category=categories.get(ticker, "Core"))
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    recorder = ProviderRecorder()
    patch_providers(monkeypatch, recorder)
    settings = make_settings()
    session = client.rows("intel_run_sessions")[0]
    for task in list(client.rows("intel_run_tasks")):
        if task["task_type"] != TASK_COLLECT_EVIDENCE_LANE:
            continue
        result = await execute_collector_task(client, task=task, settings=settings)
        client.table("intel_run_tasks").update({
            "state": result.final_state
            if result.final_state in (TASK_SUCCEEDED, TASK_DEGRADED)
            else TASK_DEGRADED,
            "output": result.output, "output_ref": result.output_ref,
            "completed_at": "2026-07-24T00:00:00+00:00",
        }).eq("id", task["id"]).execute()
    for row in client.rows("intel_run_tickers"):
        build_evidence_bundle(client, session=session, ticker_row=row)
    return session_id, session


class TestSpecialistAxisLineage:
    @pytest.mark.asyncio
    async def test_fundamental_and_technical_outputs_get_only_their_own_lanes(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        await _ready_bundle_session(client, monkeypatch, ["AAPL"])
        session = client.rows("intel_run_sessions")[0]
        run_scheduler_pass(client, session=session)
        llm = FakeLLM()
        for axis in (AXIS_FUNDAMENTAL, AXIS_TECHNICAL):
            task = next(
                t for t in client.rows("intel_run_tasks")
                if t["task_type"] == TASK_SPECIALIST_ANALYSIS and t["lane"] == axis
            )
            task = claim_task_row(client, task)
            outcome = await execute_specialist_task(client, task=task, llm=llm)
            assert outcome.final_state == TASK_SUCCEEDED

        outputs = {
            o["axis"]: o for o in client.rows("intel_run_specialist_outputs")
        }
        fundamental_manifest = outputs[AXIS_FUNDAMENTAL]["evidence_refs"]
        technical_manifest = outputs[AXIS_TECHNICAL]["evidence_refs"]
        assert fundamental_manifest["axis"] == AXIS_FUNDAMENTAL
        assert technical_manifest["axis"] == AXIS_TECHNICAL
        # Fundamental never carries a technicals-only lane reference and
        # vice versa — no cross-axis leakage of the whole-bundle list.
        fundamental_lanes = {r["lane"] for r in fundamental_manifest["refs"]}
        technical_lanes = {r["lane"] for r in technical_manifest["refs"]}
        assert LANE_TECHNICALS not in fundamental_lanes
        assert LANE_FUNDAMENTALS not in technical_lanes
        assert fundamental_manifest["status"] == lineage.LINEAGE_FULL
        assert technical_manifest["status"] == lineage.LINEAGE_FULL

    @pytest.mark.asyncio
    async def test_prompt_bundle_carries_bounded_evidence_sources_projection(
        self, monkeypatch,
    ):
        from app.services.intelligence.v3.distributed.specialist_agents_v1 import (
            axis_evidence_context,
        )

        client = FakeSupabase()
        await _ready_bundle_session(client, monkeypatch, ["AAPL"])
        row = next(r for r in client.rows("intel_run_tickers") if r["ticker"] == "AAPL")
        bundle = row["evidence_bundle"]
        context = axis_evidence_context(bundle, AXIS_TECHNICAL)
        assert LANE_PRICE in context["supplied_lanes"]
        assert LANE_TECHNICALS in context["supplied_lanes"]
        compact = context["compact_bundle"]
        assert "evidence_sources" in compact
        assert compact["evidence_sources"]
        for source in compact["evidence_sources"]:
            # Bounded, identity-only projection — never a raw payload/citation.
            # ``identity_token`` is present only when derivable (e.g. from a
            # non-price provider's output_digest) — price stays identified
            # by lane/provider alone so intraday ticks don't defeat reuse.
            assert {"lane", "ref_type", "provider"} <= set(source)
            assert set(source) <= {"lane", "ref_type", "provider", "identity_token"}


class TestSpecialistReuse:
    @pytest.mark.asyncio
    async def test_reused_output_is_rebound_to_current_session_lineage(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        await _ready_bundle_session(client, monkeypatch, ["AAPL"])
        session_1 = client.rows("intel_run_sessions")[0]
        run_scheduler_pass(client, session=session_1)
        task_1 = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS and t["lane"] == AXIS_TECHNICAL
        )
        task_1 = claim_task_row(client, task_1)
        llm = FakeLLM()
        outcome_1 = await execute_specialist_task(client, task=task_1, llm=llm)
        assert outcome_1.final_state == TASK_SUCCEEDED
        first_output = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["axis"] == AXIS_TECHNICAL
        )
        assert first_output["evidence_refs"]["refs"]

        # Second session, same ticker/evidence (fresh position row unaffected
        # — identical technicals/price fixture) — same input fingerprint.
        for row in client.rows("intel_run_sessions"):
            if row.get("status") in ("created", "running"):
                row["status"] = "completed"
        seed_position(client, USER, "AAPL", category="Core")
        session_id_2 = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id_2,
        )
        session_2 = next(
            s for s in client.rows("intel_run_sessions") if s["id"] == session_id_2
        )
        settings = make_settings()
        for task in list(client.rows("intel_run_tasks")):
            if task["run_session_id"] != session_id_2 or task["task_type"] != TASK_COLLECT_EVIDENCE_LANE:
                continue
            result = await execute_collector_task(client, task=task, settings=settings)
            client.table("intel_run_tasks").update({
                "state": result.final_state
                if result.final_state in (TASK_SUCCEEDED, TASK_DEGRADED)
                else TASK_DEGRADED,
                "output": result.output, "output_ref": result.output_ref,
                "completed_at": "2026-07-24T00:00:00+00:00",
            }).eq("id", task["id"]).execute()
        row_2 = next(
            r for r in client.rows("intel_run_tickers")
            if r["run_session_id"] == session_id_2
        )
        build_evidence_bundle(client, session=session_2, ticker_row=row_2)
        run_scheduler_pass(client, session=session_2)
        task_2 = next(
            t for t in client.rows("intel_run_tasks")
            if t["run_session_id"] == session_id_2
            and t["task_type"] == TASK_SPECIALIST_ANALYSIS and t["lane"] == AXIS_TECHNICAL
        )
        task_2 = claim_task_row(client, task_2)
        llm2 = FakeLLM()
        outcome_2 = await execute_specialist_task(client, task=task_2, llm=llm2)
        assert "AAPL" in outcome_2.reused
        assert llm2.calls == []  # reuse skips the LLM entirely

        reused_output = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["axis"] == AXIS_TECHNICAL and o["run_session_id"] == session_id_2
        )
        assert reused_output["prompt_version"] == PROMPT_VERSION
        # Rebuilt lineage never carries the FIRST session's task_id forward.
        reused_task_ids = {
            r.get("task_id") for r in reused_output["evidence_refs"]["refs"]
        }
        first_task_ids = {
            r.get("task_id") for r in first_output["evidence_refs"]["refs"]
        }
        assert reused_task_ids.isdisjoint(first_task_ids)

    @pytest.mark.asyncio
    async def test_legacy_prompt_version_output_is_never_reused(self, monkeypatch):
        client = FakeSupabase()
        await _ready_bundle_session(client, monkeypatch, ["AAPL"])
        session_1 = client.rows("intel_run_sessions")[0]
        run_scheduler_pass(client, session=session_1)
        task_1 = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_SPECIALIST_ANALYSIS and t["lane"] == AXIS_TECHNICAL
        )
        task_1 = claim_task_row(client, task_1)
        llm = FakeLLM()
        await execute_specialist_task(client, task=task_1, llm=llm)
        # Downgrade the persisted row to simulate a pre-PR-2 legacy output.
        client.table("intel_run_specialist_outputs").update({
            "prompt_version": "distributed_specialist_v1",
        }).eq("axis", AXIS_TECHNICAL).execute()

        for row in client.rows("intel_run_sessions"):
            if row.get("status") in ("created", "running"):
                row["status"] = "completed"
        seed_position(client, USER, "AAPL", category="Core")
        session_id_2 = str(uuid.uuid4())
        await control.create_distributed_session(
            client=client, user_id=USER, session_id=session_id_2,
        )
        session_2 = next(
            s for s in client.rows("intel_run_sessions") if s["id"] == session_id_2
        )
        settings = make_settings()
        for task in list(client.rows("intel_run_tasks")):
            if task["run_session_id"] != session_id_2 or task["task_type"] != TASK_COLLECT_EVIDENCE_LANE:
                continue
            result = await execute_collector_task(client, task=task, settings=settings)
            client.table("intel_run_tasks").update({
                "state": result.final_state
                if result.final_state in (TASK_SUCCEEDED, TASK_DEGRADED)
                else TASK_DEGRADED,
                "output": result.output, "output_ref": result.output_ref,
                "completed_at": "2026-07-24T00:00:00+00:00",
            }).eq("id", task["id"]).execute()
        row_2 = next(
            r for r in client.rows("intel_run_tickers")
            if r["run_session_id"] == session_id_2
        )
        build_evidence_bundle(client, session=session_2, ticker_row=row_2)
        run_scheduler_pass(client, session=session_2)
        task_2 = next(
            t for t in client.rows("intel_run_tasks")
            if t["run_session_id"] == session_id_2
            and t["task_type"] == TASK_SPECIALIST_ANALYSIS and t["lane"] == AXIS_TECHNICAL
        )
        task_2 = claim_task_row(client, task_2)
        llm2 = FakeLLM()
        outcome_2 = await execute_specialist_task(client, task=task_2, llm=llm2)
        # The legacy-prompt-version row must NOT be reused — a fresh LLM
        # call happens instead.
        assert "AAPL" not in outcome_2.reused
        assert llm2.calls


class TestReviewLineage:
    @pytest.mark.asyncio
    async def test_successful_review_inherits_union_of_input_lineage(
        self, monkeypatch,
    ):
        client = FakeSupabase()
        await _ready_bundle_session(client, monkeypatch, ["AAPL"])
        session = client.rows("intel_run_sessions")[0]
        run_scheduler_pass(client, session=session)
        llm = FakeLLM()
        for axis in (AXIS_FUNDAMENTAL, AXIS_TECHNICAL):
            task = next(
                t for t in client.rows("intel_run_tasks")
                if t["task_type"] == TASK_SPECIALIST_ANALYSIS and t["lane"] == axis
            )
            task = claim_task_row(client, task)
            await execute_specialist_task(client, task=task, llm=llm)

        review_task = {
            "id": str(uuid.uuid4()), "run_session_id": session["id"],
            "user_id": USER, "task_type": TASK_REVIEW_CONFLICT, "ticker": "AAPL",
            "state": "claimed", "claim_owner": "test-worker",
            "claim_token": str(uuid.uuid4()), "attempts": 1,
        }
        client.table("intel_run_tasks").insert(review_task).execute()
        review_task = next(
            t for t in client.rows("intel_run_tasks") if t["id"] == review_task["id"]
        )
        outcome = await execute_review_task(client, task=review_task, llm=llm)
        assert outcome.final_state == TASK_SUCCEEDED
        review_output = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["axis"] == AXIS_REVIEW
        )
        manifest = review_output["evidence_refs"]
        assert manifest["status"] == lineage.LINEAGE_FULL
        assert manifest["refs"]
        assert review_output["input_fingerprint"]  # never the empty string

    @pytest.mark.asyncio
    async def test_one_partial_input_makes_review_lineage_partial(self):
        client = FakeSupabase()
        full_manifest = lineage.build_axis_lineage_manifest(
            axis=AXIS_TECHNICAL,
            source_refs_by_lane={LANE_PRICE: [lineage.make_provider_observation_ref(
                lane=LANE_PRICE, ticker="AAA", task_id="t1",
                output={"price": 1, "source": "yfinance", "as_of": "x"},
            )]},
            supplied_lanes=[LANE_PRICE],
        )
        session_id = str(uuid.uuid4())
        client.table("intel_run_sessions").insert({
            "id": session_id, "user_id": USER, "status": "running",
            "workflow_version": 2,
        }).execute()
        client.table("intel_run_specialist_outputs").insert({
            "id": str(uuid.uuid4()), "run_session_id": session_id, "user_id": USER,
            "ticker": "AAA", "axis": AXIS_TECHNICAL, "score": 0.5, "confidence": 0.8,
            "key_findings": ["f"], "risks": [], "evidence_refs": full_manifest,
        }).execute()
        client.table("intel_run_specialist_outputs").insert({
            "id": str(uuid.uuid4()), "run_session_id": session_id, "user_id": USER,
            "ticker": "AAA", "axis": AXIS_FUNDAMENTAL, "score": 0.5, "confidence": 0.8,
            "key_findings": ["f"], "risks": [], "evidence_refs": None,
        }).execute()
        review_task = {
            "id": str(uuid.uuid4()), "run_session_id": session_id,
            "user_id": USER, "task_type": TASK_REVIEW_CONFLICT, "ticker": "AAA",
            "state": "claimed", "claim_owner": "test-worker",
            "claim_token": str(uuid.uuid4()), "attempts": 1,
        }
        client.table("intel_run_tasks").insert(review_task).execute()
        review_task = next(
            t for t in client.rows("intel_run_tasks") if t["id"] == review_task["id"]
        )
        llm = FakeLLM()
        outcome = await execute_review_task(client, task=review_task, llm=llm)
        assert outcome.final_state == TASK_SUCCEEDED
        review_output = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["axis"] == AXIS_REVIEW
        )
        assert review_output["evidence_refs"]["status"] == lineage.LINEAGE_PARTIAL

    @pytest.mark.asyncio
    async def test_invalid_specialist_rows_excluded_call_count_unchanged(self):
        client = FakeSupabase()
        session_id = str(uuid.uuid4())
        client.table("intel_run_sessions").insert({
            "id": session_id, "user_id": USER, "status": "running",
            "workflow_version": 2,
        }).execute()
        # Valid row — included.
        client.table("intel_run_specialist_outputs").insert({
            "id": str(uuid.uuid4()), "run_session_id": session_id, "user_id": USER,
            "ticker": "AAA", "axis": AXIS_TECHNICAL, "score": 0.5, "confidence": 0.8,
            "key_findings": ["f"], "risks": [], "evidence_refs": None,
        }).execute()
        # Invalid row (no confidence) — must be excluded from reconciliation.
        client.table("intel_run_specialist_outputs").insert({
            "id": str(uuid.uuid4()), "run_session_id": session_id, "user_id": USER,
            "ticker": "AAA", "axis": AXIS_FUNDAMENTAL, "score": 0.5, "confidence": None,
            "key_findings": ["f"], "risks": [], "evidence_refs": None,
        }).execute()
        review_task = {
            "id": str(uuid.uuid4()), "run_session_id": session_id,
            "user_id": USER, "task_type": TASK_REVIEW_CONFLICT, "ticker": "AAA",
            "state": "claimed", "claim_owner": "test-worker",
            "claim_token": str(uuid.uuid4()), "attempts": 1,
        }
        client.table("intel_run_tasks").insert(review_task).execute()
        review_task = next(
            t for t in client.rows("intel_run_tasks") if t["id"] == review_task["id"]
        )
        llm = FakeLLM()
        outcome = await execute_review_task(client, task=review_task, llm=llm)
        assert outcome.final_state == TASK_SUCCEEDED
        # Review model/token-budget/retry/call-count contract is untouched —
        # exactly one LLM call regardless of how many rows were filtered.
        assert outcome.llm_calls == 1
        review_output = next(
            o for o in client.rows("intel_run_specialist_outputs")
            if o["axis"] == AXIS_REVIEW
        )
        assert review_output["evidence_refs"]["derived_from_axes"] == [AXIS_TECHNICAL]


# ── Fingerprint canonical source-identity projection (contract §3 patch) ────

class TestFingerprintSourceIdentityProjection:
    def test_price_lane_output_digest_excluded_from_projection(self):
        ref_a = lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t1",
            output={"price": 100.0, "source": "yfinance", "as_of": "x"},
        )
        ref_b = lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t2",
            output={"price": 105.5, "source": "yfinance", "as_of": "y"},
        )
        proj_a = lineage.source_identity_projection(ref_a)
        proj_b = lineage.source_identity_projection(ref_b)
        assert "output_digest" not in proj_a
        assert proj_a == proj_b

    def test_other_lane_output_digest_retained_in_projection(self):
        ref_a = lineage.make_provider_observation_ref(
            lane=LANE_TECHNICALS, ticker="AAA", task_id="t1",
            output={"last": 100.0, "source": "yfinance", "as_of": "x"},
        )
        ref_b = lineage.make_provider_observation_ref(
            lane=LANE_TECHNICALS, ticker="AAA", task_id="t2",
            output={"last": 105.5, "source": "yfinance", "as_of": "y"},
        )
        proj_a = lineage.source_identity_projection(ref_a)
        proj_b = lineage.source_identity_projection(ref_b)
        assert "output_digest" in proj_a
        assert proj_a != proj_b

    def test_artifact_projection_ignores_internal_ids_keeps_external_identity(self):
        ref_a = lineage.make_research_artifact_source_ref(
            lane=LANE_SEC_COMPANY_FACTS, ticker="AAA", artifact_id="art-1",
            source_row={"id": "src-1", "provider_name": "sec_edgar",
                        "source_id": "0001234567-26-000001"},
        )
        ref_b = lineage.make_research_artifact_source_ref(
            lane=LANE_SEC_COMPANY_FACTS, ticker="AAA", artifact_id="art-2",
            source_row={"id": "src-2", "provider_name": "sec_edgar",
                        "source_id": "0001234567-26-000001"},
        )
        proj_a = lineage.source_identity_projection(ref_a)
        proj_b = lineage.source_identity_projection(ref_b)
        assert proj_a == proj_b
        assert "artifact_id" not in proj_a
        assert "artifact_source_id" not in proj_a

    def test_artifact_projection_changes_with_genuine_external_identity(self):
        ref_a = lineage.make_research_artifact_source_ref(
            lane=LANE_SEC_COMPANY_FACTS, ticker="AAA", artifact_id="art-1",
            source_row={"id": "src-1", "provider_name": "sec_edgar", "source_id": "AAA-FILING"},
        )
        ref_b = lineage.make_research_artifact_source_ref(
            lane=LANE_SEC_COMPANY_FACTS, ticker="AAA", artifact_id="art-1",
            source_row={"id": "src-1", "provider_name": "sec_edgar", "source_id": "BBB-FILING"},
        )
        assert (
            lineage.source_identity_projection(ref_a)
            != lineage.source_identity_projection(ref_b)
        )

    def test_provider_change_alters_projection(self):
        ref_a = lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t1",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )
        ref_b = lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t2",
            output={"price": 1, "source": "yfinance_v2", "as_of": "x"},
        )
        assert (
            lineage.source_identity_projection(ref_a)
            != lineage.source_identity_projection(ref_b)
        )

    def test_fingerprint_source_refs_retains_gaps(self):
        proj_a = lineage.fingerprint_source_refs({}, [])
        proj_b = lineage.fingerprint_source_refs({}, [LANE_NEWS_SENTIMENT])
        assert proj_a != proj_b


def _seed_session_and_ticker(client: FakeSupabase, ticker: str, asset_type: str = "equity") -> str:
    session_id = str(uuid.uuid4())
    client.table("intel_run_sessions").insert({
        "id": session_id, "user_id": USER, "status": "running", "workflow_version": 2,
    }).execute()
    store.insert_ticker_rows(
        client, run_session_id=session_id, user_id=USER,
        rows=[{"ticker": ticker, "asset_type": asset_type}],
    )
    return session_id


def _seed_direct_lane_task(client, *, session_id, ticker, lane, output):
    client.table("intel_run_tasks").insert({
        "id": str(uuid.uuid4()), "run_session_id": session_id, "user_id": USER,
        "task_type": TASK_COLLECT_EVIDENCE_LANE, "lane": lane, "ticker": ticker,
        "asset_type": "equity", "state": TASK_SUCCEEDED, "output": output,
    }).execute()


def _seed_full_direct_bundle_inputs(
    client, *, session_id, ticker, price_value=101.5, price_source="yfinance",
    technicals_last=100.0, has_price_ref=True,
):
    price_output = {"pct_1d": 0.1, "as_of": "2026-07-24T00:00:00+00:00"}
    if has_price_ref:
        price_output["price"] = price_value
        price_output["source"] = price_source
    else:
        price_output["price"] = price_value  # data present, but no attributable provider
    _seed_direct_lane_task(
        client, session_id=session_id, ticker=ticker, lane=LANE_PRICE, output=price_output,
    )
    _seed_direct_lane_task(
        client, session_id=session_id, ticker=ticker, lane=LANE_TECHNICALS,
        output={"last": technicals_last, "sma20": 99.0, "source": "yfinance",
                "as_of": "2026-07-24T00:00:00+00:00"},
    )
    _seed_direct_lane_task(
        client, session_id=session_id, ticker=ticker, lane=LANE_FUNDAMENTALS,
        output={"pe": 21.0, "market_cap": 1_000_000_000.0, "source": "yfinance",
                "as_of": "2026-07-24T00:00:00+00:00"},
    )
    _seed_direct_lane_task(
        client, session_id=session_id, ticker=ticker, lane=LANE_NEWS_SENTIMENT,
        output={"items": [{"headline": "x", "source": "yfinance", "datetime": 1}],
                "source": "yfinance", "as_of": "2026-07-24T00:00:00+00:00"},
    )


def _build_bundle_with_direct_inputs(**seed_kwargs) -> dict:
    client = FakeSupabase()
    ticker = seed_kwargs.get("ticker", "AAA")
    session_id = _seed_session_and_ticker(client, ticker)
    _seed_full_direct_bundle_inputs(client, session_id=session_id, **seed_kwargs)
    row = client.rows("intel_run_tickers")[0]
    return build_evidence_bundle(client, session={"id": session_id}, ticker_row=row)


class TestBundleFingerprintSourceIdentitySensitivity:
    def test_price_value_change_alone_does_not_change_bundle_fingerprint(self):
        bundle_a = _build_bundle_with_direct_inputs(ticker="AAA", price_value=101.5)
        bundle_b = _build_bundle_with_direct_inputs(ticker="AAA", price_value=999.0)
        assert bundle_a["input_fingerprint"] == bundle_b["input_fingerprint"]

    def test_technicals_value_change_alters_bundle_fingerprint(self):
        bundle_a = _build_bundle_with_direct_inputs(ticker="AAA", technicals_last=100.0)
        bundle_b = _build_bundle_with_direct_inputs(ticker="AAA", technicals_last=999.0)
        assert bundle_a["input_fingerprint"] != bundle_b["input_fingerprint"]

    def test_price_provider_identity_change_alters_bundle_fingerprint(self):
        bundle_a = _build_bundle_with_direct_inputs(ticker="AAA", price_source="yfinance")
        bundle_b = _build_bundle_with_direct_inputs(ticker="AAA", price_source="yfinance_v2")
        assert bundle_a["input_fingerprint"] != bundle_b["input_fingerprint"]

    def test_sourced_vs_gap_price_evidence_alters_bundle_fingerprint(self):
        bundle_a = _build_bundle_with_direct_inputs(ticker="AAA", has_price_ref=True)
        bundle_b = _build_bundle_with_direct_inputs(ticker="AAA", has_price_ref=False)
        assert LANE_PRICE in bundle_a["source_refs_by_lane"]
        assert LANE_PRICE in bundle_b["source_ref_gaps"]
        assert bundle_a["input_fingerprint"] != bundle_b["input_fingerprint"]


# ── Bounded reference storage (contract §4 patch) ────────────────────────────

class TestBoundedReferenceStorage:
    def test_lane_references_bounded_with_truncation_disclosed(self):
        many_rows = [
            {"id": f"src-{i}", "provider_name": "sec_edgar", "source_id": f"doc-{i}"}
            for i in range(12)
        ]
        refs = [
            lineage.make_research_artifact_source_ref(
                lane=LANE_SEC_COMPANY_FACTS, ticker="AAA", artifact_id="art-1",
                source_row=row,
            )
            for row in many_rows
        ]
        bounded, truncated = lineage.bound_references(refs, lineage.MAX_REFS_PER_LANE)
        assert len(bounded) == lineage.MAX_REFS_PER_LANE
        assert truncated == 12 - lineage.MAX_REFS_PER_LANE
        assert bounded == lineage.dedupe_references(refs)[:lineage.MAX_REFS_PER_LANE]

    def test_axis_manifest_bounded_to_24_and_round_trips(self):
        source_refs_by_lane: dict[str, list] = {}
        lanes = [LANE_PRICE, LANE_FUNDAMENTALS, LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST]
        for lane_idx, lane in enumerate(lanes):
            refs = []
            for i in range(8):
                if lane in (LANE_SEC_COMPANY_FACTS, LANE_SEC_CATALYST):
                    refs.append(lineage.make_research_artifact_source_ref(
                        lane=lane, ticker="AAA", artifact_id=f"art-{lane_idx}",
                        source_row={"id": f"src-{lane_idx}-{i}", "provider_name": "sec_edgar"},
                    ))
                else:
                    refs.append(lineage.make_provider_observation_ref(
                        lane=lane, ticker="AAA", task_id=f"t-{lane_idx}-{i}",
                        output={"value": i, "source": "yfinance", "as_of": "x"},
                    ))
            source_refs_by_lane[lane] = refs  # 8 per lane x 4 lanes = 32 raw refs
        manifest = lineage.build_axis_lineage_manifest(
            axis=AXIS_FUNDAMENTAL, source_refs_by_lane=source_refs_by_lane,
            supplied_lanes=lanes,
        )
        assert len(manifest["refs"]) <= lineage.MAX_REFS_PER_MANIFEST
        assert manifest["truncated_ref_count"] > 0
        reparsed = lineage.parse_axis_manifest(
            manifest, expected_axis=AXIS_FUNDAMENTAL, expected_ticker="AAA",
        )
        assert reparsed is not None
        assert reparsed["status"] == manifest["status"]

    def test_free_text_identifier_fields_are_capped(self):
        huge = "x" * 5000
        ref = lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id=huge,
            output={"price": 1, "source": huge, "as_of": "x"},
        )
        assert len(ref["task_id"]) <= lineage.MAX_FREE_TEXT_CHARS
        assert len(ref["provider"]) <= lineage.MAX_FREE_TEXT_CHARS

        artifact_ref = lineage.make_research_artifact_source_ref(
            lane=LANE_SEC_COMPANY_FACTS, ticker="AAA", artifact_id="art-1",
            source_row={"id": "src-1", "provider_name": "sec_edgar", "source_url": huge},
        )
        assert len(artifact_ref["source_url"]) <= lineage.MAX_FREE_TEXT_CHARS


# ── Review input-fingerprint sensitivity (contract §5 patch) ────────────────

class TestReviewInputFingerprintSensitivity:
    def _base_prompt_input(self) -> dict:
        return {
            "axis": AXIS_TECHNICAL, "stance": "positive", "score": 0.5, "confidence": 0.8,
            "key_findings": ["f1"], "risks": ["r1"],
            "lineage_status": lineage.LINEAGE_FULL, "linked_lanes": [LANE_PRICE],
            "missing_ref_lanes": [],
            "evidence_sources": [
                {"lane": LANE_PRICE, "ref_type": lineage.REF_TYPE_PROVIDER_OBSERVATION,
                 "provider": "yfinance"},
            ],
        }

    def test_finding_change_alters_fingerprint(self):
        base = self._base_prompt_input()
        changed = dict(base, key_findings=["a different finding"])
        fp1 = lineage.review_input_fingerprint([base], ticker="AAA", prompt_version="v2")
        fp2 = lineage.review_input_fingerprint([changed], ticker="AAA", prompt_version="v2")
        assert fp1 != fp2

    def test_risk_change_alters_fingerprint(self):
        base = self._base_prompt_input()
        changed = dict(base, risks=["a different risk"])
        fp1 = lineage.review_input_fingerprint([base], ticker="AAA", prompt_version="v2")
        fp2 = lineage.review_input_fingerprint([changed], ticker="AAA", prompt_version="v2")
        assert fp1 != fp2

    def test_score_or_confidence_change_alters_fingerprint(self):
        base = self._base_prompt_input()
        changed = dict(base, confidence=0.1)
        fp1 = lineage.review_input_fingerprint([base], ticker="AAA", prompt_version="v2")
        fp2 = lineage.review_input_fingerprint([changed], ticker="AAA", prompt_version="v2")
        assert fp1 != fp2

    def test_lineage_status_change_alters_fingerprint(self):
        base = self._base_prompt_input()
        changed = dict(base, lineage_status=lineage.LINEAGE_PARTIAL)
        fp1 = lineage.review_input_fingerprint([base], ticker="AAA", prompt_version="v2")
        fp2 = lineage.review_input_fingerprint([changed], ticker="AAA", prompt_version="v2")
        assert fp1 != fp2

    def test_missing_ref_lane_change_alters_fingerprint(self):
        base = self._base_prompt_input()
        changed = dict(base, missing_ref_lanes=[LANE_TECHNICALS])
        fp1 = lineage.review_input_fingerprint([base], ticker="AAA", prompt_version="v2")
        fp2 = lineage.review_input_fingerprint([changed], ticker="AAA", prompt_version="v2")
        assert fp1 != fp2

    def test_source_identity_change_alters_fingerprint(self):
        base = self._base_prompt_input()
        changed = dict(base, evidence_sources=[
            {"lane": LANE_PRICE, "ref_type": lineage.REF_TYPE_PROVIDER_OBSERVATION,
             "provider": "coingecko"},
        ])
        fp1 = lineage.review_input_fingerprint([base], ticker="AAA", prompt_version="v2")
        fp2 = lineage.review_input_fingerprint([changed], ticker="AAA", prompt_version="v2")
        assert fp1 != fp2

    def test_ordering_alone_does_not_alter_fingerprint(self):
        a = self._base_prompt_input()
        b = dict(self._base_prompt_input(), axis=AXIS_FUNDAMENTAL)
        fp1 = lineage.review_input_fingerprint([a, b], ticker="AAA", prompt_version="v2")
        fp2 = lineage.review_input_fingerprint([b, a], ticker="AAA", prompt_version="v2")
        assert fp1 == fp2

    def test_prompt_version_change_alters_fingerprint(self):
        base = self._base_prompt_input()
        fp1 = lineage.review_input_fingerprint([base], ticker="AAA", prompt_version="v2")
        fp2 = lineage.review_input_fingerprint([base], ticker="AAA", prompt_version="v3")
        assert fp1 != fp2

    def test_identity_token_change_alone_alters_fingerprint(self):
        base = self._base_prompt_input()
        base["evidence_sources"] = [{
            "lane": LANE_SEC_COMPANY_FACTS, "ref_type": lineage.REF_TYPE_RESEARCH_ARTIFACT_SOURCE,
            "provider": "sec_edgar", "identity_token": "token-a",
        }]
        changed = dict(base, evidence_sources=[{
            "lane": LANE_SEC_COMPANY_FACTS, "ref_type": lineage.REF_TYPE_RESEARCH_ARTIFACT_SOURCE,
            "provider": "sec_edgar", "identity_token": "token-b",
        }])
        fp1 = lineage.review_input_fingerprint([base], ticker="AAA", prompt_version="v2")
        fp2 = lineage.review_input_fingerprint([changed], ticker="AAA", prompt_version="v2")
        assert fp1 != fp2

    def test_finding_order_within_one_axis_is_preserved_not_resorted(self):
        # The OUTER list of axis-entries is order-independent (sorted by
        # axis), but findings/risks WITHIN one axis entry keep their
        # original semantic order — reordering them is a real content
        # change, not noise to normalize away.
        base = self._base_prompt_input()
        base["key_findings"] = ["first finding", "second finding"]
        reordered = dict(base, key_findings=["second finding", "first finding"])
        fp1 = lineage.review_input_fingerprint([base], ticker="AAA", prompt_version="v2")
        fp2 = lineage.review_input_fingerprint([reordered], ticker="AAA", prompt_version="v2")
        assert fp1 != fp2


class TestBuildReviewPromptContext:
    """Round-3 item 5: ONE normalized, bounded object drives BOTH the review
    LLM prompt (via ``json.dumps``) and ``review_input_fingerprint`` — never
    two independently hand-built shapes that could silently diverge."""

    def _reviewed_input(self, axis, *, findings=None, has_ref=True):
        manifest = lineage.build_axis_lineage_manifest(
            axis=axis,
            source_refs_by_lane=(
                {LANE_PRICE: [lineage.make_provider_observation_ref(
                    lane=LANE_PRICE, ticker="AAA", task_id=f"t-{axis}",
                    output={"price": 1, "source": "yfinance", "as_of": "x"},
                )]} if has_ref else {}
            ),
            supplied_lanes=[LANE_PRICE],
        )
        return {
            "axis": axis, "stance": "positive", "score": 0.5, "confidence": 0.8,
            "key_findings": findings or [f"{axis} finding"], "risks": [],
            "evidence_refs": manifest,
        }

    def test_context_items_carry_the_exact_fields_the_fingerprint_reads(self):
        inputs = [
            self._reviewed_input(AXIS_TECHNICAL),
            self._reviewed_input(AXIS_FUNDAMENTAL, has_ref=False),
        ]
        context = lineage.build_review_prompt_context(inputs, ticker="AAA")
        assert len(context) == 2
        for item in context:
            assert set(item) == {
                "axis", "stance", "score", "confidence", "key_findings", "risks",
                "lineage_status", "linked_lanes", "missing_ref_lanes", "evidence_sources",
            }
        # Sorted deterministically by axis.
        assert [c["axis"] for c in context] == sorted(c["axis"] for c in context)

    def test_same_object_feeds_both_prompt_json_and_fingerprint(self):
        import json

        inputs = [self._reviewed_input(AXIS_TECHNICAL)]
        context = lineage.build_review_prompt_context(inputs, ticker="AAA")
        # The exact object serialized for the LLM prompt...
        prompt_json = json.dumps(context, default=str)
        # ...must be exactly what review_input_fingerprint is fed.
        fp_from_context = lineage.review_input_fingerprint(
            context, ticker="AAA", prompt_version="v2",
        )
        reparsed_context = json.loads(prompt_json)
        fp_from_reparsed = lineage.review_input_fingerprint(
            reparsed_context, ticker="AAA", prompt_version="v2",
        )
        assert fp_from_context == fp_from_reparsed

    def test_db_row_order_does_not_change_built_context_or_fingerprint(self):
        inputs = [
            self._reviewed_input(AXIS_TECHNICAL),
            self._reviewed_input(AXIS_FUNDAMENTAL, has_ref=False),
        ]
        context_a = lineage.build_review_prompt_context(inputs, ticker="AAA")
        context_b = lineage.build_review_prompt_context(list(reversed(inputs)), ticker="AAA")
        assert context_a == context_b
        fp_a = lineage.review_input_fingerprint(context_a, ticker="AAA", prompt_version="v2")
        fp_b = lineage.review_input_fingerprint(context_b, ticker="AAA", prompt_version="v2")
        assert fp_a == fp_b

    def test_duplicate_axis_keeps_first_occurrence_only(self):
        first = self._reviewed_input(AXIS_TECHNICAL, findings=["first"])
        duplicate = self._reviewed_input(AXIS_TECHNICAL, findings=["second"])
        context = lineage.build_review_prompt_context([first, duplicate], ticker="AAA")
        assert len(context) == 1
        assert context[0]["key_findings"] == ["first"]

    def test_invalid_score_or_confidence_input_is_excluded(self):
        valid = self._reviewed_input(AXIS_TECHNICAL)
        invalid = {**self._reviewed_input(AXIS_FUNDAMENTAL), "score": None}
        context = lineage.build_review_prompt_context([valid, invalid], ticker="AAA")
        assert len(context) == 1
        assert context[0]["axis"] == AXIS_TECHNICAL

    def test_findings_and_risks_are_bounded_in_context(self):
        many_findings = [f"finding {i}" for i in range(10)]
        item = self._reviewed_input(AXIS_TECHNICAL, findings=many_findings)
        context = lineage.build_review_prompt_context([item], ticker="AAA")
        assert len(context[0]["key_findings"]) <= 2
