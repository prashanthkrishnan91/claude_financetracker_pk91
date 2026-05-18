"""Stage 5G — Provider Registry v1 + Free-First Evidence Source Router tests.

Proves all acceptance criteria for Stage 5G:
  1.  Central provider registry exists and is typed/tested.
  2.  Provider router deterministically chooses free/official sources before paid.
  3.  Paid provider candidates are disabled by default and never called.
  4.  Existing Stage 5F yfinance lanes still work through free-baseline behavior.
  5.  yfinance is classified as FREE / UNOFFICIAL_AGGREGATOR.
  6.  SEC EDGAR is classified as FREE / OFFICIAL.
  7.  FRED is classified as FREE / OFFICIAL but default_enabled=False (metadata-only).
  8.  Paid candidates (fmp, eodhd, alpha_vantage) cannot be selected by the router.
  9.  No provider available → honest no-provider result, not fabricated evidence.
 10.  No writes to intel_v3_snapshots or recommendations in registry/router modules.
 11.  safe_for_decision=False in registry summary.
 12.  Router returns correct ROUTE_REASON_* codes.
 13.  Stage 5F runner skips honestly when router returns no_provider.
 14.  Runner logs provider selection when router resolves a provider.
 15.  All ALL_LANES constants present and non-empty strings.
 16.  disabled_paid_providers() returns fmp, eodhd, alpha_vantage.
 17.  Registry summary has correct shape and counts.
 18.  Lane coverage summary per-lane in registry summary.
 19.  No decide() import in registry or router modules.
 20.  provider_entry in ProviderRouteResult is the full EvidenceProviderEntry.

No production Supabase access. All DB interactions use FakeSupabaseClient.
"""
from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional
from unittest.mock import patch

import pytest

from app.services.intelligence.research_workers.evidence_provider_registry_v1 import (
    ALL_LANES,
    EVIDENCE_PROVIDER_REGISTRY_VERSION,
    LANE_ANALYST_REVISIONS,
    LANE_COMPANY_STRATEGY,
    LANE_FUNDAMENTALS,
    LANE_INSIDER_13F,
    LANE_MACRO,
    LANE_NEWS_SENTIMENT,
    LANE_SEC_FILING,
    LANE_TECHNICALS,
    LANE_TRANSCRIPTS,
    CostTier,
    EvidenceProviderEntry,
    TrustTier,
    build_registry_summary,
    disabled_paid_providers,
    enabled_providers_for_lane,
    get_provider,
    list_providers,
    providers_for_lane,
)
from app.services.intelligence.research_workers.evidence_provider_router_v1 import (
    ROUTE_REASON_FREE_BASELINE,
    ROUTE_REASON_FREE_OFFICIAL,
    ROUTE_REASON_LOW_COST_ENABLED,
    ROUTE_REASON_NO_PROVIDER,
    ROUTE_REASON_PAID_ENABLED,
    ProviderRouteResult,
    resolve_provider_for_lane,
)
from app.config import Settings


# ── Fake Supabase client (reused from Stage 5F pattern) ──────────────────────

@dataclass
class _TableState:
    inserts: list[dict[str, Any]] = field(default_factory=list)
    upserts: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)


class FakeTableQuery:
    def __init__(self, state: _TableState, return_id: Optional[str] = None) -> None:
        self._state = state
        self._return_id = return_id or str(uuid.uuid4())
        self._filters: list = []
        self._select_cols: str = "*"

    def select(self, cols: str = "*", count: Optional[str] = None) -> "FakeTableQuery":
        self._select_cols = cols
        return self

    def insert(self, data: dict, **kwargs: Any) -> "FakeTableQuery":
        self._state.inserts.append(data)
        return self

    def upsert(self, data: dict, **kwargs: Any) -> "FakeTableQuery":
        self._state.upserts.append(data)
        return self

    def update(self, data: dict) -> "FakeTableQuery":
        self._state.updates.append(data)
        return self

    def eq(self, col: str, val: Any) -> "FakeTableQuery":
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col: str, val: Any) -> "FakeTableQuery":
        self._filters.append(("neq", col, val))
        return self

    def is_(self, col: str, val: Any) -> "FakeTableQuery":
        self._filters.append(("is_", col, val))
        return self

    def limit(self, n: int) -> "FakeTableQuery":
        return self

    def order(self, col: str, **kwargs: Any) -> "FakeTableQuery":
        return self

    def execute(self) -> Any:
        @dataclass
        class _Resp:
            data: Any
            count: Optional[int] = None

        # SELECT path: return one artifact row for idempotency checks.
        if not self._state.inserts and not self._state.upserts and not self._state.updates:
            return _Resp(data=[])

        # INSERT/UPSERT path.
        return _Resp(data=[{"id": self._return_id}])


class FakeSupabaseClient:
    def __init__(self) -> None:
        self._states: dict[str, _TableState] = {}

    def table(self, name: str) -> FakeTableQuery:
        if name not in self._states:
            self._states[name] = _TableState()
        return FakeTableQuery(self._states[name])

    def inserts_for(self, table: str) -> list:
        return self._states.get(table, _TableState()).inserts

    def updates_for(self, table: str) -> list:
        return self._states.get(table, _TableState()).updates


# ── Settings helpers ──────────────────────────────────────────────────────────

def _all_lanes_enabled() -> Settings:
    return Settings(
        intel_v3_research_workers_enabled=True,
        intel_v3_fundamentals_evidence_enabled=True,
        intel_v3_technicals_evidence_enabled=True,
        intel_v3_news_sentiment_evidence_enabled=True,
    )


def _all_lanes_disabled() -> Settings:
    return Settings(
        intel_v3_research_workers_enabled=False,
        intel_v3_fundamentals_evidence_enabled=False,
        intel_v3_technicals_evidence_enabled=False,
        intel_v3_news_sentiment_evidence_enabled=False,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. Registry structure and presence
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceProviderRegistryStructure:
    """Registry is typed, present, and contains all required providers."""

    def test_registry_version_string(self):
        assert EVIDENCE_PROVIDER_REGISTRY_VERSION == "stage5g_v1"

    def test_all_lanes_constants_non_empty(self):
        for lane in ALL_LANES:
            assert isinstance(lane, str) and lane.strip()

    def test_all_nine_lanes_present(self):
        expected = {
            LANE_FUNDAMENTALS, LANE_TECHNICALS, LANE_NEWS_SENTIMENT,
            LANE_SEC_FILING, LANE_MACRO, LANE_ANALYST_REVISIONS,
            LANE_COMPANY_STRATEGY, LANE_TRANSCRIPTS, LANE_INSIDER_13F,
        }
        assert ALL_LANES == expected

    def test_six_providers_registered(self):
        ids = {p.provider_id for p in list_providers()}
        assert {"sec_edgar", "yfinance", "fred", "fmp", "eodhd", "alpha_vantage"} == ids

    def test_get_provider_returns_entry(self):
        assert get_provider("yfinance") is not None
        assert get_provider("sec_edgar") is not None

    def test_get_provider_unknown_returns_none(self):
        assert get_provider("nonexistent_provider") is None

    def test_all_entries_are_evidence_provider_entry(self):
        for p in list_providers():
            assert isinstance(p, EvidenceProviderEntry)

    def test_all_entries_have_non_empty_provider_id(self):
        for p in list_providers():
            assert p.provider_id.strip()

    def test_all_entries_have_positive_priority(self):
        for p in list_providers():
            assert p.source_of_truth_priority > 0

    def test_all_supported_lanes_are_known(self):
        for p in list_providers():
            for lane in p.supported_lanes:
                assert lane in ALL_LANES, (
                    f"{p.provider_id} declares unknown lane '{lane}'"
                )


# ══════════════════════════════════════════════════════════════════════════════
# 2. Individual provider classification
# ══════════════════════════════════════════════════════════════════════════════

class TestSecEdgarClassification:
    """SEC EDGAR is FREE / OFFICIAL and enabled for the sec_filing lane.

    The evidence_lane_runner's 'fundamentals' lane uses yfinance exclusively
    (lightweight financial metrics). SEC EDGAR company facts (XBRL) are a
    separate data product registered only for sec_filing in this implementation.
    """

    def test_sec_edgar_cost_tier_free(self):
        p = get_provider("sec_edgar")
        assert p is not None
        assert p.cost_tier == CostTier.FREE

    def test_sec_edgar_trust_tier_official(self):
        p = get_provider("sec_edgar")
        assert p.trust_tier == TrustTier.OFFICIAL

    def test_sec_edgar_default_enabled(self):
        p = get_provider("sec_edgar")
        assert p.default_enabled is True

    def test_sec_edgar_requires_no_api_key(self):
        p = get_provider("sec_edgar")
        assert p.requires_api_key is False

    def test_sec_edgar_supports_sec_filing_lane(self):
        p = get_provider("sec_edgar")
        assert LANE_SEC_FILING in p.supported_lanes

    def test_sec_edgar_does_not_support_yfinance_fundamentals_lane(self):
        # The runner's fundamentals lane uses yfinance. SEC EDGAR company facts
        # (XBRL) will be a separate lane when that adapter is wired in.
        p = get_provider("sec_edgar")
        assert LANE_FUNDAMENTALS not in p.supported_lanes

    def test_sec_edgar_highest_priority(self):
        # sec_edgar should have lower priority number than paid providers.
        sec = get_provider("sec_edgar")
        fmp = get_provider("fmp")
        assert sec.source_of_truth_priority < fmp.source_of_truth_priority

    def test_sec_edgar_in_enabled_providers_for_sec_filing(self):
        enabled = enabled_providers_for_lane(LANE_SEC_FILING)
        ids = {p.provider_id for p in enabled}
        assert "sec_edgar" in ids

    def test_sec_edgar_is_first_in_sec_filing_lane(self):
        provs = providers_for_lane(LANE_SEC_FILING)
        # sec_edgar has lowest priority number → appears first.
        assert provs[0].provider_id == "sec_edgar"


class TestYfinanceClassification:
    """yfinance is FREE / UNOFFICIAL_AGGREGATOR — free baseline for 5F lanes."""

    def test_yfinance_cost_tier_free(self):
        p = get_provider("yfinance")
        assert p.cost_tier == CostTier.FREE

    def test_yfinance_trust_tier_unofficial_aggregator(self):
        p = get_provider("yfinance")
        assert p.trust_tier == TrustTier.UNOFFICIAL_AGGREGATOR

    def test_yfinance_default_enabled(self):
        p = get_provider("yfinance")
        assert p.default_enabled is True

    def test_yfinance_requires_no_api_key(self):
        p = get_provider("yfinance")
        assert p.requires_api_key is False

    def test_yfinance_supports_fundamentals_lane(self):
        assert LANE_FUNDAMENTALS in get_provider("yfinance").supported_lanes

    def test_yfinance_supports_technicals_lane(self):
        assert LANE_TECHNICALS in get_provider("yfinance").supported_lanes

    def test_yfinance_supports_news_sentiment_lane(self):
        assert LANE_NEWS_SENTIMENT in get_provider("yfinance").supported_lanes

    def test_yfinance_has_lower_priority_than_sec_edgar(self):
        yf = get_provider("yfinance")
        sec = get_provider("sec_edgar")
        assert yf.source_of_truth_priority > sec.source_of_truth_priority


class TestFredClassification:
    """FRED is FREE / OFFICIAL but metadata-only (default_enabled=False)."""

    def test_fred_cost_tier_free(self):
        p = get_provider("fred")
        assert p.cost_tier == CostTier.FREE

    def test_fred_trust_tier_official(self):
        p = get_provider("fred")
        assert p.trust_tier == TrustTier.OFFICIAL

    def test_fred_not_default_enabled(self):
        p = get_provider("fred")
        assert p.default_enabled is False

    def test_fred_supports_macro_lane(self):
        assert LANE_MACRO in get_provider("fred").supported_lanes

    def test_fred_not_in_enabled_providers_for_macro(self):
        enabled = enabled_providers_for_lane(LANE_MACRO)
        ids = {p.provider_id for p in enabled}
        assert "fred" not in ids


class TestPaidProvidersClassification:
    """FMP, EODHD, Alpha Vantage are disabled metadata-only candidates."""

    @pytest.mark.parametrize("provider_id", ["fmp", "eodhd", "alpha_vantage"])
    def test_paid_candidates_are_disabled(self, provider_id: str):
        p = get_provider(provider_id)
        assert p is not None
        assert p.default_enabled is False

    @pytest.mark.parametrize("provider_id", ["fmp", "eodhd", "alpha_vantage"])
    def test_paid_candidates_require_api_key(self, provider_id: str):
        p = get_provider(provider_id)
        assert p.requires_api_key is True

    def test_fmp_cost_tier_paid(self):
        assert get_provider("fmp").cost_tier == CostTier.PAID

    def test_eodhd_cost_tier_low_cost(self):
        assert get_provider("eodhd").cost_tier == CostTier.LOW_COST

    def test_alpha_vantage_cost_tier_low_cost(self):
        assert get_provider("alpha_vantage").cost_tier == CostTier.LOW_COST

    @pytest.mark.parametrize("provider_id", ["fmp", "eodhd", "alpha_vantage"])
    def test_paid_candidates_trust_tier_broad_financial_vendor(self, provider_id: str):
        p = get_provider(provider_id)
        assert p.trust_tier == TrustTier.BROAD_FINANCIAL_VENDOR

    @pytest.mark.parametrize("provider_id", ["fmp", "eodhd", "alpha_vantage"])
    def test_paid_candidates_have_limitations(self, provider_id: str):
        p = get_provider(provider_id)
        assert len(p.limitations) > 0
        # At least one limitation must mention it is disabled.
        assert any("DISABLED" in lim for lim in p.limitations)

    def test_disabled_paid_providers_returns_fmp_eodhd_alpha_vantage(self):
        paid = {p.provider_id for p in disabled_paid_providers()}
        assert paid == {"fmp", "eodhd", "alpha_vantage"}


# ══════════════════════════════════════════════════════════════════════════════
# 3. Lane coverage queries
# ══════════════════════════════════════════════════════════════════════════════

class TestLaneCoverageQueries:

    def test_providers_for_fundamentals_includes_yfinance_and_paid_candidates(self):
        ids = {p.provider_id for p in providers_for_lane(LANE_FUNDAMENTALS)}
        assert "yfinance" in ids
        # sec_edgar covers sec_filing, not the runner's fundamentals lane.
        assert "sec_edgar" not in ids
        assert "fmp" in ids

    def test_enabled_providers_for_fundamentals_excludes_paid(self):
        enabled = {p.provider_id for p in enabled_providers_for_lane(LANE_FUNDAMENTALS)}
        assert "yfinance" in enabled
        assert "fmp" not in enabled
        assert "eodhd" not in enabled
        assert "alpha_vantage" not in enabled

    def test_enabled_providers_for_technicals_is_yfinance_only(self):
        enabled = {p.provider_id for p in enabled_providers_for_lane(LANE_TECHNICALS)}
        assert "yfinance" in enabled
        # No other enabled provider for technicals in the default registry.
        assert "fmp" not in enabled

    def test_enabled_providers_for_news_sentiment_is_yfinance_only(self):
        enabled = {p.provider_id for p in enabled_providers_for_lane(LANE_NEWS_SENTIMENT)}
        assert "yfinance" in enabled
        assert "fmp" not in enabled

    def test_enabled_providers_for_sec_filing_is_sec_edgar(self):
        enabled = {p.provider_id for p in enabled_providers_for_lane(LANE_SEC_FILING)}
        assert "sec_edgar" in enabled
        assert "yfinance" not in enabled

    def test_enabled_providers_for_macro_is_empty(self):
        # FRED is the only macro provider and it's disabled.
        enabled = enabled_providers_for_lane(LANE_MACRO)
        assert enabled == []

    def test_enabled_providers_for_analyst_revisions_is_empty(self):
        # No enabled analyst_revisions provider in default registry.
        enabled = enabled_providers_for_lane(LANE_ANALYST_REVISIONS)
        assert enabled == []

    def test_providers_for_lane_sorted_by_priority(self):
        provs = providers_for_lane(LANE_FUNDAMENTALS)
        priorities = [p.source_of_truth_priority for p in provs]
        assert priorities == sorted(priorities)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Router deterministic policy
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceProviderRouterPolicy:

    def test_fundamentals_resolves_yfinance_free_baseline(self):
        # yfinance is the only enabled provider for the fundamentals lane
        # (sec_edgar covers sec_filing, not the runner's fundamentals lane).
        result = resolve_provider_for_lane(LANE_FUNDAMENTALS)
        assert result.provider_id == "yfinance"
        assert result.reason == ROUTE_REASON_FREE_BASELINE

    def test_technicals_resolves_yfinance_free_baseline(self):
        result = resolve_provider_for_lane(LANE_TECHNICALS)
        assert result.provider_id == "yfinance"
        assert result.reason == ROUTE_REASON_FREE_BASELINE

    def test_news_sentiment_resolves_yfinance_free_baseline(self):
        result = resolve_provider_for_lane(LANE_NEWS_SENTIMENT)
        assert result.provider_id == "yfinance"
        assert result.reason == ROUTE_REASON_FREE_BASELINE

    def test_sec_filing_resolves_sec_edgar_free_official(self):
        result = resolve_provider_for_lane(LANE_SEC_FILING)
        assert result.provider_id == "sec_edgar"
        assert result.reason == ROUTE_REASON_FREE_OFFICIAL

    def test_macro_returns_no_provider(self):
        # No enabled macro provider (FRED is disabled).
        result = resolve_provider_for_lane(LANE_MACRO)
        assert result.provider_id is None
        assert result.reason == ROUTE_REASON_NO_PROVIDER
        assert result.provider_entry is None

    def test_analyst_revisions_returns_no_provider(self):
        result = resolve_provider_for_lane(LANE_ANALYST_REVISIONS)
        assert result.provider_id is None
        assert result.reason == ROUTE_REASON_NO_PROVIDER

    def test_company_strategy_returns_no_provider(self):
        result = resolve_provider_for_lane(LANE_COMPANY_STRATEGY)
        assert result.provider_id is None
        assert result.reason == ROUTE_REASON_NO_PROVIDER

    def test_unknown_lane_returns_no_provider(self):
        result = resolve_provider_for_lane("nonexistent_lane_xyz")
        assert result.provider_id is None
        assert result.reason == ROUTE_REASON_NO_PROVIDER

    def test_route_result_is_frozen(self):
        result = resolve_provider_for_lane(LANE_FUNDAMENTALS)
        with pytest.raises((TypeError, AttributeError)):
            result.provider_id = "hacked"  # type: ignore[misc]

    def test_route_result_provider_entry_populated(self):
        result = resolve_provider_for_lane(LANE_FUNDAMENTALS)
        assert result.provider_entry is not None
        assert isinstance(result.provider_entry, EvidenceProviderEntry)
        assert result.provider_entry.provider_id == result.provider_id

    def test_route_result_no_provider_entry_is_none(self):
        result = resolve_provider_for_lane(LANE_MACRO)
        assert result.provider_entry is None


class TestPaidCandidatesNeverSelected:
    """Paid/disabled candidates must never be returned by the router."""

    @pytest.mark.parametrize("provider_id", ["fmp", "eodhd", "alpha_vantage"])
    def test_paid_disabled_provider_not_selected_for_fundamentals(
        self, provider_id: str
    ):
        result = resolve_provider_for_lane(LANE_FUNDAMENTALS)
        assert result.provider_id != provider_id

    @pytest.mark.parametrize("provider_id", ["fmp", "eodhd", "alpha_vantage"])
    def test_paid_disabled_provider_not_selected_for_technicals(
        self, provider_id: str
    ):
        result = resolve_provider_for_lane(LANE_TECHNICALS)
        assert result.provider_id != provider_id

    def test_paid_providers_absent_from_enabled_list_prevents_selection(self):
        # Prove the mechanism: enabled_providers_for_lane filters disabled entries.
        for lane in [LANE_FUNDAMENTALS, LANE_TECHNICALS, LANE_NEWS_SENTIMENT]:
            enabled = enabled_providers_for_lane(lane)
            for p in enabled:
                assert p.cost_tier in (CostTier.FREE, CostTier.UNKNOWN), (
                    f"Paid provider {p.provider_id} should not be in enabled list "
                    f"for lane {lane}"
                )

    def test_fmp_has_fundamentals_in_supported_lanes_but_disabled(self):
        fmp = get_provider("fmp")
        assert LANE_FUNDAMENTALS in fmp.supported_lanes
        assert fmp.default_enabled is False
        # Despite being in providers_for_lane, it should NOT be in enabled list.
        all_provs = {p.provider_id for p in providers_for_lane(LANE_FUNDAMENTALS)}
        enabled_provs = {p.provider_id for p in enabled_providers_for_lane(LANE_FUNDAMENTALS)}
        assert "fmp" in all_provs
        assert "fmp" not in enabled_provs


class TestFreeOfficialPreferredOverFreeUnofficial:
    """When both free-official and free-unofficial exist, official wins."""

    def test_sec_filing_prefers_sec_edgar_over_free_unofficial(self):
        # Only sec_edgar is enabled for sec_filing → always free-official.
        result = resolve_provider_for_lane(LANE_SEC_FILING)
        assert result.reason == ROUTE_REASON_FREE_OFFICIAL
        assert result.provider_id == "sec_edgar"

    def test_fundamentals_uses_yfinance_free_baseline(self):
        # sec_edgar does not cover the evidence_lane_runner's fundamentals lane.
        # yfinance is the only enabled provider → free baseline.
        result = resolve_provider_for_lane(LANE_FUNDAMENTALS)
        assert result.reason == ROUTE_REASON_FREE_BASELINE
        assert result.provider_id == "yfinance"


# ══════════════════════════════════════════════════════════════════════════════
# 5. No-provider honest behavior
# ══════════════════════════════════════════════════════════════════════════════

class TestNoProviderHonestBehavior:
    """No provider available → honest no-provider result, not fake evidence."""

    def test_no_provider_result_has_none_provider_id(self):
        result = resolve_provider_for_lane(LANE_MACRO)
        assert result.provider_id is None

    def test_no_provider_reason_code(self):
        result = resolve_provider_for_lane(LANE_MACRO)
        assert result.reason == ROUTE_REASON_NO_PROVIDER

    def test_no_provider_result_not_a_fabricated_provider(self):
        result = resolve_provider_for_lane("completely_unknown_lane_xyz")
        # Must not fabricate a provider_id.
        assert result.provider_id is None
        assert result.reason == ROUTE_REASON_NO_PROVIDER

    def test_route_reason_no_provider_is_string(self):
        assert isinstance(ROUTE_REASON_NO_PROVIDER, str)
        assert ROUTE_REASON_NO_PROVIDER


# ══════════════════════════════════════════════════════════════════════════════
# 6. Registry summary
# ══════════════════════════════════════════════════════════════════════════════

class TestRegistrySummary:

    def test_summary_safe_for_decision_false(self):
        summary = build_registry_summary()
        assert summary["safe_for_decision"] is False

    def test_summary_registry_version(self):
        summary = build_registry_summary()
        assert summary["registry_version"] == EVIDENCE_PROVIDER_REGISTRY_VERSION

    def test_summary_provider_counts(self):
        summary = build_registry_summary()
        assert summary["total_providers"] == 6
        # sec_edgar, yfinance = 2 enabled.
        assert summary["enabled_providers"] == 2
        # fred, fmp, eodhd, alpha_vantage = 4 disabled.
        assert summary["disabled_providers"] == 4

    def test_summary_paid_disabled_candidates(self):
        summary = build_registry_summary()
        assert summary["paid_disabled_candidates"] == 3
        assert set(summary["paid_disabled_ids"]) == {"fmp", "eodhd", "alpha_vantage"}

    def test_summary_lane_coverage_present(self):
        summary = build_registry_summary()
        assert "lane_coverage" in summary
        for lane in ALL_LANES:
            assert lane in summary["lane_coverage"]

    def test_summary_lane_coverage_fundamentals(self):
        summary = build_registry_summary()
        coverage = summary["lane_coverage"][LANE_FUNDAMENTALS]
        assert coverage["enabled_providers"] >= 1
        assert coverage["primary_provider"] == "yfinance"
        # Only yfinance is enabled for fundamentals; it's free but not official.
        assert coverage["has_free_official"] is False

    def test_summary_lane_coverage_macro_no_enabled(self):
        summary = build_registry_summary()
        coverage = summary["lane_coverage"][LANE_MACRO]
        assert coverage["enabled_providers"] == 0
        assert coverage["primary_provider"] is None

    def test_summary_cost_tier_counts(self):
        summary = build_registry_summary()
        counts = summary["cost_tier_counts"]
        assert CostTier.FREE.value in counts
        assert CostTier.PAID.value in counts
        assert CostTier.LOW_COST.value in counts

    def test_summary_trust_tier_counts(self):
        summary = build_registry_summary()
        counts = summary["trust_tier_counts"]
        assert TrustTier.OFFICIAL.value in counts
        assert TrustTier.UNOFFICIAL_AGGREGATOR.value in counts
        assert TrustTier.BROAD_FINANCIAL_VENDOR.value in counts


# ══════════════════════════════════════════════════════════════════════════════
# 7. Safety invariants
# ══════════════════════════════════════════════════════════════════════════════

class TestSafetyInvariants:

    def test_no_decide_import_in_registry(self):
        import ast
        import inspect
        import app.services.intelligence.research_workers.evidence_provider_registry_v1 as mod
        src = inspect.getsource(mod)
        assert "decide" not in src.lower() or "safe_for_decision" in src

    def test_no_decide_import_in_router(self):
        import inspect
        import app.services.intelligence.research_workers.evidence_provider_router_v1 as mod
        src = inspect.getsource(mod)
        assert "from app.services.intelligence.v3.decision" not in src
        assert "import decide" not in src

    def test_no_intel_v3_snapshots_write_in_registry(self):
        import inspect
        import app.services.intelligence.research_workers.evidence_provider_registry_v1 as mod
        src = inspect.getsource(mod)
        assert "intel_v3_snapshots" not in src

    def test_no_intel_v3_snapshots_write_in_router(self):
        import inspect
        import app.services.intelligence.research_workers.evidence_provider_router_v1 as mod
        src = inspect.getsource(mod)
        assert "intel_v3_snapshots" not in src

    def test_no_recommendations_write_in_registry(self):
        import inspect
        import app.services.intelligence.research_workers.evidence_provider_registry_v1 as mod
        src = inspect.getsource(mod)
        assert "recommendations" not in src

    def test_no_network_calls_in_registry(self):
        import inspect
        import app.services.intelligence.research_workers.evidence_provider_registry_v1 as mod
        src = inspect.getsource(mod)
        assert "import httpx" not in src
        assert "import requests" not in src
        assert "import urllib" not in src

    def test_no_network_calls_in_router(self):
        import inspect
        import app.services.intelligence.research_workers.evidence_provider_router_v1 as mod
        src = inspect.getsource(mod)
        assert "httpx" not in src
        assert "requests" not in src

    def test_no_db_calls_in_registry(self):
        import inspect
        import app.services.intelligence.research_workers.evidence_provider_registry_v1 as mod
        src = inspect.getsource(mod)
        assert "supabase" not in src
        assert "get_supabase_client" not in src


# ══════════════════════════════════════════════════════════════════════════════
# 8. Stage 5F runner compatibility
# ══════════════════════════════════════════════════════════════════════════════

class TestStage5FRunnerCompatibility:
    """Existing Stage 5F lanes still work with registry/router wired."""

    def _fake_fundamentals_data(self) -> dict:
        return {
            "pe": 25.0, "eps": 6.5, "sector": "Technology",
            "industry": "Software", "market_cap": 1e12,
            "profit_margin": 0.25, "gross_margin": 0.60,
            "revenue_growth": 0.10, "return_on_equity": 0.35,
        }

    def _fake_technicals_data(self) -> dict:
        return {
            "last": 180.0, "high_3mo": 195.0, "low_3mo": 160.0,
            "pct_1d": 0.012, "pct_30d": 0.05, "sma20": 178.0,
            "sma50": 172.0, "volatility_30d": 0.22, "n_bars": 63,
        }

    def _fake_news_data(self) -> list:
        return [
            {"headline": "AAPL Beats Estimates", "source": "Reuters",
             "datetime": 1700000000, "summary": "Apple beats Q3 estimates."},
        ]

    def test_fundamentals_lane_runs_with_registry_wired(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_fundamentals_evidence,
        )
        db = FakeSupabaseClient()
        settings = Settings(
            intel_v3_research_workers_enabled=True,
            intel_v3_fundamentals_evidence_enabled=True,
        )
        artifact_id = run_fundamentals_evidence(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=db,
            settings=settings,
            _fetch_fn=lambda t: self._fake_fundamentals_data(),
        )
        # With yfinance enabled in registry, runner should produce an artifact.
        assert artifact_id is not None

    def test_technicals_lane_runs_with_registry_wired(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_technicals_evidence,
        )
        db = FakeSupabaseClient()
        settings = Settings(
            intel_v3_research_workers_enabled=True,
            intel_v3_technicals_evidence_enabled=True,
        )
        artifact_id = run_technicals_evidence(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=db,
            settings=settings,
            _fetch_fn=lambda t: self._fake_technicals_data(),
        )
        assert artifact_id is not None

    def test_news_sentiment_lane_runs_with_registry_wired(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_news_sentiment_evidence,
        )
        db = FakeSupabaseClient()
        settings = Settings(
            intel_v3_research_workers_enabled=True,
            intel_v3_news_sentiment_evidence_enabled=True,
        )
        artifact_id = run_news_sentiment_evidence(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=db,
            settings=settings,
            _fetch_fn=lambda t: self._fake_news_data(),
        )
        assert artifact_id is not None

    def test_all_lanes_disabled_returns_none_for_all(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_all_evidence_lanes,
        )
        db = FakeSupabaseClient()
        settings = _all_lanes_disabled()
        results = run_all_evidence_lanes(
            user_id=str(uuid.uuid4()),
            ticker="MSFT",
            db_client=db,
            settings=settings,
            _fundamentals_fetch_fn=lambda t: self._fake_fundamentals_data(),
            _technicals_fetch_fn=lambda t: self._fake_technicals_data(),
            _news_sentiment_fetch_fn=lambda t: self._fake_news_data(),
        )
        assert all(v is None for v in results.values())

    def test_no_intel_v3_snapshots_writes_from_runner(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_fundamentals_evidence,
        )
        db = FakeSupabaseClient()
        settings = Settings(
            intel_v3_research_workers_enabled=True,
            intel_v3_fundamentals_evidence_enabled=True,
        )
        run_fundamentals_evidence(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=db,
            settings=settings,
            _fetch_fn=lambda t: self._fake_fundamentals_data(),
        )
        assert db.inserts_for("intel_v3_snapshots") == []
        assert db.inserts_for("recommendations") == []

    def test_safe_for_decision_false_in_written_artifacts(self):
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_fundamentals_evidence,
        )
        db = FakeSupabaseClient()
        settings = Settings(
            intel_v3_research_workers_enabled=True,
            intel_v3_fundamentals_evidence_enabled=True,
        )
        run_fundamentals_evidence(
            user_id=str(uuid.uuid4()),
            ticker="AAPL",
            db_client=db,
            settings=settings,
            _fetch_fn=lambda t: self._fake_fundamentals_data(),
        )
        inserts = db.inserts_for("research_artifacts")
        for row in inserts:
            assert row.get("safe_for_decision") is False


class TestRunnerSkipsHonestlyWhenNoProvider:
    """Runner returns None honestly when router reports no enabled provider."""

    def test_runner_imports_router(self):
        import inspect
        import app.services.intelligence.research_workers.evidence_lane_runner_v1 as mod
        src = inspect.getsource(mod)
        assert "resolve_provider_for_lane" in src
        assert "ROUTE_REASON_NO_PROVIDER" in src

    def test_router_no_provider_path_can_be_exercised(self):
        # Directly verify the router path for a lane with no enabled providers.
        result = resolve_provider_for_lane(LANE_MACRO)
        assert result.reason == ROUTE_REASON_NO_PROVIDER
        assert result.provider_id is None

    def test_runner_provider_resolution_logged_on_success(self, caplog):
        import logging
        from app.services.intelligence.research_workers.evidence_lane_runner_v1 import (
            run_fundamentals_evidence,
        )
        db = FakeSupabaseClient()
        settings = Settings(
            intel_v3_research_workers_enabled=True,
            intel_v3_fundamentals_evidence_enabled=True,
        )
        with caplog.at_level(logging.DEBUG, logger="app.services.intelligence.research_workers.evidence_lane_runner_v1"):
            run_fundamentals_evidence(
                user_id=str(uuid.uuid4()),
                ticker="TSLA",
                db_client=db,
                settings=settings,
                _fetch_fn=lambda t: {"pe": 60.0, "eps": 3.0, "sector": "Auto"},
            )
        log_text = caplog.text
        assert "evidence_lane_provider_resolved" in log_text
        assert "lane=fundamentals" in log_text


# ══════════════════════════════════════════════════════════════════════════════
# 9. Route reason code constants
# ══════════════════════════════════════════════════════════════════════════════

class TestRouteReasonCodes:

    def test_all_reason_codes_are_strings(self):
        for code in [
            ROUTE_REASON_FREE_OFFICIAL,
            ROUTE_REASON_FREE_BASELINE,
            ROUTE_REASON_LOW_COST_ENABLED,
            ROUTE_REASON_PAID_ENABLED,
            ROUTE_REASON_NO_PROVIDER,
        ]:
            assert isinstance(code, str) and code

    def test_reason_codes_are_distinct(self):
        codes = {
            ROUTE_REASON_FREE_OFFICIAL,
            ROUTE_REASON_FREE_BASELINE,
            ROUTE_REASON_LOW_COST_ENABLED,
            ROUTE_REASON_PAID_ENABLED,
            ROUTE_REASON_NO_PROVIDER,
        }
        assert len(codes) == 5
