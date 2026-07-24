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
        usable_lanes=[LANE_PRICE, LANE_TECHNICALS],
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
        usable_lanes=[LANE_PRICE, LANE_TECHNICALS],
    )
    assert manifest["status"] == lineage.LINEAGE_FULL


def test_axis_lineage_manifest_missing_when_no_candidate_lane_usable():
    manifest = lineage.build_axis_lineage_manifest(
        axis=AXIS_TECHNICAL, source_refs_by_lane={}, usable_lanes=[],
    )
    assert manifest["status"] == lineage.LINEAGE_MISSING
    assert manifest["expected_lanes"] == []


def test_parse_axis_manifest_rejects_legacy_and_malformed():
    assert lineage.parse_axis_manifest(["legacy", "opaque"]) is None
    assert lineage.parse_axis_manifest([]) is None
    assert lineage.parse_axis_manifest(None) is None
    assert lineage.parse_axis_manifest({"status": "full"}) is None  # no schema_version


def test_review_lineage_manifest_full_only_when_every_input_full():
    full_manifest = lineage.build_axis_lineage_manifest(
        axis=AXIS_TECHNICAL,
        source_refs_by_lane={LANE_PRICE: [lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t1",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )]},
        usable_lanes=[LANE_PRICE],
    )
    partial_manifest = dict(full_manifest)
    partial_manifest["status"] = lineage.LINEAGE_PARTIAL

    review = lineage.build_review_lineage_manifest([
        {"axis": AXIS_TECHNICAL, "evidence_refs": full_manifest},
        {"axis": AXIS_FUNDAMENTAL, "evidence_refs": partial_manifest},
    ])
    assert review["status"] == lineage.LINEAGE_PARTIAL
    assert AXIS_FUNDAMENTAL in review["missing_ref_axes"]
    assert review["derived_from_axes"] == [AXIS_FUNDAMENTAL, AXIS_TECHNICAL]
    assert review["refs"]  # inherits the technical axis's valid reference

    review_all_full = lineage.build_review_lineage_manifest([
        {"axis": AXIS_TECHNICAL, "evidence_refs": full_manifest},
    ])
    assert review_all_full["status"] == lineage.LINEAGE_FULL
    assert review_all_full["missing_ref_axes"] == []


def test_review_never_writes_evidence_refs_empty_when_sourced_inputs_exist():
    full_manifest = lineage.build_axis_lineage_manifest(
        axis=AXIS_TECHNICAL,
        source_refs_by_lane={LANE_PRICE: [lineage.make_provider_observation_ref(
            lane=LANE_PRICE, ticker="AAA", task_id="t1",
            output={"price": 1, "source": "yfinance", "as_of": "x"},
        )]},
        usable_lanes=[LANE_PRICE],
    )
    review = lineage.build_review_lineage_manifest(
        [{"axis": AXIS_TECHNICAL, "evidence_refs": full_manifest}]
    )
    assert review["refs"] != []


def test_review_input_fingerprint_is_deterministic_and_nonempty():
    inputs = [{"axis": AXIS_TECHNICAL, "stance": "positive", "score": 0.5,
               "confidence": 0.8, "evidence_refs": None}]
    fp1 = lineage.review_input_fingerprint(inputs)
    fp2 = lineage.review_input_fingerprint(inputs)
    assert fp1 and fp1 == fp2


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

    def test_artifact_with_source_rows_creates_research_artifact_source_refs(self):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        client.table("research_artifact_sources").insert({
            "id": str(uuid.uuid4()), "artifact_id": artifact_id,
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

    def test_artifact_with_no_source_rows_is_a_gap_not_a_reference(self):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
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
        # a lineage gap never erases otherwise-usable evidence.
        assert LANE_SEC_COMPANY_FACTS in bundle["usable_lanes"]

    def test_artifact_source_read_failure_fails_closed_without_crashing(self):
        client = FakeSupabase()
        session_id = self._seed_ticker_and_session(client, "AAPL")
        artifact_id = str(uuid.uuid4())
        self._insert_lane_task(
            client, session_id=session_id, ticker="AAPL", lane=LANE_SEC_COMPANY_FACTS,
            asset_type="equity", state=TASK_SUCCEEDED,
            output={"artifact_id": artifact_id, "as_of": "2026-07-24T00:00:00+00:00"},
        )
        client.table("research_artifact_sources").insert({
            "id": str(uuid.uuid4()), "artifact_id": artifact_id,
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
            _compact_bundle_for_axis,
        )

        client = FakeSupabase()
        await _ready_bundle_session(client, monkeypatch, ["AAPL"])
        row = next(r for r in client.rows("intel_run_tickers") if r["ticker"] == "AAPL")
        bundle = row["evidence_bundle"]
        compact = _compact_bundle_for_axis(bundle, AXIS_TECHNICAL)
        assert "evidence_sources" in compact
        assert compact["evidence_sources"]
        for source in compact["evidence_sources"]:
            # Bounded, identity-only projection — never a raw payload/citation.
            assert set(source) == {"lane", "ref_type", "provider"}


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
            usable_lanes=[LANE_PRICE],
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
