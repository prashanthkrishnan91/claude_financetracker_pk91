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

## Sections completed later in this refactor

- Kept and why — see "What was kept"
- Deleted and why — see "What was deleted"
- Fixes and verification output — see "Fixes"
- Judgment calls — see "Judgment call log"
- Final full test run — see "Final test suite output"

(Placeholders intentionally listed so the working plan is explicit; each is filled in
before the PR is opened.)
