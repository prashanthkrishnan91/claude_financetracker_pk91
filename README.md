# ⚔️ Portfolio War Room v10.0

A modular, production-ready personal portfolio intelligence system.
Live prices · Tax-optimized recommendations · Biweekly $900 deploy engine · Target rebalancing

---

## Architecture

```
main_app.py         — Streamlit UI (zero business logic)
data_engine.py      — All data processing, recs, deposit planning
requirements.txt    — Python dependencies
.streamlit/config.toml — Dark theme
```

Runtime files (auto-created, excluded from git):
```
tx_store.json       — All transactions (baked + uploaded)
crypto_overrides.json
rec_history.json
deposit_log.json
targets.json
```

---

## Deploy to Streamlit Cloud

```bash
git add main_app.py data_engine.py requirements.txt .streamlit/config.toml README.md .gitignore
git commit -m "v10.0: modular architecture, target rebalancing, drift engine"
git push
```

Set **Main file path** = `main_app.py` in Streamlit Cloud settings.

---

## Features

| Feature | Description |
|---|---|
| Live Prices | yfinance (stocks/ETFs) + CoinGecko (crypto) |
| On-demand Refresh | Click 🔄 in sidebar — unique cache bust per click |
| Smart Rebalancing | Set target % → auto-allocate $900 to most underweight |
| Dynamic Recs | SELL/BUY/TRIM/HOLD — recalculates on every refresh |
| Tax Intelligence | LT vs ST flag · never sell ST · harvest losses Dec |
| Deposit Calendar | 16 upcoming biweekly Fridays · rotating pick schedule |
| Deposit Logger | Log executed deposits · track history |
| Portfolio Charts | Donut allocation · P&L bar · equity waterfall |
| History Tab | Snapshot portfolio value over time |
| Import Tab | SHA-1 dedup CSV ingestion · manual entry fallback |
| Test Suite | 25 live system tests with real price verification |

---

## First Use

1. Open the app — 30 positions pre-loaded from baked bootstrap data
2. Click **🔄 Refresh** in sidebar — fetches live prices for all positions
3. Go to **⚡ Actions** tab — see what to buy/sell/trim today
4. Go to **💰 Invest $900** tab — see exactly where to put next deposit
5. To import new trades: **📥 Import** tab → upload Robinhood CSV

---

## Tax Playbook

- **Rule #1:** Never sell < 1 year held — 37% ST vs 15% LT
- **Apr 3:** Sell VTV/VEA/VWO/BND (LT eligible) → buy VOO/VYM same day
- **May 20:** SPY turns LT → swap to VOO (not a wash sale — different fund)
- **Jul 15:** VUG turns LT → swap to QQQ
- **Dec 20:** Tax-loss harvest — net gains vs losses before Dec 31
