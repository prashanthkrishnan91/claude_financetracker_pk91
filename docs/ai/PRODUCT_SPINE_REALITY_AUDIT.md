# Product Spine Reality Audit — Stage 10A

**Date:** 2026-06-11
**Mode:** audit / planning only. No implementation, UI, SQL, provider migration, artifact writes, synthesis changes, or Buy/Hold/Trim/Sell behavior changes.
**Purpose:** Map the revised product spine onto the actual v2 codebase, decide what is truly usable today, and choose the next 3–5 implementation PRs from code evidence — not roadmap assumption.

This document is grounded in a direct read of `v2/backend` code plus `docs/ai/HANDOFF.md`, `docs/product/{NORTH_STAR,ROADMAP,BUILD_QUEUE}.md`. File:line references are illustrative anchors, not exhaustive.

**Evidence standard:** This audit is based on static code/docs inspection. It identifies implemented paths and gaps; it does not certify runtime behavior unless runtime evidence is explicitly cited.

---

## Executive verdict

**Implemented / code-wired today (not runtime-certified by this PR):**

- **Books of record (F1)** — positions/transactions/deposits/DRIP/Plaid/CSV are real and wired. Positions table is the holdings source of truth; cost basis is weighted-average (AVCO); Plaid is authoritative on sync with a 24h cache; CSV import dedups (SHA-256) and reconciles. This is the most under-celebrated, genuinely-implemented layer (per static inspection).
- **Data backbone (F2)** — multi-source concurrent price engine (yfinance + Finnhub + Alpaca + Polygon + CoinGecko) with per-source timeout, 5-minute cache, and circuit breaker. Evidence providers (sec_edgar OFFICIAL, fred OFFICIAL, yfinance unofficial) are wired read-only.
- **Honest Intel (Stage 1)** — by far the most mature area. Deterministic `decision_policy_v1` owns visible Buy/Hold/Trim/Sell. Suppression, freshness SLAs, source credibility bands, and evidence-aware governance all exist. `safe_for_decision` is held False by design in the shadow adapter.
- **Labeled advisory analyst (Stage 4)** — single-LLM orchestrator produces conviction + narrative; action is deterministic post-hoc; conviction is capped by data quality; banned-indicator-language validator is enforced. The LLM is genuinely explanatory-only.
- **Deploy exact-dollar math (Stage 2, partial)** — allocation/adaptive/deployment engines plus `deploy_dollar_math_v1` produce exact-dollar buy/trim/sell with rounding + cash-residual handling and a cash guardrail. Documented as Stage 2 exit / previously validated per repo docs ($900/$1,500 flows, BUILD_QUEUE); not revalidated in this PR.

**Not usable today (gap / scaffold / absent):**

- **Tax & wash-sale safety** — *not implemented*. `tax_guardrail_status` / `wash_sale_guardrail_status` are literal `"not_evaluated_yet"` placeholders; every actionable Deploy item terminates in `ACTIONABLE_PENDING_TAX`, never `ACTIONABLE`. There is no tax-lot accounting; `lt_eligible`/`lt_date` exist on positions as fields with no enforcing logic.
- **VTI-only benchmark truth (Stage 3)** — *absent as a user-facing capability*. `benchmark.py` fetches SPY price-action as a **decision signal input only**. There is no deposit-history-vs-hypothetical-VTI-DCA comparison anywhere.
- **Behavioral discipline (Stage 5)** — concentration caps + data-quality caps + cash constraint exist, but there is **no overtrading / panic / cooldown discipline in the main decision or deploy path** (cooldowns live only in the alert-suppression layer).
- **Watchtower delivery (Stage 6)** — candidate generation + outbox are code-wired / implemented; real email send is parked behind Resend domain verification; push/in_app channels are scaffold.
- **Teaching layer (Stage 7)** — not built; exists only as "Coming-Later" chrome and Stage 6E–6H build-queue items.
- **Premium polish (Stage 8)** — design foundation shipped (Stage 4A/4B/4C); polish is correctly gated behind a stable decision/action loop.

**One-line verdict:** The cockpit *spine is real from Books → Backbone → Intel → labeled advisory → exact-dollar Deploy math*. The two true gaps blocking an honest, usable cockpit are **(1) tax/wash-sale safety before action** and **(2) a user-facing VTI-only benchmark of truth**. Neither requires more ETF certification or a provider migration.

---

## Product spine map

Legend — Risk: ⬛ low / 🟧 medium / 🟥 high. Readiness: ✅ wired / 🟡 partial / ⛔ absent.

### F1 — Books of Record  ✅  Risk ⬛

- **Existing assets:** `models/{position,transaction,deposit,drip,portfolio}.py`; `services/portfolio_service.py` (get_summary, snapshots, rebalance, backfill), `portfolio_engine.py` (pure AVCO cost-basis math), `plaid_service.py` (sync, 24h cache, audit log, sold-position removal), `import_service.py` (CSV SHA-256 dedup + post-import reconciliation), `deposit_service.py`, `drip_service.py`, `routers/sync.py`.
- **Current readiness:** Code-wired / implemented. Positions table = holdings source of truth. Plaid authoritative for shares + cost_basis on sync; manual + crypto-PDF positions preserved. Cost basis is weighted-average (one bucket per ticker).
- **Missing gaps:** No lot-level cost basis (blocks real tax-lot/wash-sale later). No cross-source reconciliation *audit surface* — Plaid simply wins on conflict; there is no visible drift report between Plaid holdings and the transaction-derived ledger. DRIP dividend dates come from yfinance with no fallback (best-effort).
- **Entry gate:** none (foundational).
- **Exit gate:** Per-position source provenance + cost-basis integrity are inspectable; Plaid-vs-transaction drift is detectable. → enables `facts_ready`.
- **Recommended next action:** A **read-only books integrity / reconciliation diagnostic** (no writes) to prove F1 is trustworthy before benchmark/tax build on top of it. See PR-1.

### F2 — Data Backbone  ✅  Risk ⬛

- **Existing assets:** `services/price_engine.py` (concurrent stock sources yfinance/Finnhub/Alpaca/Polygon; crypto CoinGecko/yfinance; 8s timeout, 5-min cache, 3-fail/300s circuit breaker), `price_service.py`, `services/market_data/`, evidence registries `intelligence/research_workers/evidence_provider_registry_v1.py` (sec_edgar/yfinance/fred) and `intelligence/v3/provider_registry_v1.py`.
- **Current readiness:** Code-wired / implemented. Price refresh races multiple sources; first valid wins; stale-cache fallback only when all sources circuit-broken.
- **Missing gaps:** DRIP dividend-date path is yfinance-only. No single-provider hard dependency on the price hot path.
- **Entry gate:** none.
- **Exit gate:** Concurrent design is intended to tolerate a single-source outage; not runtime-revalidated in this PR.
- **Recommended next action:** **None urgent.** Specifically, *yfinance is not a sole critical-path dependency* — see "yfinance criticality" below. Do not migrate to Finnhub.

### Stage 1 — Honest Intel  ✅  Risk 🟧 (highest-value, already built)

- **Existing assets:** `intelligence/v3/decision_policy_v1.py` (deterministic action authority, no composite score), `decision_contracts.py` (sanitized plain-English output, forbidden raw-metric keys), `intel_v3_evidence_aware_governance_v1.py` (Stage 6), `research_evidence_decision_input_adapter_v1.py` (Stage 5K shadow readiness, `safe_for_decision=False` immutable), `watchtower_freshness_ledger_v1.py` (per-type SLAs), `watchtower_deploy_gate_v1.py`, `source_credibility_registry_v1.py`.
- **Current readiness:** Code-wired / implemented and mature. Source/freshness/suppression/blockers are surfaced in plain English; LLM cannot own visible action.
- **Missing gaps:** None blocking. `safe_for_decision` intentionally False; evidence-aware governance is flag-gated.
- **Entry gate:** Intel v3 backend policy in place — met.
- **Exit gate:** Intel v3 Certification Gate (decisions certified, no LLM final authority, honest gaps) — substantially met per HANDOFF.
- **Recommended next action:** Leave alone. Do **not** keep iterating ETF/evidence certification (Stage 9O closed `issuer_official_vanguard` as rejected). Treat Intel as a stable input to Deploy/Benchmark.

### Stage 2 — Deploy + Tax Safety  🟡  Risk 🟥 (tax safety is the gap)

- **Existing assets:** `allocation_engine.py`, `adaptive_deployment.py`, `deployment_engine.py`, `deploy/deploy_dollar_math_v1.py`, `deploy/deploy_sizing_source_adapter_v1.py` (reads `portfolio_snapshots` + `target_allocations` + Settings), `deploy/deploy_cash_guardrail_v1.py`, `deploy/deploy_finalization_v1.py`, `deploy/deploy_contracts.py`, `routers/deploy_v3.py`. Exact-dollar + rounding + cash-residual documented as Stage 2 exit / previously validated per repo docs ($900/$1,500); not revalidated in this PR.
- **Current readiness:** Exact-dollar sizing is code-wired / implemented (documented Stage 2 exit per repo docs; not revalidated here). Allocation is user-driven via `target_allocations` (theme map is only an example/enrichment layer, not the source of truth).
- **Missing gaps:** **Tax & wash-sale evaluation is absent.** Items terminate in `ACTIONABLE_PENDING_TAX`; `tax_guardrail_status`/`wash_sale_guardrail_status` = `"not_evaluated_yet"`. No tax-lot model; `lt_eligible`/`lt_date` unenforced. This is the single biggest "before action" safety gap.
- **Entry gate:** Stage 1 certified Intel decisions (met).
- **Exit gate:** Deploy Readiness Gate *plus* an honest tax/wash-sale verdict before any item is labeled fully actionable.
- **Recommended next action:** Tax safety is **design-gated** (BUILD_QUEUE: "requires explicit tax-lot / trade-history source decisions before any build"). Next step is a **tax-lot source design + contract PR**, not an implementation. See PR-3.

### Stage 3 — VTI Benchmark Truth  ⛔  Risk 🟧

- **Existing assets:** `intelligence/benchmark.py` — SPY price-action fetcher, **signal input only**, 5-min cache, fails to `{}` silently.
- **Current readiness:** No user-facing benchmark. `benchmark.py` cannot answer "how does my actual deposit history compare to VTI-only DCA."
- **Missing gaps:** Needs (a) user deposit/transaction history (already in F1), (b) hypothetical VTI-only DCA cost basis built from the same deposit cadence, (c) a read-only comparison surface. All inputs exist in F1 + F2; this is net-new, read-only, low-risk composition.
- **Entry gate:** `facts_ready` (F1 trustworthy).
- **Exit gate:** A plain-English VTI-only DCA-vs-actual comparison that never fabricates returns and degrades honestly on missing deposit history.
- **Recommended next action:** Build a read-only VTI-only DCA benchmark on top of existing deposit history. See PR-2.

### Stage 4 — Labeled Advisory Analyst  ✅  Risk 🟧

- **Existing assets:** `services/agents/orchestrator.py` (5-phase, ≤1 LLM call/run), `agents/llm.py` (Sonnet→Haiku failover), `intelligence/per_ticker_analyst.py` (banned-language validator, INSUFFICIENT_DATA fallback), `intelligence/portfolio_synthesis.py` (deterministic fallback), `agents/portfolio_manager.py` (concentration caps), migrations 010/011 (`analyst_verdict`, `portfolio_synthesis` JSONB).
- **Current readiness:** Code-wired / implemented. Conviction capped by data quality; action derived deterministically; LLM output is explanatory-only and persisted as labeled advisory.
- **Missing gaps:** None blocking. The advisory attach-point already exists (`analyst_verdict` / `portfolio_synthesis`).
- **Entry gate:** Intel deterministic authority (met).
- **Exit gate:** LLM never owns visible action; advisory clearly labeled — met.
- **Recommended next action:** When attaching Claude to Deploy/Benchmark, attach **only at the narrative/why layer** of `analyst_verdict`/synthesis. Do not let Claude compute numeric indicators or sizing.

### Stage 5 — Discipline / Behavioral Guardrails  🟡  Risk 🟧

- **Existing assets:** concentration caps (`portfolio_manager.py`, soft 10%/hard 20%), data-quality conviction caps (`orchestrator._confidence_cap_for`), cash constraint (`deploy_cash_guardrail_v1.py`), alert-layer cooldowns (`alert_trigger_policy_v1.py`, `action_feedback_service.py`).
- **Current readiness:** Partial. Position-sizing discipline exists; behavioral "before you act" discipline (overtrading, panic-sell, churn cooldown) does **not** exist in the decision/deploy path.
- **Missing gaps:** No pre-action behavioral guardrail; tax safety (Stage 2) is the prerequisite safety layer.
- **Entry gate:** Stage 2 tax safety scaffolding.
- **Exit gate:** A user about to act gets honest tax + behavioral friction before execution.
- **Recommended next action:** Defer until Stage 2 tax safety lands. Do not build speculative cooldowns first.

### Stage 6 — Watchtower / Rare Alerts  🟡  Risk 🟧

- **Existing assets:** `alert/watchtower_alert_candidate_hook_v1.py`, `alert/alert_trigger_policy_v1.py` (pure deterministic), `alert/alert_candidate_service.py`, `alert/alert_delivery_outbox_service.py`, `alert/alert_email_delivery_worker_v1.py` (Resend), routers + models, Watchtower loop wired as a Railway PROCESS_TYPE (per repo docs).
- **Current readiness:** Candidate generation + outbox = code-wired / implemented. Real email send parked behind Resend domain verification (dry-run passing per repo docs). Push/in_app = scaffold.
- **Missing gaps:** Real-send activation (Stage 5M in BUILD_QUEUE) — operational, not a code gap.
- **Entry gate:** Stage 2/Deploy loop stable.
- **Exit gate:** Alert Readiness Gate.
- **Recommended next action:** Leave alone for now; not on the critical usable-spine path.

### Stage 7 — Teaching Layer  ⛔  Risk ⬛

- **Existing assets:** "Coming-Later" chrome only (Stage 4B); Stage 6E–6H build-queue placeholders.
- **Current readiness:** Not built.
- **Entry/Exit gates:** After the decision/action loop is stable.
- **Recommended next action:** Do not build now.

### Stage 8 — Premium Polish  🟡  Risk ⬛

- **Existing assets:** design foundation (Stage 4A token system, 4B Today Command Center, 4C Intel redesign).
- **Current readiness:** Foundation shipped; polish correctly gated.
- **Recommended next action:** Do not build now. Polish waits for a stable spine (NORTH_STAR + DESIGN_VISION timing gate).

---

## Keep / deprecate / leave-alone

- **Keep & build on:** F1 books stack, F2 price engine, Intel v3 decision/governance/freshness stack, Deploy exact-dollar math, labeled advisory orchestrator.
- **Leave alone (do not iterate):** Intel v3 policy internals; ETF provider matrix / NPORT / Vanguard issuer-official lane (Stage 9O closed — `issuer_official_vanguard` rejected). Watchtower/alert internals.
- **Deprecate / do not extend:** further ETF holdings *canonical* certification attempts; SEC NPORT CIK patching for VTI/SCHD/VXUS without manual filing-entity research. These are enrichment, not a usability gate.

## yfinance critical-path usage — is migration urgent?

**No.** yfinance appears in three places:
1. **Price hot path (`price_engine.py`)** — one of *several concurrent* stock sources (with Finnhub, Alpaca, Polygon). First valid result wins; a yfinance outage is absorbed by the others plus stale-cache fallback. **Not a sole dependency.**
2. **Evidence lanes (fundamentals/technicals/sentiment)** — yfinance is an *unofficial* provider feeding read-only evidence; it never owns a visible decision and degrades to suppression honestly.
3. **DRIP dividend dates + `benchmark.py` (SPY)** — yfinance-only, but best-effort and failure-isolated (`{}` / no fallback but non-blocking).

**Verdict:** yfinance is *not* on a single-point-of-failure critical path for daily refresh. **Do not recommend a Finnhub migration.** The only yfinance-only seams (DRIP dates, SPY benchmark signal) are non-blocking and low-value to migrate.

## Plaid / books reconciliation — sufficient, or repair-first?

**Sufficient to operate, but unproven to the user — needs a read-only audit surface, not a repair.** Plaid is authoritative on sync (24h cache), CSV imports dedup + reconcile, sold positions are removed, manual/crypto positions preserved. There is **no detected corruption**, but there is **no visible drift report** between Plaid holdings and the transaction-derived ledger, and cost basis is single-bucket AVCO (no lots). Before building Benchmark (Stage 3) and Tax safety (Stage 2) *on top of* the books, prove the books with a **read-only reconciliation/integrity diagnostic** (PR-1). This is an audit, not a repair.

## benchmark.py — signal input or user-facing benchmark?

**Signal input only.** `fetch_benchmark_price_action()` returns SPY `pct_30d/pct_5d/volatility_30d` for relative-strength inside the feature engine and fails silently to `{}`. It **cannot** support a user-facing VTI-only DCA benchmark, which needs deposit history + a hypothetical VTI-only cost basis + a comparison surface. The VTI benchmark is a **net-new read-only build** (PR-2), not an extension of `benchmark.py`.

## Deploy — production-ready for exact-dollar biweekly plans?

**Exact-dollar math: code-wired / implemented (documented Stage 2 exit per repo docs; not revalidated in this PR). Action safety: no.** Sizing, rounding, cash-residual distribution, and the cash guardrail are implemented and are documented as previously validated for $900/$1,500 flows. But every item ends in `ACTIONABLE_PENDING_TAX` because tax/wash-sale are `"not_evaluated_yet"`. Deploy can *plan* exact dollars today; it cannot honestly say an item is *safe to act on* until tax safety exists.

## Tax / wash-sale / lot capabilities

**Absent (honest placeholders).** No tax-lot accounting; no wash-sale window logic; `lt_eligible`/`lt_date` are unenforced position fields; deploy guardrail statuses are literal `"not_evaluated_yet"`. BUILD_QUEUE explicitly flags real tax-lot/wash-sale logic as **design-dependent** (requires a cost-basis source, lot accounting model, and wash-sale window scope decision before any build).

## Where Claude advisory should attach (without pretending to calculate)

- **Attach at:** the existing `analyst_verdict` narrative and `portfolio_synthesis` themes/risks — plain-English "why / why-now / what would make this wrong."
- **For Benchmark (Stage 3):** Claude may *narrate* the VTI-vs-actual gap ("you are tracking close to a VTI-only plan") but the **numbers must be computed deterministically** from deposit history.
- **For Deploy/Tax (Stage 2):** Claude may *explain* a tax consequence in plain English; it must **never compute** holding-period eligibility, wash-sale windows, or dollar sizing.
- **Hard line (NORTH_STAR):** deterministic backend owns every visible number and every Buy/Hold/Trim/Sell. Claude is labeled advisory narrative only.

---

## Proposed readiness flags (no code change)

These are *proposed* read-model flags to compute later; they are documented here, not implemented in this PR. `safe_for_decision` semantics are **unchanged** and remain owned by Intel v3 (held False in the shadow adapter). ETF holdings remain **enrichment/exposure evidence only** — never a global usability gate.

| Flag | Proposed meaning | True when |
|---|---|---|
| `facts_ready` | Books of record are trustworthy | Positions reconcile to transactions; cost basis present; no unresolved Plaid-vs-ledger drift; prices fresh |
| `advisory_ready` | Labeled advisory available | A fresh, non-template `analyst_verdict` (and/or synthesis) exists for the holding |
| `deployment_safe` | Safe to act on a deploy item | Exact-dollar sizing computed **and** cash guardrail cleared **and** tax/wash-sale evaluated (today: always False — tax not evaluated) |
| `decision_ready` | Intel decision certified | Intel v3 certified snapshot present and within freshness SLA |
| `canonical_ready` | Canonical ETF holdings present | SEC NPORT canonical (SPY/QQQ only today); otherwise False (enrichment-only) |

Note: `deployment_safe` is intentionally **False everywhere today** because tax/wash-sale is `"not_evaluated_yet"` — this flag honestly encodes the Stage 2 gap.

---

## Recommended next-PR sequence (3–5)

Chosen from code evidence: prove the books, deliver the one missing user-facing truth (VTI benchmark), then unblock tax safety by design. No Finnhub migration, no UI polish, no more ETF proof.

### PR-1 — Stage 10B: Books-of-record integrity & reconciliation diagnostic (read-only)
- **Scope (one sentence):** Add a read-only diagnostic that reconciles each position against its transaction-derived ledger and Plaid sync, reporting cost-basis integrity and any drift — no writes, no repair.
- **Entry gate:** This audit (Stage 10A).
- **Exit gate:** Per-position provenance + cost-basis + Plaid-vs-ledger drift are inspectable; `facts_ready` is computable. No mutation of books.
- **Why next:** Everything downstream (Benchmark, Deploy tax safety) sits on the books; cheapest, lowest-risk way to prove F1 before building on it. Confirms whether Plaid/books need a later repair PR or are sound (audit answers this empirically).

### PR-2 — Stage 10C: VTI-only DCA benchmark of truth (read-only)
- **Scope (one sentence):** Compute and surface a deterministic VTI-only DCA comparison from the user's actual deposit history vs a hypothetical VTI-only deployment of the same cash cadence.
- **Entry gate:** PR-1 establishes `facts_ready` (trustworthy deposit/transaction history).
- **Exit gate:** Plain-English VTI-vs-actual comparison that never fabricates returns and degrades honestly when deposit history is missing; numbers deterministic, optional Claude narrative labeled advisory.
- **Why next:** It is the only *missing user-facing truth* in the revised spine, it is net-new and read-only (low risk), and all inputs already exist (deposits in F1, prices in F2). Highest user value per unit risk.

### PR-3 — Stage 10D: Deploy tax-lot / wash-sale source design + contract (design PR, no behavior change)
- **Scope (one sentence):** Decide and document the tax-lot/cost-basis source, lot accounting model, and wash-sale window scope, and define the contract that will later flip `ACTIONABLE_PENDING_TAX` → `ACTIONABLE`.
- **Entry gate:** PR-1 (lot-level feasibility known from real books).
- **Exit gate:** A written, reviewed tax-lot source + contract; no code behavior change; `deployment_safe` semantics specified.
- **Why next:** Tax safety is the single biggest "before action" gap, but BUILD_QUEUE flags it design-dependent — the correct next move is a design/contract PR, not a speculative implementation. Unblocks Stage 2 exit and Stage 5 discipline.

### PR-4 (optional) — Stage 10E: Readiness-flag read model (backend, no UI, no policy change)
- **Scope (one sentence):** Implement the five proposed readiness flags (`facts_ready`, `advisory_ready`, `deployment_safe`, `decision_ready`, `canonical_ready`) as a pure backend read model over existing data.
- **Entry gate:** PR-1 + PR-2 (facts + advisory signals exist to read).
- **Exit gate:** Flags computed honestly from existing state; `safe_for_decision` unchanged; ETF holdings remain enrichment-only; no visible decision change.
- **Why next:** Makes spine readiness observable in one place and `deployment_safe=False` honest about the tax gap, without touching decision authority.

**Sequence rationale:** PR-1 proves the foundation, PR-2 ships the one missing truth users can feel, PR-3 unblocks the hardest safety gap by design, PR-4 makes readiness honest and observable. Stop at PR-4; do not pre-plan implementation of tax logic until PR-3's design is reviewed.

---

## Confirmations

- **ETF holdings remain enrichment/exposure evidence only** — not a global gate for app usability. Confirmed; Stage 9O closed `issuer_official_vanguard` as rejected.
- **`safe_for_decision` semantics unchanged** — owned by Intel v3, held False in the shadow adapter; not migrated by this audit.
- **No implementation, UI, SQL, provider migration, artifact writes, synthesis changes, or Buy/Hold/Trim/Sell behavior changes in this PR.**
</content>
</invoke>
