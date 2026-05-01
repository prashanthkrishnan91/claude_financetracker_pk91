# AI Handoff — Investing App

## Last change
Deploy Logic v2 PR 1: deterministic deployment-mode classifier, output schema, and focused backend tests (PR: "feat(deploy-v2-pr1): deterministic deployment-mode classifier").

## Files touched
- `v2/backend/app/services/deployment_engine.py` — **new module**. Pure deterministic deployment mode classifier. Emits `DeploymentDecision` with `deployment_mode ∈ {full_deploy, staged_deploy, defensive_reserve, skip_or_wait}`, `deploy_now_amount`, `reserve_amount`, `deployment_confidence`, `reserve_trigger` (required when reserve > $25), `per_ticker_allocations`, `risks`, `data_quality`, `evaluation_notes_for_future_decision_log`, `deployment_score`, `adjustments_applied`.
- `v2/backend/tests/test_deployment_engine.py` — **new test file**. 32 focused tests covering: full deploy (no reserve trigger), hard reserve trigger rule, cash drag penalty, concentration risk, WATCH-tier cap, deploy-now denominator correctness, no generic reserve text, data quality confidence, edge cases.
- `v2/frontend/src/lib/api.ts` — added `DeploymentModeV2`, `DeploymentDecisionV2`, `ReserveTriggerV2`, `PerTickerDeploymentV2`, `TickerRole` types. Old `DeploymentMode` type unchanged (backward compatible).
- `v2/progress_log.md` — concise entry added.

## QA scope completed
- `pytest -q v2/backend/tests/test_deployment_engine.py` passed (32 tests).
- `pytest -q v2/backend/tests/test_adaptive_deployment.py v2/backend/tests/test_allocation_engine.py` passed (49 tests — no regressions).
- No UI files changed.
- No API contracts changed (new types are additive).
- No Supabase SQL required.
- No LLM calls added or changed.
- `adaptive_deployment.py` and its tests untouched.

## Behavior change
- **New**: `classify_deployment()` function in `deployment_engine.py` implements deterministic scoring: BASE(70) + structural_bonus(0-15) + quality_bonus(0-10) + cash_drag_bonus(0-20) - concentration_penalty(0-20) - regime_penalty(0-25) - data_quality_penalty(0-15). Mode thresholds: full≥70, staged≥50, defensive≥30, skip<30.
- **Hard rule**: `reserve_amount > $25` requires a valid non-generic trigger; otherwise reserve forced to 0 and mode forced to `full_deploy`.
- **Cash drag**: when prelim unallocated reserve > $25 and no strong trigger, cd_bonus added proportional to reserve ratio. When cash == plan total (no unallocated excess), the hard trigger rule enforces this instead.
- **WATCH cap**: LOW conviction tickers capped at 25% of total plan amount.
- **Existing adaptive_deployment.py not changed** — it continues to be the production deployment engine. The new `deployment_engine.py` is a parallel module, ready to be wired in via a subsequent PR.

## Known issues / next steps
- `deployment_engine.py` is not yet wired into the allocation router (`app/routers/allocation.py`). Wiring is PR 2 scope.
- `adaptive_deployment.py` uses old mode labels (`full/partial/defensive/wait`) — these are kept for backward compatibility and will be migrated in PR 2.
- `npm install` not run in CI; frontend type check requires deployment environment.

## Debug notes
- Score constants centralized at top of `deployment_engine.py` (BASE_DEPLOYMENT_SCORE, FULL_DEPLOY_SCORE, etc.) — change them there only.
- `_generate_reserve_trigger` has 4 priority paths: near-cap → Watch-tier → risk-off → concentration. Returns `None` only when all 4 are inapplicable (very rare in practice).
- The cash drag bonus uses `prelim_reserve = max(0, cash - plan_total)` as the scale signal — this is unallocated excess cash, not the staged portion of the plan.

---

## Previous change
Deploy Step 3 + Decision History refactor: correct deploy-now amount semantics, execution status, copy, and UI split (PR: "refactor(deploy-step3): correct amount semantics + split Decision History card").

## Files touched
- `v2/frontend/src/app/dashboard/deposits/page.tsx` — Replaced `DecisionLogMemoryPanel` with two-card layout: Card A (Step 3 Execute & Record) and Card B (Decision History). Fixed `buildInitialActualDecisions` call site to pass `adjustedAmountsForLog` so "Use AI Plan" prefills actual rows from deploy-now amount not full deposit. Fixed execution copy to use deploy-now denominator. Added `executionStatusLabel`/`executionStatusCls`/`buildExecutionCopy` helpers. Added `DecisionHistoryEntry` component with expandable per-ticker actuals and performance windows.
- `v2/frontend/src/lib/decision-log.ts` — Fixed `buildInitialActualDecisions` signature to accept optional `adjustedAmounts: Map<string, number>`. Added `deriveExecutionStatus` (uses deploy-now denominator). Exported `ExecutionStatus` type.
- `v2/frontend/src/lib/decision-log.test.ts` — Added 7 new tests: adjusted-amount sums to deploy-now; fallback to rec.amount; skipped/fully_executed/partially_executed/modified status derivation; denominator correctness ($725 actual vs $900 deposit = fully_executed).
- `v2/progress_log.md` — Concise entry added.

## QA scope completed
- No node_modules in CI environment; `npm test` cannot run. Tests are authored and will run in deployment environment.
- No backend files touched.
- No API contracts changed.
- No recommendation/allocation algorithm changed.
- No Supabase SQL required.
- No LLM calls added or changed.

## Behavior change
- "Use AI Plan" now prefills rows summing to deploy-now amount (e.g. $725), not full deposit ($900).
- Execution status badge derives from actual vs deploy-now (not deposit).
- Execution copy: "Executed $X of $Y planned now. Reserved $Z from your $D deposit."
- Decision History is a separate card (Card B) below Step 3. Each entry shows date, status badge, deposit/invested/reserve, ticker actuals. Expand for performance vs AI (7d/30d/90d) and deviation detail.
- Performance/insights moved from Step 3 editor to Decision History expand section.

## Known issues
- `npm install` not run in CI; full build verification requires deployment environment.
- `tsc --noEmit` would show only pre-existing errors (missing node_modules types).

## Next likely task
- Playwright snapshot baseline update for the new two-card Step 3 layout.
- Optional: further polish of Decision History (e.g. filter by status, sort controls).

## Debug notes
- `deriveExecutionStatus` tolerance is $0.51 to handle floating-point allocation math.
- `buildInitialActualDecisions` is backward-compatible: without `adjustedAmounts` it uses `rec.amount` (old behavior) — safe for any callers that don't pass adjusted amounts.
- Rehydration `useEffect` guards on `savedLog` being null before applying `matchingRecentLog`, preventing overwrite of in-session edits.

---

## Previous change
Frontend UI foundation pass: elite intelligence design system (PR: "Investing UI Foundation: elite intelligence design system pass").

## Files touched
- `v2/frontend/tailwind.config.ts` — Extended token set: `accent-blue`, `accent-purple`, `positive`, `negative`, `caution`, `neutral`, `surface-hover`, `border-strong`, box-shadow tokens, `2xs` font size, `label`/`widest2` letter-spacing
- `v2/frontend/src/app/globals.css` — New component layer primitives: badge system (`badge`, `badge-positive`, `badge-negative`, `badge-caution`, `badge-info`, `badge-accent`, `badge-purple`, `badge-surface`), action badges (`action-badge-*`), data state colors (`data-positive/negative/caution/neutral`), table primitives (`data-table-header/row/footer`), button primitives (`btn-primary/secondary/ghost/danger`), typography helpers (`metric-label`, `section-header`, `data-value*`, `ticker-symbol`), page shell helpers (`page-header`, `page-main`), block helpers (`risk-block`, `info-block`), `intel-card`. Kept all existing classes. Font-feature-settings added to body.
- `v2/frontend/src/components/navigation/BottomNav.tsx` — Active indicator (top hairline on mobile, left border on desktop), platform name two-line treatment, tighter icon labels, v2.0 footer in SideNav
- `v2/frontend/src/components/holdings/HoldingsList.tsx` — Filter pills use `badge-surface` / accent pattern, holdings list uses `data-card` + `divide-y`, improved `ticker-symbol` / `metric-label` usage, `+` prefix on gains
- `v2/frontend/src/components/holdings/PortfolioSummaryCard.tsx` — SummaryPill uses `data-card`, `metric-label`, `text-positive/negative` data state. PriceHealthBadge uses new badge classes. Day change shows `+` prefix.
- `v2/frontend/src/components/cards/InsightCard.tsx` — `ACTION_STYLES` expanded with `badge` key using `action-badge-*` classes. Card uses `intel-card`. Footer pills use `badge-*` system. Rationale has left-border accent treatment.
- `v2/frontend/src/components/ui/EmptyState.tsx` — Horizontal rule divider, tighter spacing

## QA scope completed
- Build environment (no node_modules) prevents full `next build`; tsc run confirms all errors are pre-existing environment issues (missing React/Next type declarations), none introduced by this PR
- No backend files touched
- No API contracts changed
- No business logic changed
- No allocation math changed (Deploy tab)
- No Intel reasoning changed
- No decision logging changed
- No auth changed
- No routing changed
- No Supabase SQL required

## Behavior change
- Visual only. All existing flows, data, and calculations are identical.
- App uses new design tokens and shared component classes for consistency.

## Known issues
- `npm install` not run in CI environment, so full build verification requires deployment environment
- `tsc --noEmit` shows only pre-existing errors (missing node_modules types)

## Next likely task
- Page-specific polish using the new primitives (Deploy table rows, Intel page filter pills, DRIP page)
- Optional: apply `data-table-header/row` to AllocationBreakdownTable in deposits page
- Optional: Playwright snapshot baseline update

## Debug notes
- All new CSS classes are additive; no existing classes removed or renamed
- `card-glass` and `card-elevated` kept intact for backward compat
- `pnl-positive` / `pnl-negative` kept; `data-positive` / `data-negative` added as semantic aliases

## Last change
Decision Log Performance v1: windowed evaluation statuses + minimal Deploy memory surfacing.

## Files touched
- `v2/backend/app/services/decision_log_service.py` — Added window-level performance rollups (`7d`/`30d`/`90d`) with status model (`pending`, `ready`, `insufficient_data`, `unavailable`) and included them under `performance_snapshot.windows`.
- `v2/backend/tests/test_decision_performance.py` — Added tests for window status transitions and unavailable data handling while preserving existing baseline/missing-price checks.
- `v2/frontend/src/lib/api.ts` — Extended `DecisionMemoryLog.performance_snapshot` typing to include new status variants and `windows` structure.
- `v2/frontend/src/app/dashboard/deposits/page.tsx` — Minimal Step 3 UI update to display compact 7d/30d/90d results/status without redesigning Deploy.
- `v2/progress_log.md` — Added concise project progress note.

## QA scope completed
- `pytest -q v2/backend/tests/test_decision_performance.py` passed (6 tests).
- No recommendation/allocation algorithm changes.
- No Intel tab logic changes.
- No Supabase schema migration required for this patch (JSON snapshot extension only).

## Behavior change
- Decision logs now expose time-window evaluation state explicitly so frontend can show pending/unavailable instead of ambiguous or misleading 0% outputs.
- Deploy execution cockpit remains intact; update is additive and compact.

## Known limitations
- Window returns are based on available baseline-vs-current prices; no separate historical candles are fetched per window.
- If price points are missing, window status reports `unavailable` and does not fabricate return percentages.
