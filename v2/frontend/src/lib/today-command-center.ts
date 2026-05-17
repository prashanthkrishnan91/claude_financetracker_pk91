/**
 * Today Command Center — deterministic composition helpers.
 * Pure functions only. No LLM. No fabricated claims.
 * Empty / unavailable states use honest plain-English copy.
 */

import type {
  IntelV3Snapshot,
  IntelV3HeldCard,
  DeployV3PlanResponse,
  AlertCandidate,
} from "@/lib/api";

// ── Exported types ────────────────────────────────────────────────────────────

export interface TodayBriefResult {
  sentences: string[];
  dataAvailable: boolean;
}

export interface ActTodayRow {
  ticker: string;
  name: string;
  action: "BUY" | "TRIM" | "SELL";
  conviction: string;
  evidenceBand: string;
  whyText: string;
  whyThisMatters: string | null;
}

export interface ActTodayResult {
  rows: ActTodayRow[];
  hasActionableItems: boolean;
  /** True when snapshot has holdings but none are BUY / TRIM / SELL. */
  allHold: boolean;
}

export interface RiskPulseRow {
  ticker: string;
  name: string;
  riskLevel: string;
  riskText: string;
}

export interface RiskPulseResult {
  rows: RiskPulseRow[];
  hasElevatedRisk: boolean;
}

export interface DeployReadyResult {
  planReadinessStatus: string;
  planReadinessLabel: string;
  buyCount: number;
  cashNote: string | null;
  hasData: boolean;
}

export interface WatchtowerSummaryResult {
  candidateCount: number;
  highSeverityCount: number;
  summaryLine: string;
  hasData: boolean;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const CONVICTION_SCORE: Record<string, number> = { HIGH: 3, MEDIUM: 2, LOW: 1 };
const EVIDENCE_SCORE: Record<string, number> = { STRONG: 3, PARTIAL: 2, THIN: 1 };
const ELEVATED_RISK_TIERS = new Set(["ELEVATED", "ACUTE"]);
const MAX_ACT_TODAY_ROWS = 5;
const DEPLOY_READINESS_LABELS: Record<string, string> = {
  no_items: "No actions pending",
  all_informational: "Informational only",
  all_suppressed: "All items suppressed",
  ready_pending_guardrails: "Ready — pending guardrail review",
  partially_ready: "Partially ready",
  blocked: "Plan blocked",
  not_ready: "Plan not ready",
};

// ── Internal helpers ──────────────────────────────────────────────────────────

function cardRankScore(card: IntelV3HeldCard): number {
  const conv = CONVICTION_SCORE[(card.conviction ?? "").toUpperCase()] ?? 0;
  const evid = EVIDENCE_SCORE[(card.evidence_band ?? "").toUpperCase()] ?? 0;
  return conv * 10 + evid;
}

function plural(count: number, singular: string, pluralForm: string): string {
  return count === 1 ? `${count} ${singular}` : `${count} ${pluralForm}`;
}

// ── The Brief ─────────────────────────────────────────────────────────────────

/**
 * Build 3–4 deterministic prose sentences for the Today brief.
 * Sources: Intel v3 snapshot action counts, what_changed array,
 * Deploy v3 plan readiness, and Watchtower alert candidate count.
 * No LLM. No fabricated claims.
 */
export function buildTheBrief(
  snapshot: IntelV3Snapshot | null | undefined,
  deployPlan: DeployV3PlanResponse | null | undefined,
  alertCandidates: AlertCandidate[] | null | undefined,
): TodayBriefResult {
  if (!snapshot) {
    return {
      sentences: [
        "Portfolio intelligence is not yet available.",
        "Visit the Intel tab and run an analysis to generate your first snapshot.",
      ],
      dataAvailable: false,
    };
  }

  const pcc = snapshot.portfolio_command_center;
  const buyCount = pcc.buy_count ?? 0;
  const trimCount = pcc.trim_count ?? 0;
  const sellCount = pcc.sell_count ?? 0;
  const holdCount = pcc.hold_count ?? 0;
  const total = pcc.total_holdings ?? 0;
  const actionCount = buyCount + trimCount + sellCount;

  // S1: portfolio action summary
  let s1: string;
  if (total === 0) {
    s1 = "No active holdings yet — add positions to get your first brief.";
  } else if (actionCount === 0) {
    s1 = `${plural(holdCount || total, "holding", "holdings")} reviewed — all on Hold, no new actions today.`;
  } else {
    const parts: string[] = [];
    if (buyCount > 0) parts.push(plural(buyCount, "Buy", "Buys"));
    if (trimCount > 0) parts.push(plural(trimCount, "Trim", "Trims"));
    if (sellCount > 0) parts.push(plural(sellCount, "Sell", "Sells"));
    s1 = `${plural(total, "holding", "holdings")} reviewed — ${parts.join(", ")} recommended today.`;
  }

  // S2: what changed since last run
  const changedItems = snapshot.what_changed ?? [];
  const s2 = changedItems.length > 0
    ? changedItems[0]
    : "No changes since the last intelligence run.";

  // S3: Deploy plan state
  let s3: string;
  if (!deployPlan) {
    s3 = "Deploy plan is not yet available — check the Deploy tab to set up targets.";
  } else {
    const planBuys = (deployPlan.items ?? []).filter(i => i.intel_action === "BUY").length;
    const status = deployPlan.rollup?.plan_readiness_status ?? "";
    if (planBuys > 0) {
      s3 = `Deploy has ${plural(planBuys, "Buy candidate", "Buy candidates")} — review the Deploy tab.`;
    } else if (
      status === "no_items" ||
      status === "all_suppressed" ||
      status === "all_informational"
    ) {
      s3 = "No Deploy actions are pending right now.";
    } else {
      s3 = "Deploy plan is available — check the Deploy tab for details.";
    }
  }

  // S4: Watchtower / alert candidates
  let s4: string;
  if (!alertCandidates) {
    s4 = "Watchtower data is loading.";
  } else if (alertCandidates.length === 0) {
    s4 = "No Watchtower alerts are currently waiting for review.";
  } else {
    const highCount = alertCandidates.filter(
      c => (c.severity ?? "").toUpperCase() === "HIGH"
    ).length;
    s4 =
      highCount > 0
        ? `Watchtower flagged ${plural(alertCandidates.length, "candidate", "candidates")}, including ${plural(highCount, "high-severity item", "high-severity items")} — check Alerts.`
        : `Watchtower has ${plural(alertCandidates.length, "active candidate", "active candidates")} — check the Alerts tab.`;
  }

  return { sentences: [s1, s2, s3, s4], dataAvailable: true };
}

// ── Act Today ─────────────────────────────────────────────────────────────────

/**
 * Return the top 3–5 actionable Buy / Trim / Sell rows from the Intel v3 snapshot,
 * sorted by conviction (HIGH > MEDIUM > LOW) then evidence band (STRONG > PARTIAL > THIN).
 */
export function buildActToday(
  snapshot: IntelV3Snapshot | null | undefined,
): ActTodayResult {
  if (!snapshot) {
    return { rows: [], hasActionableItems: false, allHold: false };
  }

  const buys = (snapshot.best_buys ?? []).filter(c => c.action === "BUY");
  const trimSells = (snapshot.trim_sell_desk ?? []).filter(
    c => c.action === "TRIM" || c.action === "SELL"
  );
  const all = [...buys, ...trimSells];

  if (all.length === 0) {
    const totalHoldings = snapshot.portfolio_command_center.total_holdings ?? 0;
    return { rows: [], hasActionableItems: false, allHold: totalHoldings > 0 };
  }

  const sorted = [...all].sort((a, b) => cardRankScore(b) - cardRankScore(a));
  const rows: ActTodayRow[] = sorted.slice(0, MAX_ACT_TODAY_ROWS).map(card => ({
    ticker: card.ticker,
    name: card.name,
    action: card.action as "BUY" | "TRIM" | "SELL",
    conviction: card.conviction,
    evidenceBand: card.evidence_band,
    whyText: card.why_text ?? "",
    whyThisMatters: buildWhyThisMatters(card),
  }));

  return { rows, hasActionableItems: true, allHold: false };
}

// ── Risk Pulse ────────────────────────────────────────────────────────────────

/**
 * Return tickers in Elevated or Acute risk tier from the Intel v3 snapshot.
 * Returns empty result when no elevated risk is present.
 */
export function buildRiskPulse(
  snapshot: IntelV3Snapshot | null | undefined,
): RiskPulseResult {
  if (!snapshot) {
    return { rows: [], hasElevatedRisk: false };
  }

  const elevated = (snapshot.current_holdings ?? []).filter(c =>
    ELEVATED_RISK_TIERS.has((c.risk_level ?? "").toUpperCase())
  );

  const rows: RiskPulseRow[] = elevated.map(card => ({
    ticker: card.ticker,
    name: card.name,
    riskLevel: card.risk_level,
    riskText: card.risk_text ?? "",
  }));

  return { rows, hasElevatedRisk: rows.length > 0 };
}

// ── Deploy Ready ──────────────────────────────────────────────────────────────

/**
 * Summarise the Deploy v3 plan readiness for the Today fold.
 * BUY count is the number of plan items with intel_action === "BUY".
 */
export function buildDeployReady(
  deployPlan: DeployV3PlanResponse | null | undefined,
): DeployReadyResult {
  if (!deployPlan) {
    return {
      planReadinessStatus: "unavailable",
      planReadinessLabel: "Deploy plan unavailable",
      buyCount: 0,
      cashNote: null,
      hasData: false,
    };
  }

  const status = deployPlan.rollup?.plan_readiness_status ?? "unknown";
  const buyCount = (deployPlan.items ?? []).filter(i => i.intel_action === "BUY").length;
  const planReadinessLabel = DEPLOY_READINESS_LABELS[status] ?? "Status unknown";

  let cashNote: string | null = null;
  if (
    deployPlan.source?.amount_aware &&
    deployPlan.source.cash_to_deploy != null &&
    deployPlan.source.cash_to_deploy > 0
  ) {
    cashNote = `$${deployPlan.source.cash_to_deploy.toLocaleString()} in planning capital.`;
  }

  return { planReadinessStatus: status, planReadinessLabel, buyCount, cashNote, hasData: true };
}

// ── Watchtower Summary ────────────────────────────────────────────────────────

/**
 * Summarise Watchtower alert candidates for the Today fold.
 * Returns hasData: false when the array is null/undefined (loading or error).
 */
export function buildWatchtowerSummary(
  alertCandidates: AlertCandidate[] | null | undefined,
): WatchtowerSummaryResult {
  if (!alertCandidates) {
    return {
      candidateCount: 0,
      highSeverityCount: 0,
      summaryLine: "Watchtower data is not yet available.",
      hasData: false,
    };
  }

  const candidateCount = alertCandidates.length;
  const highSeverityCount = alertCandidates.filter(
    c => (c.severity ?? "").toUpperCase() === "HIGH"
  ).length;

  let summaryLine: string;
  if (candidateCount === 0) {
    summaryLine = "No active alerts are currently waiting for review.";
  } else if (highSeverityCount > 0) {
    summaryLine = `${plural(candidateCount, "alert candidate", "alert candidates")}, including ${plural(highSeverityCount, "high-severity item", "high-severity items")}.`;
  } else {
    summaryLine = `${plural(candidateCount, "alert candidate", "alert candidates")} active.`;
  }

  return { candidateCount, highSeverityCount, summaryLine, hasData: true };
}

// ── Why This Matters ──────────────────────────────────────────────────────────

/**
 * Build a plain-English "Why this matters" note for an Act Today row.
 * Uses existing why_text, detail_drawer_payload.rationale, or action_text from the card.
 * Returns null if no informative content is available — never fabricates.
 */
export function buildWhyThisMatters(card: IntelV3HeldCard): string | null {
  if (card.why_text?.trim()) return card.why_text.trim();
  if (card.detail_drawer_payload?.rationale?.trim()) return card.detail_drawer_payload.rationale.trim();
  if (card.action_text?.trim()) return card.action_text.trim();
  return null;
}

// ── Today secondary rail (Stage 4H) ──────────────────────────────────────────

export interface SecondaryRailLink {
  href: string;
  label: string;
  category: "alerts" | "journal" | "radar";
}

/**
 * Static secondary rail links for the Today page.
 * Always present — Alerts is reachable regardless of alert candidate count.
 * Journal and Radar are desktop-SideNav destinations made reachable on mobile
 * via this rail per §30.8.
 */
export const TODAY_SECONDARY_RAIL_LINKS: readonly SecondaryRailLink[] = [
  { href: "/dashboard/alerts", label: "Watchtower", category: "alerts" },
  { href: "/dashboard/journal", label: "Journal",    category: "journal" },
  { href: "/dashboard/radar",   label: "Radar",      category: "radar"   },
] as const;

// ── Coming-Later slot caption ─────────────────────────────────────────────────

/**
 * The canonical Coming-Later caption for the "What I Learned Today" slot.
 * Stage 6E activates this slot with real pattern-detection output.
 */
export function buildLearningSlotCaption(): string {
  return "This daily lesson is being prepared. The next intelligence stage will surface it here.";
}

// ── Today mini-bar (Stage 4H) ─────────────────────────────────────────────────

export interface TodayMiniBarResult {
  show: boolean;
  primaryLabel: string;
  primaryHref: string;
  secondaryLabel: string | null;
  secondaryHref: string | null;
}

/**
 * Build the compact mobile Today mini-bar state from existing deterministic data.
 * Priority: Act Today actions > Deploy buy candidates > Watchtower alerts.
 * Returns show:false when no actionable signal exists.
 * Never invents intelligence — all inputs come from existing API responses.
 */
export function buildTodayMiniBar(
  actToday: ActTodayResult,
  deployReady: DeployReadyResult,
  watchtowerSummary: WatchtowerSummaryResult,
): TodayMiniBarResult {
  const none: TodayMiniBarResult = {
    show: false,
    primaryLabel: "",
    primaryHref: "",
    secondaryLabel: null,
    secondaryHref: null,
  };

  if (actToday.hasActionableItems) {
    const count = actToday.rows.length;
    const primaryLabel = `${count} action${count !== 1 ? "s" : ""} today — Intel`;
    const secondaryLabel =
      deployReady.hasData && deployReady.buyCount > 0
        ? `${deployReady.buyCount} Deploy candidate${deployReady.buyCount !== 1 ? "s" : ""}`
        : null;
    return {
      show: true,
      primaryLabel,
      primaryHref: "/dashboard/recommendations",
      secondaryLabel,
      secondaryHref: secondaryLabel ? "/dashboard/deposits" : null,
    };
  }

  if (deployReady.hasData && deployReady.buyCount > 0) {
    return {
      show: true,
      primaryLabel: `${deployReady.buyCount} Buy candidate${deployReady.buyCount !== 1 ? "s" : ""} — Deploy`,
      primaryHref: "/dashboard/deposits",
      secondaryLabel: null,
      secondaryHref: null,
    };
  }

  if (watchtowerSummary.candidateCount > 0) {
    return {
      show: true,
      primaryLabel: `${watchtowerSummary.candidateCount} Watchtower alert${watchtowerSummary.candidateCount !== 1 ? "s" : ""}`,
      primaryHref: "/dashboard/alerts",
      secondaryLabel: null,
      secondaryHref: null,
    };
  }

  return none;
}
