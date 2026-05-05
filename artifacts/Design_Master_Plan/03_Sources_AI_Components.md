# Design Master Plan — Part 4
## Source/Evidence UX, AI Interaction, Component System

---

## 13. Source and Evidence UX

Source rendering is the moral spine of the product. Every claim that can be sourced must be sourced. Every source must be inspectable. Every source has freshness. Every source has credibility. Contradictions between sources are a feature, not noise.

### 13.1 Inline evidence

Inside any claim text, a source citation appears as a **hairline superscript number** (`¹`, `²`, `³`) at body weight, accent.lapis. Clicking the superscript opens the source drawer to that source. The superscripts are unobtrusive — readable at distance, ignorable up close.

> *"NVDA is positioned for continued data-center demand¹, with three fresh sources confirming H100 backlog² and one source noting a softening cycle in consumer GPUs³."*

### 13.2 Source drawer

A right-side drawer (consistent with the Intel detail drawer pattern) summoned by clicking any superscript or the "Evidence" affordance on a card.

Each entry in the drawer:
- **Ordinal** (1, 2, 3 …) matching the inline superscript.
- **Snippet** — 1–3 sentences of the source verbatim, in monospace tabular treatment to signal "raw quotation."
- **Freshness dot** — green (fresh, ≤ 24h), amber (watch, 24–72h), grey (stale, > 72h).
- **Credibility tier** — 5 dots, filled to credibility tier (Tier 1: regulator/issuer filings; Tier 2: paid analyst, well-known; Tier 3: established financial press; Tier 4: independent analyst; Tier 5: aggregated commentary).
- **Source name** with date and URL (URL is not displayed inline; copy-link affordance available).
- **Contradiction tag** if this source disagrees with another in the same evidence trail. Renders as a plum chip: *"Contradicts source 2 on capex outlook."*

### 13.3 Freshness indicators

Three states, three colors, always paired with text:

| Dot | Color | Label | Meaning |
|---|---|---|---|
| ● | atelier-green | Fresh | Updated within last 24 hours |
| ◐ | accent.saffron | Watch | 24–72 hours old |
| ○ | grey 50% | Stale | > 72 hours old |

A stale source does **not** disappear — it persists with its grey dot, so the user knows the system has *not yet* refreshed it, rather than wondering if it ever existed.

### 13.4 Contradiction display

When two sources in the same evidence trail disagree, the drawer renders a **contradiction strip** between them — a thin plum rule with a one-line summary: *"Source 1 (Tier 1, fresh) and Source 4 (Tier 3, watch) disagree on revenue trajectory."* The user sees the disagreement first, decides whose weight to trust.

### 13.5 "Evidence is weak" display

When the total evidence body is below the threshold the system requires for a confident claim, the drawer renders a **calm caution panel**: *"Evidence is currently Tentative — 2 sources, one Tier 4. The next intelligence run will attempt to broaden the evidence base."* Never a red banner, never a warning icon.

### 13.6 "Data missing" display

When a piece of data the user might expect is genuinely missing (e.g., no fresh source for a thesis claim), the slot renders an explicit grey pill: `Data missing — last attempted 3 May`. The pill has a tooltip explaining what was attempted and what would be needed. **Never** an empty `--` or zero.

### 13.7 Source credibility display

Five-dot ladder. Hovering reveals the tier label. Clicking opens a small sheet defining each tier and what kinds of sources qualify. Credibility tiers are **deterministic**, defined backend-side in a curated registry — the LLM does not invent credibility.

### 13.8 Avoiding source clutter

- Inline superscripts cap at 3 per claim. If a claim has more than 3 supporting sources, the superscripts compress: `¹⁻⁵`.
- The drawer paginates after 8 sources.
- Sources are deduplicated across an evidence trail — the same source supporting two claims appears once with two link references.

### 13.9 Source provenance for AI prose

When the system uses a small AI surface to compose prose (e.g., the Brief on Today), the prose itself does not get inline superscripts — instead, a **"Composed from"** affordance below the prose lists the source ordinals used. AI-composed prose is **always** labeled (see §14.6).

---

## 14. AI Interaction Design

The AI layer is **ambient, structured, and labeled**. It is never a chatbot, never a personality, never an avatar. It exists to explain, challenge, compare, summarize — never to invent.

### 14.1 The non-negotiable contract

The AI is **forbidden** from inventing or modifying any of the following:
- Numbers (price, percent, allocation, ratio, count).
- Scores or confidence values.
- Source claims or citations.
- Price targets.
- Allocation amounts.
- Risk tier classifications.
- Buy / Hold / Trim / Sell verdicts.

The AI is **permitted** to:
- Explain a thesis or recommendation in plain English, drawing only from the system's own evidence.
- Challenge a thesis (produce a counter-argument), drawing only from the system's own evidence.
- Compare two tickers, drawing only from the system's own data.
- Summarize the brief, the journal, or a ticker's history.
- Translate jargon for beginners.
- Surface the source trail.

Any AI output must include a **"Composed"** mark and a list of source ordinals used. If the AI cannot answer without inventing, it must say so plainly: *"I can't answer this without inventing a number. Try opening the source drawer for the underlying data."*

### 14.2 Where AI appears

The AI surfaces in **four places**, no others:

1. **The `⌘K` command bar** — keyboard-first ambient surface for "ask" / "challenge" / "compare" / "explain like I'm a beginner" / "what changed" / "summarize risks" / "find a better opportunity."
2. **Inline action chips** at the bottom of the Intel detail drawer: `[Ask why]` `[Challenge]` `[Compare]` `[Explain to a beginner]`.
3. **The Brief composition** on Today — composed deterministically (preferred) or via a constrained LLM prompt that takes only system-supplied facts.
4. **End-of-day digest** — a small composed summary, source-listed, opt-in.

The AI does **not** appear:
- As a floating chat bubble.
- As a sidebar avatar.
- As a "tutor" overlay.
- On Deploy. (Deploy is deterministic.)
- On Portfolio. (Portfolio is direct.)
- As a typeahead in plain inputs.

### 14.3 The `⌘K` command bar

Summoned by `⌘K` (or `Ctrl+K`), or by clicking the small command pill in the top right.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ⌘  Ask, jump, or compare ...                                              │
│                                                                            │
│  Recent                                                                    │
│  ▸ Why is RIVN flagged Trim?                                               │
│  ▸ Compare NVDA and AMD                                                    │
│  ▸ What changed since 5 May?                                               │
│                                                                            │
│  Suggestions                                                               │
│  ▸ Explain the SCHD thesis like I'm a beginner                            │
│  ▸ Summarize today's risks                                                │
│  ▸ Show me opportunities I haven't reviewed                               │
│                                                                            │
│  Jump to                                                                   │
│  ▸ Today  ▸ Intel  ▸ Deploy  ▸ Portfolio  ▸ Radar  ▸ Journal              │
└──────────────────────────────────────────────────────────────────────────┘
```

- The bar lives at 28 px backdrop blur with a 60% surface opacity, centered, 720 px wide on desktop, full-width sheet on mobile.
- Recent and Suggestions are populated from the user's history and the current page's context.
- Results render as composed prose with the **"Composed"** mark and source ordinals.
- Long-running queries show a "composing" breath skeleton, never a spinner.

### 14.4 Inline action chips

At the bottom of every Intel detail drawer:

```
[ Ask why ]   [ Challenge ]   [ Compare ]   [ Explain to a beginner ]
```

- `Ask why` — explain the recommendation in plain English.
- `Challenge` — generate a counter-thesis.
- `Compare` — opens a small sheet to pick a comparison ticker.
- `Explain to a beginner` — rewrite the thesis at a 10th-grade reading level.

Each chip outputs into the drawer below, never into a popup.

### 14.5 The Brief composition

Today's Brief paragraph is composed deterministically by the backend whenever possible. When LLM composition is used, it is constrained: the LLM receives only the system's own facts (counts, deltas, ticker names, action verdicts) and is forbidden from adding numerics or claims not in its input. The Brief carries a "Composed" mark and lists the source ordinals.

### 14.6 The "Composed" mark

A subtle iridescent shimmer on the first word of any AI-composed prose, plus a thin caption-sized label below: `Composed · 4 sources used`. The shimmer is a single pass on first render — never a loop. The mark is the user's reliable signal: *"This sentence was synthesized; the underlying data is below."*

### 14.7 Anti-patterns banned in AI surface

- Personality. The AI does not have a name.
- Avatars. No face.
- Typing animation. (Mimicking a human typing is dishonest.)
- "I think..." or "In my opinion..." framings. The AI does not have opinions; the system has opinions.
- Emojis in AI output.
- Open-ended generation that exceeds the source trail.
- Refusing to cite sources.

---

## 15. Component System

The components below are the atoms of the product. Each is defined as: **purpose**, **content hierarchy**, **visual treatment**, **interaction**, and **state matrix**.

### 15.1 Action Card

The signature card of Intel.

| Field | Detail |
|---|---|
| **Purpose** | Render one ticker's current Buy / Hold / Trim / Sell verdict with thesis, conviction, and source freshness. |
| **Content hierarchy** | Ticker name (display serif, 22 px) → action chip → 3-line plain-English thesis → confidence ring → source freshness dot → "what changed" pill (conditional). |
| **Visual treatment** | `bg.surface`, 1 pt `border.subtle`, `radius.card`, 16 px internal padding. Tabular numerals on the price meta. Action chip uses semantic color (Buy/Hold/Trim/Sell). |
| **Interaction** | Hover lifts border to `border.strong` (no scale). Click opens the detail drawer. Long-press on mobile opens drawer too. |
| **States** | Default · Hover · Selected · "What changed" present · Stale source · Insufficient data. |

### 15.2 Thesis Card

A larger reading surface used inside the Intel detail drawer and on Holding detail.

| Field | Detail |
|---|---|
| **Purpose** | Render the full plain-English thesis with inline source citations. |
| **Content hierarchy** | Display serif headline → composed prose body → "Composed · 4 sources" caption. |
| **Visual treatment** | `bg.surface.elevated`, `radius.panel`, generous 24 px padding, body.lg type, line-height 1.5. |
| **Interaction** | Hover on a superscript reveals a 1-line preview. Click opens source drawer. |
| **States** | Default · Insufficient evidence · LLM-composed (with shimmer mark) · Deterministic (no mark). |

### 15.3 Source-Backed Claim

A primitive used inside any composed prose.

| Field | Detail |
|---|---|
| **Purpose** | Bind a single sentence to one or more sources. |
| **Visual treatment** | Inline body text with hairline superscript (1-3 max, or compressed range like `¹⁻⁵`). The superscript is `accent.lapis`, 0.85 em, baseline-aligned. |
| **Interaction** | Hover reveals 1-line source preview. Click opens drawer to that source. |

### 15.4 Risk Challenge Card

| Field | Detail |
|---|---|
| **Purpose** | Render the system's own counter-thesis. *"If we are wrong, this is how it breaks."* |
| **Content hierarchy** | Eyebrow `RISK CHALLENGE` (caption, plum) → 1–3 sentence challenge prose → linked sources (if any). |
| **Visual treatment** | `bg.surface`, plum 1 pt left rule (4 px), `radius.sharp` on the rule edge. |
| **Interaction** | Click expands to full counter-thesis sheet with sources and risk tier. |
| **States** | Default · Acute risk (rule thickens to 6 px crimson) · Elevated · Quiet (omit the card if no challenge). |

### 15.5 Opportunity Card (Radar)

| Field | Detail |
|---|---|
| **Purpose** | Surface a candidate not yet held. |
| **Content hierarchy** | Ticker + name → "Why now" headline (display serif) → 2-3 reasons with freshness dots → workflow chip (`Add to research` / `Promote to Buy candidate`). |
| **Visual treatment** | `bg.surface.elevated`, 1 pt plum left border (4 px), `radius.card`, 20 px padding. |
| **Interaction** | Click opens compare drawer. Workflow chip click triggers state transition. |
| **States** | Surfaced · Researching · Promoted · Dismissed · Insufficient evidence. |

### 15.6 Deploy Instruction Row

| Field | Detail |
|---|---|
| **Purpose** | One row in the Deploy allocation ledger. |
| **Content hierarchy** | Ticker → action chip (Buy only) → dollar amount → percent of pool → allocation bar → 1-line rationale → Intel link. |
| **Visual treatment** | Single-row, no card chrome, 1 pt `border.subtle` rule below. Tabular numerals, fixed decimal alignment. |
| **Interaction** | Hover lifts background to `bg.surface.elevated`. Intel link opens detail drawer. |

### 15.7 Confidence / Caveat Badge

| Field | Detail |
|---|---|
| **Purpose** | Render confidence ladder (5 steps) as a compact ring. |
| **Visual treatment** | Outer ring 1 pt, inner fill at 0/25/50/75/100% in semantic color of the action. 14 px diameter on cards, 22 px in drawers. |
| **Interaction** | Hover reveals tier label ("Strong", "Working", "Tentative", "Sketch", "Settled"). |

### 15.8 Data Freshness Indicator

| Field | Detail |
|---|---|
| **Purpose** | Single-glance freshness signal. |
| **Visual treatment** | 6 px filled dot in green/amber/grey. Always paired with text ("Fresh"/"Watch"/"Stale") at caption size when in non-card contexts. |

### 15.9 Decision Timeline

| Field | Detail |
|---|---|
| **Purpose** | Vertical timeline of decisions on Journal. |
| **Content hierarchy** | Date stamp → entry rows → chapter break (monthly). |
| **Visual treatment** | A single 1 pt vertical rule on the left. Each entry sits 16 px right of the rule with a 6 px dot at its date stamp. |
| **Interaction** | Hover on a dot highlights the row. Click expands the entry. |

### 15.10 Intelligence Feed Item

| Field | Detail |
|---|---|
| **Purpose** | Single chronological event ("Source freshness drop on AAPL", "New thesis composed for NVDA"). |
| **Content hierarchy** | Time stamp (caption) → 1-line event sentence → optional link (Intel detail / Source drawer). |
| **Visual treatment** | No card chrome. Single line per event. |

### 15.11 Compare Drawer

| Field | Detail |
|---|---|
| **Purpose** | Side-by-side comparison of two tickers. |
| **Content hierarchy** | Two columns: Ticker A | Ticker B. Rows: Action, Conviction, Sector, Source freshness, Source count, Risk tier, "Why this view." A "Delta" caption between columns where applicable. |
| **Visual treatment** | Bottom sheet on mobile, right drawer on desktop. |

### 15.12 Empty / Loading / Error States

(Covered in §4.13. Each component has its own variant honoring that pattern.)

### 15.13 Mobile Card

A compact variant of the Action Card for phone widths.

| Field | Detail |
|---|---|
| **Visual treatment** | Full-width card, 12 px padding, ticker + action chip on row 1, thesis on rows 2-3, confidence ring + freshness dot on row 4. |
| **Interaction** | Tap opens bottom sheet (drawer). Long-press opens compare picker. |

### 15.14 Top Navigation Bar

| Field | Detail |
|---|---|
| **Visual treatment** | Glass surface (24 px blur, 70% opacity). Brand mark + 6 destination labels + `⌘K` pill + data-health dot + settings cog. |
| **Interaction** | Active destination has a 1 pt accent rule under the label. Hovers underline at 50% opacity. |

### 15.15 The "Composed" Mark

| Field | Detail |
|---|---|
| **Purpose** | Label any AI-composed prose. |
| **Visual treatment** | Iridescent shimmer on the first word (single-pass on render) + caption-sized label below: `Composed · N sources used`. |

---

*Continued in `04_Mobile_QA_Sequencing_Narrative.md`.*
