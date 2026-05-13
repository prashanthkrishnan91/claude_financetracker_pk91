/**
 * Behavioral contract tests for api.deployV3.getPlan URL construction.
 *
 * These tests call the real getPlan function and intercept fetch to assert
 * the actual URL built — not string-inspection of source code.
 *
 * Invariants verified:
 *   getPlan(900)       → GET /api/v1/deploy/v3/plan?cash_to_deploy=900
 *   getPlan(0)         → GET /api/v1/deploy/v3/plan  (no query string)
 *   getPlan(undefined) → GET /api/v1/deploy/v3/plan  (no query string)
 *   getPlan()          → GET /api/v1/deploy/v3/plan  (no query string)
 *   getPlan(1)         → GET /api/v1/deploy/v3/plan?cash_to_deploy=1
 */

// Mock Supabase before importing api — jest.mock() calls are hoisted.
jest.mock("@/lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: jest.fn().mockResolvedValue({ data: { session: null } }),
    },
  },
}));

import { api } from "@/lib/api";

// ── Helpers ────────────────────────────────────────────────────────────────────

function makeMockFetch(): { capturedUrl: () => string; mock: jest.Mock } {
  let _url = "";
  const mock = jest.fn().mockImplementation((url: string) => {
    _url = url;
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({}),
    } as unknown as Response);
  });
  return { capturedUrl: () => _url, mock };
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe("api.deployV3.getPlan — URL construction (behavioral)", () => {
  let capturedUrl: () => string;

  beforeEach(() => {
    const { capturedUrl: getUrl, mock } = makeMockFetch();
    capturedUrl = getUrl;
    (global as unknown as { fetch: jest.Mock }).fetch = mock;
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("getPlan(900) fetches /api/v1/deploy/v3/plan?cash_to_deploy=900", async () => {
    await api.deployV3.getPlan(900);
    expect(capturedUrl()).toBe("/api/v1/deploy/v3/plan?cash_to_deploy=900");
  });

  it("getPlan(0) fetches base endpoint — no cash_to_deploy param", async () => {
    await api.deployV3.getPlan(0);
    expect(capturedUrl()).toBe("/api/v1/deploy/v3/plan");
    expect(capturedUrl()).not.toContain("cash_to_deploy");
  });

  it("getPlan(undefined) fetches base endpoint — no cash_to_deploy param", async () => {
    await api.deployV3.getPlan(undefined);
    expect(capturedUrl()).toBe("/api/v1/deploy/v3/plan");
    expect(capturedUrl()).not.toContain("cash_to_deploy");
  });

  it("getPlan() (no arg) fetches base endpoint — no cash_to_deploy param", async () => {
    await api.deployV3.getPlan();
    expect(capturedUrl()).toBe("/api/v1/deploy/v3/plan");
    expect(capturedUrl()).not.toContain("cash_to_deploy");
  });

  it("getPlan(1) fetches /api/v1/deploy/v3/plan?cash_to_deploy=1", async () => {
    await api.deployV3.getPlan(1);
    expect(capturedUrl()).toBe("/api/v1/deploy/v3/plan?cash_to_deploy=1");
  });

  it("getPlan(900) URL contains no legacy endpoint segments", async () => {
    await api.deployV3.getPlan(900);
    const url = capturedUrl();
    expect(url).not.toContain("/api/deposit-plan");
    expect(url).not.toContain("/api/v1/allocation/plan");
    expect(url).not.toContain("/api/v1/portfolio/rebalance");
  });
});
