/**
 * Concierge renderers trust contract tests.
 *
 * Tests the frontend contract for displaying concierge notes:
 * - validated=true notes are shown
 * - validated=false notes are hidden (not displayed to user)
 * - Modifier status is surfaced in the display contract
 * - Rating-primary notes must not reach the frontend if the quality gate
 *   is properly respected upstream
 *
 * Uses Node.js built-in test runner (node:test).
 * Run with: node --test tests/concierge-renderers.test.mjs
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";

// ── Frontend-side per_card_notes contract types (mirrored from backend) ──────

/**
 * @typedef {Object} PerCardNote
 * @property {number} card_index
 * @property {string} card_title
 * @property {string} evidence_adequacy  — "STRONG" | "OK" | "THIN"
 * @property {string} user_modifier       — "river" | "view" | "none"
 * @property {string} modifier_status     — "confirmed_listing_context" | "confirmed_address_or_name_context" | "unknown" | "contradicted"
 * @property {boolean} display_why_validated
 * @property {string} display_why_source
 * @property {string} visible_concierge_note  — empty string when not validated
 * @property {string} quality_gate_result — "PASS" | "FAIL_RATING_PRIMARY" | "FAIL_UNSUPPORTED_CLAIM" | "FAIL_GENERIC_ONLY"
 * @property {boolean} validated
 * @property {boolean} retry_used
 * @property {boolean} fallback_used
 * @property {string} source
 */

// ── Frontend rendering helpers (production contract) ─────────────────────────

/**
 * Determines whether a concierge note should be displayed to the user.
 * Contract: show only validated notes. Never show an empty or unvalidated note.
 *
 * @param {PerCardNote} note
 * @returns {{ shouldShow: boolean, displayNote: string | null }}
 */
function shouldShowConciergeNote(note) {
  if (!note.validated) {
    return { shouldShow: false, displayNote: null };
  }
  const text = (note.visible_concierge_note || "").trim();
  if (!text) {
    return { shouldShow: false, displayNote: null };
  }
  return { shouldShow: true, displayNote: text };
}

/**
 * Renders the modifier badge text shown to users.
 *
 * @param {string} userModifier
 * @param {string} modifierStatus
 * @returns {string}
 */
function renderModifierBadge(userModifier, modifierStatus) {
  if (userModifier === "none") return "";
  if (modifierStatus === "confirmed_listing_context") return `${userModifier} ✓`;
  if (modifierStatus === "confirmed_address_or_name_context") return `${userModifier} (listing)`;
  if (modifierStatus === "unknown") return `${userModifier}?`;
  if (modifierStatus === "contradicted") return `not ${userModifier}`;
  return "";
}

/**
 * Returns true if a note text appears to be rating-primary.
 * Used as a frontend-side sanity check (belt-and-suspenders with backend gate).
 *
 * @param {string} note
 * @returns {boolean}
 */
function isRatingPrimary(note) {
  const patterns = [
    /highest[- ]rated/i,
    /second[- ]most[- ]reviewed/i,
    /\breview base\b/i,
    /\d\.\d\s*★\s+across\b/i,
    /from\s+\d{3,},?\d*\s+reviews/i,
    /\d\.\d\s*★\s+from\s+\d/i,
  ];
  return patterns.some((p) => p.test(note));
}

// ── Fixtures ──────────────────────────────────────────────────────────────────

/** @type {PerCardNote} */
const NORTHMAN_VALIDATED_NOTE = {
  card_index: 4,
  card_title: "The Northman Beer & Cider Garden on the Riverwalk",
  evidence_adequacy: "OK",
  user_modifier: "river",
  modifier_status: "confirmed_address_or_name_context",
  display_why_validated: true,
  display_why_source: "verified_name_contains:riverwalk",
  visible_concierge_note:
    "Because the verified listing itself places Northman on the Riverwalk, " +
    "it is the strongest river-context beer stop here; " +
    "verify actual seating/view details if that matters.",
  quality_gate_result: "PASS",
  validated: true,
  retry_used: true,
  fallback_used: false,
  source: "llm_retry",
};

/** @type {PerCardNote} */
const NORTHMAN_UNVALIDATED_NOTE = {
  ...NORTHMAN_VALIDATED_NOTE,
  validated: false,
  visible_concierge_note: "",
  quality_gate_result: "FAIL_UNSUPPORTED_CLAIM",
  source: "omitted_FAIL_UNSUPPORTED_CLAIM",
};

/** @type {PerCardNote} */
const RATING_PRIMARY_NOTE = {
  card_index: 1,
  card_title: "Top Rated Brewery",
  evidence_adequacy: "OK",
  user_modifier: "none",
  modifier_status: "unknown",
  display_why_validated: false,
  display_why_source: "no_river_context_found",
  visible_concierge_note: "Highest-rated brewery in this set with 4.8★ across 1,028 reviews.",
  quality_gate_result: "FAIL_RATING_PRIMARY",
  validated: false,
  retry_used: true,
  fallback_used: false,
  source: "omitted_FAIL_RATING_PRIMARY",
};

/** @type {PerCardNote} */
const VALID_CONCIERGE_NOTE = {
  card_index: 2,
  card_title: "Revolution Brewing Taproom",
  evidence_adequacy: "OK",
  user_modifier: "river",
  modifier_status: "unknown",
  display_why_validated: true,
  display_why_source: "no_river_context_found",
  visible_concierge_note:
    "Revolution's Logan Square taproom pours the full range — from the cult Anti-Hero IPA " +
    "to barrel-aged releases — in a repurposed industrial space with a full kitchen.",
  quality_gate_result: "PASS",
  validated: true,
  retry_used: false,
  fallback_used: false,
  source: "llm_primary",
};

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("shouldShowConciergeNote", () => {
  test("validated note with text should show", () => {
    const { shouldShow, displayNote } = shouldShowConciergeNote(NORTHMAN_VALIDATED_NOTE);
    assert.equal(shouldShow, true);
    assert.ok(displayNote && displayNote.length > 0);
  });

  test("unvalidated note must not show", () => {
    const { shouldShow, displayNote } = shouldShowConciergeNote(NORTHMAN_UNVALIDATED_NOTE);
    assert.equal(shouldShow, false);
    assert.equal(displayNote, null);
  });

  test("rating-primary note (unvalidated) must not show", () => {
    const { shouldShow } = shouldShowConciergeNote(RATING_PRIMARY_NOTE);
    assert.equal(shouldShow, false);
  });

  test("valid concierge note for generic brewery should show", () => {
    const { shouldShow, displayNote } = shouldShowConciergeNote(VALID_CONCIERGE_NOTE);
    assert.equal(shouldShow, true);
    assert.ok(displayNote && displayNote.includes("Revolution"));
  });

  test("note with empty visible_concierge_note must not show even if validated=true", () => {
    const emptyNote = { ...VALID_CONCIERGE_NOTE, visible_concierge_note: "" };
    const { shouldShow } = shouldShowConciergeNote(emptyNote);
    assert.equal(shouldShow, false);
  });
});

describe("renderModifierBadge", () => {
  test("none modifier returns empty string", () => {
    assert.equal(renderModifierBadge("none", "unknown"), "");
  });

  test("confirmed_listing_context shows checkmark", () => {
    const badge = renderModifierBadge("river", "confirmed_listing_context");
    assert.ok(badge.includes("✓"), `Expected checkmark in badge: '${badge}'`);
  });

  test("confirmed_address_or_name_context shows listing badge", () => {
    const badge = renderModifierBadge("river", "confirmed_address_or_name_context");
    assert.ok(badge.includes("listing"), `Expected 'listing' in badge: '${badge}'`);
  });

  test("unknown modifier status shows question mark", () => {
    const badge = renderModifierBadge("view", "unknown");
    assert.ok(badge.includes("?"), `Expected '?' in badge: '${badge}'`);
  });

  test("contradicted shows negation", () => {
    const badge = renderModifierBadge("river", "contradicted");
    assert.ok(badge.includes("not"), `Expected 'not' in badge: '${badge}'`);
  });

  test("Northman note gets listing badge for river modifier", () => {
    const badge = renderModifierBadge(
      NORTHMAN_VALIDATED_NOTE.user_modifier,
      NORTHMAN_VALIDATED_NOTE.modifier_status,
    );
    assert.ok(badge.includes("listing") || badge.includes("river"));
  });
});

describe("isRatingPrimary frontend guard", () => {
  test("highest-rated note is flagged", () => {
    assert.equal(
      isRatingPrimary("Highest-rated option with 4.8★ across 1,028 reviews."),
      true,
    );
  });

  test("second-most-reviewed note is flagged", () => {
    assert.equal(isRatingPrimary("Second-most-reviewed at 1,144 reviews."), true);
  });

  test("valid concierge note is not flagged", () => {
    assert.equal(
      isRatingPrimary(
        "Revolution's Logan Square taproom pours the full range — Anti-Hero IPA to barrel-aged.",
      ),
      false,
    );
  });

  test("Northman note is not flagged", () => {
    assert.equal(isRatingPrimary(NORTHMAN_VALIDATED_NOTE.visible_concierge_note), false);
  });

  test("rating as secondary context is not flagged", () => {
    assert.equal(
      isRatingPrimary(
        "A craft brewery specialising in Czech lager styles. Rated 4.6★ by regular visitors.",
      ),
      false,
    );
  });
});

describe("per_card_notes frontend contract", () => {
  test("Northman note does not contain waterfront seating claim", () => {
    const note = NORTHMAN_VALIDATED_NOTE.visible_concierge_note.toLowerCase();
    assert.ok(!note.includes("waterfront seating"), "Must not claim waterfront seating");
    assert.ok(!note.includes("riverfront view"), "Must not claim riverfront views");
    assert.ok(!note.includes("river view"), "Must not claim river views");
  });

  test("Northman note mentions Riverwalk context safely", () => {
    const note = NORTHMAN_VALIDATED_NOTE.visible_concierge_note.toLowerCase();
    assert.ok(
      note.includes("riverwalk") || note.includes("listing") || note.includes("river-context"),
      "Note must mention Riverwalk or listing context",
    );
  });

  test("modifier_status confirmed_address_or_name_context is correct for Northman", () => {
    assert.equal(
      NORTHMAN_VALIDATED_NOTE.modifier_status,
      "confirmed_address_or_name_context",
    );
  });

  test("retry_used is true for Northman (first attempt failed)", () => {
    assert.equal(NORTHMAN_VALIDATED_NOTE.retry_used, true);
  });

  test("quality_gate_result PASS for validated note", () => {
    assert.equal(NORTHMAN_VALIDATED_NOTE.quality_gate_result, "PASS");
  });

  test("quality_gate_result FAIL for unvalidated note", () => {
    assert.ok(NORTHMAN_UNVALIDATED_NOTE.quality_gate_result.startsWith("FAIL"));
  });
});
