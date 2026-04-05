# ⚡ Portfolio War Room — Project Progress Log
**Last Updated:** April 5, 2026
**User:** prashanthkrishnan91
**Repo:** github.com/prashanthkrishnan91/my-portfolio-ai
**Current Version:** v13 ✅

---

## Project Overview

A full-stack personal portfolio intelligence system for tracking, analyzing, and optimizing a Robinhood investment account. Designed for an amateur investor — plain-English recommendations, tax-aware guidance, live prices, and a biweekly $900 deployment plan.

Evolved across many sessions: React artifact → Python/Streamlit → single-file production app → fully modular two-file architecture → real-time Plaid/Finnhub backend → Smart Sync (24h Plaid cache) → cash-informed rebalancing + 3-layer deduplication + decision log → high-integrity SHA-256 hashing engine with canonical fingerprints → bootstrap/live mode isolation + sidebar persistence → permanent sidebar fix + DRIP Analytics tab.

**Current file count: 10 Python files + 1 requirements.txt + system_state.json (runtime)**

---

## Architecture at a Glance (v13)

```
App.py                   Streamlit UI — zero business logic (~1,340 lines)
drip_analytics.py        DRIP Analytics module — extract, clean, project, render (310 lines)
data_engine.py           All processing, recs, deposit planning, price fetching (~1,260 lines)
price_service.py         Real-time Finnhub/Polygon/CoinGecko pricing engine (401 lines)
holdings_manager.py      Plaid 24h smart cache — HoldingsManager class (371 lines)
portfolio_aggregator.py  qty × price math, PortfolioSnapshot builder (321 lines)
plaid_client.py          Thin Plaid API wrapper (216 lines)
main_sync.py             CLI runner — Smart Sync loop, JSON export (378 lines)
requirements.txt         All dependencies
```

**Runtime files (auto-created, excluded from git):**
```
tx_store.json            All transactions (SHA-256 canonical fingerprints)
system_state.json        Mode: "bootstrap" | "live" — controls data origin
holdings_cache.json      Plaid holdings cache (TTL = 24h)
plaid_snapshot.json      Full portfolio snapshot from last Plaid sync
crypto_overrides.json    BTC/XRP from PDF import
rec_history.json         Saved portfolio snapshots (max 200)
deposit_log.json         Logged $900 deposits
decision_log.json        Manual override decisions (max 500)
targets.json             User target allocations
price_cache.json         Last-known prices (offline fallback)
recon_log.json           Rolling CSV ingest audit log (max 100)
```

---

## Session Summary — April 5, 2026 (v11.4 → v12)

### What was asked
Two production bugs reported for v11.4:

1. **Duplicate holdings** after CSV or PDF import — bootstrap summary data was not being cleared when real transaction data arrived, causing `recompute_portfolio()` to replay both synthetic bootstrap rows and real CSV rows, doubling every holding.
2. **Sidebar disappears** — once Streamlit collapsed the sidebar, there was no reliable way to reopen it.

### What was diagnosed
- `_bootstrap()` writes synthetic summary rows into `tx_store.json`. These rows look like real transactions and `recompute_portfolio()` can't distinguish them from CSV-imported rows. The dedup engine was correctly blocking re-import of matching rows, but the bootstrap rows themselves stayed in `tx_store` and contributed to the computed portfolio alongside the new CSV data.
- The sidebar had no persistent state variable — a Streamlit rerun would restore the default expanded state, but browser-side collapse events were not tracked, so a collapsed sidebar had no programmatic escape hatch.

### What was built (v12)

**data_engine.py** — 3 new additions, 1 modified:

| Change | Detail |
|--------|--------|
| `SYSTEM_STATE_PATH` | New path constant for `system_state.json` |
| `get_system_mode() → str` | Reads `"bootstrap"` (default) or `"live"` |
| `transition_to_live()` | Wipes `tx_store.json` + writes `{mode:"live"}`. Called once, ever. |
| `IngestStats.mode_transitioned` | New bool field; True when transition fired during this upload |
| `ingest_csv()` preamble | If mode == bootstrap: transition_to_live(), clear existing_ids in-place |

**App.py** — 5 targeted edits:

| Change | Detail |
|--------|--------|
| `sidebar_open: True` in `_init()` | Session state entry persisting sidebar visibility across reruns |
| Sidebar CSS injection block | `section[data-testid='stSidebar']{display:none}` when `sidebar_open=False` |
| Global CSS patch | `[data-testid="collapsedControl"]` forced `display:flex !important` — native hamburger always reachable |
| `◀ Hide / ▶ Show` button | Top-right of header, `[8,1]` column split — accessible even when sidebar is fully hidden |
| System mode badge in sidebar | `⚠️ BOOTSTRAP MODE` (amber) or `✅ LIVE MODE` (green) |
| CSV upload path | Resets `processed_ids = new_ids` when `stats.mode_transitioned` |
| PDF upload path | Calls `get_system_mode()` / `transition_to_live()` before `parse_crypto_pdf()` |

### What was clarified (not built)
User also asked to "update the app to fix sidebar visibility and improve UI using React/Tailwind + shadcn/ui". This repo is a Streamlit Python app — no React/JSX files exist. The sidebar fix described above already resolves the underlying issue in the actual stack. A React frontend would require a separate project scaffold.

### Files changed
- `data_engine.py` — +47 lines (new functions + IngestStats field + ingest_csv preamble)
- `App.py` — +73 lines, -7 lines (sidebar state, toggle button, CSS, import wiring)
- `progress_log.md` — updated

### Constraints preserved
- SHA-256 hashing untouched
- Decimal precision untouched
- All existing JSON schemas untouched (`system_state.json` is additive-new)
- UI/logic separation maintained

### Commit
`e60a541` — branch `claude/fix-holdings-sidebar-Aj5gr`

---

## Full Session History

### Phases 1–13 (pre-session — from progress_log0403.md)

| Phase | Version | Date | Summary |
|-------|---------|------|---------|
| 1 | v1.0 | Early 2026 | React artifact — CORS/rate-limit failures |
| 2 | v2.0 | Mar 2026 | Migrated to Python/Streamlit; yfinance + CoinGecko |
| 3 | v3.0 | Apr 1 | Fixed Sell transaction parser (0 sell handlers) |
| 4 | v4.0 | Apr 1 | DRIP tracking, equity fix (was showing cost basis), 56 unit tests |
| 5–6 | v5–6.1 | Apr 2 | Single-file App.py; clickable KPI cards; CSV dedup attempt (failed) |
| 7 | v7.0 | Apr 2 | tx_store architecture — recompute from scratch, idempotent uploads |
| 8 | v8.0–8.2 | Apr 2 | Disk persistence; SHA-1→SHA-256; safe-price helper; null-bytes fixed |
| 9 | v9.0 | Apr 2 | Baked-in bootstrap data; amateur-first UI redesign |
| 10 | v9.1 | Apr 2 | Ghost KPI boxes; NameError; tab style; refresh bust counter |
| 11 | v10.0 | Apr 3 | Modular split: `data_engine.py` + `main_app.py`; AI targets; drift engine |
| 12 | v10.1 | Apr 3 | HTML table → st.dataframe (Markdown parser corruption) |
| 13 | v10.2 | Apr 3 | Deep audit: SHA-256 dedup, Decimal precision, correct bootstrap, recon log |

---

### Phase 14 — Real-Time Pricing Backend (v11.0, April 3, 2026)

**Goal:** Replace yfinance (delayed) with real-time Finnhub/Polygon/CoinGecko. Wire Plaid as the authoritative source of holding quantities to match Robinhood's Mark Price.

New files: `plaid_client.py`, `price_service.py`, `portfolio_aggregator.py`, `main_sync.py`. Mark Price formula: `mid = (bid + ask) / 2`. Provider cascade: Finnhub → Polygon → CoinGecko → cache. Test suite: 22/22 passing.

---

### Phase 15 — Smart Sync: Plaid 24h Cache (v11.1, April 3, 2026)

New file: `holdings_manager.py`. Three-condition sync trigger: no cache file / cache >24h old / force_refresh=True. Cache schema stores qty, cost_basis, institution_price per holding. Plaid called at most once per day. Prices update every 60s independently. Test suite: 29/29 passing.

---

### Phase 16 — Three Features: Dedup Hardening, Cash Rebalancing, Decision Log (v11.2, April 3, 2026)

**Dedup (3-layer):** Layer 1 = session set, Layer 2 = disk snapshot frozen before loop, Layer 3 = intra-file set. `IngestStats` gains `seen_in_file` counter. `strip_existing_tx_store_fingerprints()` for cold-start seeding.

**Cash rebalancing:** `compute_rebalancing(cash_available=0.0)` — drift vs targets. `generate_deposit_recs(cash_balance=0.0)` — `total_investable = deposit + cash`.

**Decision log:** `DecisionLogEntry` dataclass, `log_decision()`, `load_decision_log()`, `apply_overrides_to_recs()`. Persists to `decision_log.json` (max 500 entries).

---

### Phase 17 — Bug Fixes: Date Parsing + Pandas 3.0 Compatibility (v11.3, April 3, 2026)

**Bug 23:** `ValueError: Invalid isoformat '1/10/2025'` — fixed with `_parse_date_robust()` helper (ISO fast-path + `pd.to_datetime` fallback). Applied to `is_lt_eligible()`, `days_to_lt()`, `generate_recs()`.

**Bug 24:** `AttributeError: Styler has no attribute 'applymap'` — `applymap` removed in pandas 3.0. All 5 occurrences replaced with `.map()`. Test suite: 7/7 passing.

---

### Phase 18 — Critical Dedup Refactor: High-Integrity Hashing Engine (v11.4, April 4, 2026)

**Root causes diagnosed (5 separate issues):**

1. `_bootstrap()` used opaque `sha256("BOOTSTRAP|TICKER")` keys. These **never matched** real CSV row fingerprints. Every upload appeared all-new because `processed_ids` was seeded with un-matchable keys.
2. `make_tx_fingerprint()` hashed raw strings: `"4/2/2026"` and `"2026-04-02"` produced different hashes; `"$173.78"` and `"173.78"` produced different hashes.
3. `ingest_csv()` never updated `existing_ids` in-place — Layer 1 session dedup could never block same-session re-uploads.
4. `_init()` seeded `processed_ids` with the wrong (opaque bootstrap) keys, so sidebar showed ~34 fingerprints but none matched any CSV row.
5. `parse_crypto_pdf()` extracted market value (`$2301.45`) as "shares" instead of actual quantity (`0.03432981`).

**Fixes applied:**

| Change | Description |
|--------|-------------|
| `_norm_decimal(val, places=6)` | New helper: strips `$`, commas, `()`, converts to Decimal at fixed precision. `"$874.63"` == `"874.630000"`. Never raises. |
| `_norm_date(val)` | New helper: `pd.to_datetime().strftime('%Y-%m-%d')` with ISO fast-path. `"4/2/2026"` == `"2026-04-02"`. |
| `make_tx_fingerprint()` rewritten | Canonical string: `NormDate\|Ticker\|Code\|NormQty\|NormPrice`. Cash-only rows use `NormAmt\|Settle`. Identical hashes for bootstrap and CSV rows of the same transaction. |
| `_bootstrap()` rewritten | Now calls `make_tx_fingerprint()` to write keys — canonical, not opaque. Bootstrap fingerprints match what ingest_csv() produces. |
| `ingest_csv()` atomic update | `existing_ids.update(seen_this_upload)` called **after** loop, **before** return. Caller's session set updated in-place atomically. |
| `seed_processed_ids_from_history()` | New public function: returns bootstrap FPs ∪ disk FPs. On fresh install: 34 FPs. After CSV import: ~624 FPs. |
| `parse_crypto_pdf()` rewritten | Correct regex reads quantity (`0.03432981`) not market value. Primary pattern matches Robinhood statement table format. |
| `main_app.py _init()` | Now calls `seed_processed_ids_from_history()` instead of `strip_existing_tx_store_fingerprints()`. Sidebar badge shows correct full count. |

**Test results: 36/36 passing**

| Group | Tests | Result |
|-------|-------|--------|
| `_norm_decimal` | 9 | ✅ |
| `_norm_date` | 5 | ✅ |
| `make_tx_fingerprint` | 8 | ✅ |
| `_bootstrap()` parity | 2 | ✅ |
| `seed_processed_ids_from_history` | 3 | ✅ |
| `ingest_csv` real CSVs | 7 | ✅ |
| tx_store schema | 2 | ✅ |
| `parse_crypto_pdf` | 1 | ✅ |

**Verified against real uploaded data:**
- CSV1 (`73aa0200`) — 94 unique rows, 4 genuinely new vs CSV2
- CSV2 (`14fe8e53`) — 590 rows imported fresh; 0 on re-import
- PDF (`75a35b3f`) — Feb 2026 crypto statement: BTC=0.03432981, XRP=1.066

---

---

### Phase 19 — Bootstrap/Live Mode + Sidebar Fix (v12, April 5, 2026)

**Issue 1 — Duplicate holdings after CSV/PDF import (root cause: bootstrap data mixed with real tx data)**

Root cause: `_bootstrap()` writes synthetic summary-level rows keyed by `make_tx_fingerprint()`. When a real CSV is imported the dedup hash engine correctly identifies matches — BUT the summaries survive inside `tx_store.json` and `recompute_portfolio()` replays BOTH the bootstrap rows and the real CSV rows, doubling holdings.

Fix: introduced `system_state.json` with two modes.

| Mode | What it means |
|------|--------------|
| `bootstrap` | Only BAKED_BOOTSTRAP data exists. No real CSV/PDF ever uploaded. |
| `live` | Real data imported. Bootstrap rows have been permanently purged. |

New functions in `data_engine.py`:

| Function | Description |
|----------|-------------|
| `get_system_mode() -> str` | Returns `"bootstrap"` or `"live"` from `system_state.json`. |
| `transition_to_live() -> None` | Wipes `tx_store.json`, writes `system_state.json{mode:"live"}`. Called once. |

`IngestStats` gains `mode_transitioned: bool` flag.

`ingest_csv()` preamble: if `get_system_mode() == "bootstrap"` → call `transition_to_live()`, clear `existing_ids` in-place, set `stats.mode_transitioned = True`. The `_load(TX_STORE_PATH)` after transition returns `{}` so every real row is imported fresh.

`App.py` CSV upload: if `stats.mode_transitioned` → reset `st.session_state.processed_ids = new_ids` (bootstrap FPs discarded). PDF upload: same guard via direct `get_system_mode()` / `transition_to_live()` call.

Sidebar shows `⚠️ BOOTSTRAP MODE` badge (yellow) or `✅ LIVE MODE` (green).

**Issue 2 — Sidebar disappears**

- `sidebar_open: True` added to `_init()` session state defaults — persists across all reruns.
- CSS `section[data-testid='stSidebar']{display:none}` injected when `sidebar_open == False`.
- `[data-testid="collapsedControl"]` forced visible in global CSS — native hamburger never hidden.
- `◀ Hide` / `▶ Show` toggle button added top-right of header (always in main content area, accessible regardless of sidebar state).
- `st.set_page_config(initial_sidebar_state="expanded")` already present — sidebar opens expanded on every cold start.

**Constraints preserved:**
- SHA-256 hashing system untouched
- Decimal precision untouched
- Existing JSON schemas untouched (system_state.json is new, additive)
- UI/logic separation maintained: `get_system_mode()` / `transition_to_live()` in `data_engine.py`; UI wiring in `App.py`

---

---

### Phase 20 — Sidebar Permanent Fix + DRIP Analytics Tab (v13, April 5, 2026)

#### What was asked
Two upgrades on top of v12:
1. **Sidebar bug** — clicking Streamlit's native `<<` collapse button still made the sidebar unrecoverable. Custom toggle stopped working. Only fix was clearing cache.
2. **DRIP Analytics tab** — full dividend tracking dashboard: history, projections, DRIP share impact.

#### Sidebar fix (root cause + solution)

**Root cause (v12 was incomplete):** The v12 fix kept `[data-testid="collapsedControl"]` visible (`display:flex !important`). Users could still click Streamlit's native collapse button, which set the sidebar to Streamlit's internal "collapsed" state. After that, even re-setting `sidebar_open=True` and removing the CSS `display:none` couldn't bring it back — Streamlit's JS had already moved it.

**Fix (v13):**

| Change | Detail |
|--------|--------|
| `[data-testid="collapsedControl"]{display:none !important}` | Native collapse button completely removed from DOM — Streamlit can never internally collapse the sidebar |
| Sidebar-hidden CSS changed | `display:none` → `margin-left:-21rem; width:0; overflow:hidden` — sidebar stays in Streamlit's DOM as "expanded" at all times, just visually off-screen |
| Toggle button changed | `◀ Hide / ▶ Show` → single `☰` button always in main header |
| `sidebar_open` | Still the single source of truth in `st.session_state` |

#### DRIP Analytics (`drip_analytics.py` — new file, 310 lines)

| Function | Description |
|----------|-------------|
| `extract_dividends(path)` | Reads all `CDIV` rows from `tx_store.json` |
| `clean_dividends(raw)` | Deduplicates by `(date, ticker, amount)`, parses types |
| `calculate_projections(div_json, portfolio_json, prices_json)` | `@st.cache_data` — infers payment frequency per ticker (monthly/quarterly/semi-annual/annual), projects next dates, rolling 30/60/90-day income windows, DRIP share accumulation and current value |
| `render_drip_dashboard(portfolio, prices)` | Full Streamlit page rendered inside `tabs[10]` |

**Dashboard sections:**
- 4 KPI cards: Total dividends (all-time), This Year, Last Dividend (ticker/date/amount), Projected Annual
- Filterable history table: multiselect tickers, date-range picker, sort column, dedup caption
- Charts: dividends over time (monthly/quarterly/yearly toggle), total by stock (colour-scaled)
- Future projections: per-ticker table with frequency, avg payout, next estimated date, days-away urgency (🟢 ≤14d · 🟡 ≤45d), upcoming-dividends scatter timeline
- Next 30/60/90 day income KPI cards
- DRIP Impact: shares gained per ticker, current value at live prices, stacked bar (original position vs DRIP boost)

#### Navigation change
- Removed sidebar radio nav (added in earlier v13 commit, reverted per user request)
- DRIP Analytics added as **tab 11** (`tabs[10]`) alongside the existing 10 tabs — same UX pattern as Portfolio, Import, Tests, etc.

#### Files changed
- `App.py` — sidebar CSS fix, ☰ toggle, DRIP tab added (+/- ~30 lines net)
- `drip_analytics.py` — new file, 310 lines
- `progress_log.md` — updated

#### Commit
`claude/sidebar-fix-drip-analytics-9ocGu`

---

## Current App Architecture (v13)

### Tab Structure (11 tabs)

| # | Tab | Contents |
|---|-----|----------|
| 0 | 🎯 Actions | Dynamic buy/sell/trim/hold rec cards, sorted by priority and equity |
| 1 | 📊 Portfolio | Holdings table (Plaid positions preferred), position detail drill-down |
| 2 | ⚖️ Rebalancing | Drift vs targets bar chart, cash-to-deploy distribution |
| 3 | 💰 Invest $900 | Cash-informed deposit plan, override inputs, lock plan, deposit schedule |
| 4 | 📋 Decision Log | Full override history, delta analytics, CSV export |
| 5 | 📅 Schedule | 2026 action calendar, tax playbook |
| 6 | 📈 Charts | Allocation pie, P&L bar, category breakdown |
| 7 | 🕐 History | Portfolio value over time, deposit log |
| 8 | 📥 Import | CSV upload (3-layer dedup), crypto PDF parser, manual crypto override |
| 9 | 🧪 Tests | 15 live system health checks against real data |
| 10 | 💸 DRIP Analytics | Dividend history, projections, DRIP impact analysis (`drip_analytics.py`) |

### Hashing Engine (v11.4)

```
Canonical fingerprint string:
  Trade/CDIV rows:  NormDate | Ticker | Code | NormQty(6dp) | NormPrice(6dp)
  Cash-only rows:   NormDate | ""     | Code | NormAmt(6dp) | SettleDate

_norm_decimal: strips $, commas, parens → Decimal → 6dp string
_norm_date:    pd.to_datetime().strftime('%Y-%m-%d') with ISO fast-path
```

### Three-Layer Dedup (v11.4)

| Layer | Set | When populated | What it catches |
|-------|-----|---------------|-----------------|
| 1 — Session | `existing_ids` | Cold-start via `seed_processed_ids_from_history()` + post-loop update | Same file re-uploaded same session |
| 2 — Disk | `existing_on_disk` (frozen pre-loop snapshot) | Never mutated during loop | Same rows from previous sessions |
| 3 — Intra-file | `seen_this_upload` | During loop | Same row appearing twice in one CSV |

### Recommendation Engine Logic (v11.4, unchanged from v11.3)

| Priority | Condition | Action |
|----------|-----------|--------|
| 0 | SELL_LIST + LT eligible | SELL NOW — LT ✅ |
| 0 | SELL_PENDING + LT eligible | SELL NOW (ETF swap) |
| 1 | P&L < −20% | REVIEW — BIG LOSS ⚠️ |
| 2 | FOREVER_HOLD (VYM/SCHD/VTI) | HOLD FOREVER — DRIP on |
| 2 | DCA_ALWAYS (VOO/QQQ) | DCA EVERY DEPOSIT |
| 2 | P&L < −8% AND upside > 20% | STRONG BUY — ON DIP |
| 2 | Crypto AND upside > 25% | ACCUMULATE — CRYPTO |
| 2 | upside > 20% | ACCUMULATE |
| 3 | dtlt ≤ 30 days | HOLD — LT IN N DAYS |
| 3 | IPO_HOLDS + LT eligible | TRIM 25% — IPO LT |
| 3 | P&L > 20% + LT eligible | TRIM 20% — TAKE GAINS |
| 4 | Everything else | HOLD |

### Price Engine Cascade (v11.4, unchanged from v11.3)

```
Finnhub /quote + /stock/bidask
  ↓ fail
Polygon /v2/snapshot
  ↓ fail
CoinGecko /simple/price (crypto)
  ↓ fail
institution_price from HoldingsCache (Plaid)
  ↓ fail
In-memory price cache
  ↓ fail
price_cache.json on disk
```

---

## Portfolio Snapshot (April 4, 2026)

| Metric | Value |
|--------|-------|
| Robinhood Stocks and ETFs | ~$43,820 |
| Robinhood Cryptocurrencies | ~$2,303 (Feb 2026 PDF verified) |
| Robinhood Cash | $1,042.17 |
| **Total Account Value** | **~$47,165** |
| Active Positions | 34 |
| LT Eligible Positions | 20+ |
| BTC (PDF-verified Feb 2026) | 0.03432981 |
| XRP | 1.066 |

---

## 2026 Action Calendar

| Date | Type | Ticker | Action |
|------|------|--------|--------|
| **Apr 3** | SELL | VTV, VEA, VWO, BND | LT eligible — reinvest into VOO+VYM same day ✅ done |
| **Apr 3** | BUY | Deposit #1 | $900 → NVDA/VOO/VYM/QQQ + META |
| **Apr 4** | TRIM | GLD | GLD turns LT — trim 25% near $450 |
| **Apr 17** | BUY | Deposit #2 | $900 → NVDA/VOO/VYM/QQQ + GOOGL |
| **May 1** | BUY | Deposit #3 | $900 → NVDA/VOO/VYM/QQQ + AAPL |
| **May 20** | SELL | SPY | SPY turns LT — sell, buy VOO same day |
| **Jul 15** | SELL | VUG | VUG turns LT — sell, buy QQQ same day |
| **Aug 14** | EVAL | BLSH | Hits 1yr — trim 25% if up >20% |
| **Sep 11** | EVAL | KLAR | Hits 1yr — trim 25% if up >20% |
| **Nov 6** | TRIM | TSM | Big lot turns LT — trim 20% |
| **Dec 15** | TRIM | GOOGL | Big lot turns LT — trim 20% |
| **Dec 20** | TAX | Portfolio | Year-end harvest — net gains vs losses before Dec 31 |

---

## Tax Playbook

- **Rule #1:** Never sell a position held < 1 year — pay 37% (ST) vs 15% (LT)
- **Rule #2:** ETF swaps are NOT wash sales — selling SPY and buying VOO same day is allowed
- **Rule #3:** DRIP lots — each reinvestment creates a new tax lot. Track individually.
- **Rule #4:** Crypto — BTC/XRP both held >1yr. LT rate applies. Never sell short-term.
- **Rule #5:** Year-end harvest — net realized gains vs losses before Dec 31

---

## Biweekly $900 Deploy Formula

Starting **April 3, 2026** — every other Friday:

| Ticker | % | Amount | Why |
|--------|---|--------|-----|
| NVDA | 28% | $252 | AI supercycle — top conviction |
| VOO | 22% | $198 | S&P 500 index — DCA forever |
| VYM | 17% | $153 | Dividend ETF — compounds income |
| QQQ | 17% | $153 | Nasdaq-100 — tech exposure |
| Rotating | 16% | $144 | META → GOOGL → AAPL → MSFT → COST → TSM → CRM → NFLX |

With cash balance: `total_investable = $900 deposit + Robinhood cash`. All allocations scale to `total_investable` so idle cash is deployed alongside the deposit.

---

## Streamlit Cloud Secrets Required

```toml
# .streamlit/secrets.toml
FINNHUB_API_KEY = "your_key"        # required for live prices
POLYGON_API_KEY = "your_key"        # optional Finnhub fallback
PLAID_ACCESS_TOKEN = "access-xxx"   # required for Plaid sync
PLAID_CLIENT_ID = "your_client_id"
PLAID_SECRET = "your_secret"
PLAID_ENV = "production"
HOLDINGS_CACHE_TTL_HOURS = "24"     # optional, default 24h
```

Minimum viable: just `FINNHUB_API_KEY` — app works fully without Plaid.

---

## Deploy Instructions (v11.4)

```bash
git add main_app.py data_engine.py requirements.txt \
        price_service.py holdings_manager.py \
        portfolio_aggregator.py plaid_client.py main_sync.py
git commit -m "v11.4: high-integrity hashing — canonical FPs, bootstrap parity, 36/36 tests"
git push
# Streamlit Cloud: Main file path = main_app.py
# Auto-redeploys in ~60 seconds
```

**First use after deploy:**
1. Open app — 34 positions pre-loaded from bootstrap
2. Sidebar shows `🔒 Dedup active — 34 fingerprints` (bootstrap FPs)
3. Click **🔄 Refresh** — fetches live prices via Finnhub/CoinGecko
4. Go to **📥 Import** tab → drop Robinhood CSV → sidebar count jumps to ~624
5. Re-upload same CSV → `0 new rows` (dedup working correctly)
6. Drop crypto PDF → BTC/XRP quantities updated correctly
7. Click **🏦 Sync Plaid** (if configured) — syncs authoritative quantities

---

## Cumulative Bug Fix Log

| # | Symptom | Root Cause | Fix | Version |
|---|---------|------------|-----|---------|
| 1 | Sell transactions ignored | Parser had no Sell handler | Added full Sell handler | v3 |
| 2 | Equity showed cost basis | Displaying avg_cost × shares | Fixed to live_price × shares | v4 |
| 3 | SELL recs did not clear | Reconciler never removed 0-share positions | Auto-remove on 0 shares | v4 |
| 4 | DRIP not tracked | No differentiation of reinvestment buys | Detect "Reinvestment" in description | v4 |
| 5 | CSV upload crashed | Robinhood multiline cells broke pd.read_csv | Switched to csv.DictReader with QUOTE_ALL | v5 |
| 6 | Uploading CSV doubled holdings | BASELINE_PORTFOLIO + additive delta | Replaced with tx_store + recompute from scratch | v7 |
| 7 | Dedup did not survive page reload | tx_ledger in session_state resets on restart | Content-hash dedup in persistent tx_store | v7 |
| 8 | KPI cards empty after CSV import | fetch_prices(list) unhashable | Changed to fetch_prices(tuple) | v8.2 |
| 9 | Prices blank for single-ticker | yf.download([t]) returns Series not DataFrame | Per-ticker fast_info["last_price"] | v8.2 |
| 10 | None × shares crash | prices.get(t, avg_cost) returns None | _safe_price() helper | v8.2 |
| 11 | Null bytes in App.py | Shell heredoc string escaping | Rewrote in Python open(...,'w') | v8.2 |
| 12 | KPI cards had ghost blank boxes | opacity:0 overlay rendered visible | .kpi-toggle button below card | v9.1 |
| 13 | Actions Needed crashed NameError | _rcard() called before defined | Moved definition above first call | v9.1 |
| 14 | Refresh button did not update P&L | @st.cache_data re-hit same cache key | _bust counter forces unique cache key | v9.1 |
| 15 | $1 default price for all assets | fast_info["last_price"] returned None | Three-layer fallback: CoinGecko → yfinance → disk | v10.0 |
| 16 | Allocation table showed raw HTML | st.markdown() runs Markdown parser on HTML | Replaced with st.dataframe() + column_config | v10.1 |
| 17 | SHA-1 fingerprint had 6 ACH collisions | Same-day same-amount ACH deposits got identical keys | SHA-256; settle date tiebreaker for cash rows | v10.2 |
| 18 | $4k portfolio value delta | Stale bootstrap + float accumulation drift | Full Decimal CSV replay; Decimal(prec=28) throughout | v10.2 |
| 19 | Cost basis off $0.01–$0.03 per row | qty × price used; Robinhood rounds independently | Use abs(Amount) — actual cash debited | v10.2 |
| 20 | Fractional qty lost precision in JSON | Storing as float (0.023644 → 0.02364399...) | Store as string "0.023644" — lossless Decimal | v10.2 |
| 21 | Session dedup reset on restart | processed_ids not wired to ingest_csv | existing_ids param merges session+disk IDs | v10.2 |
| 22 | Intra-file dupes miscounted | tx_store mutated during loop — Layer 2 saw own new rows | Snapshot existing_on_disk before loop | v11.2 |
| 23 | ValueError: Invalid isoformat '1/10/2025' | date.fromisoformat() only handles YYYY-MM-DD | _parse_date_robust() — ISO fast path + pd.to_datetime | v11.3 |
| 24 | AttributeError: Styler has no attribute 'applymap' | applymap removed in pandas 3.0 | Replaced all 5 occurrences with .map() | v11.3 |
| 25 | Every CSV upload imports all rows as "new" | _bootstrap() wrote opaque `BOOTSTRAP\|ticker` keys that never matched CSV row fingerprints | _bootstrap() rewrote to use make_tx_fingerprint() — canonical keys match CSV hashes | v11.4 |
| 26 | Same transaction hashes differently from CSV vs bootstrap | Raw date ("4/2/2026") and price ("$173.78") not normalised before hashing | _norm_decimal() + _norm_date() normalise all fields; make_tx_fingerprint() uses them | v11.4 |
| 27 | Same-session re-upload imports duplicate rows | existing_ids never updated in-place during ingest | existing_ids.update(seen_this_upload) called post-loop before return | v11.4 |
| 28 | Sidebar showed ~34 FPs instead of ~624 | _init() seeded processed_ids with opaque bootstrap keys | seed_processed_ids_from_history() returns bootstrap FPs ∪ disk FPs | v11.4 |
| 29 | parse_crypto_pdf extracted market value as shares | Regex matched "$2301.45" (dollar amount) instead of "0.03432981" (quantity) | Rewritten to match qty before ticker symbol in statement table | v11.4 |
| 30 | Holdings doubled after first CSV import | Bootstrap summary rows persisted in tx_store; recompute_portfolio() replayed both bootstrap + real CSV rows | system_state.json bootstrap/live mode; transition_to_live() wipes tx_store on first real import | v12 |
| 31 | Sidebar permanently disappeared | No persistent state or escape hatch when sidebar collapsed | sidebar_open in session_state; CSS inject to hide; ◀/▶ toggle button always visible in main content | v12 |
| 32 | Sidebar still unrecoverable after v12 | Native collapsedControl was still visible; clicking it set Streamlit's internal collapsed state which CSS display:none couldn't escape | `[data-testid="collapsedControl"]{display:none !important}` — native button removed; hidden state uses margin-left:-21rem not display:none | v13 |

---

## Open Items / Next Steps

- [ ] **Upload updated Robinhood CSV** after Apr 3 trades (VTV/VEA/VWO/BND sold, Deposit #1) to keep tx_store current
- [ ] **Apr 4** — GLD is now LT eligible, trim 25% near $450
- [ ] **Connect Plaid** — run Plaid Link flow to get production access_token; add to Streamlit secrets
- [ ] Future: Price alert system — push notification when position hits target price
- [ ] Future: Historical performance chart — portfolio value over time line graph
- [ ] Future: Year-end tax-loss harvesting calculator — show which positions to sell to offset gains
- [ ] Future: Migrate tx_store.json to SQLite for faster replay on large stores (currently ~600 rows, fine as JSON)
- [ ] Future: Async Streamlit — run `calculate_total_value_async()` natively without run_in_executor

---

*Log updated April 5, 2026 · Portfolio War Room v13 · 34 positions · 11-tab UI · bootstrap/live mode · permanent sidebar fix · DRIP Analytics · high-integrity SHA-256 hashing · canonical fingerprints · 36/36 tests passing*
