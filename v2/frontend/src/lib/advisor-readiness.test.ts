/**
 * Tests for the Advisor readiness model (Section A state machine).
 * Node environment — pure helpers only, no React.
 */

import type { IntelV3HeldCard, IntelV3RunResult, IntelV3Snapshot } from "@/lib/api";
import type { AdvisorTruthContract } from "@/lib/advisor-truth";
import {
  ADD_POSITIONS_SENTENCE,
  CERTIFIED_CURRENT_SENTENCE,
  NO_STALE_EVIDENCE_SENTENCE,
  QUEUE_ONLY_SENTENCE,
  RUN_IDLE_CERTIFIED_SENTENCE,
  RUN_IDLE_SENTENCE,
  RUN_REQUEST_FAILED_SENTENCE,
  SNAPSHOT_WRITES_DISABLED_SENTENCE,
  continueSentence,
  deriveActionCounts,
  deriveAdvisorReadiness,
  deriveRunJobs,
  deriveRunModel,
  deriveTruthRows,
  evidenceFreshnessLabel,
  formatSnapshotAge,
  isSnapshotMissingError,
} from "@/lib/advisor-readiness";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeCard(ticker: string, action: IntelV3HeldCard["action"]): IntelV3HeldCard {
  return { ticker, action } as IntelV3HeldCard;
}

function makeSnapshot(overrides: Partial<IntelV3Snapshot> = {}): IntelV3Snapshot {
  const base = {
    schema_version: "v3",
    snapshot_id: "snap_1",
    run_id: "run_1",
    generated_at: "2026-07-18T10:00:00Z",
    is_stale: false,
    source_health: { status: "ok" },
    portfolio_command_center: {
      total_holdings: 4,
      buy_count: 1,
      hold_count: 2,
      trim_count: 1,
      sell_count: 0,
      high_conviction: 1,
      thin_evidence: 0,
      source_health: { status: "ok" },
    },
    action_counts: { BUY: 1, HOLD: 2, TRIM: 1, SELL: 0 },
    evidence_band_counts: { THIN: 0, PARTIAL: 2, STRONG: 2 },
    conviction_counts: { LOW: 0, MEDIUM: 3, HIGH: 1 },
    best_buys: [],
    trim_sell_desk: [],
    current_holdings: [
      makeCard("VTI", "BUY"),
      makeCard("AAPL", "HOLD"),
      makeCard("MSFT", "HOLD"),
      makeCard("NVDA", "TRIM"),
    ],
    opportunity_radar_preview: { status: "deferred" as const },
    what_changed: [],
    warnings: [],
    legacy_path_used: false as const,
    snapshot_source: "worker_certified" as const,
    certified_holding_count: 4,
    total_holding_count: 4,
    evidence_freshness_state: "certified_current",
  };
  return { ...base, ...overrides } as IntelV3Snapshot;
}

function makeRunResult(overrides: Partial<IntelV3RunResult> = {}): IntelV3RunResult {
  return {
    status: "refresh_requested",
    queued_ticker_count: 0,
    on_demand_processing_enabled: true,
    on_demand_jobs_attempted: 0,
    on_demand_jobs_succeeded: 0,
    on_demand_jobs_failed: 0,
    snapshot_available_after_run: false,
    next_required_action: "reclick_run_intel_to_retry",
    ...overrides,
  };
}

const NO_RUN = { isRunPending: false, isRunError: false, lastRunResult: null };

function queryWith(snapshot: IntelV3Snapshot | null) {
  return { snapshot, isLoading: false, isError: false };
}

function makeTruth(overrides: Partial<AdvisorTruthContract> = {}): AdvisorTruthContract {
  return {
    portfolio_truth: "certified",
    price_truth: "ok",
    reconciliation: "pass",
    snapshot_value: 100_000,
    position_derived_value: 100_050,
    snapshot_stale: false,
    next_required_repair: null,
    as_of: "2026-07-18T10:00:00+00:00",
    ...overrides,
  };
}

// ── Run state machine ─────────────────────────────────────────────────────────

describe("deriveRunModel — state machine", () => {
  it("idle before any run", () => {
    const run = deriveRunModel(NO_RUN);
    expect(run.state).toBe("idle");
    expect(run.buttonLabel).toBe("Run Intel");
    expect(run.buttonBusy).toBe(false);
    expect(run.shouldRefetchSnapshot).toBe(false);
  });

  it("running while the request is in flight (disabled button)", () => {
    const run = deriveRunModel({
      isRunPending: true,
      isRunError: false,
      lastRunResult: null,
    });
    expect(run.state).toBe("running");
    expect(run.buttonBusy).toBe(true);
    expect(run.shouldRefetchSnapshot).toBe(false);
  });

  it("first bounded batch: partial progress with continue label and honest sentence", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeRunResult({
        queued_ticker_count: 10,
        on_demand_jobs_attempted: 5,
        on_demand_jobs_succeeded: 5,
        on_demand_jobs_failed: 0,
        snapshot_available_after_run: false,
        next_required_action:
          "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining",
      }),
    });
    expect(run.state).toBe("partial");
    expect(run.buttonLabel).toBe("Continue Intel run");
    expect(run.nextActionSentence).toBe(continueSentence(5, 10));
    expect(run.nextActionSentence).toBe(
      "This run refreshed 5 of 10 holdings. Continue to process the rest.",
    );
    expect(run.jobs).toEqual({
      queued: 10,
      attempted: 5,
      succeeded: 5,
      failed: 0,
      remaining: 5,
    });
    expect(run.boundedStopReason).toContain("5 holdings still waiting");
    expect(run.shouldRefetchSnapshot).toBe(false);
  });

  it("continuation batch completes: snapshot available, refetch requested", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeRunResult({
        queued_ticker_count: 5,
        on_demand_jobs_attempted: 5,
        on_demand_jobs_succeeded: 5,
        snapshot_available_after_run: true,
        next_required_action: "none_certified_snapshot_current",
      }),
    });
    expect(run.state).toBe("complete");
    expect(run.buttonLabel).toBe("Run Intel");
    expect(run.nextActionSentence).toBe(CERTIFIED_CURRENT_SENTENCE);
    expect(run.shouldRefetchSnapshot).toBe(true);
  });

  it("complete-with-refresh wins even when a reclick action string is present", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeRunResult({
        queued_ticker_count: 6,
        on_demand_jobs_succeeded: 6,
        snapshot_available_after_run: true,
        next_required_action: "reclick_run_intel_to_retry",
      }),
    });
    expect(run.state).toBe("complete");
    expect(run.shouldRefetchSnapshot).toBe(true);
  });

  it("all jobs failed and none succeeded → failed with retry label", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeRunResult({
        queued_ticker_count: 3,
        on_demand_jobs_attempted: 3,
        on_demand_jobs_succeeded: 0,
        on_demand_jobs_failed: 3,
        next_required_action: "reclick_run_intel_to_retry",
      }),
    });
    expect(run.state).toBe("failed");
    expect(run.buttonLabel).toBe("Retry Intel run");
    expect(run.nextActionSentence).toContain("3 jobs failed");
  });

  it("some jobs failed but some succeeded → partial, not failed", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeRunResult({
        queued_ticker_count: 4,
        on_demand_jobs_attempted: 4,
        on_demand_jobs_succeeded: 2,
        on_demand_jobs_failed: 2,
        next_required_action:
          "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining",
      }),
    });
    expect(run.state).toBe("partial");
    expect(run.buttonLabel).toBe("Continue Intel run");
  });

  it("failure→retry: request error yields failed regardless of prior result", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: true,
      lastRunResult: makeRunResult({ snapshot_available_after_run: true }),
    });
    expect(run.state).toBe("failed");
    expect(run.buttonLabel).toBe("Retry Intel run");
    expect(run.nextActionSentence).toBe(RUN_REQUEST_FAILED_SENTENCE);
    expect(run.shouldRefetchSnapshot).toBe(false);
  });

  it("enqueue_failed status → failed", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeRunResult({ status: "enqueue_failed" }),
    });
    expect(run.state).toBe("failed");
    expect(run.buttonLabel).toBe("Retry Intel run");
  });

  it("queue-only: on-demand disabled → queue_only with the exact honest sentence", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeRunResult({
        queued_ticker_count: 8,
        on_demand_processing_enabled: false,
        next_required_action:
          "queue_only_enable_intel_v3_on_demand_refresh_enabled_or_run_analyst_refresh_worker_entrypoint_separately",
      }),
    });
    expect(run.state).toBe("queue_only");
    expect(run.nextActionSentence).toBe(QUEUE_ONLY_SENTENCE);
    expect(run.nextActionSentence).toContain("paused on the server");
    expect(run.nextActionSentence).not.toMatch(/[A-Z0-9_]{10,}/); // no env-var names in visible copy
    expect(run.boundedStopReason).toContain("on-demand processing is disabled");
  });

  it("snapshot writes disabled by cost guard → failed with the cost-guard sentence", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeRunResult({
        queued_ticker_count: 4,
        on_demand_jobs_attempted: 4,
        on_demand_jobs_succeeded: 4,
        next_required_action:
          "on_demand_drain_completed_but_intel_v3_snapshot_writes_enabled_is_false",
      }),
    });
    expect(run.state).toBe("failed");
    expect(run.nextActionSentence).toBe(SNAPSHOT_WRITES_DISABLED_SENTENCE);
    expect(run.nextActionSentence).toContain("temporarily paused on the server");
    expect(run.nextActionSentence).not.toMatch(/INTEL_V3_[A-Z_]+/); // env-var name stays in technical detail only
  });

  it("no stale evidence to refresh → complete with honest no-op sentence", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeRunResult({
        status: "analyst_evidence_current",
        queued_ticker_count: 0,
        next_required_action: "none_no_stale_evidence_to_refresh",
      }),
    });
    expect(run.state).toBe("complete");
    expect(run.nextActionSentence).toBe(NO_STALE_EVIDENCE_SENTENCE);
  });

  it("no active holdings → idle with add-positions sentence", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeRunResult({
        status: "no_active_holdings",
        next_required_action: "add_positions_before_running_intel",
      }),
    });
    expect(run.state).toBe("idle");
    expect(run.nextActionSentence).toBe(ADD_POSITIONS_SENTENCE);
  });

  it("never leaks raw next_required_action codes into visible sentences", () => {
    const rawActions = [
      "queue_only_enable_intel_v3_on_demand_refresh_enabled_or_run_analyst_refresh_worker_entrypoint_separately",
      "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining",
      "on_demand_drain_completed_but_intel_v3_snapshot_writes_enabled_is_false",
      "none_certified_snapshot_current",
      "reclick_run_intel_to_retry",
    ];
    for (const action of rawActions) {
      const run = deriveRunModel({
        isRunPending: false,
        isRunError: false,
        lastRunResult: makeRunResult({
          queued_ticker_count: 2,
          on_demand_jobs_succeeded: 1,
          next_required_action: action,
        }),
      });
      expect(run.nextActionSentence).not.toContain(action);
      expect(run.nextActionSentence).not.toMatch(/reclick_|queue_only_/);
    }
  });
});

describe("deriveRunJobs", () => {
  it("computes remaining and never goes negative", () => {
    expect(
      deriveRunJobs(
        makeRunResult({
          queued_ticker_count: 3,
          on_demand_jobs_succeeded: 5,
          on_demand_jobs_failed: 1,
        }),
      ).remaining,
    ).toBe(0);
    expect(deriveRunJobs(null)).toEqual({
      queued: 0,
      attempted: 0,
      succeeded: 0,
      failed: 0,
      remaining: 0,
    });
  });
});

// ── Snapshot-side readiness ───────────────────────────────────────────────────

describe("deriveAdvisorReadiness — snapshot states", () => {
  const NOW = new Date("2026-07-18T12:00:00Z");

  it("no-snapshot 404 state: missing, never Ready", () => {
    const model = deriveAdvisorReadiness(
      {
        snapshot: null,
        isLoading: false,
        isError: true,
        errorMessage: "API error: 404",
      },
      NO_RUN,
      null,
      NOW,
    );
    expect(model.snapshotState).toBe("missing");
    expect(model.ready).toBe(false);
    expect(model.statusPillLabel).not.toBe("Ready");
    expect(model.generatedAt).toBeNull();
    expect(model.actionCounts).toEqual({ BUY: 0, HOLD: 0, TRIM: 0, SELL: 0 });
    // Rows are honest Unknowns without a snapshot and without the truth endpoint.
    const certRow = model.truthRows.find((r) => r.key === "intel_certification");
    expect(certRow?.status).toBe("unavailable");
    expect(certRow?.detail).toContain("Unknown");
  });

  it("collapsed 404 detail object ('[object Object]') is treated as missing", () => {
    expect(isSnapshotMissingError("[object Object]")).toBe(true);
    expect(isSnapshotMissingError("No Intel v3 snapshot exists yet. Run Intel v3 first.")).toBe(true);
    expect(isSnapshotMissingError("network down")).toBe(false);
  });

  it("non-404 error → error state, not missing, never Ready", () => {
    const model = deriveAdvisorReadiness(
      {
        snapshot: null,
        isLoading: false,
        isError: true,
        errorMessage: "network down",
      },
      NO_RUN,
      null,
      NOW,
    );
    expect(model.snapshotState).toBe("error");
    expect(model.ready).toBe(false);
  });

  it("certified current snapshot → ready with derived action counts", () => {
    const model = deriveAdvisorReadiness(queryWith(makeSnapshot()), NO_RUN, null, NOW);
    expect(model.snapshotState).toBe("certified");
    expect(model.ready).toBe(true);
    expect(model.statusPillLabel).toBe("Ready");
    // Counts derived from current_holdings, not the summary block.
    expect(model.actionCounts).toEqual({ BUY: 1, HOLD: 2, TRIM: 1, SELL: 0 });
    expect(model.certifiedCount).toBe(4);
    expect(model.totalCount).toBe(4);
    expect(model.snapshotAgeLabel).toBe("2h ago");
    expect(model.evidenceFreshnessLabel).toBe("Evidence current");
    const certRow = model.truthRows.find((r) => r.key === "intel_certification");
    expect(certRow?.status).toBe("ok");
    const sourceRow = model.truthRows.find((r) => r.key === "snapshot_source_health");
    expect(sourceRow?.status).toBe("ok");
    const reconRow = model.truthRows.find((r) => r.key === "books_reconciliation");
    expect(reconRow?.status).toBe("unavailable");
  });

  it("stale snapshot detection: is_stale certified snapshot is NOT ready", () => {
    const model = deriveAdvisorReadiness(
      queryWith(makeSnapshot({ is_stale: true })),
      NO_RUN,
      null,
      NOW,
    );
    expect(model.snapshotState).toBe("stale");
    expect(model.ready).toBe(false);
    expect(model.statusPillLabel).not.toBe("Ready");
  });

  it("republish_pending freshness blocks ready", () => {
    const model = deriveAdvisorReadiness(
      queryWith(makeSnapshot({ evidence_freshness_state: "republish_pending" })),
      NO_RUN,
      null,
      NOW,
    );
    expect(model.ready).toBe(false);
    expect(model.snapshotState).toBe("stale");
  });

  it("uncertified snapshot (partial coverage) is never Ready", () => {
    const model = deriveAdvisorReadiness(
      queryWith(makeSnapshot({ certified_holding_count: 2, total_holding_count: 4 })),
      NO_RUN,
      null,
      NOW,
    );
    expect(model.snapshotState).toBe("uncertified");
    expect(model.ready).toBe(false);
    expect(model.statusPillLabel).not.toBe("Ready");
  });

  it("a complete run without a certified snapshot still never reports Ready", () => {
    const model = deriveAdvisorReadiness(
      {
        snapshot: null,
        isLoading: false,
        isError: true,
        errorMessage: "API error: 404",
      },
      {
        isRunPending: false,
        isRunError: false,
        // Run claims completion, but the snapshot query says nothing exists.
        lastRunResult: makeRunResult({
          snapshot_available_after_run: true,
          next_required_action: "none_certified_snapshot_current",
        }),
      },
      null,
      NOW,
    );
    expect(model.ready).toBe(false);
    expect(model.statusPillLabel).not.toBe("Ready");
    expect(model.run.state).toBe("complete");
    expect(model.run.shouldRefetchSnapshot).toBe(true);
  });

  it("idle + certified snapshot: sentence says refresh, not generate", () => {
    const model = deriveAdvisorReadiness(queryWith(makeSnapshot()), NO_RUN, null, NOW);
    expect(model.run.state).toBe("idle");
    expect(model.run.nextActionSentence).toBe(RUN_IDLE_CERTIFIED_SENTENCE);
    expect(model.run.nextActionSentence).not.toContain("generate a certified snapshot");
  });

  it("idle without a certified snapshot keeps the generate sentence", () => {
    const model = deriveAdvisorReadiness(
      {
        snapshot: null,
        isLoading: false,
        isError: true,
        errorMessage: "API error: 404",
      },
      NO_RUN,
      null,
      NOW,
    );
    expect(model.run.state).toBe("idle");
    expect(model.run.nextActionSentence).toBe(RUN_IDLE_SENTENCE);
  });

  it("idle + certified never overrides the add-positions sentence", () => {
    const model = deriveAdvisorReadiness(
      queryWith(makeSnapshot()),
      {
        isRunPending: false,
        isRunError: false,
        lastRunResult: makeRunResult({
          status: "no_active_holdings",
          next_required_action: "add_positions_before_running_intel",
        }),
      },
      null,
      NOW,
    );
    expect(model.run.state).toBe("idle");
    expect(model.run.nextActionSentence).toBe(ADD_POSITIONS_SENTENCE);
  });

  it("loading state", () => {
    const model = deriveAdvisorReadiness(
      { snapshot: null, isLoading: true, isError: false },
      NO_RUN,
      null,
      NOW,
    );
    expect(model.snapshotState).toBe("loading");
    expect(model.ready).toBe(false);
  });
});

// ── Truth-row vocabulary (six-dimension contract) ─────────────────────────────

describe("deriveTruthRows — renamed Intel vocabulary + endpoint-fed truth rows", () => {
  const NOW = new Date("2026-07-18T12:00:00Z");

  it("exposes the six-dimension vocabulary with no legacy labels", () => {
    const rows = deriveTruthRows(makeSnapshot(), "certified", null);
    expect(rows.map((r) => r.key)).toEqual([
      "intel_certification",
      "intel_evidence_freshness",
      "snapshot_source_health",
      "portfolio_financial_truth",
      "current_price_truth",
      "books_reconciliation",
    ]);
    expect(rows.map((r) => r.label)).toEqual([
      "Intel certification",
      "Intel evidence freshness",
      "Snapshot source health",
      "Portfolio financial truth",
      "Current-price truth",
      "Books reconciliation",
    ]);
    // Snapshot fields must no longer feed anything labeled portfolio/price truth.
    expect(rows.map((r) => r.label)).not.toContain("Portfolio truth");
    expect(rows.map((r) => r.label)).not.toContain("Price truth");
  });

  it("truth rows are honest Unknowns by default (endpoint not fetched)", () => {
    const rows = deriveTruthRows(makeSnapshot(), "certified", null);
    for (const key of [
      "portfolio_financial_truth",
      "current_price_truth",
      "books_reconciliation",
    ] as const) {
      const row = rows.find((r) => r.key === key);
      expect(row?.status).toBe("unavailable");
      expect(row?.detail).toContain("Unknown");
    }
  });

  it("worker-certified Intel must NOT mark portfolio financial truth certified", () => {
    // Fully certified, current Intel snapshot — but no truth endpoint result.
    const model = deriveAdvisorReadiness(queryWith(makeSnapshot()), NO_RUN, null, NOW);
    expect(model.ready).toBe(true); // Intel-side readiness
    const intelRow = model.truthRows.find((r) => r.key === "intel_certification");
    expect(intelRow?.status).toBe("ok");
    // Financial truth stays unknown — Intel certification is not financial truth.
    const financialRow = model.truthRows.find(
      (r) => r.key === "portfolio_financial_truth",
    );
    expect(financialRow?.status).toBe("unavailable");
    expect(financialRow?.detail).toContain("Unknown");
  });

  it("endpoint-fed rows reflect the truth contract when provided", () => {
    const model = deriveAdvisorReadiness(
      queryWith(makeSnapshot()),
      NO_RUN,
      makeTruth(),
      NOW,
    );
    expect(
      model.truthRows.find((r) => r.key === "portfolio_financial_truth")?.status,
    ).toBe("ok");
    expect(
      model.truthRows.find((r) => r.key === "current_price_truth")?.status,
    ).toBe("ok");
    expect(
      model.truthRows.find((r) => r.key === "books_reconciliation")?.status,
    ).toBe("ok");
  });

  it("degraded/blocked truth dimensions map to pending/blocked row statuses", () => {
    const rows = deriveTruthRows(
      makeSnapshot(),
      "certified",
      makeTruth({
        portfolio_truth: "degraded",
        price_truth: "missing",
        reconciliation: "blocked",
      }),
    );
    expect(rows.find((r) => r.key === "portfolio_financial_truth")?.status).toBe("pending");
    expect(rows.find((r) => r.key === "current_price_truth")?.status).toBe("blocked");
    expect(rows.find((r) => r.key === "books_reconciliation")?.status).toBe("blocked");
  });

  it("stale price truth maps to pending, and stale intel freshness to pending", () => {
    const rows = deriveTruthRows(
      makeSnapshot({ evidence_freshness_state: "republish_pending" }),
      "stale",
      makeTruth({ price_truth: "stale" }),
    );
    expect(rows.find((r) => r.key === "current_price_truth")?.status).toBe("pending");
    expect(rows.find((r) => r.key === "intel_evidence_freshness")?.status).toBe("pending");
  });

  it("intel evidence freshness is its own row with honest unknown handling", () => {
    const certified = deriveTruthRows(makeSnapshot(), "certified", null);
    expect(certified.find((r) => r.key === "intel_evidence_freshness")?.status).toBe("ok");

    const noFreshness = deriveTruthRows(
      makeSnapshot({ evidence_freshness_state: undefined }),
      "certified",
      null,
    );
    expect(noFreshness.find((r) => r.key === "intel_evidence_freshness")?.status).toBe(
      "unavailable",
    );
  });
});

// ── Small helpers ─────────────────────────────────────────────────────────────

describe("helpers", () => {
  it("deriveActionCounts counts only valid actions from current_holdings", () => {
    const snap = makeSnapshot({
      current_holdings: [
        makeCard("A", "BUY"),
        makeCard("B", "BUY"),
        makeCard("C", "SELL"),
      ],
    });
    expect(deriveActionCounts(snap)).toEqual({ BUY: 2, HOLD: 0, TRIM: 0, SELL: 1 });
    expect(deriveActionCounts(null)).toEqual({ BUY: 0, HOLD: 0, TRIM: 0, SELL: 0 });
  });

  it("formatSnapshotAge buckets minutes/hours/days", () => {
    const now = new Date("2026-07-18T12:00:00Z");
    expect(formatSnapshotAge("2026-07-18T11:59:40Z", now)).toBe("just now");
    expect(formatSnapshotAge("2026-07-18T11:30:00Z", now)).toBe("30m ago");
    expect(formatSnapshotAge("2026-07-18T06:00:00Z", now)).toBe("6h ago");
    expect(formatSnapshotAge("2026-07-15T12:00:00Z", now)).toBe("3d ago");
    expect(formatSnapshotAge(null, now)).toBeNull();
    expect(formatSnapshotAge("not-a-date", now)).toBeNull();
  });

  it("evidenceFreshnessLabel maps known states and is honest for unknown ones", () => {
    expect(evidenceFreshnessLabel("certified_current")).toBe("Evidence current");
    expect(evidenceFreshnessLabel("no_snapshot_exists")).toBe("No evidence snapshot yet");
    expect(evidenceFreshnessLabel("weird_new_state")).toBe("Evidence freshness unknown");
    expect(evidenceFreshnessLabel(null)).toBeNull();
  });
});

// The readiness pill describes Intel state, never whole-system readiness —
// the panel renders the "Ready" pill as "Intel Ready" (see
// AdvisorReadinessPanel), keeping financial-truth dimensions separate.
it('panel renders the Ready pill as "Intel Ready" so it cannot read as system-wide readiness', () => {
  const fs = require("fs");
  const src = fs.readFileSync(
    __dirname + "/../components/advisor/AdvisorReadinessPanel.tsx",
    "utf8"
  );
  expect(src).toContain('"Intel Ready"');
});
