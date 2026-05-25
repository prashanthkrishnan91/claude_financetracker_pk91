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
import re
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
# Accession folder base URL — used to construct fallback index discovery URLs.
_FILING_FOLDER_URL_TPL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"
)

# Budget: company_tickers(opt) + submissions + fallback chain A/B/C(≤3) + xml_doc = 5 max.
_MAX_REQUESTS_PER_TICKER: int = 5
_DEFAULT_TIMEOUT_SECONDS: float = 15.0

# NPORT form types to search for in EDGAR submissions.
# Include /A (amended) variants — amended is still valid holdings data.
_NPORT_FORM_TYPES: frozenset[str] = frozenset({
    "NPORT-P", "NPORT-EX", "NPORT-P/A", "NPORT-EX/A",
})

# Maximum holdings to return from one filing (guard against pathological XML).
_MAX_HOLDINGS: int = 10_000

# ── ETF CIK Seed Map ──────────────────────────────────────────────────────────
# Static CIK hints for our ETF universe.  Avoids a company_tickers.json HTTP
# request for known tickers, keeping us within _MAX_REQUESTS_PER_TICKER.
#
# IMPORTANT — Vanguard ETF CIK architecture:
#   Vanguard ETFs are series of parent registrant companies ("VANGUARD INDEX
#   FUNDS", "VANGUARD SPECIALIZED FUNDS", etc.).  The NPORT-P filer is the
#   PARENT REGISTRANT entity, not an individual ETF share-class entity.
#   company_tickers.json often maps a Vanguard ticker to a share-class CIK
#   that has no NPORT-P filings (→ no_nport_filing status).  The seed map must
#   store the PARENT REGISTRANT CIK.
#
#   Production diagnostic legend:
#     no_nport_filing  — CIK resolves but entity files no NPORT-P (wrong CIK type).
#     sec_error        — HTTP error on submissions fetch (likely 404, wrong CIK).
#   Use scripts/validation/nport_live_check.py to discover correct CIKs live.
#
# CIKs are 10-digit zero-padded strings (canonical SEC EDGAR format).
_ETF_CIK_SEED_MAP: dict[str, str] = {
    # ── SSGA/SPDR standalone trusts ───────────────────────────────────────────
    # These trusts are single-series registrants and file NPORT-P directly.
    "SPY": "0000884394",   # SPDR S&P 500 ETF Trust
    "XLE": "0001168164",   # Energy Select Sector SPDR Fund (SPDR Series Trust series)
    "GLD": "0001222333",   # SPDR Gold Shares — commodity trust, no equity holdings
    # ── Invesco ───────────────────────────────────────────────────────────────
    "QQQ": "0001067839",   # Invesco QQQ Trust, Series 1
    # ── Vanguard — PARENT REGISTRANT CIKs required (see note above) ──────────
    # VOO, VTI are series of "VANGUARD INDEX FUNDS" — CIK needs live verification.
    "VOO": "0001480511",   # VERIFY: may be series CIK, not parent registrant
    "VTI": "0000732834",   # VERIFY: Vanguard Total Stock Market — CIK candidate
    "VGT": "0001137774",   # VERIFY: Vanguard Info Tech ETF — CIK candidate
    # VHT, VIS, VXUS had confirmed sec_error (wrong CIK) — omitted; falls back
    # to company_tickers.json for dynamic lookup.
    "VYM": "0001383310",   # VERIFY: Vanguard High Dividend Yield ETF
    # ── Schwab ────────────────────────────────────────────────────────────────
    "SCHD": "0001510588",  # VERIFY: Schwab U.S. Dividend Equity ETF
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
    primary_doc: str                # Filename used to construct the fetch URL
    filing_url: str                 # Public EDGAR URL for the filing index
    xml_extracted_from_sgml: bool = False  # True when XML was in SGML wrapper


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
    # Diagnostic fields — surfaced in thin no-data artifact payloads.
    primary_doc_attempted: Optional[str] = None   # filename tried for XML fetch
    parse_failure_stage: Optional[str] = None     # "xml_parse_error" | "no_holdings_container" | "no_parseable_doc_in_index" | None
    # Document selector diagnostics (Stage 9F.2a document discovery fix).
    primary_doc_from_submissions: Optional[str] = None  # raw primaryDocument from submissions API
    selected_doc_source: Optional[str] = None            # "index_json" | "index_html" | "complete_submission_txt" | "submissions"
    candidate_doc_count: Optional[int] = None            # non-XSL doc count in filing index
    index_urls_attempted_count: Optional[int] = None     # number of index URLs tried in fallback chain

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


def _is_xsl_viewer_path(doc: str) -> bool:
    """Return True when doc is an EDGAR XSL transform viewer path, not a raw document.

    EDGAR's submissions API sometimes returns a primaryDocument like
    'xslFormNPORT-P_X01/primary_doc.xml', which is a server-side XSL
    transform HTML view — not the underlying raw XML or SGML file.
    """
    dl = doc.lower()
    return dl.startswith("xslform") or "/xslform" in dl


def _select_best_nport_doc_from_index(
    index_body: dict[str, Any],
    acc_raw: str,
) -> tuple[Optional[str], int]:
    """Select the best parseable NPORT document from a filing index JSON body.

    Handles two SEC EDGAR JSON index formats:
    1. Filing-specific ``{acc_raw}-index.json`` → ``{"documents": [...]}``
       Each item: ``{"document": filename, "type": form-type-string}``
    2. Folder directory listing ``index.json`` → ``{"directory": {"item": [...]}}``
       Each item: ``{"name": filename, "type": MIME-type-string}``

    Ranking (highest to lowest):
      1. .xml file whose type contains NPORT-P, NPORT-EX, or NPORT
      2. .xml file of any type
      3. .txt complete-submission file (SGML wrapper with embedded XML)
      4. first non-XSL candidate

    Returns (selected_filename, candidate_count).  selected_filename is None
    when no parseable candidate exists.  XSL viewer paths are excluded from
    both the selection and the count.
    """
    valid: list[tuple[str, str]] = []

    # Format 1: "documents" array (filing-specific index JSON)
    docs_raw = index_body.get("documents")
    if isinstance(docs_raw, list) and docs_raw:
        for doc in docs_raw:
            if not isinstance(doc, dict):
                continue
            name = str(doc.get("document") or "").strip()
            dtype = str(doc.get("type") or "").upper().strip()
            if not name or _is_xsl_viewer_path(name):
                continue
            valid.append((name, dtype))
    else:
        # Format 2: "directory.item" array (EDGAR folder index.json)
        directory = index_body.get("directory") or {}
        items = directory.get("item") or []
        for item in (items if isinstance(items, list) else []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("href") or "").strip()
            mime = str(item.get("type") or "").lower()
            if "xml" in mime:
                dtype = "NPORT" if "nport" in name.lower() else "XML"
            elif "plain" in mime or "text" in mime:
                dtype = "TXT"
            else:
                dtype = ""
            # Skip folder entries (end with "/") and XSL paths
            if not name or name.endswith("/") or _is_xsl_viewer_path(name):
                continue
            valid.append((name, dtype))

    candidate_count = len(valid)

    # Priority 1: NPORT-typed XML file
    for name, dtype in valid:
        if name.lower().endswith(".xml") and any(
            t in dtype for t in ("NPORT-P", "NPORT-EX", "NPORT")
        ):
            return name, candidate_count

    # Priority 2: any XML file
    for name, dtype in valid:
        if name.lower().endswith(".xml"):
            return name, candidate_count

    # Priority 3: complete-submission .txt (SGML wrapper with embedded XML)
    for name, dtype in valid:
        if name.lower().endswith(".txt"):
            return name, candidate_count

    # Priority 4: first available non-XSL candidate
    if valid:
        return valid[0][0], candidate_count

    return None, 0


def _parse_index_html_links(html_text: str) -> list[str]:
    """Extract document filenames from a SEC EDGAR filing index HTML page.

    Uses simple regex to find href attributes in <a> tags.  Excludes external
    links, anchors, directory entries, and XSL viewer paths.  Returns only
    relative filenames with .xml or .txt extensions.
    """
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
    seen: set[str] = set()
    result: list[str] = []
    for href in hrefs:
        href = href.strip()
        if not href or href.startswith(("http", "#", "?", "/", "mailto:")):
            continue
        if _is_xsl_viewer_path(href):
            continue
        lname = href.lower()
        if lname.endswith((".xml", ".txt")) and href not in seen:
            seen.add(href)
            result.append(href)
    return result


def _select_best_nport_doc_from_html_links(hrefs: list[str]) -> tuple[Optional[str], int]:
    """Select best NPORT document from href filenames extracted from an HTML index page.

    Applies the same priority ordering as _select_best_nport_doc_from_index:
    NPORT XML > any XML > .txt > first available.
    """
    valid = [h for h in hrefs if h and not _is_xsl_viewer_path(h)]
    count = len(valid)

    for name in valid:
        if name.lower().endswith(".xml") and "nport" in name.lower():
            return name, count
    for name in valid:
        if name.lower().endswith(".xml"):
            return name, count
    for name in valid:
        if name.lower().endswith(".txt"):
            return name, count
    return (valid[0] if valid else None), count


def _extract_xml_from_sgml_submission(sgml_text: str) -> Optional[str]:
    """Extract NPORT-P XML from an EDGAR complete submission text file (SGML wrapper).

    EDGAR stores the full filing as an SGML document when the filer submits the
    complete-submission format.  The actual NPORT-P XML is embedded inside the
    first <DOCUMENT> section whose <TYPE> is NPORT-P (or NPORT-EX / amended
    variants), between <TEXT> and </TEXT> tags.

    Example shape:
        <SEC-DOCUMENT>...
        <DOCUMENT>
        <TYPE>NPORT-P
        <SEQUENCE>1
        <FILENAME>primary_doc.xml
        <TEXT>
        <?xml version="1.0"?>
        <edgarSubmission ...>...</edgarSubmission>
        </TEXT>
        </DOCUMENT>
        </SEC-DOCUMENT>

    Returns the extracted XML string (stripped), or None if content is not
    an SGML wrapper or contains no NPORT document.  Never raises.
    """
    # Fast path: if content starts with XML declaration or NPORT root, not SGML.
    stripped = sgml_text.strip()
    if stripped.startswith("<?xml") or stripped.startswith("<edgarSubmission"):
        return None

    try:
        # Match a NPORT document section and capture between <TEXT> and </TEXT>.
        # re.DOTALL so '.' matches newlines in the multi-line XML body.
        doc_match = re.search(
            r"<TYPE>\s*NPORT[^\n\r]*[\r\n]+(?:.*?[\r\n]+)*?<TEXT>\s*[\r\n]+(.*?)\s*</TEXT>",
            sgml_text,
            re.DOTALL | re.IGNORECASE,
        )
        if not doc_match:
            return None

        candidate = doc_match.group(1).strip()
        # Only return if the extracted block looks like XML.
        if candidate.startswith("<?xml") or candidate.startswith("<edgarSubmission"):
            return candidate
        # Some filings omit the XML declaration — accept any element start.
        if candidate.startswith("<") and not candidate.startswith("</"):
            return candidate
        return None
    except Exception:  # noqa: BLE001
        return None


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


def _parse_nport_xml(
    xml_text: str,
) -> tuple[list[NportHolding], dict[str, Any], str]:
    """Parse NPORT-P primary XML document into structured holdings.

    Returns (holdings_list, fund_metadata_dict, parse_status).
    parse_status values:
      "ok"                  — XML parsed and invstOrSecs container found.
      "xml_parse_error"     — Content is not parseable XML (even after SGML extraction).
      "no_holdings_container" — XML parsed but no invstOrSecs element present.

    Handles:
      - Standalone NPORT-P XML files (most modern EDGAR filings).
      - EDGAR complete submission text files (SGML wrappers) — extracts embedded XML.

    fund_metadata_dict keys: total_assets_usd, net_assets_usd, series_name,
    report_period_date, xml_extracted_from_sgml.

    Never raises.
    """
    holdings: list[NportHolding] = []
    fund_meta: dict[str, Any] = {
        "total_assets_usd": None,
        "net_assets_usd": None,
        "series_name": None,
        "report_period_date": None,
        "xml_extracted_from_sgml": False,
    }

    # ── Attempt 1: direct XML parse ───────────────────────────────────────────
    parse_text = xml_text.strip()
    try:
        root = ET.fromstring(parse_text)
    except ET.ParseError:
        # ── Attempt 2: EDGAR SGML submission wrapper ──────────────────────────
        # Many EDGAR NPORT-P primaryDocument files are the complete submission text
        # (SGML format) rather than a standalone XML file.  Extract the embedded XML.
        extracted = _extract_xml_from_sgml_submission(xml_text)
        if extracted is None:
            return holdings, fund_meta, "xml_parse_error"
        try:
            root = ET.fromstring(extracted.strip())
            fund_meta["xml_extracted_from_sgml"] = True
            parse_text = extracted
        except ET.ParseError:
            return holdings, fund_meta, "xml_parse_error"

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
        return holdings, fund_meta, "no_holdings_container"

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

    return holdings, fund_meta, "ok"


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
        primary_doc_from_submissions = primary_doc
        selected_doc_source: Optional[str] = None
        candidate_doc_count: Optional[int] = None
        filing_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_int}&type=NPORT-P"

        # ── Step 2.5: Filing index discovery when primaryDocument is XSL viewer ─
        # EDGAR's submissions API sometimes returns primaryDocument as an XSL
        # transform viewer path (e.g. "xslFormNPORT-P_X01/primary_doc.xml").
        # That path serves an HTML view, not the raw XML or SGML file.
        #
        # Robust fallback chain (deterministic order):
        #   A. {folder}/index.json          — folder directory listing (directory.item)
        #   B. {folder}/{acc_raw}-index.html — filing-specific HTML index
        #   C. {folder}/{acc_nodash}.txt     — complete submission text (SGML); direct parse
        _prefetched_doc_text: Optional[str] = None  # set when Option C already fetched the doc
        index_urls_attempted_count: Optional[int] = None

        if _is_xsl_viewer_path(primary_doc):
            folder_url = _FILING_FOLDER_URL_TPL.format(cik_int=cik_int, acc_nodash=acc_nodash)
            _index_sources = [
                (folder_url + "index.json", "index_json"),
                (folder_url + acc_raw + "-index.html", "index_html"),
            ]
            _complete_txt_url = folder_url + acc_nodash + ".txt"
            index_urls_attempted_count = 0

            for _idx_url, _src_label in _index_sources:
                if request_count >= config.max_requests_per_ticker:
                    return NportProviderResult(
                        ticker=ticker_upper,
                        fetch_status="filing_not_parseable",
                        error_message=(
                            f"Request budget exhausted during index fallback after "
                            f"{index_urls_attempted_count} URL(s). "
                            f"XSL primaryDocument: {primary_doc_from_submissions!r}."
                        ),
                        fetched_at=fetched_at,
                        cik=cik_padded,
                        request_count=request_count,
                        primary_doc_from_submissions=primary_doc_from_submissions,
                        parse_failure_stage="budget_exhausted",
                        index_urls_attempted_count=index_urls_attempted_count,
                    )
                index_urls_attempted_count += 1
                try:
                    _resp_idx = _get(_idx_url)
                    _resp_idx.raise_for_status()
                    request_count += 1
                except Exception as exc:  # noqa: BLE001
                    if _is_timeout_exc(exc):
                        return NportProviderResult(
                            ticker=ticker_upper,
                            fetch_status="timeout",
                            error_message=f"Timeout fetching filing index ({_src_label}): {exc}",
                            fetched_at=fetched_at,
                            cik=cik_padded,
                            request_count=request_count,
                            primary_doc_from_submissions=primary_doc_from_submissions,
                            index_urls_attempted_count=index_urls_attempted_count,
                        )
                    # Non-timeout (e.g. 404) — try next source in chain.
                    continue

                # Parse document list from this index source.
                try:
                    if _src_label == "index_json":
                        _idx_body = _resp_idx.json() or {}
                        _sel_doc, _n_cands = _select_best_nport_doc_from_index(_idx_body, acc_raw)
                    else:  # index_html
                        _html_links = _parse_index_html_links(
                            getattr(_resp_idx, "text", "") or ""
                        )
                        _sel_doc, _n_cands = _select_best_nport_doc_from_html_links(_html_links)
                except Exception:  # noqa: BLE001
                    _sel_doc, _n_cands = None, 0

                if _sel_doc:
                    primary_doc = _sel_doc
                    selected_doc_source = _src_label
                    candidate_doc_count = _n_cands
                    break  # found a parseable document via this index source
            else:
                # Options A and B yielded no parseable document (loop exhausted without break).
                # Option C: fetch complete submission text directly — it is both the index
                # discovery step and the document.  No additional fetch needed after this.
                if request_count < config.max_requests_per_ticker:
                    index_urls_attempted_count += 1
                    try:
                        _resp_c = _get(_complete_txt_url)
                        _resp_c.raise_for_status()
                        request_count += 1
                        _txt_content = getattr(_resp_c, "text", None) or ""
                        if _txt_content.strip():
                            primary_doc = acc_nodash + ".txt"
                            selected_doc_source = "complete_submission_txt"
                            candidate_doc_count = 1
                            _prefetched_doc_text = _txt_content
                    except Exception as exc:  # noqa: BLE001
                        if _is_timeout_exc(exc):
                            return NportProviderResult(
                                ticker=ticker_upper,
                                fetch_status="timeout",
                                error_message=f"Timeout fetching complete submission text: {exc}",
                                fetched_at=fetched_at,
                                cik=cik_padded,
                                request_count=request_count,
                                primary_doc_from_submissions=primary_doc_from_submissions,
                                index_urls_attempted_count=index_urls_attempted_count,
                            )
                        # Option C also failed — fall through to the failure return below.

                if selected_doc_source is None:
                    return NportProviderResult(
                        ticker=ticker_upper,
                        fetch_status="filing_not_parseable",
                        error_message=(
                            f"No parseable NPORT document found after {index_urls_attempted_count} "
                            f"index URL(s). Submissions primaryDocument: {primary_doc_from_submissions!r}."
                        ),
                        fetched_at=fetched_at,
                        cik=cik_padded,
                        request_count=request_count,
                        primary_doc_from_submissions=primary_doc_from_submissions,
                        selected_doc_source=None,
                        candidate_doc_count=candidate_doc_count,
                        parse_failure_stage="no_parseable_doc_in_index",
                        index_urls_attempted_count=index_urls_attempted_count,
                    )
        else:
            selected_doc_source = "submissions"

        filing_meta = NportFilingMeta(
            accession_number=acc_raw,
            form_type=filing_info["form_type"],
            filing_date=filing_info.get("filing_date"),
            report_period_date=filing_info.get("report_period_date"),
            primary_doc=primary_doc,
            filing_url=filing_url,
        )

        # ── Step 3: Fetch and parse the primary XML document ──────────────────
        # Option C prefetch: complete submission text already retrieved as part of
        # index discovery — reuse it directly without an additional HTTP request.
        if _prefetched_doc_text is not None:
            xml_text = _prefetched_doc_text
        else:
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
                        primary_doc_from_submissions=primary_doc_from_submissions,
                        selected_doc_source=selected_doc_source,
                        candidate_doc_count=candidate_doc_count,
                        index_urls_attempted_count=index_urls_attempted_count,
                    )
                return NportProviderResult(
                    ticker=ticker_upper,
                    fetch_status="filing_not_parseable",
                    error_message=f"Error fetching filing XML: {exc}",
                    fetched_at=fetched_at,
                    cik=cik_padded,
                    filing_meta=filing_meta,
                    request_count=request_count,
                    primary_doc_from_submissions=primary_doc_from_submissions,
                    selected_doc_source=selected_doc_source,
                    candidate_doc_count=candidate_doc_count,
                    index_urls_attempted_count=index_urls_attempted_count,
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
                primary_doc_attempted=primary_doc,
                parse_failure_stage="empty_body",
                primary_doc_from_submissions=primary_doc_from_submissions,
                selected_doc_source=selected_doc_source,
                candidate_doc_count=candidate_doc_count,
                index_urls_attempted_count=index_urls_attempted_count,
            )

        holdings, fund_meta, parse_status = _parse_nport_xml(xml_text)

        # Record whether XML was extracted from an SGML wrapper for diagnostics.
        if filing_meta is not None and fund_meta.get("xml_extracted_from_sgml"):
            filing_meta.xml_extracted_from_sgml = True

        if parse_status == "xml_parse_error":
            # Document is neither valid XML nor a parseable SGML+XML wrapper.
            # Common cause: primaryDocument points to an HTML page or binary file.
            return NportProviderResult(
                ticker=ticker_upper,
                fetch_status="filing_not_parseable",
                error_message=(
                    "Document is not valid XML and could not be extracted from SGML "
                    f"wrapper. Filename attempted: {primary_doc!r}. "
                    "May be an HTML page or non-XML file — check filing index."
                ),
                fetched_at=fetched_at,
                cik=cik_padded,
                filing_meta=filing_meta,
                request_count=request_count,
                primary_doc_attempted=primary_doc,
                parse_failure_stage="xml_parse_error",
                primary_doc_from_submissions=primary_doc_from_submissions,
                selected_doc_source=selected_doc_source,
                candidate_doc_count=candidate_doc_count,
                index_urls_attempted_count=index_urls_attempted_count,
            )

        if parse_status == "no_holdings_container":
            # XML parsed but lacks invstOrSecs element — cover page or wrong file.
            return NportProviderResult(
                ticker=ticker_upper,
                fetch_status="filing_not_parseable",
                error_message=(
                    "NPORT-P XML has no invstOrSecs element. "
                    f"File {primary_doc!r} may be a cover page or non-holdings document."
                ),
                fetched_at=fetched_at,
                cik=cik_padded,
                filing_meta=filing_meta,
                request_count=request_count,
                primary_doc_attempted=primary_doc,
                parse_failure_stage="no_holdings_container",
                primary_doc_from_submissions=primary_doc_from_submissions,
                selected_doc_source=selected_doc_source,
                candidate_doc_count=candidate_doc_count,
                index_urls_attempted_count=index_urls_attempted_count,
            )

        if not holdings:
            # XML parsed and invstOrSecs found, but zero invstOrSec child elements.
            # Distinguishes genuinely empty holdings from parse failures above.
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
                primary_doc_attempted=primary_doc,
                primary_doc_from_submissions=primary_doc_from_submissions,
                selected_doc_source=selected_doc_source,
                candidate_doc_count=candidate_doc_count,
                index_urls_attempted_count=index_urls_attempted_count,
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
            primary_doc_attempted=primary_doc,
            primary_doc_from_submissions=primary_doc_from_submissions,
            selected_doc_source=selected_doc_source,
            candidate_doc_count=candidate_doc_count,
            index_urls_attempted_count=index_urls_attempted_count,
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
