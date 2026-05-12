/**
 * Pure helpers for the Deploy v3 target allocation setup flow.
 * No React, no Supabase, no external dependencies — safe to import in tests.
 */

export const TARGET_TOTAL_MIN = 98;
export const TARGET_TOTAL_MAX = 102;

export function parseInputPct(val: string): number | null {
  const n = parseFloat(val);
  if (!isFinite(n) || n < 0) return null;
  return n;
}

export function computeTotal(rows: Record<string, string>): number {
  return Object.values(rows).reduce((sum, v) => {
    const n = parseFloat(v);
    return sum + (isFinite(n) && n >= 0 ? n : 0);
  }, 0);
}

/** Returns tickers that are missing or have invalid (non-numeric / negative) values. */
export function getMissingTickers(
  tickers: string[],
  rows: Record<string, string>,
): string[] {
  return tickers.filter((t) => {
    const v = rows[t];
    if (!v) return true;
    return parseInputPct(v) === null;
  });
}

/** Returns true when saving is allowed (all tickers present, total in range). */
export function isSaveAllowed(
  tickers: string[],
  rows: Record<string, string>,
): boolean {
  if (tickers.length === 0) return false;
  if (getMissingTickers(tickers, rows).length > 0) return false;
  const total = computeTotal(rows);
  return total >= TARGET_TOTAL_MIN && total <= TARGET_TOTAL_MAX;
}

/** Build the PUT /api/v1/portfolio/targets payload from current rows. */
export function buildTargetPayload(
  tickers: string[],
  rows: Record<string, string>,
): { ticker: string; target_pct: number }[] {
  return tickers.map((ticker) => ({
    ticker,
    target_pct: parseInputPct(rows[ticker])!,
  }));
}

/** Merge positions + saved targets + previous rows + touched set into next rows.
 *
 * Invariants:
 * - Touched tickers (user has typed) are never overwritten by refetches.
 * - Untouched tickers use the saved target value if present, else "".
 * - Tickers not in positionTickers are dropped (position removed).
 */
export function hydrateRows(
  positionTickers: string[],
  savedTargets: Record<string, string>,
  prevRows: Record<string, string>,
  touched: Set<string>,
): Record<string, string> {
  const next: Record<string, string> = {};
  for (const ticker of positionTickers) {
    if (touched.has(ticker)) {
      next[ticker] = prevRows[ticker] ?? "";
    } else {
      next[ticker] = savedTargets[ticker] ?? "";
    }
  }
  return next;
}
