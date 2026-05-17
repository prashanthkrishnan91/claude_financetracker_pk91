"use client";

import { cn } from "@/lib/utils";
import type { IntelV3Action, IntelV3Conviction, IntelV3EvidenceBand } from "@/lib/api";

// ── Action glyph map ──────────────────────────────────────────────────────────
const ACTION_GLYPHS: Record<IntelV3Action, string> = {
  BUY:  "↑",
  HOLD: "─",
  TRIM: "↓",
  SELL: "✕",
};

// ── Action token style map ─────────────────────────────────────────────────────
// Routes through design-system tokens only. No raw Tailwind color classes.
export const ACTION_TOKEN_STYLES: Record<
  IntelV3Action,
  { text: string; bg: string; border: string; glyph: string; dot: string }
> = {
  BUY:  { text: "text-action-buy",  bg: "bg-action-buy/10",  border: "border-action-buy/30",  glyph: ACTION_GLYPHS.BUY,  dot: "bg-action-buy"  },
  HOLD: { text: "text-action-hold", bg: "bg-action-hold/10", border: "border-action-hold/30", glyph: ACTION_GLYPHS.HOLD, dot: "bg-action-hold" },
  TRIM: { text: "text-action-trim", bg: "bg-action-trim/10", border: "border-action-trim/30", glyph: ACTION_GLYPHS.TRIM, dot: "bg-action-trim" },
  SELL: { text: "text-action-sell", bg: "bg-action-sell/10", border: "border-action-sell/30", glyph: ACTION_GLYPHS.SELL, dot: "bg-action-sell" },
};

// ── ActionGlyph ───────────────────────────────────────────────────────────────
export function ActionGlyph({
  action,
  className,
}: {
  action: IntelV3Action;
  className?: string;
}) {
  const t = ACTION_TOKEN_STYLES[action] ?? ACTION_TOKEN_STYLES.HOLD;
  return (
    <span
      className={cn("font-mono font-bold leading-none select-none", t.text, className)}
      aria-hidden="true"
    >
      {t.glyph}
    </span>
  );
}

// ── ConfidenceRing ────────────────────────────────────────────────────────────
// 3-step conviction primitive. Dots fill from left to right.
const CONVICTION_STEPS: Record<IntelV3Conviction, number> = {
  LOW: 1, MEDIUM: 2, HIGH: 3,
};

export function ConfidenceRing({
  conviction,
  dotClass,
  className,
}: {
  conviction: IntelV3Conviction;
  dotClass: string;
  className?: string;
}) {
  const filled = CONVICTION_STEPS[conviction] ?? 1;
  const label = conviction.charAt(0) + conviction.slice(1).toLowerCase();
  return (
    <span
      className={cn("flex items-center gap-0.5", className)}
      title={`${label} conviction`}
      aria-label={`${label} conviction`}
    >
      {[1, 2, 3].map((n) => (
        <span
          key={n}
          className={cn(
            "w-1.5 h-1.5 rounded-full",
            n <= filled ? dotClass : "bg-surface-elevated opacity-40"
          )}
        />
      ))}
    </span>
  );
}

// ── RiskGlyph ─────────────────────────────────────────────────────────────────
// Renders nothing for LOW or UNKNOWN — only surfaces elevated risk.
export function RiskGlyph({
  level,
  className,
}: {
  level: string;
  className?: string;
}) {
  if (!level || level === "LOW" || level === "UNKNOWN") return null;
  const style =
    level === "HIGH"
      ? "bg-action-sell/10 text-action-sell border-action-sell/20"
      : "bg-action-trim/10 text-action-trim border-action-trim/20";
  return (
    <span
      className={cn(
        "text-[10px] px-1.5 py-0.5 rounded border font-medium uppercase tracking-wide",
        style,
        className
      )}
    >
      {level} risk
    </span>
  );
}

// ── FreshnessDot ──────────────────────────────────────────────────────────────
// Evidence freshness / quality primitive. STRONG → buy color, PARTIAL → hold
// color, THIN → neutral. Missing / unknown → neutral muted.
const FRESHNESS_STYLES: Record<string, string> = {
  STRONG:  "bg-action-buy/10 text-action-buy border-action-buy/20",
  PARTIAL: "bg-action-hold/10 text-action-hold border-action-hold/20",
  THIN:    "bg-surface-elevated text-text-muted border-border",
};
const FRESHNESS_LABELS: Record<string, string> = {
  STRONG:  "Strong signal",
  PARTIAL: "Partial data",
  THIN:    "Thin data",
};

export function FreshnessDot({
  band,
  className,
}: {
  band: IntelV3EvidenceBand | string;
  className?: string;
}) {
  const style = FRESHNESS_STYLES[band] ?? FRESHNESS_STYLES.THIN;
  const label = FRESHNESS_LABELS[band] ?? band;
  return (
    <span
      className={cn(
        "text-[10px] px-1.5 py-0.5 rounded border font-medium tracking-wide",
        style,
        className
      )}
    >
      {label}
    </span>
  );
}

// ── DataMissingPill ───────────────────────────────────────────────────────────
// Calm, honest indicator when a field is absent or insufficient.
export function DataMissingPill({
  label = "Data missing",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-block text-[10px] px-1.5 py-0.5 rounded border border-border text-text-muted bg-surface-elevated font-medium tracking-wide uppercase",
        className
      )}
    >
      {label}
    </span>
  );
}

// ── ComingLaterPanel ──────────────────────────────────────────────────────────
// Coming-Later chrome for future Stage 5/6 intelligence modules.
// Renders exactly one calm caption — no fake content.
export function ComingLaterPanel({
  title,
  caption,
  className,
}: {
  title: string;
  caption?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-dashed border-border bg-surface/40 px-4 py-4",
        className
      )}
    >
      <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
        {title}
      </p>
      <p className="text-xs text-text-muted mt-1 leading-snug">
        {caption ??
          "This intelligence module is being prepared. The next intelligence stage will surface it here."}
      </p>
    </div>
  );
}
