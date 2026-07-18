/**
 * Stage 4D — Evidence Shell + Source UX + Data Health Drawer contracts.
 *
 * Covers:
 * - Evidence band → beginner label (spec §4 source UX contract)
 * - Committee/source-pack status → plain-English label
 * - Source metadata handles missing IDs and dates
 * - Data health rows render unavailable/coming-later honestly
 * - No fake credibility/contradiction/completeness labels appear as live values
 * - Stage 4D Coming-Later captions use canonical pattern
 * - Current fields map without raw metric keys
 * - DataHealthStatus type covers ok/pending/unavailable/blocked
 */

import {
  evidenceBandToBeginnerLabel,
  committeeStatusToPlainLabel,
  formatSnapshotIdShort,
  formatUpdatedAtSafe,
  buildDataHealthRows,
  type DataHealthStatus,
} from "@/lib/intel-v3-evidence";
import { COMING_LATER_CANONICAL_CAPTION } from "./IntelV3PrimitivesData";

// ── Stage 4D source UX spec mapping ──────────────────────────────────────────

describe("Stage 4D — evidence band to beginner label spec mapping", () => {
  it("STRONG maps to 'Strong current evidence' (spec §4)", () => {
    expect(evidenceBandToBeginnerLabel("STRONG")).toBe("Strong current evidence");
  });

  it("PARTIAL maps to 'Some evidence, still incomplete' (spec §4)", () => {
    expect(evidenceBandToBeginnerLabel("PARTIAL")).toBe("Some evidence, still incomplete");
  });

  it("THIN maps to 'Thin evidence — treat as lower confidence' (spec §4)", () => {
    expect(evidenceBandToBeginnerLabel("THIN")).toBe("Thin evidence — treat as lower confidence");
  });

  it("THIN label never promotes THIN to STRONG or PARTIAL", () => {
    const label = evidenceBandToBeginnerLabel("THIN");
    expect(label).not.toContain("Strong");
    expect(label).not.toContain("complete");
  });

  it("evidence band labels are beginner-friendly (no finance jargon)", () => {
    const BANNED_JARGON = ["AxisBand", "OK band", "signal band", "evidence_band", "IntelV3"];
    for (const band of ["STRONG", "PARTIAL", "THIN"]) {
      const label = evidenceBandToBeginnerLabel(band);
      for (const jargon of BANNED_JARGON) {
        expect(label).not.toContain(jargon);
      }
    }
  });
});

describe("Stage 4D — committee/source-pack status to plain-English label spec mapping", () => {
  it("source_validated maps to 'Source-linked' (spec §4)", () => {
    expect(committeeStatusToPlainLabel("source_validated")).toBe("Source-linked");
  });

  it("pending maps to 'Source linking not complete yet' (spec §4)", () => {
    expect(committeeStatusToPlainLabel("pending")).toBe("Source linking not complete yet");
  });

  it("deferred maps to 'Source linking not complete yet' (spec §4)", () => {
    expect(committeeStatusToPlainLabel("deferred")).toBe("Source linking not complete yet");
  });

  it("unknown maps to 'Source state unavailable' (spec §4)", () => {
    expect(committeeStatusToPlainLabel("some_unknown")).toBe("Source state unavailable");
  });

  it("ready and source_validated map to the same label", () => {
    expect(committeeStatusToPlainLabel("ready")).toBe(committeeStatusToPlainLabel("source_validated"));
  });
});

// ── Source metadata: handles missing IDs and dates ────────────────────────────

describe("Stage 4D — source metadata handles missing IDs/dates", () => {
  it("missing snapshot ID returns safe fallback dash", () => {
    expect(formatSnapshotIdShort(null)).toBe("—");
    expect(formatSnapshotIdShort(undefined)).toBe("—");
    expect(formatSnapshotIdShort("")).toBe("—");
  });

  it("missing updated_at returns safe fallback dash", () => {
    expect(formatUpdatedAtSafe(null)).toBe("—");
    expect(formatUpdatedAtSafe(undefined)).toBe("—");
    expect(formatUpdatedAtSafe("")).toBe("—");
  });

  it("invalid date string returns safe fallback dash — no raw exception exposed", () => {
    expect(formatUpdatedAtSafe("not-a-date")).toBe("—");
    expect(formatUpdatedAtSafe("2999-99-99T99:99:99Z")).toBe("—");
  });

  it("valid ISO date formats to human-readable, not raw ISO", () => {
    const result = formatUpdatedAtSafe("2026-01-15T12:00:00Z");
    expect(result).not.toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(result).not.toBe("—");
  });

  it("snapshot ID is truncated to 8 chars — not the full UUID", () => {
    const fullId = "abc12345-def0-1234-5678-abcdef012345";
    const short = formatSnapshotIdShort(fullId);
    expect(short).toBe("abc12345");
    expect(short).not.toContain("-def0");
  });
});

// ── Data health rows: honest unavailable rendering ────────────────────────────

describe("Stage 4D — data health rows render unavailable honestly", () => {
  it("all rows with no input are unavailable — never fake ok/pending", () => {
    const rows = buildDataHealthRows({});
    // Email delivery safety is always ok (static copy), all others should be unavailable
    const nonEmail = rows.filter((r) => r.label !== "Email delivery safety");
    for (const row of nonEmail) {
      expect(row.status).toBe("unavailable");
    }
  });

  it("unavailable rows detail text says 'Not connected to this view yet'", () => {
    const rows = buildDataHealthRows({});
    const nonEmail = rows.filter((r) => r.label !== "Email delivery safety");
    for (const row of nonEmail) {
      expect(row.detail).toContain("Not connected to this view yet");
    }
  });


  it("DataHealthStatus type covers all 4 valid values", () => {
    const validStatuses: DataHealthStatus[] = ["ok", "pending", "unavailable", "blocked"];
    expect(validStatuses).toHaveLength(4);
    expect(validStatuses).toContain("ok");
    expect(validStatuses).toContain("pending");
    expect(validStatuses).toContain("unavailable");
    expect(validStatuses).toContain("blocked");
  });
});

// ── No fake credibility/contradiction/completeness labels ─────────────────────

describe("Stage 4D — no fake Stage 5/6 intelligence appears as live data", () => {
  const FORBIDDEN_LIVE_INTELLIGENCE = [
    "credibility score",
    "contradiction detected",
    "evidence completeness",
    "SEC filing analysis",
    "technical analysis",
    "fundamental analysis",
    "sentiment confirmed",
    "company strategy confirmed",
    "source verified",
    "accuracy",
    "reliability score",
  ];

  it("data health row details never contain fake intelligence labels", () => {
    const rows = buildDataHealthRows({
      intelSnapshotSource: "worker_certified",
      intelFreshnessState: "certified_current",
      pricesFresh: 10,
      pricesStale: 0,
      plaidStatus: "connected",
      plaidLastSyncedAt: "2026-05-17T00:00:00Z",
    });
    for (const row of rows) {
      for (const forbidden of FORBIDDEN_LIVE_INTELLIGENCE) {
        expect(row.detail.toLowerCase()).not.toContain(forbidden.toLowerCase());
        expect(row.label.toLowerCase()).not.toContain(forbidden.toLowerCase());
      }
    }
  });

  it("evidence band beginner labels contain no fake credibility values", () => {
    for (const band of ["STRONG", "PARTIAL", "THIN"]) {
      const label = evidenceBandToBeginnerLabel(band);
      for (const forbidden of FORBIDDEN_LIVE_INTELLIGENCE) {
        expect(label.toLowerCase()).not.toContain(forbidden.toLowerCase());
      }
    }
  });
});

// ── Coming-Later canonical caption contract ───────────────────────────────────

describe("Stage 4D — Coming-Later caption uses canonical pattern", () => {
  it("canonical caption contains 'being prepared'", () => {
    expect(COMING_LATER_CANONICAL_CAPTION).toContain("being prepared");
  });

  it("canonical caption contains 'next intelligence stage'", () => {
    expect(COMING_LATER_CANONICAL_CAPTION).toContain("next intelligence stage");
  });

  it("canonical caption does not claim live source intelligence", () => {
    const FAKE_CLAIMS = [
      "source credibility score",
      "contradiction detected",
      "evidence complete",
      "SEC analysis",
      "sentiment confirmed",
    ];
    for (const claim of FAKE_CLAIMS) {
      expect(COMING_LATER_CANONICAL_CAPTION.toLowerCase()).not.toContain(claim.toLowerCase());
    }
  });
});

// ── Current fields map without raw metric keys ────────────────────────────────

describe("Stage 4D — current fields map without raw metric keys", () => {
  const RAW_METRIC_KEYS = [
    "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm",
    "peg_ratio", "p_fcf", "ebit_margin", "net_margin_ttm",
    "evidence_band", "AxisBand", "STRONG_literal",
  ];

  it("evidenceBandToBeginnerLabel output has no raw metric keys", () => {
    for (const band of ["STRONG", "PARTIAL", "THIN"]) {
      const label = evidenceBandToBeginnerLabel(band);
      for (const key of RAW_METRIC_KEYS) {
        expect(label).not.toContain(key);
      }
    }
  });

  it("committeeStatusToPlainLabel output has no raw metric keys", () => {
    for (const status of ["source_validated", "ready", "pending", "deferred"]) {
      const label = committeeStatusToPlainLabel(status);
      for (const key of RAW_METRIC_KEYS) {
        expect(label).not.toContain(key);
      }
    }
  });

  it("data health row labels have no raw metric keys", () => {
    const rows = buildDataHealthRows({ intelSnapshotSource: "worker_certified" });
    for (const row of rows) {
      for (const key of RAW_METRIC_KEYS) {
        expect(row.label).not.toContain(key);
        expect(row.detail).not.toContain(key);
      }
    }
  });
});

// ── Stage 4D canonical label set contract ────────────────────────────────────

describe("Stage 4D — data health canonical label set", () => {
  const CANONICAL_LABELS = [
    "Intel snapshot",
    "Evidence freshness",
    "Price data",
    "Broker sync",
  ];

  it("buildDataHealthRows returns exactly the canonical 4 labels", () => {
    const rows = buildDataHealthRows({});
    const labels = rows.map((r) => r.label);
    expect(labels).toHaveLength(CANONICAL_LABELS.length);
    for (const canonical of CANONICAL_LABELS) {
      expect(labels).toContain(canonical);
    }
  });

  it("no raw backend field names appear as row labels", () => {
    const RAW_BACKEND_NAMES = [
      "snapshot_source", "evidence_freshness_state", "plan_readiness_status",
      "prices_fresh", "prices_stale", "last_synced_at", "alert_candidates",
    ];
    const rows = buildDataHealthRows({});
    const labels = rows.map((r) => r.label);
    for (const raw of RAW_BACKEND_NAMES) {
      expect(labels).not.toContain(raw);
    }
  });
});
