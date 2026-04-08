/**
 * API client for the FastAPI backend.
 * All requests include the Supabase JWT for authentication.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

async function getAuthHeaders(): Promise<HeadersInit> {
  // In Phase 3, this will get the JWT from Supabase session
  return {
    "Content-Type": "application/json",
  };
}

async function fetchApi<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = await getAuthHeaders();
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: { ...headers, ...options.headers },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(error.detail || `API error: ${response.status}`);
  }

  return response.json();
}

// ── Portfolio ────────────────────────────────────────────────────────────────

export const api = {
  portfolio: {
    getSummary: () => fetchApi<PortfolioSummary>("/api/v1/portfolio/summary"),
    getSnapshots: (limit = 50) =>
      fetchApi<Snapshot[]>(`/api/v1/portfolio/snapshots?limit=${limit}`),
    createSnapshot: () =>
      fetchApi<Snapshot>("/api/v1/portfolio/snapshots", { method: "POST" }),
  },

  positions: {
    list: (category?: string) =>
      fetchApi<Position[]>(
        `/api/v1/positions/${category ? `?category=${category}` : ""}`
      ),
    get: (ticker: string) => fetchApi<Position>(`/api/v1/positions/${ticker}`),
  },

  prices: {
    batch: (tickers: string[]) =>
      fetchApi<BatchPriceResponse>("/api/v1/prices/batch", {
        method: "POST",
        body: JSON.stringify({ tickers }),
      }),
    history: (ticker: string, period = "1Y") =>
      fetchApi<PriceHistory>(`/api/v1/prices/${ticker}/history?period=${period}`),
  },

  recommendations: {
    list: (action?: string) =>
      fetchApi<InsightCard[]>(
        `/api/v1/recommendations/${action ? `?action=${action}` : ""}`
      ),
    refresh: () =>
      fetchApi<InsightCard[]>("/api/v1/recommendations/refresh", {
        method: "POST",
      }),
  },

  sync: {
    plaid: (force = false) =>
      fetchApi<SyncResult>(`/api/v1/sync/plaid?force=${force}`, {
        method: "POST",
      }),
    plaidStatus: () => fetchApi<SyncStatus>("/api/v1/sync/plaid/status"),
    refreshPrices: () =>
      fetchApi<SyncResult>("/api/v1/sync/prices/refresh", { method: "POST" }),
  },
};

// ── Types (mirrors backend Pydantic models) ──────────────────────────────────

export interface PortfolioSummary {
  total_equity: number;
  total_cost: number;
  total_pnl: number;
  total_pnl_pct: number;
  cash_balance: number;
  day_change: number;
  day_change_pct: number;
  stocks_value: number;
  etfs_value: number;
  crypto_value: number;
  positions_count: number;
  prices_fresh: number;
  prices_stale: number;
}

export interface Position {
  id: string;
  ticker: string;
  name: string;
  category: string;
  shares: number;
  avg_cost: number;
  current_price?: number;
  market_value?: number;
  unrealised_pnl?: number;
  unrealised_pnl_pct?: number;
  day_change?: number;
  day_change_pct?: number;
  lt_eligible: boolean;
}

export interface InsightCard {
  id: string;
  ticker: string;
  name: string;
  action: string;
  detail: string;
  rationale: string;
  urgency: number;
  color: string;
  tax_note: string;
  drip_note: string;
  current_price?: number;
  pnl_pct?: number;
  category: string;
}

export interface Snapshot {
  id: string;
  snapshot_at: string;
  total_equity: number;
  total_pnl: number;
  total_pnl_pct: number;
}

export interface PriceHistory {
  ticker: string;
  period: string;
  data_points: { price_date: string; close_price: number }[];
}

export interface BatchPriceResponse {
  prices: Record<string, { mid_price: number; source: string }>;
}

export interface SyncResult {
  status: string;
  message?: string;
}

export interface SyncStatus {
  status: string;
  last_synced_at?: string;
  age_hours?: number;
  holdings_count?: number;
}
