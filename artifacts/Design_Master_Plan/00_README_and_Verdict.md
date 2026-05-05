# Design Master Plan — Finance Tracker v2/v3
## The Quiet Atelier — Design Bible

> *"Beauty without logic and logic without beauty are equally bad."*

This is the long-term design bible for the Investing Intelligence app (Intel + Deploy + Portfolio + Radar + Journal). It is **planning and specification only**. It does not propose code, schemas, or recommendation-logic changes. It is meant to be referenced by future implementation PRs once Intel v3 foundation is stable and the timing rule in `docs/ai/DESIGN_VISION.md` is satisfied.

The bible is split into 5 reference files inside `artifacts/Design_Master_Plan/`:

| # | File | Sections covered |
|---|---|---|
| 00 | `00_README_and_Verdict.md` | Executive verdict, competitive teardown |
| 01 | `01_Principles_Identity_Motion.md` | Design principles, visual identity, motion |
| 02 | `02_Architecture_and_Pages.md` | Information architecture, Command Center, Intel, Deploy, Portfolio, Radar, Journal |
| 03 | `03_Sources_AI_Components.md` | Source/evidence UX, AI interaction, component system |
| 04 | `04_Mobile_QA_Sequencing_Narrative.md` | Mobile, accessibility, anti-patterns, sequencing, QA, north-star narrative |

---

## 1. Executive Design Verdict

### The direction has a name: **The Quiet Atelier**

A **private investment atelier**: the calm of a wealth-management office, the density of an analyst desk, the readability of a luxury editorial publication, and the live pulse of a command center — fused into a single, dark-first interface that respects the reader as a serious thinker, not a retail trader to be entertained.

It is not a dashboard. It is a **morning letter that updates itself**.

### Why this beats every category we studied

| Reference | What they do best | Why The Quiet Atelier wins for *this* product |
|---|---|---|
| **Robinhood** | Frictionless one-tap action | Confuses entertainment with confidence; gamification erodes trust. We keep the simplicity, drop the dopamine. |
| **Robinhood Legend** | Multi-pane charting density | Cluttered, no narrative, no decision guidance. We keep selective density behind progressive disclosure. |
| **Bloomberg Terminal** | Information per square inch | Brutalist, hostile to non-experts, 1990s muscle memory. We keep the density discipline and rebuild it as legible editorial. |
| **Monarch / Copilot** | Calm typography, soft palettes | Family-budget skew, no analyst depth. We borrow the calm; we add a real intelligence layer. |
| **Wealthfront** | Clarity, low-pressure tone | Advisor-paternal, pre-decided for you. We provide guidance without paternalism, with sources. |
| **Public** | Storytelling around tickers | Influencer feed, social cosplay. We keep the narrative spine; we cut the social theater. |
| **Seeking Alpha** | Depth of evidence | Cluttered UI, paywall walls, opinion roulette. We borrow the source depth and re-author it with composition discipline. |
| **OpenBB / analyst workspaces** | Open data, multi-pane analysis | Engineer-skewed, IDE feel. We borrow the data orientation; we throw away the IDE. |
| **Premium editorial (NYT, FT, Monocle, The Browser Co.)** | Typographic confidence, hierarchy, restraint | Not transactional or live. We graft live data onto an editorial spine. |
| **AI-native (Linear, Arc, Raycast, Notion AI)** | Ambient AI, keyboard-first, restrained surfaces | Built for prose/projects, not source-anchored finance. We borrow ambient AI and add source discipline. |

### What this product actually is

Internally it is an **investment committee + opportunity radar**. The backend debates, scores, validates sources, challenges itself, and produces deterministic guidance. The frontend's job is the opposite of "more features": its job is to **let one human read the committee's decisions in the time it takes to drink a cup of coffee** — and act with confidence that the math underneath has not been faked.

### The single design promise

> Within 30 seconds of opening the app, the user should know:
> 1. What changed in the portfolio overnight.
> 2. What deserves attention.
> 3. What action to take (Buy, Hold, Trim, Sell).
> 4. Why the system thinks that.
> 5. What evidence supports it.
> 6. What risks or missing data weaken confidence.
> 7. How Deploy should act on the next deposit.

Every page, every component, every motion in this bible exists to keep that 30-second promise.

### What The Quiet Atelier deliberately is **not**

- Not a colorful KPI dashboard.
- Not a brokerage trading screen.
- Not a Robinhood clone.
- Not a Bloomberg clone.
- Not a SaaS admin panel.
- Not a social investing feed.
- Not a "famous-investor cosplay" UI.
- Not a chatbot wrapper around stock data.
- Not a generic `shadcn` card layout reskinned with green text.

---

## 2. Competitive Teardown

For each reference, in three columns: **Borrow** (good ideas worth stealing), **Reject** (anti-patterns we will not repeat), **Improve** (where we can do meaningfully better).

### 2.1 Robinhood

| Borrow | Reject | Improve |
|---|---|---|
| One primary action per ticker view. Clean ticker stream. Big-number-first hierarchy. Calm whitespace on holding pages. | Confetti, gamification, streaks, "rounds up" cuteness. Hidden complexity that erases risk. Loud red/green theatrics. The "Lists" social tab. | Replace one-tap action with one-tap action *plus* a one-line plain-English thesis. Replace celebratory micro-interactions with quiet confirmation. Replace "Top Movers" with "What changed that matters." |

### 2.2 Robinhood Legend

| Borrow | Reject | Improve |
|---|---|---|
| Multi-pane workspace, dark editorial chrome, dense numerical tables, chart-as-canvas. | Side-panel clutter. Power-user-only ergonomics. Lack of decision guidance. No narrative around the numbers. | Reframe panes as **chapters of one document**, not floating tools. Each pane must answer a single question. No pane is allowed to exist if you cannot describe its question in one sentence. |

### 2.3 Public

| Borrow | Reject | Improve |
|---|---|---|
| Story-around-each-ticker. Calm card density. Treating finance as something narratable. | Influencer feed, "follow this user's portfolio," social cosplay, talking-head videos. | Story is told by the *system's own evidence trail*, not by personalities. Every narrative claim links to a verifiable source. |

### 2.4 Monarch Money

| Borrow | Reject | Improve |
|---|---|---|
| Quiet palette, generous typography, calm hierarchy, gentle empty states. | Family-budget mental model. "Spending" rituals. Generic green-blue chart language. | Same calm, but **with risk and uncertainty visible** instead of softened. Calm should never look like everything is fine. |

### 2.5 Copilot Money

| Borrow | Reject | Improve |
|---|---|---|
| Tactile micro-motion, gestural drawers, beautifully tuned animations. Soft haptics on iOS. | Slightly playful copy. Consumer-spending skew. Round, illustrative iconography out of place for serious capital. | Same motion vocabulary, drier copy, sharper iconography. Motion **explains intelligence**, never decorates a flick. |

### 2.6 Wealthfront

| Borrow | Reject | Improve |
|---|---|---|
| Clarity around fees, allocations, and outcomes. Calm low-pressure tone. Honest projection ranges. | Advisor paternalism ("we've decided for you"). Heavy reliance on illustrative graphics that imply false precision. | Show the *system's* working: every recommendation has a source trail and a confidence band. The user is co-pilot, not passenger. |

### 2.7 Seeking Alpha

| Borrow | Reject | Improve |
|---|---|---|
| Depth of evidence, willingness to publish a contrarian view, source taxonomy. | Pop-up paywalls, ad density, opinion roulette, no synthesis. | Synthesize multiple sources into one composed thesis. Show contradictions between sources as a feature, not noise. |

### 2.8 Bloomberg Terminal

| Borrow | Reject | Improve |
|---|---|---|
| Density per square inch. The discipline that *every* glyph carries information. The keyboard-first power layer. | Brutalist 1990s aesthetics. Hostile to anyone outside the trading floor. No progressive disclosure. Color-blind unsafe by default. | Keep the density discipline; render it in editorial typography with progressive disclosure. Keep the keyboard layer (`⌘K`); rewrite it for plain English. |

### 2.9 OpenBB / Analyst Workspaces

| Borrow | Reject | Improve |
|---|---|---|
| Multi-source data orientation, willingness to surface raw analyst-grade structure, scriptable workflows. | IDE-skinned UI, dev-only ergonomics, lack of narrative. | Treat the data layer as an *editorial source room* — analyst-grade behind the scenes, plain English on the page. |

### 2.10 Premium Editorial / Luxury (NYT, FT Weekend, Monocle, The Browser Company)

| Borrow | Reject | Improve |
|---|---|---|
| Typographic system. Engraved rules. Restrained color. Section numerals. Confident whitespace. The reader is treated as intelligent. | Static. No live data. No transactional spine. | Graft live, deterministic data onto the editorial spine. The result is an editorial product *that updates itself*. |

### 2.11 AI-Native Apps (Linear, Notion AI, Arc, Raycast, Granola)

| Borrow | Reject | Improve |
|---|---|---|
| Ambient AI surfaces. Keyboard-first command bar. AI as utility, not personality. Inline action chips ("explain", "rewrite"). | Floating chat bubble. Avatar with a name. Over-reliance on conversation when a structured object is better. AI inventing facts. | AI never invents numbers, scores, allocations, or sources. AI explains, challenges, compares, summarizes — and is **labeled** when it does. |

### 2.12 Cross-cut synthesis — what we are stealing from each, what we will not

**We are stealing**: editorial typography (NYT/FT), dark graphite density (Bloomberg), tactile motion (Copilot), source taxonomy (Seeking Alpha), keyboard-first ambient AI (Linear/Raycast), calm palette (Monarch), and one-decision-per-screen discipline (Robinhood).

**We are not building**: confetti, streaks, badges, social feeds, illustrated mascots, neon glow, glassmorphism overload, generic shadcn cards, advisor paternalism, gamified deposits, raw metric keys in UI, fake price targets, fake confidence percentages, or famous-investor cosplay.

---

*Continued in `01_Principles_Identity_Motion.md`.*
