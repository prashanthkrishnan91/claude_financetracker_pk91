"""Stage 9F.2c — ETF Issuer Source Certification Layer.

Diagnostic-only. Probes issuer-official holdings URLs and certifies whether
each source satisfies four requirements:
  1. HTTP reachable (status 200, non-empty body)
  2. Content looks like CSV (not HTML or binary)
  3. Fund identity provable (fund name in metadata rows matches expected)
  4. As-of date present (data freshness can be verified)
  5. Percent weight column present (holdings weights available)

Certification statuses (CERT_STATUS_* constants):
  CERTIFIED            — HTTP 200 + identity + as_of + weights all proven.
  FETCH_FAILED         — HTTP error, network error, or timeout.
  IDENTITY_NOT_PROVEN  — Fetched OK but fund name absent or mismatched.
  AS_OF_NOT_PROVEN     — Identity proven but as-of date absent.
  WEIGHTS_NOT_PROVEN   — Identity + as-of proven but no percent weight column.
  SOURCE_NOT_FOUND     — No candidate URL configured for this ticker/provider.

Stage 9F.2b runtime evidence:
  - Vanguard investor.vanguard.com CSV URLs returned 404 for all 7 Vanguard ETFs.
  - SSGA ssga.com CSV URL returned 404 for XLE.
  - SCHD: no confirmed stable public CSV URL.
  - QQQ: already succeeds via SEC NPORT; Invesco URL is secondary.

Hard constraints:
  - Never raises; always returns SourceCertificationResult.
  - canonical_ready=False and safe_for_decision=False always.
  - No paid providers, no LLM, no DB writes, no holdings extraction.
  - No guessed URLs — only issuer-official domains.
  - Live HTTP stays out of default CI (injectable http_get_fn).
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Certification status constants ─────────────────────────────────────────────

CERT_STATUS_CERTIFIED = "CERTIFIED"
CERT_STATUS_FETCH_FAILED = "FETCH_FAILED"
CERT_STATUS_IDENTITY_NOT_PROVEN = "IDENTITY_NOT_PROVEN"
CERT_STATUS_AS_OF_NOT_PROVEN = "AS_OF_NOT_PROVEN"
CERT_STATUS_WEIGHTS_NOT_PROVEN = "WEIGHTS_NOT_PROVEN"
CERT_STATUS_SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"

# Proof status values
PROOF_PROVEN = "proven"
PROOF_NOT_PROVEN = "not_proven"
PROOF_NOT_CHECKED = "not_checked"

# File shape hints (not a decision; diagnostic label only)
SHAPE_CSV_WITH_HEADER = "csv_with_header"
SHAPE_HTML = "html"
SHAPE_EMPTY = "empty"
SHAPE_FETCH_FAILED = "fetch_failed"
SHAPE_UNKNOWN = "unknown"

CERTIFIER_VERSION = "stage9f2c_v1"


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class CandidateProbeResult:
    """Result of probing one candidate URL."""
    url: str
    url_label: str                    # Human-readable label for this URL variant
    http_status: Optional[int]
    content_type: Optional[str]
    file_shape_hint: str              # SHAPE_* constant
    identity_proof: str               # PROOF_* constant
    as_of_proof: str
    weight_proof: str
    certification_status: str         # CERT_STATUS_* constant
    certification_reason: str


@dataclass
class SourceCertificationResult:
    """Certification result for one (ticker, provider_id) pair.

    Covers all candidate URLs tried for this issuer provider + ticker.
    If any candidate is CERTIFIED, selected_source_url is set.
    """
    ticker: str
    provider_id: str
    issuer_family: str
    candidate_urls_checked: list[str]
    selected_source_url: Optional[str]        # URL that achieved CERTIFIED, if any
    source_certification_status: str          # CERT_STATUS_* constant (best achieved)
    source_certification_reason: str
    http_status: Optional[int]                # of the selected/best candidate
    content_type: Optional[str]
    identity_proof: str                       # PROOF_* constant
    as_of_proof: str
    weight_proof: str
    candidate_probes: list[CandidateProbeResult] = field(default_factory=list)
    certifier_version: str = CERTIFIER_VERSION
    # Governance invariants — never mutated
    canonical_ready: bool = False
    safe_for_decision: bool = False


# ── Candidate URL configuration ────────────────────────────────────────────────
#
# Only issuer-official domains. No third-party aggregators or browser-only pages.
# Stage 9F.2b evidence noted inline.

_VANGUARD_CSV_TEMPLATE = (
    "https://investor.vanguard.com/content/dam/fas-portspec-images/downloads/"
    "etf-shares/{ticker}_QuantDataFundHoldings.csv"
)

_SSGA_CSV_TEMPLATE = (
    "https://www.ssga.com/library-content/products/fund-data/etfs/us/"
    "holdings-daily-us-en-{ticker_lower}.csv"
)

_INVESCO_HOLDINGS_TEMPLATE = (
    "https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/"
    "0/{ticker}/false/relevance/asc/25"
)

# (url_template, label) pairs per provider_id.
# Schwab has no publicly stable CSV URL — returns SOURCE_NOT_FOUND.
_PROVIDER_CANDIDATES: dict[str, list[tuple[str, str]]] = {
    "vanguard_official_v1": [
        (_VANGUARD_CSV_TEMPLATE, "vanguard_investor_csv_v1"),
        # Stage 9F.2b: returned 404 at runtime for VOO/VTI/VGT/VHT/VIS/VXUS/VYM.
    ],
    "spdr_official_v1": [
        (_SSGA_CSV_TEMPLATE, "ssga_daily_csv_v1"),
        # Stage 9F.2b: returned 404 at runtime for XLE.
    ],
    "schwab_official_v1": [
        # No confirmed publicly stable Schwab ETF holdings CSV URL.
        # SOURCE_NOT_FOUND is the correct status until a URL is discovered.
    ],
    "invesco_official_v1": [
        (_INVESCO_HOLDINGS_TEMPLATE, "invesco_holdings_page_v1"),
        # Note: Invesco URL may return HTML (not CSV); expect FETCH_FAILED or
        # source_shape_changed. QQQ is already proven via SEC NPORT.
    ],
    "gld_commodity_v1": [
        # GLD commodity trust — no equity holdings CSV; certification not applicable.
    ],
}

_PROVIDER_ISSUER_FAMILY: dict[str, str] = {
    "vanguard_official_v1": "vanguard",
    "spdr_official_v1": "ssga_spdr",
    "schwab_official_v1": "schwab",
    "invesco_official_v1": "invesco",
    "gld_commodity_v1": "ssga_spdr",
}

# Expected fund names per ticker (for identity verification).
_EXPECTED_FUND_NAMES: dict[str, tuple[str, ...]] = {
    "VOO":  ("Vanguard S&P 500 ETF", "Vanguard S&P 500 Index Fund", "Vanguard 500 Index Fund"),
    "VTI":  ("Vanguard Total Stock Market ETF", "Vanguard Total Stock Market Index Fund"),
    "VGT":  ("Vanguard Information Technology ETF", "Vanguard Information Technology Index Fund"),
    "VHT":  ("Vanguard Health Care ETF", "Vanguard Health Care Index Fund"),
    "VIS":  ("Vanguard Industrials ETF", "Vanguard Industrials Index Fund"),
    "VXUS": ("Vanguard Total International Stock ETF", "Vanguard Total International Stock Index Fund"),
    "VYM":  ("Vanguard High Dividend Yield ETF", "Vanguard High Dividend Yield Index Fund"),
    "XLE":  ("Energy Select Sector SPDR Fund", "Energy Select Sector", "XLE"),
    "SPY":  ("SPDR S&P 500 ETF Trust", "S&P 500"),
    "SCHD": ("Schwab U.S. Dividend Equity ETF", "Schwab US Dividend Equity ETF", "SCHD"),
    "QQQ":  ("Invesco QQQ Trust", "Invesco QQQ", "QQQ"),
    "GLD":  ("SPDR Gold Shares", "SPDR Gold Trust", "GLD"),
}

# Weight column names (normalized) that prove percent weights are present.
_WEIGHT_COLUMN_NAMES = frozenset({
    "weight", "weight ()", "weight pct", "of fund", "of funds", "of net assets",
    "pct", "percent", "portfolio weight", "weight",
})

_MAX_PROBE_LINES = 30
_MAX_CONTENT_TYPE_LEN = 100


# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalize(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9\s%]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _fund_name_matches(detected: str, expected: tuple[str, ...]) -> bool:
    if not detected or not expected:
        return False
    det = _normalize(detected)
    if not det:
        return False
    for exp in expected:
        exp_n = _normalize(exp)
        if exp_n and (det == exp_n or exp_n in det or det in exp_n):
            return True
    return False


def _has_date_pattern(text: str) -> bool:
    """Return True if text contains a plausible date string."""
    patterns = [
        r"\d{1,2}/\d{1,2}/\d{4}",
        r"\d{4}-\d{2}-\d{2}",
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2},?\s+\d{4}",
        r"\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{4}",
    ]
    low = text.lower()
    for p in patterns:
        if re.search(p, low):
            return True
    return False


def _is_html_content(text: str) -> bool:
    stripped = text.strip().lower()
    return stripped.startswith("<!doctype") or stripped.startswith("<html")


def _probe_csv_text(
    text: str,
    ticker: str,
) -> tuple[str, str, str, str]:
    """Probe CSV text for fund identity, as-of date, and weight column.

    Returns (file_shape_hint, identity_proof, as_of_proof, weight_proof).
    Does NOT parse full holdings — lightweight probe only.
    """
    if not text or not text.strip():
        return SHAPE_EMPTY, PROOF_NOT_CHECKED, PROOF_NOT_CHECKED, PROOF_NOT_CHECKED

    if _is_html_content(text):
        return SHAPE_HTML, PROOF_NOT_CHECKED, PROOF_NOT_CHECKED, PROOF_NOT_CHECKED

    expected_names = _EXPECTED_FUND_NAMES.get(ticker.upper(), ())

    try:
        reader = csv.reader(io.StringIO(text))
        lines = []
        for i, row in enumerate(reader):
            if i >= _MAX_PROBE_LINES:
                break
            lines.append(row)
    except Exception:  # noqa: BLE001
        return SHAPE_UNKNOWN, PROOF_NOT_CHECKED, PROOF_NOT_CHECKED, PROOF_NOT_CHECKED

    if not lines:
        return SHAPE_EMPTY, PROOF_NOT_CHECKED, PROOF_NOT_CHECKED, PROOF_NOT_CHECKED

    # Identity probe: scan early rows for fund name.
    identity_proof = PROOF_NOT_PROVEN
    for row in lines[:15]:
        cell = (row[0].strip() if row else "").strip()
        if len(cell) > 8 and not re.match(r"^[\d,\.%\-]+$", cell):
            if expected_names and _fund_name_matches(cell, expected_names):
                identity_proof = PROOF_PROVEN
                break

    # As-of date probe: scan early rows for a date pattern.
    as_of_proof = PROOF_NOT_PROVEN
    for row in lines[:15]:
        row_text = " ".join(c.strip() for c in row if c.strip())
        if re.search(r"as\s*of|date", row_text, re.IGNORECASE) and _has_date_pattern(row_text):
            as_of_proof = PROOF_PROVEN
            break
        # Also accept a bare date pattern in metadata rows.
        if _has_date_pattern(row_text) and len(row_text) < 80:
            as_of_proof = PROOF_PROVEN
            break

    # Weight column probe: find a header row and check for weight column.
    weight_proof = PROOF_NOT_PROVEN
    header_found = False
    for row in lines:
        norm_cells = [_normalize(c) for c in row]
        # Detect header row.
        if any(kw in cell for cell in norm_cells for kw in ("name", "holding", "ticker", "symbol")):
            header_found = True
            # Check for weight column.
            for cell in norm_cells:
                for wkw in ("weight", "pct", "percent", "of fund", "of funds", "of net assets"):
                    if wkw in cell:
                        weight_proof = PROOF_PROVEN
                        break
                if weight_proof == PROOF_PROVEN:
                    break
            break

    shape = SHAPE_CSV_WITH_HEADER if header_found else SHAPE_UNKNOWN
    return shape, identity_proof, as_of_proof, weight_proof


def _build_probe_result(
    url: str,
    url_label: str,
    http_status: Optional[int],
    content_type: Optional[str],
    file_shape_hint: str,
    identity_proof: str,
    as_of_proof: str,
    weight_proof: str,
) -> CandidateProbeResult:
    """Build CandidateProbeResult and compute certification_status."""
    # Determine certification status bottom-up (fail-closed at each gate).
    if http_status is None:
        status = CERT_STATUS_FETCH_FAILED
        reason = "HTTP fetch failed — network error or timeout"
    elif http_status != 200:
        status = CERT_STATUS_FETCH_FAILED
        reason = f"HTTP {http_status} — source not reachable"
    elif file_shape_hint == SHAPE_HTML:
        status = CERT_STATUS_FETCH_FAILED
        reason = "Response is HTML — not a downloadable holdings file"
    elif file_shape_hint == SHAPE_EMPTY:
        status = CERT_STATUS_FETCH_FAILED
        reason = "Response body is empty"
    elif identity_proof == PROOF_NOT_PROVEN:
        status = CERT_STATUS_IDENTITY_NOT_PROVEN
        reason = "Fund identity not provable from file metadata"
    elif as_of_proof == PROOF_NOT_PROVEN:
        status = CERT_STATUS_AS_OF_NOT_PROVEN
        reason = "As-of date not found in file — data freshness cannot be verified"
    elif weight_proof == PROOF_NOT_PROVEN:
        status = CERT_STATUS_WEIGHTS_NOT_PROVEN
        reason = "Percent weight column not found in CSV header"
    else:
        status = CERT_STATUS_CERTIFIED
        reason = (
            f"HTTP 200 — identity proven, as-of date found, "
            f"percent weight column confirmed"
        )

    return CandidateProbeResult(
        url=url,
        url_label=url_label,
        http_status=http_status,
        content_type=content_type[:_MAX_CONTENT_TYPE_LEN] if content_type else None,
        file_shape_hint=file_shape_hint,
        identity_proof=identity_proof,
        as_of_proof=as_of_proof,
        weight_proof=weight_proof,
        certification_status=status,
        certification_reason=reason,
    )


def _probe_url(
    url: str,
    url_label: str,
    ticker: str,
    http_get_fn: Callable[[str], Any],
) -> CandidateProbeResult:
    """Probe one URL and return a CandidateProbeResult."""
    try:
        resp = http_get_fn(url)
        resp.raise_for_status()
        http_status = getattr(resp, "status_code", 200)
        content_type = getattr(resp, "headers", {}).get("content-type", None) if hasattr(resp, "headers") else None
        text = getattr(resp, "text", None) or ""
        if not text:
            try:
                text = resp.content.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                text = ""
    except Exception as exc:  # noqa: BLE001
        exc_str = str(exc)
        # Extract HTTP status if present in exception message.
        http_status_m = re.search(r"(\d{3})", exc_str)
        http_status = int(http_status_m.group(1)) if http_status_m else None
        return _build_probe_result(
            url, url_label, http_status, None,
            SHAPE_FETCH_FAILED, PROOF_NOT_CHECKED, PROOF_NOT_CHECKED, PROOF_NOT_CHECKED,
        )

    shape, identity_proof, as_of_proof, weight_proof = _probe_csv_text(text, ticker)
    return _build_probe_result(
        url, url_label, http_status, content_type,
        shape, identity_proof, as_of_proof, weight_proof,
    )


# ── Public API ─────────────────────────────────────────────────────────────────


def certify_issuer_source(
    ticker: str,
    provider_id: str,
    http_get_fn: Optional[Callable[[str], Any]] = None,
) -> SourceCertificationResult:
    """Probe all candidate URLs for an issuer-official ETF holdings source.

    Returns SourceCertificationResult — always. Never raises.

    For each candidate URL:
      1. HTTP GET (injectable http_get_fn or real httpx.Client).
      2. HTTP status + content-type recorded.
      3. Lightweight CSV probe: fund identity, as-of date, weight column.
      4. CandidateProbeResult recorded.

    Stops at first CERTIFIED candidate. Reports all tried.

    Args:
        ticker:       Uppercase ETF ticker (e.g. "VOO").
        provider_id:  Provider ID (e.g. "vanguard_official_v1").
        http_get_fn:  Injectable HTTP GET callable for tests.
                      Signature: fn(url) -> response with .raise_for_status() and .text
                      If None, httpx.Client(timeout=20.0) is used.
    """
    ticker_upper = ticker.upper().strip()
    issuer_family = _PROVIDER_ISSUER_FAMILY.get(provider_id, "unknown")
    candidates = _PROVIDER_CANDIDATES.get(provider_id, [])

    def _not_found(reason: str) -> SourceCertificationResult:
        return SourceCertificationResult(
            ticker=ticker_upper,
            provider_id=provider_id,
            issuer_family=issuer_family,
            candidate_urls_checked=[],
            selected_source_url=None,
            source_certification_status=CERT_STATUS_SOURCE_NOT_FOUND,
            source_certification_reason=reason,
            http_status=None,
            content_type=None,
            identity_proof=PROOF_NOT_CHECKED,
            as_of_proof=PROOF_NOT_CHECKED,
            weight_proof=PROOF_NOT_CHECKED,
            candidate_probes=[],
            canonical_ready=False,
            safe_for_decision=False,
        )

    if not candidates:
        return _not_found(
            f"No candidate URL configured for {provider_id}/{ticker_upper}. "
            "Post-deploy: discover and validate the official holdings CSV URL."
        )

    # Resolve HTTP client.
    _get: Callable[[str], Any]
    _own_client = False
    http_client: Any = None
    try:
        if http_get_fn is not None:
            _get = http_get_fn
        else:
            import httpx
            http_client = httpx.Client(timeout=20.0)
            _get = http_client.get
            _own_client = True

        probes: list[CandidateProbeResult] = []
        candidate_urls: list[str] = []
        certified_probe: Optional[CandidateProbeResult] = None

        for url_template, url_label in candidates:
            url = url_template.format(
                ticker=ticker_upper,
                ticker_lower=ticker_upper.lower(),
            )
            candidate_urls.append(url)
            probe = _probe_url(url, url_label, ticker_upper, _get)
            probes.append(probe)

            if probe.certification_status == CERT_STATUS_CERTIFIED:
                certified_probe = probe
                break  # Stop at first certified source.

        # Use the certified probe if found; otherwise the last attempted.
        best = certified_probe or probes[-1]

        return SourceCertificationResult(
            ticker=ticker_upper,
            provider_id=provider_id,
            issuer_family=issuer_family,
            candidate_urls_checked=candidate_urls,
            selected_source_url=best.url if certified_probe else None,
            source_certification_status=best.certification_status,
            source_certification_reason=best.certification_reason,
            http_status=best.http_status,
            content_type=best.content_type,
            identity_proof=best.identity_proof,
            as_of_proof=best.as_of_proof,
            weight_proof=best.weight_proof,
            candidate_probes=probes,
            canonical_ready=False,
            safe_for_decision=False,
        )

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "etf_issuer_source_certifier_error provider=%s ticker=%s: %s",
            provider_id, ticker_upper, exc,
        )
        return _not_found(
            f"Unexpected error in certifier for {provider_id}/{ticker_upper}: {exc}"
        )
    finally:
        if _own_client and http_client is not None:
            try:
                http_client.close()
            except Exception:  # noqa: BLE001
                pass


def build_certification_dict(cert: SourceCertificationResult) -> dict:
    """Convert a SourceCertificationResult to a compact diagnostic dict.

    Excludes candidate_probes details (too verbose for the top-level response).
    Only includes per-candidate summary counts.
    """
    return {
        "certifier_version": cert.certifier_version,
        "candidate_urls_checked": cert.candidate_urls_checked,
        "candidate_urls_checked_count": len(cert.candidate_urls_checked),
        "selected_source_url": cert.selected_source_url,
        "source_certification_status": cert.source_certification_status,
        "source_certification_reason": cert.source_certification_reason,
        "http_status": cert.http_status,
        "content_type": cert.content_type,
        "identity_proof": cert.identity_proof,
        "as_of_proof": cert.as_of_proof,
        "weight_proof": cert.weight_proof,
        "issuer_family": cert.issuer_family,
        # Per-candidate summary (status only, no full probe details).
        "candidate_statuses": [
            {
                "url_label": p.url_label,
                "http_status": p.http_status,
                "certification_status": p.certification_status,
                "file_shape_hint": p.file_shape_hint,
            }
            for p in cert.candidate_probes
        ],
        "canonical_ready": False,
        "safe_for_decision": False,
    }
