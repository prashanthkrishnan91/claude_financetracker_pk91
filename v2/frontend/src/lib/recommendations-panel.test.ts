/**
 * Recommendations panel hard-rule contract:
 * an item with an empty/missing rationale must NEVER render.
 */

import {
  hasRenderableRationale,
  renderableRecommendations,
  secondaryEngineReason,
  actionBadgeStyle,
} from "./recommendations-panel";
import type { RecommendationPanelItem } from "./api";

function makeItem(
  overrides: Partial<RecommendationPanelItem> = {}
): RecommendationPanelItem {
  return {
    ticker: "AAPL",
    name: "Apple Inc.",
    action: "HOLD",
    conviction: "MEDIUM",
    evidence_band: "PARTIAL",
    rationale: "Position is within target range and no threshold was crossed.",
    engine_reason: "no_threshold_crossed",
    components: null,
    ...overrides,
  };
}

// ── HARD RULE: rationale filter ───────────────────────────────────────────────

describe("renderableRecommendations — mandatory rationale", () => {
  it("keeps items with a real one-line rationale", () => {
    const items = [makeItem({ ticker: "A" }), makeItem({ ticker: "B" })];
    expect(renderableRecommendations(items)).toHaveLength(2);
  });

  it("drops items with an empty-string rationale", () => {
    const items = [makeItem({ ticker: "A", rationale: "" }), makeItem({ ticker: "B" })];
    const out = renderableRecommendations(items);
    expect(out).toHaveLength(1);
    expect(out[0].ticker).toBe("B");
  });

  it("drops items with a whitespace-only rationale", () => {
    const items = [makeItem({ rationale: "   \n\t " })];
    expect(renderableRecommendations(items)).toHaveLength(0);
  });

  it("drops items with a null rationale", () => {
    const items = [makeItem({ rationale: null })];
    expect(renderableRecommendations(items)).toHaveLength(0);
  });

  it("drops items with an undefined/missing rationale", () => {
    const items = [makeItem({ rationale: undefined })];
    expect(renderableRecommendations(items)).toHaveLength(0);
  });

  it("drops items with a non-string rationale (defensive against contract drift)", () => {
    const bad = makeItem({ rationale: 42 as unknown as string });
    expect(renderableRecommendations([bad])).toHaveLength(0);
  });

  it("returns [] for null/undefined input", () => {
    expect(renderableRecommendations(null)).toEqual([]);
    expect(renderableRecommendations(undefined)).toEqual([]);
  });

  it("preserves order of surviving items", () => {
    const items = [
      makeItem({ ticker: "A" }),
      makeItem({ ticker: "B", rationale: "" }),
      makeItem({ ticker: "C" }),
    ];
    expect(renderableRecommendations(items).map(i => i.ticker)).toEqual(["A", "C"]);
  });
});

describe("hasRenderableRationale", () => {
  it("true for a non-empty rationale", () => {
    expect(hasRenderableRationale({ rationale: "Reason." })).toBe(true);
  });

  it("false for empty, whitespace, null, undefined", () => {
    expect(hasRenderableRationale({ rationale: "" })).toBe(false);
    expect(hasRenderableRationale({ rationale: "  " })).toBe(false);
    expect(hasRenderableRationale({ rationale: null })).toBe(false);
    expect(hasRenderableRationale({ rationale: undefined })).toBe(false);
  });
});

// ── secondaryEngineReason ─────────────────────────────────────────────────────

describe("secondaryEngineReason", () => {
  it("returns engine_reason when it differs from rationale", () => {
    const item = makeItem({ rationale: "Holding steady.", engine_reason: "no_threshold_crossed" });
    expect(secondaryEngineReason(item)).toBe("no_threshold_crossed");
  });

  it("returns null when engine_reason duplicates rationale (case/whitespace-insensitive)", () => {
    const item = makeItem({ rationale: "Holding steady.", engine_reason: "  holding STEADY. " });
    expect(secondaryEngineReason(item)).toBeNull();
  });

  it("returns null when engine_reason is empty or missing", () => {
    expect(secondaryEngineReason(makeItem({ engine_reason: "" }))).toBeNull();
    expect(secondaryEngineReason(makeItem({ engine_reason: null }))).toBeNull();
    expect(secondaryEngineReason(makeItem({ engine_reason: undefined }))).toBeNull();
  });
});

// ── actionBadgeStyle ──────────────────────────────────────────────────────────

describe("actionBadgeStyle — color conventions", () => {
  it("BUY is green, HOLD is blue, TRIM is yellow, SELL is red", () => {
    expect(actionBadgeStyle("BUY")).toContain("green");
    expect(actionBadgeStyle("HOLD")).toContain("blue");
    expect(actionBadgeStyle("TRIM")).toContain("yellow");
    expect(actionBadgeStyle("SELL")).toContain("red");
  });

  it("unknown action falls back to a muted style", () => {
    expect(actionBadgeStyle("WATCH")).toContain("text-text-muted");
  });
});
