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

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const depositAmount = Math.max(0, Number(searchParams.get("cash_to_invest") || 900));

  const executedCount = 0;
  const rotatingPick = ROTATION_ORDER[executedCount % ROTATION_ORDER.length];

  const pctMap: Record<string, number> = Object.fromEntries(
    Object.entries(BREAKDOWN).map(([sym, pct]) => [
      sym === "ROTATING" ? rotatingPick : sym,
      pct,
    ])
  );

  const depositDate = toISODate(nextBiweeklyFriday(new Date()));

  // Layer 1: Original Plan — pure percentage breakdown, no adjustments
  const originalActions = Object.entries(pctMap).map(([symbol, pct]) => ({
    symbol,
    amount: Math.round(depositAmount * pct * 100) / 100,
    delta_weight: pct,
    deposit_date: depositDate,
  }));

  // Layer 2: Personalized Plan — mild growth bias (NVDA/QQQ up, VYM down), normalized
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
  const personalizedActions = normalizeToTotal(rawPersonalized, depositAmount);

  // Layer 3: Final (Strategy-Adjusted) — balanced mode keeps personalized allocation
  const finalActions = personalizedActions.map((a) => ({ ...a }));

  const explanation = {
    summary:
      "Balanced biweekly deployment across core holdings with a rotating growth pick. " +
      `This cycle's rotating slot is ${rotatingPick}.`,
    actions: Object.fromEntries(
      originalActions.map(({ symbol, amount, delta_weight }) => [
        symbol,
        `Deploy $${amount.toFixed(2)} (${(delta_weight * 100).toFixed(0)}% of deposit) into ${symbol}.`,
      ])
    ),
  };

  console.log("[deposit-plan] depositAmount:", depositAmount);
  console.log("[deposit-plan] original actions:", originalActions);
  console.log("[deposit-plan] personalized actions:", personalizedActions);
  console.log("[deposit-plan] final actions:", finalActions);

  return NextResponse.json({
    decision_id: crypto.randomUUID(),
    plan: { actions: finalActions },
    original_plan: { actions: originalActions },
    personalized_plan: { actions: personalizedActions },
    strategy_mode: STRATEGY_MODE,
    explanation,
  });
}
