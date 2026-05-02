# UI Baseline — Investing App

Purpose: prevent future UI prompts from rediscovering the visual foundation.

## Current baseline

Latest foundation PR: #128 — Investing UI Foundation: elite intelligence design system pass.

Observed cost: ~23% of Claude session before merge, ~29% lifecycle. Treat 8-file UI foundation work as Medium-High usage.

## Visual direction

- High-end investing intelligence platform
- Bloomberg terminal density + Apple polish
- Dark graphite / black base
- High-contrast financial data readability
- Subtle blue / green / purple accents
- Sharp, professional, dense but readable
- No crypto-casino styling

## Known foundation files

Use these as baseline references before any future UI work:

- `v2/frontend/tailwind.config.ts`
- `v2/frontend/src/app/globals.css`
- navigation components
- holdings list components
- portfolio summary components
- insight card components
- empty state components

## Known limitations after foundation pass

- Some Deploy/Intel sub-surfaces may need page-specific polish.
- Data-dense mobile states should be validated separately.
- Future work should preserve Deploy allocation math and Intel reasoning behavior.

## Future UI prompt rules

- Do not rediscover the foundation.
- Reference PR #128 and this file as baseline.
- Use one page/component pass at a time unless UI budget explicitly approves more.
- Max 6 files for Sonnet UI implementation unless Code Committee approves.
- Use Codex cheap visual merge gate after UI PRs.
- Stop Sonnet chat after PR.
