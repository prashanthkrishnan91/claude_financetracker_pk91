/**
 * Pure data helpers for Intel v3 evidence display and data health rows.
 * No JSX, no React. Safe to import in Node test environments.
 */

// ── Evidence band ─────────────────────────────────────────────────────────────

/** Evidence band → plain-English beginner label (per task spec §4 source UX contract). */
export function evidenceBandToBeginnerLabel(band: string): string {
  switch (band) {
    case "STRONG":  return "Strong current evidence";
    case "PARTIAL": return "Some evidence, still incomplete";
    case "THIN":    return "Thin evidence — treat as lower confidence";
    default:        return "Evidence state unavailable";
  }
}

// ── Committee / source-pack status ───────────────────────────────────────────

/** Committee/source-pack status → plain-English label (per task spec §4 source UX contract). */
export function committeeStatusToPlainLabel(status: string): string {
  switch (status) {
    case "source_validated":
    case "ready":    return "Source-linked";
    case "pending":
    case "deferred": return "Source linking not complete yet";
    default:         return "Source state unavailable";
  }
}

// ── Run Intel trust contract (run_trust_contract_v1) display helpers ─────────

/** Explicit "not assessed"/"missing" text — never a bare "—" for source health.
 * ``status`` is the authoritative field (full/partial/missing/unknown);
 * ``has_source_refs`` is a legacy convenience boolean (true only when full). */
export function sourceLineageToLabel(
  sourceLineage: { status?: string; has_source_refs: boolean } | null | undefined,
): string {
  if (!sourceLineage) return "Source lineage not assessed for this holding.";
  switch (sourceLineage.status) {
    case "full":    return "Every output behind this decision carries a source reference.";
    case "partial": return "Some but not all outputs behind this decision carry a source reference.";
    case "unknown": return "Source lineage could not be re-verified for this holding.";
    case "missing":
      return "No source references are recorded for this holding yet.";
    default:
      return sourceLineage.has_source_refs
        ? "Source references are recorded for this holding."
        : "No source references are recorded for this holding yet.";
  }
}

/** Per-card conflict-review status → plain-English label. The status
 * vocabulary is shared by historical LLM-reviewed holdings and new
 * deterministically-resolved holdings, so this copy is method-neutral —
 * true for either generation, never "review passed"/"AI review"/consensus
 * language, and never claims "no conflict was detected" for "unknown"
 * (fail-closed read-time overlay) — that would silently re-introduce the
 * exact optimistic-default bug this exists to prevent. */
export function conflictReviewStatusToLabel(status: string | null | undefined): string {
  switch (status) {
    case "succeeded": return "Specialist signal handling completed for this holding.";
    case "failed":    return "Specialist signal handling could not complete safely.";
    case "pending":   return "Specialist signal handling is still pending.";
    case "unknown":   return "Specialist signal handling status could not be re-verified for this holding.";
    case "not_required":
      return "No specialist conflict or low-confidence case was detected.";
    default:
      return "Specialist signal handling status unavailable for this holding.";
  }
}

// ── Source metadata formatting ────────────────────────────────────────────────

/** Format a snapshot or run ID to an 8-char short form, or "—" when absent. */
export function formatSnapshotIdShort(id?: string | null): string {
  if (!id) return "—";
  return id.slice(0, 8);
}

/**
 * Safe ISO date formatter for client-only display.
 * Uses UTC timezone to avoid server/client hydration mismatch for near-midnight timestamps.
 * Returns "—" on any parse failure.
 */
export function formatUpdatedAtSafe(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      timeZone: "UTC",
    });
  } catch {
    return "—";
  }
}

// ── Evidence freshness state ──────────────────────────────────────────────────

/** Intel v3 evidence freshness state → plain-English label. */
export function evidenceFreshnessToLabel(state?: string | null): string {
  switch (state) {
    case "certified_current":     return "Up to date";
    case "rebuilt_and_published": return "Refreshed and certified";
    case "republish_pending":     return "Refresh pending";
    case "certification_blocked": return "Certification blocked";
    case "no_snapshot_exists":    return "No snapshot yet";
    default:                      return "Freshness state unavailable";
  }
}

// ── Data health rows ──────────────────────────────────────────────────────────

export type DataHealthStatus = "ok" | "pending" | "unavailable" | "blocked";

export interface DataHealthRow {
  label: string;
  status: DataHealthStatus;
  detail: string;
}

export interface DataHealthInput {
  intelSnapshotSource?: string | null;
  intelFreshnessState?: string | null;
  pricesFresh?: number | null;
  pricesStale?: number | null;
  plaidStatus?: string | null;
  plaidLastSyncedAt?: string | null;
}

/**
 * Build data health rows from current frontend-readable state.
 * Missing inputs produce an "unavailable" row — never fake status.
 */
export function buildDataHealthRows(input: DataHealthInput): DataHealthRow[] {
  const {
    intelSnapshotSource,
    intelFreshnessState,
    pricesFresh,
    pricesStale,
    plaidStatus,
    plaidLastSyncedAt,
  } = input;

  const UNAVAILABLE_DETAIL = "Not connected to this view yet";

  const rows: DataHealthRow[] = [];

  // Intel snapshot
  rows.push({
    label: "Intel snapshot",
    status:
      intelSnapshotSource === "worker_certified" ? "ok"
      : intelSnapshotSource === "worker_certified_with_gaps" ? "pending"
      : intelSnapshotSource === "certification_failed" ? "blocked"
      : intelSnapshotSource != null ? "pending"
      : "unavailable",
    detail:
      intelSnapshotSource === "worker_certified"
        ? "Worker-certified snapshot available"
      : intelSnapshotSource === "worker_certified_with_gaps"
        ? "Certified snapshot available — some holdings couldn't be analyzed in the last run"
      : intelSnapshotSource != null
        ? intelSnapshotSource.replace(/_/g, " ")
      : UNAVAILABLE_DETAIL,
  });

  // Evidence freshness
  rows.push({
    label: "Evidence freshness",
    status:
      intelFreshnessState === "certified_current" || intelFreshnessState === "rebuilt_and_published"
        ? "ok"
      : intelFreshnessState != null
        ? "pending"
      : "unavailable",
    detail: intelFreshnessState != null
      ? evidenceFreshnessToLabel(intelFreshnessState)
      : UNAVAILABLE_DETAIL,
  });

  // Price data
  {
    const fresh = pricesFresh ?? null;
    const stale = pricesStale ?? null;
    const total = fresh != null && stale != null ? fresh + stale : null;
    rows.push({
      label: "Price data",
      status:
        total == null ? "unavailable"
        : (stale ?? 0) > 0 ? "pending"
        : "ok",
      detail:
        total == null ? UNAVAILABLE_DETAIL
        : total > 0 ? `${fresh} of ${total} prices fresh`
        : "No price data",
    });
  }

  // Broker sync
  rows.push({
    label: "Broker sync",
    status:
      plaidStatus == null ? "unavailable"
      : plaidStatus === "connected" ? "ok"
      : plaidStatus === "pending" ? "pending"
      : "unavailable",
    detail:
      plaidStatus == null ? UNAVAILABLE_DETAIL
      : plaidStatus === "connected"
        ? `Connected — last synced ${formatUpdatedAtSafe(plaidLastSyncedAt)}`
      : plaidStatus.replace(/_/g, " "),
  });

  return rows;
}
