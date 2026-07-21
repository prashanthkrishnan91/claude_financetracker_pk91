/**
 * @jest-environment jsdom
 *
 * Real render/hook contract for useRunIntelV3's bounded automatic
 * continuation (Part A3 — Run Intel product recovery).
 *
 * One button click must:
 *   1. send the initial request;
 *   2. automatically send bounded continuation requests while resumable;
 *   3. stop on certified completion;
 *   4. never require another user click;
 *   5. abort on unmount;
 *   6. stop at the continuation-attempt/time cap;
 *   7. restore Retry state after a terminal or network failure;
 *   8. never start polling before the explicit click.
 *
 * Durable-session contract (fix/run-intel-durable-sessions):
 *   9. a manual click mints ONE browser UUID (crypto.randomUUID);
 *  10. the first request sends it, and EVERY automatic continuation sends
 *      the exact same id;
 *  11. a second manual click always sends a DIFFERENT id;
 *  12. no request (and therefore no session id) exists before the click.
 *
 * Uses real DOM mount/unmount (react-dom/client + act), same pattern as
 * AdvisorReadinessPanel.render.test.tsx — no @testing-library/react in this
 * project's devDependencies.
 */
import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot, Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useRunIntelV3, type UseRunIntelV3Result } from "./hooks";
import { api } from "./api";
import { RUN_INTEL_MAX_CONTINUATIONS } from "./advisor-readiness";
import type { IntelV3RunResult } from "./api";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("./api", () => ({
  api: {
    intelV3: {
      runV3: jest.fn(),
    },
  },
}));

const runV3 = api.intelV3.runV3 as jest.Mock;

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

/** Session ids observed by the mocked endpoint, in call order. */
function sentSessionIds(): string[] {
  return runV3.mock.calls.map((c) => c[0] as string);
}

async function flush(ticks = 40): Promise<void> {
  for (let i = 0; i < ticks; i++) {
    await Promise.resolve();
  }
}

function partialResult(overrides: Partial<IntelV3RunResult> = {}): IntelV3RunResult {
  return {
    status: "refresh_requested",
    queued_ticker_count: 6,
    on_demand_processing_enabled: true,
    on_demand_jobs_attempted: 3,
    on_demand_jobs_succeeded: 3,
    on_demand_jobs_failed: 0,
    snapshot_available_after_run: false,
    next_required_action:
      "reclick_run_intel_or_run_worker_entrypoint_to_continue_draining",
    ...overrides,
  } as IntelV3RunResult;
}

function completeResult(overrides: Partial<IntelV3RunResult> = {}): IntelV3RunResult {
  return {
    status: "refresh_requested",
    queued_ticker_count: 3,
    on_demand_processing_enabled: true,
    on_demand_jobs_attempted: 3,
    on_demand_jobs_succeeded: 3,
    on_demand_jobs_failed: 0,
    snapshot_available_after_run: true,
    next_required_action: "none_certified_snapshot_current",
    ...overrides,
  } as IntelV3RunResult;
}

let latestHook: UseRunIntelV3Result | null = null;

function Harness() {
  latestHook = useRunIntelV3();
  return null;
}

function mountHarness(): { root: Root; container: HTMLDivElement } {
  const qc = new QueryClient();
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
  return { root, container };
}

beforeEach(() => {
  runV3.mockReset();
  latestHook = null;
});

describe("useRunIntelV3 — bounded automatic continuation", () => {
  it("never calls the endpoint before the explicit click", async () => {
    const { root, container } = mountHarness();
    await act(async () => {
      await flush(5);
    });
    expect(runV3).not.toHaveBeenCalled();
    act(() => root.unmount());
    container.remove();
  });

  it("one click sends the initial request and auto-continues while partial, stopping on completion", async () => {
    runV3
      .mockResolvedValueOnce(partialResult())
      .mockResolvedValueOnce(partialResult())
      .mockResolvedValueOnce(completeResult());

    const { root, container } = mountHarness();

    await act(async () => {
      latestHook!.mutate();
      await flush(60);
    });

    // Three requests from ONE click — no further clicks issued.
    expect(runV3).toHaveBeenCalledTimes(3);
    expect(latestHook!.data?.snapshot_available_after_run).toBe(true);
    expect(latestHook!.isPending).toBe(false);
    expect(latestHook!.isError).toBe(false);

    act(() => root.unmount());
    container.remove();
  });

  it("stops at the continuation-attempt cap even if every batch stays partial", async () => {
    runV3.mockImplementation(() => Promise.resolve(partialResult()));

    const { root, container } = mountHarness();
    await act(async () => {
      latestHook!.mutate();
      await flush(400);
    });

    expect(runV3).toHaveBeenCalledTimes(RUN_INTEL_MAX_CONTINUATIONS);
    // The cap must leave the control usable again, not stuck spinning.
    expect(latestHook!.isPending).toBe(false);

    act(() => root.unmount());
    container.remove();
  });

  it("restores Retry state after a network/request failure and stops continuing", async () => {
    runV3.mockRejectedValueOnce(new Error("401 Unauthorized"));

    const { root, container } = mountHarness();
    await act(async () => {
      latestHook!.mutate();
      await flush(20);
    });

    expect(runV3).toHaveBeenCalledTimes(1);
    expect(latestHook!.isError).toBe(true);
    expect(latestHook!.isPending).toBe(false);

    act(() => root.unmount());
    container.remove();
  });

  it("a later click after a failure works again (control stays usable)", async () => {
    runV3
      .mockRejectedValueOnce(new Error("network error"))
      .mockResolvedValueOnce(completeResult());

    const { root, container } = mountHarness();

    await act(async () => {
      latestHook!.mutate();
      await flush(20);
    });
    expect(latestHook!.isError).toBe(true);

    await act(async () => {
      latestHook!.mutate();
      await flush(20);
    });
    expect(latestHook!.isError).toBe(false);
    expect(latestHook!.data?.snapshot_available_after_run).toBe(true);

    act(() => root.unmount());
    container.remove();
  });

  it("aborts in-flight work on unmount and issues no further requests", async () => {
    let capturedSignal: AbortSignal | undefined;
    let resolveFirst: ((v: IntelV3RunResult) => void) | undefined;
    runV3.mockImplementationOnce((_sessionId: string, signal?: AbortSignal) => {
      capturedSignal = signal;
      return new Promise<IntelV3RunResult>((resolve) => {
        resolveFirst = resolve;
      });
    });

    const { root, container } = mountHarness();
    act(() => {
      latestHook!.mutate();
    });

    expect(runV3).toHaveBeenCalledTimes(1);
    expect(capturedSignal?.aborted).toBe(false);

    act(() => {
      root.unmount();
    });

    expect(capturedSignal?.aborted).toBe(true);

    // Resolving the in-flight promise after unmount must not trigger a
    // further continuation request or a React state-update-after-unmount.
    await act(async () => {
      resolveFirst?.(partialResult());
      await flush(20);
    });
    expect(runV3).toHaveBeenCalledTimes(1);

    container.remove();
  });
});

describe("useRunIntelV3 — durable run-session identity", () => {
  it("a manual click mints a browser UUID and the first request sends it", async () => {
    runV3.mockResolvedValueOnce(completeResult());

    const { root, container } = mountHarness();
    await act(async () => {
      latestHook!.mutate();
      await flush(20);
    });

    const ids = sentSessionIds();
    expect(ids).toHaveLength(1);
    expect(ids[0]).toMatch(UUID_RE);

    act(() => root.unmount());
    container.remove();
  });

  it("every automatic continuation of one click sends the SAME session id", async () => {
    runV3
      .mockResolvedValueOnce(partialResult())
      .mockResolvedValueOnce(partialResult())
      .mockResolvedValueOnce(partialResult())
      .mockResolvedValueOnce(completeResult());

    const { root, container } = mountHarness();
    await act(async () => {
      latestHook!.mutate();
      await flush(80);
    });

    const ids = sentSessionIds();
    expect(ids).toHaveLength(4);
    expect(new Set(ids).size).toBe(1);
    expect(ids[0]).toMatch(UUID_RE);

    act(() => root.unmount());
    container.remove();
  });

  it("a second manual click always sends a DIFFERENT session id", async () => {
    runV3.mockResolvedValue(completeResult());

    const { root, container } = mountHarness();
    await act(async () => {
      latestHook!.mutate();
      await flush(20);
    });
    await act(async () => {
      latestHook!.mutate();
      await flush(20);
    });

    const ids = sentSessionIds();
    expect(ids).toHaveLength(2);
    expect(ids[0]).toMatch(UUID_RE);
    expect(ids[1]).toMatch(UUID_RE);
    expect(ids[0]).not.toBe(ids[1]);

    act(() => root.unmount());
    container.remove();
  });

  it("a retry click after a failed run uses a fresh session id", async () => {
    runV3
      .mockRejectedValueOnce(new Error("network error"))
      .mockResolvedValueOnce(completeResult());

    const { root, container } = mountHarness();
    await act(async () => {
      latestHook!.mutate();
      await flush(20);
    });
    await act(async () => {
      latestHook!.mutate();
      await flush(20);
    });

    const ids = sentSessionIds();
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);

    act(() => root.unmount());
    container.remove();
  });
});
