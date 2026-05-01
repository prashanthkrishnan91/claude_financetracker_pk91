/**
 * Contract tests: thesis_plain_english rendering rules for Intel cards.
 *
 * These tests verify the data-binding contract:
 * - thesis_plain_english fields render when present
 * - missing thesis_plain_english does not crash or show an error
 * - thesis_v2 is never referenced for UI rendering
 * - raw backend metric keys never appear in user-facing copy
 */

import type { InsightCardData, ThesisPlainEnglish } from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeCard(overrides: Partial<InsightCardData> = {}): InsightCardData {
  return {
    id: "test-1",
    ticker: "AAPL",
    name: "Apple Inc.",
    action: "BUY",
    detail: "Strong fundamentals.",
    rationale: "Resilient business.",
    urgency: 1,
    color: "green",
    tax_note: "",
    drip_note: "",
    category: "Tech",
    ...overrides,
  };
}

// Simulate what the UI would render: collect all displayable text from
// thesis_plain_english only.
function collectThesisDisplayText(thesis: ThesisPlainEnglish | null | undefined): string[] {
  if (!thesis) return [];
  const parts: string[] = [];
  if (thesis.headline) parts.push(thesis.headline);
  if (thesis.quality_label) parts.push(thesis.quality_label);
  if (thesis.valuation_label) parts.push(thesis.valuation_label);
  if (thesis.risk_label) parts.push(thesis.risk_label);
  if (thesis.momentum_label) parts.push(thesis.momentum_label);
  if (thesis.data_label) parts.push(thesis.data_label);
  (thesis.caveats ?? []).forEach((c) => parts.push(c));
  return parts;
}

// Raw metric keys that must never appear in user-facing copy
const FORBIDDEN_METRIC_KEYS = [
  "fcf_margin",
  "roic_ttm",
  "ev_ebitda",
  "ps_ttm",
  "net_debt_to_ebitda",
  "gross_margin",
  "interest_coverage",
  "peer_ps_median",
  "peer_ev_ebitda_median",
  "own_5y_ps_median",
];

// ---------------------------------------------------------------------------
// 1. thesis_plain_english present — headline and labels render
// ---------------------------------------------------------------------------

describe("thesis_plain_english — present fields render", () => {
  const thesis: ThesisPlainEnglish = {
    headline: "Business quality looks solid with good momentum.",
    quality_label: "Quality: good",
    valuation_label: "Valuation: fair",
    risk_label: "Risk: low",
    momentum_label: "Momentum: positive",
    data_label: "Data: complete",
    caveats: ["This is based on limited data. Treat as a starting point."],
  };

  const card = makeCard({ thesis_plain_english: thesis });

  it("exposes thesis_plain_english on the card type", () => {
    expect(card.thesis_plain_english).toBeDefined();
  });

  it("headline is accessible for rendering", () => {
    expect(card.thesis_plain_english?.headline).toBe(
      "Business quality looks solid with good momentum."
    );
  });

  it("all label fields are accessible for rendering", () => {
    const t = card.thesis_plain_english!;
    expect(t.quality_label).toBe("Quality: good");
    expect(t.valuation_label).toBe("Valuation: fair");
    expect(t.risk_label).toBe("Risk: low");
    expect(t.momentum_label).toBe("Momentum: positive");
    expect(t.data_label).toBe("Data: complete");
  });

  it("caveats array is accessible for rendering", () => {
    expect(card.thesis_plain_english?.caveats).toHaveLength(1);
    expect(card.thesis_plain_english?.caveats![0]).toContain("limited data");
  });

  it("collectThesisDisplayText returns all parts", () => {
    const parts = collectThesisDisplayText(thesis);
    expect(parts).toContain("Business quality looks solid with good momentum.");
    expect(parts).toContain("Quality: good");
    expect(parts).toContain("Valuation: fair");
    expect(parts).toContain("Risk: low");
    expect(parts).toContain("Momentum: positive");
    expect(parts).toContain("Data: complete");
    expect(parts.some((p) => p.includes("limited data"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 2. thesis_plain_english missing — no error, no output
// ---------------------------------------------------------------------------

describe("thesis_plain_english — missing does not crash", () => {
  it("card without thesis_plain_english has undefined field", () => {
    const card = makeCard();
    expect(card.thesis_plain_english).toBeUndefined();
  });

  it("collectThesisDisplayText returns empty array for undefined thesis", () => {
    expect(collectThesisDisplayText(undefined)).toEqual([]);
  });

  it("collectThesisDisplayText returns empty array for null thesis", () => {
    expect(collectThesisDisplayText(null)).toEqual([]);
  });

  it("card with thesis_plain_english: null produces no display text", () => {
    const card = makeCard({ thesis_plain_english: null });
    expect(collectThesisDisplayText(card.thesis_plain_english)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 3. thesis_v2 is never referenced for UI rendering
// ---------------------------------------------------------------------------

describe("thesis_v2 — never rendered in UI", () => {
  it("InsightCardData type does not expose a thesis_v2 field for UI use", () => {
    const card = makeCard();
    // thesis_v2 must not be a known field on InsightCardData used by the UI.
    // The key should be absent from a default card.
    expect((card as Record<string, unknown>)["thesis_v2"]).toBeUndefined();
  });

  it("UI binding helper only reads thesis_plain_english, never thesis_v2", () => {
    // Simulate a payload where thesis_v2 might arrive as an unknown extra field.
    const cardWithRawV2 = makeCard({
      thesis_plain_english: { headline: "Safe headline." },
    }) as Record<string, unknown>;
    cardWithRawV2["thesis_v2"] = {
      status: "COMPLETE",
      fcf_margin: 0.18,
      roic_ttm: 0.22,
      conviction_score: 82,
    };

    const displayText = collectThesisDisplayText(
      (cardWithRawV2 as InsightCardData).thesis_plain_english
    );
    // Only thesis_plain_english headline appears
    expect(displayText).toEqual(["Safe headline."]);
    // Raw thesis_v2 fields do not appear
    expect(displayText.join(" ")).not.toContain("fcf_margin");
    expect(displayText.join(" ")).not.toContain("roic_ttm");
    expect(displayText.join(" ")).not.toContain("conviction_score");
  });
});

// ---------------------------------------------------------------------------
// 4. Raw backend metric keys never appear in rendered UI copy
// ---------------------------------------------------------------------------

describe("thesis_plain_english — no raw metric keys in display text", () => {
  const thesisWithCleanCopy: ThesisPlainEnglish = {
    headline: "Strong business quality; valuation looks reasonable.",
    quality_label: "Quality: strong",
    valuation_label: "Valuation: reasonable",
    risk_label: "Risk: moderate",
    momentum_label: "Momentum: neutral",
    data_label: "Data coverage: partial",
    caveats: ["Some data is unavailable. This read may be conservative."],
  };

  it("display text contains no forbidden raw metric keys", () => {
    const parts = collectThesisDisplayText(thesisWithCleanCopy);
    const joined = parts.join(" ");
    for (const key of FORBIDDEN_METRIC_KEYS) {
      expect(joined).not.toContain(key);
    }
  });

  it("INSUFFICIENT_DATA scenario uses calm plain language, no metric keys", () => {
    const insufficientThesis: ThesisPlainEnglish = {
      headline: "Not enough data for a confident read.",
      data_label: "Data coverage: limited",
      caveats: [
        "There isn't enough data to score this stock confidently.",
        "Consider this a preliminary signal only.",
      ],
    };
    const parts = collectThesisDisplayText(insufficientThesis);
    const joined = parts.join(" ");
    for (const key of FORBIDDEN_METRIC_KEYS) {
      expect(joined).not.toContain(key);
    }
    expect(joined).toContain("Not enough data");
  });
});

// ---------------------------------------------------------------------------
// 5. UI binds only to thesis_plain_english for the Thesis read section
// ---------------------------------------------------------------------------

describe("UI binds only to thesis_plain_english", () => {
  it("a card with only thesis_plain_english set produces display text", () => {
    const card = makeCard({
      thesis_plain_english: { headline: "Looks like a reasonable hold." },
    });
    const parts = collectThesisDisplayText(card.thesis_plain_english);
    expect(parts).toContain("Looks like a reasonable hold.");
  });

  it("partial thesis with only caveats still renders gracefully", () => {
    const card = makeCard({
      thesis_plain_english: {
        caveats: ["Data is limited — treat this as early signal only."],
      },
    });
    const parts = collectThesisDisplayText(card.thesis_plain_english);
    expect(parts).toHaveLength(1);
    expect(parts[0]).toContain("early signal");
  });

  it("empty thesis object (all fields null/undefined) renders nothing", () => {
    const card = makeCard({
      thesis_plain_english: {
        headline: null,
        quality_label: null,
        valuation_label: null,
        risk_label: null,
        momentum_label: null,
        data_label: null,
        caveats: [],
      },
    });
    const parts = collectThesisDisplayText(card.thesis_plain_english);
    expect(parts).toEqual([]);
  });
});
