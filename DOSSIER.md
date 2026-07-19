# INTERVIEW DOSSIER — Portfolio Intelligence Platform

**Prepared for:** Director of Analytics interview preparation
**Truth policy:** Every claim in this document is verifiable from this repository (file paths and commit hashes cited inline). Anything that would strengthen the story but cannot be verified here is listed in §6 "ASK PRASHANTH" instead of being asserted.
**Repo state audited:** commit `b113d62` (merge of PR #476, 2026-07-19), 190 commits, 48 merged PRs.

---

## Verified fact sheet (memorize these — every number was re-measured, not copied)

| Fact | Value | Source |
|---|---|---|
| Backend application code | 209 Python files, 86,381 lines | `find v2/backend/app -name '*.py'` |
| Backend test suite | 201 test files, 138,358 lines, ~7,900 `def test_` functions | `v2/backend/tests/` |
| Test-to-app code ratio | ~1.6 : 1 (tests exceed app code) | measured above |
| Backend suite at PR #473 close | 8,290 passed / 0 failed (parametrized collection) | `REFACTOR_REPORT.md` (final phase records) |
| Frontend | Next.js 14 App Router; 17 pages, 13 components, 21 test files, 585 test cases | `v2/frontend/src/`, `jest` |
| HTTP endpoints | 69 across 10 routers (36 of them operator diagnostics) | `v2/backend/app/routers/` |
| Database migrations | 001–025 across `v2/database/` + `v2/backend/migrations/` | both dirs |
| Git history | 190 commits, 48 merged PRs (numbered #424–#476), all from `claude/*` branches | `git log` |
| Commit authorship | Claude 141 commits / prashanthkrishnan91 49 (merges + direct) | `git shortlog -sn` |
| AI governance assets | 21 skills, 17 read-only reviewer agents, 3 hooks, 2 CI workflow scripts, 15 slash commands | `.claude/`, `scripts/workflow/` |
| Miss ledger | 15 recorded process failures (12 dated + 3 seed) | `docs/ai/MISS_LEDGER.md` |
| Usage ledger | 172 committed per-PR usage rows (PRs ~#77→#474), 26 columns | `docs/ai/USAGE_LEDGER.md` |
| Largest real change | Consolidation refactor: ~43K lines **deleted** across two commits | `git show --stat e52b40a 9b7715f` |
| Named safety packs / build archetypes | 12 packs (8 shared + 4 finance-specific) / 10 archetypes | `docs/ai/SAFETY_PACKS_AND_ARCHETYPES.md` |

Caveats to volunteer before an interviewer finds them: PR numbers run #424–#476 because this repo's history begins with a squashed import (root commit `f3702017`, 2026-05-25) — earlier PRs lived elsewhere (see §6). This is a personal, single-user product; the interview value is the *system of work*, not user scale.

---

# 1. SYSTEM NARRATIVE

**What it is.** A personal portfolio intelligence platform: deterministic, auditable Buy/Hold/Trim/Sell decisions for each holding, plus a deterministic "I have $X of new cash — what do I buy, how much, and why?" plan, delivered in plain English to an amateur investor. Next.js 14 on Vercel → FastAPI on Railway → Supabase Postgres, with Plaid (brokerage sync), yfinance/Alpaca/Finnhub/Polygon/CoinGecko (prices), and SEC EDGAR/FRED (research evidence) (`README.md`, `v2/docs/architecture.md`).

**The real problem it solves is a trust problem, not a features problem.** An AI-heavy investing tool has two catastrophic failure modes: the model confidently tells you to do something wrong with your money, and the system quietly shows you numbers it shouldn't trust. The entire architecture is organized around preventing both:

1. **Decision authority is deterministic and singular.** Visible Buy/Hold/Trim/Sell is computed by one pure function — `decide()` in `v2/backend/app/services/intelligence/v3/decision_policy_v1.py` — axis-based (evidence, attractiveness, price, fit, risk), no weighted composite score, no IO, no LLM. LLMs, agents, and research workers produce *sourced evidence artifacts* only. This is enforced at three independent layers: the pure-function kernel itself; import-boundary tests that parse source code and fail if any evidence/worker module imports `decide()` (e.g. `v2/backend/tests/test_stage5h1_intel_v3_evidence_lane_orchestration.py:687-704`); and the database schema, where the research artifact store hard-locks `safe_for_decision=false` with a CHECK constraint and a recursive JSONB trigger that rejects any payload containing keys like `final_action` or `buy/sell/trim/hold` (`v2/database/017_research_artifact_store_v1.sql:26-76,161`).

2. **Data truth fails closed.** Missing, stale, weak, or conflicting data suppresses the affected decision axis rather than being papered over (`v2/backend/app/services/data_truth_v1.py`). Tax lots that don't reconcile against certified positions are *blocked from display*, not estimated (`v2/backend/app/services/tax_lot_engine.py:8-13`). On the frontend, an unreachable truth endpoint renders every dimension as `unknown` — and `unknown` is explicitly "a neutral state, not a healthy one," never conflated with `ok` (`v2/frontend/src/lib/advisor-truth.ts`). Raw internal metric keys are banned from user-facing strings by an explicit blocklist (`RAW_KEYS_BANNED`, `v2/frontend/src/lib/intel-v3-explanation.ts`).

**The second system in the repo is the one that built the first.** The code was written almost entirely by AI agents (141 of 190 commits authored "Claude", every one of 48 merged PRs from a `claude/*` branch), directed through a browser/mobile workflow with no CLI assumed (`CLAUDE.md` line 3). That constraint forced the distinctive artifact here: the **AI Repo Operating System v4** (`docs/ai/AI_REPO_OPERATING_SYSTEM.md`) — a written, versioned, self-correcting management system for an AI workforce. Prompts are compressed "work orders" (<700–1,200 words) that reference named safety packs instead of restating rules; a 731-line CI gate (`scripts/workflow/ai_pr_readiness_check.py`) mechanically enforces PR standards; a miss ledger records every process failure; and a promotion ladder converts *repeated* misses — never one-offs — into new rules. The ledger shows this loop actually firing: the third occurrence of a missing usage-ledger row is explicitly counted ("#338 patch-1, #350, and now #352 — third occurrence — promotion warranted," `docs/ai/MISS_LEDGER.md:211-224`) and the corresponding hard-fail check now lives in CI.

**The architectural philosophy in one sentence:** put judgment in deterministic, testable, auditable structures; use AI for leverage everywhere else; and make every claim — by the product to its user, and by the AI workforce to its manager — carry its evidence or be suppressed. The repo applies the same standard to itself that the product applies to market data: the refactor report documents pre-existing test failures honestly, states "zero tests deleted to get green," and lists exactly what was *not* validated against production (`REFACTOR_REPORT.md`).

---

# 2. DECISION LOG

The 8–12 most significant architectural decisions evidenced in the repo. ⭐ = demonstrates judgment under constraints.

### D1. A deterministic kernel owns all visible investment decisions; LLMs are evidence-only ⭐
- **Context:** The system uses LLM agents for research, yet produces financial recommendations where a hallucinated "SELL" is unacceptable.
- **Options considered:** The rejected alternative is written down: "let LLM/research override on edge cases" — rejected in `docs/product/DECISION_LOG.md` (decision 1), which also records what would change the decision (regulatory shift + rigorous eval).
- **Decision:** All visible Buy/Hold/Trim/Sell derives from the pure function `decide()` (`decision_policy_v1.py:457`, header: "no composite score… Pure function — no IO, LLM, DB"). LLM text feeds only sanitized rationale fields; `_clean_evidence_text()` discards any LLM output containing raw metric keys or price targets (`decision_policy_v1.py:50-90`).
- **Consequence:** Three-layer defense-in-depth (function purity, import-boundary tests, DB CHECK + JSONB forbidden-key trigger in migration 017). Cost honestly acknowledged: a large orchestration surface (~80 files in `v3/`) exists purely to keep evidence and decision separate.

### D2. Fail-closed data truth: suppress, never fabricate ⭐
- **Context:** A finance UI showing a wrong number is worse than one showing "blocked."
- **Decision:** Signals classify as PRESENT/WEAK/MISSING/STALE/CONFLICTING/UNAVAILABLE; only PRESENT/WEAK are `safe_for_decision` (`data_truth_v1.py:10-16`). Tax lots display only when FIFO lots reconcile against certified positions within documented tolerances; unknown transaction types *block* the ticker rather than being skipped (`tax_lot_engine.py:1-21,35-62`). Providers never let exceptions escape and never fabricate (`sec_edgar_provider.py:14-19`). Frontend: `unknown ≠ ok` invariant, `sanitizeAdvisorTruth` belt-and-braces (`advisor-truth.ts`).
- **Consequence:** The product shows honest degraded states — captured in committed proof: a deliberately unreconciled AAPL tax-lot scenario returning `authoritative:false`, and a degraded cash plan showing disagreeing totals 21,129.06 vs 20,633.85 with a repair action (`docs/ai/proof/consolidation/RESPONSES.md`).

### D3. Browser-only AI workforce, governed by a repo-native operating system ⭐
- **Context:** `CLAUDE.md:3` — "Use this repo through a browser/mobile Claude + Codex workflow unless the user explicitly says CLI is available." No local dev loop to lean on; every instruction must survive being handed to a fresh agent context.
- **Decision:** Move all repeatable process *into the repo*: OS v4 doc, 21 skills, 17 reviewer agents, safety packs, PR template, CI gates. Prompts carry only the "task delta" with a hard <700–1,200-word compression budget; a prompt that is "mostly repeated workflow/process language **fails the gate**" (`docs/ai/PROMPT_ENGINEERING_STANDARD.md`). Roles are explicit: architect/prompt-engineer, builder models, and a surgical-fix lane (`AGENTS.md:30-35`).
- **Consequence:** 48 PRs merged from `claude/*` branches with a written audit trail (172 usage-ledger rows). The doctrine itself was born from a real failure — the old "prompt contains everything" standard "caused prompt bloat and tiny micro-PRs" (`PROMPT_ENGINEERING_STANDARD.md:7`).

### D4. Mechanical gates over documentation — because documentation demonstrably failed ⭐
- **Context:** `SETUP_AUDIT.md` Finding 1: the same PR-body mistake recurred 4 times *after* being promoted to `KNOWN_FAILURE_MODES.md`. Writing rules down did not change agent behavior.
- **Decision:** Enforce in code: `scripts/workflow/ai_pr_readiness_check.py` (731 lines, stdlib-only, ships `--self-test` with ~30 assertions) hard-fails PRs missing usage-ledger rows, claiming usage tracked without ledger changes, lacking failure-seam evidence on runtime fixes, claiming "visual transformation" without screenshots, or exceeding 3 follow-ups without an escalation note. Every hard-fail check traces to a specific miss-ledger entry (`docs/ai/AI_PR_READINESS_GATE.md`, `docs/ai/MISS_LEDGER.md`).
- **Consequence:** Claim-vs-reality checking became CI, not culture. The gate even audits its own scaffolding via a second script (`scripts/workflow/certify_v4_1.py`).

### D5. One coherent capability slice per PR; escalation ladder on failed patches
- **Context:** Early history shows both failure modes: micro-phase churn and runaway patch loops.
- **Decision:** Batch-vs-split gate (`AI_REPO_OPERATING_SYSTEM.md:64-90`): one slice owns its code + contract + tests + docs; split only for enumerated reasons. Severity routing Levels 0–3 with a patch-exhaustion rule: "After one failed patch, reclassify. After two related patches, stop patching — escalate" (`docs/ai/ISSUE_SEVERITY_ROUTING.md`, `docs/ai/FAILURE_RECOVERY.md:6-9`).
- **Consequence:** Honest split verdict: the rule existed and was still overrun in practice (~22-PR provider loop, §3.5) — which is *why* Check H (follow-up count ≥3 hard-fails without escalation note) was added to CI.

### D6. Reject-and-redo: PR #472 thrown away, consolidation re-run under a deletion gate ⭐
- **Context:** A prior consolidation attempt (PR #472) deleted protected product surfaces — the Paycheck Advisor allocation spine, operator diagnostics, truth/repair services.
- **Decision:** Reject #472 outright; mark it reference-only ("not continued, branched, or cherry-picked"); re-run as PR #473 under a written Phase-0 contract committed *before* any code change, with a deletion gate: nothing deleted without grep-verified proof that no protected path imports it (`REFACTOR_REPORT.md:6-12`, keep/delete table and import-graph proofs).
- **Consequence:** `recommendation_engine` and agent services were *kept* (proven imported by protected paths) while 12 legacy routers and orphaned engines were deleted with proofs recorded. The two biggest commits are ~43K lines of deletion (`e52b40a`, `9b7715f`) — the largest real change in the repo is a simplification.

### D7. Product surface collapsed to exactly three views
- **Context:** Multiple competing recommendation/allocation/deploy surfaces had accreted (deploy, deposits, journal, alerts, radar, recommendations…).
- **Decision:** Exactly three primary views — Positions / Advisor / Watchlist — with the Advisor as the *single* recommendation surface; 9 legacy routes become server-side redirects (`v2/frontend/src/lib/route-redirects.ts`, `nav-items.ts`); retired backend endpoints "must not be re-registered" (`v2/backend/app/main.py:90-95`).
- **Consequence:** One decision spine, one snapshot query, one run mutation (`dashboard/advisor/page.tsx`). Product docs enforce the boundary going forward (`docs/product/DO_NOT_BUILD_YET.md`).

### D8. Deterministic new-cash allocation with hard-coded caps and evidence gating
- **Context:** "What do I buy with $X?" invites LLM freelancing.
- **Decision:** `allocation_policy_v1.py` — "Read-only. No writes. No provider calls. No LLM calls." ETF floor 40%, single-stock cap 20%, speculative/crypto/alternatives caps 5% each; core-ETF preference order VTI>VOO>SPY>QQQ governs over raw gap size; individual stocks require fresh Intel v3 BUY evidence — "missing or ambiguous evidence blocks the ticker rather than defaulting to a fake pass" (`allocation_policy_v1.py:16-75`). Ticker classification externalized to `app/policy_tickers.json` with parity tests guarding the migration from hardcoded values (`test_policy_tickers.py`).
- **Consequence:** The output is bounded, explainable, and labeled "deterministic allocation guidance, not personalized investment advice" (`paycheck_plan_preview.py:29`).

### D9. Single-user trust model instead of RLS — a documented, deliberate downgrade
- **Context:** Multi-user row-level security adds complexity a 1–2 person family app doesn't need.
- **Decision:** `v2/database/001_initial_schema.sql:5` — "Single-user / family model — NO Row Level Security needed"; `middleware/auth.py:1-7` documents the simplification. Security posture instead: Supabase JWT (JWKS-validated) + AES-256-GCM for at-rest API keys (96-bit random nonce, key never in DB — `crypto_service.py`, `config.py:33-34`).
- **Consequence:** Later feature migrations (016–025) reintroduce RLS per-table where cheap (watchlist, artifact store) — scope-appropriate security, upgraded incrementally rather than paid for up front.

### D10. Cost and egress engineered as first-class constraints ⭐
- **Context:** A once-per-minute Watchtower loop was reading 50–200KB JSONB snapshots — ~14 GB/month of PostgREST egress on a personal budget.
- **Decision:** Migration `024_intel_v3_snapshots_flat_metadata.sql:1-17` denormalizes hot metadata into flat columns specifically to kill that egress; cost-guard kill switches and run-mode accounting (migration 012, `cost_guard_retention_cleanup.sql`); per-ticker queries sized to dodge Supabase's silent 1,000-row cap after a documented VTI truncation bug (`current_price_truth_repair_v1.py:5-9`). AI-side: a committed usage ledger with waste classification per PR and an explicit budget stance ("ChatGPT Plus + Claude Pro only… try Codex first," `docs/ai/PROMPT_LIBRARY.md`).
- **Consequence:** Cost discipline is visible in both the product runtime and the AI workforce that builds it.

### D11. Concurrent-first, circuit-broken market data engine
- **Context:** v1's sequential provider fallback "degraded to stale institution prices" (`price_engine.py:5-14`).
- **Decision:** Fire all price sources concurrently, first valid wins; 8s per-source timeout; 5-min LRU; per-provider circuit breakers after 3 failures (`price_engine.py`, `agents/data_sources.py:312-324`).
- **Consequence:** Freshness over API frugality on the read path — the opposite trade from D10, and correctly so: price staleness is a *decision-safety* input (a stale-priced ticker is excluded from new-cash candidacy — see §3.2).

### D12. Smallest-sufficient test tier, stated and justified on every PR
- **Context:** The full backend suite is too large to run reflexively (documented at 3,926 tests when the rule was written, far larger now — `docs/ai/TEST_ROUTING.md:13`).
- **Decision:** Tier 0 (changed-file adjacent, default) → Tier 3 (full suite, allowed only for 6 enumerated reasons; "'Big change, just to be safe' is **not** a reason"). Every PR states tier used + why sufficient (PR template, `TEST_ROUTING.md`).
- **Consequence:** Test compute is allocated by risk, and the *reasoning* is auditable per PR. Test style is notable for analytics leadership: architecture-as-test (source-parsing import-boundary assertions), contract/certification tests, and config-parity tests.

---

# 3. FAILURE & RECOVERY STORIES

These are the strongest interview assets: real, documented, and recovered-from.

### 3.1 The false-completion bug that took four audits to actually kill
A production regression after PR #473: "Run Intel" reported completion off a *stale historical snapshot* while 12 of 32 refresh jobs were still undone, so the UI never offered "Continue Intel run." Four commits, each triggered by a fresh-context semantic audit finding the previous fix incomplete (all in PR #474):
- `7f99c96` — completion now requires `worker_certified` + `certified_current` + no remaining drain work (the original check tested only the first).
- `9b00e88` — audit found an *older* certified snapshot could still masquerade as this run's outcome → completion now requires proof-of-publication: a new snapshot id that differs from the pre-run id.
- `26a158d` — audit found the zero-queued path still reported completion for failure statuses (`no_active_holdings`, `enqueue_failed`) → explicit success-status allowlist.
- `df62a9f` — Stage 8E/8F recertification failures fell through to a success message ("Analyst evidence is current") next to a failed retry button → explicit failure-message mapping.

**Telling it honestly:** each fix was real but incomplete; the process (independent re-audit until dry) is what converged, not any single fix. The usage ledger's rows for #474 record the lesson per round (`docs/ai/USAGE_LEDGER.md`).

### 3.2 "We fixed the wording, not the bug" — the stale-price release blocker
`docs/ai/HANDOFF.md:3-10` states it plainly: PR #473/#474 "fixed only response wording." The real defects were fixed in PR #476 (`9756b81`, then same-day `0495b3b`):
- A drain deadline was ignored by an adapter defaulting to 180s, "turning a nominal 90s bound into a ~148s hang."
- A stale-priced ticker could still be selected to receive new cash. The fix went to the *canonical* boundary — `allocation_policy_v1._compute_gaps` (new exclusion reason `stale_price_not_eligible_for_new_cash`) — plus a defense-in-depth invariant that blocks the entire plan if a stale-priced ticker ever slips through. Explicitly "a canonical fix, not a presentation filter" (`HANDOFF.md:87-106`).
- Drain scope could claim jobs for sold tickers; job-state classification read only `total_due`, so retry-backoff backlogs looked like "nothing to do" (`0495b3b`).

This is the repo's own `FAILURE_RECOVERY.md:18-22` rule ("debug visible decision plumbing… do NOT tune labels as a proxy") being violated and then honored, with the paper trail intact.

### 3.3 The reality audits: finding scaffold where capability was assumed
Two commissioned read-only audits of the system's *own claims*:
- `docs/ai/PRODUCT_SPINE_REALITY_AUDIT.md` (2026-06-11): tax/wash-sale safety **not implemented** — literal `"not_evaluated_yet"` placeholders; every actionable item stuck at `ACTIONABLE_PENDING_TAX`; the proposed `deployment_safe` flag is *intentionally false everywhere* to encode the gap honestly. Also: benchmark comparison absent as a user capability, behavioral cooldowns missing from the decision path, teaching layer "Coming-Later chrome" only. And it argues against two tempting wrong moves (don't migrate off yfinance; stop iterating ETF certification).
- `docs/ai/STAGE_12A_ALLOCATION_REBALANCING_REALITY_AUDIT.md` (2026-06-23): **no target-weight generator existed**; a hardcoded deposit formula (NVDA 28%/VOO 22%/VYM 17%/QQQ 17%/rotating 16%, $900 fixed) sat disconnected from Intel entirely; a legacy route used live prices, bypassing certified truth. This audit's prescriptions became the shipped `allocation_policy_v1` — the audit is the documented origin of the Paycheck Advisor.

### 3.4 An agent deleted the product spine — rejected PR #472
The first consolidation attempt deleted the Paycheck Advisor, operator diagnostics, and truth/repair services. It was rejected wholesale and quarantined ("reference-only… not continued, branched, or cherry-picked"), and the redo added the deletion gate: grep-verified import proofs before any deletion (`REFACTOR_REPORT.md:6-12`). The lesson generalizes: an AI workforce needs *structural* guardrails around destructive operations, not trust.

### 3.5 The 22-PR provider patch loop — and auditing the process itself
`SETUP_AUDIT.md` (PR #475) turned the audit lens on the AI workflow: the ETF-provider saga ran ~22 PRs (#415–#451) until a PR literally titled "stop provider patch loop" (#447); ~1 in 5 commits (36/191) was post-push PR-compliance repair with no product change; 19 of 21 skills lacked the frontmatter needed to auto-fire; the reviewer-agent effectiveness ledger was still empty after ~160 PRs — "what actually catches things: CI gates, tests, fresh-context audit sessions." The dead ends themselves are documented so they can't be re-investigated: Alpha Vantage rejected as canonical (no as-of date; VXUS returned 37 holdings of thousands), FMP paywalled (HTTP 402) with explicit "do not build a canonical FMP adapter" (`docs/ai/intel/STAGE_9F3_ALPHA_VANTAGE_PROOF.md`, `STAGE_9F4_FMP_ETF_HOLDINGS_PROOF.md`).

### 3.6 Semantic-review catches during the consolidation
Independent fresh-context reviewers across ten dimensions found, among others (`REFACTOR_REPORT.md:508-519,615-690`, commits `a4733df`, `8959ace`, `8acf411`, `abe90d9`): a zero-price Buy could mint a zero-basis tax lot (now fail-closed); a watchlist create race (now 409 backstop); a drawer still calling *deleted* endpoints; two competing "Run Intel" controllers; placeholder panels that violated the no-placeholder rule; snapshot-derived fields mislabeled as portfolio truth. Plus the test-count reconciliation, stated to the test: 9,003 collected − 807 deleted strictly with retired surfaces + 94 added = 8,290 passing, "zero tests deleted to 'get green.'"

### 3.7 Small misses that built the immune system
- An error-swallowing helper silently converted DB errors into a false "certified_current" — caught by the first test run, fixed by inlining the call so exceptions reach the honest boundary (`MISS_LEDGER.md:24-37`).
- A policy filtered on `"OK"` while the serializer emitted `"PARTIAL"` — silently suppressing every OK-band card; caught in review before production wiring (`MISS_LEDGER.md:177-190`).
- The Streamlit v1 prototype was retired outright, with hygiene tooling that scans for its residue — and an explicit guard against over-eager deletion of the legitimate `/api/v1/` namespace (`docs/ai/REPO_HYGIENE.md:15-46`).

---

# 4. STAR CASE STUDIES

Calibrated for Director of Analytics: system thinking, quality guardrails, directing an AI workforce. Every Result is repo-verifiable; business outcomes you can't verify from the repo are flagged to §6.

### STAR 1 — Building a management system for an AI workforce
- **S:** Building a production-grade financial platform with AI agents doing nearly all implementation (141 of 190 commits; every merged PR from a `claude/*` branch), through a browser-only workflow with no CLI (`CLAUDE.md:3`), across multiple model versions and fresh contexts that retain nothing between sessions.
- **T:** Get consistent, safe, cost-controlled output from workers that have no memory — without prompts ballooning into unmaintainable rule dumps.
- **A:** Encoded the process into the repository itself as a versioned operating system (`docs/ai/AI_REPO_OPERATING_SYSTEM.md`): compressed work-order prompts (<700–1,200 words) referencing 12 named safety packs and 10 build archetypes instead of restating rules; 21 skills and 17 read-only reviewer agents routed by domain (`AGENT_ROUTER.md` — "fewer high-signal reviewers," explicit anti-pattern list); a 26-column usage ledger per PR; and a self-learning loop (miss ledger → promotion ladder → mechanical CI gate) where only *repeated* misses become rules (`OS_LEARNING_PROTOCOL.md:22-27`).
- **R:** 48 merged PRs with a complete audit trail (172 ledger rows); a governance loop that provably fired (third-occurrence promotion recorded verbatim in `MISS_LEDGER.md:211-224`; each CI hard-fail check traces to a named miss); and consolidation discipline — ~75 stale workflow assets deleted during the v4 collapse, with re-introduction banned. Honest limit, volunteered: the agent-effectiveness ledger stayed empty — the pruning insight came from a later audit (STAR 5).

### STAR 2 — Directing a deletion-first refactor with evidence gates (PR #473)
- **S:** The product had accreted competing recommendation/deploy/deposit/journal surfaces; a prior AI attempt (PR #472) "consolidated" by deleting the protected product spine and was rejected.
- **T:** Collapse the product to three views and one decision spine without losing protected capability, and *prove* nothing broke.
- **A:** Committed a Phase-0 contract (baseline SHA, keep/delete table, honest baseline of 93 pre-existing test failures) before any code change; enforced a deletion gate requiring grep-verified import proofs per deleted module; ran ten independent fresh-context semantic reviews; demanded runtime proof under a real constraint (no production credentials) by running the real app locally with fixtures only at outermost boundaries — 19 screenshots + 13 sanitized API captures, with an explicit "real vs fixture" and "not validated" ledger (`REFACTOR_REPORT.md`, `docs/ai/proof/consolidation/RESPONSES.md`).
- **R:** ~43K lines deleted across the two largest commits (`e52b40a`, `9b7715f`); backend suite from 93 failing/8,910 passing to 8,290 passing/0 failing with per-file deletion accounting and "zero tests deleted to get green" (`REFACTOR_REPORT.md`); semantic reviews caught real defects pre-merge (zero-basis tax lot, watchlist race, calls to deleted endpoints).

### STAR 3 — Designing decision authority so AI can help but can't decide
- **S:** An investing product wants LLM research leverage, but a hallucinated recommendation touching real money is an unacceptable failure class.
- **T:** Architect the system so the failure is *structurally impossible*, not just discouraged.
- **A:** Put all visible decisions in one pure deterministic function with axis-based logic and conviction caps (`decision_policy_v1.py`); sanitized LLM text of raw metrics and price targets before it can reach rationale fields; enforced the boundary at three layers — code (single call site, `intel_v3_service.py:233` "Final decision authority stays with decide()"), tests (source-parsing assertions that evidence modules never import `decide()`), and schema (`safe_for_decision` CHECK-locked false + recursive JSONB trigger rejecting action keys, migration 017). Mirrored it in the UI: banned raw-key blocklist, plain-English translation layer, and a trust drawer where `unknown` never reads as healthy.
- **R:** A verifiable invariant chain from database to pixel, restated as product law in `NORTH_STAR.md`, `RELEASE_GATES.md`, and `GOLDEN_SCENARIOS.md`. Interview framing: this is analytics governance — the same discipline as keeping a self-serve metrics layer from silently overriding certified definitions.

### STAR 4 — Auditing my own system's claims and finding the gaps
- **S:** After months of staged building, claimed capabilities ("before-action tax safety," "allocation intelligence") needed verification before building on top of them.
- **T:** Establish ground truth about what actually existed versus what the stage names implied.
- **A:** Commissioned two read-only reality audits with explicit evidence standards (static inspection; runtime claims only when cited). They found: tax/wash-sale checks were literal `"not_evaluated_yet"` placeholders gating every action; no target-weight generator existed; a hardcoded $900 deposit formula sat disconnected from the intelligence layer; a legacy route bypassed certified price truth (`PRODUCT_SPINE_REALITY_AUDIT.md`, `STAGE_12A_ALLOCATION_REBALANCING_REALITY_AUDIT.md`).
- **R:** The gap analysis became the build plan — the 12A audit's prescriptions are the shipped `allocation_policy_v1.py` (deterministic caps, evidence gating). The unimplemented tax gap was encoded honestly into the system (`deployment_safe` intentionally false) rather than hidden. Framing: "I'd rather ship a system that says 'not evaluated yet' than one that implies safety it doesn't have."

### STAR 5 — Measuring the process itself, then cutting it
- **S:** Suspicion that the AI workflow was accumulating overhead the way codebases accumulate cruft.
- **T:** Quantify workflow waste from repo evidence and prune ruthlessly, including my own governance inventions.
- **A:** Ran a ranked audit across config, ledgers, and 60 PRs of history using four parallel read-only subagents (`SETUP_AUDIT.md`, PR #475): measured 19% of commits as post-push compliance repair; identified a 22-PR patch loop that overran the written 2-patch escalation rule; found 19 of 21 skills couldn't auto-fire; found the 17-agent reviewer roster had zero recorded effectiveness after ~160 PRs; found doc bloat violating its own "never append" rule. Also judged where *not* to act: the "46% same-chat vs fresh-chat rule" deviation was deliberately left alone because most same-chat sessions were legitimate (Finding 9: do nothing).
- **R:** A prioritized fix list (single-pass PR packaging skill, ledger truncation, 12h PR sweep replacing hourly polling) with a candid meta-conclusion in the repo: documentation alone didn't change behavior — CI gates and tests did. Framing: this is exactly the Director-level muscle of measuring a team's process, killing your own darlings, and knowing which metric deviations are noise.

---

# 5. LIKELY CHALLENGE QUESTIONS

The 10 hardest questions a skeptical interviewer would ask, with repo-grounded honest answers.

**Q1. "This is a single-user personal app. Why does it say anything about operating at Director scale?"**
Honest answer: user scale is 1, and I won't pretend otherwise. What scales is the *management system*: I ran a multi-agent workforce through 48 PRs with written role contracts, compressed work orders, routed reviewers, per-PR cost accounting, a miss ledger, and CI-enforced quality gates — and I have the audit trail (`docs/ai/USAGE_LEDGER.md`, 172 rows; `MISS_LEDGER.md`; `ai_pr_readiness_check.py`). The transferable claim is directing AI-augmented delivery with governance, not serving users at scale.

**Q2. "The AI wrote 141 of 190 commits. What did *you* actually do?"**
The commits are the least interesting layer. The human-authored artifacts are the direction system: the operating system doc, safety packs, severity routing, release gates, the decision log with rejected alternatives, the audits that reset course, and the reject/redo call on PR #472. Every merged PR also passed through human review as the merge gate (`git shortlog`: 49 commits by the human, largely merges). The honest framing: I was the architect, editor, and quality authority for a very fast, very literal workforce — which is precisely the emerging shape of analytics leadership.

**Q3. "You built 17 reviewer agents and your own effectiveness ledger for them is empty. Isn't that governance theater?"**
Yes — partially, and I found it myself. `AGENT_EFFECTIVENESS_LEDGER.md` still reads "None yet," and my own setup audit (Finding 7) concluded that CI gates, tests, and fresh-context audit sessions were catching issues, not the agent roster, and recommended pruning ~11 agents. The lesson I carry: instrument the ROI of governance mechanisms from day one, and be willing to delete your own inventions.

**Q4. "Your rules say 'stop after two failed patches.' You ran a ~22-PR provider patch loop. So the rules don't work?"**
The written rule didn't stop it; the loop ended with a PR literally titled "stop provider patch loop" (#447), and the audit quantified the damage. What actually changed behavior was mechanizing the rule — CI now hard-fails a PR with follow-up count ≥3 and no escalation note (Check H). That's the general finding, stated candidly in my own audit: policy documents don't change agent behavior; enforcement systems do. Same lesson applies to human teams.

**Q5. "Where's the evidence the investment recommendations are any *good*? Do you have outcome data?"**
Not in this repo, and I won't claim it. The repo contains outcome-tracking scaffolding (migration `005_outcome_tracking.sql`) but no performance evaluation of recommendations, and the product explicitly labels output "deterministic allocation guidance, not personalized investment advice" (`paycheck_plan_preview.py:29`). What I can defend rigorously is decision *integrity*: deterministic, auditable, evidence-gated, fail-closed. Whether the policy beats VTI-and-chill is an open question the system was explicitly designed *not* to overclaim — a benchmark-vs-VTI feature was specced and consciously deferred (`PRODUCT_SPINE_REALITY_AUDIT.md`).

**Q6. "You claim 'runtime proof' but admit you never validated against production."**
Correct, and the report says so before you could ask: production credentials weren't available in the build environment, so proof ran the real application locally — real routers, real JWT verification, real policy engines — with fixtures injected only at the outermost data boundaries, and an explicit list of what was and wasn't validated, including that the Vercel preview couldn't be reached from the sandbox (`REFACTOR_REPORT.md:521-552`, `docs/ai/proof/consolidation/RESPONSES.md:679-684`). I'd rather show you a labeled evidence boundary than an unlabeled green checkmark.

**Q7. "diagnostics.py is 212KB. orchestrator.py is 117KB. Is this quality code?"**
No — those files are technical debt, and I'll say so. Context: `diagnostics.py` is a cert-gated *operator* surface (36 endpoints), deliberately kept during consolidation because protected paths import it (`REFACTOR_REPORT.md:363-378`); it's not user-facing. The consolidation prioritized deleting whole legacy surfaces (~43K lines) over restructuring survivors. Splitting those files is real, acknowledged follow-up work. Notably, the invariant tests don't care about file size — the `decide()` import boundary is enforced regardless.

**Q8. "Isn't your 'deterministic policy' just hardcoded if-statements with extra ceremony?"**
The kernel *is* explicit conditional logic — by design, because auditability was the requirement: no composite score, strict priority order, every HOLD carries its blocker reasons, conviction caps prevent overconfident output (`decision_policy_v1.py:120-163,457-523`). The ceremony isn't the if-statements; it's the containment system that keeps stochastic components from bypassing them — import-boundary tests, DB constraints, sanitizers. In an era of LLM-everything, choosing boring, testable logic for the decision layer and restricting AI to evidence generation *was* the architectural judgment.

**Q9. "One in five of your commits was PR-compliance repair. Your governance created that overhead."**
Partly true, and I measured it myself rather than hiding it (`SETUP_AUDIT.md` Finding 1: 36/191 commits). Root cause analysis showed most repair came from authoring PR bodies before running the gate, and from rules living in docs instead of pre-push mechanics; the fix was process placement (single-pass PR packaging, validate-before-push), not abandoning the gate. The gate also demonstrably caught real claim-vs-reality mismatches. Governance ROI needs the same cost-benefit scrutiny as any pipeline — and this repo contains that scrutiny.

**Q10. "Your PR numbers run to #476 but there are only 48 merges here. Are you inflating history?"**
The opposite — I'll flag it before you do. This repo's history starts at a squashed import (root commit `f3702017`, 2026-05-25); PRs merged *in this history* are #424–#476 with 48 merges and a few gaps (#434, #448, #465, #472 — #472 being the rejected consolidation). The usage ledger references earlier PRs (back to ~#77) from the pre-import history, which isn't in this repo — that's exactly the kind of thing I put in the "verify with me" column rather than assert (§6).

---

# 6. ASK PRASHANTH

Gaps only you can fill. Do **not** assert these in interviews until you've confirmed them from your own records.

1. **Pre-import history.** This repo begins 2026-05-25 with a squashed import; the usage ledger references PRs back to ~#77. Where did the project actually start, when, and what happened in PRs #1–#423? (Prior repo, earlier prototypes, the Streamlit v1 era timeline.)
2. **Total calendar time and weekly effort.** The visible history spans 2026-05-25 → 2026-07-19 in episodic bursts (35-commit and 30-commit peak days, a fully quiet week). What was the real elapsed time and hours/week for the whole project?
3. **Production reality.** Railway/Vercel/Supabase configs and a "production regression" narrative exist in-repo (`HANDOFF.md`), but the repo can't prove current deployment status. Is it live today? Used daily/weekly?
4. **Real money and real decisions.** Portfolio size, whether you actually follow the Buy/Hold/Trim/Sell and Paycheck Advisor outputs, and any outcome you'd be comfortable citing (even qualitatively). The repo deliberately contains no performance claims.
5. **Actual AI spend.** 92% of finance ledger rows carry `unavailable` in token columns (`SETUP_AUDIT.md` Finding 2). What did this cost per month in subscriptions/API? A real dollar figure makes the cost-discipline story land.
6. **The three-actor workflow in practice.** `AGENTS.md` codifies ChatGPT as architect/prompt-engineer, Claude models as builders, Codex for surgical fixes. How did this actually run day-to-day (e.g., were prompts really authored in ChatGPT and pasted)? Any numbers on prompts per PR?
7. **The sister travel repo.** `SETUP_AUDIT.md` audits a second repo (travel concierge) with its own ledgers. Is that a story you want to pair with this one (portfolio of AI-managed projects), and what's its status?
8. **Post-audit follow-through.** Did the SETUP_AUDIT "do these three first" fixes (ship-pr skill, ledger truncation, PR-sweep routine) get implemented after 2026-07-18? The repo at `b113d62` predates any evidence of them.
9. **Why this project.** The business/personal motivation (frustration with existing tools? learning vehicle for AI-directed development? real family finance need?) — the narrative hook only you can supply.
10. **Where it goes next.** Multi-user ambitions (which would reverse decision D9), the deferred tax/wash-sale engine, Watchtower delivery (currently parked behind Resend domain verification per `PRODUCT_SPINE_REALITY_AUDIT.md`) — what's actually planned vs. abandoned.

---

*Every file path and commit hash above is directly checkable in this repository. Where two internal sources disagreed on a count (e.g., test totals recorded at different dates), the number shown was re-measured against the working tree at `b113d62`, with the source of any historical figure cited.*
