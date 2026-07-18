/**
 * Watchlist pure-logic tests.
 * Covers: payload validation (ticker/threshold/notes), backend error-message
 * extraction (409 duplicate, 503 migration, string detail), criterion label
 * formatting, and tri-state criteria status.
 */

// watchlist.ts imports ./supabase (which requires env vars at module load) —
// mock it so pure helpers are testable in a node environment. jest.mock is
// hoisted above the import.
jest.mock("./supabase", () => ({
  supabase: { auth: { getSession: jest.fn() } },
}));

import {
  WatchlistApiError,
  extractApiErrorInfo,
  isMigrationRequiredError,
  isDuplicateEntryError,
  normalizeTicker,
  validateWatchlistInput,
  criteriaTypeLabel,
  formatCriteriaSentence,
  criteriaStatus,
  criteriaStatusLabel,
} from "./watchlist";

// ── Validation ────────────────────────────────────────────────────────────────

describe("validateWatchlistInput", () => {
  it("accepts a valid entry", () => {
    expect(
      validateWatchlistInput({ ticker: "vti", threshold: "215.5", notes: "dip buy" })
    ).toEqual({});
  });

  it("accepts dotted and dashed tickers like the backend", () => {
    expect(validateWatchlistInput({ ticker: "BRK.B", threshold: 100 })).toEqual({});
    expect(validateWatchlistInput({ ticker: "BTC-USD", threshold: 50000 })).toEqual({});
  });

  it("rejects empty and malformed tickers", () => {
    expect(validateWatchlistInput({ ticker: "", threshold: 10 }).ticker).toBeDefined();
    expect(validateWatchlistInput({ ticker: "   ", threshold: 10 }).ticker).toBeDefined();
    expect(validateWatchlistInput({ ticker: "TOOLONGTICKER", threshold: 10 }).ticker).toBeDefined();
    expect(validateWatchlistInput({ ticker: "A..B", threshold: 10 }).ticker).toBeDefined();
    expect(validateWatchlistInput({ ticker: "A B", threshold: 10 }).ticker).toBeDefined();
  });

  it("rejects missing, non-numeric, non-positive and oversized thresholds", () => {
    expect(validateWatchlistInput({ ticker: "VTI", threshold: "" }).threshold).toBeDefined();
    expect(validateWatchlistInput({ ticker: "VTI", threshold: "abc" }).threshold).toBeDefined();
    expect(validateWatchlistInput({ ticker: "VTI", threshold: 0 }).threshold).toBeDefined();
    expect(validateWatchlistInput({ ticker: "VTI", threshold: -5 }).threshold).toBeDefined();
    expect(
      validateWatchlistInput({ ticker: "VTI", threshold: 10_000_001 }).threshold
    ).toBeDefined();
  });

  it("rejects notes longer than 500 characters", () => {
    expect(
      validateWatchlistInput({ ticker: "VTI", threshold: 10, notes: "x".repeat(501) }).notes
    ).toBeDefined();
    expect(
      validateWatchlistInput({ ticker: "VTI", threshold: 10, notes: "x".repeat(500) }).notes
    ).toBeUndefined();
  });
});

describe("normalizeTicker", () => {
  it("trims and uppercases", () => {
    expect(normalizeTicker("  vti ")).toBe("VTI");
    expect(normalizeTicker("brk.b")).toBe("BRK.B");
    expect(normalizeTicker("")).toBe("");
  });
});

// ── Error extraction ──────────────────────────────────────────────────────────

describe("extractApiErrorInfo", () => {
  it("extracts the 409 duplicate detail object verbatim", () => {
    const info = extractApiErrorInfo(
      {
        detail: {
          error: "duplicate_watchlist_entry",
          message: "VTI already has a price below entry. Edit that entry instead of adding a duplicate.",
        },
      },
      409
    );
    expect(info.code).toBe("duplicate_watchlist_entry");
    expect(info.message).toContain("Edit that entry");
  });

  it("extracts the 503 migration detail object verbatim", () => {
    const info = extractApiErrorInfo(
      {
        detail: {
          error: "watchlist_migration_required",
          message: "The Watchlist table has not been created yet. Apply v2/database/025_watchlist.sql in the Supabase SQL editor, then retry.",
        },
      },
      503
    );
    expect(info.code).toBe("watchlist_migration_required");
    expect(info.message).toContain("025_watchlist.sql");
  });

  it("handles plain-string detail", () => {
    const info = extractApiErrorInfo({ detail: "Watchlist entry not found" }, 404);
    expect(info.code).toBeNull();
    expect(info.message).toBe("Watchlist entry not found");
  });

  it("never yields [object Object] for malformed bodies", () => {
    for (const body of [null, {}, { detail: {} }, { detail: 42 }, "boom"]) {
      const info = extractApiErrorInfo(body, 500);
      expect(info.message).toBe("Request failed (HTTP 500).");
      expect(info.message).not.toContain("[object");
    }
  });
});

describe("error classifiers", () => {
  it("classifies migration-required errors", () => {
    expect(
      isMigrationRequiredError(new WatchlistApiError(503, "watchlist_migration_required", "m"))
    ).toBe(true);
    expect(isMigrationRequiredError(new WatchlistApiError(409, "duplicate_watchlist_entry", "m"))).toBe(false);
    expect(isMigrationRequiredError(new Error("503"))).toBe(false);
  });

  it("classifies duplicate errors", () => {
    expect(
      isDuplicateEntryError(new WatchlistApiError(409, "duplicate_watchlist_entry", "m"))
    ).toBe(true);
    expect(isDuplicateEntryError(new WatchlistApiError(503, "watchlist_migration_required", "m"))).toBe(false);
    expect(isDuplicateEntryError(null)).toBe(false);
  });
});

// ── Labels & status ───────────────────────────────────────────────────────────

describe("criteria labels", () => {
  it("renders plain-English criterion types", () => {
    expect(criteriaTypeLabel("price_below")).toBe("Price falls below");
    expect(criteriaTypeLabel("price_above")).toBe("Price rises above");
  });

  it("formats the full criterion sentence with currency", () => {
    expect(formatCriteriaSentence("price_below", 215)).toBe("Price falls below $215.00");
    expect(formatCriteriaSentence("price_above", 1234.5)).toBe("Price rises above $1,234.50");
  });
});

describe("criteriaStatus", () => {
  it("maps the tri-state criteria_met", () => {
    expect(criteriaStatus({ criteria_met: true })).toBe("met");
    expect(criteriaStatus({ criteria_met: false })).toBe("not_met");
    expect(criteriaStatus({ criteria_met: null })).toBe("unknown");
  });

  it("labels each state without relying on color", () => {
    expect(criteriaStatusLabel("met")).toBe("Criteria met");
    expect(criteriaStatusLabel("not_met")).toBe("Not met");
    expect(criteriaStatusLabel("unknown")).toBe("Unknown — no trusted current price");
  });
});
