"use client";

import { cn } from "@/lib/utils";
import type { IntelV3Action, IntelV3Conviction, IntelV3EvidenceBand } from "@/lib/api";
import { ACTION_TOKEN_STYLES } from "./IntelV3PrimitivesData";

export { ACTION_TOKEN_STYLES };

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
