"""Stage 9F.2a — SEC NPORT-P ETF Holdings Provider v1.

Keyless, synchronous SEC EDGAR provider for ETF fund holdings from NPORT-P
(or NPORT-EX) regulatory filings.

NPORT-P is the SEC monthly portfolio holdings report for large registered
investment companies (>$1B AUM). Holdings for the third month of each quarter
are publicly disclosed with approximately a 60-day filing lag.

This is an official, PRIMARY_AUTHORITY source — published directly on SEC EDGAR
with no API key required. SEC User-Agent header is required per SEC terms.

Fetch sequence (up to _MAX_REQUESTS_PER_TICKER HTTP calls):
  1. Resolve ticker → CIK: static seed map (bootstrap), then company_tickers.json.
  2. Fetch submissions API: https://data.sec.gov/submissions/CIK{cik}.json
     — scan recent filings for the latest NPORT-P or NPORT-EX form.
  3. Fetch filing primary document from EDGAR Archives and parse XML for holdings.

Explicit failure states (never raises; always returns NportProviderResult):
  success                          — holdings parsed from NPORT-P.
  missing_cik                      — CIK not in seed map and not in company_tickers.json.
  no_nport_filing                  — no NPORT-P/NPORT-EX filing found in submissions.
  filing_not_parseable             — XML document could not be fetched or parsed.
  no_holdings_found                — filing parsed; zero holding elements present.
  sec_error                        — SEC API returned a non-200 HTTP error.
  timeout                          — HTTP request timed out.
  commodity_trust_or_no_nport_data — trust type (e.g. GLD bullion) without equity holdings.
  error                            — defence-in-depth catch for unexpected exceptions.

Hard constraints:
  - Never raises; always returns NportProviderResult.
  - Requires SEC User-Agent header (no anonymous calls permitted).
  - Total HTTP requests capped at _MAX_REQUESTS_PER_TICKER (default 3).
  - No raw XML, no raw filing text stored or passed downstream.
  - Never fabricates holdings, weights, values, or identifiers.
  - Injectable http_get_fn and cik_lookup_fn for deterministic testing.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── SEC EDGAR endpoint constants ───────────────────────────────────────────────

_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL_TPL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FILING_URL_TPL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{primary_doc}"
)

_MAX_REQUESTS_PER_TICKER: int = 3
_DEFAULT_TIMEOUT_SECONDS: float = 15.0

# NPORT form types to search for in EDGAR submissions.
_NPORT_FORM_TYPES: frozenset[str] = frozenset({"NPORT-P", "NPORT-EX"})

# Maximum holdings to return from one filing (guard against pathological XML).
_MAX_HOLDINGS: int = 10_000

# ── ETF CIK Seed Map ──────────────────────────────────────────────────────────
# Bootstrap placeholder — static CIK hints for our 12-ticker ETF universe.
#
# Purpose: fallback when company_tickers.json does not contain a direct ticker
# match (e.g., some ETF trust registrations differ from their trading ticker).
# The company_tickers.json dynamic lookup (request 1 in the fetch sequence) is
# tried FIRST; this map is consulted only when the dynamic lookup fails.
#
# Provenance: SEC EDGAR full-text company search (company_tickers.json source).
# Source: public SEC EDGAR records, https://www.sec.gov/files/company_tickers.json.
# Verify each entry against live SEC EDGAR before relying on production results.
#
# CIKs are stored as 10-digit zero-padded strings (canonical SEC CIK format).
# If a CIK here is incorrect, the submissions fetch at step 2 will return a
# non-NPORT-P result or 404, and the provider will fail closed (sec_error or
# no_nport_filing) — never producing fabricated holdings.
_ETF_CIK_SEED_MAP: dict[str, str] = {
    # SSGA/SPDR fund trusts
    "SPY": "0000884394",   # SPDR S&P 500 ETF Trust
    "XLE": "0001168164",   # Energy Select Sector SPDR Fund
    "GLD": "0001222333",   # SPDR Gold Shares (commodity trust — no equity holdings)
    # Invesco
    "QQQ": "0001067839",   # Invesco QQQ Trust
    # Vanguard index funds / ETF series
    "VOO": "0001480511",   # Vanguard S&P 500 ETF
    "VTI": "0000912884",   # Vanguard Total Stock Market ETF
    "VGT": "0001137774",   # Vanguard Information Technology ETF
    "VHT": "0001091424",   # Vanguard Health Care ETF
    "VIS": "0001121411",   # Vanguard Industrials ETF
    "VXUS": "0001482920",  # Vanguard Total International Stock ETF
    "VYM": "0001383310",   # Vanguard High Dividend Yield ETF
    # Schwab
    "SCHD": "0001510588",  # Schwab U.S. Dividend Equity ETF
}


# ── Provider config ────────────────────────────────────────────────────────────


@dataclass
class NportProviderConfig:
    """Immutable config for one NPORT-P fetch session.

    All values must come from Settings (env vars) — never from user input.
    """

    user_agent: str                              # Required per SEC terms of service.
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_requests_per_ticker: int = _MAX_REQUESTS_PER_TICKER


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class NportHolding:
    """One parsed holding from an NPORT-P filing.

    All fields are sourced directly from the SEC filing XML. No inference,
    no sector/geography derivation beyond what the XML directly provides.
    country_of_risk and country_of_investment are taken from NPORT filing
    issuer-level fields when present.
    """

    name: str                               # Holding name (always present)
    cusip: Optional[str] = None             # CUSIP if in filing
    isin: Optional[str] = None              # ISIN if in filing
    lei: Optional[str] = None              # LEI if in filing
    ticker: Optional[str] = None           # Ticker if in filing identifiers
    value_usd: Optional[float] = None      # Market value in USD from filing
    weight_pct: Optional[float] = None     # pctVal from filing (weight in portfolio)
    currency: Optional[str] = None         # Currency code (e.g. "USD")
    asset_category: Optional[str] = None   # assetCat (e.g. "EC" = equity, "DBT" = debt)
    country_of_risk: Optional[str] = None  # countryOfRisk ISO code if present
    issuer_category: Optional[str] = None  # issuerCat if present


@dataclass
class NportFilingMeta:
    """Metadata about the resolved NPORT-P filing."""

    accession_number: str           # Formatted "0001234567-25-000001"
    form_type: str                  # "NPORT-P" or "NPORT-EX"
    filing_date: Optional[str]      # YYYY-MM-DD when filed with SEC
    report_period_date: Optional[str]  # YYYY-MM-DD — period the holdings reflect
    primary_doc: str                # Filename of the primary XML document
    filing_url: str                 # Public EDGAR URL for the filing index


@dataclass
class NportProviderResult:
    """Aggregate result of one NPORT-P holdings fetch attempt.

    fetch_status values:
      success                          — holdings list populated from XML.
      missing_cik                      — ticker not in CIK map or company_tickers.json.
      no_nport_filing                  — no NPORT-P/NPORT-EX filing in submissions.
      filing_not_parseable             — XML fetch or parse failed.
      no_holdings_found                — XML parsed; zero holding elements.
      sec_error                        — HTTP error from SEC API.
      timeout                          — request timed out.
      commodity_trust_or_no_nport_data — trust type without standard equity holdings.
      error                            — unexpected exception.
    """

    ticker: str
    fetch_status: str = "unknown"
    error_message: Optional[str] = None
    fetched_at: str = ""
    cik: Optional[str] = None
    filing_meta: Optional[NportFilingMeta] = None
    holdings: list[NportHolding] = field(default_factory=list)
    total_assets_usd: Optional[float] = None    # totAssets from fundInfo
    net_assets_usd: Optional[float] = None      # netAssets from fundInfo
    total_reported_value_present: bool = False  # True when totAssets is in the filing
    weights_available: bool = False             # True when pctVal present on holdings
    weights_derived: bool = False               # True when weights computed from values
    request_count: int = 0

    @property
    def is_success(self) -> bool:
        return self.fetch_status == "success" and bool(self.holdings)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _is_timeout_exc(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "timeout" in str(exc).lower()


def _is_http_error(exc: Exception) -> bool:
    return hasattr(exc, "response") and getattr(
        getattr(exc, "response", None), "status_code", 0
    ) >= 400


def _fail_closed(
    ticker: str,
    status: str,
    message: str,
    request_count: int = 0,
    cik: Optional[str] = None,
) -> NportProviderResult:
    return NportProviderResult(
        ticker=ticker,
        fetch_status=status,
        error_message=message,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        request_count=request_count,
        cik=cik,
    )


def _padded_cik(cik_raw: str) -> str:
    """Return CIK as 10-digit zero-padded string."""
    try:
        return str(int(cik_raw)).zfill(10)
    except (ValueError, TypeError):
        return str(cik_raw).zfill(10)


def _strip_ns(tag: str) -> str:
    """Strip XML namespace from an ElementTree tag name."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _resolve_cik_from_tickers_json(
    ticker_upper: str,
    tickers_json_body: dict[str, Any],
) -> Optional[str]:
    """Extract CIK for a ticker from the company_tickers.json response body.

    The response is a dict keyed by integer index; values have keys:
    cik_str (int), ticker (str), title (str).
    Returns 10-digit zero-padded CIK, or None if ticker not found.
    """
    for entry in tickers_json_body.values():
        if not isinstance(entry, dict):
            continue
        t = str(entry.get("ticker") or "").upper().strip()
        if t == ticker_upper:
            raw_cik = entry.get("cik_str")
            if raw_cik is not None:
                try:
                    return str(int(raw_cik)).zfill(10)
                except (ValueError, TypeError):
                    pass
    return None


def _find_latest_nport_filing(
    submissions_body: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Find the latest NPORT-P or NPORT-EX filing in the submissions response.

    Returns a dict with: accession_number, form_type, filing_date,
    report_period_date, primary_doc. Returns None if none found.

    The submissions.recent arrays are in reverse-chronological order (newest first).
    """
    try:
        recent = submissions_body.get("filings", {}).get("recent", {})
        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        report_dates = recent.get("reportDate") or []
        primary_docs = recent.get("primaryDocument") or []
    except (AttributeError, TypeError):
        return None

    for i, form in enumerate(forms):
        if str(form).upper().strip() in _NPORT_FORM_TYPES:
            acc = str(accessions[i]) if i < len(accessions) else ""
            if not acc:
                continue
            return {
                "accession_number": acc,
                "form_type": str(form).upper().strip(),
                "filing_date": str(filing_dates[i]) if i < len(filing_dates) else None,
                "report_period_date": str(report_dates[i]) if i < len(report_dates) else None,
                "primary_doc": str(primary_docs[i]) if i < len(primary_docs) else "primary_doc.xml",
            }
    return None


def _parse_float(raw: Any) -> Optional[float]:
    """Parse a numeric value from the NPORT XML safely."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_nport_xml(xml_text: str) -> tuple[list[NportHolding], dict[str, Any]]:
    """Parse NPORT-P primary XML document into structured holdings.

    Returns (holdings_list, fund_metadata_dict). Never raises.
    fund_metadata_dict has keys: total_assets_usd, net_assets_usd,
    series_name, report_period_date.

    The NPORT XML namespace is http://www.sec.gov/edgar/nport — we strip
    namespaces for robustness across different filing versions.

    Handles both prefixed and non-prefixed namespace formats.
    Skips individual holdings that cannot be parsed rather than failing.
    """
    holdings: list[NportHolding] = []
    fund_meta: dict[str, Any] = {
        "total_assets_usd": None,
        "net_assets_usd": None,
        "series_name": None,
        "report_period_date": None,
    }
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return holdings, fund_meta

    # ── Build a namespace-stripped helper ────────────────────────────────────
    def _text(parent: ET.Element, *tags: str) -> Optional[str]:
        """Traverse child tags (namespace-stripped) and return .text."""
        node = parent
        for tag in tags:
            found: Optional[ET.Element] = None
            for child in node:
                if _strip_ns(child.tag) == tag:
                    found = child
                    break
            if found is None:
                return None
            node = found
        return (node.text or "").strip() or None

    def _find_all_children(parent: ET.Element, tag: str) -> list[ET.Element]:
        return [c for c in parent if _strip_ns(c.tag) == tag]

    def _find_child(parent: ET.Element, tag: str) -> Optional[ET.Element]:
        for c in parent:
            if _strip_ns(c.tag) == tag:
                return c
        return None

    # ── Navigate: edgarSubmission → formData ─────────────────────────────────
    form_data = _find_child(root, "formData")
    if form_data is None:
        # Some filings use the root element itself as formData equivalent.
        form_data = root

    # ── genInfo ──────────────────────────────────────────────────────────────
    gen_info = _find_child(form_data, "genInfo")
    if gen_info is not None:
        fund_meta["series_name"] = _text(gen_info, "seriesName")
        fund_meta["report_period_date"] = (
            _text(gen_info, "repPdDate") or _text(gen_info, "repPdEndDate")
        )

    # ── fundInfo ─────────────────────────────────────────────────────────────
    fund_info = _find_child(form_data, "fundInfo")
    if fund_info is not None:
        fund_meta["total_assets_usd"] = _parse_float(_text(fund_info, "totAssets"))
        fund_meta["net_assets_usd"] = _parse_float(_text(fund_info, "netAssets"))

    # ── invstOrSecs ───────────────────────────────────────────────────────────
    inv_or_secs = _find_child(form_data, "invstOrSecs")
    if inv_or_secs is None:
        return holdings, fund_meta

    for inv_elem in _find_all_children(inv_or_secs, "invstOrSec"):
        if len(holdings) >= _MAX_HOLDINGS:
            break
        try:
            name = _text(inv_elem, "name") or ""
            if not name:
                continue  # Skip unnamed elements

            cusip = _text(inv_elem, "cusip")
            lei = _text(inv_elem, "lei")
            value_usd = _parse_float(_text(inv_elem, "valUSD"))
            weight_pct = _parse_float(_text(inv_elem, "pctVal"))
            currency = _text(inv_elem, "curCd")
            asset_cat = _text(inv_elem, "assetCat")
            issuer_cat = _text(inv_elem, "issuerCat")

            # ISIN and other identifiers inside <identifiers> block
            isin: Optional[str] = None
            ticker_id: Optional[str] = None
            identifiers_elem = _find_child(inv_elem, "identifiers")
            if identifiers_elem is not None:
                for id_child in identifiers_elem:
                    tag = _strip_ns(id_child.tag)
                    val = (id_child.get("value") or "").strip()
                    if tag == "isin" and val:
                        isin = val
                    elif tag == "ticker" and val:
                        ticker_id = val

            # Country of risk (issuer-level geography hint)
            country_of_risk: Optional[str] = None
            sec_and_lending = _find_child(inv_elem, "securityLending")
            # countryOfRisk is a direct child in some filing versions
            cor_raw = _text(inv_elem, "countryOfRisk")
            if cor_raw:
                country_of_risk = cor_raw

            holdings.append(NportHolding(
                name=name,
                cusip=cusip,
                isin=isin,
                lei=lei,
                ticker=ticker_id,
                value_usd=value_usd,
                weight_pct=weight_pct,
                currency=currency,
                asset_category=asset_cat,
                country_of_risk=country_of_risk,
                issuer_category=issuer_cat,
            ))
        except Exception:  # noqa: BLE001 — per-holding fail-soft, never stop the parse
            continue

    return holdings, fund_meta


# ── Public API ────────────────────────────────────────────────────────────────


def fetch_etf_nport_holdings(
    ticker: str,
    config: NportProviderConfig,
    http_get_fn: Optional[Callable[[str], Any]] = None,
    cik_lookup_fn: Optional[Callable[[str], Optional[str]]] = None,
) -> NportProviderResult:
    """Fetch and parse NPORT-P holdings for one ETF ticker.

    Returns NportProviderResult — always. Never raises.

    CIK resolution order:
      1. _ETF_CIK_SEED_MAP (static bootstrap — sourced from SEC EDGAR records).
      2. cik_lookup_fn (injectable callable; defaults to company_tickers.json fetch).
         If provided by caller, the seed map is not consulted first — the caller
         fully controls CIK resolution.
    When cik_lookup_fn is None, the seed map is tried first, then the dynamic
    company_tickers.json lookup runs as a second request.

    Args:
        ticker:       ETF ticker symbol (case-insensitive).
        config:       Provider configuration with user_agent and limits.
        http_get_fn:  Injectable GET callable(url) → response-like object with
                      .raise_for_status() and .text / .json(). None → real httpx.
        cik_lookup_fn: Injectable CIK resolver callable(ticker_upper) → str|None.
                       If provided, skips both seed map and company_tickers.json fetch,
                       reducing the request count by 1.
    """
    ticker_upper = ticker.upper().strip()
    fetched_at = datetime.now(timezone.utc).isoformat()
    request_count = 0

    if not config.user_agent or not config.user_agent.strip():
        return _fail_closed(
            ticker_upper, "sec_error",
            "SEC User-Agent not configured. No anonymous calls permitted.",
        )

    http_client: Any = None
    _own_client = False

    try:
        if http_get_fn is None:
            import httpx  # deferred import; not needed in test paths
            http_client = httpx.Client(
                timeout=config.timeout_seconds,
                headers={"User-Agent": config.user_agent},
            )
            _own_client = True
            _get: Callable[[str], Any] = http_client.get
        else:
            _get = http_get_fn

        # ── Step 1: Resolve CIK ───────────────────────────────────────────────
        cik: Optional[str] = None

        if cik_lookup_fn is not None:
            # Caller provides CIK resolution — fully injectable (used in tests).
            cik = cik_lookup_fn(ticker_upper)
        else:
            # Try static seed map first (zero HTTP requests, bootstrap hints).
            cik = _ETF_CIK_SEED_MAP.get(ticker_upper)

            # Dynamic fallback: company_tickers.json
            if cik is None:
                if request_count >= config.max_requests_per_ticker:
                    return _fail_closed(
                        ticker_upper, "missing_cik",
                        "Request budget exhausted before CIK lookup.",
                        request_count,
                    )
                try:
                    resp_tickers = _get(_COMPANY_TICKERS_URL)
                    resp_tickers.raise_for_status()
                    request_count += 1
                    tickers_body = resp_tickers.json() or {}
                    cik = _resolve_cik_from_tickers_json(ticker_upper, tickers_body)
                except Exception as exc:  # noqa: BLE001
                    if _is_timeout_exc(exc):
                        return _fail_closed(
                            ticker_upper, "timeout",
                            f"Timeout fetching company_tickers.json: {exc}",
                            request_count,
                        )
                    return _fail_closed(
                        ticker_upper, "sec_error",
                        f"Error fetching company_tickers.json: {exc}",
                        request_count,
                    )

        if not cik:
            return _fail_closed(
                ticker_upper, "missing_cik",
                (
                    f"Ticker {ticker_upper!r} not found in ETF CIK seed map or "
                    "company_tickers.json. Add to _ETF_CIK_SEED_MAP or verify SEC EDGAR."
                ),
                request_count,
            )

        cik_padded = _padded_cik(cik)
        cik_int = int(cik_padded)

        # ── Step 2: Fetch submissions to find latest NPORT-P ─────────────────
        if request_count >= config.max_requests_per_ticker:
            return _fail_closed(
                ticker_upper, "sec_error",
                "Request budget exhausted before submissions fetch.",
                request_count, cik=cik_padded,
            )

        submissions_url = _SUBMISSIONS_URL_TPL.format(cik=cik_padded)
        try:
            resp_sub = _get(submissions_url)
            resp_sub.raise_for_status()
            request_count += 1
            sub_body = resp_sub.json() or {}
        except Exception as exc:  # noqa: BLE001
            if _is_timeout_exc(exc):
                return _fail_closed(
                    ticker_upper, "timeout",
                    f"Timeout fetching submissions for CIK {cik_padded}: {exc}",
                    request_count, cik=cik_padded,
                )
            return _fail_closed(
                ticker_upper, "sec_error",
                f"HTTP error fetching submissions for CIK {cik_padded}: {exc}",
                request_count, cik=cik_padded,
            )

        filing_info = _find_latest_nport_filing(sub_body)
        if filing_info is None:
            # GLD and commodity trusts may not file NPORT-P.
            is_commodity_trust = ticker_upper in {"GLD"}
            status = "commodity_trust_or_no_nport_data" if is_commodity_trust else "no_nport_filing"
            msg = (
                "Commodity trust — no NPORT-P/NPORT-EX filing expected (e.g. GLD bullion trust)."
                if is_commodity_trust
                else f"No NPORT-P or NPORT-EX filing found in EDGAR submissions for CIK {cik_padded}."
            )
            return _fail_closed(ticker_upper, status, msg, request_count, cik=cik_padded)

        acc_raw = filing_info["accession_number"]
        acc_nodash = acc_raw.replace("-", "")
        primary_doc = filing_info.get("primary_doc") or "primary_doc.xml"
        filing_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_int}&type=NPORT-P"
        filing_meta = NportFilingMeta(
            accession_number=acc_raw,
            form_type=filing_info["form_type"],
            filing_date=filing_info.get("filing_date"),
            report_period_date=filing_info.get("report_period_date"),
            primary_doc=primary_doc,
            filing_url=filing_url,
        )

        # ── Step 3: Fetch and parse the primary XML document ──────────────────
        if request_count >= config.max_requests_per_ticker:
            return _fail_closed(
                ticker_upper, "sec_error",
                "Request budget exhausted before XML document fetch.",
                request_count, cik=cik_padded,
            )

        doc_url = _FILING_URL_TPL.format(
            cik_int=cik_int,
            acc_nodash=acc_nodash,
            primary_doc=primary_doc,
        )
        try:
            resp_doc = _get(doc_url)
            resp_doc.raise_for_status()
            request_count += 1
            # Access raw text — we parse XML ourselves, never store the raw text.
            xml_text = getattr(resp_doc, "text", None) or ""
            if not xml_text and hasattr(resp_doc, "json"):
                # Some HTTP clients may return bytes; attempt text coercion.
                try:
                    xml_text = resp_doc.content.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    xml_text = ""
        except Exception as exc:  # noqa: BLE001
            if _is_timeout_exc(exc):
                return NportProviderResult(
                    ticker=ticker_upper,
                    fetch_status="timeout",
                    error_message=f"Timeout fetching filing document: {exc}",
                    fetched_at=fetched_at,
                    cik=cik_padded,
                    filing_meta=filing_meta,
                    request_count=request_count,
                )
            return NportProviderResult(
                ticker=ticker_upper,
                fetch_status="filing_not_parseable",
                error_message=f"Error fetching filing XML: {exc}",
                fetched_at=fetched_at,
                cik=cik_padded,
                filing_meta=filing_meta,
                request_count=request_count,
            )

        if not xml_text.strip():
            return NportProviderResult(
                ticker=ticker_upper,
                fetch_status="filing_not_parseable",
                error_message="Empty document body received from EDGAR.",
                fetched_at=fetched_at,
                cik=cik_padded,
                filing_meta=filing_meta,
                request_count=request_count,
            )

        holdings, fund_meta = _parse_nport_xml(xml_text)

        if not holdings:
            # Distinguish between parse failure and genuinely empty holdings.
            # An empty list after successful parse is honest — some NPORT
            # filings (e.g. commodity trusts, cash-only) have no invstOrSec elements.
            is_commodity_trust = ticker_upper in {"GLD"}
            status = "commodity_trust_or_no_nport_data" if is_commodity_trust else "no_holdings_found"
            return NportProviderResult(
                ticker=ticker_upper,
                fetch_status=status,
                error_message=(
                    "Commodity trust — no equity holding elements in NPORT filing (expected for GLD)."
                    if is_commodity_trust
                    else "Filing parsed but no invstOrSec holding elements found."
                ),
                fetched_at=fetched_at,
                cik=cik_padded,
                filing_meta=filing_meta,
                request_count=request_count,
            )

        # Assess weight/value availability.
        weights_available = any(h.weight_pct is not None for h in holdings)
        total_assets = fund_meta.get("total_assets_usd")
        total_reported_value_present = total_assets is not None

        # Derive weights from values only when all conditions hold:
        # - total portfolio value present in same filing
        # - at least one holding has a USD value
        # - no weights directly available
        weights_derived = False
        if not weights_available and total_assets and total_assets > 0:
            holdings_with_values = [h for h in holdings if h.value_usd is not None]
            if holdings_with_values:
                for h in holdings_with_values:
                    if h.value_usd is not None:
                        h.weight_pct = round((h.value_usd / total_assets) * 100, 6)
                weights_derived = True
                weights_available = True

        return NportProviderResult(
            ticker=ticker_upper,
            fetch_status="success",
            fetched_at=fetched_at,
            cik=cik_padded,
            filing_meta=filing_meta,
            holdings=holdings,
            total_assets_usd=total_assets,
            net_assets_usd=fund_meta.get("net_assets_usd"),
            total_reported_value_present=total_reported_value_present,
            weights_available=weights_available,
            weights_derived=weights_derived,
            request_count=request_count,
        )

    except Exception as exc:  # noqa: BLE001 — defence-in-depth outer catch
        return NportProviderResult(
            ticker=ticker_upper,
            fetch_status="error",
            error_message=f"Unexpected error in NPORT provider: {exc}",
            fetched_at=fetched_at,
            request_count=request_count,
        )
    finally:
        if _own_client and http_client is not None:
            try:
                http_client.close()
            except Exception:  # noqa: BLE001
                pass
