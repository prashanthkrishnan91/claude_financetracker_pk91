/**
 * Pure data constants for the IntelV3 primitive design system.
 *
 * No JSX, no React — safe to import in Node test environments.
 * Used by the Positions ledger (portfolio page) for action tokens and the
 * canonical Coming-Later caption.
 */

import type { IntelV3Action } from "@/lib/api";

const ACTION_GLYPHS: Record<IntelV3Action, string> = {
  BUY:  "↑",
  HOLD: "─",
  TRIM: "↓",
  SELL: "✕",
};

export const ACTION_TOKEN_STYLES: Record<
  IntelV3Action,
  { text: string; bg: string; border: string; glyph: string; dot: string }
> = {
  BUY:  { text: "text-action-buy",  bg: "bg-action-buy/10",  border: "border-action-buy/30",  glyph: ACTION_GLYPHS.BUY,  dot: "bg-action-buy"  },
  HOLD: { text: "text-action-hold", bg: "bg-action-hold/10", border: "border-action-hold/30", glyph: ACTION_GLYPHS.HOLD, dot: "bg-action-hold" },
  TRIM: { text: "text-action-trim", bg: "bg-action-trim/10", border: "border-action-trim/30", glyph: ACTION_GLYPHS.TRIM, dot: "bg-action-trim" },
  SELL: { text: "text-action-sell", bg: "bg-action-sell/10", border: "border-action-sell/30", glyph: ACTION_GLYPHS.SELL, dot: "bg-action-sell" },
};

export const COMING_LATER_CANONICAL_CAPTION =
  "This intelligence module is being prepared. The next intelligence stage will surface it here.";
