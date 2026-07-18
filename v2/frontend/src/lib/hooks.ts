"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { IntelV3Snapshot, IntelV3RunResult } from "./api";

// ── Portfolio ────────────────────────────────────────────

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

export function useTargets() {
  return useQuery({
    queryKey: ["portfolio", "targets"],
    queryFn: api.portfolio.getTargets,
  });
}

// ── Positions ────────────────────────────────────────────

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

// ── Prices ───────────────────────────────────────────────

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

// ── Sync ─────────────────────────────────────────────────

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

// ── Cash Override ─────────────────────────────────────────────

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

// ── Auth / Profile ────────────────────────────────────────────

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

// ── Intel v3 snapshot ────────────────────────────────────────────

/** Read the latest Intel v3 snapshot. Zero LLM calls on this path. */
export function useIntelV3Snapshot(enabled = true) {
  return useQuery<IntelV3Snapshot>({
    queryKey: ["intel_v3", "snapshot"],
    queryFn: api.intelV3.getSnapshot,
    enabled,
    staleTime: 60_000,
    retry: (failureCount, error: unknown) => {
      // Do not retry on 404 (no snapshot yet).
      if (error instanceof Error && error.message.includes("404")) return false;
      return failureCount < 2;
    },
  });
}

/**
 * Stage 3.3 — Enqueue an Intel v3 analyst refresh.
 * POST /intel/v3/run returns a refresh-enqueue status, NOT a snapshot.
 * On success, invalidates the snapshot query so the UI immediately re-fetches.
 * The cockpit polls the snapshot until snapshot_source=worker_certified appears.
 */
export function useRunIntelV3() {
  const qc = useQueryClient();
  return useMutation<IntelV3RunResult>({
    mutationFn: api.intelV3.runV3,
    onSuccess: () => {
      // Immediately refetch the snapshot (still shows old certified snapshot if any).
      qc.invalidateQueries({ queryKey: ["intel_v3", "snapshot"] });
    },
  });
}

/** Poll a v3 run status by run_id. */
export function useIntelV3RunStatus(runId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["intel_v3", "run", runId],
    queryFn: () => api.intelV3.getRunStatus(runId!),
    enabled: enabled && !!runId,
    refetchInterval: 2_000,
    staleTime: 0,
  });
}

