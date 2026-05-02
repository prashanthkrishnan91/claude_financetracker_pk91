import React from "react";
import { render, screen } from "@testing-library/react";
import { AgentInsightCard } from "@/components/cards/AgentInsightCard";
import type { InsightCardData } from "@/lib/api";

describe("AgentInsightCard Business read visibility", () => {
  const baseCard: InsightCardData = {
    id: "rec_1",
    ticker: "NVDA",
    action: "BUY",
    analyst_action: "BUY",
    confidence: 0.8,
    conviction: 0.8,
    conviction_level: "HIGH",
    amount: 1000,
    thesis: null,
    summary: null,
    risk: null,
    category: "Core",
    current_price: 900,
    target_price: null,
    upside_pct: null,
    momentum_30d: null,
    pe_ratio: null,
    analyst_score: null,
    score: null,
    analyst_conviction: 0.8,
    reasoning_schema_version: "human_v2",
    primary_driver: "Demand for AI compute remains strong.",
    risk_flag: "Valuation drawdown risk if growth decelerates.",
    action_reason: "Add on weakness while respecting sizing rules.",
    differentiation: "Alternative view: near-term volatility may persist.",
    thesis_plain_english: {
      headline: "Not enough data for a reliable investment-case read",
      quality_label: "Business quality data is incomplete",
      valuation_label: "Valuation data is incomplete",
      risk_label: "Risk data is incomplete",
      momentum_label: "Momentum data is incomplete",
      data_label: "Data is still incomplete",
      caveats: ["Use this as a directional read, not a final answer"],
    },
  } as InsightCardData;

  it("renders WHY/RISK/ACTION/ALT VIEW while hiding Business read even when thesis_plain_english exists", () => {
    render(<AgentInsightCard card={baseCard} />);

    expect(screen.getByText("WHY")).toBeInTheDocument();
    expect(screen.getByText("RISK")).toBeInTheDocument();
    expect(screen.getByText("ACTION")).toBeInTheDocument();
    expect(screen.getByText("ALT VIEW")).toBeInTheDocument();

    expect(screen.queryByText("Business read")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Not enough data for a reliable investment-case read")
    ).not.toBeInTheDocument();
  });

  it("does not expose raw thesis_v2 metric keys in rendered UI", () => {
    render(<AgentInsightCard card={baseCard} />);

    expect(screen.queryByText(/fcf_margin/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/roic_ttm/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/ev_ebitda/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/ps_ttm/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/net_debt_to_ebitda/i)).not.toBeInTheDocument();
  });
});
