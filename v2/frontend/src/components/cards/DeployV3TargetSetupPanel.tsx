"use client";

import { useState, useEffect, useMemo } from "react";
import { useTargets, usePositions, useSetDeployTargets } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { InlineLoader } from "@/components/ui/Spinner";
import type { DeployV3ReadinessDiagnostic } from "@/lib/api";
import {
  TARGET_TOTAL_MIN,
  TARGET_TOTAL_MAX,
  parseInputPct,
  computeTotal,
  getMissingTickers,
  isSaveAllowed,
  buildTargetPayload,
  hydrateRows,
} from "@/lib/deploy-v3-target-helpers";

const MIN_TOTAL = TARGET_TOTAL_MIN;
const MAX_TOTAL = TARGET_TOTAL_MAX;

// ── Policy guidance section ───────────────────────────────────────────────────

function PolicyGuidance({
  policy,
}: {
  policy: DeployV3ReadinessDiagnostic["policy"] | undefined;
}) {
  if (!policy || policy.policy_valid) return null;
  return (
    <section
      aria-label="Deploy policy configuration guidance"
      className="card-glass p-4 border border-yellow-700/50 bg-yellow-900/10 space-y-2"
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-yellow-300">
        Deploy Policy — Environment Variables Required
      </p>
      <p className="text-xs text-text-secondary leading-snug">
        Deploy v3 exact-dollar sizing requires two Railway environment variables to be set on the backend service:
      </p>
      <ul className="text-xs text-text-secondary space-y-1 list-disc list-inside">
        {!policy.minimum_trade_configured && (
          <li>
            <span className="font-mono text-yellow-300">DEPLOY_MINIMUM_TRADE_USD</span>{" "}
            — minimum trade size in dollars (e.g. 10)
          </li>
        )}
        {!policy.rounding_policy_configured && (
          <li>
            <span className="font-mono text-yellow-300">DEPLOY_ROUNDING_POLICY</span>{" "}
            — allowed values:{" "}
            <span className="font-mono">WHOLE_DOLLAR</span>,{" "}
            <span className="font-mono">NEAREST_DOLLAR</span>,{" "}
            <span className="font-mono">NO_ROUNDING</span>
          </li>
        )}
      </ul>
      <p className="text-xs text-text-muted">
        Set these in Railway → your backend service → Variables, then redeploy.
        The app never reads or displays env var values.
      </p>
    </section>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

interface Props {
  readinessDiagnostic?: DeployV3ReadinessDiagnostic;
}

export function DeployV3TargetSetupPanel({ readinessDiagnostic }: Props) {
  const { data: existingTargets, isLoading: targetsLoading } = useTargets();
  const { data: positions, isLoading: positionsLoading } = usePositions();
  const setTargets = useSetDeployTargets();

  // Build ordered list of current tickers from live positions
  const positionTickers: string[] = useMemo(
    () =>
      positions
        ? [...positions].sort((a, b) => a.ticker.localeCompare(b.ticker)).map((p) => p.ticker)
        : [],
    [positions],
  );

  // Editable rows: ticker → string (% value as typed)
  const [rows, setRows] = useState<Record<string, string>>({});
  // Track which tickers the user has explicitly edited — those are never overwritten by refetches.
  const [touchedTickers, setTouchedTickers] = useState<Set<string>>(new Set());
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Hydrate rows when positions or saved targets change.
  // Touched tickers are never overwritten by late-arriving query data.
  useEffect(() => {
    if (!positionTickers.length) return;
    const saved = Object.fromEntries(
      (existingTargets ?? []).map((t) => [t.ticker, String(t.target_pct)])
    );
    setRows((prev) => hydrateRows(positionTickers, saved, prev, touchedTickers));
  // touchedTickers is intentionally excluded — it's a stable ref used inside hydrateRows
  // and reading it here would cause an infinite loop on every keystroke.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positionTickers, existingTargets]);

  const total = useMemo(() => computeTotal(rows), [rows]);
  const totalValid = total >= MIN_TOTAL && total <= MAX_TOTAL;

  const missingTickers = useMemo(
    () => getMissingTickers(positionTickers, rows),
    [positionTickers, rows],
  );
  const invalidTickers = positionTickers.filter((t) => {
    const v = rows[t];
    if (!v) return false;
    return parseInputPct(v) === null;
  });

  const canSave = invalidTickers.length === 0 && isSaveAllowed(positionTickers, rows);

  function handleChange(ticker: string, value: string) {
    setSaveError(null);
    setSaveSuccess(false);
    setTouchedTickers((prev) => new Set([...prev, ticker]));
    setRows((prev) => ({ ...prev, [ticker]: value }));
  }

  // "Use current weights as draft" — only when market_value data is available
  const canUseDraft = useMemo(() => {
    if (!positions || positions.length === 0) return false;
    return positions.every((p) => typeof p.market_value === "number" && p.market_value > 0);
  }, [positions]);

  function handleUseDraft() {
    if (!positions || !canUseDraft) return;
    const totalMv = positions.reduce((s, p) => s + (p.market_value ?? 0), 0);
    if (totalMv <= 0) return;
    const draft: Record<string, string> = {};
    for (const p of positions) {
      const pct = ((p.market_value ?? 0) / totalMv) * 100;
      draft[p.ticker] = pct.toFixed(2);
    }
    setRows(draft);
    setTouchedTickers(new Set(Object.keys(draft)));
    setSaveError(null);
    setSaveSuccess(false);
  }

  async function handleSave() {
    if (!canSave) return;
    setSaveError(null);
    setSaveSuccess(false);
    const payload = buildTargetPayload(positionTickers, rows);
    try {
      await setTargets.mutateAsync(payload);
      setSaveSuccess(true);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed. Please try again.");
    }
  }

  if (targetsLoading || positionsLoading) {
    return (
      <section
        aria-label="Target allocation setup"
        className="card-glass p-4 border border-border/80 space-y-2"
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          Target Allocations
        </p>
        <InlineLoader text="Loading targets…" />
      </section>
    );
  }

  return (
    <>
      <section
        aria-label="Target allocation setup"
        className="card-glass p-4 border border-border/80 space-y-3"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-2 flex-wrap">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
            Target Allocations
          </p>
          <span className="text-[10px] px-2 py-0.5 rounded-full border border-border bg-surface-elevated text-text-muted font-semibold uppercase tracking-wide">
            Setup
          </span>
        </div>

        {/* Explainer */}
        <div className="text-xs text-text-secondary space-y-1">
          <p>Targets tell Deploy what your ideal portfolio mix should be.</p>
          <p>Intel decides Buy / Hold / Trim / Sell. Targets tell Deploy how much to move.</p>
          <p className="text-text-muted">Total must be near 100% (98–102%).</p>
        </div>

        {positionTickers.length === 0 ? (
          <p className="text-sm text-text-muted">
            No current positions found. Sync your positions first.
          </p>
        ) : (
          <>
            {/* Ticker rows */}
            <div className="space-y-1.5">
              {positionTickers.map((ticker) => {
                const val = rows[ticker] ?? "";
                const parsed = parseInputPct(val);
                const isInvalid = val !== "" && parsed === null;
                const isEmpty = val === "";
                return (
                  <div key={ticker} className="flex items-center gap-2">
                    <span className="font-mono text-xs text-text-primary w-16 shrink-0">
                      {ticker}
                    </span>
                    <input
                      type="number"
                      min="0"
                      max="100"
                      step="0.01"
                      value={val}
                      onChange={(e) => handleChange(ticker, e.target.value)}
                      placeholder="e.g. 10.00"
                      className={cn(
                        "flex-1 min-w-0 bg-surface-elevated border rounded px-2 py-1 text-xs font-mono text-text-primary outline-none",
                        isInvalid
                          ? "border-red-500"
                          : isEmpty
                            ? "border-yellow-600"
                            : "border-border focus:border-accent",
                      )}
                      aria-label={`Target percentage for ${ticker}`}
                    />
                    <span className="text-xs text-text-muted w-4">%</span>
                    {isEmpty && (
                      <span className="text-[10px] text-yellow-300 whitespace-nowrap">
                        Missing
                      </span>
                    )}
                  </div>
                );
              })}
            </div>

            {/* Live total */}
            <div
              className={cn(
                "flex items-center justify-between px-3 py-2 rounded-md border text-xs font-semibold",
                totalValid
                  ? "border-emerald-700/50 bg-emerald-900/20 text-emerald-300"
                  : "border-yellow-700/50 bg-yellow-900/10 text-yellow-300",
              )}
            >
              <span>Total</span>
              <span className="font-mono">
                {total.toFixed(2)}%
                {!totalValid && (
                  <span className="font-normal ml-2 text-[10px]">
                    — must be 98–102%
                  </span>
                )}
              </span>
            </div>

            {/* Draft helper */}
            {canUseDraft && (
              <button
                type="button"
                onClick={handleUseDraft}
                className="text-xs text-text-muted underline underline-offset-2 hover:text-text-secondary transition-colors"
              >
                Use current weights as draft starting point (not a recommendation — review before saving)
              </button>
            )}

            {/* Missing tickers warning */}
            {missingTickers.length > 0 && (
              <p className="text-xs text-yellow-300">
                Enter targets for all tickers before saving:{" "}
                <span className="font-mono">{missingTickers.join(", ")}</span>
              </p>
            )}

            {/* Save feedback */}
            {saveError && (
              <p className="text-xs text-red-400">{saveError}</p>
            )}
            {saveSuccess && (
              <p className="text-xs text-emerald-300">
                Targets saved. Readiness will update shortly.
              </p>
            )}

            {/* Save button */}
            <button
              type="button"
              onClick={handleSave}
              disabled={!canSave || setTargets.isPending}
              className={cn(
                "w-full text-sm font-semibold py-2 rounded-md border transition-colors",
                canSave && !setTargets.isPending
                  ? "border-accent bg-accent/20 text-accent hover:bg-accent/30"
                  : "border-border bg-surface-elevated text-text-muted cursor-not-allowed opacity-60",
              )}
              aria-disabled={!canSave}
            >
              {setTargets.isPending ? "Saving…" : "Save Targets"}
            </button>
          </>
        )}
      </section>

      {/* Policy guidance — shown when policy env vars are missing */}
      <PolicyGuidance policy={readinessDiagnostic?.policy} />
    </>
  );
}
