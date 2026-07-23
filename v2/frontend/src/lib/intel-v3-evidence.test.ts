/**
 * Stage 4D — intel-v3-evidence.ts pure helper contracts.
 *
 * Covers:
 * - evidenceBandToBeginnerLabel: all bands + unknown
 * - committeeStatusToPlainLabel: all statuses + unknown
 * - formatSnapshotIdShort: null, undefined, full ID, short ID
 * - formatUpdatedAtSafe: valid ISO, invalid, null/undefined
 * - evidenceFreshnessToLabel: all states
 * - buildDataHealthRows: various input combinations
 */

import {
  evidenceBandToBeginnerLabel,
  committeeStatusToPlainLabel,
  conflictReviewStatusToLabel,
  sourceLineageToLabel,
  formatSnapshotIdShort,
  formatUpdatedAtSafe,
  evidenceFreshnessToLabel,
  buildDataHealthRows,
  type DataHealthRow,
} from "./intel-v3-evidence";

// ── evidenceBandToBeginnerLabel ───────────────────────────────────────────────

describe("evidenceBandToBeginnerLabel", () => {
  it("STRONG → Strong current evidence", () => {
    expect(evidenceBandToBeginnerLabel("STRONG")).toBe("Strong current evidence");
  });

  it("PARTIAL → Some evidence, still incomplete", () => {
    expect(evidenceBandToBeginnerLabel("PARTIAL")).toBe("Some evidence, still incomplete");
  });

  it("THIN → Thin evidence — treat as lower confidence", () => {
    expect(evidenceBandToBeginnerLabel("THIN")).toBe("Thin evidence — treat as lower confidence");
  });

  it("unknown band → Evidence state unavailable", () => {
    expect(evidenceBandToBeginnerLabel("UNKNOWN")).toBe("Evidence state unavailable");
    expect(evidenceBandToBeginnerLabel("")).toBe("Evidence state unavailable");
  });

  it("labels contain no raw metric keys", () => {
    const RAW_KEYS = ["fcf_margin", "roic_ttm", "ev_ebitda", "peg_ratio", "gross_margin_ttm"];
    for (const band of ["STRONG", "PARTIAL", "THIN"]) {
      const label = evidenceBandToBeginnerLabel(band);
      for (const key of RAW_KEYS) {
        expect(label.toLowerCase()).not.toContain(key);
      }
    }
  });

  it("STRONG label does not appear for THIN band", () => {
    expect(evidenceBandToBeginnerLabel("THIN")).not.toContain("Strong");
  });
});

// ── committeeStatusToPlainLabel ───────────────────────────────────────────────

describe("committeeStatusToPlainLabel", () => {
  it("source_validated → Source-linked", () => {
    expect(committeeStatusToPlainLabel("source_validated")).toBe("Source-linked");
  });

  it("ready → Source-linked", () => {
    expect(committeeStatusToPlainLabel("ready")).toBe("Source-linked");
  });

  it("pending → Source linking not complete yet", () => {
    expect(committeeStatusToPlainLabel("pending")).toBe("Source linking not complete yet");
  });

  it("deferred → Source linking not complete yet", () => {
    expect(committeeStatusToPlainLabel("deferred")).toBe("Source linking not complete yet");
  });

  it("unknown/empty → Source state unavailable", () => {
    expect(committeeStatusToPlainLabel("unknown")).toBe("Source state unavailable");
    expect(committeeStatusToPlainLabel("")).toBe("Source state unavailable");
  });

  it("labels contain no fake credibility or contradiction text", () => {
    const FORBIDDEN = ["credibility score", "contradiction", "SEC", "sentiment confirmed"];
    for (const status of ["source_validated", "ready", "pending", "deferred"]) {
      const label = committeeStatusToPlainLabel(status);
      for (const forbidden of FORBIDDEN) {
        expect(label.toLowerCase()).not.toContain(forbidden.toLowerCase());
      }
    }
  });
});

// ── conflictReviewStatusToLabel / sourceLineageToLabel (run_trust_contract_v1) ─

describe("conflictReviewStatusToLabel", () => {
  it("succeeded → passed", () => {
    expect(conflictReviewStatusToLabel("succeeded")).toContain("passed");
  });

  it("failed → explicit failure, without successful reconciliation", () => {
    const label = conflictReviewStatusToLabel("failed");
    expect(label).toContain("failed");
    expect(label).toContain("without successful reconciliation");
  });

  it("pending → still pending", () => {
    expect(conflictReviewStatusToLabel("pending")).toContain("pending");
  });

  it("not_required/undefined → no review required, never a bare dash", () => {
    expect(conflictReviewStatusToLabel("not_required")).not.toBe("—");
    expect(conflictReviewStatusToLabel(undefined)).not.toBe("—");
    expect(conflictReviewStatusToLabel(null)).toContain("No conflict review");
  });
});

describe("sourceLineageToLabel", () => {
  it("has_source_refs=true → references recorded", () => {
    expect(sourceLineageToLabel({ has_source_refs: true })).toContain("are recorded");
  });

  it("has_source_refs=false → explicit missing, never a bare dash", () => {
    const label = sourceLineageToLabel({ has_source_refs: false });
    expect(label).toContain("No source references");
    expect(label).not.toBe("—");
  });

  it("null/undefined → explicit not-assessed text", () => {
    expect(sourceLineageToLabel(null)).toContain("not assessed");
    expect(sourceLineageToLabel(undefined)).toContain("not assessed");
  });
});

// ── formatSnapshotIdShort ─────────────────────────────────────────────────────

describe("formatSnapshotIdShort", () => {
  it("null → —", () => {
    expect(formatSnapshotIdShort(null)).toBe("—");
  });

  it("undefined → —", () => {
    expect(formatSnapshotIdShort(undefined)).toBe("—");
  });

  it("empty string → —", () => {
    expect(formatSnapshotIdShort("")).toBe("—");
  });

  it("full UUID returns first 8 chars", () => {
    expect(formatSnapshotIdShort("abc12345-def0-1234-5678-abcdef012345")).toBe("abc12345");
  });

  it("short ID (< 8 chars) returns full string", () => {
    expect(formatSnapshotIdShort("abcd")).toBe("abcd");
  });

  it("exactly 8 chars returns all 8 chars", () => {
    expect(formatSnapshotIdShort("12345678")).toBe("12345678");
  });
});

// ── formatUpdatedAtSafe ───────────────────────────────────────────────────────

describe("formatUpdatedAtSafe", () => {
  it("null → —", () => {
    expect(formatUpdatedAtSafe(null)).toBe("—");
  });

  it("undefined → —", () => {
    expect(formatUpdatedAtSafe(undefined)).toBe("—");
  });

  it("empty string → —", () => {
    expect(formatUpdatedAtSafe("")).toBe("—");
  });

  it("invalid ISO string → —", () => {
    expect(formatUpdatedAtSafe("not-a-date")).toBe("—");
  });

  it("valid ISO string returns a non-empty human-readable date", () => {
    const result = formatUpdatedAtSafe("2026-01-15T00:00:00Z");
    expect(result).not.toBe("—");
    expect(result.length).toBeGreaterThan(3);
    // Should contain month abbreviation and year
    expect(result).toMatch(/Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec/);
    expect(result).toContain("2026");
  });

  it("does not output a raw ISO timestamp", () => {
    const result = formatUpdatedAtSafe("2026-05-17T10:30:00Z");
    expect(result).not.toMatch(/T\d{2}:\d{2}:\d{2}/);
  });
});

// ── evidenceFreshnessToLabel ──────────────────────────────────────────────────

describe("evidenceFreshnessToLabel", () => {
  it("certified_current → Up to date", () => {
    expect(evidenceFreshnessToLabel("certified_current")).toBe("Up to date");
  });

  it("rebuilt_and_published → Refreshed and certified", () => {
    expect(evidenceFreshnessToLabel("rebuilt_and_published")).toBe("Refreshed and certified");
  });

  it("republish_pending → Refresh pending", () => {
    expect(evidenceFreshnessToLabel("republish_pending")).toBe("Refresh pending");
  });

  it("certification_blocked → Certification blocked", () => {
    expect(evidenceFreshnessToLabel("certification_blocked")).toBe("Certification blocked");
  });

  it("no_snapshot_exists → No snapshot yet", () => {
    expect(evidenceFreshnessToLabel("no_snapshot_exists")).toBe("No snapshot yet");
  });

  it("null/undefined → Freshness state unavailable", () => {
    expect(evidenceFreshnessToLabel(null)).toBe("Freshness state unavailable");
    expect(evidenceFreshnessToLabel(undefined)).toBe("Freshness state unavailable");
  });

  it("unknown state → Freshness state unavailable", () => {
    expect(evidenceFreshnessToLabel("some_unknown_state")).toBe("Freshness state unavailable");
  });
});

// ── buildDataHealthRows ───────────────────────────────────────────────────────

describe("buildDataHealthRows", () => {
  it("returns exactly 4 rows always (Intel snapshot, Evidence freshness, Price, Broker sync)", () => {
    const rows = buildDataHealthRows({});
    expect(rows).toHaveLength(4);
  });

  it("all rows have label, status, detail fields", () => {
    const rows = buildDataHealthRows({});
    for (const row of rows) {
      expect(typeof row.label).toBe("string");
      expect(row.label.length).toBeGreaterThan(0);
      expect(["ok", "pending", "unavailable", "blocked"]).toContain(row.status);
      expect(typeof row.label).toBe("string");
    }
  });

  it("missing inputs produce unavailable rows — never fake status", () => {
    const rows = buildDataHealthRows({});
    const intelRow = rows.find((r) => r.label === "Intel snapshot")!;
    const freshnessRow = rows.find((r) => r.label === "Evidence freshness")!;
    const priceRow = rows.find((r) => r.label === "Price data")!;
    const brokerRow = rows.find((r) => r.label === "Broker sync")!;

    expect(intelRow.status).toBe("unavailable");
    expect(freshnessRow.status).toBe("unavailable");
    expect(priceRow.status).toBe("unavailable");
    expect(brokerRow.status).toBe("unavailable");
  });

  it("unavailable rows show 'Not connected to this view yet'", () => {
    const rows = buildDataHealthRows({});
    for (const row of rows.filter((r) => r.status === "unavailable")) {
      expect(row.detail).toContain("Not connected");
    }
  });

  it("worker_certified snapshot source → ok status", () => {
    const rows = buildDataHealthRows({ intelSnapshotSource: "worker_certified" });
    const intelRow = rows.find((r) => r.label === "Intel snapshot")!;
    expect(intelRow.status).toBe("ok");
    expect(intelRow.detail).toContain("certified");
  });

  it("certification_failed snapshot source → blocked status", () => {
    const rows = buildDataHealthRows({ intelSnapshotSource: "certification_failed" });
    const intelRow = rows.find((r) => r.label === "Intel snapshot")!;
    expect(intelRow.status).toBe("blocked");
  });

  it("worker_certified_with_gaps snapshot source → amber pending, plain English, never blocked", () => {
    const rows = buildDataHealthRows({ intelSnapshotSource: "worker_certified_with_gaps" });
    const intelRow = rows.find((r) => r.label === "Intel snapshot")!;
    expect(intelRow.status).toBe("pending");
    expect(intelRow.status).not.toBe("blocked");
    expect(intelRow.detail).toContain("couldn't be analyzed");
    // The raw enum (with or without underscores) must never render.
    expect(intelRow.detail).not.toContain("worker_certified_with_gaps");
    expect(intelRow.detail.toLowerCase()).not.toContain("worker certified with gaps");
  });

  it("certified_current freshness → ok status", () => {
    const rows = buildDataHealthRows({ intelFreshnessState: "certified_current" });
    const row = rows.find((r) => r.label === "Evidence freshness")!;
    expect(row.status).toBe("ok");
    expect(row.detail).toBe("Up to date");
  });

  it("republish_pending freshness → pending status", () => {
    const rows = buildDataHealthRows({ intelFreshnessState: "republish_pending" });
    const row = rows.find((r) => r.label === "Evidence freshness")!;
    expect(row.status).toBe("pending");
    expect(row.detail).toBe("Refresh pending");
  });

  it("all prices fresh → ok status", () => {
    const rows = buildDataHealthRows({ pricesFresh: 10, pricesStale: 0 });
    const row = rows.find((r) => r.label === "Price data")!;
    expect(row.status).toBe("ok");
    expect(row.detail).toContain("10 of 10");
  });

  it("some stale prices → pending status", () => {
    const rows = buildDataHealthRows({ pricesFresh: 8, pricesStale: 2 });
    const row = rows.find((r) => r.label === "Price data")!;
    expect(row.status).toBe("pending");
  });

  it("plaid connected → ok status with last-synced detail", () => {
    const rows = buildDataHealthRows({
      plaidStatus: "connected",
      plaidLastSyncedAt: "2026-05-17T10:00:00Z",
    });
    const row = rows.find((r) => r.label === "Broker sync")!;
    expect(row.status).toBe("ok");
    expect(row.detail).toContain("Connected");
  });

});
