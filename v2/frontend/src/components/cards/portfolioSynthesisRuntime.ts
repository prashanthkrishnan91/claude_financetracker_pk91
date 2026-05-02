import type { InsightCardData, PortfolioSynthesisPayload } from "@/lib/api";

const TICKER_SECTOR_MAP: Record<string, string> = {
  AAPL: "Technology", MSFT: "Technology", NVDA: "Technology", AMD: "Technology", CRM: "Technology", SNOW: "Technology", TSM: "Technology", QCOM: "Technology",
  GOOGL: "Communication Services", META: "Communication Services", NFLX: "Communication Services", RDDT: "Communication Services",
  COST: "Consumer", WMT: "Consumer", CAVA: "Consumer",
  "BRK-B": "Financials",
  ALK: "Industrials / Autos", RIVN: "Industrials / Autos", BMWYY: "Industrials / Autos",
  VOO: "ETFs / Broad Market", VTI: "ETFs / Broad Market", SPY: "ETFs / Broad Market", QQQ: "ETFs / Broad Market", SCHD: "ETFs / Broad Market", VYM: "ETFs / Broad Market", VXUS: "ETFs / Broad Market", VEA: "ETFs / Broad Market", VWO: "ETFs / Broad Market",
  BND: "Gold / Bonds / Defensive", GLD: "Gold / Bonds / Defensive",
  BTC: "Crypto", XRP: "Crypto",
};

const STRATEGY_MAP: Record<string, string> = {
  VOO: "Core index ETFs", VTI: "Core index ETFs", SPY: "Core index ETFs", QQQ: "Core index ETFs",
  AAPL: "Mega-cap quality growth", MSFT: "Mega-cap quality growth", GOOGL: "Mega-cap quality growth", META: "Mega-cap quality growth",
  NVDA: "Semiconductors / AI infrastructure", AMD: "Semiconductors / AI infrastructure", TSM: "Semiconductors / AI infrastructure", QCOM: "Semiconductors / AI infrastructure",
  SCHD: "Dividend income", VYM: "Dividend income",
  VXUS: "International diversification", VEA: "International diversification", VWO: "International diversification",
  BTC: "Crypto / alternatives", XRP: "Crypto / alternatives", GLD: "Crypto / alternatives",
  RIVN: "Speculative / IPO / high volatility", KLAR: "Speculative / IPO / high volatility", BLSH: "Speculative / IPO / high volatility", STUB: "Speculative / IPO / high volatility",
};

export function mapTickerToSector(ticker?: string | null): string {
  if (!ticker) return "Technology";
  return TICKER_SECTOR_MAP[ticker.toUpperCase()] || "Technology";
}

function normalizeAction(action?: string | null): "BUY" | "HOLD" | "TRIM" | "SELL" {
  const raw = (action || "").toUpperCase();
  if (raw === "REDUCE") return "TRIM";
  if (raw === "BUY" || raw === "HOLD" || raw === "TRIM" || raw === "SELL") return raw;
  return "HOLD";
}

function classifyStrategy(card: InsightCardData): string {
  const ticker = (card.ticker || "").toUpperCase();
  if (STRATEGY_MAP[ticker]) return STRATEGY_MAP[ticker];
  if ((card.technical_signal || "").toUpperCase() === "SELL") return "Turnaround or elevated risk";
  return "Mega-cap quality growth";
}

function summarizeReason(card: InsightCardData): string {
  return (
    card.analyst_drivers?.[0]
    || card.key_drivers?.[0]
    || card.plain_language_explanation
    || card.thesis
    || card.reasoning_summary
    || card.detail
    || "Monitor execution and new business evidence."
  );
}

export function computePortfolioSynthesisFromCards(cards: InsightCardData[] | null | undefined): PortfolioSynthesisPayload | null {
  const roster = cards ?? [];
  if (roster.length === 0) return null;

  const total = roster.length;
  const counts: Record<string, number> = { BUY: 0, HOLD: 0, TRIM: 0, SELL: 0 };
  const strategyCounts: Record<string, number> = {};
  const sectorCounts: Record<string, number> = {};
  const riskCounts: Record<string, number> = {};

  const buys: InsightCardData[] = [];
  const trims: InsightCardData[] = [];

  for (const card of roster) {
    const action = normalizeAction(card.action);
    counts[action] += 1;
    const strategy = classifyStrategy(card);
    const sector = card.sector || mapTickerToSector(card.ticker);
    strategyCounts[strategy] = (strategyCounts[strategy] || 0) + 1;
    sectorCounts[sector] = (sectorCounts[sector] || 0) + 1;

    if (action === "BUY") buys.push(card);
    if (action === "TRIM" || action === "SELL") trims.push(card);

    if ((card.technical_signal || "").toUpperCase() === "SELL") {
      riskCounts["Elevated downside risk"] = (riskCounts["Elevated downside risk"] || 0) + 1;
    }
    if (["BTC", "XRP"].includes((card.ticker || "").toUpperCase())) {
      riskCounts["Crypto volatility"] = (riskCounts["Crypto volatility"] || 0) + 1;
    }
    if (["RIVN", "KLAR", "BLSH", "STUB"].includes((card.ticker || "").toUpperCase())) {
      riskCounts["Speculative risk"] = (riskCounts["Speculative risk"] || 0) + 1;
    }
    if ((card.data_quality_label || "").toUpperCase() === "LOW" || card.analyst_used_fallback) {
      riskCounts["Missing fundamental data"] = (riskCounts["Missing fundamental data"] || 0) + 1;
    }
  }

  const topStrategy = Object.entries(strategyCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "mixed allocation";
  const topSector = Object.entries(sectorCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "mixed sectors";
  const topOpportunities = buys.slice(0, 5).map((card) => ({
    ticker: card.ticker,
    reason: summarizeReason(card),
    confidence: Number((card.analyst_confidence ?? card.confidence ?? 0).toFixed(2)),
    risk_note: card.analyst_risks?.[0] || card.main_risks?.[0] || "Use staged entries and size by conviction.",
    suggested_use: (card.analyst_confidence ?? 0) >= 0.65 ? "add" : "watch",
  }));

  const trimCandidates = trims.slice(0, 5).map((card) => ({
    ticker: card.ticker,
    why_trim: card.analyst_risks?.[0] || card.main_risks?.[0] || "Risk-adjusted upside is weaker than top BUY ideas.",
    what_to_watch: card.what_changed?.split("\n").find(Boolean) || "Watch business execution and earnings revision direction.",
    redirect_proceeds_to: topOpportunities.slice(0, 3).map((t) => t.ticker),
  }));

  const enriched = roster.filter((c) => (c.analysis_source ?? "") === "live_llm").length;
  const highQuality = roster.filter((c) => (c.data_quality_label ?? "").toUpperCase() === "HIGH" && !c.analyst_used_fallback).length;
  const fallback = roster.filter((c) => c.analyst_used_fallback || c.analysis_source === "deterministic_fallback").length;
  const ratio = enriched / total;
  const quality = ratio >= 0.8 ? "HIGH" : ratio >= 0.5 ? "MEDIUM" : "LOW";
  const bias = counts.BUY > (counts.TRIM + counts.SELL) ? "bullish" : (counts.TRIM + counts.SELL > counts.BUY ? "defensive" : "neutral");

  const headline = `Your portfolio is ${bias} with concentration in ${topStrategy} and ${topSector}. Top opportunities include ${topOpportunities.slice(0, 3).map((o) => o.ticker).join(", ") || "select names"}, while key risks are in ${trimCandidates.slice(0, 3).map((o) => o.ticker).join(", ") || "higher-volatility holdings"}. Use trim proceeds to fund higher-conviction buys instead of adding to concentrated sleeves.`;

  return {
    portfolio_bias: bias,
    key_themes: Object.entries(strategyCounts).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([name, count]) => `${name} ~${Math.round((count / total) * 100)}%`),
    risk_concentrations: Object.entries(riskCounts).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([name, count]) => `${name} in ${count} holdings`),
    overexposure_flags: Object.entries(strategyCounts).filter(([, count]) => (count / total) >= 0.35).map(([name, count]) => `${name} ~${Math.round((count / total) * 100)}%`),
    rebalancing_suggestions: [
      topOpportunities.length ? `Prioritize staged adds in ${topOpportunities.slice(0, 3).map((o) => o.ticker).join(", ")}.` : "",
      trimCandidates.length ? `Fund buys using trims from ${trimCandidates.slice(0, 3).map((t) => t.ticker).join(", ")}.` : "",
    ].filter(Boolean),
    summary: headline,
    quality,
    aggregate_quality: quality,
    counts,
    action_counts: counts,
    headline,
    executive_summary: `Action mix is ${counts.BUY} Buy / ${counts.HOLD} Hold / ${counts.TRIM} Trim / ${counts.SELL} Sell. Focus new money on best BUY setups and avoid adding to concentrated risk buckets.`,
    exposures: {
      strategy_buckets: Object.entries(strategyCounts).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, percentage: Number(((count / total) * 100).toFixed(1)), top_tickers: roster.filter((c) => classifyStrategy(c) === name).slice(0, 3).map((c) => c.ticker), why_it_matters: "Sizing here drives concentration and diversification outcomes." })),
      sector_buckets: Object.entries(sectorCounts).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, percentage: Number(((count / total) * 100).toFixed(1)), top_tickers: roster.filter((c) => (c.sector || mapTickerToSector(c.ticker)) === name).slice(0, 3).map((c) => c.ticker), why_it_matters: "Sector concentration affects drawdown behavior and correlation." })),
      risk_buckets: Object.entries(riskCounts).sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, percentage: Number(((count / total) * 100).toFixed(1)), top_tickers: roster.filter((c) => ["BTC", "XRP", "RIVN", "KLAR", "BLSH", "STUB"].includes((c.ticker || "").toUpperCase())).slice(0, 3).map((c) => c.ticker), why_it_matters: "Risk clusters indicate where to tighten sizing and monitoring." })),
    },
    top_opportunities: topOpportunities,
    top_risks: Object.entries(riskCounts).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([name]) => ({ label: name, note: "Risk cluster to monitor." })),
    trim_candidates: trimCandidates,
    deploy_suggestions: [
      `Add to ${topOpportunities.slice(0, 3).map((o) => o.ticker).join(", ") || "top BUY names"} in staged tranches.`,
      `Recycle proceeds from ${trimCandidates.slice(0, 3).map((o) => o.ticker).join(", ") || "trim candidates"} toward higher-conviction setups.`,
    ],
    what_changed: roster.filter((c) => c.what_changed).slice(0, 6).map((c) => ({ ticker: c.ticker, change: c.what_changed?.split("\n").find(Boolean) })),
    watchlist: roster.filter((c) => (c.technical_signal || "").toUpperCase() === "SELL" || (c.data_quality_label || "").toUpperCase() === "LOW").slice(0, 6).map((c) => ({ ticker: c.ticker, focus: c.analyst_risks?.[0] || "Recheck the business case and evidence", trigger: c.what_changed?.split("\n").find(Boolean) || "Review after earnings" })),
    top_sectors: Object.keys(sectorCounts).slice(0, 3),
    sector_allocation: Object.fromEntries(Object.entries(sectorCounts).map(([k, v]) => [k, Number(((v / total) * 100).toFixed(1))])),
    quality_breakdown: { total_cards: total, enriched, high_quality: highQuality, fallback },
  } as PortfolioSynthesisPayload;
}
