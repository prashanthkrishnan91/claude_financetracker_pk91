"""Session-native publication — REAL builder + certification + persistence.

The ten required semantic acceptance proofs (no fake publication service —
the real ``session_publication_v1`` builder, the real distributed
certification contract and the real persistence seam run end to end over the
in-memory Supabase fake):

  1. snapshot action equals ``intel_run_tickers.decision.action`` for every
     decided ticker;
  2. canonical ``decide()`` is called once per decided ticker and NEVER
     during publication;
  3. a prior active BUY recommendation for a new-session NO CALL ticker does
     not appear in the new snapshot;
  4. NO CALL is represented only as an explicit coverage gap;
  5. certification covers the complete frozen scope;
  6. a clean session publishes ``completed``;
  7. a partial session publishes ``completed_with_gaps``;
  8. the distributed snapshot never reads decision authority from global
     recommendation state;
  9. exactly one session-linked snapshot is inserted;
 10. publication retry performs zero collector, zero specialist and zero
     policy calls.
"""
from __future__ import annotations

import uuid

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.intelligence.v3.distributed import decision_tasks_v1
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed import session_publication_v1 as pub
from app.services.intelligence.v3.distributed.decision_tasks_v1 import (
    execute_ticker_decision_task,
)
from app.services.intelligence.v3.distributed.publication_v1 import (
    execute_publication_task,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_FUNDAMENTAL,
    AXIS_TECHNICAL,
    TASK_PORTFOLIO_JOIN_PUBLISH,
    TASK_TICKER_DECISION,
    TICKER_DECIDED,
    TICKER_FAILED,
    TICKER_NO_CALL,
)
from tests.distributed_run_intel_test_utils import (
    FakeSupabase,
    make_claimed_task,
    make_settings,
    seed_position,
)

USER = str(uuid.uuid4())


def _seed_outputs(client, session_id, ticker, *, score=0.6):
    for axis in (AXIS_FUNDAMENTAL, AXIS_TECHNICAL):
        store.upsert_specialist_output(
            client,
            run_session_id=session_id,
            user_id=USER,
            ticker=ticker,
            axis=axis,
            output={
                "stance": "positive" if score >= 0 else "negative",
                "score": score, "confidence": 0.8,
                "key_findings": [f"{ticker} {axis} finding"],
                "risks": [], "evidence_refs": [], "missing_evidence": [],
                "limitations": [],
                "valid_until": "2027-01-01T00:00:00+00:00",
                "model": "fake", "prompt_version": "test",
                "input_fingerprint": "sha256:test", "batch_key": None,
            },
        )


async def _decided_session(
    client: FakeSupabase,
    tickers: list[str],
    *,
    no_call: tuple = (),
    failed: tuple = (),
    scores: dict | None = None,
) -> str:
    scores = scores or {}
    for ticker in tickers:
        seed_position(client, USER, ticker)
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    for ticker in tickers:
        client.table("intel_run_tickers").update({
            "evidence_bundle": {
                "ticker": ticker, "input_fingerprint": "sha256:test",
                "usable_lanes": ["price", "technicals", "fundamentals"],
                "required_lanes_missing": [],
            },
        }).eq("run_session_id", session_id).eq("ticker", ticker).execute()
        if ticker in no_call:
            client.table("intel_run_tickers").update({
                "state": TICKER_NO_CALL,
                "decision": {"outcome": "NO_CALL",
                             "reason": "evidence_incomplete"},
            }).eq("run_session_id", session_id).eq("ticker", ticker).execute()
            continue
        if ticker in failed:
            client.table("intel_run_tickers").update({
                "state": TICKER_FAILED,
            }).eq("run_session_id", session_id).eq("ticker", ticker).execute()
            continue
        _seed_outputs(client, session_id, ticker, score=scores.get(ticker, 0.6))
        outcome = await execute_ticker_decision_task(
            client,
            task=make_claimed_task(
                client, run_session_id=session_id, user_id=USER,
                task_type=TASK_TICKER_DECISION, ticker=ticker,
            ),
        )
        assert outcome.final_ticker_state == TICKER_DECIDED
    return session_id


def _publish_task(client, session_id, *, batch_key=None):
    return make_claimed_task(
        client, run_session_id=session_id, user_id=USER,
        task_type=TASK_PORTFOLIO_JOIN_PUBLISH, batch_key=batch_key,
    )


async def _publish(client, session_id, **kwargs):
    return await execute_publication_task(
        client,
        task=_publish_task(client, session_id, batch_key=kwargs.pop("batch_key", None)),
        settings=make_settings(),
        **kwargs,
    )


def _session_snapshot(client, session_id):
    rows = [
        s for s in client.rows("intel_v3_snapshots")
        if s.get("run_session_id") == session_id
    ]
    assert len(rows) == 1, f"expected exactly one session snapshot, got {len(rows)}"
    return rows[0]


class TestRealPublicationContract:
    @pytest.mark.asyncio
    async def test_1_snapshot_action_equals_persisted_decision_action(self):
        client = FakeSupabase()
        session_id = await _decided_session(
            client, ["AAPL", "MSFT", "GOOGL"],
            scores={"AAPL": 0.9, "MSFT": 0.0, "GOOGL": -0.9},
        )
        outcome = await _publish(client, session_id)
        assert outcome.final_state == "succeeded"
        payload = _session_snapshot(client, session_id)["payload"]
        persisted = {
            r["ticker"]: r["decision"]["action"]
            for r in client.rows("intel_run_tickers")
        }
        cards = {c["ticker"]: c for c in payload["current_holdings"]}
        assert set(cards) == set(persisted)
        for ticker, card in cards.items():
            assert card["action"] == persisted[ticker], (
                f"{ticker}: visible {card['action']} != persisted "
                f"{persisted[ticker]}"
            )
            assert card["source_run_id"] == session_id

    @pytest.mark.asyncio
    async def test_2_decide_once_per_ticker_never_during_publication(
        self, monkeypatch
    ):
        client = FakeSupabase()
        calls = {"n": 0}
        real_decide = decision_tasks_v1.decide

        def _counting(inp):
            calls["n"] += 1
            return real_decide(inp)

        monkeypatch.setattr(decision_tasks_v1, "decide", _counting)
        session_id = await _decided_session(client, ["AAPL", "MSFT"])
        assert calls["n"] == 2  # once per decided ticker

        # Publication: decide() is FORBIDDEN — patch every route to it.
        import app.services.intelligence.v3.decision_policy_v1 as policy

        def _forbidden(*args, **kwargs):
            raise AssertionError("decide() must never run during publication")

        monkeypatch.setattr(decision_tasks_v1, "decide", _forbidden)
        monkeypatch.setattr(policy, "decide", _forbidden)
        outcome = await _publish(client, session_id)
        assert outcome.final_state == "succeeded"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_3_prior_buy_recommendation_never_surfaces_for_no_call(self):
        client = FakeSupabase()
        # An older session/run left an ACTIVE BUY recommendation for GOOGL.
        client.store.setdefault("recommendations", []).append({
            "id": str(uuid.uuid4()), "user_id": USER, "ticker": "GOOGL",
            "action": "BUY", "suggested_action": "BUY", "is_active": True,
            "agent_run_id": str(uuid.uuid4()),
            "created_at": "2026-07-20T00:00:00+00:00",
        })
        session_id = await _decided_session(
            client, ["AAPL", "GOOGL"], no_call=("GOOGL",),
        )
        outcome = await _publish(client, session_id)
        assert outcome.session_status == "completed_with_gaps"
        payload = _session_snapshot(client, session_id)["payload"]
        card_tickers = {c["ticker"] for c in payload["current_holdings"]}
        assert "GOOGL" not in card_tickers, (
            "a NO CALL ticker surfaced an action card (stale recommendation)"
        )
        assert "BUY" not in {
            c["action"] for c in payload["current_holdings"]
            if c["ticker"] == "GOOGL"
        }

    @pytest.mark.asyncio
    async def test_4_no_call_is_explicit_coverage_gap_only(self):
        client = FakeSupabase()
        session_id = await _decided_session(
            client, ["AAPL", "GOOGL", "VHT"],
            no_call=("GOOGL",), failed=("VHT",),
        )
        await _publish(client, session_id)
        payload = _session_snapshot(client, session_id)["payload"]
        coverage = payload["session_coverage"]
        gaps = {g["ticker"]: g for g in coverage["gaps"]}
        assert set(gaps) == {"GOOGL", "VHT"}
        assert gaps["GOOGL"]["state"] == TICKER_NO_CALL
        assert gaps["VHT"]["state"] == TICKER_FAILED
        for gap in gaps.values():
            assert gap["reason"]  # plain-English reason present
        # Gap tickers appear nowhere in cards / desks.
        for section in ("current_holdings", "best_buys", "trim_sell_desk"):
            for entry in payload.get(section) or []:
                assert entry.get("ticker") not in ("GOOGL", "VHT")

    @pytest.mark.asyncio
    async def test_5_certification_covers_complete_frozen_scope(self):
        client = FakeSupabase()
        session_id = await _decided_session(
            client, ["AAPL", "MSFT", "GOOGL"], no_call=("GOOGL",),
        )
        await _publish(client, session_id)
        payload = _session_snapshot(client, session_id)["payload"]
        coverage = payload["session_coverage"]
        assert coverage["frozen_holding_count"] == 3
        assert payload["total_holding_count"] == 3  # never shrunk to hide gaps
        accounted = (
            set(coverage["decided_tickers"])
            | set(coverage["no_call_tickers"])
            | set(coverage["failed_tickers"])
        )
        assert accounted == {"AAPL", "MSFT", "GOOGL"}
        # The certification function itself proves scope accounting.
        session = client.rows("intel_run_sessions")[0]
        rows = client.rows("intel_run_tickers")
        result = pub.certify_session_snapshot(
            payload=payload, session=session, ticker_rows=rows,
        )
        assert result.certified, result.errors

    @pytest.mark.asyncio
    async def test_6_clean_session_publishes_completed(self):
        client = FakeSupabase()
        session_id = await _decided_session(client, ["AAPL", "MSFT"])
        outcome = await _publish(client, session_id)
        assert outcome.session_status == "completed"
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] == "completed"
        payload = _session_snapshot(client, session_id)["payload"]
        assert payload["snapshot_source"] == "worker_certified"
        assert payload["session_status"] == "completed"

    @pytest.mark.asyncio
    async def test_7_partial_session_publishes_completed_with_gaps(self):
        client = FakeSupabase()
        session_id = await _decided_session(
            client, ["AAPL", "MSFT", "GOOGL"], failed=("GOOGL",),
        )
        outcome = await _publish(client, session_id)
        assert outcome.session_status == "completed_with_gaps"
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] == "completed_with_gaps"
        payload = _session_snapshot(client, session_id)["payload"]
        assert payload["snapshot_source"] == "worker_certified_with_gaps"
        assert payload["session_status"] == "completed_with_gaps"

    @pytest.mark.asyncio
    async def test_8_publication_never_reads_global_recommendation_state(
        self, monkeypatch
    ):
        """The builder gets NO chance to read recommendations/agent_insights:
        every read of those tables during publication fails the test."""
        client = FakeSupabase()
        session_id = await _decided_session(client, ["AAPL", "MSFT"])

        real_table = client.table

        def _guarded_table(name):
            if name in ("recommendations", "agent_insights", "agent_runs"):
                raise AssertionError(
                    f"publication read global {name} state — decision "
                    "authority must come from session rows only"
                )
            return real_table(name)

        monkeypatch.setattr(client, "table", _guarded_table)
        outcome = await _publish(client, session_id)
        assert outcome.final_state == "succeeded"
        monkeypatch.undo()
        payload = _session_snapshot(client, session_id)["payload"]
        assert len(payload["current_holdings"]) == 2

    @pytest.mark.asyncio
    async def test_9_exactly_one_session_linked_snapshot(self):
        client = FakeSupabase()
        session_id = await _decided_session(client, ["AAPL"])
        first = await _publish(client, session_id)
        assert first.final_state == "succeeded"
        # A duplicate/late publish task adopts — never inserts a second row.
        second = await _publish(client, session_id, batch_key="late-retry")
        assert second.final_state == "succeeded"
        assert second.snapshot_row_id == first.snapshot_row_id
        _session_snapshot(client, session_id)  # asserts exactly one

    @pytest.mark.asyncio
    async def test_10_publication_retry_zero_collectors_specialists_policy(
        self, monkeypatch
    ):
        client = FakeSupabase()
        session_id = await _decided_session(client, ["AAPL", "MSFT"])

        # Forbid every non-publication work path during publication retry.
        import app.services.intelligence.v3.distributed.collectors_v1 as col
        import app.services.intelligence.v3.distributed.specialist_agents_v1 as spec
        import app.services.intelligence.v3.decision_policy_v1 as policy

        def _forbidden(*args, **kwargs):
            raise AssertionError("forbidden work during publication retry")

        monkeypatch.setattr(col, "execute_collector_task", _forbidden)
        monkeypatch.setattr(spec, "execute_specialist_task", _forbidden)
        monkeypatch.setattr(decision_tasks_v1, "decide", _forbidden)
        monkeypatch.setattr(policy, "decide", _forbidden)

        def _failing_persist(*args, **kwargs):
            raise RuntimeError("simulated outage")

        first = await _publish(client, session_id, persist=_failing_persist)
        assert first.final_state == store.TASK_FAILED_RETRYABLE
        specialist_rows_before = len(client.rows("intel_run_specialist_outputs"))

        second = await _publish(client, session_id, batch_key="retry")
        assert second.final_state == "succeeded"
        assert len(
            client.rows("intel_run_specialist_outputs")
        ) == specialist_rows_before
        _session_snapshot(client, session_id)
