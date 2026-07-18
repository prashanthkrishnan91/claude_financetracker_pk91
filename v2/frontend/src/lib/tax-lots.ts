"use client";

/**
 * Tax lots — typed fetch helper + React Query hook.
 *
 * GET /api/v1/positions/tax-lots returns reconciled FIFO lot estimates per
 * holding. Estimates only — never tax advice. The backend gates lots behind
 * reconciliation; non-authoritative holdings carry a message instead of lots.
 *
 * fetchApi in ./api is module-private, so the same Bearer-token fetch pattern
 * is replicated locally here.
 */

import { useQuery } from "@tanstack/react-query";
import { supabase } from "./supabase";

// Mirror of api.ts base-URL handling (enforce HTTPS on HTTPS pages).
const _rawBase = process.env.NEXT_PUBLIC_API_URL || "";
const API_BASE =
  typeof window !== "undefined" &&
  window.location.protocol === "https:" &&
  _rawBase.startsWith("http://")
    ? _rawBase.replace("http://", "https://")
    : _rawBase;

// ── Types (mirror backend tax_lot_engine presentation shapes) ─────────────────

export interface TaxLotRow {
  acquired_date: string;
  source_tx_type: string | null;
  remaining_shares: number;
  cost_per_share: number;
  cost_basis: number;
  estimated_holding_classification: "long_term" | "short_term" | string;
  estimated_long_term_start_date: string;
  days_until_long_term: number;
  current_value: number | null;
  unrealized_gain: number | null;
  unrealized_gain_pct: number | null;
}

export interface TaxLotReconciliation {
  status:
    | "reconciled"
    | "quantity_mismatch"
    | "basis_mismatch"
    | "blocked_unsupported_events"
    | "blocked_share_ledger_oversold"
    | "no_transaction_history"
    | string;
  position_shares?: number;
  lot_shares?: number;
  share_difference?: number;
  quantity_tolerance?: number;
  position_cost_basis?: number | null;
  lot_cost_basis?: number;
  basis_difference_pct?: number | null;
  basis_tolerance_pct?: number;
}

export interface TaxLotHolding {
  ticker: string;
  reconciliation: TaxLotReconciliation;
  authoritative: boolean;
  /** Backend-authored explanation when lots are not authoritative; null otherwise. */
  message: string | null;
  lots: TaxLotRow[] | null;
  unsupported_events: unknown[];
  event_counts?: Record<string, number>;
}

export interface TaxLotsResponse {
  engine_version: string;
  jurisdiction_note: string;
  disclaimer: string;
  holdings: TaxLotHolding[];
}

// ── Fetch helper ──────────────────────────────────────────────────────────────

export async function fetchTaxLots(): Promise<TaxLotsResponse> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers: HeadersInit = { "Content-Type": "application/json" };
  if (session?.access_token) {
    headers["Authorization"] = `Bearer ${session.access_token}`;
  }

  const response = await fetch(`${API_BASE}/api/v1/positions/tax-lots`, { headers });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Request failed" }));
    throw new Error(
      typeof error.detail === "string" ? error.detail : `API error: ${response.status}`
    );
  }
  return response.json();
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export const TAX_LOTS_QUERY_KEY = ["positions", "tax-lots"] as const;

/**
 * Tax lots query — fetched only when a tax-lot section is actually opened
 * (enabled=false until then). One query serves every holding row; React Query
 * dedupes concurrent consumers on the shared key.
 */
export function useTaxLots(enabled: boolean) {
  return useQuery<TaxLotsResponse>({
    queryKey: [...TAX_LOTS_QUERY_KEY],
    queryFn: fetchTaxLots,
    enabled,
    staleTime: 5 * 60_000,
  });
}

/** Find a ticker's tax-lot holding entry (case-insensitive), or null. */
export function findTaxLotHolding(
  data: TaxLotsResponse | undefined,
  ticker: string
): TaxLotHolding | null {
  if (!data) return null;
  return (
    data.holdings.find(h => h.ticker.toUpperCase() === ticker.toUpperCase()) ?? null
  );
}
