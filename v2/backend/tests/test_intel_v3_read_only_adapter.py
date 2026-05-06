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
