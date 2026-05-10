# TEST_ROUTING — Finance Tracker

## Purpose

Prevent routine PRs from triggering full broad test runs while preserving critical regression coverage for Intel v3 contracts, deterministic policy, snapshot/runtime boundaries, and Data Truth adapters.

**Default rule:** Claude/Codex should **not** run the full backend suite by default for ordinary PRs. Use the lowest sufficient tier.

If exact test commands or bundle filenames are uncertain in this repo, Claude / `test-selector` should choose the existing tests that match the pattern below and state which ones it ran. Do not invent commands or fixture names.

## Tier model

### Tier 0 — Changed-file adjacent (default)

Use for docs-only changes, isolated backend helpers, isolated frontend changes, or pure-helper refactors.

- Run only the tests directly tied to the changed modules/files.
- Add one adjacent contract/snapshot test if the changed file is in a shared mapper / contract surface.
- Pure helper refactors: targeted unit tests only.

### Tier 1 — Intel v3 contract / policy / adapter bundles

Use for slices touching Intel v3 contracts, deterministic policy authority, evidence adapters, Data Truth, decision logs, or snapshot decision shape.

Canonical bundle patterns (run only relevant bundles):

- **Intel v3 contract bundle** — Intel v3 snapshot/contract tests for the changed surface.
- **Deterministic policy bundle** — policy/decision tests covering Buy/Hold/Trim/Sell authority, including the Deterministic Decision Authority Pack invariants.
- **Evidence adapter bundle** — adapter tests covering Data Truth suppression and evidence shape.
- **Snapshot endpoint bundle** — snapshot endpoint tests for the changed seam.

Run multiple bundles when the slice crosses bundles, but do not escalate to Tier 3 unless the criteria below are met.

### Tier 2 — Snapshot / runtime boundary bundles

Use at milestone boundaries or when touching the Deploy / Watchtower / Intel snapshot boundary, manual decision-log endpoints, or persistence seams.

- Run the relevant Tier 1 bundles plus the snapshot/runtime boundary bundle for the affected surface.
- Add a runtime trace if the slice changes deployment-visible behavior.

### Tier 3 — Full backend suite

Allowed only for:

- release checkpoints,
- shared infrastructure changes,
- test infrastructure changes,
- broad model/schema changes,
- suspicious targeted-test failures needing broad confirmation,
- explicit merge-gate request from PK.

If Tier 3 is used, the PR summary must include the explicit reason.

## Required PR evidence

Every PR summary must state:

1. **Test tier used**
2. **Why this tier was sufficient**
3. **Full suite skipped: Yes/No**
4. If full suite was run, the explicit reason
5. If skipped, the targeted bundles/tests that replaced it

## Critical invariants that must remain covered

Do not merge without targeted coverage for the slice's relevant invariants. Common ones:

- Deterministic Intel v3 backend policy owns visible Buy/Hold/Trim/Sell authority
- Decision logs remain deterministic with no LLM dependency
- Snapshot endpoints and visible decision contracts remain source-of-truth consistent
- Missing/stale/weak/conflicting data suppresses affected axes (no fabrication)
- No raw metric / threshold / shadow label leakage in visible UI
- No price target / fair value / intrinsic value text in visible surfaces

## Runtime validation

Required only when deployment or user-visible behavior changes (e.g., visible decision authority, snapshot shape, UI surface, provider behavior). Not required for backend-only scaffolds gated off by default.

Use the `railway-logs` personal Claude skill (when available) before patching app code for a runtime symptom; do not patch code based on speculation when logs would resolve it.

## How a prompt uses this file

The prompt names the **Test Tier Pack** plus the relevant Finance pack(s) in `<safety_packs>`. The PR summary then states tier + reason. The prompt does not need to paste the tier definitions.
