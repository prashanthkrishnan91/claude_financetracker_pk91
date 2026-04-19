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

const DEPOSIT_AMOUNT = 900.0;
const STRATEGY_MODE = "balanced";

function nextBiweeklyFriday(from: Date): Date {
  const d = new Date(from);
  // weekday: 0=Sun,1=Mon,...,5=Fri
  const dow = d.getDay();
  const daysAhead = (5 - dow + 7) % 7;

  if (daysAhead === 0) {
    // Today IS Friday — skip to next Friday (+7), then +14
    d.setDate(d.getDate() + 7 + 14);
  } else {
    // Next Friday, then one more week
    d.setDate(d.getDate() + daysAhead + 7);
  }
  return d;
}

function toISODate(d: Date): string {
  return d.toISOString().split("T")[0];
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const depositAmount = Number(searchParams.get("cash_to_invest") || DEPOSIT_AMOUNT);

  const executedCount = 0;
  const rotatingPick = ROTATION_ORDER[executedCount % ROTATION_ORDER.length];

  const pctMap: Record<string, number> = Object.fromEntries(
    Object.entries(BREAKDOWN).map(([sym, pct]) => [
      sym === "ROTATING" ? rotatingPick : sym,
      pct,
    ])
  );

  const depositDate = toISODate(nextBiweeklyFriday(new Date()));

  const actions = Object.entries(pctMap).map(([symbol, pct]) => ({
    symbol,
    amount: Math.round(depositAmount * pct * 100) / 100,
    delta_weight: pct,
    deposit_date: depositDate,
  }));

  const planShape = { actions };

  const explanation = {
    summary:
      "Balanced biweekly deployment across core holdings with a rotating growth pick. " +
      `This cycle's rotating slot is ${rotatingPick}.`,
    actions: Object.fromEntries(
      actions.map(({ symbol, amount, delta_weight }) => [
        symbol,
        `Deploy $${amount} (${(delta_weight * 100).toFixed(0)}% of deposit) into ${symbol}.`,
      ])
    ),
  };

  console.log("[deposit-plan] depositAmount:", depositAmount, "actions:", actions);

  return NextResponse.json({
    decision_id: crypto.randomUUID(),
    plan: planShape,
    original_plan: planShape,
    personalized_plan: planShape,
    strategy_mode: STRATEGY_MODE,
    explanation,
  });
}
