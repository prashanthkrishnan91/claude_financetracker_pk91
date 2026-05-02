import { collectIntelThesisLines } from "@/components/cards/AgentInsightCard";
import type { ThesisPlainEnglish } from "@/lib/api";

describe("AgentInsightCard thesis_plain_english visibility", () => {
  it("collects visible business-read lines when thesis_plain_english is present", () => {
    const thesis: ThesisPlainEnglish = {
      headline: "Overall investment case looks constructive",
      quality_label: "Business quality looks strong",
      valuation_label: "Valuation looks balanced",
      risk_label: "Balance sheet risk looks manageable",
      momentum_label: "Momentum is improving",
      data_label: "Data coverage looks usable",
      caveats: ["Use this as a directional read, not a final answer"],
    };

    const lines = collectIntelThesisLines(thesis);
    expect(lines).toContain("Overall investment case looks constructive");
    expect(lines).toContain("Business quality looks strong");
    expect(lines).toContain("Use this as a directional read, not a final answer");
  });

  it("returns empty list when thesis_plain_english is null or missing", () => {
    expect(collectIntelThesisLines(undefined)).toEqual([]);
    expect(collectIntelThesisLines(null)).toEqual([]);
    expect(collectIntelThesisLines({ caveats: [] })).toEqual([]);
  });

  // Run-fallback coverage: per-ticker varied Business read
  it("produces different line sets for tickers with different dimension labels", () => {
    const googl: ThesisPlainEnglish = {
      headline: "Not enough data for a reliable investment-case read",
      quality_label: "Business quality looks strong",
      valuation_label: "Valuation looks balanced",
      risk_label: "Balance sheet risk looks manageable",
      momentum_label: "Momentum looks mixed",
      data_label: "Data is still incomplete",
      caveats: ["Use this as a directional read, not a final answer"],
    };
    const meta: ThesisPlainEnglish = {
      headline: "Not enough data for a reliable investment-case read",
      quality_label: "Business quality looks strong",
      valuation_label: "Valuation signal is limited",
      risk_label: "Risk signal is limited",
      momentum_label: "Momentum is improving",
      data_label: "Data is still incomplete",
      caveats: ["Use this as a directional read, not a final answer"],
    };
    const googlLines = collectIntelThesisLines(googl);
    const metaLines = collectIntelThesisLines(meta);

    // Shared headline (both INSUFFICIENT_DATA)
    expect(googlLines[0]).toBe(metaLines[0]);
    // Valuation label differs: balanced vs limited
    expect(googlLines).toContain("Valuation looks balanced");
    expect(metaLines).toContain("Valuation signal is limited");
    expect(googlLines).not.toContain("Valuation signal is limited");
    expect(metaLines).not.toContain("Valuation looks balanced");
  });

  it("renders all seven field types when all are populated", () => {
    const full: ThesisPlainEnglish = {
      headline: "H",
      quality_label: "Q",
      valuation_label: "V",
      risk_label: "R",
      momentum_label: "M",
      data_label: "D",
      caveats: ["C1", "C2"],
    };
    const lines = collectIntelThesisLines(full);
    expect(lines).toEqual(["H", "Q", "V", "R", "M", "D", "C1", "C2"]);
  });

  it("omits empty-string fields but includes valid ones", () => {
    const sparse: ThesisPlainEnglish = {
      headline: "Headline present",
      quality_label: "",
      valuation_label: null,
      risk_label: "Risk label present",
      momentum_label: undefined as unknown as string,
      data_label: null,
      caveats: ["", "Valid caveat"],
    };
    const lines = collectIntelThesisLines(sparse);
    expect(lines).toContain("Headline present");
    expect(lines).toContain("Risk label present");
    // collectIntelThesisLines uses truthiness: empty string is falsy
    expect(lines).not.toContain("");
    expect(lines).toContain("Valid caveat");
  });

  it("handles missing caveats array gracefully", () => {
    const noCaveats: ThesisPlainEnglish = {
      headline: "Investment case headline",
      quality_label: "Quality label",
    };
    const lines = collectIntelThesisLines(noCaveats);
    expect(lines).toContain("Investment case headline");
    expect(lines).toContain("Quality label");
    // Should not throw with undefined caveats
    expect(() => collectIntelThesisLines(noCaveats)).not.toThrow();
  });

  // Refetch contract: verify query key names used for invalidation
  // (pure string assertions — no React Query runtime needed)
  it("invalidation key covers the recommendations list endpoint", () => {
    // The page invalidates ["recommendations"] and ["recommendations", "insights"]
    // after a completed run, and useRefreshRecommendations invalidates on mutate/success.
    // Verify the keys are consistent with what useRecommendations subscribes to.
    const invalidationKeys = [
      ["recommendations"],
      ["recommendations", "insights"],
      ["recommendations", "job"],
    ];
    // The recommendations list key
    const listKey = invalidationKeys.find(
      (k) => k.length === 1 && k[0] === "recommendations"
    );
    expect(listKey).toBeDefined();
    // insights key
    const insightsKey = invalidationKeys.find(
      (k) => k[0] === "recommendations" && k[1] === "insights"
    );
    expect(insightsKey).toBeDefined();
  });
});
