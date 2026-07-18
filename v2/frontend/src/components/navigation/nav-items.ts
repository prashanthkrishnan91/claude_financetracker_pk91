/**
 * Canonical navigation constants — single source of truth for BottomNav (mobile)
 * and SideNav (desktop).
 *
 * The app has exactly three primary destinations:
 *   Positions → /dashboard/positions
 *   Advisor   → /dashboard/advisor
 *   Watchlist → /dashboard/watchlist
 *
 * Settings is a secondary action only (compact gear in the SideNav footer),
 * never a primary nav item. Import is reachable from the Positions page header.
 * All legacy surfaces (Today, Intel, Deploy, Portfolio, Alerts, DRIP, Import,
 * Journal, Radar, Paycheck) are retired and redirect to the three views.
 */

export interface NavItem {
  href: string;
  label: string;
}

export const PRIMARY_NAV_ITEMS: readonly NavItem[] = [
  { href: "/dashboard/positions", label: "Positions" },
  { href: "/dashboard/advisor", label: "Advisor" },
  { href: "/dashboard/watchlist", label: "Watchlist" },
] as const;

/** Mobile BottomNav renders exactly the three primary items. */
export const MOBILE_NAV_ITEMS: readonly NavItem[] = PRIMARY_NAV_ITEMS;

/** Desktop SideNav renders exactly the three primary items. */
export const DESKTOP_NAV_ITEMS: readonly NavItem[] = PRIMARY_NAV_ITEMS;

/**
 * Secondary (non-primary) destinations. Settings renders as a compact,
 * visually-secondary entry in the SideNav footer — not a fourth tab.
 */
export const SECONDARY_NAV_ITEMS: readonly NavItem[] = [
  { href: "/settings", label: "Settings" },
] as const;
