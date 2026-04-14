FINANCIAL TRACKER ARCHITECTURAL MAP
<repo_structure>

apps/

web/ (Next.js 16, React 19) -> Robinhood UI, Charts

api/ (Python 3.12, FastAPI) -> Stock Analysis, yfinance, News Scraping

packages/

database/ (Supabase migrations, RLS policies)

shared/ (Zod schemas, Shared Types)

supabase/ (Migrations, Edge Functions)
-.claude/ (Production Intelligence Library)
</repo_structure>

KEY DATA FLOWS
Robinhood Data -> Python API -> Supabase DB

Supabase DB -> Next.js Frontend (Server Components)

News/Analyst Reports -> Firecrawl -> Python Analyzer -> Recommendation UI
