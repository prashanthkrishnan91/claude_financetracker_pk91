import { convictionBadgeLabel, formatCategoryLine } from "./AgentInsightCard";


describe("AgentInsightCard rendering contracts", () => {
  it("collapses duplicate category/subcategory labels", () => {
    expect(formatCategoryLine("Core", "Core")).toBe("Core");
    expect(formatCategoryLine("ETF", "ETF")).toBe("ETF");
  });

  it("renders both labels once when category and subcategory differ", () => {
    expect(formatCategoryLine("Core", "Technology")).toBe("Core · Technology");
  });

  it("handles missing category/subcategory safely", () => {
    expect(formatCategoryLine("Core", null)).toBe("Core");
    expect(formatCategoryLine("", "Technology")).toBe("Technology");
  });
});


describe("conviction copy", () => {
  it("does not use LOW CONVICTION wording", () => {
    expect(convictionBadgeLabel("LOW")).toBe("Evidence limited");
    expect(convictionBadgeLabel("LOW").toUpperCase()).not.toContain("LOW CONVICTION");
  });
});
