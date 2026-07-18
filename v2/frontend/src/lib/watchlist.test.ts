import {
  criteriaTypeLabel,
  criteriaStatus,
  criteriaStatusLabel,
} from "./watchlist";

describe("criteriaTypeLabel — plain English, no raw keys", () => {
  it("price_below → Price at or below", () => {
    expect(criteriaTypeLabel("price_below")).toBe("Price at or below");
  });

  it("price_above → Price at or above", () => {
    expect(criteriaTypeLabel("price_above")).toBe("Price at or above");
  });

  it("unknown type falls back to a generic label, never the raw key", () => {
    const label = criteriaTypeLabel("some_future_type");
    expect(label).toBe("Price criteria");
    expect(label).not.toContain("_");
  });
});

describe("criteriaStatus — honest tri-state", () => {
  it("true → met", () => {
    expect(criteriaStatus({ criteria_met: true })).toBe("met");
  });

  it("false → not_met", () => {
    expect(criteriaStatus({ criteria_met: false })).toBe("not_met");
  });

  it("null → unknown (missing price data is never fabricated)", () => {
    expect(criteriaStatus({ criteria_met: null })).toBe("unknown");
  });
});

describe("criteriaStatusLabel", () => {
  it("maps every status to plain English", () => {
    expect(criteriaStatusLabel("met")).toBe("Criteria met");
    expect(criteriaStatusLabel("not_met")).toBe("Not met yet");
    expect(criteriaStatusLabel("unknown")).toBe("No price data yet");
  });
});
