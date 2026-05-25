"""Stage 9F.2a — Live SEC EDGAR NPORT-P validation script.

GATED / MANUAL USE ONLY.  This script makes real HTTP requests to SEC EDGAR.
Do NOT run in CI — use it locally or in a Railway staging shell to diagnose
CIK resolution and filing parse issues before enabling the production flag.

Usage:
    # From project root (venv active):
    cd v2/backend
    SEC_EDGAR_USER_AGENT="MyApp myemail@example.com" python scripts/validation/nport_live_check.py

    # Test specific tickers:
    SEC_EDGAR_USER_AGENT="MyApp me@example.com" python scripts/validation/nport_live_check.py SPY VOO QQQ

Success criteria:
    - At least one ticker shows: status=success holdings_count=N (N > 0)
    - No ticker shows sec_error (indicates wrong CIK in seed map)
    - Tickers showing no_nport_filing need parent-registrant CIK update

What to do with results:
    no_nport_filing  → CIK resolves but entity doesn't file NPORT-P. The entity
                       is likely an individual share-class CIK rather than the
                       parent registrant. Use the `resolved_cik` in the diagnostic
                       to search EDGAR for the correct registrant:
                       https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<resolved_cik>&type=NPORT-P
                       The parent registrant (e.g. "VANGUARD INDEX FUNDS") will
                       have NPORT-P filings listed.  Update _ETF_CIK_SEED_MAP with
                       the parent registrant CIK.

    sec_error        → HTTP error fetching submissions.  Likely wrong CIK (404).
                       Check the CIK with:
                       https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<cik>&owner=include&count=10

    filing_not_parseable → Filing found and document fetched, but XML parse failed.
                           Check primary_doc_attempted in the diagnostic output.
                           If it ends in .txt, the SGML extraction may have failed
                           on an unusual format.  Inspect the raw document manually.

    no_holdings_found → XML parsed, invstOrSecs found, but zero invstOrSec children.
                        The filing might be for a commodity trust or cash-only fund.

Log keys to watch in production Railway logs (when flag enabled):
    sec_nport_etf_holdings_start        — lane started for ticker
    sec_nport_etf_holdings_written      — artifact written (holdings_count > 0)
    sec_nport_etf_holdings_no_data_artifact — thin artifact written (no holdings)
    sec_nport_etf_holdings_skip         — transient error, write skipped
    sec_nport_etf_holdings_complete     — lane finished
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

# Allow running from scripts/validation/ or from project root.
_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "v2", "backend")
if os.path.isdir(_BACKEND):
    sys.path.insert(0, os.path.abspath(_BACKEND))

DEFAULT_TICKERS = ["SPY", "VOO", "VTI", "QQQ", "VGT", "VHT", "VIS",
                   "XLE", "VXUS", "SCHD", "VYM", "GLD"]

_DELAY_BETWEEN_REQUESTS = 1.5   # seconds — respect SEC EDGAR rate limit


def run_check(tickers: list[str], user_agent: str) -> None:
    from app.services.intelligence.research_workers.nport_provider_v1 import (
        NportProviderConfig,
        fetch_etf_nport_holdings,
    )

    cfg = NportProviderConfig(user_agent=user_agent, timeout_seconds=20.0)
    print(f"\nSEC EDGAR NPORT-P Live Check — {datetime.now(timezone.utc).isoformat()}")
    print(f"User-Agent: {user_agent}")
    print(f"Tickers: {tickers}\n")
    print(f"{'TICKER':<8} {'STATUS':<32} {'CIK':<12} {'HOLDINGS':>8}  {'PRIMARY_DOC / DETAIL'}")
    print("-" * 100)

    successes = 0
    failures: list[str] = []

    for ticker in tickers:
        result = fetch_etf_nport_holdings(ticker, cfg)
        holdings_n = len(result.holdings)
        cik_display = (result.cik or "?")[:12]
        doc_display = ""
        if result.primary_doc_attempted:
            doc_display = result.primary_doc_attempted[:40]
        elif result.error_message:
            doc_display = result.error_message[:60]

        flag = "✓" if result.is_success else "✗"
        print(
            f"{flag} {ticker:<6} {result.fetch_status:<32} {cik_display:<12} "
            f"{holdings_n:>8}  {doc_display}"
        )
        if result.is_success:
            successes += 1
            # Print top 3 holdings.
            for h in result.holdings[:3]:
                print(f"          → {h.name:<40} pct={h.weight_pct}  cusip={h.cusip}")
        else:
            failures.append(f"{ticker}: {result.fetch_status} — {result.error_message or ''}")

        time.sleep(_DELAY_BETWEEN_REQUESTS)

    print("-" * 100)
    print(f"\nSummary: {successes}/{len(tickers)} tickers produced holdings.")
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  {f}")

    print("\nNext steps:")
    print("  • sec_error        → wrong CIK. Find correct CIK via EDGAR company search.")
    print("  • no_nport_filing  → series CIK, not parent registrant. Use parent CIK.")
    print("  • filing_not_parseable → check primary_doc; may need SGML or index fix.")
    print("  • no_holdings_found  → XML parsed but empty. Genuine no-holdings filing.")


if __name__ == "__main__":
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if not ua:
        print("ERROR: Set SEC_EDGAR_USER_AGENT env var before running.")
        print('  export SEC_EDGAR_USER_AGENT="MyApp myemail@example.com"')
        sys.exit(1)

    tickers = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_TICKERS
    run_check(tickers, ua)
