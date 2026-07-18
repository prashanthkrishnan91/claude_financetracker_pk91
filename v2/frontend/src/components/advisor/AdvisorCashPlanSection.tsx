"use client";

/**
 * AdvisorCashPlanSection — Section C of the unified Advisor view.
 *
 * Evolved cash-plan surface over POST /api/advisor/paycheck-plan/preview
 * (local route handler adds the server-only cert secret). Supersedes
 * PaycheckPlanPreviewCard (which is left untouched for a later retirement
 * pass). Renders the additive `explanations` contract: selected allocations
 * with deterministic reasons, grouped not-selected buckets in plain English
 * with raw codes only behind a "Technical detail" expander, plan notes, and
 * trusted/blocked status.
 *
 * Vocabulary: this is always a PLAN. Never order/execute language.
 * HOLD is never presented as a reason to deploy cash (the backend only
 * selects BUY-evidence stocks and policy ETFs; this surface adds nothing).
 */

import { useState } from "react";
import { supabase } from "@/lib/supabase";
import { cn, formatCurrency } from "@/lib/utils";
import { Spinner } from "@/components/ui/Spinner";
import { PAYCHECK_PLAN_PREVIEW_ENDPOINT } from "@/lib/paycheck-plan-helpers";
import {
  allocationTotals,
  cashPlanErrorMessage,
  cashPlanStateCopy,
  deriveCashPlanState,
  deriveCashPlanTrust,
  formatPercentOfCash,
  groupNotSelected,
  validateCashPlanRequest,
  type AdvisorCashPlanResponse,
  type CashPlanBucketGroup,
  type CashPlanRequestBody,
  type CashPlanSelectedEntry,
} from "@/lib/advisor-cash-plan";

const TONE_TEXT_CLASS: Record<string, string> = {
  positive: "text-action-buy",
  caution: "text-action-trim",
  negative: "text-action-sell",
  neutral: "text-text-secondary",
};

class CashPlanHttpError extends Error {
  status: number;
  constructor(status: number) {
    super(`cash_plan_http_${status}`);
    this.status = status;
  }
}

async function postCashPlan(body: CashPlanRequestBody): Promise<AdvisorCashPlanResponse> {
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const res = await fetch(PAYCHECK_PLAN_PREVIEW_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) throw new CashPlanHttpError(res.status);
  return (await res.json()) as AdvisorCashPlanResponse;
}

export function AdvisorCashPlanSection({
  runState,
  onResult,
}: {
  /** Advisor run state from the readiness model (marks partial Intel runs). */
  runState?: string;
  /** Notifies the page so the trust drawer can reflect the latest plan. */
  onResult?: (response: AdvisorCashPlanResponse | null) => void;
}) {
  const [cashInput, setCashInput] = useState("");
  const [minTradeInput, setMinTradeInput] = useState("");
  const [maxPositionsInput, setMaxPositionsInput] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [response, setResponse] = useState<AdvisorCashPlanResponse | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [hadError, setHadError] = useState(false);

  const handlePlan = async () => {
    const validation = validateCashPlanRequest({
      cash: cashInput,
      minTrade: minTradeInput,
      maxPositions: maxPositionsInput,
    });
    if (!validation.ok) {
      setValidationError(validation.error);
      return;
    }
    setValidationError(null);
    setIsLoading(true);
    setHadError(false);
    setErrorStatus(null);
    try {
      const result = await postCashPlan(validation.request);
      setResponse(result);
      onResult?.(result);
    } catch (err) {
      setResponse(null);
      setHadError(true);
      setErrorStatus(err instanceof CashPlanHttpError ? err.status : null);
      onResult?.(null);
    } finally {
      setIsLoading(false);
    }
  };

  const showResult = !isLoading && (response !== null || hadError);
  const planState = showResult
    ? deriveCashPlanState({
        response,
        hadError,
        errorStatus,
        runState: runState ?? null,
      })
    : null;
  const stateCopy = planState ? cashPlanStateCopy(planState) : null;
  const trust = response ? deriveCashPlanTrust(response) : null;
  const buckets = response ? groupNotSelected(response.explanations) : [];
  const totals = allocationTotals(response);
  const selected = response?.explanations?.selected ?? [];
  const planNotes = response?.explanations?.plan_notes ?? [];
  const isErrorState = planState === "backend-error" || planState === "auth-error";

  return (
    <section aria-labelledby="advisor-cash-plan-heading" className="data-card p-4 space-y-4">
      <div className="space-y-1">
        <h2 id="advisor-cash-plan-heading" className="section-header">
          Cash plan
        </h2>
        <p className="text-xs text-text-muted">
          Enter available cash to see a deterministic, policy-driven plan for where it
          would go — and why the rest of the portfolio was not selected.
        </p>
      </div>

      {/* Input form */}
      <div className="space-y-3">
        <div className="flex items-end gap-2 flex-wrap">
          <div className="flex flex-col gap-1">
            <label htmlFor="advisor-cash-input" className="metric-label">
              Available cash (USD)
            </label>
            <input
              id="advisor-cash-input"
              type="number"
              inputMode="decimal"
              min={0}
              step="0.01"
              value={cashInput}
              onChange={(e) => setCashInput(e.target.value)}
              placeholder="0.00"
              className="w-44 rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm font-mono tabular-nums text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
            />
          </div>
          <button
            type="button"
            onClick={handlePlan}
            disabled={isLoading}
            className="btn-primary flex items-center gap-1.5"
          >
            {isLoading && <Spinner className="h-3 w-3" />}
            {isLoading ? "Planning…" : "Plan my cash"}
          </button>
        </div>

        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          aria-expanded={advancedOpen}
          aria-controls="advisor-cash-plan-advanced"
          className="btn-ghost"
        >
          {advancedOpen ? "Hide advanced constraints" : "Advanced constraints"}
        </button>

        {advancedOpen && (
          <div id="advisor-cash-plan-advanced" className="flex items-end gap-3 flex-wrap">
            <div className="flex flex-col gap-1">
              <label htmlFor="advisor-min-trade-input" className="metric-label">
                Min trade ($, ≥ 1)
              </label>
              <input
                id="advisor-min-trade-input"
                type="number"
                inputMode="decimal"
                min={1}
                step="1"
                value={minTradeInput}
                onChange={(e) => setMinTradeInput(e.target.value)}
                placeholder="25"
                className="w-32 rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm font-mono tabular-nums text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="advisor-max-positions-input" className="metric-label">
                Max positions (1–20)
              </label>
              <input
                id="advisor-max-positions-input"
                type="number"
                inputMode="numeric"
                min={1}
                max={20}
                step="1"
                value={maxPositionsInput}
                onChange={(e) => setMaxPositionsInput(e.target.value)}
                placeholder="5"
                className="w-32 rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm font-mono tabular-nums text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
              />
            </div>
          </div>
        )}

        {validationError && (
          <p className="text-xs text-action-sell" role="alert">
            {validationError}
          </p>
        )}
      </div>

      {/* Results — polite live region so plan updates are announced */}
      <div aria-live="polite" className="space-y-4">
        {isLoading && (
          <div className="flex items-center gap-2 text-text-muted text-sm py-4">
            <Spinner className="h-4 w-4" />
            <span>Building the plan…</span>
          </div>
        )}

        {showResult && isErrorState && stateCopy && (
          <div className="rounded-md border border-action-sell/30 bg-action-sell/5 p-3 space-y-2">
            <p className={cn("text-sm font-semibold", TONE_TEXT_CLASS[stateCopy.tone])}>
              {stateCopy.headline}
            </p>
            <p className="text-xs text-text-muted">{cashPlanErrorMessage(errorStatus)}</p>
            {planState === "backend-error" && (
              <button type="button" onClick={handlePlan} className="btn-secondary">
                Try again
              </button>
            )}
          </div>
        )}

        {showResult && !isErrorState && response && stateCopy && trust && (
          <div className="space-y-4">
            {/* Status + trust */}
            <div className="space-y-1.5">
              <p className={cn("text-sm font-semibold", TONE_TEXT_CLASS[stateCopy.tone])}>
                {stateCopy.headline}
              </p>
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={cn(
                    "text-[10px] px-1.5 py-0.5 rounded border font-semibold",
                    trust.trusted
                      ? "border-action-buy/30 bg-action-buy/10 text-action-buy"
                      : "border-action-trim/30 bg-action-trim/10 text-action-trim",
                  )}
                >
                  {trust.label}
                </span>
                {response.generated_at && (
                  <span className="text-[10px] text-text-muted">
                    Data as of {new Date(response.generated_at).toLocaleString()}
                  </span>
                )}
              </div>
              {trust.blocker && (
                <p className="text-xs text-text-muted">Blocker: {trust.blocker}</p>
              )}
            </div>

            {/* Plan notes — prominent for ETF-only explanations */}
            {planNotes.length > 0 && (
              <div className="rounded-md border border-accent/30 bg-accent/5 p-3 space-y-1">
                {planNotes.map((note) => (
                  <p key={note} className="text-xs text-text-primary leading-snug">
                    {note}
                  </p>
                ))}
              </div>
            )}

            {/* Selected allocations */}
            {selected.length > 0 && (
              <div className="space-y-2">
                <h3 className="metric-label">Planned allocations</h3>
                {selected.map((entry) => (
                  <SelectedAllocationRow key={entry.ticker} entry={entry} />
                ))}
              </div>
            )}
            {selected.length === 0 && response.planned_buys.length > 0 && (
              // Fallback when explanations are absent (older backend): planned_buys only.
              <div className="space-y-2">
                <h3 className="metric-label">Planned allocations</h3>
                {response.planned_buys.map((buy) => (
                  <div key={buy.ticker} className="rounded-md border border-border/60 p-2.5 flex items-center justify-between">
                    <span className="ticker-symbol text-sm">{buy.ticker}</span>
                    <span className="data-value-sm">{formatCurrency(buy.amount)}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Totals */}
            <div className="space-y-1 border-t border-border/50 pt-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-muted">Allocated cash</span>
                <span className="data-value-sm">{formatCurrency(totals.allocated)}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-muted">Unallocated cash</span>
                <span className="data-value-sm">{formatCurrency(totals.unallocated)}</span>
              </div>
            </div>

            {/* Not-selected buckets */}
            {buckets.length > 0 && (
              <div className="space-y-2">
                <h3 className="metric-label">Why the rest was not selected</h3>
                {buckets.map((group) => (
                  <BucketGroup key={group.bucket} group={group} />
                ))}
              </div>
            )}

            {/* Caveats */}
            {response.caveats.length > 0 && (
              <ul className="space-y-0.5">
                {response.caveats.map((caveat) => (
                  <li key={caveat} className="text-[10px] text-text-muted italic leading-snug">
                    {caveat}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

// ── Selected allocation row ───────────────────────────────────────────────────

function SelectedAllocationRow({ entry }: { entry: CashPlanSelectedEntry }) {
  const isStock = entry.asset_type === "equity";
  return (
    <div className="rounded-md border border-border/60 p-2.5 space-y-1.5">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="ticker-symbol text-sm">{entry.ticker}</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded border border-border bg-surface-elevated text-text-muted uppercase tracking-wide">
            {isStock ? "Stock" : entry.asset_type === "etf" ? "ETF" : entry.asset_type}
          </span>
          {isStock && entry.evidence && (
            <span className="action-badge-buy">
              {entry.evidence.action} · {entry.evidence.evidence_band}
            </span>
          )}
        </div>
        <div className="flex items-baseline gap-2">
          <span className="data-value-sm">{formatCurrency(entry.amount)}</span>
          <span className="text-[10px] text-text-muted font-mono tabular-nums">
            {formatPercentOfCash(entry.percent_of_deployable_cash)} of cash
          </span>
        </div>
      </div>
      {entry.policy_role && (
        <p className="text-[11px] text-text-secondary leading-snug">{entry.policy_role}</p>
      )}
      {entry.reasons.map((reason) => (
        <p key={reason} className="text-[11px] text-text-muted leading-snug">
          • {reason}
        </p>
      ))}
    </div>
  );
}

// ── Not-selected bucket group (collapsible) ───────────────────────────────────

function BucketGroup({ group }: { group: CashPlanBucketGroup }) {
  const [open, setOpen] = useState(false);
  const contentId = `advisor-bucket-${group.bucket}`;
  return (
    <div className="rounded-md border border-border/60 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={contentId}
        className="w-full flex items-center justify-between px-3 py-2 text-left text-xs text-text-secondary hover:text-text-primary transition-colors motion-reduce:transition-none"
      >
        <span className="font-semibold">{group.title}</span>
        <span className="text-text-muted font-mono tabular-nums">
          {group.entries.length}
          <span aria-hidden="true" className="ml-1.5">
            {open ? "▲" : "▼"}
          </span>
        </span>
      </button>
      {open && (
        <div id={contentId} className="border-t border-border/50 divide-y divide-border/40">
          {group.entries.map((entry, idx) => (
            <BucketEntryRow key={`${entry.ticker}-${idx}`} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}

function BucketEntryRow({
  entry,
}: {
  entry: CashPlanBucketGroup["entries"][number];
}) {
  const [detailOpen, setDetailOpen] = useState(false);
  return (
    <div className="px-3 py-2 space-y-1">
      <p className="text-[11px] text-text-secondary leading-snug">{entry.text}</p>
      {entry.technicalDetail && (
        <>
          <button
            type="button"
            onClick={() => setDetailOpen((v) => !v)}
            aria-expanded={detailOpen}
            className="text-[10px] text-text-muted hover:text-text-primary transition-colors motion-reduce:transition-none"
          >
            {detailOpen ? "Hide technical detail" : "Technical detail"}
          </button>
          {detailOpen && (
            <p className="text-[10px] text-text-muted font-mono break-words">
              {entry.technicalDetail}
            </p>
          )}
        </>
      )}
    </div>
  );
}
