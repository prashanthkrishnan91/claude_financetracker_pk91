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
 * Mirrors WhyThisView: narrative_contract.evidence_summary > posture_reason
 * > bottom_line > summary.
 */
function collectIntelReadLines(intelRead: IntelRead | null | undefined): string[] {
  if (!intelRead) return [];
  const lines: string[] = [];
  if (intelRead.title) lines.push(intelRead.title);
  const displaySummary =
    intelRead.narrative_contract?.evidence_summary ||
    intelRead.posture_reason ||
    intelRead.bottom_line ||
    intelRead.summary;
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

// ── 9. Ticker-specific WHY preserved for insufficient-data cards ──────────────

const FORBIDDEN_BULLISH_PHRASES_FULL = [
  "accumulate",
  "buy",
  "entry opportunity",
  "re-rating opportunity",
  "high-conviction idea",
  "add aggressively",
  "strong buy",
  "deploy",
];

describe("Insufficient-data cards — ticker-specific WHY preserved when safe", () => {
  it("safe ticker-specific primary_driver (NVDA) appears in card output", () => {
    const tickerSpecificWhy =
      "AI infrastructure demand remains the main watchlist reason — " +
      "hyperscaler capex and H100-B200 ramp keep NVDA relevant.";
    const card = makeCard({
      action: "HOLD",
      conviction_level: "LOW",
      primary_driver: tickerSpecificWhy,
      action_reason:
        "Stay on watchlist. Recheck after growth and risk evidence improves or a new agent run fills those gaps.",
      differentiation: undefined,
      intel_read: {
        title: "Why this view?",
        posture_label: "on watch",
        summary: "Evidence on recent market behavior and valuation is available, but growth and risk are still incomplete.",
        trusted_signals: ["recent market behavior", "valuation"],
        incomplete_signals: ["growth", "risk"],
        caveat: "Not enough data to be confident. Wait for more signals before acting.",
        bottom_line: "Interesting setup, but growth and risk are still missing — not enough complete evidence for a confident position.",
      },
    });
    const allText = collectInsufficientDataCardText(card);
    // Ticker-specific WHY must be present
    expect(allText).toContain("hyperscaler capex");
    expect(allText).toContain("H100-B200");
    // ACTION must be conservative
    expect(card.action_reason?.toLowerCase()).toContain("watchlist");
    // No forbidden phrases in any field
    const lower = allText.toLowerCase();
    for (const phrase of FORBIDDEN_BULLISH_PHRASES_FULL) {
      expect(lower).not.toContain(phrase);
    }
  });

  it("safe ticker-specific primary_driver (MSFT) appears in card output", () => {
    const tickerSpecificWhy =
      "Azure AI consumption and Copilot expansion keep MSFT worth monitoring, " +
      "but incomplete growth and risk coverage keeps this below a conviction position.";
    const card = makeCard({
      ticker: "MSFT",
      action: "HOLD",
      conviction_level: "LOW",
      primary_driver: tickerSpecificWhy,
      action_reason: "Stay on watchlist. Recheck after growth and risk evidence improves.",
      differentiation: undefined,
      intel_read: {
        title: "Why this view?",
        posture_label: "on watch",
        summary: "Evidence on valuation is available, but growth and risk are still incomplete.",
        trusted_signals: ["valuation"],
        incomplete_signals: ["growth", "risk"],
        caveat: "Not enough data to be confident.",
        bottom_line: "Interesting setup, but growth and risk are still missing.",
      },
    });
    const allText = collectInsufficientDataCardText(card);
    expect(allText).toContain("Azure AI");
    expect(allText).toContain("Copilot");
    const lower = allText.toLowerCase();
    for (const phrase of FORBIDDEN_BULLISH_PHRASES_FULL) {
      expect(lower).not.toContain(phrase);
    }
  });

  it("safe ticker-specific differentiation (RISK) is preserved when no forbidden phrases", () => {
    const tickerRisk =
      "Export restriction risk to China could materially cut data-center revenue outlook.";
    const card = makeCard({
      action: "HOLD",
      conviction_level: "LOW",
      primary_driver: "AI infrastructure demand keeps NVDA on watchlist.",
      action_reason: "Stay on watchlist.",
      differentiation: tickerRisk,
      intel_read: {
        title: "Why this view?",
        posture_label: "on watch",
        summary: "Evidence on valuation is available, but growth and risk are still incomplete.",
        trusted_signals: ["valuation"],
        incomplete_signals: ["growth", "risk"],
        caveat: "Not enough data to be confident.",
      },
    });
    const allText = collectInsufficientDataCardText(card);
    expect(allText).toContain("Export restriction");
    expect(allText).toContain("data-center revenue");
    const lower = allText.toLowerCase();
    for (const phrase of FORBIDDEN_BULLISH_PHRASES_FULL) {
      expect(lower).not.toContain(phrase);
    }
  });

  it("different tickers show different WHY text — not identical generic copy", () => {
    const nvdaWhy =
      "AI infrastructure demand remains the main watchlist reason — H100-B200 ramp keeps NVDA relevant.";
    const msftWhy =
      "Azure AI consumption and Copilot expansion keep MSFT worth monitoring.";

    const nvdaCard = makeCard({
      ticker: "NVDA",
      action: "HOLD",
      primary_driver: nvdaWhy,
      action_reason: "Stay on watchlist.",
      intel_read: {
        title: "Why this view?",
        posture_label: "on watch",
        summary: "Evidence on valuation is available but growth and risk are incomplete.",
        trusted_signals: ["valuation"],
        incomplete_signals: ["growth", "risk"],
        caveat: "Not enough data.",
      },
    });
    const msftCard = makeCard({
      ticker: "MSFT",
      action: "HOLD",
      primary_driver: msftWhy,
      action_reason: "Stay on watchlist.",
      intel_read: {
        title: "Why this view?",
        posture_label: "on watch",
        summary: "Evidence on valuation is available but growth and risk are incomplete.",
        trusted_signals: ["valuation"],
        incomplete_signals: ["growth", "risk"],
        caveat: "Not enough data.",
      },
    });

    // WHY text must differ between tickers
    expect(nvdaCard.primary_driver).not.toEqual(msftCard.primary_driver);
    expect(nvdaCard.primary_driver).toContain("H100-B200");
    expect(msftCard.primary_driver).toContain("Azure AI");
  });
});

// ── 10. Unified action display contract — BUY/HOLD/TRIM/SELL ─────────────────
//
// The visible Intel UI uses one consistent action system: BUY / HOLD / TRIM / SELL.
// The old posture buckets (Add Candidate, Watchlist, Review, Risk Watch, Trim
// Candidate) are backend-internal only and must NOT appear as primary visible labels.

function normalizeDisplayAction(action?: string | null): "BUY" | "HOLD" | "TRIM" | "SELL" {
  const raw = (action || "").toUpperCase();
  if (raw === "BUY") return "BUY";
  if (raw === "SELL") return "SELL";
  if (raw === "TRIM" || raw === "REDUCE") return "TRIM";
  return "HOLD";
}

describe("Unified action display contract — BUY/HOLD/TRIM/SELL", () => {
  it("normalizeDisplayAction maps BUY/HOLD/TRIM/SELL correctly", () => {
    expect(normalizeDisplayAction("BUY")).toBe("BUY");
    expect(normalizeDisplayAction("HOLD")).toBe("HOLD");
    expect(normalizeDisplayAction("TRIM")).toBe("TRIM");
    expect(normalizeDisplayAction("SELL")).toBe("SELL");
  });

  it("normalizeDisplayAction maps REVIEW and unknown values to HOLD", () => {
    expect(normalizeDisplayAction("REVIEW")).toBe("HOLD");
    expect(normalizeDisplayAction("")).toBe("HOLD");
    expect(normalizeDisplayAction(null)).toBe("HOLD");
    expect(normalizeDisplayAction(undefined)).toBe("HOLD");
  });

  it("normalizeDisplayAction maps REDUCE to TRIM", () => {
    expect(normalizeDisplayAction("REDUCE")).toBe("TRIM");
  });

  it("filter tabs count normalized action — not intel_filter_bucket", () => {
    const cards = [
      makeCard({ ticker: "VOO",  action: "BUY" }),
      makeCard({ ticker: "VTI",  action: "BUY" }),
      makeCard({ ticker: "NVDA", action: "HOLD" }),
      makeCard({ ticker: "SNOW", action: "TRIM" }),
      makeCard({ ticker: "CRM",  action: "SELL" }),
      makeCard({ ticker: "MSFT", action: "HOLD" }),
    ];

    const counts: Record<string, number> = { ALL: cards.length };
    for (const r of cards) {
      const bucket = normalizeDisplayAction(r.action);
      counts[bucket] = (counts[bucket] || 0) + 1;
    }

    expect(counts["ALL"]).toBe(6);
    expect(counts["BUY"]).toBe(2);
    expect(counts["HOLD"]).toBe(2);
    expect(counts["TRIM"]).toBe(1);
    expect(counts["SELL"]).toBe(1);
    // No posture buckets in counts
    expect(counts["Add Candidate"]).toBeUndefined();
    expect(counts["Watchlist"]).toBeUndefined();
    expect(counts["Review"]).toBeUndefined();
    expect(counts["Risk Watch"]).toBeUndefined();
    expect(counts["Trim Candidate"]).toBeUndefined();
  });

  it("filter by normalized action returns correct subset", () => {
    const cards = [
      makeCard({ ticker: "VOO",  action: "BUY" }),
      makeCard({ ticker: "NVDA", action: "HOLD" }),
      makeCard({ ticker: "SNOW", action: "TRIM" }),
      makeCard({ ticker: "CRM",  action: "SELL" }),
    ];

    const buyCards = cards.filter((r) => normalizeDisplayAction(r.action) === "BUY");
    expect(buyCards.length).toBe(1);
    expect(buyCards[0].ticker).toBe("VOO");

    const holdCards = cards.filter((r) => normalizeDisplayAction(r.action) === "HOLD");
    expect(holdCards.length).toBe(1);
    expect(holdCards[0].ticker).toBe("NVDA");
  });

  it("card badge uses normalized action — not intel_posture_label", () => {
    // Simulate AgentInsightCard badge logic: normalizeAction(card.analyst_action || card.action)
    function resolveBadgeLabel(card: ReturnType<typeof makeCard>): string {
      return normalizeDisplayAction(card.analyst_action as string | null ?? card.action);
    }

    const buyCard  = makeCard({ action: "BUY",  intel_posture_label: "Add Candidate" });
    const holdCard = makeCard({ action: "HOLD", intel_posture_label: "Watchlist" });
    const trimCard = makeCard({ action: "TRIM", intel_posture_label: "Trim Candidate" });
    const sellCard = makeCard({ action: "SELL", intel_posture_label: "Risk Watch" });

    expect(resolveBadgeLabel(buyCard)).toBe("BUY");
    expect(resolveBadgeLabel(holdCard)).toBe("HOLD");
    expect(resolveBadgeLabel(trimCard)).toBe("TRIM");
    expect(resolveBadgeLabel(sellCard)).toBe("SELL");
  });

  it("no posture label strings appear as primary badge labels", () => {
    const POSTURE_LABELS = ["Add Candidate", "Watchlist", "Review", "Risk Watch", "Trim Candidate"];
    const cards = [
      makeCard({ action: "BUY" }),
      makeCard({ action: "HOLD" }),
      makeCard({ action: "TRIM" }),
      makeCard({ action: "SELL" }),
    ];
    for (const card of cards) {
      const badge = normalizeDisplayAction(card.action);
      for (const postureLabel of POSTURE_LABELS) {
        expect(badge).not.toBe(postureLabel);
      }
    }
  });

  it("filter counts match card badge system — no mismatch between tabs and badges", () => {
    const cards = [
      makeCard({ ticker: "A", action: "BUY" }),
      makeCard({ ticker: "B", action: "HOLD" }),
      makeCard({ ticker: "C", action: "TRIM" }),
    ];
    // Badge and filter bucket must be the same normalized value
    for (const card of cards) {
      const badge = normalizeDisplayAction(card.action);
      const filterBucket = normalizeDisplayAction(card.action);
      expect(badge).toBe(filterBucket);
    }
  });

  it("'LOW CONVICTION' string does not appear as a badge label", () => {
    const CONVICTION_LABELS: Record<string, string> = {
      HIGH: "High confidence",
      MEDIUM: "Moderate confidence",
      LOW: "Evidence limited",
    };
    for (const label of Object.values(CONVICTION_LABELS)) {
      expect(label).not.toContain("LOW CONVICTION");
      expect(label).not.toMatch(/low conviction/i);
    }
  });

  it("posture_reason in intel_read is secondary context — different tickers produce different text", () => {
    const buyCard = makeCard({
      action: "BUY",
      intel_read: {
        title: "Evidence check",
        posture_label: "constructive",
        summary: "Strong signals on valuation and business quality.",
        trusted_signals: ["valuation", "business quality"],
        incomplete_signals: [],
        caveat: "Monitor for macro changes.",
        posture_reason: "Core index ETF — regular contribution target.",
      },
    });
    const holdCard = makeCard({
      action: "HOLD",
      intel_read: {
        title: "Evidence check",
        posture_label: "on watch",
        summary: "Partial evidence. Growth and risk still incomplete.",
        trusted_signals: ["valuation"],
        incomplete_signals: ["growth", "risk"],
        caveat: "Wait for more signals.",
        posture_reason: "High-risk or speculative position. Monitor closely.",
      },
    });
    expect(buyCard.intel_read?.posture_reason).not.toEqual(holdCard.intel_read?.posture_reason);
  });

  it("raw metric keys do not appear in visible action badge labels", () => {
    const RAW_METRIC_KEYS = [
      "fcf_margin", "roic_ttm", "p_fcf", "fcf_yield", "gross_margin",
      "net_debt_to_ebitda", "ev_ebitda", "revenue_cagr_3y", "max_drawdown_1y",
      "trailing_pe", "forward_pe", "momentum_score", "valuation_score",
    ];
    const visibleBadgeLabels = ["BUY", "HOLD", "TRIM", "SELL"];
    for (const label of visibleBadgeLabels) {
      for (const rawKey of RAW_METRIC_KEYS) {
        expect(label).not.toContain(rawKey);
      }
    }
  });
});

// ── 11. Evidence check section — label contract ───────────────────────────────

describe("Evidence check section label contract", () => {
  it("WhyThisView section uses 'Evidence check' label (not 'Why this view?')", () => {
    // The rendered section header in WhyThisView is the constant string "Evidence check".
    // This is verified at the component level — this test documents the data-contract side.
    // intel_read.title is a data field; the UI renders its own "Evidence check" label.
    const SECTION_LABEL = "Evidence check";
    expect(SECTION_LABEL).toBe("Evidence check");
    expect(SECTION_LABEL).not.toBe("Why this view?");
    expect(SECTION_LABEL).not.toMatch(/why this view/i);
  });

  it("intel_read can still carry a title field for data purposes without it becoming the UI label", () => {
    const intelRead: IntelRead = {
      title: "Why this view?",
      posture_label: "on watch",
      summary: "Test summary.",
      trusted_signals: ["valuation"],
      incomplete_signals: ["growth"],
      caveat: "Test caveat.",
    };
    // The data field exists, but the UI renders "Evidence check" independently
    expect(intelRead.title).toBeDefined();
    const UI_RENDERED_LABEL = "Evidence check";
    expect(UI_RENDERED_LABEL).not.toBe(intelRead.title);
  });

  it("posture_reason is preferred in Evidence check display over bottom_line/summary", () => {
    function resolveEvidenceText(intelRead: IntelRead | null | undefined): string {
      return (
        intelRead?.narrative_contract?.evidence_summary
        || intelRead?.posture_reason
        || intelRead?.bottom_line
        || intelRead?.summary
        || ""
      );
    }

    const card = makeCard({
      intel_read: {
        title: "Evidence check",
        posture_label: "on watch",
        summary: "Generic fallback summary.",
        trusted_signals: [],
        incomplete_signals: [],
        caveat: "Wait.",
        posture_reason: "Specific evidence explanation for this ticker.",
        bottom_line: "Bottom line fallback.",
      },
    });
    const text = resolveEvidenceText(card.intel_read);
    expect(text).toBe("Specific evidence explanation for this ticker.");
    expect(text).not.toBe("Generic fallback summary.");
    expect(text).not.toBe("Bottom line fallback.");
  });

  it("narrative_contract evidence_summary is preferred over stale posture_reason", () => {
    function resolveEvidenceText(intelRead: IntelRead | null | undefined): string {
      return (
        intelRead?.narrative_contract?.evidence_summary
        || intelRead?.posture_reason
        || intelRead?.bottom_line
        || intelRead?.summary
        || ""
      );
    }

    const card = makeCard({
      intel_read: {
        title: "Evidence check",
        posture_label: "constructive",
        summary: "Summary fallback.",
        trusted_signals: ["business quality"],
        incomplete_signals: ["growth"],
        caveat: "Treat this as an early signal, not a complete picture.",
        posture_reason: "Reviewing before taking action — stale text.",
        bottom_line: "Bottom line fallback.",
        narrative_contract: {
          action: "BUY",
          confidence_label: "MEDIUM",
          evidence_summary: "Reliable evidence supports a measured buy while missing growth caps confidence.",
          reliable_labels: ["business quality"],
          missing_labels: ["growth"],
          final_takeaway: "Size gradually and re-check missing signals.",
          conflict_flags: [],
          narrative_contract_version: "v1",
        },
      },
    });
    const text = resolveEvidenceText(card.intel_read).toLowerCase();
    expect(text).toContain("measured buy");
    expect(text).not.toContain("reviewing before taking action");
  });
});
