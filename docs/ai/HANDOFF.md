# AI Handoff — Investing App

## Last change
Deploy tab regression QA + mobile execution visibility fix after PR #126.

## Files touched
- v2/frontend/src/app/dashboard/deposits/page.tsx

## QA scope completed
- Verified Deploy tab 3-step flow remains intact:
  - Step 1: deposit amount
  - Step 2: per-ticker allocation
  - Step 3: execution + decision log
- Verified allocation table still shows ticker, role, short WHY, invest-now amount, now %, after %.
- Verified no duplicate "after this trade" preview block remains outside table.
- Verified WATCH role allocations are capped via `computeAdjustedAmounts` and appear smaller when cap applies.
- Verified "Total deploying now" equals sum of per-ticker invest-now amounts.
- Verified decision-log snapshot still includes entered amount, deploy amount, reserve amount, ticker allocations, roles, reasons, timestamp context.
- Verified no Supabase SQL migration needed for this QA/fix pass.

## Behavior change
- Mobile/tablet view now shows each ticker's short WHY inline in the allocation row (`sm:hidden`) so critical execution context is visible without desktop columns.

## Known issues
- Build/test environment still requires proper Supabase env setup for full runtime validation.

## Next likely task
- Optional: add Playwright snapshot coverage for Deploy mobile layout to guard against future hidden-column regressions.

## Debug notes
- Allocation math unchanged; only presentation layer modified
- Supabase env errors affect local build validation but not logic correctness
