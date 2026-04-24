import { NextResponse } from "next/server";

type LatestRun = {
  id: string;
  status: string;
  summary?: string | null;
  portfolio_synthesis?: {
    summary?: string;
    key_themes?: string[];
    overexposure_flags?: string[];
  } | null;
};

type InsightSlim = {
  ticker: string;
  conviction_score: number | null;
  suggested_action: string | null;
  investment_thesis: string | null;
  analyst_confidence?: number | null;
  analyst_verdict?: {
    primary_driver?: string | null;
    risk_flag?: string | null;
    action_reason?: string | null;
    differentiation?: string | null;
    generation_version?: string | null;
  } | null;
};

type PositionSlim = {
  ticker: string;
  shares?: number;
  avg_cost?: number;
  current_price?: number;
  market_value?: number;
};

type RecommendationSlim = {
  ticker: string;
  action: string;
  tax_note?: string | null;
  detail?: string | null;
};

async function fetchJson<T>(url: string, authHeader: string): Promise<T | null> {
  try {
    const res = await fetch(url, {
      headers: { Authorization: authHeader, "Content-Type": "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

function safeNumber(v: unknown): number {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const depositAmount = Math.max(0, safeNumber(searchParams.get("cash_to_invest")));
  const portfolioBalance = Math.max(0, safeNumber(searchParams.get("portfolio_balance")));
  const authHeader = req.headers.get("Authorization") ?? "";
  const apiBase = process.env.NEXT_PUBLIC_API_URL || "";

  if (!apiBase || !authHeader) {
    return NextResponse.json({
      plan: {
        total_amount: depositAmount,
        strategy: "No data connection — hold cash",
        generated_at: new Date().toISOString(),
      },
      recommendations: [],
      summary: {
        positions_count: 0,
        total_deployed: 0,
        fully_allocated: false,
        strategy_mode: "conservative",
        ranked_candidates: 0,
      },
      funding: { deposit_amount: depositAmount, sale_proceeds: 0, total_cash: depositAmount },
      trims: [],
      notes: ["Portfolio data unavailable. Deployment plan withheld to avoid fabricated allocations."],
    });
  }

  const [latestRun, insights, positions, recommendations] = await Promise.all([
    fetchJson<LatestRun>(`${apiBase}/api/v1/recommendations/jobs/latest`, authHeader),
    fetchJson<InsightSlim[]>(`${apiBase}/api/v1/recommendations/insights/latest`, authHeader),
    fetchJson<PositionSlim[]>(`${apiBase}/api/v1/positions`, authHeader),
    fetchJson<RecommendationSlim[]>(`${apiBase}/api/v1/recommendations/`, authHeader),
  ]);

  const insightsList = Array.isArray(insights) ? insights : [];
  const posList = Array.isArray(positions) ? positions : [];
  const recList = Array.isArray(recommendations) ? recommendations : [];

  const positionByTicker = new Map<string, PositionSlim>();
  let computedBalance = 0;
  for (const p of posList) {
    const ticker = String(p.ticker || "").toUpperCase();
    if (!ticker) continue;
    positionByTicker.set(ticker, p);
    const mv = safeNumber(p.market_value) || safeNumber(p.shares) * safeNumber(p.current_price || p.avg_cost);
    computedBalance += mv;
  }
  const portfolioTotal = portfolioBalance > 0 ? portfolioBalance : computedBalance;

  const candidateRows = insightsList
    .map((ins) => {
      const ticker = String(ins.ticker || "").toUpperCase();
      const action = String(ins.suggested_action || "HOLD").toUpperCase();
      const conviction = safeNumber(ins.conviction_score);
      const confidence = Math.max(0, Math.min(1, safeNumber(ins.analyst_confidence) || 0.45));
      const position = positionByTicker.get(ticker);
      const marketValue = safeNumber(position?.market_value) || safeNumber(position?.shares) * safeNumber(position?.current_price || position?.avg_cost);
      const weightPct = portfolioTotal > 0 ? (marketValue / portfolioTotal) * 100 : 0;
      const concentrationPenalty = weightPct >= 20 ? 0.35 : weightPct >= 12 ? 0.15 : 0;
      const qualityFloor = conviction >= 0.2 ? 1 : 0.6;
      const score = Math.max(0, conviction) * confidence * (1 - concentrationPenalty) * qualityFloor;
      const verdict = ins.analyst_verdict;
      return {
        ticker,
        action,
        conviction,
        confidence,
        score,
        weightPct,
        thesis: ins.investment_thesis || "",
        why: verdict?.primary_driver || null,
        risk: verdict?.risk_flag || null,
        do: verdict?.action_reason || null,
        alt_view: verdict?.differentiation || null,
        schema_version: verdict?.generation_version || null,
      };
    })
    .filter((r) => r.ticker && r.action === "BUY" && r.score > 0.04)
    .sort((a, b) => b.score - a.score)
    .slice(0, 8);

  const totalScore = candidateRows.reduce((sum, c) => sum + c.score, 0);
  let remaining = depositAmount;
  const recommendationsOut = candidateRows.map((c, idx) => {
    const raw = totalScore > 0 ? (depositAmount * c.score) / totalScore : 0;
    const capped = c.weightPct > 24 ? raw * 0.6 : raw;
    const amount = idx === candidateRows.length - 1 ? Math.max(0, Number(remaining.toFixed(2))) : Math.max(0, Number(capped.toFixed(2)));
    remaining = Math.max(0, remaining - amount);
    return {
      symbol: c.ticker,
      action: "BUY",
      amount,
      target_weight: Number((depositAmount > 0 ? (amount / depositAmount) * 100 : 0).toFixed(1)),
      confidence: Math.round(c.confidence * 100),
      rationale:
        c.thesis ||
        `${c.ticker} ranks highly on conviction-adjusted score (${c.score.toFixed(2)}), with concentration-aware sizing.`,
      portfolio_weight: Number(c.weightPct.toFixed(1)),
      conviction_score: Number(c.conviction.toFixed(2)),
      linked_intel:
        latestRun?.portfolio_synthesis?.key_themes?.[0] ||
        latestRun?.portfolio_synthesis?.summary ||
        "Linked to latest Intel run.",
      why: c.why,
      risk: c.risk,
      do: c.do,
      alt_view: c.alt_view,
      schema_version: c.schema_version,
    };
  });

  const trims = recList
    .filter((r) => ["TRIM", "SELL"].includes(String(r.action || "").toUpperCase()))
    .slice(0, 5)
    .map((r) => ({
      ticker: r.ticker,
      action: String(r.action || "").toUpperCase(),
      tax_note: r.tax_note || "Tax-lot precision unavailable; use conservative trimming.",
      market_note: r.detail || "",
    }));

  const totalDeployed = recommendationsOut.reduce((sum, r) => sum + r.amount, 0);
  const strategy = recommendationsOut.length
    ? "Deploy by conviction × data quality, concentration-aware"
    : "No high-conviction opportunities — preserve cash";

  return NextResponse.json({
    plan: {
      total_amount: depositAmount,
      strategy,
      generated_at: new Date().toISOString(),
      intel_summary:
        latestRun?.portfolio_synthesis?.summary ||
        latestRun?.summary ||
        "No recent Intel summary available.",
    },
    recommendations: recommendationsOut,
    summary: {
      positions_count: posList.length,
      total_deployed: Number(totalDeployed.toFixed(2)),
      fully_allocated: Math.abs(totalDeployed - depositAmount) < 1,
      strategy_mode: "formula",
      ranked_candidates: recommendationsOut.length,
    },
    funding: {
      deposit_amount: depositAmount,
      sale_proceeds: 0,
      total_cash: depositAmount,
    },
    trims,
    notes: [
      "Uses latest persisted Intel artifacts only (no fabricated market data).",
      "If tax-lot detail is missing, trim suggestions stay conservative and non-prescriptive.",
      recommendationsOut.length === 0
        ? "No BUY candidate cleared the confidence and concentration gates."
        : "Overweight names were capped to reduce concentration drift.",
    ],
    debug: {
      latest_run_id: latestRun?.id || null,
      latest_run_status: latestRun?.status || null,
      insights_considered: insightsList.length,
      recommendations_considered: recList.length,
    },
  });
}
