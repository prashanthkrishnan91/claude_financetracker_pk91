# Intel v3 Architecture Plan Draft3 — Living Investment Cockpit Addendum

Date: 2026-05-08
Status: **Current canonical north-star addendum** (supersedes sequencing guidance in Draft2 PDF and Anthropic Finance Agent Addendum where conflicts exist)
Scope: Post-Phase-9 architecture direction for Finance Tracker Intel v3.

This addendum does **not** replace `artifacts/Intel_v3_Architecture_Plan_Draft2.pdf` or
`artifacts/Intel_v3_Architecture_Plan_Draft2_Anthropic_Finance_Agent_Addendum.md`.
Both remain valid historical architecture inputs. This addendum supersedes only sequencing
guidance, Deploy/Watchtower scope, and evidence-lane breadth decisions where conflicts exist.

---

## Core product north star

Finance Tracker Intel v3 is not just a recommendation page. It is a **living, low-cost
investment cockpit** for long-term personal investing.

The product should:
- monitor the user's portfolio continuously,
- analyze evidence asynchronously,
- produce deterministic Buy/Hold/Trim/Sell decisions,
- convert decisions into exact whole-dollar Deploy plans,
- send rare event-driven email alerts when meaningful action thresholds are crossed,
- remain simple, fast, and low-cost.

---

## Architecture principle

> **Research is asynchronous. Decisions are deterministic. UI reads certified snapshots.
> Deploy generates exact action plans. Watchtower sends rare actionable email alerts.**

---

## Current stack constraint

Stay within the existing architecture where possible:
- **Frontend**: Next.js
- **Backend**: FastAPI on Railway
- **Database**: Supabase
- **Notifications**: low-cost email (no native push, no mobile app store)

No new platforms, runtimes, or major infrastructure additions unless explicitly approved.

---

## Explicit non-goals

- No native Android or iOS app. No Play Store or App Store dependency.
- No auto-trading. No broker API integration.
- No daily-trading behavior.
- No prediction-oracle claims (no "tomorrow's price" or exact peak/bottom claims).
- No hidden LLM decision authority over Buy/Hold/Trim/Sell.
- No vague user-facing ranges for actionable Deploy plans (e.g., "Buy $500–$1,000").
- No visible decision changes without explicit operator approval.

---

## Evidence architecture

SEC metrics are the **first certified hard-data lane**, not the full evidence universe.

Future evidence lanes should include:
1. SEC/company fundamentals (Phase 8–9 readiness established)
2. Valuation context (multiples, peers, history)
3. Market behavior / volatility
4. Analyst expectations and revisions
5. Earnings transcripts and guidance
6. News / event risk
7. Sector and macro context
8. ETF/fund exposure and holdings overlap
9. Portfolio exposure / concentration risk
10. User thesis memory per holding

All evidence lanes must pass through:
- **Evidence Source Registry / source governance** — explicit registration, trust tier, freshness SLA
- **Truth scoring** — per-lane quality, recency, and missing-data contracts
- **Replay/diff validation** — snapshot projections that can be replayed and diffed
- **Explicit operator approval** before any lane contributes to visible decision consumption

Weak or missing evidence **suppresses** affected axes. It must never fabricate confidence.

---

## Finance-agent role

Finance agents and LLM workers are **research artifact workers only**.

They may produce sourced, structured artifacts for:
- Earnings summaries
- Analyst change context
- News / event risk summaries
- Valuation context
- Risk red-team / counter-thesis
- ETF overlap findings
- Thesis pillar and falsification tracking

They **must never**:
- directly produce final Buy/Hold/Trim/Sell decisions,
- directly produce exact Deploy amounts,
- hold final visible action authority.

**Deterministic policy owns final visible decisions.**
**Deploy sizing must be deterministic.**

Every artifact must carry: provenance, source links, input snapshot version, missing-data
flags, model/tool version, cost/latency envelope, and audit metadata.

---

## Deploy architecture

Intel answers: *"What action is justified?"*
Deploy answers: *"How exactly should I act?"*

Deploy must convert certified Intel decisions into **exact whole-dollar action plans**.

### User-facing Deploy plans must say:
- Buy $500 of VTI
- Buy $300 of GOOGL if the trigger fires
- Trim $700 of NVDA if it crosses the concentration limit
- Do nothing on AMD — evidence is incomplete

### User-facing Deploy plans must NOT say:
- Buy $500–$1,000
- Consider adding some
- Maybe trim a little

Internal sizing ranges are allowed for calculation, but the **final user-facing plan must
choose one exact rounded whole-dollar amount or $0 / no action**.

### Deploy should eventually include:
- Available cash and deposit cadence
- Target allocation per holding
- Max position size and concentration limits
- Buy/trim/sell bands
- Tax-aware / tax-lot logic
- Wash-sale guardrails
- Staged action plans with trigger conditions
- Execution status (executed / snoozed / ignored feedback)

---

## Watchtower architecture

Watchtower continuously monitors for changes worth acting on:
- Evidence changes (new earnings, analyst revisions, news)
- Valuation changes and buy/trim/sell band crossings
- Market volatility shifts
- Portfolio drift vs target allocation
- Cash/deposit availability with strong candidates
- Event risk changes
- Thesis-break signals
- Action changes since prior certified snapshot

### Alert behavior
Alerts are:
- **event-driven, not daily**
- **rare by default** (not noisy digests)
- **email-first** (no native push, no mobile app store dependency)
- not limited to the biweekly deposit window
- sent only when meaningful actionability thresholds are crossed

### Watchtower modes
- **Calm Mode** — only alert on high-confidence, time-sensitive Buy/Trim/Sell triggers
- **Active Mode** — include Watch alerts when bands are approached
- **Storm Mode** — elevated alerting during high-volatility or event-risk periods
- **Deploy Mode** — focused on best use of available cash/deposit

### Alert format requirements
Every alert must include:
- Exact action amount when actionable
- Plain-English reason (no raw metric keys, no jargon)
- Evidence status summary
- Urgency level
- Link to current Deploy plan
- No noisy daily digest behavior

### Alert examples
- **Action Alert**: Buy $500 of GOOGL — it entered the buy zone and evidence remains strong.
- **Watch Alert**: NVDA is approaching trim zone; no action yet.
- **Risk Review**: ALK thesis risk increased; review before next deploy.
- **Deploy Plan**: Best use of this deposit is $400 VTI and $300 GOOGL.

---

## Timing philosophy

The app should not claim to predict tomorrow's price or exact peaks/bottoms.
Instead, it detects **evidence-backed triggers**:
- Buy zone crossed with strong evidence
- Trim zone crossed with elevated risk
- Thesis-break risk threshold reached
- Valuation/risk-reward meaningfully improved
- Portfolio concentration limit exceeded
- Cash available with high-confidence candidates
- Event risk changed materially

---

## Tax and execution philosophy

The app should eventually account for:
- Short-term vs long-term gains
- Unrealized gains/losses and tax-lot selection
- Wash-sale risk
- Loss harvesting constraints
- Using deposits to rebalance before selling
- Manual execution in Robinhood (or equivalent)
- User marking actions as executed / snoozed / ignored

No auto-trading. All execution is manual and user-confirmed.

---

## Fast UI principle

The UI must not compute intelligence live.

**Fast path** (what the user sees immediately):
- User opens Intel or Deploy.
- Next.js reads latest certified snapshot from Supabase.
- Cards and exact plans render quickly.
- Deep evidence opens on demand (lazy-loaded drawers).

**Slow path** (async background jobs):
- Scheduled or manual jobs refresh evidence.
- Research workers create sourced artifacts.
- Truth contract scores evidence quality.
- Deterministic policy builds a new certified snapshot.
- Deploy creates exact whole-dollar action plan.
- Watchtower sends email only if actionability threshold is crossed.

---

## Recommended sequencing after Phase 9

**Do not move directly from Phase 9 to SEC metric consumption in DecisionInputV3.**

The next implementation phase must be:

> **Phase 10 — Evidence Source Registry v1 / Multi-Lane Governance v1**

Actual SEC metric consumption by DecisionInputV3 remains a **later explicitly approved phase**
after all four gates are satisfied:
1. Phase 9 readiness adapter is validated in production.
2. Evidence Source Registry / Multi-Lane Governance exists.
3. Replay/diff governance exists.
4. Operator approval is given for shadow-only input consumption.

---

## Future roadmap (post-Phase-9)

| Phase | Name |
|-------|------|
| 10 | Evidence Source Registry v1 / Multi-Lane Governance v1 |
| 11 | Snapshot Projection / Replay / Decision Diff Governance v1 |
| 12 | Multi-Lane Truth Contract v2 |
| 13 | ETF/Fund Intelligence Lane |
| 14 | First Finance-Agent Research Worker (Earnings Reviewer or Analyst Expectations) |
| 15 | Portfolio Thesis Memory |
| 16 | Shadow Consumption of selected evidence lanes |
| 17 | Deploy Contract Audit |
| 18 | Portfolio Targets + Buy/Trim/Sell Bands |
| 19 | Deploy Plan Generator — shadow mode with exact dollar sizing |
| 20 | Event-Driven Watchtower Trigger Engine |
| 21 | Email Notification v1 |
| 22 | Snooze / Ignore / Mark Executed feedback loop |
| 23 | Plain-English UI Intelligence Layer |

---

## Architecture invariants (must be preserved in all future phases)

- `decide()` / deterministic policy remains sole owner of visible Buy/Hold/Trim/Sell.
- `intel_v3_snapshots` — writes only through the certified snapshot pipeline.
- `safe_for_decision` remains DB-hard-locked false until all four Phase 10 gates pass.
- LLM/agent workers produce artifacts only; they never write visible actions directly.
- UI stays plain-English: no raw metric keys, no posture labels, no diagnostic leakage.
- Missing/stale/weak data suppresses affected axes; it must not fabricate confidence.
- Deploy final output is one exact whole-dollar amount or $0 / no action — never a range.
- Watchtower alerts are event-driven and email-first — never a noisy daily digest.
