import { computePortfolioSynthesisFromCards } from "./portfolioSynthesisRuntime";

describe("computePortfolioSynthesisFromCards", () => {
  it("builds investment-useful buckets instead of Core/ETF/Other summary", () => {
    const synthesis = computePortfolioSynthesisFromCards([
      { id: "1", ticker: "VOO", name: "VOO", action: "HOLD", detail: "", rationale: "", urgency: 1, color: "", tax_note: "", drip_note: "", category: "ETF", sector: "ETFs / Broad Market" },
      { id: "2", ticker: "NVDA", name: "NVDA", action: "BUY", detail: "", rationale: "", urgency: 1, color: "", tax_note: "", drip_note: "", category: "Core", sector: "Technology", analyst_confidence: 0.8 },
      { id: "3", ticker: "BTC", name: "BTC", action: "TRIM", detail: "", rationale: "", urgency: 1, color: "", tax_note: "", drip_note: "", category: "Crypto", sector: "Crypto" },
    ]);

    expect(synthesis).toBeTruthy();
    expect(synthesis?.headline?.toLowerCase()).toContain("nvda");
    expect(synthesis?.headline?.toLowerCase()).not.toContain("core ~");
    expect(synthesis?.exposures?.strategy_buckets?.length).toBeGreaterThan(0);
  });

  it("does not crash when optional fields are missing", () => {
    const synthesis = computePortfolioSynthesisFromCards([
      { id: "1", ticker: "MSFT", name: "MSFT", action: "BUY", detail: "", rationale: "", urgency: 1, color: "", tax_note: "", drip_note: "", category: "Core" },
      { id: "2", ticker: "RIVN", name: "RIVN", action: "TRIM", detail: "", rationale: "", urgency: 1, color: "", tax_note: "", drip_note: "", category: "Core" },
    ]);

    expect(synthesis).toBeTruthy();
    expect(synthesis?.summary).toBeTruthy();
    expect(synthesis?.top_opportunities).toBeDefined();
  });
});
