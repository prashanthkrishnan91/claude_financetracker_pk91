"""Deterministic decision authority (single, final) + publication basics.

Proves:
  * ``decide()`` runs exactly once per decided ticker, inside the ticker
    decision task; the COMPLETE deterministic input and output are persisted
    on the session ticker row;
  * compatibility evidence rows (agent_runs/agent_insights/recommendations)
    are projections of the FINAL deterministic action — no BUY/HOLD/TRIM/SELL
    row exists before canonical policy determined it, and the recommendation
    action equals the persisted decision action exactly;
  * an LLM signal cannot override policy blockers (concentration BREACH);
  * missing required evidence produces NO CALL with zero fabricated rows;
  * publication marks completed vs completed_with_gaps from frozen-scope
    accounting and is retry-isolated (deeper real-integration publication
    proofs live in test_distributed_session_publication.py).
"""
from __future__ import annotations

import uuid

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.intelligence.v3.distributed import conflict_policy_v1
from app.services.intelligence.v3.distributed import decision_tasks_v1
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed import source_lineage_v1
from app.services.intelligence.v3.distributed.decision_tasks_v1 import (
    aggregate_advisory_signal,
    execute_ticker_decision_task,
)
from app.services.intelligence.v3.distributed.publication_v1 import (
    execute_publication_task,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_FUNDAMENTAL,
    AXIS_REVIEW,
    AXIS_RISK_FILING,
    AXIS_SENTIMENT,
    AXIS_TECHNICAL,
    TASK_PORTFOLIO_JOIN_PUBLISH,
    TASK_REVIEW_CONFLICT,
    TASK_SUCCEEDED,
    TASK_TICKER_DECISION,
    TICKER_DECIDED,
    TICKER_NO_CALL,
)
from tests.distributed_run_intel_test_utils import (
    FakeSupabase,
    make_claimed_task,
    make_settings,
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
    return make_claimed_task(
        client,
        run_session_id=session_id,
        user_id=USER,
        task_type=TASK_TICKER_DECISION,
        ticker=ticker,
    )


class TestDeterministicAuthority:
    @pytest.mark.asyncio
    async def test_decide_called_exactly_once_and_record_is_complete(
        self, monkeypatch
    ):
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_specialist_outputs(client, session_id, "AAPL", score=0.9)

        calls = {"n": 0}
        real_decide = decision_tasks_v1.decide

        def _counting_decide(inp):
            calls["n"] += 1
            return real_decide(inp)

        monkeypatch.setattr(decision_tasks_v1, "decide", _counting_decide)
        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        assert outcome.final_ticker_state == TICKER_DECIDED
        assert calls["n"] == 1, "decide() must run exactly once per ticker"

        record = outcome.decision
        # Complete OUTPUT persisted.
        for key in ("action", "conviction", "evidence_quality",
                    "attractiveness", "price_context", "portfolio_fit",
                    "risk_band", "blockers", "suppression_reasons",
                    "rationale_plain_english", "why_now", "why_not_now",
                    "policy_schema_version"):
            assert key in record, f"decision record missing output field {key}"
        # Complete INPUT persisted (replay/audit).
        decision_input = record["decision_input"]
        for key in ("ticker", "evidence_quality", "price_context",
                    "portfolio_fit", "risk_band", "raw_action",
                    "upstream_conviction", "asset_type_hint"):
            assert key in decision_input
        assert record["policy_schema_version"] == "v3.1"

        # The durable ticker row carries the exact same record.
        row = next(
            r for r in client.rows("intel_run_tickers")
            if r["ticker"] == "AAPL"
        )
        assert row["decision"]["action"] == record["action"]

    @pytest.mark.asyncio
    async def test_compat_rows_are_projections_of_final_action(self):
        """No recommendation exists before decide(); the written action IS
        the deterministic action (over-concentrated: bullish LLM → TRIM)."""
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_specialist_outputs(
            client, session_id, "AAPL", score=1.0, confidence=1.0,
        )
        client.table("intel_run_tickers").update(
            {"portfolio_weight_pct": 45.0}
        ).eq("run_session_id", session_id).eq("ticker", "AAPL").execute()

        assert client.rows("recommendations") == []
        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        final_action = outcome.decision["action"]
        assert final_action in ("TRIM", "SELL"), (
            "bullish advisory must not survive a concentration breach"
        )
        recs = [r for r in client.rows("recommendations") if r["is_active"]]
        assert len(recs) == 1
        # The compatibility row carries the FINAL deterministic action — not
        # the advisory BUY the specialists implied.
        assert recs[0]["action"] == final_action
        insight = client.rows("agent_insights")[0]
        assert insight["suggested_action"] == final_action

    @pytest.mark.asyncio
    async def test_llm_score_cannot_override_policy_blockers(self):
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_specialist_outputs(
            client, session_id, "AAPL", score=1.0, confidence=1.0,
        )
        client.table("intel_run_tickers").update(
            {"portfolio_weight_pct": 45.0}
        ).eq("run_session_id", session_id).eq("ticker", "AAPL").execute()

        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        assert outcome.decision["action"] in ("TRIM", "SELL")
        assert outcome.decision["portfolio_fit"] in ("BREACH", "OVERWEIGHT")

    @pytest.mark.asyncio
    async def test_missing_required_evidence_is_no_call_not_fabricated_hold(self):
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(
            client, session_id, "AAPL",
            required_lanes_missing=["fundamentals", "technicals"],
        )
        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        assert outcome.final_ticker_state == TICKER_NO_CALL
        assert outcome.decision["outcome"] == "NO_CALL"
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
        first = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        assert first.evidence_written
        # Retry (RE-claim of the same durable task): decide() is NOT re-run
        # for a decided ticker; the projections rewrite idempotently from the
        # persisted action.
        from tests.distributed_run_intel_test_utils import claim_task_row

        existing_task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_TICKER_DECISION and t["ticker"] == "AAPL"
        )
        second = await execute_ticker_decision_task(
            client, task=claim_task_row(client, existing_task),
        )
        assert second.decision["action"] == first.decision["action"]
        assert len(client.rows("agent_runs")) == 1
        assert len(client.rows("agent_insights")) == 1
        recs = [r for r in client.rows("recommendations") if r["is_active"]]
        assert len(recs) == 1
        verdict = client.rows("agent_insights")[0]["analyst_verdict"]
        for key in ("primary_driver", "action_reason", "risk_flag",
                    "conviction_level"):
            assert verdict.get(key)

    @pytest.mark.asyncio
    async def test_historical_llm_reviewed_decision_replays_from_persisted_audit_record(
        self, monkeypatch
    ):
        """A ticker already TICKER_DECIDED under the legacy LLM-review era
        must replay its PERSISTED action/verdict on retry — never
        recomputed under current deterministic-conflict rules — even when
        CURRENT specialist inputs would themselves trigger a fresh
        conflict (material spread + major position)."""
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_specialist_outputs(
            client, session_id, "AAPL", score=0.9, confidence=0.9,
            axes=(AXIS_FUNDAMENTAL,),
        )
        _seed_specialist_outputs(
            client, session_id, "AAPL", score=-0.9, confidence=0.9,
            axes=(AXIS_TECHNICAL,),
        )
        client.table("intel_run_tickers").update(
            {"portfolio_weight_pct": 45.0}
        ).eq("run_session_id", session_id).eq("ticker", "AAPL").execute()

        run_id = str(uuid.uuid4())
        existing_decision = {
            "action": "BUY",
            "agent_run_id": run_id,
            "advisory_signal": {
                "advisory_action": "BUY", "aggregate_score": 0.85,
                "mean_confidence": 0.82,
            },
            "decision_input": {
                "primary_driver": "Legacy review: durable fundamental strength confirmed",
                "action_reason": "Historical review reconciled the technical weakness",
                "risk_flag_text": "Historical filing risk noted by review",
                "upstream_conviction": "HIGH",
                "analyst_drivers": ["fundamental", "technical"],
            },
            "conviction": "HIGH",
            "policy_schema_version": "v3.1",
        }
        client.table("intel_run_tickers").update({
            "state": TICKER_DECIDED, "decision": existing_decision,
        }).eq("run_session_id", session_id).eq("ticker", "AAPL").execute()

        def _forbidden(*_a, **_k):
            raise AssertionError("must not recompute a historical decision")

        monkeypatch.setattr(decision_tasks_v1, "decide", _forbidden)
        monkeypatch.setattr(decision_tasks_v1, "resolve_conflict_advisory", _forbidden)

        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )

        assert outcome.final_ticker_state == TICKER_DECIDED
        assert outcome.decision == existing_decision
        assert outcome.decision["action"] == "BUY", (
            "a historical decision must never be newly neutralized"
        )
        recs = [r for r in client.rows("recommendations") if r["is_active"]]
        assert len(recs) == 1
        assert recs[0]["action"] == "BUY"
        insight = client.rows("agent_insights")[0]
        assert insight["suggested_action"] == "BUY"
        verdict = insight["analyst_verdict"]
        assert verdict["primary_driver"] == (
            existing_decision["decision_input"]["primary_driver"]
        )
        assert verdict["conviction_level"] == "HIGH"

    def test_advisory_aggregation_is_pure_deterministic_math(self):
        outputs = [
            {"axis": AXIS_FUNDAMENTAL, "score": 0.8, "confidence": 0.9},
            {"axis": AXIS_TECHNICAL, "score": 0.4, "confidence": 0.5},
        ]
        first = aggregate_advisory_signal(outputs)
        second = aggregate_advisory_signal(list(reversed(outputs)))
        assert first == second
        assert first["advisory_action"] == "BUY"
        assert aggregate_advisory_signal([])["advisory_action"] is None

    def test_advisory_aggregation_ignores_review_axis_even_if_passed_directly(self):
        """Strict authority — AXIS_REVIEW is excluded even when a caller
        mistakenly supplies the full output list (including a review row
        with an extreme score that would otherwise dominate the average)."""
        outputs = [
            {"axis": AXIS_FUNDAMENTAL, "score": 0.6, "confidence": 0.9},
            {"axis": AXIS_REVIEW, "score": -1.0, "confidence": 0.9},
        ]
        with_review = aggregate_advisory_signal(outputs)
        without_review = aggregate_advisory_signal(outputs[:1])
        assert with_review == without_review
        assert with_review["advisory_action"] == "BUY"

    @pytest.mark.asyncio
    async def test_unclaimed_task_cannot_decide(self):
        """The claim fence: a fabricated/stale task cannot produce a decision."""
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_specialist_outputs(client, session_id, "AAPL")
        fake_task = {
            "id": str(uuid.uuid4()),  # not a durable claimed task
            "run_session_id": session_id,
            "user_id": USER,
            "task_type": TASK_TICKER_DECISION,
            "ticker": "AAPL",
        }
        outcome = await execute_ticker_decision_task(client, task=fake_task)
        assert outcome.error == "claim_lost"
        row = next(
            r for r in client.rows("intel_run_tickers")
            if r["ticker"] == "AAPL"
        )
        assert row["state"] == "pending"
        assert client.rows("recommendations") == []


def _seed_conflict_outputs(
    client: FakeSupabase, session_id: str, ticker: str, *, weight_pct: float,
    axes: tuple = ((AXIS_FUNDAMENTAL, 0.8, 0.9), (AXIS_TECHNICAL, -0.8, 0.9)),
) -> list[dict]:
    """Materially conflicting non-review specialist rows (one per ``axes``
    ``(axis, score, confidence)`` tuple), PLUS a durable ``TASK_SUCCEEDED``
    conflict task and a CURRENT, valid deterministic ``axis=review`` row —
    built the exact same way ``execute_conflict_resolution_task`` would, so
    decision-time strict activation (task state + fingerprint + lineage)
    actually passes."""
    client.table("intel_run_tickers").update(
        {"portfolio_weight_pct": weight_pct}
    ).eq("run_session_id", session_id).eq("ticker", ticker).execute()

    rows = []
    for axis, score, confidence in axes:
        output = {
            "axis": axis,
            "stance": "positive" if score > 0 else "negative",
            "score": score, "confidence": confidence,
            "key_findings": [f"{ticker} {axis} finding"], "risks": [],
            "evidence_refs": [], "missing_evidence": [], "limitations": [],
            "valid_until": "2027-01-01T00:00:00+00:00",
            "model": "fake", "prompt_version": "test",
            "input_fingerprint": "sha256:test", "batch_key": None,
        }
        store.upsert_specialist_output(
            client, run_session_id=session_id, user_id=USER, ticker=ticker,
            axis=axis, output=output,
        )
        rows.append(output)

    store.create_task(
        client, run_session_id=session_id, user_id=USER,
        task_type=TASK_REVIEW_CONFLICT, ticker=ticker, state=TASK_SUCCEEDED,
    )
    normalized = conflict_policy_v1.normalize_valid_inputs(rows)
    assessment = conflict_policy_v1.assess_conflict(normalized, weight_pct)
    lineage = source_lineage_v1.build_review_lineage_manifest(normalized, ticker=ticker)
    prompt_context = source_lineage_v1.build_review_prompt_context(normalized, ticker=ticker)
    fingerprint = conflict_policy_v1.conflict_fingerprint(
        ticker=ticker, prompt_context=prompt_context, assessment=assessment,
        major=conflict_policy_v1.safe_major_position(weight_pct),
    )
    store.upsert_specialist_output(
        client, run_session_id=session_id, user_id=USER, ticker=ticker,
        axis=AXIS_REVIEW,
        output={
            "stance": "neutral", "score": 0.0,
            "confidence": conflict_policy_v1.CONFLICT_CONFIDENCE_CAP,
            "key_findings": [conflict_policy_v1.conflict_summary_sentence(assessment)],
            "risks": [
                "Conflicting specialist evidence increases the risk of "
                "acting prematurely.",
            ],
            "missing_evidence": [],
            "limitations": [
                "Directional signal neutralized until the evidence "
                "becomes more consistent.",
            ],
            "evidence_refs": lineage,
            "valid_until": "2027-01-01T00:00:00+00:00",
            "model": conflict_policy_v1.SCHEMA_VERSION,
            "prompt_version": conflict_policy_v1.SCHEMA_VERSION,
            "input_fingerprint": fingerprint,
            "batch_key": None,
        },
    )
    return rows


class TestConflictIntegration:
    """Acceptance matrix rows 2/3/4/5 — deterministic conflict handling."""

    @pytest.mark.asyncio
    async def test_conflict_neutralizes_to_hold_low_conviction(self):
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        # Normal (non-overweight) portfolio weight — a single-position fake
        # session otherwise defaults to 100% (breach).
        _seed_conflict_outputs(client, session_id, "AAPL", weight_pct=5.0)

        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        assert outcome.final_ticker_state == TICKER_DECIDED
        record = outcome.decision
        assert record["action"] == "HOLD"
        assert record["conviction"] == "LOW"
        assert record["advisory_signal"]["advisory_action"] == "HOLD"
        assert record["advisory_signal"]["mean_confidence"] <= 0.49
        assert record["advisory_signal"]["conflict_detected"] is True
        assert "pre_conflict_advisory_signal" in record
        assert record["pre_conflict_advisory_signal"]["advisory_action"] in (
            "BUY", "HOLD", "REDUCE",
        )
        assert "analysis_conflict" in record["decision_input"]["suppression_reasons"]
        assert "disagreed across" in record["decision_input"]["primary_driver"]
        assert "Fundamental analysis" in record["decision_input"]["primary_driver"]
        assert "fundamental" not in record["decision_input"]["primary_driver"]

    @pytest.mark.asyncio
    async def test_conflict_with_overweight_still_trims(self):
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_conflict_outputs(client, session_id, "AAPL", weight_pct=45.0)

        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        record = outcome.decision
        # Existing portfolio-fit policy remains authoritative — conflict
        # neutralization never weakens a real overweight/breach TRIM.
        assert record["action"] in ("TRIM", "SELL")
        assert record["portfolio_fit"] in ("OVERWEIGHT", "BREACH")
        assert record["advisory_signal"]["conflict_detected"] is True
        assert "analysis_conflict" in record["decision_input"]["suppression_reasons"]

    @pytest.mark.asyncio
    async def test_no_conflict_aggregate_and_action_unchanged(self):
        """Acceptance row 1 — aligned specialists, no conflict row: ordinary
        aggregation/decision are byte-for-byte unaffected."""
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_specialist_outputs(client, session_id, "AAPL", score=0.6)
        client.table("intel_run_tickers").update(
            {"portfolio_weight_pct": 5.0}
        ).eq("run_session_id", session_id).eq("ticker", "AAPL").execute()

        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        record = outcome.decision
        assert record["advisory_signal"].get("conflict_detected") is None
        assert record["advisory_signal"] == record["pre_conflict_advisory_signal"]
        assert record["action"] in ("BUY", "HOLD")

    @pytest.mark.asyncio
    async def test_conflict_with_critical_risk_and_breach_still_sells(self):
        """Portfolio-fit/risk priority remains authoritative over a HOLD
        overlay: CRITICAL risk + BREACH concentration forces SELL even
        though the conflict guardrail neutralized the advisory signal."""
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_conflict_outputs(
            client, session_id, "AAPL", weight_pct=60.0,
            axes=(
                (AXIS_FUNDAMENTAL, 0.8, 0.9), (AXIS_TECHNICAL, -0.8, 0.9),
                (AXIS_RISK_FILING, -0.8, 0.9),
            ),
        )
        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        record = outcome.decision
        assert record["portfolio_fit"] == "BREACH"
        assert record["risk_band"] == "CRITICAL"
        assert record["action"] == "SELL"
        assert record["advisory_signal"]["conflict_detected"] is True
        assert record["advisory_signal"]["advisory_action"] == "HOLD"

    @pytest.mark.asyncio
    async def test_low_confidence_major_holding_without_disagreement_holds_low(self):
        """Low confidence alone (no directional disagreement) on a major
        holding still neutralizes to HOLD/LOW, but the wording must say
        confidence was low — never that specialists disagreed."""
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_conflict_outputs(
            client, session_id, "AAPL", weight_pct=8.0,
            axes=((AXIS_FUNDAMENTAL, 0.2, 0.2), (AXIS_TECHNICAL, 0.3, 0.9)),
        )
        outcome = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        record = outcome.decision
        assert record["action"] == "HOLD"
        assert record["conviction"] == "LOW"
        assert record["advisory_signal"]["conflict_detected"] is True
        primary_driver = record["decision_input"]["primary_driver"]
        assert "disagreed" not in primary_driver
        assert "confidence was low" in primary_driver
        assert "Fundamental analysis" in primary_driver

    @pytest.mark.asyncio
    async def test_conflict_retry_is_idempotent(self):
        client = FakeSupabase()
        session_id = await _session(client, ["AAPL"])
        _seed_bundle(client, session_id, "AAPL")
        _seed_conflict_outputs(client, session_id, "AAPL", weight_pct=5.0)

        first = await execute_ticker_decision_task(
            client, task=_decision_task(client, session_id, "AAPL"),
        )
        existing_task = next(
            t for t in client.rows("intel_run_tasks")
            if t["task_type"] == TASK_TICKER_DECISION and t["ticker"] == "AAPL"
        )
        from tests.distributed_run_intel_test_utils import claim_task_row

        second = await execute_ticker_decision_task(
            client, task=claim_task_row(client, existing_task),
        )
        assert second.decision["action"] == first.decision["action"]
        assert (
            second.decision["advisory_signal"]
            == first.decision["advisory_signal"]
        )


class TestPublicationStatusSemantics:
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

    def _publish_task(self, client: FakeSupabase, session_id: str) -> dict:
        return make_claimed_task(
            client,
            run_session_id=session_id,
            user_id=USER,
            task_type=TASK_PORTFOLIO_JOIN_PUBLISH,
        )

    @pytest.mark.asyncio
    async def test_clean_session_publishes_completed(self):
        client = FakeSupabase()
        session_id = await self._terminal_session(client, ["AAPL", "MSFT"])
        outcome = await execute_publication_task(
            client,
            task=self._publish_task(client, session_id),
            settings=make_settings(),
        )
        assert outcome.final_state == "succeeded"
        assert outcome.session_status == "completed"
        snapshots = [
            s for s in client.rows("intel_v3_snapshots")
            if s.get("run_session_id") == session_id
        ]
        assert len(snapshots) == 1
        assert snapshots[0]["payload"]["snapshot_source"] == "worker_certified"
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] == "completed"
        assert session["completed_snapshot_id"] == snapshots[0]["id"]

    @pytest.mark.asyncio
    async def test_gaps_yield_completed_with_gaps_and_non_green_source(self):
        client = FakeSupabase()
        session_id = await self._terminal_session(
            client, ["AAPL", "MSFT", "GOOGL"], no_call=["GOOGL"],
        )
        outcome = await execute_publication_task(
            client,
            task=self._publish_task(client, session_id),
            settings=make_settings(),
        )
        assert outcome.session_status == "completed_with_gaps"
        snapshot = next(
            s for s in client.rows("intel_v3_snapshots")
            if s.get("run_session_id") == session_id
        )
        payload = snapshot["payload"]
        # Visibly non-green, distinguishable source.
        assert payload["snapshot_source"] == "worker_certified_with_gaps"
        assert payload["certified_holding_count"] == 2
        assert payload["total_holding_count"] == 3
        assert payload["session_coverage"]["no_call_tickers"] == ["GOOGL"]

    @pytest.mark.asyncio
    async def test_publication_failure_retries_publication_only(self):
        client = FakeSupabase()
        session_id = await self._terminal_session(client, ["AAPL", "MSFT"])
        evidence_before = (
            len(client.rows("agent_insights")),
            len(client.rows("recommendations")),
            len(client.rows("intel_run_specialist_outputs")),
        )

        # Narrow error injection at the persistence seam only.
        def _failing_persist(*args, **kwargs):
            raise RuntimeError("simulated persistence outage")

        first = await execute_publication_task(
            client,
            task=self._publish_task(client, session_id),
            settings=make_settings(),
            persist=_failing_persist,
        )
        assert first.final_state == store.TASK_FAILED_RETRYABLE
        assert client.rows("intel_v3_snapshots") == []

        second = await execute_publication_task(
            client,
            task=make_claimed_task(
                client, run_session_id=session_id, user_id=USER,
                task_type=TASK_PORTFOLIO_JOIN_PUBLISH, batch_key="retry",
            ),
            settings=make_settings(),
        )
        assert second.final_state == "succeeded"
        assert (
            len(client.rows("agent_insights")),
            len(client.rows("recommendations")),
            len(client.rows("intel_run_specialist_outputs")),
        ) == evidence_before
        snapshots = [
            s for s in client.rows("intel_v3_snapshots")
            if s.get("run_session_id") == session_id
        ]
        assert len(snapshots) == 1

    @pytest.mark.asyncio
    async def test_crash_between_insert_and_session_update_adopts_snapshot(self):
        client = FakeSupabase()
        session_id = await self._terminal_session(client, ["AAPL"])
        orphan_id = str(uuid.uuid4())
        client.table("intel_v3_snapshots").insert({
            "id": orphan_id, "user_id": USER, "run_session_id": session_id,
            "is_active": True,
            "payload": {"run_session_id": session_id,
                        "snapshot_source": "worker_certified"},
        }).execute()

        def _forbidden_build(*args, **kwargs):
            raise AssertionError("adoption path must not rebuild the payload")

        outcome = await execute_publication_task(
            client,
            task=self._publish_task(client, session_id),
            settings=make_settings(),
            build_payload=_forbidden_build,
        )
        assert outcome.final_state == "succeeded"
        assert outcome.snapshot_row_id == orphan_id
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
        outcome = await execute_publication_task(
            client,
            task=self._publish_task(client, session_id),
            settings=make_settings(),
        )
        assert outcome.final_state == "failed"
        assert outcome.error == "no_decided_tickers"
        session = client.rows("intel_run_sessions")[0]
        assert session["status"] == "failed"
        assert client.rows("intel_v3_snapshots") == []
