"use client";

/**
 * IntelV3Drawer — Detail drawer for a v3 held-position card.
 *
 * Reads ONLY from IntelV3HeldCard.detail_drawer_payload.
 * No legacy fields. No raw metric keys. No price targets.
 * Committee is deferred and shown as such.
 */

import { cn } from "@/lib/utils";
import type { IntelV3HeldCard } from "@/lib/api";

interface IntelV3DrawerProps {
  card: IntelV3HeldCard | null;
  onClose: () => void;
}

const ACTION_COLORS: Record<string, string> = {
  BUY:  "text-green-400",
  HOLD: "text-blue-400",
  TRIM: "text-amber-400",
  SELL: "text-red-400",
};

function DrawerSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <h4 className="text-[11px] font-semibold uppercase tracking-widest text-text-muted">
        {title}
      </h4>
      <div className="text-sm text-text-secondary leading-relaxed">{children}</div>
    </div>
  );
}

function Divider() {
  return <div className="border-t border-border" />;
}

export function IntelV3Drawer({ card, onClose }: IntelV3DrawerProps) {
  if (!card) return null;

  const payload = card.detail_drawer_payload;
  const actionColor = ACTION_COLORS[card.action] ?? "text-text-primary";

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer panel */}
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`${card.ticker} detail`}
        className={cn(
          "fixed right-0 top-0 bottom-0 z-50 w-full max-w-md",
          "bg-background border-l border-border",
          "overflow-y-auto"
        )}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 bg-background border-b border-border px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-base font-bold text-text-primary font-mono">{card.ticker}</span>
            <span className="text-sm text-text-muted">{card.name}</span>
            <span className={cn("text-sm font-bold uppercase", actionColor)}>
              {card.action}
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary transition-colors text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-5 space-y-5">

          {/* Why this view */}
          <DrawerSection title="Why this view">
            <p>{payload.rationale}</p>
          </DrawerSection>

          <Divider />

          {/* Why act now / why wait */}
          <DrawerSection title="Why act now">
            <p>{payload.why_now}</p>
          </DrawerSection>

          <DrawerSection title="What would change this view">
            <p>{card.what_would_change_view}</p>
          </DrawerSection>

          <Divider />

          {/* Risk */}
          <DrawerSection title="Risk">
            <p>{card.risk_text}</p>
          </DrawerSection>

          <Divider />

          {/* How it fits my portfolio */}
          <DrawerSection title="How it fits my portfolio">
            <p>{card.portfolio_fit}</p>
            {card.flags.length > 0 && (
              <ul className="mt-2 space-y-1">
                {card.flags.map((flag, i) => (
                  <li key={i} className="text-xs text-text-muted flex items-start gap-1.5">
                    <span className="mt-0.5 text-amber-400">•</span>
                    <span>{flag}</span>
                  </li>
                ))}
              </ul>
            )}
          </DrawerSection>

          <Divider />

          {/* Evidence check */}
          <DrawerSection title="Evidence check">
            <div className="space-y-2">
              <p>{card.evidence_text}</p>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="bg-surface-elevated rounded p-2">
                  <span className="text-text-muted block mb-0.5">Signal quality</span>
                  <span className="font-medium text-text-primary">{card.evidence_band}</span>
                </div>
                <div className="bg-surface-elevated rounded p-2">
                  <span className="text-text-muted block mb-0.5">Conviction</span>
                  <span className="font-medium text-text-primary">{card.conviction}</span>
                </div>
              </div>
            </div>
          </DrawerSection>

          <Divider />

          {/* Valuation context — only shown when real evidence supports it */}
          {payload.valuation_context && (
            <>
              <Divider />
              <DrawerSection title="Valuation context">
                <p>{payload.valuation_context.visible_text}</p>
                <p className="text-[10px] text-text-muted mt-2 leading-snug">
                  {payload.valuation_context.limitation_text}
                </p>
              </DrawerSection>
            </>
          )}

          {/* Committee — deferred */}
          {payload.committee.status === "deferred" && (
            <DrawerSection title="Committee view">
              <div className="bg-surface-elevated rounded-lg p-3 text-xs text-text-muted border border-border">
                <span className="block font-medium text-text-secondary mb-1">Analysis pending</span>
                {payload.committee.reason ?? "Committee view will be available after source validation is complete."}
              </div>
            </DrawerSection>
          )}

          {/* Snapshot metadata */}
          <div className="text-[10px] text-text-muted space-y-0.5 pt-2 border-t border-border">
            <p>Last updated: {new Date(card.updated_at).toLocaleString()}</p>
            <p>Snapshot: {card.source_snapshot_id.slice(0, 8)}…</p>
            <p>Run: {card.source_run_id.slice(0, 8)}…</p>
            <p>Schema: {payload.schema_version}</p>
          </div>
        </div>
      </aside>
    </>
  );
}
