# Design Master Plan — Part 2
## Principles, Visual Identity, Motion

---

## 3. Product Design Principles

These are hard rules, not taste. Every screen, component, and motion in this bible should be checkable against this list.

1. **Trust before excitement.** Visual polish must make the product feel *more* trustworthy, never *more* exciting. If a treatment makes risk look smaller than it is, kill the treatment.
2. **One primary decision per screen.** Each screen has exactly one question it is allowed to answer. Secondary information is allowed; secondary primary actions are not.
3. **Source-backed claims earn visual weight.** A claim with three reliable sources renders heavier (slightly larger, slightly more saturated) than a claim with one. A claim with no source does not render at all.
4. **Missing data is honest, not hidden.** A grey "data missing" pill is always preferable to silent absence or interpolated guesses. The user should never wonder if a number was real.
5. **Numbers are sacred.** Tabular figures only. Slashed zeros. Fixed decimal alignment. Currency symbols sized at 0.85× of the digit run. Negative numbers in parentheses *or* with a minus, never both. Every number has a unit. Every number has a timestamp accessible within one click.
6. **Motion reveals intelligence, not decoration.** Motion exists to explain what changed, what is loading, or what is about to happen. If a motion has no informational job, remove it.
7. **Beginner language, expert structure.** The vocabulary on screen reads as if written for a smart relative who is new to investing. The structure underneath is institutional.
8. **Density behind drawers, calm above the fold.** Above the fold is calm and decisive. Density lives in drawers, sheets, and `⌘K`-summoned panels — earned, not pushed.
9. **Never make uncertainty look certain.** Confidence is rendered as a 5-step ladder, never as a percentage above 90 unless backed by a deterministic computation. Probabilistic claims read as probabilistic.
10. **Plain English wins, jargon stays backend.** Words like "alpha," "Sharpe," "free cash flow yield" are allowed in tooltips and source drawers, never in the primary copy. Raw metric keys (`pe_ratio_ttm`) never appear in UI.
11. **Risk is visible, never buried.** Every page that shows opportunity also shows the risk of acting on that opportunity. Risk gets the same visual hierarchy as opportunity, never less.
12. **Confidence is earned, displayed proportionally.** Higher-conviction claims get more visual weight; lower-conviction claims get less. The reader can read confidence at 3 meters distance.
13. **Time is a first-class data dimension.** Every claim has a "fresh as of" timestamp. Every recommendation has a "what changed since last time" link. Every chart has a date range visible without hover.
14. **The app reads like a private letter.** Tone is direct, considered, not breathless. No exclamation marks in primary copy. No "🚀" anywhere. No "Congrats!"
15. **Beauty must serve logic; logic must inform beauty.** Visual beauty without informational service is decoration. Logic without visual care is brutalism. Both are bugs.

### Operational corollaries

- **No primary information lives in tooltips.** Tooltips are for vocabulary, not for facts.
- **No KPI tile glow on hover.** Hover surfaces are for affordance, not theater.
- **No animations on numbers that did not change.** A tween implies a delta; a non-delta tween is a lie.
- **No "tap to learn more" without telling the user what they will learn.** Disclosure must be honest about scope.
- **No skeleton loaders that do not match the final content shape.** A skeleton is a *promise* of the layout that follows.

---

## 4. Visual Identity System

The system is defined as **tokens**, not bespoke values, so that future implementation can land it in CSS variables and Tailwind config without rework.

### 4.1 Mode

**Dark mode is the canonical mode.** The product was designed dark-first because financial data is read in low-light evenings, on phones at night, before the open. Light mode is a deliberate, calm secondary mode — not an afterthought.

#### Dark mode — *Obsidian*

A near-black base with a faint green-blue cast. Avoids the "purple-black" SaaS dark and the "blue-black" gaming dark.

| Token | Hex (proposed) | Use |
|---|---|---|
| `bg.canvas` | `#0A0B0F` | Page canvas, behind everything |
| `bg.surface` | `#10131A` | Default card surface |
| `bg.surface.elevated` | `#161A23` | Elevated cards, drawers |
| `bg.surface.high` | `#1D222C` | Modals, command bar |
| `bg.surface.peak` | `#262C38` | Tooltips, top-most overlays |
| `border.subtle` | `#1F2531` | Default 1pt rules |
| `border.default` | `#2A3140` | Inputs, default cards |
| `border.strong` | `#3A4253` | Focus rings, hovered cards |

#### Light mode — *Paper*

Warm paper, graphite ink, forest accent. Reads like a private banking letter — not a stark medical screen.

| Token | Hex (proposed) | Use |
|---|---|---|
| `bg.canvas` | `#FAF7F2` | Warm paper |
| `bg.surface` | `#FFFFFF` | Cards |
| `bg.surface.elevated` | `#F4F1EB` | Drawers |
| `border.subtle` | `#E7E1D6` | Rules |
| `border.default` | `#D8D1C2` | Inputs |
| `border.strong` | `#B8AE9A` | Focus, hovered |

### 4.2 Color palette

The palette is intentionally narrow. **One signature accent.** Three secondary accents reserved for specific semantic uses. Everything else is grayscale.

#### Signature accent — *Atelier Green*

`#2EC27E` (dark mode) / `#138659` (light mode). Used for:
- The single most important affordance on a page.
- The Buy semantic.
- The brand mark.

Atelier Green is **not** used for hover glows, decorative gradients, or "active" states.

#### Secondary accents

| Token | Hex (dark / light) | Reserved for |
|---|---|---|
| `accent.lapis` | `#5B7CFF` / `#3955D6` | Information, Hold semantic, AI-composed prose marker |
| `accent.plum` | `#B47EFF` / `#7E48D6` | Review / "needs human attention" |
| `accent.saffron` | `#F2A93B` / `#B97800` | Trim / caution |
| `accent.crimson` | `#D14C5A` / `#9A2A38` | Sell / risk |

These accents are reserved. They do not appear decoratively. If you see Plum on the page, it is because something needs human attention — never because plum looked nice.

### 4.3 Semantic colors — Buy / Hold / Trim / Sell

The visible action model is **Buy / Hold / Trim / Sell**. Each has a single color, used identically everywhere (badge, border, dot, chart marker, deploy row). Color is **never the only signal** — every semantic is also distinguished by glyph and label.

| Action | Dark hex | Light hex | Glyph | Tone |
|---|---|---|---|---|
| **Buy** | `#2EC27E` | `#138659` | `▲` filled | Atelier Green. Earned, not loud. |
| **Hold** | `#5B7CFF` | `#3955D6` | `■` filled | Lapis. Calm, deliberate, "the thesis stands." |
| **Trim** | `#F2A93B` | `#B97800` | `◆` filled | Saffron. Warm, considered — not panic. |
| **Sell** | `#D14C5A` | `#9A2A38` | `▼` filled | Oxblood. Muted, decisive — never Robinhood-red. |

> Note: **Sell deliberately avoids the standard fintech red.** Standard red (`#FF3B30`) reads as alarm. Oxblood crimson reads as decision.

### 4.4 Risk and confidence colors

Risk is rendered as a 4-tier ladder. Confidence is rendered as a 5-tier ladder. Both are **always paired with a glyph**.

#### Risk ladder

| Tier | Label | Dark token | Glyph |
|---|---|---|---|
| 1 | Quiet | grayscale | `·` |
| 2 | Watch | accent.lapis 30% | `◔` |
| 3 | Elevated | accent.saffron 60% | `◑` |
| 4 | Acute | accent.crimson 80% | `◕` |

#### Confidence ladder

| Tier | Label | Visualization |
|---|---|---|
| 1 | Sketch | empty ring |
| 2 | Tentative | quarter ring |
| 3 | Working | half ring |
| 4 | Strong | three-quarter ring |
| 5 | Settled | full ring |

Confidence is **never** rendered as a percentage above 90 unless the underlying score is deterministic and source-backed. The ladder is the canonical render.

### 4.5 Typography

The product uses **two type families**, paired like an editorial magazine.

| Role | Family (proposal) | Notes |
|---|---|---|
| Display / headlines | **Tiempos Headline** *or* **GT Sectra** | A refined, modern serif. Used at 48 / 36 / 28 / 22 px for editorial moments and section openers. |
| Body / data | **Söhne** *or* **Inter Tight** | A warm geometric sans with strong tabular figures. Used at 16 / 14 / 13 / 11 px. |
| Numerics | Body family with `tabular-nums` and `slashed-zero` opentype features always on | Numbers occupy a fixed grid; never reflow on tween. |
| Eyebrow / metric label | Body family, 10–11 px, 0.08 em letterspacing, uppercase | Used as section labels, ticker meta, "as of" stamps. |

**Type tokens (vertical rhythm on a 4-pt baseline):**

| Token | Size / line-height | Use |
|---|---|---|
| `display.xl` | 48 / 56 | Cover, north-star headlines |
| `display.lg` | 36 / 44 | Section openers |
| `display.md` | 28 / 36 | Page titles |
| `headline.lg` | 22 / 30 | Card titles |
| `headline.md` | 18 / 26 | Sub-section titles |
| `body.lg` | 16 / 24 | Reading copy |
| `body.md` | 14 / 22 | Default UI |
| `body.sm` | 13 / 20 | Dense tables |
| `caption` | 11 / 16 | Eyebrows, timestamps |

### 4.6 Spacing scale

4-pt baseline. Allowed values: **4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80**. Nothing else exists. If a layout asks for 18 px, the layout is wrong.

### 4.7 Border radius

| Token | Radius | Use |
|---|---|---|
| `radius.sharp` | 2 px | Data chips, ticker tags, action badges |
| `radius.card` | 8 px | Cards |
| `radius.panel` | 12 px | Drawers, multi-pane panels |
| `radius.modal` | 20 px | Modals, command bar |
| `radius.pill` | 999 px | Status pills, filter pills |

No half-radii. No 4px, 6px, 10px exceptions.

### 4.8 Elevation and shadow

The product is **almost flat**. Depth is communicated by 1-pt borders and a single low ambient shadow.

| Token | Spec | Use |
|---|---|---|
| `elev.0` | none | Page canvas |
| `elev.1` | `0 1px 2px rgba(0,0,0,0.25)` + 1pt border | Default cards |
| `elev.2` | `0 6px 24px rgba(0,0,0,0.35)` + 1pt border | Drawers, modals |
| `elev.3` | `0 24px 48px rgba(0,0,0,0.45)` + 1pt border | Command bar, top-most |

No drop-shadow drama. No glowing rings. No multi-layer "neumorphic" trickery.

### 4.9 Glass / blur

Reserved for **three** surfaces only:

1. The top navigation bar (24 px backdrop blur, 70% surface opacity).
2. The `⌘K` command bar (28 px backdrop blur, 60% surface opacity).
3. Confirmation overlays for Deploy execution (32 px backdrop blur, 55% surface opacity).

Glass is **not** used for cards. Glass overuse is the single fastest way to make a fintech app look amateur.

### 4.10 Texture and background

- **1% monochromatic noise** on hero canvas surfaces (Command Center hero, login). Visually invisible up close; reads as "premium paper" at distance.
- **Faint engraved grid (0.5pt at 4% opacity)** behind charts, optional toggle.
- **No gradients** on serious data surfaces. Gradients are reserved for the brand mark and login moment only.
- **No background images.** Ever.

### 4.11 Chart styling

Charts are **editorial line work**, not "visualization library defaults."

- 1.5 px line weight for the primary series.
- 1 px for secondaries.
- No shadows. No gradients on lines.
- Axis: 0.5 px grid at 6% opacity, only horizontal — never both axes.
- Labels: tabular numerals, caption size, 60% opacity unless hovered.
- Tooltip: a single rule (1 px) drawn vertically through the data, with a small inline panel — never a floating box.
- Color: semantic only (Buy green, Hold lapis, Sell oxblood, neutral graphite). Never rainbow.
- Sparklines: 24 px tall, no axis, no labels, only the line and a single end-dot.
- Negative regions: filled at 8% of stroke color.

### 4.12 Iconography

- **Single icon family**, 1.5 px stroke, geometric, slightly rationalist (Lucide as base, with selective custom icons for the top-level intents: Intel, Deploy, Radar, Journal).
- **Action icons heavier** (2 px stroke) than navigation icons (1.5 px) — so Buy/Hold/Trim/Sell glyphs read first.
- No filled illustrative icons. No emoji. No mascots.

### 4.13 Empty / loading / error states

#### Empty — *Quiet states*

A small editorial illustration (line art, single-color, 64 px). Plain copy: *"Nothing to act on yet. The next intelligence run is at 06:30 ET."* Always names what will appear and when.

#### Loading — *Composing intelligence*

Skeletons that mimic the final shape of the content (header rule + 3 lines + chip row), with a slow 1.4 s breath. Never generic gray bars. Never a spinner.

For long-running operations (a fresh Run Agents pass), a top-of-page progress affordance: `Composing intelligence · 04 of 34 tickers`. Plain English, current-of-total format.

#### Error — *Specific, apologetic, actionable*

A small inline panel: what happened, what we know, what to try. Never red flood-fill. Never modal block. Never "Something went wrong." Errors always name a follow-up: *"Try Run Agents again, or open the source drawer to see partial data."*

### 4.14 Premium details that make the app feel custom

These are the small touches that distinguish The Quiet Atelier from a generic fintech reskin. None of them is decorative — each carries a small piece of information.

- **Engraved divider rule** between sections — a 0.5 pt line over a 1 pt highlight, hairline elegance.
- **Chapter numerals** at section openers (`I`, `II`, `III`) in display serif — used for Intel chapters, Journal months, Decision History.
- **Tiny build label** in the bottom-right of every page, caption-sized, 30% opacity: `as of 06:42 ET · build 412.b`.
- **Page-corner timestamp**: top-right of every page in caption: `Tuesday · 7 May 2026 · 06:42 ET · NYSE pre-open`.
- **A signature mark** on the Command Center hero — a small letterform initial, drawn once per session, that subtly reflects portfolio temperature (one of five expressions: settled, watching, alert, opportunity, deploy-ready). Never face-like, never animated more than once per page load.

---

## 5. Motion and Interaction System

Motion exists for one of four reasons:
1. To explain **what changed**.
2. To indicate **what is loading**.
3. To suggest **what is about to happen**.
4. To confirm **what just happened**.

Anything else is decoration and is rejected.

### 5.1 Motion tokens

| Token | Curve | Duration | Use |
|---|---|---|---|
| `motion.enter` | `cubic-bezier(0.2, 0.8, 0.2, 1)` | 240 ms | Cards, drawers entering |
| `motion.exit` | `cubic-bezier(0.4, 0.0, 1.0, 1.0)` | 160 ms | Anything leaving |
| `motion.cross` | `cubic-bezier(0.4, 0.0, 0.2, 1.0)` | 280 ms | Page transitions |
| `motion.tween` | `cubic-bezier(0.25, 0.1, 0.25, 1.0)` | 120 ms | Number tweens |
| `motion.emphasis` | `cubic-bezier(0.34, 1.56, 0.64, 1)` | 320 ms | Confirmation moments only — used in <5% of motion |
| `motion.breath` | sinusoidal | 1400 ms loop | Skeletons |

### 5.2 Specific interactions

| Surface | Motion | Notes |
|---|---|---|
| **Page transition** | `motion.cross`: cross-fade with 8 px Y translate | Never slide-in from a side. |
| **Card entry** | Stagger 40 ms × n; each card uses `motion.enter` with 8 → 0 px Y | Stops staggering after card 6 (others arrive instantly). |
| **Thesis update reveal** | Characters fade in left-to-right with 4 ms per character; subtle vertical jitter (1 px) | Used only on the *single* changed thesis on page load — not on every load. |
| **Source drawer reveal** | Slide-up 320 ms with `motion.enter`; depth shadow appears at the end (last 60 ms) | Drawer always covers ≤ 60% of viewport height. |
| **Risk alert reveal** | One-shot pulse on first appearance (scale 1.00 → 1.04 → 1.00, 320 ms emphasis curve); never repeats | The pulse is the alert — the color alone is not enough. |
| **Buy / Hold / Trim / Sell state change** | Badge cross-morph in 200 ms; semantic color shift; tabular numerals tween | Optional very-quiet chime, off by default. |
| **Deploy plan confirmation** | Ceremonial 600 ms: card lifts 4 px, blueprint hairlines draw from each holding to its ticker target (left → right), then a soft "stamp" appears at the top-right corner | This is the one ceremonial moment in the product. Use sparingly. |
| **Portfolio number change** | Numbers tween up/down with `motion.tween`; positive change finishes faster (eases out at 100 ms), negative slightly slower (140 ms) — gives losses subtle weight | Never animate a number that did not change. |
| **Hover** | 1 pt border lift + background shift to surface.elevated; **no scale**, **no glow** | Cards do not bounce. |
| **Skeleton loading** | `motion.breath` at 1.4 s loop; opacity 0.5 ↔ 0.7 | No throb, no shimmer-bar. |
| **"New intelligence available"** | Top-bar dot fades in (160 ms); side notification slides in from top-right with `motion.enter`: *"3 updates since you were away"* | Notification persists 6 s, then collapses to a quiet dot. |
| **Number copying** | Selected number gets a 1 pt accent underline that fades 280 ms after copy | Avoids "Copied!" toasts. |
| **Empty → populated** | First card uses thesis-update reveal; subsequent cards use card entry stagger | Distinguishes "first arrival" from "subsequent updates." |

### 5.3 Reduced motion mode

`prefers-reduced-motion: reduce` collapses every motion to a static fade (160 ms, no translate, no scale). Skeletons stop breathing — they show a single quiet shimmer. Deploy ceremony loses its blueprint draw and shows a single "stamped" affirmation at the end.

### 5.4 Motion anti-patterns banned

- No scale-up-on-hover for cards.
- No bounce on landing.
- No confetti.
- No spring physics on data values.
- No parallax.
- No animated charts that "draw themselves" on every render — only on first load and on user-initiated change.
- No skeletons that look like the page is broken.
- No transitions on hover-out (only on hover-in) — out should feel instant.

---

*Continued in `02_Architecture_and_Pages.md`.*
