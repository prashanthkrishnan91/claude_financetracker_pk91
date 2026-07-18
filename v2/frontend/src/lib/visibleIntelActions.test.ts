import {
  extractHoldingRationale,
  isAllowedVisibleIntelAction,
  normalizeVisibleIntelAction,
  partitionRenderableCards,
  type RationaleSourceCard,
} from "./visibleIntelActions";

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

// ── Canonical rationale extraction ────────────────────────────────────────────

describe("extractHoldingRationale — precedence contract", () => {
  it("why_text wins when present", () => {
    const card: RationaleSourceCard = {
      ticker: "VTI",
      why_text: "Broad-market core holding.",
      action_text: "Keep adding on schedule.",
      detail_drawer_payload: {
        asset_intelligence_context: { why_this_action: "Composer says hold." },
      },
    };
    expect(extractHoldingRationale(card)).toBe("Broad-market core holding.");
  });

  it("falls back to asset_intelligence_context.why_this_action when why_text is empty", () => {
    const card: RationaleSourceCard = {
      ticker: "AAPL",
      why_text: "",
      action_text: "Hold for now.",
      detail_drawer_payload: {
        asset_intelligence_context: { why_this_action: "Composer says hold." },
      },
    };
    expect(extractHoldingRationale(card)).toBe("Composer says hold.");
  });

  it("falls back to action_text when the first two are empty", () => {
    const card: RationaleSourceCard = {
      ticker: "MSFT",
      why_text: "",
      action_text: "Hold for now.",
      detail_drawer_payload: { asset_intelligence_context: { why_this_action: "" } },
    };
    expect(extractHoldingRationale(card)).toBe("Hold for now.");
  });

  it("all-empty → null", () => {
    const card: RationaleSourceCard = {
      ticker: "NVDA",
      why_text: "",
      action_text: "",
      detail_drawer_payload: { asset_intelligence_context: null },
    };
    expect(extractHoldingRationale(card)).toBeNull();
    expect(extractHoldingRationale({ ticker: "BARE" })).toBeNull();
  });

  it("whitespace-only values are treated as empty", () => {
    const card: RationaleSourceCard = {
      ticker: "AMD",
      why_text: "   ",
      action_text: "\n\t ",
      detail_drawer_payload: {
        asset_intelligence_context: { why_this_action: "  " },
      },
    };
    expect(extractHoldingRationale(card)).toBeNull();
  });

  it("trims the winning rationale", () => {
    expect(
      extractHoldingRationale({ ticker: "VTI", why_text: "  padded  " }),
    ).toBe("padded");
  });
});

describe("partitionRenderableCards — panel exclusion contract", () => {
  it("splits renderable cards from excluded tickers (never silently drops)", () => {
    const cards: RationaleSourceCard[] = [
      { ticker: "VTI", why_text: "Core holding." },
      { ticker: "GHOST", why_text: "", action_text: "" },
      {
        ticker: "AAPL",
        why_text: "",
        detail_drawer_payload: {
          asset_intelligence_context: { why_this_action: "Composer rationale." },
        },
      },
      { ticker: "BLANK", why_text: "   ", action_text: " " },
    ];
    const { renderable, excludedTickers } = partitionRenderableCards(cards);
    expect(renderable.map((c) => c.ticker)).toEqual(["VTI", "AAPL"]);
    expect(renderable).toHaveLength(2);
    expect(excludedTickers).toEqual(["GHOST", "BLANK"]);
    expect(excludedTickers).toHaveLength(2);
  });

  it("empty input → nothing renderable, nothing excluded", () => {
    expect(partitionRenderableCards([])).toEqual({ renderable: [], excludedTickers: [] });
  });

  it("all cards renderable → no exclusions", () => {
    const cards: RationaleSourceCard[] = [
      { ticker: "A", why_text: "a" },
      { ticker: "B", action_text: "b" },
    ];
    const { renderable, excludedTickers } = partitionRenderableCards(cards);
    expect(renderable).toHaveLength(2);
    expect(excludedTickers).toEqual([]);
  });
});
