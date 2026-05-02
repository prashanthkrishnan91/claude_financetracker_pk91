"use client";

import { cn } from "@/lib/utils";
import type { PortfolioSynthesisPayload } from "@/lib/api";

const BIAS_STYLES: Record<string, { label: string; cls: string }> = {
  bullish: { label: "Bullish", cls: "bg-green-500/10 text-green-400 border-green-500/30" },
  neutral: { label: "Neutral", cls: "bg-blue-500/10 text-blue-400 border-blue-500/30" },
  defensive: { label: "Defensive", cls: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30" },
};

export function PortfolioSynthesisPanel({ synthesis }: { synthesis: PortfolioSynthesisPayload | null | undefined }) {
  if (!synthesis) return null;

  const biasKey = (synthesis.portfolio_bias || "neutral").toLowerCase();
  const bias = BIAS_STYLES[biasKey] || BIAS_STYLES.neutral;
  const quality = (synthesis.aggregate_quality || synthesis.quality || "UNKNOWN").toString().toUpperCase();
  const actionCounts = synthesis.action_counts || synthesis.counts || {};
  const strategyBuckets = synthesis.exposures?.strategy_buckets || [];
  const sectorBuckets = synthesis.exposures?.sector_buckets || [];
  const riskBuckets = synthesis.exposures?.risk_buckets || [];

  return (
    <section className="card-glass rounded-xl border border-border/70 p-4 md:p-5 space-y-4 md:space-y-5 bg-gradient-to-b from-surface-elevated/40 to-transparent">
      <div className="space-y-2.5 md:space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">Portfolio Command Center</span>
          <span className={cn("text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase", bias.cls)}>{bias.label}</span>
          <span className="text-[10px] px-2 py-0.5 rounded-full border border-border text-text-secondary">Run data {quality}</span>
          {synthesis.quality_breakdown?.enriched !== undefined && (
            <span className="text-[10px] px-2 py-0.5 rounded-full border border-border text-text-secondary">
              Enriched {synthesis.quality_breakdown.enriched}/{synthesis.quality_breakdown.total_cards}
            </span>
          )}
        </div>
        <p className="text-sm md:text-[15px] text-text-primary leading-relaxed font-medium">{synthesis.headline || synthesis.summary}</p>
        <p className="text-xs text-text-secondary leading-relaxed max-w-4xl">{synthesis.executive_summary || "Portfolio intelligence is generated from current recommendations, risk tags, and confidence signals."}</p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 pt-0.5">
          <MetricChip label="Buy" value={actionCounts.BUY || 0} tone="positive" />
          <MetricChip label="Hold" value={actionCounts.HOLD || 0} tone="neutral" />
          <MetricChip label="Trim" value={actionCounts.TRIM || 0} tone="caution" />
          <MetricChip label="Sell" value={actionCounts.SELL || 0} tone="negative" />
        </div>
      </div>

      <div className="grid md:grid-cols-3 gap-3">
        <BucketBlock title="Strategy exposure" buckets={strategyBuckets} />
        <BucketBlock title="Sector exposure" buckets={sectorBuckets} />
        <BucketBlock title="Risk exposure" buckets={riskBuckets} />
      </div>

      <div className="grid md:grid-cols-2 gap-3">
        <ListBlock
          title="Best Opportunities"
          items={(synthesis.top_opportunities || []).slice(0, 5).map((o) => `${o.ticker}: ${o.reason} (${o.suggested_use || "watch"})`)}
        />
        <ListBlock
          title="Risk & Trim Desk"
          items={(synthesis.trim_candidates || []).slice(0, 5).map((t) => `${t.ticker}: ${t.why_trim || "Trim risk"} → ${t.redirect_proceeds_to?.join(", ") || "top buys"}`)}
        />
      </div>

      <div className="grid md:grid-cols-2 gap-3">
        <ListBlock
          title="Watchlist / What Changed"
          items={[
            ...(synthesis.what_changed || []).slice(0, 4).map((c) => `${c.ticker || ""}: ${c.change || "Update"}`),
            ...(synthesis.watchlist || []).slice(0, 4).map((w) => `${w.ticker || ""}: ${w.focus || "Monitor"}`),
          ].slice(0, 6)}
        />
        <ListBlock title="Deploy Suggestions" items={(synthesis.deploy_suggestions || synthesis.rebalancing_suggestions || []).slice(0, 5)} />
      </div>
    </section>
  );
}

function BucketBlock({ title, buckets }: { title: string; buckets: Array<{ name: string; percentage: number; top_tickers?: string[]; why_it_matters?: string }> }) {
  return (
    <div className="rounded-lg border border-border/70 bg-surface-elevated/20 p-3 space-y-2.5">
      <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">{title}</p>
      {(buckets || []).slice(0, 4).map((bucket, idx) => (
        <div key={`${bucket.name}-${idx}`} className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <span className="text-text-primary">{bucket.name}</span>
            <span className="text-text-muted">{Math.round(Number(bucket.percentage || 0))}%</span>
          </div>
          {bucket.top_tickers?.length ? <p className="text-[11px] text-text-secondary leading-relaxed">{bucket.top_tickers.join(", ")}</p> : null}
          {bucket.why_it_matters ? <p className="text-[11px] text-text-muted">{bucket.why_it_matters}</p> : null}
        </div>
      ))}
      {(!buckets || buckets.length === 0) && <p className="text-xs text-text-muted">No exposure data yet.</p>}
    </div>
  );
}

function ListBlock({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-lg border border-border/70 bg-surface-elevated/15 p-3 space-y-2.5">
      <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">{title}</p>
      {items.length === 0 ? <p className="text-xs text-text-muted">No items available.</p> : (
        <ul className="space-y-1.5">
          {items.map((item, idx) => (
            <li key={idx} className="text-xs text-text-secondary flex items-start gap-1.5"><span className="text-accent mt-0.5">•</span><span>{item}</span></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MetricChip({ label, value, tone }: { label: string; value: number; tone: "positive" | "neutral" | "caution" | "negative" }) {
  const toneCls =
    tone === "positive"
      ? "border-green-500/30 text-green-300 bg-green-500/10"
      : tone === "negative"
      ? "border-red-500/30 text-red-300 bg-red-500/10"
      : tone === "caution"
      ? "border-yellow-500/30 text-yellow-300 bg-yellow-500/10"
      : "border-border text-text-secondary bg-surface-elevated/40";
  return (
    <div className={cn("rounded-md border px-2.5 py-1.5", toneCls)}>
      <p className="text-[10px] uppercase tracking-wide">{label}</p>
      <p className="text-base font-display leading-tight">{value}</p>
    </div>
  );
}
