import { buildRecommendationSnapshotWithContext } from './decision-log';

describe('decision log session key', () => {
  const plan: any = {
    recommendations: [
      { symbol: 'AAPL', action: 'BUY', amount: 100, why: 'x' },
      { symbol: 'MSFT', action: 'BUY', amount: 50, why: 'y' },
    ],
    plan: { total_amount: 150, recommended_deploy_amount: 150 },
  };

  it('creates deterministic session key for the same recommendation context', () => {
    const a = buildRecommendationSnapshotWithContext(plan, {
      entered_capital_amount: 200,
      deploy_now_amount: 150,
      reserve_amount: 50,
      ticker_context: [
        { ticker: 'AAPL', amount: 100, role: 'core', why_reason: null },
        { ticker: 'MSFT', amount: 50, role: 'growth', why_reason: null },
      ],
    });
    const b = buildRecommendationSnapshotWithContext(plan, {
      entered_capital_amount: 200,
      deploy_now_amount: 150,
      reserve_amount: 50,
      ticker_context: [
        { ticker: 'MSFT', amount: 50, role: 'growth', why_reason: null },
        { ticker: 'AAPL', amount: 100, role: 'core', why_reason: null },
      ],
    });

    expect((a as any).decision_context.session_key).toEqual((b as any).decision_context.session_key);
  });
});
