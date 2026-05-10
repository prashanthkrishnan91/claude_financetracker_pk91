# Repo Hygiene

Lightweight rules + a read-only audit script so obsolete source and tests do not silently accumulate again. Owned by the AI Repo Operating System v4.

## Scope

This doc governs **what to delete, what to preserve, and how to detect it cheaply**. It does not replace contract audit, test-tier selection, or product-stage routing.

---

## What counts as obsolete source

Delete (or rewrite) when **all** of these are true:

1. The source surface refers to a retired product (e.g. the Streamlit v1 Portfolio War Room app), a removed deployment target, or a one-time migration that has finished.
2. No active v2 router, service, frontend hook, model, test, or doc references the symbol or path.
3. Removal does not break a documented API contract for an external consumer (mobile shell, future agent, etc.).
4. Removal does not delete a finance/safety invariant, decision authority, or evidence-handling rule.

Examples that qualify: root `App.py` Streamlit shim, `.streamlit/`, `v1/` tree, root `requirements.txt` that only points to `v1/requirements.txt`, `migration_service.py` that only exists to bootstrap v1 → v2 data, the `/api/v1/positions/seed-v1` router endpoint with zero callers.

## What counts as obsolete test coverage

Delete a test only when you can name **why** in one line:

- It targets a removed surface (router, service, page) and adds no other coverage.
- It is permanently `pytest.mark.skip` / `xfail` with no current product reason.
- It duplicates another test's assertions on the same module and the duplicate adds no scenario.
- It is not collected by the active test runner (orphaned naming convention or gated module that no longer exists).

Always prefer rewrite over deletion for tests that exercise:

- Intel v3 policy (`decision_policy_v1`, `data_truth_v1`, snapshot builder),
- evidence/source readiness (`artifact_truth_readiness`, `artifact_observability`, SEC adapters),
- API auth, runtime certification, deploy/allocation, pricing fanout,
- recommendation contract / narrative contract regression,
- deterministic Buy/Hold/Trim/Sell / suppression invariants.

The default disposition for those families is **preserve**, even if the suite is large.

## What must be preserved even if it looks legacy

These are legitimate version labels, not v1-Streamlit residue. Do not delete:

- `/api/v1/...` and `api_v1` route prefixes — these are the live FastAPI namespace.
- Policy module names ending in `_v1` (`decision_policy_v1`, `data_truth_v1`, `valuation_context_adapter_v1`, …).
- Database migration filenames and on-disk SQL versions (`001_…`, `017_research_artifact_store_v1.sql`).
- Intel v3 architecture artifacts (`Intel_v3_Architecture_Plan_Draft2_*`, `Intel_v3_Living_Cockpit_*`, `Plan_v1.pdf`, etc.).
- Reasoning schema labels like `compact_v1`, `human_v2`, snapshot `schema_version`.
- Auth provider routes such as Supabase `/auth/v1/.well-known/jwks.json`.

When in doubt, keep the symbol and add an entry to the audit allowlist (see below).

## Required cleanup checklist for future PRs

Before opening any non-trivial PR:

1. Did this PR remove or replace a router, service, page, model, env var, or deployment target?
2. If yes, do any other source files, frontend hooks, tests, docs, env examples, or scripts still reference it? List them and either remove/rewrite or open a tracked follow-up.
3. Did this PR add a test for a surface that may itself be retired soon? If yes, mark the test path in the PR description.
4. Run `python3 scripts/repo_hygiene/audit_repo_hygiene.py` and resolve, suppress, or acknowledge each finding.
5. State the answer in the PR body, even if it is "no obsolete source produced."

## Test retirement rules

- Removal of a test must be paired with a one-line justification in the PR body or in this doc's commit message.
- Do not delete a test purely because it is slow, large, or noisy. Move slow tests to the appropriate tier per `docs/ai/TEST_ROUTING.md` first.
- Do not delete a test because the suite "passes without it" — that is necessary but not sufficient.
- A test that conditionally `pytest.skip()`s when an external directory or env is missing is **not** considered a stale skip; it is a defensive guard.

## Progress log size/quality rules

`v2/progress_log.md` follows the convention at the top of that file:

- ~150–250 lines target. Temporary expansion is allowed only for a major release.
- Keep current active phase, latest 3–5 merged PRs, next step, unresolved risks, durable architecture decisions. Nothing else.
- Move durable workflow lessons to `docs/ai/MISS_LEDGER.md` and durable product decisions to `docs/product/DECISION_LOG.md`.
- Do not append PR-by-PR forever; replace or summarize older entries when the file grows.
- Do not introduce new `progress_log_archive.md` files. If older detail must survive, summarize a one-paragraph "Compaction note" and discard the rest.

---

## Audit script

```bash
python3 scripts/repo_hygiene/audit_repo_hygiene.py
```

Read-only. Never deletes anything. Exits non-zero only when it cannot read a path; **findings alone do not fail CI**. Use the report as a merge-gate aid.

What it scans:

- Legacy strings: `streamlit`, `Portfolio War Room`, `seed_v1_positions`, `seed-v1` migration endpoint, `migration_service` import, root `App.py` shim references.
- Stale paths: presence of `v1/`, root `App.py`, root `requirements.txt` Streamlit shim, `.streamlit/`.
- Skipped/xfail test markers without a documented reason.
- `v2/progress_log.md` line count vs the ~250 soft cap.
- Orphaned `progress_log_archive.md` files.

What it intentionally **does not** flag:

- `/api/v1/`, `api_v1`, `_v1` policy/migration/artifact identifiers (allowlisted).
- Tests with conditional `pytest.skip()` guards on external directory presence.

### Allowlist

The script reads inline allowlist constants near the top of `scripts/repo_hygiene/audit_repo_hygiene.py`. Add a new pattern there with a one-line comment explaining why the match is legitimate. Keep the allowlist short — if it is growing, the rule itself probably needs updating.

### Adding new checks

Prefer to extend the existing scan with a new (pattern, description, severity) tuple rather than spawning a parallel script. If a check needs a new external dep, it does not belong here.

### Why this is not CI-enforced (yet)

The script is a manual merge-gate aid. We deliberately avoid:

- adding a heavy CI job for a low-frequency cleanup task,
- failing builds on cosmetic legacy strings,
- adding a new package dependency just for a string scan.

If repeated misses show up in `MISS_LEDGER.md`, promote the script to CI per the OS Learning Protocol promotion ladder — not before.
