/**
 * @jest-environment jsdom
 *
 * Render-level canonical-rationale contract for IntelV3Card.
 *
 * The card must consume extractHoldingRationale (why_text →
 * asset_intelligence_context.why_this_action → action_text, trimmed) for both
 * the visible rationale and the aria-label, and must fail closed — an
 * all-empty rationale renders NO card, never a blank recommendation card.
 */

import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot, Root } from "react-dom/client";

import { IntelV3Card } from "./IntelV3Card";
import { extractHoldingRationale } from "@/lib/visibleIntelActions";
import type { IntelV3HeldCard } from "@/lib/api";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function makeCard(overrides: Partial<IntelV3HeldCard> = {}): IntelV3HeldCard {
  return {
    ticker: "VTI",
    action: "BUY",
    conviction: "MEDIUM",
    evidence_band: "PARTIAL",
    why_text: "",
    action_text: "",
    detail_drawer_payload: undefined,
    ...overrides,
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
    root.render(<IntelV3Card card={card} onSelect={() => undefined} />);
  });
}

describe("IntelV3Card — canonical rationale consumption", () => {
  it("renders why_text when valid", () => {
    render(makeCard({ why_text: "Strong fundamentals support adding." }));
    expect(container.textContent).toContain("Strong fundamentals support adding.");
  });

  it("falls back to why_this_action when why_text is empty", () => {
    render(
      makeCard({
        why_text: "",
        detail_drawer_payload: {
          asset_intelligence_context: { why_this_action: "Core exposure remains the foundation." },
        } as IntelV3HeldCard["detail_drawer_payload"],
      }),
    );
    expect(container.textContent).toContain("Core exposure remains the foundation.");
  });

  it("falls back to action_text when the first two are empty", () => {
    render(makeCard({ why_text: "", action_text: "Position is on target." }));
    expect(container.textContent).toContain("Position is on target.");
  });

  it("whitespace-only why_text does not mask a valid action_text fallback", () => {
    render(makeCard({ why_text: "   \n  ", action_text: "Evidence supports holding." }));
    expect(container.textContent).toContain("Evidence supports holding.");
    expect(container.textContent).not.toMatch(/^\s+Evidence/);
  });

  it("all-empty rationale renders no card at all", () => {
    const card = makeCard({ why_text: "", action_text: "   " });
    expect(extractHoldingRationale(card)).toBeNull();
    render(card);
    expect(container.querySelector("button")).toBeNull();
    expect((container.textContent ?? "").trim()).toBe("");
  });

  it("aria-label carries the same canonical rationale shown visually", () => {
    const card = makeCard({ why_text: "  Trimmed rationale text.  " });
    const canonical = extractHoldingRationale(card)!;
    expect(canonical).toBe("Trimmed rationale text.");
    render(card);
    const btn = container.querySelector("button")!;
    expect(btn.getAttribute("aria-label")).toContain(canonical.slice(0, 80));
    expect(container.textContent).toContain(canonical);
  });
});
