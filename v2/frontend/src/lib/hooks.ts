"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { IntelV3Snapshot, IntelV3SessionStatus } from "./api";
import { isTerminalSessionStatus } from "./advisor-readiness";

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
  /** Starts one Run Intel session (or adopts the active one) — the single control. */
  mutate: () => void;
  /** True from the click (or recovered session) until the session is terminal. */
  isPending: boolean;
  /** True once the create request itself (network/HTTP/auth) failed. */
  isError: boolean;
  /** The create-request error, if any. */
  error: Error | null;
  /** Latest session-status payload (create response or poll result). */
  data: IntelV3SessionStatus | null;
}

/** Poll cadence for GET /intel/v3/sessions/{id}/status while a run executes. */
export const RUN_INTEL_POLL_INTERVAL_MS = 2_500;

/**
 * Distributed Run Intel workflow — create one durable session, then observe.
 *
 * A click mints ONE browser UUID (crypto.randomUUID()) and sends ONE
 * POST /intel/v3/run. The backend executes the whole run on its own; this
 * hook then only polls GET /sessions/{id}/status every ~2.5s until the
 * session reaches a terminal state. It NEVER re-POSTs /run automatically.
 *
 * - Unmounting stops polling only — backend work continues untouched.
 * - On mount, GET /sessions/active runs once; if a session is still live
 *   (e.g. the user navigated away and back), polling resumes on that id.
 * - A click while a session is active is safe: the backend adopts the
 *   active session (adopted_active_session=true) and we poll the id it
 *   returns.
 * - When a terminal completed/completed_with_gaps status carries a
 *   completed_snapshot_id, the ["intel_v3","snapshot"] query is invalidated
 *   so the fresh snapshot loads.
 */
export function useRunIntelV3(): UseRunIntelV3Result {
  const qc = useQueryClient();
  const [isPending, setIsPending] = useState(false);
  const [isError, setIsError] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [data, setData] = useState<IntelV3SessionStatus | null>(null);
  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Session id currently being observed; null when no observation is active. */
  const observedSessionIdRef = useRef<string | null>(null);
  /** Guards against double-invalidating for the same published snapshot. */
  const invalidatedSnapshotIdRef = useRef<string | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // Stable machinery via refs so mount-effect/mutate need no dep churn.
  const handleStatusRef = useRef<(sessionId: string, status: IntelV3SessionStatus) => void>(() => {});

  const pollOnce = useCallback(async (sessionId: string): Promise<void> => {
    if (!mountedRef.current || observedSessionIdRef.current !== sessionId) return;
    let status: IntelV3SessionStatus;
    try {
      status = await api.intelV3.getSessionStatus(sessionId);
    } catch {
      // Transient poll failure (network blip): the backend keeps working
      // regardless, so keep observing rather than declaring the run failed.
      if (!mountedRef.current || observedSessionIdRef.current !== sessionId) return;
      pollTimerRef.current = setTimeout(() => {
        void pollOnce(sessionId);
      }, RUN_INTEL_POLL_INTERVAL_MS);
      return;
    }
    if (!mountedRef.current || observedSessionIdRef.current !== sessionId) return;
    handleStatusRef.current(sessionId, status);
  }, []);

  handleStatusRef.current = (sessionId: string, status: IntelV3SessionStatus) => {
    setData(status);
    if (isTerminalSessionStatus(status)) {
      observedSessionIdRef.current = null;
      stopPolling();
      setIsPending(false);
      const sessionStatus = status.session_status;
      const snapshotId = status.completed_snapshot_id ?? null;
      if (
        (sessionStatus === "completed" || sessionStatus === "completed_with_gaps") &&
        snapshotId &&
        invalidatedSnapshotIdRef.current !== snapshotId
      ) {
        invalidatedSnapshotIdRef.current = snapshotId;
        qc.invalidateQueries({ queryKey: ["intel_v3", "snapshot"] });
      }
      return;
    }
    // Still executing — observe again shortly. Polling never advances work.
    stopPolling();
    pollTimerRef.current = setTimeout(() => {
      void pollOnce(sessionId);
    }, RUN_INTEL_POLL_INTERVAL_MS);
  };

  // Mount: rediscover an active backend session once and resume observing it.
  // Unmount: stop polling only — no abort, backend work continues.
  useEffect(() => {
    mountedRef.current = true;
    void (async () => {
      let active;
      try {
        active = await api.intelV3.getActiveSession();
      } catch {
        return; // Recovery is best-effort; a later click still works.
      }
      if (!mountedRef.current || !active.active) return;
      // A click that happened before recovery resolved wins.
      if (observedSessionIdRef.current !== null) return;
      const sessionId = active.run_session_id;
      if (!sessionId) return;
      observedSessionIdRef.current = sessionId;
      setIsPending(true);
      setIsError(false);
      handleStatusRef.current(sessionId, active);
    })();
    return () => {
      mountedRef.current = false;
      stopPolling();
      observedSessionIdRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const mutate = useCallback(() => {
    // ONE uuid per explicit manual click; a later click mints a fresh one.
    const runSessionId = crypto.randomUUID();
    stopPolling();
    observedSessionIdRef.current = runSessionId;
    setIsPending(true);
    setIsError(false);
    setError(null);
    void (async () => {
      let result: IntelV3SessionStatus;
      try {
        result = await api.intelV3.runV3(runSessionId);
      } catch (err) {
        if (!mountedRef.current || observedSessionIdRef.current !== runSessionId) return;
        observedSessionIdRef.current = null;
        setIsError(true);
        setError(err instanceof Error ? err : new Error(String(err)));
        setIsPending(false);
        return;
      }
      if (!mountedRef.current || observedSessionIdRef.current !== runSessionId) return;
      // The backend may adopt an already-active session — poll the id it
      // reports, never a second POST.
      const effectiveId = result.run_session_id || runSessionId;
      observedSessionIdRef.current = effectiveId;
      handleStatusRef.current(effectiveId, result);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopPolling]);

  return { mutate, isPending, isError, error, data };
}

