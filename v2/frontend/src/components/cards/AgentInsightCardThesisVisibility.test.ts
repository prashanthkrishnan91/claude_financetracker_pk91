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
});
