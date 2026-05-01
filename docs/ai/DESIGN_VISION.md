# Aspirational Design Vision — Investing Intelligence App

This document captures the long-term design ambition. It is not an instruction to start broad UI work now.

## Timing rule

Do not start a major design transformation until the product workflows are stable enough that visual work will not be repeatedly invalidated by feature churn.

Design-session timing is appropriate when:

- Intel, Deploy, decision logging, DRIP/analytics, and portfolio workflows are stable enough to preserve.
- No active blocking data, auth, persistence, or recommendation bugs are being triaged.
- Recent UI work has stayed stable for at least a few feature passes.
- The user explicitly asks for a design session or agrees that it is time.

Until then, apply only small UI fixes when they block usability or confidence.

## Product emotion

The app should feel cutting-edge, premium, personal, and worth opening daily. It should not feel like a generic fintech dashboard or spreadsheet wrapper.

For the Investing app, the target mood is:

- Private wealth cockpit, hedge-fund intelligence terminal, modern consumer finance app, calm confidence.
- Dense but not overwhelming; concise, signal-rich, and beautiful.
- Login should feel premium and secure, not plain.
- Deploy should make action feel clear and trustworthy.
- Intel should feel sharp and curated, not verbose or generic.

## Future design-session agenda

When the timing rule says design is ready, run a dedicated design session before implementation:

1. Establish 3-5 visual directions.
2. Compare palettes, typography, chart/data visualization style, motion language, card systems, and density.
3. Review inspiration sources such as high-end finance apps, market terminals, premium dashboards, Pinterest boards, Google Stitch outputs if available, and user-provided screenshots.
4. Let the user pick and mix: palette, type personality, chart style, motion level, dashboard density, and login tone.
5. Convert the chosen direction into a design brief before coding.
6. Implement in capped phases with UI budget gates.

Do not use live inspiration claims unless current web research or user-provided screenshots are available in the session.

## Candidate inspiration/integration sources

These are optional inputs for the future design session, not automatic dependencies:

- Pinterest mood boards supplied by the user.
- Google Stitch outputs supplied by the user.
- Premium finance dashboard references supplied by the user.
- Existing Claude personal skills: `frontend-design`, `ui-ux-pro-max`, `brainstorming` for design ideation only.
- Existing frontend capabilities: Tailwind, CSS variables, responsive component primitives.

## Candidate design capabilities to explore later

- Premium login page with secure, cinematic, low-noise motion.
- Market-aware dashboard atmosphere: subtle gradients, data glows, glass, and crisp cards.
- Dense-but-readable Intel cards with concise reasoning hierarchy.
- Deploy cockpit with strong action clarity and visual confidence.
- Better chart/card primitives for returns, allocation, dividends, and decision history.
- Premium empty/loading states instead of plain skeletons.
- Typography pairing with modern finance personality.
- Mobile-first polish so the app feels intentional on phone browser.

## Guardrails

- Do not run broad UI redesigns while feature workflows are still changing.
- Do not let visual work touch backend, API, Supabase, recommendations, allocation math, decision logging, or business logic unless explicitly scoped.
- Do not add heavy animation libraries beyond what is already available without budget approval.
- Preserve accessibility, readability, and performance.
- Avoid generic dark-mode SaaS styling.

## Implementation strategy when ready

Use this order:

1. Design discovery session — no code.
2. Design brief / style direction — no code.
3. Token and primitive pass — CSS variables, typography, shared primitives.
4. Login/auth page showcase.
5. One core page pass.
6. Component family pass.
7. Motion/animation pass.
8. Codex visual merge gate after each PR.

Each implementation phase must use `docs/ai/skills/ui_fix.md`, `docs/ai/UI_BASELINE.md`, and the UI budget gate in `docs/ai/PROMPT_LIBRARY.md`.
