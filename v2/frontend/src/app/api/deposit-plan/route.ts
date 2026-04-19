import { NextRequest, NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export async function GET(request: NextRequest) {
  const cashToInvest =
    request.nextUrl.searchParams.get("cash_to_invest") ?? "900";
  const authHeader = request.headers.get("authorization");

  try {
    const headers: HeadersInit = { "Content-Type": "application/json" };
    if (authHeader) headers["Authorization"] = authHeader;

    const resp = await fetch(
      `${API_BASE}/api/v1/deposits/deposit-plan?cash_to_invest=${cashToInvest}`,
      { headers }
    );

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: "Request failed" }));
      return NextResponse.json(err, { status: resp.status });
    }

    const raw = await resp.json();
    return NextResponse.json(normalize(raw));
  } catch (err) {
    console.error("[deposit-plan route]", err);
    return NextResponse.json(
      { detail: "Failed to fetch deposit plan" },
      { status: 500 }
    );
  }
}

function toActions(
  allocation: Record<string, number>
): Array<{ symbol: string; amount: number }> {
  return Object.entries(allocation).map(([symbol, amount]) => ({
    symbol,
    amount,
  }));
}

function normalizePlan(
  plan: Record<string, unknown> | null | undefined
): { actions: Array<{ symbol: string; amount: number }> } | null {
  if (!plan) return null;
  const alloc = plan.allocation as Record<string, number> | undefined;
  if (alloc) {
    return { ...plan, actions: toActions(alloc) } as {
      actions: Array<{ symbol: string; amount: number }>;
    };
  }
  // Already has actions array shape
  return plan as { actions: Array<{ symbol: string; amount: number }> };
}

function normalize(raw: Record<string, unknown>) {
  const expl = raw.explanation as Record<string, unknown> | null | undefined;

  let explanationActions: Record<string, string> | undefined;
  if (Array.isArray(expl?.actions)) {
    explanationActions = Object.fromEntries(
      (expl!.actions as Array<{ symbol: string; explanation?: string }>).map(
        (a) => [a.symbol, a.explanation ?? ""]
      )
    );
  } else if (expl?.actions && typeof expl.actions === "object") {
    explanationActions = expl.actions as Record<string, string>;
  }

  return {
    decision_id: raw.decision_id as string,
    plan: normalizePlan(raw.plan as Record<string, unknown>),
    original_plan: normalizePlan(
      raw.original_plan as Record<string, unknown> | null
    ),
    personalized_plan: normalizePlan(
      raw.personalized_plan as Record<string, unknown> | null
    ),
    strategy_mode: raw.strategy_mode as string,
    explanation: expl
      ? { ...expl, actions: explanationActions }
      : { actions: explanationActions },
  };
}
