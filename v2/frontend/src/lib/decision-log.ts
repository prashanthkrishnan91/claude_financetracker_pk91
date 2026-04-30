import type {
  ActualDecisionItem,
  AdaptiveBlock,
  DecisionLogPatch,
  DecisionMemoryLog,
  DepositPlanResult,
  DepositRecommendation,
} from "./api";
import { api } from "./api";

function toNum(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function normalizeTicker(rec: DepositRecommendation): Record<string, unknown> {
  return {
    ticker: rec.symbol,
    action: rec.action,
    amount: toNum(rec.amount) ?? 0,
    target_weight: toNum(rec.target_weight) ?? null,
    current_weight: toNum(rec.current_weight ?? rec.portfolio_weight) ?? null,
    after_weight: toNum(rec.after_weight) ?? null,
    role_label: null,
    rationale: rec.rationale ?? null,
    why_selected: rec.why ?? rec.why_selected ?? null,
    risk: rec.risk ?? null,
    what_to_do_now: rec.do ?? null,
  };
}

export function buildRecommendationSnapshot(plan: DepositPlanResult): Record<string, unknown> {
  const recommendations = plan.recommendations ?? plan.allocations ?? [];
  const normalized = recommendations.map(normalizeTicker);
  const adaptive = (plan.adaptive ?? null) as AdaptiveBlock | null;

  return {
    source: "deploy",
    created_at_client: new Date().toISOString(),
    raw_recommendation_payload: plan,
    funding: {
      deposit_amount: plan.funding?.deposit_amount ?? null,
      sale_proceeds: plan.funding?.sale_proceeds ?? null,
      total_cash: plan.funding?.total_cash ?? null,
    },
    deployment: {
      total_amount: plan.plan?.total_amount ?? null,
      recommended_deploy_amount: adaptive?.recommended_deploy_amount ?? plan.plan?.recommended_deploy_amount ?? null,
      cash_reserve: adaptive?.cash_reserve_amount ?? plan.plan?.cash_reserve ?? null,
      deploy_percentage: adaptive?.deploy_percentage ?? plan.plan?.deploy_percentage ?? null,
      deployment_mode: adaptive?.deployment_mode ?? plan.plan?.deployment_mode ?? null,
      strategy: plan.plan?.strategy ?? null,
      generated_at: plan.plan?.generated_at ?? null,
      staging_plan: recommendations.map((rec) => ({
        ticker: rec.symbol,
        immediate_amount: rec.immediate_amount ?? rec.amount,
        reserve_amount: rec.reserve_amount ?? null,
        execution_timing: rec.execution_timing ?? null,
        staging_instruction: rec.staging_instruction ?? null,
      })),
    },
    normalized_tickers: normalized,
    explanations: {
      why_this_plan: plan.explanation ?? plan.plan?.intel_summary ?? null,
      why_these_made_the_cut: recommendations.map((rec) => ({ ticker: rec.symbol, text: rec.why ?? rec.why_selected ?? null })),
      deployment_risks: plan.deployment_risks ?? [],
      what_to_do_now: recommendations.map((rec) => ({ ticker: rec.symbol, text: rec.do ?? null })),
      notes: plan.notes ?? [],
      warning: plan.warning ?? null,
    },
    adaptive_allocation: adaptive,
    regime: plan.regime ?? null,
  };
}

export function buildRecommendationSnapshotWithContext(
  plan: DepositPlanResult,
  context: {
    entered_capital_amount: number;
    deploy_now_amount: number;
    reserve_amount: number;
    ticker_context: Array<{ ticker: string; amount: number; role: string; why_reason: string | null }>;
  },
): Record<string, unknown> {
  const base = buildRecommendationSnapshot(plan);
  const tickerKey = context.ticker_context
    .map((item) => `${item.ticker}:${Math.round(item.amount * 100) / 100}`)
    .sort()
    .join("|");
  const recommendationKey = [
    `entered:${Math.round(context.entered_capital_amount * 100) / 100}`,
    `deploy:${Math.round(context.deploy_now_amount * 100) / 100}`,
    `reserve:${Math.round(context.reserve_amount * 100) / 100}`,
    `tickers:${tickerKey}`,
  ].join(";");
  return {
    ...base,
    decision_context: {
      entered_capital_amount: context.entered_capital_amount,
      deploy_now_amount: context.deploy_now_amount,
      reserve_amount: context.reserve_amount,
      ticker_allocations: context.ticker_context,
      role_and_why_summary: context.ticker_context.map((item) => ({
        ticker: item.ticker,
        role: item.role,
        why_reason: item.why_reason,
        amount: item.amount,
      })),
      recommendation_key: recommendationKey,
      session_key: recommendationKey,
      timestamp: new Date().toISOString(),
    },
  };
}

export const decisionLogApi = {
  createDecisionLog: (snapshot: Record<string, unknown>) =>
    api.decisionLogs.createDecisionLog(snapshot),
  listDecisionLogs: () => api.decisionLogs.listDecisionLogs(),
  getDecisionLog: (id: string) => api.decisionLogs.getDecisionLog(id),
  updateDecisionLog: (id: string, patch: DecisionLogPatch) => api.decisionLogs.updateDecisionLog(id, patch),
  deleteDecisionLog: (id: string) => api.decisionLogs.deleteDecisionLog(id),
};

export function buildInitialActualDecisions(recommendations: DepositRecommendation[]): ActualDecisionItem[] {
  return recommendations.map((rec) => ({
    ticker: rec.symbol,
    recommended_action: rec.action,
    actual_action: "BOUGHT",
    recommended_amount: rec.amount,
    actual_amount: rec.amount,
  }));
}

export type { DecisionMemoryLog };
