"use client";

/**
 * Advisor — the single user-facing recommendation surface.
 *
 * Consolidation to three primary views (Positions / Advisor / Watchlist).
 * Sections:
 *   A — Readiness (trust rows, snapshot age, Run Intel state machine)
 *   B — Holding actions (existing IntelV3Cockpit, mounted unconditionally —
 *       it is the deterministic Intel v3 surface; no env flag gate here)
 *   C — Cash plan (evolved paycheck-plan preview with explanations)
 *   D — Trust drawer (collapsible operational detail, closed by default)
 *
 * No visible action comes from any legacy LLM/agent surface — only the
 * IntelV3Cockpit and the deterministic paycheck endpoint. No new polling is
 * added (the cockpit's built-in snapshot polling is the only interval).
 *
 * Deep link: /dashboard/advisor?section=cash-plan scrolls to and focuses the
 * cash-plan section on mount (useSearchParams inside <Suspense> as Next
 * requires for static builds).
 */

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useIntelV3Snapshot, useRunIntelV3 } from "@/lib/hooks";
import { IntelV3Cockpit } from "@/components/cards/IntelV3Cockpit";
import { AdvisorReadinessPanel } from "@/components/advisor/AdvisorReadinessPanel";
import { AdvisorCashPlanSection } from "@/components/advisor/AdvisorCashPlanSection";
import { AdvisorTrustPanel } from "@/components/advisor/AdvisorTrustPanel";
import { deriveAdvisorReadiness } from "@/lib/advisor-readiness";
import type { AdvisorCashPlanResponse } from "@/lib/advisor-cash-plan";

function CashPlanDeepLink({
  targetRef,
}: {
  targetRef: React.RefObject<HTMLDivElement>;
}) {
  const searchParams = useSearchParams();
  const section = searchParams.get("section");

  useEffect(() => {
    if (section !== "cash-plan") return;
    const el = targetRef.current;
    if (!el) return;
    // No smooth scrolling — reduced-motion safe by default.
    el.scrollIntoView({ block: "start" });
    el.focus({ preventScroll: true });
  }, [section, targetRef]);

  return null;
}

export default function AdvisorPage() {
  const snapshotQuery = useIntelV3Snapshot();
  const runMutation = useRunIntelV3();
  const [lastPlan, setLastPlan] = useState<AdvisorCashPlanResponse | null>(null);
  const cashPlanRef = useRef<HTMLDivElement>(null);

  const model = deriveAdvisorReadiness(
    {
      snapshot: snapshotQuery.data ?? null,
      isLoading: snapshotQuery.isLoading,
      isError: snapshotQuery.isError,
      errorMessage:
        snapshotQuery.error instanceof Error ? snapshotQuery.error.message : null,
    },
    {
      isRunPending: runMutation.isPending,
      isRunError: runMutation.isError,
      lastRunResult: runMutation.data ?? null,
    },
  );

  return (
    <>
      <Suspense fallback={null}>
        <CashPlanDeepLink targetRef={cashPlanRef} />
      </Suspense>

      <header className="page-header">
        <div className="page-header-inner">
          <div>
            <h1 className="page-title">Advisor</h1>
            <p className="text-xs text-text-muted">
              What should I buy now, how much, and why
            </p>
          </div>
        </div>
      </header>

      <main className="page-main">
        {/* Mobile-first stack (A, B, C, D); on desktop a two-column layout:
            readiness + trust in the rail, holding actions + cash plan main. */}
        <div className="flex flex-col gap-6 lg:grid lg:grid-cols-[minmax(280px,340px)_minmax(0,1fr)] lg:items-start lg:gap-6">
          {/* Rail (desktop) — Sections A + D */}
          <div className="contents lg:flex lg:flex-col lg:gap-6 lg:min-w-0">
            <div className="order-1 lg:order-none">
              <AdvisorReadinessPanel
                model={model}
                onRun={() => runMutation.mutate()}
                lastRunResult={runMutation.data ?? null}
              />
            </div>
            <div className="order-4 lg:order-none">
              <AdvisorTrustPanel model={model} plan={lastPlan} />
            </div>
          </div>

          {/* Main (desktop) — Sections B + C */}
          <div className="contents lg:flex lg:flex-col lg:gap-6 lg:min-w-0">
            <section
              aria-labelledby="advisor-holding-actions-heading"
              className="order-2 lg:order-none min-w-0"
            >
              <h2 id="advisor-holding-actions-heading" className="section-header mb-3">
                Holding actions
              </h2>
              {/* Deterministic Intel v3 surface — mounted unconditionally. */}
              <IntelV3Cockpit />
            </section>

            <div
              id="cash-plan"
              ref={cashPlanRef}
              tabIndex={-1}
              className="order-3 lg:order-none min-w-0 scroll-mt-20 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-md"
            >
              <AdvisorCashPlanSection
                runState={model.run.state}
                onResult={setLastPlan}
              />
            </div>
          </div>
        </div>
      </main>
    </>
  );
}
