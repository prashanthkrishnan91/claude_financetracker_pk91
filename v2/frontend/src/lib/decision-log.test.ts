import { buildInitialActualDecisions, buildRecommendationSnapshotWithContext, dedupeDecisionLogsForDisplay, deriveExecutionStatus } from './decision-log';

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

describe('buildInitialActualDecisions with adjusted amounts', () => {
  const recommendations: any[] = [
    { symbol: 'AAPL', action: 'BUY', amount: 500 },
    { symbol: 'MSFT', action: 'BUY', amount: 400 },
  ];

  it('uses adjusted amounts when provided, not raw rec.amount', () => {
    // Scenario: $900 deposit, $725 deploy-now → AAPL $450, MSFT $275
    const adjustedAmounts = new Map([['AAPL', 450], ['MSFT', 275]]);
    const decisions = buildInitialActualDecisions(recommendations, adjustedAmounts);
    const total = decisions.reduce((s, d) => s + (d.actual_amount ?? 0), 0);
    expect(total).toBe(725); // sums to deploy-now, not full deposit ($900)
    expect(decisions[0].actual_amount).toBe(450);
    expect(decisions[1].actual_amount).toBe(275);
    expect(decisions[0].recommended_amount).toBe(450);
  });

  it('falls back to rec.amount when no adjusted amounts provided', () => {
    const decisions = buildInitialActualDecisions(recommendations);
    expect(decisions[0].actual_amount).toBe(500);
    expect(decisions[1].actual_amount).toBe(400);
  });
});

describe('deriveExecutionStatus — uses deploy-now denominator', () => {
  it('returns skipped when actual total is 0', () => {
    const decisions: any[] = [{ actual_amount: 0 }, { actual_amount: 0 }];
    expect(deriveExecutionStatus(decisions, 725)).toBe('skipped');
  });

  it('returns fully_executed when actual equals deploy-now amount', () => {
    // $725 actual vs $725 deploy-now = fully executed, NOT partial
    const decisions: any[] = [
      { actual_amount: 450, actual_action: 'BOUGHT' },
      { actual_amount: 275, actual_action: 'BOUGHT' },
    ];
    expect(deriveExecutionStatus(decisions, 725)).toBe('fully_executed');
  });

  it('does NOT use deposit amount as denominator — $725 actual vs $900 deposit is fully_executed not partial', () => {
    const decisions: any[] = [
      { actual_amount: 450, actual_action: 'BOUGHT' },
      { actual_amount: 275, actual_action: 'BOUGHT' },
    ];
    // If we wrongly used $900 deposit as denominator, this would be partially_executed
    expect(deriveExecutionStatus(decisions, 725)).toBe('fully_executed');
    expect(deriveExecutionStatus(decisions, 900)).toBe('partially_executed');
  });

  it('returns partially_executed when actual is less than deploy-now', () => {
    const decisions: any[] = [
      { actual_amount: 300, actual_action: 'BOUGHT' },
      { actual_amount: 200, actual_action: 'BOUGHT' },
    ];
    expect(deriveExecutionStatus(decisions, 725)).toBe('partially_executed');
  });

  it('returns modified when tickers have replacements', () => {
    const decisions: any[] = [
      { actual_amount: 450, actual_action: 'BOUGHT' },
      { actual_amount: 275, actual_action: 'REPLACED', replacement_ticker: 'GOOG' },
    ];
    expect(deriveExecutionStatus(decisions, 725)).toBe('modified');
  });

  it('returns modified when a ticker is skipped but amount is otherwise matched', () => {
    const decisions: any[] = [
      { actual_amount: 725, actual_action: 'BOUGHT' },
      { actual_amount: 0, actual_action: 'SKIPPED' },
    ];
    expect(deriveExecutionStatus(decisions, 725)).toBe('modified');
  });
});
