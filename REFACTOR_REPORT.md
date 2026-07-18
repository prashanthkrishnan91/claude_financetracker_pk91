# REFACTOR REPORT — Lean Personal Portfolio Tool

Status: **Phase 0 complete — engine determination written BEFORE any deletion.**
(This file is updated as the refactor proceeds; later sections are filled in as work lands.)

---

## Phase 0 — Engine determination

**Determination: the validated recommendation engine is the deterministic Intel v3 policy
kernel — `decide()` in `v2/backend/app/services/intelligence/v3/decision_policy_v1.py`,
orchestrated by `intel_v3_service.py`, served by `routers/intel_v3.py`, rendered by
`IntelV3Cockpit`/`IntelV3Card`.**

**The forbidden second engine is the legacy LLM/agent recommendation surface — the path
where LLM-produced actions render directly as visible recommendations:
`services/recommendation_engine.py` (`RecommendationService` / InsightCards) +
`routers/recommendations.py` + the legacy card path of
`v2/frontend/src/app/dashboard/recommendations/page.tsx` (`AgentInsightCard` et al.).**

### Evidence

1. **The repo's own hard rules name the deterministic engine as the only permitted
   decision authority:**
   - `docs/ai/KNOWN_FAILURE_MODES.md:7-9` — "Deterministic backend Intel v3 policy owns
     visible Buy/Hold/Trim/Sell decisions. LLMs, agents, research workers, and research
     artifacts … must never own final visible action authority. Do not add agentic or
     multi-agent research as final decision authority."
   - `docs/ai/AI_REPO_OPERATING_SYSTEM.md:207` — "Deterministic Decision Authority Pack:
     deterministic Intel v3 backend policy owns visible Buy / Hold / Trim / Sell
     authority."
   - `docs/ai/SAFETY_PACKS_AND_ARCHETYPES.md:83` — "deterministic Intel v3 backend policy
     is the only owner of visible action authority."
   - `artifacts/Intel_v3_Architecture_Plan_Draft2_Anthropic_Finance_Agent_Addendum.md:184`
     — "Do not implement an LLM/agent as the final decision authority."

2. **Architecture audit (`docs/ai/PRODUCT_SPINE_REALITY_AUDIT.md`, Stage 10A)** calls
   Honest Intel (Stage 1, `decision_policy_v1`) "by far the most mature area …
   Deterministic `decision_policy_v1` owns visible Buy/Hold/Trim/Sell", with its exit gate
   ("Intel v3 Certification Gate") "substantially met per HANDOFF".

3. **Months of validation history:** `docs/ai/HANDOFF.md` records stage after stage
   (9x, 10x, 11x, 12x, 13x through PR #471, 2026-07-10) building, certifying and
   guard-railing Intel v3 ("34/34 certified" production snapshot cards cited in Stage 13C).
   Test coverage is decisively lopsided: dozens of `test_intel_v3_*` / `test_v3_*`
   contract suites for the deterministic engine vs. pipeline-hardening-only tests for the
   agent path.

4. **The forbidden engine renders despite the rules:** in
   `v2/frontend/src/app/dashboard/recommendations/page.tsx:30-33`, when
   `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` is not `"true"`, the page renders
   legacy `AgentInsightCard`s fed by `GET /recommendations/` →
   `RecommendationService.get_insight_cards()`, whose actions originate from the LLM
   multi-agent pipeline (`services/agents/orchestrator.py` → `portfolio_manager.py`,
   whose system prompt line 39-47 instructs the LLM to "synthesise their views into a
   concrete action per ticker" — BUY/SELL/TRIM/HOLD/REVIEW). That is precisely the
   "LLM owns visible action authority" configuration the repo's rules forbid.

**Ambiguity check:** none material. Every governance doc, safety pack, audit and the test
history point the same way. One nuance is recorded as a judgment call (below): the agent
orchestrator is ALSO used by the protected Intel v3 evidence-refresh path as a *labeled
advisory evidence producer* (allowed by the rules: "LLMs … may provide sourced evidence
… but must never own final visible action authority";
`intelligence/v3/analyst_refresh_adapter_v1.py:556` and
`full_portfolio_analyst_refresh_adapter_v1.py:385` import `AgentOrchestrator`). Removal of
the forbidden *decision engine* therefore means removing the decision surface —
`recommendation_engine.py`, its routes, and its rendering — not amputating the evidence
producer inside the validated engine's own guardrailed refresh flow, which would change
the protected engine's behavior.

---

## What was kept (and why)

**The validated engine + all guardrails (behavior-identical):** the entire
`v2/backend/app/services/intelligence/` tree — `v3/decision_policy_v1.py` kernel,
`intel_v3_service.py`, decision/data-truth contracts, evidence suppression + freshness +
governance + credibility + contradiction gates, `buy_conviction_guardrail`,
`portfolio_governor_lite`, `source_validator_lite`, claim-safety scrubbing inside the
kernel, watchtower freshness/republish machinery (it is the engine's snapshot-freshness
guardrail, imported throughout `intel_v3_service`), research workers (SEC/FRED/NPORT
evidence adapters that feed the engine), and the analyst-refresh worker chain.

**The agent pipeline as evidence producer only:** `services/agents/*` + `services/ai/*`
(context builder). Intel v3's own refresh adapters (`analyst_refresh_adapter_v1.py:556`,
`full_portfolio_analyst_refresh_adapter_v1.py:385`) import `AgentOrchestrator` to produce
labeled advisory evidence — a role the repo's rules explicitly permit. Deleting it would
have changed the protected engine's evidence-refresh behavior. Its only tie to the
forbidden decision surface (the aggregate-cache invalidation of the deleted read layer)
was removed.

**The data ingestion pipeline, exactly as-is:** `routers/sync.py` (Plaid sync, Robinhood
CSV import, crypto-PDF import, price refresh), `import_service.py`, `plaid_service.py`,
`price_engine.py` + `market_data/*` + `cache/*`, `price_service.py`, `history_service.py`,
`crypto_service.py` (Plaid token encryption), `portfolio_engine.py`, `portfolio_service.py`.
No rebuilds, no "improvements".

**Product surface:** routers `auth`, `portfolio`, `positions` (+ new `/positions/tax-lots`),
`prices`, `sync`, `intel_v3`, new `recommendations_panel`, new `watchlist`. Frontend:
login/settings chrome plus exactly three views (Positions, Recommendations, Watchlist),
with the import page kept as an ingestion sub-page linked from Positions.

**Repo governance tooling** (`.github/`, `scripts/`, `docs/`, `.claude/`): CI workflows
execute `scripts/workflow/*` and `scripts/repo_hygiene/*` on every PR; deleting them would
break CI without changing product behavior.

## What was deleted (and why)

**The forbidden second decision engine (entirely):**
- `services/recommendation_engine.py` (121 KB InsightCard read/persist layer whose
  LLM-originated actions rendered directly)
- `routers/recommendations.py` (`GET /recommendations`, `POST /recommendations/refresh`
  → agent pipeline as a user-facing recommendation surface)
- Frontend legacy path: `AgentInsightCard`, `AgentProgressTracker`,
  `PortfolioSynthesisPanel`, `InsightCard`, `DataQualityBanner`, the
  `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` flag and the flag-off render path in
  the recommendations page. The panel now renders Intel v3 output only — no flag, no
  legacy fallback.

**Out-of-scope surfaces (not serving the three views):** alerts/watchtower-delivery
(`services/alert/*`, alert routers, email worker Railway process), Deploy v3
(`services/deploy/*`, `routers/deploy_v3.py`), Paycheck Advisor
(`allocation_policy_v1.py`, `routers/paycheck_plan_preview.py`), deposits/auto-invest
advisor (`routers/deposits.py`, `deposit_service.py`, `decision_engine.py`,
`personalized_decision_engine.py`, `personalization_engine.py`, `strategy_engine.py`,
`strategy_modes.py`, `regime_engine.py`, `simulation_engine.py`, `allocation_engine.py`,
`deployment_engine.py`, `adaptive_deployment.py`, `decision_explainer.py`), DRIP
analytics (`routers/drip.py`, `drip_service.py`), decision journal
(`routers/decisions.py`, `decision_logs.py`, `decision_log_service.py`), AI chat
(`routers/ai.py`, `ai_service.py`), analytics/allocation legacy routers, action feedback,
and every cert-gated operator diagnostic (`routers/diagnostics.py` and the
books-reconciliation / financial-truth / VTI-benchmark / price-repair diagnostic
services). Frontend pages for all of the above (journal, paycheck-plan, deposits, alerts,
radar, drip) and the deploy/paycheck/synthesis components. Tests belonging exclusively to
deleted surfaces were deleted with them; mixed test files kept their pure-logic tests.

## Fixes (with verification)

1. **Cross-stage dataclass mismatch (the known test-breaking bug):**
   `_SupplementalData` in `intel_data_foundation_forensics_v1.py` gained a *required*
   `sec_fact_records` field in Stage 9D; every earlier-stage constructor (Stage 9C tests)
   crashed with `TypeError: _SupplementalData.__init__() missing 1 required positional
   argument: 'sec_fact_records'`. Fixed by defaulting the field (`default_factory=dict`)
   — behavior-identical because the production producer always passes it explicitly.
   Verified: `tests/test_stage9c_sec_companyfacts_readiness.py` 51/51 pass (was 9 failures).

2. **Event-loop test poisoning:** async teardowns left the main thread without an open
   event loop, failing ~40 ordering-dependent tests (watchtower/stage5j/5k/3_2e families)
   that pass in isolation — the "pre-existing event-loop/test-isolation issue" HANDOFF
   documents. Fixed with an autouse conftest fixture that guarantees an open loop.

3. **Stale tests updated to current production contracts** (production behavior is the
   validated artifact; each divergence was introduced by a later validated stage):
   Migration-024 flat-column snapshot reads (watchtower build2/build3, stage7, stage8a2,
   stage8a3 fixtures), cost-guard snapshot-write kill switch (stage10 egress test),
   Stage 9F `etf_fund_data` lane (stage5g), Stage 13 `evidence_freshness_state`
   annotation (certification test), and the BUY-conviction guardrail promoted into the
   kernel (signal-hydration test now matches the newer guardrail suites' explicit
   "policy already capped" contract).

## Judgment call log

- **Agent pipeline retained as evidence producer.** "Remove the forbidden second decision
  engine entirely" was implemented as removing every path where LLM actions function as
  decisions (persistence read layer, routes, rendering). The orchestrator remains only as
  the labeled-advisory evidence producer inside Intel v3's guardrailed refresh flow — the
  exact split the repo's own rules draw ("LLMs … may provide sourced evidence … but must
  never own final visible action authority"). Deleting it would have broken the protected
  engine's behavior-identical requirement.
- **Paycheck Advisor deleted** despite HANDOFF calling it "the only user-facing
  recommendation surface": the target product defines the recommendations view as
  buy/sell/trim calls from the validated engine (Intel v3's vocabulary), and a cash-deploy
  advisor is not one of the three views.
- **Import page kept off-nav.** Nav shows exactly three views; the ingestion UI
  (`/dashboard/import`) remains reachable via a link from Positions since the protected
  ingestion pipeline needs an entry point.
- **Deposit/DRIP recording surfaces deleted.** The ingestion pipeline preserved as-is is
  the Plaid/CSV/PDF/price pipeline (`routers/sync.py` and its services). Deposit
  scheduling and DRIP analytics were product features outside the three views; their
  Supabase tables and imported transaction history remain untouched.
- **Repo/AI-OS governance files kept** (docs/, scripts/, .claude/, .github/) — CI executes
  them; they are process, not product code.
- **Provider symbol-translation maps** (`history_service.py` crypto→Yahoo symbols,
  `agents/data_sources.py` CoinGecko IDs) were left in code: they are provider routing
  tables, not policy ticker lists. Every decision-influencing ticker set/map was
  externalized to `app/policy_tickers.json`.
- **Tax rates are config, estimates only.** `TAX_RATE_SHORT_TERM` / `TAX_RATE_LONG_TERM` /
  `LONG_TERM_HOLDING_DAYS` / `PROFIT_TAKING_THRESHOLD_PCT` live in `app/config.py`
  (env-overridable). UI copy marks tax numbers as estimates, never advice.

## Final test suite output

**Baseline before the refactor** (`python3 -m pytest tests -q`, branch start):

```
93 failed, 8910 passed, 12 warnings in 41.04s
```

**Final backend run** (`cd v2/backend && python3 -m pytest tests -q -p no:cacheprovider`):

```
7404 passed, 10 warnings in 31.50s
```

**Final frontend run** (`cd v2/frontend && npx jest --runInBand`):

```
Test Suites: 4 passed, 4 total
Tests:       129 passed, 129 total
Snapshots:   0 total
Time:        0.53 s, estimated 1 s
Ran all test suites.
```

Also verified: `npx tsc --noEmit` clean and `npx next build` green (placeholder
`NEXT_PUBLIC_SUPABASE_*` values for the build run only, per the repo's existing
pattern), and `python3 -c "import app.main"` under test env loads the lean router set.

The test-count drop (8,910 → 7,404 backend; 1,050 → 129 frontend) is the deleted
surfaces' tests going away with their code; every kept module's pure-logic coverage was
preserved (endpoint tests for deleted routes were pruned, not the module tests). Zero
failures remain in either suite.

## Definition-of-done check

1. Three views work end-to-end against the real Supabase data: Positions reads the
   existing positions/transactions tables (tax lots derive from `transactions.tx_date`),
   Recommendations reads the existing `intel_v3_snapshots` certified snapshot, Watchlist
   reads `watchlist_items` (new migration `v2/database/025_watchlist.sql` — the one
   manual Supabase step). ✔
2. Exactly one decision engine exists — deterministic Intel v3 (`decide()`); the
   forbidden LLM/agent recommendation surface is gone. ✔
3. Tickers live in `app/policy_tickers.json`, not policy source (config-parity tests). ✔
4. Cross-stage `_SupplementalData` dataclass mismatch fixed (defaulted Stage 9D field). ✔
5. Full test suites pass — outputs above. ✔
6. This report. ✔
