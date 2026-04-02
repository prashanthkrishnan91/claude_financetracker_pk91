# Portfolio War Room v5.0 - Enhanced Edition

⚡ Real-time portfolio intelligence with live pricing, tax-optimized recommendations, and mobile-first design.

## 🆕 What's New in v5.0

### 1. **Clickable Overview Cards**
- All 4 overview metric cards now expand to show detailed buy/sell/trim information
- Quick decision-making without scrolling through tables
- Color-coded urgency indicators

### 2. **Visual Holdings Improvements**
- ✅ Red highlighting for positions with losses
- ✅ Green highlighting for positions with gains
- ✅ Fixed SELL category sync between Overview and Holdings tabs
- Sortable by Equity, Gain %, or Ticker
- Filterable by Category and Action

### 3. **CSV/PDF Import Enhancements**
- ✅ Fixed upload button text alignment
- ✅ Added PDF upload support for crypto statements (parsing in progress)
- Transaction preview before applying changes
- Confirm/cancel workflow for position updates

### 4. **Cash Deployment Recommendations**
- 💰 Real-time recommendations for current cash balance ($1,042.17)
- 💰 Dynamic allocation based on future cash inflows from sells
- Automatic calculation of shares to purchase at live prices
- Biweekly $900 deployment calendar with all 2026 dates

### 5. **Mobile & Desktop Responsive**
- Fully responsive grid layout (1 column mobile → 4 columns desktop)
- Touch-friendly buttons and cards
- Optimized typography and spacing for all screen sizes
- Works perfectly in laptop and mobile browsers

## 📱 Deployment Options

### Option 1: Streamlit Cloud (Recommended - Free & Mobile-Accessible)

1. **Push to GitHub:**
```bash
cd my-portfolio-ai
git add .
git commit -m "v5.0 enhanced with mobile responsive design"
git push origin main
```

2. **Deploy on Streamlit Cloud:**
   - Go to [share.streamlit.io](https://share.streamlit.io)
   - Click "New app"
   - Connect to `prashanthkrishnan91/my-portfolio-ai`
   - Set main file: `app_enhanced.py`
   - Click "Deploy"
   - Your app will be live at: `https://[your-app-name].streamlit.app`

3. **Access anywhere:**
   - Desktop browser: Full desktop experience
   - Mobile browser: Optimized mobile layout
   - Bookmark for quick access

### Option 2: Local Development

```bash
# Clone repo
git clone https://github.com/prashanthkrishnan91/my-portfolio-ai.git
cd my-portfolio-ai

# Install dependencies
pip install -r requirements.txt

# Run locally
streamlit run app_enhanced.py

# Access at http://localhost:8501
```

## 🎯 Quick Start Guide

### First Time Setup

1. **Refresh Prices**: Click "🔄 REFRESH PRICES" to fetch live data
2. **Import CSV**: Go to "Import Data" tab and upload your latest Robinhood CSV
3. **Review Holdings**: Check the "Holdings" tab to verify all positions
4. **Check Recommendations**: "Overview" tab shows all buy/sell/trim actions

### Weekly Workflow

**Every Friday (Deposit Day):**
1. Click "Deploy $900" tab
2. Review recommended allocation
3. Buy stocks per recommendations in Robinhood
4. Click "✓ Log This Deposit" to update holdings
5. Done!

**As Needed (Market Moves):**
1. Click "🔄 REFRESH PRICES"
2. Check "Overview" for any new SELL or TRIM alerts
3. Execute trades as recommended
4. Import CSV to update holdings

## 🔧 Technical Architecture

### File Structure
```
my-portfolio-ai/
├── app_enhanced.py          # Main Streamlit app (v5.0)
├── requirements.txt         # Python dependencies
├── README.md               # This file
├── .streamlit/
│   └── config.toml         # Dark theme config
└── data/
    └── (optional) CSV backups
```

### Data Sources
- **Stocks/ETFs**: yfinance (batch fetch, no API key needed)
- **Crypto**: CoinGecko free API (BTC, XRP)
- **Rate Limits**: ~100 requests/hour (more than enough for 41 positions)

### Storage
- Session state (in-memory during use)
- Export to JSON for backups
- CSV import for reconciliation

## 💡 Pro Tips

### Tax Optimization
- 🔴 **SELL NOW** = Long-term eligible, execute immediately
- ⏳ **WAIT → SELL** = Short-term, wait for LT date to avoid 37% tax
- 📅 Mark your calendar for LT conversion dates (shown in Overview)

### Portfolio Strategy
- **Income ETFs (VYM, SCHD)**: Never sell, DRIP forever
- **Core ETFs (VOO, QQQ, VTI)**: DCA $200-150 biweekly always
- **SELL List**: Consolidate underperformers into core positions
- **Rotating Pick**: Changes based on highest upside opportunity

### Cash Management
- Keep $1,000+ available for opportunities
- SELL proceeds automatically calculate in deployment recs
- Emergency cash stays in account, invested cash goes to $900 plan

## 🚀 Roadmap

### Planned Features
- [ ] PDF crypto statement parsing (PyPDF2 integration)
- [ ] Historical performance charts
- [ ] Tax loss harvesting calculator
- [ ] Dividend reinvestment tracking
- [ ] Export to Excel for tax reporting
- [ ] Price alerts via email/SMS

### Known Issues
- PDF upload UI ready, parsing logic pending
- Large CSV files (>1000 rows) may take 5-10 seconds to parse

## 📊 Portfolio Summary (as of April 1, 2026)

| Metric | Value |
|--------|-------|
| **Total Positions** | 41 active |
| **Total Equity** | ~$48,288 |
| **Cash Available** | $1,042.17 |
| **Categories** | Crypto (2), Core (13), Other (6), IPO (3), ETF✓ (12), ETF🔴 (5) |
| **DRIPs Active** | 26 positions |
| **Next Deposit** | April 3, 2026 ($900) |

## 🔐 Security & Privacy

- No API keys required (uses free public APIs)
- All data stored locally in session
- No data sent to third parties
- CSV/PDF uploads never leave your browser
- Export JSON for backups (store securely)

## 📞 Support

Questions or issues? Check:
1. Progress log: `progress_log0401.md`
2. GitHub Issues: Create issue in repo
3. Streamlit Docs: [docs.streamlit.io](https://docs.streamlit.io)

---

**Version**: 5.0 Enhanced Edition  
**Last Updated**: April 1, 2026  
**License**: MIT  
**Author**: Prashanth Krishnan (@prashanthkrishnan91)
