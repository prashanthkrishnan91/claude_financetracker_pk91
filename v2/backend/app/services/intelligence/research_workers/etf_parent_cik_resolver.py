"""Stage 9F.2a — ETF NPORT Parent-Registrant CIK Resolver.

Why ETFs need parent-registrant CIK resolution
-----------------------------------------------
SEC EDGAR's company_tickers.json maps an ETF ticker to the entity CIK that
was originally registered for that share class (the "series CIK").  For many
ETF providers — especially Vanguard — this series CIK belongs to the share-class
entity, which has **no NPORT-P filings**.  NPORT-P is filed by the PARENT
REGISTRANT (the umbrella fund company that contains many series/share classes).

Example:
  VOO ticker → company_tickers.json → CIK 0001480511
    (Vanguard S&P 500 ETF share-class entity — no NPORT-P in submissions)
  Correct filer: VANGUARD INDEX FUNDS, CIK 0000764180
    (parent registrant that files NPORT-P for all its series, including VOO)

Symptoms of a wrong (series) CIK:
  - fetch_status = "no_nport_filing"   (CIK resolves but entity has no NPORT-P)
  - fetch_status = "sec_error" / 404   (series CIK may not exist as a filer)

Registrants that file NPORT-P directly (single-series trusts):
  - SPY, QQQ are standalone trust registrants that ARE their own filer.
    Their share-class CIK == NPORT-P filing CIK.  No parent lookup needed.
    Identity is assumed (standalone_trust=True).

Series-based multi-fund registrants (XLE, Vanguard, SCHD):
  - These ETFs are series of a larger registrant.  The registrant files one
    NPORT-P per series.  We must verify that the parsed NPORT series name
    matches the expected series name for the requested ticker before accepting
    the holdings as identity-certified.  expected_series_names drives this check.

This module provides a static, vetted map from ETF ticker → parent registrant.
Live SEC calls are NOT made here.  Provenance metadata is attached to every
entry so the source and confidence level are always visible.

Provenance notation:
  "Confirmed" — resolved and validated by a successful diagnostic NPORT-P fetch.
  "Candidate" — best-available parent CIK from SEC EDGAR records; requires
                post-deploy live validation to confirm.

Identity contract (Stage 9F.2a identity-certification repair):
  standalone_trust=True   → identity assumed; no expected_series_names check.
  commodity_trust=True    → commodity trust; no equity holdings expected.
  expected_series_names   → one or more fund/series names to match in NPORT genInfo.
                            If the parsed seriesName does not match, the result is
                            series_identity_not_proven (not safe success).
  candidate_ciks          → additional CIK candidates to try after the primary fails
                            with no_nport_filing or retriable sec_error.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ETFParentRegistrantEntry:
    """Static record linking an ETF ticker to its NPORT-P filing entity.

    Fields:
        ticker                  ETF ticker symbol (uppercase).
        parent_name             SEC registrant entity name that files NPORT-P.
        parent_cik              10-digit zero-padded CIK of that registrant.
        provenance              Source / confidence note for this mapping.
        is_parent_registrant    True when the ETF uses a separate parent filer.
                                False when the ETF is itself the NPORT-P registrant
                                (single-series trusts like SPY, QQQ).
        expected_status         "confirmed"   — validated by live diagnostic.
                                "candidate"   — unverified; requires post-deploy check.
                                "no_nport"    — commodity/non-equity trust; no NPORT-P.

    Identity hint fields (Stage 9F.2a identity-certification):
        expected_series_names   Tuple of expected fund/series names as they appear
                                (or should appear) in the NPORT genInfo.seriesName XML
                                field.  Used for normalized substring matching.  For
                                standalone trusts, leave empty (standalone_trust=True).
        standalone_trust        True when the ETF IS the sole registrant (no multi-series
                                ambiguity).  Identity is assumed without a series name
                                check.  SPY and QQQ are the current confirmed examples.
        commodity_trust         True for commodity/bullion trusts with no equity
                                holdings (GLD).  Expected result is always
                                commodity_trust_or_no_nport_data.
        candidate_ciks          Additional CIK candidates to try if the primary
                                parent_cik fails with no_nport_filing or retriable
                                sec_error.  Tried in order after parent_cik fails.
    """

    ticker: str
    parent_name: str
    parent_cik: str
    provenance: str
    is_parent_registrant: bool
    expected_status: str
    # Identity hint fields — all have defaults so existing code still compiles.
    expected_series_names: tuple[str, ...] = ()
    standalone_trust: bool = False
    commodity_trust: bool = False
    candidate_ciks: tuple[str, ...] = ()


# ── Static ETF parent-registrant map ──────────────────────────────────────────
#
# Ordering: confirmed single-series trusts first, then Vanguard parent-registrant
# ETFs grouped by parent, then Schwab.
#
# CIK format: 10-digit zero-padded strings (canonical SEC EDGAR format).

_ETF_PARENT_REGISTRANT_MAP: dict[str, ETFParentRegistrantEntry] = {

    # ── SSGA/SPDR — single-series standalone trust ────────────────────────────
    # SPY is a standalone trust: the trust itself is the NPORT-P registrant.
    # No multi-series ambiguity. Identity assumed via standalone_trust=True.
    "SPY": ETFParentRegistrantEntry(
        ticker="SPY",
        parent_name="SPDR S&P 500 ETF TRUST",
        parent_cik="0000884394",
        provenance=(
            "Confirmed. SPDR S&P 500 ETF Trust is a standalone trust (single-series"
            " registrant) that files NPORT-P directly. Validated: diagnostic"
            " holdings_count=503."
        ),
        is_parent_registrant=False,
        expected_status="confirmed",
        standalone_trust=True,
        expected_series_names=(),
    ),

    # XLE is a series of SPDR Series Trust (multi-series registrant).
    # Identity must be verified via series name match in NPORT genInfo.
    "XLE": ETFParentRegistrantEntry(
        ticker="XLE",
        parent_name="SPDR SERIES TRUST",
        parent_cik="0001168164",
        provenance=(
            "Confirmed. XLE (Energy Select Sector SPDR Fund) is a series of SPDR"
            " Series Trust; the Trust is the NPORT-P filer. Validated:"
            " diagnostic holdings_count=1250. Series identity requires verification"
            " via genInfo.seriesName match against expected_series_names."
        ),
        is_parent_registrant=False,
        expected_status="confirmed",
        standalone_trust=False,
        expected_series_names=(
            "Energy Select Sector SPDR Fund",
            "Energy Select Sector",
        ),
    ),

    # ── Invesco — standalone trust ────────────────────────────────────────────
    # QQQ is a standalone trust: the trust itself is the NPORT-P registrant.
    "QQQ": ETFParentRegistrantEntry(
        ticker="QQQ",
        parent_name="INVESCO QQQ TRUST SERIES 1",
        parent_cik="0001067839",
        provenance=(
            "Confirmed. Invesco QQQ Trust, Series 1 is a standalone trust that files"
            " NPORT-P directly. Validated: diagnostic holdings_count=101."
        ),
        is_parent_registrant=False,
        expected_status="confirmed",
        standalone_trust=True,
        expected_series_names=(),
    ),

    # ── SPDR Gold Trust — commodity trust, no equity holdings ────────────────
    # GLD holds physical gold bullion and does not file NPORT-P for equity.
    "GLD": ETFParentRegistrantEntry(
        ticker="GLD",
        parent_name="SPDR GOLD TRUST",
        parent_cik="0001222333",
        provenance=(
            "Confirmed. SPDR Gold Trust holds physical gold; no equity holdings."
            " Expected status: commodity_trust_or_no_nport_data."
        ),
        is_parent_registrant=False,
        expected_status="no_nport",
        commodity_trust=True,
        expected_series_names=(),
    ),

    # ── Vanguard — PARENT REGISTRANT required ─────────────────────────────────
    #
    # Vanguard ETFs are share classes of underlying index funds that are
    # themselves series of large umbrella registrant entities.
    #
    # company_tickers.json returns the series/share-class CIK (no NPORT-P).
    # The umbrella parent registrant is the correct NPORT-P filer.
    #
    # Identity verification is required: the parent registrant files separate
    # NPORT-P documents for each series.  expected_series_names provides the
    # matching hint so we can verify that the found filing belongs to the
    # specific ETF/fund requested, not another series of the same registrant.
    #
    # Previously observed wrong share-class CIKs → "no_nport_filing":
    #   VOO → 0001480511, VTI → 0000732834, VGT → 0001137774,
    #   VYM → 0001383310,  SCHD → 0001510588
    # VHT, VIS, VXUS had no seed-map entry → "missing_cik".
    #
    # Parent registrant CIKs are "candidate" status — confirmed by post-deploy
    # live diagnostic validation (see post-deploy instructions in PR body).

    # VANGUARD INDEX FUNDS — parent for US broad-market index ETFs
    "VOO": ETFParentRegistrantEntry(
        ticker="VOO",
        parent_name="VANGUARD INDEX FUNDS",
        parent_cik="0000764180",
        provenance=(
            "Candidate. VOO is ETF share class of Vanguard S&P 500 Index Fund, a"
            " series of VANGUARD INDEX FUNDS (CIK 0000764180). Wrong share-class CIK"
            " 0001480511 produced no_nport_filing. Post-deploy: verify NPORT-P exists"
            " under 0000764180 and series name matches expected."
        ),
        is_parent_registrant=True,
        expected_status="candidate",
        expected_series_names=(
            "Vanguard S&P 500 Index Fund",
            "Vanguard 500 Index Fund",
        ),
    ),
    "VTI": ETFParentRegistrantEntry(
        ticker="VTI",
        parent_name="VANGUARD INDEX FUNDS",
        parent_cik="0000764180",
        provenance=(
            "Candidate. VTI is ETF share class of Vanguard Total Stock Market Index"
            " Fund, a series of VANGUARD INDEX FUNDS (CIK 0000764180). Wrong"
            " share-class CIK 0000732834 produced no_nport_filing. Post-deploy:"
            " verify NPORT-P exists under 0000764180 and series name matches expected."
        ),
        is_parent_registrant=True,
        expected_status="candidate",
        expected_series_names=(
            "Vanguard Total Stock Market Index Fund",
            "Vanguard Total Stock Market",
        ),
    ),

    # VANGUARD WORLD FUND — parent for Vanguard sector ETFs
    # These three ETFs share the same parent CIK.  The parent registrant files
    # separate NPORT-P filings for each series.  Identity verification is critical
    # here: without it, all three tickers could blindly succeed from the same
    # filing (the most recent one) regardless of which series it covers.
    "VGT": ETFParentRegistrantEntry(
        ticker="VGT",
        parent_name="VANGUARD WORLD FUND",
        parent_cik="0000036405",
        provenance=(
            "Candidate. VGT is ETF share class of Vanguard Information Technology"
            " Index Fund, a series of VANGUARD WORLD FUND (CIK 0000036405). Wrong"
            " share-class CIK 0001137774 produced no_nport_filing. Post-deploy:"
            " verify NPORT-P series name matches expected."
        ),
        is_parent_registrant=True,
        expected_status="candidate",
        expected_series_names=(
            "Vanguard Information Technology Index Fund",
            "Vanguard IT Index Fund",
        ),
    ),
    "VHT": ETFParentRegistrantEntry(
        ticker="VHT",
        parent_name="VANGUARD WORLD FUND",
        parent_cik="0000036405",
        provenance=(
            "Candidate. VHT is ETF share class of Vanguard Health Care Index Fund, a"
            " series of VANGUARD WORLD FUND (CIK 0000036405). Previously missing_cik"
            " (not in seed map). Post-deploy: verify NPORT-P series name matches expected."
        ),
        is_parent_registrant=True,
        expected_status="candidate",
        expected_series_names=(
            "Vanguard Health Care Index Fund",
            "Vanguard Health Care",
        ),
    ),
    "VIS": ETFParentRegistrantEntry(
        ticker="VIS",
        parent_name="VANGUARD WORLD FUND",
        parent_cik="0000036405",
        provenance=(
            "Candidate. VIS is ETF share class of Vanguard Industrials Index Fund, a"
            " series of VANGUARD WORLD FUND (CIK 0000036405). Previously missing_cik"
            " (not in seed map). Post-deploy: verify NPORT-P series name matches expected."
        ),
        is_parent_registrant=True,
        expected_status="candidate",
        expected_series_names=(
            "Vanguard Industrials Index Fund",
            "Vanguard Industrials",
        ),
    ),

    # VANGUARD WHITEHALL FUNDS — parent for Vanguard dividend ETFs
    "VYM": ETFParentRegistrantEntry(
        ticker="VYM",
        parent_name="VANGUARD WHITEHALL FUNDS",
        parent_cik="0000916548",
        provenance=(
            "Candidate. VYM is ETF share class of Vanguard High Dividend Yield Index"
            " Fund, a series of VANGUARD WHITEHALL FUNDS (CIK 0000916548). Wrong"
            " share-class CIK 0001383310 produced no_nport_filing. Post-deploy:"
            " verify NPORT-P exists under 0000916548 and series name matches expected."
        ),
        is_parent_registrant=True,
        expected_status="candidate",
        expected_series_names=(
            "Vanguard High Dividend Yield Index Fund",
            "Vanguard High Dividend Yield",
        ),
    ),

    # Vanguard Total International Stock Index Fund — parent for VXUS
    "VXUS": ETFParentRegistrantEntry(
        ticker="VXUS",
        parent_name="VANGUARD INTERNATIONAL EQUITY INDEX FUNDS",
        parent_cik="0001004244",
        provenance=(
            "Candidate — requires post-deploy verification. VXUS is ETF share class"
            " of Vanguard Total International Stock Index Fund. Best-available parent"
            " CIK 0001004244 (VANGUARD INTERNATIONAL EQUITY INDEX FUNDS); previously"
            " missing_cik (not in seed map). Validate: check submissions for NPORT-P"
            " under this CIK. If no_nport_filing, run SEC EDGAR company search for"
            " the correct parent registrant."
        ),
        is_parent_registrant=True,
        expected_status="candidate",
        expected_series_names=(
            "Vanguard Total International Stock Index Fund",
            "Vanguard Total International Stock",
        ),
    ),

    # ── Schwab — PARENT REGISTRANT required ───────────────────────────────────
    # SCHD is a series of Schwab Strategic Trust; the Trust is the NPORT-P filer.
    "SCHD": ETFParentRegistrantEntry(
        ticker="SCHD",
        parent_name="SCHWAB STRATEGIC TRUST",
        parent_cik="0001477379",
        provenance=(
            "Candidate. SCHD (Schwab U.S. Dividend Equity ETF) is a series of"
            " SCHWAB STRATEGIC TRUST (CIK 0001477379). Wrong share-class CIK"
            " 0001510588 produced no_nport_filing. Post-deploy: verify NPORT-P"
            " exists under 0001477379 and series name matches expected."
        ),
        is_parent_registrant=True,
        expected_status="candidate",
        expected_series_names=(
            "Schwab U.S. Dividend Equity ETF",
            "Schwab US Dividend Equity ETF",
        ),
    ),
}


# ── Public API ────────────────────────────────────────────────────────────────


def resolve_etf_parent_cik(
    ticker: str,
) -> Optional[tuple[str, str, bool]]:
    """Return (parent_cik, parent_name, is_parent_registrant) for a ticker.

    Ticker lookup is case-insensitive.  Returns None if the ticker is not in
    the vetted static map.

    Callers should prefer ``get_parent_registrant_entry`` when they need the
    full entry (provenance, expected_status, etc.).
    """
    entry = _ETF_PARENT_REGISTRANT_MAP.get(ticker.upper().strip())
    if entry is None:
        return None
    return entry.parent_cik, entry.parent_name, entry.is_parent_registrant


def get_parent_registrant_entry(ticker: str) -> Optional[ETFParentRegistrantEntry]:
    """Return the full ETFParentRegistrantEntry for a ticker, or None."""
    return _ETF_PARENT_REGISTRANT_MAP.get(ticker.upper().strip())
