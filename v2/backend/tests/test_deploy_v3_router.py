"""Tests — Deploy v3 read-only plan API (Stage 2.4A + Stage 2.5A).

Proves the following invariants:
  1. Router is registered in the app at /api/v1/deploy/v3/plan.
  2. Feature flag off → 404 with flag-off message.
  3. No Intel v3 snapshot → 404 with no_snapshot code.
  4. Snapshot present → valid response with plan_status, items, guardrail_summary, rollup, source.
  5. Rollup is included and contains plan_readiness_status.
  6. No sizing bundle provided → honest scaffold/not_ready behavior
     (dollar fields null, exact_dollar_math_evaluated=False).
  7. Route does NOT call legacy allocation engine.
  8. Intel v3 action is preserved read-only in item fields.
  9. Auth dependency is wired (get_current_user used).
  10. Sizing adapter is wired — source metadata exposes readiness gates.
  11. When adapter returns None (no portfolio snapshot), source reports sizing_bundle_provided=False.
  12. When adapter returns a bundle, source reports exact_dollar_ready, sizing_values_ready,
      target_allocation_ready, policy_ready, and suppression_reasons.
  13. Route does not call providers, LLM paths, or legacy allocation engine even after wiring.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Module-level autouse fixture ──────────────────────────────────────────────
# Patches the sizing adapter to return None by default for all existing tests.
# This keeps existing tests isolated from DB calls and preserves their behavior.
# New test classes that need a real bundle override this with their own patch.

@pytest.fixture(autouse=True)
def _patch_sizing_adapter():
    """Default: adapter returns None (no portfolio snapshot available)."""
    with patch(
        "app.routers.deploy_v3.build_sizing_bundle_from_persisted_data",
        new_callable=AsyncMock,
        return_value=None,
    ):
        yield

from app.services.intelligence.v3.decision_contracts import ActionV3, ConvictionV3
from app.services.intelligence.v3.snapshot_builder import build_snapshot
from app.services.intelligence.v3.decision_contracts import (
    AxisBand,
    DecisionOutputV3,
    FitBand,
    PriceBand,
    RiskBand,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_output(ticker: str, action: ActionV3 = ActionV3.HOLD) -> DecisionOutputV3:
    return DecisionOutputV3(
        ticker=ticker,
        action=action,
        conviction=ConvictionV3.MEDIUM,
        evidence_quality=AxisBand.OK,
        attractiveness=AxisBand.OK,
        price_context=PriceBand.FAIR,
        portfolio_fit=FitBand.ON_TARGET,
        risk_band=RiskBand.LOW,
        blockers=[],
        suppression_reasons={},
        rationale_plain_english="Signals support this position.",
        why_now="Evidence and fit support acting now.",
        why_not_now="Watch for evidence weakening.",
        source_signal_summary={},
        schema_version="v3.1",
    )


def _make_snapshot(*ticker_actions: tuple[str, ActionV3]) -> dict:
    decisions = [_make_output(t, a) for t, a in ticker_actions]
    metas = [
        {"ticker": t, "name": t, "category": "stock", "thesis_state": "intact"}
        for t, _ in ticker_actions
    ]
    return build_snapshot(
        run_id="test-run-deploy-v3",
        decisions=decisions,
        card_metas=metas,
        source_health={"status": "ok"},
        is_stale=False,
    )


def _mock_intel_service(snapshot_or_none):
    """Return a mock IntelV3Service instance that returns the given snapshot."""
    mock_svc = MagicMock()
    mock_svc.get_latest_snapshot = AsyncMock(return_value=snapshot_or_none)
    return mock_svc


# ── Gate A: Router registration ───────────────────────────────────────────────

class TestRouterRegistration:
    def test_deploy_v3_router_imported_and_registered(self):
        """deploy_v3 router must be importable and registered in the app."""
        from app.main import app
        from app.routers.deploy_v3 import router
        assert router is not None
        paths = [r.path for r in app.routes]
        assert "/api/v1/deploy/v3/plan" in paths

    def test_deploy_v3_plan_is_get_method(self):
        """The /deploy/v3/plan route must be a GET endpoint."""
        from app.main import app
        route = next(
            (r for r in app.routes if getattr(r, "path", "") == "/api/v1/deploy/v3/plan"),
            None,
        )
        assert route is not None
        assert "GET" in route.methods

    def test_legacy_allocation_plan_route_still_exists(self):
        """/allocation/plan legacy route must remain unaffected."""
        from app.main import app
        paths = [r.path for r in app.routes]
        assert "/api/v1/allocation/plan" in paths


# ── Gate B: Feature flag ──────────────────────────────────────────────────────

class TestFeatureFlag:
    def test_check_flag_raises_404_when_disabled(self):
        """_check_flag raises HTTPException 404 when feature flag is off."""
        from fastapi import HTTPException
        from app.routers.deploy_v3 import _check_flag
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(HTTPException) as exc_info:
                _check_flag()
            assert exc_info.value.status_code == 404

    def test_check_flag_does_not_raise_when_enabled(self):
        """_check_flag does not raise when feature flag is on."""
        from app.routers.deploy_v3 import _check_flag
        with patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}):
            _check_flag()  # must not raise


# ── Gate C: No snapshot → 404 ─────────────────────────────────────────────────

class TestNoSnapshot:
    def test_no_snapshot_returns_404_with_code(self):
        """When no Intel v3 snapshot exists, the endpoint raises 404 with no_snapshot code."""
        from fastapi import HTTPException
        from app.routers.deploy_v3 import get_deploy_v3_plan

        mock_user = MagicMock()
        mock_user.id = uuid.UUID("00000000-0000-0000-0000-000000000010")

        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
            patch(
                "app.routers.deploy_v3.IntelV3Service",
                return_value=_mock_intel_service(None),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(get_deploy_v3_plan(user=mock_user))

        assert exc_info.value.status_code == 404
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail.get("code") == "no_snapshot"


# ── Gate D: Snapshot present → valid plan response ────────────────────────────

class TestPlanResponse:
    def _get_plan(self, snapshot: dict) -> dict:
        """Call get_deploy_v3_plan with a mocked snapshot and return the result."""
        from app.routers.deploy_v3 import get_deploy_v3_plan

        mock_user = MagicMock()
        mock_user.id = uuid.UUID("00000000-0000-0000-0000-000000000020")

        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
            patch(
                "app.routers.deploy_v3.IntelV3Service",
                return_value=_mock_intel_service(snapshot),
            ),
        ):
            return asyncio.run(get_deploy_v3_plan(user=mock_user))

    def test_response_has_required_top_level_keys(self):
        """Response must include plan_status, items, guardrail_summary, rollup, source."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("MSFT", ActionV3.HOLD))
        result = self._get_plan(snap)

        required = {"plan_status", "snapshot_id", "run_id", "schema_version",
                    "items", "guardrail_summary", "rollup", "source"}
        missing = required - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_plan_status_is_valid_string(self):
        """plan_status must be a non-empty string (one of the DeployPlanStatus values)."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = self._get_plan(snap)
        assert isinstance(result["plan_status"], str)
        assert result["plan_status"] in {"SCAFFOLD", "SUPPRESSED", "HOLD_ONLY"}

    def test_items_list_matches_snapshot_card_count(self):
        """items list length must equal the number of holdings in the snapshot."""
        snap = _make_snapshot(
            ("AAPL", ActionV3.BUY),
            ("MSFT", ActionV3.HOLD),
            ("GOOG", ActionV3.TRIM),
        )
        result = self._get_plan(snap)
        assert len(result["items"]) == 3

    def test_intel_action_preserved_in_items(self):
        """intel_action in each item must match the original Intel v3 card action."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("MSFT", ActionV3.HOLD))
        result = self._get_plan(snap)
        actions_by_ticker = {item["ticker"]: item["intel_action"] for item in result["items"]}
        assert actions_by_ticker.get("AAPL") == "BUY"
        assert actions_by_ticker.get("MSFT") == "HOLD"

    def test_guardrail_summary_included(self):
        """guardrail_summary must be present and include total_items."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("MSFT", ActionV3.HOLD))
        result = self._get_plan(snap)
        gs = result["guardrail_summary"]
        assert gs is not None
        assert "total_items" in gs
        assert gs["total_items"] == 2


# ── Gate E: Rollup included ───────────────────────────────────────────────────

class TestRollupIncluded:
    def _get_plan(self, snapshot: dict) -> dict:
        from app.routers.deploy_v3 import get_deploy_v3_plan
        mock_user = MagicMock()
        mock_user.id = uuid.UUID("00000000-0000-0000-0000-000000000030")
        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
            patch(
                "app.routers.deploy_v3.IntelV3Service",
                return_value=_mock_intel_service(snapshot),
            ),
        ):
            return asyncio.run(get_deploy_v3_plan(user=mock_user))

    def test_rollup_is_present_in_response(self):
        """rollup block must be present in the response."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = self._get_plan(snap)
        assert result["rollup"] is not None

    def test_rollup_contains_plan_readiness_status(self):
        """rollup must include plan_readiness_status."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = self._get_plan(snap)
        rollup = result["rollup"]
        assert "plan_readiness_status" in rollup
        assert isinstance(rollup["plan_readiness_status"], str)

    def test_rollup_contains_total_items(self):
        """rollup.total_items must equal the number of items in the plan."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("MSFT", ActionV3.HOLD))
        result = self._get_plan(snap)
        assert result["rollup"]["total_items"] == 2

    def test_rollup_contains_counts_by_final_status(self):
        """rollup must include counts_by_final_actionability_status."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = self._get_plan(snap)
        rollup = result["rollup"]
        assert "counts_by_final_actionability_status" in rollup
        assert isinstance(rollup["counts_by_final_actionability_status"], dict)

    def test_all_hold_snapshot_gives_all_informational_rollup(self):
        """A snapshot with only HOLD cards must give plan_readiness_status=all_informational."""
        snap = _make_snapshot(("MSFT", ActionV3.HOLD), ("GOOG", ActionV3.HOLD))
        result = self._get_plan(snap)
        assert result["rollup"]["plan_readiness_status"] == "all_informational"


# ── Gate F: No sizing bundle → honest scaffold behavior ──────────────────────

class TestNoSizingBundleHonesty:
    def _get_plan(self, snapshot: dict) -> dict:
        from app.routers.deploy_v3 import get_deploy_v3_plan
        mock_user = MagicMock()
        mock_user.id = uuid.UUID("00000000-0000-0000-0000-000000000040")
        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
            patch(
                "app.routers.deploy_v3.IntelV3Service",
                return_value=_mock_intel_service(snapshot),
            ),
        ):
            return asyncio.run(get_deploy_v3_plan(user=mock_user))

    def test_dollar_fields_are_null_when_no_sizing_bundle(self):
        """All items must have null recommended_dollar_amount when no sizing bundle provided."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY), ("NVDA", ActionV3.TRIM))
        result = self._get_plan(snap)
        for item in result["items"]:
            assert item["recommended_dollar_amount"] is None, (
                f"{item['ticker']} has non-null dollar amount without sizing bundle"
            )

    def test_exact_dollar_math_not_evaluated(self):
        """guardrail_summary.exact_dollar_math_evaluated must be False without sizing bundle."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = self._get_plan(snap)
        assert result["guardrail_summary"]["exact_dollar_math_evaluated"] is False

    def test_source_metadata_reflects_no_sizing_bundle(self):
        """source block must report sizing_bundle_provided=False."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = self._get_plan(snap)
        assert result["source"]["sizing_bundle_provided"] is False
        assert result["source"]["intel_source"] == "INTEL_V3"

    def test_buy_item_final_status_is_not_ready_without_sizing(self):
        """BUY ACTIONABLE_CANDIDATE without sizing bundle must have not_ready final status."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = self._get_plan(snap)
        buy_items = [i for i in result["items"] if i["intel_action"] == "BUY"]
        assert buy_items, "Expected at least one BUY item"
        for item in buy_items:
            assert item["final_actionability_status"] == "not_ready", (
                f"Expected not_ready for {item['ticker']}, got {item['final_actionability_status']}"
            )


# ── Gate G: Legacy allocation engine not called ───────────────────────────────

class TestLegacyEngineNotCalled:
    def test_allocation_engine_not_imported_by_deploy_v3_router(self):
        """deploy_v3 router must not import from legacy allocation engine modules."""
        import inspect
        import app.routers.deploy_v3 as deploy_v3_module

        source = inspect.getsource(deploy_v3_module)
        forbidden = [
            "allocation_engine",
            "adaptive_deployment",
            "deployment_engine",
            "regime_engine",
            "RecommendationService",
            "build_allocation_plan",
        ]
        for name in forbidden:
            assert name not in source, (
                f"deploy_v3 router must not reference legacy engine: '{name}'"
            )

    def test_get_plan_does_not_call_recommendation_service(self):
        """get_deploy_v3_plan must never instantiate RecommendationService."""
        from app.routers.deploy_v3 import get_deploy_v3_plan
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        mock_user = MagicMock()
        mock_user.id = uuid.UUID("00000000-0000-0000-0000-000000000050")

        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
            patch(
                "app.routers.deploy_v3.IntelV3Service",
                return_value=_mock_intel_service(snap),
            ),
            patch("app.services.recommendation_engine.RecommendationService") as mock_rec,
        ):
            asyncio.run(get_deploy_v3_plan(user=mock_user))
            mock_rec.assert_not_called()


# ── Gate H: Source metadata ───────────────────────────────────────────────────

class TestSourceMetadata:
    def _get_plan(self, snapshot: dict) -> dict:
        from app.routers.deploy_v3 import get_deploy_v3_plan
        mock_user = MagicMock()
        mock_user.id = uuid.UUID("00000000-0000-0000-0000-000000000060")
        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
            patch(
                "app.routers.deploy_v3.IntelV3Service",
                return_value=_mock_intel_service(snapshot),
            ),
        ):
            return asyncio.run(get_deploy_v3_plan(user=mock_user))

    def test_source_block_has_required_fields(self):
        """source block must include intel_source, sizing_bundle_provided, and note."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = self._get_plan(snap)
        source = result["source"]
        assert "intel_source" in source
        assert "sizing_bundle_provided" in source
        assert "note" in source

    def test_source_intel_source_is_intel_v3(self):
        """source.intel_source must always be 'INTEL_V3'."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = self._get_plan(snap)
        assert result["source"]["intel_source"] == "INTEL_V3"

    def test_snapshot_id_and_run_id_in_response(self):
        """snapshot_id and run_id in response must match the Intel v3 snapshot."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        result = self._get_plan(snap)
        assert result["snapshot_id"] == snap["snapshot_id"]
        assert result["run_id"] == snap["run_id"]


# ── Gate I: Adapter wired — source metadata exposes readiness gates ───────────

class TestAdapterWiredSourceMetadata:
    """Stage 2.5A: source block exposes sizing bundle readiness gates."""

    def _get_plan_with_bundle(self, snapshot: dict, bundle) -> dict:
        """Call the endpoint with a specific sizing bundle mock."""
        from app.routers.deploy_v3 import get_deploy_v3_plan
        mock_user = MagicMock()
        mock_user.id = uuid.UUID("00000000-0000-0000-0000-000000000070")
        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
            patch(
                "app.routers.deploy_v3.IntelV3Service",
                return_value=_mock_intel_service(snapshot),
            ),
            patch(
                "app.routers.deploy_v3.build_sizing_bundle_from_persisted_data",
                new_callable=AsyncMock,
                return_value=bundle,
            ),
        ):
            return asyncio.run(get_deploy_v3_plan(user=mock_user))

    def _make_bundle(self, exact_dollar_ready: bool):
        """Build a mock sizing bundle with controllable readiness state."""
        from app.services.deploy.deploy_sizing_contracts import (
            DeploySizingInputBundle,
            DeployCashInput,
            DeployPortfolioSizingInput,
            DeployPositionSizingInput,
            DeploySizingPolicyPlaceholder,
            DeployTargetAllocationInput,
            DeploySizingTrustStatus,
        )
        from app.services.deploy.deploy_target_allocation_bridge import certify_target_allocation
        from app.services.deploy.deploy_policy_bridge import certify_sizing_policy

        if exact_dollar_ready:
            # Build a fully certified bundle
            cash = DeployCashInput(
                available_cash_usd=5_000.0,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
                source_label="portfolio_snapshots:abc12345",
            )
            portfolio = DeployPortfolioSizingInput(
                total_portfolio_value_usd=100_000.0,
                trust_status=DeploySizingTrustStatus.CERTIFIED,
                source_label="portfolio_snapshots:abc12345",
            )
            positions = {
                "AAPL": DeployPositionSizingInput(
                    ticker="AAPL",
                    current_market_value_usd=60_000.0,
                    current_weight=0.6,
                    trust_status=DeploySizingTrustStatus.CERTIFIED,
                    source_label="portfolio_snapshots:abc12345",
                )
            }
            target_allocations = {
                "AAPL": certify_target_allocation("AAPL", 1.0, "target_allocations_table")
            }
            policy = certify_sizing_policy(1.0, "WHOLE_DOLLAR")
            return DeploySizingInputBundle(
                cash=cash,
                portfolio=portfolio,
                positions=positions,
                target_allocations=target_allocations,
                policy=policy,
            )
        else:
            # Build a bundle where sizing values are missing
            cash = DeployCashInput(
                available_cash_usd=None,
                trust_status=DeploySizingTrustStatus.MISSING,
                source_label="not_provided",
            )
            portfolio = DeployPortfolioSizingInput(
                total_portfolio_value_usd=None,
                trust_status=DeploySizingTrustStatus.MISSING,
                source_label="not_provided",
            )
            return DeploySizingInputBundle(cash=cash, portfolio=portfolio)

    def test_source_has_sizing_bundle_provided_true_when_adapter_returns_bundle(self):
        """source.sizing_bundle_provided is True when adapter returns a bundle."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = self._make_bundle(exact_dollar_ready=False)
        result = self._get_plan_with_bundle(snap, bundle)
        assert result["source"]["sizing_bundle_provided"] is True

    def test_source_exposes_exact_dollar_ready_gate(self):
        """source must include exact_dollar_ready reflecting bundle state."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = self._make_bundle(exact_dollar_ready=False)
        result = self._get_plan_with_bundle(snap, bundle)
        source = result["source"]
        assert "exact_dollar_ready" in source
        assert source["exact_dollar_ready"] is False

    def test_source_exposes_sizing_values_ready_gate(self):
        """source must include sizing_values_ready."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = self._make_bundle(exact_dollar_ready=False)
        result = self._get_plan_with_bundle(snap, bundle)
        assert "sizing_values_ready" in result["source"]

    def test_source_exposes_target_allocation_ready_gate(self):
        """source must include target_allocation_ready."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = self._make_bundle(exact_dollar_ready=False)
        result = self._get_plan_with_bundle(snap, bundle)
        assert "target_allocation_ready" in result["source"]

    def test_source_exposes_policy_ready_gate(self):
        """source must include policy_ready."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = self._make_bundle(exact_dollar_ready=False)
        result = self._get_plan_with_bundle(snap, bundle)
        assert "policy_ready" in result["source"]

    def test_source_exposes_suppression_reasons(self):
        """source must include suppression_reasons list."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = self._make_bundle(exact_dollar_ready=False)
        result = self._get_plan_with_bundle(snap, bundle)
        source = result["source"]
        assert "suppression_reasons" in source
        assert isinstance(source["suppression_reasons"], list)

    def test_source_exact_dollar_ready_true_when_bundle_certified(self):
        """source.exact_dollar_ready is True when bundle is fully certified."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = self._make_bundle(exact_dollar_ready=True)
        result = self._get_plan_with_bundle(snap, bundle)
        assert result["source"]["exact_dollar_ready"] is True

    def test_source_intel_source_always_intel_v3_with_bundle(self):
        """source.intel_source remains INTEL_V3 regardless of sizing bundle state."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = self._make_bundle(exact_dollar_ready=True)
        result = self._get_plan_with_bundle(snap, bundle)
        assert result["source"]["intel_source"] == "INTEL_V3"

    def test_source_note_present_when_bundle_not_exact_dollar_ready(self):
        """source.note is present when bundle is provided but not exact_dollar_ready."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = self._make_bundle(exact_dollar_ready=False)
        result = self._get_plan_with_bundle(snap, bundle)
        assert "note" in result["source"]
        assert "suppression_reasons" in result["source"]["note"].lower() or \
               "not yet ready" in result["source"]["note"].lower()

    def test_source_note_certified_when_exact_dollar_ready(self):
        """source.note says 'certified' when exact_dollar_ready is True."""
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        bundle = self._make_bundle(exact_dollar_ready=True)
        result = self._get_plan_with_bundle(snap, bundle)
        assert "certified" in result["source"]["note"].lower()

    def test_adapter_none_gives_sizing_bundle_provided_false(self):
        """When adapter returns None (autouse fixture), source.sizing_bundle_provided is False."""
        # This test relies on the autouse _patch_sizing_adapter returning None.
        from app.routers.deploy_v3 import get_deploy_v3_plan
        mock_user = MagicMock()
        mock_user.id = uuid.UUID("00000000-0000-0000-0000-000000000080")
        snap = _make_snapshot(("AAPL", ActionV3.BUY))
        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
            patch(
                "app.routers.deploy_v3.IntelV3Service",
                return_value=_mock_intel_service(snap),
            ),
        ):
            result = asyncio.run(get_deploy_v3_plan(user=mock_user))
        assert result["source"]["sizing_bundle_provided"] is False
        assert result["source"]["exact_dollar_ready"] is False
        assert result["source"]["intel_source"] == "INTEL_V3"

    def test_router_does_not_call_providers_or_legacy_engine_after_adapter_wiring(self):
        """deploy_v3 router source must not reference providers or legacy engine modules."""
        import inspect
        import app.routers.deploy_v3 as deploy_v3_module
        source = inspect.getsource(deploy_v3_module)
        forbidden = [
            "allocation_engine",
            "adaptive_deployment",
            "deployment_engine",
            "PriceService",
            "fetch_prices",
            "RecommendationService",
            "build_allocation_plan",
        ]
        for name in forbidden:
            assert name not in source, (
                f"deploy_v3 router must not reference legacy/provider module: '{name}'"
            )


# ── Readiness endpoint tests (Stage 2.5D) ─────────────────────────────────────


class TestReadinessEndpoint:
    """Tests for GET /api/v1/deploy/v3/readiness."""

    def _get_readiness(self, diagnostic_return: dict) -> dict:
        """Call the readiness endpoint with a mocked diagnostic builder."""
        from app.routers.deploy_v3 import get_deploy_v3_readiness
        mock_user = MagicMock()
        mock_user.id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
            patch(
                "app.routers.deploy_v3.build_readiness_diagnostic",
                new_callable=AsyncMock,
                return_value=diagnostic_return,
            ) as mock_diag,
        ):
            result = asyncio.run(get_deploy_v3_readiness(user=mock_user))
        return result

    def test_route_registered_at_readiness_path(self):
        """Route /api/v1/deploy/v3/readiness is registered in the app."""
        from app.main import app
        routes = [r.path for r in app.routes]
        assert "/api/v1/deploy/v3/readiness" in routes

    def test_route_method_is_get(self):
        """Readiness route accepts GET requests."""
        from app.main import app
        for route in app.routes:
            if getattr(route, "path", None) == "/api/v1/deploy/v3/readiness":
                assert "GET" in route.methods
                return
        pytest.fail("Route /api/v1/deploy/v3/readiness not found")

    def test_flag_off_returns_404(self):
        """Feature flag disabled → 404 with flag-off message."""
        from fastapi import HTTPException
        from app.routers.deploy_v3 import get_deploy_v3_readiness
        mock_user = MagicMock()
        mock_user.id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "false"}),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(get_deploy_v3_readiness(user=mock_user))
        assert exc_info.value.status_code == 404

    def test_flag_on_calls_build_readiness_diagnostic_with_user_id(self):
        """Flag enabled → build_readiness_diagnostic is called with user_id."""
        from app.routers.deploy_v3 import get_deploy_v3_readiness
        mock_user = MagicMock()
        user_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        mock_user.id = user_id
        diagnostic_return = {
            "exact_dollar_ready": False,
            "next_required_action": "Create a fresh portfolio snapshot to begin.",
        }
        with (
            patch.dict(os.environ, {"INTEL_V3_VISIBLE_SNAPSHOT_ENABLED": "true"}),
            patch(
                "app.routers.deploy_v3.build_readiness_diagnostic",
                new_callable=AsyncMock,
                return_value=diagnostic_return,
            ) as mock_diag,
        ):
            asyncio.run(get_deploy_v3_readiness(user=mock_user))
        mock_diag.assert_called_once()
        call_kwargs = mock_diag.call_args
        assert call_kwargs.kwargs.get("user_id") == user_id or (
            call_kwargs.args and call_kwargs.args[0] == user_id
        )

    def test_response_returns_diagnostic_dict(self):
        """Response is the diagnostic dict returned by build_readiness_diagnostic."""
        diagnostic = {
            "exact_dollar_ready": False,
            "sizing_values_ready": False,
            "target_allocation_ready": False,
            "policy_ready": False,
            "snapshot": {"present": False, "status": "missing"},
            "next_required_action": "Create a fresh portfolio snapshot to begin.",
        }
        result = self._get_readiness(diagnostic)
        assert result["exact_dollar_ready"] is False
        assert result["next_required_action"] == "Create a fresh portfolio snapshot to begin."
        assert result["snapshot"]["status"] == "missing"

    def test_router_source_has_no_providers_llm_broker_or_legacy(self):
        """deploy_v3 router must not reference providers, LLM, broker, or legacy engine."""
        import inspect
        import app.routers.deploy_v3 as deploy_v3_module
        source = inspect.getsource(deploy_v3_module)
        forbidden = [
            "allocation_engine",
            "adaptive_deployment",
            "deployment_engine",
            "PriceService",
            "fetch_prices",
            "RecommendationService",
            "build_allocation_plan",
            "openai",
            "anthropic",
            "broker",
        ]
        for name in forbidden:
            assert name not in source, (
                f"deploy_v3 router must not reference forbidden module: '{name}'"
            )

    def test_auth_wired_consistently_with_plan_endpoint(self):
        """Readiness endpoint uses get_current_user, consistent with the plan endpoint."""
        import inspect
        from app.routers import deploy_v3 as deploy_v3_module
        source = inspect.getsource(deploy_v3_module)
        readiness_fn_src = inspect.getsource(deploy_v3_module.get_deploy_v3_readiness)
        assert "get_current_user" in readiness_fn_src
        assert "AuthenticatedUser" in readiness_fn_src or "Depends" in readiness_fn_src
