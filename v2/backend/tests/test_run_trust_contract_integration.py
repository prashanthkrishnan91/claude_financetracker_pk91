"""Integration proofs for run_trust_contract_v1 wired into the real pipeline:

  9. an existing (pre-PR1-shaped) snapshot can be enriched from its
     run_session_id alone — zero provider/LLM calls, using only durable rows;
  10. future publication persists the SAME pure trust projection that
     read-time enrichment independently recomputes over freshly-fetched rows.

Uses the real session-native builder, the real read-time enrichment function
and the in-memory Supabase fake — no mocked trust-contract internals.
"""
from __future__ import annotations

import uuid

import pytest

import app.services.intelligence.v3.distributed.run_task_store_v1 as store
from app.services.intelligence.v3.distributed import run_trust_contract_v1 as trust
from app.services.intelligence.v3.distributed import session_control_v1 as control
from app.services.intelligence.v3.distributed.decision_tasks_v1 import (
    execute_ticker_decision_task,
)
from app.services.intelligence.v3.distributed.publication_v1 import (
    execute_publication_task,
)
from app.services.intelligence.v3.distributed.task_contracts_v1 import (
    AXIS_FUNDAMENTAL,
    AXIS_TECHNICAL,
    TASK_FAILED,
    TASK_PORTFOLIO_JOIN_PUBLISH,
    TASK_REVIEW_CONFLICT,
    TASK_SUCCEEDED,
    TASK_TICKER_DECISION,
    TICKER_DECIDED,
)
from app.services.intelligence.v3.intel_v3_service import (
    _enrich_snapshot_with_run_trust_contract,
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


async def _session_with_review(client: FakeSupabase) -> str:
    """3 tickers: AAPL/MSFT decided normally, GOOGL has a FAILED required
    conflict-review task (production-shaped: a real conflict review ran and
    failed, not merely absent)."""
    tickers = ["AAPL", "MSFT", "GOOGL"]
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
                "required_lanes_missing": [], "source_refs": [],
            },
        }).eq("run_session_id", session_id).eq("ticker", ticker).execute()
        _seed_outputs(client, session_id, ticker)
        outcome = await execute_ticker_decision_task(
            client,
            task=make_claimed_task(
                client, run_session_id=session_id, user_id=USER,
                task_type=TASK_TICKER_DECISION, ticker=ticker,
            ),
        )
        assert outcome.final_ticker_state == TICKER_DECIDED

    # A real conflict review ran for GOOGL and failed.
    client.table("intel_run_tasks").insert({
        "id": str(uuid.uuid4()), "run_session_id": session_id, "user_id": USER,
        "task_type": TASK_REVIEW_CONFLICT, "ticker": "GOOGL",
        "state": TASK_FAILED, "attempts": 3, "max_attempts": 3,
        "priority": 50,
    }).execute()

    return session_id


async def _publish(client, session_id):
    task = make_claimed_task(
        client, run_session_id=session_id, user_id=USER,
        task_type=TASK_PORTFOLIO_JOIN_PUBLISH,
    )
    return await execute_publication_task(
        client, task=task, settings=make_settings(),
    )


def _snapshot_row(client, session_id):
    rows = [
        s for s in client.rows("intel_v3_snapshots")
        if s.get("run_session_id") == session_id
    ]
    assert len(rows) == 1
    return rows[0]


@pytest.mark.asyncio
async def test_publication_fails_retryable_instead_of_publishing_falsely_optimistic_trust():
    """Every real session seeds a task graph before any ticker can freeze —
    frozen tickers with a completely empty task list means the tasks read
    silently failed (the store degrades read errors to `[]`, never raises).
    Publishing on that would report every conflict review as not-required
    and every axis as merely missing instead of failed, falsely improving
    `overall_status`. Publication must fail retryable instead."""
    from app.services.intelligence.v3.distributed.run_task_store_v1 import (
        TASK_FAILED_RETRYABLE,
    )

    client = FakeSupabase()
    session_id = await _session_with_review(client)
    publish_task = make_claimed_task(
        client, run_session_id=session_id, user_id=USER,
        task_type=TASK_PORTFOLIO_JOIN_PUBLISH,
    )
    # Simulate the tasks read silently failing/being empty by deleting every
    # OTHER task row for this session (keep only the publish task itself,
    # which the executor's own claim fence needs) — an otherwise-healthy
    # session whose tasks read comes back suspiciously empty.
    client.store["intel_run_tasks"] = [
        t for t in client.store.get("intel_run_tasks", [])
        if t.get("run_session_id") != session_id or t.get("id") == publish_task["id"]
    ]
    outcome = await execute_publication_task(
        client, task=publish_task, settings=make_settings(),
    )
    assert outcome.final_state == TASK_FAILED_RETRYABLE
    assert outcome.error == "session_tasks_read_empty_or_failed"
    # No snapshot was persisted from the falsely-optimistic read.
    snapshots = [
        s for s in client.rows("intel_v3_snapshots")
        if s.get("run_session_id") == session_id
    ]
    assert snapshots == []


@pytest.mark.asyncio
async def test_10_future_publication_persists_same_projection_as_fresh_recompute():
    client = FakeSupabase()
    session_id = await _session_with_review(client)
    outcome = await _publish(client, session_id)
    assert outcome.final_state == "succeeded"

    payload = _snapshot_row(client, session_id)["payload"]
    persisted_contract = payload["run_trust_contract"]
    assert persisted_contract["run_session_id"] == session_id
    assert persisted_contract["conflict_review_coverage"]["failed_count"] == 1
    assert "GOOGL" in persisted_contract["conflict_review_coverage"]["failed_tickers"]

    # Independently recompute over freshly-fetched durable rows.
    from app.services.intelligence.v3.intel_run_session_store_v1 import get_session

    session = get_session(client, session_id)
    ticker_rows = store.list_ticker_rows(client, run_session_id=session_id)
    tasks = store.list_tasks(client, run_session_id=session_id)
    specialist_outputs = store.list_specialist_outputs(
        client, run_session_id=session_id
    )
    recomputed = trust.build_run_trust_contract(
        session=session, ticker_rows=ticker_rows, tasks=tasks,
        specialist_outputs=specialist_outputs,
        now=None,
    )

    # generated_at is a fresh timestamp each call — compare everything else.
    persisted_stable = {k: v for k, v in persisted_contract.items() if k != "generated_at"}
    recomputed_stable = {k: v for k, v in recomputed.items() if k != "generated_at"}
    assert persisted_stable == recomputed_stable


@pytest.mark.asyncio
async def test_9_existing_snapshot_enriched_from_run_session_id_zero_provider_llm():
    client = FakeSupabase()
    session_id = await _session_with_review(client)
    outcome = await _publish(client, session_id)
    assert outcome.final_state == "succeeded"

    # Simulate a snapshot payload PERSISTED BEFORE run_trust_contract_v1
    # existed: strip the new field and reset the two cards' committee status
    # to the old evidence-band-only computation (what the legacy builder
    # would have produced for STRONG/OK evidence, ignoring lineage/review).
    payload = dict(_snapshot_row(client, session_id)["payload"])
    payload.pop("run_trust_contract", None)
    old_shaped_holdings = []
    for card in payload["current_holdings"]:
        card = dict(card)
        ddp = dict(card["detail_drawer_payload"])
        ddp.pop("source_lineage", None)
        ddp.pop("conflict_review_status", None)
        ddp.pop("decision_constraints", None)
        ddp["committee"] = {"status": "source_validated"}  # the old, wrong label
        card["detail_drawer_payload"] = ddp
        old_shaped_holdings.append(card)
    payload["current_holdings"] = old_shaped_holdings
    payload["source_health"] = {"status": "not_assessed"}

    assert "run_trust_contract" not in payload

    _enrich_snapshot_with_run_trust_contract(payload, client=client, user_id=USER)

    assert payload["run_trust_contract"]["run_session_id"] == session_id
    assert payload["source_health"]["status"] == trust.STATUS_BLOCKED  # zero lineage
    cards = {c["ticker"]: c for c in payload["current_holdings"]}

    # GOOGL: failed required review — must no longer read source_validated.
    googl_ddp = cards["GOOGL"]["detail_drawer_payload"]
    assert googl_ddp["committee"]["status"] != "source_validated"
    assert googl_ddp["conflict_review_status"] == "failed"
    assert "conflict_review" in googl_ddp["decision_constraints"]

    # AAPL/MSFT: no review required, but STILL zero source lineage — must not
    # be mislabeled source_validated either.
    for ticker in ("AAPL", "MSFT"):
        ddp = cards[ticker]["detail_drawer_payload"]
        assert ddp["committee"]["status"] != "source_validated"
        assert ddp["source_lineage"]["status"] == trust.LINEAGE_MISSING
        assert ddp["source_lineage"]["has_source_refs"] is False

    # Zero provider/LLM calls: FakeSupabase exposes only .table()/.rows() —
    # no provider or LLM client methods exist on it at all, so re-running
    # enrichment against the SAME fake (durable rows only, no network seam)
    # is itself the proof. Re-run to confirm it's deterministic/idempotent.
    payload2 = dict(payload)
    payload2.pop("run_trust_contract", None)
    _enrich_snapshot_with_run_trust_contract(payload2, client=client, user_id=USER)
    assert payload2["run_trust_contract"]["run_session_id"] == session_id


# ── Fail-closed historical enrichment (release-blocker patch, point 5) ───────
#
# The read-time overlay must NEVER preserve an old snapshot's optimistic
# source_validated/committee status when durable trust state cannot be read
# — it must attach an explicit "unknown" overlay instead, on both the
# contract and every existing card, with zero provider/LLM calls in every
# case (FakeSupabase has no such methods at all, so any read past .table()/
# .rows() would itself raise — the tests below never see that happen).

def _old_shaped_optimistic_payload(session_id: str, tickers: list[str]) -> dict:
    """A payload shaped like a pre-run_trust_contract_v1 snapshot: every
    card wrongly reads committee.status == source_validated, no trust
    fields at all."""
    cards = []
    for ticker in tickers:
        cards.append({
            "ticker": ticker,
            "evidence_band": "STRONG",
            "detail_drawer_payload": {
                "evidence_band": "STRONG",
                "committee": {"status": "source_validated"},
                "evidence_explanation": {
                    "technical_signals_status": "READY",
                    "sentiment_status": "READY",
                },
            },
        })
    return {
        "run_session_id": session_id,
        "source_health": {"status": "ok"},
        "portfolio_command_center": {"source_health": {"status": "ok"}},
        "current_holdings": cards,
        "best_buys": [cards[0]] if cards else [],
        "trim_sell_desk": [],
    }


def _assert_failed_closed(payload: dict, tickers: list[str]) -> None:
    contract = payload["run_trust_contract"]
    assert contract["overall_status"] == trust.STATUS_UNKNOWN
    assert payload["source_health"]["status"] == trust.STATUS_UNKNOWN
    assert payload["portfolio_command_center"]["source_health"]["status"] == trust.STATUS_UNKNOWN
    cards = {c["ticker"]: c for c in payload["current_holdings"]}
    for ticker in tickers:
        ddp = cards[ticker]["detail_drawer_payload"]
        # NEVER preserved as the old optimistic "source_validated".
        assert ddp["committee"]["status"] != "source_validated"
        assert ddp["committee"]["status"] == "pending"
        assert ddp["source_lineage"]["status"] == "unknown"
        assert ddp["conflict_review_status"] == "unknown"
        assert ddp["trust_status"] == "unknown"
        # Technical/sentiment readiness is never fabricated — untouched.
        assert ddp["evidence_explanation"]["technical_signals_status"] == "READY"
        assert ddp["evidence_explanation"]["sentiment_status"] == "READY"
    assert payload["source_pack_validated_count"] == 0


def test_fail_closed_missing_session_row():
    client = FakeSupabase()
    tickers = ["AAPL", "MSFT"]
    payload = _old_shaped_optimistic_payload("nonexistent-session-id", tickers)
    _enrich_snapshot_with_run_trust_contract(payload, client=client, user_id=USER)
    _assert_failed_closed(payload, tickers)


@pytest.mark.asyncio
async def test_fail_closed_missing_ticker_rows():
    client = FakeSupabase()
    session_id = str(uuid.uuid4())
    await control.create_distributed_session(
        client=client, user_id=USER, session_id=session_id,
    )
    # Delete every frozen ticker row out from under an otherwise-real session.
    client.store["intel_run_tickers"] = [
        t for t in client.store.get("intel_run_tickers", [])
        if t.get("run_session_id") != session_id
    ]
    tickers = ["AAPL"]
    payload = _old_shaped_optimistic_payload(session_id, tickers)
    _enrich_snapshot_with_run_trust_contract(payload, client=client, user_id=USER)
    _assert_failed_closed(payload, tickers)


@pytest.mark.asyncio
async def test_fail_closed_suspiciously_empty_task_read():
    client = FakeSupabase()
    session_id = await _session_with_review(client)
    # A real session with real ticker rows, but every task row is gone —
    # exactly the "read came back suspiciously empty" signal.
    client.store["intel_run_tasks"] = [
        t for t in client.store.get("intel_run_tasks", [])
        if t.get("run_session_id") != session_id
    ]
    tickers = ["AAPL", "MSFT", "GOOGL"]
    payload = _old_shaped_optimistic_payload(session_id, tickers)
    _enrich_snapshot_with_run_trust_contract(payload, client=client, user_id=USER)
    _assert_failed_closed(payload, tickers)
    # Never silently claims "no review was required" for GOOGL just because
    # the task read came back empty.
    assert "not_required" not in {
        c["detail_drawer_payload"]["conflict_review_status"]
        for c in payload["current_holdings"]
    }


@pytest.mark.asyncio
async def test_fail_closed_raised_read_failure(monkeypatch):
    import app.services.intelligence.v3.distributed.run_task_store_v1 as dstore

    client = FakeSupabase()
    session_id = await _session_with_review(client)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated transient DB failure")

    monkeypatch.setattr(dstore, "list_specialist_outputs", _boom)
    tickers = ["AAPL", "MSFT", "GOOGL"]
    payload = _old_shaped_optimistic_payload(session_id, tickers)
    _enrich_snapshot_with_run_trust_contract(payload, client=client, user_id=USER)
    _assert_failed_closed(payload, tickers)
