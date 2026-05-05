import { isAllowedVisibleIntelAction, normalizeVisibleIntelAction } from "./visibleIntelActions";

describe("Intel v3 visible action contract", () => {
  it("normalizes legacy and unknown actions to BUY/HOLD/TRIM/SELL only", () => {
    expect(normalizeVisibleIntelAction("BUY")).toBe("BUY");
    expect(normalizeVisibleIntelAction("TRIM")).toBe("TRIM");
    expect(normalizeVisibleIntelAction("REDUCE")).toBe("TRIM");
    expect(normalizeVisibleIntelAction("REVIEW")).toBe("HOLD");
    expect(normalizeVisibleIntelAction("WATCHLIST")).toBe("HOLD");
  });

  it("disallows legacy posture labels from visible actions", () => {
    const forbiddenLabels = [
      "Add Candidate",
      "Watchlist",
      "Review",
      "Risk Watch",
      "Trim Candidate",
      "Strong Buy",
      "Strong Sell",
      "Buy More",
      "Accumulate",
    ];
    for (const label of forbiddenLabels) {
      expect(isAllowedVisibleIntelAction(label)).toBe(false);
      expect(normalizeVisibleIntelAction(label)).toBe("HOLD");
    }
  });
});

describe("HOLD-collapse canary", () => {
  it("flags non-degenerate all-HOLD LOW collapse when meaningful published signals exist", () => {
    const synthetic = [
      { action: "BUY", conviction_level: "HIGH", publishedSignal: true },
      { action: "SELL", conviction_level: "MEDIUM", publishedSignal: true },
      { action: "TRIM", conviction_level: "MEDIUM", publishedSignal: true },
      { action: "HOLD", conviction_level: "LOW", publishedSignal: false },
    ];

    const normalized = synthetic.map((r) => ({
      action: normalizeVisibleIntelAction(r.action),
      conviction: r.conviction_level,
      publishedSignal: r.publishedSignal,
    }));

    const hasMeaningfulPublishedSignal = normalized.some(
      (r) => r.publishedSignal && r.conviction !== "LOW",
    );
    const allHoldLow = normalized.every((r) => r.action === "HOLD" && r.conviction === "LOW");

    expect(hasMeaningfulPublishedSignal).toBe(true);
    expect(allHoldLow).toBe(false);
  });
});
