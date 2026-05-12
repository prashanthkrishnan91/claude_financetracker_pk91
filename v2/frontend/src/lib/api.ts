/**
 * API client for the FastAPI backend.
 * All requests include the Supabase JWT for authentication.
 */

import { supabase } from "./supabase";
import { DEPLOY_V3_PLAN_ENDPOINT } from "./deploy-v3-helpers";

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
    getPlan: (cashToInvest = 0, _portfolioBalance = 0) =>
      fetchLocal<DepositPlanResult>(
        `/api/deposit-plan?cash_to_invest=${cashToInvest}`
      ),
  },

  decisionLogs: {
    createDecisionLog: (snapshot: Record<string, unknown>, actualDecisions?: ActualDecisionItem[]) =>
      fetchApi<DecisionMemoryLog>("/api/v1/decision-logs", {
        method: "POST",
        body: JSON.stringify({ recommendation_snapshot: snapshot, source: "deploy", actual_decisions: actualDecisions ?? [] }),
      }),
    listDecisionLogs: (limit = 25) =>
      fetchApi<DecisionMemoryLog[]>(`/api/v1/decision-logs?limit=${limit}`),
    getDecisionLog: (id: string) =>
      fetchApi<DecisionMemoryLog>(`/api/v1/decision-logs/${id}`),
    evaluateDecisionLog: (id: string) =>
      fetchApi<DecisionMemoryLog>(`/api/v1/decision-logs/${id}/evaluate`, { method: "POST" }),
    getDecisionInsights: () =>
      fetchApi<DecisionPerformanceInsights>("/api/v1/decision-logs/insights"),
    updateDecisionLog: (id: string, patch: DecisionLogPatch) =>
      fetchApi<DecisionMemoryLog>(`/api/v1/decision-logs/${id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    deleteDecisionLog: (id: string) =>
      fetchApi<void>(`/api/v1/decision-logs/${id}`, { method: "DELETE" }),
  },

  analytics: {
    getStrategyPerformance: () =>
      fetchApi<StrategyPerformance[]>("/api/v1/analytics/strategy-performance"),
  },

  // Intel v3 snapshot path. Only active when NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true.
  intelV3: {
    getSnapshot: () =>
      fetchApi<IntelV3Snapshot>("/api/v1/intel/v3/snapshot"),
    runV3: () =>
      fetchApi<IntelV3RunResult>("/api/v1/intel/v3/run", { method: "POST" }),
    getRunStatus: (runId: string) =>
      fetchApi<IntelV3RunStatus>(`/api/v1/intel/v3/runs/${runId}`),
  },

  // Deploy v3 read-only plan. Calls GET /api/v1/deploy/v3/plan.
  // Intel v3 is the only Buy/Hold/Trim/Sell authority. No legacy allocation engine.
  deployV3: {
    getPlan: () => fetchApi<DeployV3PlanResponse>(DEPLOY_V3_PLAN_ENDPOINT),
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
  sector?: string | null;
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
  // Canonical reasoning contract from backend normalization.
  summary?: string | null;
  reasoning_summary?: string | null;
  thesis?: string | null;
  why_this_matters?: string | null;
  key_drivers?: string[] | null;
  main_risks?: string[] | null;
  confidence?: number | null;
  conviction?: number | null;
  supporting_evidence?: string[] | null;
  plain_language_explanation?: string | null;
  fallback_flags?: string[] | null;
  analysis_source?: "live_llm" | "cached_run" | "deterministic_fallback" | null;
  // Hedge-fund memo fields (Phase 7)
  conviction_level?: "HIGH" | "MEDIUM" | "LOW" | null;
  primary_driver?: string | null;
  risk_flag?: string | null;
  action_reason?: string | null;
  // human_v2 schema fields
  differentiation?: string | null;
  reasoning_schema_version?: string | null;
  reasoning_source?: "fresh_llm" | "fallback" | "cache" | "stale_db" | "no_analyst_data" | string | null;
  // Intel v2 PR-9: plain-English thesis labels (never render thesis_v2 directly)
  thesis_plain_english?: ThesisPlainEnglish | null;
  // Intel v2 reasoning_v2 UI: compact "Why this view?" projection.
  // Populated when _reasoning_v2 exists. Contains no raw metric keys.
  intel_read?: IntelRead | null;
  // Intel posture system (v3): advisor-facing posture decoupled from broker BUY/HOLD/SELL.
  // intel_posture_label: badge shown on the card ("Add Candidate", "Watchlist", etc.)
  // intel_filter_bucket: key used by filter tabs for counting + filtering.
  intel_posture_label?: string | null;
  intel_filter_bucket?: string | null;
}

export interface IntelRead {
  title: string;
  posture_label: string;
  summary: string;
  trusted_signals: string[];
  incomplete_signals: string[];
  caveat: string;
  /** Shown in WHY THIS VIEW instead of summary for insufficient-data cards. */
  bottom_line?: string | null;
  /** True when the card is in insufficient-data mode. */
  insufficient_data?: boolean;
  /** Card-specific explanation of WHY the Intel posture was assigned. Injected
   *  after _derive_intel_posture runs; more specific than summary/bottom_line.
   *  Overridden by narrative_contract.evidence_summary when contract is present. */
  posture_reason?: string | null;
  /** Intel Card Narrative Contract v1. When present, evidence_summary and
   *  final_takeaway are the action-consistent Evidence Check copy. */
  narrative_contract?: {
    action: string;
    confidence_label: string;
    evidence_summary: string;
    reliable_labels: string[];
    missing_labels: string[];
    final_takeaway: string;
    conflict_flags: string[];
    narrative_contract_version: string;
  } | null;
}

export interface ThesisPlainEnglish {
  headline?: string | null;
  quality_label?: string | null;
  valuation_label?: string | null;
  risk_label?: string | null;
  momentum_label?: string | null;
  data_label?: string | null;
  caveats?: string[] | null;
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
  quality?: "HIGH" | "MEDIUM" | "LOW" | string | null;
  top_sectors?: string[] | null;
  sector_allocation?: Record<string, number> | null;
  counts?: Record<string, number> | null;
  aggregate_quality?: "HIGH" | "MEDIUM" | "LOW" | string | null;
  quality_breakdown?: {
    total_cards: number;
    enriched: number;
    high_quality: number;
    fallback: number;
  } | null;
  used_fallback?: boolean;
  error?: string | null;
  bias?: "Bullish" | "Neutral" | "Defensive" | string | null;
  headline?: string | null;
  executive_summary?: string | null;
  action_counts?: Record<string, number> | null;
  exposures?: {
    strategy_buckets?: Array<{ name: string; percentage: number; top_tickers?: string[]; why_it_matters?: string }>;
    sector_buckets?: Array<{ name: string; percentage: number; top_tickers?: string[]; why_it_matters?: string }>;
    risk_buckets?: Array<{ name: string; percentage: number; top_tickers?: string[]; why_it_matters?: string }>;
  } | null;
  top_opportunities?: Array<{ ticker: string; reason: string; confidence?: number; risk_note?: string; suggested_use?: string }>;
  top_risks?: Array<{ label?: string; tickers?: string[]; note?: string }>;
  trim_candidates?: Array<{ ticker: string; why_trim?: string; what_to_watch?: string; redirect_proceeds_to?: string[] }>;
  deploy_suggestions?: string[];
  what_changed?: Array<{ ticker?: string; change?: string }>;
  watchlist?: Array<{ ticker?: string; focus?: string; trigger?: string }>;
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
  actual_llm_calls?: number;
  attempted_llm_calls?: number;
  successful_llm_calls?: number;
  failed_llm_calls?: number;
  skipped_llm_calls?: number;
  llm_enriched_cards?: number;
  discarded_llm_calls?: number;
  fallback_cards?: number;
  reused_cached_cards?: number;
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
  status: "queued" | "running" | "in_progress" | "completed" | "failed" | "cancelled";
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

export type DecisionLogStatus = "DRAFT" | "FULLY_EXECUTED" | "PARTIALLY_EXECUTED" | "SKIPPED";

export interface ActualDecisionItem {
  ticker?: string;
  recommended_action?: string;
  actual_action?: string;
  recommended_amount?: number;
  actual_amount?: number;
  replacement_ticker?: string;
  replacement_amount?: number;
  reason?: string;
  executed_at?: string;
}

export interface DecisionLogPatch {
  actual_decisions?: ActualDecisionItem[];
  notes?: string;
}

export interface DecisionDelta {
  total_recommended: number;
  total_actual: number;
  deploy_delta: number;
  skipped_tickers: string[];
  replaced_tickers: Array<{ from: string | null; to: string | null; reason: string | null }>;
  category_shift: {
    growth_to_income: boolean;
    single_to_etf: boolean;
    concentration_change: number;
  };
}

export interface DecisionMemoryLog {
  id: string;
  user_id: string;
  source: string;
  status: DecisionLogStatus;
  recommendation_snapshot: Record<string, unknown>;
  price_snapshot?: Record<string, { price?: number; timestamp?: string } | unknown> | null;
  actual_decisions: ActualDecisionItem[];
  performance_snapshot?: {
    status?: "baseline_captured" | "ready" | "partial_data" | "missing_price" | "pending" | "insufficient_data" | "unavailable";
    evaluated_at: string;
    baseline_captured_at?: string;
    portfolio: {
      recommended_return: number;
      actual_return: number;
      delta: number;
      matched_model?: boolean;
      too_early_to_judge?: boolean;
      backfilled_baseline?: boolean;
      summary_text?: string;
      total_recommended_return?: number;
      total_actual_return?: number;
      total_delta?: number;
      best_decision?: { ticker: string; delta_pct: number } | null;
      worst_decision?: { ticker: string; delta_pct: number } | null;
    };
    windows?: {
      [key in "7d" | "30d" | "90d"]?: {
        status: "pending" | "ready" | "insufficient_data" | "unavailable";
        recommended_return_pct: number | null;
        actual_return_pct: number | null;
        delta_pct: number | null;
        as_of: string;
      };
    };
    per_ticker: Array<{
      ticker: string;
      recommended_ticker?: string;
      actual_ticker?: string;
      actual_action?: string;
      status?: string;
      reason?: string | null;
      recommended_return_pct: number | null;
      actual_return_pct: number | null;
      delta_pct: number | null;
    }>;
    data_quality?: Array<{
      status: string;
      reason: string;
      ticker: string;
      leg?: string;
      for_ticker?: string;
    }>;
  } | null;
  decision_delta?: DecisionDelta | null;
  risk_behavior?: "more_conservative" | "more_aggressive" | "aligned" | null;
  style_shift?: "growth_to_income" | "income_to_growth" | null;
  execution_gap_percent?: number | null;
  realized_pnl?: number | null;
  unrealized_pnl?: number | null;
  review_date?: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface DecisionInsightBucket {
  count: number;
  avg_delta: number | null;
  win_rate: number | null;
}

export interface DecisionPerformanceInsights {
  eligible_logs: number;
  total_logs: number;
  confidence: "low" | "medium" | "high";
  summary: {
    avg_actual_return: number;
    avg_model_return: number;
    avg_delta: number;
    win_rate_vs_model: number;
    best_override?: { ticker: string; delta_pct: number; actual_action?: string | null } | null;
    worst_override?: { ticker: string; delta_pct: number; actual_action?: string | null } | null;
  };
  behavior_insights: {
    replacements: DecisionInsightBucket;
    skipped: DecisionInsightBucket;
    under_deployment: DecisionInsightBucket;
    etf_replacements: DecisionInsightBucket;
  };
  messages: string[];
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
  current_weight?: number;
  after_weight?: number;
  rationale: string;
  confidence: number;
  portfolio_weight?: number;
  conviction_score?: number;
  conviction_level?: string;
  score?: number;
  linked_intel?: string;
  // compact_v1 reasoning fields — aligned with Intel tab
  why?: string | null;
  risk?: string | null;
  do?: string | null;
  execution_style?: string | null;
  alt_view?: string | null;
  schema_version?: string | null;
  category?: string | null;
  features?: {
    momentum?: number | null;
    volatility?: number | null;
  } | null;
  why_selected?: string;
  execution_plan?: string;
  // Adaptive deployment (added when backend regime+adaptive layer runs)
  immediate_amount?: number | null;
  reserve_amount?: number | null;
  staging_instruction?: string | null;
  execution_timing?: string | null;
}

export type RegimeLabel = "bull" | "neutral" | "risk_off";
export type DeploymentMode = "full" | "partial" | "defensive" | "wait";

// Deploy Logic v2 — deterministic mode classifier output
export type DeploymentModeV2 =
  | "full_deploy"
  | "staged_deploy"
  | "defensive_reserve"
  | "skip_or_wait";

export type TickerRole = "Primary" | "Supporting" | "Watch";

export interface ReserveTriggerV2 {
  reserve_reason: string;
  reserve_target_tickers: string[];
  reserve_purpose: string;
  trigger_type: string;
  trigger_condition: string;
  suggested_review_event: string | null;
  suggested_review_date: string | null;
  when_to_deploy_reserve: string;
}

export interface PerTickerDeploymentV2 {
  ticker: string;
  role: TickerRole;
  amount: number;
  deploy_now: number;
  reserve: number;
  conviction_level: string;
  rationale: string;
  capped?: boolean;
  cap_reason?: string | null;
}

export interface DeploymentDecisionV2 {
  total_deposit: number;
  deploy_now_amount: number;
  reserve_amount: number;
  deployment_mode: DeploymentModeV2;
  deployment_confidence: number;
  deployment_reason: string;
  cash_drag_penalty_applied: boolean;
  reserve_reason: string | null;
  reserve_trigger: ReserveTriggerV2 | null;
  per_ticker_allocations: PerTickerDeploymentV2[];
  risks: string[];
  data_quality: "high" | "medium" | "low";
  evaluation_notes_for_future_decision_log: string[];
  deployment_score: number;
  adjustments_applied: string[];
}

export interface RegimeBlock {
  regime_label: RegimeLabel;
  regime_score: number;
  regime_reasons: string[];
  data_quality: "high" | "medium" | "low";
}

export interface AdaptiveBlock {
  deploy_percentage: number;
  deployment_mode: DeploymentMode;
  recommended_deploy_amount: number;
  cash_reserve_amount: number;
  adaptive_reasons: string[];
  adjustments_applied: string[];
  style_messages?: string[];
  behavior_profile?: {
    avg_deploy_ratio?: number;
    stable_deploy_ratio?: number;
    skip_rate?: number;
    replace_rate?: number;
    prefers_etf?: boolean;
    prefers_income?: boolean;
    growth_to_income_count?: number;
    single_to_etf_count?: number;
    avg_execution_gap_percent?: number;
    sample_size?: number;
    personalization_confidence?: "Low" | "Medium" | "High";
    adjustment_strength?: number;
    under_deployer?: boolean;
  };
}

export interface AllocationExclusion {
  ticker: string;
  reason: string;
}

export interface AllocationTrim {
  ticker: string;
  action: string;
  current_weight?: number;
  tax_note: string;
  market_note: string;
}

export interface DepositPlanResult {
  plan: {
    total_amount: number;
    strategy: string;
    generated_at: string;
    intel_summary?: string;
    recommended_deploy_amount?: number;
    cash_reserve?: number;
    deploy_percentage?: number;
    deployment_mode?: DeploymentMode;
    // v2 canonical fields
    deploy_now_amount?: number;
    reserve_amount?: number;
    deployment_mode_v2?: DeploymentModeV2;
    deployment_confidence?: number;
    deployment_reason?: string;
    cash_drag_penalty_applied?: boolean;
    reserve_reason?: string | null;
  };
  recommendations: DepositRecommendation[];
  allocations?: DepositRecommendation[];
  exclusions?: AllocationExclusion[];
  summary: {
    positions_count: number;
    total_deployed: number;
    fully_allocated: boolean;
    strategy_mode: string;
    ranked_candidates: number;
    candidates_considered?: number;
  };
  funding: {
    deposit_amount: number;
    sale_proceeds: number;
    total_cash: number;
  };
  trims: AllocationTrim[];
  notes: string[];
  warning?: string | null;
  explanation?: string;
  deployment_risks?: string[];
  regime?: RegimeBlock | null;
  adaptive?: AdaptiveBlock | null;
  deployment_v2?: DeploymentDecisionV2 | null;
  debug?: Record<string, unknown>;
}

// ── Intel v3 types (K3 snapshot contract) ────────────────────────────────────

/** Actions valid for held positions. Radar labels (WATCH/AVOID) must not appear here. */
export type IntelV3Action = "BUY" | "HOLD" | "TRIM" | "SELL";
export type IntelV3Conviction = "LOW" | "MEDIUM" | "HIGH";
export type IntelV3EvidenceBand = "THIN" | "PARTIAL" | "STRONG";

export interface IntelV3HeldCard {
  ticker: string;
  name: string;
  asset_type: string;
  action: IntelV3Action;
  conviction: IntelV3Conviction;
  evidence_band: IntelV3EvidenceBand;
  portfolio_fit: string;
  risk_level: string;
  thesis_state: string;
  why_text: string;
  risk_text: string;
  action_text: string;
  what_would_change_view: string;
  fit_text: string;
  evidence_text: string;
  flags: string[];
  source_snapshot_id: string;
  source_run_id: string;
  updated_at: string;
  detail_drawer_payload: {
    rationale: string;
    why_now: string;
    why_not_now: string;
    evidence_band: IntelV3EvidenceBand;
    evidence_quality: string;
    attractiveness: string;
    price_context: string;
    portfolio_fit_raw: string;
    risk_band: string;
    blockers: string[];
    suppression_reasons: Record<string, string>;
    schema_version: string;
    committee: { status: "deferred" | "ready"; reason?: string };
  };
}

export interface IntelV3Snapshot {
  schema_version: string;
  snapshot_id: string;
  run_id: string;
  generated_at: string;
  is_stale: boolean;
  source_health: { status: string };
  portfolio_command_center: {
    total_holdings: number;
    buy_count: number;
    hold_count: number;
    trim_count: number;
    sell_count: number;
    high_conviction: number;
    thin_evidence: number;
    source_health: { status: string };
  };
  action_counts: Record<IntelV3Action, number>;
  evidence_band_counts: Record<IntelV3EvidenceBand, number>;
  conviction_counts: Record<IntelV3Conviction, number>;
  best_buys: IntelV3HeldCard[];
  trim_sell_desk: IntelV3HeldCard[];
  current_holdings: IntelV3HeldCard[];
  opportunity_radar_preview: { status: "deferred" | "dark_launch"; reason?: string };
  what_changed: string[];
  warnings: string[];
  legacy_path_used: false;
}

export interface IntelV3RunResult {
  status: "completed" | "running" | "failed";
  snapshot_id?: string;
  run_id?: string;
  total_cards?: number;
  action_counts?: Record<IntelV3Action, number>;
}

export interface IntelV3RunStatus {
  run_id: string;
  status: "completed" | "running" | "failed";
  snapshot_id?: string;
  action_counts?: Record<IntelV3Action, number>;
  total_cards?: number;
  generated_at?: string;
}

// ── Deploy v3 types (read-only plan contract) ─────────────────────────────────

/** Plan-level readiness status literals from the backend rollup. */
export type DeployV3ReadinessStatus =
  | "no_items"
  | "all_informational"
  | "all_suppressed"
  | "ready_pending_guardrails"
  | "partially_ready"
  | "blocked"
  | "not_ready";

/** Deterministic plan-level readiness rollup from DeployPlanRollup. */
export interface DeployV3PlanRollup {
  total_items: number;
  counts_by_final_actionability_status: Record<string, number>;
  counts_by_pending_guardrails_reason: Record<string, number>;
  actionable_count: number;
  pending_count: number;
  blocked_count: number;
  informational_count: number;
  suppressed_count: number;
  not_ready_count: number;
  unknown_count: number;
  plan_readiness_status: DeployV3ReadinessStatus | string;
  schema_version: string;
}

/** Per-item scaffold from the Deploy plan (read-only display). */
export interface DeployV3PlanItem {
  ticker: string;
  intel_action: string;
  actionability_status: string;
  action_source: string;
  intel_snapshot_id: string;
  intel_run_id: string;
  plan_status: string;
  recommended_dollar_amount: number | null;
  final_actionability_status: string;
  pending_guardrails_reason: string;
  suppression_reason: string | null;
  schema_version: string;
}

/** Guardrail integrity summary for a Deploy plan run. */
export interface DeployV3GuardrailSummary {
  total_items: number;
  buy_candidates: number;
  trim_candidates: number;
  sell_candidates: number;
  hold_items: number;
  suppressed_items: number;
  hold_never_actionable: boolean;
  dollar_fields_null: boolean;
  exact_dollar_math_evaluated: boolean;
  priceband_not_authority: boolean;
  intel_action_preserved: boolean;
  schema_version: string;
}

/** Full response from GET /api/v1/deploy/v3/plan. */
export interface DeployV3PlanResponse {
  plan_status: string;
  snapshot_id: string;
  run_id: string;
  schema_version: string;
  items: DeployV3PlanItem[];
  guardrail_summary: DeployV3GuardrailSummary | null;
  rollup: DeployV3PlanRollup | null;
  source: {
    intel_source: string;
    sizing_bundle_provided: boolean;
    note: string;
    exact_dollar_ready?: boolean;
    sizing_values_ready?: boolean;
    target_allocation_ready?: boolean;
    policy_ready?: boolean;
    suppression_reasons?: string[];
    cash_source?: string | null;
    portfolio_source?: string | null;
  };
}
