/**
 * Stage 7 — intel-v3-explanation.ts pure helper contracts.
 *
 * Covers:
 * - No raw metric keys / internal field names leak into any display output
 * - BTC/XRP conservative (blocked/limited) states render honestly
 * - Missing technical/sentiment shown as missing/unusable, not supporting
 * - Action labels remain BUY/HOLD/TRIM/SELL only (not tested here — tested in visibleIntelActions)
 * - Conviction cap visible and understandable
 * - Evidence-limited vs better-supported labels are distinct
 */

import {
  readinessToDisplay,
  governancePriorityToExplanation,
  convictionCapLabel,
  buildEvidenceLaneRows,
  buildSafetyDisplay,
  buildPortfolioEvidenceSummary,
  buildSupportingEvidenceSentences,
  buildIncompleteEvidenceSentences,
  RAW_KEYS_BANNED,
} from "./intel-v3-explanation";
import type { IntelV3EvidenceExplanation, IntelV3HeldCard } from "./api";

// ── Raw-key leak guard helpers ────────────────────────────────────────────────

function containsRawKey(text: string): string | null {
  for (const key of RAW_KEYS_BANNED) {
    if (text.includes(key)) return key;
  }
  return null;
}

function assertNoRawKeys(text: string) {
  const found = containsRawKey(text);
  if (found) throw new Error(`Raw key leaked into display copy: "${found}" in "${text}"`);
}

// ── readinessToDisplay ────────────────────────────────────────────────────────

describe("readinessToDisplay", () => {
  const ALL_READINESS_VALUES = [
    "READY", "LIMITED", "USABLE_WITH_LIMITATIONS",
    "INSUFFICIENT", "SUPPRESSED", "SUPPRESSED_CONTRADICTED",
    "SUPPRESSED_UNKNOWN_SOURCE", "SUPPRESSED_INCOMPLETE",
    "MISSING", "NOT_APPLICABLE", "STALE_OR_UNKNOWN",
    "UNKNOWN_FUTURE_VALUE",
  ];

  it("READY → isUsable=true, not blocked", () => {
    const d = readinessToDisplay("READY");
    expect(d.isUsable).toBe(true);
    expect(d.isBlocked).toBe(false);
    expect(d.label).toBeTruthy();
  });

  it("LIMITED → isUsable=true, not blocked", () => {
    const d = readinessToDisplay("LIMITED");
    expect(d.isUsable).toBe(true);
    expect(d.isBlocked).toBe(false);
  });

  it("USABLE_WITH_LIMITATIONS → isUsable=true (alias for LIMITED)", () => {
    const d = readinessToDisplay("USABLE_WITH_LIMITATIONS");
    expect(d.isUsable).toBe(true);
  });

  it("MISSING → isUsable=false, not blocked", () => {
    const d = readinessToDisplay("MISSING");
    expect(d.isUsable).toBe(false);
    expect(d.isBlocked).toBe(false);
  });

  it("SUPPRESSED → isUsable=false, isBlocked=true", () => {
    const d = readinessToDisplay("SUPPRESSED");
    expect(d.isUsable).toBe(false);
    expect(d.isBlocked).toBe(true);
  });

  it("SUPPRESSED_UNKNOWN_SOURCE → isBlocked=true (no raw key in label)", () => {
    const d = readinessToDisplay("SUPPRESSED_UNKNOWN_SOURCE");
    expect(d.isBlocked).toBe(true);
    assertNoRawKeys(d.label);
    assertNoRawKeys(d.detail);
  });

  it("SUPPRESSED_INCOMPLETE → isBlocked=true", () => {
    const d = readinessToDisplay("SUPPRESSED_INCOMPLETE");
    expect(d.isBlocked).toBe(true);
  });

  it("INSUFFICIENT → isUsable=false, not blocked", () => {
    const d = readinessToDisplay("INSUFFICIENT");
    expect(d.isUsable).toBe(false);
    expect(d.isBlocked).toBe(false);
  });

  it("NOT_APPLICABLE → isUsable=false, not blocked (ETF/crypto SEC lane)", () => {
    const d = readinessToDisplay("NOT_APPLICABLE");
    expect(d.isUsable).toBe(false);
    expect(d.isBlocked).toBe(false);
  });

  it("unknown future value → graceful fallback, not blocked", () => {
    const d = readinessToDisplay("UNKNOWN_FUTURE_VALUE");
    expect(d.isBlocked).toBe(false);
    expect(d.label).toBeTruthy();
  });

  it("no raw keys leak in any readiness display output", () => {
    for (const val of ALL_READINESS_VALUES) {
      const d = readinessToDisplay(val);
      assertNoRawKeys(d.label);
      assertNoRawKeys(d.detail);
    }
  });
});

// ── governancePriorityToExplanation ──────────────────────────────────────────

describe("governancePriorityToExplanation", () => {
  const ALL_PRIORITIES = [
    "p1", "p2_stale_no_usable_axes", "p3a", "p3b", "p4a",
    "p4b_limited_no_corroboration", "p5", "fallback",
    "governance_inactive", "unknown", "",
  ];

  it("p4b_limited_no_corroboration → mentions cap or conviction, no raw key", () => {
    const text = governancePriorityToExplanation("p4b_limited_no_corroboration");
    assertNoRawKeys(text);
    expect(text.length).toBeGreaterThan(0);
    expect(text.toLowerCase()).toMatch(/cap|conviction|partial|precaution/);
  });

  it("p1 → mentions quality issues or conservative, no raw key", () => {
    const text = governancePriorityToExplanation("p1");
    assertNoRawKeys(text);
    expect(text.toLowerCase()).toMatch(/quality|conservative|issue/);
  });

  it("p3a → mentions strong or confirmed, no raw key", () => {
    const text = governancePriorityToExplanation("p3a");
    assertNoRawKeys(text);
    expect(text.toLowerCase()).toMatch(/solid|confirmed|higher confidence/);
  });

  it("governance_inactive / unknown / empty → empty string (no noise)", () => {
    expect(governancePriorityToExplanation("governance_inactive")).toBe("");
    expect(governancePriorityToExplanation("unknown")).toBe("");
    expect(governancePriorityToExplanation("")).toBe("");
  });

  it("no raw keys leak in any priority explanation", () => {
    for (const p of ALL_PRIORITIES) {
      const text = governancePriorityToExplanation(p);
      assertNoRawKeys(text);
    }
  });
});

// ── convictionCapLabel ────────────────────────────────────────────────────────

describe("convictionCapLabel", () => {
  it("cap=false → empty string", () => {
    expect(convictionCapLabel(false, null)).toBe("");
    expect(convictionCapLabel(false, "some_reason")).toBe("");
  });

  it("cap=true, null reason → generic cap label", () => {
    const text = convictionCapLabel(true, null);
    expect(text.length).toBeGreaterThan(0);
    expect(text.toLowerCase()).toMatch(/limit|incomplete|cap/);
  });

  it("cap=true, thin reason → mentions thin or conservative", () => {
    const text = convictionCapLabel(true, "band_thin_p1");
    expect(text.toLowerCase()).toMatch(/thin|conservative|limited/);
  });

  it("cap=true, ok/limited reason → mentions partial or cap", () => {
    const text = convictionCapLabel(true, "ok_cap_medium");
    assertNoRawKeys(text);
    expect(text.toLowerCase()).toMatch(/cap|moderate|partial/);
  });

  it("no raw keys in any cap label", () => {
    const reasons = [null, "band_thin_p1", "ok_cap_medium", "p4b_limited_no_corroboration", "suppressed"];
    for (const r of reasons) {
      const text = convictionCapLabel(true, r);
      assertNoRawKeys(text);
    }
  });
});

// ── buildEvidenceLaneRows ─────────────────────────────────────────────────────

function makeExplanation(overrides: Partial<IntelV3EvidenceExplanation> = {}): IntelV3EvidenceExplanation {
  return {
    primary_evidence_status: "LIMITED",
    technical_signals_status: "MISSING",
    sentiment_status: "SUPPRESSED",
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

describe("buildEvidenceLaneRows", () => {
  it("returns 3 lanes: fundamentals, technicals, sentiment", () => {
    const rows = buildEvidenceLaneRows(makeExplanation());
    expect(rows).toHaveLength(3);
    expect(rows.map(r => r.laneId)).toEqual(["fundamentals", "technicals", "sentiment"]);
  });

  it("MISSING technical → isUsable=false (not shown as supporting evidence)", () => {
    const rows = buildEvidenceLaneRows(makeExplanation({ technical_signals_status: "MISSING" }));
    const tech = rows.find(r => r.laneId === "technicals")!;
    expect(tech.statusDisplay.isUsable).toBe(false);
  });

  it("SUPPRESSED sentiment → isBlocked=true (not shown as supporting evidence)", () => {
    const rows = buildEvidenceLaneRows(makeExplanation({ sentiment_status: "SUPPRESSED" }));
    const sent = rows.find(r => r.laneId === "sentiment")!;
    expect(sent.statusDisplay.isBlocked).toBe(true);
    expect(sent.statusDisplay.isUsable).toBe(false);
  });

  it("READY technical → isUsable=true", () => {
    const rows = buildEvidenceLaneRows(makeExplanation({ technical_signals_status: "READY" }));
    const tech = rows.find(r => r.laneId === "technicals")!;
    expect(tech.statusDisplay.isUsable).toBe(true);
  });

  it("no raw keys in any lane label or detail", () => {
    const rows = buildEvidenceLaneRows(makeExplanation());
    for (const row of rows) {
      assertNoRawKeys(row.label);
      assertNoRawKeys(row.statusDisplay.label);
      assertNoRawKeys(row.statusDisplay.detail);
    }
  });
});

// ── buildSafetyDisplay ───────────────────────────────────────────────────────

describe("buildSafetyDisplay", () => {
  it("action_blocks present → tier=blocked", () => {
    const d = buildSafetyDisplay(makeExplanation({ action_blocks: ["buy_blocked_thin_evidence"] }));
    expect(d.tier).toBe("blocked");
    assertNoRawKeys(d.label);
    assertNoRawKeys(d.detail);
  });

  it("safe=false, no blocks → tier=limited", () => {
    const d = buildSafetyDisplay(makeExplanation({ safe_for_visible_decision: false, action_blocks: [] }));
    expect(d.tier).toBe("limited");
  });

  it("safe=true, corroboration_gap=false, fundamentals READY → tier=stronger", () => {
    const d = buildSafetyDisplay(makeExplanation({
      safe_for_visible_decision: true,
      corroboration_gap: false,
      primary_evidence_status: "READY",
      action_blocks: [],
    }));
    expect(d.tier).toBe("stronger");
  });

  it("safe=true, corroboration_gap=true → tier=limited (no corroboration)", () => {
    const d = buildSafetyDisplay(makeExplanation({
      safe_for_visible_decision: true,
      corroboration_gap: true,
      action_blocks: [],
    }));
    expect(d.tier).toBe("limited");
  });

  it("labels are distinct: stronger vs limited vs blocked", () => {
    const stronger = buildSafetyDisplay(makeExplanation({
      safe_for_visible_decision: true,
      corroboration_gap: false,
      primary_evidence_status: "READY",
      action_blocks: [],
    }));
    const limited = buildSafetyDisplay(makeExplanation({
      safe_for_visible_decision: false,
      action_blocks: [],
    }));
    const blocked = buildSafetyDisplay(makeExplanation({
      action_blocks: ["buy_blocked"],
    }));
    expect(stronger.label).not.toBe(limited.label);
    expect(limited.label).not.toBe(blocked.label);
    expect(stronger.label).not.toBe(blocked.label);
  });

  it("BTC/XRP-style: SUPPRESSED fundamentals + action_blocks → blocked", () => {
    const d = buildSafetyDisplay(makeExplanation({
      primary_evidence_status: "SUPPRESSED",
      action_blocks: ["buy_blocked_suppressed_evidence"],
      safe_for_visible_decision: false,
    }));
    expect(d.tier).toBe("blocked");
  });

  it("crypto-style: MISSING fundamentals, safe=false → limited", () => {
    const d = buildSafetyDisplay(makeExplanation({
      primary_evidence_status: "MISSING",
      action_blocks: [],
      safe_for_visible_decision: false,
    }));
    expect(d.tier).toBe("limited");
  });
});

// ── buildPortfolioEvidenceSummary ─────────────────────────────────────────────

function makeCard(exOverrides: Partial<IntelV3EvidenceExplanation> = {}): IntelV3HeldCard {
  return {
    ticker: "TEST",
    name: "Test Corp",
    asset_type: "stock",
    action: "HOLD",
    conviction: "MEDIUM",
    evidence_band: "PARTIAL",
    portfolio_fit: "ON_TARGET",
    risk_level: "LOW",
    thesis_state: "intact",
    why_text: "Solid business held at target weight.",
    risk_text: "",
    action_text: "Hold current position.",
    what_would_change_view: "",
    fit_text: "On target",
    evidence_text: "Some evidence is available.",
    flags: [],
    source_snapshot_id: "snap-001",
    source_run_id: "run-001",
    updated_at: "2026-05-20T00:00:00Z",
    detail_drawer_payload: {
      rationale: "Good fundamentals.",
      why_now: "",
      why_not_now: "",
      evidence_band: "PARTIAL",
      evidence_quality: "OK",
      attractiveness: "MEDIUM",
      price_context: "FAIRLY_VALUED",
      portfolio_fit_raw: "ON_TARGET",
      risk_band: "LOW",
      blockers: [],
      suppression_reasons: {},
      schema_version: "v3.1",
      committee: { status: "source_validated" },
      evidence_explanation: makeExplanation(exOverrides),
    },
  };
}

describe("buildPortfolioEvidenceSummary", () => {
  it("empty portfolio → all zeros", () => {
    const s = buildPortfolioEvidenceSummary([]);
    expect(s.totalCards).toBe(0);
    expect(s.safeCount).toBe(0);
    expect(s.limitedCount).toBe(0);
    expect(s.blockedCount).toBe(0);
  });

  it("card with no evidence_explanation → counted as limited", () => {
    const card = makeCard();
    card.detail_drawer_payload.evidence_explanation = null;
    const s = buildPortfolioEvidenceSummary([card]);
    expect(s.limitedCount).toBe(1);
    expect(s.safeCount).toBe(0);
    expect(s.blockedCount).toBe(0);
  });

  it("blocked card → counted in blockedCount", () => {
    const card = makeCard({ action_blocks: ["buy_blocked_thin"] });
    const s = buildPortfolioEvidenceSummary([card]);
    expect(s.blockedCount).toBe(1);
    expect(s.safeCount).toBe(0);
  });

  it("safe card with corroboration → counted in safeCount", () => {
    const card = makeCard({
      safe_for_visible_decision: true,
      corroboration_gap: false,
      primary_evidence_status: "READY",
      action_blocks: [],
    });
    const s = buildPortfolioEvidenceSummary([card]);
    expect(s.safeCount).toBe(1);
  });

  it("conviction_cap_applied=true → counted in convictionCappedCount", () => {
    const card = makeCard({ conviction_cap_applied: true });
    const s = buildPortfolioEvidenceSummary([card]);
    expect(s.convictionCappedCount).toBe(1);
  });

  it("MISSING technical + SUPPRESSED sentiment → both NOT counted as usable", () => {
    const card = makeCard({
      technical_signals_status: "MISSING",
      sentiment_status: "SUPPRESSED",
    });
    const s = buildPortfolioEvidenceSummary([card]);
    expect(s.technicalUsableCount).toBe(0);
    expect(s.sentimentUsableCount).toBe(0);
  });

  it("READY technical → counted in technicalUsableCount", () => {
    const card = makeCard({ technical_signals_status: "READY" });
    const s = buildPortfolioEvidenceSummary([card]);
    expect(s.technicalUsableCount).toBe(1);
  });

  it("mixed portfolio: 2 limited, 1 blocked, 1 safe", () => {
    const limited1 = makeCard({ safe_for_visible_decision: true, corroboration_gap: true, action_blocks: [] });
    const limited2 = makeCard({ safe_for_visible_decision: false, action_blocks: [] });
    const blocked = makeCard({ action_blocks: ["buy_blocked_thin"] });
    const safe = makeCard({
      safe_for_visible_decision: true,
      corroboration_gap: false,
      primary_evidence_status: "READY",
      action_blocks: [],
    });
    const s = buildPortfolioEvidenceSummary([limited1, limited2, blocked, safe]);
    expect(s.totalCards).toBe(4);
    expect(s.limitedCount).toBe(2);
    expect(s.blockedCount).toBe(1);
    expect(s.safeCount).toBe(1);
  });

  it("fundamentalsUsableCount counts READY and LIMITED as usable", () => {
    const ready = makeCard({ primary_evidence_status: "READY", action_blocks: [] });
    const limited = makeCard({ primary_evidence_status: "LIMITED", action_blocks: [] });
    const missing = makeCard({ primary_evidence_status: "MISSING", action_blocks: [] });
    const suppressed = makeCard({ primary_evidence_status: "SUPPRESSED", action_blocks: [] });
    const s = buildPortfolioEvidenceSummary([ready, limited, missing, suppressed]);
    expect(s.fundamentalsUsableCount).toBe(2);
    expect(s.cardsWithExplanation).toBe(4);
  });

  it("cardsWithExplanation excludes cards with no evidence_explanation", () => {
    const withEx = makeCard({ action_blocks: [] });
    const noEx = makeCard({});
    noEx.detail_drawer_payload.evidence_explanation = null;
    const s = buildPortfolioEvidenceSummary([withEx, noEx]);
    expect(s.cardsWithExplanation).toBe(1);
    expect(s.totalCards).toBe(2);
  });

  it("all fundamentals usable → fundamentalsUsableCount equals cardsWithExplanation", () => {
    const cards = [
      makeCard({ primary_evidence_status: "READY", action_blocks: [] }),
      makeCard({ primary_evidence_status: "USABLE_WITH_LIMITATIONS", action_blocks: [] }),
    ];
    const s = buildPortfolioEvidenceSummary(cards);
    expect(s.fundamentalsUsableCount).toBe(2);
    expect(s.cardsWithExplanation).toBe(2);
  });

  it("no fundamentals usable → fundamentalsUsableCount is zero", () => {
    const cards = [
      makeCard({ primary_evidence_status: "MISSING", action_blocks: [] }),
      makeCard({ primary_evidence_status: "SUPPRESSED", action_blocks: [] }),
    ];
    const s = buildPortfolioEvidenceSummary(cards);
    expect(s.fundamentalsUsableCount).toBe(0);
    expect(s.cardsWithExplanation).toBe(2);
  });
});

// ── buildSupportingEvidenceSentences ──────────────────────────────────────────

describe("buildSupportingEvidenceSentences", () => {
  it("READY fundamentals → fundamentals sentence present", () => {
    const ex = makeExplanation({ primary_evidence_status: "READY" });
    const sentences = buildSupportingEvidenceSentences(ex);
    expect(sentences.some(s => s.toLowerCase().includes("fundamentals"))).toBe(true);
  });

  it("READY technicals → technicals sentence present", () => {
    const ex = makeExplanation({ technical_signals_status: "READY" });
    const sentences = buildSupportingEvidenceSentences(ex);
    expect(sentences.some(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"))).toBe(true);
  });

  it("READY sentiment → sentiment sentence present", () => {
    const ex = makeExplanation({ sentiment_status: "READY" });
    const sentences = buildSupportingEvidenceSentences(ex);
    expect(sentences.some(s => s.toLowerCase().includes("news") || s.toLowerCase().includes("sentiment"))).toBe(true);
  });

  it("all MISSING → no supporting sentences", () => {
    const ex = makeExplanation({
      primary_evidence_status: "MISSING",
      technical_signals_status: "MISSING",
      sentiment_status: "MISSING",
    });
    expect(buildSupportingEvidenceSentences(ex)).toHaveLength(0);
  });

  it("no raw keys in any supporting sentence", () => {
    const ex = makeExplanation({
      primary_evidence_status: "READY",
      technical_signals_status: "LIMITED",
      sentiment_status: "READY",
    });
    buildSupportingEvidenceSentences(ex).forEach(s => assertNoRawKeys(s));
  });
});

// ── buildIncompleteEvidenceSentences ──────────────────────────────────────────

describe("buildIncompleteEvidenceSentences", () => {
  it("all READY → no incomplete sentences", () => {
    const ex = makeExplanation({
      primary_evidence_status: "READY",
      technical_signals_status: "READY",
      sentiment_status: "READY",
    });
    expect(buildIncompleteEvidenceSentences(ex)).toHaveLength(0);
  });

  it("MISSING technicals → 'not yet available' wording, not 'present but not strong'", () => {
    const ex = makeExplanation({ technical_signals_status: "MISSING" });
    const sentences = buildIncompleteEvidenceSentences(ex);
    const techSentence = sentences.find(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"));
    expect(techSentence).toBeDefined();
    expect(techSentence).toContain("not yet available");
    expect(techSentence).not.toContain("not yet strong enough");
  });

  it("INSUFFICIENT technicals → 'available but not yet strong enough' wording, not 'not yet available'", () => {
    const ex = makeExplanation({ technical_signals_status: "INSUFFICIENT" });
    const sentences = buildIncompleteEvidenceSentences(ex);
    const techSentence = sentences.find(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"));
    expect(techSentence).toBeDefined();
    expect(techSentence).toContain("not yet strong enough");
    expect(techSentence).not.toContain("not yet available");
  });

  it("STALE_OR_UNKNOWN technicals → 'available but not yet strong enough' wording", () => {
    const ex = makeExplanation({ technical_signals_status: "STALE_OR_UNKNOWN" });
    const sentences = buildIncompleteEvidenceSentences(ex);
    const techSentence = sentences.find(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"));
    expect(techSentence).toBeDefined();
    expect(techSentence).toContain("not yet strong enough");
  });

  it("SUPPRESSED technicals → blocked wording (quality issues)", () => {
    const ex = makeExplanation({ technical_signals_status: "SUPPRESSED" });
    const sentences = buildIncompleteEvidenceSentences(ex);
    const techSentence = sentences.find(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"));
    expect(techSentence).toBeDefined();
    expect(techSentence).toContain("quality");
  });

  it("MISSING sentiment → 'not yet available' wording (distinct from INSUFFICIENT)", () => {
    const ex = makeExplanation({ sentiment_status: "MISSING" });
    const sentences = buildIncompleteEvidenceSentences(ex);
    const sentSentence = sentences.find(s => s.toLowerCase().includes("news") || s.toLowerCase().includes("sentiment"));
    expect(sentSentence).toBeDefined();
    expect(sentSentence).not.toContain("not yet strong enough");
    expect(sentSentence).toContain("not yet available");
  });

  it("INSUFFICIENT sentiment → 'available but not yet strong enough' wording", () => {
    const ex = makeExplanation({ sentiment_status: "INSUFFICIENT" });
    const sentences = buildIncompleteEvidenceSentences(ex);
    const sentSentence = sentences.find(s => s.toLowerCase().includes("news") || s.toLowerCase().includes("sentiment"));
    expect(sentSentence).toBeDefined();
    expect(sentSentence).toContain("not yet strong enough");
  });

  it("MISSING vs INSUFFICIENT are distinguishable in copy", () => {
    const missing = buildIncompleteEvidenceSentences(makeExplanation({ technical_signals_status: "MISSING" }));
    const insufficient = buildIncompleteEvidenceSentences(makeExplanation({ technical_signals_status: "INSUFFICIENT" }));
    const missingTech = missing.find(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"))!;
    const insufficientTech = insufficient.find(s => s.toLowerCase().includes("market") || s.toLowerCase().includes("price"))!;
    expect(missingTech).not.toBe(insufficientTech);
  });

  it("no raw keys in any incomplete sentence", () => {
    const statuses = ["MISSING", "INSUFFICIENT", "SUPPRESSED", "STALE_OR_UNKNOWN"];
    for (const status of statuses) {
      const ex = makeExplanation({
        technical_signals_status: status,
        sentiment_status: status,
      });
      buildIncompleteEvidenceSentences(ex).forEach(s => assertNoRawKeys(s));
    }
  });
});
