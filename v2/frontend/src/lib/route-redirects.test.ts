/**
 * Legacy redirect map contract tests.
 *
 * Every retired surface maps to one of the three canonical views (or the
 * advisor cash-plan deep link). No legacy route maps to another legacy route.
 */

import { LEGACY_ROUTE_REDIRECTS } from "./route-redirects";

const CANONICAL_TARGETS = [
  "/dashboard/positions",
  "/dashboard/advisor",
  "/dashboard/watchlist",
  "/dashboard/advisor?section=cash-plan",
];

const EXPECTED_MAP: Record<string, string> = {
  "/dashboard": "/dashboard/positions",
  "/dashboard/portfolio": "/dashboard/positions",
  "/dashboard/recommendations": "/dashboard/advisor",
  "/dashboard/deposits": "/dashboard/advisor",
  "/dashboard/paycheck-plan": "/dashboard/advisor?section=cash-plan",
  "/dashboard/alerts": "/dashboard/advisor",
  "/dashboard/journal": "/dashboard/advisor",
  "/dashboard/drip": "/dashboard/positions",
  "/dashboard/radar": "/dashboard/watchlist",
};

describe("LEGACY_ROUTE_REDIRECTS", () => {
  it("contains exactly the documented legacy→canonical pairs", () => {
    expect(LEGACY_ROUTE_REDIRECTS).toEqual(EXPECTED_MAP);
  });

  it("has no extra or missing keys", () => {
    expect(Object.keys(LEGACY_ROUTE_REDIRECTS).sort()).toEqual(
      Object.keys(EXPECTED_MAP).sort(),
    );
  });

  it("every target is one of the three canonical views (or advisor?section=cash-plan)", () => {
    for (const target of Object.values(LEGACY_ROUTE_REDIRECTS)) {
      expect(CANONICAL_TARGETS).toContain(target);
    }
  });

  it("no legacy route maps to another legacy route", () => {
    const legacyRoutes = Object.keys(LEGACY_ROUTE_REDIRECTS);
    for (const target of Object.values(LEGACY_ROUTE_REDIRECTS)) {
      const targetPath = target.split("?")[0];
      expect(legacyRoutes).not.toContain(targetPath);
      expect(legacyRoutes).not.toContain(target);
    }
  });

  it("no target redirects to the bare /dashboard root (itself a legacy redirect)", () => {
    for (const target of Object.values(LEGACY_ROUTE_REDIRECTS)) {
      expect(target).not.toBe("/dashboard");
    }
  });
});
