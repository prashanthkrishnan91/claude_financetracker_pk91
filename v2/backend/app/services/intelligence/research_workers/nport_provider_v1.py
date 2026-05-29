"""Stage 9F.2a — SEC NPORT-P ETF Holdings Provider v1.

Keyless, synchronous SEC EDGAR provider for ETF fund holdings from NPORT-P
(or NPORT-EX) regulatory filings.

NPORT-P is the SEC monthly portfolio holdings report for large registered
investment companies (>$1B AUM). Holdings for the third month of each quarter
are publicly disclosed with approximately a 60-day filing lag.

This is an official, PRIMARY_AUTHORITY source — published directly on SEC EDGAR
with no API key required. SEC User-Agent header is required per SEC terms.

Identity contract (Stage 9F.2a identity-certification repair):
  For standalone trusts (SPY, QQQ), identity is assumed: the trust IS the filer.
  For series-based registrants (XLE, Vanguard, SCHD), the parsed NPORT genInfo
  seriesName is normalized and compared against expected_series_names from the
  resolver entry.  If it does not match, fetch_status=series_identity_not_proven
  is returned with NO holdings — preventing false attribution of one series'
  holdings to a different ETF ticker.

Identity status values (in identity_status field):
  success_identity_verified           — series name matched resolver hints.
  success_identity_assumed_single_series — standalone trust; identity assumed.
  series_identity_not_proven          — holdings found but series doesn't match.
  commodity_trust_or_no_nport_data   — commodity trust (GLD).
  no_nport_filing                    — no NPORT-P/NPORT-EX filing found.
  sec_error / timeout / error        — infrastructure failure.

Fetch sequence (up to _MAX_REQUESTS_PER_TICKER HTTP calls):
  1. Resolve ticker → CIK candidates: static parent map (bootstrap), then
     company_tickers.json.
  2. For each candidate CIK: fetch submissions API, find latest NPORT-P/NPORT-EX.
  3. Fetch filing primary document and parse XML for holdings.
  4. Verify series identity against resolver hints.

Explicit fetch_status values (never raises; always returns NportProviderResult):
  success                          — holdings parsed from NPORT-P.
  series_identity_not_proven       — holdings found but identity not certified.
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
  - Total HTTP requests capped at _MAX_REQUESTS_PER_TICKER (default 5).
  - No raw XML, no raw filing text stored or passed downstream.
  - Never fabricates holdings, weights, values, or identifiers.
  - Injectable http_get_fn, cik_lookup_fn, and _candidate_ciks_override for testing.
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

# Budget: for the common XSL primaryDocument path each scanned filing costs
# 2 requests (index.json discovery + selected XML document).
# Full scan cap: submissions(1) + 12 * (index_json + xml_doc)(24) + slack(5) = 30.
# A budget of 20 could exhaust before completing 12 filings on the XSL path.
# Increased from 20 to 30 to make max_filings_to_scan=12 realistically reachable.
_MAX_REQUESTS_PER_TICKER: int = 30
_DEFAULT_TIMEOUT_SECONDS: float = 15.0

# NPORT form types to search for in EDGAR submissions.
# Include /A (amended) variants — amended is still valid holdings data.
_NPORT_FORM_TYPES: frozenset[str] = frozenset({
    "NPORT-P", "NPORT-EX", "NPORT-P/A", "NPORT-EX/A",
})

# Maximum holdings to return from one filing (guard against pathological XML).
_MAX_HOLDINGS: int = 10_000

# ── Filing scan caps (Stage 9F.2a multi-filing scan) ─────────────────────────
# Max NPORT-P filings to scan per candidate CIK, newest first.
# Scanning stops as soon as identity matches or budget/cap is exhausted.
_DEFAULT_FILING_SCAN_CAP: int = 12  # production and diagnostic default

# Status returned when scan budget is exhausted before identity match.
_STATUS_SCAN_BUDGET_EXHAUSTED = "series_identity_scan_budget_exhausted"

# ── Identity status constants (Stage 9F.2a) ───────────────────────────────────

# Holdings parsed AND series name matched resolver hints.
_IDENTITY_VERIFIED = "success_identity_verified"
# Standalone single-series trust — identity assumed without series name check.
_IDENTITY_STANDALONE = "success_identity_assumed_single_series"
# Holdings found but series name does not match expected hints.
_IDENTITY_NOT_PROVEN = "series_identity_not_proven"

# ── ETF CIK Seed Map (superseded) ────────────────────────────────────────────
# Replaced by etf_parent_cik_resolver.ETFParentRegistrantEntry map (Stage 9F.2a).
# CIK resolution now uses get_parent_registrant_entry() in Step 1 of
# fetch_etf_nport_holdings.  This dict is retained only as a historical reference
# and is NOT consulted during CIK resolution.
_ETF_CIK_SEED_MAP: dict[str, str] = {}


# ── Provider config ────────────────────────────────────────────────────────────


@dataclass
class NportProviderConfig:
    """Immutable config for one NPORT-P fetch session.

    All values must come from Settings (env vars) — never from user input.
    """

    user_agent: str                              # Required per SEC terms of service.
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_requests_per_ticker: int = _MAX_REQUESTS_PER_TICKER
    # Max NPORT filings to scan per candidate CIK (Stage 9F.2a multi-filing scan).
    # Scanning stops as soon as identity matches or budget/cap is exhausted.
    max_filings_to_scan: int = _DEFAULT_FILING_SCAN_CAP


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
      series_identity_not_proven       — holdings found but identity not certified.
      missing_cik                      — ticker not in CIK map or company_tickers.json.
      no_nport_filing                  — no NPORT-P/NPORT-EX filing in submissions.
      filing_not_parseable             — XML fetch or parse failed.
      no_holdings_found                — XML parsed; zero holding elements.
      sec_error                        — HTTP error from SEC API.
      timeout                          — request timed out.
      commodity_trust_or_no_nport_data — trust type without standard equity holdings.
      error                            — unexpected exception.

    identity_status values (diagnostic; may differ from fetch_status):
      success_identity_verified          — series name matched resolver hints.
      success_identity_assumed_single_series — standalone trust, identity assumed.
      series_identity_not_proven         — identity check failed.
      commodity_trust_or_no_nport_data   — commodity trust.
      (other)                            — mirrors fetch_status for failures.
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
    # Parent-registrant resolver diagnostics (Stage 9F.2a CIK resolver).
    resolver_source: Optional[str] = None                # "etf_parent_map" | "company_tickers" | "injected"
    parent_registrant_name: Optional[str] = None         # SEC registrant name when parent map used
    # Identity certification fields (Stage 9F.2a identity-certification repair).
    identity_status: Optional[str] = None               # one of _IDENTITY_* constants or None
    identity_verified: bool = False                      # True only when identity is proven
    identity_basis: Optional[str] = None                 # human-readable explanation
    candidate_ciks_tried: list[str] = field(default_factory=list)  # CIKs tried in order
    selected_candidate_cik: Optional[str] = None         # CIK that yielded this result
    detected_registrant_name: Optional[str] = None       # from genInfo.regName
    detected_series_name: Optional[str] = None           # from genInfo.seriesName
    detected_class_name: Optional[str] = None            # from classesContracts classContract
    detected_series_id: Optional[str] = None             # from genInfo.seriesId
    detected_class_id: Optional[str] = None              # from classesContracts classContract
    identity_mismatch_reason: Optional[str] = None       # why identity_status=series_identity_not_proven
    # Per-candidate identity failure log for multi-candidate resolution diagnostics.
    # Each entry: {candidate_cik, accession_number, filing_rank, detected_series_name,
    #              detected_registrant_name, identity_mismatch_reason}
    candidate_identity_failures: list[dict] = field(default_factory=list)
    # Scan diagnostic fields (Stage 9F.2a multi-filing scan).
    filings_scanned_count: int = 0           # total filings scanned across all candidates
    matching_filing_rank: Optional[int] = None  # 1-based rank in candidate where match found
    scan_limit_reached: bool = False          # True when budget exhausted before identity match
    # Submissions structure diagnostics for no_nport_filing path (Stage 9L).
    submissions_recent_form_count: int = 0   # forms present in filings.recent
    submissions_has_files_pages: bool = False  # whether filings.files[] pages exist
    submissions_files_page_tried: bool = False  # whether a files page was fetched

    @property
    def is_success(self) -> bool:
        return self.fetch_status == "success" and bool(self.holdings)

    @property
    def is_identity_certified(self) -> bool:
        """True when holdings are present AND identity is verified."""
        return self.is_success and self.identity_verified


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
    resolver_source: Optional[str] = None,
    parent_registrant_name: Optional[str] = None,
    candidate_ciks_tried: Optional[list[str]] = None,
    identity_status: Optional[str] = None,
    identity_mismatch_reason: Optional[str] = None,
    detected_series_name: Optional[str] = None,
    detected_registrant_name: Optional[str] = None,
    selected_candidate_cik: Optional[str] = None,
) -> NportProviderResult:
    return NportProviderResult(
        ticker=ticker,
        fetch_status=status,
        error_message=message,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        request_count=request_count,
        cik=cik,
        resolver_source=resolver_source,
        parent_registrant_name=parent_registrant_name,
        candidate_ciks_tried=candidate_ciks_tried or [],
        identity_status=identity_status or status,
        identity_mismatch_reason=identity_mismatch_reason,
        detected_series_name=detected_series_name,
        detected_registrant_name=detected_registrant_name,
        selected_candidate_cik=selected_candidate_cik,
    )


def _padded_cik(cik_raw: str) -> str:
    """Return CIK as 10-digit zero-padded string."""
    try:
        return str(int(cik_raw)).zfill(10)
    except (ValueError, TypeError):
        return str(cik_raw).zfill(10)


def _is_xsl_viewer_path(doc: str) -> bool:
    """Return True when doc is an EDGAR XSL transform viewer path, not a raw document."""
    dl = doc.lower()
    return dl.startswith("xslform") or "/xslform" in dl


def _select_best_nport_doc_from_index(
    index_body: dict[str, Any],
    acc_raw: str,
) -> tuple[Optional[str], int]:
    """Select the best parseable NPORT document from a filing index JSON body."""
    valid: list[tuple[str, str]] = []

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
            if not name or name.endswith("/") or _is_xsl_viewer_path(name):
                continue
            valid.append((name, dtype))

    candidate_count = len(valid)

    for name, dtype in valid:
        if name.lower().endswith(".xml") and any(
            t in dtype for t in ("NPORT-P", "NPORT-EX", "NPORT")
        ):
            return name, candidate_count

    for name, dtype in valid:
        if name.lower().endswith(".xml"):
            return name, candidate_count

    for name, dtype in valid:
        if name.lower().endswith(".txt"):
            return name, candidate_count

    if valid:
        return valid[0][0], candidate_count

    return None, 0


def _parse_index_html_links(html_text: str) -> list[str]:
    """Extract document filenames from a SEC EDGAR filing index HTML page."""
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
    """Select best NPORT document from href filenames extracted from an HTML index page."""
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
    """Extract NPORT-P XML from an EDGAR complete submission text file (SGML wrapper)."""
    stripped = sgml_text.strip()
    if stripped.startswith("<?xml") or stripped.startswith("<edgarSubmission"):
        return None

    try:
        doc_match = re.search(
            r"<TYPE>\s*NPORT[^\n\r]*[\r\n]+(?:.*?[\r\n]+)*?<TEXT>\s*[\r\n]+(.*?)\s*</TEXT>",
            sgml_text,
            re.DOTALL | re.IGNORECASE,
        )
        if not doc_match:
            return None

        candidate = doc_match.group(1).strip()
        if candidate.startswith("<?xml") or candidate.startswith("<edgarSubmission"):
            return candidate
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
    """Extract CIK for a ticker from the company_tickers.json response body."""
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
    """Find the latest NPORT-P or NPORT-EX filing in the submissions response."""
    results = _collect_recent_nport_filings(submissions_body, max_filings=1)
    return results[0] if results else None


def _collect_recent_nport_filings(
    submissions_body: dict[str, Any],
    max_filings: int = _DEFAULT_FILING_SCAN_CAP,
) -> list[dict[str, Any]]:
    """Collect up to max_filings recent NPORT-P/NPORT-EX filings, newest first.

    Returns a list of filing-info dicts (same shape as _find_latest_nport_filing).
    The submissions API returns filings ordered newest-first, so index 0 is the
    most recent.  This function preserves that order.

    Used by the multi-filing identity scan (Stage 9F.2a) to allow the provider
    to scan past a wrong-series filing (e.g. the latest SPDR China ETF filing
    under SPDR Series Trust) until it finds the matching series or exhausts the cap.
    """
    try:
        recent = submissions_body.get("filings", {}).get("recent", {})
        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        report_dates = recent.get("reportDate") or []
        primary_docs = recent.get("primaryDocument") or []
    except (AttributeError, TypeError):
        return []

    results: list[dict[str, Any]] = []
    for i, form in enumerate(forms):
        if len(results) >= max_filings:
            break
        if str(form).upper().strip() in _NPORT_FORM_TYPES:
            acc = str(accessions[i]) if i < len(accessions) else ""
            if not acc:
                continue
            results.append({
                "accession_number": acc,
                "form_type": str(form).upper().strip(),
                "filing_date": str(filing_dates[i]) if i < len(filing_dates) else None,
                "report_period_date": str(report_dates[i]) if i < len(report_dates) else None,
                "primary_doc": str(primary_docs[i]) if i < len(primary_docs) else "primary_doc.xml",
            })
    return results


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

    fund_metadata_dict keys (Stage 9F.2a additions): total_assets_usd, net_assets_usd,
    series_name, registrant_name, series_id, class_id, class_name,
    report_period_date, xml_extracted_from_sgml.

    Never raises.
    """
    holdings: list[NportHolding] = []
    fund_meta: dict[str, Any] = {
        "total_assets_usd": None,
        "net_assets_usd": None,
        "series_name": None,
        "registrant_name": None,
        "series_id": None,
        "class_id": None,
        "class_name": None,
        "report_period_date": None,
        "xml_extracted_from_sgml": False,
    }

    # ── Attempt 1: direct XML parse ───────────────────────────────────────────
    parse_text = xml_text.strip()
    try:
        root = ET.fromstring(parse_text)
    except ET.ParseError:
        # ── Attempt 2: EDGAR SGML submission wrapper ──────────────────────────
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
        form_data = root

    # ── genInfo — extract identity metadata ──────────────────────────────────
    gen_info = _find_child(form_data, "genInfo")
    if gen_info is not None:
        fund_meta["series_name"] = _text(gen_info, "seriesName")
        fund_meta["registrant_name"] = _text(gen_info, "regName")
        fund_meta["series_id"] = _text(gen_info, "seriesId")
        fund_meta["report_period_date"] = (
            _text(gen_info, "repPdDate") or _text(gen_info, "repPdEndDate")
        )
        # classesContracts → first classContract → classId, className
        cc = _find_child(gen_info, "classesContracts")
        if cc is not None:
            first_class = _find_child(cc, "classContract")
            if first_class is not None:
                fund_meta["class_id"] = _text(first_class, "classId")
                fund_meta["class_name"] = _text(first_class, "className")

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
                continue

            cusip = _text(inv_elem, "cusip")
            lei = _text(inv_elem, "lei")
            value_usd = _parse_float(_text(inv_elem, "valUSD"))
            weight_pct = _parse_float(_text(inv_elem, "pctVal"))
            currency = _text(inv_elem, "curCd")
            asset_cat = _text(inv_elem, "assetCat")
            issuer_cat = _text(inv_elem, "issuerCat")

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

            country_of_risk: Optional[str] = None
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
        except Exception:  # noqa: BLE001
            continue

    return holdings, fund_meta, "ok"


# ── Identity matching helpers (Stage 9F.2a) ───────────────────────────────────


def _normalize_name(s: str) -> str:
    """Normalize a fund name for identity matching: lower-case, punctuation removed."""
    s = s.strip().lower()
    # Remove punctuation entirely (not space-substituted) so "U.S." → "us", "US" → "us"
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _identity_name_matches(detected: Optional[str], expected_names: tuple[str, ...]) -> bool:
    """Return True if detected name is a normalized substring match of any expected name.

    Conservative: false negatives are acceptable; false positives are not.
    Uses bidirectional substring matching after normalization.
    """
    if not detected or not expected_names:
        return False
    det_norm = _normalize_name(detected)
    if not det_norm:
        return False
    for exp in expected_names:
        exp_norm = _normalize_name(exp)
        if not exp_norm:
            continue
        # Exact match
        if det_norm == exp_norm:
            return True
        # Bidirectional substring: one is contained in the other
        if exp_norm in det_norm or det_norm in exp_norm:
            return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────


def fetch_etf_nport_holdings(
    ticker: str,
    config: NportProviderConfig,
    http_get_fn: Optional[Callable[[str], Any]] = None,
    cik_lookup_fn: Optional[Callable[[str], Optional[str]]] = None,
    _candidate_ciks_override: Optional[list[str]] = None,
) -> NportProviderResult:
    """Fetch and parse NPORT-P holdings for one ETF ticker.

    Returns NportProviderResult — always. Never raises.

    CIK resolution order:
      1. cik_lookup_fn (injectable callable; used in tests — bypasses parent map).
      2. etf_parent_cik_resolver.get_parent_registrant_entry() (primary map).
      3. company_tickers.json fetch (fallback for unknown tickers).

    _candidate_ciks_override: optional list of CIKs to try (testing only).
      When provided, overrides the candidate list from the parent map.

    Identity verification:
      - standalone_trust entries: identity assumed, no series name check.
      - commodity_trust entries: commodity_trust_or_no_nport_data expected.
      - parent-registrant entries with expected_series_names: series name from
        parsed NPORT genInfo must normalize-match one of the expected names.
      - Injected/company_tickers path: identity check skipped (no hints).
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

        # ── Step 1: Resolve CIK candidates ───────────────────────────────────
        from .etf_parent_cik_resolver import get_parent_registrant_entry, ETFParentRegistrantEntry

        _entry: Optional[ETFParentRegistrantEntry] = None
        _resolver_source: Optional[str] = None
        _parent_registrant_name: Optional[str] = None
        candidate_ciks: list[str] = []

        if cik_lookup_fn is not None:
            # Caller provides CIK resolution — fully injectable (used in tests).
            cik = cik_lookup_fn(ticker_upper)
            _resolver_source = "injected"
            if cik:
                candidate_ciks = [_padded_cik(cik)]
        else:
            # 1a. ETF parent-registrant resolver (highest priority).
            _entry = get_parent_registrant_entry(ticker_upper)
            if _entry is not None:
                _parent_registrant_name = _entry.parent_name
                _resolver_source = "etf_parent_map"
                candidate_ciks = [_entry.parent_cik] + list(_entry.candidate_ciks)
            else:
                # 1b. Dynamic fallback: company_tickers.json
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
                    _resolver_source = "company_tickers"
                    if cik:
                        candidate_ciks = [_padded_cik(cik)]
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

        # Override candidates for testing multi-candidate paths
        if _candidate_ciks_override is not None:
            candidate_ciks = [_padded_cik(c) for c in _candidate_ciks_override]

        if not candidate_ciks:
            return _fail_closed(
                ticker_upper, "missing_cik",
                (
                    f"Ticker {ticker_upper!r} not found in ETF parent-registrant map or "
                    "company_tickers.json. Add to etf_parent_cik_resolver or verify SEC EDGAR."
                ),
                request_count,
                resolver_source=_resolver_source,
            )

        # ── Step 2-N: Try each candidate CIK ─────────────────────────────────
        candidate_ciks_tried: list[str] = []
        # Track results for diagnostics when all candidates fail
        _last_no_nport_diag: Optional[dict[str, Any]] = None
        _last_error_diag: Optional[dict[str, Any]] = None
        # Per-candidate identity failure log — populated when a candidate yields
        # holdings but the series name does not match the requested ETF.
        _candidate_identity_failures: list[dict] = []
        # Carry last-parsed identity metadata for series_identity_not_proven response.
        _last_detected_series_name: Optional[str] = None
        _last_detected_registrant_name: Optional[str] = None
        _last_identity_mismatch_reason: Optional[str] = None
        _last_identity_filing_meta: Optional[NportFilingMeta] = None
        # Scan state (Stage 9F.2a multi-filing scan).
        filings_scanned_count: int = 0
        _scan_limit_reached: bool = False
        # Submissions structure diagnostics (Stage 9L — updated each candidate iteration).
        _sub_recent_form_count: int = 0
        _sub_has_files_pages: bool = False
        _sub_files_page_tried: bool = False

        for candidate_cik in candidate_ciks:
            candidate_ciks_tried.append(candidate_cik)
            cik_padded = _padded_cik(candidate_cik)
            cik_int = int(cik_padded)

            # ── Step 2: Fetch submissions ─────────────────────────────────────
            if request_count >= config.max_requests_per_ticker:
                break  # Budget exhausted — exit loop

            submissions_url = _SUBMISSIONS_URL_TPL.format(cik=cik_padded)
            try:
                resp_sub = _get(submissions_url)
                resp_sub.raise_for_status()
                request_count += 1
                sub_body = resp_sub.json() or {}
            except Exception as exc:  # noqa: BLE001
                if _is_timeout_exc(exc):
                    # Timeout is a hard stop — don't try more candidates
                    return _fail_closed(
                        ticker_upper, "timeout",
                        f"Timeout fetching submissions for CIK {cik_padded}: {exc}",
                        request_count, cik=cik_padded,
                        resolver_source=_resolver_source,
                        parent_registrant_name=_parent_registrant_name,
                        candidate_ciks_tried=candidate_ciks_tried,
                        selected_candidate_cik=cik_padded,
                    )
                # HTTP error (e.g. 404) — record and try next candidate
                _last_error_diag = {
                    "cik": cik_padded,
                    "error": str(exc),
                    "status": "sec_error",
                }
                continue  # Try next candidate

            # ── Collect recent NPORT filings and scan for identity match ─────
            # Stage 9F.2a: scan up to config.max_filings_to_scan filings per
            # candidate, newest first.  Identity mismatch → continue to next
            # filing (not next candidate) so a wrong-series latest filing does
            # not block finding the matching series in an earlier filing.
            recent_filings = _collect_recent_nport_filings(sub_body, config.max_filings_to_scan)

            # Track submissions structure diagnostics for no-data path.
            _sub_recent_form_count = len(
                (sub_body.get("filings") or {}).get("recent", {}).get("form") or []
            )
            _sub_files_pages_list = (sub_body.get("filings") or {}).get("files") or []
            _sub_has_files_pages = bool(_sub_files_pages_list)
            _sub_files_page_tried = False

            # ── Fallback: try first filings.files page when recent has no NPORT-P ─
            # Bounded: at most 1 extra HTTP call per candidate CIK.
            # Large registrants (Vanguard, Schwab) may have recent[] filled with
            # non-NPORT forms, pushing NPORT-P filings into a files[] page.
            if not recent_filings and _sub_files_pages_list and request_count < config.max_requests_per_ticker:
                _fp_entry = _sub_files_pages_list[0] if isinstance(_sub_files_pages_list[0], dict) else None
                _fp_name = _fp_entry.get("name") if _fp_entry else None
                if _fp_name:
                    _fp_url = f"https://data.sec.gov/submissions/{_fp_name}"
                    try:
                        _resp_fp = _get(_fp_url)
                        _resp_fp.raise_for_status()
                        request_count += 1
                        _fp_body = _resp_fp.json() or {}
                        # Files page body is flat — wrap for _collect_recent_nport_filings
                        recent_filings = _collect_recent_nport_filings(
                            {"filings": {"recent": _fp_body}},
                            config.max_filings_to_scan,
                        )
                        _sub_files_page_tried = True
                    except Exception:  # noqa: BLE001
                        pass

            if not recent_filings:
                # No NPORT filing for this CIK
                # Check if this is a commodity trust (GLD behavior)
                is_commodity_trust = (
                    _entry is not None and _entry.commodity_trust
                ) or ticker_upper in {"GLD"}
                if is_commodity_trust:
                    # Commodity trust — definitive, don't try more candidates
                    return _fail_closed(
                        ticker_upper, "commodity_trust_or_no_nport_data",
                        "Commodity trust — no NPORT-P/NPORT-EX filing expected (e.g. GLD bullion trust).",
                        request_count, cik=cik_padded,
                        resolver_source=_resolver_source,
                        parent_registrant_name=_parent_registrant_name,
                        candidate_ciks_tried=candidate_ciks_tried,
                        identity_status="commodity_trust_or_no_nport_data",
                        selected_candidate_cik=cik_padded,
                    )
                _last_no_nport_diag = {"cik": cik_padded}
                continue  # Try next candidate

            # Inner scan loop — try each filing until identity match or exhaustion
            for _filing_scan_rank, filing_info in enumerate(recent_filings, start=1):
                # Budget check before each filing document fetch
                if request_count >= config.max_requests_per_ticker:
                    _scan_limit_reached = True
                    break  # inner filing loop

                filings_scanned_count += 1

                acc_raw = filing_info["accession_number"]
                acc_nodash = acc_raw.replace("-", "")
                primary_doc = filing_info.get("primary_doc") or "primary_doc.xml"
                primary_doc_from_submissions = primary_doc
                selected_doc_source: Optional[str] = None
                candidate_doc_count: Optional[int] = None
                filing_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_int}&type=NPORT-P"

                # ── Step 2.5: Filing index discovery when primaryDocument is XSL viewer ─
                _prefetched_doc_text: Optional[str] = None
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
                                resolver_source=_resolver_source,
                                parent_registrant_name=_parent_registrant_name,
                                candidate_ciks_tried=candidate_ciks_tried,
                                identity_status="filing_not_parseable",
                                filings_scanned_count=filings_scanned_count,
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
                                    resolver_source=_resolver_source,
                                    parent_registrant_name=_parent_registrant_name,
                                    candidate_ciks_tried=candidate_ciks_tried,
                                    identity_status="timeout",
                                    filings_scanned_count=filings_scanned_count,
                                )
                            continue

                        try:
                            if _src_label == "index_json":
                                _idx_body = _resp_idx.json() or {}
                                _sel_doc, _n_cands = _select_best_nport_doc_from_index(_idx_body, acc_raw)
                            else:
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
                            break
                    else:
                        # Options A and B yielded no parseable document.
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
                                        resolver_source=_resolver_source,
                                        parent_registrant_name=_parent_registrant_name,
                                        candidate_ciks_tried=candidate_ciks_tried,
                                        identity_status="timeout",
                                        filings_scanned_count=filings_scanned_count,
                                    )

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
                                resolver_source=_resolver_source,
                                parent_registrant_name=_parent_registrant_name,
                                candidate_ciks_tried=candidate_ciks_tried,
                                identity_status="filing_not_parseable",
                                filings_scanned_count=filings_scanned_count,
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

                # ── Step 3: Fetch and parse the primary XML document ──────────────
                if _prefetched_doc_text is not None:
                    xml_text = _prefetched_doc_text
                else:
                    if request_count >= config.max_requests_per_ticker:
                        _scan_limit_reached = True
                        break  # inner filing loop — budget gone before XML fetch

                    doc_url = _FILING_URL_TPL.format(
                        cik_int=cik_int,
                        acc_nodash=acc_nodash,
                        primary_doc=primary_doc,
                    )
                    try:
                        resp_doc = _get(doc_url)
                        resp_doc.raise_for_status()
                        request_count += 1
                        xml_text = getattr(resp_doc, "text", None) or ""
                        if not xml_text and hasattr(resp_doc, "json"):
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
                                resolver_source=_resolver_source,
                                parent_registrant_name=_parent_registrant_name,
                                candidate_ciks_tried=candidate_ciks_tried,
                                identity_status="timeout",
                                filings_scanned_count=filings_scanned_count,
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
                            resolver_source=_resolver_source,
                            parent_registrant_name=_parent_registrant_name,
                            candidate_ciks_tried=candidate_ciks_tried,
                            identity_status="filing_not_parseable",
                            filings_scanned_count=filings_scanned_count,
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
                        resolver_source=_resolver_source,
                        parent_registrant_name=_parent_registrant_name,
                        candidate_ciks_tried=candidate_ciks_tried,
                        identity_status="filing_not_parseable",
                        filings_scanned_count=filings_scanned_count,
                    )

                holdings, fund_meta, parse_status = _parse_nport_xml(xml_text)

                if filing_meta is not None and fund_meta.get("xml_extracted_from_sgml"):
                    filing_meta.xml_extracted_from_sgml = True

                if parse_status == "xml_parse_error":
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
                        resolver_source=_resolver_source,
                        parent_registrant_name=_parent_registrant_name,
                        candidate_ciks_tried=candidate_ciks_tried,
                        identity_status="filing_not_parseable",
                        filings_scanned_count=filings_scanned_count,
                    )

                if parse_status == "no_holdings_container":
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
                        resolver_source=_resolver_source,
                        parent_registrant_name=_parent_registrant_name,
                        candidate_ciks_tried=candidate_ciks_tried,
                        identity_status="filing_not_parseable",
                        filings_scanned_count=filings_scanned_count,
                    )

                # ── Extract detected identity metadata ────────────────────────
                detected_series_name = fund_meta.get("series_name")
                detected_registrant_name = fund_meta.get("registrant_name")
                detected_series_id = fund_meta.get("series_id")
                detected_class_id = fund_meta.get("class_id")
                detected_class_name = fund_meta.get("class_name")

                # ── Handle zero holdings ──────────────────────────────────────
                if not holdings:
                    is_commodity_trust = (
                        _entry is not None and _entry.commodity_trust
                    ) or ticker_upper in {"GLD"}
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
                        resolver_source=_resolver_source,
                        parent_registrant_name=_parent_registrant_name,
                        candidate_ciks_tried=candidate_ciks_tried,
                        selected_candidate_cik=cik_padded,
                        detected_series_name=detected_series_name,
                        detected_registrant_name=detected_registrant_name,
                        detected_series_id=detected_series_id,
                        detected_class_id=detected_class_id,
                        detected_class_name=detected_class_name,
                        identity_status=status,
                        filings_scanned_count=filings_scanned_count,
                    )

                # ── Step 4: Identity verification ─────────────────────────────
                identity_status: Optional[str] = None
                identity_verified = False
                identity_basis: Optional[str] = None
                identity_mismatch_reason: Optional[str] = None

                if _resolver_source == "injected" or _entry is None:
                    # No entry = no identity hints; skip identity check.
                    # (Tests using cik_lookup_fn get this path — backward compatible.)
                    identity_status = None
                    identity_verified = False
                    identity_basis = "cik_injected_no_identity_check"

                elif _entry.commodity_trust:
                    # Should not reach here (handled above at zero-holdings), but defensive.
                    identity_status = "commodity_trust_or_no_nport_data"

                elif _entry.standalone_trust:
                    # Single-series trust — the filing IS this ETF by definition.
                    identity_status = _IDENTITY_STANDALONE
                    identity_verified = True
                    identity_basis = f"standalone_single_series_trust: {_entry.parent_name}"

                elif _entry.expected_series_names:
                    # Multi-series registrant: must verify series name matches.
                    if _identity_name_matches(detected_series_name, _entry.expected_series_names):
                        identity_status = _IDENTITY_VERIFIED
                        identity_verified = True
                        identity_basis = (
                            f"series_name_matched: detected={detected_series_name!r} "
                            f"against expected={_entry.expected_series_names!r}"
                        )
                    else:
                        # Series name does not match — this filing belongs to a different
                        # series of the same parent registrant.  Record the failure and
                        # continue to the next FILING (Stage 9F.2a multi-filing scan).
                        # This allows scanning past a wrong-series latest filing to find
                        # the matching series in an earlier filing.
                        mismatch_reason = (
                            f"Expected one of {_entry.expected_series_names!r}, "
                            f"detected seriesName={detected_series_name!r} "
                            f"in accession {acc_raw} (scan rank {_filing_scan_rank})"
                        )
                        _candidate_identity_failures.append({
                            "candidate_cik": cik_padded,
                            "accession_number": acc_raw,
                            "filing_rank": _filing_scan_rank,
                            "detected_series_name": detected_series_name,
                            "detected_registrant_name": detected_registrant_name,
                            "identity_mismatch_reason": mismatch_reason,
                        })
                        _last_detected_series_name = detected_series_name
                        _last_detected_registrant_name = detected_registrant_name
                        _last_identity_mismatch_reason = mismatch_reason
                        _last_identity_filing_meta = filing_meta
                        continue  # Try next filing within this candidate
                else:
                    # entry exists but no expected_series_names configured.
                    identity_status = "no_identity_hints_configured"
                    identity_basis = f"no_expected_series_names for {ticker_upper}"

                # ── Assess weight/value availability ─────────────────────────
                weights_available = any(h.weight_pct is not None for h in holdings)
                total_assets = fund_meta.get("total_assets_usd")
                total_reported_value_present = total_assets is not None

                weights_derived = False
                if not weights_available and total_assets and total_assets > 0:
                    holdings_with_values = [h for h in holdings if h.value_usd is not None]
                    if holdings_with_values:
                        for h in holdings_with_values:
                            if h.value_usd is not None:
                                h.weight_pct = round((h.value_usd / total_assets) * 100, 6)
                        weights_derived = True
                        weights_available = True

                # ── Return success ────────────────────────────────────────────
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
                    resolver_source=_resolver_source,
                    parent_registrant_name=_parent_registrant_name,
                    candidate_ciks_tried=candidate_ciks_tried,
                    selected_candidate_cik=cik_padded,
                    detected_series_name=detected_series_name,
                    detected_registrant_name=detected_registrant_name,
                    detected_series_id=detected_series_id,
                    detected_class_id=detected_class_id,
                    detected_class_name=detected_class_name,
                    identity_status=identity_status,
                    identity_verified=identity_verified,
                    identity_basis=identity_basis,
                    identity_mismatch_reason=identity_mismatch_reason,
                    candidate_identity_failures=_candidate_identity_failures,
                    filings_scanned_count=filings_scanned_count,
                    matching_filing_rank=_filing_scan_rank,
                )
            # ── End inner filing scan loop ────────────────────────────────────
            if _scan_limit_reached:
                break  # outer candidate loop — budget exhausted during filing scan

        # ── All candidates exhausted ──────────────────────────────────────────
        if _scan_limit_reached:
            return NportProviderResult(
                ticker=ticker_upper,
                fetch_status=_STATUS_SCAN_BUDGET_EXHAUSTED,
                error_message=(
                    f"Filing scan budget exhausted after scanning {filings_scanned_count} "
                    f"filing(s) across {len(candidate_ciks_tried)} candidate CIK(s). "
                    f"No matching series found. "
                    f"Identity mismatches: {len(_candidate_identity_failures)}. "
                    f"Increase max_requests_per_ticker or refine candidate CIKs."
                ),
                fetched_at=fetched_at,
                cik=candidate_ciks_tried[-1] if candidate_ciks_tried else None,
                filing_meta=_last_identity_filing_meta,
                request_count=request_count,
                resolver_source=_resolver_source,
                parent_registrant_name=_parent_registrant_name,
                candidate_ciks_tried=candidate_ciks_tried,
                selected_candidate_cik=None,
                detected_series_name=_last_detected_series_name,
                detected_registrant_name=_last_detected_registrant_name,
                identity_status=_STATUS_SCAN_BUDGET_EXHAUSTED,
                identity_verified=False,
                identity_mismatch_reason=_last_identity_mismatch_reason,
                candidate_identity_failures=_candidate_identity_failures,
                filings_scanned_count=filings_scanned_count,
                scan_limit_reached=True,
            )

        if _candidate_identity_failures:
            # All filings scanned across all candidates; no series identity match found.
            return NportProviderResult(
                ticker=ticker_upper,
                fetch_status=_IDENTITY_NOT_PROVEN,
                error_message=(
                    f"Scanned {filings_scanned_count} NPORT filing(s) across "
                    f"{len(candidate_ciks_tried)} CIK candidate(s) — "
                    f"series identity not proven for {ticker_upper}. "
                    f"Last mismatch: {_last_identity_mismatch_reason}"
                ),
                fetched_at=fetched_at,
                cik=candidate_ciks_tried[-1] if candidate_ciks_tried else None,
                filing_meta=_last_identity_filing_meta,
                request_count=request_count,
                resolver_source=_resolver_source,
                parent_registrant_name=_parent_registrant_name,
                candidate_ciks_tried=candidate_ciks_tried,
                selected_candidate_cik=None,
                detected_series_name=_last_detected_series_name,
                detected_registrant_name=_last_detected_registrant_name,
                identity_status=_IDENTITY_NOT_PROVEN,
                identity_verified=False,
                identity_mismatch_reason=_last_identity_mismatch_reason,
                candidate_identity_failures=_candidate_identity_failures,
                filings_scanned_count=filings_scanned_count,
            )

        if _last_error_diag:
            # All candidates returned sec_error/404
            return _fail_closed(
                ticker_upper, "sec_error",
                (
                    f"All {len(candidate_ciks_tried)} CIK candidate(s) returned HTTP errors. "
                    f"Last error CIK: {_last_error_diag.get('cik')!r}. "
                    f"Tickers: {ticker_upper}. Check resolver map."
                ),
                request_count,
                cik=candidate_ciks_tried[-1] if candidate_ciks_tried else None,
                resolver_source=_resolver_source,
                parent_registrant_name=_parent_registrant_name,
                candidate_ciks_tried=candidate_ciks_tried,
                identity_status="sec_error",
            )

        if _last_no_nport_diag:
            # All candidates had no NPORT filing
            is_commodity_trust = _entry is not None and _entry.commodity_trust
            status = "commodity_trust_or_no_nport_data" if is_commodity_trust else "no_nport_filing"
            msg = (
                "Commodity trust — no NPORT-P/NPORT-EX filing expected."
                if is_commodity_trust
                else (
                    f"No NPORT-P or NPORT-EX filing found for any of the "
                    f"{len(candidate_ciks_tried)} CIK candidate(s) tried: "
                    f"{candidate_ciks_tried!r}. "
                    f"filings.recent had {_sub_recent_form_count} form(s); "
                    f"filings.files pages present: {_sub_has_files_pages}; "
                    f"files page tried: {_sub_files_page_tried}. "
                    "Verify CIK via SEC EDGAR company search or add candidate_ciks fallbacks."
                )
            )
            res = _fail_closed(
                ticker_upper, status, msg, request_count,
                cik=candidate_ciks_tried[-1] if candidate_ciks_tried else None,
                resolver_source=_resolver_source,
                parent_registrant_name=_parent_registrant_name,
                candidate_ciks_tried=candidate_ciks_tried,
                identity_status=status,
            )
            res.submissions_recent_form_count = _sub_recent_form_count
            res.submissions_has_files_pages = _sub_has_files_pages
            res.submissions_files_page_tried = _sub_files_page_tried
            return res

        # Budget exhausted before any candidate produced a result
        return _fail_closed(
            ticker_upper, "sec_error",
            "Request budget exhausted before completing candidate CIK resolution.",
            request_count,
            resolver_source=_resolver_source,
            parent_registrant_name=_parent_registrant_name,
            candidate_ciks_tried=candidate_ciks_tried,
            identity_status="sec_error",
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
