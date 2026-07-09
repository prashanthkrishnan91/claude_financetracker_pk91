"use client";

import { useState } from "react";
import { supabase } from "@/lib/supabase";
import { cn, formatCurrency } from "@/lib/utils";
import { InlineLoader } from "@/components/ui/Spinner";
import {
  PAYCHECK_PLAN_PREVIEW_ENDPOINT,
  PAYCHECK_PLAN_PREVIEW_SAMPLE_CASH,
  isPlanActionable,
  planBuyReasonBullets,
  previewStatusMeta,
  sortPlannedBuys,
  type PaycheckPlanPreviewResponse,
} from "@/lib/paycheck-plan-helpers";

const ADVICE_CAVEAT =
  "This is deterministic allocation guidance, not personalized investment advice.";

async function fetchPaycheckPlanPreview(cashToDeploy: number): Promise<PaycheckPlanPreviewResponse> {
  const {
    data: { session },
  } = await supabase.auth.getSession();

  const res = await fetch(PAYCHECK_PLAN_PREVIEW_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
    },
    body: JSON.stringify({ cash_to_deploy: cashToDeploy }),
  });

  if (!res.ok) {
    throw new Error(`Paycheck plan preview unavailable (${res.status})`);
  }
  return (await res.json()) as PaycheckPlanPreviewResponse;
}

export function PaycheckPlanPreviewCard() {
  const [cashInput, setCashInput] = useState(String(PAYCHECK_PLAN_PREVIEW_SAMPLE_CASH));
  const [preview, setPreview] = useState<PaycheckPlanPreviewResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleGenerate = async () => {
    const cashToDeploy = Number(cashInput);
    if (!Number.isFinite(cashToDeploy) || cashToDeploy <= 0) {
      setErrorMessage("Enter an amount greater than 0.");
      return;
    }
    setIsLoading(true);
    setErrorMessage(null);
    try {
      const result = await fetchPaycheckPlanPreview(cashToDeploy);
      setPreview(result);
    } catch {
      setErrorMessage("Paycheck plan preview unavailable. Please try again later.");
      setPreview(null);
    } finally {
      setIsLoading(false);
    }
  };

  const statusMeta = preview ? previewStatusMeta(preview.status) : null;
  const actionable = preview ? isPlanActionable(preview) : false;
  const orderedBuys = preview ? sortPlannedBuys(preview.planned_buys) : [];

  return (
    <section aria-label="Paycheck plan preview" className="card-glass p-4 border border-border/80 space-y-4">
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          What should I buy next?
        </p>
        <span className="text-[10px] px-2 py-0.5 rounded-full border border-border bg-surface-elevated text-text-muted font-semibold uppercase tracking-wide">
          Read-only
        </span>
      </div>

      <div className="flex items-end gap-2 flex-wrap">
        <label className="flex flex-col gap-1 text-xs text-text-muted">
          Available cash
          <input
            type="number"
            min={0}
            step="0.01"
            value={cashInput}
            onChange={(e) => setCashInput(e.target.value)}
            className="w-40 rounded border border-border bg-surface px-2 py-1.5 text-sm text-text-primary"
            aria-label="Available cash to deploy"
          />
        </label>
        <button
          type="button"
          onClick={handleGenerate}
          disabled={isLoading}
          className="rounded bg-accent px-3 py-1.5 text-xs font-semibold text-black disabled:opacity-50"
        >
          {isLoading ? "Generating…" : "Preview plan"}
        </button>
      </div>

      {isLoading && <InlineLoader text="Generating paycheck plan preview…" />}

      {!isLoading && errorMessage && (
        <p className="text-sm text-action-sell">{errorMessage}</p>
      )}

      {!isLoading && preview && statusMeta && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={cn("text-sm font-semibold", statusMeta.cls)}>{statusMeta.label}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded border border-border bg-surface-elevated text-text-muted">
              Data freshness: {preview.data_freshness_status}
            </span>
          </div>

          <p className="text-xs text-text-muted">
            Cash entered: <span className="font-mono text-text-primary">{formatCurrency(preview.cash_to_deploy)}</span>
          </p>

          {!actionable ? (
            <div className="rounded border border-action-sell/30 bg-action-sell/5 p-3 space-y-1.5">
              <p className="text-sm font-semibold text-action-sell">
                This plan is not actionable yet.
              </p>
              <p className="text-xs text-text-muted">
                {preview.next_required_fix
                  ? `Next required fix: ${preview.next_required_fix}`
                  : "Underlying portfolio data needs to be fully refreshed before a plan can be confirmed."}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {orderedBuys.length === 0 && (
                <p className="text-xs text-text-muted">No planned buys for this cash amount.</p>
              )}
              {orderedBuys.map((buy, idx) => (
                <div
                  key={buy.ticker}
                  className={cn(
                    "rounded border border-border/60 p-2.5 space-y-1",
                    idx === 0 && "border-accent/40 bg-accent/5",
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-text-primary">{buy.ticker}</span>
                    <span className="font-mono text-sm text-text-primary">{formatCurrency(buy.amount)}</span>
                  </div>
                  {planBuyReasonBullets(buy).map((bullet) => (
                    <p key={bullet} className="text-[11px] text-text-muted leading-snug">
                      • {bullet}
                    </p>
                  ))}
                </div>
              ))}

              <div className="flex items-center justify-between text-xs pt-1">
                <span className="text-text-muted">Allocated cash</span>
                <span className="font-mono text-text-primary">
                  {formatCurrency(preview.allocation_summary.allocated_cash)}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-text-muted">Unallocated cash</span>
                <span className="font-mono text-text-primary">
                  {formatCurrency(preview.allocation_summary.unallocated_cash)}
                </span>
              </div>
            </div>
          )}

          <p className="text-[10px] text-text-muted italic">{ADVICE_CAVEAT}</p>
        </div>
      )}
    </section>
  );
}
