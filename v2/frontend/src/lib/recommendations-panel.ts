/**
 * Pure helpers for the deterministic recommendations panel.
 * No React, no fetch — safe for unit tests.
 *
 * HARD RULE: a recommendation without a non-empty one-line rationale must
 * never render. The backend enforces this; the client enforces it again here
 * so a contract regression can never show an unexplained call.
 */

import type { RecommendationPanelItem } from "./api";

/** True when the item carries a real, non-whitespace rationale. */
export function hasRenderableRationale(
  item: Pick<RecommendationPanelItem, "rationale">
): boolean {
  return typeof item.rationale === "string" && item.rationale.trim().length > 0;
}

/**
 * Filter panel items down to the ones allowed to render.
 * Items with an empty or missing rationale are dropped — never shown.
 */
export function renderableRecommendations<
  T extends Pick<RecommendationPanelItem, "rationale">
>(items: T[] | null | undefined): T[] {
  if (!items) return [];
  return items.filter(hasRenderableRationale);
}

/**
 * Show engine_reason as a secondary line only when it exists and adds
 * something beyond the rationale (case/whitespace-insensitive comparison).
 */
export function secondaryEngineReason(
  item: Pick<RecommendationPanelItem, "rationale" | "engine_reason">
): string | null {
  const reason = item.engine_reason?.trim();
  if (!reason) return null;
  const rationale = (item.rationale ?? "").trim();
  if (reason.toLowerCase() === rationale.toLowerCase()) return null;
  return reason;
}

/** Action badge classes — mirrors the app-wide action color conventions. */
export const RECOMMENDATION_ACTION_STYLES: Record<string, string> = {
  BUY: "bg-green-500/10 text-green-400 border-green-500/30",
  HOLD: "bg-blue-500/10 text-blue-400 border-blue-500/30",
  TRIM: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30",
  SELL: "bg-red-500/10 text-red-400 border-red-500/30",
};

export function actionBadgeStyle(action: string): string {
  return (
    RECOMMENDATION_ACTION_STYLES[action?.toUpperCase()] ??
    "bg-surface-elevated text-text-muted border-border"
  );
}
