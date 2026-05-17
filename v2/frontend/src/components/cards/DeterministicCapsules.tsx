"use client";

/**
 * Deterministic capsule components — Stage 4G.
 *
 * Buildable capsules that compose from existing data only:
 *   - WhyThisMatters (Alert Center, extended from Today 4B)
 *   - WhyTrimIsNotBadCompany
 *   - WhatMissingDataMeans
 *   - PatienceIsAction (wired in Deploy 4E; integration-only here)
 *   - HowDecisionChangesPortfolioShape (shows Coming-Later since shape data not yet available)
 *
 * Future-only capsules render Coming-Later, never fake content.
 * No fabrication. No external data.
 */

import { useState } from "react";
import { cn } from "@/lib/utils";
import { ComingLaterPanel, COMING_LATER_CANONICAL_CAPTION } from "./IntelV3Primitives";

export { COMING_LATER_CANONICAL_CAPTION };

// ── Shared capsule shell ──────────────────────────────────────────────────────

function CapsuleShell({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border/60 bg-surface/60 px-3.5 py-3",
        className
      )}
    >
      <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted mb-1.5">
        {title}
      </p>
      {children}
    </div>
  );
}

// ── WhyThisMatters ────────────────────────────────────────────────────────────
// Extended to Alert Center in Stage 4G. Headline + body from existing fields.

export function WhyThisMattersCapsule({
  headline,
  body,
  className,
}: {
  headline: string;
  body: string;
  className?: string;
}) {
  return (
    <CapsuleShell title="Why this matters" className={className}>
      <p className="text-xs font-medium text-text-secondary leading-snug">{headline}</p>
      <p className="text-xs text-text-muted leading-relaxed mt-1">{body}</p>
    </CapsuleShell>
  );
}

// ── WhyTrimIsNotBadCompany ────────────────────────────────────────────────────
// Shown on every Trim row visible in Intel / Alert Center / Today.

export const TRIM_NOT_BAD_COMPANY_BODY =
  "Trim does not automatically mean the company is bad. " +
  "It can mean the position is too large for the portfolio, risk has changed, evidence is less compelling, or the system is applying discipline after gains. " +
  "Treat it as a review signal and open Intel for the full thesis.";

export function WhyTrimIsNotBadCompanyCapsule({ className }: { className?: string }) {
  return (
    <CapsuleShell title="Why Trim does not mean bad company" className={className}>
      <p className="text-xs text-text-muted leading-relaxed">{TRIM_NOT_BAD_COMPANY_BODY}</p>
    </CapsuleShell>
  );
}

// ── WhatMissingDataMeans ──────────────────────────────────────────────────────
// Attached to every suppressed candidate or thin-evidence row.

export function WhatMissingDataMeansCapsule({
  detail,
  className,
}: {
  /** Optional override for why data is missing in this specific case. */
  detail?: string;
  className?: string;
}) {
  const defaultDetail =
    "Missing or thin evidence means the system cannot meet the confidence threshold required for this signal. " +
    "When evidence quality improves — through a fresh Intel run or updated source data — signals in this area may reappear as active candidates.";
  return (
    <CapsuleShell title="What missing data means" className={className}>
      <p className="text-xs text-text-muted leading-relaxed">{detail ?? defaultDetail}</p>
    </CapsuleShell>
  );
}

// ── PatienceIsAction ──────────────────────────────────────────────────────────
// Hold and Reserve rows in Deploy context. Integration-only in Stage 4G.

export const PATIENCE_IS_ACTION_BODY =
  "Holding cash in reserve is a deliberate portfolio decision, not inaction. " +
  "The Deploy plan sets a reserve amount because deploying all available capital at once carries timing risk. " +
  "Patience here means waiting for a more favorable entry, not missing an opportunity.";

export function PatienceIsActionCapsule({ className }: { className?: string }) {
  return (
    <CapsuleShell title="Patience is an action" className={className}>
      <p className="text-xs text-text-muted leading-relaxed">{PATIENCE_IS_ACTION_BODY}</p>
    </CapsuleShell>
  );
}

// ── HowDecisionChangesPortfolioShape ─────────────────────────────────────────
// Shows Coming-Later until current_weight / after_weight are available per contract §28.8.

export function HowDecisionChangesPortfolioShapeCapsule({ className }: { className?: string }) {
  return (
    <ComingLaterPanel
      title="How this changes your portfolio shape"
      caption="Portfolio weight impact will surface here once the next stage wires target-allocation comparison data."
      className={className}
    />
  );
}

// ── Future-only capsules (Coming-Later) ───────────────────────────────────────

export function BusinessStoryCapsule({ className }: { className?: string }) {
  return (
    <ComingLaterPanel
      title="Business story"
      caption={COMING_LATER_CANONICAL_CAPTION}
      className={className}
    />
  );
}

export function CompanyStrategyPrimerCapsule({ className }: { className?: string }) {
  return (
    <ComingLaterPanel
      title="Company strategy primer"
      caption={COMING_LATER_CANONICAL_CAPTION}
      className={className}
    />
  );
}

export function WhatWouldMakeThesisWrongCapsule({ className }: { className?: string }) {
  return (
    <ComingLaterPanel
      title="What would make this thesis wrong?"
      caption={COMING_LATER_CANONICAL_CAPTION}
      className={className}
    />
  );
}

export function GoodCompanyVsGoodStockCapsule({ className }: { className?: string }) {
  return (
    <ComingLaterPanel
      title="Good company vs. good stock"
      caption={COMING_LATER_CANONICAL_CAPTION}
      className={className}
    />
  );
}

export function WhatILearnedTodayCapsule({ className }: { className?: string }) {
  return (
    <ComingLaterPanel
      title="What I learned today"
      caption="This daily lesson is being prepared. The next intelligence stage will surface it here."
      className={className}
    />
  );
}

// ── Expandable capsule wrapper ────────────────────────────────────────────────
// Wraps any capsule content behind an expandable trigger.

export function ExpandableCapsule({
  triggerLabel,
  children,
  className,
}: {
  triggerLabel: string;
  children: React.ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={className}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-[11px] text-text-muted hover:text-accent transition-colors duration-160"
        aria-expanded={open}
      >
        <span
          className={cn(
            "inline-block transition-transform duration-160",
            open ? "rotate-90" : "rotate-0"
          )}
        >
          ▶
        </span>
        {triggerLabel}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}
