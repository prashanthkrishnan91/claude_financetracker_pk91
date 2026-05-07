# Failure Recovery — Finance Tracker

Use this whenever a PR, prompt, runtime validation, SQL check, or UI test exposes a miss.

## Patch exhaustion rule

- After one failed patch: reclassify severity and restate the root cause hypothesis.
- After two related patches: stop patching. Move to full plumbing analysis or split plan.
- Do not stack local fixes when the failure is a policy, snapshot, Data Truth, SQL, UI contract, or product-invariant problem.

## If tests pass but product behavior fails

1. Write or identify an adversarial test that would have caught the miss.
2. Audit producer and consumer contracts.
3. Check runtime/SQL evidence if the issue involves Railway, Supabase, Vercel, snapshot persistence, providers, or deployment.
4. Fix the root cause, not the visible symptom.

## If visible decisions are wrong

- Debug visible decision plumbing first.
- Do not tune shadow diagnostics as a proxy for visible behavior.
- Trace policy input, deterministic policy output, snapshot serialization, frontend client, and visible card rendering.

## If runtime evidence is missing

- Do not guess.
- Use the `railway-logs` personal skill if available.
- If logs are insufficient because the app does not emit the right evidence, propose observability before claiming validation.

## If SQL validation fails

- Identify whether the issue is migration syntax, RLS, trigger/function behavior, idempotency, or data shape.
- Do not continue feature work until persistence correctness is restored.
- Include the exact manual SQL/sanity action in PR summary.

## Escalate when

- Deterministic decision authority is compromised.
- Research artifacts or LLM output can influence final visible actions.
- Raw diagnostics/metrics leak to UI.
- Snapshot source-of-truth and UI disagree.
- A fix requires three or more skill areas.
