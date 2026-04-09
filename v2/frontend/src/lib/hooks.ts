"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
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

export function useRecommendations(action?: string) {
  return useQuery({
    queryKey: ["recommendations", action],
    queryFn: () => api.recommendations.list(action),
  });
}

export function useRefreshRecommendations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.recommendations.refresh,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recommendations"] }),
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

export function useDecisionLog(limit = 50) {
  return useQuery({
    queryKey: ["recommendations", "decisions", limit],
    queryFn: () => api.recommendations.getDecisions(limit),
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
  return useMutation({
    mutationFn: api.auth.updateApiKeys,
  });
}

// ── AI ────────────────────────────────────────────────────────────────────────

export function useAiRebalance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.ai.rebalance,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["portfolio", "targets"] }),
  });
}
