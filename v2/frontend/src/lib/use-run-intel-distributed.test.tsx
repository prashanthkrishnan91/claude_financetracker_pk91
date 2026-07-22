/**
 * @jest-environment jsdom
 *
 * Real render/hook contract for useRunIntelV3's distributed run workflow.
 *
 * The backend now executes one durable Run Intel session on its own; the
 * browser only creates the session and observes it. This file proves:
 *   (a) no run/poll request exists before the explicit click;
 *   (b) one click sends exactly ONE POST /intel/v3/run with a UUID body;
 *   (c) polling calls getSessionStatus with the SAME session id and NEVER
 *       re-POSTs /run;
 *   (d) polling stops on a terminal status, and completion invalidates the
 *       ["intel_v3","snapshot"] query;
 *   (e) unmount stops polling (no further getSessionStatus calls);
 *   (f) on mount with an active backend session, getActiveSession recovery
 *       resumes polling that session id (still no POST);
 *   (g) a new explicit click after a terminal run mints a NEW uuid;
 *   plus: backend adoption of an active session is polled, not errored, and
 *   a failed create restores the Retry state.
 *
 * Uses real DOM mount/unmount (react-dom/client + act) with jest fake
 * timers — same pattern as AdvisorReadinessPanel.render.test.tsx.
 */
import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot, Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
  useRunIntelV3,
  RUN_INTEL_POLL_INTERVAL_MS,
  type UseRunIntelV3Result,
} from "./hooks";
import { api } from "./api";
import type { IntelV3SessionStatus } from "./api";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("./api", () => ({
  api: {
    intelV3: {
      runV3: jest.fn(),
      getSessionStatus: jest.fn(),
      getActiveSession: jest.fn(),
    },
  },
}));

const runV3 = api.intelV3.runV3 as jest.Mock;
const getSessionStatus = api.intelV3.getSessionStatus as jest.Mock;
const getActiveSession = api.intelV3.getActiveSession as jest.Mock;

// jsdom in some Node versions lacks crypto.randomUUID — the hook requires the
// browser UUID API, so polyfill it from node:crypto for the test environment.
beforeAll(() => {
  const g = globalThis as Record<string, any>;
  if (!g.crypto) g.crypto = {};
  if (typeof g.crypto.randomUUID !== "function") {
    const { randomUUID } = require("crypto");
    g.crypto.randomUUID = randomUUID;
  }
});

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Session ids the create endpoint received, in call order. */
function postedSessionIds(): string[] {
  return runV3.mock.calls.map((c) => c[0] as string);
}

/** Session ids the status endpoint received, in call order. */
function polledSessionIds(): string[] {
  return getSessionStatus.mock.calls.map((c) => c[0] as string);
}

async function flush(ticks = 40): Promise<void> {
  for (let i = 0; i < ticks; i++) {
    await Promise.resolve();
  }
}

/** Advance fake time by one poll interval and settle async work. */
async function advanceOnePoll(): Promise<void> {
  await act(async () => {
    jest.advanceTimersByTime(RUN_INTEL_POLL_INTERVAL_MS);
    await flush();
  });
}

function runningStatus(
  sessionId: string,
  overrides: Partial<IntelV3SessionStatus> = {},
): IntelV3SessionStatus {
  return {
    run_session_id: sessionId,
    session_status: "running",
    workflow_version: 2,
    current_stage: "collecting_evidence",
    total_tickers: 4,
    evidence_complete_tickers: 1,
    analysis_complete_tickers: 0,
    decision_complete_tickers: 0,
    decided_tickers: 0,
    failed_or_degraded_tickers: 0,
    task_counts: {},
    completed_snapshot_id: null,
    plain_status: "Gathering evidence — 1 of 4 holdings",
    retryable: true,
    terminal: false,
    ...overrides,
  };
}

function completedStatus(
  sessionId: string,
  overrides: Partial<IntelV3SessionStatus> = {},
): IntelV3SessionStatus {
  return runningStatus(sessionId, {
    session_status: "completed",
    current_stage: "done",
    decision_complete_tickers: 4,
    decided_tickers: 4,
    completed_snapshot_id: "snap-published-1",
    plain_status: "Completed — your recommendations are up to date.",
    retryable: false,
    terminal: true,
    ...overrides,
  });
}

let latestHook: UseRunIntelV3Result | null = null;

function Harness() {
  latestHook = useRunIntelV3();
  return null;
}

function mountHarness(): {
  root: Root;
  container: HTMLDivElement;
  qc: QueryClient;
  invalidateSpy: jest.SpyInstance;
} {
  const qc = new QueryClient();
  const invalidateSpy = jest.spyOn(qc, "invalidateQueries");
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <Harness />
      </QueryClientProvider>,
    );
  });
  return { root, container, qc, invalidateSpy };
}

/** Flush the on-mount active-session recovery request. */
async function settleMount(): Promise<void> {
  await act(async () => {
    await flush();
  });
}

function snapshotInvalidations(invalidateSpy: jest.SpyInstance): number {
  return invalidateSpy.mock.calls.filter(
    (c) => JSON.stringify(c[0]?.queryKey) === JSON.stringify(["intel_v3", "snapshot"]),
  ).length;
}

beforeEach(() => {
  jest.useFakeTimers();
  runV3.mockReset();
  getSessionStatus.mockReset();
  getActiveSession.mockReset();
  getActiveSession.mockResolvedValue({ active: false });
  latestHook = null;
});

afterEach(() => {
  jest.useRealTimers();
});

describe("useRunIntelV3 — distributed run session (create + poll)", () => {
  it("(a) sends no run or poll request before the explicit click", async () => {
    const { root, container } = mountHarness();
    await settleMount();
    await advanceOnePoll();

    expect(runV3).not.toHaveBeenCalled();
    expect(getSessionStatus).not.toHaveBeenCalled();

    act(() => root.unmount());
    container.remove();
  });

  it("(b) one click sends exactly ONE POST /run with a browser-minted UUID", async () => {
    runV3.mockImplementation((id: string) => Promise.resolve(runningStatus(id)));
    getSessionStatus.mockImplementation((id: string) =>
      Promise.resolve(completedStatus(id)),
    );

    const { root, container } = mountHarness();
    await settleMount();

    await act(async () => {
      latestHook!.mutate();
      await flush();
    });

    const ids = postedSessionIds();
    expect(ids).toHaveLength(1);
    expect(ids[0]).toMatch(UUID_RE);
    expect(latestHook!.isPending).toBe(true);

    act(() => root.unmount());
    container.remove();
  });

  it("(c) polls getSessionStatus with the SAME session id and never re-POSTs /run", async () => {
    runV3.mockImplementation((id: string) => Promise.resolve(runningStatus(id)));
    getSessionStatus
      .mockImplementationOnce((id: string) => Promise.resolve(runningStatus(id)))
      .mockImplementationOnce((id: string) =>
        Promise.resolve(
          runningStatus(id, {
            current_stage: "specialist_analysis",
            plain_status: "Specialist analysis — 2 of 4 holdings",
          }),
        ),
      )
      .mockImplementation((id: string) => Promise.resolve(completedStatus(id)));

    const { root, container } = mountHarness();
    await settleMount();

    await act(async () => {
      latestHook!.mutate();
      await flush();
    });
    const sessionId = postedSessionIds()[0];

    await advanceOnePoll(); // running
    await advanceOnePoll(); // running (analysis)
    await advanceOnePoll(); // completed

    expect(getSessionStatus).toHaveBeenCalledTimes(3);
    expect(new Set(polledSessionIds())).toEqual(new Set([sessionId]));
    // The mission-critical invariant: never a second POST, no matter what.
    expect(runV3).toHaveBeenCalledTimes(1);

    act(() => root.unmount());
    container.remove();
  });

  it("(d) stops polling on terminal status and invalidates the snapshot query on completion", async () => {
    runV3.mockImplementation((id: string) => Promise.resolve(runningStatus(id)));
    getSessionStatus.mockImplementation((id: string) =>
      Promise.resolve(completedStatus(id)),
    );

    const { root, container, invalidateSpy } = mountHarness();
    await settleMount();

    await act(async () => {
      latestHook!.mutate();
      await flush();
    });
    await advanceOnePoll(); // terminal completed

    expect(latestHook!.isPending).toBe(false);
    expect(latestHook!.data?.session_status).toBe("completed");
    expect(snapshotInvalidations(invalidateSpy)).toBe(1);

    // No further polling after terminal — even after a long quiet stretch.
    const callsAtTerminal = getSessionStatus.mock.calls.length;
    await advanceOnePoll();
    await advanceOnePoll();
    expect(getSessionStatus).toHaveBeenCalledTimes(callsAtTerminal);
    expect(runV3).toHaveBeenCalledTimes(1);

    act(() => root.unmount());
    container.remove();
  });

  it("(d2) completed_with_gaps also stops polling and invalidates the snapshot query", async () => {
    runV3.mockImplementation((id: string) => Promise.resolve(runningStatus(id)));
    getSessionStatus.mockImplementation((id: string) =>
      Promise.resolve(
        completedStatus(id, {
          session_status: "completed_with_gaps",
          failed_or_degraded_tickers: 1,
          plain_status:
            "Completed with gaps — some holdings had limited evidence this run.",
        }),
      ),
    );

    const { root, container, invalidateSpy } = mountHarness();
    await settleMount();

    await act(async () => {
      latestHook!.mutate();
      await flush();
    });
    await advanceOnePoll();

    expect(latestHook!.isPending).toBe(false);
    expect(latestHook!.data?.session_status).toBe("completed_with_gaps");
    expect(snapshotInvalidations(invalidateSpy)).toBe(1);

    act(() => root.unmount());
    container.remove();
  });

  it("(e) unmount stops polling but never aborts the backend run", async () => {
    runV3.mockImplementation((id: string) => Promise.resolve(runningStatus(id)));
    getSessionStatus.mockImplementation((id: string) =>
      Promise.resolve(runningStatus(id)),
    );

    const { root, container } = mountHarness();
    await settleMount();

    await act(async () => {
      latestHook!.mutate();
      await flush();
    });
    await advanceOnePoll();
    expect(getSessionStatus).toHaveBeenCalledTimes(1);

    act(() => root.unmount());

    // Time passes after unmount — no further status polls may fire.
    await act(async () => {
      jest.advanceTimersByTime(RUN_INTEL_POLL_INTERVAL_MS * 5);
      await flush();
    });
    expect(getSessionStatus).toHaveBeenCalledTimes(1);
    expect(runV3).toHaveBeenCalledTimes(1);

    container.remove();
  });

  it("(f) on mount with an active backend session, recovery resumes polling that id without POSTing", async () => {
    const recoveredId = "11111111-2222-4333-8444-555555555555";
    getActiveSession.mockResolvedValue({
      active: true,
      ...runningStatus(recoveredId),
    });
    getSessionStatus.mockImplementation((id: string) =>
      Promise.resolve(completedStatus(id)),
    );

    const { root, container, invalidateSpy } = mountHarness();
    await settleMount();

    // Recovery adopted the live session: pending, with its status as data.
    expect(latestHook!.isPending).toBe(true);
    expect(latestHook!.data?.run_session_id).toBe(recoveredId);

    await advanceOnePoll();
    expect(polledSessionIds()).toEqual([recoveredId]);
    expect(latestHook!.isPending).toBe(false);
    expect(snapshotInvalidations(invalidateSpy)).toBe(1);
    // Rediscovery never creates a session.
    expect(runV3).not.toHaveBeenCalled();

    act(() => root.unmount());
    container.remove();
  });

  it("(g) a new explicit click after a terminal run mints a NEW uuid", async () => {
    runV3.mockImplementation((id: string) => Promise.resolve(completedStatus(id)));

    const { root, container } = mountHarness();
    await settleMount();

    await act(async () => {
      latestHook!.mutate();
      await flush();
    });
    expect(latestHook!.isPending).toBe(false);

    await act(async () => {
      latestHook!.mutate();
      await flush();
    });

    const ids = postedSessionIds();
    expect(ids).toHaveLength(2);
    expect(ids[0]).toMatch(UUID_RE);
    expect(ids[1]).toMatch(UUID_RE);
    expect(ids[0]).not.toBe(ids[1]);

    act(() => root.unmount());
    container.remove();
  });

  it("a click while a session is active polls the ADOPTED session id (no error, no second POST)", async () => {
    const serverActiveId = "99999999-8888-4777-8666-555555555544";
    runV3.mockImplementation(() =>
      Promise.resolve(
        runningStatus(serverActiveId, { adopted_active_session: true }),
      ),
    );
    getSessionStatus.mockImplementation((id: string) =>
      Promise.resolve(completedStatus(id)),
    );

    const { root, container } = mountHarness();
    await settleMount();

    await act(async () => {
      latestHook!.mutate();
      await flush();
    });
    await advanceOnePoll();

    // Polling follows the backend's session id, not the freshly minted uuid.
    expect(polledSessionIds()).toEqual([serverActiveId]);
    expect(latestHook!.isError).toBe(false);
    expect(runV3).toHaveBeenCalledTimes(1);

    act(() => root.unmount());
    container.remove();
  });

  it("a failed create restores the Retry state and starts no polling", async () => {
    runV3.mockRejectedValueOnce(new Error("401 Unauthorized"));

    const { root, container } = mountHarness();
    await settleMount();

    await act(async () => {
      latestHook!.mutate();
      await flush();
    });

    expect(latestHook!.isError).toBe(true);
    expect(latestHook!.isPending).toBe(false);
    await advanceOnePoll();
    expect(getSessionStatus).not.toHaveBeenCalled();

    // The control stays usable: a retry click works with a fresh uuid.
    runV3.mockImplementation((id: string) => Promise.resolve(completedStatus(id)));
    await act(async () => {
      latestHook!.mutate();
      await flush();
    });
    expect(latestHook!.isError).toBe(false);
    const ids = postedSessionIds();
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);

    act(() => root.unmount());
    container.remove();
  });
});
