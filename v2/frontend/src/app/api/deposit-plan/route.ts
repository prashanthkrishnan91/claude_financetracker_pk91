import { NextResponse } from "next/server";

/**
 * Deploy tab — Portfolio Allocation Engine proxy.
 *
 * Thin pass-through to the backend `/api/v1/allocation/plan` endpoint,
 * reshaped to the `DepositPlanResult` contract the Deploy UI already
 * consumes. The backend engine owns all scoring, constraints, rounding
 * and exclusion logic; this route only forwards auth headers and adapts
 * field names.
 */

type AllocationItem = {
  ticker: string;
  symbol?: string;
  action: string;
  execution_style?: string | null;
  amount: number;
  current_weight: number;
  after_weight: number;
  target_weight: number;
  conviction_level: string;
  conviction_score: number;
  confidence: number;
  score: number;
  reason: string;
  why?: string | null;
  risk?: string | null;
  do?: string | null;
  alt_view?: string | null;
  category?: string;
};

type TrimItem = {
  ticker: string;
  action: string;
  current_weight: number;
  reason: string;
};

type AllocationPlanPayload = {
  plan: {
    cash_to_invest: number;
    total_deployed: number;
    fully_allocated: boolean;
    strategy: string;
  };
  allocations: AllocationItem[];
  exclusions: Array<{ ticker: string; reason: string }>;
  trims: TrimItem[];
  explanation: string;
  warning: string | null;
  summary: {
    cash_to_invest: number;
    total_deployed: number;
    positions_count: number;
    candidates_considered: number;
    strategy_mode: string;
    fully_allocated: boolean;
  };
};

function safeNumber(v: unknown): number {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
}

function emptyPayload(depositAmount: number, note: string) {
  return {
    plan: {
      total_amount: depositAmount,
      strategy: "No data connection — hold cash",
      generated_at: new Date().toISOString(),
      intel_summary: note,
    },
    recommendations: [] as Array<Record<string, unknown>>,
    allocations: [] as AllocationItem[],
    exclusions: [] as Array<{ ticker: string; reason: string }>,
    summary: {
      positions_count: 0,
      total_deployed: 0,
      fully_allocated: false,
      strategy_mode: "conservative",
      ranked_candidates: 0,
      candidates_considered: 0,
    },
    funding: { deposit_amount: depositAmount, sale_proceeds: 0, total_cash: depositAmount },
    trims: [] as TrimItem[],
    notes: [note],
    warning: null,
    explanation: note,
  };
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const depositAmount = Math.max(0, safeNumber(searchParams.get("cash_to_invest")));
  const authHeader = req.headers.get("Authorization") ?? "";
  const apiBase = process.env.NEXT_PUBLIC_API_URL || "";

  if (!apiBase || !authHeader) {
    return NextResponse.json(emptyPayload(
      depositAmount,
      "Portfolio data unavailable. Deployment plan withheld to avoid fabricated allocations.",
    ));
  }

  let payload: AllocationPlanPayload | null = null;
  try {
    const res = await fetch(
      `${apiBase}/api/v1/allocation/plan?cash_to_invest=${depositAmount}`,
      {
        headers: { Authorization: authHeader, "Content-Type": "application/json" },
        cache: "no-store",
      },
    );
    if (res.ok) payload = (await res.json()) as AllocationPlanPayload;
  } catch {
    payload = null;
  }

  if (!payload) {
    return NextResponse.json(emptyPayload(
      depositAmount,
      "Allocation engine unavailable. Holding cash until the backend responds.",
    ));
  }

  // Map engine → Deploy-UI contract. Keep a `recommendations` alias for back-compat.
  const recommendations = payload.allocations.map((a) => ({
    symbol: a.ticker,
    action: a.action,
    amount: a.amount,
    target_weight: a.target_weight,
    current_weight: a.current_weight,
    after_weight: a.after_weight,
    confidence: a.confidence,
    conviction_level: a.conviction_level,
    conviction_score: a.conviction_score,
    score: a.score,
    rationale: a.reason,
    portfolio_weight: a.current_weight,
    linked_intel: payload!.explanation,
    why: a.why ?? null,
    risk: a.risk ?? null,
    do: a.do ?? null,
    execution_style: a.execution_style ?? null,
    alt_view: a.alt_view ?? null,
    category: a.category ?? null,
  }));

  return NextResponse.json({
    plan: {
      total_amount: payload.plan.cash_to_invest,
      strategy: payload.plan.strategy,
      generated_at: new Date().toISOString(),
      intel_summary: payload.explanation,
    },
    recommendations,
    allocations: payload.allocations,
    exclusions: payload.exclusions,
    summary: {
      positions_count: payload.summary.positions_count,
      total_deployed: payload.summary.total_deployed,
      fully_allocated: payload.summary.fully_allocated,
      strategy_mode: payload.summary.strategy_mode,
      ranked_candidates: payload.summary.positions_count,
      candidates_considered: payload.summary.candidates_considered,
    },
    funding: {
      deposit_amount: depositAmount,
      sale_proceeds: 0,
      total_cash: depositAmount,
    },
    trims: payload.trims.map((t) => ({
      ticker: t.ticker,
      action: t.action,
      current_weight: t.current_weight,
      tax_note: "Review tax lots before trimming.",
      market_note: t.reason,
    })),
    warning: payload.warning,
    explanation: payload.explanation,
    notes: [
      payload.explanation,
      ...(payload.warning ? [payload.warning] : []),
    ],
    debug: {
      candidates_considered: payload.summary.candidates_considered,
      deployed: payload.summary.total_deployed,
    },
  });
}
