/**
 * API client for the FastAPI backend.
 * All requests include the Supabase JWT for authentication.
 */

import { supabase } from "./supabase";

// Enforce HTTPS when the page is served over HTTPS.
// Guards against NEXT_PUBLIC_API_URL being set to http:// in production,
// which causes a browser mixed-content block.
const _rawBase = process.env.NEXT_PUBLIC_API_URL || "";
const API_BASE =
  typeof window !== "undefined" &&
  window.location.protocol === "https:" &&
  _rawBase.startsWith("http://")
    ? _rawBase.replace("http://", "https://")
    : _rawBase;

async function getAuthHeaders(): Promise<HeadersInit> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers: HeadersInit = { "Content-Type": "application/json" };
  if (session?.access_token) {
    headers["Authorization"] = `Bearer ${session.access_token}`;
  }
  return headers;
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
    const error = await response
      .json()
      .catch(() => ({ detail: "Request failed" }));
    throw new Error(error.detail || `API error: ${response.status}`);
  }

  return response.json();
}

/** Fetch a local Next.js API route (relative URL), forwarding auth headers. */
async function fetchLocal<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = await getAuthHeaders();
  const response = await fetch(endpoint, {
    ...options,
    headers: { ...headers, ...options.headers },
  });

  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Request failed" }));
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
    backfillSnapshots: () =>
      fetchApi<BackfillResult>("/api/v1/portfolio/snapshots/backfill", { method: "POST" }),
    getTargets: () =>
      fetchApi<TargetAllocation[]>("/api/v1/portfolio/targets"),
    setTargets: (targets: { ticker: string; target_pct: number }[]) =>
      fetchApi<TargetAllocation[]>("/api/v1/portfolio/targets", {
        method: "PUT",
        body: JSON.stringify(targets),
      }),
    getRebalance: (cashToDeploy?: number) =>
      fetchApi<RebalanceResult[]>(
        `/api/v1/portfolio/rebalance${cashToDeploy ? `?cash_to_deploy=${cashToDeploy}` : ""}`
      ),
    getCash: () => fetchApi<CashBalance>("/api/v1/portfolio/cash"),
    setCash: (amount: number | null) =>
      fetchApi<CashBalance>("/api/v1/portfolio/cash", {
        method: "PATCH",
        body: JSON.stringify({ amount }),
      }),
  },

  positions: {
    list: (category?: string) =>
      fetchApi<Position[]>(
        `/api/v1/positions${category ? `?category=${category}` : ""}`
      ),
    get: (ticker: string) => fetchApi<Position>(`/api/v1/positions/${ticker}`),
  },

  prices: {
    get: (ticker: string) => fetchApi<PriceQuote>(`/api/v1/prices/${ticker}`),
    batch: (tickers: string[]) =>
      fetchApi<BatchPriceResponse>("/api/v1/prices/batch", {
        method: "POST",
        body: JSON.stringify({ tickers }),
      }),
    history: (ticker: string, period = "1Y") =>
      fetchApi<PriceHistory>(
        `/api/v1/prices/${ticker}/history?period=${period}`
      ),
    health: () => fetchApi<PriceHealthStatus>("/api/v1/prices/health/status"),
  },

  recommendations: {
    list: (action?: string) =>
      fetchApi<InsightCardData[]>(
        `/api/v1/recommendations/${action ? `?action=${action}` : ""}`
      ),
    refresh: (body?: { deposit_amount?: number; sale_proceeds?: number }) =>
      fetchApi<AgentRunQueued>("/api/v1/recommendations/refresh", {
        method: "POST",
        body: JSON.stringify(body || {}),
      }),
    getJob: (jobId: string) =>
      fetchApi<AgentRunStatus>(`/api/v1/recommendations/jobs/${jobId}`),
    getLatestJob: () =>
      fetchApi<AgentRunStatus | null>("/api/v1/recommendations/jobs/latest"),
    getJobInsights: (jobId: string) =>
      fetchApi<AgentInsightData[]>(
        `/api/v1/recommendations/jobs/${jobId}/insights`
      ),
    getLatestInsights: () =>
      fetchApi<AgentInsightData[]>("/api/v1/recommendations/insights/latest"),
    resolve: (recId: string, resolution: string, notes?: string) =>
      fetchApi<void>(`/api/v1/recommendations/${recId}/resolve`, {
        method: "PATCH",
        body: JSON.stringify({ resolution, notes }),
      }),
    getDecisions: (limit = 50) =>
      fetchApi<DecisionLogEntry[]>(
        `/api/v1/recommendations/decisions?limit=${limit}`
      ),
    getOutcomes: () =>
      fetchApi<DecisionLogEntry[]>("/api/v1/recommendations/decisions/outcomes"),
  },

  sync: {
    plaid: (force = false) =>
      fetchApi<SyncResult>(`/api/v1/sync/plaid?force=${force}`, {
        method: "POST",
      }),
    plaidStatus: () => fetchApi<SyncStatus>("/api/v1/sync/plaid/status"),
    refreshPrices: () =>
      fetchApi<PriceRefreshResult>("/api/v1/sync/prices/refresh", {
        method: "POST",
      }),
    importCsv: (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return fetchApiForm<ImportResult>("/api/v1/sync/csv/import", formData);
    },
    importPdf: (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return fetchApiForm<PdfImportResult>("/api/v1/sync/pdf/import", formData);
    },
  },

  drip: {
    getSummary: () => fetchApi<DripSummary>("/api/v1/drip/summary"),
    getPositions: () => fetchApi<DripPosition[]>("/api/v1/drip/positions"),
    getHistory: () => fetchApi<DripHistoryEntry[]>("/api/v1/drip/history"),
  },

  auth: {
    me: () => fetchApi<UserProfile>("/api/v1/auth/me"),
    updateProfile: (data: Partial<UserProfile>) =>
      fetchApi<UserProfile>("/api/v1/auth/me", {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    updateApiKeys: (keys: ApiKeysUpdate) =>
      fetchApi<{ status: string }>("/api/v1/auth/me/api-keys", {
        method: "PUT",
        body: JSON.stringify(keys),
      }),
  },

  ai: {
    rebalance: () =>
      fetchApi<AiRebalanceResult>("/api/v1/ai/rebalance", { method: "POST" }),
    getLatest: () =>
      fetchApi<AiRebalanceResult | null>("/api/v1/ai/rebalance/latest"),
    getChanges: (ticker: string) =>
      fetchApi<AnalysisChangesResponse>(`/api/v1/ai/analysis/changes?ticker=${encodeURIComponent(ticker)}`),
  },

  deposits: {
    getPlan: (cashToInvest = 0, portfolioBalance = 0) =>
      fetchLocal<DepositPlanResult>(
        `/api/deposit-plan?cash_to_invest=${cashToInvest}&portfolio_balance=${portfolioBalance}`
      ),
  },

  decisionLogs: {
    list: (limit = 50) =>
      fetchApi<Record<string, unknown>[]>(`/api/v1/decision/logs?limit=${limit}`),
    create: (entry: {
      ticker: string;
      action: string;
      amount: number;
      confidence?: number;
      reasoning?: string;
      source?: string;
      metadata?: Record<string, unknown>;
      strategy_tag?: string;
      confidence_score?: number;
    }) =>
      fetchApi<Record<string, unknown>>("/api/v1/decision/logs", {
        method: "POST",
        body: JSON.stringify(entry),
      }),
  },

  analytics: {
    getStrategyPerformance: () =>
      fetchApi<StrategyPerformance[]>("/api/v1/analytics/strategy-performance"),
  },
};

/** Form data upload (no JSON content-type) */
async function fetchApiForm<T>(
  endpoint: string,
  formData: FormData
): Promise<T> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers: HeadersInit = {};
  if (session?.access_token) {
    headers["Authorization"] = `Bearer ${session.access_token}`;
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    method: "POST",
    headers,
    body: formData,
  });

  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Upload failed" }));
    throw new Error(error.detail || `API error: ${response.status}`);
  }

  return response.json();
}

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
  last_price_fetch?: string;
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
  lt_date?: string;
  drip_shares?: number;
  divs_received?: number;
  source: string;
}

export interface InsightCardData {
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
  // Multi-agent enrichment (nullable for legacy rows)
  investment_thesis?: string | null;
  sentiment_score?: number | null;
  sentiment_label?: string | null;
  technical_signal?: string | null;
  conviction_score?: number | null;
  suggested_allocation?: number | null;
  agent_run_id?: string | null;
  what_changed?: string | null;
  // Data-quality UX (populated on Phase 6+)
  data_confidence_score?: number | null;
  data_quality_label?: "HIGH" | "MEDIUM" | "LOW" | string | null;
  reason_tags?: string[] | null;
  // Phase 3 analyst verdict projection (nullable on pre-Phase-3 rows)
  analyst_action?: string | null;
  analyst_conviction?: number | null;
  analyst_confidence?: number | null;
  analyst_drivers?: string[] | null;
  analyst_risks?: string[] | null;
  analyst_used_fallback?: boolean | null;
}

export interface AgentRunQueued {
  job_id: string;
  status: string;
  message: string;
}

export interface PortfolioSynthesisPayload {
  portfolio_bias: "bullish" | "neutral" | "defensive" | string;
  key_themes: string[];
  risk_concentrations: string[];
  overexposure_flags: string[];
  rebalancing_suggestions: string[];
  summary: string;
  used_fallback?: boolean;
  error?: string | null;
}

export interface ModeDecisionPayload {
  mode: "FULL" | "DEGRADED" | string;
  avg_quality: number;
  insufficient_count: number;
  total_tickers: number;
  reason: string;
  explanation: string;
}

export interface CostMetricsPayload {
  mode: "FULL" | "DEGRADED" | string;
  total_calls: number;
  total_cost_usd: number;
  calls_by_kind: Record<string, number>;
  calls_by_model: Record<string, number>;
  entries: Array<{
    kind: string;
    model: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }>;
}

export interface AgentRunStatus {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  current_agent: string | null;
  progress_pct: number;
  tickers: string[];
  deposit_amount: number;
  sale_proceeds: number;
  allocation: Record<string, number>;
  summary: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  // Phase 4 — portfolio synthesis
  portfolio_synthesis?: PortfolioSynthesisPayload | null;
  synthesis_used_fallback?: boolean | null;
  // Phase 5 — run mode + cost
  run_mode?: "FULL" | "DEGRADED" | null;
  run_mode_decision?: ModeDecisionPayload | null;
  cost_metrics?: CostMetricsPayload | null;
}

export interface AnalystVerdictPayload {
  ticker: string;
  action: "BUY" | "HOLD" | "REDUCE" | "INSUFFICIENT_DATA" | string;
  conviction: number;
  key_drivers: string[];
  risks: string[];
  confidence: number;
  used_fallback: boolean;
  error?: string | null;
}

export interface AgentInsightData {
  id: string;
  run_id: string | null;
  ticker: string;
  investment_thesis: string | null;
  sentiment_score: number | null;
  sentiment_label: string | null;
  technical_signal: string | null;
  technical_summary: string | null;
  fundamental_score: number | null;
  fundamental_summary: string | null;
  conviction_score: number | null;
  suggested_allocation: number | null;
  suggested_action: string | null;
  created_at: string | null;
  what_changed: string | null;
  // Phase 3 — per-ticker analyst
  analyst_verdict: AnalystVerdictPayload | null;
  analyst_confidence: number | null;
}

export interface AnalysisChangesResponse {
  ticker: string;
  what_changed: string | null;
  run_id: string | null;
  created_at: string | null;
}

export interface PriceQuote {
  ticker: string;
  mid_price: number;
  bid?: number;
  ask?: number;
  last_trade: number;
  source: string;
  timestamp: number;
  error?: string;
}

export interface Snapshot {
  id: string;
  snapshot_at: string;
  total_equity: number;
  total_cost: number;
  total_pnl: number;
  total_pnl_pct: number;
  cash_balance: number;
}

export interface PriceHistory {
  ticker: string;
  period: string;
  data_points: PriceHistoryPoint[];
}

export interface PriceHistoryPoint {
  price_date: string;
  open_price?: number;
  high_price?: number;
  low_price?: number;
  close_price: number;
  volume?: number;
  source?: string;
}

export interface PriceHealthStatus {
  total_tickers: number;
  fresh_count: number;
  stale_count: number;
  error_count: number;
  sources_used: string[];
}

export interface BatchPriceResponse {
  prices: Record<string, PriceQuote>;
  health: PriceHealthStatus;
}

export interface SyncResult {
  status: string;
  message?: string;
  holdings_count?: number;
  cash_balance?: number;
  positions_updated?: number;
  positions_created?: number;
  synced_at?: string;
  duration_ms?: number;
}

export interface SyncStatus {
  status: string;
  last_synced_at?: string;
  age_hours?: number;
  holdings_count?: number;
  cash_balance?: number;
  next_sync_in_hours?: number;
}

export interface PriceRefreshResult {
  status: string;
  total: number;
  fresh: number;
  stale: number;
  errors: number;
  sources_used: string[];
}

export interface ImportResult {
  total_rows: number;
  new_rows: number;
  duplicates_skipped: number;
  errors: number;
  error_details: string[];
}

export interface PdfImportResult {
  tickers_found: string[];
  positions_updated: number;
  positions_created: number;
  errors: string[];
}

export interface TargetAllocation {
  id: string;
  ticker: string;
  target_pct: number;
  updated_at: string;
}

export interface RebalanceResult {
  ticker: string;
  current_pct: number;
  target_pct: number;
  drift_pct: number;
  suggested_action: string;
  suggested_amount: number;
  // Enrichment fields
  intel_action?: string;
  intel_urgency?: number;
  drip_note?: string;
  rationale?: string;
  is_default_formula?: boolean;
}

export interface DripSummary {
  lifetime_earned: number;
  annual_projection: number;
  monthly_estimate: number;
  top_earner: string | null;
  positions_with_drip: number;
}

export interface DripPosition {
  ticker: string;
  name: string;
  shares: number;
  drip_shares: number;
  drip_cost: number;
  drip_value: number;
  drip_gain: number;
  annual_income: number;
  yield_pct: number;
  ex_date: string | null;
  pay_date: string | null;
  category: string;
}

export interface DripHistoryEntry {
  id: string;
  ticker: string | null;
  amount: number;
  tx_date: string;
  description: string | null;
}

export interface CashBalance {
  cash_balance: number;
  source: "plaid" | "manual" | "none";
  manual_override: number | null;
}

export interface DecisionLogEntry {
  id: string;
  recommendation_id: string | null;
  ticker: string;
  decision: string;
  notes: string | null;
  price_at_decision: number | null;
  shares_at_decision: number | null;
  current_price: number | null;
  return_pct: number | null;
  status: "active" | "closed";
  closed_at: string | null;
  created_at: string;
  strategy_tag: string | null;
  confidence_score: number | null;
}

export interface StrategyPerformance {
  strategy_tag: string;
  avg_return: number | null;
  win_rate: number | null;
  total_trades: number;
}

export interface AiAllocation {
  ticker: string;
  name: string;
  current_pct: number;
  suggested_pct: number;
  change_pct: number;
  rationale: string;
}

export interface AiRebalanceResult {
  allocation_table: AiAllocation[];
  narrative: string;
  total_value: number;
  generated_at: string;
}

export interface UserProfile {
  id: string;
  email: string;
  display_name: string | null;
  deposit_amount: number;
  deposit_frequency: string;
  theme: string;
  has_plaid: boolean;
  has_plaid_client: boolean;
  has_plaid_secret: boolean;
  has_finnhub: boolean;
  has_polygon: boolean;
  has_alpaca: boolean;
  has_anthropic: boolean;
}

export interface BackfillResult {
  created: number;
  skipped: number;
  message: string;
}

export interface ApiKeysUpdate {
  plaid_access_token?: string;
  plaid_client_id?: string;
  plaid_secret?: string;
  plaid_env?: string;
  finnhub_api_key?: string;
  polygon_api_key?: string;
  alpaca_api_key?: string;
  alpaca_secret_key?: string;
  anthropic_api_key?: string;
}

export interface DepositRecommendation {
  symbol: string;
  action: string;
  amount: number;
  target_weight: number;
  rationale: string;
  confidence: number;
  portfolio_weight?: number;
  conviction_score?: number;
  linked_intel?: string;
}

export interface DepositPlanResult {
  plan: {
    total_amount: number;
    strategy: string;
    generated_at: string;
    intel_summary?: string;
  };
  recommendations: DepositRecommendation[];
  summary: {
    positions_count: number;
    total_deployed: number;
    fully_allocated: boolean;
    strategy_mode: string;
    ranked_candidates: number;
  };
  funding: {
    deposit_amount: number;
    sale_proceeds: number;
    total_cash: number;
  };
  trims: Array<{
    ticker: string;
    action: string;
    tax_note: string;
    market_note: string;
  }>;
  notes: string[];
  debug: {
    latest_run_id: string | null;
    latest_run_status: string | null;
    insights_considered: number;
    recommendations_considered: number;
  };
}
