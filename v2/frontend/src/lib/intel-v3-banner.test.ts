/**
 * Intel v3 trust-banner contract — Stage 3.3.
 *
 * Covers deriveIntelV3UIStatus and buildBannerState (all-or-nothing certified
 * intelligence run contract), plus legacy analystRefreshRequestNote tests.
 */
import { analystRefreshRequestNote, deriveIntelV3UIStatus, buildBannerState } from "@/lib/intel-v3-banner";
import type { IntelV3Snapshot, IntelV3SnapshotDiagnostics } from "@/lib/api";

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

// ── Stage 3.3: deriveIntelV3UIStatus ─────────────────────────────────────────

function makeCertifiedSnapshot(overrides: Partial<IntelV3Snapshot> = {}): IntelV3Snapshot {
  return {
    snapshot_source: "worker_certified",
    certified_holding_count: 3,
    total_holding_count: 3,
    failed_tickers_in_certification: [],
    ...overrides,
  } as unknown as IntelV3Snapshot;
}

describe("deriveIntelV3UIStatus — certified_current", () => {
  it("returns certified_current when worker_certified and counts match and not refreshing", () => {
    const snap = makeCertifiedSnapshot();
    expect(deriveIntelV3UIStatus(snap, false)).toBe("certified_current");
  });

  it("green only if certified_holding_count === total_holding_count", () => {
    const snap = makeCertifiedSnapshot({ certified_holding_count: 2, total_holding_count: 3 });
    expect(deriveIntelV3UIStatus(snap, false)).not.toBe("certified_current");
  });

  it("green requires snapshot_source === worker_certified", () => {
    const snap = makeCertifiedSnapshot({ snapshot_source: "http_request" } as any);
    expect(deriveIntelV3UIStatus(snap, false)).not.toBe("certified_current");
  });

  it("green requires total_holding_count > 0", () => {
    const snap = makeCertifiedSnapshot({ certified_holding_count: 0, total_holding_count: 0 });
    expect(deriveIntelV3UIStatus(snap, false)).not.toBe("certified_current");
  });
});

describe("deriveIntelV3UIStatus — refreshing states", () => {
  it("returns latest_certified_new_refresh_running when certified and isRefreshing", () => {
    const snap = makeCertifiedSnapshot();
    expect(deriveIntelV3UIStatus(snap, true)).toBe("latest_certified_new_refresh_running");
  });

  it("returns refreshing_analyst_intelligence when no snapshot and isRefreshing", () => {
    expect(deriveIntelV3UIStatus(null, true)).toBe("refreshing_analyst_intelligence");
  });

  it("returns refreshing when uncertified snapshot exists and isRefreshing", () => {
    const snap = makeCertifiedSnapshot({ snapshot_source: "http_request" } as any);
    expect(deriveIntelV3UIStatus(snap, true)).toBe("refreshing_analyst_intelligence");
  });
});

describe("deriveIntelV3UIStatus — unavailable states", () => {
  it("returns unavailable_evidence_incomplete when no snapshot and not refreshing", () => {
    expect(deriveIntelV3UIStatus(null, false)).toBe("unavailable_evidence_incomplete");
  });

  it("returns blocked_certification_failed when snapshot_source is certification_failed", () => {
    const snap = makeCertifiedSnapshot({ snapshot_source: "certification_failed" } as any);
    expect(deriveIntelV3UIStatus(snap, false)).toBe("blocked_certification_failed");
  });

  it("returns unavailable_evidence_incomplete for http_request snapshot without enqueued run", () => {
    const snap = makeCertifiedSnapshot({ snapshot_source: "http_request" } as any);
    expect(deriveIntelV3UIStatus(snap, false)).toBe("unavailable_evidence_incomplete");
  });
});

// ── Stage 3.3: buildBannerState tone rules ────────────────────────────────────

describe("buildBannerState — tone and copy", () => {
  it("certified_current uses green tone", () => {
    const snap = makeCertifiedSnapshot();
    const banner = buildBannerState(snap, false);
    expect(banner.tone).toBe("green");
    expect(banner.status).toBe("certified_current");
  });

  it("refreshing_analyst_intelligence uses grey tone", () => {
    const banner = buildBannerState(null, true);
    expect(banner.tone).toBe("grey");
    expect(banner.showProvenance).toBe(false);
  });

  it("latest_certified_new_refresh_running uses amber tone", () => {
    const snap = makeCertifiedSnapshot();
    const banner = buildBannerState(snap, true);
    expect(banner.tone).toBe("amber");
    expect(banner.showProvenance).toBe(true);
  });

  it("blocked_certification_failed uses red tone", () => {
    const snap = makeCertifiedSnapshot({ snapshot_source: "certification_failed" } as any);
    const banner = buildBannerState(snap, false);
    expect(banner.tone).toBe("red");
  });

  it("unavailable_evidence_incomplete uses grey tone", () => {
    const banner = buildBannerState(null, false);
    expect(banner.tone).toBe("grey");
  });

  it("certified_current detail mentions worker and coverage", () => {
    const snap = makeCertifiedSnapshot();
    const banner = buildBannerState(snap, false);
    expect(banner.detail).toContain("3/3");
    expect(banner.detail?.toLowerCase()).toContain("worker");
  });

  it("green banner never shows when http_request snapshot present", () => {
    const snap = makeCertifiedSnapshot({ snapshot_source: "http_request" } as any);
    const banner = buildBannerState(snap, false);
    expect(banner.tone).not.toBe("green");
  });

  it("certified_current detail does not claim agents ran for this request", () => {
    const snap = makeCertifiedSnapshot();
    const banner = buildBannerState(snap, false);
    // Must not imply the current click/request triggered the agent run
    expect(banner.detail?.toLowerCase()).not.toContain("agents ran for this request");
    expect(banner.detail?.toLowerCase()).not.toContain("yes — background worker");
    // Must still mention how the analysis was produced
    expect(banner.detail?.toLowerCase()).toContain("background worker");
    expect(banner.detail?.toLowerCase()).toContain("certified");
  });
});
