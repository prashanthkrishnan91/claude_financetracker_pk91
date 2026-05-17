"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { createPortal } from "react-dom";
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
  useDeployV3Plan,
  useDeployV3Readiness,
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
import { DeployV3Panel } from "@/components/cards/DeployV3Panel";
import { DeployV3ReadinessPanel } from "@/components/cards/DeployV3ReadinessPanel";
import { DeployV3TargetSetupPanel } from "@/components/cards/DeployV3TargetSetupPanel";
import { mapDeployV3ToStep2 } from "@/lib/deploy-v3-step2-mapper";
import { buildInitialActualDecisions, buildRecommendationSnapshotWithContext, dedupeDecisionLogsForDisplay, deriveExecutionStatus, getDecisionLogSessionKey } from "@/lib/decision-log";
import type { ExecutionStatus } from "@/lib/decision-log";
import { buildDeployV3DecisionSnapshot, buildDeployV3InitialActualDecisions, buildDeployV3ManualRow, buildDeployV3SessionKey, classifyActualAction, computeJournalTotals, getDeployV3LogSessionKey, isManualDecisionRow, isSessionKeyChanged, shouldUpdateExistingLog } from "@/lib/deploy-v3-decision-log";
import type { DeployV3PlanResponse } from "@/lib/api";
import {
  CashPlanningStrip,
  LedgerPlanSummaryBar,
  LedgerActionCard,
  GuardrailStatusRail,
  PortfolioShapePreview,
  ComingLaterLedgerSection,
  NonBrokerageDisclaimer,
} from "@/components/cards/DeployLedger";
import {
  buildLedgerItems,
  buildLedgerPlanState,
  buildGuardrailGroups,
} from "@/lib/deploy-ledger";

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

function getCanonicalDeployNow(plan: DepositPlanResult["plan"], adaptive: AdaptiveBlock | null, fallbackAmount: number): number {
  return plan.deploy_now_amount ?? plan.recommended_deploy_amount ?? adaptive?.recommended_deploy_amount ?? fallbackAmount;
}

function getCanonicalReserve(plan: DepositPlanResult["plan"], adaptive: AdaptiveBlock | null, fallbackAmount: number): number {
  const deployNow = getCanonicalDeployNow(plan, adaptive, fallbackAmount);
  return plan.reserve_amount ?? plan.cash_reserve ?? adaptive?.cash_reserve_amount ?? Math.max(0, fallbackAmount - deployNow);
}

export default function DepositsPage() {
  const [amount, setAmount] = useState(900);
  const { data: summary } = usePortfolioSummary();
  const portfolioBalance = summary?.total_equity ?? 0;
  const { data: deployPlan, isLoading: isPlanLoading } = useDepositPlan(amount, portfolioBalance);
  const { data: outcomes } = useDecisionOutcomes();
  const { data: readinessDiagnostic } = useDeployV3Readiness();
  // Only enable when amount is a valid positive number — prevents the deposits
  // page from subscribing to the base no-amount query key (["deploy_v3","plan"])
  // and receiving the stale no-cash result cached by DeployV3Panel.
  const deployV3Enabled = Number.isFinite(amount) && amount > 0;
  const { data: v3Plan, isLoading: isV3Loading } = useDeployV3Plan(
    deployV3Enabled,
    deployV3Enabled ? amount : undefined,
  );

  // Map Deploy v3 plan → Step 2 display. Falls back gracefully when unavailable.
  const step2 = mapDeployV3ToStep2(v3Plan ?? null);
  const useV3ForStep2 = step2.state !== "not_available";

  return (
    <>
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h1 className="text-xl font-display text-text-primary">Capital Allocation Ledger</h1>
            <p className="text-[10px] text-text-muted mt-0.5 hidden sm:block">
              Planning only — not a brokerage account. Intel v3 owns all decisions.
            </p>
          </div>
          <span className="text-[10px] px-2 py-0.5 rounded-pill border border-border bg-surface-elevated text-text-muted font-semibold uppercase tracking-wide">
            Read-only · No trades
          </span>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6 space-y-5">

        <CashPlanningStrip
          amount={amount}
          onChange={setAmount}
          availableCash={summary?.cash_balance ?? null}
        />

        {/* Step 2 — powered by Deploy v3 when available, legacy plan as fallback */}
        {(isV3Loading || isPlanLoading) && !v3Plan && !deployPlan ? (
          <InlineLoader text="Building deployment plan…" />
        ) : useV3ForStep2 ? (
          <DeployV3Step2Section step2={step2} v3Plan={v3Plan ?? null} amount={amount} />
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

        {/* Setup & diagnostics — collapsed by default */}
        <details className="card-glass border border-border/70">
          <summary className="px-4 py-3 text-[10px] font-semibold uppercase tracking-widest text-text-muted cursor-pointer select-none">
            Setup &amp; diagnostics
          </summary>
          <div className="px-4 pb-4 space-y-4 pt-2">
            <DeployV3Panel />
            <DeployV3ReadinessPanel />
            <DeployV3TargetSetupPanel readinessDiagnostic={readinessDiagnostic} />
          </div>
        </details>

      </main>
    </>
  );
}

// ── Deploy v3 Step 2 section ──────────────────────────────────────────────────

import type { Step2Result } from "@/lib/deploy-v3-step2-mapper";

function DeployV3Step2Section({
  step2,
  v3Plan,
  amount,
}: {
  step2: Step2Result;
  v3Plan: DeployV3PlanResponse | null;
  amount: number;
}) {
  const allItems = v3Plan?.items ?? [];
  const ledgerItems = buildLedgerItems(allItems);
  const planState = buildLedgerPlanState(v3Plan?.rollup?.plan_readiness_status ?? null, v3Plan?.rollup ?? null);
  const guardrailGroups = buildGuardrailGroups(ledgerItems);

  // Actionable items (BUY/TRIM/SELL with positive dollar amounts) surface as action cards.
  const actionCardItems = ledgerItems.filter(
    (it) => it.action !== "HOLD" && it.dollarAmount != null && it.dollarAmount > 0,
  );

  return (
    <div className="space-y-4">
      {/* Ledger main section */}
      <section
        aria-label="Capital allocation plan"
        className="card-glass border border-border/80 overflow-hidden"
      >
        {/* Section header */}
        <div className="px-4 pt-4 pb-3 border-b border-border/60 flex items-center justify-between gap-2 flex-wrap">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
            Capital allocation plan
          </p>
          <span className="text-[10px] px-2 py-0.5 rounded-pill border border-border bg-surface-elevated text-text-muted font-semibold uppercase tracking-wide">
            Intel v3 · Deploy v3
          </span>
        </div>

        <div className="px-4 py-4 space-y-5">
          {/* Plan state summary bar */}
          {v3Plan && (
            <LedgerPlanSummaryBar
              planState={planState}
              cashToDeploy={step2.cash_to_deploy}
              amountAware={step2.amount_aware}
            />
          )}

          {/* Setup incomplete */}
          {step2.state === "setup_incomplete" && (
            <div className="rounded-md border border-action-trim/25 bg-action-trim/5 px-4 py-3 space-y-1.5">
              <p className="text-xs font-semibold text-action-trim">Setup required before sizing</p>
              <p className="text-[11px] text-text-secondary leading-snug">
                Target allocations or deploy policy settings are not fully configured yet.
                Use the Setup &amp; diagnostics section below to complete setup.
              </p>
              <p className="text-[10px] text-text-muted">
                Intel v3 Buy / Hold / Trim / Sell authority is unaffected — only exact-dollar sizing is paused.
              </p>
            </div>
          )}

          {/* No moves */}
          {step2.state === "no_moves" && (
            <div className="rounded-md border border-action-hold/25 bg-action-hold/5 px-4 py-3 space-y-1.5">
              <p className="text-xs font-semibold text-action-hold">Hold as planned — no moves needed</p>
              <p className="text-[11px] text-text-secondary leading-snug">
                The current portfolio already matches your targets.
                No cash deployment moves were produced from your certified portfolio model.
              </p>
              <p className="text-[10px] text-text-muted">
                Intel v3 owns all Buy / Hold / Trim / Sell decisions. Deploy only sizes validated moves.
              </p>
            </div>
          )}

          {/* Recommended dollar actions */}
          {actionCardItems.length > 0 && (
            <div className="space-y-3">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
                Recommended actions
              </p>
              {/* Mobile: stack cards. Desktop: keep cards full-width for readability. */}
              <div className="grid gap-2 sm:gap-3">
                {actionCardItems.map((item) => (
                  <LedgerActionCard key={item.ticker} item={item} />
                ))}
              </div>
              <p className="text-[10px] text-text-muted leading-snug">
                Intel v3 policy owns all Buy / Hold / Trim / Sell decisions.
                Pending tax and guardrail checks — do not treat as executable trade instructions.
                {step2.amount_aware && (
                  <> Sized for user-entered planning capital — not broker-verified cash.</>
                )}
              </p>
            </div>
          )}

          {/* No actionable cards + no_items → prompt */}
          {step2.state === "not_available" && (
            <p className="text-xs text-text-secondary leading-snug">
              Run Intel v3 first to generate a capital allocation plan.
            </p>
          )}

          {/* Guardrail status rail */}
          {guardrailGroups.length > 0 && (
            <div className="border-t border-border/50 pt-4">
              <GuardrailStatusRail groups={guardrailGroups} />
            </div>
          )}

          {/* Portfolio shape preview — honest Coming Later */}
          <div className="border-t border-border/50 pt-4">
            <PortfolioShapePreview />
          </div>

          {/* Coming later modules */}
          <div className="border-t border-border/50 pt-4">
            <ComingLaterLedgerSection />
          </div>

          {/* Non-brokerage disclaimer */}
          <div className="border-t border-border/50 pt-3">
            <NonBrokerageDisclaimer />
          </div>
        </div>
      </section>

      <DeployV3DecisionLogSection step2={step2} v3Plan={v3Plan} amount={amount} />

      <Link
        href="/dashboard/recommendations"
        className="flex items-center gap-1.5 text-xs text-accent hover:text-accent-hover transition-colors font-semibold"
      >
        View full Intel v3 analysis
        <ArrowRightIcon className="w-3.5 h-3.5" />
      </Link>
    </div>
  );
}

function DeployV3AllocationTable({ items }: { items: Step2Result["items"] }) {
  if (items.length === 0) return null;
  return (
    <div className="card-glass overflow-hidden border border-border/80">
      <div className="px-4 py-2.5 border-b border-border">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Allocation breakdown — Deploy v3
        </p>
      </div>
      {/* Desktop table header */}
      <div className="hidden sm:grid grid-cols-12 gap-2 px-4 py-2 text-[10px] uppercase tracking-wide text-text-muted font-semibold bg-surface-elevated/40 border-b border-border/60">
        <div className="col-span-4">Ticker</div>
        <div className="col-span-2">Action</div>
        <div className="col-span-3">Why</div>
        <div className="col-span-2 text-right">Amount</div>
        <div className="col-span-1 text-right">Status</div>
      </div>
      <div className="divide-y divide-border/60">
        {items.map((item) => (
          <div key={item.ticker} className="px-4 py-3 hover:bg-surface-elevated/20 transition-colors">
            {/* Mobile layout: stack */}
            <div className="sm:hidden space-y-1">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-bold text-sm text-text-primary">{item.ticker}</span>
                  <span className={cn(
                    "text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded border",
                    item.action === "BUY"  ? "bg-action-buy/10 text-action-buy border-action-buy/30" :
                    item.action === "TRIM" || item.action === "SELL" ? "bg-action-sell/10 text-action-sell border-action-sell/30" :
                    "bg-action-hold/10 text-action-hold border-action-hold/25",
                  )}>
                    {item.action}
                  </span>
                </div>
                <span className="font-mono font-semibold text-sm text-text-primary tabular-nums">
                  {item.dollar_amount != null ? formatCurrency(item.dollar_amount) : "—"}
                </span>
              </div>
              {item.reason && (
                <p className="text-[10px] text-text-muted leading-snug">{item.reason}</p>
              )}
            </div>
            {/* Desktop layout: grid */}
            <div className="hidden sm:grid grid-cols-12 gap-2 items-center">
              <div className="col-span-4 font-mono font-bold text-text-primary text-sm">{item.ticker}</div>
              <div className="col-span-2">
                <span className={cn(
                  "text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded border",
                  item.action === "BUY"  ? "bg-action-buy/10 text-action-buy border-action-buy/30" :
                  item.action === "TRIM" || item.action === "SELL" ? "bg-action-sell/10 text-action-sell border-action-sell/30" :
                  "bg-action-hold/10 text-action-hold border-action-hold/25",
                )}>
                  {item.action}
                </span>
              </div>
              <div className="col-span-3 text-[10px] text-text-muted leading-snug">{item.reason}</div>
              <div className="col-span-2 text-right font-mono font-semibold text-text-primary tabular-nums text-sm">
                {item.dollar_amount != null ? formatCurrency(item.dollar_amount) : "—"}
              </div>
              <div className="col-span-1 text-right text-[9px] text-text-muted">
                {item.final_actionability_status.replace(/_/g, " ")}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Deploy v3 Step 3 — decision logging ──────────────────────────────────────

const V3_ACTUAL_STATUS_OPTIONS: string[] = [
  "BOUGHT",
  "PARTIAL",
  "SKIPPED",
  "WATCHED",
  "TRIMMED",
  "SOLD",
  "HELD",
];

function DeployV3DecisionLogSection({
  step2,
  v3Plan,
  amount,
}: {
  step2: Step2Result;
  v3Plan: DeployV3PlanResponse | null;
  amount: number;
}) {
  const createLog = useCreateDecisionMemoryLog();
  const updateLog = useUpdateDecisionMemoryLog();
  const evaluateLog = useEvaluateDecisionMemoryLog();
  const { data: recentLogs } = useDecisionMemoryLogs(10, true);
  const [savedLog, setSavedLog] = useState<DecisionMemoryLog | null>(null);
  const [actualDecisions, setActualDecisions] = useState<ActualDecisionItem[]>([]);
  const [notes, setNotes] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [manualDraft, setManualDraft] = useState<{ ticker: string; action: "BUY" | "TRIM" | "SELL"; amount: string; note: string }>({
    ticker: "",
    action: "BUY",
    amount: "",
    note: "",
  });

  const sessionKey = buildDeployV3SessionKey(v3Plan?.run_id, step2.items);

  // Track the previous sessionKey so we can reset stale state when the active
  // Deploy v3 plan/session changes (different amount, recommendation set, or
  // run_id). Without this, savedLog/actualDecisions/manual rows from a prior
  // session would silently overwrite the previous log on save.
  const previousSessionKeyRef = useRef<string | null>(null);

  // Initialize actual decisions from visible Step 2 items
  useEffect(() => {
    if (step2.state !== "has_moves" || step2.items.length === 0) return;
    if (actualDecisions.length > 0) return;
    setActualDecisions(buildDeployV3InitialActualDecisions(step2.items));
  }, [actualDecisions.length, step2.items, step2.state]);

  // Rehydrate from a matching existing log (same active plan fingerprint)
  const matchingLog = useMemo(() => {
    if (!recentLogs?.length) return null;
    return recentLogs.find((log) => getDeployV3LogSessionKey(log) === sessionKey) ?? null;
  }, [recentLogs, sessionKey]);

  // Reset stale state when sessionKey changes (amount / recommendation set / run_id
  // changed). Drops manual rows and any savedLog from the previous session;
  // matchingLog effect below then rehydrates from the new session's saved log
  // if one exists, otherwise the init effect leaves fresh Step 2 defaults.
  useEffect(() => {
    if (isSessionKeyChanged(previousSessionKeyRef.current, sessionKey)) {
      setSavedLog(null);
      setNotes("");
      setSaveSuccess(false);
      setErrorMessage("");
      if (step2.state === "has_moves" && step2.items.length > 0) {
        setActualDecisions(buildDeployV3InitialActualDecisions(step2.items));
      } else {
        setActualDecisions([]);
      }
    }
    previousSessionKeyRef.current = sessionKey;
  }, [sessionKey, step2.items, step2.state]);

  useEffect(() => {
    if (savedLog || !matchingLog) return;
    // Guard: only rehydrate when matchingLog belongs to the current sessionKey.
    if (getDeployV3LogSessionKey(matchingLog) !== sessionKey) return;
    setSavedLog(matchingLog);
    setNotes(matchingLog.notes ?? "");
    if (matchingLog.actual_decisions?.length) {
      setActualDecisions(matchingLog.actual_decisions);
    }
  }, [matchingLog, savedLog, sessionKey]);

  function updateRow(index: number, patch: Partial<ActualDecisionItem>) {
    setActualDecisions((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  function removeRow(index: number) {
    setActualDecisions((prev) => prev.filter((_, i) => i !== index));
  }

  function addManualRow() {
    const ticker = manualDraft.ticker.trim().toUpperCase();
    const amt = Number(manualDraft.amount);
    if (!ticker || !Number.isFinite(amt) || amt <= 0) {
      setErrorMessage("Manual row needs a ticker and a positive amount.");
      return;
    }
    setErrorMessage("");
    const row = buildDeployV3ManualRow(ticker, manualDraft.action, amt, manualDraft.note);
    setActualDecisions((prev) => [...prev, row]);
    setManualDraft({ ticker: "", action: "BUY", amount: "", note: "" });
  }

  async function onSave() {
    if (createLog.isPending || updateLog.isPending) return;
    setErrorMessage("");
    setSaveSuccess(false);
    try {
      const base = buildDeployV3DecisionSnapshot(
        step2,
        v3Plan ? { snapshot_id: v3Plan.snapshot_id, run_id: v3Plan.run_id, plan_status: v3Plan.plan_status } : null,
        { entered_amount: amount },
      );
      const deployNowAmount =
        step2.amount_aware && typeof step2.cash_to_deploy === "number" ? step2.cash_to_deploy : amount;
      const snapshot = {
        ...base,
        session_key: sessionKey,
        // Mirror context so dedupeDecisionLogsForDisplay / DecisionHistoryEntry
        // can read entered/deploy/reserve amounts uniformly across legacy + v3 logs.
        decision_context: {
          entered_capital_amount: amount,
          deploy_now_amount: deployNowAmount,
          reserve_amount: Math.max(0, amount - deployNowAmount),
          session_key: sessionKey,
          timestamp: new Date().toISOString(),
        },
      };
      // Only update an existing log if it belongs to the CURRENT sessionKey.
      // A stale savedLog from a prior plan/amount/session must not be patched
      // with new-session actual_decisions — that would overwrite the wrong log.
      const candidate = savedLog ?? matchingLog;
      const target = shouldUpdateExistingLog(candidate, sessionKey) ? candidate : null;
      if (target) {
        const updated = await updateLog.mutateAsync({ id: target.id, patch: { actual_decisions: actualDecisions, notes } });
        setSavedLog(updated);
      } else {
        const created = await createLog.mutateAsync({ snapshot, actualDecisions, notes, source: "deploy_v3" });
        setSavedLog(created);
      }
      setSaveSuccess(true);
    } catch {
      setErrorMessage("Failed to save decision log. Please try again.");
    }
  }

  async function onEvaluatePerformance(logId: string) {
    try {
      const evaluated = await evaluateLog.mutateAsync(logId);
      if (savedLog?.id === logId) setSavedLog(evaluated);
    } catch {
      // Evaluate errors are non-blocking for the journal UX.
    }
  }

  // Decision log history (visible across all states except setup_incomplete)
  const historyLogs = useMemo(() => dedupeDecisionLogsForDisplay(recentLogs ?? []).slice(0, 10), [recentLogs]);
  // Only treat a log as the "active" one when it belongs to the current sessionKey.
  const activeLog = shouldUpdateExistingLog(savedLog ?? matchingLog, sessionKey)
    ? (savedLog ?? matchingLog)
    : null;

  // setup_incomplete: direct to diagnostics
  if (step2.state === "setup_incomplete") {
    return (
      <section id="step-3" className="card-glass p-4 border border-border/80 space-y-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">Execution journal</p>
        <p className="text-xs text-text-muted">Complete setup in Setup &amp; diagnostics below before logging.</p>
      </section>
    );
  }

  const isPending = createLog.isPending || updateLog.isPending;
  const isLogged = !!activeLog;
  const totalActual = actualDecisions.reduce((s, r) => s + (Number(r.actual_amount) || 0), 0);

  return (
    <div className="space-y-4">
      <section id="step-3" className="card-glass p-4 border border-border/80 space-y-4">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">Execution journal — log what you did</p>
          {isLogged && (
            <span className="text-[10px] px-2 py-0.5 rounded-pill border border-action-buy/30 bg-action-buy/10 text-action-buy font-semibold uppercase tracking-wide">
              Logged
            </span>
          )}
        </div>

        <p className="text-xs text-text-secondary leading-snug">
          These are Intel v3 planning recommendations, not broker-executed trades. Review and adjust below before
          logging what you actually did.
        </p>

        {step2.state === "no_moves" ? (
          <p className="text-xs text-text-muted">
            No model moves to log right now — your portfolio already matches your targets. You can still add a manual
            entry below if you acted on your own.
          </p>
        ) : (
          <p className="text-xs text-text-muted">
            Defaults match the Step 2 recommendation. Edit amounts, change status, or add a manual row before saving.{" "}
            {step2.amount_aware
              ? <span className="text-text-secondary">Sized for {formatCurrency(amount)} planning capital (not broker-verified cash).</span>
              : <span className="text-text-secondary">Step 1 amount ({formatCurrency(amount)}) is your context only.</span>
            }
          </p>
        )}

        {/* Editable row list */}
        {actualDecisions.length > 0 && (
          <div className="space-y-1.5">
            <div className="hidden sm:grid grid-cols-12 gap-2 px-1 text-[10px] uppercase tracking-wide text-text-muted font-semibold">
              <div className="col-span-2">Ticker</div>
              <div className="col-span-2">Recommended</div>
              <div className="col-span-2 text-right">Rec. $</div>
              <div className="col-span-2">Status</div>
              <div className="col-span-3 text-right">Actual $</div>
              <div className="col-span-1" />
            </div>
            {actualDecisions.map((row, i) => {
              const manual = isManualDecisionRow(row);
              return (
                <div
                  key={`${row.ticker ?? "row"}-${i}`}
                  data-testid={`v3-actual-row-${i}`}
                  className={cn(
                    "grid grid-cols-12 gap-2 items-center text-xs px-1 py-1 rounded",
                    manual ? "bg-amber-500/5 border border-amber-400/20" : "",
                  )}
                >
                  <div className="col-span-2 font-mono font-bold text-text-primary flex items-center gap-1">
                    <span>{row.ticker || "—"}</span>
                    {manual && (
                      <span
                        data-testid={`v3-manual-badge-${i}`}
                        className="text-[9px] px-1 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-400/30 font-semibold uppercase tracking-wide"
                      >
                        Manual
                      </span>
                    )}
                  </div>
                  <div className="col-span-2 text-text-secondary">
                    {row.recommended_action ?? <span className="text-text-muted italic">user-added</span>}
                  </div>
                  <div className="col-span-2 font-mono text-text-muted text-right">
                    {typeof row.recommended_amount === "number" && row.recommended_amount > 0
                      ? formatCurrency(row.recommended_amount)
                      : "—"}
                  </div>
                  <div className="col-span-2">
                    <select
                      aria-label={`Actual status for ${row.ticker ?? "row"}`}
                      data-testid={`v3-actual-action-${i}`}
                      value={row.actual_action ?? "BOUGHT"}
                      onChange={(e) => updateRow(i, { actual_action: e.target.value })}
                      className="w-full bg-surface border border-border/80 rounded text-text-primary text-xs px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-accent"
                    >
                      {V3_ACTUAL_STATUS_OPTIONS.map((opt) => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  </div>
                  <div className="col-span-3 text-right">
                    <input
                      type="number"
                      aria-label={`Actual amount for ${row.ticker ?? "row"}`}
                      data-testid={`v3-actual-amount-${i}`}
                      value={row.actual_amount ?? 0}
                      onChange={(e) => updateRow(i, { actual_amount: Number(e.target.value) || 0 })}
                      min={0}
                      step={1}
                      className="w-full bg-surface border border-border/80 rounded text-text-primary text-xs px-2 py-1 text-right font-mono focus:outline-none focus:ring-1 focus:ring-accent"
                    />
                  </div>
                  <div className="col-span-1 flex justify-end">
                    {manual && (
                      <button
                        type="button"
                        aria-label={`Remove manual row ${row.ticker ?? ""}`}
                        onClick={() => removeRow(i)}
                        className="text-[11px] text-text-muted hover:text-red-300"
                      >
                        ×
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
            <p className="text-[10px] text-text-muted text-right">
              Total logged: <span className="font-mono text-text-primary">{formatCurrency(totalActual)}</span>
            </p>
          </div>
        )}

        {/* Manual add row */}
        <details className="border border-border/70 rounded-md">
          <summary className="px-3 py-2 text-[11px] uppercase tracking-wide font-semibold text-text-muted cursor-pointer select-none">
            Add a manual action (e.g. NVDA BUY $100)
          </summary>
          <div className="p-3 grid grid-cols-12 gap-2 items-center text-xs border-t border-border/60">
            <input
              data-testid="v3-manual-ticker"
              placeholder="Ticker"
              value={manualDraft.ticker}
              onChange={(e) => setManualDraft((d) => ({ ...d, ticker: e.target.value.toUpperCase() }))}
              className="col-span-3 bg-surface border border-border/80 rounded text-text-primary text-xs px-2 py-1 font-mono focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <select
              data-testid="v3-manual-action"
              value={manualDraft.action}
              onChange={(e) => setManualDraft((d) => ({ ...d, action: e.target.value as "BUY" | "TRIM" | "SELL" }))}
              className="col-span-2 bg-surface border border-border/80 rounded text-text-primary text-xs px-2 py-1 focus:outline-none focus:ring-1 focus:ring-accent"
            >
              <option value="BUY">BUY</option>
              <option value="TRIM">TRIM</option>
              <option value="SELL">SELL</option>
            </select>
            <input
              type="number"
              data-testid="v3-manual-amount"
              placeholder="Amount"
              value={manualDraft.amount}
              onChange={(e) => setManualDraft((d) => ({ ...d, amount: e.target.value }))}
              min={0}
              step={1}
              className="col-span-2 bg-surface border border-border/80 rounded text-text-primary text-xs px-2 py-1 text-right font-mono focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <input
              data-testid="v3-manual-note"
              placeholder="Note (optional)"
              value={manualDraft.note}
              onChange={(e) => setManualDraft((d) => ({ ...d, note: e.target.value }))}
              className="col-span-3 bg-surface border border-border/80 rounded text-text-primary text-xs px-2 py-1 focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <button
              type="button"
              data-testid="v3-manual-add"
              onClick={addManualRow}
              className="col-span-2 px-2 py-1 rounded bg-accent text-background text-xs font-semibold hover:bg-accent-hover transition-colors"
            >
              Add row
            </button>
            <p className="col-span-12 text-[10px] text-text-muted">
              Manual entries are clearly labelled and never claim a model recommendation.
            </p>
          </div>
        </details>

        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Notes (optional)"
          rows={2}
          className="w-full bg-surface border border-border/80 rounded-lg text-text-primary text-xs px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-accent"
        />

        {errorMessage && <p className="text-xs text-red-400">{errorMessage}</p>}
        {saveSuccess && !errorMessage && (
          <p className="text-xs text-emerald-400">Decision logged.</p>
        )}

        <button
          onClick={onSave}
          disabled={isPending || actualDecisions.length === 0}
          className="px-4 py-2 text-xs font-semibold rounded-lg bg-accent text-background hover:bg-accent-hover transition-colors disabled:opacity-50"
        >
          {isPending ? "Saving…" : isLogged ? "Update log" : "Log this decision"}
        </button>

        <p className="text-[10px] text-text-muted">
          Intel v3 owns all Buy / Hold / Trim / Sell authority. This log records your execution journal — not a broker
          trade or a new recommendation.
        </p>
      </section>

      {historyLogs.length > 0 && (
        <section
          id="deploy-v3-decision-history"
          data-testid="v3-decision-history"
          className="card-glass p-4 space-y-3 border border-border/80"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
              Recent decision log history
            </p>
            <span className="text-[10px] text-text-muted">{historyLogs.length} shown</span>
          </div>
          <div className="space-y-2">
            {historyLogs.map((log) => (
              <DecisionHistoryEntry
                key={log.id}
                log={log}
                deployNow={
                  (log.recommendation_snapshot as { decision_context?: { deploy_now_amount?: number } } | undefined)
                    ?.decision_context?.deploy_now_amount ?? amount
                }
                isActive={activeLog?.id === log.id}
                onEvaluate={() => onEvaluatePerformance(log.id)}
                isEvaluating={evaluateLog.isPending}
                onSelect={() => {
                  setSavedLog(log);
                  setActualDecisions(
                    log.actual_decisions?.length
                      ? log.actual_decisions
                      : buildDeployV3InitialActualDecisions(step2.items),
                  );
                  setNotes(log.notes ?? "");
                }}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// ── Legacy deployment plan ────────────────────────────────────────────────────

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
  const deployNowAmount = getCanonicalDeployNow(plan, adaptive ?? null, plan.total_amount);
  const sortedByAmount = [...enrichedAllocs].sort((a, b) => (b.immediate_amount ?? b.amount ?? 0) - (a.immediate_amount ?? a.amount ?? 0));
  const primaryTickers = sortedByAmount
    .filter((rec, idx) => deriveRoleLabel(rec, idx, sortedByAmount.length) === "Primary")
    .slice(0, 2)
    .map(rec => rec.symbol ?? "")
    .filter(Boolean);

  return (
    <div className="space-y-4">
      <section className="card-glass p-4 space-y-4 border border-border/80 bg-gradient-to-b from-surface-elevated/20 to-transparent">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
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
  const immediate = getCanonicalDeployNow(plan, adaptive, plan.total_amount);
  const reserve = getCanonicalReserve(plan, adaptive, plan.total_amount);
  const regimeBadge = regime ? regimeBadgeMeta(regime.regime_label) : null;
  const modeBadge = adaptive ? modeBadgeMeta(adaptive.deployment_mode) : null;
  const subtitleParts: string[] = [`Across ${allocationCount} ticker${allocationCount === 1 ? "" : "s"}`];
  if (reserve > 0) subtitleParts.push(`Hold ${formatCurrency(reserve)} for pullbacks`);
  if (primaryTickers.length > 0) subtitleParts.push(`Prioritize ${primaryTickers.join(" & ")}`);
  const subtitle = subtitleParts.join(" • ");

  return (
    <div className="card-glass p-4 space-y-4 border border-accent/25 bg-gradient-to-br from-accent/10 via-surface/80 to-blue-500/5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <p className="text-[10px] uppercase tracking-[0.14em] font-semibold text-accent">
            Recommended Deployment
          </p>
          <p className="text-2xl sm:text-[1.8rem] font-display text-text-primary mt-1 leading-none">
            Deploy {formatCurrency(immediate)} now
          </p>
          <p className="text-xs text-text-secondary mt-1">{subtitle}</p>
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
        <div className="bg-surface-elevated/70 border border-border/70 rounded-md p-2.5">
          <p className="text-text-muted uppercase tracking-wide text-[10px]">Tickers</p>
          <p className="font-mono text-text-primary text-lg leading-tight">{allocationCount}</p>
        </div>
        <div className="bg-surface-elevated/70 border border-emerald-500/25 rounded-md p-2.5">
          <p className="text-text-muted uppercase tracking-wide text-[10px]">{hasAdaptive ? "Deploy now" : "Deployed"}</p>
          <p className="font-mono text-emerald-300 text-lg leading-tight font-semibold">
            {formatCurrency(hasAdaptive ? immediate : summary.total_deployed)}
          </p>
        </div>
        <div className="bg-surface-elevated/70 border border-blue-500/25 rounded-md p-2.5">
          <p className="text-text-muted uppercase tracking-wide text-[10px]">{hasAdaptive ? "Reserve" : "Considered"}</p>
          <p className="font-mono text-blue-300 text-lg leading-tight font-semibold">
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
  const canonicalAmounts = new Map(ranked.map((rec) => [rec.symbol ?? "", Math.max(0, rec.immediate_amount ?? rec.amount ?? 0)]));
  const displayRanked = [...ranked].sort(
    (a, b) => (canonicalAmounts.get(b.symbol ?? "") ?? 0) - (canonicalAmounts.get(a.symbol ?? "") ?? 0)
  );
  const allocatedNowTotal = displayRanked.reduce((sum, rec) => sum + (canonicalAmounts.get(rec.symbol ?? "") ?? 0), 0);
  const denominator = deployNowAmount > 0 ? deployNowAmount : allocatedNowTotal;
  return (
    <div className="card-glass overflow-hidden border border-border/80 bg-gradient-to-b from-surface-elevated/20 to-transparent">
      <div className="px-4 py-2.5 border-b border-border flex items-end justify-between gap-3">
        <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Allocation Breakdown
        </p>
        <p className="text-[11px] text-text-secondary mt-1">
          Deploy {formatCurrency(denominator)} now across {displayRanked.length} ticker{displayRanked.length === 1 ? "" : "s"}.
        </p>
        </div>
        <div className="text-right">
          <p className="text-[10px] uppercase tracking-wide text-text-muted">Deploy now total</p>
          <p className="font-mono text-sm sm:text-base text-text-primary font-semibold">{formatCurrency(allocatedNowTotal)}</p>
        </div>
      </div>
      <div className="divide-y divide-border">
        {/* Header */}
        <div className="hidden sm:grid grid-cols-12 gap-2 px-4 py-2 text-[10px] uppercase tracking-wide text-text-muted font-semibold bg-surface-elevated/40">
          <div className="col-span-6">Ticker</div>
          <div className="col-span-2">Role</div>
          <div className="col-span-2 text-right">Invest now</div>
          <div className="col-span-1 text-right">Now %</div>
          <div className="col-span-1 text-right">After %</div>
        </div>
        {displayRanked.map((rec) => {
          const role = roleMap.get(rec.symbol ?? "") ?? "Supporting";
          const roleClass =
            role === "Primary"
              ? "bg-emerald-500/10 text-emerald-300 border border-emerald-400/30"
              : role === "Watch"
                ? "bg-amber-500/10 text-amber-300 border border-amber-400/30"
                : "bg-blue-500/10 text-blue-300 border border-blue-400/25";
          const immediate = canonicalAmounts.get(rec.symbol ?? "") ?? 0;
          const why = deriveAllocationWhy(rec, role);
          const tickerSubtitle = why || toCompactLine(rec.staging_instruction || rec.execution_plan || "Buy first tranche now.", 10);
          return (
            <div key={rec.symbol} className="px-4 py-2.5 text-sm hover:bg-surface-elevated/20 transition-colors">
              <div className="grid grid-cols-12 gap-2 items-start">
                <div className="col-span-6">
                  <span className="font-mono font-bold text-text-primary">{rec.symbol}</span>
                  <p className="text-[11px] text-text-muted leading-snug mt-1">
                    {tickerSubtitle}
                  </p>
                </div>
                <div className="col-span-6 sm:col-span-2 flex items-start pt-0.5 sm:justify-start justify-end">
                  <span
                    className={cn(
                      "text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded-full",
                      roleClass
                    )}
                  >
                    {role}
                  </span>
                </div>
                                <div className="col-span-6 sm:col-span-2 text-right font-mono font-semibold text-emerald-300">
                  {formatCurrency(immediate)}
                </div>
                <div className="col-span-3 sm:col-span-1 text-right font-mono text-xs text-text-muted">
                  {(rec.current_weight ?? rec.portfolio_weight ?? 0).toFixed(1)}%
                </div>
                <div className="col-span-3 sm:col-span-1 text-right font-mono text-xs text-blue-300 font-semibold">
                  {(rec.after_weight ?? 0).toFixed(1)}%
                </div>
              </div>
            </div>
          );
        })}
        <div className="px-4 py-2 bg-surface-elevated/30 flex items-center justify-between text-xs border-t border-border/70">
          <span className="text-text-muted uppercase tracking-wide font-semibold">Total deploying now</span>
          <span className="font-mono font-semibold text-emerald-300">{formatCurrency(allocatedNowTotal)}</span>
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

// ─── Step 3 + Decision History ────────────────────────────────────────────────

function executionStatusLabel(status: ExecutionStatus): string {
  switch (status) {
    case "fully_executed": return "Fully executed";
    case "partially_executed": return "Partially executed";
    case "modified": return "Modified";
    case "skipped": return "Skipped";
  }
}

function executionStatusCls(status: ExecutionStatus): string {
  switch (status) {
    case "fully_executed": return "bg-emerald-500/10 text-emerald-300 border-emerald-500/30";
    case "partially_executed": return "bg-yellow-500/10 text-yellow-300 border-yellow-500/30";
    case "modified": return "bg-blue-500/10 text-blue-300 border-blue-500/30";
    case "skipped": return "bg-surface-elevated text-text-muted border-border";
  }
}

function buildExecutionCopy(
  actualDeployed: number,
  aiDeployNow: number,
  totalDeposit: number,
  status: ExecutionStatus,
): string {
  const reserve = totalDeposit - actualDeployed;
  if (status === "skipped") {
    return `Skipped this deploy-now plan. ${formatCurrency(totalDeposit)} remains uninvested/reserved.`;
  }
  const pct = aiDeployNow > 0 ? Math.round((actualDeployed / aiDeployNow) * 100) : 0;
  const pctText = pct < 100 ? ` (${pct}% of deploy-now plan)` : "";
  return `Executed ${formatCurrency(actualDeployed)} of ${formatCurrency(aiDeployNow)} planned now${pctText}. Reserved ${formatCurrency(reserve)} from your ${formatCurrency(totalDeposit)} deposit.`;
}

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
  const [editorOpen, setEditorOpen] = useState(false);
  const createLog = useCreateDecisionMemoryLog();
  const updateLog = useUpdateDecisionMemoryLog();
  const { data: recentLogs } = useDecisionMemoryLogs(6, true);
  const evaluateLog = useEvaluateDecisionMemoryLog();
  const { data: insights } = useDecisionPerformanceInsights(true);
  const [savedLog, setSavedLog] = useState<DecisionMemoryLog | null>(null);
  const [saveMessage, setSaveMessage] = useState<string>("");
  const [notes, setNotes] = useState<string>("");
  const [actualDecisions, setActualDecisions] = useState<ActualDecisionItem[]>([]);
  const [errorMessage, setErrorMessage] = useState("");

  const deployNow = getCanonicalDeployNow(deployPlan.plan, adaptive, amount);
  const reserveAmount = getCanonicalReserve(deployPlan.plan, adaptive, amount);

  const rankedForLog = useMemo(
    () => [...recommendations].sort((a, b) => (b.immediate_amount ?? b.amount ?? 0) - (a.immediate_amount ?? a.amount ?? 0)),
    [recommendations],
  );
  const adjustedAmountsForLog = useMemo(
    () => new Map(rankedForLog.map((rec) => [rec.symbol ?? "", Math.max(0, rec.immediate_amount ?? rec.amount ?? 0)])),
    [rankedForLog],
  );
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
  const currentSessionKey = useMemo(
    () =>
      ((snapshot as { decision_context?: { session_key?: unknown } }).decision_context?.session_key as string | undefined) ?? null,
    [snapshot],
  );
  const logsToShow = useMemo(() => dedupeDecisionLogsForDisplay(recentLogs ?? []), [recentLogs]);
  const matchingRecentLog = useMemo(() => {
    if (!recentLogs?.length || !currentSessionKey) return null;
    return logsToShow.find((log) => getDecisionLogSessionKey(log) === currentSessionKey) ?? null;
  }, [currentSessionKey, logsToShow, recentLogs]);

  // Initialize actualDecisions from adjusted amounts (deploy-now, not full deposit)
  useEffect(() => {
    if (actualDecisions.length > 0) return;
    if (rankedForLog.length === 0) return;
    setActualDecisions(buildInitialActualDecisions(recommendations, adjustedAmountsForLog));
  }, [actualDecisions.length, adjustedAmountsForLog, rankedForLog.length, recommendations]);

  // Rehydrate from backend on load
  useEffect(() => {
    if (savedLog || !matchingRecentLog) return;
    setSavedLog(matchingRecentLog);
    setNotes(matchingRecentLog.notes ?? "");
    setActualDecisions(
      matchingRecentLog.actual_decisions?.length
        ? matchingRecentLog.actual_decisions
        : buildInitialActualDecisions(recommendations, adjustedAmountsForLog),
    );
  }, [adjustedAmountsForLog, matchingRecentLog, recommendations, savedLog]);

  const activeLog = savedLog;

  function updateDecision(index: number, patch: Partial<ActualDecisionItem>) {
    setActualDecisions((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  async function onSaveLog(overrideDecisions?: ActualDecisionItem[]) {
    const decisionData = overrideDecisions ?? actualDecisions;
    if (createLog.isPending || updateLog.isPending) return;
    try {
      setErrorMessage("");
      const targetLog = savedLog ?? matchingRecentLog;
      if (targetLog) {
        const updated = await updateLog.mutateAsync({ id: targetLog.id, patch: { actual_decisions: decisionData, notes } });
        setSavedLog(updated);
        setNotes(updated.notes ?? "");
        if (!overrideDecisions) {
          setActualDecisions(
            updated.actual_decisions?.length
              ? updated.actual_decisions
              : buildInitialActualDecisions(recommendations, adjustedAmountsForLog),
          );
        }
      } else {
        const created = await createLog.mutateAsync({ snapshot, actualDecisions: decisionData });
        setSavedLog(created);
        setNotes(created.notes ?? "");
        if (!overrideDecisions) {
          setActualDecisions(
            created.actual_decisions?.length
              ? created.actual_decisions
              : buildInitialActualDecisions(recommendations, adjustedAmountsForLog),
          );
        }
      }
      setSaveMessage("Decision log saved");
    } catch (error) {
      setSaveMessage("");
      setErrorMessage(error instanceof Error ? error.message : "Failed to save decision log.");
    }
  }

  async function onSkipPlan() {
    const skipped = actualDecisions.map((row) => ({
      ...row,
      actual_action: "SKIPPED",
      actual_amount: 0,
      executed_at: new Date().toISOString(),
    }));
    setActualDecisions(skipped);
    await onSaveLog(skipped);
  }

  async function onEvaluatePerformance(logId: string) {
    const evaluated = await evaluateLog.mutateAsync(logId);
    if (savedLog?.id === logId) setSavedLog(evaluated);
    setSaveMessage("Performance refreshed");
  }

  // Execution status derived from the saved log's actual decisions vs deploy-now amount
  const savedActuals = activeLog?.actual_decisions ?? [];
  const savedActualTotal = savedActuals.reduce((s, r) => s + (Number(r.actual_amount) || 0), 0);
  const savedStatus: ExecutionStatus | null = activeLog ? deriveExecutionStatus(savedActuals, deployNow) : null;

  // Pending (unsaved) total for display in the editor
  const pendingTotal = actualDecisions.reduce((s, r) => s + (Number(r.actual_amount) || 0), 0);

  const executeRows = tickerContext.filter((item) => item.ticker && item.amount > 0);

  const insightsConfidenceLabel =
    insights?.confidence === "low" ? "Early signal" : insights?.confidence === "medium" ? "Building history" : "Higher confidence";
  const winCount = insights ? Math.round((insights.summary.win_rate_vs_model ?? 0) * insights.eligible_logs) : 0;

  return (
    <div className="space-y-4">

      {/* ── Card A: Step 3 — Execute & Record ─────────────────────────────── */}
      <div className="card-glass p-4 space-y-4 border border-border/80">
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
            Step 3 — Execute &amp; Record
          </p>
          {activeLog ? (
            <span className={cn("text-[11px] px-2 py-0.5 rounded-full border font-semibold", executionStatusCls(savedStatus!))}>
              {executionStatusLabel(savedStatus!)}
            </span>
          ) : (
            <span className="text-[11px] px-2 py-0.5 rounded-full border text-text-muted border-border bg-surface-elevated/40">
              Not saved
            </span>
          )}
        </div>

        {/* AI plan summary */}
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className="bg-surface-elevated/60 border border-border/70 rounded-md p-2.5">
            <p className="text-[10px] uppercase tracking-wide text-text-muted">Deposit</p>
            <p className="font-mono text-text-primary font-semibold">{formatCurrency(amount)}</p>
          </div>
          <div className="bg-surface-elevated/60 border border-emerald-500/25 rounded-md p-2.5">
            <p className="text-[10px] uppercase tracking-wide text-text-muted">Invest now</p>
            <p className="font-mono text-emerald-300 font-semibold">{formatCurrency(deployNow)}</p>
          </div>
          <div className="bg-surface-elevated/60 border border-blue-500/20 rounded-md p-2.5">
            <p className="text-[10px] uppercase tracking-wide text-text-muted">Reserve</p>
            <p className="font-mono text-blue-300 font-semibold">{formatCurrency(reserveAmount)}</p>
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => {
              setActualDecisions(buildInitialActualDecisions(recommendations, adjustedAmountsForLog));
              setConfirmOpen(true);
            }}
            className="px-3 py-1.5 rounded-md text-xs font-semibold bg-accent text-background hover:bg-accent-hover transition-colors"
          >
            Use AI Plan
          </button>
          <button
            onClick={() => setEditorOpen(true)}
            className="px-3 py-1.5 rounded-md text-xs font-semibold border border-border bg-surface-elevated/40 text-text-primary hover:bg-surface-elevated transition-colors"
          >
            Modify Plan
          </button>
          <button
            onClick={onSkipPlan}
            disabled={createLog.isPending || updateLog.isPending}
            className="px-3 py-1.5 rounded-md text-xs font-semibold border border-border bg-surface-elevated/40 text-text-muted hover:text-text-primary transition-colors disabled:opacity-50"
          >
            Skip Plan
          </button>
        </div>

        {/* Execution copy — shown after save */}
        {activeLog && savedStatus && (
          <p className="text-xs text-text-secondary leading-snug">
            {buildExecutionCopy(savedActualTotal, deployNow, amount, savedStatus)}
          </p>
        )}

        {/* Editor */}
        {editorOpen && (
          <div className="space-y-3 border-t border-border pt-3">
            <div className="flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">Actual execution</p>
              <p className="text-[11px] text-text-muted">
                Total: <span className="font-mono text-text-primary">{formatCurrency(pendingTotal)}</span>
                {" "}of <span className="font-mono text-emerald-300">{formatCurrency(deployNow)}</span> planned
              </p>
            </div>
            <div className="space-y-2">
              {actualDecisions.map((row, idx) => (
                <div key={`${row.ticker || "row"}-${idx}`} className="grid grid-cols-12 gap-2 items-center text-xs">
                  <div className="col-span-2 font-mono text-text-primary">{row.ticker || "—"}</div>
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
                    placeholder="Alt ticker"
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
            <div className="flex gap-2">
              <button
                onClick={() => onSaveLog()}
                disabled={createLog.isPending || updateLog.isPending}
                className="px-3 py-1.5 rounded-md text-xs font-semibold bg-accent text-background disabled:opacity-60"
              >
                {createLog.isPending || updateLog.isPending ? "Saving…" : activeLog ? "Update log" : "Save decision log"}
              </button>
              <button
                onClick={() => setEditorOpen(false)}
                className="px-3 py-1.5 rounded-md text-xs font-semibold border border-border bg-surface-elevated/40 text-text-muted"
              >
                Close
              </button>
            </div>
          </div>
        )}

        {saveMessage && <p className="text-xs text-emerald-400">{saveMessage}</p>}
        {errorMessage && <p className="text-xs text-red-400">{errorMessage}</p>}
      </div>

      {/* ── Confirm modal (Use AI Plan) ─────────────────────────────────────── */}
      {confirmOpen && createPortal(
        <div className="fixed inset-0 z-[2000] bg-black/60 flex items-center justify-center p-4 pointer-events-auto">
          <div className="w-full max-w-md rounded-lg border border-border bg-surface p-4 space-y-3">
            <p className="text-sm font-semibold text-text-primary">Confirm — Use AI plan</p>
            <div className="text-xs space-y-1">
              <p className="text-text-secondary">
                Invest now: <span className="font-mono text-emerald-300 font-semibold">{formatCurrency(deployNow)}</span>
              </p>
              <p className="text-text-secondary">
                Reserve: <span className="font-mono text-blue-300 font-semibold">{formatCurrency(reserveAmount)}</span>
                {" "}from your <span className="font-mono">{formatCurrency(amount)}</span> deposit
              </p>
            </div>
            <div className="max-h-48 overflow-y-auto border border-border rounded-md divide-y divide-border">
              {executeRows.map((row) => (
                <div key={row.ticker} className="px-3 py-2 flex items-center justify-between text-xs">
                  <span className="font-mono text-text-primary">{row.ticker}</span>
                  <span className="font-mono text-emerald-300">{formatCurrency(row.amount)}</span>
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
                onClick={async () => {
                  setConfirmOpen(false);
                  setEditorOpen(false);
                  setErrorMessage("");
                  const executedDecisions = actualDecisions.map((row) => ({
                    ...row,
                    actual_action: row.actual_action ?? "BOUGHT",
                    actual_amount: row.actual_amount ?? row.recommended_amount ?? 0,
                    executed_at: row.executed_at ?? new Date().toISOString(),
                  }));
                  setActualDecisions(executedDecisions);
                  await onSaveLog(executedDecisions);
                }}
                className="px-3 py-1.5 rounded-md text-xs font-semibold bg-accent text-background"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}

      {/* ── Card B: Decision History ──────────────────────────────────────── */}
      {logsToShow.length > 0 && (
        <div className="card-glass p-4 space-y-3 border border-border/80">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
            Decision History
          </p>

          {/* Global insights summary */}
          {insights && insights.eligible_logs >= 3 && (
            <div className="rounded-md border border-border/70 bg-surface-elevated/30 p-2.5 space-y-1">
              <p className="text-[10px] uppercase tracking-wide font-semibold text-text-muted">
                {insightsConfidenceLabel} · {insights.eligible_logs} evaluated
              </p>
              <p className={cn("text-xs", (insights.summary.avg_delta ?? 0) >= 0 ? "text-emerald-300" : "text-red-300")}>
                Avg delta vs model: {(insights.summary.avg_delta ?? 0) >= 0 ? "+" : ""}{(insights.summary.avg_delta ?? 0).toFixed(2)}% · beat model {winCount}/{insights.eligible_logs}
              </p>
            </div>
          )}

          {/* Log entries */}
          <div className="space-y-2">
            {logsToShow.map((log) => (
              <DecisionHistoryEntry
                key={log.id}
                log={log}
                deployNow={deployNow}
                isActive={activeLog?.id === log.id}
                onEvaluate={() => onEvaluatePerformance(log.id)}
                isEvaluating={evaluateLog.isPending}
                onSelect={() => {
                  setSavedLog(log);
                  setActualDecisions(
                    log.actual_decisions?.length
                      ? log.actual_decisions
                      : buildInitialActualDecisions(recommendations, adjustedAmountsForLog),
                  );
                  setNotes(log.notes ?? "");
                  setSaveMessage("");
                }}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DecisionHistoryEntry({
  log,
  deployNow,
  isActive,
  onEvaluate,
  isEvaluating,
  onSelect,
}: {
  log: DecisionMemoryLog;
  deployNow: number;
  isActive: boolean;
  onEvaluate: () => void;
  isEvaluating: boolean;
  onSelect: () => void;
}) {
  const [open, setOpen] = useState(false);

  const snap = log.recommendation_snapshot as Record<string, unknown>;
  const ctx = (snap?.decision_context as Record<string, unknown> | undefined) ?? {};
  const totalDeposit = Number(ctx.entered_capital_amount) || 0;
  const aiDeployNow = Number(ctx.deploy_now_amount) || deployNow;
  const aiReserve = Number(ctx.reserve_amount) || 0;
  const actuals = log.actual_decisions ?? [];
  // Action-aware journal totals: separates BUY spend from TRIM/SELL activity
  // and never produces negative "reserve". Planned cash input prefers the
  // deploy_now_amount (Deploy v3 cash_to_deploy) over the entered amount so
  // amount-aware plans compare against the actual sleeve budget.
  const plannedCashInput = aiDeployNow > 0 ? aiDeployNow : totalDeposit;
  const journal = computeJournalTotals(actuals, plannedCashInput);
  const status = deriveExecutionStatus(actuals, aiDeployNow);

  const tickerActuals = actuals
    .filter((r) => r.ticker && (r.actual_amount ?? 0) > 0 && classifyActualAction(r.actual_action) !== "skipped")
    .map((r) => `${r.ticker} ${formatCurrency(r.actual_amount ?? 0)}`)
    .join(" · ");

  const perf = log.performance_snapshot;
  const perfStatus = perf?.status ?? null;
  const portfolio = perf?.portfolio;
  const perfDelta = portfolio?.delta ?? 0;
  const showPerf = perfStatus === "ready" || perfStatus === "partial_data";

  return (
    <div className={cn("border rounded-lg overflow-hidden", isActive ? "border-accent/40" : "border-border/80")}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left px-3 py-2.5 hover:bg-surface-elevated/20 transition-colors"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-text-muted">
            {new Date(log.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
          </span>
          <div className="flex items-center gap-1.5">
            {isActive && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent border border-accent/30 font-semibold">
                Active
              </span>
            )}
            <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full border font-semibold", executionStatusCls(status))}>
              {executionStatusLabel(status)}
            </span>
            <ChevronIcon className={cn("w-3.5 h-3.5 text-text-muted transition-transform", open && "rotate-180")} />
          </div>
        </div>
        <div className="flex gap-3 mt-1 text-[11px] text-text-muted flex-wrap">
          {totalDeposit > 0 && <span>Deposit {formatCurrency(totalDeposit)}</span>}
          <span data-testid="v3-history-buy-total">
            BUY spend <span className="text-text-primary font-semibold">{formatCurrency(journal.actualBuyTotal)}</span>
          </span>
          {journal.manualBuyTotal > 0 && (
            <span data-testid="v3-history-manual-buy-total" className="text-amber-300">
              incl. manual {formatCurrency(journal.manualBuyTotal)}
            </span>
          )}
          {journal.trimSellTotal > 0 && (
            <span data-testid="v3-history-trim-sell-total">
              Trim/Sell <span className="text-text-primary">{formatCurrency(journal.trimSellTotal)}</span>
            </span>
          )}
          {journal.overPlannedAmount > 0 ? (
            <span data-testid="v3-history-over-planned" className="text-amber-300">
              Over planned by {formatCurrency(journal.overPlannedAmount)}
            </span>
          ) : journal.unallocatedAmount > 0 ? (
            <span data-testid="v3-history-unallocated">
              Unallocated <span className="text-text-primary">{formatCurrency(journal.unallocatedAmount)}</span>
            </span>
          ) : null}
        </div>
        {tickerActuals && (
          <p className="text-[11px] text-text-secondary mt-1 truncate">{tickerActuals}</p>
        )}
      </button>

      {open && (
        <div className="border-t border-border px-3 pb-3 pt-2.5 space-y-2">
          {/* Ticker detail */}
          {actuals.filter((r) => r.ticker).length > 0 && (
            <div className="divide-y divide-border border border-border rounded-md overflow-hidden">
              {actuals.filter((r) => r.ticker).map((r, i) => {
                const kind = classifyActualAction(r.actual_action);
                const manual = isManualDecisionRow(r);
                const amountCls =
                  kind === "trim_sell" ? "text-amber-300"
                  : kind === "skipped" ? "text-text-muted"
                  : "text-text-muted";
                const actionCls =
                  r.actual_action === "SKIPPED" ? "text-red-300"
                  : r.actual_action === "REPLACED" ? "text-amber-300"
                  : kind === "trim_sell" ? "text-amber-300"
                  : kind === "skipped" ? "text-text-muted"
                  : "text-emerald-300";
                return (
                  <div key={`${r.ticker}-${i}`} className="flex items-center justify-between px-3 py-1.5 text-xs">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-text-primary">{r.ticker}</span>
                      {manual && (
                        <span
                          data-testid={`v3-history-manual-badge-${i}`}
                          className="text-[9px] px-1 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-400/30 font-semibold uppercase tracking-wide"
                        >
                          Manual
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 text-text-muted">
                      <span className={cn("text-[10px] px-1 rounded font-semibold", actionCls)}>{r.actual_action ?? "BOUGHT"}</span>
                      {(r.actual_amount ?? 0) > 0 && (
                        <span className={cn("font-mono", amountCls)}>{formatCurrency(r.actual_amount ?? 0)}</span>
                      )}
                      {r.replacement_ticker && (
                        <span className="text-amber-300">→ {r.replacement_ticker}</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Plan comparison */}
          {aiDeployNow > 0 && (
            <p className="text-[11px] text-text-muted">
              AI planned: {formatCurrency(aiDeployNow)} now · {formatCurrency(aiReserve)} reserve
            </p>
          )}

          {/* Performance */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <p className="text-[10px] uppercase tracking-wide font-semibold text-text-muted">Performance vs AI</p>
              <button
                onClick={onEvaluate}
                disabled={isEvaluating}
                className="px-2 py-0.5 rounded text-[10px] font-semibold bg-surface-elevated text-text-primary border border-border disabled:opacity-60"
              >
                {isEvaluating ? "…" : "Evaluate"}
              </button>
            </div>
            {!perf ? (
              <p className="text-[11px] text-text-muted">Not yet evaluated.</p>
            ) : perfStatus === "baseline_captured" ? (
              <p className="text-[11px] text-amber-300">Baseline captured — re-evaluate next trading day.</p>
            ) : perfStatus === "pending" || perfStatus === "insufficient_data" ? (
              <p className="text-[11px] text-text-muted">
                {perfStatus === "pending" ? "Pending data." : "Insufficient data yet."}
              </p>
            ) : (
              <>
                {showPerf && portfolio && (
                  <p className={cn("text-xs font-semibold", perfDelta >= 0 ? "text-emerald-300" : "text-red-300")}>
                    {portfolio.summary_text
                      ? portfolio.summary_text
                      : `Delta vs model: ${perfDelta >= 0 ? "+" : ""}${perfDelta.toFixed(2)}%`}
                  </p>
                )}
                {showPerf && portfolio && (
                  <p className="text-[11px] text-text-muted">
                    AI {(portfolio.recommended_return ?? 0).toFixed(2)}% · You {(portfolio.actual_return ?? 0).toFixed(2)}%
                  </p>
                )}
                {perf.windows && (
                  <div className="space-y-0.5">
                    {(["7d", "30d", "90d"] as const).map((w) => {
                      const win = perf.windows?.[w];
                      if (!win) return null;
                      if (win.status !== "ready") {
                        return <p key={w} className="text-[11px] text-text-muted">{w}: {win.status.replace("_", " ")}</p>;
                      }
                      return (
                        <p key={w} className="text-[11px] text-text-muted">
                          {w}: AI {(win.recommended_return_pct ?? 0).toFixed(2)}% · You {(win.actual_return_pct ?? 0).toFixed(2)}% · Δ {(win.delta_pct ?? 0).toFixed(2)}%
                        </p>
                      );
                    })}
                  </div>
                )}
                {perf.per_ticker?.length ? (
                  <div className="space-y-0.5">
                    {perf.per_ticker.map((row) => {
                      const label = row.recommended_ticker && row.actual_ticker && row.recommended_ticker !== row.actual_ticker
                        ? `${row.recommended_ticker}→${row.actual_ticker}`
                        : row.actual_ticker ?? row.recommended_ticker ?? row.ticker;
                      if (row.status === "missing_price") {
                        return <p key={row.ticker} className="text-[11px] text-text-muted">{label}: missing price</p>;
                      }
                      return (
                        <p key={row.ticker} className="text-[11px] text-text-muted">
                          {label}: AI {(row.recommended_return_pct ?? 0).toFixed(2)}% · You {(row.actual_return_pct ?? 0).toFixed(2)}% · Δ {(row.delta_pct ?? 0).toFixed(2)}%
                        </p>
                      );
                    })}
                  </div>
                ) : null}
                {perf.data_quality?.length ? (
                  <p className="text-[11px] text-amber-300">Some tickers excluded due to missing price data.</p>
                ) : null}
              </>
            )}
          </div>

          {/* Load into editor */}
          {!isActive && (
            <button
              onClick={onSelect}
              className="text-[11px] text-accent hover:text-accent-hover transition-colors font-semibold"
            >
              Load into editor
            </button>
          )}
          {log.notes && (
            <p className="text-[11px] text-text-muted italic">{log.notes}</p>
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
