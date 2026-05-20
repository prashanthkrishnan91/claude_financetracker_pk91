"use client";

/**
 * IntelV3Drawer — plain-English decision explanation.
 *
 * Stage 7B redesign. Sections explain WHY the action, WHAT evidence supports it,
 * WHAT is incomplete, WHY conviction is capped, risk, what would change, and fit.
 *
 * Accessibility:
 * - role="dialog" + aria-modal + aria-labelledby
 * - Close button is focused on open
 * - Escape key closes
 * - Backdrop click closes
 * - Focus-visible ring on close button
 */

import { useState, useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import type { IntelV3EvidenceExplanation, IntelV3HeldCard } from "@/lib/api";
import {
  ActionGlyph,
  ConfidenceRing,
  RiskGlyph,
  FreshnessDot,
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
import {
  buildEvidenceLaneRows,
  buildSafetyDisplay,
  convictionCapLabel,
  governancePriorityToExplanation,
  buildWhyActionExplanation,
  buildSupportingEvidenceSentences,
  buildIncompleteEvidenceSentences,
} from "@/lib/intel-v3-explanation";

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

function BulletList({ items }: { items: string[] }) {
  return (
    <ul className="space-y-1">
      {items.map((item, i) => (
        <li key={i} className="flex items-start gap-1.5 text-sm text-text-secondary leading-relaxed">
          <span className="shrink-0 mt-0.5 text-text-muted" aria-hidden="true">•</span>
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

// ── Expandable evidence lane detail ──────────────────────────────────────────

function ReadinessChip({ status, isUsable, isBlocked }: { status: string; isUsable: boolean; isBlocked: boolean }) {
  const style = isBlocked
    ? "bg-action-trim/10 text-action-trim border-action-trim/30"
    : isUsable
    ? "bg-action-buy/10 text-action-buy border-action-buy/30"
    : "bg-surface-elevated text-text-muted border-border";
  return (
    <span className={cn("text-[10px] px-1.5 py-0.5 rounded border font-medium tracking-wide", style)}>
      {status}
    </span>
  );
}

function EvidenceLaneDetail({ ex }: { ex: IntelV3EvidenceExplanation }) {
  const [open, setOpen] = useState(false);
  const lanes = buildEvidenceLaneRows(ex);
  const priorityText = governancePriorityToExplanation(ex.governance_priority);

  return (
    <div className="space-y-2">
      {priorityText && (
        <p className="text-[11px] text-text-muted leading-relaxed">{priorityText}</p>
      )}

      <button
        onClick={() => setOpen((v) => !v)}
        className="text-[10px] text-text-muted hover:text-text-primary transition-colors motion-reduce:transition-none"
        aria-expanded={open}
      >
        {open ? "▲ Hide evidence sources" : "▼ Show evidence sources"}
      </button>

      {open && (
        <div className="space-y-2 pt-1">
          {lanes.map((lane) => (
            <div key={lane.laneId} className="flex items-start gap-3">
              <div className="w-[130px] shrink-0 text-[10px] text-text-muted pt-0.5 leading-snug">
                {lane.label}
              </div>
              <div className="flex-1 min-w-0">
                <ReadinessChip
                  status={lane.statusDisplay.label}
                  isUsable={lane.statusDisplay.isUsable}
                  isBlocked={lane.statusDisplay.isBlocked}
                />
                <p className="text-[10px] text-text-muted mt-0.5 leading-snug">
                  {lane.statusDisplay.detail}
                </p>
              </div>
            </div>
          ))}

          <div className="pt-1 border-t border-border">
            <p className="text-[10px] text-text-muted leading-snug">
              <span className="font-medium">Macro backdrop</span> — Portfolio context that can shape confidence and caution, but is not a standalone Buy/Sell reason.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export function IntelV3Drawer({ card, onClose }: IntelV3DrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = "intel-v3-drawer-title";

  useEffect(() => {
    if (!card) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [card, onClose]);

  useEffect(() => {
    if (card) closeRef.current?.focus();
  }, [card]);

  if (!card) return null;

  const payload = card.detail_drawer_payload;
  const t = ACTION_TOKEN_STYLES[card.action] ?? ACTION_TOKEN_STYLES.HOLD;
  const ex = payload.evidence_explanation ?? null;

  // De-duplicate: each text is shown at most once across all sections.
  const seenTexts = new Set<string>();
  function onceOnly(text: string | null | undefined): string | null {
    if (!text?.trim()) return null;
    const key = text.trim().toLowerCase();
    if (seenTexts.has(key)) return null;
    seenTexts.add(key);
    return text.trim();
  }

  // Section 1: primary decision narrative
  const primaryNarrative =
    onceOnly(payload.rationale) ??
    onceOnly(card.why_text) ??
    onceOnly(card.action_text);

  // Fallback explanation if no narrative text available
  const fallbackNarrative = primaryNarrative
    ? null
    : buildWhyActionExplanation(card.action, ex);

  // Section 5: risk
  const riskText = onceOnly(card.risk_text);
  const hasBlockers = payload.blockers && payload.blockers.length > 0;
  const hasFlags = card.flags && card.flags.length > 0;

  // Section 6: what would change
  const whatWouldChange = onceOnly(card.what_would_change_view);

  // Section 7: portfolio fit
  const fitText = onceOnly(card.fit_text);
  const portfolioFitLabel =
    card.portfolio_fit && card.portfolio_fit !== "UNKNOWN"
      ? card.portfolio_fit.replace(/_/g, " ").toLowerCase()
      : null;

  const convictionLabel =
    card.conviction.charAt(0) + card.conviction.slice(1).toLowerCase();
  const evidenceBandBeginnerLabel = evidenceBandToBeginnerLabel(card.evidence_band);
  const committeeStatusLabel = committeeStatusToPlainLabel(
    payload.committee?.status ?? ""
  );

  // Evidence sections (Stage 7 active path)
  const supportingSentences = ex ? buildSupportingEvidenceSentences(ex) : [];
  const incompleteSentences = ex ? buildIncompleteEvidenceSentences(ex) : [];
  const capLabel = ex ? convictionCapLabel(ex.conviction_cap_applied, ex.conviction_cap_reason) : "";
  const safety = ex ? buildSafetyDisplay(ex) : null;

  const safetyChipStyle = !safety
    ? "bg-surface-elevated text-text-secondary border-border"
    : safety.tier === "blocked"
    ? "bg-action-trim/10 text-action-trim border-action-trim/30"
    : safety.tier === "stronger"
    ? "bg-action-buy/10 text-action-buy border-action-buy/30"
    : "bg-surface-elevated text-text-secondary border-border";

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
          "fixed inset-x-0 bottom-0 z-50",
          "max-h-[88dvh] rounded-t-2xl",
          "border-t border-border bg-background",
          "overflow-y-auto overscroll-contain",
          "sheet-slide-up",
          "lg:inset-x-auto lg:right-0 lg:top-0 lg:bottom-0",
          "lg:w-full lg:max-w-md lg:max-h-none",
          "lg:rounded-none lg:border-t-0 lg:border-l",
          "lg:[animation:none]",
        )}
      >
        {/* Mobile drag handle */}
        <div className="lg:hidden flex justify-center pt-3 pb-1" aria-hidden="true">
          <div className="w-8 h-1 rounded-full bg-border-strong opacity-50" />
        </div>

        {/* ── Sticky header ───────────────────────────────────────────────── */}
        <div className="sticky top-0 z-10 bg-background/95 backdrop-blur-sm border-b border-border px-5 py-4">
          <div className="flex items-center justify-between gap-3">
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

          {/* Section 1: Why this is a {ACTION} */}
          <Section title={`Why this is a ${card.action}`}>
            {primaryNarrative ? (
              <p>{primaryNarrative}</p>
            ) : fallbackNarrative ? (
              <p>{fallbackNarrative}</p>
            ) : (
              <DataMissingPill label="Analysis not yet available" />
            )}
          </Section>

          <Rule />

          {/* Section 2: Evidence supporting this */}
          {ex ? (
            <>
              <Section title="Evidence supporting this">
                {supportingSentences.length > 0 ? (
                  <>
                    {/* Evidence support tier chip */}
                    <div className="flex items-center gap-2 flex-wrap mb-2">
                      <span className={cn("text-[10px] px-1.5 py-0.5 rounded border font-semibold tracking-wide uppercase", safetyChipStyle)}>
                        {safety!.label}
                      </span>
                    </div>
                    <BulletList items={supportingSentences} />
                  </>
                ) : (
                  <p className="text-sm text-text-muted italic">
                    Evidence is limited — see what&#39;s incomplete below.
                  </p>
                )}
              </Section>

              {/* Section 3: What is still incomplete */}
              {incompleteSentences.length > 0 && (
                <>
                  <Rule />
                  <Section title="What is still incomplete">
                    <BulletList items={incompleteSentences} />
                  </Section>
                </>
              )}

              {/* Section 4: Why conviction is capped */}
              {ex.conviction_cap_applied && capLabel && (
                <>
                  <Rule />
                  <Section title="Why conviction is capped">
                    <div className="rounded-lg border border-action-trim/20 bg-action-trim/5 px-3 py-2">
                      <p className="text-[11px] text-action-trim leading-relaxed">{capLabel}</p>
                    </div>
                  </Section>
                </>
              )}
            </>
          ) : (
            /* Stage 7 not active for this card — show card-level evidence text honestly */
            <Section title="Evidence supporting this">
              {card.evidence_text ? (
                <p>{card.evidence_text}</p>
              ) : (
                <DataMissingPill label="Evidence data unavailable" />
              )}
            </Section>
          )}

          <Rule />

          {/* Section 5: Risk to watch */}
          <Section title="Risk to watch">
            {riskText ? (
              <p>{riskText}</p>
            ) : !hasBlockers && !hasFlags ? (
              <DataMissingPill label="No risk data" />
            ) : null}
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

          {/* Section 6: What would change this view */}
          {whatWouldChange && (
            <>
              <Rule />
              <Section title="What would change this view">
                <p>{whatWouldChange}</p>
              </Section>
            </>
          )}

          <Rule />

          {/* Section 7: How it fits your portfolio */}
          <Section title="How it fits your portfolio">
            {fitText || portfolioFitLabel ? (
              <p>{fitText || portfolioFitLabel}</p>
            ) : (
              <DataMissingPill label="Portfolio fit not assessed" />
            )}
          </Section>

          <Rule />

          {/* Section 8: Evidence check — source attribution + collapsible lane detail */}
          <Section title="Evidence check">
            <div className="rounded-lg border border-border bg-surface/40 px-3 py-2.5 mb-3">
              <p className="text-[10px] text-text-muted uppercase tracking-wide mb-1">Source</p>
              <p className="text-xs font-medium text-text-secondary">
                Built from Intel v3 snapshot
              </p>
              <p className="text-[10px] text-text-muted mt-0.5">{committeeStatusLabel}</p>
            </div>

            {/* Evidence quality grid */}
            <div className="grid grid-cols-2 gap-2 text-xs mb-3">
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

            {/* Expandable lane detail — only when Stage 7 explanation is available */}
            {ex && <EvidenceLaneDetail ex={ex} />}
          </Section>

          {/* Valuation context — rendered only when present */}
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

          {/* ── Snapshot metadata ───────────────────────────────────────────── */}
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
