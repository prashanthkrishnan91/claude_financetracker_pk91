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

// ── Portfolio ────────────────────────────────────────────────────────────────────────────

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

  // Intel v3 snapshot path.
  intelV3: {
    getSnapshot: () =>
      fetchApi<IntelV3Snapshot>("/api/v1/intel/v3/snapshot"),
    /**
     * One bounded request of a durable Run Intel session. The browser mints
     * `runSessionId` (crypto.randomUUID()) once per manual click; every
     * automatic continuation of that click sends the SAME id so the backend
     * resumes the exact session instead of guessing from queue state.
     */
    runV3: (runSessionId: string, signal?: AbortSignal) =>
      fetchApi<IntelV3RunResult>("/api/v1/intel/v3/run", {
        method: "POST",
        body: JSON.stringify({ run_session_id: runSessionId }),
        signal,
      }),
    getRunStatus: (runId: string) =>
      fetchApi<IntelV3RunStatus>(`/api/v1/intel/v3/runs/${runId}`),
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

// ── Types (mirrors backend Pydantic models) ────────────────────────────────────────────────────────────────────────────

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

export interface CashBalance {
  cash_balance: number;
  source: "plaid" | "manual" | "none";
  manual_override: number | null;
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

// ── Intel v3 types (K3 snapshot contract) ────────────────────────────────────────────────────────────────────────────

/** Stage 8D/8E — SEC/company catalyst evidence display fields. All boolean flags + optional explanation strings. */
export interface SecCatalystEvidenceDisplay {
  /** True when official company filing activity contributed to sentiment readiness */
  sec_catalyst_found: boolean;
  /** True when general news/editorial sources were found but did not meet quality bar */
  editorial_suppressed: boolean;
  /** False for ETFs, crypto, and non-equity instruments */
  sec_lane_applicable: boolean;
  // Stage 8E: optional plain-English explanation fields derived from artifact payload.
  // Present when backend has sufficient detail; absent when payload is missing or minimal.
  /** e.g. "Recent official filing activity was found. The filing appears material enough..." */
  event_summary?: string;
  /** e.g. "Filing activity is within the relevant reporting window." */
  freshness_label?: string;
  /** e.g. "One recent official filing was found." */
  material_filing_label?: string;
  /** e.g. "This covers official company/SEC events only, not broad market opinion." */
  limitation_note?: string;
  /** e.g. "This is useful context, but it does not decide Buy, Hold, Trim, or Sell by itself." */
  decision_authority_note?: string;
  // Stage 8F: optional filing-type specificity derived from stored source section_references.
  // Present when the artifact's source records contain a recognised SEC form type.
  /** e.g. "Annual report (10-K)" | "Quarterly report (10-Q)" | "Company event filing (8-K)" | "Multiple recent official filings" */
  filing_type_label?: string;
}

/** Stage 7 — evidence explanation from the governance engine. Null when governance inactive. */
export interface IntelV3EvidenceExplanation {
  /** Company fundamentals readiness: READY | LIMITED | SUPPRESSED | MISSING | INSUFFICIENT | NOT_APPLICABLE */
  primary_evidence_status: string;
  /** Technical/price signals readiness — same set of values */
  technical_signals_status: string;
  /** News & sentiment readiness — same set of values */
  sentiment_status: string;
  /** True when conviction was capped below what the action would normally allow */
  conviction_cap_applied: boolean;
  /** Short reason for the cap, or null */
  conviction_cap_reason: string | null;
  /** True when the engine considers this ticker safe for a visible decision */
  safe_for_visible_decision: boolean;
  /** Brief reason string for safe_for_visible_decision */
  safe_for_visible_decision_reason: string;
  /** Internal priority label (p3a, p4b_limited_no_corroboration, etc.) — translated by the UI */
  governance_priority: string;
  /** True when fundamentals exist but no technical/sentiment corroboration */
  corroboration_gap: boolean;
  /** List of backend action-block reason codes applied */
  action_blocks: string[];
  /** Stage 8D — SEC/company catalyst evidence readiness for plain-English UI. Absent when no shadow available. */
  sec_catalyst_evidence?: SecCatalystEvidenceDisplay | null;
}

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
    /** Build 3 PR 2B — plain-English valuation context. Null when suppressed or unavailable. */
    valuation_context?: { visible_text: string; limitation_text: string; source_basis: string } | null;
    committee: { status: "deferred" | "ready" | "source_validated" | "pending"; reason?: string };
    /** Stage 7C — evidence explanation. Always present: real from Stage 6 governance, or synthetic from decision band. */
    evidence_explanation?: IntelV3EvidenceExplanation | null;
    /** Stage 9I — asset intelligence context from composer. Explanatory only; never overrides visible action. */
    asset_intelligence_context?: {
      role_lens: string;
      why_this_action: string;
      add_more_trigger: string;
      trim_sell_trigger: string;
      evidence_caveat?: string | null;
      /** Stage 9J — current portfolio weight note, fit-aware plain English. Present only when backend has pct data. */
      portfolio_weight_context?: string;
      lens_applied: string;
      asset_class_display: string;
      adapter_version: string;
    } | null;
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
  source_pack_validated_count?: number;
  source_pack_pending_count?: number;
  best_buys: IntelV3HeldCard[];
  trim_sell_desk: IntelV3HeldCard[];
  current_holdings: IntelV3HeldCard[];
  opportunity_radar_preview: { status: "deferred" | "dark_launch"; reason?: string };
  what_changed: string[];
  warnings: string[];
  legacy_path_used: false;
  diagnostics?: IntelV3SnapshotDiagnostics;
  // Stage 3.3 — provenance fields (set by worker prewarm, absent on HTTP-built snapshots)
  snapshot_source?: "worker_certified" | "http_request" | "certification_failed" | "prewarm";
  agents_ran_via_worker?: boolean;
  agents_ran_for_this_click?: string;
  this_click_used_llm?: boolean;
  certified_holding_count?: number;
  total_holding_count?: number;
  failed_tickers_in_certification?: string[];
  certification_summary?: {
    certified: boolean;
    certified_holding_count: number;
    total_holding_count: number;
    failed_holding_count: number;
    latest_agent_run_at: string | null;
    latest_recommendation_at: string | null;
    agent_run_ids_used: string[];
    certification_errors: string[];
  };
  // Build 2 — evidence freshness state from watchtower republisher
  evidence_freshness_state?:
    | "certified_current"
    | "republish_pending"
    | "certification_blocked"
    | "rebuilt_and_published"
    | "no_snapshot_exists"
    | string;
}

export type IntelV3RunMode =
  | "FAST_CERTIFIED"
  | "REFRESH_THEN_RUN"
  | "PARTIAL_CERTIFIED"
  | "BLOCKED_UNCERTIFIED";

export type IntelV3TrustStatus = "trusted" | "partial_trust" | "uncertified";

export interface IntelV3SourceFreshness {
  state: "FRESH" | "STALE" | "HARD_STALE" | "MISSING" | "UNKNOWN";
  is_critical: boolean;
  fresh_count: number;
  stale_count: number;
  hard_stale_count: number;
  missing_count: number;
  oldest_age_hours: number | null;
  newest_age_hours: number | null;
}

export interface IntelV3SnapshotDiagnostics {
  evidence_mode: string;
  attempted_llm_calls: number;
  live_provider_calls: number;
  recommendation_count: number;
  agent_insight_count: number;
  position_count: number;
  missing_evidence_count: number;
  stale_evidence_count: number;
  max_recommendation_age_hours: number | null;
  max_agent_insight_age_hours: number | null;
  oldest_source_timestamp: string | null;
  newest_source_timestamp: string | null;
  // Plain-English per-source age summary (Stage 3.0b.6). Backend-built so the
  // banner never has to guess which source's age applies; reports both
  // recommendation and analyst evidence ages separately.
  banner_age_summary?: string;
  previous_snapshot_id: string | null;
  previous_action_counts: Record<string, number> | null;
  current_action_counts: Record<string, number>;
  changed_decision_count: number;
  changed_decisions: Array<{ ticker: string; previous_action: string; current_action: string }>;
  unchanged_decision_count: number;
  // Stage 3.0b — Evidence Refresh Orchestrator additions (optional for back-compat).
  run_mode?: IntelV3RunMode;
  trust_status?: IntelV3TrustStatus;
  banner_copy?: string;
  source_freshness?: Record<string, IntelV3SourceFreshness>;
  stale_source_count?: number;
  hard_stale_source_count?: number;
  missing_source_count?: number;
  refresh_targets?: string[];
  blocked_sources?: string[];
  refreshed_source_count?: number;
  failed_refresh_count?: number;
  attempted_provider_calls?: number;
  successful_provider_calls?: number;
  failed_provider_calls?: number;
  successful_llm_calls?: number;
  failed_llm_calls?: number;
  refresh_duration_ms?: number;
  analyst_refresh_supported?: boolean;
  analyst_refresh_status?: string;
  // Stage 3.0b.6 — per-ticker analyst refresh accounting (optional fields).
  analyst_refresh_per_ticker?: Array<{
    ticker: string;
    success: boolean;
    refreshed_recommendation_at?: string | null;
    refreshed_agent_insight_at?: string | null;
    error_reason?: string | null;
    llm_call_count?: number;
    llm_success_count?: number;
  }>;
  analyst_refresh_selected_tickers?: string[];
  analyst_refresh_deferred_tickers?: string[];
  analyst_refresh_successful_tickers?: string[];
  analyst_refresh_failed_tickers?: string[];
  budget_exhausted?: boolean;
  orchestrator_notes?: string[];
}

/**
 * Stage 3.3 — Run Intel v3 enqueue result.
 * POST /intel/v3/run now returns a refresh-enqueue status, NOT a snapshot.
 * The UI should poll GET /intel/v3/snapshot until snapshot_source=worker_certified.
 */
export interface IntelV3RunResult {
  status:
    | "refresh_requested"
    | "refresh_in_progress"
    | "analyst_evidence_current"  // backend: evidence fresh, no jobs queued
    | "mapping_version_recertified"  // stale mapping version recertified via zero-LLM prewarm
    | "mapping_version_recertification_failed"  // prewarm failed during mapping recertification
    | "stage7_contract_recertified"  // Stage 7 explanation contract recertified via zero-LLM prewarm
    | "stage7_contract_recertification_failed"
    | "stage8e_contract_recertified"  // Stage 8E catalyst explanation contract recertified via zero-LLM prewarm
    | "stage8e_contract_recertification_failed"
    | "stage8f_contract_recertified"  // Stage 8F filing-type contract recertified via zero-LLM prewarm
    | "stage8f_contract_recertification_failed"
    | "no_active_holdings"
    | "enqueue_failed"
    | "completed"   // legacy — may appear if backend version mismatch
    | "running"
    | "failed";
  queued_ticker_count?: number;
  stale_analyst_count?: number;
  total_holding_count?: number;
  existing_certified_snapshot_id?: string | null;
  existing_certified_snapshot?: boolean;
  message?: string;
  // Stage 13B — bounded on-demand evidence drain operational-truth fields.
  // Present on every /intel/v3/run response so the UI never implies a
  // snapshot is being built when the queue is only queued, not draining.
  on_demand_processing_enabled?: boolean;
  on_demand_jobs_attempted?: number;
  on_demand_jobs_succeeded?: number;
  on_demand_jobs_failed?: number;
  snapshot_available_after_run?: boolean;
  next_required_action?: string;
  // Additive — only present when next_required_action reports the durable-job
  // "backoff" state (a failed-but-retryable job's next_retry_at hasn't
  // arrived yet). The earliest retry time among such jobs, if known.
  earliest_retry_at?: string | null;
  // ── Durable Run Intel session state (authoritative; explicit, no
  //    client-side inference from batch counts) ──
  run_session_id?: string;
  session_status?: string;
  expected_ticker_count?: number;
  session_succeeded_ticker_count?: number;
  session_remaining_ticker_count?: number;
  publication_status?:
    | "not_started"
    | "pending"
    | "retryable_failed"
    | "completed";
  completed_snapshot_id?: string | null;
  retryable?: boolean;
  // Legacy fields — kept for back-compat if old backend serves them
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

// ── Deploy v3 types (read-only plan contract) ────────────────────────────────────────────────────────────────────────────

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

