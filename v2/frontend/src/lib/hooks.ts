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
