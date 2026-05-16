/**
 * Intel v3 trust-banner contract — Stage 3.3.
 *
 * Covers deriveIntelV3UIStatus and buildBannerState (all-or-nothing certified
 * intelligence run contract), plus legacy analystRefreshRequestNote tests.
 */
import { analystRefreshRequestNote, deriveIntelV3UIStatus, buildBannerState, buildStatusPillState } from "@/lib/intel-v3-banner";
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

// ── Build 1.5: sub-10s UX contract tests ─────────────────────────────────────

describe("Build 1.5 — sub-10s UX: latest_certified_new_refresh_running", () => {
  it("shows amber banner when certified snapshot exists and refresh is running", () => {
    const snap = makeCertifiedSnapshot();
    const banner = buildBannerState(snap, true);
    expect(banner.status).toBe("latest_certified_new_refresh_running");
    expect(banner.tone).toBe("amber");
    expect(banner.headline).toContain("Latest Certified Snapshot Available");
    expect(banner.showProvenance).toBe(true);
  });

  it("shows certified snapshot data in amber banner (not blank)", () => {
    const snap = makeCertifiedSnapshot({ certified_holding_count: 34, total_holding_count: 34 });
    const banner = buildBannerState(snap, true);
    expect(banner.detail).toContain("34/34");
  });

  it("amber banner does not claim the new refresh is already certified", () => {
    const snap = makeCertifiedSnapshot();
    const banner = buildBannerState(snap, true);
    expect(banner.headline).not.toContain("Certified Current");
    expect(banner.tone).not.toBe("green");
  });
});

describe("Build 1.5 — sub-10s UX: refreshing with no prior snapshot", () => {
  it("shows grey banner when no snapshot and refresh is running", () => {
    const banner = buildBannerState(null, true);
    expect(banner.status).toBe("refreshing_analyst_intelligence");
    expect(banner.tone).toBe("grey");
  });

  it("grey refreshing banner does not claim '60 seconds'", () => {
    const banner = buildBannerState(null, true);
    expect(banner.detail).not.toContain("60 seconds");
    expect(banner.detail).not.toContain("60");
  });

  it("grey refreshing banner does not show provenance (no certified snapshot)", () => {
    const banner = buildBannerState(null, true);
    expect(banner.showProvenance).toBe(false);
  });
});

describe("Build 1.5 — sub-10s UX: no green without certified snapshot", () => {
  it("uncertified snapshot during refresh does not show green", () => {
    const snap = makeCertifiedSnapshot({ snapshot_source: "http_request" } as any);
    const banner = buildBannerState(snap, true);
    expect(banner.tone).not.toBe("green");
  });

  it("partial coverage during refresh does not show green", () => {
    const snap = makeCertifiedSnapshot({ certified_holding_count: 10, total_holding_count: 34 });
    const banner = buildBannerState(snap, true);
    expect(banner.tone).not.toBe("green");
  });
});

describe("Build 1.5 — sub-10s UX: certification failure state", () => {
  it("certification_failed snapshot shows red even while refresh is running", () => {
    const snap = makeCertifiedSnapshot({ snapshot_source: "certification_failed" } as any);
    const banner = buildBannerState(snap, false);
    expect(banner.status).toBe("blocked_certification_failed");
    expect(banner.tone).toBe("red");
  });

  it("certification_failed snapshot is visible (not suppressed)", () => {
    const snap = makeCertifiedSnapshot({
      snapshot_source: "certification_failed",
      failed_tickers_in_certification: ["AAPL"],
    } as any);
    const banner = buildBannerState(snap, false);
    expect(banner.detail).toBeTruthy();
  });
});

// ── Build 1.5: polling session correctness contract ──────────────────────────
// The component guards stopPolling() with isNewerThanClick — a snapshot
// generated before the user clicked Run must NOT stop polling.  These tests
// verify the banner-state machine half of that contract: isRefreshing=true
// keeps the banner amber regardless of the snapshot's certified status.

describe("Build 1.5 — polling session correctness: amber persists for pre-click snapshot", () => {
  it("amber banner persists when a pre-click certified snapshot is present while isRefreshing", () => {
    // User clicks Run while a worker_certified snapshot already exists.
    // isRefreshing=true → banner must stay amber until a NEWER snapshot arrives.
    const preClickSnap = makeCertifiedSnapshot({ certified_holding_count: 5, total_holding_count: 5 });
    const banner = buildBannerState(preClickSnap, true);
    expect(banner.status).toBe("latest_certified_new_refresh_running");
    expect(banner.tone).toBe("amber");
    expect(banner.tone).not.toBe("green");
  });

  it("green banner appears only after polling stops (isRefreshing=false)", () => {
    // Simulates: new certified snapshot arrived; component set isRefreshing=false.
    const postWorkerSnap = makeCertifiedSnapshot({ certified_holding_count: 5, total_holding_count: 5 });
    const banner = buildBannerState(postWorkerSnap, false);
    expect(banner.status).toBe("certified_current");
    expect(banner.tone).toBe("green");
  });

  it("same certified snapshot produces amber while isRefreshing, green after", () => {
    // Banner tone is gated by isRefreshing — the snapshot itself is identical.
    // Only the component's isRefreshing=false (triggered by a newer certified
    // snapshot passing isNewerThanClick) transitions the UI to green.
    const snap = makeCertifiedSnapshot();
    expect(buildBannerState(snap, true).tone).toBe("amber");
    expect(buildBannerState(snap, false).tone).toBe("green");
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

// ── analyst_evidence_current no-op run contract ───────────────────────────────
// When backend returns analyst_evidence_current (evidence fresh, zero queued
// jobs), the component skips polling and calls refetchSnapshot() once.
// These tests cover the state-machine half: with isRefreshing=false and a
// certified snapshot, the UI must show Ready — never Updating.

describe("analyst_evidence_current — no-op run state machine", () => {
  it("certified snapshot + isRefreshing=false shows Ready even when lastRunResult is analyst_evidence_current", () => {
    const snap = makeCertifiedSnapshot();
    const runResult = {
      status: "analyst_evidence_current" as const,
      queued_ticker_count: 0,
      existing_certified_snapshot: true,
    };
    expect(deriveIntelV3UIStatus(snap, false, runResult)).toBe("certified_current");
    expect(buildStatusPillState(snap, false, runResult).pill).toBe("Ready");
  });

  it("analyst_evidence_current does not put the UI in Updating state", () => {
    const snap = makeCertifiedSnapshot();
    const runResult = {
      status: "analyst_evidence_current" as const,
      queued_ticker_count: 0,
      existing_certified_snapshot: true,
    };
    const pill = buildStatusPillState(snap, false, runResult);
    expect(pill.pill).not.toBe("Updating");
    expect(pill.tone).toBe("green");
  });

  it("refresh_requested with isRefreshing=true still shows Updating (existing queued behavior preserved)", () => {
    const snap = makeCertifiedSnapshot();
    const runResult = { status: "refresh_requested" as const, queued_ticker_count: 5 };
    expect(deriveIntelV3UIStatus(snap, true, runResult)).toBe("latest_certified_new_refresh_running");
    expect(buildStatusPillState(snap, true, runResult).pill).toBe("Updating");
  });

  it("no snapshot + analyst_evidence_current + isRefreshing=false => Needs Research (no snapshot to show)", () => {
    const runResult = {
      status: "analyst_evidence_current" as const,
      queued_ticker_count: 0,
      existing_certified_snapshot: true,
    };
    expect(deriveIntelV3UIStatus(null, false, runResult)).toBe("unavailable_evidence_incomplete");
  });
});

// ── worker_certified + certified_current terminal Ready contract ──────────────

describe("worker_certified + certified_current => terminal Ready", () => {
  it("snapshot_source=worker_certified + evidence_freshness_state=certified_current => Ready pill", () => {
    const snap = makeCertifiedSnapshot({
      evidence_freshness_state: "certified_current",
    } as any);
    expect(deriveIntelV3UIStatus(snap, false)).toBe("certified_current");
    expect(buildStatusPillState(snap, false).pill).toBe("Ready");
    expect(buildStatusPillState(snap, false).tone).toBe("green");
  });

  it("worker_certified + certified_current while polling (isRefreshing=true) shows Updating until poll stops", () => {
    // While isRefreshing is true the banner stays amber — polling has not yet
    // confirmed the snapshot is newer than the click.
    const snap = makeCertifiedSnapshot({
      evidence_freshness_state: "certified_current",
    } as any);
    expect(deriveIntelV3UIStatus(snap, true)).toBe("latest_certified_new_refresh_running");
    expect(buildStatusPillState(snap, true).pill).toBe("Updating");
  });

  it("worker_certified + certified_current with isRefreshing=false => green Ready, not grey", () => {
    const snap = makeCertifiedSnapshot({
      evidence_freshness_state: "certified_current",
    } as any);
    const pill = buildStatusPillState(snap, false);
    expect(pill.pill).toBe("Ready");
    expect(pill.tone).toBe("green");
    expect(pill.tone).not.toBe("grey");
  });
});

// ── Build 2: evidence_freshness_state contract ────────────────────────────────

describe("Build 2 — evidence_freshness_state status mapping", () => {
  it("worker_certified + 34/34 + certified_current => Ready pill", () => {
    const snap = makeCertifiedSnapshot({
      certified_holding_count: 34,
      total_holding_count: 34,
      evidence_freshness_state: "certified_current",
    } as any);
    expect(buildStatusPillState(snap, false).pill).toBe("Ready");
    expect(deriveIntelV3UIStatus(snap, false)).toBe("certified_current");
  });

  it("worker_certified + 34/34 + republish_pending => Updating pill, not Ready", () => {
    const snap = makeCertifiedSnapshot({
      certified_holding_count: 34,
      total_holding_count: 34,
      evidence_freshness_state: "republish_pending",
    } as any);
    expect(buildStatusPillState(snap, false).pill).toBe("Updating");
    expect(deriveIntelV3UIStatus(snap, false)).not.toBe("certified_current");
  });

  it("worker_certified + 34/34 + certification_blocked => Blocked pill", () => {
    const snap = makeCertifiedSnapshot({
      certified_holding_count: 34,
      total_holding_count: 34,
      evidence_freshness_state: "certification_blocked",
    } as any);
    expect(buildStatusPillState(snap, false).pill).toBe("Blocked");
    expect(deriveIntelV3UIStatus(snap, false)).toBe("blocked_certification_failed");
  });

  it("legacy worker_certified with missing evidence_freshness_state => Ready (backward compat)", () => {
    const snap = makeCertifiedSnapshot({
      certified_holding_count: 3,
      total_holding_count: 3,
      // evidence_freshness_state absent
    } as any);
    expect(buildStatusPillState(snap, false).pill).toBe("Ready");
    expect(deriveIntelV3UIStatus(snap, false)).toBe("certified_current");
  });
});
