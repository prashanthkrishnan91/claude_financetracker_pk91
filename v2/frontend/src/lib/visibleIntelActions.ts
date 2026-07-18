export const VISIBLE_INTEL_ACTIONS = ["BUY", "HOLD", "TRIM", "SELL"] as const;

export type VisibleIntelAction = (typeof VISIBLE_INTEL_ACTIONS)[number];

const LEGACY_TO_VISIBLE_MAP: Record<string, VisibleIntelAction> = {
  BUY: "BUY",
  SELL: "SELL",
  TRIM: "TRIM",
  REDUCE: "TRIM",
  HOLD: "HOLD",
  REVIEW: "HOLD",
};

export function normalizeVisibleIntelAction(action?: string | null): VisibleIntelAction {
  const raw = (action || "").trim().toUpperCase();
  return LEGACY_TO_VISIBLE_MAP[raw] ?? "HOLD";
}

export function isAllowedVisibleIntelAction(action?: string | null): action is VisibleIntelAction {
  return VISIBLE_INTEL_ACTIONS.includes((action || "").toUpperCase() as VisibleIntelAction);
}

// ── Canonical holding rationale extraction ────────────────────────────────────
//
// One extractor for "why is this the action?" text on a held-position card.
// Precedence (first non-empty wins after String() + trim):
//   1. card.why_text
//   2. card.detail_drawer_payload.asset_intelligence_context.why_this_action
//   3. card.action_text
// All-empty → null. A card with a null rationale must NOT be rendered as a
// recommendation card — the panel shows an honest exclusion note instead.

/** Structural subset of IntelV3HeldCard needed for rationale extraction. */
export interface RationaleSourceCard {
  ticker: string;
  why_text?: string | null;
  action_text?: string | null;
  detail_drawer_payload?: {
    asset_intelligence_context?: {
      why_this_action?: string | null;
    } | null;
  } | null;
}

export function extractHoldingRationale(card: RationaleSourceCard): string | null {
  const candidates = [
    card.why_text,
    card.detail_drawer_payload?.asset_intelligence_context?.why_this_action,
    card.action_text,
  ];
  for (const candidate of candidates) {
    if (candidate === null || candidate === undefined) continue;
    const text = String(candidate).trim();
    if (text) return text;
  }
  return null;
}

/**
 * Partition holding cards into renderable recommendation cards (a rationale
 * exists) and excluded tickers (no explanation available). Excluded cards are
 * never silently dropped — the caller must surface the excluded tickers.
 */
export function partitionRenderableCards<T extends RationaleSourceCard>(
  cards: readonly T[],
): { renderable: T[]; excludedTickers: string[] } {
  const renderable: T[] = [];
  const excludedTickers: string[] = [];
  for (const card of cards) {
    if (extractHoldingRationale(card) === null) {
      excludedTickers.push(card.ticker);
    } else {
      renderable.push(card);
    }
  }
  return { renderable, excludedTickers };
}
