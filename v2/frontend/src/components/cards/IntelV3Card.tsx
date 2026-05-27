"use client";

/**
 * IntelV3Card — Investment Committee Action Card.
 *
 * Stage 4C redesign. Renders ONLY from IntelV3HeldCard (v3 snapshot).
 * Actions: BUY / HOLD / TRIM / SELL only (locked contract).
 * Uses design-system action tokens — no raw Tailwind color classes.
 * No raw metric keys. No price targets. No allocation math.
 */

import { cn } from "@/lib/utils";
import type { IntelV3HeldCard } from "@/lib/api";
import {
  ActionGlyph,
  ConfidenceRing,
  RiskGlyph,
  FreshnessDot,
  DataMissingPill,
  ACTION_TOKEN_STYLES,
} from "./IntelV3Primitives";

interface IntelV3CardProps {
  card: IntelV3HeldCard;
  onSelect: (card: IntelV3HeldCard) => void;
}

export function IntelV3Card({ card, onSelect }: IntelV3CardProps) {
  const t = ACTION_TOKEN_STYLES[card.action] ?? ACTION_TOKEN_STYLES.HOLD;
  const intelCtx = card.detail_drawer_payload?.asset_intelligence_context;
  // Prefer composer why_this_action over generic action_text fallback.
  const whyText = card.why_text || intelCtx?.why_this_action || card.action_text;
  const isThinData = card.evidence_band === "THIN";
  // Show role/lens chip for non-stock assets where the role adds meaning.
  const showRoleLens = intelCtx?.role_lens &&
    intelCtx.lens_applied !== "stock_fundamental_lens";

  return (
    <button
      onClick={() => onSelect(card)}
      className={cn(
        "group w-full text-left rounded-xl border p-4 transition-colors",
        "bg-surface hover:bg-surface-elevated",
        "border-border hover:border-border-strong",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
        // Respect reduced-motion
        "motion-reduce:transition-none"
      )}
      aria-label={`${card.ticker} — ${card.action}. ${(whyText ?? "").slice(0, 80)}`}
    >
      {/* Row 1 — Action badge left, ticker right */}
      <div className="flex items-start justify-between gap-2 mb-2.5">
        {/* Action badge: glyph + label + conviction ring */}
        <div className="flex items-center gap-2 shrink-0">
          <span
            className={cn(
              "flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-bold uppercase tracking-wider",
              t.bg, t.text, t.border
            )}
          >
            <ActionGlyph action={card.action} className="text-[11px]" />
            {card.action}
          </span>
          <ConfidenceRing conviction={card.conviction} dotClass={t.dot} />
        </div>

        {/* Ticker + company name */}
        <div className="flex flex-col items-end min-w-0">
          <span className="text-sm font-bold text-text-primary font-mono tracking-wide leading-tight">
            {card.ticker}
          </span>
          <span
            className="text-[10px] text-text-muted truncate max-w-[110px] leading-tight"
            title={card.name}
          >
            {card.name}
          </span>
        </div>
      </div>

      {/* Row 2 — Plain-English why_text */}
      <p className="text-xs text-text-secondary leading-relaxed mb-2 line-clamp-2 min-h-[2.5rem]">
        {whyText ?? ""}
      </p>

      {/* Row 2b — Role / Lens chip (ETF, commodity, crypto only) */}
      {showRoleLens && (
        <p
          className="text-[10px] text-text-muted leading-snug mb-2 line-clamp-1"
          aria-label={`Asset role: ${intelCtx!.role_lens}`}
        >
          {intelCtx!.role_lens}
        </p>
      )}

      {/* Row 3 — Evidence / risk / portfolio fit chips */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {isThinData ? (
          <DataMissingPill label="Thin data" />
        ) : (
          <FreshnessDot band={card.evidence_band} />
        )}
        {card.portfolio_fit && card.portfolio_fit !== "UNKNOWN" && (
          <span className="text-[10px] px-1.5 py-0.5 rounded border border-border text-text-muted uppercase tracking-wide">
            {card.portfolio_fit.replace(/_/g, " ")}
          </span>
        )}
        <RiskGlyph level={card.risk_level} />
      </div>
    </button>
  );
}
