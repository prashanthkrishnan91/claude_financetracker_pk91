# ⚡ Portfolio War Room v13.1

A modular, production-ready personal portfolio intelligence system for Robinhood accounts.
Live prices · Tax-optimized recommendations · Research-backed rationale · Plaid smart sync · Biweekly $900 deploy engine · DRIP analytics

---

## Architecture

```
App.py                  — Streamlit UI — zero business logic (~1,360 lines)
data_engine.py          — All processing, recs, hashing, deposit planning (~1,350 lines)
drip_analytics.py       — DRIP dividend module — extract, project, render (~150 lines)
price_service.py        — Real-time Finnhub/Polygon/CoinGecko pricing engine (401 lines)
holdings_manager.py     — Plaid 24h smart cache — HoldingsManager class (371 lines)
portfolio_aggregator.py — qty × price math, PortfolioSnapshot builder (321 lines)
plaid_client.py         — Thin Plaid Investments API wrapper (216 lines)
main_sync.py            — CLI runner — Smart Sync loop, JSON export (378 lines)
utils/csv_parser.py     — Robinhood CSV parser v4 (multiline-safe)
utils/rec_engine.py     — Standalone recommendation engine v4
data/portfolio.py       — Master position reference data (39 positions)
requirements.txt        — Python dependencies
.streamlit/config.toml  — Dark theme config
```

**Runtime files (auto-created, excluded from git):**
```
tx_store.json           — All transactions (SHA-256 canonical fingerprints)
system_state.json       — Mode: "bootstrap" | "live"
holdings_cache.json     — Plaid holdings cache (TTL = 24h)
plaid_snapshot.json     — Full portfolio snapshot from last Plaid sync
crypto_overrides.json   — BTC/XRP quantities from PDF import
rec_history.json        — Saved portfolio snapshots (max 200)
deposit_log.json        — Logged $900 biweekly deposits
decision_log.json       — Manual override decisions (max 500)
targets.json            — User target allocations
price_cache.json        — Last-known prices (offline fallback)
recon_log.json          — Rolling CSV ingest audit log (max 100)
```

---

## Features

### Recommendations & Intelligence

| Feature | Description |
|---|---|
| **Active Recommendations** | SELL / BUY / TRIM / REVIEW / HOLD cards with one-line research rationale per ticker |
| **Clickable Filter Cards** | Click any category card (SELL, BUY, TRIM, REVIEW, HOLD) to filter the list below; multi-select; click again to deselect; empty = show all |
| **Research-Backed Rationale** | Every rec card shows a current analyst-style one-liner (macro + micro context, April 2026) |
| **Tax Intelligence** | LT vs ST flag on every position · never recommend selling ST · harvest losses Dec |
| **Recommendation Engine** | Priority matrix: SELL LT-eligible → BIG LOSS review → FOREVER HOLD → DCA always → STRONG BUY dip → ACCUMULATE crypto → ACCUMULATE → HOLD LT soon → IPO TRIM → TAKE GAINS |
| **Decision Log** | Manual override history with delta analytics, full sort, CSV export |
| **Save Snapshot** | One-click portfolio snapshot saved to rec_history.json for trend comparison |

### Data & Prices

| Feature | Description |
|---|---|
| **Bootstrap / Live Mode** | App pre-loads 34 positions from baked data; `transition_to_live()` wipes bootstrap on first real CSV upload |
| **High-Integrity Hashing** | SHA-256 canonical fingerprints — `_norm_decimal()` + `_norm_date()` ensure same tx hashes identically from CSV and bootstrap |
| **3-Layer Deduplication** | Layer 1: session set · Layer 2: disk snapshot pre-loop · Layer 3: intra-file set — zero duplicate rows ever written |
| **Plaid Smart Sync** | Calls Plaid Investments API at most once per 24h (TTL cache); force-pull option for same-day trades |
| **Real-Time Prices** | Finnhub bid/ask midpoint → Polygon snapshot → CoinGecko (crypto) → Plaid institution_price → memory cache → disk cache |
| **yfinance Fallback** | Used for dividend intel in DRIP tab; stock prices prefer Finnhub/Polygon |
| **CSV Import** | Robinhood CSV parser handles multiline CUSIP fields, all transaction codes (Buy/Sell/CDIV/DRIP/SPL/ACH/RTP) |
| **Crypto PDF Import** | Parses Robinhood crypto statements for BTC/XRP quantities (regex-matched, not dollar amounts) |
| **Decimal Precision** | Fixed 6dp throughout; Decimal(28) for portfolio sums — zero float drift |

### Portfolio Management

| Feature | Description |
|---|---|
| **Portfolio Heatmap** | Treemap with P&L colour gradient (green/red), category grouping, hover tooltips |
| **Target Rebalancing** | Set target % per ticker → auto-calculate drift → distribute cash to most underweight |
| **$900 Deploy Engine** | Biweekly deposit plan: NVDA 28% / VOO 22% / VYM 17% / QQQ 17% / Rotating 16% |
| **Cash-Informed Allocation** | `total_investable = $900 deposit + Robinhood cash` — idle cash deployed every cycle |
| **Rotating Pick Schedule** | META → GOOGL → AAPL → MSFT → COST → TSM → CRM → NFLX |
| **Deposit Calendar** | 16 upcoming biweekly Fridays · rotate pick · log executed deposits |
| **LT Eligibility Tracking** | Days-to-LT counter per position; tax note on every recommendation card |
| **Position Detail Drill-Down** | Expandable per-ticker panel: shares, avg cost, P&L, LT status, equity |

### DRIP Analytics

| Feature | Description |
|---|---|
| **Dividend History Ledger** | All CDIV transactions extracted from tx_store, deduped, sorted by date |
| **Upcoming Payouts** | yfinance trailing yield × current shares → annual income projection per ticker |
| **Pay / Ex-Dividend Dates** | Fetched from yfinance calendar with 24h cache (TTL = 86400s) |
| **KPI Cards** | Lifetime earned · Annual projection · Estimated monthly income |

### UI / UX

| Feature | Description |
|---|---|
| **11-Tab Layout** | Intel (Recommendations + Holdings + Charts + DRIP) · Operations · Archive · Terminal |
| **Permanent Sidebar** | Native collapse button removed; CSS off-screen slide; ☰ toggle always accessible |
| **System Mode Badge** | `⚠️ BOOTSTRAP MODE` (amber) or `✅ LIVE MODE` (green) in sidebar |
| **Plaid Sync Status** | Fresh / Stale / No cache badge with age in hours and next-sync countdown |
| **Price Data Health** | Live / Partial / Stale indicator with count and last API fetch timestamp |
| **Glassmorphic Theme** | Dark (#07090f) base · DM Serif Display headings · JetBrains Mono data · Plotly dark |
| **Refresh Bust Counter** | Every 🔄 click busts @st.cache_data with a unique counter — no stale cache |

### Testing & Integrity

| Feature | Description |
|---|---|
| **Test Suite** | 36/36 unit tests: norm_decimal, norm_date, fingerprint, bootstrap parity, ingest_csv, tx_store schema |
| **Integration Tests** | 27 tests: Plaid parser, PriceResult, mid-price formula, Finnhub/Polygon/CoinGecko (mocked), PortfolioAggregator |
| **In-App Test Runner** | 🧪 Tests tab runs 15 live health checks against real session data |

---

## Streamlit Cloud Secrets

```toml
# .streamlit/secrets.toml
FINNHUB_API_KEY      = "your_key"        # required for real-time prices
POLYGON_API_KEY      = "your_key"        # optional Finnhub fallback
PLAID_ACCESS_TOKEN   = "access-xxx"      # required for Plaid sync
PLAID_CLIENT_ID      = "your_client_id"
PLAID_SECRET         = "your_secret"
PLAID_ENV            = "production"
HOLDINGS_CACHE_TTL_HOURS = "24"          # optional, default 24h
```

Minimum viable: just `FINNHUB_API_KEY` — app works fully without Plaid.

---

## Deploy

```bash
git add App.py data_engine.py drip_analytics.py \
        price_service.py holdings_manager.py \
        portfolio_aggregator.py plaid_client.py \
        main_sync.py requirements.txt
git commit -m "v13.1: research rationale, clickable filter cards, DRIP refactor"
git push
# Streamlit Cloud → Main file path = App.py
# Auto-redeploys in ~60 seconds
```

---

## First Use

1. Open app — 34 positions pre-loaded from bootstrap data
2. Sidebar shows `⚠️ BOOTSTRAP MODE` badge
3. Click **🔄 Refresh** — fetches live prices via Finnhub/CoinGecko
4. Go to **🛡️ Intel → 🎯 Active Recommendations** — click any category card to filter
5. Go to **📥 Import** tab → drop Robinhood CSV → mode switches to `✅ LIVE MODE`
6. Click **🏦 Sync Plaid** (if configured) — syncs authoritative share quantities
7. Go to **💸 DRIP Analytics** — view dividend history and annual income projection

---

## Tax Playbook

- **Rule #1:** Never sell a position held < 1 year — 37% ST vs 15% LT
- **Rule #2:** ETF swaps are NOT wash sales (SPY → VOO, VUG → QQQ allowed same day)
- **Rule #3:** DRIP lots — each reinvestment creates a new tax lot; tracked individually
- **Rule #4:** Crypto — BTC/XRP both held >1yr; LT rate applies
- **Rule #5:** Year-end harvest — net realized gains vs losses before Dec 31

## 2026 Action Calendar

| Date | Type | Ticker | Action |
|------|------|--------|--------|
| **Apr 3** ✅ | SELL | VTV, VEA, VWO, BND | LT eligible — sold, reinvested into VOO+VYM |
| **Apr 3** ✅ | BUY | Deposit #1 | $900 → NVDA/VOO/VYM/QQQ + META |
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
