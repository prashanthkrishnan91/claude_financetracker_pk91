"use client";

import { cn } from "@/lib/utils";
import type { PortfolioSynthesisPayload } from "@/lib/api";

/**
 * PortfolioSynthesisPanel — Phase 4 cross-ticker insights rendered as a
 * headline panel above the recommendations list. Reads directly from
 * ``AgentRunStatus.portfolio_synthesis`` so the panel always shows the
 * freshest run's themes + risks.
 *
 * Stays graceful when the synthesis is missing (pre-Phase-4 runs or
 * pipelines that short-circuited on empty portfolios): returns ``null``
 * so the recommendations page keeps its existing layout.
 */

const BIAS_STYLES: Record<string, { label: string; cls: string }> = {
  bullish:   { label: "Bullish",   cls: "bg-green-500/10 text-green-400 border-green-500/30" },
  neutral:   { label: "Neutral",   cls: "bg-blue-500/10 text-blue-400 border-blue-500/30" },
  defensive: { label: "Defensive", cls: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30" },
};

export function PortfolioSynthesisPanel({
  synthesis,
}: {
  synthesis: PortfolioSynthesisPayload | null | undefined;
}) {
  if (!synthesis || !synthesis.portfolio_bias) return null;

  const bias = BIAS_STYLES[synthesis.portfolio_bias] || BIAS_STYLES.neutral;
  const themes = synthesis.key_themes ?? [];
  const risks = synthesis.risk_concentrations ?? [];
  const overexposure = synthesis.overexposure_flags ?? [];
  const rebalance = synthesis.rebalancing_suggestions ?? [];

  return (
    <section className="card-glass rounded-xl border border-border px-4 py-4 space-y-3">
      <header className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">
            Portfolio Synthesis
          </span>
          <span
            className={cn(
              "text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase",
              bias.cls
            )}
          >
            {bias.label}
          </span>
          {synthesis.used_fallback && (
            <span
              title="Deterministic synthesis — fresh LLM context unavailable."
              className="text-[10px] px-2 py-0.5 rounded-full bg-surface-elevated text-text-muted border border-border uppercase"
            >
              Fallback
            </span>
          )}
        </div>
      </header>

      {synthesis.summary && (
        <p className="text-sm text-text-primary leading-relaxed">
          {synthesis.summary}
        </p>
      )}

      <div className="grid sm:grid-cols-2 gap-3">
        {themes.length > 0 && (
          <Block title="Key themes" items={themes} bulletCls="text-accent" />
        )}
        {risks.length > 0 && (
          <Block title="Risk concentrations" items={risks} bulletCls="text-red-400" />
        )}
        {overexposure.length > 0 && (
          <Block
            title="Overexposure flags"
            items={overexposure}
            bulletCls="text-yellow-400"
          />
        )}
        {rebalance.length > 0 && (
          <Block
            title="Rebalancing suggestions"
            items={rebalance}
            bulletCls="text-blue-400"
          />
        )}
      </div>
    </section>
  );
}

function Block({
  title,
  items,
  bulletCls,
}: {
  title: string;
  items: string[];
  bulletCls: string;
}) {
  return (
    <div className="space-y-1.5">
      <span className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">
        {title}
      </span>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li
            key={i}
            className="flex items-start gap-1.5 text-xs text-text-secondary leading-relaxed"
          >
            <span className={cn("mt-0.5 shrink-0", bulletCls)}>•</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
