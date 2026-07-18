/**
 * Navigation contract tests — post-consolidation.
 *
 * The app has exactly three primary destinations (mobile and desktop):
 *   Positions → /dashboard/positions
 *   Advisor   → /dashboard/advisor
 *   Watchlist → /dashboard/watchlist
 *
 * Settings is secondary only (SideNav footer gear), never a primary item.
 * No legacy route may appear anywhere in the nav constants or the nav
 * component source.
 */

import fs from "fs";
import path from "path";
import {
  PRIMARY_NAV_ITEMS,
  MOBILE_NAV_ITEMS,
  DESKTOP_NAV_ITEMS,
  SECONDARY_NAV_ITEMS,
} from "./nav-items";

const CANONICAL_ROUTES = [
  "/dashboard/positions",
  "/dashboard/advisor",
  "/dashboard/watchlist",
];

const LEGACY_ROUTES = [
  "/dashboard/portfolio",
  "/dashboard/recommendations",
  "/dashboard/deposits",
  "/dashboard/paycheck-plan",
  "/dashboard/alerts",
  "/dashboard/journal",
  "/dashboard/drip",
  "/dashboard/radar",
  "/dashboard/import",
];

describe("primary navigation — exactly three items", () => {
  it("mobile nav has exactly three items with the canonical routes in order", () => {
    expect(MOBILE_NAV_ITEMS).toHaveLength(3);
    expect(MOBILE_NAV_ITEMS.map((i) => i.href)).toEqual(CANONICAL_ROUTES);
  });

  it("desktop nav has exactly three items with the canonical routes in order", () => {
    expect(DESKTOP_NAV_ITEMS).toHaveLength(3);
    expect(DESKTOP_NAV_ITEMS.map((i) => i.href)).toEqual(CANONICAL_ROUTES);
  });

  it("labels are Positions / Advisor / Watchlist", () => {
    expect(PRIMARY_NAV_ITEMS.map((i) => i.label)).toEqual([
      "Positions",
      "Advisor",
      "Watchlist",
    ]);
  });
});

describe("no legacy routes anywhere in nav constants", () => {
  const allNavHrefs = [
    ...MOBILE_NAV_ITEMS,
    ...DESKTOP_NAV_ITEMS,
    ...PRIMARY_NAV_ITEMS,
    ...SECONDARY_NAV_ITEMS,
  ].map((i) => i.href);

  it.each(LEGACY_ROUTES)("%s is not a nav destination", (legacy) => {
    expect(allNavHrefs).not.toContain(legacy);
  });

  it("bare /dashboard is not a nav destination", () => {
    expect(allNavHrefs).not.toContain("/dashboard");
  });

  it("nav constants source contains no legacy route strings", () => {
    const source = fs.readFileSync(path.join(__dirname, "nav-items.ts"), "utf-8");
    for (const legacy of LEGACY_ROUTES) {
      expect(source).not.toContain(`"${legacy}"`);
    }
  });

  it("BottomNav component source links to no legacy route", () => {
    const source = fs.readFileSync(path.join(__dirname, "BottomNav.tsx"), "utf-8");
    for (const legacy of LEGACY_ROUTES) {
      expect(source).not.toContain(`"${legacy}"`);
    }
  });
});

describe("settings is secondary only", () => {
  it("is NOT in any primary array", () => {
    for (const items of [PRIMARY_NAV_ITEMS, MOBILE_NAV_ITEMS, DESKTOP_NAV_ITEMS]) {
      expect(items.map((i) => i.href)).not.toContain("/settings");
    }
  });

  it("is present exactly once in the secondary array", () => {
    const settings = SECONDARY_NAV_ITEMS.filter((i) => i.href === "/settings");
    expect(settings).toHaveLength(1);
    expect(settings[0].label).toBe("Settings");
  });
});
