"""Stage 9F.2b — ETF Holdings Provider Registry v1.

Central registry for ETF holdings providers. Each provider declares:
  - provider_id and source_type (sec_nport / issuer_official)
  - supported tickers
  - url_resolver_strategy: how to locate the holdings file
  - parser_strategy: how to parse the file format
  - identity_check_strategy: how to verify holdings belong to the requested ETF
  - weight_field_strategy: how to extract holding weights
  - freshness_strategy: how to extract the as-of date
  - enabled_for_diagnostics: True → runs in the registry diagnostic endpoint
  - enabled_for_canonical: False for ALL providers in this PR

Ticker → provider priority order (first identity-verified result wins):
  SPY, QQQ: sec_nport_v1 first (standalone trust, identity proven)
  XLE:      spdr_official_v1 first, then sec_nport_v1
  GLD:      gld_commodity_v1 only (commodity/bullion — no equity basket)
  VOO/VTI/VGT/VHT/VIS/VXUS/VYM: vanguard_official_v1 first, then sec_nport_v1
  SCHD:     schwab_official_v1 first, then sec_nport_v1

Normalized result contract (ETFHoldingsResult):
  All providers must return this shape. No raw full holdings in diagnostic
  output — only holdings_count and a sample (max 5 names).

Hard constraints:
  - enabled_for_canonical=False for every provider in this PR.
  - canonical_ready=False and safe_for_decision=False in every result.
  - No paid providers. No LLM. No network or DB IO at import time.
  - Identity false negative is acceptable; false positive is not.
  - If identity cannot be proven, holdings must not be returned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PROVIDER_REGISTRY_VERSION = "stage9f2b_v1"

# ── Normalized result contract ─────────────────────────────────────────────────


@dataclass
class ETFHoldingsResult:
    """Normalized output of one ETF holdings provider attempt.

    All providers (SEC NPORT and issuer-official) return this shape.
    holdings_count is the full parsed count; sample_holding_names are the
    first max-5 names only — never the full holdings payload.

    fetch_status values:
      success                           — holdings parsed and identity verified.
      commodity_trust_no_equity_holdings — GLD / commodity trust; no basket.
      source_url_fetch_error            — HTTP error fetching issuer file.
      source_url_not_validated          — URL strategy not yet confirmed; skip.
      identity_not_proven               — file fetched but identity not provable.
      parser_not_supported              — file format not parseable by this adapter.
      source_shape_changed              — required columns/structure absent.
      sec_nport_<status>                — wraps SEC NPORT fetch_status verbatim.
      error                             — unexpected exception; defensive catch.
    """

    ticker: str
    provider_id: str
    source_type: str                     # "sec_nport" | "issuer_official"
    source_url: Optional[str]            # URL attempted (or None for special cases)
    source_authority: str                # "sec_primary_authority" | "issuer_official"
    as_of_date: Optional[str]            # YYYY-MM-DD or ISO string when available
    holdings_count: int                  # total parsed holding rows
    sample_holding_names: list[str]      # first max-5 names only
    weights_available: bool
    weight_basis: str                    # "percent" | "market_value_derived" | "unavailable"
    identity_verified: bool
    identity_basis: Optional[str]        # human-readable explanation
    freshness_status: str                # "fresh" | "stale" | "unknown" | "not_applicable"
    fetch_status: str
    error_message: Optional[str] = None
    limitations: list[str] = field(default_factory=list)
    detected_fund_name: Optional[str] = None  # raw fund name from CSV metadata; None if not parsed
    # Governance invariants — never mutated by any provider in this PR
    canonical_ready: bool = False
    safe_for_decision: bool = False


# ── Provider record ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ETFHoldingsProviderRecord:
    """Static metadata for one ETF holdings provider.

    No network calls, no DB, no env reads — pure registry metadata.
    """

    provider_id: str
    source_type: str                     # "sec_nport" | "issuer_official"
    source_authority: str                # "sec_primary_authority" | "issuer_official"
    issuer_family: str                   # "vanguard" | "ssga_spdr" | "schwab" | "invesco" | "various"
    supported_tickers: frozenset[str]
    url_resolver_strategy: str
    parser_strategy: str
    identity_check_strategy: str
    weight_field_strategy: str
    freshness_strategy: str
    enabled_for_diagnostics: bool
    enabled_for_canonical: bool          # False for ALL providers in Stage 9F.2b
    limitations: tuple[str, ...] = ()
    notes: str = ""


# ── Registered providers ───────────────────────────────────────────────────────

_PROVIDER_RECORDS: dict[str, ETFHoldingsProviderRecord] = {

    "sec_nport_v1": ETFHoldingsProviderRecord(
        provider_id="sec_nport_v1",
        source_type="sec_nport",
        source_authority="sec_primary_authority",
        issuer_family="various",
        supported_tickers=frozenset({
            "SPY", "QQQ", "XLE", "GLD",
            "VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM", "SCHD",
        }),
        url_resolver_strategy="sec_edgar_nport_p",
        parser_strategy="nport_xml_v1",
        identity_check_strategy="series_name_match_or_standalone_trust",
        weight_field_strategy="pct_val_from_nport_xml",
        freshness_strategy="report_period_date_from_nport_xml",
        enabled_for_diagnostics=True,
        enabled_for_canonical=False,
        limitations=(
            "Series ETFs (Vanguard/SCHD) require series name match in NPORT genInfo; "
            "failed for VGT/VHT/VIS/VOO/VTI/VXUS/VYM/SCHD in Stage 9F.2a runtime.",
            "~60-day lag from quarter-end reporting period.",
            "XLE: scanned 12 SPDR Series Trust filings but did not find Energy Select Sector.",
            "Identity not provable without correct parent-registrant CIK + series name match.",
        ),
        notes="Existing Stage 9F.2a provider. Kept intact. Identity gate preserved.",
    ),

    "vanguard_official_v1": ETFHoldingsProviderRecord(
        provider_id="vanguard_official_v1",
        source_type="issuer_official",
        source_authority="issuer_official",
        issuer_family="vanguard",
        supported_tickers=frozenset({
            "VOO", "VTI", "VGT", "VHT", "VIS", "VXUS", "VYM",
        }),
        url_resolver_strategy="vanguard_investor_csv_v1",
        parser_strategy="vanguard_etf_csv_v1",
        identity_check_strategy="fund_name_in_csv_metadata_rows",
        weight_field_strategy="weight_pct_column_vanguard",
        freshness_strategy="as_of_date_from_csv_metadata",
        enabled_for_diagnostics=True,
        enabled_for_canonical=False,
        limitations=(
            "URL pattern needs post-deploy validation per-ticker.",
            "CSV schema subject to Vanguard layout changes.",
            "Weight column name varies across Vanguard fund families.",
            "Identity check: fund name must appear in file metadata rows.",
        ),
        notes=(
            "Vanguard publishes ETF holdings CSVs via their investor portal. "
            "URL template: investor.vanguard.com/content/dam/fas-portspec-images/"
            "downloads/etf-shares/{TICKER}_QuantDataFundHoldings.csv. "
            "Post-deploy: verify CSV is accessible and identity provable."
        ),
    ),

    "spdr_official_v1": ETFHoldingsProviderRecord(
        provider_id="spdr_official_v1",
        source_type="issuer_official",
        source_authority="issuer_official",
        issuer_family="ssga_spdr",
        supported_tickers=frozenset({"XLE", "SPY"}),
        url_resolver_strategy="ssga_csv_v1",
        parser_strategy="ssga_etf_csv_v1",
        identity_check_strategy="fund_name_in_csv_metadata_rows",
        weight_field_strategy="weight_pct_column_ssga",
        freshness_strategy="as_of_date_from_csv_metadata",
        enabled_for_diagnostics=True,
        enabled_for_canonical=False,
        limitations=(
            "SSGA CSV URL availability per-ticker needs post-deploy confirmation.",
            "Fund name in file header required for identity verification.",
            "Weight column name may be 'Weight' or '% of Fund'.",
        ),
        notes=(
            "SSGA publishes ETF holdings CSVs. URL template: "
            "ssga.com/library-content/products/fund-data/etfs/us/"
            "holdings-daily-us-en-{ticker_lower}.csv. "
            "SPY already identity-verified via SEC NPORT (standalone trust); "
            "spdr_official_v1 is secondary for SPY."
        ),
    ),

    "schwab_official_v1": ETFHoldingsProviderRecord(
        provider_id="schwab_official_v1",
        source_type="issuer_official",
        source_authority="issuer_official",
        issuer_family="schwab",
        supported_tickers=frozenset({"SCHD"}),
        url_resolver_strategy="schwab_csv_v1",
        parser_strategy="schwab_etf_csv_v1",
        identity_check_strategy="fund_name_in_csv_metadata_rows",
        weight_field_strategy="weight_pct_column_schwab",
        freshness_strategy="as_of_date_from_csv_metadata",
        enabled_for_diagnostics=True,
        enabled_for_canonical=False,
        limitations=(
            "Schwab ETF holdings CSV URL is not publicly stable — needs post-deploy validation.",
            "URL pattern changes with Schwab site updates.",
            "Fund name must be present in file metadata for identity verification.",
        ),
        notes=(
            "Schwab Strategic Trust publishes SCHD holdings. Direct CSV URL is not "
            "publicly documented in a stable form. Post-deploy: locate official CSV "
            "download URL from Schwab ETF product page and validate identity."
        ),
    ),

    "invesco_official_v1": ETFHoldingsProviderRecord(
        provider_id="invesco_official_v1",
        source_type="issuer_official",
        source_authority="issuer_official",
        issuer_family="invesco",
        supported_tickers=frozenset({"QQQ"}),
        url_resolver_strategy="invesco_csv_v1",
        parser_strategy="invesco_etf_csv_v1",
        identity_check_strategy="fund_name_in_csv_metadata_rows",
        weight_field_strategy="weight_pct_column_invesco",
        freshness_strategy="as_of_date_from_csv_metadata",
        enabled_for_diagnostics=True,
        enabled_for_canonical=False,
        limitations=(
            "QQQ already identity-verified via SEC NPORT (standalone trust); "
            "invesco_official_v1 is secondary.",
            "Invesco holdings CSV URL needs post-deploy validation.",
        ),
        notes=(
            "Invesco QQQ Trust is a standalone trust; SEC NPORT is primary and proven. "
            "invesco_official_v1 is registered as a secondary/fallback provider."
        ),
    ),

    "gld_commodity_v1": ETFHoldingsProviderRecord(
        provider_id="gld_commodity_v1",
        source_type="issuer_official",
        source_authority="issuer_official",
        issuer_family="ssga_spdr",
        supported_tickers=frozenset({"GLD"}),
        url_resolver_strategy="commodity_trust_special_case",
        parser_strategy="commodity_trust_no_equity_holdings",
        identity_check_strategy="commodity_trust_assumed",
        weight_field_strategy="unavailable",
        freshness_strategy="not_applicable",
        enabled_for_diagnostics=True,
        enabled_for_canonical=False,
        limitations=(
            "GLD holds physical gold bullion — no equity holdings basket.",
            "No NPORT-P equity holdings expected or returned.",
            "canonical_ready=False and safe_for_decision=False permanently for GLD.",
        ),
        notes="SPDR Gold Trust commodity special case. Mirrors existing SEC NPORT behavior.",
    ),
}


# ── Ticker → provider priority ────────────────────────────────────────────────
# Ordered list: first identity-verified result wins.
# SEC NPORT first for tickers where it's confirmed (SPY, QQQ).
# Issuer-official first for tickers where SEC NPORT is insufficient (Vanguard, SCHD, XLE).

_TICKER_PROVIDER_PRIORITY: dict[str, list[str]] = {
    "SPY":  ["sec_nport_v1", "spdr_official_v1"],
    "QQQ":  ["sec_nport_v1", "invesco_official_v1"],
    "XLE":  ["spdr_official_v1", "sec_nport_v1"],
    "GLD":  ["gld_commodity_v1"],
    "VOO":  ["vanguard_official_v1", "sec_nport_v1"],
    "VTI":  ["vanguard_official_v1", "sec_nport_v1"],
    "VGT":  ["vanguard_official_v1", "sec_nport_v1"],
    "VHT":  ["vanguard_official_v1", "sec_nport_v1"],
    "VIS":  ["vanguard_official_v1", "sec_nport_v1"],
    "VXUS": ["vanguard_official_v1", "sec_nport_v1"],
    "VYM":  ["vanguard_official_v1", "sec_nport_v1"],
    "SCHD": ["schwab_official_v1", "sec_nport_v1"],
}

_ETF_UNIVERSE: frozenset[str] = frozenset(_TICKER_PROVIDER_PRIORITY.keys())


# ── Public API ────────────────────────────────────────────────────────────────


def get_provider(provider_id: str) -> Optional[ETFHoldingsProviderRecord]:
    """Return the provider record for a provider_id, or None."""
    return _PROVIDER_RECORDS.get(provider_id)


def list_all_providers() -> list[ETFHoldingsProviderRecord]:
    """Return all registered provider records."""
    return list(_PROVIDER_RECORDS.values())


def get_providers_for_ticker(ticker: str) -> list[ETFHoldingsProviderRecord]:
    """Return ordered list of provider records for a ticker (priority order).

    Returns empty list if ticker is not in the ETF universe.
    """
    priority = _TICKER_PROVIDER_PRIORITY.get(ticker.upper().strip(), [])
    return [_PROVIDER_RECORDS[pid] for pid in priority if pid in _PROVIDER_RECORDS]


def get_etf_universe() -> frozenset[str]:
    """Return the set of all tickers registered in the provider registry."""
    return _ETF_UNIVERSE


def registry_summary() -> dict:
    """Return a compact summary of the registry for diagnostic use."""
    return {
        "version": PROVIDER_REGISTRY_VERSION,
        "provider_count": len(_PROVIDER_RECORDS),
        "etf_universe_count": len(_ETF_UNIVERSE),
        "etf_universe": sorted(_ETF_UNIVERSE),
        "providers": [
            {
                "provider_id": r.provider_id,
                "source_type": r.source_type,
                "source_authority": r.source_authority,
                "issuer_family": r.issuer_family,
                "supported_tickers_count": len(r.supported_tickers),
                "supported_tickers": sorted(r.supported_tickers),
                "enabled_for_diagnostics": r.enabled_for_diagnostics,
                "enabled_for_canonical": r.enabled_for_canonical,
                "url_resolver_strategy": r.url_resolver_strategy,
                "parser_strategy": r.parser_strategy,
                "identity_check_strategy": r.identity_check_strategy,
                "limitations": list(r.limitations),
            }
            for r in _PROVIDER_RECORDS.values()
        ],
        "ticker_priority": {
            t: list(pids)
            for t, pids in _TICKER_PROVIDER_PRIORITY.items()
        },
    }
