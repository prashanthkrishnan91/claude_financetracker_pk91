# ⚔️ Portfolio War Room

> Amateur-investor–first portfolio intelligence system. Live prices, plain-English guidance, tax-aware recommendations, biweekly $900 deploy plan.

## Features

| Tab | What it does |
|-----|-------------|
| 🏠 Dashboard | Live KPI cards, action-required alerts, plain-English rec cards, 2026 calendar |
| 📋 Holdings | Color-coded table (red=loss), allocation pie, P&L bar chart, active sell list |
| 💰 Invest | Cash tracker, biweekly $900 deploy schedule with rotating picks, deposit logger |
| 📥 Import | CSV upload (SHA-1 dedup — never doubles), Crypto PDF parser |
| 🌱 DRIP | Per-ticker dividend reinvestment breakdown + bar chart |
| 📸 History | Timestamped recommendation snapshots |
| ⚙️ Settings | Crypto overrides, portfolio export, system status, reset options |

## First Launch

**No upload needed.** Your 585 historical transactions are baked into the app.  
On first launch they're written to `tx_store.json` automatically.  
Just deploy and click **🔄 Refresh**.

## Uploading New Activity

1. Go to Robinhood → Account → Statements & History → Export CSV
2. Open **📥 Import** tab → drop the file
3. Only NEW rows are added — re-uploading the same file is always safe (0 duplicates)

## Deploy to Streamlit Cloud

```bash
git add App.py requirements.txt .streamlit/config.toml README.md
git commit -m "v9.0: redesigned UI + baked data"
git push
```

Streamlit Cloud auto-redeploys in ~60 seconds.

## Architecture

```
App.py                   # Single-file app — all logic + UI
tx_store.json            # Created automatically on first launch
crypto_overrides.json    # BTC/XRP positions
rec_history.json         # Saved recommendation snapshots
deposit_log.json         # Your logged deposits
.streamlit/config.toml   # Dark theme
requirements.txt         # Dependencies
```

## Recommendation Logic

| Category | Rule |
|----------|------|
| ♾ HOLD FOREVER | VYM, SCHD, VTI — income ETFs, DRIP always on |
| 📈 DCA ALWAYS | VOO, QQQ — add every $900 deposit |
| 🔴 SELL NOW | SELL_LIST positions once LT eligible (>1 yr) |
| 💎 STRONG BUY | Down >8% AND >20% upside to target |
| 🟢 ACCUMULATE | >20% upside to target |
| ✂️ TRIM | Up >20% AND LT eligible — harvest partial gains |
| 🔒 IPO HOLD | BLSH, KLAR, STUB, SNOW — hold until LT |
| 🚨 REVIEW | Down >20% from cost basis |

## Biweekly $900 Allocation

| Ticker | % | Why |
|--------|---|-----|
| NVDA | 28% | AI supercycle — top conviction |
| VOO | 22% | S&P 500 — DCA forever |
| VYM | 17% | Dividend engine |
| QQQ | 17% | Nasdaq-100 |
| Rotating | 16% | META → GOOGL → AAPL → MSFT → COST → TSM → CRM → NFLX |
