/**
 * Stage 7B — IntelV3Drawer clarity contracts.
 *
 * Tests at the view-model (helper function) layer — no React rendering required.
 * Covers:
 * - deduplicateTexts: identical text not repeated across sections
 * - buildWhyActionExplanation: decision-specific, no raw keys, BTC/XRP-style blocked
 * - buildSupportingEvidenceSentences: usable lanes show as supporting, missing/suppressed do not
 * - buildIncompleteEvidenceSentences: incomplete lanes clearly labeled, not shown as supporting
 * - COMING_LATER_CANONICAL_CAPTION not present in any new helper output
 * - Raw metric keys do not appear in any helper output
 * - BUY/HOLD/TRIM/SELL labels are the only visible actions mentioned
 */

import {
  deduplicateTexts,
  buildWhyActionExplanation,
  buildSupportingEvidenceSentences,
  buildIncompleteEvidenceSentences,
  RAW_KEYS_BANNED,
  buildSafetyDisplay,
} from "./intel-v3-explanation";
import type { IntelV3EvidenceExplanation } from "./api";
import { COMING_LATER_CANONICAL_CAPTION } from "../components/cards/IntelV3PrimitivesData";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeEx(overrides: Partial<IntelV3EvidenceExplanation> = {}): IntelV3EvidenceExplanation {
  return {
    primary_evidence_status: "LIMITED",
    technical_signals_status: "MISSING",
    sentiment_status: "MISSING",
    conviction_cap_applied: true,
    conviction_cap_reason: "ok_cap_medium",
    safe_for_visible_decision: true,
    safe_for_visible_decision_reason: "limited_fundamentals_no_corroboration_ok_with_cap",
    governance_priority: "p4b_limited_no_corroboration",
    corroboration_gap: true,
    action_blocks: [],
    ...overrides,
  };
}

function assertNoRawKeys(text: string) {
  for (const key of RAW_KEYS_BANNED) {
    if (text.includes(key)) {
      throw new Error(`Raw key leaked: "${key}" in "${text}"`);
    }
  }
}

function assertNoComingLaterCaption(text: string) {
  if (text.includes(COMING_LATER_CANONICAL_CAPTION) || text.includes("intelligence module is being prepared")) {
    throw new Error(`Stale placeholder text found in: "${text}"`);
  }
}

function assertNoStaleGovernancePlaceholder(text: string) {
  if (text.toLowerCase().includes("will appear here once the evidence governance engine is active")) {
    throw new Error(`Stale governance placeholder found in: "${text}"`);
  }
}

// ── deduplicateTexts ──────────────────────────────────────────────────────────

describe("deduplicateTexts", () => {
  it("removes exact duplicate strings", () => {
    const result = deduplicateTexts(["Same text.", "Same text."]);
    expect(result).toHaveLength(1);
    expect(result[0]).toBe("Same text.");
  });

  it("removes case-insensitive duplicates", () => {
    const result = deduplicateTexts(["Hello world.", "hello world."]);
    expect(result).toHaveLength(1);
  });

  it("removes whitespace-only and null/undefined entries", () => {
    const result = deduplicateTexts([null, undefined, "  ", "Real text."]);
    expect(result).toHaveLength(1);
    expect(result[0]).toBe("Real text.");
  });

  it("preserves distinct texts in order", () => {
    const result = deduplicateTexts(["First.", "Second.", "Third."]);
    expect(result).toEqual(["First.", "Second.", "Third."]);
  });

  it("MSFT-like: thesis text repeated 3 times → shown only once", () => {
    const thesis = "Solid fundamentals and strong recurring revenue.";
    const result = deduplicateTexts([thesis, thesis, thesis]);
    expect(result).toHaveLength(1);
  });

  it("returns empty array for all-null input", () => {
    expect(deduplicateTexts([null, null, undefined])).toHaveLength(0);
  });
});

// ── buildWhyActionExplanation ─────────────────────────────────────────────────

describe("buildWhyActionExplanation", () => {
  const ALL_ACTIONS = ["BUY", "HOLD", "TRIM", "SELL"];

  it("returns a non-empty string for all standard actions", () => {
    for (const action of ALL_ACTIONS) {
      const text = buildWhyActionExplanation(action, null);
      expect(text.length).toBeGreaterThan(0);
    }
  });

  it("BUY without evidence_explanation → mentions positive evidence or justify", () => {
    const text = buildWhyActionExplanation("BUY", null);
    expect(text.toLowerCase()).toMatch(/evidence|positive|justify|adding/);
  });

  it("BUY with limited evidence → mentions conviction cap or corroboration", () => {
    const text = buildWhyActionExplanation("BUY", makeEx({ corroboration_gap: true }));
    expect(text.toLowerCase()).toMatch(/conviction|corroboration|enough/);
  });

  it("BUY with stronger evidence → mentions corroborated or confirms", () => {
    const text = buildWhyActionExplanation(
      "BUY",
      makeEx({ safe_for_visible_decision: true, corroboration_gap: false, primary_evidence_status: "READY", action_blocks: [] })
    );
    expect(text.toLowerCase()).toMatch(/corroborat|signal|enough/);
  });

  it("BTC/XRP-style BUY blocked → mentions quality issues, not raw codes", () => {
    const text = buildWhyActionExplanation(
      "BUY",
      makeEx({ action_blocks: ["buy_blocked_suppressed_evidence"], safe_for_visible_decision: false })
    );
    assertNoRawKeys(text);
    expect(text.toLowerCase()).toMatch(/quality|issue|data/);
  });

  it("HOLD → mentions holding or thesis, no raw keys", () => {
    const text = buildWhyActionExplanation("HOLD", makeEx());
    assertNoRawKeys(text);
    expect(text.toLowerCase()).toMatch(/hold|thesis|weight/);
  });

  it("TRIM → mentions reducing or exposure", () => {
    const text = buildWhyActionExplanation("TRIM", makeEx());
    assertNoRawKeys(text);
    expect(text.toLowerCase()).toMatch(/reduc|exposure|trim/);
  });

  it("SELL → mentions exit or negative signals", () => {
    const text = buildWhyActionExplanation("SELL", makeEx());
    assertNoRawKeys(text);
    expect(text.toLowerCase()).toMatch(/exit|sell|signal|negative/);
  });

  it("no raw keys in any action explanation", () => {
    for (const action of ALL_ACTIONS) {
      assertNoRawKeys(buildWhyActionExplanation(action, null));
      assertNoRawKeys(buildWhyActionExplanation(action, makeEx()));
      assertNoRawKeys(buildWhyActionExplanation(action, makeEx({ action_blocks: ["buy_blocked"] })));
    }
  });

  it("no ComingLater placeholder text in any explanation", () => {
    for (const action of ALL_ACTIONS) {
      const text = buildWhyActionExplanation(action, makeEx());
      assertNoComingLaterCaption(text);
      assertNoStaleGovernancePlaceholder(text);
    }
  });

  it("does not mention WATCH or AVOID (radar-only actions)", () => {
    for (const action of ALL_ACTIONS) {
      const text = buildWhyActionExplanation(action, makeEx());
      expect(text).not.toMatch(/\bWATCH\b|\bAVOID\b/);
    }
  });
});

// ── buildSupportingEvidenceSentences ─────────────────────────────────────────

describe("buildSupportingEvidenceSentences", () => {
  it("READY fundamentals → sentence shown in supporting evidence", () => {
    const sentences = buildSupportingEvidenceSentences(
      makeEx({ primary_evidence_status: "READY" })
    );
    expect(sentences.length).toBeGreaterThan(0);
    expect(sentences.some(s => s.toLowerCase().includes("fundamental"))).toBe(true);
  });

  it("MISSING technicals → NOT shown as supporting evidence", () => {
    const sentences = buildSupportingEvidenceSentences(
      makeEx({ technical_signals_status: "MISSING" })
    );
    const techSentence = sentences.find(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"));
    expect(techSentence).toBeUndefined();
  });

  it("SUPPRESSED sentiment → NOT shown as supporting evidence", () => {
    const sentences = buildSupportingEvidenceSentences(
      makeEx({ sentiment_status: "SUPPRESSED" })
    );
    const sentSentence = sentences.find(s => s.toLowerCase().includes("sentiment") || s.toLowerCase().includes("news"));
    expect(sentSentence).toBeUndefined();
  });

  it("all MISSING → returns empty array", () => {
    const sentences = buildSupportingEvidenceSentences(
      makeEx({
        primary_evidence_status: "MISSING",
        technical_signals_status: "MISSING",
        sentiment_status: "MISSING",
      })
    );
    expect(sentences).toHaveLength(0);
  });

  it("all READY → returns 3 sentences", () => {
    const sentences = buildSupportingEvidenceSentences(
      makeEx({
        primary_evidence_status: "READY",
        technical_signals_status: "READY",
        sentiment_status: "READY",
        action_blocks: [],
      })
    );
    expect(sentences).toHaveLength(3);
  });

  it("no raw keys in any supporting evidence sentence", () => {
    const sentences = buildSupportingEvidenceSentences(makeEx({
      primary_evidence_status: "READY",
      technical_signals_status: "LIMITED",
      sentiment_status: "READY",
    }));
    for (const s of sentences) {
      assertNoRawKeys(s);
    }
  });

  it("no stale placeholder text in supporting sentences", () => {
    const sentences = buildSupportingEvidenceSentences(makeEx({ primary_evidence_status: "READY" }));
    for (const s of sentences) {
      assertNoComingLaterCaption(s);
      assertNoStaleGovernancePlaceholder(s);
    }
  });
});

// ── buildIncompleteEvidenceSentences ──────────────────────────────────────────

describe("buildIncompleteEvidenceSentences", () => {
  it("MISSING technicals → shown in incomplete evidence", () => {
    const sentences = buildIncompleteEvidenceSentences(
      makeEx({ technical_signals_status: "MISSING" })
    );
    const techSentence = sentences.find(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"));
    expect(techSentence).toBeDefined();
  });

  it("SUPPRESSED sentiment → shown in incomplete with 'suppressed' language, not as supporting", () => {
    const sentences = buildIncompleteEvidenceSentences(
      makeEx({ sentiment_status: "SUPPRESSED" })
    );
    const sentSentence = sentences.find(s => s.toLowerCase().includes("sentiment") || s.toLowerCase().includes("news"));
    expect(sentSentence).toBeDefined();
    expect(sentSentence!.toLowerCase()).toMatch(/suppressed|quality|blocked/);
  });

  it("READY fundamentals → NOT shown in incomplete evidence", () => {
    const sentences = buildIncompleteEvidenceSentences(
      makeEx({ primary_evidence_status: "READY" })
    );
    const fundSentence = sentences.find(s => s.toLowerCase().includes("fundamental"));
    expect(fundSentence).toBeUndefined();
  });

  it("all READY → returns empty array", () => {
    const sentences = buildIncompleteEvidenceSentences(
      makeEx({
        primary_evidence_status: "READY",
        technical_signals_status: "READY",
        sentiment_status: "READY",
        action_blocks: [],
      })
    );
    expect(sentences).toHaveLength(0);
  });

  it("all MISSING → returns 3 incomplete sentences", () => {
    const sentences = buildIncompleteEvidenceSentences(
      makeEx({
        primary_evidence_status: "MISSING",
        technical_signals_status: "MISSING",
        sentiment_status: "MISSING",
      })
    );
    expect(sentences).toHaveLength(3);
  });

  it("BTC/XRP-style SUPPRESSED fundamentals → blocked language, no raw keys", () => {
    const sentences = buildIncompleteEvidenceSentences(
      makeEx({
        primary_evidence_status: "SUPPRESSED",
        technical_signals_status: "SUPPRESSED",
        sentiment_status: "SUPPRESSED",
      })
    );
    expect(sentences).toHaveLength(3);
    for (const s of sentences) {
      assertNoRawKeys(s);
      expect(s.toLowerCase()).toMatch(/suppressed|blocked|quality/);
    }
  });

  it("no raw keys in any incomplete sentence", () => {
    const sentences = buildIncompleteEvidenceSentences(
      makeEx({
        primary_evidence_status: "MISSING",
        technical_signals_status: "SUPPRESSED",
        sentiment_status: "INSUFFICIENT",
      })
    );
    for (const s of sentences) {
      assertNoRawKeys(s);
    }
  });

  it("no stale placeholder text in incomplete sentences", () => {
    const sentences = buildIncompleteEvidenceSentences(makeEx({ technical_signals_status: "MISSING" }));
    for (const s of sentences) {
      assertNoComingLaterCaption(s);
      assertNoStaleGovernancePlaceholder(s);
    }
  });
});

// ── Cross-section non-repetition contract ─────────────────────────────────────

describe("section non-repetition contract", () => {
  it("MSFT-like BUY: supporting and incomplete sentences are disjoint (no same sentence in both)", () => {
    const ex = makeEx({
      primary_evidence_status: "LIMITED",
      technical_signals_status: "MISSING",
      sentiment_status: "MISSING",
    });
    const supporting = buildSupportingEvidenceSentences(ex);
    const incomplete = buildIncompleteEvidenceSentences(ex);

    const supportingSet = new Set(supporting.map(s => s.toLowerCase()));
    for (const s of incomplete) {
      expect(supportingSet.has(s.toLowerCase())).toBe(false);
    }
  });

  it("why-action explanation is distinct from supporting sentences", () => {
    const ex = makeEx({ primary_evidence_status: "LIMITED" });
    const why = buildWhyActionExplanation("BUY", ex);
    const supporting = buildSupportingEvidenceSentences(ex);
    for (const s of supporting) {
      expect(s.toLowerCase()).not.toBe(why.toLowerCase());
    }
  });
});

// ── Stage 7C synthetic evidence_explanation (governance_inactive path) ────────

describe("Stage 7C: synthetic evidence_explanation (governance_inactive)", () => {
  // Simulates what _build_synthetic_evidence_explanation produces for a MSFT-like
  // BUY/MEDIUM/PARTIAL card when Stage 6 governance is off (AxisBand.OK).
  const msftSyntheticEx: import("./api").IntelV3EvidenceExplanation = {
    primary_evidence_status: "LIMITED",
    technical_signals_status: "MISSING",
    sentiment_status: "MISSING",
    conviction_cap_applied: true,
    conviction_cap_reason: "ok_cap_medium",
    safe_for_visible_decision: true,
    safe_for_visible_decision_reason: "",
    governance_priority: "governance_inactive",
    corroboration_gap: true,
    action_blocks: [],
  };

  it("MSFT-like BUY/PARTIAL: supporting sentences contain fundamentals reference", () => {
    const sentences = buildSupportingEvidenceSentences(msftSyntheticEx);
    expect(sentences.length).toBeGreaterThan(0);
    expect(sentences.some(s => s.toLowerCase().includes("fundamental"))).toBe(true);
  });

  it("MSFT-like: market/price NOT in supporting (MISSING technical)", () => {
    const sentences = buildSupportingEvidenceSentences(msftSyntheticEx);
    expect(sentences.some(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"))).toBe(false);
  });

  it("MSFT-like: incomplete sentences include market/price reference", () => {
    const sentences = buildIncompleteEvidenceSentences(msftSyntheticEx);
    expect(sentences.some(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"))).toBe(true);
  });

  it("MSFT-like: incomplete sentences include news/sentiment reference", () => {
    const sentences = buildIncompleteEvidenceSentences(msftSyntheticEx);
    expect(sentences.some(s => s.toLowerCase().includes("news") || s.toLowerCase().includes("sentiment"))).toBe(true);
  });

  it("MSFT-like: incomplete sentences exist (partial data → not empty)", () => {
    const sentences = buildIncompleteEvidenceSentences(msftSyntheticEx);
    expect(sentences.length).toBeGreaterThan(0);
  });

  it("MSFT-like BUY conviction cap label mentions partial or moderate", () => {
    const { convictionCapLabel } = require("./intel-v3-explanation");
    const label = convictionCapLabel(msftSyntheticEx.conviction_cap_applied, msftSyntheticEx.conviction_cap_reason);
    expect(label.length).toBeGreaterThan(0);
    expect(label.toLowerCase()).toMatch(/cap|moderate|partial/);
  });

  it("governance_inactive priority → empty string (no noise in UI)", () => {
    const { governancePriorityToExplanation } = require("./intel-v3-explanation");
    expect(governancePriorityToExplanation("governance_inactive")).toBe("");
  });

  it("supporting and incomplete sentences are disjoint for synthetic MSFT explanation", () => {
    const supporting = buildSupportingEvidenceSentences(msftSyntheticEx);
    const incomplete = buildIncompleteEvidenceSentences(msftSyntheticEx);
    const supportingSet = new Set(supporting.map(s => s.toLowerCase()));
    for (const s of incomplete) {
      expect(supportingSet.has(s.toLowerCase())).toBe(false);
    }
  });

  it("no raw keys leak in synthetic explanation helper output", () => {
    for (const s of buildSupportingEvidenceSentences(msftSyntheticEx)) assertNoRawKeys(s);
    for (const s of buildIncompleteEvidenceSentences(msftSyntheticEx)) assertNoRawKeys(s);
    assertNoRawKeys(buildWhyActionExplanation("BUY", msftSyntheticEx));
  });

  it("safety display for synthetic MSFT explanation is limited tier (safe=true, corroboration gap)", () => {
    const d = buildSafetyDisplay(msftSyntheticEx);
    expect(d.tier).toBe("limited");
  });

  it("generic fallback text does not appear in supporting sentences", () => {
    const genericFallback = "Some evidence is available; gaps noted where present.";
    const sentences = buildSupportingEvidenceSentences(msftSyntheticEx);
    expect(sentences.some(s => s === genericFallback)).toBe(false);
  });

  it("SUPPRESSED synthetic (BTC/XRP-style) renders blocked tier", () => {
    const btcEx: import("./api").IntelV3EvidenceExplanation = {
      ...msftSyntheticEx,
      primary_evidence_status: "SUPPRESSED",
      safe_for_visible_decision: false,
      action_blocks: ["buy_blocked_suppressed_evidence"],
    };
    const d = buildSafetyDisplay(btcEx);
    expect(d.tier).toBe("blocked");
  });

  it("THIN synthetic renders limited and NOT safe", () => {
    const thinEx: import("./api").IntelV3EvidenceExplanation = {
      ...msftSyntheticEx,
      primary_evidence_status: "INSUFFICIENT",
      safe_for_visible_decision: false,
      conviction_cap_applied: true,
      conviction_cap_reason: "band_thin",
    };
    const d = buildSafetyDisplay(thinEx);
    expect(d.tier).toBe("limited");
  });
});

// ── Stage 8A.3 — Post-lane republish payload contract ────────────────────────
// Production shape after MSFT technicals complete FRESH (USABLE_WITH_LIMITATIONS):
//   technical_signals_status = "LIMITED"
//   sentiment_status = "SUPPRESSED"
// These tests prove the clean Stage 7/8 drawer renders correctly for this shape.

describe("Stage 8A.3: post-lane republish payload → clean drawer (LIMITED technicals)", () => {
  const postLaneEx: import("./api").IntelV3EvidenceExplanation = {
    primary_evidence_status: "LIMITED",
    technical_signals_status: "LIMITED",   // USABLE_WITH_LIMITATIONS after lane completion
    sentiment_status: "SUPPRESSED",        // thin/suppressed remains not usable
    conviction_cap_applied: true,
    conviction_cap_reason: "ok_cap_medium",
    safe_for_visible_decision: true,
    safe_for_visible_decision_reason: "limited_fundamentals_partial_tech",
    governance_priority: "governance_inactive",
    corroboration_gap: true,
    action_blocks: [],
  };

  it("technical_signals_status=LIMITED appears in supporting sentences", () => {
    const sentences = buildSupportingEvidenceSentences(postLaneEx);
    const techSentence = sentences.find(
      s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price")
    );
    expect(techSentence).toBeDefined();
    expect(sentences.length).toBeGreaterThan(0);
  });

  it("LIMITED technicals uses 'Some market' language (partial, not full)", () => {
    const sentences = buildSupportingEvidenceSentences(postLaneEx);
    const techSentence = sentences.find(
      s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price")
    );
    expect(techSentence).toBeDefined();
    expect(techSentence!.toLowerCase()).toMatch(/some|partial|available/);
  });

  it("SUPPRESSED sentiment does NOT appear in supporting sentences", () => {
    const sentences = buildSupportingEvidenceSentences(postLaneEx);
    const sentSentence = sentences.find(
      s => s.toLowerCase().includes("news") || s.toLowerCase().includes("sentiment")
    );
    expect(sentSentence).toBeUndefined();
  });

  it("SUPPRESSED sentiment appears in incomplete sentences", () => {
    const sentences = buildIncompleteEvidenceSentences(postLaneEx);
    const sentSentence = sentences.find(
      s => s.toLowerCase().includes("news") || s.toLowerCase().includes("sentiment")
    );
    expect(sentSentence).toBeDefined();
  });

  it("supporting and incomplete do not overlap for post-lane payload", () => {
    const supporting = buildSupportingEvidenceSentences(postLaneEx);
    const incomplete = buildIncompleteEvidenceSentences(postLaneEx);
    const supportingSet = new Set(supporting.map(s => s.toLowerCase().trim()));
    for (const s of incomplete) {
      expect(supportingSet.has(s.toLowerCase().trim())).toBe(false);
    }
  });

  it("no raw metric keys in post-lane supporting sentences", () => {
    for (const s of buildSupportingEvidenceSentences(postLaneEx)) assertNoRawKeys(s);
  });

  it("no raw metric keys in post-lane incomplete sentences", () => {
    for (const s of buildIncompleteEvidenceSentences(postLaneEx)) assertNoRawKeys(s);
  });

  it("no legacy placeholder text in any output", () => {
    const allTexts = [
      ...buildSupportingEvidenceSentences(postLaneEx),
      ...buildIncompleteEvidenceSentences(postLaneEx),
      buildWhyActionExplanation("HOLD", postLaneEx),
    ];
    // Stale placeholder patterns (section-title-style or preparation markers)
    const legacyPhrases = [
      "intelligence module is being prepared",
      "intelligence modules in preparation",
      "will appear here once",
    ];
    for (const text of allTexts) {
      for (const phrase of legacyPhrases) {
        expect(text.toLowerCase()).not.toContain(phrase.toLowerCase());
      }
      assertNoComingLaterCaption(text);
      assertNoStaleGovernancePlaceholder(text);
    }
  });

  it("safety display for post-lane LIMITED+tech LIMITED is limited tier (not blocked)", () => {
    const d = buildSafetyDisplay(postLaneEx);
    expect(d.tier).toBe("limited");
  });
});

// ── Stage 8A.3 — null evidence_explanation compact fallback ──────────────────

describe("Stage 8A.3: null evidence_explanation → no legacy repeated sections", () => {
  // When evidence_explanation is null, the drawer falls back to compact honest text.
  // Verify the helper outputs (used by the fallback path) don't produce legacy content.

  it("buildWhyActionExplanation(HOLD, null) returns compact text, no legacy placeholder phrases", () => {
    const text = buildWhyActionExplanation("HOLD", null);
    const legacyPhrases = [
      "intelligence module",
      "will appear here once",
    ];
    for (const phrase of legacyPhrases) {
      expect(text.toLowerCase()).not.toContain(phrase.toLowerCase());
    }
    expect(text.length).toBeGreaterThan(0);
  });

  it("buildWhyActionExplanation does not return COMING_LATER_CANONICAL_CAPTION", () => {
    const { COMING_LATER_CANONICAL_CAPTION } = require("../components/cards/IntelV3PrimitivesData");
    for (const action of ["BUY", "HOLD", "TRIM", "SELL"]) {
      const text = buildWhyActionExplanation(action, null);
      expect(text).not.toContain(COMING_LATER_CANONICAL_CAPTION);
    }
  });
});

// ── Action label safety (no WATCH/AVOID) ─────────────────────────────────────

describe("action label safety", () => {
  it("helper outputs never contain WATCH or AVOID radar-only labels", () => {
    const radarLabels = ["WATCH", "AVOID"];
    const allTexts = [
      ...["BUY", "HOLD", "TRIM", "SELL"].map(a => buildWhyActionExplanation(a, makeEx())),
      ...buildSupportingEvidenceSentences(makeEx({ primary_evidence_status: "READY" })),
      ...buildIncompleteEvidenceSentences(makeEx({ technical_signals_status: "MISSING" })),
    ];
    for (const text of allTexts) {
      for (const label of radarLabels) {
        expect(text).not.toMatch(new RegExp(`\\b${label}\\b`));
      }
    }
  });
});
