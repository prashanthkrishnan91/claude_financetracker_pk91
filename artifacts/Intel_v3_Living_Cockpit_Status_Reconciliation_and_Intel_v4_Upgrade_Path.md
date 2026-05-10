# Intel v3 Living Cockpit — Status Reconciliation and Intel v4 Upgrade Path

Date: 2026-05-10
Status: **Current canonical roadmap bridge.** Reconciles `artifacts/Intel_v3_Architecture_Plan_Draft3_Living_Investment_Cockpit_Addendum.md` (the existing north-star addendum) and `artifacts/Intel_v3_Architecture_Plan_Draft2_Anthropic_Finance_Agent_Addendum.md` with the current Phase 14 SEC/valuation/PriceBand reality and with external finance/trading tool references. Does **not** replace either prior artifact. Where sequencing or scope conflicts exist, this document is the latest reference.

Scope: documentation only. No code, SQL, providers, env vars, or dependencies are introduced or changed by this artifact.

---

## 0. Why this artifact exists

The repo has accumulated three distinct streams of architectural input:

1. The existing Intel v3 plan (Draft2 + Draft3) describing the deterministic-decision living-cockpit north star.
2. Active in-flight work — Phases 14A–14D — that has already shipped SEC EDGAR + CompanyFacts ingestion, source-linked `research_artifact_facts`, FY EPS earnings yield, and a shadow PriceBand classification.
3. External finance/trading intelligence references (TradingAgents, last30days-skill, OpenBB, Microsoft Qlib, K-Dense scientific-agent-skills, Anthropic financial-services, Xynth, EdgarTools, sec-edgar-downloader / sec-api, FinanceBench / Fin-RATE / FinRetrieval, vectorbt / PyBroker / Alphalens / QuantStats / LEAN / NautilusTrader / FinRL, PyPortfolioOpt / skfolio / Riskfolio-Lib, FinGPT / FinRobot, Quiver Quantitative / Unusual Whales, FinChat).

Without a bridge, two failure modes are likely:

- **Orphan drift** — external tools enter the repo as standalone "trading features" that contradict the cockpit's deterministic-decision boundary.
- **Status drift** — future prompts and reviewers treat SEC ingestion, valuation, or PriceBand as if they were greenfield, ignoring Phase 14 work already in production.

This artifact is the bridge. It is intentionally an addendum and not a rewrite, so that:

- Draft2 / Draft3 remain valid historical inputs.
- Phase 14 in-flight work continues without re-certification.
- Every external idea is mapped into an existing lane rather than chartered as a new orphan feature.
- A future redesign and a future "Intel v4" remain optional, governed evolutions of the same spine, not a parallel rebuild.

---

## 1. Current Reality Checkpoint

The repo is **not** in the pre-Phase-9 posture that Draft3 was written against. The following are already live in `v2/backend/`:

- **Intel v3 deterministic decision path** — `decide()` in `decision_policy_v1.py` is the sole authority for visible Buy/Hold/Trim/Sell. Page-load and Run paths converge on certified `intel_v3_snapshots`. Snapshot certification (`generic_copy_count`, `repeated_skeleton_count`, `ticker_prefix_only_reason_count`, `weak_buy_rationale_count`) is enforced.
- **Research Artifact Store v1** — migration `017_research_artifact_store_v1.sql` is applied. Tables `research_artifacts`, `research_artifact_sources`, `research_artifact_facts`, `worker_audit_events`. Forbidden visible-decision keys are rejected at write time by both column-level CHECK and a recursive PL/pgSQL JSONB walker.
- **Evidence Source Registry — partial / conceptual** — Draft3 names "Evidence Source Registry" as a Phase 10 module. In current code, source governance is partly enforced by per-lane structures: SEC `SourceRecord` provenance, `ReadOnlyEvidenceAdapter`, and the Phase 5 truth-adapter readiness contract (`docs/ai/INTEL_V3_TRUTH_ADAPTER_READINESS_CONTRACT.md`). A unified Signal-Registry-grade table does **not** yet exist. Status: in scope, in concept, not yet a single registered artifact.
- **SEC EDGAR / CompanyFacts lane** — Phase 6A added the SEC provider. Phase 7A added bounded XBRL parsing (13-tag us-gaap allowlist, ≤2 periods/tag, source-linked accessions only). Earnings Reviewer dark-runs produce source-linked `metric_observation` facts.
- **Phase 14A SEC Metric Truth & Coverage** and **Phase 14B Valuation Input Verification** — coverage diagnostics and source-linked-fact checks are active and aggregate-only.
- **Phase 14C / 14C.1 FY EPS Earnings Yield (shadow/diagnostics)** — earnings yield is computed from source-linked SEC EPS facts and certified `market_snapshots` price/sector. Aggregate buckets only. No raw EPS, no raw prices, no raw yields are exposed.
- **Phase 14D PriceBand Shadow Policy v1 (shadow/diagnostics)** — humble valuation classification (`unavailable`, `negative_eps`, `expensive`, `elevated`, `reasonable`, `attractive`, `unusually_cheap`) using static broad-market thresholds. Hard locks: `safe_for_decision=False`, `shadow_only=True`, `decision_input_mutated=False`, `no_target_price_emitted=True`, `no_fair_value_emitted=True`, `ttm_computed=False`. **No PriceBand enum wiring**, **no DecisionInputV3 mutation**, **no UI exposure**.
- **`safe_for_decision = False`** remains DB-hard-locked for research artifacts. The Phase 5 readiness contract is in place and will continue to gate any future consumption.
- **No frontend redesign has begun.** UI remains the existing plain-English Intel v3 cards. Major design transformation is gated by `docs/ai/DESIGN_VISION.md`.

**Reconciliation rule:** Every section below is written assuming the above is already true. Future prompts must not propose SEC ingestion, EPS facts, valuation diagnostics, or PriceBand classification as *new ideas*; they exist and must be **matured**, not duplicated.

UI redesign should wait until the intelligence spine produces certified snapshots stable enough to design around. Designing around uncertified or in-flight signals would lock in shadow data into visible IA.

---

## 2. Unified Intelligence Spine

A single sentence organizes everything below:

> **Evidence enters through governed lanes. Research artifacts explain and challenge evidence. Eval and replay validate signals. Deterministic policy decides. Deploy sizes exact actions. Watchtower monitors event-driven changes. UI presents certified snapshots in plain English.**

This spine is the only organizing principle. Every external tool, every new lane, every future agent, every redesign surface must land on a labeled stage of this spine. Anything that cannot be mapped to a stage is not a roadmap item — it is either deferred input or rejected.

The stages, named for cross-prompt referencing:

1. **Ingestion / Lane Adapters** — provider gateways, parsers, freshness/coverage diagnostics.
2. **Source Governance** — Evidence Source Registry, Signal Registry / Feature Store metadata, trust tiers, freshness SLAs, kill switches.
3. **Research Artifact Workers** — sourced artifacts (claims, facts, risks, catalysts, debate memos). Never hold final visible authority.
4. **Eval & Replay** — Finance QA Eval Harness, Quant Replay / Signal Validation Lab, decision diff, signal tear sheets.
5. **Deterministic Policy** — `decide()`, the only authority for visible Buy/Hold/Trim/Sell.
6. **Deploy Sizing** — exact whole-dollar plans or `$0`, deterministic.
7. **Watchtower** — event-driven, rare, email-first alerts with trigger governance.
8. **UI / Certified Snapshots** — plain-English surfaces; never compute live; never expose raw metric keys.

---

## 3. Current Plan vs Missing Maturity Layers

The matrix below reconciles Draft3's roadmap and Phase 14 reality with what is still missing. "Already covered" means it exists in the repo or is named in Draft3 and has at least partial implementation. "Missing" means roadmap-level work to be sequenced.

| Lane | Already covered | Still missing | Where missing piece belongs |
|---|---|---|---|
| **SEC hard data** | Phase 6A (SEC EDGAR provider), Phase 7A (CompanyFacts XBRL parser, 13-tag allowlist, source-linked facts), Phase 14A coverage diagnostics. | Multi-period statements (TTM > 2 periods), comprehensive 10-K section extraction, period-selection policy beyond `_MAX_PERIODS_PER_TAG=2`, fallback parser evaluation. | Section 5 (SEC Company Fundamentals Lane Maturity Ladder). |
| **Evidence governance** | Phase 5 truth-adapter readiness contract, Phase 4 artifact observability, Phase 6B readiness aggregates. | Single Evidence Source Registry table with explicit registration, trust tiers, freshness SLAs, allowed-use flags, kill switches; promotion-gate audit log. | Section 7 (Signal Registry / Feature Store Extension). |
| **Valuation / PriceBand** | Phase 14B input verification, Phase 14C FY EPS earnings yield (shadow), Phase 14D PriceBand classification (shadow). | Sector/industry-aware bands (deferred until benchmark data lands), TTM EPS, multi-method valuation context (P/E history, peers, FCF yield, EV/EBITDA), governance-approved promotion to `DecisionInputV3.price_context`. | Section 5 (SEC ladder), Section 7 (registry), Section 14 (sequencing). |
| **LLM / finance agents** | Phase 1 Finance Agent Skill Pack Audit; Phase 3 Earnings Reviewer dark-run; research artifact contract; forbidden-key trigger. | Catalyst Watch, Valuation Context Reviewer, Risk Red-Team, Thesis Memory, Market Researcher, Analyst Expectations Reviewer, ETF/Fund Exposure, Narrative Shift Reviewer, Company KPI Reviewer; Debate Artifacts (bull/bear/what-changed/thesis-break). | Section 9 (Research Artifact Workers + Debate Layer). |
| **Quant validation / replay** | Conceptual mention in Draft3 (Phase 11 — Snapshot Projection / Replay / Decision Diff Governance v1). No implementation. | Decision-diff replay, signal tear sheets, false-positive analysis, drawdown impact, walk-forward checks, benchmark comparison vs SPY/QQQ/sector. | Section 8 (Quant Replay / Signal Validation Lab). |
| **Deploy sizing** | Draft3 Deploy contract: exact whole-dollar amount or `$0`. Not yet implemented. | Cash & deposit cadence inputs, target weights, concentration limits, tax-lot awareness, wash-sale guardrails, staged plans, manual-execution feedback. Optimizer research lane shadow. | Section 11 (Deploy Optimizer Research Lane). |
| **Watchtower alerts** | Draft3 specification (event-driven, email-first, four modes, alert format requirements). Not yet implemented. | Trigger registry, severity levels, cooldowns, snooze/ignore/executed feedback, action-change diffing, usefulness/false-positive tracking. | Section 12 (Watchtower Trigger Governance). |
| **Product redesign / UI IA** | `docs/ai/DESIGN_VISION.md` timing gate. Existing Intel v3 cards as baseline. | Cohesive surface map — Command Center, Intel, Holding Detail, Market Lab / Ask Intel, Deploy, Watchtower, Evidence Drawer, Decision History, Outcome Reflection, Admin/Governance Diagnostics. | Section 13 (Redesign-Ready Product IA). |

Reading the matrix: most missing pieces are **maturity layers on top of work already underway**, not new product surface area. That is the point — the cockpit grows by hardening existing lanes, not by importing tools.

---

## 4. External Tool Lessons Mapped Into Existing Lanes

Each reference is mapped to a stage of the unified spine (§2). No standalone "external tool" feature is created. **Absorb** = study and adopt patterns; **Defer** = revisit only when a named lane needs it; **Reject** = explicitly out of scope, with rationale.

### TradingAgents (multi-agent debate / role specialization)

- **Absorb:** the *role-segregated debate pattern* (analyst, risk, manager). Maps to Section 9's research workers + Debate Artifacts.
- **Defer:** any "trader" agent that produces final actions; its role is filled deterministically by `decide()`.
- **Reject:** auto-execution; broker-API integration; day-trading posture; live-market loop.
- **Lane:** Stage 3 — Research Artifact Workers (debate layer only).

### last30days-skill

- **Absorb:** rolling 30-day evidence-window framing for Catalyst Watch and Narrative Shift Reviewer artifacts.
- **Defer:** as a UI-facing product surface; if it appears, it appears as an evidence-backed artifact or as a Watchtower mode, not a standalone "last 30 days" page.
- **Reject:** treating recency as a substitute for trust-tiered evidence.
- **Lane:** Stage 3 (Catalyst Watch / Narrative Shift workers); Stage 7 (Watchtower Storm Mode inputs).

### OpenBB

- **Absorb:** the *provider-gateway abstraction* — many providers behind a uniform adapter contract. Maps to a future Phase 10–11 provider gateway evaluation in §14 sequencing.
- **Defer:** importing OpenBB as a runtime dependency. Evaluation only after Evidence Source Registry exists and a concrete second provider is justified.
- **Reject:** OpenBB as the visible decision engine; OpenBB-driven UI screens.
- **Lane:** Stage 1 — Ingestion / Lane Adapters (provider gateway pattern).

### Microsoft Qlib

- **Absorb:** the *signal/feature library + backtest harness* concept. Maps to Section 7 (Signal Registry / Feature Store) and Section 8 (Quant Replay Lab).
- **Defer:** Qlib's stack/runtime. Pattern only.
- **Reject:** Qlib's day-trading or HFT-style signals as visible decision inputs; alpha-seeking objectives that contradict long-term personal investing.
- **Lane:** Stage 2 (Signal Registry); Stage 4 (Eval & Replay).

### K-Dense scientific-agent-skills

- **Absorb:** the *skill packaging + reproducible artifact pattern* (skill = inputs + outputs + checks + examples). Already partly embodied in `.claude/skills/`. Maps to Section 9's per-worker skill contract.
- **Defer:** scientific-agent-skill patterns that imply autonomous research planning beyond bounded skills.
- **Reject:** any pattern that gives a skill final visible authority.
- **Lane:** Stage 3 — Research Artifact Workers (skill contract template).

### Anthropic financial-services

- **Absorb:** sourced, audited, plain-English finance-agent outputs. Already addressed in `Intel_v3_Architecture_Plan_Draft2_Anthropic_Finance_Agent_Addendum.md`. Maps to Section 9.
- **Defer:** Managed-Agents-as-default; reserved for explicit external evaluation later (Draft2 Phase 5).
- **Reject:** any pattern where the LLM is the visible decision engine.
- **Lane:** Stage 3 — Research Artifact Workers.

### Xynth AI trading tool

- **Absorb:** narrative + alternative-data context as *explanation/context*, not as decision authority.
- **Defer:** any signal-generation behavior until it passes Section 8 replay and Section 15 promotion gates.
- **Reject:** trade signals; options recommendations; auto-trading; day-trading posture.
- **Lane:** Stage 1 (lane adapter) → Stage 3 (research artifact); never Stage 5.

### EdgarTools / SEC parsing fallback candidates

- **Absorb:** richer XBRL parsing, multi-period statement support, normalization patterns. Maps to Section 5 ladder rung 3.
- **Defer:** adopting a third-party parser until current `sec_companyfacts_parser.py` hits a documented coverage / period-selection / normalization gap.
- **Reject:** wholesale replacement of the existing parser without a clear gap report.
- **Lane:** Stage 1 — SEC sub-lane.

### sec-edgar-downloader / sec-api or equivalent filing/section extraction candidates

- **Absorb:** filing-section extraction patterns (MD&A, risk factors, footnotes) for future Earnings/Risk/Catalyst workers.
- **Defer:** until §9 workers actually need section-level evidence beyond CompanyFacts metric observations.
- **Reject:** any path that bypasses source-linked accession provenance or stores raw filings in the artifact store.
- **Lane:** Stage 1 — SEC sub-lane (filing-text extraction); Stage 3 inputs.

### FinanceBench / Fin-RATE / FinRetrieval-style finance QA benchmarks

- **Absorb:** the *evaluation harness pattern* — typed QA tests for ticker/period/value/source correctness. Maps to Section 6.
- **Defer:** adopting an external benchmark wholesale; build a small repo-local harness first, then map external benchmarks into it.
- **Reject:** treating any external benchmark as proof of UI safety; UI safety still requires the claim-safety-gate skill.
- **Lane:** Stage 4 — Eval & Replay (Finance QA Eval Harness).

### vectorbt / PyBroker / Alphalens / QuantStats / LEAN / NautilusTrader / FinRL

- **Absorb:** signal tear sheets, replay primitives, drawdown/turnover diagnostics, walk-forward ideas. Maps to Section 8.
- **Defer:** reinforcement-learning-driven trading (FinRL) and event-driven HFT patterns (LEAN/Nautilus); cockpit horizon is long-term personal investing.
- **Reject:** any backtest harness used to derive visible Buy/Sell levels without operator-approved promotion through §15 gates.
- **Lane:** Stage 4 — Quant Replay / Signal Validation Lab (backend-only, shadow).

### PyPortfolioOpt / skfolio / Riskfolio-Lib

- **Absorb:** portfolio-construction *references* — risk-parity, CVaR, HRP, Black–Litterman framings. Maps to Section 11.
- **Defer:** runtime adoption. Cockpit Deploy must remain deterministic and emit an exact whole-dollar action; sophisticated optimizers can inform sizing research, not replace the contract.
- **Reject:** anything that returns a *range* to the user; final user-facing Deploy plan stays exact dollar or `$0`.
- **Lane:** Stage 6 — Deploy Optimizer Research Lane (shadow only).

### FinGPT / FinRobot

- **Absorb:** finance-tuned LLM patterns and multi-skill agent compositions for research artifacts. Maps to Section 9.
- **Defer:** any path that lets a finance-LLM drive `decide()` or Deploy.
- **Reject:** integration that requires LLM-as-final-decision authority.
- **Lane:** Stage 3 — Research Artifact Workers.

### Quiver Quantitative / Unusual Whales

- **Absorb:** alternative-data *context* — politician trades, insider trades, lobbying/contracts, options flow, dark-pool prints, app/search trends. Maps to Section 10.
- **Defer:** any signal that wants direct decision weight; soft/context lane only by default.
- **Reject:** day-trading triggers, options-trade recommendations, "follow the politician" automation.
- **Lane:** Stage 1 (lane adapter) → Stage 3 (sourced artifact) → Stage 4 (replay before any promotion).

### FinChat-style company intelligence / product benchmark

- **Absorb:** plain-English company Q&A surface as a *reference target* for the Market Lab / Ask Intel surface in §13. Quality bar: no hallucinated metrics, no unsupported claims, source-linked answers.
- **Defer:** building such a surface until the §6 Finance QA Eval Harness exists.
- **Reject:** any LLM-only Ask Intel that ships before the harness can certify it.
- **Lane:** Stage 8 — UI / Certified Snapshots (Market Lab / Ask Intel surface), gated by Stage 4.

---

## 5. SEC Company Fundamentals Lane Maturity Ladder

SEC ingestion is **already in production** as `sec_edgar_provider.py` + `sec_companyfacts_parser.py` + `earnings_sec_adapter.py`. This ladder defines how the SEC sub-lane matures without adopting a fallback parser before evidence justifies it.

**Rung 1 — current parser path (in production).**
SEC EDGAR `company_tickers.json` + `submissions/CIK{cik}.json` + `companyfacts/CIK{cik}.json`. 13-tag us-gaap allowlist. Max 2 periods per tag. Source-linked accession only. Fail-closed. Public JSON only. **First path remains here.**

**Rung 2 — coverage and source-linked-fact diagnostics (in production).**
Phase 14A and 14B aggregate diagnostics already report which tickers / periods / shapes are present, missing, or rejected. Phase 14C.1 added shape-A/B/C extraction. **Required before considering Rung 3.**

**Rung 3 — explicit gap diagnostics first.**
Before evaluating any third-party parser or paid API, the gap must be documented as a diagnostic: which periods, which tags, which forms, which normalization issues, which multi-period statement gaps. Decisions to advance to Rung 4 are operator-approved against these diagnostics, not against a feature wishlist.

**Rung 4 — EdgarTools or similar XBRL tooling (evaluation only).**
Allowed only when Rung 3 documents clear coverage, period-selection, normalization, or multi-period-statement limits in the current parser. Evaluation includes: provenance handling, source-linked accession preservation, fail-closed behavior, license, latency, dependency footprint. Adoption requires a separate PR with operator approval.

**Rung 5 — paid APIs (sec-api / equivalent) — last resort.**
Allowed only when open-source paths at Rungs 1–4 are documented as insufficient or too costly/brittle. Cost/latency budgets and kill-switch must be defined before flag enablement.

**Hard rule across all rungs:** SEC data — current or future — does **not** affect visible decisions until §15 promotion gates pass. PriceBand stays shadow-only. Earnings yield stays diagnostics-only. `safe_for_decision = False` is preserved at the DB layer.

---

## 6. Finance QA Eval Harness

A dedicated eval harness is required **before** any LLM/agent research artifact receives serious UI weight. It is a Stage-4 module (Eval & Replay).

The harness must be able to assert, per artifact under test:

- **Correct ticker / entity** — answer is bound to the ticker requested; no ticker drift.
- **Correct period** — fiscal year, fiscal quarter, or calendar window matches the question.
- **Correct fiscal year / date** — uses the same fiscal-year convention as our SEC parser; no off-by-one.
- **Correct numeric value** — value matches a source-linked fact (e.g., a `research_artifact_facts` row).
- **Correct source / citation** — citation resolves to an accession, URL, or registered source id; no orphan citations.
- **Correct comparison across time** — when asked "vs prior year", the prior period is real and source-linked.
- **Correct comparison across companies** — peer comparisons honor sector/industry registry and use comparable periods.
- **Refusal / honesty when evidence is missing** — explicit "evidence missing / unavailable" rather than fabricated value.
- **No hallucinated metrics** — every metric mentioned is drawn from a registered tag/source.
- **No unsupported claims in user-facing explanations** — every visible sentence traces to certified evidence.

The harness lives backend-only and produces aggregate-only pass rates. Per-question raw outputs are operator-only. UI weight for any artifact class — Earnings Reviewer, Catalyst Watch, Valuation Context Reviewer, etc. — requires a documented harness pass rate plus operator approval.

This explicitly precedes §9 worker promotion. Workers may run dark-run before the harness exists; they may not surface in the UI before it exists.

---

## 7. Signal Registry / Feature Store Extension

Draft3 names Phase 10 as "Evidence Source Registry v1 / Multi-Lane Governance v1". This section extends that concept into a Signal Registry / Feature Store with explicit metadata. It is a Stage-2 module (Source Governance).

Every signal — SEC metric, valuation ratio, PriceBand class, analyst expectation, narrative score, options-flow indicator, ETF-overlap percentage — must eventually carry:

- **`source_id`** — primary key in the registry.
- **`evidence_lane`** — one of: SEC fundamentals, valuation context, market behavior, analyst expectations, transcripts/guidance, news/event, sector/macro, ETF/fund exposure, portfolio exposure, thesis memory, alternative data.
- **`trust_tier`** — primary / secondary / supplementary / experimental; tier governs allowed use.
- **`freshness_sla`** — maximum acceptable staleness in days/hours; weak/missing/stale degrades or suppresses use.
- **`provider_adapter`** — ingestion module reference.
- **`cost_latency_budget`** — per-call time and dollar ceiling; hard cap; circuit-break beyond ceiling.
- **`historical_coverage`** — date range with ≥X% coverage; per-asset-type breakdown.
- **`asset_type_applicability`** — equities / ETFs / ADRs / crypto-equity proxies; signal is muted outside applicability.
- **`benchmark_replay_status`** — has the signal been replayed (§8)? what tear sheet?
- **`promotion_status`** — `experimental` / `shadow_only` / `decision_eligible` / `deploy_eligible` / `watchtower_eligible`.
- **`kill_switch`** — explicit feature-flag handle to disable consumption immediately without code change.
- **`allowed_use`** — `explanation_only` / `shadow_only` / `decision_eligible` / `deploy_eligible` / `watchtower_eligible`. Multiple values allowed; *visible-decision* use requires `decision_eligible` plus a satisfied promotion gate (§15).

Implementation note (documentation only): the existing `research_artifact_sources` and the implicit per-lane provenance structures (SEC `SourceRecord`, `ReadOnlyEvidenceAdapter`, Phase 5 readiness contract) are the seed inputs. A first-pass Signal Registry can be additive (no migration of existing source rows) and start with SEC metric tags, market_snapshots, and PriceBand classes. Promotion of any signal to `decision_eligible` flows through §15.

---

## 8. Quant Replay / Signal Validation Lab

Stage-4 module. Backend-only. Existing Draft3 Phase 11 ("Snapshot Projection / Replay / Decision Diff Governance v1") is renamed in spirit to a broader "Quant Replay / Signal Validation Lab", but **does not reset Phase 11 progress** if any has begun.

Required capabilities:

- **Decision-diff replay** — given two certified snapshots S0 and S1, produce a diff of (ticker, action, conviction, why) for review. Catches silent decision drift.
- **Signal tear sheets** — per signal: hit rate, false-positive rate, drawdown contribution, turnover induced, confidence calibration, coverage by asset type and time period.
- **Benchmark comparison** — per signal-driven decision set: total return vs SPY / QQQ / sector ETF / no-action baseline. Long horizons only. No alpha-chasing claims.
- **False-positive analysis** — frequency and severity of "act now" signals that resolve as wrong-way moves.
- **Drawdown impact** — worst-case loss attributable to acting on the signal.
- **Turnover / action frequency** — does the signal cause too many actions for a long-term cockpit?
- **Coverage and missing-data analysis** — what fraction of holdings would the signal apply to, and where would suppression dominate?
- **Walk-forward / out-of-sample checks** — when applicable; the cockpit's horizon is long-term, so naive in-sample fits are insufficient.

**Hard rule:** replay results are advisory only. No visible decision consumption changes until replay evidence is reviewed and operator-approved (§15). Tear sheets are operator-only artifacts; the UI never exposes them.

---

## 9. Research Artifact Workers + Debate Layer

Stage-3 module. Extends Phase 1 Audit + Phase 3 Earnings Reviewer + Draft3 §"Finance-agent role" into a richer skill library, mapped from TradingAgents / Anthropic / FinRobot / FinGPT / K-Dense lessons.

Each worker is bounded by the same skill contract. Per worker, define:

- **inputs** — source-linked facts/metrics, holding context, prior artifacts.
- **outputs** — structured artifact rows; never visible decisions or actions.
- **artifact schema** — the typed shape stored in `research_artifacts` + `research_artifact_facts` + `research_artifact_sources`.
- **examples** — golden examples for the §6 Finance QA Eval Harness.
- **validation checks** — forbidden-key trigger, source-linkage check, freshness check, plain-English filter.
- **failure modes** — empty input, stale evidence, conflicting sources, provider error, parser miss.
- **source / citation requirements** — every claim cites a registered source; no unlinked claims.
- **cost / time budget** — per-run wall-clock and dollar ceiling.
- **explicit non-authority over final decisions** — restated in the worker's own header.

Future workers (deferred behind this contract):

- **Earnings Reviewer** — already running dark; extend to guidance/surprise/margin/demand artifacts.
- **Analyst Expectations Reviewer** — consensus, revisions, dispersion, expectation-vs-actual narrative; never a price target.
- **Valuation Context Reviewer** — multi-method context (P/E history, peers, FCF yield, EV/EBITDA, growth-adjusted); never a fair value.
- **Risk Red-Team** — counter-thesis, concentration, balance-sheet quality, drawdown drivers.
- **Catalyst Watch** — windowed earnings/product/regulatory/macro events, with probability/uncertainty language only.
- **Thesis Memory Reviewer** — per-holding thesis pillars and falsification triggers.
- **Market Researcher** — sector/theme context, peer set assembly, no idea-pick authority.
- **ETF / Fund Exposure Reviewer** — overlap and concentration findings.
- **Narrative Shift Reviewer** — news/transcript narrative deltas, never a sentiment-driven action.
- **Company KPI Reviewer** — segment KPIs, customer concentration, unit economics where source-linked.

### Debate Artifacts

Adds a typed *debate* artifact class. Per artifact:

- **bull case** — strongest evidence-backed arguments to add or hold.
- **bear case** — strongest evidence-backed arguments to trim or sell.
- **what changed** — diff vs prior debate artifact for the same ticker.
- **evidence missing** — explicit gaps; absence is information.
- **thesis-break risk** — conditions that would invalidate the dominant case.
- **deterministic policy conclusion** — the actual `decide()` output for transparency, *not* derived inside the artifact.
- **why action was suppressed (if suppressed)** — explanation of which axis was suppressed by missing/stale/weak data.

Debate artifacts are explanation surfaces, never decision surfaces. They feed the Evidence Drawer and Decision History (§13), not `decide()`.

---

## 10. Alternative Data / Narrative / Flow Lane

Stage-1 ingestion + Stage-3 research artifact, **never** Stage-5 deterministic policy authority. Maps Xynth, Quiver Quantitative, Unusual Whales, news/social attention, options flow, dark-pool prints, politician/insider trades, lobbying & government contracts, app trends, search trends, and similar references.

Rules — non-negotiable:

- **Default use is `explanation_only` or `shadow_only`.** Watch / risk / catalyst context only.
- **Corroboration required.** A single alt-data signal cannot drive any decision-eligible promotion; corroboration with a primary lane (SEC fundamentals, valuation, analyst expectations) is required.
- **Replay required before promotion.** §8 tear sheets must show non-trivial information beyond the primary lane, with bounded false-positive and turnover impact.
- **No direct Buy/Sell authority.** Ever. Even after promotion, the deterministic policy decides; alt data is at most an axis input via a registered, governed signal.
- **No day-trading posture.** No intraday triggers. No "buy the dip in 5 minutes" signals.
- **No options-trade recommendations.** Even if the underlying source is options-flow, the cockpit emits no options strategies.

Storage and provenance follow the same source-linked artifact contract as primary lanes. Forbidden visible-decision keys remain rejected at write time.

---

## 11. Deploy Optimizer Research Lane

Stage-6 module. Extends Draft3's Deploy contract.

In-flight constraint (already in Draft3, restated): user-facing Deploy plans must say an **exact whole-dollar amount or `$0` / no action**. Internal computation may use ranges; the user-facing artifact picks one.

Future shadow-only research lanes:

- **Exact whole-dollar sizing** — turning intent + cash + bands into one number.
- **Cash / deposit cadence** — biweekly deposit awareness; no rebalancing claims that ignore deposit timing.
- **Portfolio target weights** — per-holding targets and tolerances.
- **Concentration limits** — per-name, per-sector, per-theme caps.
- **Tax-lot awareness** — short-term vs long-term, lot selection.
- **Wash-sale guardrails** — recent-loss tracking that vetoes immediate re-buys.
- **Risk parity / CVaR / HRP / Black-Litterman-style references** — research framing only; no runtime adoption without operator approval.
- **Staged action plans** — multi-step plans with trigger conditions ("Buy $300 of GOOGL if it crosses $X with evidence still strong").
- **Manual execution feedback** — executed / snoozed / ignored states feed back into Deploy + Watchtower (§12).

Hard rules:

- **Deploy final output stays exact dollar or `$0`/no action.** No vague ranges in user-facing plans.
- **No auto-trading.** Ever.
- **Deterministic sizing policy owns the final output.** Optimizer research informs sizing logic; it does not own it.
- **No broker integration.** No order-routing, no Robinhood automation. Manual execution only.

---

## 12. Watchtower Trigger Governance

Stage-7 module. Extends Draft3 Watchtower with explicit trigger governance.

Required components:

- **Trigger registry** — every trigger registered with: trigger id, trigger lane, severity ceiling, cooldown, suppression rules, registered signal dependencies, kill switch.
- **Alert severity levels** — info / watch / risk-review / action; the "action" severity is the only one that should suggest exact deploy amounts.
- **Cooldowns** — per (ticker, trigger) cooldown to avoid same-evidence repeats.
- **Snooze / ignore / executed feedback** — user feedback rolls back into trigger calibration.
- **Action-change diffing** — alerts on diffs vs the prior certified snapshot, not on absolute states.
- **Usefulness tracking** — per-trigger hit rate (executed) vs noise rate (ignored), aggregated per Watchtower mode.
- **False-positive tracking** — explicit metric; high false-positive triggers degrade automatically.
- **Rare event-driven email-first behavior** — alerts only when meaningful actionability thresholds are crossed.
- **No noisy daily digest behavior.** Ever. No "5 minor signals" emails.

Watchtower modes from Draft3 — Calm / Active / Storm / Deploy — remain intact. Trigger governance is the implementation substrate that prevents mode drift.

---

## 13. Redesign-Ready Product Information Architecture

The future redesign is **not implemented here**. This section defines the surface map so that, when the Design Vision timing gate is satisfied, the redesign flows from certified snapshots and registered artifacts — not from in-flight diagnostics.

| Surface | User job-to-be-done | Data source | Why it depends on certified snapshots / artifacts | Must not expose |
|---|---|---|---|---|
| **Command Center** | "What should I be paying attention to right now across my whole portfolio?" | Latest certified `intel_v3_snapshots` + Watchtower active triggers + Deploy availability. | Mixes deterministic action distribution with rare actionable triggers; both must be certified to avoid noise. | Raw metric keys, posture labels, diagnostic counters, shadow-only signals. |
| **Intel** | "What is the action and why for each holding?" | Certified snapshot cards. | Visible Buy/Hold/Trim/Sell must come from `decide()` only. | Raw EPS, raw earnings yield, raw PriceBand class while shadow-only, parser/coverage diagnostics. |
| **Holding Detail** | "Show me the full evidence and debate behind this ticker." | Certified snapshot card + linked research artifacts + Evidence Drawer. | Evidence depth must be source-linked and trust-tiered. | Unlinked claims, unsourced numbers, raw provider payloads. |
| **Market Lab / Ask Intel** | "Help me understand a company, peer set, or theme." | Registered signals + research artifacts + §6 Finance QA Eval Harness-passed answers. | Plain-English Q&A that can otherwise hallucinate; harness-gated. | Hallucinated metrics, unsupported claims, internal tag names. |
| **Deploy** | "Exactly how should I act with the cash I have?" | Certified Intel snapshot + cash inputs + bands + tax/wash-sale state. | Exact dollar amounts; ranges are unsafe. | Vague ranges, optimizer internals, broker integrations. |
| **Watchtower** | "What event-driven changes should I act on?" | Trigger registry outputs + alert severity. | Avoid noisy digest; preserve email-first contract. | Daily digests, raw trigger metrics, internal cooldown counters. |
| **Evidence Drawer** | "Where did this come from?" | Source-linked artifacts + registry metadata. | Provenance is the antidote to fabricated confidence. | Unregistered sources, raw URLs without context, provider-internal ids without trust tier. |
| **Decision History** | "What changed across snapshots and why?" | Snapshot diff + debate artifacts + outcome notes. | Replayable decision diff is the spine of trust. | Drift-causing untracked side outputs. |
| **Outcome Reflection** | "What worked, what didn't, what to learn?" | Executed/snoozed/ignored feedback + realized outcomes (manual). | Personal long-horizon learning loop without trade-execution claims. | Performance-claim language that implies prediction-oracle behavior. |
| **Admin / Governance Diagnostics** | Operator-only: "Is the spine healthy?" | Aggregate observability endpoints + promotion-gate audit log + kill-switch states. | Operator surface stays separate so the user UI can stay plain-English. | Any leakage of operator diagnostics into the user UI. |

UI-IA invariants:

- Plain-English everywhere. No raw metric keys, posture labels, shadow flags, or trust-tier strings in user-facing copy.
- Suppression is a first-class state. "Evidence incomplete on AMD" is correct; fabricated confidence is not.
- Every visible card is replayable. Decision History must be able to reconstruct the why.
- The operator surface is separate. Diagnostics never bleed into the user UI.

---

## 14. Updated Sequencing

This sequencing **reconciles** Draft3's post-Phase-9 list with current Phase 14 reality. It does not replace prior phase numbers. Where Draft3 named a future module (e.g., Phase 10 Evidence Source Registry, Phase 11 Replay), the same module is honored and renamed only when scope is widened (e.g., Replay Lab in §8).

1. **Finish current SEC / valuation / PriceBand shadow path.** Phases 14B, 14C, 14C.1, 14D are operator-validated in production; coverage diagnostics inform Rung 3 of the §5 SEC ladder.
2. **Keep PriceBand shadow-only** until §15 promotion gates pass. No DecisionInputV3 wiring. No UI exposure.
3. **Status-reconcile the Evidence Source Registry with current Phase 14 work.** Treat the existing partial source governance (SEC `SourceRecord`, `ReadOnlyEvidenceAdapter`, Phase 5 readiness, market_snapshots certification) as Phase-10-precursor inputs; do not start over.
4. **Add Signal Registry / Feature Store metadata** (§7) — additive table, seed with SEC tags, market_snapshots, PriceBand class, FY EPS earnings yield diagnostic.
5. **Add Finance QA Eval Harness** (§6) — backend-only, golden cases drawn from SEC observation set.
6. **Add Replay / Decision Diff / Signal Tear Sheet governance** (§8) — operator-only diagnostics.
7. **Add provider gateway evaluation** — pattern only; OpenBB and others evaluated, not adopted.
8. **Add finance skill library and debate artifacts** (§9) — extend Earnings Reviewer; add Catalyst Watch, Valuation Context Reviewer, Risk Red-Team, Thesis Memory Reviewer behind flags.
9. **Add company KPI lane** — segment-level facts via §5 ladder rung 4 if needed.
10. **Add narrative / alternative-data lane, shadow-only** (§10) — corroboration and replay required before any promotion.
11. **Add Deploy optimizer shadow lane** (§11) — exact whole-dollar contract preserved.
12. **Add Watchtower trigger governance** (§12) — registry, severity, cooldowns, feedback.
13. **Only then do the full redesign** around Command Center / Intel / Market Lab / Deploy / Watchtower (§13). Designing earlier would lock in shadow surfaces.

Steps 1–3 are mostly hardening; steps 4–8 are the maturity layers that raise the roadmap ceiling; steps 9–12 are lane breadth; step 13 is the visible product transformation.

---

## 15. Promotion Gates

Before any new signal affects visible Buy/Hold/Trim/Sell, Deploy, or Watchtower alerts, **all** of the following must be satisfied. The gate is binary and operator-approved.

- **Source registered** — present in the Signal Registry (§7) with a stable `source_id`.
- **Trust tier assigned** — primary / secondary / supplementary / experimental; not `experimental` for visible use.
- **Freshness SLA defined** — explicit; suppression behavior on stale verified.
- **Cost / latency budget defined** — per-call ceiling and circuit-break verified.
- **Historical coverage known** — by date range and asset type; gaps documented.
- **Data quality measured** — null/zero/negative/conflict rates; suppression rules tested.
- **Replay / backtest completed where applicable** — §8 tear sheet attached.
- **False-positive and drawdown impact reviewed** — per-signal, not aggregate hand-waving.
- **Decision diff reviewed** — sample snapshots S0/S1 reviewed, no silent drift.
- **Plain-English explanation approved** — claim-safety-gate skill passes; no raw metric keys.
- **Operator approval recorded** — explicit, with date and reviewer.
- **Kill switch available** — feature-flag handle exists and has been exercised.
- **Tests added** — unit + harness + integration as appropriate; existing certification surfaces still pass.
- **No hidden LLM authority** — no path lets an LLM produce visible Buy/Hold/Trim/Sell, Deploy amount, or Watchtower trigger.

Failure to satisfy any one gate keeps the signal `shadow_only`. There is no expedited path.

---

## 16. Anti-Drift Rule

User idea dumps and external tool references are **strategic input, not automatic feature requests.**

For every new idea or external tool that arrives via prompt, link, or research note:

- **Map** it into an existing lane / stage of the unified spine (§2). If it cannot be mapped, it is not a roadmap item.
- **Defer** with rationale if it belongs to a lane that is not yet ready (e.g., Narrative Shift Reviewer before §6 harness exists).
- **Reject** explicitly if it violates an invariant (auto-trading, day-trading posture, LLM final authority, options recommendations, vague Deploy ranges, daily digests).

The cockpit is allowed to evolve; it is not allowed to chase. Newer tools may inform a lane's *implementation*, but they cannot create *new visible surfaces* outside the §13 IA without going through §14 sequencing and §15 promotion gates.

When in doubt, the fastest correct response is to cite this addendum and the existing lane; the second-fastest is to defer with a one-line rationale; the slowest is to charter a new module — and that should rarely be the answer.

---

## Cross-references

- `artifacts/Intel_v3_Architecture_Plan_Draft2_Anthropic_Finance_Agent_Addendum.md` — Anthropic finance-agent direction, original Phase 0–5 sequencing, prompting rule for build prompts.
- `artifacts/Intel_v3_Architecture_Plan_Draft3_Living_Investment_Cockpit_Addendum.md` — current canonical north star (cockpit, lanes, Deploy contract, Watchtower contract, post-Phase-9 sequencing).
- `docs/ai/INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md` — Phase 1 spec; per-skill allowed/forbidden outputs; worker boundary contract.
- `docs/ai/INTEL_V3_RESEARCH_ARTIFACT_STORE_V1.md` — Research Artifact Store schema and forbidden-key contract.
- `docs/ai/INTEL_V3_TRUTH_ADAPTER_READINESS_CONTRACT.md` — Phase 5 readiness 12-condition contract.
- `docs/ai/HANDOFF.md` — Phase 14A–14D entries (current-state ground truth).
- `v2/progress_log.md` — historical progression of Phases 6A through 14D.
- `docs/ai/EXECUTION_PRINCIPLES.md` — think-before-coding, simplicity-first, surgical changes; this addendum is the long-form companion.
- `docs/ai/DESIGN_VISION.md` — UI redesign timing gate.

---

## Closing invariant

> Deterministic backend policy is the only authority for visible Buy/Hold/Trim/Sell.
> Research workers, LLMs, agents, external tools, and alternative data may produce sourced artifacts only.
> No auto-trading. No broker execution. No day-trading pivot. No vague Deploy ranges.
> Missing / stale / weak / conflicting evidence suppresses; it never fabricates.
> Current SEC / valuation / PriceBand work continues; this addendum does **not** pause, replace, or duplicate it.
