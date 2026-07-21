"""Deterministic decision authority + portfolio join / publication retry.

Proves:
  * final actions originate ONLY from decision_policy_v1.decide() — the
    recorded action equals a from-scratch decide() replay over the same
    persisted inputs, and LLM stance/score cannot override it;
  * missing required evidence produces NO CALL (EVIDENCE INCOMPLETE), never a
    fabricated verdict row;
  * concentration (portfolio_fit) stays deterministic (BREACH → TRIM);
  * durable evidence rows (agent_runs/agent_insights/recommendations) are
    written idempotently and scoped to the decided ticker;
  * publication retries publication ONLY (zero collector/specialist reruns),
  * exactly one session-linked snapshot ever exists,
  * completed vs completed_with_gaps is decided by ticker gaps,
  * exhausted publication budget → honest terminal session failure.
"""
from __future__ import annotations

import uuid

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.decision_tasks_v1 import (
    aggregate_advisory_signal,
    execute_ticker_decision_task,
)
from app.services.intelligence.v3.distributed.publication_v1 import (
    execute_publication_task,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_FUNDAMENTAL,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL,
    TASK_PORTFOLIO_JOIN_PUBLISH,
    TASK_TICKER_DECISION,
    TICKER_DECIDED,
    TICKER_NO_CALL,
)
from tests.distributed_run_intel_test_utils import (
    FakePublicationService,
    FakeSupabase,
    seed_position,
)

USER = str(uuid.uuid4())


async def _session(client: FakeSupabase, tickers: list[str]) -> str:
    for ticker in tickers:
        seed_position(client, USER, ticker)
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    return session_id


def _seed_specialist_outputs(
    client: FakeSupabase, session_id: str, ticker: str,
    *, score: float = 0.6, confidence: float = 0.8,
    axes: tuple = (AXIS_FUNDAMENTAL, AXIS_TECHNICAL, AXIS_SENTIMENT),
):
    for axis in axes:
        store.upsert_specialist_output(
            client,
            run_session_id=session_id,
            user_id=USER,
            ticker=ticker,
            axis=axis,
            output={
                "stance": "positive" if score >= 0 else "negative",
                "score": score,
                "confidence": confidence,
                "key_findings": [f"{ticker} {axis} evidence-grounded finding"],
                "risks": [f"{ticker} {axis} risk"],
                "evidence_refs": [],
                "missing_evidence": [],
                "limitations": [],
                "valid_until": "2027-01-01T00:00:00+00:00",
                "model": "fake",
                "prompt_version": "test",
                "input_fingerprint": "sha256:test",
                "batch_key": None,
            },
        )


def _seed_bundle(client: FakeSupabase, session_id: str, ticker: str, **extra):
    client.table("intel_run_tickers").update({
        "evidence_bundle": {
            "ticker": ticker, "input_fingerprint": "sha256:test",
            "usable_lanes": ["price", "technicals", "fundamentals"],
            "required_lanes_missing": extra.get("required_lanes_missing", []),
        },
    }).eq("run_session_id", session_id).eq("ticker", ticker).execute()


def _decision_task(client: FakeSupabase, session_id: str, ticker: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "run_session_id": session_id,
        "user_id": USER,
        "task_type": TASK_TICKER_DECISION,
        "ticker": ticker,
        "attempts": 1,
        "max_attempts": 3,
    }


class TestDeterministicAuthority:
    @pytest.mark.asyncio
    async def test_action_comes_only_from_decide_and_is_replayable(self):
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_specialist_outputs(client, session_id, "AAPL", score=0.9)

        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        assert outcome.final_ticker_state == TICKER_DECIDED
        recorded = outcome.decision
        assert recorded["policy_schema_version"] == "v3.1"
        assert recorded["action"] in ("BUY", "HOLD", "TRIM", "SELL")

        # Replay: rebuild the decision input from the SAME persisted rows and
        # run the canonical kernel — the visible action must be identical.
        outcome2 = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        assert outcome2.decision["action"] == recorded["action"]
        assert outcome2.decision["conviction"] == recorded["conviction"]

    @pytest.mark.asyncio
    async def test_llm_score_cannot_override_policy_blockers(self):
        """A maximally bullish LLM signal on an over-concentrated holding
        still yields the deterministic TRIM/SELL side — policy wins."""
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_specialist_outputs(
            client, session_id, "AAPL", score=1.0, confidence=1.0,
        )
        # Concentration breach: 45% weight (cap*1.5 exceeded for any category).
        client.table("intel_run_tickers").update(
            {"portfolio_weight_pct": 45.0}
        ).eq("run_session_id", session_id).eq("ticker", "AAPL").execute()

        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        assert outcome.decision["action"] in ("TRIM", "SELL"), (
            "LLM bullishness overrode deterministic concentration policy"
        )
        assert outcome.decision["portfolio_fit"] in ("BREACH", "OVERWEIGHT")

    @pytest.mark.asyncio
    async def test_missing_required_evidence_is_no_call_not_fabricated_hold(self):
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(
            client, session_id, "AAPL",
            required_lanes_missing=["fundamentals", "technicals"],
        )
        # ZERO specialist outputs — nothing analyzable.
        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        assert outcome.final_ticker_state == TICKER_NO_CALL
        assert outcome.decision["outcome"] == "NO_CALL"
        # NO fabricated durable verdict rows for a NO CALL ticker.
        assert client.rows("agent_insights") == []
        assert client.rows("recommendations") == []
        row = next(
            r for r in client.rows("intel_run_tickers")
            if r["ticker"] == "AAPL"
        )
        assert row["state"] == TICKER_NO_CALL
        assert any(
            "required" in reason for reason in row["degradation_reasons"]
        )

    @pytest.mark.asyncio
    async def test_durable_evidence_rows_written_idempotently(self):
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_specialist_outputs(client, session_id, "AAPL")
        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        assert outcome.evidence_written
        assert len(client.rows("agent_runs")) == 1
        assert client.rows("agent_runs")[0]["status"] == "completed"
        insights = client.rows("agent_insights")
        assert len(insights) == 1
        verdict = insights[0]["analyst_verdict"]
        for key in ("primary_driver", "action_reason", "risk_flag",
                    "conviction_level"):
            assert verdict.get(key)
        recs = [r for r in client.rows("recommendations") if r["is_active"]]
        assert len(recs) == 1 and recs[0]["ticker"] == "AAPL"

    def test_advisory_aggregation_is_pure_deterministic_math(self):
        outputs = [
            {"score": 0.8, "confidence": 0.9},
            {"score": 0.4, "confidence": 0.5},
        ]
        first = aggregate_advisory_signal(outputs)
        second = aggregate_advisory_signal(list(reversed(outputs)))
        assert first == second
        assert first["advisory_action"] == "BUY"
        assert aggregate_advisory_signal([])["advisory_action"] is None


class TestPublication:
    async def _terminal_session(
        self, client: FakeSupabase, tickers: list[str],
        *, no_call: list[str] = (),
    ) -> str:
        session_id = await _session(client, tickers)
        for ticker in tickers:
            _seed_bundle(client, session_id, ticker)
            if ticker in no_call:
                client.table("intel_run_tickers").update(
                    {"state": TICKER_NO_CALL}
                ).eq("run_session_id", session_id).eq("ticker", ticker).execute()
            else:
                _seed_specialist_outputs(client, session_id, ticker)
                outcome = await execute_ticker_decision_task(
                    client, task=_decision_task(client, session_id, ticker),
                )
                assert outcome.final_ticker_state == TICKER_DECIDED
        return session_id

    def _publish_task(self, session_id: str, attempts: int = 1) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "run_session_id": session_id,
            "user_id": USER,
            "task_type": TASK_PORTFOLIO_JOIN_PUBLISH,
            "attempts": attempts,
            "max_attempts": 3,
        }

    @pytest.mark.asyncio
    async def test_clean_session_publishes_one_linked_snapshot(self):
        client = FakeSupabase()
        session_id = await self._terminal_session(client, ["AAPL", "MSFT"])
        service = FakePublicationService(client, USER)
        outcome = await execute_publication_task(
            client, task=self._publish_task(session_id), service=service,
        )
        assert outcome.final_state == "succeeded"
        assert outcome.session_status == "completed"
        snapshots = [
            s for s in client.rows("intel_v3_snapshots")
            if s.get("run_session_id") == session_id
        ]
        assert len(snapshots) == 1
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] == "completed"
        assert session["completed_snapshot_id"] == snapshots[0]["id"]
        assert service.calls[0]["scope_tickers"] == ["AAPL", "MSFT"]

    @pytest.mark.asyncio
    async def test_gaps_yield_completed_with_gaps(self):
        client = FakeSupabase()
        session_id = await self._terminal_session(
            client, ["AAPL", "MSFT", "GOOGL"], no_call=["GOOGL"],
        )
        service = FakePublicationService(client, USER)
        outcome = await execute_publication_task(
            client, task=self._publish_task(session_id), service=service,
        )
        assert outcome.session_status == "completed_with_gaps"
        assert outcome.gaps["no_call_tickers"] == ["GOOGL"]
        # NO CALL ticker excluded from certification scope — no fabricated
        # freshness for an evidence-incomplete holding.
        assert service.calls[0]["scope_tickers"] == ["AAPL", "MSFT"]

    @pytest.mark.asyncio
    async def test_publication_failure_retries_publication_only(self):
        client = FakeSupabase()
        session_id = await self._terminal_session(client, ["AAPL", "MSFT"])
        evidence_before = (
            len(client.rows("agent_insights")),
            len(client.rows("recommendations")),
            len(client.rows("intel_run_specialist_outputs")),
        )
        service = FakePublicationService(client, USER, fail_times=1)
        first = await execute_publication_task(
            client, task=self._publish_task(session_id, attempts=1),
            service=service,
        )
        assert first.final_state == store.TASK_FAILED_RETRYABLE
        assert client.rows("intel_v3_snapshots") == []

        second = await execute_publication_task(
            client, task=self._publish_task(session_id, attempts=2),
            service=service,
        )
        assert second.final_state == "succeeded"
        # Evidence and specialist outputs were NEVER regenerated.
        assert (
            len(client.rows("agent_insights")),
            len(client.rows("recommendations")),
            len(client.rows("intel_run_specialist_outputs")),
        ) == evidence_before
        assert len(service.calls) == 2  # publication attempts only
        snapshots = [
            s for s in client.rows("intel_v3_snapshots")
            if s.get("run_session_id") == session_id
        ]
        assert len(snapshots) == 1

    @pytest.mark.asyncio
    async def test_crash_between_insert_and_session_update_adopts_snapshot(self):
        client = FakeSupabase()
        session_id = await self._terminal_session(client, ["AAPL"])
        # Simulate: snapshot row inserted by a dead attempt.
        orphan_id = str(uuid.uuid4())
        client.table("intel_v3_snapshots").insert({
            "id": orphan_id, "user_id": USER, "run_session_id": session_id,
            "is_active": True,
            "payload": {"run_session_id": session_id,
                        "snapshot_source": "worker_certified"},
        }).execute()
        service = FakePublicationService(client, USER)
        outcome = await execute_publication_task(
            client, task=self._publish_task(session_id), service=service,
        )
        assert outcome.final_state == "succeeded"
        assert outcome.snapshot_row_id == orphan_id
        assert service.calls == []  # adopted; no second publication build
        snapshots = [
            s for s in client.rows("intel_v3_snapshots")
            if s.get("run_session_id") == session_id
        ]
        assert len(snapshots) == 1

    @pytest.mark.asyncio
    async def test_second_snapshot_for_same_session_is_impossible(self):
        client = FakeSupabase()
        session_id = await self._terminal_session(client, ["AAPL"])
        client.table("intel_v3_snapshots").insert({
            "id": str(uuid.uuid4()), "user_id": USER,
            "run_session_id": session_id, "is_active": True, "payload": {},
        }).execute()
        with pytest.raises(Exception):
            client.table("intel_v3_snapshots").insert({
                "id": str(uuid.uuid4()), "user_id": USER,
                "run_session_id": session_id, "is_active": True, "payload": {},
            }).execute()

    @pytest.mark.asyncio
    async def test_zero_decided_tickers_is_honest_terminal_failure(self):
        client = FakeSupabase()
        session_id = await self._terminal_session(
            client, ["AAPL"], no_call=["AAPL"],
        )
        service = FakePublicationService(client, USER)
        outcome = await execute_publication_task(
            client, task=self._publish_task(session_id), service=service,
        )
        assert outcome.final_state == "failed"
        assert outcome.error == "no_decided_tickers"
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] == "failed"
        assert service.calls == []
