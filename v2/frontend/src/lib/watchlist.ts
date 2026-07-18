"use client";

/**
 * Watchlist — typed API client + React Query hooks + pure form/error helpers.
 *
 * Backend contract (v2/backend/app/routers/watchlist.py):
 * - GET    /api/v1/watchlist            → WatchlistItem[]
 * - POST   /api/v1/watchlist            → 201 | 409 duplicate | 503 migration
 * - PATCH  /api/v1/watchlist/{id}       → item | 409 | 404 | 503
 * - DELETE /api/v1/watchlist/{id}       → 204
 *
 * 409/503 responses carry detail: { error, message } — those messages are
 * surfaced verbatim to the UI via WatchlistApiError.
 *
 * fetchApi in ./api is module-private, so the same Bearer-token fetch pattern
 * is replicated locally, with structured-detail error extraction added.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { supabase } from "./supabase";

// Mirror of api.ts base-URL handling (enforce HTTPS on HTTPS pages).
const _rawBase = process.env.NEXT_PUBLIC_API_URL || "";
const API_BASE =
  typeof window !== "undefined" &&
  window.location.protocol === "https:" &&
  _rawBase.startsWith("http://")
    ? _rawBase.replace("http://", "https://")
    : _rawBase;

// ── Types ─────────────────────────────────────────────────────────────────────

export type WatchlistCriteriaType = "price_below" | "price_above";

export interface WatchlistItem {
  id: string;
  ticker: string;
  criteria_type: WatchlistCriteriaType;
  threshold: number;
  notes: string | null;
  created_at: string;
  updated_at: string | null;
  current_price: number | null;
  price_as_of: string | null;
  /** True/False with a trusted price; null = unknown (no trusted price). */
  criteria_met: boolean | null;
}

export interface WatchlistCreatePayload {
  ticker: string;
  criteria_type: WatchlistCriteriaType;
  threshold: number;
  notes?: string | null;
}

export interface WatchlistUpdatePayload {
  criteria_type?: WatchlistCriteriaType;
  threshold?: number;
  notes?: string | null;
}

// ── Errors ────────────────────────────────────────────────────────────────────

export class WatchlistApiError extends Error {
  status: number;
  /** Backend error code, e.g. "duplicate_watchlist_entry" | "watchlist_migration_required". */
  code: string | null;

  constructor(status: number, code: string | null, message: string) {
    super(message);
    this.name = "WatchlistApiError";
    this.status = status;
    this.code = code;
    Object.setPrototypeOf(this, WatchlistApiError.prototype);
  }
}

/**
 * Pure: extract { code, message } from a FastAPI error body. Handles both
 * detail-as-string and detail-as-{error,message} shapes without ever
 * rendering "[object Object]".
 */
export function extractApiErrorInfo(
  body: unknown,
  status: number
): { code: string | null; message: string } {
  const fallback = `Request failed (HTTP ${status}).`;
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) {
      return { code: null, message: detail };
    }
    if (detail && typeof detail === "object") {
      const d = detail as { error?: unknown; message?: unknown };
      const code = typeof d.error === "string" ? d.error : null;
      const message =
        typeof d.message === "string" && d.message.trim() ? d.message : fallback;
      return { code, message };
    }
  }
  return { code: null, message: fallback };
}

export function isMigrationRequiredError(error: unknown): boolean {
  return (
    error instanceof WatchlistApiError &&
    (error.code === "watchlist_migration_required" || error.status === 503)
  );
}

export function isDuplicateEntryError(error: unknown): boolean {
  return (
    error instanceof WatchlistApiError &&
    (error.code === "duplicate_watchlist_entry" || error.status === 409)
  );
}

// ── Fetch helper ──────────────────────────────────────────────────────────────

async function watchlistFetch<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers: HeadersInit = { "Content-Type": "application/json" };
  if (session?.access_token) {
    headers["Authorization"] = `Bearer ${session.access_token}`;
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: { ...headers, ...options.headers },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const { code, message } = extractApiErrorInfo(body, response.status);
    throw new WatchlistApiError(response.status, code, message);
  }

  if (response.status === 204) return undefined as T;
  return response.json();
}

export const watchlistApi = {
  list: () => watchlistFetch<WatchlistItem[]>("/api/v1/watchlist"),
  create: (payload: WatchlistCreatePayload) =>
    watchlistFetch<WatchlistItem>("/api/v1/watchlist", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  update: (id: string, payload: WatchlistUpdatePayload) =>
    watchlistFetch<WatchlistItem>(`/api/v1/watchlist/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  remove: (id: string) =>
    watchlistFetch<void>(`/api/v1/watchlist/${id}`, { method: "DELETE" }),
};

// ── Hooks ─────────────────────────────────────────────────────────────────────

export const WATCHLIST_QUERY_KEY = ["watchlist"] as const;

export function useWatchlist() {
  return useQuery<WatchlistItem[], Error>({
    queryKey: [...WATCHLIST_QUERY_KEY],
    queryFn: watchlistApi.list,
    staleTime: 60_000,
    retry: (failureCount, error) => {
      // Migration-required is a stable operational state — retrying won't help.
      if (isMigrationRequiredError(error)) return false;
      return failureCount < 2;
    },
  });
}

export function useCreateWatchlistItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: WatchlistCreatePayload) => watchlistApi.create(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...WATCHLIST_QUERY_KEY] }),
  });
}

export function useUpdateWatchlistItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: WatchlistUpdatePayload }) =>
      watchlistApi.update(id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...WATCHLIST_QUERY_KEY] }),
  });
}

export function useDeleteWatchlistItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => watchlistApi.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...WATCHLIST_QUERY_KEY] }),
  });
}

// ── Pure form helpers (tested) ────────────────────────────────────────────────

/** Mirror of the backend's conservative ticker shape (VTI, BRK.B, BTC-USD). */
const TICKER_RE = /^[A-Z0-9]{1,10}([.-][A-Z0-9]{1,6})?$/;
const MAX_THRESHOLD = 10_000_000;
const MAX_NOTES_LENGTH = 500;

export function normalizeTicker(raw: string): string {
  return (raw || "").trim().toUpperCase();
}

export interface WatchlistFormErrors {
  ticker?: string;
  threshold?: string;
  notes?: string;
}

/**
 * Pure client-side validation matching the backend's constraints.
 * Returns {} when the input is valid.
 */
export function validateWatchlistInput(input: {
  ticker: string;
  threshold: string | number;
  notes?: string;
}): WatchlistFormErrors {
  const errors: WatchlistFormErrors = {};

  const ticker = normalizeTicker(input.ticker);
  if (!ticker) {
    errors.ticker = "Enter a ticker symbol.";
  } else if (!TICKER_RE.test(ticker)) {
    errors.ticker =
      "Ticker must be 1-10 letters/digits, optionally with one '.' or '-' (examples: VTI, BRK.B, BTC-USD).";
  }

  const threshold =
    typeof input.threshold === "number" ? input.threshold : Number(input.threshold);
  if (input.threshold === "" || Number.isNaN(threshold)) {
    errors.threshold = "Enter a price threshold.";
  } else if (!isFinite(threshold) || threshold <= 0) {
    errors.threshold = "Threshold must be a positive price.";
  } else if (threshold > MAX_THRESHOLD) {
    errors.threshold = "Threshold must be $10,000,000 or less.";
  }

  if (input.notes && input.notes.trim().length > MAX_NOTES_LENGTH) {
    errors.notes = `Note must be ${MAX_NOTES_LENGTH} characters or fewer.`;
  }

  return errors;
}

/** Criterion type → plain-English label for the form select. */
export function criteriaTypeLabel(type: WatchlistCriteriaType): string {
  return type === "price_below" ? "Price falls below" : "Price rises above";
}

/** Full plain-English criterion sentence, e.g. "Price falls below $215.00". */
export function formatCriteriaSentence(
  type: WatchlistCriteriaType,
  threshold: number
): string {
  const amount = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(threshold);
  return `${criteriaTypeLabel(type)} ${amount}`;
}

export type CriteriaStatus = "met" | "not_met" | "unknown";

/** Deterministic criterion status from the backend's tri-state criteria_met. */
export function criteriaStatus(item: Pick<WatchlistItem, "criteria_met">): CriteriaStatus {
  if (item.criteria_met === true) return "met";
  if (item.criteria_met === false) return "not_met";
  return "unknown";
}

export function criteriaStatusLabel(status: CriteriaStatus): string {
  switch (status) {
    case "met":     return "Criteria met";
    case "not_met": return "Not met";
    default:        return "Unknown — no trusted current price";
  }
}
