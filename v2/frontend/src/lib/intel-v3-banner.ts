/**
 * Intel v3 trust-banner copy helpers (pure, no React).
 *
 * Stage 3.1 — the synchronous Run Intel v3 path never runs an analyst/LLM
 * refresh inside the click. When analyst evidence is stale it records a
 * refresh *request*. These helpers produce the honest plain-English banner
 * copy for the new states:
 *   - fresh certified            → run_mode FAST_CERTIFIED
 *   - partial / uncertified      → run_mode PARTIAL_CERTIFIED / BLOCKED_UNCERTIFIED
 *   - analyst refresh requested  → analyst_refresh_status === "refresh_requested"
 *
 * The refresh-requested copy must never claim a background job is running or
 * that the user must wait for a synchronous analyst run.
 */
import type { IntelV3SnapshotDiagnostics } from "@/lib/api";

/**
 * Honest note for the analyst refresh-request seam. Returns null unless the
 * run recorded a refresh request (i.e. analyst evidence was stale and the
 * synchronous path declined to run an in-request LLM refresh).
 */
export function analystRefreshRequestNote(
  diag: IntelV3SnapshotDiagnostics | undefined,
): string | null {
  if (!diag) return null;
  if (diag.analyst_refresh_status !== "refresh_requested") return null;
  const count = diag.analyst_refresh_deferred_tickers?.length ?? 0;
  if (count > 0) {
    return `Analyst evidence is stale for ${count} holding${count === 1 ? "" : "s"} — a refresh has been requested. Showing last certified analyst evidence.`;
  }
  return "Analyst evidence is stale — a refresh has been requested. Showing last certified analyst evidence.";
}
