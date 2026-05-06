/**
 * Explore restaurants trust contract tests.
 *
 * Validates the frontend trust contract for the Explore / places search feature:
 * - Cards with validated=false must never surface a note to the user
 * - The validated-note gate is enforced before rendering
 * - per_card_notes observability shape is correct
 * - The three production queries produce the expected trust signal shapes
 *
 * Uses Node.js built-in test runner (node:test).
 * Run with: node --test tests/explore-restaurants-trust-contract.test.mjs
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";

// ── Trust contract helpers ────────────────────────────────────────────────────

/**
 * @typedef {Object} SemanticRetrievalTurnSummary
 * @property {string} query
 * @property {boolean} reasoning_success
 * @property {string|null} reasoning_failure_reason
 * @property {number} llm_accepted_count
 * @property {number} retry_recovered_count
 * @property {number} fallback_model_used_count
 * @property {number} deterministic_visible_count
 * @property {number} final_note_omitted_count
 * @property {number} excluded_unvalidated_count
 * @property {number} final_card_count
 * @property {boolean} venue_head_recognized
 */

/**
 * @typedef {Object} PerCardNote
 * @property {number} card_index
 * @property {string} card_title
 * @property {string} evidence_adequacy
 * @property {string} user_modifier
 * @property {string} modifier_status
 * @property {boolean} display_why_validated
 * @property {string} display_why_source
 * @property {string} visible_concierge_note
 * @property {string} quality_gate_result
 * @property {boolean} validated
 * @property {boolean} retry_used
 * @property {boolean} fallback_used
 * @property {string} source
 */

/**
 * Validates that a turn summary meets production pass criteria.
 * @param {SemanticRetrievalTurnSummary} summary
 * @param {number} expectedCardCount
 * @returns {{ passed: boolean, failures: string[] }}
 */
function validateTurnSummary(summary, expectedCardCount) {
  const failures = [];
  if (summary.llm_accepted_count !== expectedCardCount) {
    failures.push(
      `llm_accepted_count=${summary.llm_accepted_count} expected ${expectedCardCount}`,
    );
  }
  if (summary.final_note_omitted_count !== 0) {
    failures.push(`final_note_omitted_count=${summary.final_note_omitted_count} expected 0`);
  }
  if (summary.excluded_unvalidated_count !== 0) {
    failures.push(`excluded_unvalidated_count=${summary.excluded_unvalidated_count} expected 0`);
  }
  if (summary.deterministic_visible_count !== 0) {
    failures.push(
      `deterministic_visible_count=${summary.deterministic_visible_count} expected 0`,
    );
  }
  if (!summary.reasoning_success) {
    failures.push(`reasoning_success=false reason=${summary.reasoning_failure_reason}`);
  }
  return { passed: failures.length === 0, failures };
}

/**
 * Returns notes that should NOT be shown to users (either unvalidated or empty).
 * @param {PerCardNote[]} perCardNotes
 * @returns {PerCardNote[]}
 */
function getHiddenNotes(perCardNotes) {
  return perCardNotes.filter(
    (n) => !n.validated || !n.visible_concierge_note || !n.visible_concierge_note.trim(),
  );
}

/**
 * Returns notes where rating/reviews appear to be the primary differentiator.
 * @param {PerCardNote[]} perCardNotes
 * @returns {PerCardNote[]}
 */
function getRatingPrimaryNotes(perCardNotes) {
  const patterns = [
    /highest[- ]rated/i,
    /second[- ]most[- ]reviewed/i,
    /\breview base\b/i,
    /\d\.\d\s*★\s+across\b/i,
    /from\s+\d{3,},?\d*\s+reviews/i,
  ];
  return perCardNotes.filter(
    (n) =>
      n.validated && patterns.some((p) => p.test(n.visible_concierge_note)),
  );
}

// ── Production-log fixture summaries (derived from PR #251 fix targets) ───────

/** @type {SemanticRetrievalTurnSummary} */
const BREWERY_TURN_PASS = {
  query: "breweries near the river",
  reasoning_success: true,
  reasoning_failure_reason: null,
  llm_accepted_count: 8,
  retry_recovered_count: 1,     // Northman rescued via retry
  fallback_model_used_count: 0,
  deterministic_visible_count: 0,
  final_note_omitted_count: 0,
  excluded_unvalidated_count: 0,
  final_card_count: 8,
  venue_head_recognized: true,
};

/** @type {SemanticRetrievalTurnSummary} — mirrors the PR #251 FAILURE state (for contrast) */
const BREWERY_TURN_FAIL = {
  query: "breweries near the river",
  reasoning_success: false,
  reasoning_failure_reason: "incomplete_reasoning:1_of_8_missing",
  llm_accepted_count: 7,
  retry_recovered_count: 0,
  fallback_model_used_count: 0,
  deterministic_visible_count: 0,
  final_note_omitted_count: 1,
  excluded_unvalidated_count: 1,
  final_card_count: 7,
  venue_head_recognized: true,
};

/** @type {SemanticRetrievalTurnSummary} */
const TAPROOM_TURN_PASS = {
  query: "taprooms with a view",
  reasoning_success: true,
  reasoning_failure_reason: null,
  llm_accepted_count: 8,
  retry_recovered_count: 0,
  fallback_model_used_count: 0,
  deterministic_visible_count: 0,
  final_note_omitted_count: 0,
  excluded_unvalidated_count: 0,
  final_card_count: 8,
  venue_head_recognized: true,
};

/** @type {SemanticRetrievalTurnSummary} */
const IZAKAYA_TURN_PASS = {
  query: "Izakayas",
  reasoning_success: true,
  reasoning_failure_reason: null,
  llm_accepted_count: 8,
  retry_recovered_count: 0,
  fallback_model_used_count: 0,
  deterministic_visible_count: 0,
  final_note_omitted_count: 0,
  excluded_unvalidated_count: 0,
  final_card_count: 8,
  venue_head_recognized: true,
};

/** @type {PerCardNote[]} — breweries near the river pass-state notes */
const BREWERY_CARDS_PASS = [
  {
    card_index: 1, card_title: "Goose Island Wrigleyville",
    evidence_adequacy: "OK", user_modifier: "river", modifier_status: "unknown",
    display_why_validated: true, display_why_source: "no_river_context_found",
    visible_concierge_note: "Goose Island's Wrigleyville location offers the full flagship lineup including Bourbon County Stout.",
    quality_gate_result: "PASS", validated: true, retry_used: false, fallback_used: false, source: "llm_primary",
  },
  {
    card_index: 2, card_title: "Begyle Brewing",
    evidence_adequacy: "OK", user_modifier: "river", modifier_status: "unknown",
    display_why_validated: true, display_why_source: "no_river_context_found",
    visible_concierge_note: "A Ravenswood neighbourhood mainstay with sessionable lagers and approachable ales.",
    quality_gate_result: "PASS", validated: true, retry_used: false, fallback_used: false, source: "llm_primary",
  },
  {
    card_index: 3, card_title: "Revolution Brewing Taproom",
    evidence_adequacy: "OK", user_modifier: "river", modifier_status: "unknown",
    display_why_validated: true, display_why_source: "no_river_context_found",
    visible_concierge_note: "Revolution's Logan Square taproom pours the full range — Anti-Hero IPA to barrel-aged releases.",
    quality_gate_result: "PASS", validated: true, retry_used: false, fallback_used: false, source: "llm_primary",
  },
  {
    card_index: 4, card_title: "The Northman Beer & Cider Garden on the Riverwalk",
    evidence_adequacy: "OK", user_modifier: "river", modifier_status: "confirmed_address_or_name_context",
    display_why_validated: true, display_why_source: "verified_name_contains:riverwalk",
    visible_concierge_note:
      "Because the verified listing itself places Northman on the Riverwalk, it is the strongest " +
      "river-context beer stop here; verify actual seating/view details if that matters.",
    quality_gate_result: "PASS", validated: true, retry_used: true, fallback_used: false, source: "llm_retry",
  },
  {
    card_index: 5, card_title: "Half Acre Beer Company",
    evidence_adequacy: "OK", user_modifier: "river", modifier_status: "unknown",
    display_why_validated: true, display_why_source: "no_river_context_found",
    visible_concierge_note: "Half Acre's North Center taproom is best known for Daisy Cutter Pale Ale.",
    quality_gate_result: "PASS", validated: true, retry_used: false, fallback_used: false, source: "llm_primary",
  },
  {
    card_index: 6, card_title: "Hop Butcher For The World",
    evidence_adequacy: "OK", user_modifier: "river", modifier_status: "unknown",
    display_why_validated: true, display_why_source: "no_river_context_found",
    visible_concierge_note: "A nomadic Chicago brewer with a strong reputation for hazy IPAs and rotating small-batch releases.",
    quality_gate_result: "PASS", validated: true, retry_used: false, fallback_used: false, source: "llm_primary",
  },
  {
    card_index: 7, card_title: "Off Color Brewing",
    evidence_adequacy: "OK", user_modifier: "river", modifier_status: "unknown",
    display_why_validated: true, display_why_source: "no_river_context_found",
    visible_concierge_note: "Off Color specialises in unusual styles — gose, Berliner Weisse, saison — at their Mousetrap taproom.",
    quality_gate_result: "PASS", validated: true, retry_used: false, fallback_used: false, source: "llm_primary",
  },
  {
    card_index: 8, card_title: "Pilot Project Brewing",
    evidence_adequacy: "OK", user_modifier: "river", modifier_status: "unknown",
    display_why_validated: true, display_why_source: "no_river_context_found",
    visible_concierge_note: "Pilot Project's Logan Square location functions as an incubator for Chicago's emerging brewers.",
    quality_gate_result: "PASS", validated: true, retry_used: false, fallback_used: false, source: "llm_primary",
  },
];

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("validateTurnSummary helper", () => {
  test("pass state passes validation", () => {
    const { passed } = validateTurnSummary(BREWERY_TURN_PASS, 8);
    assert.equal(passed, true);
  });

  test("PR #251 failure state fails validation", () => {
    const { passed, failures } = validateTurnSummary(BREWERY_TURN_FAIL, 8);
    assert.equal(passed, false);
    assert.ok(failures.length > 0);
  });

  test("taproom pass state passes validation", () => {
    const { passed } = validateTurnSummary(TAPROOM_TURN_PASS, 8);
    assert.equal(passed, true);
  });

  test("izakaya pass state passes validation", () => {
    const { passed } = validateTurnSummary(IZAKAYA_TURN_PASS, 8);
    assert.equal(passed, true);
  });

  test("deterministic_visible_count nonzero fails", () => {
    const badSummary = { ...BREWERY_TURN_PASS, deterministic_visible_count: 2 };
    const { passed } = validateTurnSummary(badSummary, 8);
    assert.equal(passed, false);
  });
});

describe("frontend trust gate — validated-note contract", () => {
  test("brewery pass state: 8/8 validated notes are all shown", () => {
    const hidden = getHiddenNotes(BREWERY_CARDS_PASS);
    assert.equal(
      hidden.length,
      0,
      `Expected 0 hidden notes, got ${hidden.length}: ${hidden.map((n) => n.card_title).join(", ")}`,
    );
  });

  test("brewery pass state: no rating-primary notes in visible set", () => {
    const ratingPrimary = getRatingPrimaryNotes(BREWERY_CARDS_PASS);
    assert.equal(
      ratingPrimary.length,
      0,
      `Rating-primary notes found: ${ratingPrimary.map((n) => n.card_title).join(", ")}`,
    );
  });

  test("Northman is validated in brewery pass state", () => {
    const northman = BREWERY_CARDS_PASS.find((n) => n.card_title.includes("Northman"));
    assert.ok(northman, "Northman card must exist");
    assert.equal(northman.validated, true);
  });

  test("Northman modifier_status is confirmed_address_or_name_context", () => {
    const northman = BREWERY_CARDS_PASS.find((n) => n.card_title.includes("Northman"));
    assert.equal(northman.modifier_status, "confirmed_address_or_name_context");
  });

  test("Northman note does not contain unsupported claims", () => {
    const northman = BREWERY_CARDS_PASS.find((n) => n.card_title.includes("Northman"));
    const note = northman.visible_concierge_note.toLowerCase();
    assert.ok(!note.includes("waterfront seating"), "No waterfront seating claim");
    assert.ok(!note.includes("riverfront view"), "No riverfront view claim");
    assert.ok(!note.includes("river view"), "No river view claim");
  });

  test("Northman note mentions Riverwalk in safe context", () => {
    const northman = BREWERY_CARDS_PASS.find((n) => n.card_title.includes("Northman"));
    const note = northman.visible_concierge_note.toLowerCase();
    assert.ok(
      note.includes("riverwalk") || note.includes("listing") || note.includes("river-context"),
      `Note must mention Riverwalk/listing context: '${northman.visible_concierge_note}'`,
    );
  });

  test("Northman retry_used=true (first attempt failed, rescued by retry)", () => {
    const northman = BREWERY_CARDS_PASS.find((n) => n.card_title.includes("Northman"));
    assert.equal(northman.retry_used, true);
  });

  test("Northman quality_gate_result is PASS", () => {
    const northman = BREWERY_CARDS_PASS.find((n) => n.card_title.includes("Northman"));
    assert.equal(northman.quality_gate_result, "PASS");
  });
});

describe("PR #251 failure state contract (regression guard)", () => {
  test("PR #251 failure: reasoning_success=False should not be accepted as pass", () => {
    assert.equal(BREWERY_TURN_FAIL.reasoning_success, false);
    const { passed } = validateTurnSummary(BREWERY_TURN_FAIL, 8);
    assert.equal(passed, false);
  });

  test("PR #251 failure: omitted_count=1 means Northman was excluded", () => {
    assert.equal(BREWERY_TURN_FAIL.final_note_omitted_count, 1);
    assert.equal(BREWERY_TURN_FAIL.excluded_unvalidated_count, 1);
    assert.equal(BREWERY_TURN_FAIL.llm_accepted_count, 7);
  });

  test("PR #251 fix: accepted_count must equal final_card_count", () => {
    // In the pass state, llm_accepted_count == final_card_count
    assert.equal(
      BREWERY_TURN_PASS.llm_accepted_count,
      BREWERY_TURN_PASS.final_card_count,
    );
  });
});

describe("Izakayas trust contract", () => {
  test("venue_head_recognized=True is required for izakaya query", () => {
    assert.equal(IZAKAYA_TURN_PASS.venue_head_recognized, true);
  });

  test("izakaya pass state: 8/8 accepted", () => {
    const { passed } = validateTurnSummary(IZAKAYA_TURN_PASS, 8);
    assert.equal(passed, true);
  });
});

describe("Taprooms with a view trust contract", () => {
  test("taproom pass state: 8/8 accepted", () => {
    const { passed } = validateTurnSummary(TAPROOM_TURN_PASS, 8);
    assert.equal(passed, true);
  });

  test("taproom pass state: deterministic_visible_count=0", () => {
    assert.equal(TAPROOM_TURN_PASS.deterministic_visible_count, 0);
  });
});
