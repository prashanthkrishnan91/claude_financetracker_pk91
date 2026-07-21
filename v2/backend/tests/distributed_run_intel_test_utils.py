"""Shared in-memory Supabase fake + fixtures for distributed Run Intel tests.

Extends the migration-026 fake (run_intel_session_test_utils) with:
  * unique-index emulation for the migration-027 constraints:
      - intel_run_tickers: one row per (run_session_id, ticker)
      - intel_run_tasks: one row per logical
        (run_session_id, task_type, lane, ticker, batch_key)
      - intel_run_specialist_outputs: one row per (run_session_id, ticker, axis)
      - intel_run_sessions: at most one non-terminal workflow_version>=2 row
        per user (uq_intel_run_sessions_active_per_user)
  * owner-guard trigger emulation (cross-user session/ticker/task links raise)
  * a deterministic FakeLLM that answers specialist/review prompts and records
    every call (tickers requested, axis) for exact accounting assertions
  * provider-call recording patches so tests can assert exact ticker scoping.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from tests.run_intel_session_test_utils import (
    FakeQuery as BaseFakeQuery,
    FakeResult,
    UniqueViolation,
)


class OwnerGuardViolation(Exception):
    """Emulates the migration-027 cross-user owner-guard trigger."""


_TERMINAL_SESSION = ("completed", "completed_with_gaps", "failed", "superseded")


class FakeQuery(BaseFakeQuery):
    def _check_unique(self, new_row: dict, rows: list[dict]) -> None:
        super()._check_unique(new_row, rows)
        if self._table == "intel_run_tickers":
            key = (str(new_row.get("run_session_id")), str(new_row.get("ticker")))
            for r in rows:
                if (str(r.get("run_session_id")), str(r.get("ticker"))) == key:
                    raise UniqueViolation("uq_intel_run_tickers_session_ticker")
        if self._table == "intel_run_tasks":
            key = (
                str(new_row.get("run_session_id")),
                str(new_row.get("task_type")),
                str(new_row.get("lane") or ""),
                str(new_row.get("ticker") or ""),
                str(new_row.get("batch_key") or ""),
            )
            for r in rows:
                if (
                    str(r.get("run_session_id")),
                    str(r.get("task_type")),
                    str(r.get("lane") or ""),
                    str(r.get("ticker") or ""),
                    str(r.get("batch_key") or ""),
                ) == key:
                    raise UniqueViolation("uq_intel_run_tasks_logical")
        if self._table == "intel_run_specialist_outputs":
            key = (
                str(new_row.get("run_session_id")),
                str(new_row.get("ticker")),
                str(new_row.get("axis")),
            )
            for r in rows:
                if (
                    str(r.get("run_session_id")),
                    str(r.get("ticker")),
                    str(r.get("axis")),
                ) == key:
                    raise UniqueViolation("uq_intel_run_specialist_outputs_key")
        if self._table == "intel_run_sessions":
            if int(new_row.get("workflow_version") or 1) >= 2 and str(
                new_row.get("status") or ""
            ) not in _TERMINAL_SESSION:
                for r in rows:
                    if (
                        str(r.get("user_id")) == str(new_row.get("user_id"))
                        and int(r.get("workflow_version") or 1) >= 2
                        and str(r.get("status") or "") not in _TERMINAL_SESSION
                    ):
                        raise UniqueViolation(
                            "uq_intel_run_sessions_active_per_user"
                        )

    def _check_owner_guard(self, new_row: dict) -> None:
        if self._table not in (
            "intel_run_tickers", "intel_run_tasks", "intel_run_specialist_outputs",
        ):
            return
        sessions = self._store.get("intel_run_sessions", [])
        session = next(
            (
                s for s in sessions
                if str(s.get("id")) == str(new_row.get("run_session_id"))
            ),
            None,
        )
        if session is None:
            raise OwnerGuardViolation(
                f"{self._table}: unknown run_session_id "
                f"{new_row.get('run_session_id')}"
            )
        if str(session.get("user_id")) != str(new_row.get("user_id")):
            raise OwnerGuardViolation(
                f"{self._table}: user {new_row.get('user_id')} does not own "
                f"session {new_row.get('run_session_id')}"
            )

    def execute(self):
        if self._op == "insert":
            for row in self._payload:
                self._check_owner_guard(row)
        return super().execute()


class FakeSupabase:
    def __init__(self):
        self.store: dict[str, list[dict]] = {}
        self.rpc_calls: list[tuple[str, dict]] = []

    def table(self, name):
        return FakeQuery(self.store, name)

    def rows(self, name):
        return self.store.get(name, [])

    # No RPC support — forces the store's guarded-UPDATE CAS fallback, which
    # is what the fake can emulate faithfully.


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def seed_position(
    client: FakeSupabase,
    user_id: str,
    ticker: str,
    *,
    category: str = "Core",
    shares: float = 10.0,
    avg_cost: float = 100.0,
    close_price: Optional[float] = 120.0,
) -> None:
    client.store.setdefault("positions", []).append({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "ticker": ticker,
        "category": category,
        "shares": shares,
        "avg_cost": avg_cost,
        "drip_shares": 0,
        "drip_cost": 0,
        "lt_eligible": False,
        "lt_date": None,
    })
    if close_price is not None:
        client.store.setdefault("price_history", []).append({
            "id": str(uuid.uuid4()),
            "ticker": ticker.upper(),
            "price_date": now_utc().date().isoformat(),
            "close_price": close_price,
        })


# The deterministic 34-holding golden fixture: 28 equities + 4 ETFs + 2 crypto.
GOLDEN_EQUITIES = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD",
    "CRM", "ADBE", "NFLX", "COST", "PEP", "KO", "JPM", "V",
    "MA", "UNH", "JNJ", "PFE", "XOM", "CVX", "WMT", "HD",
    "DIS", "ALK", "BA", "CAT",
]
GOLDEN_ETFS = ["VTI", "VOO", "VHT", "QQQ"]
GOLDEN_CRYPTO = ["BTC", "ETH"]
GOLDEN_34 = GOLDEN_EQUITIES + GOLDEN_ETFS + GOLDEN_CRYPTO


def seed_golden_portfolio(client: FakeSupabase, user_id: str) -> list[str]:
    for i, ticker in enumerate(GOLDEN_EQUITIES):
        seed_position(
            client, user_id, ticker,
            category="Core", shares=5 + i, avg_cost=50 + i, close_price=100 + i,
        )
    for i, ticker in enumerate(GOLDEN_ETFS):
        seed_position(
            client, user_id, ticker,
            category="ETF", shares=20 + i, avg_cost=180 + i, close_price=220 + i,
        )
    for i, ticker in enumerate(GOLDEN_CRYPTO):
        seed_position(
            client, user_id, ticker,
            category="Crypto", shares=0.5, avg_cost=20000, close_price=30000,
        )
    return list(GOLDEN_34)


# ── Provider recording / forbidding ──────────────────────────────────────────

class ProviderRecorder:
    """Deterministic provider fakes that record every (fn, ticker) call and
    can fail selected tickers/lanes."""

    def __init__(
        self,
        *,
        fail_price: Optional[set[str]] = None,
        fail_fundamentals: Optional[set[str]] = None,
        fail_history: Optional[set[str]] = None,
        fail_crypto: Optional[set[str]] = None,
    ):
        self.calls: list[tuple[str, str]] = []
        self.fail_price = {t.upper() for t in (fail_price or set())}
        self.fail_fundamentals = {t.upper() for t in (fail_fundamentals or set())}
        self.fail_history = {t.upper() for t in (fail_history or set())}
        self.fail_crypto = {t.upper() for t in (fail_crypto or set())}

    def tickers_called(self) -> set[str]:
        return {t for _, t in self.calls}

    async def fetch_price_action(self, ticker: str) -> dict[str, Any]:
        self.calls.append(("price_action", ticker.upper()))
        if ticker.upper() in self.fail_price or ticker.upper() in self.fail_history:
            return {}
        return {
            "last": 101.5, "pct_1d": 0.4, "pct_5d": 1.2, "pct_30d": 3.4,
            "pct_3mo": 8.0, "sma20": 100.0, "sma50": 98.0,
            "volatility_30d": 0.22, "high_3mo": 110.0, "low_3mo": 90.0,
            "vol_last": 1000, "vol_avg_20d": 900, "n_bars": 63,
        }

    async def fetch_fundamentals(self, ticker: str) -> dict[str, Any]:
        self.calls.append(("fundamentals", ticker.upper()))
        if ticker.upper() in self.fail_fundamentals:
            return {}
        return {
            "pe": 21.0, "forward_pe": 19.0, "profit_margin": 0.24,
            "revenue_growth": 0.09, "debt_to_equity": 80.0,
            "market_cap": 1_000_000_000.0, "sector": "Technology",
        }

    async def fetch_yfinance_news(self, ticker: str, limit: int = 6):
        self.calls.append(("news", ticker.upper()))
        return [
            {"headline": f"{ticker.upper()} update {i}", "source": "test",
             "datetime": 1700000000 + i}
            for i in range(3)
        ]

    async def fetch_coingecko_market(self, _http, ticker: str) -> dict[str, Any]:
        self.calls.append(("coingecko", ticker.upper()))
        if ticker.upper() in self.fail_crypto:
            return {}
        return {
            "price_usd": 30000.0, "ath_pct": -40.0, "pct_24h": 1.0,
            "pct_7d": 4.0, "pct_30d": -2.0, "market_cap_rank": 1,
            "sentiment_up_pct": 70.0, "sentiment_down_pct": 30.0,
        }


def patch_providers(monkeypatch, recorder: ProviderRecorder) -> None:
    """Patch the collector module's provider seams to the recorder."""
    import app.services.intelligence.v3.distributed.collectors_v1 as collectors

    async def _no_http():
        return None

    monkeypatch.setattr(collectors, "fetch_price_action", recorder.fetch_price_action)
    monkeypatch.setattr(collectors, "fetch_fundamentals", recorder.fetch_fundamentals)
    monkeypatch.setattr(collectors, "fetch_yfinance_news", recorder.fetch_yfinance_news)
    monkeypatch.setattr(
        collectors, "fetch_coingecko_market", recorder.fetch_coingecko_market
    )
    monkeypatch.setattr(collectors, "_get_http_client", _no_http)


def forbid_providers(monkeypatch) -> None:
    """Any provider call fails the test outright (fast-create proof)."""
    import app.services.intelligence.v3.distributed.collectors_v1 as collectors

    async def _forbidden(*args, **kwargs):
        raise AssertionError("provider call is forbidden in this test")

    monkeypatch.setattr(collectors, "fetch_price_action", _forbidden)
    monkeypatch.setattr(collectors, "fetch_fundamentals", _forbidden)
    monkeypatch.setattr(collectors, "fetch_yfinance_news", _forbidden)
    monkeypatch.setattr(collectors, "fetch_coingecko_market", _forbidden)


# ── Fake LLM ─────────────────────────────────────────────────────────────────

class FakeLLM:
    """Deterministic ask_json fake recording every specialist/review call.

    ``script`` may override responses: {(axis, ticker): result_dict_or_None}.
    None → the ticker is omitted from the response (malformed simulation).
    """

    def __init__(
        self,
        *,
        script: Optional[dict[tuple[str, str], Optional[dict]]] = None,
        fail_all: bool = False,
        score_by_ticker: Optional[dict[str, float]] = None,
    ):
        self.calls: list[dict[str, Any]] = []
        self.script = script or {}
        self.fail_all = fail_all
        self.score_by_ticker = {
            k.upper(): v for k, v in (score_by_ticker or {}).items()
        }
        self.primary_model = "fake-claude"

    def _default_result(self, axis: str, ticker: str) -> dict[str, Any]:
        score = self.score_by_ticker.get(ticker.upper(), 0.5)
        return {
            "ticker": ticker,
            "stance": "positive" if score >= 0 else "negative",
            "score": score,
            "confidence": 0.8,
            "key_findings": [f"{ticker} {axis} finding from provided evidence"],
            "risks": [f"{ticker} {axis} risk"],
            "missing_evidence": [],
            "limitations": [],
        }

    async def ask_json(
        self, system: str, user: str, max_tokens: int = 1024,
        normalizer: Any = None, metadata: Optional[dict] = None,
    ) -> dict[str, Any]:
        axis = str((metadata or {}).get("axis") or "unknown")
        tickers = re.findall(r"Analyze these tickers: ([A-Z0-9+, .]+)\.", user)
        requested = (
            [t.strip() for t in tickers[0].split(",")] if tickers else []
        )
        if not requested and "Ticker:" in user:
            match = re.search(r"Ticker: ([A-Z0-9.]+)", user)
            requested = [match.group(1)] if match else []
        self.calls.append({
            "axis": axis,
            "tickers": [t.upper() for t in requested],
            "metadata": metadata or {},
            "prompt_chars": len(user),
        })
        if self.fail_all:
            return {}
        if axis == "review":
            ticker = requested[0] if requested else ""
            override = self.script.get(("review", ticker.upper()))
            if override is not None:
                return override
            return {
                "ticker": ticker, "stance": "neutral", "score": 0.0,
                "confidence": 0.7,
                "key_findings": ["reconciled conflicting specialist views"],
                "risks": [], "missing_evidence": [], "limitations": [],
            }
        results = []
        for ticker in requested:
            key = (axis, ticker.upper())
            if key in self.script:
                override = self.script[key]
                if override is None:
                    continue  # simulate malformed/missing ticker
                results.append(override)
            else:
                results.append(self._default_result(axis, ticker))
        return {"results": results}


def make_settings(**overrides: Any) -> Any:
    from types import SimpleNamespace

    base = {
        "intel_v3_distributed_max_collector_concurrency": 4,
        "intel_v3_distributed_max_llm_concurrency": 2,
        "intel_v3_distributed_max_specialist_batch": 5,
        "intel_v3_distributed_task_lease_seconds": 300,
        "intel_v3_distributed_max_task_attempts": 3,
        "intel_v3_research_workers_enabled": False,
        "intel_v3_fundamentals_evidence_enabled": False,
        "intel_v3_technicals_evidence_enabled": False,
        "intel_v3_news_sentiment_evidence_enabled": False,
        "intel_v3_sec_companyfacts_evidence_enabled": False,
        "intel_v3_sentiment_catalyst_evidence_enabled": False,
        "intel_v3_etf_nport_evidence_enabled": False,
        "intel_v3_macro_evidence_enabled": False,
        "sec_edgar_user_agent": "",
        "fred_api_key": None,
        "intel_v3_snapshot_writes_enabled": True,
        "anthropic_api_key": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakePublicationService:
    """Stands in for IntelV3Service.run_prewarm_snapshot in worker tests."""

    def __init__(self, client: FakeSupabase, user_id: str, *, fail_times: int = 0):
        self.client = client
        self.user_id = user_id
        self.fail_times = fail_times
        self.calls: list[dict[str, Any]] = []

    async def run_prewarm_snapshot(
        self, *, prewarm_run_id: str, skip_persist_on_fail: bool = False,
        run_session_id: Optional[str] = None,
        scope_tickers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        self.calls.append({
            "run_session_id": run_session_id,
            "scope_tickers": sorted(scope_tickers or []),
        })
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("simulated publication outage")
        row = {
            "id": str(uuid.uuid4()),
            "user_id": self.user_id,
            "run_session_id": run_session_id,
            "is_active": True,
            "payload": {
                "run_session_id": run_session_id,
                "snapshot_source": "worker_certified",
            },
            "created_at": now_utc().isoformat(),
        }
        self.client.table("intel_v3_snapshots").insert(row).execute()
        return {
            "snapshot_source": "worker_certified",
            "snapshot_row_id": row["id"],
        }


async def drive_supervisor_to_completion(
    supervisor: Any, *, max_passes: int = 200,
) -> int:
    """Run supervisor passes until no active session remains. Returns passes."""
    passes = 0
    while passes < max_passes:
        stats = await supervisor.run_pass()
        passes += 1
        if stats["sessions"] == 0:
            return passes
    raise AssertionError(
        f"supervisor did not converge within {max_passes} passes"
    )
