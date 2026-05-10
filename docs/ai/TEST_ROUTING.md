# TEST_ROUTING — Finance Tracker

## Purpose

Prevent routine PRs from triggering full broad test runs while preserving critical regression coverage for Intel v3 contracts, deterministic policy, snapshot/runtime boundaries, and Data Truth adapters.

**Default rule:** Claude/Codex should **not** run the full backend suite by default for ordinary PRs. Use the lowest sufficient tier.

If exact test commands or bundle filenames are uncertain in this repo, Claude / `test-selector` should choose the existing tests that match the pattern below and state which ones it ran. Do not invent commands or fixture names.

## Do not run all 3,900+ tests by default

The backend suite currently holds **3,926** tests (collected from `v2/backend`). That bank exists as a **release / regression / infra-change gate**, not as default PR validation. Running it on every routine PR is wasted compute and trains future prompts to default to Tier 3.

Hard rules:

- The full backend suite is a **Tier 3** gate only.
- Claude must **not** run the full backend suite by default.
- Every PR summary must state the smallest sufficient test tier and why it was sufficient.
- Tier 3 (full backend suite) is allowed **only** with one of these explicit reasons:
  1. Test infrastructure changed (event-loop usage, async helpers, broad mock helpers).
  2. Shared fixture / `conftest.py` / autouse fixture changed.
  3. Broad schema, contract, or model boundary changed (cross-cutting).
  4. Release checkpoint.
  5. Targeted tests failed suspiciously and broad confirmation is needed to rule out wider damage.
  6. PK explicitly requested the full suite.
- "Big change, just to be safe" is **not** a reason. Pick the smallest sufficient bundle and state why it covers the slice.

When in doubt, default to Tier 0 / Tier 1 with a stated reason rather than escalating.

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

## What to run — quick routing table

Match the change shape to the smallest sufficient validation. Do not invent
exact filenames or fixture names that aren't already known; use the named
patterns or named bundles.

| Change shape | Run | Skip |
|---|---|---|
| Docs-only / prompt / workflow / `.github` / `.claude` docs | `python3 scripts/repo_hygiene/audit_repo_hygiene.py` only | Backend full suite, frontend build |
| Single backend helper / service / one-file refactor | Changed-file adjacent backend tests only | Other unrelated bundles, full suite |
| Intel policy / Data Truth / SEC / valuation / EPS / priceband adapter | Relevant Tier-1 named bundle (decision policy / evidence adapter / Intel v3 contract) + adjacent contract/snapshot tests | Full suite unless a contract boundary moved |
| API route or response contract change | Route-specific tests + one response-shape test for the changed endpoint | Full suite unless many routes changed at once |
| Snapshot / runtime / Deploy / Watchtower boundary | Tier-1 relevant bundles + Tier-2 snapshot/runtime boundary bundle | Full suite unless schema/model crosses boundaries |
| Frontend-only change (no API contract touched) | Frontend tests / type / lint relevant to changed files only | Backend suite |
| Frontend change that crosses an API contract | Adjacent backend route tests + the relevant frontend tests | Full suite unless contract is shared across many endpoints |
| Test infrastructure change (event-loop, async helpers, broad mocks) | Tier 3 full backend suite | — |
| Shared fixture / `conftest.py` / autouse fixture change | Tier 3 full backend suite | — |
| Broad schema / contract / model boundary change | Tier 3 full backend suite | — |
| Release checkpoint or PK-requested merge gate | Tier 3 full backend suite | — |

Examples (only when already known) of named bundles:

- Intel v3 contract bundle — `tests/test_intel_v3_*` files matching the changed surface.
- Deterministic policy bundle — `tests/test_intel_v3_decision_policy.py`, `tests/test_v3_decision_policy.py`, etc.
- Evidence adapter bundle — adapter tests covering Data Truth suppression / evidence shape.
- Snapshot endpoint bundle — `tests/test_intel_v3_snapshot.py`, `tests/test_intel_v3_router_service.py`, etc.

If a bundle name is uncertain, run the changed-file adjacent tests and the nearest contract/snapshot test, then state in the PR summary which tests ran and why.

## Required PR evidence

Every PR summary must state, in this exact shape:

1. **Test tier used** — Tier 0 / 1 / 2 / 3.
2. **Why this tier was sufficient** — one or two sentences tying the tier to the change shape.
3. **Full suite run: Yes / No.**
4. If **Yes**, the explicit Tier-3 reason from the list above (1–6).
5. If **No**, the exact targeted tests / bundles / commands that ran instead.

A PR summary that omits any of the five lines should be treated as failing the merge gate and rewritten before merge.

## Critical invariants that must remain covered

Do not merge without targeted coverage for the slice's relevant invariants. Common ones:

- Deterministic Intel v3 backend policy owns visible Buy/Hold/Trim/Sell authority
- Decision logs remain deterministic with no LLM dependency
- Snapshot endpoints and visible decision contracts remain source-of-truth consistent
- Missing/stale/weak/conflicting data suppresses affected axes (no fabrication)
- No raw metric / threshold / shadow label leakage in visible UI
- No price target / fair value / intrinsic value text in visible surfaces

## Test infrastructure invariants

Two classes of failure look like product bugs but are actually test-infra bugs.
Catch them at PR time rather than chasing assertions:

- **Order-dependent async tests.** Never use `asyncio.get_event_loop()`
  inside a test. In a pytest-asyncio suite the default loop is created
  and closed per async test, so a sync test that grabs the default loop
  passes in isolation but fails after any async test runs first
  (`RuntimeError: There is no current event loop in thread 'MainThread'`).
  Use `asyncio.run(...)` (or explicit `asyncio.new_event_loop()` +
  `asyncio.set_event_loop(...)`) instead. The hygiene audit flags this.
- **Source-grep tests for forbidden field names.** Asserting
  `"forbidden_key" not in <module source>` is fragile: it conflates
  legitimate row reads (`row.get("structured_payload")`) with response
  exposure. Prefer asserting on the runtime response shape (e.g.
  `forbidden_keys & set(resp.keys())`) — see
  `tests/test_intel_v3_phase4_artifact_observability_endpoint.py
  ::TestEndpointResponseShape::test_response_has_no_raw_payload_field`.

## Runtime validation

Required only when deployment or user-visible behavior changes (e.g., visible decision authority, snapshot shape, UI surface, provider behavior). Not required for backend-only scaffolds gated off by default.

Use the `railway-logs` personal Claude skill (when available) before patching app code for a runtime symptom; do not patch code based on speculation when logs would resolve it.

## How a prompt uses this file

The prompt names the **Test Tier Pack** plus the relevant Finance pack(s) in `<safety_packs>`. The PR summary then states tier + reason. The prompt does not need to paste the tier definitions.

## Counting the regression bank

The full backend bank (Tier-3 gate) is sized via pytest's collect-only:

```bash
cd v2/backend && python3 -m pytest --collect-only -q | tail -3
```

Use this only when sizing the Tier-3 gate or after retiring/adding tests. It is **not** part of routine PR validation.
