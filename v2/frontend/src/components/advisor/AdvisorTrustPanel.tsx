"use client";

/**
 * AdvisorTrustPanel — Section D of the unified Advisor view.
 *
 * Collapsible operational drawer (closed by default; not a nav destination).
 * When something is degraded or blocked it explains — from the readiness
 * model, the run state machine, the financial-truth contract, and the last
 * cash-plan response — what is wrong, whether values disagree, whether
 * informational holding actions still show, whether the cash plan is
 * blocked, and the exact repair action needed in plain English.
 *
 * Healthy rule (hard): "Nothing is degraded" appears ONLY when the Intel
 * snapshot is certified and current AND portfolio financial truth is
 * certified AND current-price truth is ok AND books reconciliation passes
 * AND (no cash plan was requested OR its numbers were trusted). Any
 * "unknown" truth dimension yields the distinct honest state "Some truth
 * checks could not be run yet" — never healthy.
 */

import { useState } from "react";
import { cn } from "@/lib/utils";
import {
  DataUnavailableCallout,
  TrustStatusRow,
} from "@/components/cards/TrustPrimitives";
import type { AdvisorReadinessModel } from "@/lib/advisor-readiness";
import {
  deriveTrustHealth,
  type AdvisorTruthContract,
} from "@/lib/advisor-truth";
import {
  deriveCashPlanTrust,
  repairActionFromFix,
  type AdvisorCashPlanResponse,
  type CashPlanRepairAction,
} from "@/lib/advisor-cash-plan";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60";

function deriveRepairAction(
  model: AdvisorReadinessModel,
  plan: AdvisorCashPlanResponse | null,
): CashPlanRepairAction {
  // Run-side repairs take precedence — nothing downstream can be trusted
  // until a certified snapshot exists.
  if (model.snapshotState === "missing" || model.snapshotState === "uncertified") {
    return "Run Intel required";
  }
  if (model.snapshotState === "stale") return "Run Intel required";
  // Plan-side repairs from the exact backend fix.
  const fromFix = repairActionFromFix(plan?.next_required_fix ?? null);
  if (fromFix) return fromFix;
  return null;
}

export function AdvisorTrustPanel({
  model,
  plan,
  truth,
}: {
  model: AdvisorReadinessModel;
  plan: AdvisorCashPlanResponse | null;
  truth: AdvisorTruthContract | null;
}) {
  const [open, setOpen] = useState(false);
  const [repairDetailOpen, setRepairDetailOpen] = useState(false);

  const planTrust = plan ? deriveCashPlanTrust(plan) : null;
  const planBlocked = plan ? plan.status !== "ready" || plan.trusted !== true : null;
  const snapshotExists =
    model.snapshotState === "certified" ||
    model.snapshotState === "stale" ||
    model.snapshotState === "uncertified";
  const reconcileSuspected =
    (plan?.next_required_fix ?? "").toLowerCase().includes("reconcil") ||
    truth?.reconciliation === "degraded" ||
    truth?.reconciliation === "blocked";
  const repairAction = deriveRepairAction(model, plan);

  const trustHealth = deriveTrustHealth({
    intelCertifiedCurrent: model.ready,
    truth,
    planRequested: plan !== null,
    numericPlanTrusted: plan === null ? null : planBlocked === false,
  });

  const problems: string[] = [];
  if (model.snapshotState === "missing") {
    problems.push("No certified Intel snapshot exists yet.");
  }
  if (model.snapshotState === "error") {
    problems.push("The Intel snapshot could not be loaded.");
  }
  if (model.snapshotState === "stale") {
    problems.push("The certified snapshot is stale.");
  }
  if (model.snapshotState === "uncertified") {
    problems.push("A snapshot exists but is not fully certified.");
  }
  if (model.run.state === "failed") {
    problems.push(model.run.nextActionSentence);
  }
  // Financial-truth problems (degraded/blocked dimensions from the endpoint).
  problems.push(...trustHealth.truthProblems);
  if (planTrust && !planTrust.trusted && planTrust.blocker) {
    problems.push(planTrust.blocker);
  }

  // Hard healthy rule — full conjunction, never satisfied by unknowns.
  const healthy = trustHealth.healthy && problems.length === 0;
  const hasUnknownChecks = trustHealth.unknownDimensions.length > 0;

  // Both values known and disagreeing — show the actual numbers honestly.
  const showDisagreeingValues =
    truth !== null &&
    truth.snapshot_value !== null &&
    truth.position_derived_value !== null &&
    (truth.reconciliation === "degraded" || truth.reconciliation === "blocked");

  return (
    <section aria-labelledby="advisor-trust-heading" className="data-card overflow-hidden">
      <h2 id="advisor-trust-heading" className="m-0">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="advisor-trust-content"
          className={cn(
            "w-full flex items-center justify-between px-4 py-3 min-h-[40px] text-left transition-colors motion-reduce:transition-none hover:bg-surface-elevated/40",
            FOCUS_RING,
          )}
        >
          <span className="section-header">Trust &amp; data health</span>
          <span className="text-[10px] text-text-muted" aria-hidden="true">
            {open ? "▲" : "▼"}
          </span>
        </button>
      </h2>

      <div
        id="advisor-trust-content"
        hidden={!open}
        className="border-t border-border/50 px-4 py-3 space-y-4"
      >
          {healthy ? (
            <p className="text-xs text-text-secondary">
              Nothing is degraded right now. The snapshot is certified, financial
              truth is certified, prices are current, and the books reconcile
              {plan ? " — and the last cash plan was trusted." : ". No cash plan has been requested yet."}
            </p>
          ) : (
            <div className="space-y-1.5">
              <h3 className="metric-label">What is wrong</h3>
              {problems.length === 0 && !hasUnknownChecks ? (
                <DataUnavailableCallout label="No specific problem reported — data may still be loading." />
              ) : (
                <ul className="space-y-1">
                  {problems.map((problem) => (
                    <li key={problem} className="text-xs text-text-secondary leading-snug">
                      {problem}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Unknown truth checks — distinct honest state, never conflated with OK */}
          {!healthy && hasUnknownChecks && (
            <div className="space-y-1.5">
              <h3 className="metric-label">Truth checks not yet run</h3>
              <p className="text-xs text-text-secondary leading-snug">
                Some truth checks could not be run yet:
              </p>
              <ul className="space-y-0.5">
                {trustHealth.unknownDimensions.map((dimension) => (
                  <li key={dimension} className="text-xs text-text-muted leading-snug">
                    {dimension} — unknown
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Disagreeing values — only when the truth check actually flags divergence */}
          {reconcileSuspected && (
            <div className="space-y-1.5">
              <h3 className="metric-label">Disagreeing values</h3>
              <p className="text-xs text-text-secondary leading-snug">
                Snapshot-derived and position-derived portfolio values diverge
                beyond tolerance.
              </p>
              {showDisagreeingValues ? (
                <p className="text-xs text-text-secondary font-mono tabular-nums">
                  Snapshot: {truth.snapshot_value!.toFixed(2)} · Positions:{" "}
                  {truth.position_derived_value!.toFixed(2)}
                </p>
              ) : (
                <DataUnavailableCallout label="The individual disagreeing numbers are not available." />
              )}
            </div>
          )}

          {/* Status rows */}
          <div className="divide-y divide-border/40">
            <TrustStatusRow
              label="Informational holding actions"
              status={snapshotExists ? "ok" : "unavailable"}
              detail={
                snapshotExists
                  ? "Holding action cards still show for context, with their certification status labeled."
                  : "Not shown — no snapshot exists to display."
              }
            />
            <TrustStatusRow
              label="Cash-plan trust"
              status={
                plan === null ? "unavailable" : planBlocked ? "blocked" : "ok"
              }
              detail={
                plan === null
                  ? "No cash plan has been requested in this session."
                  : planBlocked
                    ? "The last cash plan was degraded or blocked — its numbers are directional only."
                    : "The last cash plan was trusted."
              }
            />
          </div>

          {/* Exact repair action */}
          <div className="space-y-1.5">
            <h3 className="metric-label">Repair action</h3>
            {healthy ? (
              <p className="text-xs text-text-secondary">No repair needed.</p>
            ) : trustHealth.repairPlain ? (
              <div className="space-y-1">
                <p className="text-xs font-medium text-text-primary">
                  {trustHealth.repairPlain}
                </p>
                {trustHealth.repairTechnical && (
                  <div>
                    <button
                      type="button"
                      onClick={() => setRepairDetailOpen((v) => !v)}
                      aria-expanded={repairDetailOpen}
                      className={cn(
                        "text-[10px] text-text-muted hover:text-text-primary transition-colors motion-reduce:transition-none",
                        FOCUS_RING,
                      )}
                    >
                      {repairDetailOpen ? "▲ Hide technical detail" : "▼ Technical detail"}
                    </button>
                    {repairDetailOpen && (
                      <p className="mt-1 text-[10px] text-text-muted font-mono break-words">
                        {trustHealth.repairTechnical}
                      </p>
                    )}
                  </div>
                )}
              </div>
            ) : repairAction ? (
              <p className="text-xs font-medium text-text-primary">
                {repairAction === "new portfolio snapshot required" && "New portfolio snapshot required."}
                {repairAction === "current-price repair required" && "Current-price repair required."}
                {repairAction === "Run Intel required" && "Run Intel required."}
              </p>
            ) : (
              <DataUnavailableCallout label="No specific repair action reported yet." />
            )}
          </div>
      </div>
    </section>
  );
}
