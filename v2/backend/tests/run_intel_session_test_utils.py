"""Shared in-memory Supabase fake + helpers for durable Run Intel session tests.

Modeled on the fake used by test_intel_v3_stage_3_2_analyst_refresh_worker.py,
extended with:

  * ``desc`` ordering + real ``limit`` support;
  * unique-index emulation for the two new migration-026 constraints:
      - intel_v3_snapshots: at most one row per non-null run_session_id
      - analyst_refresh_jobs: at most one row per (run_session_id, ticker)
        for non-null run_session_id
    so tests exercise the same idempotency the production SQL enforces.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional


class UniqueViolation(Exception):
    """Emulates a Postgres unique-index violation raised by the client."""


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store: dict, table: str):
        self._store = store
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters: list[tuple] = []
        self._order: list[tuple[str, bool]] = []
        self._limit: Optional[int] = None

    # ── builders ──
    def insert(self, rows):
        self._op = "insert"
        self._payload = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, values):
        self._op = "update"
        self._payload = dict(values)
        return self

    def select(self, *_cols, **_kw):
        if self._op not in ("insert", "update"):
            self._op = "select"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def filter(self, col, op, val):
        self._filters.append((f"filter_{op}", col, val))
        return self

    def order(self, col, desc: bool = False):
        self._order.append((col, desc))
        return self

    def limit(self, n):
        self._limit = int(n)
        return self

    # ── evaluation ──
    def _match(self, row) -> bool:
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and str(rv) != str(val):
                return False
            if kind == "neq" and str(rv) == str(val):
                return False
            if kind == "in" and rv not in val:
                return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(val)):
                return False
            if kind == "filter_is" and val == "null" and rv is not None:
                return False
        return True

    def _check_unique(self, new_row: dict, rows: list[dict]) -> None:
        if self._table == "intel_v3_snapshots":
            rsid = new_row.get("run_session_id")
            if rsid is not None and any(
                r.get("run_session_id") is not None
                and str(r.get("run_session_id")) == str(rsid)
                for r in rows
            ):
                raise UniqueViolation(
                    "duplicate key value violates unique constraint "
                    '"uq_intel_v3_snapshots_run_session"'
                )
        if self._table == "analyst_refresh_jobs":
            rsid = new_row.get("run_session_id")
            if rsid is not None:
                key = (str(rsid), str(new_row.get("ticker") or "").upper())
                for r in rows:
                    if r.get("run_session_id") is not None and (
                        str(r.get("run_session_id")),
                        str(r.get("ticker") or "").upper(),
                    ) == key:
                        raise UniqueViolation(
                            "duplicate key value violates unique constraint "
                            '"uq_analyst_refresh_jobs_session_ticker"'
                        )
            else:
                key = (
                    str(new_row.get("user_id")),
                    str(new_row.get("ticker") or "").upper(),
                    str(new_row.get("refresh_window") or ""),
                )
                for r in rows:
                    if r.get("run_session_id") is None and (
                        str(r.get("user_id")),
                        str(r.get("ticker") or "").upper(),
                        str(r.get("refresh_window") or ""),
                    ) == key:
                        raise UniqueViolation(
                            "duplicate key value violates unique constraint "
                            '"uq_analyst_refresh_jobs_legacy_window"'
                        )
        if self._table == "intel_run_sessions":
            rid = new_row.get("id")
            if rid is not None and any(str(r.get("id")) == str(rid) for r in rows):
                raise UniqueViolation(
                    "duplicate key value violates primary key "
                    '"intel_run_sessions_pkey"'
                )

    def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._op == "insert":
            inserted = []
            for r in self._payload:
                nr = dict(r)
                nr.setdefault("id", str(uuid.uuid4()))
                self._check_unique(nr, rows)
                rows.append(nr)
                inserted.append(dict(nr))
            return FakeResult(inserted)
        if self._op == "update":
            updated = []
            for r in rows:
                if self._match(r):
                    r.update(self._payload)
                    updated.append(dict(r))
            return FakeResult(updated)
        out = [dict(r) for r in rows if self._match(r)]
        for col, desc in reversed(self._order):
            out.sort(
                key=lambda x: (x.get(col) is None, str(x.get(col) or "")),
                reverse=desc,
            )
        if self._limit is not None:
            out = out[: self._limit]
        return FakeResult(out)


class FakeSupabase:
    def __init__(self):
        self.store: dict[str, list[dict]] = {}

    def table(self, name):
        return FakeQuery(self.store, name)

    def rows(self, name):
        return self.store.get(name, [])


# ── Common helpers ────────────────────────────────────────────────────────────

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def seed_positions(client: FakeSupabase, user_id: str, tickers: list[str]) -> None:
    for t in tickers:
        client.store.setdefault("positions", []).append(
            {"id": str(uuid.uuid4()), "user_id": user_id, "ticker": t}
        )


def write_ticker_evidence(
    client: FakeSupabase,
    *,
    user_id: str,
    ticker: str,
    agent_run_id: str,
    created_at: Optional[str] = None,
) -> None:
    """Durable per-ticker analyst evidence: one agent_insights row + one
    matching recommendations row, the shape the session flow verifies."""
    ts = created_at or now_utc().isoformat()
    client.store.setdefault("agent_insights", []).append({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "ticker": ticker,
        "run_id": agent_run_id,
        "created_at": ts,
        "analyst_verdict": {
            "primary_driver": f"{ticker} earnings momentum is improving",
            "action_reason": "valuation supports holding",
            "risk_flag": "watch concentration",
            "conviction_level": "MEDIUM",
        },
    })
    client.store.setdefault("recommendations", []).append({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "ticker": ticker,
        "agent_run_id": agent_run_id,
        "created_at": ts,
        "is_active": True,
    })


class RecordingAnalystAdapter:
    """Production-shaped analyst adapter fake used behind the REAL worker.

    Mirrors FullPortfolioAnalystRefreshAdapter's contract: called with the
    claimed batch's tickers, performs the "analyst work" (records one analyst
    call per ticker + writes durable evidence rows), and reports per-ticker
    success. Never calls any synthesis stage — and the tests separately patch
    AgentOrchestrator._run_portfolio_synthesis to raise if anything ever does.
    """

    def __init__(
        self,
        client: FakeSupabase,
        user_id: str,
        *,
        fail_tickers: Optional[set[str]] = None,
        analyst_calls: Optional[list[str]] = None,
        write_evidence: bool = True,
        mark_success: bool = True,
    ):
        self.client = client
        self.user_id = user_id
        self.fail_tickers = {t.upper() for t in (fail_tickers or set())}
        self.analyst_calls = analyst_calls if analyst_calls is not None else []
        self.write_evidence = write_evidence
        self.mark_success = mark_success

    async def __call__(self, tickers, *, priority_hints=None, started_at=None):
        from app.services.intelligence.v3.analyst_refresh_adapter_v1 import (
            AnalystRefreshResult,
            STATUS_FAILED,
            STATUS_PARTIAL_SUCCESS,
            STATUS_SUCCEEDED,
            TickerRefreshOutcome,
        )

        agent_run_id = str(uuid.uuid4())
        per_ticker = []
        successful = failed = 0
        for t in tickers:
            up = str(t).upper()
            self.analyst_calls.append(up)
            if up in self.fail_tickers:
                failed += 1
                per_ticker.append(TickerRefreshOutcome(
                    ticker=up, success=False, error_reason="fallback_verdict",
                    llm_call_count=1, llm_success_count=0,
                ))
                continue
            if self.write_evidence:
                write_ticker_evidence(
                    self.client, user_id=self.user_id, ticker=up,
                    agent_run_id=agent_run_id,
                )
            successful += 1
            per_ticker.append(TickerRefreshOutcome(
                ticker=up,
                success=self.mark_success,
                refreshed_agent_insight_at=now_utc().isoformat(),
                llm_call_count=1, llm_success_count=1,
            ))
        status = (
            STATUS_SUCCEEDED if failed == 0
            else STATUS_FAILED if successful == 0
            else STATUS_PARTIAL_SUCCESS
        )
        return AnalystRefreshResult(
            status=status,
            selected_tickers=[str(t).upper() for t in tickers],
            deferred_tickers=[],
            per_ticker=per_ticker,
            attempted_llm_calls=len(list(tickers)),
            successful_llm_calls=successful,
            failed_llm_calls=failed,
            duration_ms=1,
            agent_run_id=agent_run_id,
        )
