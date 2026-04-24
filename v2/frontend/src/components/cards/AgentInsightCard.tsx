"use client";

import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import type { InsightCardData } from "@/lib/api";

const ACTION_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  BUY: { bg: "bg-green-500/10", text: "text-green-400", border: "border-green-500/30" },
  SELL: { bg: "bg-red-500/10", text: "text-red-400", border: "border-red-500/30" },
  TRIM: { bg: "bg-yellow-500/10", text: "text-yellow-400", border: "border-yellow-500/30" },
  HOLD: { bg: "bg-blue-500/10", text: "text-blue-400", border: "border-blue-500/30" },
  REVIEW: { bg: "bg-purple-500/10", text: "text-purple-400", border: "border-purple-500/30" },
};

function normalizeAction(action?: string | null): string {
  const raw = (action || "").toUpperCase();
  if (raw === "REDUCE") return "TRIM";
  if (["BUY", "HOLD", "TRIM", "SELL", "REVIEW"].includes(raw)) return raw;
  return "HOLD";
}

function roleForCard(card: InsightCardData): string {
  const ticker = (card.ticker || "").toUpperCase();
  if (["VOO", "VTI", "SPY", "QQQ", "SCHD", "VYM", "VXUS", "VEA", "VWO", "BND"].includes(ticker)) return "ETF";
  if (["BTC", "XRP"].includes(ticker)) return "Crypto";
  if (["RIVN", "KLAR", "BLSH", "STUB"].includes(ticker)) return "Speculative";
  if (["SCHD", "VYM"].includes(ticker)) return "Income";
  if (["AAPL", "MSFT", "GOOGL", "META", "NVDA"].includes(ticker)) return "Growth";
  return "Core";
}

function momentumLabel(card: InsightCardData): "Strong" | "Weak" | "Neutral" {
  const signal = (card.technical_signal || "").toUpperCase();
  if (["BUY", "BULLISH", "STRONG"].includes(signal)) return "Strong";
  if (["SELL", "BEARISH", "WEAK"].includes(signal)) return "Weak";
  return "Neutral";
}

function riskLabel(card: InsightCardData): "High" | "Medium" | "Low" {
  if ((card.data_quality_label || "").toUpperCase() === "LOW" || card.analyst_used_fallback) return "High";
  const risks = (card.analyst_risks || card.main_risks || []).join(" ").toLowerCase();
  if (risks.includes("volatility") || risks.includes("breakdown")) return "High";
  if (risks.length > 0) return "Medium";
  return "Low";
}

function thesis(card: InsightCardData): string {
  return (
    card.plain_language_explanation
    || card.thesis
    || card.reasoning_summary
    || card.summary
    || card.investment_thesis
    || card.detail
    || `${card.ticker} remains on watch pending stronger confirmation.`
  );
}

function whatShouldIDo(action: string): string {
  if (action === "BUY") return "Add only if this does not increase concentration too much. Prefer staged buying.";
  if (action === "HOLD") return "Do nothing now. Recheck after earnings / trend change / price move.";
  if (action === "TRIM") return "Reduce exposure gradually or redirect proceeds into higher conviction buys.";
  if (action === "SELL") return "Exit if thesis is broken or tax impact is acceptable.";
  return "Monitor this position and reassess when new data arrives.";
}

export function AgentInsightCard({ card, onClick }: { card: InsightCardData; onClick?: () => void }) {
  const action = normalizeAction(card.analyst_action || card.action);
  const styles = ACTION_STYLES[action] || ACTION_STYLES.HOLD;
  const momentum = momentumLabel(card);
  const risk = riskLabel(card);
  const role = roleForCard(card);

  return (
    <div onClick={onClick} className={cn("card-glass p-4 space-y-3 border", styles.border, styles.bg, onClick && "cursor-pointer hover:brightness-110") }>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono font-bold text-text-primary text-base">{card.ticker}</div>
          <div className="text-xs text-text-muted">{card.category} · {card.sector || "Technology"}</div>
        </div>
        <div className="flex flex-col items-end gap-1 text-[10px] uppercase">
          <span className={cn("px-2 py-0.5 rounded border font-bold", styles.bg, styles.text, styles.border)}>{action}</span>
          <span className="text-text-muted"><span className={cn("inline-block w-1.5 h-1.5 rounded-full mr-1", card.analysis_source === "live_llm" ? "bg-green-400" : "bg-text-muted")} />{card.analysis_source === "live_llm" ? "Live" : "Cached"}</span>
          {card.data_quality_label && <span className="font-semibold text-text-secondary">{card.data_quality_label}</span>}
          {card.current_price != null && <span className="font-mono text-sm text-text-secondary normal-case">{formatCurrency(card.current_price)}</span>}
        </div>
      </div>

      <p className="text-sm text-text-primary leading-relaxed">{thesis(card)}</p>

      <div className="flex flex-wrap gap-2">
        <Chip label={`Momentum: ${momentum}`} tone={momentum === "Strong" ? "good" : momentum === "Weak" ? "bad" : "neutral"} />
        <Chip label={`Risk: ${risk}`} tone={risk === "High" ? "bad" : risk === "Low" ? "good" : "neutral"} />
        <Chip label={`Role: ${role}`} tone="neutral" />
        {card.pnl_pct != null && <Chip label={`P&L ${formatPercent(card.pnl_pct)}`} tone={card.pnl_pct >= 0 ? "good" : "bad"} />}
      </div>

      <details className="rounded-md border border-border/80 bg-surface-elevated/40 px-3 py-2">
        <summary className="text-xs text-text-secondary cursor-pointer">Expand details</summary>
        <div className="mt-2 space-y-2 text-xs text-text-secondary">
          <DetailList title="Key drivers" items={(card.analyst_drivers || card.key_drivers || []).slice(0, 3)} />
          <DetailList title="Risks" items={(card.analyst_risks || card.main_risks || []).slice(0, 3)} />
          <div>
            <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">What I should do</p>
            <p>{whatShouldIDo(action)}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">Data used</p>
            <p>{(card.analysis_source || "deterministic").replaceAll("_", " ")}, confidence {(card.analyst_confidence ?? card.confidence ?? 0).toFixed(2)}, quality {card.data_quality_label || "N/A"}.</p>
          </div>
        </div>
      </details>
    </div>
  );
}

function Chip({ label, tone }: { label: string; tone: "good" | "bad" | "neutral" }) {
  const cls = tone === "good" ? "bg-green-500/10 text-green-400" : tone === "bad" ? "bg-red-500/10 text-red-400" : "bg-surface-elevated text-text-secondary";
  return <span className={cn("text-[10px] px-2 py-0.5 rounded-full", cls)}>{label}</span>;
}

function DetailList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">{title}</p>
      <ul className="space-y-0.5">
        {items.map((item, idx) => <li key={idx} className="flex items-start gap-1.5"><span className="text-accent mt-0.5">•</span><span>{item}</span></li>)}
      </ul>
    </div>
  );
}
