# Stage 9F.3 — Alpha Vantage ETF_PROFILE Proof Gate

**Decision artifact — diagnostic proof endpoint only. No canonical adapter built.**

## Stage 9F.3c — Live proof findings and provider decision (final)

### Observed live results

| Ticker | holdings_count | weights | as-of date | freshness_status | coverage_quality |
|---|---|---|---|---|---|
| XLE | 24 | ✓ | absent | date_missing | usable_supplemental |
| VOO | 519 | ✓ | absent | date_missing | usable_supplemental |
| SCHD | 103 | ✓ | absent | date_missing | usable_supplemental |
| VXUS | 37 | ✓ | absent | date_missing | partial_or_suspicious |

VOO, VTI, VGT, VHT, VIS, VYM, SCHD required a premium/paid AV plan to unblock
(free tier returned `entitlement_or_premium_required` for Vanguard tickers on first run).

### Provider decision record

**Alpha Vantage ETF_PROFILE is accepted ONLY as non-canonical supplemental ETF
exposure evidence.** It is NOT accepted for canonical S-grade ETF holdings truth.

**Canonical rejection criteria (all apply):**

1. **No as-of/date field** — every observed response is missing the as-of date.
   `fetched_at` is not a valid provider date. Canonical use requires a verifiable
   as-of date from the provider; none was present.

2. **Partial/incomplete coverage for broad ETFs** — VXUS (Vanguard Total
   International Stock ETF) holds thousands of positions but returned only 37.
   This is top-holdings-like partial coverage, not full canonical holdings.

3. **fund_name null** — several responses returned no fund name, indicating
   incomplete schema coverage.

**Acceptance as supplemental exposure evidence (non-canonical):**

- Alpha Vantage can provide holdings + weights for focused ETFs (XLE: 24, SCHD: 103)
  and large-cap domestic ETFs (VOO: 519) as supplemental exposure diagnostic evidence.
- This evidence is useful for non-canonical portfolio ETF exposure analysis, NOT for
  driving visible Buy/Hold/Trim/Sell decisions or synthesis.
- Classification: `usable_supplemental` (focused ETF with plausible count) or
  `partial_or_suspicious` (broad international fund with suspiciously low count).

**Next provider decision:**

- Use Alpha Vantage only for supplemental exposure diagnostics (non-canonical).
- If canonical S-grade ETF holdings are required (full holdings list + verified
  as-of date for all ETF types), evaluate a paid/full ETF holdings provider
  separately. Candidates from Stage 9F.2c: Intrinio, FMP paid tier, ETF Global /
  Massive Financial. Do not build a canonical AV adapter.

---

## First run result (Stage 9F.3a — post-deploy run)

Run sent 9 tickers. Result: `candidate_partial`.

| Ticker | Outcome |
|---|---|
| XLE | `success` — 24 holdings + weights. No as-of date field → `freshness_status=date_missing`. |
| VOO, VTI, VGT, VHT, VIS, VXUS, VYM | `provider_note` / `Information` response — 0 holdings. |
| SCHD | `provider_note` / `Information` response — 0 holdings. |

The `Information` responses returned 0 holdings, but the **actual provider message text was not
captured in the first run** (snippet field was absent). Without the message, we cannot determine
whether the cause was:
- **Quota/rate limit** — 25 req/day free tier exhausted (9 tickers × 1 run = 9 req; 25/day limit
  may have been hit if the key was used earlier in the day).
- **Entitlement** — `ETF_PROFILE` may require a premium Alpha Vantage plan for Vanguard symbols.
- **Coverage gap** — The function may simply not support those tickers.

**Stage 9F.3b fixes this**: the diagnostic now returns a `provider_message_snippet` per ticker
(capped 200 chars, API key redacted) and sub-classifies `Information` responses as:
- `rate_limited` — if the text mentions daily/per-minute limits or standard API frequency.
- `entitlement_or_premium_required` — if the text mentions premium, subscription, or entitlement.
- `provider_note` — only when neither can be determined.

## Next operator run instructions (after Stage 9F.3b is deployed)

**Run a single ticker first** after quota resets (midnight UTC for the free tier):

```bash
curl -X POST https://<your-railway-host>/api/v1/diagnostics/finance-intel/alpha-vantage-etf-profile-check \
  -H "Content-Type: application/json" \
  -H "X-Finance-Runtime-Cert-Secret: <FINANCE_RUNTIME_CERT_SECRET>" \
  -d '{"tickers": ["VOO"]}'
```

Inspect `per_ticker[0].fetch_status` and `per_ticker[0].provider_message_snippet`:
- `rate_limited` + snippet mentions daily/per-minute limit → wait for quota reset, retry.
- `entitlement_or_premium_required` → AV free tier does not support ETF_PROFILE for Vanguard; evaluate paid plan or alternative provider.
- `provider_note` (no matching keywords) → read snippet for new clues; may need manual AV support contact.
- `success` with 0 holdings → truly no data for this ticker.

**Do not re-run the full 9-ticker default** until the per-ticker reason is confirmed — each run
consumes 9 of the 25 daily free requests.

After identifying the root cause for VOO, test SCHD separately:
```bash
  -d '{"tickers": ["SCHD"]}'
```

## What this endpoint tests

`POST /api/v1/diagnostics/finance-intel/alpha-vantage-etf-profile-check`

Tests whether the Alpha Vantage `ETF_PROFILE` function can provide S-grade ETF
holdings data (holdings list + per-holding weights + as-of date) for the ETF
tickers that are currently uncovered by the SEC NPORT path.

The endpoint makes one `GET https://www.alphavantage.co/query?function=ETF_PROFILE&symbol={TICKER}&apikey=...`
call per ticker and returns a normalized diagnostic shape with per-ticker and
aggregate pass/fail signals.

This is a **provider proof gate** — not a canonical adapter. The result informs
whether to invest in building a permanent Alpha Vantage ETF holdings adapter.

## Exact missing ticker set

The following tickers are uncovered after Stage 9F.2b (SEC NPORT + issuer-official
both failed):

| Ticker | Issuer | Reason uncovered |
|---|---|---|
| XLE | SSGA/SPDR | SEC NPORT: series_identity_not_proven; issuer URL: 404 |
| VOO | Vanguard | SEC NPORT: series_identity_not_proven; issuer URL: 404 |
| VTI | Vanguard | SEC NPORT: series_identity_not_proven; issuer URL: 404 |
| VGT | Vanguard | SEC NPORT: series_identity_not_proven; issuer URL: 404 |
| VHT | Vanguard | SEC NPORT: series_identity_not_proven; issuer URL: 404 |
| VIS | Vanguard | SEC NPORT: series_identity_not_proven; issuer URL: 404 |
| VXUS | Vanguard | SEC NPORT: series_identity_not_proven; issuer URL: 404 |
| VYM | Vanguard | SEC NPORT: series_identity_not_proven; issuer URL: 404 |
| SCHD | Schwab | No confirmed public CSV URL; SEC NPORT: not attempted |

SPY and QQQ are already covered via SEC NPORT (identity_verified, holdings > 0).
GLD is correctly classified as `commodity_trust_no_equity_holdings`.

## Pass/fail criteria

| Verdict | Condition |
|---|---|
| `candidate_pass` | XLE + SCHD both return holdings\_count > 0 with per-holding weights AND as-of date, AND ≥ 5 of 7 Vanguard ETFs (VOO/VTI/VGT/VHT/VIS/VXUS/VYM) return holdings\_count > 0 with per-holding weights |
| `candidate_partial` | Some holdings/weights returned but pass criteria not met: coverage incomplete, top-holdings-only, date missing, or schema weak |
| `candidate_fail` | Entitlement/rate-limit/no-data/malformed responses dominate — provider not viable |

Additional blocking rule: **missing as-of date prevents canonical readiness** even
if holdings and weights are present. A `candidate_pass` requires `freshness_status=verified`
for all holdings-bearing tickers.

## Not canonical — not decision-safe

This endpoint is **diagnostic-only**:

- `canonical_ready = false` always.
- `safe_for_decision = false` always.
- `diagnostics_only = true` always.
- `artifact_writes = 0` always.
- `decision_policy_changed = false` always.
- `synthesis_ready_changed = false` always.
- `visible_snapshot_unchanged = true` always.

**Do not interpret any result as authorizing a canonical adapter without a separate
product decision gate.** The result informs whether the provider can supply the
required data shape — it does not automatically wire the provider into any decision
or evidence lane.

## Warning: free quota

The free Alpha Vantage tier is capped at **25 requests per day** (and 5 per minute).
The default run sends 9 requests (one per missing ticker). With `include_controls=true`
it sends 11 requests.

**Do not run more than once per day on the free tier unless you have confirmed you
are on a higher quota plan.** Repeated runs risk hitting the daily limit and getting
`rate_limited` / `provider_note` responses that obscure the actual entitlement result.

## Post-deploy run instructions

**Recommended: single-ticker first** (1 request, preserves quota):

```bash
curl -X POST https://<your-railway-host>/api/v1/diagnostics/finance-intel/alpha-vantage-etf-profile-check \
  -H "Content-Type: application/json" \
  -H "X-Finance-Runtime-Cert-Secret: <FINANCE_RUNTIME_CERT_SECRET>" \
  -d '{"tickers": ["VOO"]}'
```

Full missing set (9 requests — use only once reason for Information responses is confirmed):

```bash
curl -X POST https://<your-railway-host>/api/v1/diagnostics/finance-intel/alpha-vantage-etf-profile-check \
  -H "Content-Type: application/json" \
  -H "X-Finance-Runtime-Cert-Secret: <FINANCE_RUNTIME_CERT_SECRET>" \
  -d '{}'
```

With control tickers (11 requests — uses most of free daily quota):

```bash
curl -X POST https://<your-railway-host>/api/v1/diagnostics/finance-intel/alpha-vantage-etf-profile-check \
  -H "Content-Type: application/json" \
  -H "X-Finance-Runtime-Cert-Secret: <FINANCE_RUNTIME_CERT_SECRET>" \
  -d '{"include_controls": true}'
```

## Interpreting the result

- `candidate_pass` → Alpha Vantage provides full S-grade coverage for the missing
  set. Proceed to build a permanent `alpha_vantage_etf_holdings_adapter_v1` in a
  new capability slice.
- `candidate_partial` → Partial coverage. Identify which tickers and which fields
  (weights/date) are missing. May still be worth building an adapter with honest
  limitations annotations.
- `candidate_fail` → Provider not viable for this use case. Evaluate Intrinio,
  FMP paid tier, or ETF Global/Massive (see Stage 9F.2c findings).
