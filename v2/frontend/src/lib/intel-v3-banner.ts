/**
 * Intel v3 trust-banner copy helpers (pure, no React).
 *
 * Stage 3.3 — all-or-nothing certified intelligence run contract.
 *
 * The UI must show exactly one of six states based on snapshot provenance
 * and refresh status. Green is only allowed when:
 *   - snapshot_source === "worker_certified"
 *   - certified_holding_count === total_holding_count
 *   - No pending refresh is in progress
 *
 * A user who clicks "Run Intel v3" must see "Refreshing" immediately —
 * never green from a previous worker run dressed up as a fresh click.
 */
import type { IntelV3Snapshot, IntelV3RunResult } from "@/lib/api";

// ── Status literals ───────────────────────────────────────────────────────────

export type IntelV3UIStatus =
  | "certified_current"
  | "refreshing_analyst_intelligence"
  | "latest_certified_new_refresh_running"
  | "unavailable_refresh_failed"
  | "unavailable_evidence_incomplete"
  | "blocked_certification_failed";

export interface IntelV3BannerState {
  status: IntelV3UIStatus;
  headline: string;
  detail: string | null;
  tone: "green" | "amber" | "red" | "grey";
  showProvenance: boolean;
}

// ── Status derivation ─────────────────────────────────────────────────────────

/**
 * Derive the canonical UI status from snapshot + refresh state.
 *
 * @param snapshot  Latest snapshot from GET /intel/v3/snapshot (null = no snapshot)
 * @param isRefreshing  True when a Run Intel click is in flight or pending
 * @param lastRunResult  The result of the most recent POST /intel/v3/run call
 */
export function deriveIntelV3UIStatus(
  snapshot: IntelV3Snapshot | null | undefined,
  isRefreshing: boolean,
  lastRunResult?: IntelV3RunResult | null,
): IntelV3UIStatus {
  const hasSnapshot = !!snapshot;
  const isCertified =
    hasSnapshot &&
    snapshot!.snapshot_source === "worker_certified" &&
    typeof snapshot!.certified_holding_count === "number" &&
    typeof snapshot!.total_holding_count === "number" &&
    snapshot!.total_holding_count > 0 &&
    snapshot!.certified_holding_count === snapshot!.total_holding_count;

  const isCertificationFailed =
    hasSnapshot && snapshot!.snapshot_source === "certification_failed";

  if (isRefreshing) {
    if (isCertified) {
      return "latest_certified_new_refresh_running";
    }
    return "refreshing_analyst_intelligence";
  }

  if (!hasSnapshot) {
    return "unavailable_evidence_incomplete";
  }

  if (isCertified) {
    return "certified_current";
  }

  if (isCertificationFailed) {
    return "blocked_certification_failed";
  }

  // Snapshot exists but is from HTTP path (no worker certification) or
  // is certification_failed. Show evidence incomplete until worker certifies.
  const runEnqueued = lastRunResult?.status === "refresh_requested" ||
    lastRunResult?.status === "refresh_in_progress";
  if (runEnqueued) {
    return "refreshing_analyst_intelligence";
  }

  return "unavailable_evidence_incomplete";
}

// ── Banner copy ───────────────────────────────────────────────────────────────

export function buildBannerState(
  snapshot: IntelV3Snapshot | null | undefined,
  isRefreshing: boolean,
  lastRunResult?: IntelV3RunResult | null,
): IntelV3BannerState {
  const status = deriveIntelV3UIStatus(snapshot, isRefreshing, lastRunResult);
  const cert = snapshot?.certification_summary;
  const certCount = snapshot?.certified_holding_count ?? cert?.certified_holding_count;
  const totalCount = snapshot?.total_holding_count ?? cert?.total_holding_count;
  const failedTickers = snapshot?.failed_tickers_in_certification ?? [];
  const latestRunAt = cert?.latest_agent_run_at
    ? new Date(cert.latest_agent_run_at).toLocaleString()
    : null;

  switch (status) {
    case "certified_current":
      return {
        status,
        headline: "Certified Current",
        detail: [
          `Coverage: ${certCount}/${totalCount} certified.`,
          latestRunAt ? `Latest certified analyst run: ${latestRunAt}.` : null,
          "Certified by: background worker (no LLM calls on page load).",
          `Snapshot source: worker certified.`,
        ].filter(Boolean).join(" "),
        tone: "green",
        showProvenance: true,
      };

    case "refreshing_analyst_intelligence":
      return {
        status,
        headline: "Refreshing Analyst Intelligence",
        detail: "Background worker is running LLM analysis for all holdings. Results will appear automatically when certification completes.",
        tone: "grey",
        showProvenance: false,
      };

    case "latest_certified_new_refresh_running":
      return {
        status,
        headline: "Latest Certified Snapshot Available — New Refresh Running",
        detail: [
          `Coverage: ${certCount}/${totalCount} certified (prior run).`,
          latestRunAt ? `Latest analyst run: ${latestRunAt}.` : null,
          "New worker analysis in progress — snapshot will update automatically.",
        ].filter(Boolean).join(" "),
        tone: "amber",
        showProvenance: true,
      };

    case "unavailable_refresh_failed":
      return {
        status,
        headline: "Intel Unavailable — Refresh Failed",
        detail:
          failedTickers.length > 0
            ? `Failed tickers: ${failedTickers.slice(0, 5).join(", ")}${failedTickers.length > 5 ? ` +${failedTickers.length - 5} more` : ""}. Click Run Intel v3 to retry.`
            : "Worker refresh failed. Click Run Intel v3 to retry.",
        tone: "red",
        showProvenance: false,
      };

    case "unavailable_evidence_incomplete":
      return {
        status,
        headline: "Intel Unavailable — Evidence Incomplete",
        detail: "No certified analyst evidence found. Click Run Intel v3 to start a fresh analysis run.",
        tone: "grey",
        showProvenance: false,
      };

    case "blocked_certification_failed": {
      const failedCount = cert?.failed_holding_count ?? failedTickers.length;
      return {
        status,
        headline: "Intel Blocked — Certification Failed",
        detail: [
          `${failedCount} holding${failedCount === 1 ? "" : "s"} failed certification.`,
          failedTickers.length > 0
            ? `Failed: ${failedTickers.slice(0, 5).join(", ")}${failedTickers.length > 5 ? ` +${failedTickers.length - 5} more` : ""}.`
            : null,
          "Click Run Intel v3 to retry.",
        ].filter(Boolean).join(" "),
        tone: "red",
        showProvenance: true,
      };
    }
  }
}

// ── User-facing status pill (Build 2.5) ──────────────────────────────────────

export type IntelUserPill = "Ready" | "Updating" | "Needs Research" | "Blocked";

export interface IntelStatusPillState {
  pill: IntelUserPill;
  line: string;
  tone: "green" | "amber" | "red" | "grey";
}

/**
 * Maps the 6 internal UI states to 4 plain-English user-facing statuses.
 * buildBannerState() remains for the diagnostics drawer; this drives the
 * compact status area shown by default.
 */
export function buildStatusPillState(
  snapshot: IntelV3Snapshot | null | undefined,
  isRefreshing: boolean,
  lastRunResult?: IntelV3RunResult | null,
): IntelStatusPillState {
  const status = deriveIntelV3UIStatus(snapshot, isRefreshing, lastRunResult);

  switch (status) {
    case "certified_current": {
      const ts = snapshot?.generated_at ? new Date(snapshot.generated_at) : null;
      const today = ts ? ts.toDateString() === new Date().toDateString() : false;
      const timeStr = ts
        ? ts.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
        : null;
      const line = timeStr
        ? today
          ? `Updated today at ${timeStr}.`
          : `Updated ${ts!.toLocaleDateString()} at ${timeStr}.`
        : "Up to date.";
      return { pill: "Ready", line, tone: "green" };
    }

    case "latest_certified_new_refresh_running":
      return { pill: "Updating", line: "Refreshing portfolio intelligence…", tone: "amber" };

    case "refreshing_analyst_intelligence":
      return { pill: "Updating", line: "Refreshing portfolio intelligence…", tone: "grey" };

    case "unavailable_refresh_failed":
      return { pill: "Needs Research", line: "Last refresh failed. Run Intel to retry.", tone: "red" };

    case "blocked_certification_failed":
      return { pill: "Blocked", line: "Some holdings couldn't be certified. Run Intel to retry.", tone: "red" };

    case "unavailable_evidence_incomplete":
    default:
      return { pill: "Needs Research", line: "Research is stale. Run Intel to refresh recommendations.", tone: "grey" };
  }
}

// ── Legacy compat: analyst refresh-request note ───────────────────────────────

/**
 * Honest note for the analyst refresh-request seam.
 * Kept for backward compatibility — the Stage 3.0b.6 amber banner.
 */
export function analystRefreshRequestNote(
  diag: IntelV3Snapshot["diagnostics"] | undefined,
): string | null {
  if (!diag) return null;
  if (diag.analyst_refresh_status !== "refresh_requested") return null;
  const count = diag.analyst_refresh_deferred_tickers?.length ?? 0;
  if (count > 0) {
    return `Analyst evidence is stale for ${count} holding${count === 1 ? "" : "s"} — a refresh has been requested. Showing last certified analyst evidence.`;
  }
  return "Analyst evidence is stale — a refresh has been requested. Showing last certified analyst evidence.";
}
