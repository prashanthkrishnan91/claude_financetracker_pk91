"""Phase 10 — Evidence Source Registry v1 / Multi-Lane Governance v1 tests.

Acceptance criteria verified by this file:

 1. Backend-only registry/governance contract exists with contract version.
 2. All eleven evidence lanes are represented in the registry.
 3. Current SEC metric lane (sec_companyfacts_v1) is represented and ACTIVE
    (Phase 11.1 registry lifecycle promotion).
 4. Finance-agent/research-artifact outputs are represented as non-authoritative
    (decision_input_eligible=False, trust_tier=LLM_GENERATED).
 5. Registry distinguishes decision_input_eligible from explanation_only.
 6. Registry includes trust tier, freshness SLA, failure behavior,
    corroboration requirement, numeric authority, audit URL requirement,
    provider/adapter identity, lifecycle/status, and notes/constraints.
 7. No source is decision_input_eligible without explicit governance fields
    set (i.e., corroboration_required or trust_tier checked).
 8. Finance-agent/research-artifact outputs (RESEARCH_ARTIFACT lane /
    LLM_GENERATED trust tier) do not have decision_input_eligible=True
    and do not have numeric_authority=True.
 9. Open-web/news sources (OPEN_WEB trust tier) require
    corroboration_required=True if decision_input_eligible is ever True.
    (Current: decision_input_eligible=False, corroboration_required=True.)
10. No visible decision path (decision_policy_v1, IntelV3Service, snapshot_builder,
    intel_v3_service) imports or calls the new registry module.
11. Diagnostics build_registry_summary() always returns safe_for_decision=False.
12. Diagnostics build_registry_summary() always returns
    visible_snapshot_unchanged=True.
13. No UI changes — registry module has no frontend imports.
14. No SQL — registry module has no DB/supabase imports.
15. No provider/LLM calls — registry module has no external IO imports.

Architecture invariants verified:
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER imports IntelV3Service, recommendation_engine, or any frontend path.
    - NEVER sets safe_for_decision=True.
    - safe_for_decision always False in diagnostics summary.
    - visible_snapshot_unchanged always True in diagnostics summary.
    - LLM_GENERATED trust tier sources are never decision_input_eligible.
    - explanation_only=True sources are never decision_input_eligible=True.
    - OPEN_WEB sources with decision_input_eligible=True must have
      corroboration_required=True (invariant; currently none are eligible).
    - RESEARCH_ARTIFACT lane sources are never decision_input_eligible.

All tests use in-memory fixtures — no Supabase dependency.
"""
from __future__ import annotations

import ast
import pathlib
from typing import List

import pytest

from app.services.intelligence.v3.evidence_source_registry import (
    EVIDENCE_SOURCE_REGISTRY,
    EVIDENCE_SOURCE_REGISTRY_CONTRACT_VERSION,
    ALL_EVIDENCE_LANES,
    EvidenceLane,
    EvidenceSourceDefinition,
    FailureBehavior,
    LifecycleStatus,
    SourceType,
    TrustTier,
    build_registry_summary,
    get_active_decision_eligible_sources,
    get_all_sources,
    get_decision_eligible_sources,
    get_explanation_only_sources,
    get_lanes_represented,
    get_sources_by_lane,
    get_sources_requiring_corroboration,
)

# ── Module paths for static import analysis ──────────────────────────────────

_REGISTRY_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/v3/evidence_source_registry.py"
)

_DECISION_POLICY_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/v3/decision_policy_v1.py"
)

_INTEL_V3_SERVICE_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/v3/intel_v3_service.py"
)

_SNAPSHOT_BUILDER_MODULE = (
    pathlib.Path(__file__).parent.parent
    / "app/services/intelligence/v3/snapshot_builder.py"
)


def _load_module_source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _get_imports_from_source(source: str) -> list[str]:
    """Return all module names referenced in import statements."""
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


# ── AC 1: Contract version ────────────────────────────────────────────────────

class TestContractVersion:
    def test_contract_version_exists(self) -> None:
        assert EVIDENCE_SOURCE_REGISTRY_CONTRACT_VERSION == "phase10_v1"

    def test_registry_is_non_empty(self) -> None:
        assert len(EVIDENCE_SOURCE_REGISTRY) > 0

    def test_registry_indexed_by_source_id(self) -> None:
        for source_id, defn in EVIDENCE_SOURCE_REGISTRY.items():
            assert defn.source_id == source_id


# ── AC 2: All 11 evidence lanes represented ───────────────────────────────────

class TestAllLanesRepresented:
    def test_eleven_lanes_defined_in_enum(self) -> None:
        assert len(EvidenceLane) == 11

    def test_all_lanes_have_at_least_one_source(self) -> None:
        represented = get_lanes_represented()
        for lane in EvidenceLane:
            assert lane in represented, f"Lane {lane} has no source in registry"

    def test_all_lanes_represented_flag(self) -> None:
        assert get_lanes_represented() == ALL_EVIDENCE_LANES

    def test_build_registry_summary_confirms_all_lanes(self) -> None:
        summary = build_registry_summary()
        assert summary["all_lanes_represented"] is True
        assert summary["total_lanes"] == 11
        assert summary["lanes_represented_count"] == 11

    @pytest.mark.parametrize("lane", list(EvidenceLane))
    def test_each_lane_has_source(self, lane: EvidenceLane) -> None:
        sources = get_sources_by_lane(lane)
        assert len(sources) >= 1, f"Lane {lane.value} has no governed source"


# ── AC 3: SEC fundamentals lane — shadow/planned, not decision-consuming ──────

class TestSecFundamentalsLane:
    def test_sec_companyfacts_v1_exists(self) -> None:
        assert "sec_companyfacts_v1" in EVIDENCE_SOURCE_REGISTRY

    def test_sec_companyfacts_lane_is_sec_company_fundamentals(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        assert defn.lane == EvidenceLane.SEC_COMPANY_FUNDAMENTALS

    def test_sec_companyfacts_lifecycle_is_active(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        # Phase 11.1: promoted from PLANNED to ACTIVE after Phase 11 production validation.
        assert defn.lifecycle_status == LifecycleStatus.ACTIVE

    def test_sec_companyfacts_in_active_decision_eligible(self) -> None:
        active_eligible = {s.source_id for s in get_active_decision_eligible_sources()}
        assert "sec_companyfacts_v1" in active_eligible

    def test_sec_companyfacts_trust_tier_primary_hard_data(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        assert defn.trust_tier == TrustTier.PRIMARY_HARD_DATA

    def test_sec_companyfacts_audit_url_required(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        assert defn.audit_url_required is True

    def test_sec_companyfacts_numeric_authority(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        assert defn.numeric_authority is True

    def test_sec_companyfacts_not_explanation_only(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["sec_companyfacts_v1"]
        assert defn.explanation_only is False


# ── AC 4: Finance-agent/research-artifact non-authoritative ──────────────────

class TestResearchArtifactNonAuthoritative:
    def test_research_artifact_llm_v1_exists(self) -> None:
        assert "research_artifact_llm_v1" in EVIDENCE_SOURCE_REGISTRY

    def test_research_artifact_trust_tier_llm_generated(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["research_artifact_llm_v1"]
        assert defn.trust_tier == TrustTier.LLM_GENERATED

    def test_research_artifact_not_decision_input_eligible(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["research_artifact_llm_v1"]
        assert defn.decision_input_eligible is False

    def test_research_artifact_not_numeric_authority(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["research_artifact_llm_v1"]
        assert defn.numeric_authority is False

    def test_research_artifact_explanation_only(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["research_artifact_llm_v1"]
        assert defn.explanation_only is True

    def test_research_artifact_corroboration_required(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["research_artifact_llm_v1"]
        assert defn.corroboration_required is True

    def test_research_artifact_lane_is_research_artifact(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["research_artifact_llm_v1"]
        assert defn.lane == EvidenceLane.RESEARCH_ARTIFACT

    def test_no_llm_generated_source_is_decision_eligible(self) -> None:
        for source in get_all_sources():
            if source.trust_tier == TrustTier.LLM_GENERATED:
                assert source.decision_input_eligible is False, (
                    f"LLM_GENERATED source {source.source_id} must not be "
                    "decision_input_eligible"
                )

    def test_no_research_artifact_lane_source_is_decision_eligible(self) -> None:
        for source in get_sources_by_lane(EvidenceLane.RESEARCH_ARTIFACT):
            assert source.decision_input_eligible is False, (
                f"RESEARCH_ARTIFACT lane source {source.source_id} must not be "
                "decision_input_eligible"
            )

    def test_no_llm_generated_source_has_numeric_authority(self) -> None:
        for source in get_all_sources():
            if source.trust_tier == TrustTier.LLM_GENERATED:
                assert source.numeric_authority is False, (
                    f"LLM_GENERATED source {source.source_id} must not have "
                    "numeric_authority"
                )


# ── AC 5: decision_input_eligible vs explanation_only distinction ─────────────

class TestDecisionEligibleVsExplanationOnly:
    def test_explanation_only_sources_not_decision_eligible(self) -> None:
        for source in get_explanation_only_sources():
            assert source.decision_input_eligible is False, (
                f"explanation_only source {source.source_id} must not be "
                "decision_input_eligible"
            )

    def test_decision_eligible_sources_not_explanation_only(self) -> None:
        for source in get_decision_eligible_sources():
            assert source.explanation_only is False, (
                f"decision_input_eligible source {source.source_id} must not "
                "be explanation_only"
            )

    def test_registry_summary_counts_are_consistent(self) -> None:
        summary = build_registry_summary()
        assert summary["decision_eligible_source_count"] == len(get_decision_eligible_sources())
        assert summary["explanation_only_source_count"] == len(get_explanation_only_sources())


# ── AC 6: Governance fields completeness ─────────────────────────────────────

class TestGovernanceFieldsCompleteness:
    @pytest.mark.parametrize("source_id", list(EVIDENCE_SOURCE_REGISTRY.keys()))
    def test_every_source_has_required_governance_fields(self, source_id: str) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY[source_id]
        assert isinstance(defn.trust_tier, TrustTier), f"{source_id}: missing trust_tier"
        assert isinstance(defn.failure_behavior, FailureBehavior), f"{source_id}: missing failure_behavior"
        assert isinstance(defn.corroboration_required, bool), f"{source_id}: missing corroboration_required"
        assert isinstance(defn.numeric_authority, bool), f"{source_id}: missing numeric_authority"
        assert isinstance(defn.audit_url_required, bool), f"{source_id}: missing audit_url_required"
        assert defn.provider_adapter, f"{source_id}: empty provider_adapter"
        assert isinstance(defn.lifecycle_status, LifecycleStatus), f"{source_id}: missing lifecycle_status"
        # freshness_sla_hours may be None (user-defined)
        assert defn.freshness_sla_hours is None or isinstance(defn.freshness_sla_hours, int), (
            f"{source_id}: freshness_sla_hours must be int or None"
        )

    @pytest.mark.parametrize("source_id", list(EVIDENCE_SOURCE_REGISTRY.keys()))
    def test_every_source_has_display_name_and_description(self, source_id: str) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY[source_id]
        assert defn.display_name, f"{source_id}: empty display_name"
        assert defn.description, f"{source_id}: empty description"


# ── AC 7: No source decision-eligible without explicit governance ─────────────

class TestDecisionEligibilityGovernance:
    def test_all_decision_eligible_sources_have_explicit_lifecycle(self) -> None:
        for source in get_decision_eligible_sources():
            assert source.lifecycle_status in (
                LifecycleStatus.ACTIVE,
                LifecycleStatus.PLANNED,
            ), (
                f"{source.source_id}: decision_input_eligible sources must be "
                "ACTIVE or PLANNED, not DEPRECATED/BLOCKED"
            )

    def test_all_decision_eligible_sources_have_failure_behavior_not_ignore(self) -> None:
        for source in get_decision_eligible_sources():
            assert source.failure_behavior != FailureBehavior.IGNORE, (
                f"{source.source_id}: decision_input_eligible source must not "
                "use IGNORE failure behavior — decisions must degrade or suppress"
            )

    def test_blocked_sources_not_decision_eligible(self) -> None:
        for source in get_all_sources():
            if source.lifecycle_status == LifecycleStatus.BLOCKED:
                assert source.decision_input_eligible is False, (
                    f"BLOCKED source {source.source_id} must not be decision_input_eligible"
                )


# ── AC 8: Finance-agent outputs do not own final actions or Deploy sizing ─────

class TestFinanceAgentOutputsNonAuthoritative:
    def test_research_artifact_lane_no_decision_eligible(self) -> None:
        for source in get_sources_by_lane(EvidenceLane.RESEARCH_ARTIFACT):
            assert not source.decision_input_eligible

    def test_research_artifact_lane_all_explanation_only(self) -> None:
        for source in get_sources_by_lane(EvidenceLane.RESEARCH_ARTIFACT):
            assert source.explanation_only

    def test_research_artifact_lane_all_require_corroboration(self) -> None:
        for source in get_sources_by_lane(EvidenceLane.RESEARCH_ARTIFACT):
            assert source.corroboration_required

    def test_research_artifact_lane_no_numeric_authority(self) -> None:
        for source in get_sources_by_lane(EvidenceLane.RESEARCH_ARTIFACT):
            assert not source.numeric_authority


# ── AC 9: Open-web/news requires corroboration ───────────────────────────────

class TestOpenWebCorroboration:
    def test_news_feed_v1_exists_and_requires_corroboration(self) -> None:
        assert "news_feed_v1" in EVIDENCE_SOURCE_REGISTRY
        defn = EVIDENCE_SOURCE_REGISTRY["news_feed_v1"]
        assert defn.corroboration_required is True

    def test_news_feed_v1_not_decision_eligible(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["news_feed_v1"]
        assert defn.decision_input_eligible is False

    def test_news_feed_v1_trust_tier_open_web(self) -> None:
        defn = EVIDENCE_SOURCE_REGISTRY["news_feed_v1"]
        assert defn.trust_tier == TrustTier.OPEN_WEB

    def test_all_open_web_sources_require_corroboration(self) -> None:
        for source in get_all_sources():
            if source.trust_tier == TrustTier.OPEN_WEB:
                assert source.corroboration_required is True, (
                    f"OPEN_WEB source {source.source_id} must have "
                    "corroboration_required=True"
                )

    def test_open_web_decision_eligible_requires_corroboration(self) -> None:
        """If any OPEN_WEB source were ever decision_input_eligible,
        corroboration_required must be True. Currently none are eligible."""
        for source in get_all_sources():
            if source.trust_tier == TrustTier.OPEN_WEB and source.decision_input_eligible:
                assert source.corroboration_required is True, (
                    f"OPEN_WEB source {source.source_id} with "
                    "decision_input_eligible=True must require corroboration"
                )

    def test_news_event_risk_lane_no_decision_eligible(self) -> None:
        for source in get_sources_by_lane(EvidenceLane.NEWS_EVENT_RISK):
            assert source.decision_input_eligible is False


# ── AC 10: No visible decision path imports the registry ─────────────────────

class TestNoDecisionPathImport:
    def _source_imports_registry(self, module_path: pathlib.Path) -> bool:
        if not module_path.exists():
            return False
        source = _load_module_source(module_path)
        imports = _get_imports_from_source(source)
        return any("evidence_source_registry" in imp for imp in imports)

    def test_decision_policy_v1_does_not_import_registry(self) -> None:
        assert not self._source_imports_registry(_DECISION_POLICY_MODULE), (
            "decision_policy_v1 must not import evidence_source_registry in Phase 10"
        )

    def test_intel_v3_service_does_not_import_registry(self) -> None:
        assert not self._source_imports_registry(_INTEL_V3_SERVICE_MODULE), (
            "intel_v3_service must not import evidence_source_registry in Phase 10"
        )

    def test_snapshot_builder_does_not_import_registry(self) -> None:
        assert not self._source_imports_registry(_SNAPSHOT_BUILDER_MODULE), (
            "snapshot_builder must not import evidence_source_registry in Phase 10"
        )

    def test_registry_module_does_not_import_decision_policy(self) -> None:
        source = _load_module_source(_REGISTRY_MODULE)
        imports = _get_imports_from_source(source)
        forbidden = ["decision_policy_v1", "decide", "IntelV3Service", "snapshot_builder"]
        for imp in imports:
            for name in forbidden:
                assert name not in imp, (
                    f"evidence_source_registry must not import '{name}'"
                )


# ── AC 11 & 12: Diagnostics safety invariants ────────────────────────────────

class TestDiagnosticsSafetyInvariants:
    def test_build_registry_summary_safe_for_decision_false(self) -> None:
        summary = build_registry_summary()
        assert summary["safe_for_decision"] is False

    def test_build_registry_summary_visible_snapshot_unchanged_true(self) -> None:
        summary = build_registry_summary()
        assert summary["visible_snapshot_unchanged"] is True

    def test_build_registry_summary_returns_expected_keys(self) -> None:
        summary = build_registry_summary()
        required_keys = {
            "contract_version",
            "safe_for_decision",
            "visible_snapshot_unchanged",
            "total_sources",
            "total_lanes",
            "lanes_represented_count",
            "all_lanes_represented",
            "decision_eligible_source_count",
            "active_decision_eligible_source_count",
            "explanation_only_source_count",
            "corroboration_required_source_count",
            "sources_by_lane",
            "source_ids",
            "lifecycle_status_counts",
            "trust_tier_counts",
        }
        for key in required_keys:
            assert key in summary, f"Missing key in registry summary: {key}"

    def test_build_registry_summary_contract_version(self) -> None:
        summary = build_registry_summary()
        assert summary["contract_version"] == EVIDENCE_SOURCE_REGISTRY_CONTRACT_VERSION

    def test_build_registry_summary_idempotent(self) -> None:
        first = build_registry_summary()
        second = build_registry_summary()
        assert first == second


# ── AC 14: No SQL in registry module ─────────────────────────────────────────

class TestNoSQLInRegistry:
    def test_registry_module_no_db_imports(self) -> None:
        source = _load_module_source(_REGISTRY_MODULE)
        imports = _get_imports_from_source(source)
        forbidden_import_patterns = [
            "supabase",
            "sqlalchemy",
            "psycopg",
            "asyncpg",
            "database",
        ]
        for imp in imports:
            for pattern in forbidden_import_patterns:
                assert pattern not in imp, (
                    f"evidence_source_registry must not import '{pattern}'"
                )
        # Check for get_supabase_client call anywhere in source (not just imports)
        assert "get_supabase_client" not in source, (
            "evidence_source_registry must not call get_supabase_client"
        )


# ── AC 15: No provider/LLM calls in registry module ──────────────────────────

class TestNoProviderLLMCallsInRegistry:
    def test_registry_module_no_provider_imports(self) -> None:
        source = _load_module_source(_REGISTRY_MODULE)
        forbidden_patterns = [
            "anthropic",
            "openai",
            "httpx",
            "requests",
            "aiohttp",
            "finnhub",
            "polygon",
            "sec_edgar",
            "asyncio",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"evidence_source_registry must not import '{pattern}'"
            )

    def test_registry_module_no_frontend_imports(self) -> None:
        source = _load_module_source(_REGISTRY_MODULE)
        forbidden = ["frontend", "routers", "recommendation_engine"]
        for pattern in forbidden:
            assert pattern not in source, (
                f"evidence_source_registry must not reference '{pattern}'"
            )


# ── Additional structural invariants ─────────────────────────────────────────

class TestStructuralInvariants:
    def test_all_source_ids_unique(self) -> None:
        source_ids = [s.source_id for s in get_all_sources()]
        assert len(source_ids) == len(set(source_ids)), "All source_ids must be unique"

    def test_portfolio_exposure_lane_is_active(self) -> None:
        sources = get_sources_by_lane(EvidenceLane.PORTFOLIO_EXPOSURE)
        assert any(s.lifecycle_status == LifecycleStatus.ACTIVE for s in sources)

    def test_portfolio_exposure_uses_deterministic_provider(self) -> None:
        for source in get_sources_by_lane(EvidenceLane.PORTFOLIO_EXPOSURE):
            assert "llm" not in source.provider_adapter.lower(), (
                f"Portfolio exposure source {source.source_id} must not use "
                "an LLM provider"
            )

    def test_etf_fund_exposure_does_not_reuse_sec_company_logic(self) -> None:
        etf_sources = get_sources_by_lane(EvidenceLane.ETF_FUND_EXPOSURE)
        sec_sources = get_sources_by_lane(EvidenceLane.SEC_COMPANY_FUNDAMENTALS)
        etf_adapters = {s.provider_adapter for s in etf_sources}
        sec_adapters = {s.provider_adapter for s in sec_sources}
        overlap = etf_adapters & sec_adapters
        assert not overlap, (
            f"ETF lane must not share provider_adapter with SEC company lane: {overlap}"
        )

    def test_user_thesis_is_explanation_only(self) -> None:
        for source in get_sources_by_lane(EvidenceLane.USER_THESIS_MEMORY):
            assert source.explanation_only is True
            assert source.decision_input_eligible is False

    def test_missing_stale_failure_behaviors_suppress_not_fabricate(self) -> None:
        """Sources using SUPPRESS_AXIS or BLOCK_DECISION must be decision-eligible
        (otherwise IGNORE is the correct behavior for non-critical sources)."""
        suppressing_sources = [
            s for s in get_all_sources()
            if s.failure_behavior in (
                FailureBehavior.SUPPRESS_AXIS,
                FailureBehavior.BLOCK_DECISION,
            )
        ]
        for source in suppressing_sources:
            assert source.decision_input_eligible is True or (
                source.failure_behavior == FailureBehavior.BLOCK_DECISION
                and source.lifecycle_status == LifecycleStatus.ACTIVE
            ), (
                f"{source.source_id}: SUPPRESS_AXIS/BLOCK_DECISION only makes sense "
                "for decision-eligible or ACTIVE sources"
            )

    def test_sources_by_lane_helper_returns_correct_lane(self) -> None:
        for lane in EvidenceLane:
            for source in get_sources_by_lane(lane):
                assert source.lane == lane

    def test_active_decision_eligible_is_subset_of_decision_eligible(self) -> None:
        all_eligible = {s.source_id for s in get_decision_eligible_sources()}
        active_eligible = {s.source_id for s in get_active_decision_eligible_sources()}
        assert active_eligible.issubset(all_eligible)
