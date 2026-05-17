import {
  toRomanNumeral,
  buildJournalEntries,
  buildEvaluationState,
  buildSourceLabel,
  buildStatusLabel,
  computeCashDeployed,
  JOURNAL_LESSONS_CAPTION,
  JOURNAL_WHAT_I_LEARNED_CAPTION,
} from "./journal-ledger";
import type { DecisionMemoryLog } from "./api";

function makeLog(overrides: Partial<DecisionMemoryLog> = {}): DecisionMemoryLog {
  return {
    id: "log-1",
    user_id: "u1",
    source: "deploy_v3",
    status: "FULLY_EXECUTED",
    recommendation_snapshot: { source: "deploy_v3" },
    actual_decisions: [
      { ticker: "AAPL", actual_action: "BOUGHT", actual_amount: 1000, recommended_action: "BUY", recommended_amount: 1000, is_manual: false },
    ],
    performance_snapshot: null,
    notes: null,
    created_at: "2026-05-10T12:00:00.000Z",
    updated_at: "2026-05-10T12:00:00.000Z",
    ...overrides,
  };
}

// ── toRomanNumeral ────────────────────────────────────────────────────────────

describe("toRomanNumeral", () => {
  it("converts 1 to I", () => expect(toRomanNumeral(1)).toBe("I"));
  it("converts 2 to II", () => expect(toRomanNumeral(2)).toBe("II"));
  it("converts 3 to III", () => expect(toRomanNumeral(3)).toBe("III"));
  it("converts 4 to IV", () => expect(toRomanNumeral(4)).toBe("IV"));
  it("converts 5 to V", () => expect(toRomanNumeral(5)).toBe("V"));
  it("converts 9 to IX", () => expect(toRomanNumeral(9)).toBe("IX"));
  it("converts 10 to X", () => expect(toRomanNumeral(10)).toBe("X"));
  it("converts 14 to XIV", () => expect(toRomanNumeral(14)).toBe("XIV"));
  it("converts 40 to XL", () => expect(toRomanNumeral(40)).toBe("XL"));
  it("fallback to I for 0 or negative", () => {
    expect(toRomanNumeral(0)).toBe("I");
    expect(toRomanNumeral(-1)).toBe("I");
  });
});

// ── buildEvaluationState ──────────────────────────────────────────────────────

describe("buildEvaluationState — no performance_snapshot", () => {
  it("returns pending when performance_snapshot is null", () => {
    const log = makeLog({ performance_snapshot: null });
    const state = buildEvaluationState(log);
    expect(state.kind).toBe("pending");
    expect(state.label.toLowerCase()).toMatch(/pending/);
    expect(state.detail.length).toBeGreaterThan(10);
  });

  it("pending detail does not fabricate outcomes", () => {
    const log = makeLog({ performance_snapshot: null });
    const state = buildEvaluationState(log);
    expect(state.detail.toLowerCase()).not.toMatch(/gained|lost|profit|return|percent/);
  });
});

describe("buildEvaluationState — window_open states", () => {
  it("returns window_open for baseline_captured", () => {
    const log = makeLog({
      performance_snapshot: {
        status: "baseline_captured",
        evaluated_at: new Date().toISOString(),
        portfolio: { recommended_return: 0, actual_return: 0, delta: 0 },
        per_ticker: [],
      },
    });
    const state = buildEvaluationState(log);
    expect(state.kind).toBe("window_open");
  });

  it("returns window_open for pending status", () => {
    const log = makeLog({
      performance_snapshot: {
        status: "pending",
        evaluated_at: new Date().toISOString(),
        portfolio: { recommended_return: 0, actual_return: 0, delta: 0 },
        per_ticker: [],
      },
    });
    const state = buildEvaluationState(log);
    expect(state.kind).toBe("window_open");
  });
});

describe("buildEvaluationState — ready", () => {
  it("returns ready for ready status", () => {
    const log = makeLog({
      performance_snapshot: {
        status: "ready",
        evaluated_at: new Date().toISOString(),
        portfolio: { recommended_return: 5, actual_return: 4, delta: -1 },
        per_ticker: [],
      },
    });
    const state = buildEvaluationState(log);
    expect(state.kind).toBe("ready");
  });
});

describe("buildEvaluationState — unavailable states", () => {
  it("returns unavailable for insufficient_data", () => {
    const log = makeLog({
      performance_snapshot: {
        status: "insufficient_data",
        evaluated_at: new Date().toISOString(),
        portfolio: { recommended_return: 0, actual_return: 0, delta: 0 },
        per_ticker: [],
      },
    });
    const state = buildEvaluationState(log);
    expect(state.kind).toBe("unavailable");
  });

  it("returns unavailable for missing_price", () => {
    const log = makeLog({
      performance_snapshot: {
        status: "missing_price",
        evaluated_at: new Date().toISOString(),
        portfolio: { recommended_return: 0, actual_return: 0, delta: 0 },
        per_ticker: [],
      },
    });
    const state = buildEvaluationState(log);
    expect(state.kind).toBe("unavailable");
  });
});

// ── buildSourceLabel ──────────────────────────────────────────────────────────

describe("buildSourceLabel", () => {
  it("maps deploy_v3 to Deploy v3", () => {
    expect(buildSourceLabel("deploy_v3")).toBe("Deploy v3");
  });
  it("maps deploy to Deploy", () => {
    expect(buildSourceLabel("deploy")).toBe("Deploy");
  });
  it("passes through unknown sources", () => {
    expect(buildSourceLabel("custom")).toBe("custom");
  });
  it("returns Unknown for empty string", () => {
    expect(buildSourceLabel("")).toBe("Unknown");
  });
});

// ── buildStatusLabel ──────────────────────────────────────────────────────────

describe("buildStatusLabel", () => {
  it("maps FULLY_EXECUTED to Fully executed", () => {
    expect(buildStatusLabel("FULLY_EXECUTED")).toBe("Fully executed");
  });
  it("maps PARTIALLY_EXECUTED to Partially executed", () => {
    expect(buildStatusLabel("PARTIALLY_EXECUTED")).toBe("Partially executed");
  });
  it("maps SKIPPED to Skipped", () => {
    expect(buildStatusLabel("SKIPPED")).toBe("Skipped");
  });
  it("maps DRAFT to Draft", () => {
    expect(buildStatusLabel("DRAFT")).toBe("Draft");
  });
});

// ── computeCashDeployed ───────────────────────────────────────────────────────

describe("computeCashDeployed", () => {
  it("sums BOUGHT amounts", () => {
    const decisions = [
      { ticker: "AAPL", actual_action: "BOUGHT", actual_amount: 1000 },
      { ticker: "MSFT", actual_action: "BOUGHT", actual_amount: 500 },
    ];
    expect(computeCashDeployed(decisions)).toBe(1500);
  });

  it("sums PARTIAL amounts", () => {
    const decisions = [{ ticker: "AAPL", actual_action: "PARTIAL", actual_amount: 200 }];
    expect(computeCashDeployed(decisions)).toBe(200);
  });

  it("sums REPLACED amounts", () => {
    const decisions = [{ ticker: "AAPL", actual_action: "REPLACED", actual_amount: 300 }];
    expect(computeCashDeployed(decisions)).toBe(300);
  });

  it("ignores TRIMMED amounts (not deployed capital)", () => {
    const decisions = [
      { ticker: "AAPL", actual_action: "BOUGHT", actual_amount: 1000 },
      { ticker: "MSFT", actual_action: "TRIMMED", actual_amount: 500 },
    ];
    expect(computeCashDeployed(decisions)).toBe(1000);
  });

  it("ignores SKIPPED amounts", () => {
    const decisions = [{ ticker: "AAPL", actual_action: "SKIPPED", actual_amount: 500 }];
    expect(computeCashDeployed(decisions)).toBe(0);
  });

  it("returns 0 for empty decisions", () => {
    expect(computeCashDeployed([])).toBe(0);
  });
});

// ── buildJournalEntries ───────────────────────────────────────────────────────

describe("buildJournalEntries — empty / invalid input", () => {
  it("returns empty array for empty logs", () => {
    expect(buildJournalEntries([])).toEqual([]);
  });
});

describe("buildJournalEntries — chapter numerals", () => {
  it("assigns chapter numerals oldest-first (I = earliest)", () => {
    const logs = [
      makeLog({ id: "log-1", created_at: "2026-05-01T00:00:00Z" }),
      makeLog({ id: "log-2", created_at: "2026-05-10T00:00:00Z" }),
    ];
    const entries = buildJournalEntries(logs);
    // Returns newest-first for display
    expect(entries[0].chapterNumeral).toBe("II"); // most recent
    expect(entries[1].chapterNumeral).toBe("I");  // oldest
  });

  it("single entry gets chapter I", () => {
    const entries = buildJournalEntries([makeLog()]);
    expect(entries[0].chapterNumeral).toBe("I");
  });

  it("three entries get I, II, III in correct order", () => {
    const logs = [
      makeLog({ id: "a", created_at: "2026-05-01T00:00:00Z" }),
      makeLog({ id: "b", created_at: "2026-05-05T00:00:00Z" }),
      makeLog({ id: "c", created_at: "2026-05-10T00:00:00Z" }),
    ];
    const entries = buildJournalEntries(logs);
    const numerals = entries.map((e) => e.chapterNumeral);
    expect(numerals).toEqual(["III", "II", "I"]);
  });
});

describe("buildJournalEntries — entry anatomy", () => {
  it("decision rows are extracted from actual_decisions", () => {
    const log = makeLog({
      actual_decisions: [
        { ticker: "AAPL", actual_action: "BOUGHT", actual_amount: 500, is_manual: false },
        { ticker: "MSFT", actual_action: "BOUGHT", actual_amount: 300, is_manual: false },
      ],
    });
    const entries = buildJournalEntries([log]);
    expect(entries[0].decisions).toHaveLength(2);
    expect(entries[0].decisions[0].ticker).toBe("AAPL");
  });

  it("skips decision rows without ticker", () => {
    const log = makeLog({
      actual_decisions: [
        { ticker: "", actual_action: "BOUGHT", actual_amount: 500 },
        { ticker: "AAPL", actual_action: "BOUGHT", actual_amount: 300 },
      ],
    });
    const entries = buildJournalEntries([log]);
    expect(entries[0].decisions).toHaveLength(1);
  });

  it("sourceLabel is set for deploy_v3", () => {
    const entries = buildJournalEntries([makeLog({ source: "deploy_v3" })]);
    expect(entries[0].sourceLabel).toBe("Deploy v3");
  });

  it("statusLabel is set", () => {
    const entries = buildJournalEntries([makeLog({ status: "FULLY_EXECUTED" })]);
    expect(entries[0].statusLabel).toBe("Fully executed");
  });

  it("notes are passed through", () => {
    const entries = buildJournalEntries([makeLog({ notes: "Test note" })]);
    expect(entries[0].notes).toBe("Test note");
  });

  it("null notes remain null", () => {
    const entries = buildJournalEntries([makeLog({ notes: null })]);
    expect(entries[0].notes).toBeNull();
  });

  it("cashDeployed is sum of BOUGHT amounts", () => {
    const log = makeLog({
      actual_decisions: [
        { ticker: "AAPL", actual_action: "BOUGHT", actual_amount: 600 },
        { ticker: "MSFT", actual_action: "BOUGHT", actual_amount: 400 },
      ],
    });
    const entries = buildJournalEntries([log]);
    expect(entries[0].cashDeployed).toBe(1000);
  });
});

describe("buildJournalEntries — evaluation state", () => {
  it("evaluation state is pending when no performance_snapshot", () => {
    const entries = buildJournalEntries([makeLog({ performance_snapshot: null })]);
    expect(entries[0].evaluationState.kind).toBe("pending");
  });
});

// ── Coming-Later captions ─────────────────────────────────────────────────────

describe("Journal Coming-Later captions", () => {
  it("JOURNAL_LESSONS_CAPTION is defined and non-empty", () => {
    expect(JOURNAL_LESSONS_CAPTION.length).toBeGreaterThan(10);
  });

  it("JOURNAL_WHAT_I_LEARNED_CAPTION is defined and non-empty", () => {
    expect(JOURNAL_WHAT_I_LEARNED_CAPTION.length).toBeGreaterThan(10);
  });

  it("Lessons caption does not promise pattern-detected content", () => {
    expect(JOURNAL_LESSONS_CAPTION.toLowerCase()).not.toMatch(
      /pattern.detected|ai.generated|ml.output/
    );
  });

  it("What I learned caption indicates future stage", () => {
    expect(JOURNAL_WHAT_I_LEARNED_CAPTION.toLowerCase()).toMatch(
      /prepared|stage|surface/
    );
  });
});
