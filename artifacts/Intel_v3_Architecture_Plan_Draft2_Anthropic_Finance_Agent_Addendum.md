# Intel v3 Architecture Plan Draft 2 - Anthropic Finance Agent Roadmap Addendum

Date: 2026-05-06
Scope: Finance Tracker Intel v3 roadmap only. This addendum extends `artifacts/Intel_v3_Architecture_Plan_Draft2.pdf` with lessons from Anthropic's public finance-agent direction while preserving the Finance Tracker architecture principle: deterministic policy owns recommendations; agentic/LLM systems produce sourced research artifacts only.

## Decision

Add Anthropic-inspired finance-agent ideas to the roadmap, but do not replace the Intel v3 decision engine with Claude finance agents or any black-box analyst agent.

The release validates the direction of building analyst-style workflows: skills, connectors, subagents, audit logs, source-grounded outputs, and human review. It does not change the core Finance Tracker rule that Buy/Hold/Trim/Sell must be deterministic, replayable, testable, and grounded in the app's own truth/evidence contract.

## Non-negotiable architecture boundary

- The deterministic backend decision kernel remains the only authority for visible Buy/Hold/Trim/Sell actions.
- Agentic/LLM workers may create research artifacts: claims, evidence, risks, catalysts, source links, summaries, and debate memos.
- Agentic/LLM workers must not directly set visible recommendation actions, deploy allocations, trade instructions, price targets, or conviction labels.
- Every generated artifact must carry provenance, input snapshot, source links, missing-data flags, version, and audit metadata.
- User-facing UI stays plain-English. Backend may use advanced metrics and research vocabulary, but the UI should translate into approachable language.
- No Business Read UI, no broker-style fake precision, no raw metric-key UI, no giant generic stock-report dump, and no posture labels outside Buy/Hold/Trim/Sell.

## Roadmap insertion timeline

### Phase 0 - Current priority: Intel v3 visible decision certification

Do this first. Do not pivot to finance agents while the core decision contract is unstable.

Focus:
- Certify the visible Intel v3 card path end-to-end.
- Keep Buy/Hold/Trim/Sell action labels only.
- Ensure evidence-quality/truth contracts no longer collapse rich cards into blanket HOLD.
- Ensure page-load and Run Agents paths converge.
- Preserve deterministic cache/fingerprint correctness and runtime certification logs.
- Keep Deploy untouched until Intel visible action semantics are proven.

Exit criteria:
- Production certification shows non-collapsed action distribution where evidence supports it.
- Evidence quality is not uniformly WEAK/LOW when real signals are present.
- Card explanations are action-consistent and plain-English.
- Runtime certification can be run from CI/ops without UI-only validation.

### Phase 1 - Finance Agent Skill Pack Audit

Timing: immediately after Phase 0 certification passes.

Goal: extract architecture patterns from Anthropic-style finance agents without adding new runtime dependency yet.

Deliverable:
- A repo-local architecture note mapping useful finance-agent skills into Finance Tracker modules.
- Explicit decisions on what to reuse, defer, or reject.

Audit targets/concepts:
- Market researcher: industry overview, competitive landscape, peers, idea shortlist.
- Equity research / idea generation: value, growth, quality, special situations, risk/catalyst framing.
- Thesis tracker: thesis pillars, falsification triggers, risks, catalysts, conviction history.
- Earnings reviewer: quarter review, guidance changes, surprise drivers, estimate-revision context.
- Comps and valuation reviewer: peer comparison, multiple context, valuation range evidence.
- Catalyst calendar: earnings dates, product events, regulatory events, macro/company catalysts.
- Risk red-team: counter-thesis, concentration risk, balance-sheet/quality/valuation risks.

Guardrails:
- This is research/planning only unless explicitly approved for implementation.
- No visible behavior changes.
- No provider expansion yet.
- No LLM-as-final-decision logic.

### Phase 2 - Research Artifact Store v1

Timing: after Phase 1 audit and after Intel visible decision semantics remain stable in production.

Purpose: create a durable substrate for sourced analyst-style evidence before adding active research workers.

Core objects:
- `research_artifact`: ticker/candidate, artifact_type, generated_at, source_snapshot_version, model/tool version, status.
- `sourced_claim`: claim text, normalized claim category, source URL/title/date, confidence, extraction method.
- `metric_observation`: value, unit, period, source, freshness, data-quality state, missing/unavailable/conflicting markers.
- `risk_item`: risk category, severity, evidence, source, stale/missing flag.
- `catalyst_item`: catalyst type, date/window, evidence, source, probability/uncertainty language.
- `thesis_pillar`: bull/bear/neutral pillar, supporting claims, falsification triggers.
- `audit_event`: input snapshot, tool calls, rejected claims, missing data, artifact version, cost/latency envelope.

Rules:
- Store evidence separately from decisions.
- A weak or missing artifact must not fabricate confidence.
- Artifacts may feed truth contracts and explanation builders only through typed adapters.
- Every adapter must be testable with synthetic fixtures and fail safely.

### Phase 3 - Research Analyst Workers v1

Timing: after Research Artifact Store v1 exists.

Purpose: add optional backend workers that write structured artifacts, not final actions.

Candidate workers:
- Earnings Reviewer Worker: parses quarter/news/earnings context into surprise, guidance, margin, demand, and risk artifacts.
- Thesis Tracker Worker: maintains thesis pillars, what changed, what would invalidate the thesis, and conviction-change evidence.
- Catalyst Watch Worker: tracks upcoming catalyst windows and whether they support adding, waiting, trimming, or monitoring.
- Valuation Context Worker: compares current valuation to history, peers, growth, quality, and risk; emits context, not price targets.
- Risk Red-Team Worker: produces counter-thesis and concentration/drawdown/liquidity/business-model risks.
- Market Researcher Worker: summarizes sector/theme context and peer sets.
- Idea Generation Worker: produces candidate shortlists for review only.

Hard boundaries:
- Workers run behind feature flags and cost controls.
- Workers must write artifact records with provenance.
- Workers cannot update visible card actions directly.
- Workers cannot update Deploy allocations directly.
- Workers cannot execute trades or provide execution instructions.
- Outputs must be replayable, inspectable, and suppressible if stale or low-quality.

### Phase 4 - Opportunity Scout / Hidden Gems v1

Timing: after artifact pipeline and at least one or two analyst workers are stable.

Purpose: help the user discover good companies outside the current holdings/watchlist without turning the app into a random stock picker.

Inputs:
- Themes/sectors the user cares about.
- Quality, valuation, momentum, profitability, balance-sheet, estimate-revision, insider/ownership, and coverage-neglect signals when available.
- Existing holdings and concentration to avoid duplicates and hidden overlap.
- Research artifacts from Market Researcher / Idea Generation workers.

Output contract:
- Candidate shortlist, not a final buy list.
- Plain-English reason for why the candidate is worth researching.
- What evidence is missing.
- What would make it attractive or disqualify it.
- Watchlist import path.

Promotion rule:
- A candidate cannot affect Deploy until it has passed the same evidence/truth contract as existing holdings.

### Phase 5 - Optional Claude Managed Agents / external finance-agent evaluation

Timing: later, after internal artifact schemas and deterministic decision boundaries are stable.

Purpose: evaluate whether external/managed agent infrastructure improves research quality, latency, or cost enough to justify integration.

Evaluation criteria:
- Can it produce typed artifacts matching our schema?
- Are sources, tool calls, and reasoning audit logs inspectable?
- Can costs and timeouts be capped per run?
- Can outputs be replayed or regenerated deterministically enough for debugging?
- Can weak/missing/conflicting evidence be represented honestly?
- Does it avoid final investment recommendations and trade/deploy authority?
- Does it improve quality beyond our own focused workers enough to justify dependency risk?

Default posture:
- Do not make external finance agents the default decision engine.
- Use them, if ever, as optional research workers behind flags and review boundaries.

## How this changes the original Draft 2 plan

This addendum strengthens the long-term Intel architecture in four ways:

1. It adds an explicit research-artifact substrate before agentic workers.
2. It separates analyst-style research workflows from recommendation authority.
3. It introduces Opportunity Scout / Hidden Gems as a later roadmap module.
4. It adds a future evaluation path for Claude/Anthropic finance-agent infrastructure without making it a core dependency.

## Recommended next implementation sequence

1. Finish and certify the current visible Intel v3 decision path.
2. Add this addendum to the architecture plan and HANDOFF notes.
3. Run a focused Finance Agent Skill Pack Audit prompt.
4. Design Research Artifact Store v1.
5. Build one narrow analyst worker after the artifact store exists, likely Thesis Tracker or Earnings Reviewer.
6. Add Opportunity Scout only after evidence artifacts and worker reliability are proven.

## Prompting rule for future build prompts

When generating prompts from this roadmap, the prompt must state:

- Model choice and why.
- New chat vs follow-up recommendation.
- Severity level.
- Usage estimate and budget gate.
- Success criteria.
- Non-goals.
- Tests/merge gate.
- Documentation updates: update `docs/ai/HANDOFF.md`, `v2/progress_log.md`, and the roadmap artifact/addendum when architecture changes.

For implementation prompts, include this boundary sentence:

"Do not implement an LLM/agent as the final decision authority. Agentic systems may produce sourced research artifacts only; deterministic backend policy remains the sole owner of visible Buy/Hold/Trim/Sell and Deploy behavior."
