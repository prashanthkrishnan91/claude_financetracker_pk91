# Design Master Plan — Part 6
## S-Grade Design + Intelligence Execution Contract

> **What this document is.** This is the implementation-ready execution contract for the next phase of the Finance Tracker product. It extends — and reconciles with — `00_README_and_Verdict.md` through `04_Mobile_QA_Sequencing_Narrative.md`. The earlier files remain the design bible (vocabulary, principles, tokens, motion, page intent). This file is the *contract*: what gets built, in what order, with what backend guarantees, with what learning layer, and with what stop conditions.
>
> **Status.** Docs-only. No code, no SQL, no backend, no provider, no LLM, no email-delivery activation. Stage 3G (Alert Center UI v1) is the most recent merged surface. This contract assumes Stage 3G is the baseline and Stage 4A is the next implementation step.
>
> **Audience.** Future PRs (Stage 4A → 4H), reviewer agents, and the human reading the build queue six months from now.

---

## 22. Executive Verdict

### 22.1 What this product actually is

**Finance Tracker is a private investing intelligence atelier.**

It is a single-user product that fuses four things that nobody has fused well for an amateur investor:

1. **An S-grade backend intelligence layer** that produces deterministic, auditable, source-grounded Buy / Hold / Trim / Sell decisions.
2. **A finance research workforce** (workers and constrained agents) that produces structured, sourced research artifacts — fundamentals, technicals, filings, sentiment, capital allocation, valuation context, red-team challenges, sector and macro context — without ever owning a visible decision.
3. **A boutique editorial frontend** that translates that intelligence into a calm, addictive, beginner-friendly daily brief, decision room, evidence room, and learning loop.
4. **A learning surface** that turns every decision moment into a small, contextual lesson — so the user's investing literacy grows alongside the portfolio.

It is not a brokerage. It is not a robo-advisor. It is not a budgeting app. It is not an analyst terminal. It is not a chat wrapper. It is not a generic "AI investing dashboard."

It is a **private investment committee + daily mentor + boutique editorial product** for one human.

### 22.2 Why this beats every adjacent category

| Adjacent product | What they do well | What they get wrong | Why we win for this user |
|---|---|---|---|
| **Robinhood / Legend** | Speed, action clarity, chart immediacy. | Casino dopamine, one-tap pressure, expert clutter. | We keep speed and action clarity. We replace the casino with a private letter. The user is treated as a serious reader, not a player. |
| **Public / AI Agents** | Monitored workflows, explicit approval gates, activity visibility. | Autonomous brokerage execution, social cosplay. | We adopt monitored-workflow language. We never execute. The system proposes; the user confirms; the journal remembers. |
| **TradingView** | Breadth of market context, watchlists, alerts, screeners, serious research feel. | Overwhelming chart-first density, expert-first onboarding. | We keep the breadth and seriousness. We move density behind drawers and translate every advanced metric into plain English. |
| **M1** | Visual allocation, "where each dollar goes," target shapes. | Allocation theater without thesis depth. | We adopt the allocation clarity. We back every dollar with a source-grounded thesis and a beginner-readable rationale. |
| **Wealthfront / Betterment** | Calm automation, beginner confidence, plain-English risk. | Opaque paternalism, "we decided for you." | We keep the calm and the plain English. We never hide the working. Every recommendation has a source trail and a counter-thesis. |
| **Copilot / Monarch** | Beauty, habit loop, account-overview clarity, tactile motion. | Family-budget skew, no real investing intelligence. | We adopt the beauty and the habit loop. We attach a real investment committee underneath. |
| **OpenBB / Analyst Workspaces** | Multi-source structured + unstructured finance research, scenario thinking, private-data control. | IDE clutter, analyst-only ergonomics. | We adopt the multi-source rigor. We render it as editorial, not as code. |
| **AI-chat wrappers (ChatGPT for stocks, etc.)** | Conversational reach. | Inventing numbers, no source trail, no portfolio context. | We never let the AI invent. AI explains, challenges, compares — labeled — using only the system's own data. |

### 22.3 The non-negotiable front-end / back-end boundary

> **Backend intelligence owns truth. Frontend owns comprehension.**

- The deterministic backend policy (Intel v3 → Deploy v3 → Watchtower) owns final visible Buy / Hold / Trim / Sell authority, dollar allocations, risk tiers, confidence scores, source credibility, freshness classifications, and contradiction resolution.
- The frontend owns reading order, vocabulary translation, hierarchy of attention, learning context, and the daily editorial spine.
- The frontend may never invent numbers, scores, allocations, source claims, price targets, risk tiers, or verdicts.
- The backend may never invent reading order, copy, or learning context.
- LLMs / agents may produce sourced research artifacts and AI-composed prose. They may never own a visible decision, score, allocation, risk tier, or source claim.

This boundary is the spine of every section that follows. If a design choice ever blurs it, the design choice loses.

---

## 23. Updated North Star

**The Quiet Atelier remains.** It is extended — not replaced — into:

> **A private investing mentor that turns S-grade analysis into a daily brief, a decision room, an evidence room, and a learning loop.**

### 23.1 What this means in one paragraph

The app should feel like the calmest, smartest, most discreet personal investing committee you have ever had — one that prepared a one-page brief for you overnight, can show you every source it read, can challenge its own conclusions, can plan the next dollar with discipline, will tell you honestly when it does not know enough, and will quietly teach you something every day so that in twelve months you understand investing in a way that no app in your life has bothered to teach you.

### 23.2 What this product is deliberately not

- Not a generic fintech dashboard.
- Not a brokerage trading screen.
- Not a stock-report PDF rendered as web.
- Not a chatbot wrapper around market data.
- Not a `shadcn` card grid reskinned with green text.
- Not a robo-advisor that hides the math.
- Not a social investing feed.
- Not a "famous-investor" cosplay UI.
- Not a notification spam machine.

### 23.3 The five questions the product must answer on every major screen

Every Today, Intel, Deploy, Portfolio, Alert, Radar, Journal screen must answer, in order:

1. **What changed?**
2. **Does it matter?**
3. **What should I do?**
4. **Why does the system think that?**
5. **What did I learn?**

A surface that cannot answer any one of these is incomplete.

### 23.4 The 30-second rule

Within 30 seconds of opening the app, an amateur investor should understand:

- What changed in the portfolio.
- What matters now.
- What can be ignored.
- What action, if any, is justified.
- Why the system thinks that.
- What evidence supports it.
- What risk or missing data weakens it.
- How Deploy should handle the next deposit or cash position.
- One investing concept they learned from the current situation.

This rule is testable. It is the success condition for Stage 4B Today and the implicit success condition for every other stage that follows.

---

## 24. Intelligence-to-UI Translation Contract

This is the heart of the contract: how backend intelligence becomes beginner-readable UI without leaking jargon, without losing rigor, and without letting the frontend invent.

### 24.1 Translation principles

- **Every advanced metric becomes a plain-English concept.** Raw metric keys never appear in UI.
- **Every translated concept carries its source path.** A user who taps "growth quality" gets the source trail behind it.
- **Every translation carries freshness.** A growth-quality claim built on six-month-old filings reads differently from one built on a fresh transcript.
- **Translations are never opinion.** The backend produces the score / band / verdict; the frontend names it in plain English.
- **Translations name uncertainty.** "Working," "Tentative," "Not enough evidence yet" are first-class UI states, not error states.

### 24.2 The translation table

| Backend intelligence domain | What the backend produces | What the UI shows (plain English) | What is forbidden in UI |
|---|---|---|---|
| **Fundamental analysis** | Quality scores, growth rates, margin trends, cash conversion, balance-sheet health, returns on capital, valuation bands. | "Business quality," "How fast it grows," "How efficiently it makes money," "How disciplined it is with cash," "What price the market is asking." | `pe_ratio_ttm`, `roe`, `fcf_yield`, `gross_margin_qoq_delta`, factor names. |
| **Technical analysis** | Price levels, momentum signals, volatility regimes, support / resistance, relative strength, drawdown stats. | "How the price is behaving," "Whether momentum is with or against the position," "How calm or jumpy the stock has been," "Levels the market has cared about." | Indicator names (RSI, MACD, Bollinger), raw moving-average labels, lookback-period numerics. |
| **Sentiment / news** | Source-classified news events, sentiment direction, narrative shift, freshness band, source credibility tier. | "What changed in the market story," "Whose story is changing — and how recent." Includes source name, date, and a calm caveat where the source is weak. | Sentiment scores as percentages, "buzz" graphs, follower counts, anonymous social posts. |
| **SEC filings & company public info** | Filing-derived risk items, strategic disclosures, management commentary deltas, capital-allocation actions, competitive-position notes. | "What management is saying about the business," "What the company is doing with its capital," "What risks they themselves named," "Where they say they're winning or losing." | Raw filing IDs, footnote numbers, accounting jargon, XBRL keys. |
| **Analyst / research artifacts** | Sourced analyst notes with credibility tier, agreement / disagreement state, freshness band. | "What outside analysts are saying," "Where they agree," "Where they disagree," "How fresh and credible those views are." Never as authority — always as evidence. | Buy / Hold / Sell ratings shown as authority. Aggregated "Street consensus" presented as truth. Price targets presented as forecasts. |
| **Portfolio context** | Position size, weight, concentration, sector / theme exposure, cash position, thesis fit, risk tier. | "How big this position is in your portfolio," "How concentrated you are in this theme," "Whether this is a starter, core, or oversize position," "How this fits the thesis." | Raw weight percentages without context. "Risk score: 7.2." Beta numbers in primary copy. |
| **Watchtower** | Evidence-freshness shifts, threshold crossings, contradiction emergence, source freshness drops. | "What changed enough to matter since the last brief." Plain-English event log with source links. | Generic "alert" badges. Counts without context. Push-spam framing. |
| **Deploy** | Exact-dollar allocation plan, sleeve sizing, cash discipline, target-shape gaps, sizing-source readiness. | "Where the next dollar should go and why," "How much should stay in reserve and what would unlock it," "What this allocation does to the portfolio shape." | Sliders that imply user override authority. "What if" simulators that fabricate projections. |
| **Journal** | Decision history, action taken, evaluation-window state, outcome state, lesson patterns. | "What you decided, what you learned, what the system was right or wrong about." | Performance theater. "You beat the market by X%." Streaks, leaderboards. |
| **Source room** | Per-claim source registry with credibility, freshness, contradiction tags. | "Who said this, how recent, how credible, who disagrees." | URL strings as primary content. Unattributed claims. |
| **Data health** | Per-source freshness, provider error state, certification state, missing-data inventory. | "What the system knows, what is fresh, what is stale, what is missing — and what it would take to fix it." | Internal log lines. Stack traces. Error codes. |

### 24.3 Required UI primitives that enforce translation

The following primitives are reserved and must exist before any page-level S-grade redesign lands:

1. **Plain-English Concept Pill.** Renders a translated concept (e.g., "Business quality: Strong") with a dotted underline that summons the concept capsule (see §25). Carries a source-trail link.
2. **Source-Backed Claim.** Inline sentence with hairline superscripts that open the Source Room drawer.
3. **Confidence Ring.** 5-step ladder. Never a percentage above 90 unless the score is deterministic.
4. **Risk Glyph.** 4-tier ladder. Color + glyph + label, never color alone.
5. **Freshness Dot.** Green / amber / grey + label.
6. **Composed Mark.** AI-composed prose label with source ordinals.
7. **Data-Missing Pill.** Honest grey pill. Names what was attempted and when.
8. **Contradiction Strip.** Plum rule between disagreeing sources with one-line summary.
9. **Evidence-Weak Caution Panel.** Calm caution, never alarm.

These primitives are defined in `03_Sources_AI_Components.md` (§13–§15). Stage 4A formalizes them as tokens / components.

### 24.4 What is forbidden in UI

- Raw metric keys (`pe_ratio_ttm`, `roe_3y`, `posture_reason`, `intel_filter_bucket`, `agent_run_id`, `worker_certified`, `evidence_band`, `analyst_verdict_synthesis_v1`).
- Posture bucket labels (`Add Candidate`, `Risk Watch`, `Trim Candidate`, etc.) as primary action labels.
- Worker / certification internal state in the primary surface. Diagnostics live in a drawer.
- Percentages used as confidence above 90 unless backed by a deterministic computation.
- Indicator names from technical analysis in primary copy.
- Price targets that the system itself does not produce deterministically.
- "AI thinks…" framing. The system thinks; the AI explains.

---

## 25. Beginner Learning Layer

The learning layer is **the single most important thing that distinguishes this product from every other investing app the user has installed.** It is also the easiest thing to do badly. The bar is: contextual, source-grounded, never generic.

### 25.1 Principles

- **Contextual, never generic.** Every learning capsule is anchored to the user's actual holding, alert, recommendation, or thesis. There is no "Investopedia tab."
- **Two minutes max.** Every capsule reads in under two minutes. Anything longer goes into a follow-on capsule.
- **Plain English, no jargon stack.** A capsule may introduce one term per surface. It always pairs it with a plain-English translation.
- **Source-grounded.** Capsules cite either the system's own evidence or a curated, deterministic explanation library. They never paraphrase external sources without attribution.
- **Honest about uncertainty.** Capsules name what is and isn't known. "Patience is an action" is allowed; "this stock will recover" is not.
- **No quizzes, no badges, no streaks.** Learning is not gamified. The reward is comprehension and the daily summary.

### 25.2 The capsule library

These are the reusable capsule *types* that any surface may summon. Each is a small composable unit, not a separate page.

The **Build phase** column maps each capsule to its build stage (per §28). Stage 4 ships only the "Buildable now" capsules; the rest reserve chrome via the Coming-Later Pattern.

| Capsule type | When it appears | What it teaches | Build phase |
|---|---|---|---|
| **Why this matters** | On every recommendation, alert, and Deploy row. | One paragraph: why the system is surfacing this particular item to this particular portfolio. | **Stage 4** (deterministic, from existing `posture_reason` + evidence band + freshness). |
| **Patience is an action** | On any Reserve row in Deploy. | One paragraph: holding is a decision; reserving cash is a decision. Names what is being preserved. | **Stage 4** (deterministic, from existing Deploy v3 reserve trigger). |
| **Why Trim does not mean bad company** | On every Trim recommendation. | One paragraph: a Trim can mean position-sizing discipline, risk-tier change, or thesis-weight rebalance — not "the company is bad." | **Stage 4** (deterministic, from existing Intel v3 Trim decision + posture_reason). |
| **What missing data means** | On every Data-Missing pill; on every Tentative-evidence panel. | One paragraph: what the system tried, what it couldn't find, what the next intelligence run will attempt, what the user should and should not infer from absence. | **Stage 4** (deterministic, from existing freshness state + certification state). |
| **How this decision changes your portfolio shape** | On every Deploy Review screen. | One paragraph: before / after sector exposure, concentration delta, cash discipline. Never as numbers alone — always as plain-English shape. | **Stage 4** (deterministic, from existing Deploy v3 plan + portfolio snapshot). |
| **Learn the concept** | Whenever a translated concept is shown for the first time on a session (e.g., "Business quality"). | Plain-English meaning of the concept, why it matters, what its presence / absence implies. | **Stage 6** (depends on Stage 5I / 5J translation outputs). |
| **Company strategy primer** | On Intel detail drawer; on Portfolio holding detail. | Three sentences on what the company is trying to do, where capital is going, what management is betting on. Source-linked. | **Stage 6** (depends on Stage 5J company-strategy worker). |
| **Business story** | On Intel detail drawer; on first Radar surfacing. | One paragraph: what business this is in, who pays, how money is made, who competes. Updates only on real change. | **Stage 6** (depends on Stage 5J / 5I synthesis). |
| **What would make this thesis wrong?** (artifact-backed) | On every Buy / Hold / Trim card; on every Radar candidate. | One paragraph: a source-backed red-team counter-thesis. | **Stage 6** (depends on Stage 5F risk-red-team worker). The existing Risk Challenge Card section in the drawer is a separate, deterministic, decision-policy-derived deterministic element and ships in Stage 4C. |
| **Good company vs good stock** | On any Buy candidate where business quality is Strong but evidence band is OK / Partial. On Trim where business quality is Strong but momentum is negative. | One paragraph: the difference between owning a great business and owning it at the right price / right time. | **Stage 6** (depends on Stage 5I + 5H translation). |
| **What I learned today** | On Today, end of day (a small "Today's lesson" capsule). | A single 2–4 sentence editorial note tied to the day's most consequential decision or alert. Built deterministically from cross-source synthesis. | **Stage 6** (depends on Stage 5K pattern-detection worker). |

### 25.3 Where capsules appear

Each row below names the surface, the capsules planned there, and which build stage activates each.

- **Today** (Stage 4B): "Why this matters" per Act Today row (**Stage 4**). "What I learned today" end-of-fold (**Coming-Later chrome in Stage 4; activates in Stage 6E**).
- **Intel detail drawer** (Stage 4C): Risk Challenge Card section as the deterministic counter-thesis (**Stage 4**). "Business story," "Company strategy primer," artifact-backed "What would make this thesis wrong," "Good company vs good stock," and "Learn the concept" pills on translated metrics — **Coming-Later chrome in Stage 4; activates in Stage 6D**.
- **Deploy Review** (Stage 4E): "How this decision changes your portfolio shape" (**Stage 4**, mandatory), "Patience is an action" when Reserve is non-zero (**Stage 4**), "Why this matters" per row (**Stage 4**).
- **Portfolio holding detail** (Stage 4F): "Business story," "Company strategy primer" — **Coming-Later chrome in Stage 4; activates in Stage 6D**.
- **Alert Center** (Stage 4G): "Why this matters" per candidate (**Stage 4**). "What missing data means" on suppressed candidates (**Stage 4**).
- **Radar** (Stage 4G chrome, Stage 6G activation): "Business story," artifact-backed "What would make this thesis wrong," "Good company vs good stock" on every candidate — **all Coming-Later until Stage 6G**. The Radar page itself is Coming-Later in Stage 4G until Stage 5L lights up real candidates.
- **Journal** (Stage 4G): entry timeline + Pending evaluation windows (**Stage 4**). Lessons surface and "What I learned today" archive — **Coming-Later chrome in Stage 4; activates in Stage 6F**.

### 25.4 Authoring contract

- Capsule content is composed deterministically wherever possible, drawing from system facts (decision authority, conviction, evidence band, source freshness, posture-reason text).
- When LLM composition is used, the LLM receives only system facts and must not introduce numbers, scores, sources, or verdicts.
- Every capsule carries the Composed mark and source ordinals when AI-composed.
- Every capsule has a "report this capsule" affordance for the human author (single-user system) to flag and improve over time.

### 25.5 Anti-patterns banned

- "Lesson of the day" stripped of context.
- Embedded "investing 101" articles unrelated to current state.
- Investopedia-style definition dumps.
- Glossary as a navigation destination.
- Quizzes, scores, "investing literacy level."
- Badges, completion bars, "you've learned 12 concepts."

---

## 26. Page Architecture — Final State

This section reconciles `02_Architecture_and_Pages.md` with the contract's stricter ambition. Where this section disagrees with the older bible, this section wins; the older bible remains as design vocabulary.

### 26.1 Top-level destinations (final)

| Position | Destination | Job |
|---|---|---|
| 1 | **Today** (Command Center) | The 30-second daily brief. |
| 2 | **Intel** (Investment Committee) | Buy / Hold / Trim / Sell across watched tickers, with thesis, evidence, risk, and learning. |
| 3 | **Deploy** (Capital Allocation Ledger) | Where the next dollar goes and why. |
| 4 | **Portfolio** (Living Thesis Ledger) | Holdings as living theses, with concentration, exposure, and thesis health. |
| 5 | **Alerts** (Watchtower Review Queue) | A calm, read-only review surface of candidate alerts (no send authority in UI). |
| 6 | **Radar** (Opportunity Study Room) | Candidates not yet owned, surfaced with discipline. |
| 7 | **Journal** (Decision + Learning Memory) | Decision history, outcomes, lessons, daily learnings. |
| corner | **Command Bar** (`⌘K`) | Ask, explain, compare, challenge. |
| corner | **Source Room** (drawer) | Evidence inspection (summoned from any claim or any page). |
| corner | **Data Health** (drawer) | Trust surface (summoned from the persistent data-health dot). |
| corner | **Settings** | Account, sources, brokerage links, preferences. |

### 26.2 Page contract template

Every page in §26.3–§26.10 is specified in the same shape:

- **Primary user question** — the literal sentence the page answers.
- **Above-the-fold layout** — what the user sees first.
- **Core modules** — components that compose the page.
- **What it teaches** — the learning capsules this page may summon.
- **What data it consumes** — backend contracts read.
- **What must never appear** — explicit anti-list.
- **Mobile behavior** — what changes on phone widths.

### 26.3 Today — Command Center

- **Primary user question.** *"Should I do anything different today than yesterday — and what did I learn?"*
- **Above the fold.**
  - **The Brief** — 3–4 sentence deterministic prose paragraph. Composed mark if LLM-touched.
  - **Act Today** — top 3–5 highest-confidence Buy / Trim / Sell candidates. Each row deep-links to Intel detail.
  - **Risk Pulse** — tickers in Elevated or Acute risk tier. Absent if none.
  - **Deploy Ready** — cash deployable + the suggested split + Reserve. Single CTA to Review Deploy plan.
  - **Watchtower summary** — one-line plain-English event count since last visit, with a chip linking to Alert Center.
- **Below the fold.**
  - **What I learned today** capsule.
  - **New since you were away** chronological feed.
  - **Yesterday → Today** portfolio change strip.
  - **Market context** strip (S&P, NASDAQ, BTC, 10Y) at caption size.
- **Core modules.** The Brief, Act Today, Risk Pulse, Deploy Ready, Watchtower Summary, Today's Lesson, New Since You Were Away, Yesterday → Today.
- **What it teaches.** "Why this matters" per Act Today row. "What I learned today" capsule at end of fold.
- **Data consumed.**
  - Intel v3 latest certified snapshot (`GET /intel/v3/snapshot`).
  - Deploy v3 plan readiness + plan (`GET /api/v1/deploy/v3/readiness`, `GET /api/v1/deploy/v3/plan`).
  - Watchtower alert candidates summary (`GET /api/v1/alert-candidates` — counts only on Today).
  - Portfolio snapshot summary.
- **Must never appear.** Order entry. Streaks. "Top movers." KPI tile grid. Charts above the fold. Famous-investor quotes. Raw certification banners. Diagnostics. Email-delivery status (lives in Alert Center and Data Health).
- **Mobile behavior.** Single-column. Sticky mini-bar after scrolling past the Brief: *"3 actions today · NVDA Buy, RIVN Trim, SCHD Add."*

### 26.4 Intel — Investment Committee

- **Primary user question.** *"Across everything I follow, what does the system want me to do — and is it right?"*
- **Above the fold.** Card grid on desktop with action chips (Buy ▲ / Hold ■ / Trim ◆ / Sell ▼), plain-English thesis line, confidence ring, source freshness dot.
- **Layout.** Three-pane workspace on desktop (Filter rail · Card grid · Detail drawer). Card stack with bottom-sheet drawer on mobile.
- **Core modules.** Action Card, Detail Drawer, Filter rail, Watchlist auxiliary state, Data-health micro panel.
- **Detail drawer sections (final).**
  1. Header — ticker, action chip, confidence ring, "as of" timestamp.
  2. **Why this view?** — `posture_reason` text in plain English.
  3. **Plain-English thesis** — composed prose, ≤ 6 lines.
  4. **Business story** capsule.
  5. **Company strategy primer** capsule (collapsed by default).
  6. **Evidence trail** — numbered sources with snippet, freshness dot, credibility tier.
  7. **Risk challenge card** — *"If we are wrong, this is how it breaks."*
  8. **What changed since last time** — diff strip.
  9. **What would make this thesis wrong?** capsule.
  10. **Good company vs good stock** capsule (conditional).
  11. **How this decision changes your portfolio shape** capsule (only when this card is a Buy or Trim with material weight).
  12. **Action footer** — `[Ask why]` `[Challenge]` `[Compare]` `[Explain to a beginner]` chips.
- **What it teaches.** Every translated metric pill summons "Learn the concept." Drawer summons "Business story," "Company strategy primer," "What would make this thesis wrong," "Good company vs good stock."
- **Data consumed.** Intel v3 snapshot (`certified_holding_count`, `total_holding_count`, per-ticker decision rows), recommendation + agent insight evidence, valuation context bridge.
- **Must never appear.** Raw metric keys. Posture buckets as primary labels. Business Read. Giant stock-report panels. Fake price targets. Author bylines. Social sentiment as primary truth.
- **Mobile behavior.** Card stack with 12 px peek of next card. Action chip top-right of card (informational, not button). Drawer becomes bottom sheet (≤ 80% viewport height).

### 26.5 Deploy — Capital Allocation Ledger

- **Primary user question.** *"My deposit is in — where does it go, and why?"*
- **Above the fold.**
  - Deployable cash + Reserve declared as first-class.
  - **Suggested allocation** — one ledger row per Buy.
  - **Reserve** row — first-class, with trigger condition named.
  - **Review** CTA at the bottom of the ledger.
- **Core modules.** Deploy Instruction Row, Reserve Row, Decision History sidebar (last 5), Two-step Review → Confirm flow, Decision-log history below Step 3 (Stage 2.6B+ contract preserved).
- **What it teaches.** "How this decision changes your portfolio shape" on Review (mandatory). "Patience is an action" on non-zero Reserve. "Why this matters" per row.
- **Data consumed.** Deploy v3 readiness diagnostic, Deploy v3 plan, decision log history.
- **Must never appear.** Order routing UI. Sliders that imply override authority above the row level. Fake "what if" simulators. Fees-and-taxes calculators dressed as advice. Email-delivery status. Watchtower internals.
- **Mobile behavior.** Full-screen Review. Swipe-to-confirm at the bottom. Decision history collapses to "last 3," expandable.

### 26.6 Portfolio — Living Thesis Ledger

- **Primary user question.** *"Is my portfolio still consistent with what the system believes — and is each holding still a living thesis?"*
- **Above the fold.** Editorial ledger (holdings table) on the left (60%), exposure + thesis-health summary on the right (40%).
- **Holdings ledger columns.** Ticker · Allocation · Cost basis · Today · Thesis (action chip + conviction tier) · Source freshness dot.
- **Right column panels.** Concentration · Sector exposure · Theme exposure · Thesis health (one-sentence summary) · Source freshness summary.
- **Holding detail drawer.** Header → Thesis health timeline (sparkline of action through time) → Last 3 decisions on this ticker (linked to Journal) → Source freshness panel → Stale-thesis warning if applicable → **Business story** capsule → **Company strategy primer** capsule → **What would make this thesis wrong** capsule.
- **What it teaches.** Holding as living thesis. Concentration meaning. Position-size discipline. "Good company vs good stock" when applicable.
- **Data consumed.** Portfolio snapshot, per-ticker Intel decision, sector / theme classifications, source freshness ledger.
- **Must never appear.** Buy / Sell order buttons. Real-time chart streaming. Performance attribution graphs without sources. "Beat the market by X%" framing.
- **Mobile behavior.** Holdings ledger collapses to single-column cards; exposure panels become a horizontally swipeable strip; drawer becomes bottom sheet.

### 26.7 Alerts — Watchtower Review Queue

(See §27 for the Alert Center integration decision.)

- **Primary user question.** *"What did Watchtower notice since I last looked — and is any of it worth my attention?"*
- **Above the fold.** Dry-run banner (until real-send activation). Candidate list grouped by severity. Delivery outbox panel (read-only, collapsed by default).
- **Core modules.** Candidate Row (with severity pill, plain-English status), Delivery Outbox Row, Dry-run Safety Banner, "Why this matters" capsule per candidate.
- **What it teaches.** "Why this matters" per candidate. "What missing data means" on suppressed candidates.
- **Data consumed.** `GET /api/v1/alert-candidates`, `GET /api/v1/alert-delivery-outbox`.
- **Must never appear.** Send-now controls. Toggle for `ALERT_EMAIL_DRY_RUN`. Provider configuration. Mute-per-ticker controls (deferred). Push / SMS controls (deferred).
- **Mobile behavior.** Single-column. Candidate row becomes a card. Drawer for candidate details becomes bottom sheet.

### 26.8 Radar — Opportunity Study Room

- **Primary user question.** *"Is there something I should be looking at that I am not already?"*
- **Above the fold.** Vertical feed of Opportunity Cards (plum 1pt left border, no Buy/Hold/Trim/Sell chip, workflow chip instead).
- **Core modules.** Opportunity Card, Compare Drawer (side-by-side with closest current holding), Workflow chip (Surfaced / Researching / Promoted / Dismissed), Insufficient-evidence-yet state.
- **What it teaches.** "Business story" on every candidate, "What would make this thesis wrong," "Good company vs good stock" when applicable.
- **Data consumed.** Radar candidate adapter (when wired; see §28 for backend reservation). Today: Radar is reserved in IA; backend candidates may be empty until the surface ships.
- **Must never appear.** "Trending tickers." Reddit / Twitter / Discord sentiment. Famous-investor portfolio mirroring. Fake price targets. Crystal-ball language.
- **Mobile behavior.** Vertical feed. Compare opens as bottom sheet.

### 26.9 Journal — Decision + Learning Memory

- **Primary user question.** *"Was the system right last time? Was I? And what have I learned?"*
- **Above the fold.** Monthly chapter numerals (`I`, `II`, `III`) with vertical timeline of entries.
- **Entry anatomy.** Recommendation · Action taken · Outcome (Pending until evaluation window closes) · Thesis-health change · Source freshness change · **Lesson** (deterministic pattern detection, never instant).
- **Core modules.** Decision Timeline, Entry detail sheet, Lessons surface, **What I learned today** archive (browsable by date).
- **What it teaches.** Pattern lessons (only when the system identifies a recurring pattern). "What I learned today" archive.
- **Data consumed.** Decision log (Deploy v3 + future Intel decision log when wired), action feedback events.
- **Must never appear.** "You've made $X this year!" Comparisons to S&P that are not source-cited. Streaks. Awards. Leaderboards.
- **Mobile behavior.** Single-column timeline. Entry expands inline.

### 26.10 Source Room — Evidence Drawer

A globally summonable drawer, not a top-level destination. Always reachable from any claim, any superscript, any "Evidence" affordance.

- **Primary user question.** *"Who said this, how recent, how credible, and who disagrees?"*
- **Drawer sections.** Source list (ordinal · snippet · freshness dot · credibility tier · source name · date) · Contradiction strip when applicable · Evidence-weak caution panel when applicable · "Data missing" pill where applicable.
- **What it teaches.** "What missing data means" capsule. Source-credibility tier explanation (linked sheet).
- **Data consumed.** Per-claim source registry (Stage 4D contract).
- **Must never appear.** URL strings as primary content. Unattributed claims. Hidden "see more" risk.
- **Mobile behavior.** Bottom sheet, ≤ 80% viewport height, swipe-to-dismiss.

### 26.11 Data Health — Trust Surface

A globally summonable drawer summoned by the persistent data-health dot in the chrome.

- **Primary user question.** *"What does the system know, what is fresh, what is stale, what is missing — and what would it take to fix?"*
- **Drawer sections.** Per-source freshness rollup · Provider health (ok / near-limit / limited) · Certification state plain-English line · Stale-source inventory · Email-delivery state (when relevant) · "Next intelligence run at HH:MM ET."
- **What it teaches.** "What missing data means" capsule on every stale row.
- **Data consumed.** Evidence freshness ledger, provider registry health, Intel certification state, email-delivery worker log summary (when domain-verified; before then, surfaces the dry-run state honestly).
- **Must never appear.** Stack traces. Internal log lines. Provider API keys. Resend domain-verification raw state (only plain-English summary).
- **Mobile behavior.** Bottom sheet.

### 26.12 Command Bar — Ask, Explain, Compare, Challenge

A globally summoned modal (`⌘K` / `Ctrl+K`). Spec preserved from §14.3.

- **Primary user question.** *"How do I ask the system something without leaving what I am doing?"*
- **Modal sections.** Recent · Suggestions (page-context-aware) · Jump-to.
- **Allowed commands.** Ask, explain, compare, challenge, summarize, find opportunity, jump.
- **Forbidden commands.** Execute, buy, sell, send alert, override decision.
- **What it teaches.** Every composed response carries the Composed mark and source ordinals.
- **Mobile behavior.** Full-width sheet summoned by the title-bar search affordance.

---

## 27. Alert Center Integration Decision

Stage 3G shipped Alert Center UI v1 as a read-only top-level destination at `/dashboard/alerts`. This contract preserves it as a **top-level destination** for the next phase, with the following clarifications:

### 27.1 Final design role

- **Top-level destination.** Alerts remain in the primary nav (BottomNav + SideNav).
- **Read-only and dry-run safe.** Email worker remains dry-run until Resend domain verification is complete. The dry-run safety banner remains always visible until real-send is safely activated in a future, separate, non-design stage.
- **No send controls in UI.** Ever. Send activation is an environment-level operation, not a UI gesture.
- **Today and Alerts cross-link.** Today shows a one-line plain-English Watchtower summary; tapping it opens Alerts. Alert Center does not duplicate Today's brief.

### 27.2 Why top-level (and not Today sub-surface or Journal section)

- Alert Center is **a workflow, not a side panel**. It has a queue, a review action, a feedback loop (Stage 3A action_feedback), and a delivery outbox. None of those fit Today's editorial brief format.
- Alert Center is **not a learning artifact**. It does not belong in Journal. Journal owns decisions, outcomes, and lessons. Alert Center owns "what changed enough to consider doing something."
- Alert Center is **not a diagnostic surface**. Email-delivery state lives in Data Health. Alert Center surfaces candidates + the dry-run banner only.

### 27.3 Stage 4 treatment

- Stage 4A applies the design system shell (chrome, nav, dark atelier canvas) to Alerts without changing its behavior.
- Stage 4 stages 4B–4G do not redesign Alert Center.
- A future stage (post-4H) handles Alert Center polish + real-send activation. Real-send activation is **out of scope** for the entire Stage 4 design overhaul.

### 27.4 Alert Center must continue to honor

- `ALERT_EMAIL_DRY_RUN=true` remains the default and visible state until Resend domain verification completes.
- No UI element may control `ALERT_EMAIL_DELIVERY_ENABLED` or `ALERT_EMAIL_DRY_RUN`.
- Dry-run safety banner remains always visible until real-send is safely activated.
- No push, no SMS, no provider-config UI.

---

## 28. Sequencing — Stage 4 / Stage 5 / Stage 6

> **This is the controlling sequencing rule for the entire overhaul.**
>
> The design overhaul should start now, but the full S-grade research-backend intelligence does **not** yet exist. The current system has a strong deterministic Intel v3 / Deploy v3 / Watchtower / Alert foundation. The Research Artifact Store, finance research agents, SEC filing analysis, source credibility registry, contradiction detection, technical / fundamental / sentiment synthesis, and evidence completeness scoring are **future stages**.
>
> Stage 4 must therefore be **future-proof but honest**: build the Quiet Atelier UX foundation and the core current-data surfaces; reserve shape for advanced intelligence surfaces but never fabricate them; render honest unavailable / coming-later states where backend support is required first.

### 28.1 The three-stage split

| Stage | Theme | Scope |
|---|---|---|
| **Stage 4 — Quiet Atelier UX foundation + core current-data surfaces** | Design + frontend only. Consumes the existing Intel v3 / Deploy v3 / Watchtower / Alert data. Wraps every advanced-intelligence module in an honest unavailable state. | Tokens · app shell · Today · Intel · Deploy · Portfolio · Alerts · Evidence shell · Learning shell · Mobile + motion polish. |
| **Stage 5 — S-grade Research Artifact + finance-agent intelligence backend** | Backend / data. No new visible surfaces. Lights up the data that Stage 6 surfaces will consume. | Research Artifact Store apply + writer scaffolding · finance research workers · source credibility registry · contradiction detection · freshness / staleness model expansion · evidence completeness scoring · truth adapter · replay / audit trail. |
| **Stage 6 — Advanced evidence, learning, Radar, Journal, command-bar intelligence surfaces** | Frontend. Activates the surfaces that depend on Stage 5. Replaces the honest-unavailable states from Stage 4 with real data. | Full capsule library activation · Radar live · Journal "What I learned today" + lesson patterns · command bar live AI · contradiction strip live · source credibility tier live · "what would make this thesis wrong" backed by real red-team artifacts · "company strategy primer" backed by real filing-derived strategy notes. |

### 28.2 What is currently implemented (Stage 4 may consume these)

- **Intel v3 deterministic policy** (Buy / Hold / Trim / Sell) with worker-certified snapshots.
- **Intel v3 snapshot evidence freshness state** (`evidence_freshness_state` in the API response).
- **Watchtower evidence-freshness + re-certification + alert-candidate generation** (Stage 3A–3G).
- **Deploy v3 exact-dollar plans** with readiness diagnostic, decision logging, journal accounting (Stage 2.4A–2.9).
- **Alert candidate generation + delivery outbox + dry-run email worker** (Stage 3A–3F).
- **Read-only Alert Center UI v1** (Stage 3G).
- **Visible price / valuation context bridge** (Build 3 PR 2B) — feature-flagged.
- **Posture / `posture_reason` text** — composed plain-English explanation for the visible decision.
- **Research Artifact Store v1 schema** — promoted to `v2/database/017_research_artifact_store_v1.sql`. **Not yet applied to production. No runtime writer exists.**

### 28.3 What is NOT yet implemented (Stage 4 must not fabricate)

The Stage 4 frontend may **never** render any of the following as if it existed today:

- Filing-derived risk items, strategy disclosures, capital-allocation commentary, or competitive-position notes from SEC documents.
- Technical analysis (momentum signals, volatility regimes, support / resistance levels, drawdown stats) as labeled outputs.
- Fundamental synthesis beyond what the existing valuation-context bridge already provides.
- Sentiment / news analysis (source-classified events, narrative deltas, sentiment direction).
- Analyst-revision deltas or source-classified analyst notes.
- Source credibility tiers as authoritative tier labels.
- Contradiction detection between sources within an evidence trail.
- Evidence completeness scoring.
- Company-strategy primers backed by real filings or transcripts.
- "What would make this thesis wrong" backed by a real red-team artifact.
- Pattern-detected lessons (Journal "lessons surface").
- Daily learning summaries ("What I learned today") composed from real cross-source synthesis.
- Radar candidates.
- Live AI command-bar composition (ask / challenge / compare / explain).

Where Stage 4 surfaces summon any of the above, they must render an **honest unavailable state** (see §28.4 below) — not a placeholder, not a mock, not a fake.

### 28.4 Honest unavailable / coming-later state pattern

Every Stage 4 surface that *anticipates* a Stage 5 / Stage 6 intelligence module must implement the **Coming-Later Pattern**:

- A calm, plain-English caption: *"This intelligence module is being prepared. The next intelligence stage will surface it here."*
- The surface's chrome (header, eyebrow, position in the layout) renders so the user sees where the future module will live — but the body is empty, not mock.
- No mock numbers. No mock tiers. No mock sources. No mock text that resembles a real claim.
- Once the backing Stage 5 / Stage 6 module ships, the chrome is reused; only the body content changes.

This pattern preserves layout intent without fabricating intelligence. It is the only allowed way to render future-only modules in Stage 4.

### 28.5 What every future backend slice (Stage 5+) answers to

- **Deterministic policy remains final visible authority.** No LLM, no agent, no worker may set Buy / Hold / Trim / Sell, dollar allocation, risk tier, or confidence band visible to the user.
- **No LLM-invented metrics, prices, allocations, risk tiers, or recommendations.** Ever.
- **No fabricated price targets.** Ever.
- **All artifacts source-grounded and replayable.** Artifacts without sources are rejected at write time.
- **Missing data is honest, not hidden.** The UI must always be able to render "Data missing" with the same hierarchy as any present claim.
- **Forbidden visible-decision fields rejected at write time** in the Research Artifact Store (per §1.1 of the artifact-store spec).

### 28.6 Stage 5 reservation (backend intelligence)

Stage 5 is **backend / data only**. No new visible surfaces; existing Stage 4 chrome with the Coming-Later Pattern becomes consumable for the first time as Stage 5 substages land.

| Stage 5 substage | Reservation | Surfaces unlocked |
|---|---|---|
| **5A** | Research Artifact Store production apply + writer scaffolding. | Foundation only — no UI change yet. |
| **5B** | Source credibility registry (curated, deterministic). | Source Room credibility tier becomes real (Stage 6). |
| **5C** | Contradiction detection within evidence trails. | Contradiction strip in Source Room becomes real (Stage 6). |
| **5D** | Evidence completeness scoring per claim. | Evidence-weak caution panel becomes real (Stage 6). |
| **5E** | Truth adapter (Phase 5 of architecture plan) — the only layer that may flip `safe_for_decision = true`. | Required before any research worker output is allowed to influence visible decisions. |
| **5F** | First non-decision research worker (e.g., filings-derived risk items, capital-allocation, risk red-team). | "What would make this thesis wrong" capsule becomes real (Stage 6). |
| **5G** | Sentiment / news worker (source-classified, never as primary truth). | "What changed in the market story" surfaces become real (Stage 6). |
| **5H** | Technical analysis worker (price behavior, momentum, volatility — plain-English translation, never indicator names). | Intel detail drawer technical-context section becomes real (Stage 6). |
| **5I** | Fundamental synthesis worker (business quality, growth, margins, cash discipline — plain-English translation). | Intel detail drawer fundamental-context section becomes real (Stage 6). |
| **5J** | Company-strategy primer worker (filings + transcripts → 3-sentence strategy note). | "Company strategy primer" capsule becomes real (Stage 6). |
| **5K** | Pattern-detection worker (Journal lessons). | Journal "lessons surface" becomes real (Stage 6). |
| **5L** | Radar candidate worker (sector / theme / valuation-screen synthesis, never trending tickers / social). | Radar feed becomes real (Stage 6). |
| **5M** | Real-send activation for email alerts (after Resend domain verification). | Not a design stage; bumps Alert Center dry-run banner off when ready. |

### 28.7 Stage 6 reservation (advanced visible surfaces)

Stage 6 is **frontend only**, activating the surfaces that Stage 5 enabled. The Coming-Later Pattern chrome from Stage 4 is reused; only the body content becomes real.

| Stage 6 substage | Surface | Depends on |
|---|---|---|
| **6A** | Source Room live (credibility tier + contradiction strip + evidence completeness). | 5B + 5C + 5D. |
| **6B** | Intel detail drawer — technical-context section live. | 5H. |
| **6C** | Intel detail drawer — fundamental-context section live. | 5I. |
| **6D** | Intel detail drawer — "Company strategy primer" capsule live; "What would make this thesis wrong" backed by real red-team. | 5J + 5F. |
| **6E** | Today — "What I learned today" capsule live; Watchtower-summary becomes a real cross-source synthesis. | 5K + cross-worker output. |
| **6F** | Journal — "What I learned today" archive + Lessons surface live. | 5K. |
| **6G** | Radar live. | 5L. |
| **6H** | Command bar live AI (ask / challenge / compare / explain) using only system data + constrained LLM composition. | 5B + 5C + 5D + 5F (so AI has real evidence + credibility to cite). |

### 28.8 Module-by-module: build-now vs. wait

This is the single most important table in the contract. Every Stage 4 PR reviewer checks each surface against this map.

| UI module | Build now (Stage 4)? | Requires backend first? | Notes |
|---|---|---|---|
| App shell, tokens, typography, motion tokens | ✅ Yes | — | Stage 4A. |
| Today — The Brief (deterministic prose) | ✅ Yes | — | Stage 4B. Composed from existing Intel v3 snapshot + Deploy v3 plan + Watchtower candidate counts. |
| Today — Act Today rows | ✅ Yes | — | Stage 4B. Reads existing Buy / Trim / Sell from Intel v3 snapshot. |
| Today — Risk Pulse | ✅ Yes | — | Stage 4B. Reads existing risk tier from Intel v3 snapshot. |
| Today — Deploy Ready | ✅ Yes | — | Stage 4B. Reads existing Deploy v3 plan. |
| Today — Watchtower Summary | ✅ Yes | — | Stage 4B. Reads existing `GET /api/v1/alert-candidates` counts. |
| Today — "What I learned today" capsule | ⚠ Chrome only (Coming-Later) | ✅ Stage 5K | Stage 4B reserves the slot with the Coming-Later state. Stage 6E activates. |
| Today — "Why this matters" capsule per row | ✅ Yes | — | Stage 4B. Deterministic composition from existing `posture_reason` + evidence band + freshness. |
| Intel — Action Card visual system | ✅ Yes | — | Stage 4C. |
| Intel — Detail drawer header + "Why this view?" + plain-English thesis + evidence trail + risk challenge + what changed + action footer | ✅ Yes | — | Stage 4C. All read from existing Intel v3 snapshot + posture_reason + existing analyst-evidence rows. |
| Intel — "Business story" capsule | ⚠ Chrome only (Coming-Later) | ✅ Stage 5J | Stage 4C reserves the slot. Stage 6D activates. |
| Intel — "Company strategy primer" capsule | ⚠ Chrome only (Coming-Later) | ✅ Stage 5J | Stage 4C reserves the slot. Stage 6D activates. |
| Intel — "What would make this thesis wrong" capsule | ⚠ Chrome only (Coming-Later) | ✅ Stage 5F | Stage 4C reserves the slot. Stage 6D activates. Note: the existing Risk Challenge Card section in the drawer is a separate, deterministic, decision-policy-derived element and ships live in Stage 4C; the capsule above is the future red-team-artifact-backed version. |
| Intel — "Good company vs good stock" capsule | ⚠ Chrome only (Coming-Later) | ✅ Stage 5I + 5J | Stage 4C reserves; Stage 6 activates. |
| Intel — technical-context section in drawer | ⚠ Chrome only (Coming-Later) | ✅ Stage 5H | Stage 4C reserves; Stage 6B activates. |
| Intel — fundamental-context section in drawer | ⚠ Chrome only (Coming-Later) | ✅ Stage 5I | Stage 4C reserves; Stage 6C activates. (The existing valuation-context bridge is the one allowed live exception, gated by `INTEL_V3_PRICEBAND_VISIBLE_CONTEXT_V1_ENABLED`.) |
| Intel — Watchlist auxiliary state | ✅ Yes | — | Stage 4C. Visually distinct from BHTS. |
| Evidence shell — Source-Backed Claim primitive, Source Room drawer | ✅ Yes (shell) | Partial | Stage 4D ships the drawer + ordinal list + freshness dot using existing per-claim source data (where present). Credibility tier and contradiction strip render Coming-Later until Stage 5B / 5C. |
| Evidence shell — Source credibility tier (5-dot ladder) | ⚠ Chrome only (Coming-Later) | ✅ Stage 5B | Stage 4D reserves the ladder rendering; activates in Stage 6A. |
| Evidence shell — Contradiction strip | ⚠ Chrome only (Coming-Later) | ✅ Stage 5C | Stage 4D reserves the strip rendering; activates in Stage 6A. |
| Evidence shell — Evidence-weak caution panel | ⚠ Chrome only (Coming-Later) | ✅ Stage 5D | Stage 4D reserves; activates in Stage 6A. The existing `evidence_band = OK/PARTIAL` may drive a coarse calm-caution today, but it is not the full completeness score. |
| Evidence shell — Data-Missing pill | ✅ Yes | — | Stage 4D. Renders honestly whenever the source list is empty for a claim. |
| Evidence shell — Composed mark for AI prose | ✅ Yes | — | Stage 4D. Used by Stage 4B Brief composition when LLM branch is enabled. |
| Deploy — Ledger redesign, Reserve row, Review / Confirm flow, "How this changes portfolio shape" capsule, "Patience is an action" capsule | ✅ Yes | — | Stage 4E. All from existing Deploy v3 plan + decision log. |
| Portfolio — Holdings ledger, concentration, sector / theme exposure, thesis-health, source-freshness summary | ✅ Yes | — | Stage 4F. From existing portfolio snapshot + Intel decisions. (Sector / theme classification consumes only what the existing portfolio data already provides; if a theme is missing, the panel shows the Coming-Later state for that segment.) |
| Portfolio — Holding detail drawer header, thesis-health sparkline, last 3 decisions, source freshness, stale-thesis warning | ✅ Yes | — | Stage 4F. From existing data. |
| Portfolio — Holding drawer "Business story" / "Company strategy primer" / "What would make this thesis wrong" capsules | ⚠ Chrome only (Coming-Later) | ✅ Stage 5J + 5F | Stage 4F reserves; Stage 6D activates. |
| Alert Center — Stage 3G layout, dry-run banner, candidate list, delivery outbox, severity pills, plain-English status labels | ✅ Yes | — | Already shipped (Stage 3G). Stage 4 applies the design system shell to it (Stage 4A) without changing behavior. |
| Alert Center — "Why this matters" capsule per candidate | ✅ Yes (deterministic version) | Partial | Stage 4G ships the deterministic version (composed from existing severity + posture_reason + freshness). The richer cross-source synthesis version waits for Stage 5G / 5K. |
| Alert Center — Mute / scope / cooldown controls | ❌ No | ✅ A future Alerts-product stage | Out of scope for Stage 4. |
| Alert Center — Real-send activation, push, SMS | ❌ No | ✅ Stage 5M + Resend domain verification | Out of scope for the entire design overhaul. |
| Radar | ⚠ Chrome only (Coming-Later) destination | ✅ Stage 5L | Stage 4G ships the destination with an honest empty state; Stage 6G activates. |
| Journal — entry timeline, entry anatomy, Pending evaluation windows | ✅ Yes | — | Stage 4G. From existing decision log + action-feedback events. |
| Journal — "Lessons surface" (pattern-detected lessons) | ⚠ Chrome only (Coming-Later) | ✅ Stage 5K | Stage 4G reserves the panel; Stage 6F activates. |
| Journal — "What I learned today" archive | ⚠ Chrome only (Coming-Later) | ✅ Stage 5K | Stage 4G reserves; Stage 6F activates. |
| Data Health drawer — per-source freshness rollup, certification state, stale inventory, email-delivery dry-run state, next intelligence run | ✅ Yes | — | Stage 4D. From existing evidence freshness state + Intel v3 certification + alert worker dry-run summary. |
| Data Health drawer — provider health (ok / near-limit / limited), source-class freshness expansion to filings / fundamentals / sentiment / analyst notes | ⚠ Chrome only (Coming-Later) | ✅ Stage 5 substages as each source class lights up | Stage 4D reserves. |
| Command bar (`⌘K`) — shell, recent / suggestions / jump | ✅ Yes (shell) | — | Stage 4A ships the shell with jump-to navigation and search of static labels only. |
| Command bar — live AI ask / challenge / compare / explain | ⚠ Chrome only (Coming-Later) | ✅ Stage 5B + 5C + 5D + 5F | Stage 4A reserves; Stage 6H activates. The "ask" input may be present but submits to a Coming-Later state until Stage 6H. |
| Mobile bottom-sheet drawers, card stack with peek, swipe-to-confirm, sticky mini-bar, reduced-motion collapse, a11y audit | ✅ Yes | — | Stage 4H. |

### 28.9 What the frontend must never pretend exists before Stage 5 / 6 ships

- Filing-derived risk items, strategy disclosures, or capital-allocation commentary as authority.
- Technical analysis labels (momentum, volatility, support / resistance) as authority.
- Fundamental synthesis (beyond the existing valuation-context bridge) as authority.
- Sentiment / news synthesis as authority.
- Analyst-revision deltas or analyst-note classifications as authority.
- Source credibility tiers as authoritative tier labels.
- Contradiction tags between sources.
- Evidence completeness scores.
- Hidden-gem candidate scoring.
- Live AI composition for ask / challenge / compare / explain.
- Pattern-detected Journal lessons.
- "What I learned today" daily synthesis.
- Radar candidates.

If any Stage 4 PR diff includes any of the above as if it existed today, the merge gate must reject the PR.

---

## 29. Visual Execution Standard

The visual identity, motion, typography, and tokens defined in `01_Principles_Identity_Motion.md` are the canonical reference. This section translates that bible into **implementable rules** that the Stage 4A token foundation must enforce.

### 29.1 Hard rules (each must be checkable against the diff)

- **Dark-first quiet atelier canvas.** `bg.canvas = #0A0B0F`. Light mode is secondary and uses *Paper* tokens. No raw hex outside the token file.
- **Boutique editorial typography.** Display serif (Tiempos Headline / GT Sectra) + body sans (Söhne / Inter Tight). `tabular-nums` and `slashed-zero` always on for numerics.
- **Premium but restrained motion.** Motion tokens from §5.1. `prefers-reduced-motion: reduce` collapses every motion to a 160 ms fade. No spring physics on data. No parallax. No confetti. No bounce.
- **No generic blue/gold legacy UI.** Old Intel cockpit accent colors are retired. Signature accent is Atelier Green.
- **No neon. No glow. No crypto-casino look.** Glass surfaces only on top nav, command bar, Deploy confirmation.
- **No generic KPI tile grid.** Today is an editorial spread, not a tile dashboard.
- **No raw shadcn-card reskin.** If a surface looks like `bg-zinc-900 rounded-2xl border-zinc-700 p-4`, it fails.
- **Every number has unit, timestamp, and source / freshness path where possible.** Where it doesn't, render "Data missing" honestly.
- **Every color is semantic or structural.** No decorative color.
- **Animations explain intelligence, changes, or navigation only.** No decorative animation. No animation on a number that did not change.

### 29.2 Required scaffolding (Stage 4A)

- `tailwind.config.ts` extended with Obsidian + Paper palettes (no removal of existing tokens; additive).
- `globals.css` carries CSS variables for both modes.
- Spacing scale enforced (`4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80` — no off-grid values).
- Radii enforced (`2, 8, 12, 20, 999`).
- Elevation enforced (`elev.0` through `elev.3`).
- Type tokens enforced (`display.xl`, `display.lg`, `display.md`, `headline.lg`, `headline.md`, `body.lg`, `body.md`, `body.sm`, `caption`).
- App shell reset (top nav, side nav, bottom nav) using the new tokens, with no other behavior changes.

### 29.3 Forbidden in the visual layer

- Inline raw hex.
- Off-grid spacing values (e.g., 18 px).
- Half-radii (e.g., 6 px).
- Multi-layer neumorphic shadows.
- Glass on cards.
- Rainbow chart palettes.
- Stock-photo backgrounds.
- Illustrated mascots.
- Emoji in UI strings.
- Exclamation marks in primary copy.

---

## 30. Build Sequence — Stage 4A → 4H

> **Capacity rule.** Each stage is one coherent capability slice per the OS v4 prompt-compression standard (CLAUDE.md §1). Each stage is reviewable, reversible, and stops at a clear merge gate. No "complete redesign" PR.
>
> **Authority rule.** No stage touches Intel v3 / Deploy v3 / Watchtower decision authority. No stage activates real email send. No stage applies SQL unless explicitly named and pre-approved (none in Stage 4).
>
> **Future-proof rule.** Every Stage 4 stage that *anticipates* a Stage 5 / 6 intelligence module must use the **Coming-Later Pattern** (§28.4). Never fabricate. Never mock. Always reserve chrome, not body.

The stage-by-stage spec follows. Each stage uses the same fields.

### 30.1 Stage 4A — Design System Foundation + App Shell Reset

- **Purpose.** Land the visual tokens, typography, spacing, and app shell chrome so every subsequent stage has a foundation to render against.
- **Exact scope.**
  - Add Obsidian (dark) + Paper (light) palettes to `tailwind.config.ts` (additive; no removal of existing tokens).
  - Add CSS variables to `globals.css` for both modes.
  - Add the two type families via `next/font` (display serif + body sans).
  - Add tabular-nums / slashed-zero opentype features as default for numerics.
  - Enforce 4-pt spacing scale; radii; elevation; type tokens.
  - Reset app shell: top nav (glass), side nav (engraved active rule), bottom nav (mobile), data-health dot (placeholder state — wired in 4D for real source data), `⌘K` placeholder (functional shell only, no AI calls).
  - Apply the canvas to existing pages (Today, Intel, Deploy, Portfolio, Alerts, Settings) **without** restructuring their content.
- **Model recommendation.** **Sonnet.** Mechanical but design-sensitive; Sonnet is appropriate. Codex is a fallback for pure-config diffs but the shell reset crosses too many files.
- **New chat vs follow-up.** **New chat.** This is a fresh slice; CLAUDE.md fresh-chat-default applies.
- **Estimated usage.** **Medium.**
- **UI budget.** ~12 files max (tailwind config, globals, fonts, top/side/bottom nav, shell layout, theme provider, 5 page wrappers).
- **Backend touch.** **No.**
- **SQL.** **No.**
- **Test / validation plan.**
  - Visual smoke pass on Today, Intel, Deploy, Portfolio, Alerts in dark + light.
  - All existing tests must pass (snapshot tests may need updating for chrome only — no behavior changes).
  - `prefers-reduced-motion` collapse verified.
- **Merge gate.** Plain-English UI Pack + Premium Delight reviewer. No new copy. No behavior changes. App shell renders on every existing page without regression.
- **Out of scope.** Page content restructuring. Component redesign. AI surface activation. Source room. Capsules. New backend calls.
- **Risk + split rule.** Low–medium. If `tailwind.config.ts` touches grow > 200 lines or the shell reset adds > 12 files, split into 4A.1 (tokens + fonts) and 4A.2 (shell reset).

### 30.2 Stage 4B — Today Command Center

- **Purpose.** Land the 30-second daily brief and prove the editorial spine end-to-end on the most-opened surface — using only existing Intel v3 / Deploy v3 / Watchtower data, with the Coming-Later pattern for advanced-synthesis modules.
- **Exact scope.**
  - **The Brief** component — deterministic prose composition only. (LLM-composed branch is **out of Stage 4**; the Composed-mark primitive ships in 4D for use when 5G+ enables real composition.)
  - **Act Today** (top 3–5 highest-confidence Buy / Trim / Sell rows from Intel v3 snapshot).
  - **Risk Pulse** (tickers in Elevated / Acute risk tier).
  - **Deploy Ready** (deployable cash + suggested split + Reserve, read from Deploy v3 plan).
  - **Watchtower Summary** (one-line plain-English count of candidates since last visit; links to Alert Center).
  - **"What I learned today" chrome (Coming-Later state)** — the slot, the eyebrow, the position; body renders the calm caption *"This daily lesson is being prepared. The next intelligence stage will surface it here."* Real activation is Stage 6E.
  - **New since you were away** chronological feed (built from existing events).
  - **Yesterday → Today** strip.
  - **"Why this matters"** capsule on Act Today rows (tap to expand) — deterministic composition from existing `posture_reason` + evidence band + freshness only.
- **Model recommendation.** **Sonnet.** Editorial composition needs design judgment.
- **New chat vs follow-up.** **New chat.**
- **Estimated usage.** **High.**
- **UI budget.** ~10 files (page, 6 module components, capsule primitive, Composed-mark primitive, types).
- **Backend touch.** **No.** Reads only existing endpoints.
- **SQL.** **No.**
- **Test / validation plan.**
  - Unit tests for deterministic Brief composition (covers empty / single / multi states).
  - Unit tests for `whatChangedSinceLastVisit()` pure helper.
  - Frontend snapshot tests for Today fold layout.
  - Visual smoke on dark + light + reduced motion.
  - 30-second-rule manual validation.
- **Merge gate.** Plain-English UI Pack, Premium Delight reviewer, Roadmap Guardian (Stage 4B item).
- **Out of scope.** Intel detail drawer. Deploy Review redesign. Portfolio. Radar. Journal. AI command bar live. Real LLM brief composition.
- **Risk + split rule.** High (most-opened surface; UI budget gate review per `docs/ai/PROMPT_LIBRARY.md`). If Brief composition pipeline grows past pure functions, split capsule + Brief into 4B.1 and 4B.2.

### 30.3 Stage 4C — Intel Investment Committee Redesign

- **Purpose.** Land the Action Card visual system, confidence / risk / freshness primitives, beginner thesis copy, and detail drawer with all sections wired — using only existing Intel v3 snapshot data. Coming-Later chrome for future-only capsule slots.
- **Exact scope.**
  - **Action Card** (per §15.1, on the new tokens).
  - **Confidence Ring** primitive (5-step ladder).
  - **Risk Glyph** primitive (4-tier ladder).
  - **Freshness Dot** primitive.
  - **Detail Drawer** (right-side on desktop, bottom sheet on mobile) with the following sections:
    - **Live (from existing data):** Header · "Why this view?" (posture_reason) · Plain-English thesis · Evidence trail (sources, snippets, freshness dot) · Risk Challenge Card (decision-policy-derived deterministic counter-thesis) · What changed since last time · Action footer chips (`[Ask why]` `[Challenge]` `[Compare]` `[Explain to a beginner]` — the chips render but submit to Coming-Later AI state until Stage 6H).
    - **Coming-Later chrome only:** Business story · Company strategy primer · "What would make this thesis wrong" (red-team-artifact-backed version) · Good company vs good stock · Technical context · Fundamental context (beyond the existing valuation bridge) · "How this decision changes your portfolio shape."
  - **Filter rail** (Buy / Hold / Trim / Sell counts + Watchlist auxiliary).
  - **Watchlist auxiliary state** (visually distinct from BHTS, never as a filter peer).
  - **Existing valuation-context bridge** (`INTEL_V3_PRICEBAND_VISIBLE_CONTEXT_V1_ENABLED`) rendered live within the drawer where it is enabled.
- **Model recommendation.** **Sonnet.** Component-system pass; design judgment required.
- **New chat vs follow-up.** **New chat.**
- **Estimated usage.** **High.**
- **UI budget.** ~12 files (card, drawer, drawer sections, ring, glyph, dot, filter rail, watchlist state, types, tests).
- **Backend touch.** **No.** Consumes Intel v3 snapshot read-only.
- **SQL.** **No.**
- **Test / validation plan.**
  - Snapshot tests for card states (Buy / Hold / Trim / Sell / Watchlist).
  - Drawer interaction tests (open / close / focus management).
  - aria-label tests for action chip, confidence ring, freshness dot.
  - Visual smoke on dark + light + reduced motion + mobile widths.
- **Merge gate.** Plain-English UI Pack, Policy Authority reviewer (verifies B/H/T/S authority preserved), Accessibility reviewer.
- **Out of scope.** Source Room drawer (4D). Capsule library (4G). AI command bar live. Deploy. Portfolio.
- **Risk + split rule.** High. If detail drawer grows past 8 sections wired, split drawer into 4C.1 (header + thesis + evidence) and 4C.2 (risk challenge + what changed + action footer).

### 30.4 Stage 4D — Evidence Shell + Source UX (current data) + Data Health Drawer

- **Purpose.** Land the Source Room and Data Health drawers, source-backed claim primitive, data-missing pill, and Composed-mark primitive. Reserve credibility-tier ladder, contradiction strip, and evidence-weak completeness panel as Coming-Later chrome.
- **Exact scope.**
  - **Source-Backed Claim** primitive (inline superscripts, hairline accent.lapis, max 3 or compressed `¹⁻⁵`).
  - **Source Room** drawer with the following:
    - **Live (from existing data):** ordinal · snippet · freshness dot · source name · "as of" date.
    - **Coming-Later chrome only:** 5-dot credibility ladder · contradiction strip · evidence completeness score.
  - **Data-Missing Pill** primitive (renders honestly whenever a claim has no sources).
  - **Composed-mark** primitive (used by any future AI-composed prose).
  - **Data Health Drawer** (per §26.11) with the following:
    - **Live (from existing data):** per-source freshness rollup · Intel v3 certification state (plain-English) · stale-source inventory · email-delivery dry-run state · next intelligence run.
    - **Coming-Later chrome only:** provider health (ok / near-limit / limited) · source-class freshness for filings / fundamentals / sentiment / analyst notes (until each worker class ships in Stage 5).
  - The **calm-caution panel** for the existing `evidence_band = OK / PARTIAL` is allowed live (coarse signal), but it must read *"Evidence quality is currently Working — the full evidence-completeness score will appear here when the next intelligence stage lights up."* It must not claim a completeness score it does not have.
- **Model recommendation.** **Sonnet.** Source UX is the moral spine; design judgment required.
- **New chat vs follow-up.** **New chat.**
- **Estimated usage.** **Medium-High.**
- **UI budget.** ~10 files.
- **Backend touch.** **No.** Consumes existing per-claim source data shape.
- **SQL.** **No.**
- **Test / validation plan.**
  - Snapshot tests for Source Room with 0 / 1 / 3 / 8 / 12 sources.
  - Contradiction strip rendering when at least two disagreeing sources exist.
  - Data-missing pill rendering when source list is empty.
  - aria-label tests for superscripts, freshness dots, credibility tiers.
- **Merge gate.** Plain-English UI Pack, Data Truth reviewer (verifies missing-data honesty), Accessibility reviewer.
- **Out of scope.** Source credibility registry as runtime (Stage 5B). Contradiction detection backend (Stage 5C). AI command bar live.
- **Risk + split rule.** Medium-High. If the Source Room becomes a separate page, split is needed — but the contract is "drawer, not page." Hold the line.

### 30.5 Stage 4E — Deploy Ledger Redesign

- **Purpose.** Land the Deploy editorial ledger, portfolio-shape explanation, cash discipline rendering, and "Why this dollar goes here" rationale — with no math changes.
- **Exact scope.**
  - **Deploy Instruction Row** primitive (per §15.6, on new tokens).
  - **Reserve Row** as first-class (always rendered when reserve > 0, with trigger condition named).
  - **Two-step Review → Confirm flow** redesigned (Review screen as editorial memo; Confirm with ceremonial 600 ms motion; mobile swipe-to-confirm).
  - **"How this decision changes your portfolio shape"** capsule mandatory on Review.
  - **"Patience is an action"** capsule on non-zero Reserve.
  - **Decision History sidebar** (last 5 Deploy executions).
  - **Decision-log history** below Step 3 preserved (Stage 2.6B+ contract intact).
- **Model recommendation.** **Sonnet.** Most consequential confirmation surface; design judgment required.
- **New chat vs follow-up.** **New chat.**
- **Estimated usage.** **High.**
- **UI budget.** ~10 files.
- **Backend touch.** **No.** Consumes Deploy v3 plan + decision log read-only.
- **SQL.** **No.**
- **Test / validation plan.**
  - Snapshot tests for Deploy ledger with 0 / 3 / 5 / 8 Buy rows.
  - Reserve-row rendering tests (with and without trigger condition).
  - Review / Confirm interaction tests.
  - Swipe-to-confirm gesture test on mobile.
  - "Portfolio shape" capsule deterministic content test.
- **Merge gate.** Plain-English UI Pack, Premium Delight reviewer, Policy Authority reviewer (verifies Deploy math untouched), Roadmap Guardian.
- **Out of scope.** Allocation-math changes. New providers. Real broker execution.
- **Risk + split rule.** High. If Review / Confirm ceremony grows past 600 ms motion or the swipe-to-confirm needs new gesture libs, split into 4E.1 (ledger + Review) and 4E.2 (Confirm ceremony + swipe).

### 30.6 Stage 4F — Portfolio Living Thesis Ledger

- **Purpose.** Land the editorial holdings ledger, concentration / exposure panels, and holding detail drawer using existing portfolio + Intel-decision data. Coming-Later chrome for capsules that depend on Stage 5 workers.
- **Exact scope.**
  - **Holdings ledger** (editorial table per §26.6).
  - **Concentration panel** (top 5 horizontal bar).
  - **Sector / theme exposure** panels (horizontal stacked bars) — render only what the existing portfolio data classifies. Missing theme segments render the Coming-Later state for that segment, not a fabricated tag.
  - **Thesis-health panel** (one-sentence summary + drill into tentative list).
  - **Source-freshness panel** (rollup + drill into stale list).
  - **Holding detail drawer** with the following:
    - **Live (from existing data):** header · thesis-health sparkline (built from existing decision history) · last 3 decisions · source freshness panel · stale-thesis warning.
    - **Coming-Later chrome only:** Business story · Company strategy primer · "What would make this thesis wrong" (red-team-artifact-backed version).
- **Model recommendation.** **Sonnet.** Component pass + drawer; design judgment required.
- **New chat vs follow-up.** **New chat.**
- **Estimated usage.** **Medium-High.**
- **UI budget.** ~10 files.
- **Backend touch.** **No.** Consumes portfolio snapshot + Intel v3 decisions read-only.
- **SQL.** **No.**
- **Test / validation plan.**
  - Ledger snapshot tests with varying holding counts.
  - Concentration / exposure panel tests.
  - Holding drawer interaction tests.
  - Stale-thesis warning tests.
- **Merge gate.** Plain-English UI Pack, Premium Delight reviewer, Accessibility reviewer.
- **Out of scope.** Real-time chart streaming. Performance attribution. New portfolio data fields.
- **Risk + split rule.** Medium-High. If exposure panels need new sector / theme classifications, defer those to a future stage and ship the panels with what exists.

### 30.7 Stage 4G — Alert Center Polish + Journal Chrome + Radar Destination + Buildable Capsules

- **Purpose.** Apply the design system to Alert Center; ship Journal as a real surface using existing decision history; reserve Radar as a destination with Coming-Later body; and ship the **subset of capsules that can be built deterministically from existing data**. Future-only capsules remain Coming-Later.
- **Exact scope.**
  - **Alert Center polish** — apply tokens / shell / candidate row redesign / dry-run banner restyle. Behavior unchanged from Stage 3G. Add deterministic "Why this matters" capsule per candidate (composed from existing severity + posture_reason + freshness).
  - **Journal page** redesigned: timeline with chapter numerals, entry anatomy from existing decision log (Deploy v3 + action-feedback events), Pending evaluation-window state.
  - **Journal Coming-Later chrome:** Lessons surface · "What I learned today" archive. Both render the calm caption *"This learning surface is being prepared. The next intelligence stage will surface it here."*
  - **Radar destination** at `/dashboard/radar` (or equivalent route) — chrome only. Honest empty state: *"Radar is being prepared. The next intelligence stage will surface opportunities here."* No mock candidates. No fake workflow chips populated with data.
  - **Buildable capsule library** (deterministic, current-data only):
    - **Why this matters** (already shipped in 4B Today; extended here to Alerts).
    - **Patience is an action** (used in Deploy 4E; integration-only here).
    - **Why Trim does not mean bad company** (deterministic, attached to every Trim row visible in Intel / Today).
    - **What missing data means** (deterministic, attached to every Data-Missing pill and every Tentative-evidence panel).
    - **How this decision changes your portfolio shape** (already shipped in 4E Deploy Review; reused here for any drawer that needs it).
  - **Future-only capsule slots remain Coming-Later** (Learn the concept · Business story · Company strategy primer · What would make this thesis wrong [artifact-backed] · Good company vs good stock · What I learned today). Stage 6 activates these.
- **Model recommendation.** **Sonnet.** Three surfaces + capsule subset; design judgment required.
- **New chat vs follow-up.** **New chat.**
- **Estimated usage.** **Medium-High.**
- **UI budget.** ~12 files (Alert Center restyle, Journal page, Radar destination, 5 buildable capsule components, types, tests).
- **Backend touch.** **No.** Reads only existing endpoints.
- **SQL.** **No.**
- **Test / validation plan.**
  - Deterministic-content tests for each buildable capsule (covers Trim / Hold / Buy / missing-data inputs).
  - Coming-Later chrome rendering tests (verifies no fabricated content).
  - Radar empty-state test.
  - Journal timeline tests with existing Deploy v3 decision-log rows.
  - Alert Center: dry-run banner remains; behavior unchanged.
- **Merge gate.** Plain-English UI Pack, Premium Delight reviewer, Data Truth reviewer (verifies capsules never invent), Roadmap Guardian.
- **Out of scope.** Real Radar candidates (Stage 5L). Pattern-detected Journal lessons (Stage 5K). "What I learned today" daily synthesis (Stage 5K). Live AI capsule composition (Stage 6). Real-send activation. New backend.
- **Risk + split rule.** Medium-High. If the buildable-capsule subset grows past 5 components, freeze the library; new capsule types go to a Stage 6 stage. If Alert Center polish + Journal + Radar destination + capsules exceeds 12 files, split into 4G.1 (Alert Center + capsules) and 4G.2 (Journal + Radar destination).

### 30.8 Stage 4H — Mobile Atelier + Motion Polish

- **Purpose.** Final mobile pass, motion polish, and accessibility audit.
- **Exact scope.**
  - **Bottom navigation** finalized (4 primary tabs: Today, Intel, Deploy, Portfolio; Alerts accessible from Today summary; Radar + Journal from Today secondary rail + `⌘K`).
  - **Bottom sheets** for all drawers (Intel detail, Source Room, Data Health, Holding detail, Compare).
  - **Card stack** with 12 px peek on Intel.
  - **Swipe-to-confirm** on Deploy Confirm.
  - **Sticky "what to do today" mini-bar** on Today.
  - **Reduced-motion pass** across every motion token.
  - **Accessibility audit pass** (focus rings, aria-labels, contrast, keyboard navigation).
  - **Final polish:** chapter numerals, engraved divider rules, tiny build label, page-corner timestamp, session signature mark on Today.
- **Model recommendation.** **Sonnet.** Polish + a11y judgment.
- **New chat vs follow-up.** **New chat.**
- **Estimated usage.** **Medium-High.**
- **UI budget.** ~12 files.
- **Backend touch.** **No.**
- **SQL.** **No.**
- **Test / validation plan.**
  - Mobile visual smoke at 320 / 375 / 414 / 768 widths.
  - `prefers-reduced-motion` verified across every motion token.
  - Keyboard nav full coverage (`Tab`, `[`, `]`, `?`, `Esc`, `⌘K`).
  - Lighthouse / axe a11y pass (target: 0 critical violations).
- **Merge gate.** Accessibility reviewer, Plain-English UI Pack, Premium Delight reviewer.
- **Out of scope.** Anything not specified above.
- **Risk + split rule.** Medium-High. If a11y violations exceed expected baseline, split a11y pass into 4H.1 (motion + nav) and 4H.2 (a11y audit + fixes).

### 30.9 Stage-spanning rules

- **No stage touches backend logic.** Every stage consumes existing endpoints read-only.
- **No stage applies SQL.** All Stage 4 PRs are SQL-free.
- **No stage activates email delivery.** Real-send activation is a separate, post-design stage.
- **Every stage carries the AI-usage note** in the PR summary (per `docs/ai/AI_USAGE_TRACKING.md`).
- **Every stage runs `python3 scripts/workflow/ai_pr_readiness_check.py`** before opening the PR.
- **Every stage updates `docs/ai/HANDOFF.md`** by replacing or summarizing — never appending.
- **After every Medium-High or High stage, stop.** Bring the PR to a fresh review session before proposing the next stage.
- **One coherent capability slice per stage.** Do not bundle 4B and 4C. Do not bundle 4E and 4F.

---

## 31. First Implementation Prompt Preview — Stage 4A

> **Do not implement.** This is a preview of what the Stage 4A Sonnet build prompt should contain. The actual prompt is generated later via `.claude/skills/ai-repo-os/SKILL.md` PR summary + the prompt-compression standard in CLAUDE.md.

### 31.1 Preview shape

The Stage 4A Sonnet prompt should be a single capability-slice prompt that:

- Names the safety pack: **Plain-English UI Pack + Backend-only Scaffold Pack (no visible-behavior-change variant)**.
- Names the build archetype: **UI Foundation / Token-and-Shell Pass**.
- Anchor files to read first (compact):
  - `artifacts/Design_Master_Plan/05_S_Grade_Execution_Contract.md` (§28.1, §28.4, §29, §30.1 only)
  - `artifacts/Design_Master_Plan/01_Principles_Identity_Motion.md` (§4, §5 only)
  - `v2/frontend/tailwind.config.ts`
  - `v2/frontend/src/app/globals.css`
  - `v2/frontend/src/app/dashboard/layout.tsx`
  - `v2/frontend/src/components/navigation/BottomNav.tsx`
- Exact scope: tokens + fonts + shell reset (per §30.1).
- Acceptance evidence: existing tests still pass; visual smoke on Today / Intel / Deploy / Portfolio / Alerts in dark + light; reduced-motion collapse verified.
- Stop condition: shell reset complete; **do not begin Stage 4B in the same session**. Stop after the PR is opened.
- Out of scope: page content restructuring, component redesign, AI surface, Source Room, capsules, backend calls, fabricated intelligence (any Stage 5 / 6 surface).
- Test tier: per `docs/ai/TEST_ROUTING.md` — frontend visual + snapshot tier; no backend tier needed.

### 31.2 Preview word budget

The Stage 4A prompt should target **< 900 words**, excluding the read-first anchor list. The OS v4 prompt-compression gate in CLAUDE.md applies.

### 31.3 What the Stage 4A prompt must not contain

- Repeated OS rules.
- The PR summary template.
- Exhaustive lists of skills or reviewer agents.
- Generic project invariants.
- Generic "do not" lists.
- Plans for Stage 4B–4H.
- Implementation guidance for any content beyond shell + tokens.

---

## 32. Reconciliation with the Existing Design Master Plan

This contract **extends** `00_README_and_Verdict.md` through `04_Mobile_QA_Sequencing_Narrative.md`. Where this contract and the older bible disagree, this contract wins for *what to build next*; the older bible remains canonical for *design vocabulary*.

| Older bible section | Status under this contract |
|---|---|
| §1 Executive Verdict | Preserved; extended in §22 with the intelligence-atelier framing. |
| §2 Competitive Teardown | Preserved; extended in §22.2 with Robinhood / Public / TradingView / M1 / Wealthfront / Copilot / Monarch / OpenBB / AI-chat synthesis. |
| §3 Product Design Principles | Preserved. All 15 rules remain canonical. |
| §4 Visual Identity System | Preserved. Stage 4A implements it. |
| §5 Motion and Interaction System | Preserved. Stage 4A wires the tokens; Stage 4H polishes. |
| §6 Information Architecture | **Superseded by §26.1.** Alerts is added as a top-level destination (Stage 3G already shipped). Source Room and Data Health are confirmed as drawers, not destinations. |
| §7 Today (Command Center) | **Extended by §26.3.** Adds Watchtower summary, What-I-learned-today capsule. |
| §8 Intel | **Extended by §26.4.** Adds Business story, Company strategy primer, What-would-make-this-thesis-wrong, Good-company-vs-good-stock, How-this-decision-changes-portfolio-shape capsules to the detail drawer. |
| §9 Deploy | **Extended by §26.5.** Adds How-this-decision-changes-portfolio-shape capsule (mandatory on Review). |
| §10 Portfolio | **Extended by §26.6.** Adds Business story + Company strategy primer + What-would-make-this-thesis-wrong capsules to holding drawer. |
| §11 Radar | **Extended by §26.8.** Honest empty state required until backend candidates exist. |
| §12 Journal | **Extended by §26.9.** Adds What-I-learned-today archive. |
| §13 Source and Evidence UX | Preserved; integrated in §26.10 and §30.4. |
| §14 AI Interaction Design | Preserved. AI command bar shell ships in Stage 4A; live AI lands later (not Stage 4). |
| §15 Component System | Preserved. Stage 4C–4G implement the components. |
| §16 Mobile | Preserved; Stage 4H is the final mobile pass. |
| §17 Accessibility | Preserved; Stage 4H audit verifies. |
| §18 Anti-Patterns Banned | Preserved; every Stage 4 PR must pass this list. |
| §19 Implementation Sequencing | **Superseded by §30 (Stage 4A–4H).** The older 15-PR sequence is retired in favor of 8 capability slices. The intent is preserved (small, scoped, reversible, no giant redesign PR). |
| §20 Design QA Checklist | Preserved; every Stage 4 PR runs this checklist. |
| §21 Final North-Star Narrative | Preserved as the aspirational read. |

### 32.1 What this contract adds that the older bible did not have

- **Intelligence-to-UI translation contract** (§24): the explicit mapping from backend intelligence domains to plain-English UI vocabulary, with anti-leak rules.
- **Beginner learning layer** (§25): the capsule library and integration rules, split into "buildable now" and "future-only."
- **Stage 4 / Stage 5 / Stage 6 sequencing split** (§28): the controlling rule for what gets built when, with the **Coming-Later Pattern** for honest unavailable states.
- **Module-by-module build-now vs. wait matrix** (§28.8): the single most important reviewer checklist for Stage 4 PRs.
- **Stage 4 build sequence** (§30): eight capability slices using only existing data, each with explicit Live / Coming-Later splits.
- **Source Room and Data Health as global drawers** (§26.10, §26.11) rather than destinations.
- **Alert Center final design role** (§27) preserving top-level placement and dry-run safety.

---

## 33. Hard Stop Conditions for Stage 4

This contract is bold; the implementation must remain safe. The following stop conditions apply to every Stage 4 PR:

- **Stop if any PR touches Intel v3 / Deploy v3 / Watchtower decision authority.**
- **Stop if any PR proposes SQL.**
- **Stop if any PR proposes real-send email activation.**
- **Stop if any PR proposes provider changes.**
- **Stop if any PR proposes LLM call changes that affect visible decisions.**
- **Stop if any PR fabricates a Stage 5 / 6 intelligence module** (filing analysis, technical analysis, fundamental synthesis beyond the existing valuation bridge, sentiment, source credibility tiers as authority, contradiction detection, evidence completeness, company strategy primers, pattern-detected lessons, Radar candidates, live AI composition). Any such surface must use the Coming-Later Pattern instead.
- **Stop if any PR ships a capsule** that is not on the buildable-now list in §28.8.
- **Stop if any PR exceeds its UI budget by > 25%.**
- **Stop if any PR bundles two stages.**
- **Stop and propose a split** if the durable fix exceeds the current capability slice (per CLAUDE.md "Capability slice over micro-patch").
- **Stop after every Medium-High or High stage** and bring the PR to a fresh review session before proposing the next stage.

### 33.1 Coming-Later compliance gate

For every Stage 4 PR, the reviewer must walk the §28.8 module table for the surfaces touched and verify, line by line, that:

- Every "Live" module is wired to existing data only.
- Every "Coming-Later" module renders the chrome with the calm Coming-Later caption — never with mock or fabricated content.
- No new module appears in the diff that is not in §28.8.

If any of the above fails, the PR is blocked.

---

## 34. Closing — What "S-Grade" Means

S-grade does not mean prettier. It does not mean more features. It does not mean a chatbot.

S-grade, for this product, means:

- The backend is honest about what it knows and what it does not.
- The frontend translates that honesty into reading order, vocabulary, and learning.
- The user understands more about investing every week.
- The system never invents.
- The user never wonders if a number was real.
- The portfolio is a living thesis, not a list of tickers.
- Cash discipline is a first-class decision.
- Risk is visible, not buried.
- Confidence is earned, never theatrical.
- One human can read the brief, see the evidence, consider the risk, learn one concept, make one deliberate decision, and record it for the future self to learn from — in under two minutes.

That is the bar.

Stage 4A is the first stone. Stage 4H is the last polish. Everything in between is a deliberate, reversible, beautifully boring capability slice.

---

*End of S-Grade Execution Contract. The Design Master Plan (Parts 1–5) remains canonical for vocabulary, principles, tokens, motion, and page intent. This Part 6 is the implementation contract for the next phase.*
