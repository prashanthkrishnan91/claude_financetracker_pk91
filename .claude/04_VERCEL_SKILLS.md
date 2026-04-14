Purpose: Performance guidelines for serverless stock analysis.

SKILL: VERCEL_FINANCE_DEPLOYMENT
SERVERLESS: Python analysis functions must be under 50MB to prevent cold start lag.

STREAMING: Use Next.js Suspense for loading live stock prices (no request waterfalls).

CACHING: Use unstable_cache for news reports to avoid redundant API hits.

UI AUDIT:

Accuracy: Currency formatting must be localized.

Performance: Charts must use canvas/virtualization for long-term portfolio history.
