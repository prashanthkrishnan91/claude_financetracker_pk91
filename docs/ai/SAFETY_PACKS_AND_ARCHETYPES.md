# Safety Packs and Build Archetypes — Finance

This document is the repo-native source for reusable **safety packs** and **build archetypes**. Prompts reference these by name; they do not paste the rules.

A safety pack is a named bundle of constraints / invariants / required evidence that a prompt would otherwise repeat. A build archetype is a named shape for the slice itself (capability slice, scaffold, plumbing fix, etc.).

When a slice fits a pack/archetype, the prompt names it and the pack's rules are in force automatically. Do not paste the contents below into prompts.

---

## Shared safety packs

### No Visible Behavior Change Pack

- **When to use:** refactors, internal cleanups, capability scaffolds, deduping helpers, moving code without changing visible output.
- **What it owns:** no change to user-visible UI text, layout, snapshot fields, decision authority, action surfaces, or external contracts.
- **Required evidence:** snapshot/contract diff is empty for visible fields; UI golden scenarios unchanged; targeted unit/contract bundle green.
- **When not to use:** the slice is intentionally adding/changing visible behavior — use the relevant feature pack instead.

### Backend-only Scaffold Pack

- **When to use:** new module, adapter, policy, or pipeline seam shipped disabled behind a flag/contract.
- **What it owns:** no UI surface changes, no visible decision authority change, scaffold is gated off by default, contracts are forward-compatible.
- **Required evidence:** Tier 1 contract bundle green; flag/gate verified off; visible snapshot/UI unchanged.
- **When not to use:** the slice flips visibility — use the shadow-to-visible-governance archetype instead.

### Runtime/API Contract Pack

- **When to use:** any change to API shape, snapshot endpoint, worker, db row shape, env, route, or provider behavior.
- **What it owns:** contract diff stated explicitly; downstream consumers identified; runtime evidence required (Railway / Supabase / snapshot); manual actions explicit if any.
- **Required evidence:** runtime check named (`/runtime-gate`); contract bundle green; downstream consumers updated or explicitly out-of-scope with split proposal.
- **When not to use:** purely internal helper change with no contract impact.

### No Provider/LLM Expansion Pack

- **When to use:** any slice touching prompts, providers, LLM behavior, fanout, or research workers.
- **What it owns:** no new providers, no expanded LLM authority, no new prompt surface that owns visible action authority, no broadened tool use.
- **Required evidence:** named contract showing what the LLM may and may not do; reviewer-agent check (`policy-authority-reviewer`); claim-safety check.
- **When not to use:** the slice is intentionally introducing a new provider via a deliberate, gated provider-expansion slice.

### Plain-English UI Pack

- **When to use:** any visible Finance UI / copy / card / decision surface.
- **What it owns:** no raw metric keys, no diagnostics, no shadow labels, no posture labels, no advanced jargon leakage; copy is plain-English; no leaked thresholds.
- **Required evidence:** `plain-english-ui-reviewer` clean; UI golden scenario screenshot/diff.
- **When not to use:** backend-only slices that do not touch visible UI.

### Evidence/Claim Safety Pack

- **When to use:** any change to evidence atoms, sourced artifacts, research outputs, or claim text.
- **What it owns:** every visible claim must trace to an evidence source; no fabricated claims; no leaked diagnostics.
- **Required evidence:** `claim-safety-gate` clean; reviewer-agent check (`data-truth-reviewer`).
- **When not to use:** structural changes that don't touch claim text or evidence.

### SQL/Persistence Manual Action Pack

- **When to use:** any Supabase SQL, schema, RLS, auth, persistence-contract change, or manual-action-required change.
- **What it owns:** explicit SQL listed; explicit manual actions in PR summary; runtime cert plan; rollback plan if applicable.
- **Required evidence:** SQL block; `sql-runtime-reviewer` clean; manual actions checklist updated.
- **When not to use:** the slice has no schema / persistence change.

### Performance/Latency Pack

- **When to use:** any latency-sensitive surface, snapshot endpoint, route budget, cache, db, or worker change.
- **What it owns:** named latency budget; benchmark plan; before/after numbers required for visible performance claims.
- **Required evidence:** `performance-benchmarker` evidence; runtime trace.
- **When not to use:** non-latency-sensitive slice.

### Test Tier Pack

- **When to use:** every PR.
- **What it owns:** chosen test tier per `docs/ai/TEST_ROUTING.md`; reason it was sufficient; whether full suite was skipped or run with explicit reason.
- **Required evidence:** PR summary states tier and reason.
- **When not to use:** never — every PR uses this pack.

---

## Finance-specific safety packs

### Deterministic Decision Authority Pack

- **When to use:** any slice touching Buy / Hold / Trim / Sell, decision logs, snapshots, or visible action surfaces.
- **What it owns:** deterministic Intel v3 backend policy is the only owner of visible action authority. LLMs, agents, research workers, and prompts may produce sourced artifacts but never own the final visible action. Decision logs must remain deterministic with no LLM dependency.
- **Required evidence:** `policy-authority-reviewer` clean; deterministic decision-log row produced by the test fixture; no LLM input in the visible decision path.
- **When not to use:** slice does not touch decisions/snapshots/actions.

### Valuation Safety Pack

- **When to use:** any slice touching valuation, intel cards, decision rationale, or visible valuation surfaces.
- **What it owns:**
  - no price target
  - no fair value
  - no intrinsic value
  - no buy-below / sell-above text
  - no raw metric leakage
  - no threshold leakage
  - deterministic backend policy owns Buy/Hold/Trim/Sell
- **Required evidence:** UI/copy review clean against the above list; reviewer-agent check (`plain-english-ui-reviewer`, `policy-authority-reviewer`).
- **When not to use:** non-valuation surfaces.

### Data Truth / Evidence Suppression Pack

- **When to use:** any slice touching evidence adapters, Data Truth, or visible reasoning.
- **What it owns:** missing/stale/weak/conflicting data must suppress affected axes; no fabrication; no "best effort" hallucinated reasoning.
- **Required evidence:** suppression behavior verified by adapter tests; `data-truth-reviewer` clean.
- **When not to use:** slice does not touch evidence pipelines.

### Deploy/Watchtower Boundary Pack

- **When to use:** any slice that crosses the Deploy / Watchtower / Intel boundary, snapshot endpoints, or visible decision contracts.
- **What it owns:** snapshot endpoints and visible decision contracts must remain source-of-truth consistent; no drift between Deploy view and Intel decision authority.
- **Required evidence:** snapshot diff; runtime trace; `roadmap-guardian` check on direction.
- **When not to use:** purely Intel-internal slice with no Deploy/Watchtower impact.

---

## Shared build archetypes

A build archetype is a named shape for the slice. The prompt names exactly one.

### capability-slice

One coherent product or backend capability shipped end-to-end at the appropriate visibility level. Default. Includes related code, contract, tests, docs.

### disabled-promotion-scaffold

New module/adapter/policy shipped disabled behind a flag/contract. No visible change. Followed (later) by a shadow-to-visible-governance slice when ready to promote.

### shadow-to-visible-governance

Promotes a previously-scaffolded capability from shadow to visible. Owns the governance: feature flag flip, contract reveal, decision-authority handoff, runtime cert.

### full-plumbing-root-cause-fix

Sev 1 or stuck-symptom fix that requires the durable end-to-end fix across the seam, not a tactical patch. Requires runtime evidence and contract audit.

### contract-consolidation

Unifies parallel/duplicate contracts (e.g., parallel adapters, duplicate snapshot fields, drift between Deploy and Intel). Owns the migration plan and downstream consumer audit.

### runtime-validation

Deployment / Railway / Supabase / snapshot validation slice. Produces runtime evidence to certify a prior change.

### UI-surface-pass

Capped UI polish or visual consistency pass on one page/component. Requires `<ui_budget>`.

### merge-gate

Cheap PR review for merge readiness. Read-only. No fixes; report blockers.

### workflow-update

Documentation / workflow / OS update only. No product code changes. No new OS version labels (extend OS v4 in place).

---

## How a prompt uses this file

```
<safety_packs>
Deterministic Decision Authority Pack, Valuation Safety Pack, Backend-only Scaffold Pack, Test Tier Pack.
</safety_packs>

<build_archetype>
capability-slice
</build_archetype>
```

That is sufficient. Do not paste the pack contents.
