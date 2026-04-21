import { NextResponse } from "next/server";

const ROTATION_ORDER = [
  "GOOGL", "META", "AAPL", "MSFT", "NFLX", "CRM",
  "AMD", "BRK-B", "COST", "WMT", "XLE", "VGT",
];

const BREAKDOWN: Record<string, number> = {
  NVDA: 0.28,
  VOO: 0.22,
  VYM: 0.17,
  QQQ: 0.17,
  ROTATING: 0.16,
};

const STRATEGY_MODE = "balanced";

interface InsightSlim {
  ticker: string;
  conviction_score: number | null;
  suggested_action: string | null;
  investment_thesis: string | null;
}

function nextBiweeklyFriday(from: Date): Date {
  const d = new Date(from);
  const dow = d.getDay();
  const daysAhead = (5 - dow + 7) % 7;
  if (daysAhead === 0) {
    d.setDate(d.getDate() + 7 + 14);
  } else {
    d.setDate(d.getDate() + daysAhead + 7);
  }
  return d;
}

function toISODate(d: Date): string {
  return d.toISOString().split("T")[0];
}

function normalizeToTotal(
  actions: Array<{ symbol: string; amount: number; delta_weight: number; deposit_date: string }>,
  total: number
) {
  const sum = actions.reduce((s, a) => s + a.amount, 0);
  if (sum === 0) return actions;
  return actions.map((a) => ({
    ...a,
    amount: Math.round((a.amount / sum) * total * 100) / 100,
    delta_weight: a.amount / sum,
  }));
}

async function fetchLatestInsights(authHeader: string): Promise<InsightSlim[]> {
  const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
  if (!apiBase) return [];
  try {
    const res = await fetch(`${apiBase}/api/v1/recommendations/insights/latest`, {
      headers: { Authorization: authHeader, "Content-Type": "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function calculatePositionSize(confidenceScore: number, portfolioBalance: number): { amount: number; pct: number } {
  const pct = confidenceScore > 0.8 ? 0.05 : confidenceScore >= 0.5 ? 0.02 : 0.01;
  return { amount: Math.round(portfolioBalance * pct * 100) / 100, pct: pct * 100 };
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const depositAmount = Math.max(0, Number(searchParams.get("cash_to_invest") || 0));
  const portfolioBalance = Math.max(0, Number(searchParams.get("portfolio_balance") || 0));

  // Attempt to load AI insights
  const authHeader = req.headers.get("Authorization") ?? "";
  const insights = authHeader ? await fetchLatestInsights(authHeader) : [];
  const aiDriven = insights.length > 0;

  // Build conviction map: ticker → { score, action, thesis }
  const convictionMap: Record<string, { score: number; action: string; thesis: string }> = {};
  for (const ins of insights) {
    convictionMap[ins.ticker] = {
      score: ins.conviction_score ?? 0,
      action: ins.suggested_action ?? "HOLD",
      thesis: ins.investment_thesis ?? "",
    };
  }

  // Rotating pick — highest-conviction BUY from rotation list, else fall back to GOOGL
  let rotatingPick: string;
  if (aiDriven) {
    const buyRotators = ROTATION_ORDER
      .filter((t) => convictionMap[t]?.action === "BUY")
      .sort((a, b) => (convictionMap[b]?.score ?? 0) - (convictionMap[a]?.score ?? 0));
    rotatingPick = buyRotators[0] ?? ROTATION_ORDER[0];
  } else {
    rotatingPick = ROTATION_ORDER[0]; // GOOGL until executedCount is wired to DB
  }

  const pctMap: Record<string, number> = Object.fromEntries(
    Object.entries(BREAKDOWN).map(([sym, pct]) => [
      sym === "ROTATING" ? rotatingPick : sym,
      pct,
    ])
  );

  const depositDate = toISODate(nextBiweeklyFriday(new Date()));
  const generatedAt = new Date().toISOString();

  // Layer 1: Original — pure percentage breakdown
  const originalActions = Object.entries(pctMap).map(([symbol, pct]) => ({
    symbol,
    amount: Math.round(depositAmount * pct * 100) / 100,
    delta_weight: pct,
    deposit_date: depositDate,
  }));

  // Layer 2: AI conviction-weighted or rule-based personalization
  let adjustedActions;
  if (aiDriven) {
    const raw = originalActions.map((a) => {
      const c = convictionMap[a.symbol];
      if (!c) return a;
      // conviction +1.0 → +50% boost, -1.0 → −50% reduction, clamped ±50%
      const multiplier = 1 + Math.max(-0.5, Math.min(0.5, c.score * 0.5));
      return { ...a, amount: a.amount * multiplier };
    });
    adjustedActions = normalizeToTotal(raw, depositAmount);
  } else {
    const GROWTH_SYMBOLS = new Set(["NVDA", "QQQ"]);
    const INCOME_SYMBOLS = new Set(["VYM"]);
    const rawPersonalized = originalActions.map((a) => ({
      ...a,
      amount: GROWTH_SYMBOLS.has(a.symbol)
        ? a.amount * 1.08
        : INCOME_SYMBOLS.has(a.symbol)
        ? a.amount * 0.90
        : a.amount,
    }));
    adjustedActions = normalizeToTotal(rawPersonalized, depositAmount);
  }

  const strategyLabel = STRATEGY_MODE.charAt(0).toUpperCase() + STRATEGY_MODE.slice(1);

  const totalDeployed = Math.round(
    adjustedActions.reduce((s, a) => s + a.amount, 0) * 100
  ) / 100;
  const fullyAllocated = Math.abs(totalDeployed - depositAmount) < 1;

  const recommendations = adjustedActions.map((a) => {
    const insight = convictionMap[a.symbol];
    const confidence = insight
      ? Math.min(99, Math.max(1, Math.round(75 + insight.score * 24)))
      : Math.min(99, Math.round(60 + a.delta_weight * 130));
    const rationale =
      insight?.thesis ||
      `Allocate ${(a.delta_weight * 100).toFixed(0)}% of deposit ($${a.amount.toFixed(2)}) into ${a.symbol} per ${strategyLabel} strategy.`;

    const confidenceNorm = confidence / 100;
    const posSize = portfolioBalance > 0 ? calculatePositionSize(confidenceNorm, portfolioBalance) : null;

    return {
      symbol: a.symbol,
      action: "BUY",
      amount: a.amount,
      target_weight: Math.round(a.delta_weight * 1000) / 10,
      rationale,
      confidence,
      deposit_date: a.deposit_date,
      position_size_amount: posSize?.amount ?? null,
      position_size_pct: posSize?.pct ?? null,
    };
  });

  return NextResponse.json({
    plan: {
      total_amount: depositAmount,
      strategy: aiDriven
        ? `${strategyLabel} — AI conviction-weighted allocation`
        : `${strategyLabel} — Drift + signal weighted allocation`,
      generated_at: generatedAt,
    },
    recommendations,
    summary: {
      positions_count: recommendations.length,
      total_deployed: totalDeployed,
      fully_allocated: fullyAllocated,
      strategy_mode: STRATEGY_MODE,
      rotating_pick: rotatingPick,
    },
    debug: {
      original_plan: { actions: originalActions },
      personalized_plan: { actions: adjustedActions },
      signals: {
        ai_driven: aiDriven,
        insights_count: insights.length,
        rotating_pick: rotatingPick,
        growth_bias: aiDriven ? [] : ["NVDA", "QQQ"],
        income_trim: aiDriven ? [] : ["VYM"],
      },
    },
  });
}
