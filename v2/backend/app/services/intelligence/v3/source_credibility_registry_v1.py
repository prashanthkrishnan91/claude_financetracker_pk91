"""Stage 5B — Source Credibility Registry v1.

Deterministic, typed registry that classifies research artifact sources by
capability band. No numeric scores, no LLM calls, no external API calls, no IO.

Architecture contracts (non-negotiable):
  - No fake credibility scores (no 87/100, no numeric confidence values).
  - No LLM calls, no external API calls, no IO of any kind.
  - Cannot emit or imply Buy/Hold/Trim/Sell, price target, conviction,
    allocation, deploy amount, or broker action.
  - Same inputs always produce the same output (fully replayable/auditable).
  - No-source artifacts are ALLOWED; they are assessed as UNKNOWN/INSUFFICIENT.
  - safe_for_decision is never touched — remains False as DB enforces.
  - Supports future Stage 5C contradiction detection and Stage 5D completeness
    scoring via the per-source capability metadata, but does NOT implement them.

Source kinds supported (matching migration 017 CHECK constraint):
  sec_filing, transcript, vendor_calendar, news, vendor_fundamentals,
  vendor_estimates, peer_set_def, press_release, company_disclosure, other
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional

SOURCE_CREDIBILITY_REGISTRY_VERSION = "source_credibility_registry.v1"

# ── Authority levels ──────────────────────────────────────────────────────────


class AuthorityLevel(str, Enum):
    """Named capability bands. Not numeric scores."""
    PRIMARY_AUTHORITY = "PRIMARY_AUTHORITY"
    # SEC filings — mandatory regulatory disclosure. Strongest available
    # authority for company-stated facts at the filing date.

    COMPANY_AUTHORED = "COMPANY_AUTHORED"
    # Company-originated material (press releases, transcripts, disclosures).
    # Authoritative for company-stated guidance; not independent verification.

    VENDOR_DERIVED = "VENDOR_DERIVED"
    # Vendor-provided data (fundamentals, estimates, calendar, peer sets).
    # Derived/normalized by a third-party; freshness and provenance required.

    EDITORIAL_CONTEXT = "EDITORIAL_CONTEXT"
    # Editorial/journalistic content (news).
    # Contextual evidence only. Cannot be authoritative for financial facts.

    UNKNOWN = "UNKNOWN"
    # Source kind is 'other', unrecognized, or lacks enough metadata.


# Ordering for strongest_authority_level computation (higher = stronger).
_AUTHORITY_RANK: Dict[AuthorityLevel, int] = {
    AuthorityLevel.PRIMARY_AUTHORITY: 4,
    AuthorityLevel.COMPANY_AUTHORED: 3,
    AuthorityLevel.VENDOR_DERIVED: 2,
    AuthorityLevel.EDITORIAL_CONTEXT: 1,
    AuthorityLevel.UNKNOWN: 0,
}


# ── Source authorship categories ──────────────────────────────────────────────


class SourceAuthorship(str, Enum):
    """Who wrote / produced the source material."""
    OFFICIAL_REGULATORY = "OFFICIAL_REGULATORY"   # Mandatory regulatory (SEC)
    OFFICIAL_PUBLIC_DATA = "OFFICIAL_PUBLIC_DATA" # Public-sector statistical data (FRED, BLS, etc.)
    COMPANY_AUTHORED = "COMPANY_AUTHORED"          # Company-originated material
    THIRD_PARTY_VENDOR = "THIRD_PARTY_VENDOR"      # Vendor-provided data
    EDITORIAL = "EDITORIAL"                        # Journalistic / editorial
    UNKNOWN = "UNKNOWN"


# ── Claim categories ──────────────────────────────────────────────────────────
# Labels for what a source can and cannot support.
# These are metadata labels only — not decision inputs.

CLAIM_FINANCIAL_STATED_FACT = "financial_stated_fact"
CLAIM_REGULATORY_DISCLOSURE = "regulatory_disclosure"
CLAIM_COMPANY_GUIDANCE = "company_guidance"
CLAIM_VENDOR_DERIVED_METRIC = "vendor_derived_metric"
CLAIM_EDITORIAL_CONTEXT = "editorial_context"
CLAIM_EARNINGS_CALENDAR = "earnings_calendar"
CLAIM_PEER_BENCHMARK = "peer_benchmark"
# Stage 5I patch — official public-sector macro/economic statistical data
# (Federal Reserve releases via FRED, etc.). Describes the economic environment.
# Never elevates to investment recommendation or future-performance claim.
CLAIM_OFFICIAL_MACRO_DATA = "official_macro_data"

# These claim categories are NEVER supported by any source in this registry.
_CLAIMS_NEVER_SUPPORTED: FrozenSet[str] = frozenset({
    "future_performance",
    "price_target",
    "conviction",
    "allocation",
    "buy_sell_action",
    "final_action",
    "recommendation",
})


# ── Per-source-kind canonical definitions ─────────────────────────────────────


@dataclass(frozen=True)
class SourceKindDefinition:
    """Immutable canonical definition for one source_kind value."""
    source_kind: str
    authority_level: AuthorityLevel
    authorship: SourceAuthorship
    claim_categories_supported: FrozenSet[str]
    limitations: str


_REGISTRY: Dict[str, SourceKindDefinition] = {
    "sec_filing": SourceKindDefinition(
        source_kind="sec_filing",
        authority_level=AuthorityLevel.PRIMARY_AUTHORITY,
        authorship=SourceAuthorship.OFFICIAL_REGULATORY,
        claim_categories_supported=frozenset({
            CLAIM_FINANCIAL_STATED_FACT,
            CLAIM_REGULATORY_DISCLOSURE,
        }),
        limitations=(
            "SEC filings are authoritative for company-stated regulatory facts at "
            "the filing date. They do not prove future performance, price direction, "
            "or investment outcome."
        ),
    ),
    "company_disclosure": SourceKindDefinition(
        source_kind="company_disclosure",
        authority_level=AuthorityLevel.COMPANY_AUTHORED,
        authorship=SourceAuthorship.COMPANY_AUTHORED,
        claim_categories_supported=frozenset({
            CLAIM_FINANCIAL_STATED_FACT,
            CLAIM_COMPANY_GUIDANCE,
        }),
        limitations=(
            "Company disclosures are company-authored and may include forward-looking "
            "statements. They do not constitute independent verification of financial facts."
        ),
    ),
    "press_release": SourceKindDefinition(
        source_kind="press_release",
        authority_level=AuthorityLevel.COMPANY_AUTHORED,
        authorship=SourceAuthorship.COMPANY_AUTHORED,
        claim_categories_supported=frozenset({
            CLAIM_COMPANY_GUIDANCE,
        }),
        limitations=(
            "Press releases are company-authored promotional material. They are "
            "contextual evidence, not independent financial verification."
        ),
    ),
    "transcript": SourceKindDefinition(
        source_kind="transcript",
        authority_level=AuthorityLevel.COMPANY_AUTHORED,
        authorship=SourceAuthorship.COMPANY_AUTHORED,
        claim_categories_supported=frozenset({
            CLAIM_FINANCIAL_STATED_FACT,
            CLAIM_COMPANY_GUIDANCE,
        }),
        limitations=(
            "Earnings transcripts contain company-stated guidance and management "
            "commentary. Guidance is forward-looking and subject to material change."
        ),
    ),
    "vendor_fundamentals": SourceKindDefinition(
        source_kind="vendor_fundamentals",
        authority_level=AuthorityLevel.VENDOR_DERIVED,
        authorship=SourceAuthorship.THIRD_PARTY_VENDOR,
        claim_categories_supported=frozenset({
            CLAIM_VENDOR_DERIVED_METRIC,
        }),
        limitations=(
            "Vendor fundamentals are derived/normalized by a third-party provider. "
            "Freshness and provenance metadata are required for meaningful use. "
            "Methodology differences across vendors may cause metric discrepancies."
        ),
    ),
    "vendor_estimates": SourceKindDefinition(
        source_kind="vendor_estimates",
        authority_level=AuthorityLevel.VENDOR_DERIVED,
        authorship=SourceAuthorship.THIRD_PARTY_VENDOR,
        claim_categories_supported=frozenset({
            CLAIM_VENDOR_DERIVED_METRIC,
        }),
        limitations=(
            "Vendor estimates are analyst consensus aggregations by a third-party. "
            "They represent expectations, not guarantees of future performance. "
            "Estimate revisions can be material; freshness is required."
        ),
    ),
    "vendor_calendar": SourceKindDefinition(
        source_kind="vendor_calendar",
        authority_level=AuthorityLevel.VENDOR_DERIVED,
        authorship=SourceAuthorship.THIRD_PARTY_VENDOR,
        claim_categories_supported=frozenset({
            CLAIM_EARNINGS_CALENDAR,
            CLAIM_VENDOR_DERIVED_METRIC,
        }),
        limitations=(
            "Vendor calendar data may change; event dates are subject to revision. "
            "Confirm official dates via company announcements."
        ),
    ),
    "peer_set_def": SourceKindDefinition(
        source_kind="peer_set_def",
        authority_level=AuthorityLevel.VENDOR_DERIVED,
        authorship=SourceAuthorship.THIRD_PARTY_VENDOR,
        claim_categories_supported=frozenset({
            CLAIM_PEER_BENCHMARK,
            CLAIM_VENDOR_DERIVED_METRIC,
        }),
        limitations=(
            "Peer set definitions are vendor-defined or analyst-curated. "
            "Peer composition choices affect benchmark comparisons materially."
        ),
    ),
    "news": SourceKindDefinition(
        source_kind="news",
        authority_level=AuthorityLevel.EDITORIAL_CONTEXT,
        authorship=SourceAuthorship.EDITORIAL,
        claim_categories_supported=frozenset({
            CLAIM_EDITORIAL_CONTEXT,
        }),
        limitations=(
            "News sources are editorial/contextual evidence only. They cannot serve "
            "as authoritative sources of financial truth. Corroboration from official "
            "or vendor-derived sources is required before any financial claim from "
            "news can be elevated."
        ),
    ),
    "other": SourceKindDefinition(
        source_kind="other",
        authority_level=AuthorityLevel.UNKNOWN,
        authorship=SourceAuthorship.UNKNOWN,
        claim_categories_supported=frozenset(),
        limitations=(
            "Source kind 'other' or unrecognized source kinds cannot be classified "
            "without additional provider metadata."
        ),
    ),
}

# All recognized source kinds (subset of migration 017 allowed values).
KNOWN_SOURCE_KINDS: FrozenSet[str] = frozenset(_REGISTRY.keys()) - frozenset({"other"})


# ── Provider-aware overrides (narrow, allowlisted) ────────────────────────────
# These overrides only fire for very specific (source_kind, provider_name,
# source_id-or-source_url) tuples. They exist to classify official-source
# artifacts whose source_kind is still stored as "other" because no dedicated
# DB enum value has been added yet.
#
# Generic source_kind="other" sources from unknown providers stay UNKNOWN —
# the override is never applied unless every match condition is satisfied.

# Allowlist of FRED series IDs that may carry official-source authority for
# macro evidence. Mirrors fred_provider_v1.ALLOWED_MACRO_SERIES; duplicated
# here to keep this module free of provider-module imports.
_FRED_ALLOWED_SERIES: FrozenSet[str] = frozenset({
    "FEDFUNDS", "DFF",
    "DGS10", "DGS2", "T10Y2Y",
    "CPIAUCSL",
    "UNRATE", "PAYEMS",
    "GDP", "GDPC1",
})

_FRED_URL_PREFIXES: tuple[str, ...] = (
    "https://fred.stlouisfed.org/series/",
    "http://fred.stlouisfed.org/series/",
)

_FRED_MACRO_OVERRIDE = SourceKindDefinition(
    source_kind="other",
    authority_level=AuthorityLevel.PRIMARY_AUTHORITY,
    authorship=SourceAuthorship.OFFICIAL_PUBLIC_DATA,
    claim_categories_supported=frozenset({
        CLAIM_OFFICIAL_MACRO_DATA,
    }),
    limitations=(
        "FRED (Federal Reserve Economic Data) macro observations are official "
        "public-sector statistical data. They describe the macroeconomic "
        "environment — rates, inflation, employment, growth — as published by "
        "the Federal Reserve. They are NOT investment recommendations, price "
        "targets, allocation guidance, or Buy/Hold/Trim/Sell directives. Future "
        "performance, price direction, and investment outcome are NEVER claims "
        "this source can support."
    ),
)

# yfinance price-history override — narrow, allowlisted match for technicals artifacts.
# source_kind="other" is the honest choice for yfinance price/history data because no
# dedicated DB enum value exists for price-history/technical data. Without an override,
# the registry would classify this as UNKNOWN authority, suppressing all yfinance
# technicals as SUPPRESSED_UNKNOWN_SOURCE even though the data is fresh, provider-
# derived price history. This override maps it to VENDOR_DERIVED (not higher) so the
# completeness scorer can reach PARTIAL → USABLE_WITH_LIMITATIONS (LIMITED).
# Technicals remain corroborating/auxiliary context only — never primary decision authority.
_YFINANCE_PRICE_HISTORY_OVERRIDE = SourceKindDefinition(
    source_kind="other",
    authority_level=AuthorityLevel.VENDOR_DERIVED,
    authorship=SourceAuthorship.THIRD_PARTY_VENDOR,
    claim_categories_supported=frozenset({
        CLAIM_VENDOR_DERIVED_METRIC,
    }),
    limitations=(
        "yfinance price history is vendor-derived market data (3-month window). "
        "Coverage: price, returns, moving averages, volatility. Auxiliary and "
        "corroborating context only — does not establish fundamental quality, "
        "constitute regulatory evidence, or support Buy/Hold/Trim/Sell authority. "
        "Real-time data not available; yfinance may have intraday/overnight delay."
    ),
)


def _normalize_provider_name(provider: Any) -> str:
    return (str(provider) if provider is not None else "").strip().lower()


def _normalize_source_id(source_id: Any) -> str:
    return (str(source_id) if source_id is not None else "").strip().upper()


def _matches_yfinance_price_history_source(
    source_kind: str,
    provider_name: Any,
    section_reference: Any,
) -> bool:
    """Strict narrow match for yfinance price-history carried as source_kind='other'.

    All of the following must hold:
      - source_kind is "other"
      - provider_name normalizes to "yfinance"
      - section_reference contains "history" (matches the 'yfinance.Ticker.history'
        pattern written by evidence_lane_adapter_v1.adapt_technicals)

    Returns False for generic "other" sources from non-yfinance providers,
    and for yfinance sources without a history section_reference.
    """
    if (source_kind or "").strip() != "other":
        return False
    if _normalize_provider_name(provider_name) != "yfinance":
        return False
    ref = (str(section_reference) if section_reference is not None else "").lower()
    return "history" in ref


def _matches_fred_macro_source(
    source_kind: str,
    provider_name: Any,
    source_id: Any,
    source_url: Any,
) -> bool:
    """Strict, narrow match for an official FRED macro source carried as source_kind='other'.

    All of the following must hold:
      - source_kind is "other"
      - provider_name normalizes to "fred"
      - source_id is an allowlisted FRED series id, OR source_url host/path
        matches https://fred.stlouisfed.org/series/<series_id> with an
        allowlisted id.

    Returns False for anything else, including unknown providers and unknown
    series ids. Generic source_kind="other" stays UNKNOWN.
    """
    if (source_kind or "").strip() != "other":
        return False
    if _normalize_provider_name(provider_name) != "fred":
        return False

    sid_norm = _normalize_source_id(source_id)
    if sid_norm and sid_norm in _FRED_ALLOWED_SERIES:
        return True

    url = (str(source_url) if source_url is not None else "").strip().lower()
    for prefix in _FRED_URL_PREFIXES:
        if url.startswith(prefix):
            tail = url[len(prefix):].split("?")[0].split("/")[0].strip().upper()
            if tail in _FRED_ALLOWED_SERIES:
                return True
    return False


def _resolve_definition_for_source(
    source: Any,
) -> tuple[SourceKindDefinition, bool, Optional[str]]:
    """Return (definition, provider_aware_override_applied, override_id).

    Checks overrides in priority order:
      1. FRED official macro — PRIMARY_AUTHORITY for allowlisted FRED series.
      2. yfinance price history — VENDOR_DERIVED for yfinance Ticker.history data.
    Falls back to the source_kind registry when no override matches.
    """
    sk = (getattr(source, "source_kind", None) or "other").strip() or "other"
    if _matches_fred_macro_source(
        source_kind=sk,
        provider_name=getattr(source, "provider_name", None),
        source_id=getattr(source, "source_id", None),
        source_url=getattr(source, "source_url", None),
    ):
        return _FRED_MACRO_OVERRIDE, True, "fred_macro_official_v1"
    if _matches_yfinance_price_history_source(
        source_kind=sk,
        provider_name=getattr(source, "provider_name", None),
        section_reference=getattr(source, "section_reference", None),
    ):
        return _YFINANCE_PRICE_HISTORY_OVERRIDE, True, "yfinance_price_history_vendor_v1"
    return get_source_kind_definition(sk), False, None


# ── Assessment dataclasses ────────────────────────────────────────────────────


@dataclass
class SourceCredibilityAssessment:
    """Full deterministic credibility assessment for one artifact's sources.

    Replayable: same sources list always produces the same assessment.
    Never contains Buy/Hold/Trim/Sell, price target, conviction, or allocation.
    """
    registry_version: str
    has_sources: bool
    is_insufficient: bool          # True when no sources OR all UNKNOWN
    source_count: int
    source_kind_counts: Dict[str, int]
    strongest_authority_level: str  # AuthorityLevel value string
    per_source_assessments: List[Dict[str, Any]]
    aggregate_limitations: List[str]
    claim_categories_any_source_supports: List[str]
    claim_categories_no_source_can_support: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Plain dict for JSON serialization into artifact payload.

        Key 'source_credibility_assessment' is NOT in WORKER_FORBIDDEN_PAYLOAD_KEYS.
        """
        return {
            "registry_version": self.registry_version,
            "has_sources": self.has_sources,
            "is_insufficient": self.is_insufficient,
            "source_count": self.source_count,
            "source_kind_counts": self.source_kind_counts,
            "strongest_authority_level": self.strongest_authority_level,
            "per_source_assessments": self.per_source_assessments,
            "aggregate_limitations": self.aggregate_limitations,
            "claim_categories_any_source_supports": self.claim_categories_any_source_supports,
            "claim_categories_no_source_can_support": self.claim_categories_no_source_can_support,
        }


# ── Public registry API ───────────────────────────────────────────────────────


def get_source_kind_definition(source_kind: str) -> SourceKindDefinition:
    """Return the canonical definition for source_kind; falls back to 'other'."""
    return _REGISTRY.get(source_kind, _REGISTRY["other"])


def assess_artifact_sources(
    sources: List[Any],
) -> SourceCredibilityAssessment:
    """Deterministically assess credibility for a list of SourceRecord-compatible objects.

    Args:
        sources: List of objects with at least .source_kind and .provider_name
                 attributes (compatible with contracts.SourceRecord).
                 Empty list → UNKNOWN/INSUFFICIENT assessment.

    Returns:
        SourceCredibilityAssessment — always non-None, fully replayable.
        Same inputs always produce the same output.
    """
    if not sources:
        return SourceCredibilityAssessment(
            registry_version=SOURCE_CREDIBILITY_REGISTRY_VERSION,
            has_sources=False,
            is_insufficient=True,
            source_count=0,
            source_kind_counts={},
            strongest_authority_level=AuthorityLevel.UNKNOWN.value,
            per_source_assessments=[],
            aggregate_limitations=[
                "No sources provided — credibility is UNKNOWN/INSUFFICIENT."
            ],
            claim_categories_any_source_supports=[],
            claim_categories_no_source_can_support=sorted(_CLAIMS_NEVER_SUPPORTED),
        )

    per_source: List[Dict[str, Any]] = []
    authority_levels: List[AuthorityLevel] = []
    kind_counts: Dict[str, int] = {}
    all_supported: set = set()
    limitations_seen: List[str] = []
    limitations_set: set = set()

    for idx, source in enumerate(sources):
        sk = (getattr(source, "source_kind", None) or "other").strip() or "other"
        provider = getattr(source, "provider_name", None)

        defn, override_applied, override_id = _resolve_definition_for_source(source)
        authority_levels.append(defn.authority_level)
        kind_counts[sk] = kind_counts.get(sk, 0) + 1
        all_supported.update(defn.claim_categories_supported)

        if defn.limitations not in limitations_set:
            limitations_set.add(defn.limitations)
            limitations_seen.append(defn.limitations)

        per_source.append({
            "source_index": idx,
            "source_kind": sk,
            "provider_name": provider,
            "authority_level": defn.authority_level.value,
            "authorship": defn.authorship.value,
            "claim_categories_supported": sorted(defn.claim_categories_supported),
            "claim_categories_never_supported": sorted(_CLAIMS_NEVER_SUPPORTED),
            "limitations": defn.limitations,
            "is_known_source_kind": sk in KNOWN_SOURCE_KINDS,
            "provider_aware_override_applied": override_applied,
            "provider_aware_override_id": override_id,
        })

    strongest = max(authority_levels, key=lambda lvl: _AUTHORITY_RANK.get(lvl, 0))
    is_insufficient = strongest == AuthorityLevel.UNKNOWN

    return SourceCredibilityAssessment(
        registry_version=SOURCE_CREDIBILITY_REGISTRY_VERSION,
        has_sources=True,
        is_insufficient=is_insufficient,
        source_count=len(sources),
        source_kind_counts=kind_counts,
        strongest_authority_level=strongest.value,
        per_source_assessments=per_source,
        aggregate_limitations=limitations_seen,
        claim_categories_any_source_supports=sorted(all_supported),
        claim_categories_no_source_can_support=sorted(_CLAIMS_NEVER_SUPPORTED),
    )
