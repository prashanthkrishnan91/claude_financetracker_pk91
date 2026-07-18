/**
 * @jest-environment jsdom
 *
 * Render-level single-controller contract for AdvisorReadinessPanel.
 *
 * The panel must render EXACTLY ONE control wired to onRun in every run
 * state (idle → "Run Intel", partial → "Continue Intel run", failed →
 * "Retry Intel run"), and one click must invoke onRun exactly once. These
 * are real DOM renders with real click dispatch — not source inspection.
 */

import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot, Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AdvisorReadinessPanel } from "./AdvisorReadinessPanel";
import { deriveAdvisorReadiness, type AdvisorRunInput } from "@/lib/advisor-readiness";
import type { IntelV3RunResult, IntelV3Snapshot, IntelV3HeldCard } from "@/lib/api";

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
  } as IntelV3RunResult;
}

const IDLE_RUN = { isRunPending: false, isRunError: false, lastRunResult: null };
const PARTIAL_RUN = {
  isRunPending: false,
  isRunError: false,
  lastRunResult: makeRunResult({
    queued_ticker_count: 6,
    on_demand_jobs_attempted: 4,
    on_demand_jobs_succeeded: 4,
    on_demand_jobs_failed: 0,
    snapshot_available_after_run: false,
    next_required_action:
      "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining",
  }),
};
const FAILED_RUN = {
  isRunPending: false,
  isRunError: false,
  lastRunResult: makeRunResult({
    queued_ticker_count: 4,
    on_demand_jobs_attempted: 4,
    on_demand_jobs_succeeded: 0,
    on_demand_jobs_failed: 4,
    snapshot_available_after_run: false,
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
  lastRunResult: IntelV3RunResult | null,
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

  it("partial state renders one control labeled Continue Intel run", () => {
    const calls = { count: 0 };
    const model = renderPanel(PARTIAL_RUN, PARTIAL_RUN.lastRunResult, () => { calls.count += 1; });
    expect(model.run.state).toBe("partial");
    const controls = clickableRunControls(calls);
    expect(controls).toHaveLength(1);
    expect(controls[0].textContent).toContain("Continue Intel run");
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
