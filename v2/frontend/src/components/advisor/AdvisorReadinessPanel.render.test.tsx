/**
 * @jest-environment jsdom
 *
 * Render-level single-controller contract for AdvisorReadinessPanel.
 *
 * The panel must render EXACTLY ONE control wired to onRun in every run
 * state (idle → "Run Intel", failed → "Retry Intel run"; running shows a
 * disabled "Running…" control), and one click must invoke onRun exactly
 * once. These are real DOM renders with real click dispatch — not source
 * inspection.
 */

import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot, Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AdvisorReadinessPanel } from "./AdvisorReadinessPanel";
import { deriveAdvisorReadiness, type AdvisorRunInput } from "@/lib/advisor-readiness";
import type { IntelV3SessionStatus, IntelV3Snapshot, IntelV3HeldCard } from "@/lib/api";

// React 18 act() environment flag
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function makeCard(ticker: string, action: IntelV3HeldCard["action"]): IntelV3HeldCard {
  return { ticker, action, why_text: `${ticker} rationale` } as IntelV3HeldCard;
}

function makeSnapshot(): IntelV3Snapshot {
  return {
    schema_version: "v3",
    snapshot_id: "snap_1",
    run_id: "run_1",
    generated_at: new Date().toISOString(),
    is_stale: false,
    source_health: { status: "ok" },
    action_counts: { BUY: 1, HOLD: 1, TRIM: 0, SELL: 0 },
    best_buys: [],
    trim_sell_desk: [],
    current_holdings: [makeCard("VTI", "BUY"), makeCard("AAPL", "HOLD")],
    what_changed: [],
    warnings: [],
    legacy_path_used: false,
    snapshot_source: "worker_certified",
    certified_holding_count: 2,
    total_holding_count: 2,
    evidence_freshness_state: "certified_current",
  } as unknown as IntelV3Snapshot;
}

function makeSessionStatus(
  overrides: Partial<IntelV3SessionStatus> = {},
): IntelV3SessionStatus {
  return {
    run_session_id: "3f0a26aa-8f5c-4a2e-9d7b-2f8f0a1b2c3d",
    session_status: "running",
    workflow_version: 2,
    current_stage: "collecting_evidence",
    total_tickers: 2,
    evidence_complete_tickers: 1,
    analysis_complete_tickers: 0,
    decision_complete_tickers: 0,
    decided_tickers: 0,
    failed_or_degraded_tickers: 0,
    completed_snapshot_id: null,
    plain_status: "Gathering evidence — 1 of 2 holdings",
    retryable: true,
    terminal: false,
    ...overrides,
  };
}

const IDLE_RUN = { isRunPending: false, isRunError: false, lastRunResult: null };
const RUNNING_RUN = {
  isRunPending: true,
  isRunError: false,
  lastRunResult: makeSessionStatus(),
};
const FAILED_RUN = {
  isRunPending: false,
  isRunError: false,
  lastRunResult: makeSessionStatus({
    session_status: "failed",
    plain_status: "This run could not finish. You can start a new run.",
    terminal: true,
    retryable: false,
  }),
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function renderPanel(
  runInput: AdvisorRunInput,
  lastRunResult: IntelV3SessionStatus | null,
  onRun: () => void,
) {
  const model = deriveAdvisorReadiness(
    { snapshot: makeSnapshot(), isLoading: false, isError: false },
    runInput,
    null,
  );
  const client = new QueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <AdvisorReadinessPanel model={model} onRun={onRun} lastRunResult={lastRunResult} />
      </QueryClientProvider>,
    );
  });
  return model;
}

/** All buttons that are wired to onRun — identified by clicking each one. */
function clickableRunControls(onRunCalls: { count: number }): HTMLButtonElement[] {
  const buttons = Array.from(container.querySelectorAll("button"));
  return buttons.filter((btn) => {
    const before = onRunCalls.count;
    act(() => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    return onRunCalls.count > before;
  });
}

describe("AdvisorReadinessPanel — exactly one Run Intel control per state", () => {
  it("idle state renders one control labeled Run Intel", () => {
    const calls = { count: 0 };
    const model = renderPanel(IDLE_RUN, null, () => { calls.count += 1; });
    expect(model.run.state).toBe("idle");
    const controls = clickableRunControls(calls);
    expect(controls).toHaveLength(1);
    expect(controls[0].textContent).toContain("Run Intel");
  });

  it("running state renders one disabled Running… control and the plain-English status", () => {
    const calls = { count: 0 };
    const model = renderPanel(RUNNING_RUN, RUNNING_RUN.lastRunResult, () => { calls.count += 1; });
    expect(model.run.state).toBe("running");
    // The single run control is disabled while the session executes.
    const buttons = Array.from(container.querySelectorAll("button"));
    const runButton = buttons.find((b) => (b.textContent ?? "").includes("Running…"))!;
    expect(runButton).toBeDefined();
    expect(runButton.disabled).toBe(true);
    // Backend plain_status is rendered verbatim; no raw internals appear.
    expect(container.textContent).toContain("Gathering evidence — 1 of 2 holdings");
    expect(container.textContent).not.toContain("collecting_evidence");
    expect(container.textContent).not.toContain("task_counts");
  });

  it("failed state renders one control labeled Retry Intel run (no second retry button)", () => {
    const calls = { count: 0 };
    const model = renderPanel(FAILED_RUN, FAILED_RUN.lastRunResult, () => { calls.count += 1; });
    expect(model.run.state).toBe("failed");
    const controls = clickableRunControls(calls);
    expect(controls).toHaveLength(1);
    expect(controls[0].textContent).toContain("Retry Intel run");
    // The failed-state explanation copy survives without a second control.
    expect(container.textContent).toContain(model.run.nextActionSentence);
  });

  it("one click invokes onRun exactly once", () => {
    const calls = { count: 0 };
    renderPanel(IDLE_RUN, null, () => { calls.count += 1; });
    const btn = Array.from(container.querySelectorAll("button")).find((b) =>
      (b.textContent ?? "").includes("Run Intel"),
    )!;
    expect(btn).toBeDefined();
    act(() => {
      btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(calls.count).toBe(1);
  });
});

// ── worker_certified_with_gaps — amber caveated render, never a raw enum ──────

function makeWithGapsSnapshot(): IntelV3Snapshot {
  return {
    ...makeSnapshot(),
    snapshot_source: "worker_certified_with_gaps",
    certified_holding_count: 1,
    total_holding_count: 2,
    session_status: "completed_with_gaps",
    session_coverage: {
      frozen_holding_count: 2,
      decided_count: 1,
      no_call_count: 1,
      failed_count: 0,
      no_call_tickers: ["AAPL"],
      failed_tickers: [],
      gaps: [
        {
          ticker: "AAPL",
          state: "no_call",
          reason: "Not enough fresh evidence to make a call for AAPL.",
        },
      ],
    },
  } as IntelV3Snapshot;
}

describe("AdvisorReadinessPanel — completed-with-gaps snapshot", () => {
  it("renders the amber Partly Ready state with plain-English copy and no raw enum", () => {
    const model = deriveAdvisorReadiness(
      { snapshot: makeWithGapsSnapshot(), isLoading: false, isError: false },
      IDLE_RUN,
      null,
    );
    const client = new QueryClient();
    act(() => {
      root.render(
        <QueryClientProvider client={client}>
          <AdvisorReadinessPanel model={model} onRun={() => {}} lastRunResult={null} />
        </QueryClientProvider>,
      );
    });

    expect(model.snapshotState).toBe("certified_with_gaps");
    const text = container.textContent ?? "";
    // Amber caveated pill — not the green Intel Ready pill, not Blocked.
    expect(text).toContain("Partly Ready");
    expect(text).not.toContain("Intel Ready");
    expect(text).toContain("current for 1 of 2 holdings");
    // The raw enum value must never render to the user.
    expect(text).not.toContain("worker_certified_with_gaps");
    expect(text.toLowerCase()).not.toContain("worker certified with gaps");
    // Amber pill styling (same tone family as Updating), not green/red.
    const pill = Array.from(container.querySelectorAll("span")).find((s) =>
      (s.textContent ?? "").trim() === "Partly Ready",
    )!;
    expect(pill).toBeDefined();
    expect(pill.className).toContain("text-action-trim");
    expect(pill.className).not.toContain("text-action-buy");
    expect(pill.className).not.toContain("text-action-sell");
  });
});

describe("AdvisorReadinessPanel — evidence reuse/refresh summary line", () => {
  it("renders the compact lanes/specialist reuse line on a completed run", () => {
    const completedRun = {
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "completed",
        plain_status: "Completed — your recommendations are up to date.",
        terminal: true,
        retryable: false,
        completed_snapshot_id: "snap_2",
        evidence_summary_line: "Evidence: 80 lanes reused, 13 refreshed. Specialist analysis: 70 reused, 23 refreshed.",
      }),
    };
    const model = renderPanel(completedRun, completedRun.lastRunResult, () => {});
    expect(model.run.evidenceSummaryLine).toContain("lanes reused");
    const text = container.textContent ?? "";
    expect(text).toContain("80 lanes reused, 13 refreshed");
    expect(text).toContain("70 reused, 23 refreshed");
  });

  it("omits the line when the backend reports no metrics — never a zero placeholder", () => {
    const completedRun = {
      isRunPending: false,
      isRunError: false,
      lastRunResult: makeSessionStatus({
        session_status: "completed",
        plain_status: "Completed — your recommendations are up to date.",
        terminal: true,
        retryable: false,
        completed_snapshot_id: "snap_3",
      }),
    };
    const model = renderPanel(completedRun, completedRun.lastRunResult, () => {});
    expect(model.run.evidenceSummaryLine).toBeNull();
    const text = container.textContent ?? "";
    expect(text).not.toContain("lanes reused");
  });
});

// ── Fail-closed "unknown" trust overlay — truthful render, release-blocker ───

function makeUnknownOverlaySnapshot(): IntelV3Snapshot {
  const reason = "Session row could not be found — trust status could not be re-verified.";
  return {
    ...makeSnapshot(),
    source_health: { status: "unknown", reason },
    run_trust_contract: {
      schema_version: "run_trust_contract_v1",
      run_session_id: "sess-unknown",
      generated_at: new Date().toISOString(),
      overall_status: "unknown",
      session_coverage: {
        frozen_holding_count: 0, decided_count: 0, no_call_count: 0,
        failed_count: 0, unaccounted_count: 0, publication_complete: false,
      },
      axis_coverage: {},
      conflict_review_coverage: {
        required_count: 0, succeeded_count: 0, failed_count: 0, pending_count: 0,
        required_tickers: [], succeeded_tickers: [], failed_tickers: [], pending_tickers: [],
      },
      source_lineage: {
        outputs_with_source_refs: 0, outputs_missing_source_refs: 0,
        tickers_with_lineage: [], tickers_missing_lineage: [],
        tickers_full_lineage: [], tickers_partial_lineage: [], tickers_missing_lineage_full: [],
      },
      source_health: { status: "unknown", reason },
      ticker_trust: [],
      blocking_reasons: [reason],
      warnings: [reason],
    },
  } as unknown as IntelV3Snapshot;
}

describe("AdvisorReadinessPanel — fail-closed unknown trust overlay renders truthfully", () => {
  it("renders 'Analysis trust: Unknown' and every line as 'could not be re-verified', no placeholder-derived claims", () => {
    const model = deriveAdvisorReadiness(
      { snapshot: makeUnknownOverlaySnapshot(), isLoading: false, isError: false },
      IDLE_RUN,
      null,
    );
    const client = new QueryClient();
    act(() => {
      root.render(
        <QueryClientProvider client={client}>
          <AdvisorReadinessPanel model={model} onRun={() => {}} lastRunResult={null} />
        </QueryClientProvider>,
      );
    });

    const text = container.textContent ?? "";
    expect(text).toContain("Analysis trust: Unknown");
    expect(text).toContain("Session coverage could not be re-verified");
    expect(text).toContain("Specialist-axis coverage could not be re-verified");
    expect(text).toContain("Conflict-review coverage could not be re-verified");
    expect(text).toContain("Source lineage could not be re-verified");
    // Release-blocker requirement: none of these false, placeholder-derived
    // claims may ever render for a fail-closed unknown overlay.
    expect(text).not.toContain("0 of 0");
    expect(text).not.toContain("No conflict reviews were required");
    expect(text).not.toContain("No specialist axes applied");
    expect(text).not.toContain("no specialist outputs recorded");
    // Source health row uses the backend's plain-English reason, not the
    // old hardcoded "no specialist outputs were recorded" guess.
    expect(text).toContain("Session row could not be found");
    expect(text).not.toContain("no specialist outputs were recorded");
  });
});
