# Finance Tracker — Roadmap

Staged roadmap with entry/exit gates. This is not a backlog. It is the product spine.

For live work, see `docs/product/BUILD_QUEUE.md`.
For gates, see `docs/product/RELEASE_GATES.md`.

## Stage 1 — Intel v3 correctness and evidence trust

- Goal: Intel decisions are deterministic, auditable, and trustworthy end-to-end.
- Why it matters: nothing downstream is safe to build until Intel decisions are trustworthy.
- Entry criteria: Intel v3 backend policy in place.
- Exit gate: Intel v3 Certification Gate passes (decisions certified, no LLM final action authority, Data Truth honest about gaps).
- Example build slices: deterministic policy hardening, evidence-check copy correctness, suppression integrity, snapshot persistence, certification verification.
- Do not expand into: Deploy UI, Watchtower triggers, design polish, or research-agent UI.

## Stage 2 — Deploy exact-dollar action plans

- Goal: certified Intel decisions become understandable exact-dollar action plans.
- Why it matters: turns Intel from analysis into action without ceding decision authority to LLMs.
- Entry criteria: Stage 1 exit gate passed.
- Exit gate: Deploy Readiness Gate passes.
- Example build slices: action-plan foundation, exact-dollar buy/trim/sell planner, constraint/guardrail engine, plain-English UI for Deploy.
- Do not expand into: broker execution, auto-trading, or LLM final action authority.

## Stage 3 — Watchtower event triggers

- Goal: Watchtower detects meaningful changes worth a user's attention.
- Why it matters: turns the cockpit into something that earns long-term trust.
- Entry criteria: Stage 2 exit gate passed.
- Exit gate: Watchtower Readiness Gate passes.
- Example build slices: trigger model, suppression rules, watchtower data plumbing.
- Do not expand into: alert delivery, push noise, or LLM-owned alerts.

## Stage 4 — Alerts and action feedback

- Goal: rare, actionable alerts that respect amateur-investor clarity.
- Why it matters: alerts are signal, not noise; this is where the product earns retention.
- Entry criteria: Stage 3 exit gate passed.
- Exit gate: Alert Readiness Gate passes.
- Example build slices: alert delivery surface, action-feedback loop, alert mute/scope controls.
- Do not expand into: high-frequency push, marketing alerts, or generic news.

## Stage 5 — Research artifact UX

- Goal: research artifacts (LLM/agent-produced) are exposed in a clear, support-only role.
- Why it matters: research adds value without ever owning visible decisions.
- Entry criteria: Stage 4 exit gate passed (or earlier if research artifacts already exist and need a UX surface).
- Exit gate: research artifact UX is plain-English, clearly labeled support-only, and does not leak raw metrics.
- Example build slices: research artifact viewer, evidence linkage UI, support-only labeling.
- Do not expand into: making research output visible decisions or autonomous actions.

## Stage 6 — Premium cockpit design polish

- Goal: premium feel and clarity across the cockpit.
- Why it matters: this is the final polish layer; before this, decision/action correctness rules.
- Entry criteria: Decision/action loop is stable across Intel, Deploy, Watchtower, Alerts.
- Exit gate: Finance Design Polish Gate passes.
- Example build slices: visual system, motion polish, plain-English copy pass, mobile parity.
- Do not expand into: new feature surfaces during the polish pass.
