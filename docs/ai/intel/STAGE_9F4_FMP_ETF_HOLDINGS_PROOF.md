# Stage 9F.4 — FMP ETF Holdings Free-Key Entitlement Proof Gate

**Decision artifact — diagnostic proof endpoint only. No canonical adapter built.**

## Purpose

Stage 9F.4 tests whether the FMP (Financial Modeling Prep) free API key can return
ETF holdings data with:
- Per-holding weights (required for exposure analysis)
- Provider as-of/date metadata (required for canonical readiness)
- Sufficient coverage depth for the four proof tickers

The result determines whether FMP is a viable candidate for a future canonical ETF
holdings adapter. This is a **proof gate only** — no canonical adapter is built here.

Alpha Vantage (Stage 9F.3c) was ruled out as supplemental-only due to missing as-of
dates and incomplete VXUS coverage. FMP is the next candidate.

---

## Endpoint

```
POST /api/v1/diagnostics/finance-intel/fmp-etf-holdings-check
```

**Authentication:** Requires `FINANCE_RUNTIME_CERT_ENABLED=true` and the
`X-Finance-Runtime-Cert-Secret` header.

**Request body:**
```json
{"tickers": ["VOO"]}
```

**Required env vars:**
- `FMP_API_KEY=<your free key>` — fails closed if unset (403).
- `FINANCE_RUNTIME_CERT_ENABLED=true` and `FINANCE_RUNTIME_CERT_SECRET=<secret>`.

---

## How to run the proof sequence

Run one ticker at a time to conserve free API quota:

```bash
# Step 1
curl -X POST https://<railway-host>/api/v1/diagnostics/finance-intel/fmp-etf-holdings-check \
  -H "Content-Type: application/json" \
  -H "X-Finance-Runtime-Cert-Secret: <FINANCE_RUNTIME_CERT_SECRET>" \
  -d '{"tickers": ["VOO"]}'

# Step 2
curl ... -d '{"tickers": ["SCHD"]}'

# Step 3
curl ... -d '{"tickers": ["VXUS"]}'

# Step 4
curl ... -d '{"tickers": ["XLE"]}'
```

All four together (4 API calls):
```bash
curl ... -d '{"tickers": ["VOO", "SCHD", "VXUS", "XLE"]}'
```

The endpoint caps at 10 tickers per request (free quota guard).

---

## What to look for in each ticker's result

| Field | What to check |
|---|---|
| `fetch_status` | Must be `success` for holdings to be usable |
| `holdings_count` | See coverage sanity rules below |
| `weights_available` | Must be `true` for usable holdings |
| `as_of_date_or_date_field` | Must be non-null for canonical readiness |
| `freshness_status` | Must be `verified` (not `date_missing`) |
| `coverage_quality` | `plausible_full` is the minimum for candidate_pass |
| `limitations` | Lists specific gaps (missing date, missing weights) |

---

## Coverage sanity rules

| Ticker | plausible_full threshold | Notes |
|---|---|---|
| VOO | ≥ 200 | S&P 500 — should have hundreds |
| SCHD | ≥ 50 | Dividend screen — ~100 holdings |
| XLE | ≥ 20 | Sector ETF — dozens |
| VXUS | ≥ 1000 | Total international — **thousands**; low count (< 200) is partial_or_suspicious |

**Critical:** Low VXUS count (low double-digits or low hundreds) indicates top-holdings
mode, not full canonical coverage. This is the same gap that blocked Alpha Vantage.

---

## Expected verdict semantics

| Verdict | Condition |
|---|---|
| `candidate_pass` | All 4 proof tickers (VOO, SCHD, VXUS, XLE) return `plausible_full` holdings + weights + verified as-of date |
| `candidate_partial` | Some holdings/weights returned but: date missing for some tickers, VXUS coverage weak, or not all 4 tickers succeed |
| `candidate_fail` | Paywalled (403), unauthorized (401), rate-limited (429), or no usable holdings across all tickers |

**Blocking rules:**
- Missing as-of date (`freshness_status=date_missing`) blocks `candidate_pass` — same as AV.
- Missing weights blocks usable holdings classification.
- VXUS `partial_or_suspicious` coverage blocks `candidate_pass` even if weights + date are present.

---

## Non-goals

- This endpoint does NOT write any artifacts, change any decisions, or affect any
  Intel v3 snapshots.
- This endpoint does NOT build a canonical FMP adapter.
- This endpoint does NOT affect Deploy, Watchtower, or synthesis behavior.
- The FMP API key is never logged or returned in any field value.

---

## Safety / governance guarantees

Every response always includes:

```json
{
  "diagnostics_only": true,
  "canonical_ready": false,
  "safe_for_decision": false,
  "artifact_writes": 0,
  "decision_policy_changed": false,
  "synthesis_ready_changed": false,
  "visible_snapshot_unchanged": true
}
```

These fields are immutable — they cannot be changed by any future patch without
opening a new capability slice and explicit product approval.

---

## What evidence is needed before a canonical FMP adapter is considered

A canonical FMP adapter requires ALL of the following before being considered:

1. **candidate_pass verdict** from this proof endpoint (all 4 tickers plausible_full + weights + date).
2. **VXUS full coverage confirmed** — thousands of holdings, not hundreds.
3. **Provider as-of date field confirmed** — `freshness_status=verified` for all 4 tickers.
4. **Cost model approved** — if full holdings require a paid FMP plan, cost must be justified.
5. **Separate capability slice opened** — a new PR, new product gate, new contract.

Do not build a canonical adapter based on `candidate_partial` results. If only VOO/SCHD/XLE
pass but VXUS is partial, the same gap as Alpha Vantage exists for broad international ETFs.

---

## Fetch status classification

| Status | Cause |
|---|---|
| `success` | 200 OK with holdings array |
| `paywalled` | HTTP 403, or 200 with plan/subscription error message |
| `unauthorized` | HTTP 401, or 200 with invalid API key message |
| `rate_limited` | HTTP 429, or 200 with rate-limit message |
| `no_data` | 200 OK but empty holdings array |
| `malformed` | 200 OK but response body is not valid JSON |
| `error` | Network error or unexpected HTTP status |

---

## FMP endpoint used

```
GET https://financialmodelingprep.com/stable/etf/holdings?symbol={TICKER}&apikey={FMP_API_KEY}
```

Handles both response formats:
- **Array format:** `[{asset, name, weightPercentage, sharesNumber, ...}, ...]`
- **Object format:** `{symbol, date, holdings: [{...}, ...]}`
