# Stage 9F.2c — ETF Issuer-Official Source Discovery Findings

**Decision artifact. No new code shipped. This document is the PR deliverable.**

## Finding summary

All known free issuer-official CSV/download URLs for the 12-ticker ETF universe are
blocked or missing. The issuer-official provider path cannot be certified without a paid
provider or an issuer-supplied API key. Recommendation: stop free-source ETF holdings
work and evaluate paid provider alternatives.

## URLs attempted per issuer / ticker

| Issuer | Tickers | URL template attempted | Runtime result |
|---|---|---|---|
| Vanguard | VOO, VTI, VGT, VHT, VIS, VXUS, VYM | `investor.vanguard.com/content/dam/fas-portspec-images/downloads/etf-shares/{TICKER}_QuantDataFundHoldings.csv` | HTTP 404 (Stage 9F.2b live run) |
| SSGA/SPDR | XLE, SPY | `ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{ticker_lower}.csv` | HTTP 404 for XLE (Stage 9F.2b live run); SPY not needed (covered by SEC NPORT) |
| Schwab | SCHD | None configured | No confirmed stable public CSV URL found during research |
| Invesco | QQQ | `invesco.com` holdings page | QQQ is already covered by SEC NPORT; Invesco URL not prioritised |
| GLD (SPDR Gold Trust) | GLD | N/A | Correctly handled as `commodity_trust_no_equity_holdings`; no equity holdings expected |

Additional discovery probes run from the CI environment (Stage 9F.2c spike):

| Domain | Response |
|---|---|
| `investor.vanguard.com` | HTTP 403 Forbidden |
| `ssga.com` | HTTP 403 Forbidden |
| `schwab.com` | HTTP 403 Forbidden |
| `invesco.com` | HTTP 403 Forbidden |
| `ishares.com` | HTTP 403 Forbidden |

All major ETF issuer domains block programmatic access from cloud/CI environments.
Even where a public download URL exists, the issuer enforces bot detection or
requires a browser session.

## Why each path is blocked

**Vanguard (VOO/VTI/VGT/VHT/VIS/VXUS/VYM):**
The old CSV template URL path (`/content/dam/fas-portspec-images/...`) no longer serves
holdings data (404). Vanguard does not publish a stable machine-readable ETF holdings
endpoint for programmatic use without a registered data agreement.

**SSGA/SPDR (XLE):**
The `ssga.com/library-content/...` CSV path returns 404. SSGA requires institutional
data access agreements for bulk holdings feeds.

**Schwab (SCHD):**
No confirmed stable public URL for SCHD holdings CSV. Schwab's ETF holdings are visible
via their website but no direct download URL is documented or stable.

**Invesco (QQQ):**
QQQ is already certified via SEC NPORT (identity verified, holdings count > 0). The
Invesco issuer-official path is deprioritised; its URL returns HTML, not a CSV.

## Current ETF holdings coverage (Stage 9F.2b state)

| Ticker | Source | Status |
|---|---|---|
| SPY | SEC NPORT (sec_nport_v1) | identity_verified, holdings > 0 |
| QQQ | SEC NPORT (sec_nport_v1) | identity_verified, holdings > 0 |
| XLE | SEC NPORT (sec_nport_v1) | identity_verified, holdings > 0 |
| GLD | commodity_trust (gld_commodity_v1) | commodity_trust_no_equity_holdings |
| VOO, VTI, VGT, VHT, VIS, VXUS, VYM | None | sec_nport: series_identity_not_proven or scan budget exhausted |
| SCHD | None | issuer_official_adapter: no_data; SEC NPORT: not attempted for Schwab |

`issuer_official_selected_count = 0` from Stage 9F.2b runtime run.

## Recommendation

Stop free-source ETF issuer-official holdings work. The keyless path (SEC NPORT-P) works
for standalone-trust ETFs (SPY, QQQ, XLE). For series-registrant ETFs (all Vanguard funds,
SCHD), the parent-registrant NPORT filing scan requires further tuning or cannot be
resolved without a paid provider.

Candidate paid providers to evaluate (from Stage 9F.1 research):

- **Intrinio** (~$18k/yr): full ETF holdings + weights + exposure.
- **FMP** (paid tier, ~$22–99/mo): ETF holdings endpoint; free-tier entitlement unverified.
- **ETF Global / Massive**: full ETF constituents + profiles; pricing unverified for
  personal-use tier.

Next step: provider decision checkpoint spike (key-based verification) before any
further implementation. Do not build another issuer-scraping layer without a confirmed
source that passes: HTTP 200 + identity + as-of date + holdings rows + percent weights
from an issuer-official domain.

## Governance invariants (unchanged)

- `canonical_ready = False` for all ETF tickers.
- `safe_for_decision = False` for all ETF tickers.
- SEC NPORT identity gate intact. GLD commodity path unchanged.
- No artifact writes. No decision, synthesis, or snapshot behavior changes.
