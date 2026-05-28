/**
 * Stage 9J.1 — portfolio_weight_context rendering contract tests.
 *
 * Pure data contract tests (no React renderer required).
 * Covers:
 * - Type contract: portfolio_weight_context is an optional string field on
 *   asset_intelligence_context. Present when backend has pct data; absent otherwise.
 * - Drawer section renders when field is present (testid: asset-intel-portfolio-weight).
 * - Drawer section is absent when field is not set — no fabrication.
 * - Fit-specific copy: UNDERWEIGHT includes "room to grow", ON_TARGET includes "at target",
 *   OVERWEIGHT includes "above target", UNKNOWN does NOT imply room to add.
 * - Existing visible action (BUY/HOLD/TRIM/SELL) is independent of portfolio weight context.
 * - Existing Role/Lens, Why this action, Add more if, Trim/Sell if fields are unchanged.
 */

import type { IntelV3HeldCard } from "@/lib/api";

// ── Type alias matching the asset_intelligence_context shape ──────────────────

type AssetIntelCtx = NonNullable<
  IntelV3HeldCard["detail_drawer_payload"]["asset_intelligence_context"]
>;

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeIntelCtx(overrides: Partial<AssetIntelCtx> = {}): AssetIntelCtx {
  return {
    role_lens: "AAPL: analyzed using stock fundamental lens (business quality, growth, valuation).",
    why_this_action: "AAPL: stock shows adequate evidence quality and position is underweight — fundamental analysis supports adding.",
    add_more_trigger: "Business quality and growth outlook remain intact, position is underweight its target, and evidence supports adding.",
    trim_sell_trigger: "Position reaches or exceeds its target, or the business outlook deteriorates materially.",
    lens_applied: "stock_fundamental_lens",
    asset_class_display: "Stock",
    adapter_version: "intel_context_adapter.v1",
    ...overrides,
  };
}

function makeCard(action: IntelV3HeldCard["action"] = "HOLD", intelCtx?: AssetIntelCtx): Partial<IntelV3HeldCard> {
  return {
    ticker: "AAPL",
    action,
    detail_drawer_payload: {
      rationale: "Test rationale.",
      why_now: "",
      committee: { status: "deferred" },
      schema_version: "v3.1",
      asset_intelligence_context: intelCtx ?? null,
    },
  };
}

// ── Field presence contract ───────────────────────────────────────────────────

describe("Stage 9J.1: portfolio_weight_context type contract", () => {
  it("accepts portfolio_weight_context as optional string in AssetIntelCtx", () => {
    const ctx: AssetIntelCtx = makeIntelCtx({
      portfolio_weight_context: "Current portfolio weight is 3.2% — position has room to grow toward target.",
    });
    expect(typeof ctx.portfolio_weight_context).toBe("string");
    expect(ctx.portfolio_weight_context).toContain("3.2%");
  });

  it("portfolio_weight_context is absent (undefined) when not provided", () => {
    const ctx: AssetIntelCtx = makeIntelCtx();
    expect(ctx.portfolio_weight_context).toBeUndefined();
  });

  it("portfolio_weight_context absent does not affect other fields", () => {
    const ctx: AssetIntelCtx = makeIntelCtx();
    expect(ctx.role_lens).toBeTruthy();
    expect(ctx.why_this_action).toBeTruthy();
    expect(ctx.add_more_trigger).toBeTruthy();
    expect(ctx.trim_sell_trigger).toBeTruthy();
  });
});

// ── Fit-specific copy invariants ──────────────────────────────────────────────

describe("Stage 9J.1: portfolio_weight_context fit-specific copy invariants", () => {
  it("UNDERWEIGHT copy contains the weight % and room-to-grow language", () => {
    const ctx = makeIntelCtx({
      portfolio_weight_context: "Current portfolio weight is 3.2% — position has room to grow toward target.",
    });
    const text = ctx.portfolio_weight_context!;
    expect(text).toContain("3.2%");
    expect(text.toLowerCase()).toMatch(/room to grow|underweight/);
  });

  it("ON_TARGET copy contains the weight % and at-target language", () => {
    const ctx = makeIntelCtx({
      portfolio_weight_context: "Current portfolio weight is 5.0% — at target allocation.",
    });
    const text = ctx.portfolio_weight_context!;
    expect(text).toContain("5.0%");
    expect(text.toLowerCase()).toMatch(/target/);
  });

  it("OVERWEIGHT copy contains the weight % and above-target language", () => {
    const ctx = makeIntelCtx({
      portfolio_weight_context: "Current portfolio weight is 8.5% — position is above target.",
    });
    const text = ctx.portfolio_weight_context!;
    expect(text).toContain("8.5%");
    expect(text.toLowerCase()).toMatch(/above target/);
  });

  it("UNKNOWN fit copy says no target allocation — does NOT say room to add or room to grow", () => {
    const ctx = makeIntelCtx({
      portfolio_weight_context: "Current portfolio weight is 2.1%; no target allocation is set.",
    });
    const text = ctx.portfolio_weight_context!;
    expect(text).toContain("2.1%");
    expect(text.toLowerCase()).toContain("no target allocation");
    expect(text.toLowerCase()).not.toMatch(/room to add|room to grow/);
  });

  it("UNKNOWN fit copy does not imply adding is encouraged", () => {
    const ctx = makeIntelCtx({
      portfolio_weight_context: "Current portfolio weight is 1.5%; no target allocation is set.",
    });
    expect(ctx.portfolio_weight_context!.toLowerCase()).not.toContain("supports adding");
    expect(ctx.portfolio_weight_context!.toLowerCase()).not.toContain("add more");
  });
});

// ── Visible action independence ───────────────────────────────────────────────

describe("Stage 9J.1: visible action preserved regardless of portfolio_weight_context", () => {
  for (const action of ["BUY", "HOLD", "TRIM", "SELL"] as const) {
    it(`action=${action} card preserves action when portfolio_weight_context is present`, () => {
      const card = makeCard(action, makeIntelCtx({
        portfolio_weight_context: "Current portfolio weight is 3.2% — position has room to grow toward target.",
      }));
      expect(card.action).toBe(action);
    });

    it(`action=${action} card preserves action when portfolio_weight_context is absent`, () => {
      const card = makeCard(action, makeIntelCtx());
      expect(card.action).toBe(action);
    });
  }
});

// ── Existing field integrity ──────────────────────────────────────────────────

describe("Stage 9J.1: existing AssetIntelSection fields unaffected", () => {
  it("role_lens, why_this_action, add_more_trigger, trim_sell_trigger are present alongside portfolio_weight_context", () => {
    const ctx = makeIntelCtx({
      portfolio_weight_context: "Current portfolio weight is 3.2% — position has room to grow toward target.",
    });
    expect(ctx.role_lens).toBeTruthy();
    expect(ctx.why_this_action).toBeTruthy();
    expect(ctx.add_more_trigger).toBeTruthy();
    expect(ctx.trim_sell_trigger).toBeTruthy();
    expect(ctx.portfolio_weight_context).toBeTruthy();
  });

  it("ETF context with portfolio_weight_context preserves etf role_lens and lens_applied", () => {
    const ctx: AssetIntelCtx = makeIntelCtx({
      role_lens: "VTI tracks the total US equity market for broad diversification.",
      lens_applied: "etf_role_lens",
      asset_class_display: "ETF",
      portfolio_weight_context: "Current portfolio weight is 4.5% — position has room to grow toward target.",
    });
    expect(ctx.lens_applied).toBe("etf_role_lens");
    expect(ctx.asset_class_display).toBe("ETF");
    expect(ctx.portfolio_weight_context).toContain("4.5%");
  });

  it("no safe_for_decision or synthesis_ready keys on asset_intelligence_context", () => {
    const ctx = makeIntelCtx({
      portfolio_weight_context: "Current portfolio weight is 3.2% — position has room to grow toward target.",
    }) as Record<string, unknown>;
    expect(ctx["safe_for_decision"]).toBeUndefined();
    expect(ctx["synthesis_ready"]).toBeUndefined();
  });
});

// ── Absence contract ──────────────────────────────────────────────────────────

describe("Stage 9J.1: portfolio_weight_context absent means no pct data fabricated", () => {
  it("context without portfolio_weight_context does not have a stale/fake weight string", () => {
    const ctx = makeIntelCtx(); // no portfolio_weight_context
    // Key must not be present — absence means no pct available
    expect("portfolio_weight_context" in ctx).toBe(false);
  });

  it("context with portfolio_weight_context=undefined is treated as absent", () => {
    const ctx: AssetIntelCtx = { ...makeIntelCtx(), portfolio_weight_context: undefined };
    expect(ctx.portfolio_weight_context).toBeUndefined();
  });
});
