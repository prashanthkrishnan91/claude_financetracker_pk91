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
