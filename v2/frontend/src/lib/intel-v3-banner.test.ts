/**
 * Intel v3 trust-banner copy contract — Stage 3.1.
 *
 * Verifies the honest banner copy for fresh vs stale / refresh-requested
 * states. The refresh-requested copy must never claim a background job is
 * running or that the user must wait for a synchronous analyst run.
 */
import { analystRefreshRequestNote } from "@/lib/intel-v3-banner";
import type { IntelV3SnapshotDiagnostics } from "@/lib/api";

function makeDiag(
  overrides: Partial<IntelV3SnapshotDiagnostics> = {},
): IntelV3SnapshotDiagnostics {
  return {
    evidence_mode: "deterministic_policy_over_persisted_evidence",
    attempted_llm_calls: 0,
    live_provider_calls: 0,
    recommendation_count: 2,
    agent_insight_count: 2,
    position_count: 2,
    missing_evidence_count: 0,
    stale_evidence_count: 0,
    max_recommendation_age_hours: null,
    max_agent_insight_age_hours: null,
    oldest_source_timestamp: null,
    newest_source_timestamp: null,
    previous_snapshot_id: null,
    previous_action_counts: null,
    current_action_counts: {},
    changed_decision_count: 0,
    changed_decisions: [],
    unchanged_decision_count: 0,
    ...overrides,
  };
}

describe("Intel v3 banner — fresh certified state", () => {
  it("fresh certified run produces no analyst refresh-request note", () => {
    const diag = makeDiag({
      run_mode: "FAST_CERTIFIED",
      trust_status: "trusted",
      analyst_refresh_status: "no_stale",
    });
    expect(analystRefreshRequestNote(diag)).toBeNull();
  });

  it("missing diagnostics produces no note", () => {
    expect(analystRefreshRequestNote(undefined)).toBeNull();
  });
});

describe("Intel v3 banner — analyst refresh-requested state", () => {
  it("surfaces an honest refresh-requested note with the stale holding count", () => {
    const diag = makeDiag({
      run_mode: "PARTIAL_CERTIFIED",
      trust_status: "partial_trust",
      analyst_refresh_status: "refresh_requested",
      analyst_refresh_deferred_tickers: ["AAPL", "NVDA"],
    });
    const note = analystRefreshRequestNote(diag);
    expect(note).toBe(
      "Analyst evidence is stale for 2 holdings — a refresh has been requested. Showing last certified analyst evidence.",
    );
  });

  it("uses singular phrasing for a single stale holding", () => {
    const diag = makeDiag({
      analyst_refresh_status: "refresh_requested",
      analyst_refresh_deferred_tickers: ["AAPL"],
    });
    expect(analystRefreshRequestNote(diag)).toContain("1 holding —");
  });

  it("falls back to a generic note when no ticker list is present", () => {
    const diag = makeDiag({ analyst_refresh_status: "refresh_requested" });
    expect(analystRefreshRequestNote(diag)).toBe(
      "Analyst evidence is stale — a refresh has been requested. Showing last certified analyst evidence.",
    );
  });

  it("never claims a background job is running or that the user must wait", () => {
    const diag = makeDiag({
      analyst_refresh_status: "refresh_requested",
      analyst_refresh_deferred_tickers: ["AAPL", "NVDA", "MSFT"],
    });
    const note = analystRefreshRequestNote(diag)!.toLowerCase();
    // No fake queue / background-worker language.
    expect(note).not.toContain("background");
    expect(note).not.toContain("queue");
    expect(note).not.toContain("running");
    expect(note).not.toContain("in progress");
    // No claim the user must wait for a synchronous analyst run.
    expect(note).not.toContain("please wait");
    expect(note).not.toContain("waiting");
  });
});

describe("Intel v3 banner — blocked / uncertified state", () => {
  it("blocked run still shows the honest refresh-requested note when set", () => {
    const diag = makeDiag({
      run_mode: "BLOCKED_UNCERTIFIED",
      trust_status: "uncertified",
      analyst_refresh_status: "refresh_requested",
      analyst_refresh_deferred_tickers: ["AAPL"],
    });
    expect(analystRefreshRequestNote(diag)).toContain("refresh has been requested");
  });

  it("a non-refresh-requested status produces no note even when partial", () => {
    const diag = makeDiag({
      run_mode: "PARTIAL_CERTIFIED",
      analyst_refresh_status: "succeeded",
    });
    expect(analystRefreshRequestNote(diag)).toBeNull();
  });
});
