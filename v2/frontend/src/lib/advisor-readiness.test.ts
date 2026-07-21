/**
 * Tests for the Advisor readiness model (Section A state machine).
 * Node environment — pure helpers only, no React.
 *
 * Run model contract (distributed workflow): the run state machine derives
 * from the durable session-status payload (session_status / plain_status /
 * completed_snapshot_id) — never from batch/job counts. There are no
 * "partial" or "queue_only" states and no browser-driven continuation.
 */

import type {
  IntelV3HeldCard,
  IntelV3SessionStatus,
  IntelV3Snapshot,
} from "@/lib/api";
import type { AdvisorTruthContract } from "@/lib/advisor-truth";
import {
  ADD_POSITIONS_SENTENCE,
  RUN_COMPLETED_SENTENCE,
  RUN_COMPLETED_WITH_GAPS_SENTENCE,
  RUN_FAILED_SENTENCE,
  RUN_IDLE_CERTIFIED_SENTENCE,
  RUN_IDLE_SENTENCE,
  RUN_IN_PROGRESS_SENTENCE,
  RUN_NOT_FOUND_SENTENCE,
  RUN_REQUEST_FAILED_SENTENCE,
  deriveActionCounts,
  deriveAdvisorReadiness,
  deriveRunModel,
  deriveRunProgress,
  deriveTruthRows,
  evidenceFreshnessLabel,
  formatSnapshotAge,
  isSnapshotMissingError,
  isTerminalSessionStatus,
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

function makeSessionStatus(
  overrides: Partial<IntelV3SessionStatus> = {},
): IntelV3SessionStatus {
  return {
    run_session_id: "3f0a26aa-8f5c-4a2e-9d7b-2f8f0a1b2c3d",
    session_status: "running",
    workflow_version: 2,
    current_stage: "collecting_evidence",
    total_tickers: 4,
    evidence_complete_tickers: 2,
    analysis_complete_tickers: 1,
    decision_complete_tickers: 0,
    decided_tickers: 0,
    failed_or_degraded_tickers: 0,
    task_counts: { pending: 6, running: 1, succeeded: 3 },
    completed_snapshot_id: null,
    plain_status: "Gathering evidence — 2 of 4 holdings",
    retryable: true,
    terminal: false,
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

describe("deriveRunModel — distributed session state machine", () => {
  it("idle before any run", () => {
    const run = deriveRunModel(NO_RUN);
    expect(run.state).toBe("idle");
    expect(run.buttonLabel).toBe("Run Intel");
    expect(run.buttonBusy).toBe(false);
    expect(run.nextActionSentence).toBe(RUN_IDLE_SENTENCE);
    expect(run.shouldRefetchSnapshot).toBe(false);
  });

  it("running while pending with no status yet (create request in flight)", () => {
    const run = deriveRunModel({
      isRunPending: true,
      isRunError: false,
      lastRunResult: null,
    });
    expect(run.state).toBe("running");
    expect(run.buttonLabel).toBe("Running…");
    expect(run.buttonBusy).toBe(true);
    expect(run.nextActionSentence).toBe(RUN_IN_PROGRESS_SENTENCE);
    expect(run.shouldRefetchSnapshot).toBe(false);
  });

  it("running renders the backend's pre-sanitized plain_status sentence", () => {
    const run = deriveRunModel({
      isRunPending: true,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        plain_status: "Specialist analysis — 3 of 4 holdings",
        current_stage: "specialist_analysis",
      }),
    });
    expect(run.state).toBe("running");
    expect(run.buttonBusy).toBe(true);
    expect(run.nextActionSentence).toBe("Specialist analysis — 3 of 4 holdings");
  });

  it("a non-terminal status still reads running even if pending flag lags", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({ session_status: "created" }),
    });
    expect(run.state).toBe("running");
    expect(run.buttonBusy).toBe(true);
  });

  it("completed → complete, refetch requested when a snapshot was published", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "completed",
        current_stage: "done",
        decision_complete_tickers: 4,
        decided_tickers: 4,
        completed_snapshot_id: "snap-42",
        plain_status: "Completed — your recommendations are up to date.",
        terminal: true,
        retryable: false,
      }),
    });
    expect(run.state).toBe("complete");
    expect(run.buttonLabel).toBe("Run Intel");
    expect(run.buttonBusy).toBe(false);
    expect(run.shouldRefetchSnapshot).toBe(true);
    expect(run.completedWithGaps).toBe(false);
    expect(run.nextActionSentence).toBe(
      "Completed — your recommendations are up to date.",
    );
  });

  it("completed without a published snapshot id does not request a refetch", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "completed",
        completed_snapshot_id: null,
        plain_status: undefined,
        terminal: true,
      }),
    });
    expect(run.state).toBe("complete");
    expect(run.shouldRefetchSnapshot).toBe(false);
    expect(run.nextActionSentence).toBe(RUN_COMPLETED_SENTENCE);
  });

  it("completed_with_gaps → complete with a caveat sentence, never failed", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "completed_with_gaps",
        decision_complete_tickers: 4,
        decided_tickers: 3,
        failed_or_degraded_tickers: 1,
        completed_snapshot_id: "snap-43",
        plain_status:
          "Completed with gaps — some holdings had limited evidence this run.",
        terminal: true,
      }),
    });
    expect(run.state).toBe("complete");
    expect(run.state).not.toBe("failed");
    expect(run.completedWithGaps).toBe(true);
    expect(run.buttonLabel).toBe("Run Intel");
    expect(run.shouldRefetchSnapshot).toBe(true);
    expect(run.nextActionSentence).toContain("gaps");
  });

  it("completed_with_gaps without plain_status falls back to the caveat constant", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "completed_with_gaps",
        completed_snapshot_id: "snap-44",
        plain_status: undefined,
        terminal: true,
      }),
    });
    expect(run.state).toBe("complete");
    expect(run.nextActionSentence).toBe(RUN_COMPLETED_WITH_GAPS_SENTENCE);
  });

  it("REGRESSION KILLED: zero decided holdings never reads as terminal failure while the run executes", () => {
    // The retired drain-era rule treated a zero-success batch as failed.
    // There are no batches anymore: a running session with nothing decided
    // yet (and even some degraded holdings) is simply still running.
    const run = deriveRunModel({
      isRunPending: true,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "running",
        decision_complete_tickers: 0,
        decided_tickers: 0,
        failed_or_degraded_tickers: 2,
      }),
    });
    expect(run.state).toBe("running");
    expect(run.state).not.toBe("failed");
    expect(run.buttonBusy).toBe(true);
  });

  it("REGRESSION KILLED: a fully-degraded completed_with_gaps run is complete-with-caveat, not failed", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "completed_with_gaps",
        decision_complete_tickers: 4,
        decided_tickers: 0,
        failed_or_degraded_tickers: 4,
        completed_snapshot_id: "snap-45",
        terminal: true,
      }),
    });
    expect(run.state).toBe("complete");
    expect(run.completedWithGaps).toBe(true);
    expect(run.buttonLabel).not.toBe("Retry Intel run");
  });

  it("session failed → failed with Retry label and the backend sentence", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "failed",
        plain_status: "This run could not finish. You can start a new run.",
        terminal: true,
        retryable: false,
      }),
    });
    expect(run.state).toBe("failed");
    expect(run.buttonLabel).toBe("Retry Intel run");
    expect(run.buttonBusy).toBe(false);
    expect(run.nextActionSentence).toBe(
      "This run could not finish. You can start a new run.",
    );
    expect(run.shouldRefetchSnapshot).toBe(false);
  });

  it("session failed without plain_status falls back to the failed constant", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "failed",
        plain_status: undefined,
        terminal: true,
      }),
    });
    expect(run.state).toBe("failed");
    expect(run.nextActionSentence).toBe(RUN_FAILED_SENTENCE);
  });

  it("not_created / no_active_holdings → idle with the add-positions sentence", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "not_created",
        reason: "no_active_holdings",
        plain_status: "Add positions before running Intel.",
        created: false,
        retryable: false,
      }),
    });
    expect(run.state).toBe("idle");
    expect(run.buttonLabel).toBe("Run Intel");
    expect(run.nextActionSentence).toBe(ADD_POSITIONS_SENTENCE);
  });

  it("not_created / run_session_create_failed → failed with the backend sentence", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "not_created",
        reason: "run_session_create_failed",
        plain_status:
          "Could not start the run. If this persists, verify migration " +
          "027_intel_run_distributed_tasks.sql has been applied, then retry.",
        retryable: true,
      }),
    });
    expect(run.state).toBe("failed");
    expect(run.buttonLabel).toBe("Retry Intel run");
    expect(run.nextActionSentence).toContain("Could not start the run");
  });

  it("not_found → failed with an honest restart sentence", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "not_found",
        plain_status: undefined,
        retryable: false,
      }),
    });
    expect(run.state).toBe("failed");
    expect(run.buttonLabel).toBe("Retry Intel run");
    expect(run.nextActionSentence).toBe(RUN_NOT_FOUND_SENTENCE);
  });

  it("request-level error yields failed regardless of any prior result", () => {
    const run = deriveRunModel({
      isRunPending: false,
      isRunError: true,
      lastRunResult: makeSessionStatus({
        session_status: "completed",
        completed_snapshot_id: "snap-46",
        terminal: true,
      }),
    });
    expect(run.state).toBe("failed");
    expect(run.buttonLabel).toBe("Retry Intel run");
    expect(run.nextActionSentence).toBe(RUN_REQUEST_FAILED_SENTENCE);
    expect(run.shouldRefetchSnapshot).toBe(false);
  });

  it("never leaks raw session/stage enum values into visible sentences", () => {
    const rawValues = [
      "completed_with_gaps",
      "not_created",
      "not_found",
      "collecting_evidence",
      "specialist_analysis",
      "run_session_create_failed",
      "no_active_holdings",
    ];
    const inputs = [
      makeSessionStatus({ session_status: "running", plain_status: undefined }),
      makeSessionStatus({
        session_status: "completed_with_gaps",
        plain_status: undefined,
        terminal: true,
      }),
      makeSessionStatus({
        session_status: "failed",
        plain_status: undefined,
        terminal: true,
      }),
      makeSessionStatus({
        session_status: "not_created",
        reason: "run_session_create_failed",
        plain_status: undefined,
      }),
      makeSessionStatus({
        session_status: "not_created",
        reason: "no_active_holdings",
        plain_status: undefined,
      }),
      makeSessionStatus({ session_status: "not_found", plain_status: undefined }),
    ];
    for (const lastRunResult of inputs) {
      const run = deriveRunModel({
        isRunPending: false,
        isRunError: false,
        lastRunResult,
      });
      for (const raw of rawValues) {
        expect(run.nextActionSentence).not.toContain(raw);
      }
    }
  });
});

describe("isTerminalSessionStatus", () => {
  it("true for the terminal flag and every terminal session_status", () => {
    expect(
      isTerminalSessionStatus(makeSessionStatus({ terminal: true })),
    ).toBe(true);
    for (const status of [
      "completed",
      "completed_with_gaps",
      "failed",
      "not_created",
      "not_found",
    ]) {
      expect(
        isTerminalSessionStatus(
          makeSessionStatus({ session_status: status, terminal: undefined }),
        ),
      ).toBe(true);
    }
  });

  it("false for live sessions and missing results", () => {
    expect(isTerminalSessionStatus(makeSessionStatus())).toBe(false);
    expect(
      isTerminalSessionStatus(makeSessionStatus({ session_status: "created" })),
    ).toBe(false);
    expect(isTerminalSessionStatus(null)).toBe(false);
    expect(isTerminalSessionStatus(undefined)).toBe(false);
  });
});

describe("deriveRunProgress", () => {
  it("maps ticker progress fields and defaults to zero", () => {
    expect(
      deriveRunProgress(
        makeSessionStatus({
          total_tickers: 6,
          decision_complete_tickers: 4,
          failed_or_degraded_tickers: 1,
        }),
      ),
    ).toEqual({ totalTickers: 6, decidedTickers: 4, failedOrDegradedTickers: 1 });
    expect(deriveRunProgress(null)).toEqual({
      totalTickers: 0,
      decidedTickers: 0,
      failedOrDegradedTickers: 0,
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

  it("a completed run without a certified snapshot still never reports Ready", () => {
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
        // Session claims completion, but the snapshot query says nothing exists.
        lastRunResult: makeSessionStatus({
          session_status: "completed",
          completed_snapshot_id: "snap-42",
          terminal: true,
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
        lastRunResult: makeSessionStatus({
          session_status: "not_created",
          reason: "no_active_holdings",
          plain_status: "Add positions before running Intel.",
        }),
      },
      null,
      NOW,
    );
    expect(model.run.state).toBe("idle");
    expect(model.run.nextActionSentence).toBe(ADD_POSITIONS_SENTENCE);
  });

  it("a running session keeps the pill in Updating, never green", () => {
    const model = deriveAdvisorReadiness(
      queryWith(makeSnapshot()),
      {
        isRunPending: true,
        isRunError: false,
        lastRunResult: makeSessionStatus({ session_status: "running" }),
      },
      null,
      NOW,
    );
    expect(model.run.state).toBe("running");
    expect(model.statusPillLabel).toBe("Updating");
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

// ── worker_certified_with_gaps — valid-but-caveated (amber), never blocked ────

function makeWithGapsSnapshot(overrides: Partial<IntelV3Snapshot> = {}): IntelV3Snapshot {
  return makeSnapshot({
    snapshot_source: "worker_certified_with_gaps",
    certified_holding_count: 3,
    total_holding_count: 4,
    session_status: "completed_with_gaps",
    session_coverage: {
      frozen_holding_count: 4,
      decided_count: 3,
      no_call_count: 1,
      failed_count: 0,
      no_call_tickers: ["NVDA"],
      failed_tickers: [],
      gaps: [
        {
          ticker: "NVDA",
          state: "no_call",
          reason: "Not enough fresh evidence to make a call for NVDA.",
        },
      ],
    },
    ...overrides,
  } as Partial<IntelV3Snapshot>);
}

describe("deriveAdvisorReadiness — completed-with-gaps snapshot", () => {
  const NOW = new Date("2026-07-18T12:00:00Z");

  it("classifies as certified_with_gaps — never uncertified, never error, never Ready", () => {
    const model = deriveAdvisorReadiness(queryWith(makeWithGapsSnapshot()), NO_RUN, null, NOW);
    expect(model.snapshotState).toBe("certified_with_gaps");
    expect(model.ready).toBe(false);
    expect(model.statusPillLabel).toBe("Partly Ready");
    expect(model.statusPillLabel).not.toBe("Ready");
    expect(model.statusPillLabel).not.toBe("Blocked");
    expect(model.statusLine).toContain("current for 3 of 4 holdings");
  });

  it("intel certification row is amber pending — NOT the certification_failed blocked branch", () => {
    const rows = deriveTruthRows(makeWithGapsSnapshot(), "certified_with_gaps", null);
    const certRow = rows.find((r) => r.key === "intel_certification");
    expect(certRow?.status).toBe("pending");
    expect(certRow?.status).not.toBe("blocked");
    expect(certRow?.detail).toContain("3 of 4 holdings");
    expect(certRow?.detail).not.toContain("failed certification");
  });

  it("with-gaps is distinct from certification_failed (which stays blocked)", () => {
    const failedRows = deriveTruthRows(
      makeSnapshot({ snapshot_source: "certification_failed" } as Partial<IntelV3Snapshot>),
      "uncertified",
      null,
    );
    expect(failedRows.find((r) => r.key === "intel_certification")?.status).toBe("blocked");
  });

  it("never renders the raw worker_certified_with_gaps enum anywhere user-visible", () => {
    const model = deriveAdvisorReadiness(queryWith(makeWithGapsSnapshot()), NO_RUN, null, NOW);
    const visible = [
      model.statusPillLabel,
      model.statusLine,
      model.run.nextActionSentence,
      model.evidenceFreshnessLabel ?? "",
      ...model.truthRows.flatMap((r) => [r.label, r.detail]),
    ];
    for (const text of visible) {
      expect(text).not.toContain("worker_certified_with_gaps");
      expect(text.toLowerCase()).not.toContain("worker certified with gaps");
    }
  });

  it("clean certified snapshot behavior is unchanged (green Ready)", () => {
    const model = deriveAdvisorReadiness(queryWith(makeSnapshot()), NO_RUN, null, NOW);
    expect(model.snapshotState).toBe("certified");
    expect(model.ready).toBe(true);
    expect(model.statusPillLabel).toBe("Ready");
  });

  it("a stale with-gaps snapshot still reads stale, not certified_with_gaps", () => {
    const model = deriveAdvisorReadiness(
      queryWith(makeWithGapsSnapshot({ is_stale: true })),
      NO_RUN,
      null,
      NOW,
    );
    expect(model.snapshotState).toBe("stale");
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
