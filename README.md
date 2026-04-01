# ⚡ Portfolio War Room v4.0

> Personal portfolio intelligence system — live prices, smart recommendations, DRIP tracking, tax optimization

## Live Demo
Deploy free on [Streamlit Cloud](https://share.streamlit.io) in 2 minutes.

## Features

- **Live prices** via yfinance + CoinGecko — zero API keys needed
- **Smart recommendations** — buy/sell/trim/hold with LT/ST tax awareness
- **DRIP tracking** — every dividend reinvestment tracked, compound growth projected
- **CSV import** — upload Robinhood Account Activity CSV, all transaction types handled:
  - `Buy`, `Sell`, `CDIV`, `SPL`, `RTP`, `ACH`, `LIQ`, `REC`, `SXCH`
- **Auto-removes closed positions** — sold-out SELL positions disappear after import
- **Biweekly $900 deploy plan** — rotating picks, schedule through Dec 2026
- **Snapshot history** — every price refresh saved with timestamp
- **56/56 unit tests pass**

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/my-portfolio-ai
cd my-portfolio-ai
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Cloud (free)

1. Fork this repo
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Click **New app** → select your fork → `app.py`
4. Deploy — live in ~2 minutes

## Run Tests

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from tests.test_all import TestCSVParser, TestReconcile, TestRecEngine, TestIntegration
# ... (see tests/test_all.py)
"
```

## Architecture

```
my-portfolio-ai/
├── app.py                 # Main Streamlit app (7 pages)
├── requirements.txt
├── .streamlit/config.toml
├── data/
│   └── portfolio.py       # All 39 positions, DRIP data, schedules
├── utils/
│   ├── csv_parser.py      # Full Robinhood CSV parser (all tx codes)
│   ├── rec_engine.py      # Recommendation engine (DRIP-aware, tax-aware)
│   └── price_fetcher.py   # yfinance + CoinGecko dual-source fetcher
└── tests/
    └── test_all.py        # 56 unit + integration tests
```

## Data Sources

| Source | Covers | Cost |
|--------|--------|------|
| yfinance | All 39 stocks/ETFs | Free |
| CoinGecko | BTC + XRP | Free |

Runs server-side → no CORS issues, no 429 rate limits.

## Recommendation Logic

Priority order:
1. Income ETFs (VYM, SCHD) → **HOLD FOREVER** — never sell, always DRIP
2. Core index (VOO, QQQ, VTI) → **DCA ALWAYS** — buy every deposit
3. SELL list → **SELL NOW** (if LT) or **WAIT** (with LT date)
4. Bear proximity → **STOP-LOSS ALERT** (non-crypto)
5. Crypto → accumulate/hold/trim based on 25% upside threshold
6. Declining thesis → conservative accumulate cap
7. Normal thesis → dip buying, accumulate, trim at target

Tax awareness: every recommendation includes LT vs ST tax rate impact.
DRIP yield: positions with high dividend yield get stronger hold/accumulate signals.

---
*Not financial advice. For personal tracking purposes only.*
