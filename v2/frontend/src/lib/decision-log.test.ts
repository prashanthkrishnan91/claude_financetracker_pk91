import { buildRecommendationSnapshotWithContext, dedupeDecisionLogsForDisplay } from './decision-log';

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

    expect((a as any).decision_context.recommendation_key).toEqual((b as any).decision_context.recommendation_key);
    expect((a as any).decision_context.session_key).toEqual((b as any).decision_context.session_key);
  });

  it('changes recommendation key when deposit amount changes', () => {
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
      entered_capital_amount: 900,
      deploy_now_amount: 715,
      reserve_amount: 185,
      ticker_context: [
        { ticker: 'AAPL', amount: 100, role: 'core', why_reason: null },
        { ticker: 'MSFT', amount: 50, role: 'growth', why_reason: null },
      ],
    });

    expect((a as any).decision_context.recommendation_key).not.toEqual((b as any).decision_context.recommendation_key);
  });
});


describe('decision log dedupe for display', () => {
  it('keeps only the latest log per session key', () => {
    const logs: any[] = [
      {
        id: '1',
        recommendation_snapshot: { decision_context: { session_key: 'same' } },
        created_at: '2026-04-20T10:00:00.000Z',
        updated_at: '2026-04-20T10:00:00.000Z',
      },
      {
        id: '2',
        recommendation_snapshot: { decision_context: { session_key: 'same' } },
        created_at: '2026-04-21T10:00:00.000Z',
        updated_at: '2026-04-22T10:00:00.000Z',
      },
      {
        id: '3',
        recommendation_snapshot: { decision_context: { session_key: 'other' } },
        created_at: '2026-04-23T10:00:00.000Z',
        updated_at: '2026-04-23T10:00:00.000Z',
      },
    ];

    const deduped = dedupeDecisionLogsForDisplay(logs as any);
    expect(deduped).toHaveLength(2);
    expect(deduped[0].id).toBe('3');
    expect(deduped[1].id).toBe('2');
  });
});
