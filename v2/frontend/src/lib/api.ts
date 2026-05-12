/**
 * API client for the FastAPI backend.
 * All requests include the Supabase JWT for authentication.
 */

import { supabase } from "./supabase";
import { DEPLOY_V3_PLAN_ENDPOINT, DEPLOY_V3_READINESS_ENDPOINT } from "./deploy-v3-helpers";

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

export const api = {
  portfolio: {
    getSummary: () => fetchApi<PortfolioSummary>("/api/v1/portfolio/summary"),
    getRebalance: (cashToDeploy?: number) =>
      fetchApi<RebalanceResult>(
        `/api/v1/portfolio/rebalance${cashToDeploy !== undefined ? `?cash_to_deploy=${cashToDeploy}` : ""}`
      ),
    getTargets: () => fetchApi<TargetAllocation[]>("/api/v1/portfolio/targets"),
    setTargets: (targets: Array<{ ticker: string; target_pct: number }>) =>
      fetchApi<{ saved: number }>("/api/v1/portfolio/targets", {
        method: "PUT",
        body: JSON.stringify({ targets }),
      }),
    getSnapshots: (limit = 50) =>
      fetchApi<Snapshot[]>(`/api/v1/portfolio/snapshots?limit=${limit}`),
    createSnapshot: () =>
      fetchApi<Snapshot>("/api/v1/portfolio/snapshots", { method: "POST" }),
    backfillSnapshots: () =>
      fetchApi<BackfillResult>("/api/v1/portfolio/snapshots/backfill", { method: "POST" }),
    getCash: () => fetchApi<CashBalance>("/api/v1/portfolio/cash"),
    setCash: (amount: number | null) =>
      fetchApi<CashBalance>("/api/v1/portfolio/cash", {
        method: "PUT",
        body: JSON.stringify({ cash_balance: amount }),
      }),
  },

  positions: {
    list: (category?: string) =>
      fetchApi<Position[]>(`/api/v1/positions${category ? `?category=${category}` : ""}`),
    get: (ticker: string) =>
      fetchApi<Position>(`/api/v1/positions/${ticker}`),
  },

  prices: {
    batch: (tickers: string[]) =>
      fetchApi<PriceBatch>(`/api/v1/prices/batch?tickers=${tickers.join(",")}`),
    history: (ticker: string, period: string) =>
      fetchApi<PriceHistory>(`/api/v1/prices/history/${ticker}?period=${period}`),
    health: () => fetchApi<PriceHealth>("/api/v1/prices/health"),
  },

  recommendations: {
    list: (action?: string) =>
      fetchApi<Recommendation[]>(
        `/api/v1/recommendations${action ? `?action=${action}` : ""}`
      ),
    refresh: (body?: { deposit_amount?: number; sale_proceeds?: number }) =>
      fetchApi<AgentJobResponse>("/api/v1/recommendations/refresh", {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      }),
    getJob: (jobId: string) =>
      fetchApi<AgentJobResponse>(`/api/v1/recommendations/job/${jobId}`),
    getLatestJob: () => fetchApi<AgentJobResponse>("/api/v1/recommendations/job/latest"),
    getLatestInsights: () =>
      fetchApi<LatestAgentInsights>("/api/v1/recommendations/insights/latest"),
    resolve: (recId: string, resolution: string, notes?: string) =>
      fetchApi<void>(`/api/v1/recommendations/${recId}/resolve`, {
        method: "POST",
        body: JSON.stringify({ resolution, notes }),
      }),
    getDecisions: (limit = 50) =>
      fetchApi<DecisionLogEntry[]>(`/api/v1/recommendations/decisions?limit=${limit}`),
    getOutcomes: () =>
      fetchApi<DecisionOutcome[]>("/api/v1/recommendations/outcomes"),
  },

  deposits: {
    getPlan: (cashToInvest: number, portfolioBalance: number) =>
      fetchLocal<DepositPlanResult>(
        `/api/deposit-plan?cash_to_invest=${cashToInvest}`
      ),
  },

  decisionLogs: {
    createDecisionLog: (snapshot: Record<string, unknown>, actualDecisions?: ActualDecisionItem[], opts?: { notes?: string; source?: string }) =>
      fetchApi<DecisionMemoryLog>("/api/v1/decision-logs", {
        method: "POST",
        body: JSON.stringify({ recommendation_snapshot: snapshot, source: opts?.source ?? "deploy", actual_decisions: actualDecisions ?? [], notes: opts?.notes ?? null }),
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

  auth: {
    me: () => fetchApi<UserProfile>("/api/v1/auth/me"),
    updateProfile: (data: Partial<UserProfile>) =>
      fetchApi<UserProfile>("/api/v1/auth/me", {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    updateApiKeys: (keys: { openai_key?: string; anthropic_key?: string }) =>
      fetchApi<UserProfile>("/api/v1/auth/me/api-keys", {
        method: "PATCH",
        body: JSON.stringify(keys),
      }),
  },

  ai: {
    getLatest: () => fetchApi<AiAnalysis>("/api/v1/ai/latest"),
    rebalance: () =>
      fetchApi<AiRebalanceResult>("/api/v1/ai/rebalance", { method: "POST" }),
  },

  sync: {
    plaidStatus: () => fetchApi<PlaidStatus>("/api/v1/sync/plaid/status"),
    plaid: (force: boolean) =>
      fetchApi<SyncResult>("/api/v1/sync/plaid", {
        method: "POST",
        body: JSON.stringify({ force }),
      }),
    refreshPrices: () =>
      fetchApi<SyncResult>("/api/v1/sync/prices", { method: "POST" }),
    importCsv: (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return fetchApi<ImportResult>("/api/v1/sync/import/csv", {
        method: "POST",
        body: formData,
        headers: {},
      });
    },
    importPdf: (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return fetchApi<ImportResult>("/api/v1/sync/import/pdf", {
        method: "POST",
        body: formData,
        headers: {},
      });
    },
  },

  drip: {
    getSummary: () => fetchApi<DripSummary>("/api/v1/drip/summary"),
    getPositions: () => fetchApi<DripPosition[]>("/api/v1/drip/positions"),
    getHistory: () => fetchApi<DripHistory>("/api/v1/drip/history"),
  },

  intelV3: {
    getSnapshot: () => fetchApi<IntelV3Snapshot>("/api/v1/intel/v3/snapshot"),
    runV3: () => fetchApi<IntelV3RunResult>("/api/v1/intel/v3/run", {
      method: "POST",
      body: JSON.stringify({}),
    }),
    getRunStatus: (runId: string) => fetchApi<IntelV3RunResult>(`/api/v1/intel/v3/run/${runId}`),
  },

  deployV3: {
    getPlan: () => fetchApi<DeployV3PlanResponse>(DEPLOY_V3_PLAN_ENDPOINT),
    getReadiness: () => fetchApi<DeployV3ReadinessDiagnostic>(DEPLOY_V3_READINESS_ENDPOINT),
  },
};
