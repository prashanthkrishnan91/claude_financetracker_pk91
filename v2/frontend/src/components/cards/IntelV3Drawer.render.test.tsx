/**
 * @jest-environment jsdom
 *
 * Render-level proof for IntelV3Drawer's "What's limiting this holding"
 * section (run_trust_contract_v1 signals).
 *
 * Release-blocker requirement: a clean, fully healthy holding — every
 * required/optional axis complete, no review required, full source
 * lineage, decision_constraints=[] — must render NO "What's limiting this
 * holding" section at all. Reassuring text under an alarming header would
 * be worse than no section; the honest answer for a clean holding is
 * silence, not a positive-only list.
 */

import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot, Root } from "react-dom/client";

import { IntelV3Drawer } from "./IntelV3Drawer";
import type { IntelV3HeldCard } from "@/lib/api";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function makeCard(overrides: Partial<IntelV3HeldCard["detail_drawer_payload"]> = {}): IntelV3HeldCard {
  return {
    ticker: "AAA",
    name: "Clean Corp",
    asset_type: "equity",
    action: "HOLD",
    conviction: "MEDIUM",
    evidence_band: "STRONG",
    portfolio_fit: "ON_TARGET",
    risk_level: "LOW",
    thesis_state: "active",
    why_text: "Fully sourced, healthy holding.",
    risk_text: "Risk assessed at low band.",
    action_text: "Fully sourced, healthy holding.",
    what_would_change_view: "New evidence.",
    fit_text: "On target.",
    evidence_text: "Fully sourced, healthy holding.",
    flags: [],
    source_snapshot_id: "snap-1",
    source_run_id: "run-1",
    updated_at: "2026-07-24T00:00:00Z",
    detail_drawer_payload: {
      rationale: "Fully sourced, healthy holding.",
      why_now: "",
      why_not_now: "",
      evidence_band: "STRONG",
      evidence_quality: "STRONG",
      attractiveness: "OK",
      price_context: "FAIR",
      portfolio_fit_raw: "ON_TARGET",
      risk_band: "LOW",
      blockers: [],
      suppression_reasons: {},
      schema_version: "v3.1",
      committee: { status: "source_validated" },
      evidence_explanation: null,
      source_lineage: { status: "full", has_source_refs: true },
      conflict_review_status: "not_required",
      decision_constraints: [],
      trust_status: "healthy",
      decision_bands: {
        evidence_quality: "STRONG", price_context: "FAIR",
        portfolio_fit: "ON_TARGET", risk_band: "LOW", attractiveness: "OK",
      },
      asset_intelligence_context: null,
      ...overrides,
    },
  } as unknown as IntelV3HeldCard;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(card: IntelV3HeldCard) {
  act(() => {
    root.render(<IntelV3Drawer card={card} onClose={() => {}} />);
  });
}

describe("IntelV3Drawer — 'What's limiting this holding' section", () => {
  it("clean healthy holding (decision_constraints=[], review not_required, full lineage) renders NO limiting-section", () => {
    render(makeCard());
    const text = container.textContent ?? "";
    expect(text).not.toContain("What's limiting this holding");
    expect(text).not.toContain("Other constraint noted");
  });

  it("a real constraint (e.g. portfolio_policy) still renders the section", () => {
    render(makeCard({
      decision_constraints: ["portfolio_policy"],
      portfolio_fit_raw: "OVERWEIGHT",
      decision_bands: {
        evidence_quality: "STRONG", price_context: "FAIR",
        portfolio_fit: "OVERWEIGHT", risk_band: "LOW", attractiveness: "OK",
      },
    }));
    const text = container.textContent ?? "";
    expect(text).toContain("What's limiting this holding");
    expect(text).toContain("Portfolio policy constraint");
  });

  it("a pending conflict review still renders the section even with decision_constraints=[]", () => {
    render(makeCard({ conflict_review_status: "pending" }));
    const text = container.textContent ?? "";
    expect(text).toContain("What's limiting this holding");
  });
});

describe("IntelV3Drawer — deterministic conflict handling (no LLM-review wording)", () => {
  it("a conflicted normal-weight holding shows HOLD/LOW conviction and the plain-English neutralization explanation, with zero LLM-review wording", () => {
    // Exact rationale text decision_policy_v1.decide() produces (unmodified)
    // for a conflict-neutralized HOLD — see decision_tasks_v1's
    // _apply_conflict_narrative / _ANALYSIS_CONFLICT_ACTION_REASON.
    const card = makeCard({
      conflict_review_status: "succeeded",
      rationale:
        "AAA: The directional signal was neutralized until the evidence " +
        "becomes more consistent.",
    });
    card.action = "HOLD";
    card.conviction = "LOW";
    render(card);
    const text = container.textContent ?? "";

    // Visible action/conviction — existing UI structure, no special-casing.
    expect(text).toContain("HOLD");
    expect(text).toContain("Low conviction");

    // Plain-English neutralization explanation is present (why confidence
    // was capped / directional signal suppressed).
    expect(text.toLowerCase()).toContain("neutralized");

    // No LLM-review vocabulary anywhere in the rendered drawer.
    const lower = text.toLowerCase();
    for (const forbidden of [
      "review passed",
      "senior investment research reviewer",
      "senior reviewer",
      "review model",
      "reconciliation by ai",
      "handled deterministically",
      "consensus",
    ]) {
      expect(lower).not.toContain(forbidden);
    }
  });
});
