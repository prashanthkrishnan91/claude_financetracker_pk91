# AI Handoff — Investing App

## Last change
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
