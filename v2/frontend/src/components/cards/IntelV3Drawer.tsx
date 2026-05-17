"use client";

/**
 * IntelV3Drawer — "Why this view?" Investment Committee detail room.
 *
 * Stage 4C redesign. Reads ONLY from IntelV3HeldCard and its detail_drawer_payload.
 * Live sections render from current data only. Future modules render as
 * ComingLaterPanel chrome — no fabricated content.
 *
 * Accessibility:
 * - role="dialog" + aria-modal + aria-labelledby
 * - Close button is focused on open
 * - Escape key closes
 * - Backdrop click closes
 * - Focus-visible ring on close button
 */

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import type { IntelV3HeldCard } from "@/lib/api";
import {
  ActionGlyph,
  ConfidenceRing,
  RiskGlyph,
  FreshnessDot,
  ComingLaterPanel,
  DataMissingPill,
  ACTION_TOKEN_STYLES,
} from "./IntelV3Primitives";
import { SourceMetadataStrip } from "./TrustPrimitives";
import {
  evidenceBandToBeginnerLabel,
  committeeStatusToPlainLabel,
  formatSnapshotIdShort,
  formatUpdatedAtSafe,
} from "@/lib/intel-v3-evidence";

interface IntelV3DrawerProps {
  card: IntelV3HeldCard | null;
  onClose: () => void;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <h4 className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
        {title}
      </h4>
      <div className="text-sm text-text-secondary leading-relaxed">{children}</div>
    </div>
  );
}

function Rule() {
  return <div className="border-t border-border" />;
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
      <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
    </svg>
  );
}


export function IntelV3Drawer({ card, onClose }: IntelV3DrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = "intel-v3-drawer-title";

  // Close on Escape
  useEffect(() => {
    if (!card) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [card, onClose]);

  // Focus the close button when drawer opens — keyboard-accessible entry point
  useEffect(() => {
    if (card) closeRef.current?.focus();
  }, [card]);

  if (!card) return null;

  const payload = card.detail_drawer_payload;
  const t = ACTION_TOKEN_STYLES[card.action] ?? ACTION_TOKEN_STYLES.HOLD;

  const whyView =
    payload.rationale?.trim() || card.why_text?.trim() || null;

  const thesis =
    card.why_text?.trim() || card.action_text?.trim() || null;

  const riskText = card.risk_text?.trim() || null;
  const fitText = card.fit_text?.trim() || null;
  const portfolioFitLabel =
    card.portfolio_fit && card.portfolio_fit !== "UNKNOWN"
      ? card.portfolio_fit.replace(/_/g, " ").toLowerCase()
      : null;

  const hasBlockers = payload.blockers && payload.blockers.length > 0;
  const hasFlags = card.flags && card.flags.length > 0;
  const whatWouldChange = card.what_would_change_view?.trim() || null;

  const convictionLabel =
    card.conviction.charAt(0) + card.conviction.slice(1).toLowerCase();
  const evidenceBandBeginnerLabel = evidenceBandToBeginnerLabel(card.evidence_band);
  const committeeStatusLabel = committeeStatusToPlainLabel(
    payload.committee?.status ?? ""
  );

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm motion-reduce:backdrop-blur-none"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer panel — mobile: bottom sheet; desktop: right-side drawer */}
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={cn(
          // Mobile: full-width bottom sheet anchored to viewport bottom
          "fixed inset-x-0 bottom-0 z-50",
          "max-h-[88dvh] rounded-t-2xl",
          "border-t border-border bg-background",
          "overflow-y-auto overscroll-contain",
          "sheet-slide-up",
          // Desktop: right-side drawer (overrides mobile layout)
          "lg:inset-x-auto lg:right-0 lg:top-0 lg:bottom-0",
          "lg:w-full lg:max-w-md lg:max-h-none",
          "lg:rounded-none lg:border-t-0 lg:border-l",
          "lg:[animation:none]",
        )}
      >
        {/* Mobile drag handle — visual cue, hidden on desktop */}
        <div className="lg:hidden flex justify-center pt-3 pb-1" aria-hidden="true">
          <div className="w-8 h-1 rounded-full bg-border-strong opacity-50" />
        </div>

        {/* ── Sticky header ───────────────────────────────────────────────── */}
        <div className="sticky top-0 z-10 bg-background/95 backdrop-blur-sm border-b border-border px-5 py-4">
          <div className="flex items-center justify-between gap-3">
            {/* Identity: action badge + ticker + name */}
            <div className="flex items-center gap-2.5 min-w-0">
              <span
                className={cn(
                  "flex items-center gap-1.5 px-2 py-0.5 rounded border text-[11px] font-bold uppercase tracking-wider shrink-0",
                  t.bg, t.text, t.border
                )}
              >
                <ActionGlyph action={card.action} />
                {card.action}
              </span>
              <span
                id={titleId}
                className="text-base font-bold text-text-primary font-mono tracking-wide"
              >
                {card.ticker}
              </span>
              <span className="text-sm text-text-muted truncate">{card.name}</span>
            </div>

            {/* Close button — focused on open */}
            <button
              ref={closeRef}
              onClick={onClose}
              className={cn(
                "shrink-0 text-text-muted hover:text-text-primary transition-colors",
                "rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                "motion-reduce:transition-none"
              )}
              aria-label="Close"
            >
              <CloseIcon />
            </button>
          </div>

          {/* Conviction + evidence + risk summary row */}
          <div className="flex items-center gap-2 mt-2.5 flex-wrap">
            <ConfidenceRing conviction={card.conviction} dotClass={t.dot} />
            <span className="text-[11px] text-text-muted">{convictionLabel} conviction</span>
            <span className="text-border select-none">·</span>
            <FreshnessDot band={card.evidence_band} />
            {card.risk_level && card.risk_level !== "LOW" && card.risk_level !== "UNKNOWN" && (
              <>
                <span className="text-border select-none">·</span>
                <RiskGlyph level={card.risk_level} />
              </>
            )}
          </div>
        </div>

        {/* ── Body ────────────────────────────────────────────────────────── */}
        <div className="px-5 py-5 space-y-5">

          {/* Why this view? */}
          <Section title="Why this view?">
            {whyView ? (
              <p>{whyView}</p>
            ) : (
              <DataMissingPill label="Analysis not yet available" />
            )}
          </Section>

          <Rule />

          {/* Plain-English thesis */}
          {thesis && (
            <>
              <Section title="The thesis">
                <p>{thesis}</p>
              </Section>
              <Rule />
            </>
          )}

          {/* Risk challenge */}
          <Section title="Risk to watch">
            {riskText ? (
              <p>{riskText}</p>
            ) : (
              <DataMissingPill label="No risk data" />
            )}
            {hasBlockers && (
              <ul className="mt-2 space-y-1">
                {payload.blockers!.map((b, i) => (
                  <li key={i} className="text-xs text-text-muted flex items-start gap-1.5">
                    <span className="shrink-0 mt-0.5 text-action-trim" aria-hidden="true">•</span>
                    <span>{b}</span>
                  </li>
                ))}
              </ul>
            )}
            {hasFlags && (
              <ul className="mt-2 space-y-1">
                {card.flags.map((flag, i) => (
                  <li key={i} className="text-xs text-text-muted flex items-start gap-1.5">
                    <span className="shrink-0 mt-0.5 text-action-trim" aria-hidden="true">!</span>
                    <span>{flag}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {/* What would change this view */}
          {whatWouldChange && (
            <>
              <Rule />
              <Section title="What would change this view">
                <p>{whatWouldChange}</p>
              </Section>
            </>
          )}

          <Rule />

          {/* Portfolio fit */}
          <Section title="How it fits your portfolio">
            {fitText || portfolioFitLabel ? (
              <p>{fitText || portfolioFitLabel}</p>
            ) : (
              <DataMissingPill label="Portfolio fit not assessed" />
            )}
          </Section>

          <Rule />

          {/* Evidence + source shell */}
          <Section title="Evidence check">
            {/* Source row — plainly attributes the snapshot and source-pack status */}
            <div className="rounded-lg border border-border bg-surface/40 px-3 py-2.5 mb-3">
              <p className="text-[10px] text-text-muted uppercase tracking-wide mb-1">Source</p>
              <p className="text-xs font-medium text-text-secondary">
                Built from Intel v3 snapshot
              </p>
              <p className="text-[10px] text-text-muted mt-0.5">{committeeStatusLabel}</p>
            </div>

            {/* Evidence summary */}
            {card.evidence_text ? (
              <p className="mb-3">{card.evidence_text}</p>
            ) : (
              <p className="text-xs text-text-muted italic mb-3">No evidence summary available.</p>
            )}

            {/* Evidence quality grid — beginner language */}
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="bg-surface-elevated rounded-lg p-2.5">
                <span className="text-[10px] text-text-muted block mb-0.5 uppercase tracking-wide">
                  Evidence
                </span>
                <span className="font-semibold text-text-primary text-[11px] leading-snug">
                  {evidenceBandBeginnerLabel}
                </span>
              </div>
              <div className="bg-surface-elevated rounded-lg p-2.5">
                <span className="text-[10px] text-text-muted block mb-0.5 uppercase tracking-wide">
                  Conviction
                </span>
                <span className="font-semibold text-text-primary">{convictionLabel}</span>
              </div>
            </div>
          </Section>

          {/* Valuation context — rendered only when present in current data */}
          {payload.valuation_context && (
            <>
              <Rule />
              <Section title="Valuation context">
                <p>{payload.valuation_context.visible_text}</p>
                <p className="text-[10px] text-text-muted mt-1.5 leading-snug">
                  {payload.valuation_context.limitation_text}
                </p>
              </Section>
            </>
          )}

          <Rule />

          {/* ── Coming-Later evidence modules ───────────────────────────── */}
          <div className="space-y-3">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
              Preparing for the next intelligence stage
            </p>
            <ComingLaterPanel title="Source credibility tier" />
            <ComingLaterPanel title="Contradiction strip" />
            <ComingLaterPanel title="Evidence completeness score" />
            <ComingLaterPanel title="Business story" />
            <ComingLaterPanel title="SEC filing evidence room" />
            <ComingLaterPanel title="Technical evidence room" />
            <ComingLaterPanel title="Fundamental evidence room" />
            <ComingLaterPanel title="Sentiment & news evidence room" />
            <ComingLaterPanel title="Company strategy evidence room" />
            <ComingLaterPanel title="Source snippets & citations" />
            <ComingLaterPanel
              title="Ask why / Challenge / Explain"
              caption="This intelligence module is being prepared. The next intelligence stage will surface it here."
            />
          </div>

          {/* ── Snapshot metadata ───────────────────────────────────────── */}
          <div className="pt-2 border-t border-border">
            <SourceMetadataStrip
              updatedAt={formatUpdatedAtSafe(card.updated_at)}
              snapshotIdShort={formatSnapshotIdShort(card.source_snapshot_id)}
              schemaVersion={payload.schema_version}
            />
          </div>
        </div>
      </aside>
    </>
  );
}
