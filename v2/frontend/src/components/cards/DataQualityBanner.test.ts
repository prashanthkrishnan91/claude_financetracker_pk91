import { derivePortfolioQualityBand } from "./DataQualityBanner";

describe("derivePortfolioQualityBand", () => {
  it("promotes to HIGH when most enriched cards are high quality and fallback is zero", () => {
    const band = derivePortfolioQualityBand({
      decision: {
        mode: "FULL",
        avg_quality: 0.62,
        insufficient_count: 0,
        total_tickers: 3,
        reason: "ok",
        explanation: "ok",
      },
      cost: {
        mode: "FULL",
        total_calls: 3,
        llm_enriched_cards: 3,
        fallback_cards: 0,
        total_cost_usd: 0.01,
        calls_by_kind: {},
        calls_by_model: {},
        entries: [],
      },
      cards: [
        { id: "1", ticker: "A", name: "A", action: "BUY", detail: "", rationale: "", urgency: 1, color: "", tax_note: "", drip_note: "", category: "Tech", data_quality_label: "HIGH", analysis_source: "live_llm", analyst_confidence: 0.8 },
        { id: "2", ticker: "B", name: "B", action: "HOLD", detail: "", rationale: "", urgency: 1, color: "", tax_note: "", drip_note: "", category: "Tech", data_quality_label: "HIGH", analysis_source: "live_llm", analyst_confidence: 0.7 },
        { id: "3", ticker: "C", name: "C", action: "TRIM", detail: "", rationale: "", urgency: 1, color: "", tax_note: "", drip_note: "", category: "Tech", data_quality_label: "MEDIUM", analysis_source: "live_llm", analyst_confidence: 0.55 },
      ],
      synthesis: null,
    });

    expect(band?.label).toBe("HIGH");
  });

  it("keeps baseline band when fallback cards exist", () => {
    const band = derivePortfolioQualityBand({
      decision: {
        mode: "FULL",
        avg_quality: 0.62,
        insufficient_count: 1,
        total_tickers: 3,
        reason: "ok",
        explanation: "ok",
      },
      cost: {
        mode: "FULL",
        total_calls: 3,
        llm_enriched_cards: 3,
        fallback_cards: 1,
        total_cost_usd: 0.01,
        calls_by_kind: {},
        calls_by_model: {},
        entries: [],
      },
      cards: [],
      synthesis: null,
    });

    expect(band?.label).toBe("MEDIUM");
  });

  it("uses backend aggregate quality when synthesis provides it", () => {
    const band = derivePortfolioQualityBand({
      decision: null,
      cost: null,
      cards: [],
      synthesis: {
        portfolio_bias: "neutral",
        key_themes: [],
        risk_concentrations: [],
        overexposure_flags: [],
        rebalancing_suggestions: [],
        summary: "",
        aggregate_quality: "HIGH",
        quality_breakdown: { total_cards: 34, enriched: 34, high_quality: 33, fallback: 0 },
      },
    });

    expect(band?.label).toBe("HIGH");
  });
});
