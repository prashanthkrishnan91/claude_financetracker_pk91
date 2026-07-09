import { NextResponse } from "next/server";

/**
 * Paycheck Plan Preview proxy (Stage 12E).
 *
 * Thin server-side pass-through to the cert-gated backend endpoint
 * `POST /api/v1/advisor/paycheck-plan/preview`. The backend requires an
 * `X-Finance-Runtime-Cert-Secret` header; that secret is read here from a
 * server-only env var (`FINANCE_RUNTIME_CERT_SECRET`, no `NEXT_PUBLIC_`
 * prefix) and is never sent to or readable by the browser. This route
 * forwards the caller's Supabase Authorization header and otherwise makes
 * no changes to the response shape — no allocation math is recomputed here.
 */

export async function POST(req: Request) {
  const authHeader = req.headers.get("Authorization") ?? "";
  const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
  const certSecret = process.env.FINANCE_RUNTIME_CERT_SECRET || "";

  if (!apiBase || !authHeader || !certSecret) {
    return NextResponse.json(
      { error: "paycheck_plan_preview_unavailable" },
      { status: 503 },
    );
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_request_body" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${apiBase}/api/v1/advisor/paycheck-plan/preview`, {
      method: "POST",
      headers: {
        Authorization: authHeader,
        "Content-Type": "application/json",
        "X-Finance-Runtime-Cert-Secret": certSecret,
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      { error: "paycheck_plan_preview_unavailable" },
      { status: 502 },
    );
  }

  const payload = await upstream.json().catch(() => null);
  if (!upstream.ok || !payload) {
    return NextResponse.json(
      { error: "paycheck_plan_preview_unavailable" },
      { status: upstream.status || 502 },
    );
  }

  return NextResponse.json(payload);
}
