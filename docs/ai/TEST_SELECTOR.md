# Test Selector — Finance Tracker

Run the smallest sufficient suite, but if a change touches visible decisions, snapshot contracts, Data Truth, evidence adapters, SQL, or UI copy, include downstream consumer tests.

## Required mapping

| Changed area | Required checks |
|---|---|
| Intel v3 decision policy/kernel | deterministic policy tests, conviction/action diversity checks, visible Buy/Hold/Trim/Sell contract tests |
| Evidence adapters / Data Truth contracts | adapter tests, truth/suppression tests, downstream DecisionInputV3 tests |
| Research artifact store / workers | artifact writer/idempotency tests, safe_for_decision=false checks, forbidden payload/key tests |
| Snapshot endpoints | API route tests, source-of-truth tests, frontend client/query contract tests when shape changes |
| Frontend Intel v3 UI | plain-English rendering checks, no raw metric keys/diagnostics, action/filter rendering tests |
| Supabase SQL/migrations | migration sanity SQL, RLS/index/persistence checks, manual Supabase action notes |
| Runtime certification endpoints | protected endpoint tests, secret/flag behavior, no public exposure |
| Env/feature flag changes | default-off/default-safe tests, rollback behavior, Railway/Vercel manual action notes |
| docs-only changes | markdown/readability check; no runtime tests required unless docs changed generated behavior |

## Decision-policy rule
Decision-policy changes require deterministic policy tests plus snapshot/source-of-truth tests. If visible decisions can change, include tests proving Buy/Hold/Trim/Sell only and no legacy/posture labels.

## UI rule
UI changes require plain-English/no-raw-diagnostics checks. Advanced metrics may appear only if translated into user-friendly rationale.

## Adversarial test rule
For non-trivial PRs, add or identify one adversarial test for the riskiest invariant: fabricated evidence, authority drift, raw diagnostic leakage, SQL/manual-action miss, or visible snapshot mismatch.

## Skipping tests
If skipping a likely test, explain why it is not needed and what evidence covers the invariant instead.
