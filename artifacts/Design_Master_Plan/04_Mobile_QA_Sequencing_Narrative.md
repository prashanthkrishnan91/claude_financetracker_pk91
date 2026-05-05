# Design Master Plan — Part 5
## Mobile, Accessibility, Anti-Patterns, Sequencing, QA, North-Star Narrative

---

## 16. Mobile and Responsive Design

The product is mobile-first by *behavior*, not just by viewport. The phone is where the user actually opens it before market open, on the train, in the kitchen. The phone version must be **as honest, as calm, and as decisive** as the desktop — never "lite."

### 16.1 Bottom navigation

Four primary destinations as a bottom tab bar (because thumb-reach matters):

```
┌──────────────────────────────────────────────┐
│                                                │
│              (page content)                    │
│                                                │
│                                                │
│  ┌──────────────────────────────────────────┐  │
│  │  Today   Intel   Deploy   Portfolio       │  │
│  │   ◊       ◐       ▲         ▤             │  │
│  └──────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

- **Today**, **Intel**, **Deploy**, **Portfolio** in the tab bar.
- **Radar** and **Journal** are accessible from Today's secondary rail and from `⌘K` (which on mobile becomes a pull-down search summoned by the title bar).
- No "More" hidden tab. If something's not in the four, it lives one tap inside Today.
- Active tab: 1 pt accent rule above the label, no pill.
- Tab bar uses glass surface (24 px blur, 80% opacity) with 1 pt top border.

### 16.2 Card stack with peek

On mobile, the Intel page becomes a **vertical card stack** with a 12 px peek of the next card always visible at the bottom. This communicates "more below" without requiring a scrollbar. Cards are full-width with 16 px horizontal padding.

### 16.3 Buy / Hold / Trim / Sell on phone

Action chip moves to the **top-right of the card** (thumb-reach). Tap to open the bottom-sheet detail drawer; the chip itself is informational, not a button — the action is opened via the card body, not the chip alone (avoids accidental drawer summon).

### 16.4 Evidence drawer on mobile

The evidence drawer becomes a **bottom sheet** that occupies up to 80% of viewport height. It supports a swipe-down to dismiss. The sheet has a 4 px handle at the top center. Source list is scrollable inside the sheet.

### 16.5 Deploy confirmation on mobile

Deploy's two-step (Review → Confirm) is preserved. The Review screen is full-screen, scrollable. The Confirm action requires a deliberate **swipe-to-confirm** affordance (a horizontal slider at the bottom: *"Slide to confirm Deploy of $1,240"*). Swipe is harder to accidentally trigger than a tap, and feels ceremonial without being gamified.

### 16.6 Avoiding chart clutter

On mobile:
- Charts collapse to **sparklines** by default.
- Full charts are summoned by an explicit "Open chart" affordance in the Holding detail.
- Y-axis labels are dropped; values shown only on hover/tap.
- Multi-series charts collapse to one primary series with a "+N more" summon.

### 16.7 Sticky "what to do today" mini bar

On Today, after the user scrolls past the Brief, a small sticky bar appears at the top: *"3 actions today · NVDA Buy, RIVN Trim, SCHD Add."* Tap to scroll back to Act Today, or tap a ticker to open Intel detail directly.

### 16.8 Keeping it addictive on phone (without gamifying)

Addiction here means *worth opening daily*, not *dopamine loops*. We achieve this by:
- Honest novelty: the Brief paragraph is genuinely different every morning.
- Small surprises in the data, never in the chrome: "Source freshness improved on 3 holdings."
- A morning vs. evening tonal difference: the Brief reads slightly differently after market close (recap mode) vs. before open (preview mode).
- A clear "you are caught up" state when there is nothing to act on: *"You are caught up. The next intelligence run is at 06:30 ET tomorrow."* This is *more* premium than a fake to-do list.

### 16.9 Mobile-specific anti-patterns

- No swipe-to-buy gestures (too easy, too consequential).
- No haptics on success states (we are not a game).
- No bottom-sheet auto-summon (sheets open only on tap).
- No portrait-only lock; landscape works for charts.
- No pull-to-refresh as the **only** way to update — refresh is a manual `Run Agents` affordance plus the scheduled run; pull-to-refresh is a *secondary* convenience on Today only.

---

## 17. Accessibility and Trust

A finance product is a trust product. Accessibility and trust language are not afterthoughts — they are the substrate.

### 17.1 Contrast

- All body text meets **WCAG AAA** (7:1) against its background in both modes.
- All UI text (labels, captions) meets **WCAG AA** (4.5:1) at minimum.
- Disabled states still meet 3:1, with a visible "disabled" affordance (struck-through label, not just dimming).
- Action chips use color *and* glyph *and* label — never color alone.

### 17.2 Colorblind-safe semantics

Every semantic distinction is carried by **at least two channels** — color and shape (glyph, fill style, position). The Buy/Hold/Trim/Sell glyphs (`▲`, `■`, `◆`, `▼`) are deliberately distinct shapes that read in monochrome. A user with deuteranopia can still distinguish Buy from Trim by glyph, position, and label.

### 17.3 Motion reduction

`prefers-reduced-motion: reduce` collapses all motion to static fades (160 ms, no translate, no scale). Skeletons stop breathing — they show a single quiet shimmer that fades after 1.4 s instead of looping. The Deploy ceremony's blueprint draw is replaced by an immediate stamped affirmation.

### 17.4 Readable financial explanations

- Every page has a primary copy block at body.lg (16 / 24) — the "reading copy" of the page. No primary copy below body.md.
- Sentences are kept under 24 words where possible. Multi-clause sentences are split.
- Jargon terms (e.g., "yield curve", "concentration", "drawdown") are **dotted-underlined** on first use, with a tooltip translating to plain English.

### 17.5 Beginner-friendly copy

- Action prose is written in second person, present tense: *"NVDA shows three fresh sources supporting a Buy thesis."*
- We never use "we" to mean the system as a personality. The system is referred to as "the system" or "the intelligence layer," never "I" or "we."
- We never use "alpha", "beta", "Sharpe", "drawdown", "Sortino" in primary copy. Backend-defined metrics that have those names render as plain English: "Risk-adjusted return", "downside swing", etc.
- The `Explain to a beginner` AI affordance exists everywhere a thesis exists.

### 17.6 Regulatory and trust caveats

This is a **personal** investing app — not a regulated product. But the user is one human, and their money is real. So:

- The Today Brief footer carries a single quiet caveat: *"This is your private intelligence layer. It is not investment advice. Sources may be wrong. Risks may be larger than displayed."*
- Each Deploy confirmation includes a final-step caveat: *"Confirming will record this decision in your Journal. The system can be wrong. Sources can be stale. Risks can be larger than displayed."*
- No language ever implies certainty. "Will," "guaranteed," "outperform," "beat the market" — banned.

### 17.7 No manipulative dark patterns

Banned from this product:
- Urgency theater: "Only 4 minutes left to deploy!"
- False scarcity: "Limited research slots remaining."
- Sticker shock framing: "Your portfolio could have been worth $X if..."
- Loss-aversion nudges: "Don't miss this opportunity."
- Social proof manipulation: "847 people are watching this stock right now."
- Auto-progression: confirmation flows that auto-advance after a timer.
- Pre-checked consent.
- Hidden cancel buttons.

### 17.8 No overconfident language

- The system **never** says "should" without a source. (Compare: *"You should buy NVDA"* vs. *"Three sources support a Buy on NVDA."*)
- The system **never** says "will." (Compare: *"NVDA will rise"* vs. *"NVDA's thesis is currently Strong."*)
- The system **never** says "always" or "never" about market behavior.
- Probabilities, when stated, are stated in deterministic ranges: *"In 12 of the last 15 similar setups, this outcome held."* — never *"There's an 80% chance this works."*

### 17.9 Keyboard navigation

- Every interactive element is reachable by `Tab`.
- `⌘K` opens the command bar.
- `[` and `]` navigate between cards in Intel.
- `?` opens a keyboard-shortcut sheet.
- `Esc` closes any drawer or sheet.
- Focus rings are 2 pt accent.lapis with 2 pt offset, always visible (never `outline: none`).

### 17.10 Screen reader

- Every action chip carries an `aria-label` that includes the action and the ticker: `aria-label="Buy candidate, NVDA, Strong conviction, 3 fresh sources"`.
- Every freshness dot carries an `aria-label`: `aria-label="Source 2 of 3, fresh, updated 2 hours ago"`.
- Composed prose carries an `aria-describedby` linking to the source drawer count.
- Charts carry an `aria-label` summarizing the trend in plain English.
- Live regions announce: new intelligence available, Deploy confirmation, source freshness changes (these are batched to avoid noise).

---

## 18. Anti-Patterns Banned

A strict list. If any future PR introduces any of these, the merge gate must reject.

### 18.1 Visual / aesthetic

- Confetti, animated streaks, "level up" badges.
- Neon glow on hover.
- Generic shadcn rounded-2xl card with `bg-zinc-900` and a green tag.
- Crypto-casino: gradients, glow, neon-on-black.
- Glassmorphism on cards (reserved for top nav, command bar, deploy confirmation only).
- Heavy drop shadows (multi-layer "neumorphic" trickery).
- Multi-color logos, rainbow charts, `viridis` palettes on serious finance data.
- Stock-photo backgrounds, illustrated mascots, animated mascot reactions.
- Round, illustrative iconography for capital allocation surfaces.
- Animated KPI tile counters everywhere.
- Pulsing dots that loop indefinitely.
- Skeleton loaders that look like bug bars rather than the final layout.

### 18.2 Informational / honesty

- Famous-investor cosplay: "What would Buffett do?", Munger quotes, Lynch invocations.
- Fake price targets.
- Fake confidence percentages (e.g., "82% confidence" when the underlying score is not deterministic).
- Hidden risk under "see more."
- Predictions phrased as guarantees.
- Performance theater: "You've beat the S&P by X%."
- Streak counters, awards, leaderboards.
- Made-up source claims.
- Auto-rounded numbers that hide precision drift.
- Tooltips for primary information.
- Empty `--` placeholders where "Data missing" is the truth.
- Exposing raw metric keys (`pe_ratio_ttm`, `intel_filter_bucket`) in UI.
- Conflicting label systems (e.g., showing "Watchlist" as both a Buy/Hold/Trim/Sell action and an auxiliary state simultaneously).
- Business Read panels in UI.
- Giant stock-report blocks.

### 18.3 Tone / copy

- Exclamation marks in primary copy.
- Emojis (anywhere outside the user's own free-text journal entries, if those even exist).
- Breathless adjectives: "amazing", "explosive", "huge", "incredible".
- "Pro tip!" / "Did you know?" framings.
- "Congrats!" on any monetary event.
- Second-person imperative phrased as marketing: "Don't miss this!"
- "I" or "we" referring to the system.
- "You should" without a source.

### 18.4 Interaction

- Floating chat bubble.
- Avatar with a name for the AI.
- Auto-progressing confirmation flows.
- Hidden cancel.
- Pre-checked consents.
- Pull-to-refresh as the only way to refresh data.
- Swipe-to-buy gestures.
- Haptic celebration on monetary events.
- Sound effects (other than an opt-in, very-quiet chime on Buy/Hold/Trim/Sell state change, off by default).

### 18.5 Architectural

- One PR that touches Intel + Deploy + Portfolio + Journal at once.
- A "complete redesign" PR.
- UI changes that touch backend logic.
- New component libraries imported "to support the redesign."
- New animation libraries beyond what's already available (Framer Motion if already in, otherwise CSS transitions).

---

## 19. Implementation Sequencing

This is a **proposed** PR sequence for after Intel v3 foundation is stable and the timing rule in `docs/ai/DESIGN_VISION.md` is satisfied. Each PR is small, scoped, reversible. No giant redesign PR.

For each PR: scope, model, risk, UI budget, why this order, how to avoid breaking Intel/Deploy.

| # | Scope | Model | Risk | UI budget (max files) | Why this order | Don't break |
|---|---|---|---|---|---|---|
| 1 | **Token + primitive pass.** Wire the visual identity tokens (colors, spacing, radii, elevation, motion tokens) into `tailwind.config.ts` and `globals.css`. No surface changes yet. | Codex | Low | 2 (tailwind + globals) | Foundation. Every later PR depends on these tokens. Cheap, mechanical, reversible. | Existing class names; do not rename existing tokens, only add new ones. |
| 2 | **Typography pass.** Introduce the two type families (display serif + body sans) via CSS variables; apply across global heading and body styles. | Codex | Low | 3 | Typography sets the editorial spine; everything later inherits. | Existing component class names; opt-in via new utility classes. |
| 3 | **Action Card primitive.** Redesign the `AgentInsightCard` to the spec in §15.1 — same data, new visuals. No backend change. | Sonnet | Medium | 2 (card + its test) | Action Card is the most-rendered surface in Intel; refactoring it once unlocks Intel polish. | Backend recommendation pipeline; preserve `normalizeDisplayAction` contract. |
| 4 | **Source-Backed Claim primitive + Source drawer.** New components, new drawer; wire to existing source data structure. | Sonnet | Medium | 3 | Source UX is the moral spine; it must exist before further pages depend on it. | The source data contract; this PR consumes only existing fields. |
| 5 | **Today (Command Center) above the fold.** Implement Brief, Act Today, Risk Pulse, Deploy Ready — all consuming existing data. | Sonnet | Medium-High (budget review) | 5 | Today is the most-opened surface; high-leverage polish. | Deploy logic untouched; Brief composition uses backend-supplied facts. |
| 6 | **Intel detail drawer.** Implement the right-side drawer per §8.6, replacing inline expansion. | Sonnet | Medium | 4 | Drawer pattern unlocks every other detail surface. | The posture / `posture_reason` contract; consume read-only. |
| 7 | **Portfolio holdings ledger polish.** Convert the holdings table to the editorial spec in §10.4. | Sonnet | Medium | 4 | Portfolio is the second-most-opened surface; polish after Intel and Today. | Holdings data contract; no new fields. |
| 8 | **Deploy clarity polish.** Apply visual spec to the Deploy ledger (§9.3). Logic untouched. | Sonnet | Medium-High (budget review) | 4 | Deploy is the most consequential confirmation; polish carefully, after drawer + source patterns proven. | Deploy allocation math; absolutely no logic touch. |
| 9 | **Radar (new page).** Discovery feed + Opportunity Card + workflow states. Data contract may need a small backend addition (workflow state enum). | Sonnet | High (split if backend needed) | 5 | Radar is net-new; lands later because it depends on prior primitives. | Intel display contract — Radar uses **workflow states**, never Buy/Hold/Trim/Sell labels. |
| 10 | **Journal (Decision History).** Timeline view, entry anatomy, lessons surface. | Sonnet | Medium | 4 | Net-new view; consumes existing decision log. | Decision log determinism; no LLM authorship of Confirmed/Wrong outcomes. |
| 11 | **Motion system pass.** Apply motion tokens to entries, transitions, drawers. Reduced-motion respected. | Sonnet | Medium | 3 | Motion is layered last so prior PRs are validated static-first. | Performance; motion must not regress paint times. |
| 12 | **Mobile pass.** Bottom tab bar, card stack, bottom-sheet drawers, swipe-to-confirm. | Sonnet | Medium-High (budget review) | 5 | Mobile is layered after desktop polish so the design is canonical first. | Existing routes; mobile is layout-only. |
| 13 | **AI ambient layer (`⌘K` + chips).** Implement command bar + inline action chips with the AI contract from §14.1. | Sonnet | Medium | 4 | AI sits on top; lands when source UX is solid so AI can label its citations. | The AI contract — **never** invents numbers; LLM prompts must be source-bounded. |
| 14 | **Empty / loading / error pass.** Replace generic states with the "Quiet states" / "Composing intelligence" spec. | Codex | Low | 4 | Polish; lands late because each surface has its own empty state. | Existing fallback behaviors; no logic change. |
| 15 | **Iconography swap.** Apply the bespoke top-level intent icons + heavier action icons. | Codex | Low | 3 | Final aesthetic detail. | Accessibility; ensure aria-labels remain. |

After each PR: **Codex cheap visual merge gate** per `docs/ai/skills/ui_fix.md`.

After any Medium-High or High PR: **stop the Sonnet session**; bring the PR back to ChatGPT/Codex for review.

If at any point a PR scope grows beyond its `Max files` cap, split.

### 19.1 Why not one giant redesign PR

- A giant redesign PR is unreviewable.
- It will conflict with any in-flight Intel/Deploy work.
- It will hide regressions in mass diff.
- It will exhaust Sonnet usage in one shot.
- It contradicts the project's `docs/ai/PROMPT_LIBRARY.md` UI budget gate.

### 19.2 Pre-conditions

Before PR #1 starts:
- Intel v3 recommendation engine is stable (no churn in `recommendation_engine.py` for at least 2 weeks).
- Deploy v2 is stable (no churn in allocation math).
- Decision log persistence is stable.
- The timing rule in `docs/ai/DESIGN_VISION.md` is satisfied.
- The user has reviewed and approved this bible (this artifact).

---

## 20. Design QA Checklist

Every UI PR before merge must pass this checklist. The merge gate (Codex per `docs/ai/skills/ui_fix.md`) reads this list against the diff.

### 20.1 Visual quality

- [ ] All colors come from the token palette — no raw hex.
- [ ] All spacing comes from the 4-pt scale — no off-grid values.
- [ ] All radii come from the 5 defined tokens.
- [ ] All elevation comes from the 4 defined tokens.
- [ ] Glass/blur used only on top nav, command bar, or Deploy confirmation.
- [ ] No drop shadows beyond `elev.0–elev.3`.
- [ ] Charts use semantic color only, no rainbow.
- [ ] Sparklines have no axis or labels.
- [ ] Tabular numerals on every numeric column.

### 20.2 Data correctness

- [ ] Every claim is source-backed or marked "Data missing."
- [ ] Every number has a unit and a timestamp accessible within one click.
- [ ] No `--` placeholders.
- [ ] No interpolated/guessed values.
- [ ] No rounding that hides precision drift on monetary values.

### 20.3 Source correctness

- [ ] Inline superscripts cap at 3 or compress (`¹⁻⁵`).
- [ ] Every superscript opens the source drawer to that source.
- [ ] Source drawer shows snippet, freshness, credibility, contradictions.
- [ ] Stale sources persist with grey dot, never disappear.

### 20.4 No raw metric keys

- [ ] No `pe_ratio_ttm`, `_compute_insight_cards`, `intel_filter_bucket`, etc., visible in UI.
- [ ] Backend names translated to plain English.

### 20.5 No conflicting labels

- [ ] Buy / Hold / Trim / Sell are the only visible action verdicts.
- [ ] Watchlist is rendered as auxiliary state, visually distinct.
- [ ] Radar uses workflow states (Surfaced / Researching / Promoted / Dismissed), not BHTS.
- [ ] Posture buckets do not appear on cards or filters.

### 20.6 No broken Deploy logic

- [ ] No changes to `recommendation_engine.py` allocation math.
- [ ] No changes to reserve trigger conditions.
- [ ] Deploy logic is consumed read-only by the UI.

### 20.7 Responsive behavior

- [ ] Mobile bottom nav has 4 entries; no hidden "More."
- [ ] Drawers become bottom sheets on mobile.
- [ ] Confirm flows use swipe-to-confirm on mobile.
- [ ] Charts collapse to sparklines.
- [ ] No horizontal scroll on any default page width ≥ 320 px.

### 20.8 Accessibility

- [ ] Body text meets WCAG AAA (7:1).
- [ ] UI text meets WCAG AA (4.5:1).
- [ ] Every action chip has aria-label including action, ticker, conviction.
- [ ] Every freshness dot has aria-label.
- [ ] Charts have aria-label summarizing trend.
- [ ] `prefers-reduced-motion: reduce` collapses all motion.
- [ ] Focus rings visible on every interactive element.
- [ ] Keyboard navigation reachable for every action.

### 20.9 Loading / error states

- [ ] No spinners.
- [ ] Skeletons mimic final content shape.
- [ ] Errors specific, apologetic, actionable.
- [ ] Long-running ops show "Composing intelligence · N of M."

### 20.10 Plain-English copy

- [ ] No "alpha", "Sharpe", "drawdown" in primary copy.
- [ ] Sentences ≤ 24 words where possible.
- [ ] Jargon dotted-underlined with tooltip translation.
- [ ] No exclamation marks in primary copy.
- [ ] No emojis in UI.
- [ ] No "you should" without a source.
- [ ] No "will" / "guaranteed" / "outperform" language.

### 20.11 Dark / light mode

- [ ] All tokens defined in both modes.
- [ ] Semantic colors readable in both modes.
- [ ] No hard-coded `#fff` or `#000` in components.

### 20.12 AI labeling

- [ ] All AI-composed prose carries the "Composed" mark.
- [ ] All AI-composed prose lists source ordinals used.
- [ ] No AI invents numbers, scores, allocations, sources, or verdicts.

### 20.13 Anti-pattern absence

- [ ] No confetti, streaks, leaderboards, badges.
- [ ] No floating chat bubble.
- [ ] No avatars.
- [ ] No fake price targets.
- [ ] No fake confidence percentages.
- [ ] No famous-investor cosplay.
- [ ] No hidden risk.
- [ ] No urgency theater.

---

## 21. Final North-Star Narrative

> **Tuesday morning. 6:42 ET. Pre-open.**
>
> I open the app on my phone. Glass top bar; the brand mark sits on the left, a small letterform initial drawn once for this session. The data-health dot is quiet green. The clock and the build label are tucked in the corner — calm, never decorative.
>
> The page is titled simply **THE BRIEF**, in the display serif. Below it, three editorial sentences:
>
> *Three thesis updates since yesterday's close. One acute risk on RIVN. The portfolio is up 0.4% in pre-market on broad strength. Cash to deploy: $1,240. The next intelligence run completes at 07:00 ET.*
>
> The shimmer on the first word is the gentle "Composed" mark. Below the prose, in caption: *Composed · 7 sources used.*
>
> An engraved divider rule, then two columns.
>
> On the left, **Act Today**. Three rows. NVDA — Buy, three sources, fresh. SCHD — Add to position. RIVN — Trim, risk Elevated. Each row is one tap into Intel detail.
>
> On the right, **Risk Pulse**. RIVN at Acute. BTC and KLAR at Elevated. The acute row sits at the top with a thin oxblood left rule — the only oxblood on the page.
>
> I tap RIVN. The right edge of my screen slides up a bottom sheet — the Intel detail drawer, mobile form. Header: ticker, action chip (Trim, saffron), confidence ring three-quarters filled.
>
> *Why this view?* — *"Six fresh sources have shifted from Watch to Stale on Q1 delivery numbers. The remaining fresh source notes a softening order book."*
>
> *Risk challenge*: a plum left rule. *"If we are wrong, this is how it breaks: the order book recovers on the consumer EV cycle, which the data does not yet support."*
>
> *What changed*: a small chip — *"Conviction moved from Working to Strong since 5 May."* I tap it; a tiny diff sheet shows the previous thesis next to the current one.
>
> I tap **`[ Ask why ]`**. The drawer extends with a composed paragraph, the Composed mark on its first word, three source ordinals listed below. No personality. No avatar. Just the system, explaining itself.
>
> I close the drawer. Back on Today, I scroll to **New since you were away**. Three thesis updates. One new Radar candidate.
>
> I open Radar. A vertical feed of candidate cards on `bg.surface.elevated` with thin plum left borders. The new candidate is a small specialty chip designer I had never heard of. *Why now*: *"Three Tier-2 sources cite a contract with a Tier-1 cloud provider; the closest comparable holding in your portfolio is NVDA at 18.4%."* I tap **Compare vs. current holdings**. A side-by-side sheet. A delta column. Honest.
>
> I tap **Add to research**. The card's workflow chip moves from Surfaced to Researching. No celebration. No confetti. Just a quiet, decisive state change.
>
> I switch to **Deploy**. $1,240 deployable. The page reads as a single-column ledger.
>
> NVDA — Buy — $620 — 50% — *"Three fresh sources support thesis. Conviction Strong."*
>
> SCHD — Buy — $400 — 32% — *"DCA target. Yield discipline preserved."*
>
> Reserve — $220 — 18% — *"Held for opportunity. Trigger: any Acute risk drops to Elevated."*
>
> I tap **Review**. A full-screen memo. I tap-and-hold the swipe-to-confirm slider at the bottom: *"Slide to confirm Deploy of $1,240."* The slider eases under my thumb; at the end, the ceremonial 600 ms motion plays — the blueprint hairlines draw from each Intel verdict to its allocation row, a soft stamp appears top-right. The page settles. A new entry appears in my **Journal**.
>
> *Pending — evaluation window 47 days.*
>
> I close the app. The whole interaction took 94 seconds.
>
> I have read what changed, understood what mattered, seen the evidence, considered the risk, made one deliberate decision, and recorded it for the future me to learn from. The system was there as a quiet, source-anchored co-pilot. It did not entertain me. It did not pretend to know more than it did. It did not invent numbers. It did not show me what other people were doing.
>
> It looked beautiful, because every pixel had a purpose.
>
> It was useful, because every claim had a source.
>
> It was trusted, because uncertainty was visible and honest.
>
> It was mine, because the journal will remember what I did and what came of it — better than I will.
>
> **This is The Quiet Atelier.**

---

*End of Design Master Plan.*
