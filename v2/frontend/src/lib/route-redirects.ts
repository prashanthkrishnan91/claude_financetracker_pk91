/**
 * Legacy route → canonical view redirects. Single source of truth.
 *
 * The app has exactly three primary views:
 *   /dashboard/positions, /dashboard/advisor, /dashboard/watchlist
 *
 * Every retired surface below is a minimal server component that calls
 * redirect(LEGACY_ROUTE_REDIRECTS["<legacy path>"]). Do not add content
 * back to those pages; extend this map instead.
 */

export const LEGACY_ROUTE_REDIRECTS = {
  "/dashboard": "/dashboard/positions",
  "/dashboard/portfolio": "/dashboard/positions",
  "/dashboard/recommendations": "/dashboard/advisor",
  "/dashboard/deposits": "/dashboard/advisor",
  "/dashboard/paycheck-plan": "/dashboard/advisor?section=cash-plan",
  "/dashboard/alerts": "/dashboard/advisor",
  "/dashboard/journal": "/dashboard/advisor",
  "/dashboard/drip": "/dashboard/positions",
  "/dashboard/radar": "/dashboard/watchlist",
} as const;

export type LegacyRoute = keyof typeof LEGACY_ROUTE_REDIRECTS;
