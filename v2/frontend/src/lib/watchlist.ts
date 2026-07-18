/**
 * Pure helpers for the watchlist view. No React, no fetch — safe for tests.
 * The app surfaces price criteria the user set; it never picks stocks.
 */

import type { WatchlistCriteriaType, WatchlistItem } from "./api";

/** Plain-English label for a criteria type. Never shows the raw key. */
export function criteriaTypeLabel(type: WatchlistCriteriaType | string): string {
  switch (type) {
    case "price_below":
      return "Price at or below";
    case "price_above":
      return "Price at or above";
    default:
      return "Price criteria";
  }
}

export type CriteriaStatus = "met" | "not_met" | "unknown";

/**
 * Honest criteria status: "unknown" when the backend could not evaluate
 * (criteria_met === null, usually missing price data) — never fabricated.
 */
export function criteriaStatus(
  item: Pick<WatchlistItem, "criteria_met">
): CriteriaStatus {
  if (item.criteria_met === true) return "met";
  if (item.criteria_met === false) return "not_met";
  return "unknown";
}

export function criteriaStatusLabel(status: CriteriaStatus): string {
  switch (status) {
    case "met":
      return "Criteria met";
    case "not_met":
      return "Not met yet";
    case "unknown":
      return "No price data yet";
  }
}
