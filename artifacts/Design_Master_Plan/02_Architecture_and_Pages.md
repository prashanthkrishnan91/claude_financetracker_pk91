# Design Master Plan — Part 3
## Information Architecture and Page Designs

---

## 6. Information Architecture

### 6.1 Top-level navigation

Six primary destinations. One settings/data-health corner. **No "More" menu**, no hidden hamburger items. If something is not in the six, it does not deserve top-level navigation.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ◊  Atelier        Today   Intel   Deploy   Portfolio   Radar   Journal │
│                                                          ⌘K     ◐  ⚙   │
└──────────────────────────────────────────────────────────────────────────┘
```

| Position | Surface | One-line job |
|---|---|---|
| 1 | **Today** (Command Center) | What changed, what to act on, what to ignore. |
| 2 | **Intel** | The investment-committee view: Buy/Hold/Trim/Sell across watched tickers. |
| 3 | **Deploy** | Allocation of the next deposit, with rationale. |
| 4 | **Portfolio** | Holdings as a living document — concentration, exposure, thesis health. |
| 5 | **Radar** | Opportunity discovery — companies not yet owned. |
| 6 | **Journal** | Decision history and learning loop. |
| corner | `⌘K` | Ambient AI + global search + jump. |
| corner | Data-health dot | One-glance backend / source freshness state. |
| corner | Settings | Account, sources, brokerage links, preferences. |

### 6.2 Navigation principles

- **Today is always one tap away.** Even from the deepest drawer.
- **Active section reads as engraved**, not highlighted — a 1 pt accent rule under the label, no pill.
- **The data-health dot** turns from `quiet` (default) to `watch` (one stale source) to `acute` (multiple sources stale or backend errors). It is the one persistent risk affordance.
- **No badges with numbers** on top-level nav. Notifications live in the Today rail, not the chrome.

### 6.3 Page-by-page architecture summary

For each page below, the structure is described as:
- **Purpose** — the one question this page answers.
- **Primary user question** — the literal sentence the page must answer in the first three seconds.
- **Key modules** — the components that compose the page.
- **Above the fold** — what must be visible without scrolling.
- **Hidden behind drill-down** — what lives in drawers, sheets, modals.
- **Never appears here** — explicit anti-list.
- **Connections** — how this page links to Intel and Deploy.

---

## 7. Today — Command Center

> **The morning brief.** Open the app. Read the first paragraph. Decide.

### 7.1 Purpose

Answer the user's morning question in 30 seconds: *what changed, what deserves attention, what is the next best action?*

### 7.2 Primary user question

> *"Should I do anything different today than yesterday?"*

### 7.3 Above the fold

The above-the-fold layout is a **two-column editorial spread**, not a tile grid.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Tuesday · 7 May 2026 · 06:42 ET · NYSE pre-open                          │
│                                                                            │
│  THE BRIEF                                                                 │
│                                                                            │
│  Three thesis updates since yesterday's close. One acute risk on RIVN.    │
│  The portfolio is up 0.4% in pre-market on broad strength. Cash to        │
│  deploy: $1,240. The next intelligence run completes at 07:00 ET.         │
│                                                                            │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                            │
│  ▲ ACT TODAY                                  ◐  RISK PULSE                │
│                                                                            │
│  • NVDA — Buy candidate (3 sources, fresh)    RIVN  Acute                 │
│  • SCHD — Add to position (DCA target)        BTC   Elevated              │
│  • RIVN — Trim consideration (risk elevated)  KLAR  Elevated              │
│                                                                            │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                            │
│  ◆ DEPLOY READY                              ❖ NEW SINCE YOU WERE AWAY    │
│                                                                            │
│  $1,240 deployable                            3 thesis updates             │
│  Suggested split: NVDA $620 · SCHD $400 ·    1 new Radar candidate        │
│  Reserve $220 (cash discipline)               1 source freshness drop      │
│                                                                            │
│  [Review Deploy plan →]                       [Open intelligence feed →]   │
│                                                                            │
└──────────────────────────────────────────────────────────────────────────┘
```

### 7.4 Key modules

| Module | Job |
|---|---|
| **The Brief** | A 3–4 sentence deterministic prose paragraph composed by backend (no LLM). Reads like a private letter. |
| **Act Today** | The single highest-confidence Buy / Trim / Sell candidates — capped at 5. Each row is one click into Intel detail. |
| **Risk Pulse** | The 1–4 tickers currently in Elevated or Acute risk tier. Always visible if any exist; absent if none. |
| **Deploy Ready** | Cash deployable + the system's suggested allocation. Single button to Review Deploy plan. |
| **New Since You Were Away** | A small chronological feed of intelligence events since last visit (counts only above the fold; expand below). |
| **Yesterday → Today strip** | A bottom-of-fold horizontal strip: portfolio change in dollars, %, and one editorial sentence ("Largest contribution: NVDA +1.8%."). |

### 7.5 Below the fold

- Full intelligence feed (chronological).
- Source-freshness summary (how many sources fresh, watch, stale).
- Decision Journal preview — last 3 entries.
- Market context strip (S&P, NASDAQ, BTC, 10Y) — caption-sized, never the hero.

### 7.6 Never appears on Today

- Order entry. (This is not a brokerage.)
- Streaks, badges, "you've checked the app 47 days in a row."
- "Top movers" (we curate signal, we do not report the loudest noise).
- KPI tiles. (We have a brief instead.)
- Charts above the fold. (Charts live on Portfolio and ticker drilldowns.)
- Famous-investor quotes.
- News headlines — except when the system itself ingested them as evidence (in which case they appear linked from the Brief, not as a feed).

### 7.7 Connections to Intel and Deploy

- Every Act Today row deep-links to that ticker's Intel detail drawer.
- Deploy Ready summarizes Deploy without making decisions on Deploy's behalf.
- The Brief's claims about thesis updates link to the source drawer for the relevant ticker.

---

## 8. Intel

> **The investment committee.** What does the system think about each ticker, and why?

### 8.1 Purpose

Render the deterministic Buy / Hold / Trim / Sell verdict for every watched ticker, with thesis, sources, and risk — in a way that respects the existing recommendation engine and posture model already built (`_derive_intel_posture`, `posture_reason`, the BHTS display contract from PR #128 and the simplification PR after it).

### 8.2 Primary user question

> *"Across everything I follow, what does the system want me to do — and is it right?"*

### 8.3 Layout

A **three-pane workspace**, but each pane reads as a chapter — not as a floating tool.

```
┌────────────┬─────────────────────────────────────┬──────────────────────┐
│  FILTER    │  CARD GRID                           │  DETAIL DRAWER       │
│            │                                      │  (slides in on click) │
│  All  34   │  ┌────────┐ ┌────────┐ ┌────────┐    │                       │
│  Buy   8   │  │ NVDA   │ │ SCHD   │ │ RIVN   │    │  Ticker · Action      │
│  Hold 14   │  │ Buy ▲  │ │ Buy ▲  │ │ Trim ◆ │    │  Confidence ●●●○○     │
│  Trim  4   │  │ thesis │ │ thesis │ │ thesis │    │                       │
│  Sell  2   │  │ ●●●●○  │ │ ●●○○○  │ │ ●●●●●  │    │  Why this view?       │
│            │  └────────┘ └────────┘ └────────┘    │  ───────────────      │
│  Watchlist │                                      │  Plain-English thesis │
│       6    │  ┌────────┐ ┌────────┐ ┌────────┐    │                       │
│            │  │ AAPL   │ │ MSFT   │ │ XRP    │    │  Evidence (3 sources) │
│  Sort by   │  │ Hold ■ │ │ Hold ■ │ │ Hold ■ │    │  1. ...               │
│  ────────  │  │ thesis │ │ thesis │ │ thesis │    │  2. ...               │
│  Conviction│  │ ●●●○○  │ │ ●●●●○  │ │ ●○○○○  │    │  3. ...               │
│  Recency   │  └────────┘ └────────┘ └────────┘    │                       │
│  Risk      │                                      │  Risk challenge       │
│            │                                      │                       │
│  Data      │                                      │  What changed         │
│  health ●  │                                      │                       │
│            │                                      │  [Ask why] [Compare] │
└────────────┴─────────────────────────────────────┴──────────────────────┘
```

### 8.4 Filter rail (left)

- **All / Buy / Hold / Trim / Sell** with counts. **Watchlist** is a separate auxiliary state below the divider — it does not compete with the Buy/Hold/Trim/Sell semantic.
- Sort options: Conviction, Recency of update, Risk tier.
- A **data-health micro panel** at the bottom: count of fresh / watch / stale sources across the Intel set, with one-click drill into source freshness.

### 8.5 Card grid (center)

Each card is the **Action Card** component (defined in `03_Sources_AI_Components.md`):
- Ticker (display serif, 22 px) + small price meta.
- Action chip (Buy / Hold / Trim / Sell) — always one of four, never a posture label.
- Plain-English thesis: max 3 lines.
- Confidence ring (5-step ladder).
- Source freshness dot (green / amber / grey).
- "Why this view?" affordance — a hairline link to the detail drawer.
- "What changed since last time" pill if applicable.

Cards do not bounce on hover. Border lifts to `border.strong`. The selected card's border becomes accent.lapis.

### 8.6 Detail drawer (right)

When a card is clicked, a **right-side drawer** slides in occupying 40% of viewport width on desktop, full-screen sheet on mobile.

Drawer sections, in order:
1. **Header** — ticker, action chip, confidence ring, "as of" timestamp.
2. **Why this view?** — `posture_reason` text, plain English (already produced by `build_posture_reason()`).
3. **Plain-English thesis** — composed prose, maximum 6 lines.
4. **Evidence trail** — numbered sources with snippet, freshness dot, credibility tier.
5. **Risk challenge** — the system's own counter-thesis ("If we are wrong, this is how it breaks").
6. **What changed** — diff vs. previous run if applicable; absent if first run.
7. **Action footer** — `[Ask why]` `[Challenge]` `[Compare]` chips that summon the AI layer.

### 8.7 Watchlist state

Watchlist is **not** a competing label with Buy/Hold/Trim/Sell. It is a separate workflow state for *tickers under research with insufficient evidence to take a side yet*. Visually distinct: dotted border, no action chip, only a "researching" eyebrow.

### 8.8 Never appears on Intel

- Raw metric keys (`pe_ratio_ttm`, `_compute_insight_cards` internals).
- Business Read.
- Giant stock report panels.
- Price targets.
- Famous-investor cosplay.
- "Top picks of the week."
- Author bylines, podcast cards, video embeds.
- Social sentiment scores presented as primary truth.

### 8.9 Backend contract preserved

The visible action model stays exactly **Buy / Hold / Trim / Sell**, normalized through the existing `normalizeDisplayAction` helper. Posture buckets (`Add Candidate`, `Risk Watch`, etc.) remain in the data model for backend use, but **never render as filter tabs or card badges**. This bible does not change that contract.

---

## 9. Deploy

> **The execution layer.** Convert intelligence into a deposit allocation, with confidence and clarity.

### 9.1 Purpose

Show the user how to allocate the next deposit (or available cash) given the current Intel verdicts. Deploy v2 logic is stable — this is **visual clarity only**.

### 9.2 Primary user question

> *"My deposit is in. Where does it go, and why?"*

### 9.3 Above the fold

A **single-column ledger** that reads like an investment memo.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Tuesday · 7 May 2026 · 06:42 ET                                          │
│                                                                            │
│  DEPLOY                                                                    │
│                                                                            │
│  $1,240 deployable today                                                   │
│  $220 reserved (cash discipline — 18% of pool)                             │
│                                                                            │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                            │
│  Suggested allocation                                                      │
│                                                                            │
│  NVDA   ▲ Buy   $620    50%   ▌▌▌▌▌▌▌▌▌▌                                  │
│         "Three fresh sources support thesis. Conviction Strong."           │
│         [Open Intel detail →]                                              │
│                                                                            │
│  SCHD   ▲ Buy   $400    32%   ▌▌▌▌▌▌                                      │
│         "DCA target. Yield discipline preserved."                          │
│         [Open Intel detail →]                                              │
│                                                                            │
│  Reserve         $220    18%   ▌▌▌                                        │
│         "Held for opportunity. Trigger: any Acute risk drops to Elevated." │
│                                                                            │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                            │
│  [Review →]                                                                │
│                                                                            │
└──────────────────────────────────────────────────────────────────────────┘
```

### 9.4 Allocation ledger

Each row contains:
- Ticker, action chip (Buy only — Deploy does not deploy Trim/Sell), dollar amount, percent of pool, and a thin allocation bar.
- A one-line rationale connecting back to Intel.
- A link into the relevant Intel detail drawer.

The **Reserve** row is treated as a first-class entry, never as remainder. If cash is reserved, the trigger condition is named.

### 9.5 Confirmation flow

Two-step deliberate flow:
1. **Review** — compose a Deploy memo: a single-page summary of all rows with a "what happens next" footer.
2. **Confirm** — ceremonial 600 ms motion: blueprint hairlines draw from each Intel verdict to its allocation row, then a "stamped" affirmation appears top-right.

Cancel is always one click away in Review. Confirm cannot fire on hover, double-click, or keypress without focus on the Confirm button.

### 9.6 Decision history sidebar

A right-rail showing the last 5 Deploy executions: date, total amount, rows, and outcome timestamp. Click for full record (jumps to Journal).

### 9.7 Hidden behind drill-down

- Per-row allocation math derivation (linked from each row, opens a small sheet).
- Cash-discipline rules in effect.
- Historical reserve triggers and outcomes.

### 9.8 Never appears on Deploy

- Order routing UI. (This is allocation guidance, not order entry.)
- Sliders that let the user "tweak" allocations and override Intel — Deploy presents the deterministic plan; manual override is a separate, intentional act handled via a dedicated "manual override" affordance, never as a primary slider.
- "What if" simulators presenting fake projections.
- Fees-and-taxes calculators dressed as advice.

### 9.9 Logic untouched

This bible **does not propose** changes to Deploy's allocation math, reserve logic, or trigger conditions. Visual changes only.

---

## 10. Portfolio

> **The living document of what you own.** Reading the portfolio should feel like reading the index of a private collection — calm, honest, and current.

### 10.1 Purpose

Show holdings, concentration, exposure, and thesis health in one calm surface. Not a brokerage trading screen.

### 10.2 Primary user question

> *"Is my portfolio still consistent with what the system believes?"*

### 10.3 Layout

A two-column layout: holdings ledger on the left, exposure and thesis-health summary on the right.

### 10.4 Holdings ledger (left, 60% width)

A typeset table — editorial, not spreadsheet:

| Ticker | Allocation | Cost basis | Today | Thesis | Source freshness |
|---|---|---|---|---|---|
| NVDA | 18.4% | $112,400 | +1.8% | Buy ▲ Strong | ● fresh |
| SCHD | 12.1% | $74,200 | +0.2% | Buy ▲ Working | ● fresh |
| AAPL | 9.8% | $60,100 | -0.3% | Hold ■ Working | ● fresh |
| RIVN | 4.2% | $25,700 | -2.1% | Trim ◆ Strong | ◐ watch |
| ... |

- Tabular nums everywhere.
- Negative numbers in oxblood, with a leading minus, never with parentheses *and* minus.
- Thesis column shows the action chip + confidence tier label.
- Source freshness column shows a single dot — green/amber/grey — never a percentage.

### 10.5 Right column

Stacked panels:

1. **Concentration** — top 5 holdings as a horizontal bar with names; "concentration risk: Working" label.
2. **Exposure by sector** — single horizontal stacked bar with up to 6 sector segments; legend below.
3. **Exposure by theme** — same treatment, themes (e.g., "AI infrastructure", "consumer staples", "speculative crypto") as defined backend-side.
4. **Thesis health** — single sentence: *"32 of 34 holdings have a Working or Strong thesis. 2 have Tentative thesis health."* Click drills into a sheet listing the tentative ones.
5. **Source freshness** — *"96% of holdings have at least one source updated in the last 24 hours."* Click drills into a sheet of stale ones.

### 10.6 Holding detail drawer

Click any ticker → right-side drawer:
- Header (ticker, current value, today's change, action chip).
- Thesis health timeline (sparkline, last 30 days, line color = action through time).
- Last 3 decisions on this ticker (linked to Journal).
- Source freshness panel (per-source dot list).
- Stale-thesis warning if applicable: calm caution, *"Thesis hasn't been refreshed since 4 Apr — the next intelligence run will re-evaluate."*

### 10.7 Never appears on Portfolio

- Buy / Sell order buttons. (No execution here.)
- Real-time chart streaming (we are a tracker, not a terminal).
- Performance attribution graphs that are not source-backed.
- "Beat the market by X%" framing.

### 10.8 Connections

- Every holding row links to its Intel detail drawer.
- Stale-thesis warnings link to the next intelligence run schedule on Today.

---

## 11. Radar — Opportunity Discovery

> **The hunting ground.** Companies the user does not yet own, surfaced with discipline.

### 11.1 Purpose

Surface candidate opportunities — companies not currently held — with the same evidence rigor as Intel. Never with the same labels.

### 11.2 Primary user question

> *"Is there something I should be looking at that I am not already?"*

### 11.3 Discovery feed

A vertical feed of **Opportunity Cards** — visually distinct from Intel cards (slightly elevated `bg.surface.elevated`, plum 1 pt accent border, no Buy/Hold/Trim/Sell chip).

Each card carries:
- Ticker + company name (display serif).
- A "Why now" headline (plain English, max 12 words).
- 2-3 source-backed reasons (numbered, each with a freshness dot).
- A "Compare vs. current holdings" affordance that opens a side-by-side sheet.
- Risk red flags if any (calm caution, never alarm).
- A workflow chip: **`Add to research`** or **`Promote to Buy candidate`** (the latter only available when the system has crossed an evidence threshold).

### 11.4 Workflow states (separated from Intel labels)

Radar uses **workflow states**, not BHTS. This avoids any conflict with the Intel display contract:

| State | Visual | Meaning |
|---|---|---|
| `Surfaced` | plum dot | System has surfaced this candidate; no user action yet. |
| `Researching` | plum dot, dotted ring | User has added it to research. |
| `Promoted` | atelier-green dot | User has promoted it to Buy candidate; now it appears in Intel as a Watchlist item until the next intelligence run upgrades it to Buy/Hold/Trim/Sell. |
| `Dismissed` | grey dot | User has dismissed it; remains in archive for 90 days. |

These are **workflow states**, never confused with action verdicts.

### 11.5 "Not enough evidence yet" state

If a candidate's evidence threshold is not met for promotion, the card explicitly says so: *"Not enough evidence yet. Two more reliable sources are needed before this can be promoted."* The state is not a failure — it is honest.

### 11.6 Compare drawer

When a Radar candidate is opened, the drawer shows a **side-by-side comparison** with the closest current holding (by sector, theme, or correlation as defined backend-side). Three columns: Candidate, Closest holding, Delta. Always source-linked.

### 11.7 Never appears on Radar

- "Trending tickers" lists.
- Reddit / Twitter / Discord sentiment.
- Famous-investor portfolio mirroring.
- Fake price targets.
- Crystal-ball language: "could 10x", "next NVDA."

### 11.8 Connections

Promoting a candidate writes a Watchlist entry into the Intel data model. Until the next intelligence run produces a Buy/Hold/Trim/Sell verdict, the ticker shows on Intel only as a Watchlist auxiliary item.

---

## 12. Journal — Decision History

> **The learning loop.** What did the system recommend, what did the user do, and what happened next?

### 12.1 Purpose

A long-term record of decisions and their outcomes. The journal makes the user's own track record legible. It also makes the system's own track record legible — including failures.

### 12.2 Primary user question

> *"Was the system right last time? Was I?"*

### 12.3 Layout

A vertical timeline grouped by month, with chapter numerals (`I`, `II`, `III`...) at each month break.

```
                 Tuesday · 7 May 2026

                 ▲ NVDA   Buy ($620)      Confirmed
                 System: Buy · Strong conviction · 3 sources fresh
                 You: Followed
                 Outcome: Pending — evaluation window 47 days

                 ◆ RIVN   Trim ($800)     Confirmed
                 System: Trim · Strong conviction · risk Elevated
                 You: Deferred (held position)
                 Outcome: Pending — evaluation window 30 days
                 Note: thesis weakened, source freshness Watch.

         ─────── chapter II — May 2026 ───────

                 Friday · 3 May 2026
                 ▼ XRP    Sell ($1,200)   Confirmed
                 ...
```

### 12.4 Entry anatomy

Each Journal entry has six fields:
1. **Recommendation** — what the system said (action, conviction, source count).
2. **Action taken** — what the user did (Followed / Deferred / Overrode).
3. **Outcome** — Pending (with evaluation window), Confirmed (system was right), Mixed, Wrong, or Inconclusive.
4. **Thesis health change** — Improved / Stable / Deteriorated.
5. **Source freshness change** — Fresh / Stale / Unchanged.
6. **Lesson** (optional) — a one-sentence editorial note generated when patterns emerge across multiple entries.

### 12.5 Pending evaluation windows

A decision is **never** marked Confirmed/Wrong instantly. Each ticker has a deterministic evaluation window (e.g., 30 days for Trim, 60 days for Buy). Until the window matures, the entry shows `Pending — evaluation window N days`. **No fake performance metrics.**

### 12.6 Lessons surface

A separate panel: *"Lessons learned"* — only renders when the system has identified a recurring pattern (e.g., "Trim recommendations under Acute risk have been right 4 of 5 times in the last 90 days"). Patterns are deterministic, never LLM-generated.

### 12.7 Never appears on Journal

- "You've made $X this year!" — performance theater.
- Comparisons to S&P that are not source-cited.
- Streak counters.
- Awards or trophies.
- Ranked leaderboards of any kind.

### 12.8 Connections

Every Journal entry deep-links to:
- The Intel detail drawer at the time of the decision (snapshot).
- The Deploy execution that committed the decision.
- The relevant Portfolio holding's current state.

---

*Continued in `03_Sources_AI_Components.md`.*
