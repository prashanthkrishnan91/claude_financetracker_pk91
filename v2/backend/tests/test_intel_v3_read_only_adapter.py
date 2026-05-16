import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.intelligence.v3.intel_v3_service import IntelV3Service
from app.services.intelligence.v3.source_validator_lite import certify_snapshot_cards


def _card(ticker: str, action: str = "BUY"):
    return SimpleNamespace(
        ticker=ticker,
        name=ticker,
        category="stock",
        action=action,
        analyst_action=action,
        conviction_level="MEDIUM",
        technical_signal="BUY",
        risk_flag="",
        analyst_risks=[],
        data_quality_label="MEDIUM",
        intel_read=None,
        thesis_v2=None,
        analyst_used_fallback=False,
        primary_driver=f"{ticker} has improving fundamentals",
        action_reason=f"Add {ticker} gradually",
        analyst_drivers=[f"{ticker} earnings trend"],
    )


@pytest.mark.asyncio
async def test_run_v3_uses_read_only_adapter_not_recommendation_service(caplog):
    caplog.set_level("INFO")
    service = IntelV3Service.__new__(IntelV3Service)
    service.user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    service.client = MagicMock()

    with (
        patch("app.services.intelligence.v3.intel_v3_service.ReadOnlyEvidenceAdapter") as adapter_mock,
        patch.object(service, "_get_weight_map", new_callable=AsyncMock, return_value={}),
        patch.object(service, "_persist_snapshot", new_callable=AsyncMock),
    ):
        adapter_mock.return_value.load_cards = AsyncMock(return_value=([_card("AAPL")], {
            "persisted_recommendation_count": 1,
            "persisted_agent_insight_count": 1,
            "missing_evidence_count": 0,
        }))
        await service.run_v3()

    assert not hasattr(__import__("app.services.intelligence.v3.intel_v3_service", fromlist=["x"]), "RecommendationService")
    assert "intel_v3_evidence_source_summary" in caplog.text
    assert "generated_legacy_recommendations=false" in caplog.text
    assert "attempted_llm_calls=0" in caplog.text


def test_soft_violations_include_skeleton_and_ticker_prefix_counts():
    cards = [
        {"ticker": "AAPL", "action": "BUY", "conviction": "LOW", "why_text": "AAPL: same sentence skeleton.", "risk_text": "", "action_text": "", "evidence_text": "", "fit_text": "", "what_would_change_view": ""},
        {"ticker": "MSFT", "action": "BUY", "conviction": "LOW", "why_text": "MSFT: same sentence skeleton.", "risk_text": "", "action_text": "", "evidence_text": "", "fit_text": "", "what_would_change_view": ""},
        {"ticker": "NVDA", "action": "BUY", "conviction": "LOW", "why_text": "NVDA: same sentence skeleton.", "risk_text": "", "action_text": "", "evidence_text": "", "fit_text": "", "what_would_change_view": ""},
    ]
    cert = certify_snapshot_cards(cards)
    soft = cert["generic_copy_count"] + cert["duplicate_reason_count"] + cert["repeated_skeleton_count"] + cert["ticker_prefix_only_reason_count"] + cert["weak_buy_rationale_count"]
    assert cert["repeated_skeleton_count"] > 0
    assert cert["ticker_prefix_only_reason_count"] > 0
    assert soft > 0


def test_intel_v3_service_has_no_recommendation_service_import():
    import app.services.intelligence.v3.intel_v3_service as svc
    assert not hasattr(svc, "RecommendationService")


@pytest.mark.asyncio
async def test_adapter_stats_include_missing_recommendation_coverage():
    from app.services.intelligence.v3.read_only_evidence_adapter import ReadOnlyEvidenceAdapter

    adapter = ReadOnlyEvidenceAdapter.__new__(ReadOnlyEvidenceAdapter)
    adapter.user_id = uuid.UUID("00000000-0000-0000-0000-000000000111")

    class Chain:
        def __init__(self, data):
            self._data = data
        def select(self,*a,**k): return self
        def eq(self,*a,**k): return self
        def order(self,*a,**k): return self
        def limit(self,*a,**k): return self
        def in_(self,*a,**k): return self
        def execute(self): return SimpleNamespace(data=self._data)

    class Client:
        def table(self, name):
            if name == "recommendations":
                return Chain([{"ticker":"AAPL","action":"BUY","technical_signal":"BUY","conviction_score":0.8,"agent_run_id":"r1"}])
            if name == "positions":
                return Chain([{"ticker":"AAPL","name":"AAPL","category":"stock"},{"ticker":"MSFT","name":"MSFT","category":"stock"}])
            if name == "agent_runs":
                return Chain([{"id":"r1","status":"completed"}])
            if name == "agent_insights":
                return Chain([{"run_id":"r1","ticker":"AAPL","analyst_verdict":{"primary_driver":"AAPL driver","action":"BUY"},"analyst_confidence":0.9}])
            raise AssertionError(name)

    adapter.client = Client()
    cards, stats = await adapter.load_cards()
    assert len(cards) == 1
    assert stats["active_position_count"] == 2
    assert stats["persisted_recommendation_count"] == 1
    assert stats["missing_recommendation_count"] == 1
    assert stats["generated_legacy_recommendations"] is False
    assert stats["attempted_llm_calls"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Direct adapter path tests — PR 3B
# Prove that load_cards() itself synthesizes intel_read correctly from
# persisted analyst_verdict rows. These tests exercise the real adapter code
# via a fake Supabase client, not a mirrored test helper.
# ══════════════════════════════════════════════════════════════════════════════

def _make_fake_client(
    *,
    recommendations=None,
    positions=None,
    agent_runs=None,
    agent_insights=None,
    raise_on_tables=None,
):
    """Build a fake Supabase client that returns canned data per table."""
    class Chain:
        def __init__(self, data):
            self._data = data
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def order(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def in_(self, *a, **k): return self
        def execute(self): return SimpleNamespace(data=self._data)

    class Client:
        def table(self, name):
            if raise_on_tables and name in raise_on_tables:
                raise AssertionError(
                    f"Adapter must not query table '{name}' — "
                    f"research artifacts are locked safe_for_decision=FALSE"
                )
            if name == "recommendations":
                return Chain(recommendations or [])
            if name == "positions":
                return Chain(positions or [])
            if name == "agent_runs":
                return Chain(agent_runs or [])
            if name == "agent_insights":
                return Chain(agent_insights or [])
            raise AssertionError(f"Unexpected table: {name}")

    return Client()


def _make_adapter(client):
    from app.services.intelligence.v3.read_only_evidence_adapter import ReadOnlyEvidenceAdapter
    adapter = ReadOnlyEvidenceAdapter.__new__(ReadOnlyEvidenceAdapter)
    adapter.user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    adapter.client = client
    return adapter


class TestReadOnlyAdapterDirectPath:
    """Direct load_cards() tests with a fake Supabase client — PR 3B."""

    @pytest.mark.asyncio
    async def test_source_rich_verdict_synthesizes_three_trusted_signals(self):
        """Adapter produces 3 trusted signals when all three analyst dims are present."""
        client = _make_fake_client(
            recommendations=[{
                "id": "rec1", "ticker": "AAPL", "action": "BUY",
                "technical_signal": "BUY", "conviction_score": 0.9,
                "agent_run_id": "run1", "is_active": True,
                "created_at": "2026-05-16T12:00:00",
            }],
            positions=[{"ticker": "AAPL", "name": "Apple Inc", "category": "stock"}],
            agent_runs=[{"id": "run1", "finished_at": "2026-05-16T12:00:00",
                         "status": "completed", "allocation": {}}],
            agent_insights=[{
                "run_id": "run1", "ticker": "AAPL",
                "analyst_verdict": {
                    "primary_driver": "Cloud revenue accelerating ahead of consensus.",
                    "action_reason": "Adding at current levels captures structural tailwind.",
                    "key_drivers": ["Enterprise cloud adoption driving recurring ARR growth"],
                    "action": "BUY", "conviction_level": "HIGH",
                    "used_fallback": False, "risks": [],
                },
                "analyst_confidence": 0.9,
                "created_at": "2026-05-16T12:00:00",
            }],
        )
        adapter = _make_adapter(client)
        cards, stats = await adapter.load_cards()

        assert len(cards) == 1
        card = cards[0]
        assert card.intel_read is not None, "intel_read must be synthesized from analyst_verdict"
        assert card.intel_read["source"] == "analyst_verdict_synthesis"
        assert len(card.intel_read["trusted_signals"]) == 3
        assert "analyst_primary_driver" in card.intel_read["trusted_signals"]
        assert "analyst_action_rationale" in card.intel_read["trusted_signals"]
        assert "analyst_key_drivers" in card.intel_read["trusted_signals"]

    @pytest.mark.asyncio
    async def test_source_rich_verdict_data_quality_label_not_hardcoded(self):
        """data_quality_label is None when analyst_verdict does not include it."""
        client = _make_fake_client(
            recommendations=[{
                "id": "rec1", "ticker": "AAPL", "action": "BUY",
                "technical_signal": None, "conviction_score": 0.8,
                "agent_run_id": "run1", "is_active": True,
                "created_at": "2026-05-16T12:00:00",
            }],
            positions=[{"ticker": "AAPL", "name": "Apple Inc", "category": "stock"}],
            agent_runs=[{"id": "run1", "finished_at": "2026-05-16T12:00:00",
                         "status": "completed", "allocation": {}}],
            agent_insights=[{
                "run_id": "run1", "ticker": "AAPL",
                "analyst_verdict": {
                    "primary_driver": "Solid FCF generation.",
                    "action_reason": "Attractive entry.",
                    "key_drivers": ["Revenue diversification"],
                    "action": "BUY", "conviction_level": "HIGH",
                    "used_fallback": False, "risks": [],
                    # Note: no data_quality_label field — AnalystVerdict.to_dict() never writes it
                },
                "analyst_confidence": 0.9,
                "created_at": "2026-05-16T12:00:00",
            }],
        )
        adapter = _make_adapter(client)
        cards, stats = await adapter.load_cards()

        card = cards[0]
        assert card.data_quality_label is None, (
            "data_quality_label must be None when analyst_verdict omits it — "
            "hardcoded 'MEDIUM' fallback was the root cause of PARTIAL inflation"
        )

    @pytest.mark.asyncio
    async def test_source_rich_verdict_analyst_drivers_reads_key_drivers(self):
        """analyst_drivers on the card is populated from key_drivers field."""
        client = _make_fake_client(
            recommendations=[{
                "id": "rec1", "ticker": "MSFT", "action": "BUY",
                "technical_signal": None, "conviction_score": 0.8,
                "agent_run_id": "run1", "is_active": True,
                "created_at": "2026-05-16T12:00:00",
            }],
            positions=[{"ticker": "MSFT", "name": "Microsoft Corp", "category": "stock"}],
            agent_runs=[{"id": "run1", "finished_at": "2026-05-16T12:00:00",
                         "status": "completed", "allocation": {}}],
            agent_insights=[{
                "run_id": "run1", "ticker": "MSFT",
                "analyst_verdict": {
                    "primary_driver": "Azure growth leads cloud peers.",
                    "action_reason": "Attractive on pullbacks.",
                    "key_drivers": ["Azure ARR acceleration", "Copilot enterprise adoption"],
                    "drivers": [],  # old fallback writer key — should be ignored when key_drivers present
                    "action": "BUY", "conviction_level": "HIGH",
                    "used_fallback": False, "risks": [],
                },
                "analyst_confidence": 0.85,
                "created_at": "2026-05-16T12:00:00",
            }],
        )
        adapter = _make_adapter(client)
        cards, stats = await adapter.load_cards()

        card = cards[0]
        assert card.analyst_drivers == ["Azure ARR acceleration", "Copilot enterprise adoption"], (
            "analyst_drivers must come from key_drivers, not the empty drivers list"
        )

    @pytest.mark.asyncio
    async def test_source_rich_verdict_stats_observability(self):
        """Stats include mapped_existing_analyst_signal_count and distribution."""
        client = _make_fake_client(
            recommendations=[{
                "id": "rec1", "ticker": "NVDA", "action": "BUY",
                "technical_signal": None, "conviction_score": 0.85,
                "agent_run_id": "run1", "is_active": True,
                "created_at": "2026-05-16T12:00:00",
            }],
            positions=[{"ticker": "NVDA", "name": "NVIDIA Corp", "category": "stock"}],
            agent_runs=[{"id": "run1", "finished_at": "2026-05-16T12:00:00",
                         "status": "completed", "allocation": {}}],
            agent_insights=[{
                "run_id": "run1", "ticker": "NVDA",
                "analyst_verdict": {
                    "primary_driver": "Data center demand structurally elevated.",
                    "action_reason": "Secular AI tailwind intact.",
                    "key_drivers": ["Inference compute demand driving ASP expansion"],
                    "action": "BUY", "conviction_level": "HIGH",
                    "used_fallback": False, "risks": [],
                },
                "analyst_confidence": 0.9,
                "created_at": "2026-05-16T12:00:00",
            }],
        )
        adapter = _make_adapter(client)
        cards, stats = await adapter.load_cards()

        assert stats["mapped_existing_analyst_signal_count"] == 1
        assert stats["trusted_signal_count_distribution"][3] == 1
        assert stats["artifact_decision_safe_count"] == 0
        assert stats["artifact_suppressed_unsafe_count"] == 0

    @pytest.mark.asyncio
    async def test_used_fallback_true_yields_no_synthetic_intel_read(self):
        """used_fallback=True in analyst_verdict → no synthetic intel_read, bucket 0 increments."""
        client = _make_fake_client(
            recommendations=[{
                "id": "rec1", "ticker": "SNOW", "action": "HOLD",
                "technical_signal": None, "conviction_score": 0.4,
                "agent_run_id": "run1", "is_active": True,
                "created_at": "2026-05-16T12:00:00",
            }],
            positions=[{"ticker": "SNOW", "name": "Snowflake Inc", "category": "stock"}],
            agent_runs=[{"id": "run1", "finished_at": "2026-05-16T12:00:00",
                         "status": "completed", "allocation": {}}],
            agent_insights=[{
                "run_id": "run1", "ticker": "SNOW",
                "analyst_verdict": {
                    "primary_driver": "Strong revenue growth.",  # real text, but...
                    "action_reason": "Hold at current levels.",  # ...used_fallback overrides
                    "key_drivers": ["Consumption model drives ARR"],
                    "action": "HOLD", "conviction_level": "MEDIUM",
                    "used_fallback": True,  # analyst flagged thin data
                    "risks": [],
                },
                "analyst_confidence": 0.3,
                "created_at": "2026-05-16T12:00:00",
            }],
        )
        adapter = _make_adapter(client)
        cards, stats = await adapter.load_cards()

        card = cards[0]
        assert card.intel_read is None, (
            "used_fallback=True must suppress synthetic intel_read even when text fields have content"
        )
        assert stats["mapped_existing_analyst_signal_count"] == 0
        assert stats["trusted_signal_count_distribution"][0] == 1

    @pytest.mark.asyncio
    async def test_empty_analyst_insight_yields_no_intel_read_no_partial_inflation(self):
        """No analyst insight → card has no intel_read and no hardcoded data_quality_label."""
        client = _make_fake_client(
            recommendations=[{
                "id": "rec1", "ticker": "UNKN", "action": "HOLD",
                "technical_signal": None, "conviction_score": 0.5,
                "agent_run_id": "run99", "is_active": True,
                "created_at": "2026-05-16T12:00:00",
            }],
            positions=[{"ticker": "UNKN", "name": "Unknown Corp", "category": "stock"}],
            agent_runs=[{"id": "run1", "finished_at": "2026-05-16T10:00:00",
                         "status": "completed", "allocation": {}}],
            # No agent_insights for UNKN at all (different run_id or empty)
            agent_insights=[],
        )
        adapter = _make_adapter(client)
        cards, stats = await adapter.load_cards()

        card = cards[0]
        assert card.intel_read is None, "No analyst insight → no intel_read"
        assert card.data_quality_label is None, (
            "data_quality_label must be None — must NOT be hardcoded 'MEDIUM' "
            "(that was the PARTIAL inflation root cause)"
        )
        assert stats["stale_or_missing_source_count"] == 1

    @pytest.mark.asyncio
    async def test_legacy_drivers_field_populates_analyst_drivers(self):
        """Legacy fallback writer uses 'drivers' — adapter still reads it for analyst_drivers."""
        client = _make_fake_client(
            recommendations=[{
                "id": "rec1", "ticker": "JNJ", "action": "HOLD",
                "technical_signal": None, "conviction_score": 0.6,
                "agent_run_id": "run1", "is_active": True,
                "created_at": "2026-05-16T12:00:00",
            }],
            positions=[{"ticker": "JNJ", "name": "Johnson and Johnson", "category": "stock"}],
            agent_runs=[{"id": "run1", "finished_at": "2026-05-16T12:00:00",
                         "status": "completed", "allocation": {}}],
            agent_insights=[{
                "run_id": "run1", "ticker": "JNJ",
                "analyst_verdict": {
                    "primary_driver": "Steady healthcare fundamentals.",
                    "action_reason": None,
                    # Legacy fallback writer writes 'drivers', not 'key_drivers'
                    "drivers": ["Consistent dividend growth", "Defensive positioning"],
                    # 'key_drivers' absent — AnalystVerdict.to_dict() field not present
                    "action": "HOLD", "conviction_level": "MEDIUM",
                    "used_fallback": False, "risks": [],
                    "data_quality_label": "MEDIUM",  # explicitly written by legacy path
                },
                "analyst_confidence": 0.6,
                "created_at": "2026-05-16T12:00:00",
            }],
        )
        adapter = _make_adapter(client)
        cards, stats = await adapter.load_cards()

        card = cards[0]
        # analyst_drivers should fall back to 'drivers' when 'key_drivers' is absent
        assert card.analyst_drivers == ["Consistent dividend growth", "Defensive positioning"]
        # Only primary_driver counts as trusted signal (action_reason is None,
        # key_drivers absent so analyst_key_drivers not counted)
        assert card.intel_read is not None
        assert len(card.intel_read["trusted_signals"]) == 1
        assert "analyst_primary_driver" in card.intel_read["trusted_signals"]
        assert "analyst_key_drivers" not in card.intel_read["trusted_signals"]
        # data_quality_label is read from the explicit verdict field
        assert card.data_quality_label == "MEDIUM"

    @pytest.mark.asyncio
    async def test_matching_run_insight_wins_over_newer_fallback(self):
        """Adapter uses the insight from rec's agent_run_id, not a newer fallback run."""
        client = _make_fake_client(
            recommendations=[{
                "id": "rec1", "ticker": "AAPL", "action": "BUY",
                "technical_signal": "BUY", "conviction_score": 0.8,
                "agent_run_id": "run1",  # rec was built from run1
                "is_active": True, "created_at": "2026-05-16T10:00:00",
            }],
            positions=[{"ticker": "AAPL", "name": "Apple Inc", "category": "stock"}],
            agent_runs=[
                {"id": "run1", "finished_at": "2026-05-16T10:00:00",
                 "status": "completed", "allocation": {}},
                {"id": "run2", "finished_at": "2026-05-16T12:00:00",  # newer run
                 "status": "completed", "allocation": {}},
            ],
            agent_insights=[
                {
                    "run_id": "run1", "ticker": "AAPL",
                    "analyst_verdict": {
                        "primary_driver": "Insight from the matching run — this should win.",
                        "action_reason": "Add at current levels.",
                        "key_drivers": [], "action": "BUY",
                        "conviction_level": "HIGH", "used_fallback": False, "risks": [],
                    },
                    "analyst_confidence": 0.8,
                    "created_at": "2026-05-16T10:00:00",
                },
                {
                    "run_id": "run2", "ticker": "AAPL",
                    "analyst_verdict": {
                        "primary_driver": "Newer but unmatched insight — must NOT win.",
                        "action_reason": "Hold for now.",
                        "key_drivers": [], "action": "HOLD",
                        "conviction_level": "MEDIUM", "used_fallback": False, "risks": [],
                    },
                    "analyst_confidence": 0.7,
                    "created_at": "2026-05-16T12:00:00",
                },
            ],
        )
        adapter = _make_adapter(client)
        cards, stats = await adapter.load_cards()

        card = cards[0]
        assert card.primary_driver == "Insight from the matching run — this should win.", (
            "Matching-run insight must be preferred over a newer fallback insight"
        )
        assert stats["matched_agent_insight_by_recommendation_run_count"] == 1
        assert stats["fallback_agent_insight_by_ticker_count"] == 0

    @pytest.mark.asyncio
    async def test_research_artifacts_not_queried(self):
        """Adapter must never query research_artifacts or research_artifact_facts.

        The production schema (017_research_artifact_store_v1.sql) locks
        safe_for_decision=FALSE. No adapter path in PR 3B reads artifacts.
        """
        client = _make_fake_client(
            recommendations=[{
                "id": "rec1", "ticker": "AAPL", "action": "BUY",
                "technical_signal": None, "conviction_score": 0.8,
                "agent_run_id": "run1", "is_active": True,
                "created_at": "2026-05-16T12:00:00",
            }],
            positions=[{"ticker": "AAPL", "name": "Apple Inc", "category": "stock"}],
            agent_runs=[{"id": "run1", "finished_at": "2026-05-16T12:00:00",
                         "status": "completed", "allocation": {}}],
            agent_insights=[{
                "run_id": "run1", "ticker": "AAPL",
                "analyst_verdict": {
                    "primary_driver": "Strong fundamentals.",
                    "action_reason": None, "key_drivers": [],
                    "action": "BUY", "conviction_level": "MEDIUM",
                    "used_fallback": False, "risks": [],
                },
                "analyst_confidence": 0.7,
                "created_at": "2026-05-16T12:00:00",
            }],
            # Any call to these tables will raise an AssertionError.
            raise_on_tables={"research_artifacts", "research_artifact_facts"},
        )
        adapter = _make_adapter(client)
        # Must complete without raising — no artifact tables queried.
        cards, stats = await adapter.load_cards()
        assert stats["artifact_decision_safe_count"] == 0
        assert stats["artifact_suppressed_unsafe_count"] == 0
