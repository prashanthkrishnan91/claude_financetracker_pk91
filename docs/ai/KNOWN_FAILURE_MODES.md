# Known AI Failure Modes — Finance Tracker

Use this file before non-trivial implementation, review, or follow-up prompts. Add to it after repeated failures.

## Decision authority failures

- Deterministic backend Intel v3 policy owns visible Buy/Hold/Trim/Sell decisions.
- LLMs, agents, research workers, and research artifacts may provide sourced evidence later, but must never own final visible action authority.
- Do not add agentic or multi-agent research as final decision authority.
- Do not patch shadow diagnostics when the issue is visible decision plumbing.

## Data truth failures

- Any finance data claim must be deterministic, sourced, auditable, or honestly marked unavailable.
- Missing, stale, weak, unavailable, or conflicting data must suppress affected axes, not fabricate evidence.
- Evidence quality controls should not collapse visible behavior without a traced policy reason and tests.
- Do not tune thresholds blindly when source mapping or plumbing is the root cause.

## UI leakage failures

- Do not expose raw backend metrics, raw metric keys, advanced finance jargon, shadow diagnostics, internal policy labels, or posture labels in UI.
- Frontend must stay plain-English for amateur investors.
- Backend can use advanced metrics internally only if translated to user-friendly explanation.

## Contract failures

- Snapshot endpoints and visible UI must agree on source-of-truth behavior.
- If changing decision policy/kernel, audit snapshot API, frontend Intel UI, recommendation cards, filters, and logs.
- If changing evidence adapters or Data Truth contracts, audit downstream decision inputs, suppression behavior, and visible rationale copy.
- If changing research artifact store/workers, prove artifacts remain supporting evidence only and are safe_for_decision=false unless explicitly promoted by a future approved policy.

## Runtime/manual-action failures

- Supabase SQL requirements must be explicitly stated in PR summaries.
- Env flags must state default, rollout, rollback, and whether Vercel/Railway redeploy is needed.
- Runtime certification/log evidence is required when production wiring, snapshot behavior, Railway, Supabase, or provider behavior is the claim.

## Validation failures

- Avoid UI churn unless product-visible behavior actually needs it.
- UI validation is not required after backend-only internal/shadow/docs PRs.
- If production UI shows wrong visible decisions, debug visible decision plumbing first, not shadow summaries.
