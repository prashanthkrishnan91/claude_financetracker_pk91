import type { InsightCardData, PortfolioSynthesisPayload } from "@/lib/api";

const TICKER_SECTOR_MAP: Record<string, string> = {
  AAPL: "Technology", MSFT: "Technology", NVDA: "Technology", AMD: "Technology", CRM: "Technology", SNOW: "Technology",
  GOOGL: "Communication", META: "Communication", NFLX: "Communication", RDDT: "Communication",
  COST: "Consumer", WMT: "Consumer", CAVA: "Consumer",
  QCOM: "Semis", TSM: "Semis",
  "BRK-B": "Financial",
  ALK: "Industrial/Auto", RIVN: "Industrial/Auto", BMWYY: "Industrial/Auto",
  VOO: "Broad Market", VTI: "Broad Market", SPY: "Broad Market", QQQ: "Growth", SCHD: "Dividend", VYM: "Dividend",
  VXUS: "International", VEA: "International", VWO: "International", BND: "Bonds",
  GLD: "Gold", BTC: "Crypto", XRP: "Crypto",
  KLAR: "Speculative", BLSH: "Speculative", STUB: "Speculative",
};

export function mapTickerToSector(ticker?: string | null): string {
  if (!ticker) return "Unknown";
  return TICKER_SECTOR_MAP[ticker.toUpperCase()] || "Unknown";
}

export function computePortfolioSynthesisFromCards(cards: InsightCardData[] | null | undefined): PortfolioSynthesisPayload | null {
  const roster = cards ?? [];
  if (roster.length === 0) return null;

  const counts: Record<string, number> = { BUY: 0, HOLD: 0, TRIM: 0, SELL: 0 };
  const sectorCounts: Record<string, number> = {};
  const buySectors: Record<string, number> = {};

  for (const card of roster) {
    const action = card.action === "REDUCE" ? "TRIM" : (card.action || "HOLD");
    if (counts[action] !== undefined) counts[action] += 1;
    const sector = (card.sector || card.category || mapTickerToSector(card.ticker) || "Unknown").trim() || "Unknown";
    sectorCounts[sector] = (sectorCounts[sector] || 0) + 1;
    if (action === "BUY") buySectors[sector] = (buySectors[sector] || 0) + 1;
  }

  const total = roster.length;
  const enriched = roster.filter((c) => (c.analysis_source ?? "") === "live_llm").length;
  const highQuality = roster.filter((c) => (c.data_quality_label ?? "").toUpperCase() === "HIGH" && !c.analyst_used_fallback).length;
  const fallback = roster.filter((c) => c.analyst_used_fallback || c.analysis_source === "deterministic_fallback").length;
  const ratio = enriched / total;
  const quality = ratio >= 0.8 ? "HIGH" : ratio >= 0.5 ? "MEDIUM" : "LOW";

  const topSectors = Object.entries(sectorCounts).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([sector]) => sector);
  const sectorAllocation = Object.fromEntries(
    Object.entries(sectorCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([sector, count]) => [sector, Number(((count / total) * 100).toFixed(1))])
  );
  const concentration = topSectors.map((s) => `- ${s} (~${Math.round(sectorAllocation[s])}%)`).join("\n") || "- No positions";
  const buyFocus = Object.entries(buySectors).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([s]) => s).join(", ") || "No active BUY signals";
  const riskSector = topSectors[0] || "None";

  return {
    portfolio_bias: "neutral",
    key_themes: topSectors.map((s) => `${s} concentration`),
    risk_concentrations: [riskSector !== "None" ? `${riskSector} is the largest concentration.` : "No concentration risk identified."],
    overexposure_flags: [],
    rebalancing_suggestions: [],
    summary: `Portfolio is primarily concentrated in:\n${concentration}\nRisk is concentrated in ${riskSector}. Current buys are focused on: ${buyFocus}.`,
    quality,
    aggregate_quality: quality,
    top_sectors: topSectors,
    sector_allocation: sectorAllocation,
    counts,
    quality_breakdown: {
      total_cards: total,
      enriched,
      high_quality: highQuality,
      fallback,
    },
  };
}
