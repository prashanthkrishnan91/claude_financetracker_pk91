"use client";

import { QueryClient, useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

// ── Portfolio ────────────────────────────────────────────────────────────────

export function usePortfolioSummary() {
  return useQuery({
    queryKey: ["portfolio", "summary"],
    queryFn: api.portfolio.getSummary,
    staleTime: 60_000,
  });
}

export function useSnapshots(limit = 50) {
  return useQuery({
    queryKey: ["portfolio", "snapshots", limit],
    queryFn: () => api.portfolio.getSnapshots(limit),
  });
}

export function useCreateSnapshot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.portfolio.createSnapshot,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "snapshots"] }),
  });
}

export function useBackfillSnapshots() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.portfolio.backfillSnapshots,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "snapshots"] }),
  });
}

export function useRebalance(cashToDeploy?: number) {
  return useQuery({
    queryKey: ["portfolio", "rebalance", cashToDeploy],
    queryFn: () => api.portfolio.getRebalance(cashToDeploy),
    enabled: false, // Manual trigger only
  });
}

export function useTargets() {
  return useQuery({
    queryKey: ["portfolio", "targets"],
    queryFn: api.portfolio.getTargets,
  });
}

// ── Positions ────────────────────────────────────────────────────────────────

export function usePositions(category?: string) {
  return useQuery({
    queryKey: ["positions", category],
    queryFn: () => api.positions.list(category),
    staleTime: 60_000,
  });
}

export function usePosition(ticker: string) {
  return useQuery({
    queryKey: ["positions", ticker],
    queryFn: () => api.positions.get(ticker),
    enabled: !!ticker,
  });
}

// ── Prices ───────────────────────────────────────────────────────────────────

export function useBatchPrices(tickers: string[]) {
  return useQuery({
    queryKey: ["prices", "batch", tickers],
    queryFn: () => api.prices.batch(tickers),
    enabled: tickers.length > 0,
    staleTime: 60_000,
  });
}

export function usePriceHistory(ticker: string, period = "1Y") {
  return useQuery({
    queryKey: ["prices", "history", ticker, period],
    queryFn: () => api.prices.history(ticker, period),
    enabled: !!ticker,
    staleTime: 5 * 60_000,
  });
}

export function usePriceHealth() {
  return useQuery({
    queryKey: ["prices", "health"],
    queryFn: api.prices.health,
  });
}

// ── Recommendations ──────────────────────────────────────────────────────────

export function invalidateRecommendationAggregateQueries(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: ["recommendations"] });
  qc.invalidateQueries({ queryKey: ["recommendations", "insights"] });
  qc.invalidateQueries({ queryKey: ["recommendations", "job"] });
}

export function useRecommendations(action?: string) {
  return useQuery({
    queryKey: ["recommendations", action],
    queryFn: () => api.recommendations.list(action),
    staleTime: 20_000,
    refetchOnWindowFocus: false,
  });
}

export function useRefreshRecommendations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body?: { deposit_amount?: number; sale_proceeds?: number }) =>
      api.recommendations.refresh(body),
    onMutate: async () => {
      // A fresh run was requested — don't wait for staleTime/TTL to elapse.
      invalidateRecommendationAggregateQueries(qc);
    },
    onSuccess: () => {
      invalidateRecommendationAggregateQueries(qc);
    },
  });
}

// Polling interval (ms) while an agent run is in-flight. Matches the SEV-1
// spec — 3s cadence strikes a balance between perceived responsiveness and
// avoiding request storms on slow networks.
export const AGENT_JOB_POLL_MS = 3000;
// Max poll duration is 10 minutes.
export const AGENT_JOB_MAX_POLLS = Math.ceil((10 * 60 * 1000) / AGENT_JOB_POLL_MS);
const TERMINAL_AGENT_STATUSES = new Set(["completed", "failed", "cancelled"]);

export function useAgentJob(jobId: string | null) {
  return useQuery({
    queryKey: ["recommendations", "job", jobId],
    queryFn: async () => {
      if (!jobId) return null;
      return api.recommendations.getJob(jobId);
    },
    enabled: !!jobId,
    // Poll on a 3s cadence until the run is terminal, capped at
    // AGENT_JOB_MAX_POLLS attempts to prevent runaway polling.
    refetchInterval: (query) => {
      const data = query.state.data;
      // Terminal stop condition is explicit to avoid fetch storms:
      // once completed/failed/cancelled, polling is permanently disabled.
      const terminal = data?.status ? TERMINAL_AGENT_STATUSES.has(data.status) : false;
      if (terminal) return false;
      const attempts = query.state.dataUpdateCount ?? 0;
      if (attempts >= AGENT_JOB_MAX_POLLS) return false;
      return AGENT_JOB_POLL_MS;
    },
    refetchOnWindowFocus: false,
  });
}

export function useLatestAgentInsights() {
  return useQuery({
    queryKey: ["recommendations", "insights", "latest"],
    queryFn: api.recommendations.getLatestInsights,
    staleTime: 30_000,
  });
}

export function useLatestAgentRun(enabled = true) {
  return useQuery({
    queryKey: ["recommendations", "job", "latest"],
    queryFn: api.recommendations.getLatestJob,
    enabled,
    staleTime: 0,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return data.status === "running" || data.status === "queued" || data.status === "in_progress" ? 2000 : false;
    },
    refetchOnWindowFocus: false,
  });
}

// ── Sync ─────────────────────────────────────────────────────────────────────

export function usePlaidStatus() {
  return useQuery({
    queryKey: ["sync", "plaid", "status"],
    queryFn: api.sync.plaidStatus,
    staleTime: 5 * 60_000,
  });
}

export function useSyncPlaid() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (force: boolean) => api.sync.plaid(force),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["positions"] });
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      qc.invalidateQueries({ queryKey: ["sync", "plaid"] });
    },
  });
}

export function useRefreshPrices() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.sync.refreshPrices,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["prices"] });
      qc.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });
}

export function useImportCsv() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.sync.importCsv(file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["positions"] });
      qc.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });
}

export function useImportPdf() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.sync.importPdf(file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["positions"] });
      qc.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });
}

// ── DRIP ─────────────────────────────────────────────────────────────────────

export function useDripSummary() {
  return useQuery({
    queryKey: ["drip", "summary"],
    queryFn: api.drip.getSummary,
    staleTime: 5 * 60_000,
  });
}

export function useDripPositions() {
  return useQuery({
    queryKey: ["drip", "positions"],
    queryFn: api.drip.getPositions,
    staleTime: 5 * 60_000,
  });
}

export function useDripHistory() {
  return useQuery({
    queryKey: ["drip", "history"],
    queryFn: api.drip.getHistory,
    staleTime: 10 * 60_000,
  });
}

// ── Cash Override ─────────────────────────────────────────────────────────────

export function useCashBalance() {
  return useQuery({
    queryKey: ["portfolio", "cash"],
    queryFn: api.portfolio.getCash,
    staleTime: 60_000,
  });
}

export function useSetCash() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (amount: number | null) => api.portfolio.setCash(amount),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portfolio"] });
    },
  });
}

// ── Recommendations Resolve & Decision Log ────────────────────────────────────

export function useResolveRecommendation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      recId,
      resolution,
      notes,
    }: {
      recId: string;
      resolution: string;
      notes?: string;
    }) => api.recommendations.resolve(recId, resolution, notes),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recommendations"] }),
  });
}

export function useDecisionLog(limit = 50, enabled = true) {
  return useQuery({
    queryKey: ["recommendations", "decisions", limit],
    queryFn: () => api.recommendations.getDecisions(limit),
    enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
}

export function useDecisionOutcomes() {
  return useQuery({
    queryKey: ["recommendations", "outcomes"],
    queryFn: api.recommendations.getOutcomes,
    staleTime: 60_000,
  });
}

export function useLogDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ snapshot }: { snapshot: Record<string, unknown> }) =>
      api.decisionLogs.createDecisionLog(snapshot),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["decision-logs"] }),
  });
}

export function useDepositPlan(cashToInvest = 0, portfolioBalance = 0) {
  return useQuery({
    queryKey: ["deposits", "plan", cashToInvest, portfolioBalance],
    queryFn: () => api.deposits.getPlan(cashToInvest, portfolioBalance),
    staleTime: 60_000,
  });
}

// ── Auth / Profile ────────────────────────────────────────────────────────────

export function useUserProfile() {
  return useQuery({
    queryKey: ["auth", "me"],
    queryFn: api.auth.me,
    staleTime: 5 * 60_000,
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.auth.updateProfile,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["auth", "me"] }),
  });
}

export function useUpdateApiKeys() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.auth.updateApiKeys,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["auth", "me"] }),
  });
}

// ── AI ────────────────────────────────────────────────────────────────────────

export function useAiLatestAnalysis() {
  return useQuery({
    queryKey: ["ai", "latest"],
    queryFn: api.ai.getLatest,
    staleTime: 5 * 60_000,
  });
}

export function useAiRebalance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.ai.rebalance,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["portfolio", "targets"] });
      qc.invalidateQueries({ queryKey: ["ai", "latest"] });
    },
  });
}

// ── Analytics ─────────────────────────────────────────────────────────────────

export function useStrategyPerformance() {
  return useQuery({
    queryKey: ["analytics", "strategy-performance"],
    queryFn: api.analytics.getStrategyPerformance,
    staleTime: 5 * 60_000,
  });
}


export function useDecisionMemoryLogs(limit = 10, enabled = true) {
  return useQuery({
    queryKey: ["decision-logs", limit],
    queryFn: () => api.decisionLogs.listDecisionLogs(limit),
    enabled,
    staleTime: 30_000,
  });
}

export function useCreateDecisionMemoryLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ snapshot }: { snapshot: Record<string, unknown> }) =>
      api.decisionLogs.createDecisionLog(snapshot),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["decision-logs"] }),
  });
}

export function useUpdateDecisionMemoryLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: { actual_decisions?: Array<Record<string, unknown>>; notes?: string } }) =>
      api.decisionLogs.updateDecisionLog(id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["decision-logs"] }),
  });
}

export function useEvaluateDecisionMemoryLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.decisionLogs.evaluateDecisionLog(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["decision-logs"] }),
  });
}
