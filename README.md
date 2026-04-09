# Portfolio Intelligence Platform

A personal portfolio intelligence system for Robinhood accounts with real-time pricing, tax-optimized recommendations, and DRIP analytics.

---

## Repository Structure

```
.
├── v1/                    # Portfolio War Room (Streamlit) — LIVE in production
│   ├── App.py             # Streamlit UI (~1,360 lines)
│   ├── data_engine.py     # Business logic, recs, hashing (~1,350 lines)
│   ├── price_service.py   # Finnhub/Polygon/CoinGecko pricing engine
│   ├── holdings_manager.py# Plaid 24h smart cache
│   ├── drip_analytics.py  # DRIP dividend module
│   ├── requirements.txt   # Python dependencies
│   ├── README.md          # v1-specific documentation
│   └── ...
│
├── v2/                    # Next-gen serverless platform (in development)
│   ├── backend/           # FastAPI + Supabase (Python)
│   ├── frontend/          # Next.js 14 + Tailwind + shadcn/ui
│   ├── database/          # PostgreSQL migrations & schema
│   ├── docs/              # Architecture & design documents
│   ├── README.md          # v2 roadmap & progress
│   └── progress_log.md    # Detailed change log
│
├── App.py                 # Streamlit Cloud shim → loads v1/App.py
├── requirements.txt       # Root deps → references v1/requirements.txt
├── .streamlit/            # Streamlit theme config
└── claude.md              # Development workflow rules
```

## v1 — Portfolio War Room (Streamlit)

**Status: LIVE on Streamlit Cloud**

Full-featured portfolio intelligence dashboard with 39 positions, real-time pricing via Finnhub/Polygon/CoinGecko, Plaid smart sync, tax-optimized recommendations, and DRIP analytics. See [v1/README.md](v1/README.md) for complete documentation.

## v2 — Serverless Platform (In Development)

**Stack: FastAPI + Next.js 14 + Supabase + Tailwind CSS**

A ground-up rebuild as a production-grade serverless platform:
- **Backend**: FastAPI (async) with Plaid, Alpaca, and yfinance integrations
- **Frontend**: Next.js 14 with Robinhood-inspired UI using shadcn/ui
- **Database**: Supabase (PostgreSQL) with RLS, encrypted API keys
- **Deployment**: Vercel/Netlify (frontend) + serverless (backend)

See [v2/README.md](v2/README.md) for the full roadmap and progress.

## Development

```bash
# v1 (Streamlit)
cd v1 && pip install -r requirements.txt && streamlit run App.py

# v2 Backend (FastAPI)
cd v2/backend && pip install -r requirements.txt && uvicorn app.main:app --reload

# v2 Frontend (Next.js)
cd v2/frontend && npm install && npm run dev
```
