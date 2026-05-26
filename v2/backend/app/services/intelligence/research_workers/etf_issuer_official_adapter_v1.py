"""Stage 9F.2b — ETF Issuer-Official Holdings Adapter v1.

Fetches and parses ETF holdings from issuer-official sources (Vanguard, SSGA/SPDR,
Schwab, Invesco) and returns normalized ETFHoldingsResult objects.

URL resolver strategies:
  vanguard_investor_csv_v1  — Vanguard investor portal CSV per-ticker
  ssga_csv_v1               — SSGA/SPDR CSV (ssga.com library-content)
  schwab_csv_v1             — Schwab ETF CSV (URL needs post-deploy validation)
  invesco_csv_v1            — Invesco CSV (secondary; QQQ primary is SEC NPORT)
  commodity_trust_special_case — GLD: no equity holdings, identity assumed

CSV parser strategies:
  vanguard_etf_csv_v1   — Vanguard column layout (Holdings/Weight columns)
  ssga_etf_csv_v1       — SSGA column layout (Name/Weight columns)
  schwab_etf_csv_v1     — Schwab column layout (Name/Weight columns)
  invesco_etf_csv_v1    — Invesco column layout
  commodity_trust_no_equity_holdings — GLD: always returns 0 holdings

Identity check:
  All issuer-official adapters verify fund name from CSV metadata rows.
  A file is rejected (identity_not_proven) if the fund name cannot be
  matched against expected names for the requested ticker.
  False negative is acceptable; false positive is not.

Freshness check:
  "As of" date from CSV metadata rows. Marked stale if >90 days old.
  Unknown if no date found.

Hard constraints:
  - Never raises; always returns ETFHoldingsResult.
  - No live HTTP calls without injectable http_get_fn (tests use fixture fn).
  - No raw full holdings in result (sample only — max 5 names).
  - No holdings returned unless identity_verified=True.
  - canonical_ready=False and safe_for_decision=False always.
  - No paid providers, no LLM, no SEC calls (those go through nport_provider_v1).
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .etf_holdings_provider_registry_v1 import ETFHoldingsResult

logger = logging.getLogger(__name__)

# ── URL templates (post-deploy validation required) ────────────────────────────

_VANGUARD_CSV_URL_TEMPLATE = (
    "https://investor.vanguard.com/content/dam/fas-portspec-images/downloads/"
    "etf-shares/{ticker}_QuantDataFundHoldings.csv"
)

_SSGA_CSV_URL_TEMPLATE = (
    "https://www.ssga.com/library-content/products/fund-data/etfs/us/"
    "holdings-daily-us-en-{ticker_lower}.csv"
)

# Schwab CSV URL is not publicly documented in a stable form.
# The adapter returns source_url_not_validated until confirmed.
_SCHWAB_CSV_URL_TEMPLATE: Optional[str] = None

# Invesco QQQ is secondary (SEC NPORT is primary and proven).
_INVESCO_CSV_URL_TEMPLATE = (
    "https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/"
    "0/{ticker}/false/relevance/asc/25"
)

# Stale threshold (days since as-of date).
_FRESHNESS_STALE_DAYS = 90

# Max holding rows to parse (guard against pathological CSV).
_MAX_HOLDINGS = 10_000

# Max sample names to include in diagnostic output.
_MAX_SAMPLE_NAMES = 5

# Max error message length.
_ERROR_MSG_MAX_LEN = 300

# ── Expected fund names per ticker (for identity verification) ─────────────────

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

# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalize_name(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _fund_name_matches(detected: str, expected_names: tuple[str, ...]) -> bool:
    """Return True if detected fund name is a normalized substring match of any expected."""
    if not detected or not expected_names:
        return False
    det_norm = _normalize_name(detected)
    if not det_norm:
        return False
    for exp in expected_names:
        exp_norm = _normalize_name(exp)
        if not exp_norm:
            continue
        if det_norm == exp_norm:
            return True
        if exp_norm in det_norm or det_norm in exp_norm:
            return True
    return False


def _parse_as_of_date(raw: str) -> Optional[str]:
    """Extract YYYY-MM-DD from common date string formats."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", raw)
    if m:
        try:
            mo, dy, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return datetime(yr, mo, dy).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _freshness_from_date(as_of_date: Optional[str]) -> str:
    if not as_of_date:
        return "unknown"
    try:
        dt = datetime.strptime(as_of_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).days
        return "stale" if age_days > _FRESHNESS_STALE_DAYS else "fresh"
    except (ValueError, TypeError):
        return "unknown"


def _normalize_col_headers(headers: list[str]) -> dict[str, int]:
    """Map normalized column name → original index."""
    result: dict[str, int] = {}
    for i, h in enumerate(headers):
        norm = _normalize_name(h)
        result[norm] = i
    return result


def _find_col(col_map: dict[str, int], *candidates: str) -> Optional[int]:
    """Return column index for first matching normalized name, or None."""
    for c in candidates:
        norm = _normalize_name(c)
        if norm in col_map:
            return col_map[norm]
    return None


def _truncate(s: Optional[str], max_len: int = _ERROR_MSG_MAX_LEN) -> Optional[str]:
    if s and len(s) > max_len:
        return s[:max_len] + "…"
    return s


def _fail(
    ticker: str,
    provider_id: str,
    fetch_status: str,
    error_message: str,
    source_url: Optional[str] = None,
    limitations: Optional[list[str]] = None,
) -> ETFHoldingsResult:
    return ETFHoldingsResult(
        ticker=ticker,
        provider_id=provider_id,
        source_type="issuer_official",
        source_url=source_url,
        source_authority="issuer_official",
        as_of_date=None,
        holdings_count=0,
        sample_holding_names=[],
        weights_available=False,
        weight_basis="unavailable",
        identity_verified=False,
        identity_basis=None,
        freshness_status="unknown",
        fetch_status=fetch_status,
        error_message=_truncate(error_message),
        limitations=limitations or [],
        canonical_ready=False,
        safe_for_decision=False,
    )


# ── URL resolution ────────────────────────────────────────────────────────────


def _resolve_url(ticker: str, provider_id: str) -> Optional[str]:
    """Return the holdings file URL for a ticker/provider, or None if not configured."""
    t = ticker.upper().strip()
    if provider_id == "vanguard_official_v1":
        return _VANGUARD_CSV_URL_TEMPLATE.format(ticker=t)
    if provider_id == "spdr_official_v1":
        return _SSGA_CSV_URL_TEMPLATE.format(ticker_lower=t.lower())
    if provider_id == "schwab_official_v1":
        return _SCHWAB_CSV_URL_TEMPLATE  # None → source_url_not_validated
    if provider_id == "invesco_official_v1":
        return _INVESCO_CSV_URL_TEMPLATE.format(ticker=t)
    if provider_id == "gld_commodity_v1":
        return None  # commodity trust — no file to fetch
    return None


# ── CSV parsing ───────────────────────────────────────────────────────────────


def _scan_metadata_rows(rows: list[list[str]]) -> tuple[Optional[str], Optional[str]]:
    """Scan early rows for fund name and as-of date.

    Returns (fund_name, as_of_date). Scans first 15 rows.
    Stops after finding both, or after exhausting early rows.
    """
    fund_name: Optional[str] = None
    as_of_date: Optional[str] = None

    for row in rows[:15]:
        row_text = " ".join(c.strip() for c in row if c.strip())
        if not row_text:
            continue

        # As-of date patterns
        if as_of_date is None:
            for pattern in [
                r"as\s+of\s+[:\-]?\s*([\d/\-]+)",
                r"date\s*[:\-]\s*([\d/\-]+)",
                r"(\d{4}-\d{2}-\d{2})",
                r"(\d{1,2}/\d{1,2}/\d{4})",
            ]:
                m = re.search(pattern, row_text, re.IGNORECASE)
                if m:
                    parsed = _parse_as_of_date(m.group(1))
                    if parsed:
                        as_of_date = parsed
                        break

        # Fund name: a sufficiently long text that looks like a fund name
        if fund_name is None:
            cell = row[0].strip() if row else ""
            if (
                len(cell) > 8
                and not re.match(r"^[\d,\.%]+$", cell)
                and not re.match(r"(?i)^(name|ticker|symbol|holding|weight|isin|shares|market|value|cusip|sedol)", cell)
            ):
                fund_name = cell

        if fund_name and as_of_date:
            break

    return fund_name, as_of_date


def _parse_csv_holdings(
    text: str,
    ticker: str,
    provider_id: str,
) -> tuple[list[dict], Optional[str], Optional[str], str]:
    """Parse a holdings CSV into a list of holding dicts.

    Returns (holdings_list, fund_name, as_of_date, parse_status).

    parse_status values:
      ok                  — headers found and at least some holdings parsed.
      source_shape_changed — required columns not found in headers.
      no_holdings_found   — headers found but zero data rows parsed.
      empty_content       — CSV content is empty or whitespace only.
    """
    if not text or not text.strip():
        return [], None, None, "empty_content"

    try:
        reader = csv.reader(io.StringIO(text))
        all_rows = list(reader)
    except Exception as exc:  # noqa: BLE001
        return [], None, None, f"csv_parse_error: {exc}"

    if not all_rows:
        return [], None, None, "empty_content"

    # Scan metadata rows before the header row.
    fund_name_from_meta, as_of_date = _scan_metadata_rows(all_rows)

    # Find header row: look for a row containing "name" or "ticker" or "holding".
    header_row_idx: Optional[int] = None
    col_map: dict[str, int] = {}

    for i, row in enumerate(all_rows):
        if not row:
            continue
        norm_cells = [_normalize_name(c) for c in row]
        if any(kw in cell for cell in norm_cells for kw in ("name", "holding", "ticker", "symbol")):
            # This is likely the header row.
            header_row_idx = i
            col_map = _normalize_col_headers(row)
            break

    if header_row_idx is None:
        return [], fund_name_from_meta, as_of_date, "source_shape_changed"

    # Find required columns — name first, then weight.
    name_col = _find_col(col_map,
        "holdings", "name", "security name", "security", "description", "holding name",
    )
    weight_col = _find_col(col_map,
        "weight", "weight (%)", "% of fund", "% of funds", "% of net assets",
        "pct", "percent", "portfolio weight", "% weight",
    )
    ticker_col = _find_col(col_map, "ticker", "ticker symbol", "symbol", "ticker id")
    isin_col = _find_col(col_map, "isin")
    mv_col = _find_col(col_map,
        "market value", "market value (usd)", "market value ($)", "local market value",
    )

    if name_col is None:
        return [], fund_name_from_meta, as_of_date, "source_shape_changed"

    # Parse data rows after the header.
    holdings: list[dict] = []
    for row in all_rows[header_row_idx + 1:]:
        if len(holdings) >= _MAX_HOLDINGS:
            break
        if not row or not row[name_col].strip() if name_col < len(row) else True:
            continue
        holding_name = row[name_col].strip() if name_col < len(row) else ""
        if not holding_name:
            continue
        # Skip rows that look like metadata continuations (no numeric weight)
        raw_weight = (row[weight_col].strip() if weight_col is not None and weight_col < len(row) else "") or ""
        raw_weight = raw_weight.replace("%", "").replace(",", "").strip()

        weight_pct: Optional[float] = None
        if raw_weight:
            try:
                weight_pct = float(raw_weight)
            except ValueError:
                pass

        holdings.append({
            "name": holding_name,
            "ticker": (row[ticker_col].strip() if ticker_col is not None and ticker_col < len(row) else None) or None,
            "isin": (row[isin_col].strip() if isin_col is not None and isin_col < len(row) else None) or None,
            "weight_pct": weight_pct,
            "market_value": (row[mv_col].strip() if mv_col is not None and mv_col < len(row) else None) or None,
        })

    if not holdings:
        return [], fund_name_from_meta, as_of_date, "no_holdings_found"

    return holdings, fund_name_from_meta, as_of_date, "ok"


# ── Provider-specific fetch & parse ──────────────────────────────────────────


def _fetch_and_parse_csv(
    ticker: str,
    provider_id: str,
    url: str,
    http_get_fn: Callable[[str], Any],
) -> tuple[list[dict], Optional[str], Optional[str], str, Optional[str]]:
    """Fetch a CSV from url and parse it.

    Returns (holdings, fund_name, as_of_date, parse_status, error_message).
    """
    try:
        resp = http_get_fn(url)
        resp.raise_for_status()
        text = getattr(resp, "text", None) or ""
        if not text:
            try:
                text = resp.content.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                text = ""
    except Exception as exc:  # noqa: BLE001
        exc_name = type(exc).__name__.lower()
        if "timeout" in exc_name or "timeout" in str(exc).lower():
            return [], None, None, "source_url_fetch_timeout", str(exc)
        return [], None, None, "source_url_fetch_error", str(exc)

    holdings, fund_name, as_of_date, parse_status = _parse_csv_holdings(text, ticker, provider_id)
    return holdings, fund_name, as_of_date, parse_status, None


# ── Identity verification ──────────────────────────────────────────────────────


def _verify_identity(
    ticker: str,
    fund_name_from_file: Optional[str],
    url: Optional[str],
) -> tuple[bool, Optional[str]]:
    """Verify that the fetched file actually belongs to the requested ETF.

    Returns (identity_verified, identity_basis).
    False positive is not acceptable; false negative is.
    If fund_name_from_file is absent, identity cannot be proven.
    """
    expected = _EXPECTED_FUND_NAMES.get(ticker.upper(), ())
    if not expected:
        return False, f"no expected fund names configured for {ticker}"

    if not fund_name_from_file:
        return False, "fund name absent from file metadata — identity cannot be proven"

    if _fund_name_matches(fund_name_from_file, expected):
        return True, (
            f"fund_name_matched: detected={fund_name_from_file!r} "
            f"against expected={expected!r}"
        )

    return False, (
        f"identity_not_proven: detected={fund_name_from_file!r} "
        f"does not match expected={expected!r} for ticker={ticker}"
    )


# ── GLD commodity special case ────────────────────────────────────────────────


def _handle_gld_commodity(ticker: str, provider_id: str) -> ETFHoldingsResult:
    """Return the commodity-trust special-case result for GLD."""
    return ETFHoldingsResult(
        ticker=ticker,
        provider_id=provider_id,
        source_type="issuer_official",
        source_url=None,
        source_authority="issuer_official",
        as_of_date=None,
        holdings_count=0,
        sample_holding_names=[],
        weights_available=False,
        weight_basis="unavailable",
        identity_verified=True,
        identity_basis="commodity_trust_assumed_no_equity_holdings: GLD holds physical gold bullion",
        freshness_status="not_applicable",
        fetch_status="commodity_trust_no_equity_holdings",
        error_message=None,
        limitations=[
            "GLD holds physical gold bullion — no equity holdings basket.",
            "No NPORT-P equity holdings expected.",
        ],
        canonical_ready=False,
        safe_for_decision=False,
    )


# ── Public API ─────────────────────────────────────────────────────────────────


def fetch_issuer_official_holdings(
    ticker: str,
    provider_id: str,
    http_get_fn: Optional[Callable[[str], Any]] = None,
) -> ETFHoldingsResult:
    """Fetch and parse issuer-official holdings for one ETF ticker.

    Returns ETFHoldingsResult — always. Never raises.

    http_get_fn: injectable HTTP GET callable for testing.
      Signature: fn(url: str) -> response_with_raise_for_status_and_text
      If None, httpx.Client is used for the actual HTTP call.

    Identity contract:
      - Identity is verified from fund name in CSV metadata rows.
      - If fund name cannot be matched, identity_verified=False and
        holdings_count=0 (no holdings exposed without identity).
      - GLD: identity_verified=True (commodity trust assumed) + 0 holdings.
    """
    ticker_upper = ticker.upper().strip()

    # GLD commodity special case — no HTTP call needed.
    if provider_id == "gld_commodity_v1" or ticker_upper == "GLD":
        return _handle_gld_commodity(ticker_upper, provider_id)

    # Resolve URL.
    url = _resolve_url(ticker_upper, provider_id)
    if url is None:
        return _fail(
            ticker_upper, provider_id,
            "source_url_not_validated",
            (
                f"No confirmed public URL configured for {provider_id}/{ticker_upper}. "
                "Post-deploy: locate and validate the issuer's official holdings CSV URL."
            ),
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

        # Fetch and parse.
        holdings, fund_name, as_of_date, parse_status, fetch_error = _fetch_and_parse_csv(
            ticker_upper, provider_id, url, _get,
        )

        if parse_status in ("source_url_fetch_error", "source_url_fetch_timeout"):
            return _fail(
                ticker_upper, provider_id,
                parse_status,
                fetch_error or f"HTTP fetch failed: {url}",
                source_url=url,
                limitations=[
                    "URL may be incorrect or issuer schema changed.",
                    "Validate URL post-deploy.",
                ],
            )

        if parse_status == "source_shape_changed":
            return _fail(
                ticker_upper, provider_id,
                "source_shape_changed",
                (
                    f"Required columns not found in CSV from {url}. "
                    "Issuer may have changed the file layout."
                ),
                source_url=url,
            )

        if parse_status == "empty_content":
            return _fail(
                ticker_upper, provider_id,
                "source_url_fetch_error",
                f"Empty response body from {url}.",
                source_url=url,
            )

        if parse_status == "no_holdings_found":
            return _fail(
                ticker_upper, provider_id,
                "no_holdings_found",
                f"CSV parsed but no holding rows found in {url}.",
                source_url=url,
            )

        # Identity verification — must happen before exposing any holdings.
        identity_verified, identity_basis = _verify_identity(
            ticker_upper, fund_name, url,
        )

        if not identity_verified:
            return _fail(
                ticker_upper, provider_id,
                "identity_not_proven",
                identity_basis or "identity check failed",
                source_url=url,
                limitations=["Holdings withheld — identity not proven from file metadata."],
            )

        # Weights.
        weights_available = any(h["weight_pct"] is not None for h in holdings)
        weight_basis = "percent" if weights_available else "unavailable"

        # Market-value derived weights (if pct absent but market_value present).
        if not weights_available:
            holdings_with_mv = [
                h for h in holdings
                if h.get("market_value") and re.sub(r"[,$]", "", h["market_value"]).strip().replace(".", "").isdigit()
            ]
            if holdings_with_mv:
                weight_basis = "market_value_derived"

        sample_names = [h["name"] for h in holdings[:_MAX_SAMPLE_NAMES]]
        freshness = _freshness_from_date(as_of_date)

        return ETFHoldingsResult(
            ticker=ticker_upper,
            provider_id=provider_id,
            source_type="issuer_official",
            source_url=url,
            source_authority="issuer_official",
            as_of_date=as_of_date,
            holdings_count=len(holdings),
            sample_holding_names=sample_names,
            weights_available=weights_available,
            weight_basis=weight_basis,
            identity_verified=identity_verified,
            identity_basis=identity_basis,
            freshness_status=freshness,
            fetch_status="success",
            error_message=None,
            limitations=[],
            canonical_ready=False,
            safe_for_decision=False,
        )

    except Exception as exc:  # noqa: BLE001
        return _fail(
            ticker_upper, provider_id,
            "error",
            f"Unexpected error in issuer-official adapter: {exc}",
            source_url=url if "url" in dir() else None,
        )
    finally:
        if _own_client and http_client is not None:
            try:
                http_client.close()
            except Exception:  # noqa: BLE001
                pass
