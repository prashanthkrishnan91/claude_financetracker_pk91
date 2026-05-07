# Definition of Done — Finance Tracker

Passing tests is necessary but not sufficient. Done means the relevant product invariant is proven.

| Task type | Done means |
|---|---|
| Decision policy/kernel change | Deterministic policy tests pass; snapshot/source-of-truth behavior is safe; visible actions remain Buy/Hold/Trim/Sell only. |
| Evidence/Data Truth change | Source mapping, suppression behavior, and downstream decision inputs are tested; weak/missing data does not fabricate evidence. |
| Research artifact/worker change | Artifacts are sourced, auditable, idempotent, and not final action authority; forbidden payload checks pass. |
| Snapshot endpoint change | API route, source-of-truth, and frontend client contract are audited; shape changes are documented. |
| Frontend Intel UI change | UI remains plain-English; no raw metric keys, diagnostics, shadow labels, posture labels, or jargon leak. |
| SQL/migration change | Manual Supabase SQL steps, sanity checks, and rollback/repair notes are explicit. |
| Env/feature flag change | Defaults are safe; rollout and rollback are documented; Railway/Vercel redeploy need is stated. |
| Docs/workflow change | No runtime code changed; instructions are concise and do not conflict with existing docs. |

## Required PR proof

Every PR must state:

- what changed
- why it is done
- tests actually run
- known limitations
- UI validation needed yes/no and why
- Supabase SQL yes/no
- runtime/product impact
