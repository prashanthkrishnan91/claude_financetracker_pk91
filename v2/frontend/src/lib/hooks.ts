"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { IntelV3Snapshot, IntelV3RunResult } from "./api";
import { shouldAutoContinueRun } from "./advisor-readiness";

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

export interface UseRunIntelV3Result {
  /** Starts (or restarts) one bounded Run Intel click — the single control. */
  mutate: () => void;
  /** True from the initial request through every automatic continuation. */
  isPending: boolean;
  /** True once the request itself (network/HTTP/auth) failed. */
  isError: boolean;
  /** Most recent POST /intel/v3/run result (last continuation batch). */
  data: IntelV3RunResult | null;
}

/**
 * Stage 3.3 / Part A3 — Enqueue an Intel v3 analyst refresh with automatic
 * bounded continuation.
 *
 * POST /intel/v3/run returns a refresh-enqueue status, NOT a snapshot, and is
 * itself bounded to a small server-side quantum (see
 * analyst_refresh_on_demand_drain_v1) so one request never hangs. A full
 * portfolio needs several quanta to drain. Historically this required the
 * user to keep clicking "Continue Intel run"; this hook instead keeps firing
 * bounded continuation requests on the user's behalf — from the SAME single
 * click — while `shouldAutoContinueRun` (advisor-readiness.ts) says the run
 * is still "partial" and neither the attempt cap nor the elapsed-time cap has
 * been reached. It never polls before the click, never continues past a
 * terminal/failed/complete state, and aborts in-flight work on unmount.
 *
 * On every successful batch, invalidates the snapshot query so the Advisor
 * page re-fetches — same as before this hook grew continuation logic.
 */
export function useRunIntelV3(): UseRunIntelV3Result {
  const qc = useQueryClient();
  const [isPending, setIsPending] = useState(false);
  const [isError, setIsError] = useState(false);
  const [data, setData] = useState<IntelV3RunResult | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortControllerRef.current?.abort();
    };
  }, []);

  const mutate = useCallback(() => {
    const controller = new AbortController();
    abortControllerRef.current = controller;
    const startedAt = Date.now();
    setIsPending(true);
    setIsError(false);

    const step = async (attempt: number): Promise<void> => {
      let result: IntelV3RunResult;
      try {
        result = await api.intelV3.runV3(controller.signal);
      } catch (err) {
        if (controller.signal.aborted || !mountedRef.current) return;
        setIsError(true);
        setIsPending(false);
        return;
      }
      if (controller.signal.aborted || !mountedRef.current) return;

      setData(result);
      qc.invalidateQueries({ queryKey: ["intel_v3", "snapshot"] });

      const elapsedMs = Date.now() - startedAt;
      if (shouldAutoContinueRun(result, attempt, elapsedMs)) {
        await step(attempt + 1);
      } else {
        setIsPending(false);
      }
    };

    void step(1);
  }, [qc]);

  return { mutate, isPending, isError, data };
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

