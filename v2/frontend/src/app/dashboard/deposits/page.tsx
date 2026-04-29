"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { cn, formatCurrency } from "@/lib/utils";
import {
  usePortfolioSummary,
  useCashBalance,
  useSetCash,
  useDepositPlan,
  useDecisionOutcomes,
  useCreateDecisionMemoryLog,
  useDecisionMemoryLogs,
  useEvaluateDecisionMemoryLog,
  useUpdateDecisionMemoryLog,
  useDecisionPerformanceInsights,
} from "@/lib/hooks";
import type {
  AdaptiveBlock,
  AllocationExclusion,
  DepositPlanResult,
  DepositRecommendation,
  ActualDecisionItem,
  DecisionLogEntry,
  DecisionMemoryLog,
  RegimeBlock,
} from "@/lib/api";
import { InlineLoader } from "@/components/ui/Spinner";
import { Spinner } from "@/components/ui/Spinner";
import { buildInitialActualDecisions, buildRecommendationSnapshotWithContext } from "@/lib/decision-log";

const MAX_REASON_WORDS = 12;

function cleanActionText(text?: string | null): string {
  if (!text) return "";
  let out = text
    .replace(/\$[\d,.]+[kKmM]?/g, "")
    .replace(/\b\d+(\.\d+)?\s?%/g, "")
    .replace(/\b(position|allocation|weight|sizing|size)\b/gi, "")
    .replace(/\s{2,}/g, " ")
    .trim();
  out = out.split(/[.;]/)[0]?.trim() ?? "";
  if (!out) return "Hold";
  if (/accumulate|buy/i.test(out)) return "Accumulate on pullbacks";
  if (/trim|reduce|take profit/i.test(out)) return "Trim into strength";
  if (/hold|wait/i.test(out)) return "Hold";
  return out;
}

function toCompactLine(text?: string | null, maxWords = MAX_REASON_WORDS): string {
  if (!text) return "";
  const oneIdea = text
    .replace(/[–—]/g, " ")
    .split(/[.;]/)[0]
    ?.replace(/\s+/g, " ")
    .trim();
  if (!oneIdea) return "";
  const words = oneIdea.split(" ");
  if (words.length <= maxWords) return oneIdea;
  return `${words.slice(0, maxWords).join(" ")}…`;
}

function buildCutBullet(rec: DepositRecommendation, rank: number, total: number): string {
  const scoreText = rec.score != null ? `score ${rec.score.toFixed(2)}` : "strong composite score";
  if (rank === 0) return toCompactLine(`${rec.symbol} led peers with ${scoreText} and top conviction.`, 12);
  if (rank === total - 1) return toCompactLine(`${rec.symbol} edged lower-ranked names on confidence and risk-adjusted fit.`, 12);
  return toCompactLine(`${rec.symbol} ranked above alternatives on conviction, confidence, and diversification fit.`, 12);
}

function normalizeSignal(value: number | null | undefined): number {
  if (value == null || Number.isNaN(value)) return 0;
  return value > 1 ? value / 100 : value;
}

type EnrichedAllocation = DepositRecommendation & {
  why_selected: string;
  execution_plan: string;
};

function getScoreValue(rec: DepositRecommendation): number {
  return rec.score ?? rec.conviction_score ?? normalizeSignal(rec.confidence);
}

function deriveWhySelected(rec: DepositRecommendation, sorted: DepositRecommendation[]): string {
  const ticker = (rec.symbol || "").toUpperCase();
  const scoreRank = sorted.findIndex((candidate) => candidate.symbol === rec.symbol);
  const topScore = getScoreValue(sorted[0] ?? rec);
  const ownScore = getScoreValue(rec);
  const scoreGap = topScore - ownScore;
  const momentum = normalizeSignal(rec.features?.momentum);
  const volatility = normalizeSignal(rec.features?.volatility);
  const category = (rec.category || "").toLowerCase();
  const conviction = rec.conviction_level || (rec.conviction_score != null && rec.conviction_score > 0.75 ? "high" : "");
  const rationaleText = `${rec.why || ""} ${rec.rationale || ""}`.toLowerCase();

  const tickerSpecific: Record<string, string> = {
    TSM: "AI semiconductor supply-chain exposure with less direct mega-cap concentration than NVDA-heavy alternatives.",
    NVDA: "Strongest AI momentum in the set, but sized with awareness of chase and concentration risk.",
    MSFT: "Cloud + AI compounder with high enterprise quality that balances growth with resilience.",
    META: "Cheaper mega-cap AI/ad platform exposure with strong cash generation versus higher-multiple peers.",
    GOOGL: "AI-search and cloud upside with better valuation support than many comparable mega-cap names.",
    "BRK-B": "Defensive, cash-rich compounder that diversifies away from pure technology cyclicality.",
  };

  if (tickerSpecific[ticker]) return tickerSpecific[ticker];

  if (category === "etf") {
    return "Broad diversification sleeve that reduces single-name risk and smooths deployment concentration.";
  }
  if (scoreRank === 0) {
    return "Highest blended score and conviction among current BUY candidates after risk adjustments.";
  }
  if (momentum > 0.65 && scoreGap <= 0.1) {
    return "Stronger trend signal than similarly scored alternatives while retaining acceptable risk balance.";
  }
  if (volatility > 0 && volatility < 0.4) {
    return "Lower volatility profile than peers with comparable conviction and score quality.";
  }
  if (conviction.toLowerCase() === "high") {
    return "High-conviction setup with score support that edged other candidates for this deploy window.";
  }
  if (category.includes("tech") || rationaleText.includes("technology")) {
    return "Technology exposure selected for favorable score/conviction mix without over-concentrating any single name.";
  }
  return `${rec.category || "Core"} exposure made the cut on relative score, confidence, and portfolio fit.`;
}

function deriveExecutionPlan(rec: DepositRecommendation): string {
  const ticker = (rec.symbol || "").toUpperCase();
  const momentum = normalizeSignal(rec.features?.momentum);
  const volatility = normalizeSignal(rec.features?.volatility);
  const score = getScoreValue(rec);
  const currentWeight = rec.current_weight ?? rec.portfolio_weight ?? 0;
  const afterWeight = rec.after_weight ?? rec.target_weight ?? 0;
  const weightJump = Math.max(0, afterWeight - currentWeight);
  const conviction = (rec.conviction_level || "").toLowerCase();
  const category = (rec.category || "").toLowerCase();
  const isSpeculative = category === "speculative" || category === "crypto" || category === "ipo";
  const isDefensive = ["BRK-B", "BRK.B", "XLP", "XLV", "VTV"].includes(ticker)
    || category.includes("defensive")
    || category.includes("value");
  const isEtf = category === "etf" || ticker === "VOO" || ticker === "SPY" || ticker === "QQQ";

  if (currentWeight >= 15) return "Avoid chasing; only add on major pullback";
  if (currentWeight >= 10) return "Hold/add only on pullback";
  if (isEtf) return "Buy now or dollar-cost average";
  if (isDefensive) return "Buy now; use as stabilizer";
  if (isSpeculative || volatility >= 0.7) return "Stage entry in 2–3 buys";
  if (momentum >= 0.88 && weightJump >= 1.5) return "Wait for pullback; avoid chasing";
  if ((conviction === "high" || score >= 0.72) && momentum >= 0.45 && momentum <= 0.85) {
    return "Buy first tranche now";
  }
  if (momentum >= 0.65) return "Add on small pullbacks";
  return "Start with partial buy, then reassess";
}

function compactSentence(text?: string | null): string {
  if (!text) return "";
  const cleaned = text.replace(/\s+/g, " ").trim();
  if (!cleaned) return "";
  const sentenceMatch = cleaned.match(/^.*?[.!?](?:\s|$)/);
  return (sentenceMatch ? sentenceMatch[0] : cleaned).trim();
}

function deriveRoleLabel(rec: EnrichedAllocation, rank: number, total: number): "Primary" | "Supporting" | "Watch" {
  const momentum = normalizeSignal(rec.features?.momentum);
  const volatility = normalizeSignal(rec.features?.volatility);
  const currentWeight = rec.current_weight ?? rec.portfolio_weight ?? 0;
  const highRiskProfile = currentWeight >= 10 || volatility >= 0.7 || momentum >= 0.88;
  if (highRiskProfile) return "Watch";
  if (rank < Math.min(2, total)) return "Primary";
  return "Supporting";
}

function deriveAllocationWhy(
  rec: EnrichedAllocation,
  role: "Primary" | "Supporting" | "Watch"
): string {
  const currentWeight = rec.current_weight ?? rec.portfolio_weight ?? 0;
  const afterWeight = rec.after_weight ?? 0;
  const delta = afterWeight - currentWeight;
  const category = (rec.category || "").toLowerCase();
  const isEtf = category === "etf" || ["VOO", "SPY", "QQQ", "VTI", "SCHD"].includes((rec.symbol || "").toUpperCase());

  if (role === "Watch") {
    if (currentWeight > 5) return "Large already, capped add";
    if (currentWeight > 0) return "Existing risk, keep add small";
    return "Starter size only, risk kept tight";
  }
  if (role === "Primary") {
    if (isEtf) return "Core ballast, steady move toward target";
    if (currentWeight < 1) return "New core position, starting allocation";
    if (delta > 2) return "Core position, moving toward target";
    return "Core position, moderate add toward target";
  }
  if (isEtf) return "Quality exposure, moderate add";
  if (delta > 1.5) return "Growth exposure, sized below core";
  if (currentWeight > 5) return "Near target, top up lightly";
  return "Diversifier add, sized below core";
}

function computeAdjustedAmounts(
  sorted: EnrichedAllocation[],
  deployNowAmount: number
): Map<string, number> {
  const watchCap = Math.max(deployNowAmount * 0.075, 0);
  const roles = sorted.map((rec, idx) => deriveRoleLabel(rec, idx, sorted.length));
  const amounts = sorted.map(rec => Math.max(0, rec.immediate_amount ?? rec.amount ?? 0));
  let pool = 0;

  for (let i = 0; i < sorted.length; i++) {
    if (roles[i] === "Watch" && amounts[i] > watchCap) {
      pool += amounts[i] - watchCap;
      amounts[i] = watchCap;
    }
  }

  if (pool > 0) {
    const psIndices: number[] = [];
    roles.forEach((r, i) => { if (r !== "Watch") psIndices.push(i); });
    const psTotal = psIndices.reduce((sum, i) => sum + amounts[i], 0);
    if (psTotal > 0) {
      for (const i of psIndices) {
        amounts[i] += (amounts[i] / psTotal) * pool;
      }
    }
  }

  const result = new Map<string, number>();
  sorted.forEach((rec, i) => result.set(rec.symbol ?? "", amounts[i]));
  return result;
}

export default function DepositsPage() {
  const [amount, setAmount] = useState(900);
  const { data: summary } = usePortfolioSummary();
  const portfolioBalance = summary?.total_equity ?? 0;
  const { data: deployPlan, isLoading: isPlanLoading } = useDepositPlan(amount, portfolioBalance);
  const { data: outcomes } = useDecisionOutcomes();

  return (
    <>
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-display text-text-primary">Deploy</h1>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6 space-y-5">
        <section id="step-1" className="card-glass p-4 space-y-3 border border-border/80">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-accent">
                Step 1 — How much are you investing?
              </p>
              <p className="text-xs text-text-muted mt-1">
                Set deployment capital, then review the plan below.
              </p>
            </div>
            {summary && (
              <p className="text-xs text-text-secondary">
                Available cash:{" "}
                <span className="font-mono text-text-primary font-semibold">
                  {formatCurrency(summary.cash_balance)}
                </span>
              </p>
            )}
          </div>
          <div className="flex gap-3 items-center">
            <div className="relative flex-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted">$</span>
              <input
                type="number"
                value={amount}
                onChange={(e) => setAmount(Math.max(0, Number(e.target.value)))}
                className="w-full pl-7 pr-3 py-2.5 bg-surface border border-border rounded-lg text-text-primary font-mono focus:outline-none focus:ring-1 focus:ring-accent"
                min={0}
                step={50}
              />
            </div>
          </div>
          <div className="flex gap-2">
            {[500, 900, 1500, 2000].map((preset) => (
              <button
                key={preset}
                onClick={() => setAmount(preset)}
                className={cn(
                  "px-3 py-1 text-xs rounded-md transition-colors",
                  amount === preset
                    ? "bg-accent text-background font-semibold"
                    : "text-text-muted bg-surface-elevated hover:text-text-primary"
                )}
              >
                ${preset}
              </button>
            ))}
          </div>
        </section>

        {isPlanLoading ? (
          <InlineLoader text="Building deployment plan..." />
        ) : deployPlan ? (
          <DeploymentPlan deployPlan={deployPlan} amount={amount} />
        ) : null}

        {outcomes && outcomes.some((o) => o.return_pct != null) && (
          <details className="card-glass p-4 border border-border/70">
            <summary className="text-xs font-semibold uppercase tracking-wide text-text-muted cursor-pointer">
              Decision P&amp;L history
            </summary>
            <div className="mt-3">
              <OutcomePLSection outcomes={outcomes} />
            </div>
          </details>
        )}

      </main>
    </>
  );
}

function DeploymentPlan({ deployPlan, amount }: { deployPlan: DepositPlanResult; amount: number }) {
  const { plan, recommendations, summary, trims, notes, warning, explanation, exclusions, regime, adaptive } = deployPlan;
  const allocs = recommendations ?? [];
  const rankedAllocs = [...allocs].sort((a, b) => getScoreValue(b) - getScoreValue(a));
  const enrichedAllocs: EnrichedAllocation[] = rankedAllocs.map((rec) => ({
    ...rec,
    why_selected: deriveWhySelected(rec, rankedAllocs),
    execution_plan: deriveExecutionPlan(rec),
  }));
  const uniqueExecutionPlans = new Set(enrichedAllocs.map((rec) => rec.execution_plan));
  if (enrichedAllocs.length > 1 && uniqueExecutionPlans.size === 1) {
    enrichedAllocs.sort((a, b) => normalizeSignal(b.features?.volatility) - normalizeSignal(a.features?.volatility));
    enrichedAllocs[0] = { ...enrichedAllocs[0], execution_plan: "Stage entry in 2–3 buys" };
    enrichedAllocs[enrichedAllocs.length - 1] = {
      ...enrichedAllocs[enrichedAllocs.length - 1],
      execution_plan: "Buy first tranche now",
    };
  }
  const whySeen = new Set<string>();
  for (let i = 0; i < enrichedAllocs.length; i += 1) {
    const reason = enrichedAllocs[i].why_selected;
    if (!reason) continue;
    if (whySeen.has(reason)) {
      enrichedAllocs[i] = {
        ...enrichedAllocs[i],
        why_selected: `${reason} Relative score edge vs peers: ${getScoreValue(enrichedAllocs[i]).toFixed(2)}.`,
      };
    }
    whySeen.add(enrichedAllocs[i].why_selected);
  }
  const deployNowAmount = adaptive?.recommended_deploy_amount ?? plan.recommended_deploy_amount ?? plan.total_amount;
  const sortedByAmount = [...enrichedAllocs].sort((a, b) => (b.immediate_amount ?? b.amount ?? 0) - (a.immediate_amount ?? a.amount ?? 0));
  const primaryTickers = sortedByAmount
    .filter((rec, idx) => deriveRoleLabel(rec, idx, sortedByAmount.length) === "Primary")
    .slice(0, 2)
    .map(rec => rec.symbol ?? "")
    .filter(Boolean);

  return (
    <div className="space-y-4">
      <section className="card-glass p-4 space-y-4 border border-border/80">
        <p className="text-xs font-semibold uppercase tracking-wide text-accent">
          Step 2 — Where should this go?
        </p>
        <RecommendedDeploymentCard
          plan={plan}
          summary={summary}
          allocationCount={allocs.length}
          regime={regime ?? null}
          adaptive={adaptive ?? null}
          explanation={
            adaptive?.adaptive_reasons?.length
              ? adaptive.adaptive_reasons.join(" ")
              : (explanation ?? plan.intel_summary ?? notes.join(" "))
          }
          primaryTickers={primaryTickers}
        />
        {allocs.length > 0 && (
          <AllocationBreakdownTable
            allocations={enrichedAllocs}
            deployNowAmount={deployNowAmount}
          />
        )}
        {allocs.length === 0 ? (
          <div className="px-4 py-4 text-center text-sm text-text-muted border border-border/70 rounded-lg">
            No deployment right now — keep cash reserved until conditions improve.
          </div>
        ) : (
          <>
            <WhyMadeCutSection allocations={enrichedAllocs} />
            <DeploymentRisksSection allocations={enrichedAllocs} adaptive={adaptive ?? null} />
          </>
        )}
      </section>

      <section id="step-3">
        <DecisionLogMemoryPanel
          deployPlan={deployPlan}
          recommendations={enrichedAllocs}
          amount={amount}
          adaptive={adaptive ?? null}
        />
      </section>

      {warning && (
        <div className="card-glass border border-yellow-500/30 bg-yellow-500/5 p-3 flex items-start gap-2">
          <span className="text-yellow-400 mt-0.5">⚠</span>
          <p className="text-sm text-yellow-300">{warning}</p>
        </div>
      )}

      {/* Link to Intel tab */}
      <Link
        href="/dashboard/recommendations"
        className="flex items-center gap-1.5 text-xs text-accent hover:text-accent-hover transition-colors font-semibold"
      >
        View full AI analysis
        <ArrowRightIcon className="w-3.5 h-3.5" />
      </Link>

      {/* Skipped / excluded */}
      {exclusions && exclusions.length > 0 && (
        <SkippedSection exclusions={exclusions} />
      )}

      {/* Trims */}
      {trims.length > 0 && (
        <div className="card-glass p-4 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Trim watchlist
          </p>
          {trims.map((trim) => (
            <div key={trim.ticker} className="border border-border rounded-lg px-3 py-2">
              <p className="text-sm font-mono text-text-primary">
                {trim.action} {trim.ticker}
              </p>
              <p className="text-xs text-text-secondary">{trim.market_note}</p>
              {trim.tax_note && (
                <p className="text-xs text-yellow-300 mt-1">{trim.tax_note}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Advanced details (collapsed by default) */}
      {allocs.length > 0 && <AdvancedDetails allocations={allocs} adaptive={adaptive ?? null} />}

      <details className="card-glass p-4 border border-border/70">
        <summary className="text-xs font-semibold uppercase tracking-wide text-text-muted cursor-pointer">
          Cash source & override
        </summary>
        <div className="mt-3">
          <CashOverrideWidget />
        </div>
      </details>
    </div>
  );
}

function RecommendedDeploymentCard({
  plan,
  summary,
  allocationCount,
  regime,
  adaptive,
  explanation,
  primaryTickers = [],
}: {
  plan: DepositPlanResult["plan"];
  summary: DepositPlanResult["summary"];
  allocationCount: number;
  regime: RegimeBlock | null;
  adaptive: AdaptiveBlock | null;
  explanation?: string;
  primaryTickers?: string[];
}) {
  const hasAdaptive = !!adaptive;
  const immediate = adaptive?.recommended_deploy_amount ?? plan.recommended_deploy_amount ?? plan.total_amount;
  const reserve = adaptive?.cash_reserve_amount ?? plan.cash_reserve ?? 0;
  const regimeBadge = regime ? regimeBadgeMeta(regime.regime_label) : null;
  const modeBadge = adaptive ? modeBadgeMeta(adaptive.deployment_mode) : null;
  const subtitleParts: string[] = [`Across ${allocationCount} ticker${allocationCount === 1 ? "" : "s"}`];
  if (reserve > 0) subtitleParts.push(`Hold ${formatCurrency(reserve)} for pullbacks`);
  if (primaryTickers.length > 0) subtitleParts.push(`Prioritize ${primaryTickers.join(" & ")}`);
  const subtitle = subtitleParts.join(" • ");

  return (
    <div className="card-glass p-4 space-y-3 border border-accent/20">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <p className="text-[10px] uppercase tracking-wide font-semibold text-accent">
            Recommended Deployment
          </p>
          <p className="text-2xl font-display text-text-primary mt-1">
            Deploy {formatCurrency(immediate)} now
          </p>
          <p className="text-xs text-text-secondary mt-0.5">{subtitle}</p>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap justify-end">
          {regimeBadge && (
            <span
              className={cn(
                "text-[10px] px-2 py-0.5 rounded-full font-semibold uppercase tracking-wide border",
                regimeBadge.cls
              )}
              title={regime?.regime_reasons?.[0] ?? ""}
            >
              {regimeBadge.label}
            </span>
          )}
          {modeBadge && (
            <span
              className={cn(
                "text-[10px] px-2 py-0.5 rounded-full font-semibold uppercase tracking-wide border",
                modeBadge.cls
              )}
            >
              {modeBadge.label}
            </span>
          )}
          {!hasAdaptive && (
            <span
              className={cn(
                "text-xs px-2.5 py-1 rounded-full font-semibold uppercase tracking-wide",
                summary.fully_allocated
                  ? "bg-green-500/10 text-green-400 border border-green-500/20"
                  : "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20"
              )}
            >
              {summary.fully_allocated ? "Fully Allocated" : "Partial"}
            </span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="bg-surface-elevated rounded-md p-2">
          <p className="text-text-muted">Tickers</p>
          <p className="font-mono text-text-primary">{allocationCount}</p>
        </div>
        <div className="bg-surface-elevated rounded-md p-2">
          <p className="text-text-muted">{hasAdaptive ? "Deploy now" : "Deployed"}</p>
          <p className="font-mono text-text-primary">
            {formatCurrency(hasAdaptive ? immediate : summary.total_deployed)}
          </p>
        </div>
        <div className="bg-surface-elevated rounded-md p-2">
          <p className="text-text-muted">{hasAdaptive ? "Reserve" : "Considered"}</p>
          <p className="font-mono text-text-primary">
            {hasAdaptive
              ? formatCurrency(reserve)
              : summary.candidates_considered ?? summary.ranked_candidates}
          </p>
        </div>
      </div>
    </div>
  );
}

function InvestingStyleAdjustment({ adaptive }: { adaptive: AdaptiveBlock | null }) {
  if (!adaptive) return null;

  const profile = adaptive.behavior_profile ?? {};
  const confidence = profile.personalization_confidence ?? "Low";
  const ratioPct = Math.round((profile.stable_deploy_ratio ?? profile.avg_deploy_ratio ?? 1) * 100);
  const strength = profile.adjustment_strength ?? 0;
  const hasEnoughHistory = (profile.sample_size ?? 0) >= 3;
  const deployLine = !hasEnoughHistory
    ? "Deployment unchanged: Not enough history yet to personalize deployment."
    : strength < 1
      ? `Deployment adjusted gently: recent execution baseline is ${ratioPct}% with medium confidence.`
      : `Deployment adjusted: recent execution baseline is ${ratioPct}% with high confidence.`;
  const bulletLines = [
    deployLine,
    "Ticker list unchanged: model conviction picks are preserved.",
    "ETF preference detected: show ETF alternatives as optional substitutes, not automatic replacements.",
  ];

  return (
    <div className="rounded-md border border-border/80 bg-surface-elevated/30 p-2.5">
      <p className="text-[10px] uppercase tracking-wide font-semibold text-text-muted mb-1">
        Adjusted for your investing style
      </p>
      <p className="text-[11px] text-text-secondary leading-snug mb-1">
        Personalization confidence: <span className="font-semibold">{confidence}</span>
      </p>
      <ul className="space-y-1">
        {bulletLines.map((line) => (
          <li key={line} className="text-[11px] text-text-secondary leading-snug">
            • {line}
          </li>
        ))}
      </ul>
      {profile.prefers_etf ? (
        <p className="mt-1 text-[11px] text-text-muted leading-snug">
          Optional ETF substitutes to consider: VOO, VTI, SCHD, VYM
        </p>
      ) : null}
    </div>
  );
}

function regimeBadgeMeta(label: RegimeBlock["regime_label"]): { label: string; cls: string } {
  if (label === "bull") {
    return { label: "Bull", cls: "bg-green-500/10 text-green-400 border-green-500/30" };
  }
  if (label === "risk_off") {
    return { label: "Risk-off", cls: "bg-red-500/10 text-red-400 border-red-500/30" };
  }
  return { label: "Neutral", cls: "bg-blue-500/10 text-blue-300 border-blue-500/30" };
}

function modeBadgeMeta(mode: AdaptiveBlock["deployment_mode"]): { label: string; cls: string } {
  switch (mode) {
    case "full":
      return { label: "Full", cls: "bg-green-500/10 text-green-400 border-green-500/30" };
    case "defensive":
      return { label: "Defensive", cls: "bg-yellow-500/10 text-yellow-300 border-yellow-500/30" };
    case "wait":
      return { label: "Wait", cls: "bg-red-500/10 text-red-400 border-red-500/30" };
    case "partial":
    default:
      return { label: "Partial", cls: "bg-accent/10 text-accent border-accent/30" };
  }
}

function AllocationBreakdownTable({
  allocations,
  deployNowAmount,
}: {
  allocations: EnrichedAllocation[];
  deployNowAmount: number;
}) {
  const ranked = [...allocations].sort(
    (a, b) => (b.immediate_amount ?? b.amount ?? 0) - (a.immediate_amount ?? a.amount ?? 0)
  );
  const roleMap = new Map(ranked.map((rec, idx) => [rec.symbol ?? "", deriveRoleLabel(rec, idx, ranked.length)]));
  const adjustedAmounts = computeAdjustedAmounts(ranked, deployNowAmount);
  const displayRanked = [...ranked].sort(
    (a, b) => (adjustedAmounts.get(b.symbol ?? "") ?? 0) - (adjustedAmounts.get(a.symbol ?? "") ?? 0)
  );
  const allocatedNowTotal = displayRanked.reduce((sum, rec) => sum + (adjustedAmounts.get(rec.symbol ?? "") ?? 0), 0);
  const denominator = deployNowAmount > 0 ? deployNowAmount : allocatedNowTotal;
  return (
    <div className="card-glass overflow-hidden border border-border/80">
      <div className="px-4 py-2 border-b border-border">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Allocation Breakdown
        </p>
        <p className="text-[11px] text-text-secondary mt-1">
          Deploy {formatCurrency(denominator)} now across {displayRanked.length} ticker{displayRanked.length === 1 ? "" : "s"}.
        </p>
      </div>
      <div className="divide-y divide-border">
        {/* Header */}
        <div className="hidden sm:grid grid-cols-12 gap-2 px-4 py-2 text-[10px] uppercase tracking-wide text-text-muted font-semibold bg-surface-elevated/40">
          <div className="col-span-2">Ticker</div>
          <div className="col-span-2">Role</div>
          <div className="col-span-4">Why</div>
          <div className="col-span-2 text-right">Invest now</div>
          <div className="col-span-1 text-right">Now %</div>
          <div className="col-span-1 text-right">After %</div>
        </div>
        {displayRanked.map((rec) => {
          const role = roleMap.get(rec.symbol ?? "") ?? "Supporting";
          const roleClass =
            role === "Primary"
              ? "bg-accent/10 text-accent border border-accent/30"
              : role === "Watch"
                ? "bg-yellow-500/10 text-yellow-300 border border-yellow-400/30"
                : "bg-surface-elevated text-text-muted border border-border";
          const immediate = adjustedAmounts.get(rec.symbol ?? "") ?? 0;
          const why = deriveAllocationWhy(rec, role);
          return (
            <div key={rec.symbol} className="px-4 py-2.5 text-sm">
              <div className="grid grid-cols-12 gap-2 items-start">
                <div className="col-span-6 sm:col-span-2">
                  <span className="font-mono font-bold text-text-primary">{rec.symbol}</span>
                  <p className="text-[11px] text-text-muted leading-snug mt-1">
                    {toCompactLine(rec.staging_instruction || rec.execution_plan || "Buy first tranche now.", 10)}
                  </p>
                  <p className="sm:hidden text-[11px] text-text-muted leading-snug mt-1">
                    {why}
                  </p>
                </div>
                <div className="col-span-6 sm:col-span-2 flex items-start pt-0.5">
                  <span
                    className={cn(
                      "text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded-full",
                      roleClass
                    )}
                  >
                    {role}
                  </span>
                </div>
                <div className="hidden sm:block sm:col-span-4 text-[11px] text-text-muted leading-snug pt-0.5">
                  {why}
                </div>
                <div className="col-span-6 sm:col-span-2 text-right font-mono font-semibold text-text-primary">
                  {formatCurrency(immediate)}
                </div>
                <div className="col-span-3 sm:col-span-1 text-right font-mono text-xs text-text-muted">
                  {(rec.current_weight ?? rec.portfolio_weight ?? 0).toFixed(1)}%
                </div>
                <div className="col-span-3 sm:col-span-1 text-right font-mono text-xs text-accent">
                  {(rec.after_weight ?? 0).toFixed(1)}%
                </div>
              </div>
            </div>
          );
        })}
        <div className="px-4 py-2 bg-surface-elevated/20 flex items-center justify-between text-xs">
          <span className="text-text-muted uppercase tracking-wide font-semibold">Total deploying now</span>
          <span className="font-mono font-semibold text-text-primary">{formatCurrency(allocatedNowTotal)}</span>
        </div>
      </div>
    </div>
  );
}

function WhyMadeCutSection({ allocations }: { allocations: EnrichedAllocation[] }) {
  const visible = allocations.filter((rec) => !!(rec.why_selected || rec.rationale));
  if (visible.length === 0) return null;

  return (
    <details className="border border-border/80 rounded-lg p-3">
      <summary className="text-xs font-semibold uppercase tracking-wide text-text-muted cursor-pointer">
        Why this allocation?
      </summary>
      <ul className="space-y-2 mt-2">
        {visible.map((rec, idx) => (
          <li key={`${rec.symbol}-cut`} className="text-xs text-text-secondary leading-snug">
            <span className="font-mono font-semibold text-text-primary">{rec.symbol}</span>
            <ul className="mt-0.5">
              <li className="text-xs text-text-secondary leading-snug">
                • {compactSentence(toCompactLine(rec.why_selected || rec.rationale, 14)) || buildCutBullet(rec, idx, visible.length)}
              </li>
            </ul>
          </li>
        ))}
      </ul>
    </details>
  );
}

function DeploymentRisksSection({
  allocations,
  adaptive,
}: {
  allocations: EnrichedAllocation[];
  adaptive: AdaptiveBlock | null;
}) {
  const topWeight = allocations.reduce((max, rec) => Math.max(max, rec.after_weight ?? 0), 0);
  const reserve = adaptive?.cash_reserve_amount ?? 0;
  const immediate = adaptive?.recommended_deploy_amount ?? 0;
  const risks: string[] = [];

  if (topWeight >= 12) {
    risks.push("Single-position concentration remains elevated after deployment; monitor sizing drift.");
  }
  if (reserve <= 0 && immediate > 0) {
    risks.push("No pullback reserve remains after this deployment; new volatility may reduce flexibility.");
  }

  if (risks.length === 0) {
    return (
      <div className="p-3 border border-yellow-500/30 bg-yellow-500/5 rounded-lg">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Risks
        </p>
        <p className="text-xs text-text-secondary mt-2">
          No immediate deployment concentration flags.
        </p>
      </div>
    );
  }

  return (
    <div className="p-3 space-y-2 border border-yellow-500/30 bg-yellow-500/5 rounded-lg">
      <p className="text-xs font-semibold uppercase tracking-wide text-yellow-200">
        ⚠ Risks to watch
      </p>
      <ul className="space-y-1.5">
        {risks.slice(0, 2).map((risk) => (
          <li key={risk} className="text-xs text-yellow-300 leading-snug">
            • {risk}
          </li>
        ))}
      </ul>
    </div>
  );
}

function WhyThisPlanCard({ explanation }: { explanation?: string }) {
  if (!explanation) return null;
  return (
    <div className="card-glass p-4 space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
        Why this plan
      </p>
      <p className="text-sm text-text-secondary leading-relaxed">{explanation}</p>
    </div>
  );
}

function SkippedSection({ exclusions }: { exclusions: AllocationExclusion[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card-glass overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-surface-elevated/40 transition-colors"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Skipped / excluded · {exclusions.length}
        </p>
        <ChevronIcon className={cn("w-4 h-4 text-text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-border divide-y divide-border">
          {exclusions.map((e) => (
            <div key={e.ticker} className="px-4 py-2 flex items-center justify-between gap-3 text-xs">
              <span className="font-mono font-semibold text-text-primary">{e.ticker}</span>
              <span className="text-text-muted text-right leading-snug">{e.reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AdvancedDetails({ allocations, adaptive }: { allocations: DepositRecommendation[]; adaptive: AdaptiveBlock | null }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card-glass overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-surface-elevated/40 transition-colors"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Advanced details
        </p>
        <ChevronIcon className={cn("w-4 h-4 text-text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-border p-4 space-y-3">
          <InvestingStyleAdjustment adaptive={adaptive} />
          {allocations.map((rec) => (
            <div key={rec.symbol} className="border border-border rounded-lg p-3 space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="font-mono font-bold text-text-primary">{rec.symbol}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted uppercase">
                  {rec.conviction_level ?? "—"}
                </span>
              </div>
              {rec.why && <DeployMemo label="WHY" text={rec.why} tone="positive" />}
              {rec.risk && <DeployMemo label="RISK" text={rec.risk} tone="negative" />}
              {rec.do && <DeployMemo label="ACTION" text={cleanActionText(rec.do)} tone="neutral" />}
              {rec.alt_view && rec.alt_view !== "—" && (
                <DeployMemo label="ALT VIEW" text={rec.alt_view} tone="neutral" />
              )}
              <div className="flex gap-3 flex-wrap text-[10px] text-text-muted">
                <span>Score: {rec.score?.toFixed(2) ?? "—"}</span>
                <span>Conviction: {rec.conviction_score?.toFixed(2) ?? "—"}</span>
                {rec.category && <span>Category: {rec.category}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function OutcomePLSection({ outcomes }: { outcomes: DecisionLogEntry[] }) {
  const tracked = outcomes.filter((o) => o.return_pct != null);
  if (tracked.length === 0) return null;

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-text-muted px-0.5">
        Decision P&amp;L
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {tracked.map((entry) => {
          const isGain = (entry.return_pct ?? 0) >= 0;
          const daysHeld = Math.round(
            (Date.now() - new Date(entry.created_at).getTime()) / 86_400_000
          );
          const statusLabel =
            entry.status === "closed" ? (isGain ? "WIN" : "LOSS") : "ACTIVE";
          const statusStyle =
            entry.status === "closed"
              ? isGain
                ? "bg-green-500/10 text-green-400 border-green-500/30"
                : "bg-red-500/10 text-red-400 border-red-500/30"
              : "bg-blue-500/10 text-blue-400 border-blue-500/30";

          return (
            <div
              key={entry.id}
              className={cn(
                "card-glass p-3 border-l-2",
                isGain ? "border-l-green-500" : "border-l-red-500"
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-mono font-bold text-text-primary text-sm">
                    {entry.ticker}
                  </span>
                  <span
                    className={cn(
                      "text-[10px] px-1.5 py-0.5 rounded-full border font-semibold uppercase shrink-0",
                      statusStyle
                    )}
                  >
                    {statusLabel}
                  </span>
                </div>
                <span
                  className={cn(
                    "font-mono font-bold text-sm shrink-0",
                    isGain ? "text-green-400" : "text-red-400"
                  )}
                >
                  {isGain ? "+" : ""}
                  {entry.return_pct!.toFixed(2)}%
                </span>
              </div>
              <div className="flex items-center justify-between mt-1.5 text-[10px] text-text-muted">
                <span>
                  {entry.price_at_decision != null && (
                    <>{formatCurrency(entry.price_at_decision)}</>
                  )}
                  {entry.current_price != null && entry.price_at_decision != null && (
                    <> → {formatCurrency(entry.current_price)}</>
                  )}
                </span>
                <span>{daysHeld}d held</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CashOverrideWidget() {
  const { data: cash, isLoading } = useCashBalance();
  const setCash = useSetCash();
  const [editing, setEditing] = useState(false);
  const [inputVal, setInputVal] = useState("");

  function startEdit() {
    setInputVal(cash?.manual_override?.toString() ?? cash?.cash_balance?.toString() ?? "0");
    setEditing(true);
  }

  function handleSave() {
    const parsed = parseFloat(inputVal);
    if (!isNaN(parsed)) {
      setCash.mutate(parsed, {
        onSuccess: () => setEditing(false),
      });
    }
  }

  function handleClear() {
    setCash.mutate(null, {
      onSuccess: () => setEditing(false),
    });
  }

  if (isLoading) return null;

  const sourceStyle =
    cash?.source === "plaid"
      ? "bg-blue-500/10 text-blue-400 border-blue-500/20"
      : cash?.source === "manual"
      ? "bg-yellow-500/10 text-yellow-400 border-yellow-500/20"
      : "bg-surface-elevated text-text-muted border-border";

  return (
    <div className="card-glass p-4 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-text-muted uppercase tracking-wide font-semibold">
          Cash Balance
        </p>
        {cash?.source && (
          <span className={cn("text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase", sourceStyle)}>
            {cash.source}
          </span>
        )}
      </div>

      {editing ? (
        <div className="space-y-2">
          <div className="flex gap-2 items-center">
            <div className="relative flex-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-sm">$</span>
              <input
                type="number"
                value={inputVal}
                onChange={(e) => setInputVal(e.target.value)}
                className="w-full pl-7 pr-3 py-2 bg-surface border border-border rounded-lg text-text-primary font-mono text-sm focus:outline-none focus:ring-1 focus:ring-accent"
                autoFocus
              />
            </div>
            <button
              onClick={handleSave}
              disabled={setCash.isPending}
              className="px-3 py-2 bg-accent text-background rounded-lg text-xs font-semibold hover:bg-accent-hover disabled:opacity-50 transition-colors"
            >
              {setCash.isPending ? <Spinner className="h-3 w-3" /> : "Save"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="px-3 py-2 bg-surface-elevated text-text-muted rounded-lg text-xs hover:text-text-primary transition-colors"
            >
              Cancel
            </button>
          </div>
          {cash?.manual_override !== null && cash?.manual_override !== undefined && (
            <button
              onClick={handleClear}
              className="text-xs text-danger hover:text-danger/80 transition-colors"
            >
              Clear override
            </button>
          )}
        </div>
      ) : (
        <div className="flex items-center justify-between">
          <span className="font-mono text-text-primary font-semibold">
            {cash ? formatCurrency(cash.cash_balance) : "—"}
          </span>
          <button
            onClick={startEdit}
            className="p-1.5 text-text-muted hover:text-text-primary transition-colors rounded-md hover:bg-surface-elevated"
            aria-label="Edit cash balance"
          >
            <PencilIcon className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}

function DeployMemo({
  label,
  text,
  tone,
}: {
  label: string;
  text: string;
  tone: "positive" | "negative" | "neutral";
}) {
  const labelCls =
    tone === "positive"
      ? "text-green-400"
      : tone === "negative"
      ? "text-red-400"
      : "text-text-muted";
  return (
    <div className="rounded-md bg-surface-elevated/40 px-3 py-1.5">
      <p className={cn("text-[10px] uppercase tracking-wide font-semibold mb-0.5", labelCls)}>
        {label}
      </p>
      <p className="text-xs text-text-secondary leading-relaxed">
        {label === "ACTION" ? cleanActionText(text) : toCompactLine(text, 14)}
      </p>
    </div>
  );
}

// Icons

function DecisionLogMemoryPanel({
  deployPlan,
  recommendations,
  amount,
  adaptive,
}: {
  deployPlan: DepositPlanResult;
  recommendations: EnrichedAllocation[];
  amount: number;
  adaptive: AdaptiveBlock | null;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const createLog = useCreateDecisionMemoryLog();
  const updateLog = useUpdateDecisionMemoryLog();
  const { data: recentLogs } = useDecisionMemoryLogs(6, true);
  const evaluateLog = useEvaluateDecisionMemoryLog();
  const { data: insights } = useDecisionPerformanceInsights(true);
  const [savedLog, setSavedLog] = useState<DecisionMemoryLog | null>(null);
  const [saveMessage, setSaveMessage] = useState<string>("");
  const [notes, setNotes] = useState<string>("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [executeMessage, setExecuteMessage] = useState("");
  const [actualDecisions, setActualDecisions] = useState<ActualDecisionItem[]>(
    buildInitialActualDecisions(recommendations)
  );

  const activeLog = savedLog;
  const delta = activeLog?.decision_delta;
  const behaviorLabel =
    activeLog?.risk_behavior === "more_conservative"
      ? "More conservative than model"
      : activeLog?.risk_behavior === "more_aggressive"
      ? "More aggressive than model"
      : "Aligned with model";
  const deployedPct = delta?.total_recommended ? Math.round((delta.total_actual / delta.total_recommended) * 100) : 0;
  const capitalBehavior = delta
    ? delta.deploy_delta < -0.5
      ? "Under-deployed"
      : delta.deploy_delta > 0.5
      ? "Over-deployed"
      : "Fully deployed"
    : "Fully deployed";
  const styleShiftSummary = delta
    ? delta.replaced_tickers.length
      ? `${delta.replaced_tickers
          .slice(0, 2)
          .map((item) => `${item.from || "—"} → ${item.to || "—"}${item.reason ? ` (${item.reason})` : " (style shift)"}`)
          .join(" • ")}${delta.replaced_tickers.length > 2 ? ` • +${delta.replaced_tickers.length - 2} more` : ""}`
      : "No replacements"
    : "No replacements";
  const performance = activeLog?.performance_snapshot?.portfolio;
  const performanceStatus = activeLog?.performance_snapshot?.status ?? "ready";
  const perfDelta = performance?.delta ?? 0;
  const hasQualityIssues = Boolean(activeLog?.performance_snapshot?.data_quality?.length);
  const showPortfolioPerformance = performanceStatus === "ready" || performanceStatus === "partial_data";
  const perfSummary = performanceStatus === "ready"
    ? performance?.summary_text
      ? performance.summary_text
      : Math.abs(perfDelta) < 0.05
      ? "You matched the model"
      : perfDelta > 0.05
      ? `You outperformed the model by ${perfDelta.toFixed(2)}%`
      : `You underperformed the model by ${Math.abs(perfDelta).toFixed(2)}%`
    : performance?.summary_text ?? "Performance comparison is still collecting data.";

  function updateDecision(index: number, patch: Partial<ActualDecisionItem>) {
    setActualDecisions((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  async function onSaveLog() {
    if (createLog.isPending) return;
    const created = await createLog.mutateAsync({ snapshot });
    setSavedLog(created);
    setNotes(created.notes ?? "");
    setActualDecisions(created.actual_decisions?.length ? created.actual_decisions : buildInitialActualDecisions(recommendations));
    setSaveMessage("Decision log saved");
  }

  async function onSaveActual() {
    if (!savedLog || updateLog.isPending) return;
    const patch = {
      actual_decisions: actualDecisions.map((row) => ({ ...row, executed_at: row.executed_at ?? new Date().toISOString() })),
      notes,
    };
    const updated = await updateLog.mutateAsync({ id: savedLog.id, patch });
    setSavedLog(updated);
    setSaveMessage("Actual decisions updated");
  }

  async function onEvaluatePerformance() {
    if (!savedLog || evaluateLog.isPending) return;
    const evaluated = await evaluateLog.mutateAsync(savedLog.id);
    setSavedLog(evaluated);
    setSaveMessage("Performance refreshed");
  }

  const logsToShow = recentLogs ?? [];
  const insightsConfidenceLabel =
    insights?.confidence === "low" ? "Early signal" : insights?.confidence === "medium" ? "Building history" : "Higher confidence";
  const winCount = insights ? Math.round((insights.summary.win_rate_vs_model ?? 0) * insights.eligible_logs) : 0;
  const replacementSummary = delta?.replaced_tickers?.length
    ? delta.replaced_tickers
        .slice(0, 2)
        .map((item) => `${item.from || "—"} → ${item.to || "—"}`)
        .join(" • ")
    : "No replacements";
  const performanceStatusLabel =
    performanceStatus === "baseline_captured"
      ? "Baseline captured"
      : performanceStatus === "ready" || performanceStatus === "partial_data"
      ? "Ready to evaluate"
      : "Not enough history";
  const deployNow = adaptive?.recommended_deploy_amount ?? deployPlan.plan.recommended_deploy_amount ?? amount;
  const reserveAmount = Math.max(0, amount - deployNow);
  const rankedForLog = useMemo(
    () => [...recommendations].sort((a, b) => (b.immediate_amount ?? b.amount ?? 0) - (a.immediate_amount ?? a.amount ?? 0)),
    [recommendations],
  );
  const adjustedAmountsForLog = useMemo(() => computeAdjustedAmounts(rankedForLog, deployNow), [rankedForLog, deployNow]);
  const tickerContext = useMemo(
    () =>
      rankedForLog.map((rec, idx) => ({
        ticker: rec.symbol ?? "",
        amount: adjustedAmountsForLog.get(rec.symbol ?? "") ?? 0,
        role: deriveRoleLabel(rec, idx, rankedForLog.length),
        why_reason: rec.why_selected ?? rec.why ?? rec.rationale ?? null,
      })),
    [adjustedAmountsForLog, rankedForLog],
  );
  const snapshot = useMemo(
    () =>
      buildRecommendationSnapshotWithContext(deployPlan, {
        entered_capital_amount: amount,
        deploy_now_amount: deployNow,
        reserve_amount: reserveAmount,
        ticker_context: tickerContext,
      }),
    [amount, deployNow, deployPlan, reserveAmount, tickerContext],
  );
  const executeRows = tickerContext.filter((item) => item.ticker && item.amount > 0);

  return (
    <div className="card-glass p-4 space-y-3 border border-border/80">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Step 3 — Execute &amp; record
        </p>
        <span
          className={cn(
            "text-[11px] px-2 py-1 rounded-full border",
            activeLog ? "text-emerald-300 border-emerald-500/30 bg-emerald-500/10" : "text-text-muted border-border bg-surface-elevated/40"
          )}
        >
          {activeLog ? "Saved" : "Not saved"}
        </span>
      </div>
      {activeLog ? (
        <div className="grid gap-2 sm:grid-cols-2 text-xs">
          <p className="text-text-secondary">
            Deployed: <span className="text-text-primary font-semibold">{delta ? formatCurrency(delta.total_actual) : "—"}</span>
            {delta ? <span className="text-text-muted"> ({deployedPct}%)</span> : null}
          </p>
          <p className="text-text-secondary">
            Replacements: <span className="text-text-primary">{replacementSummary}</span>
          </p>
          <p className="text-text-secondary sm:col-span-2">
            Performance status: <span className="text-text-primary">{performanceStatusLabel}</span>
          </p>
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => {
            setConfirmOpen(true);
          }}
          className="px-3 py-1.5 rounded-md text-xs font-semibold bg-accent text-background"
        >
          Invest {formatCurrency(deployNow)} Now
        </button>
        <button
          onClick={() => document.getElementById("step-1")?.scrollIntoView({ behavior: "smooth", block: "start" })}
          className="px-3 py-1.5 rounded-md text-xs font-semibold border border-border bg-surface-elevated/40 text-text-primary"
        >
          Modify Plan
        </button>
        <button
          onClick={activeLog ? onSaveActual : onSaveLog}
          disabled={activeLog ? updateLog.isPending : createLog.isPending}
          className="px-3 py-1.5 rounded-md text-xs font-semibold border border-border bg-surface-elevated/40 text-text-primary disabled:opacity-60"
        >
          {activeLog
            ? updateLog.isPending
              ? "Saving..."
              : "Update Actuals"
            : createLog.isPending
            ? "Saving..."
            : "Save Decision Log"}
        </button>
        {activeLog ? (
          <button
            onClick={() => setDetailsOpen((prev) => !prev)}
            className="px-3 py-1.5 rounded-md text-xs font-semibold border border-border bg-surface-elevated/40 text-text-primary inline-flex items-center gap-1"
          >
            {detailsOpen ? "Hide details" : "View details"}
            <ChevronIcon className={cn("w-3.5 h-3.5 transition-transform", detailsOpen ? "rotate-180" : "")} />
          </button>
        ) : null}
      </div>
      {executeMessage && <p className="text-xs text-text-secondary">{executeMessage}</p>}
      {saveMessage && <p className="text-xs text-green-400">{saveMessage}</p>}
      {confirmOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
          <div className="w-full max-w-md rounded-lg border border-border bg-surface p-4 space-y-3">
            <p className="text-sm font-semibold text-text-primary">Confirm execution</p>
            <div className="text-xs space-y-1">
              <p className="text-text-secondary">
                Total invested now: <span className="font-mono text-text-primary">{formatCurrency(deployNow)}</span>
              </p>
              <p className="text-text-secondary">
                Reserve remaining: <span className="font-mono text-text-primary">{formatCurrency(reserveAmount)}</span>
              </p>
            </div>
            <div className="max-h-48 overflow-y-auto border border-border rounded-md divide-y divide-border">
              {executeRows.map((row) => (
                <div key={`${row.ticker}-confirm`} className="px-3 py-2 flex items-center justify-between text-xs">
                  <span className="font-mono text-text-primary">{row.ticker}</span>
                  <span className="font-mono text-text-secondary">{formatCurrency(row.amount)}</span>
                </div>
              ))}
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmOpen(false)}
                className="px-3 py-1.5 rounded-md text-xs font-semibold border border-border bg-surface-elevated/40 text-text-primary"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  setConfirmOpen(false);
                  setDetailsOpen(true);
                  setExecuteMessage(`Execution focus set for ${formatCurrency(deployNow)}. Confirm orders in your broker, then save your decision log.`);
                }}
                className="px-3 py-1.5 rounded-md text-xs font-semibold bg-accent text-background"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}

      {detailsOpen && savedLog && (
        <div className="space-y-2 border-t border-border pt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Actual Decision</p>
          <div className="space-y-2">
            {actualDecisions.map((row, idx) => (
              <div key={`${row.ticker || "row"}-${idx}`} className="grid grid-cols-12 gap-2 items-center text-xs">
                <div className="col-span-2 flex items-center gap-1.5">
                  <span className="font-mono text-text-primary">{row.ticker || "—"}</span>
                  <span
                    className={cn(
                      "px-1.5 py-0.5 rounded text-[10px] font-semibold",
                      String(row.actual_action || "BOUGHT") === "SKIPPED"
                        ? "bg-red-500/15 text-red-300"
                        : String(row.actual_action || "BOUGHT") === "REPLACED"
                        ? "bg-amber-500/15 text-amber-300"
                        : "bg-emerald-500/15 text-emerald-300"
                    )}
                  >
                    {String(row.actual_action || "BOUGHT") === "SKIPPED"
                      ? "Skipped"
                      : String(row.actual_action || "BOUGHT") === "REPLACED"
                      ? "Replaced"
                      : "Matched"}
                  </span>
                </div>
                <select
                  value={String(row.actual_action || "BOUGHT")}
                  onChange={(e) => updateDecision(idx, { actual_action: e.target.value })}
                  className="col-span-3 bg-surface border border-border rounded px-2 py-1"
                >
                  <option value="BOUGHT">Bought</option>
                  <option value="SKIPPED">Skipped</option>
                  <option value="REPLACED">Replaced</option>
                  <option value="WATCH">Watch</option>
                </select>
                <input
                  type="number"
                  value={row.actual_amount ?? 0}
                  onChange={(e) => updateDecision(idx, { actual_amount: Number(e.target.value) || 0 })}
                  className="col-span-2 bg-surface border border-border rounded px-2 py-1"
                />
                <input
                  placeholder="Replacement"
                  value={row.replacement_ticker ?? ""}
                  onChange={(e) => updateDecision(idx, { replacement_ticker: e.target.value.toUpperCase() || undefined })}
                  className="col-span-2 bg-surface border border-border rounded px-2 py-1"
                />
                <input
                  placeholder="Reason"
                  value={row.reason ?? ""}
                  onChange={(e) => updateDecision(idx, { reason: e.target.value || undefined })}
                  className="col-span-3 bg-surface border border-border rounded px-2 py-1"
                />
              </div>
            ))}
          </div>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes (optional)"
            className="w-full bg-surface border border-border rounded px-2 py-1.5 text-xs"
            rows={2}
          />
        </div>
      )}

      {detailsOpen && activeLog && delta && (
        <div className="border-t border-border pt-3 space-y-1.5">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Decision Summary</p>
          <p className="text-xs text-text-secondary">
            You deployed {formatCurrency(delta.total_actual)} of {formatCurrency(delta.total_recommended)} recommended ({deployedPct}%).
          </p>
          <p className="text-xs text-text-secondary">
            Capital behavior: {capitalBehavior}
          </p>
          <p className="text-xs text-text-secondary">Style shift: {styleShiftSummary}</p>
          <p className="text-xs text-text-secondary">Net effect: {behaviorLabel}</p>
        </div>
      )}
      {detailsOpen && activeLog && (
        <div className="border-t border-border pt-3 space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Performance vs AI</p>
            <button
              onClick={onEvaluatePerformance}
              disabled={evaluateLog.isPending}
              className="px-2 py-1 rounded text-[11px] font-semibold bg-surface-elevated text-text-primary border border-border disabled:opacity-60"
            >
              {evaluateLog.isPending ? "Evaluating..." : "Evaluate"}
            </button>
          </div>
          {performance ? (
            <>
              {performanceStatus === "baseline_captured" ? (
                <p className="text-xs text-amber-300">
                  Performance baseline captured. Re-evaluate next trading day or later.
                </p>
              ) : (
                <p className={cn("text-xs", performanceStatus === "ready" ? (perfDelta >= 0 ? "text-emerald-300" : "text-red-300") : "text-amber-300")}>{perfSummary}</p>
              )}
              {hasQualityIssues ? (
                <p className="text-xs text-amber-300">
                  Data quality note: some tickers are excluded because entry or current prices are missing.
                </p>
              ) : null}
              {showPortfolioPerformance ? (
                <p className="text-xs text-text-secondary">
                  Recommended return: {(performance.recommended_return ?? 0).toFixed(2)}% • Actual return: {(performance.actual_return ?? 0).toFixed(2)}%
                </p>
              ) : null}
              {showPortfolioPerformance ? (
                <p className="text-xs text-text-secondary">Delta: {(performance.delta ?? 0).toFixed(2)}%</p>
              ) : null}
              {activeLog.performance_snapshot?.per_ticker?.length ? (
                <div className="space-y-1 pt-1">
                  {activeLog.performance_snapshot.per_ticker.map((row) => (
                    <p key={row.ticker} className={cn("text-[11px]", performanceStatus === "baseline_captured" ? "text-text-muted/70" : "text-text-muted")}>
                      {(() => {
                        const tickerLabel = row.recommended_ticker && row.actual_ticker && row.recommended_ticker !== row.actual_ticker
                          ? `${row.recommended_ticker} → ${row.actual_ticker}`
                          : row.actual_ticker ?? row.recommended_ticker ?? row.ticker;
                        if (row.status === "missing_price") {
                          return `${tickerLabel}: missing_price (${row.reason ?? "Missing entry price/current price"})`;
                        }
                        if (performanceStatus === "baseline_captured") {
                          return `${tickerLabel}: baseline captured`;
                        }
                        return `${tickerLabel}: AI ${(row.recommended_return_pct ?? 0).toFixed(2)}% • You ${(row.actual_return_pct ?? 0).toFixed(2)}% • Δ ${(row.delta_pct ?? 0).toFixed(2)}%`;
                      })()}
                    </p>
                  ))}
                </div>
              ) : null}
            </>
          ) : (
            <p className="text-xs text-text-secondary">
              Evaluate this log to compare your executed decisions against the model recommendations.
            </p>
          )}
        </div>
      )}
      {detailsOpen && (
        <div className="border-t border-border pt-3 space-y-1.5">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Decision Insights</p>
          {!insights || insights.eligible_logs < 3 ? (
            <p className="text-xs text-text-secondary">Decision insights will appear after 3+ evaluated logs.</p>
          ) : (
            <div className="space-y-1">
              {(insights.confidence === "low" || insights.confidence === "medium") ? (
                <p className="text-[11px] text-amber-300">
                  {insightsConfidenceLabel}: not enough history for a strong conclusion.
                </p>
              ) : null}
              <p className="text-xs text-text-secondary">
                You beat the model in {winCount} of {insights.eligible_logs} evaluated decisions.
              </p>
              <p className={cn("text-xs", (insights.summary.avg_delta ?? 0) >= 0 ? "text-emerald-300" : "text-red-300")}>
                Average delta vs model: {(insights.summary.avg_delta ?? 0) >= 0 ? "+" : ""}{(insights.summary.avg_delta ?? 0).toFixed(2)}%
              </p>
              {insights.behavior_insights.etf_replacements.count > 0 && insights.behavior_insights.etf_replacements.avg_delta !== null ? (
                <p className="text-xs text-text-secondary">
                  ETF replacements: {(insights.behavior_insights.etf_replacements.avg_delta ?? 0) >= 0 ? "+" : ""}
                  {(insights.behavior_insights.etf_replacements.avg_delta ?? 0).toFixed(2)}% avg delta
                </p>
              ) : null}
              {insights.behavior_insights.under_deployment.count > 0 && insights.behavior_insights.under_deployment.avg_delta !== null ? (
                <p className="text-xs text-text-secondary">
                  Under-deployment ({insights.behavior_insights.under_deployment.count} logs): {(insights.behavior_insights.under_deployment.avg_delta ?? 0) >= 0 ? "helped" : "hurt"} by{" "}
                  {Math.abs(insights.behavior_insights.under_deployment.avg_delta ?? 0).toFixed(2)}% avg delta.
                </p>
              ) : null}
              {insights.summary.worst_override ? (
                <p className="text-xs text-text-secondary">
                  Worst override: {insights.summary.worst_override.ticker}, {insights.summary.worst_override.delta_pct >= 0 ? "+" : ""}
                  {insights.summary.worst_override.delta_pct.toFixed(2)}%
                </p>
              ) : null}
            </div>
          )}
        </div>
      )}

      {detailsOpen && logsToShow.length > 0 && (
        <div className="border-t border-border pt-2">
          <button onClick={() => setHistoryOpen((v) => !v)} className="w-full flex justify-between text-xs text-text-muted">
            <span>Recent Decision Logs</span>
            <span>{historyOpen ? "−" : "+"}</span>
          </button>
          {historyOpen && (
            <div className="mt-2 space-y-1">
              {logsToShow.map((log) => {
                const recs = Array.isArray((log.recommendation_snapshot as any)?.normalized_tickers)
                  ? ((log.recommendation_snapshot as any).normalized_tickers as Array<Record<string, unknown>>)
                  : [];
                const recTotal = recs.reduce((sum, row) => sum + (Number(row.amount) || 0), 0);
                const actualTotal = (log.actual_decisions || []).reduce((sum, row) => sum + (Number(row.actual_amount) || 0), 0);
                return (
                  <button
                    key={log.id}
                    onClick={() => {
                      setSavedLog(log);
                      setActualDecisions(log.actual_decisions?.length ? log.actual_decisions : buildInitialActualDecisions(recommendations));
                      setNotes(log.notes ?? "");
                      setSaveMessage("");
                    }}
                    className="w-full text-left rounded border border-border px-2 py-1.5 text-xs hover:bg-surface-elevated"
                  >
                    <div className="flex justify-between text-text-primary">
                      <span>{new Date(log.created_at).toLocaleDateString()}</span>
                      <span className="uppercase">{log.status.replaceAll("_", " ")}</span>
                    </div>
                    <div className="text-text-muted mt-0.5">
                      {(() => {
                        const delta = log.decision_delta;
                        const deployedPct = delta?.total_recommended
                          ? Math.round((delta.total_actual / delta.total_recommended) * 100)
                          : recTotal > 0
                          ? Math.round((actualTotal / recTotal) * 100)
                          : 0;
                        const skippedCount = delta?.skipped_tickers?.length ?? 0;
                        const replacedCount = delta?.replaced_tickers?.length ?? 0;
                        const behavior =
                          log.risk_behavior === "more_conservative"
                            ? "Conservative"
                            : log.risk_behavior === "more_aggressive"
                            ? "Aggressive"
                            : "Aligned";
                        return `${deployedPct}% deployed • ${skippedCount} skipped • ${replacedCount} replaced • ${behavior}`;
                      })()}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ArrowRightIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
      <path d="M5 12h14M12 5l7 7-7 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ChevronIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PencilIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
