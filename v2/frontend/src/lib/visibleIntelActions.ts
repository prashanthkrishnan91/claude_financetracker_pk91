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
