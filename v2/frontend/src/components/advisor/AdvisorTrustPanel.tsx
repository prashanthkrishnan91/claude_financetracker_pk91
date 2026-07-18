"use client";

/**
 * AdvisorTrustPanel — Section D of the unified Advisor view.
 *
 * Collapsible operational drawer (closed by default; not a nav destination).
 * When something is degraded or blocked it explains — from the readiness
 * model, the run state machine, and the last cash-plan response — what is
 * wrong, whether values disagree, whether informational holding actions
 * still show, whether the cash plan is blocked, and the exact repair action
 * needed in plain English. Never fabricates a plan; honest empty state.
 */

import { useState } from "react";
import {
  DataUnavailableCallout,
  TrustStatusRow,
} from "@/components/cards/TrustPrimitives";
import type { AdvisorReadinessModel } from "@/lib/advisor-readiness";
import {
  deriveCashPlanTrust,
  repairActionFromFix,
  type AdvisorCashPlanResponse,
  type CashPlanRepairAction,
} from "@/lib/advisor-cash-plan";

function deriveRepairAction(
  model: AdvisorReadinessModel,
  plan: AdvisorCashPlanResponse | null,
): CashPlanRepairAction {
  // Run-side repairs take precedence — nothing downstream can be trusted
  // until a certified snapshot exists.
  if (model.run.state === "partial") return "another bounded batch required";
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
}: {
  model: AdvisorReadinessModel;
  plan: AdvisorCashPlanResponse | null;
}) {
  const [open, setOpen] = useState(false);

  const planTrust = plan ? deriveCashPlanTrust(plan) : null;
  const planBlocked = plan ? plan.status !== "ready" || plan.trusted !== true : null;
  const snapshotExists =
    model.snapshotState === "certified" ||
    model.snapshotState === "stale" ||
    model.snapshotState === "uncertified";
  const reconcileSuspected =
    (plan?.next_required_fix ?? "").toLowerCase().includes("reconcil");
  const repairAction = deriveRepairAction(model, plan);

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
  if (model.run.state === "partial") {
    problems.push(model.run.nextActionSentence);
  }
  if (model.run.state === "queue_only" || model.run.state === "failed") {
    problems.push(model.run.nextActionSentence);
  }
  if (planTrust && !planTrust.trusted && planTrust.blocker) {
    problems.push(planTrust.blocker);
  }

  const healthy = problems.length === 0 && model.ready && (plan === null || planBlocked === false);

  return (
    <section aria-labelledby="advisor-trust-heading" className="data-card overflow-hidden">
      <h2 id="advisor-trust-heading" className="m-0">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="advisor-trust-content"
          className="w-full flex items-center justify-between px-4 py-3 text-left transition-colors motion-reduce:transition-none hover:bg-surface-elevated/40"
        >
          <span className="section-header">Trust &amp; data health</span>
          <span className="text-[10px] text-text-muted" aria-hidden="true">
            {open ? "▲" : "▼"}
          </span>
        </button>
      </h2>

      {open && (
        <div id="advisor-trust-content" className="border-t border-border/50 px-4 py-3 space-y-4">
          {healthy ? (
            <p className="text-xs text-text-secondary">
              Nothing is degraded right now. The snapshot is certified
              {plan ? " and the last cash plan was trusted." : ". No cash plan has been requested yet."}
            </p>
          ) : (
            <div className="space-y-1.5">
              <h3 className="metric-label">What is wrong</h3>
              {problems.length === 0 ? (
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

          {/* Disagreeing values — only when the response actually flags divergence */}
          {reconcileSuspected && (
            <div className="space-y-1.5">
              <h3 className="metric-label">Disagreeing values</h3>
              <p className="text-xs text-text-secondary leading-snug">
                The plan reports that snapshot-derived and position-derived portfolio
                values diverge beyond tolerance.
              </p>
              <DataUnavailableCallout label="The preview response does not expose the individual disagreeing numbers." />
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
              label="Cash plan"
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
            {repairAction ? (
              <p className="text-xs font-medium text-text-primary">
                {repairAction === "new portfolio snapshot required" && "New portfolio snapshot required."}
                {repairAction === "current-price repair required" && "Current-price repair required."}
                {repairAction === "Run Intel required" && "Run Intel required."}
                {repairAction === "another bounded batch required" && "Another bounded Intel batch required — use Continue Intel run above."}
              </p>
            ) : healthy ? (
              <p className="text-xs text-text-secondary">No repair needed.</p>
            ) : (
              <DataUnavailableCallout label="No specific repair action reported yet." />
            )}
          </div>
        </div>
      )}
    </section>
  );
}
