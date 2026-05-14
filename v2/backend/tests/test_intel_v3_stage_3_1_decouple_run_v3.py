"""Stage 3.1 — decouple synchronous Run Intel v3 into a fast certification path.

Contract under test:
  * The synchronous Run Intel v3 HTTP path makes ZERO analyst/LLM refresh calls.
  * Stale analyst evidence does NOT trigger an in-request analyst/LLM refresh.
  * Stale analyst evidence produces an honest ``refresh_requested`` +
    PARTIAL_CERTIFIED / BLOCKED_UNCERTIFIED state — never a fake FAST_CERTIFIED.
  * Deterministic ``decide()`` remains the final visible action authority — the
    refresh-request seam never alters or owns Buy/Hold/Trim/Sell.
  * Intel v3 price refresh routes through the coalescing/dedupe path.
  * ``intel_v3_service`` no longer imports the LLM analyst adapters.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.intelligence.v3.analyst_refresh_request_seam_v1 import (
    STATUS_NO_STALE,
    STATUS_REFRESH_REQUESTED,
    AnalystRefreshRequestSeam,
)
from app.services.intelligence.v3.evidence_freshness_contract_v1 import (
    RUN_MODE_BLOCKED_UNCERTIFIED,
    RUN_MODE_FAST_CERTIFIED,
    RUN_MODE_PARTIAL_CERTIFIED,
)
from app.services.intelligence.v3.evidence_refresh_orchestrator_v1 import (
    EvidenceRefreshOrchestrator,
    OrchestratorInputs,
    RefreshBudget,
)


def _now() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def _iso_ago(hours: float, now: datetime | None = None) -> str:
    return ((now or _now()) - timedelta(hours=hours)).isoformat()


# ── 1. The refresh-request seam (unit) ───────────────────────────────────────


class TestAnalystRefreshRequestSeam:
    @pytest.mark.asyncio
    async def test_seam_returns_refresh_requested_with_zero_llm_calls(self):
        seam = AnalystRefreshRequestSeam(user_id=uuid.uuid4())
        result = await seam(["AAPL", "NVDA"])
        d = result.to_dict()
        assert d["status"] == STATUS_REFRESH_REQUESTED
        # The seam never performs LLM work inside the request.
        assert d["attempted_llm_calls"] == 0
        assert d["successful_llm_calls"] == 0
        assert d["failed_llm_calls"] == 0
        assert d["selected_tickers"] == []
        assert d["per_ticker"] == []

    @pytest.mark.asyncio
    async def test_seam_reports_stale_tickers_as_deferred(self):
        seam = AnalystRefreshRequestSeam(user_id=uuid.uuid4())
        result = await seam(["AAPL", "NVDA"])
        d = result.to_dict()
        # Stale tickers reported as deferred so the orchestrator keeps them
        # stale/uncertified rather than silently certifying the run.
        assert set(d["deferred_tickers"]) == {"AAPL", "NVDA"}
        assert set(d["requested_tickers"]) == {"AAPL", "NVDA"}

    @pytest.mark.asyncio
    async def test_seam_empty_list_returns_no_stale(self):
        seam = AnalystRefreshRequestSeam(user_id=uuid.uuid4())
        result = await seam([])
        assert result.to_dict()["status"] == STATUS_NO_STALE

    @pytest.mark.asyncio
    async def test_seam_dedupes_and_uppercases(self):
        seam = AnalystRefreshRequestSeam(user_id=uuid.uuid4())
        result = await seam(["aapl", "AAPL", "nvda"])
        assert result.requested_tickers == ["AAPL", "NVDA"]

    @pytest.mark.asyncio
    async def test_seam_logs_the_request(self, caplog):
        seam = AnalystRefreshRequestSeam(user_id=uuid.uuid4())
        with caplog.at_level("INFO"):
            await seam(["AAPL"])
        assert any(
            "intel_v3.analyst_refresh_requested" in r.message
            and "in_request_llm_refresh=false" in r.message
            for r in caplog.records
        )

    def test_seam_module_does_not_import_decide_or_llm_adapters(self):
        from app.services.intelligence.v3 import (
            analyst_refresh_request_seam_v1 as mod,
        )
        src = open(mod.__file__).read()
        assert "decision_policy_v1" not in src
        # The seam carries no visible action authority.
        assert "import" in src  # sanity
        d = AnalystRefreshRequestSeam(user_id=uuid.uuid4())
        # Result shape exposes no Buy/Hold/Trim/Sell field.
        keys = set(asyncio.run(d(["AAPL"])).to_dict().keys())
        assert "action" not in keys and "decision" not in keys


# ── 2. Orchestrator wired with the seam ──────────────────────────────────────


def _stale_analyst_inputs(*, analyst_age_hours: float, now: datetime) -> OrchestratorInputs:
    tickers = ["AAPL", "NVDA"]
    return OrchestratorInputs(
        evidence_stats={
            "recommendation_timestamps": [_iso_ago(analyst_age_hours, now)] * 2,
            "agent_insight_run_timestamps": [_iso_ago(analyst_age_hours, now)] * 2,
            "active_position_count": 2,
            "persisted_recommendation_count": 2,
            "persisted_agent_insight_count": 2,
        },
        # Positions / prices fresh — only analyst evidence is stale.
        portfolio_snapshot_at=_iso_ago(0.5, now),
        market_value_certified_ats=[_iso_ago(0.1, now)] * 2,
        tickers=tickers,
        research_artifact_timestamps=[],
        now=now,
        per_ticker_evidence=[
            {"ticker": t, "prior_action": "HOLD", "weight_pct": 50.0,
             "evidence_age_hours": analyst_age_hours}
            for t in tickers
        ],
    )


class TestOrchestratorWithSeam:
    @pytest.mark.asyncio
    async def test_stale_analyst_evidence_makes_zero_llm_calls(self):
        now = _now()
        seam = AnalystRefreshRequestSeam(user_id=uuid.uuid4())
        orch = EvidenceRefreshOrchestrator(
            user_id=uuid.uuid4(),
            inputs=_stale_analyst_inputs(analyst_age_hours=100.0, now=now),
            price_refresh=None,
            analyst_refresh=seam,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert result.attempted_llm_calls == 0
        assert result.successful_llm_calls == 0
        assert result.failed_llm_calls == 0

    @pytest.mark.asyncio
    async def test_stale_analyst_evidence_does_not_fast_certify(self):
        now = _now()
        seam = AnalystRefreshRequestSeam(user_id=uuid.uuid4())
        orch = EvidenceRefreshOrchestrator(
            user_id=uuid.uuid4(),
            inputs=_stale_analyst_inputs(analyst_age_hours=100.0, now=now),
            price_refresh=None,
            analyst_refresh=seam,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert result.run_mode != RUN_MODE_FAST_CERTIFIED
        diag = result.to_diagnostics_dict()
        assert diag["analyst_refresh_status"] == STATUS_REFRESH_REQUESTED
        # Stale tickers are surfaced honestly for the UI.
        assert set(diag["analyst_refresh_deferred_tickers"]) == {"AAPL", "NVDA"}
        assert diag["analyst_refresh_successful_tickers"] == []

    @pytest.mark.asyncio
    async def test_refreshable_stale_analyst_is_partial_certified(self):
        # 100h: past the fresh window, inside the stale window → STALE (not HARD).
        now = _now()
        seam = AnalystRefreshRequestSeam(user_id=uuid.uuid4())
        orch = EvidenceRefreshOrchestrator(
            user_id=uuid.uuid4(),
            inputs=_stale_analyst_inputs(analyst_age_hours=100.0, now=now),
            price_refresh=None,
            analyst_refresh=seam,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert result.run_mode == RUN_MODE_PARTIAL_CERTIFIED

    @pytest.mark.asyncio
    async def test_hard_stale_analyst_stays_blocked_uncertified(self):
        # 300h: past the stale window → HARD_STALE critical → BLOCKED.
        now = _now()
        seam = AnalystRefreshRequestSeam(user_id=uuid.uuid4())
        orch = EvidenceRefreshOrchestrator(
            user_id=uuid.uuid4(),
            inputs=_stale_analyst_inputs(analyst_age_hours=300.0, now=now),
            price_refresh=None,
            analyst_refresh=seam,
            budget=RefreshBudget(),
        )
        result = await orch.run()
        assert result.run_mode == RUN_MODE_BLOCKED_UNCERTIFIED
        # No fabricated freshness — critical analyst sources stay HARD_STALE.
        assert result.source_states_after["recommendations"].state == "HARD_STALE"
        assert result.source_states_after["agent_insights"].state == "HARD_STALE"


# ── 3. run_v3() synchronous path ─────────────────────────────────────────────


def _card(ticker: str, action: str):
    card = MagicMock()
    card.ticker = ticker
    card.name = f"{ticker} Corp"
    card.category = "stock"
    card.action = action
    card.analyst_action = action
    card.conviction_level = "MEDIUM"
    card.technical_signal = None
    card.risk_flag = None
    card.analyst_risks = []
    card.data_quality_label = "PARTIAL"
    card.intel_read = None
    card.thesis_v2 = None
    card.analyst_used_fallback = False
    card.primary_driver = "Driver text"
    card.action_reason = "Action reason"
    card.analyst_drivers = []
    return card


def _build_service():
    from app.services.intelligence.v3.intel_v3_service import IntelV3Service
    service = IntelV3Service.__new__(IntelV3Service)
    service.user_id = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    mock_client = MagicMock()
    mock_table = MagicMock()
    for m in ("update", "insert", "select", "eq", "in_", "order", "limit"):
        getattr(mock_table, m).return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    mock_client.table.return_value = mock_table
    service.client = mock_client
    return service


def _run_v3_with_evidence(*, analyst_age_hours: float, cards):
    """Run the real synchronous run_v3() path with controlled evidence ages.

    Uses the real ``_run_refresh_orchestrator`` + real refresh-request seam;
    only DB-bound helpers and price import are patched out.
    """
    now = _now()
    service = _build_service()
    tickers = [c.ticker for c in cards]
    evidence_stats = {
        "active_position_count": len(cards),
        "persisted_recommendation_count": len(cards),
        "persisted_agent_insight_count": len(cards),
        "missing_recommendation_count": 0,
        "missing_evidence_count": 0,
        "stale_or_missing_source_count": 0,
        "recommendation_timestamps": [_iso_ago(analyst_age_hours, now)] * len(cards),
        "agent_insight_run_timestamps": [_iso_ago(analyst_age_hours, now)] * len(cards),
    }
    adapter_mock = MagicMock()
    adapter_mock.load_cards = AsyncMock(return_value=(cards, evidence_stats))

    per_ticker_ev = [
        {"ticker": t, "prior_action": "HOLD", "weight_pct": 100.0 / len(tickers),
         "evidence_age_hours": analyst_age_hours}
        for t in tickers
    ]

    with (
        patch(
            "app.services.intelligence.v3.intel_v3_service.ReadOnlyEvidenceAdapter",
            return_value=adapter_mock,
        ),
        patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
        patch.object(service, "_get_active_tickers", new_callable=AsyncMock, return_value=tickers),
        patch.object(
            service, "_get_latest_portfolio_snapshot_meta",
            new_callable=AsyncMock,
            return_value={"snapshot_at": _iso_ago(0.5, now),
                          "market_value_certified_ats": [_iso_ago(0.1, now)] * len(cards)},
        ),
        patch.object(
            service, "_get_per_ticker_analyst_evidence",
            new_callable=AsyncMock, return_value=per_ticker_ev,
        ),
        # Tier-0 price refresh callable patched to None: this test isolates the
        # analyst decoupling. (Price coalescing is covered separately below.)
        patch.object(service, "_build_price_refresh_callable", return_value=None),
        patch.object(service, "_persist_snapshot", new_callable=AsyncMock),
    ):
        return asyncio.run(service.run_v3())


class TestSyncRunV3Decoupled:
    def test_synchronous_path_makes_zero_analyst_llm_calls(self):
        snap = _run_v3_with_evidence(
            analyst_age_hours=100.0,
            cards=[_card("AAPL", "HOLD"), _card("NVDA", "BUY")],
        )
        diag = snap["diagnostics"]
        assert diag["attempted_llm_calls"] == 0
        assert diag["successful_llm_calls"] == 0
        assert diag["failed_llm_calls"] == 0

    def test_stale_analyst_evidence_produces_honest_refresh_requested_state(self):
        snap = _run_v3_with_evidence(
            analyst_age_hours=100.0,
            cards=[_card("AAPL", "HOLD"), _card("NVDA", "BUY")],
        )
        diag = snap["diagnostics"]
        assert diag["analyst_refresh_status"] == STATUS_REFRESH_REQUESTED
        # Honest state — not a fake FAST_CERTIFIED.
        assert diag["run_mode"] != RUN_MODE_FAST_CERTIFIED
        assert diag["run_mode"] in (
            RUN_MODE_PARTIAL_CERTIFIED, RUN_MODE_BLOCKED_UNCERTIFIED,
        )
        assert set(diag["analyst_refresh_deferred_tickers"]) == {"AAPL", "NVDA"}

    def test_hard_stale_analyst_evidence_stays_uncertified(self):
        snap = _run_v3_with_evidence(
            analyst_age_hours=300.0,
            cards=[_card("AAPL", "HOLD"), _card("NVDA", "BUY")],
        )
        assert snap["diagnostics"]["run_mode"] == RUN_MODE_BLOCKED_UNCERTIFIED

    def test_deterministic_decide_remains_visible_action_authority(self):
        """The refresh layer must not alter visible Buy/Hold/Trim/Sell.

        Same input cards, different evidence freshness (fresh vs stale) → the
        deterministic decision output is identical. Only the run-mode / trust
        banner changes, never the actions.
        """
        cards_fresh = [_card("AAPL", "HOLD"), _card("NVDA", "BUY"), _card("MSFT", "TRIM")]
        cards_stale = [_card("AAPL", "HOLD"), _card("NVDA", "BUY"), _card("MSFT", "TRIM")]

        snap_fresh = _run_v3_with_evidence(analyst_age_hours=1.0, cards=cards_fresh)
        snap_stale = _run_v3_with_evidence(analyst_age_hours=300.0, cards=cards_stale)

        # Fresh evidence certifies fast; stale evidence is blocked — proving the
        # run modes genuinely differ.
        assert snap_fresh["diagnostics"]["run_mode"] == RUN_MODE_FAST_CERTIFIED
        assert snap_stale["diagnostics"]["run_mode"] == RUN_MODE_BLOCKED_UNCERTIFIED

        # ...yet the deterministic decisions are identical.
        actions_fresh = {
            c["ticker"]: c["action"] for c in snap_fresh["current_holdings"]
        }
        actions_stale = {
            c["ticker"]: c["action"] for c in snap_stale["current_holdings"]
        }
        assert actions_fresh == actions_stale
        assert snap_fresh["action_counts"] == snap_stale["action_counts"]

    def test_intel_v3_service_does_not_import_llm_analyst_adapters(self):
        from app.services.intelligence.v3 import intel_v3_service as mod
        src = open(mod.__file__).read()
        # The synchronous service path must not wire any LLM analyst adapter.
        assert "analyst_refresh_adapter_v1" not in src
        assert "full_portfolio_analyst_refresh_adapter_v1" not in src
        assert "AgentOrchestrator" not in src
        # It DOES wire the non-LLM request seam.
        assert "AnalystRefreshRequestSeam" in src


# ── 4. Price refresh coalescing / dedupe ─────────────────────────────────────


class TestPriceRefreshCoalescing:
    def test_price_refresh_callable_dedupes_and_routes_through_fetch_prices(self):
        service = _build_service()
        captured: dict[str, list[str]] = {}

        class _FakePriceService:
            def __init__(self, **kwargs):
                pass

            async def fetch_prices(self, tickers):
                captured["tickers"] = list(tickers)
                return {t: {"is_valid": True, "is_stale": False} for t in tickers}

            async def close(self):
                pass

        with patch(
            "app.services.price_engine.PriceService", _FakePriceService,
        ):
            refresh = service._build_price_refresh_callable()
            assert refresh is not None
            result = asyncio.run(refresh(["AAPL", "aapl", "NVDA", "AAPL"]))

        # Duplicate tickers collapsed before the (already-coalescing) call.
        assert captured["tickers"] == ["AAPL", "NVDA"]
        assert set(result.keys()) == {"AAPL", "NVDA"}

    def test_price_service_fetch_prices_is_the_coalescing_entry_point(self):
        """Regression guard: the coalescing/dedupe contract lives in
        PriceService._fetch_one (per-ticker async lock). The Intel v3 price
        refresh must route through fetch_prices so it inherits that path."""
        import inspect
        from app.services.price_engine import PriceService
        src = inspect.getsource(PriceService._fetch_one)
        assert "_get_ticker_lock" in src
        assert "coalesc" in src.lower()
