"""Phase 6A / Phase 7A — Synchronous SEC EDGAR public JSON API provider for research workers.

Fetches recent filing metadata and optional CompanyFacts XBRL data for one ticker:
  1. https://www.sec.gov/files/company_tickers.json        — ticker→CIK mapping (request 1)
  2. https://data.sec.gov/submissions/CIK{cik}.json        — recent filings metadata (request 2)
  3. https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json — XBRL metric facts (request 3)
     Phase 7A: fetched only when request_count < max_requests_per_ticker after request 2.
     Fail-closed — submissions success is preserved if companyfacts fails.

Hard constraints enforced by this module:
  - Requires a declared User-Agent (SEC_EDGAR_USER_AGENT from settings).
    No anonymous or undeclared calls are ever made.
  - All HTTP calls are bounded by timeout_seconds.
  - Total requests per ticker are capped at max_requests_per_ticker (default 3).
  - Fail-closed on any error, timeout, rate-limit, or malformed response.
    No exceptions escape — always returns SecEdgarProviderResult.
  - Never fabricates data — returns only what the SEC API provides.
  - Raw companyfacts JSON is never persisted — only parsed MetricObservations are carried.
  - Deterministic and testable: inject http_get_fn to avoid real HTTP calls in tests.
  - Never runs outside explicit worker invocation (not on page load).

Dependency: httpx (already in requirements.txt). Only imported when http_get_fn is None
(i.e., not imported in test code that supplies its own fake).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

from .sec_companyfacts_parser import (
    CompanyFactsParseResult,
    parse_companyfacts,
)

_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
_COMPANYFACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_DEFAULT_TIMEOUT_SECONDS: float = 10.0
_DEFAULT_MAX_REQUESTS: int = 3
_DEFAULT_MAX_FILINGS: int = 5
_RELEVANT_FORMS: frozenset[str] = frozenset({"10-K", "10-Q", "8-K"})


@dataclass
class SecFilingRecord:
    """Metadata for one SEC filing from the submissions endpoint."""

    form_type: str              # "10-K", "10-Q", or "8-K"
    filing_date: str            # YYYY-MM-DD
    accession_number: str       # e.g. "0000320193-23-000054"
    report_date: Optional[str]  # period-of-report date YYYY-MM-DD, or None
    filing_url: str             # EDGAR Archives filing index URL


@dataclass
class SecEdgarProviderConfig:
    """Immutable config for one SEC EDGAR fetch session.

    All values must come from Settings (env vars) — never from user input at runtime.
    """

    user_agent: str               # Required per SEC TOS. "AppName/v contact@example.com"
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_requests_per_ticker: int = _DEFAULT_MAX_REQUESTS
    max_filings_to_return: int = _DEFAULT_MAX_FILINGS


@dataclass
class SecEdgarProviderResult:
    """Structured result of one SEC EDGAR fetch attempt for a single ticker.

    fetch_status values:
      success       — fetch completed; filings list may be empty if none found
      no_user_agent — user_agent not configured; no HTTP call was made
      no_cik        — ticker not found in SEC EDGAR ticker map
      timeout       — HTTP call timed out
      rate_limited  — HTTP 429 received
      malformed     — response was not valid JSON or had unexpected structure
      error         — any other exception

    Phase 7A addition:
      companyfacts_parse_result — parsed MetricObservations from request 3, or None if
      companyfacts was not fetched or failed. Raw JSON is never stored here.
    """

    ticker: str
    cik: Optional[str] = None
    filings: list[SecFilingRecord] = field(default_factory=list)
    fetch_status: str = "unknown"
    error_message: Optional[str] = None
    fetched_at: str = ""
    request_count: int = 0
    # Phase 7A: parsed metric observations from companyfacts (never raw JSON).
    companyfacts_parse_result: Optional[CompanyFactsParseResult] = None

    @property
    def is_success(self) -> bool:
        return self.fetch_status == "success"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _padded_cik(cik_int: int) -> str:
    """CIK padded to 10 digits with leading zeros."""
    return str(cik_int).zfill(10)


def _filing_index_url(cik_int: int, accession_number: str) -> str:
    """EDGAR Archives filing index URL from numeric CIK and accession number."""
    acc_nodash = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"


def _fail_closed(
    ticker: str,
    status: str,
    message: str,
    request_count: int = 0,
) -> SecEdgarProviderResult:
    return SecEdgarProviderResult(
        ticker=ticker,
        fetch_status=status,
        error_message=message,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        request_count=request_count,
    )


def _is_timeout_exc(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "timeout" in str(exc).lower()


def _is_rate_limit_exc(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    return resp is not None and getattr(resp, "status_code", 0) == 429


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_for_ticker(
    ticker: str,
    config: SecEdgarProviderConfig,
    http_get_fn: Optional[Callable[[str], Any]] = None,
) -> SecEdgarProviderResult:
    """Fetch recent SEC EDGAR filing metadata for one ticker.

    Returns SecEdgarProviderResult — always. Never raises.

    Args:
        ticker:      Stock ticker symbol (case-insensitive).
        config:      Provider configuration with user_agent and limits.
        http_get_fn: Optional injectable callable(url) → response-like object.
                     Response must expose .raise_for_status() and .json() methods.
                     If None, httpx.Client is created internally with the configured
                     User-Agent header and timeout. Pass a fake for tests.
    """
    ticker_upper = ticker.upper().strip()
    fetched_at = datetime.now(timezone.utc).isoformat()
    request_count = 0

    # Gate: user agent is required per SEC terms of service.
    if not config.user_agent or not config.user_agent.strip():
        return SecEdgarProviderResult(
            ticker=ticker_upper,
            fetch_status="no_user_agent",
            error_message="SEC_EDGAR_USER_AGENT not configured. No anonymous calls permitted.",
            fetched_at=fetched_at,
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
            _get = http_client.get
        else:
            _get = http_get_fn

        # ── Request 1: ticker → CIK mapping ──────────────────────────────────
        if request_count >= config.max_requests_per_ticker:
            return _fail_closed(ticker_upper, "error",
                                "Request cap hit before CIK lookup.", request_count)
        try:
            resp1 = _get(_COMPANY_TICKERS_URL)
            resp1.raise_for_status()
            request_count += 1
        except Exception as exc:  # noqa: BLE001
            if _is_timeout_exc(exc):
                return _fail_closed(ticker_upper, "timeout",
                                    f"Timeout on CIK lookup: {exc}", request_count)
            if _is_rate_limit_exc(exc):
                return _fail_closed(ticker_upper, "rate_limited",
                                    "Rate limited by SEC EDGAR (CIK lookup).", request_count)
            return _fail_closed(ticker_upper, "error",
                                f"Error on CIK lookup: {exc}", request_count)
        try:
            ticker_map_raw = resp1.json() or {}
        except Exception as exc:  # noqa: BLE001
            return _fail_closed(ticker_upper, "malformed",
                                f"Malformed ticker map response: {exc}", request_count)

        # Parse ticker → CIK integer.
        cik_int: Optional[int] = None
        cik_padded: Optional[str] = None
        try:
            for entry in ticker_map_raw.values():
                if not isinstance(entry, dict):
                    continue
                t = (entry.get("ticker") or "").upper().strip()
                if t == ticker_upper:
                    raw_cik = entry.get("cik_str")
                    if raw_cik:
                        cik_int = int(raw_cik)
                        cik_padded = _padded_cik(cik_int)
                    break
        except Exception as exc:  # noqa: BLE001
            return _fail_closed(ticker_upper, "malformed",
                                f"Malformed ticker map JSON: {exc}", request_count)

        if not cik_padded:
            return SecEdgarProviderResult(
                ticker=ticker_upper,
                fetch_status="no_cik",
                error_message=f"Ticker {ticker_upper!r} not found in SEC EDGAR ticker map.",
                fetched_at=fetched_at,
                request_count=request_count,
            )

        # ── Request 2: recent submissions for this CIK ────────────────────────
        if request_count >= config.max_requests_per_ticker:
            return _fail_closed(ticker_upper, "error",
                                "Request cap hit before submissions fetch.", request_count)
        sub_url = _SUBMISSIONS_URL_TEMPLATE.format(cik=cik_padded)
        try:
            resp2 = _get(sub_url)
            resp2.raise_for_status()
            sub_data = resp2.json() or {}
            request_count += 1
        except Exception as exc:  # noqa: BLE001
            if _is_timeout_exc(exc):
                return _fail_closed(ticker_upper, "timeout",
                                    f"Timeout on submissions: {exc}", request_count)
            if _is_rate_limit_exc(exc):
                return _fail_closed(ticker_upper, "rate_limited",
                                    "Rate limited by SEC EDGAR (submissions).", request_count)
            return _fail_closed(ticker_upper, "error",
                                f"Error on submissions: {exc}", request_count)

        # Parse recent filings list.
        filings: list[SecFilingRecord] = []
        try:
            recent = (sub_data.get("filings") or {}).get("recent") or {}
            forms = recent.get("form") or []
            dates = recent.get("filingDate") or []
            accessions = recent.get("accessionNumber") or []
            report_dates = recent.get("reportDate") or []

            for i, form in enumerate(forms):
                if len(filings) >= config.max_filings_to_return:
                    break
                form_str = str(form).upper().strip()
                if form_str not in _RELEVANT_FORMS:
                    continue
                fd = dates[i] if i < len(dates) else ""
                acc = accessions[i] if i < len(accessions) else ""
                if not fd or not acc:
                    continue
                rd = report_dates[i] if i < len(report_dates) else None
                filings.append(SecFilingRecord(
                    form_type=form_str,
                    filing_date=str(fd),
                    accession_number=str(acc),
                    report_date=str(rd) if rd else None,
                    filing_url=_filing_index_url(cik_int, str(acc)),
                ))

            # Phase 14C.5: Include the latest 10-K even when recent 10-Qs fill
            # the max_filings_to_return cap before it appears.
            # For mature companies (AAPL, MSFT, etc.) the 5 most recent relevant
            # filings are often all 10-Qs, leaving the annual 10-K outside the
            # collected set. Without the 10-K accession in source_accessions, the
            # companyfacts parser skips all FY EPS observations.
            # Additive only: scan for the most recent 10-K if not already present.
            # Bounded: at most one additional 10-K filing is appended.
            if not any(f.form_type == "10-K" for f in filings):
                _collected_accns = frozenset(f.accession_number for f in filings)
                for _i, _form in enumerate(forms):
                    _form_str = str(_form).upper().strip()
                    if _form_str != "10-K":
                        continue
                    _fd = dates[_i] if _i < len(dates) else ""
                    _acc = accessions[_i] if _i < len(accessions) else ""
                    if not _fd or not _acc or str(_acc) in _collected_accns:
                        continue
                    _rd = report_dates[_i] if _i < len(report_dates) else None
                    filings.append(SecFilingRecord(
                        form_type=_form_str,
                        filing_date=str(_fd),
                        accession_number=str(_acc),
                        report_date=str(_rd) if _rd else None,
                        filing_url=_filing_index_url(cik_int, str(_acc)),
                    ))
                    break  # Only the most recent 10-K — bounded.

        except Exception as exc:  # noqa: BLE001
            return _fail_closed(ticker_upper, "malformed",
                                f"Malformed submissions JSON: {exc}", request_count)

        # ── Request 3 (Phase 7A): CompanyFacts XBRL metrics ──────────────────
        # Only attempted if request budget allows. Fail-closed: any error here
        # does not downgrade the submissions success already achieved.
        # Raw companyfacts JSON is never persisted — only parsed observations.
        companyfacts_parse_result: Optional[CompanyFactsParseResult] = None
        if request_count < config.max_requests_per_ticker:
            cf_url = _COMPANYFACTS_URL_TEMPLATE.format(cik=cik_padded)
            request_count += 1  # count attempt before the call; preserved even on timeout/error
            try:
                resp3 = _get(cf_url)
                resp3.raise_for_status()
                cf_raw = resp3.json() or {}
                # Build the set of accession numbers with SourceRecords for linkage.
                source_accessions = frozenset(
                    f.accession_number for f in filings
                )
                companyfacts_parse_result = parse_companyfacts(cf_raw, source_accessions)
                logger.debug(
                    "sec_companyfacts_fetched ticker=%s parse_status=%s observations=%d",
                    ticker_upper,
                    companyfacts_parse_result.parse_status,
                    companyfacts_parse_result.observation_count,
                )
            except Exception as cf_exc:  # noqa: BLE001
                # Companyfacts failure does not downgrade the filings success.
                logger.warning(
                    "sec_companyfacts_fetch_failed ticker=%s error=%s — continuing with filings only",
                    ticker_upper, cf_exc,
                )
                companyfacts_parse_result = CompanyFactsParseResult(
                    parse_status="error",
                    error_message=f"companyfacts_fetch_error: {cf_exc}",
                )

        return SecEdgarProviderResult(
            ticker=ticker_upper,
            cik=cik_padded,
            filings=filings,
            fetch_status="success",
            fetched_at=fetched_at,
            request_count=request_count,
            companyfacts_parse_result=companyfacts_parse_result,
        )

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "sec_edgar_provider unexpected error ticker=%s error=%s",
            ticker_upper, exc,
        )
        return _fail_closed(ticker_upper, "error",
                            f"Unexpected provider error: {exc}", request_count)
    finally:
        if _own_client and http_client is not None:
            try:
                http_client.close()
            except Exception:  # noqa: BLE001
                pass
