import { NextResponse } from "next/server";
import { mapFinancialTruthBaseline } from "@/lib/advisor-truth";

/**
 * Advisor readiness truth proxy.
 *
 * Server-side bridge to the cert-gated backend diagnostic
 * `POST /api/v1/diagnostics/finance-intel/financial-truth-baseline`. The
 * backend requires an `X-Finance-Runtime-Cert-Secret` header; that secret is
 * read here from a server-only env var (`FINANCE_RUNTIME_CERT_SECRET`, no
 * `NEXT_PUBLIC_` prefix) and is never sent to or readable by the browser.
 * This route forwards the caller's Supabase Authorization header, calls the
 * diagnostic with an empty JSON body, and maps the raw diagnostic to the
 * SMALL frontend-safe contract (see `mapFinancialTruthBaseline`) — the raw
 * diagnostic payload is never passed through to the client.
 */

// Pinned to the Node.js runtime (not edge) since this route reads a
// server-only secret env var and forwards it upstream.
export const runtime = "nodejs";

export async function GET(req: Request) {
  const authHeader = req.headers.get("Authorization") ?? "";
  const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
  const certSecret = process.env.FINANCE_RUNTIME_CERT_SECRET || "";

  if (!apiBase || !authHeader || !certSecret) {
    return NextResponse.json(
      { error: "advisor_readiness_unavailable" },
      { status: 503 },
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(
      `${apiBase}/api/v1/diagnostics/finance-intel/financial-truth-baseline`,
      {
        method: "POST",
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
          "X-Finance-Runtime-Cert-Secret": certSecret,
        },
        body: JSON.stringify({}),
        cache: "no-store",
      },
    );
  } catch {
    return NextResponse.json(
      { error: "advisor_readiness_unavailable" },
      { status: 502 },
    );
  }

  const payload = await upstream.json().catch(() => null);
  if (!upstream.ok || !payload) {
    return NextResponse.json(
      { error: "advisor_readiness_unavailable" },
      { status: upstream.status || 502 },
    );
  }

  // Map server-side to the frontend-safe contract — never expose the raw
  // diagnostic (and never the secret).
  return NextResponse.json(mapFinancialTruthBaseline(payload));
}
