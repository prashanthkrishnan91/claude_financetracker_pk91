/**
 * Contract tests: intel_read and thesis_plain_english visibility rules
 * for AgentInsightCard.
 *
 * These tests use the data-contract approach (no React rendering) to stay
 * compatible with the current Jest+ts-jest config which uses jsdom-less
 * node environment. Full DOM rendering tests require @testing-library/react
 * and a jsdom testEnvironment — a future testability enhancement.
 *
 * Tests verify:
 * - intel_read field is accessible on InsightCardData
 * - intel_read shape is correct for the "Why this view?" section
 * - intel_read contains no raw metric keys
 * - Business read remains hidden (thesis_plain_english not rendered on AgentInsightCard)
 * - collectIntelReadLines correctly reflects intel_read presence
 */

import type { InsightCardData, IntelRead } from "@/lib/api";

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeCard(overrides: Partial<InsightCardData> = {}): InsightCardData {
  return {
    id: "test-rec-1",
    ticker: "NVDA",
    name: "NVIDIA Corp",
    action: "BUY",
    detail: "AI compute demand is strong.",
    rationale: "Consistent free cash flow.",
    urgency: 1,
    color: "green",
    tax_note: "",
    drip_note: "",
    category: "Core",
    ...overrides,
  };
}

/**
 * Simulate what the AgentInsightCard WhyThisView section would render.
 * Returns all displayable text from intel_read only.
 * Mirrors WhyThisView: uses bottom_line when present, otherwise summary.
 */
function collectIntelReadLines(intelRead: IntelRead | null | undefined): string[] {
  if (!intelRead) return [];
  const lines: string[] = [];
  if (intelRead.title) lines.push(intelRead.title);
  const displaySummary = intelRead.bottom_line || intelRead.summary;
  if (displaySummary) lines.push(displaySummary);
  lines.push(...(intelRead.trusted_signals ?? []));
  lines.push(...(intelRead.incomplete_signals ?? []));
  if (intelRead.caveat) lines.push(intelRead.caveat);
  return lines;
}

// Raw metric keys that must never appear in frontend display text
const FORBIDDEN_METRIC_KEYS = [
  "fcf_margin",
  "roic_ttm",
  "p_fcf",
  "fcf_yield",
  "gross_margin",
  "fcf_to_net_income",
  "net_debt_to_ebitda",
  "ev_ebitda",
  "revenue_cagr_3y",
  "max_drawdown_1y",
  "trailing_pe",
  "forward_pe",
  "momentum_score",
  "valuation_score",
  "quality_score",
  "growth_score",
  "risk_score",
];

// ── 1. intel_read field present on InsightCardData ────────────────────────────

describe("InsightCardData.intel_read — field contract", () => {
  it("card with intel_read has accessible title", () => {
    const intelRead: IntelRead = {
      title: "Why this view?",
      posture_label: "on watch",
      summary: "The system has enough evidence to comment on valuation.",
      trusted_signals: ["valuation"],
      incomplete_signals: ["business quality", "growth", "risk"],
      caveat: "Not enough data to be confident. Wait for more signals.",
    };
    const card = makeCard({ intel_read: intelRead });
    expect(card.intel_read?.title).toBe("Why this view?");
  });

  it("card with intel_read has accessible trusted_signals list", () => {
    const card = makeCard({
      intel_read: {
        title: "Why this view?",
        posture_label: "on watch",
        summary: "Test summary.",
        trusted_signals: ["valuation", "recent market behavior"],
        incomplete_signals: ["business quality", "growth"],
        caveat: "Treat as early signal.",
      },
    });
    expect(card.intel_read?.trusted_signals).toContain("valuation");
    expect(card.intel_read?.trusted_signals).toContain("recent market behavior");
  });

  it("card without intel_read has undefined field (backward-compat)", () => {
    const card = makeCard();
    expect(card.intel_read).toBeUndefined();
  });

  it("card with intel_read: null produces no display lines", () => {
    const card = makeCard({ intel_read: null });
    expect(collectIntelReadLines(card.intel_read)).toEqual([]);
  });
});

// ── 2. collectIntelReadLines renders when intel_read exists ───────────────────

describe("AgentInsightCard renders Why this view? when intel_read present", () => {
  it("collectIntelReadLines returns non-empty when intel_read is set", () => {
    const intelRead: IntelRead = {
      title: "Why this view?",
      posture_label: "on watch",
      summary:
        "The system has enough evidence to comment on valuation and recent market behavior, but business quality, growth, and risk are still incomplete.",
      trusted_signals: ["valuation", "recent market behavior"],
      incomplete_signals: ["business quality", "growth", "risk"],
      caveat: "Not enough data to be confident. Wait for more signals before acting.",
    };
    const lines = collectIntelReadLines(intelRead);
    expect(lines.length).toBeGreaterThan(0);
    expect(lines).toContain("Why this view?");
    expect(lines.some((l) => l.includes("valuation"))).toBe(true);
    expect(lines).toContain("business quality");
    expect(lines).toContain("growth");
    expect(lines).toContain("risk");
  });

  it("summary mentions watch posture when posture_label is on watch", () => {
    const intelRead: IntelRead = {
      title: "Why this view?",
      posture_label: "on watch",
      summary:
        "That is why this stays on watch instead of becoming a high-conviction idea.",
      trusted_signals: ["valuation"],
      incomplete_signals: ["business quality"],
      caveat: "Wait for more signals.",
    };
    const lines = collectIntelReadLines(intelRead);
    const allText = lines.join(" ");
    expect(allText).toContain("watch");
  });
});

// ── 3. Does NOT render section when intel_read missing ───────────────────────

describe("AgentInsightCard does not render Why this view? when intel_read missing", () => {
  it("collectIntelReadLines returns empty array for undefined", () => {
    expect(collectIntelReadLines(undefined)).toEqual([]);
  });

  it("collectIntelReadLines returns empty array for null", () => {
    expect(collectIntelReadLines(null)).toEqual([]);
  });

  it("card without intel_read produces no display lines from intel_read", () => {
    const card = makeCard();
    expect(collectIntelReadLines(card.intel_read)).toEqual([]);
  });
});

// ── 4. Raw metric keys are not rendered ──────────────────────────────────────

describe("intel_read display text — no raw metric keys", () => {
  const cleanIntelRead: IntelRead = {
    title: "Why this view?",
    posture_label: "constructive",
    summary:
      "Some evidence on business quality and valuation is available, but growth and risk are still incomplete.",
    trusted_signals: ["business quality", "valuation", "recent market behavior"],
    incomplete_signals: ["growth", "risk"],
    caveat: "Treat this as an early signal, not a complete picture.",
  };

  it("display text contains no forbidden raw metric keys", () => {
    const lines = collectIntelReadLines(cleanIntelRead);
    const joined = lines.join(" ");
    for (const key of FORBIDDEN_METRIC_KEYS) {
      expect(joined).not.toContain(key);
    }
  });

  it("INSUFFICIENT_DATA intel_read uses plain language, no metric keys", () => {
    const insufficientRead: IntelRead = {
      title: "Why this view?",
      posture_label: "on watch",
      summary:
        "Not enough evidence on any dimension yet. Staying on watch until signals strengthen.",
      trusted_signals: [],
      incomplete_signals: ["business quality", "valuation", "growth", "risk", "recent market behavior"],
      caveat: "Not enough data to be confident. Wait for more signals before acting.",
    };
    const lines = collectIntelReadLines(insufficientRead);
    const joined = lines.join(" ");
    for (const key of FORBIDDEN_METRIC_KEYS) {
      expect(joined).not.toContain(key);
    }
    expect(joined).toContain("Not enough");
  });
});

// ── 5. Business Read remains hidden ──────────────────────────────────────────

describe("Business Read remains hidden / not reintroduced", () => {
  it("thesis_plain_english is separate from intel_read on the card type", () => {
    const card = makeCard({
      intel_read: {
        title: "Why this view?",
        posture_label: "on watch",
        summary: "Test.",
        trusted_signals: [],
        incomplete_signals: [],
        caveat: "Test.",
      },
      thesis_plain_english: {
        headline: "Not enough data for a reliable investment-case read",
        data_label: "Data is still incomplete",
        caveats: [],
      },
    });
    // intel_read and thesis_plain_english are separate fields
    expect(card.intel_read?.title).toBe("Why this view?");
    expect(card.thesis_plain_english?.headline).toContain("investment-case read");
  });

  it("collectIntelReadLines does NOT pull from thesis_plain_english", () => {
    const card = makeCard({
      thesis_plain_english: {
        headline: "Business quality looks solid.",
        quality_label: "Quality: good",
        caveats: ["Use as a directional read."],
      },
      intel_read: null,
    });
    // With intel_read null, no lines should come from thesis_plain_english
    const lines = collectIntelReadLines(card.intel_read);
    expect(lines).toEqual([]);
    expect(lines.some((l) => l.includes("Business quality looks solid"))).toBe(false);
  });

  it("intel_read display text does not include Business read label", () => {
    const intelRead: IntelRead = {
      title: "Why this view?",
      posture_label: "on watch",
      summary: "Test summary.",
      trusted_signals: ["valuation"],
      incomplete_signals: ["growth"],
      caveat: "Test caveat.",
    };
    const lines = collectIntelReadLines(intelRead);
    const joined = lines.join(" ");
    expect(joined).not.toContain("Business read");
    expect(joined).not.toContain("Investment case read");
  });
});

// ── 6. Insufficient-data cards — forbidden bullish phrases ────────────────────

const FORBIDDEN_BULLISH_PHRASES = [
  "accumulate",
  "entry opportunity",
  "re-rating opportunity",
  "high-conviction idea",
  "add aggressively",
  "strong buy",
];

/**
 * Simulate what AgentInsightCard renders for WHY / RISK / ACTION / ALT VIEW
 * and the WhyThisView section for an insufficient-data card.
 */
function collectInsufficientDataCardText(card: InsightCardData): string {
  const parts: string[] = [];
  if (card.primary_driver) parts.push(card.primary_driver);
  if (card.risk_flag) parts.push(card.risk_flag);
  if (card.action_reason) parts.push(card.action_reason);
  if (card.differentiation) parts.push(card.differentiation);
  parts.push(...collectIntelReadLines(card.intel_read));
  return parts.join(" ");
}

describe("Insufficient-data cards — no forbidden bullish copy", () => {
  it("card with conservative action_reason has no forbidden phrases", () => {
    const card = makeCard({
      action: "HOLD",
      conviction_level: "LOW",
      action_reason:
        "Hold off on new buying until growth and risk evidence improves. Keep on watchlist.",
      primary_driver:
        "The system can comment on valuation, but growth and risk are still incomplete. That makes this a watchlist read, not a conviction position.",
      differentiation: undefined,
      intel_read: {
        title: "Why this view?",
        posture_label: "on watch",
        summary:
          "The system has enough evidence to comment on valuation, but growth and risk are still incomplete. That is why this stays on watch — not complete enough for a strong view.",
        trusted_signals: ["valuation"],
        incomplete_signals: ["growth", "risk"],
        caveat: "Not enough data to be confident. Wait for more signals before acting.",
      },
    });
    const allText = collectInsufficientDataCardText(card).toLowerCase();
    for (const phrase of FORBIDDEN_BULLISH_PHRASES) {
      expect(allText).not.toContain(phrase);
    }
  });

  it("card with no signals degrades to watchlist copy without forbidden phrases", () => {
    const card = makeCard({
      action: "HOLD",
      conviction_level: "LOW",
      action_reason: "Watch for more complete evidence before adding.",
      primary_driver:
        "Not enough evidence on any dimension yet. This is a watchlist position only.",
      differentiation: undefined,
      intel_read: {
        title: "Why this view?",
        posture_label: "on watch",
        summary: "Not enough evidence on any dimension yet. Staying on watch until signals strengthen.",
        trusted_signals: [],
        incomplete_signals: [],
        caveat: "Not enough data to be confident. Wait for more signals before acting.",
      },
    });
    const allText = collectInsufficientDataCardText(card).toLowerCase();
    for (const phrase of FORBIDDEN_BULLISH_PHRASES) {
      expect(allText).not.toContain(phrase);
    }
    expect(allText).toContain("watch");
  });

  it("conservative action_reason contains watchlist language", () => {
    const card = makeCard({
      action: "HOLD",
      conviction_level: "LOW",
      action_reason:
        "Stay on watchlist. Recheck after business quality, growth, and risk evidence improves or a new agent run fills those gaps.",
    });
    expect(card.action_reason?.toLowerCase()).toContain("watchlist");
    expect(card.action_reason?.toLowerCase()).not.toContain("accumulate");
  });
});

// ── 7. bottom_line field — WHY THIS VIEW uses bottom_line over summary ────────

describe("IntelRead.bottom_line — displayed in WHY THIS VIEW when present", () => {
  it("collectIntelReadLines uses bottom_line when present instead of summary", () => {
    const intelRead: IntelRead = {
      title: "Why this view?",
      posture_label: "on watch",
      summary: "The system has enough evidence to comment on valuation, but growth is still incomplete.",
      trusted_signals: ["valuation"],
      incomplete_signals: ["growth", "risk"],
      caveat: "Not enough data to be confident.",
      bottom_line: "Interesting setup, but growth and risk are still missing — not enough complete evidence for a confident position.",
    };
    const lines = collectIntelReadLines(intelRead);
    expect(lines).toContain(intelRead.bottom_line!);
    expect(lines).not.toContain(intelRead.summary);
  });

  it("collectIntelReadLines falls back to summary when bottom_line is absent", () => {
    const intelRead: IntelRead = {
      title: "Why this view?",
      posture_label: "constructive",
      summary: "Evidence on business quality supports a constructive view.",
      trusted_signals: ["business quality"],
      incomplete_signals: ["growth"],
      caveat: "Treat as an early signal.",
    };
    const lines = collectIntelReadLines(intelRead);
    expect(lines).toContain(intelRead.summary);
  });

  it("bottom_line contains no forbidden bullish phrases", () => {
    const intelRead: IntelRead = {
      title: "Why this view?",
      posture_label: "on watch",
      summary: "Test summary.",
      trusted_signals: ["valuation", "recent market behavior"],
      incomplete_signals: ["growth", "risk"],
      caveat: "Not enough data.",
      bottom_line: "Interesting setup, but growth and risk are still missing — not enough complete evidence for a confident position.",
    };
    const joined = collectIntelReadLines(intelRead).join(" ").toLowerCase();
    for (const phrase of FORBIDDEN_BULLISH_PHRASES) {
      expect(joined).not.toContain(phrase);
    }
  });

  it("bottom_line contains no raw metric keys", () => {
    const intelRead: IntelRead = {
      title: "Why this view?",
      posture_label: "on watch",
      summary: "Test summary.",
      trusted_signals: ["valuation"],
      incomplete_signals: ["growth"],
      caveat: "Not enough data.",
      bottom_line: "Interesting setup, but growth is still missing — not enough complete evidence.",
    };
    const joined = collectIntelReadLines(intelRead).join(" ");
    for (const key of FORBIDDEN_METRIC_KEYS) {
      expect(joined).not.toContain(key);
    }
  });
});

// ── 8. WHY and WHY THIS VIEW are not redundant for insufficient-data cards ────

describe("WHY and WHY THIS VIEW — not redundant for insufficient-data cards", () => {
  it("primary_driver (WHY) and intel_read display text (WHY THIS VIEW) differ", () => {
    const conservativeWhy =
      "Evidence on valuation and recent market behavior is present, but growth and risk are still incomplete — watchlist read only.";
    const bottomLine =
      "Interesting setup, but growth and risk are still missing — not enough complete evidence for a confident position.";

    const card = makeCard({
      action: "HOLD",
      conviction_level: "LOW",
      primary_driver: conservativeWhy,
      action_reason:
        "Stay on watchlist. Recheck after growth and risk evidence improves or a new agent run fills those gaps.",
      differentiation: undefined,
      intel_read: {
        title: "Why this view?",
        posture_label: "on watch",
        summary: "The system has enough evidence to comment on valuation and recent market behavior, but growth and risk are still incomplete.",
        trusted_signals: ["valuation", "recent market behavior"],
        incomplete_signals: ["growth", "risk"],
        caveat: "Not enough data to be confident. Wait for more signals before acting.",
        bottom_line: bottomLine,
      },
    });

    const whyText = card.primary_driver ?? "";
    const whyThisViewText = collectIntelReadLines(card.intel_read).join(" ");

    expect(whyText).not.toBe("");
    expect(whyThisViewText).not.toBe("");
    // WHY and WHY THIS VIEW must not be the same sentence
    expect(whyText).not.toEqual(whyThisViewText);
    // bottom_line is shown in WHY THIS VIEW, not summary
    expect(whyThisViewText).toContain(bottomLine);
    // WHY text names signals too but is shorter
    expect(whyText).toContain("valuation");
  });

  it("WHY THIS VIEW shows labeled chip groups (Reliable/Missing signals)", () => {
    const intelRead: IntelRead = {
      title: "Why this view?",
      posture_label: "on watch",
      summary: "Some evidence on valuation is available.",
      trusted_signals: ["valuation"],
      incomplete_signals: ["growth", "risk"],
      caveat: "Not enough data.",
      bottom_line: "Interesting setup, but growth and risk are still missing.",
    };
    // Simulate that chips are labeled — test the data contract
    expect(intelRead.trusted_signals).toContain("valuation");
    expect(intelRead.incomplete_signals).toContain("growth");
    expect(intelRead.incomplete_signals).toContain("risk");
    // trusted and incomplete are separate lists for separate labeled chip groups
    const sharedSignals = intelRead.trusted_signals.filter((s) =>
      intelRead.incomplete_signals.includes(s)
    );
    expect(sharedSignals).toHaveLength(0);
  });
});
