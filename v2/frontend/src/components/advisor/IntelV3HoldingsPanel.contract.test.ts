/**
 * IntelV3HoldingsPanel contract — source-inspection tests (node env).
 *
 * Blocker 1: exactly ONE Run Intel controller on the Advisor page. The
 * holdings panel is presentation-only: no snapshot/run hooks, no polling,
 * no run button, no run-state band. The page owns the single snapshot query
 * and the single run mutation, and only the readiness panel receives it.
 *
 * Blocker 2: no Coming-Later / Opportunity Radar chrome anywhere in
 * component source.
 *
 * These assertions are source-level because the components cannot be
 * jsdom-rendered in this node test environment (renderingContract style).
 */

import fs from "fs";
import path from "path";

const SRC_ROOT = path.join(__dirname, "..", "..");

const PANEL_FILE = path.join(__dirname, "IntelV3HoldingsPanel.tsx");
const PAGE_FILE = path.join(SRC_ROOT, "app", "dashboard", "advisor", "page.tsx");
const RETIRED_COCKPIT_FILE = path.join(
  SRC_ROOT,
  "components",
  "cards",
  "IntelV3Cockpit.tsx",
);
const HOOKS_FILE = path.join(SRC_ROOT, "lib", "hooks.ts");

function read(file: string): string {
  return fs.readFileSync(file, "utf-8");
}

function countOccurrences(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1;
}

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else if (entry.isFile()) out.push(full);
  }
  return out;
}

describe("IntelV3HoldingsPanel is presentation-only", () => {
  const panelSource = read(PANEL_FILE);

  it.each([
    "useRunIntelV3",
    "useIntelV3Snapshot",
    "setInterval",
    "Run Intel",
    "lastRunResult",
    "Opportunity Radar",
    "ComingLater",
    "future stage",
    "Radar candidates",
  ])("does not contain %s", (forbidden) => {
    expect(panelSource).not.toContain(forbidden);
  });

  it("does not run any mutation", () => {
    expect(panelSource).not.toContain("useMutation");
    expect(panelSource).not.toContain(".mutate(");
  });

  it("receives the shared snapshot state via props", () => {
    expect(panelSource).toContain("snapshot: IntelV3Snapshot | null");
    expect(panelSource).toContain("isLoading: boolean");
    expect(panelSource).toContain("noSnapshot: boolean");
  });

  it("keeps its headings at h3 level (page owns the h2 section heading)", () => {
    expect(panelSource).not.toContain("<h2");
    expect(panelSource).toContain("<h3");
  });

  it("keeps the honest exclusion note for rationale-less cards", () => {
    expect(panelSource).toContain("explanation was available for their current action");
    expect(panelSource).toContain("partitionRenderableCards");
    expect(panelSource).toContain("Excluded tickers");
  });
});

describe("the retired cockpit is gone", () => {
  it("IntelV3Cockpit.tsx no longer exists", () => {
    expect(fs.existsSync(RETIRED_COCKPIT_FILE)).toBe(false);
  });

  it("nothing imports the retired cockpit module", () => {
    // Assembled at runtime so this test file never contains the literal
    // import strings it scans for.
    const retiredModule = ["IntelV3", "Cockpit"].join("");
    const importPaths = [
      `from "@/components/cards/${retiredModule}"`,
      `from "./${retiredModule}"`,
      `from "../cards/${retiredModule}"`,
    ];
    const offenders = walk(SRC_ROOT).filter((f) => {
      const source = read(f);
      return importPaths.some((p) => source.includes(p));
    });
    expect(offenders).toEqual([]);
  });
});

describe("advisor page owns the single Run Intel controller", () => {
  const pageSource = read(PAGE_FILE);

  it("invokes useRunIntelV3 exactly once", () => {
    expect(countOccurrences(pageSource, "useRunIntelV3()")).toBe(1);
  });

  it("triggers the run mutation exactly once, on the readiness panel only", () => {
    expect(countOccurrences(pageSource, "runMutation.mutate")).toBe(1);
    // The single trigger is the readiness panel's onRun prop.
    const readinessPanelChunk = pageSource.slice(
      pageSource.indexOf("<AdvisorReadinessPanel"),
      pageSource.indexOf("/>", pageSource.indexOf("<AdvisorReadinessPanel")),
    );
    expect(readinessPanelChunk).toContain("onRun={() => runMutation.mutate()}");
    expect(readinessPanelChunk).toContain("lastRunResult={runMutation.data ?? null}");
  });

  it("passes only snapshot presentation props to the holdings panel (no run wiring)", () => {
    const start = pageSource.indexOf("<IntelV3HoldingsPanel");
    expect(start).toBeGreaterThan(-1);
    const holdingsChunk = pageSource.slice(start, pageSource.indexOf("/>", start));
    expect(holdingsChunk).toContain("snapshot={snapshotQuery.data ?? null}");
    expect(holdingsChunk).not.toContain("runMutation");
    expect(holdingsChunk).not.toContain("onRun");
  });

  it("no component outside the page/hook definition/tests uses useRunIntelV3", () => {
    const offenders = walk(SRC_ROOT).filter((f) => {
      const resolved = path.resolve(f);
      if (resolved === path.resolve(PAGE_FILE)) return false;
      if (resolved === path.resolve(HOOKS_FILE)) return false;
      if (/\.test\.tsx?$/.test(f)) return false;
      return read(f).includes("useRunIntelV3");
    });
    expect(offenders).toEqual([]);
  });
});

describe("no Coming-Later / Opportunity Radar chrome remains in components", () => {
  it("component sources contain none of the retired placeholder vocabulary", () => {
    const componentFiles = walk(SRC_ROOT).filter(
      (f) => f.endsWith(".tsx") && !/\.test\.tsx$/.test(f),
    );
    const forbidden = ["Opportunity Radar", "ComingLater", "future stage", "Radar candidates"];
    const offenders: string[] = [];
    for (const file of componentFiles) {
      const source = read(file);
      for (const token of forbidden) {
        if (source.includes(token)) offenders.push(`${path.basename(file)}: ${token}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
