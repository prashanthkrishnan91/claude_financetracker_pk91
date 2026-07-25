# HANDOFF — Current Repo State

Last updated: 2026-07-25 (Run Intel operational-reliability PR #488 — item 4
of the Run Intel trust-recovery sequence, OPEN, not yet merged, patched a
second round to close eight connected correctness gaps a final producer-to-
screen audit found: (1) the strict financial-truth preflight now freezes the
EXACT open-position/price rows it reconciled — zero second read, duplicate
active tickers block instead of silently deduping, a core DB read failure is
never reported as an empty portfolio; (2) fundamentals are labeled by REAL
currency domain (statement/reporting vs. quote/security, EPS as
currency-per-share, NaN/±infinity rejected, GBp/GBX never coerced to GBP);
(3) news parsing handles the CURRENT nested yfinance shape with strict
related-ticker-metadata-first relevance (never overridden by a text match);
(4) collector cache lookup is fail-closed and asset-type-scoped (a DB outage
is retried, never reinterpreted as a legitimate miss), plus a 24h macro
reuse path on the existing task table; (5) the specialist prompt object and
its `input_fingerprint` are now ONE helper (`axis_evidence_context`) —
`market`/`prior_action` excluded from both the prompt AND the fingerprint
together, `evidence_sources` included in the fingerprint, no axis claims
price lineage since price is never sent to any axis prompt; (6) session
cost/reuse metrics are literal (portfolio-context DB reads and degraded/
failed lane attempts no longer inflate `lanes_refreshed`); (7) a blocked
preflight's specific reason and bounded repair action render in the
existing `AdvisorReadinessPanel` (no new UI surface); (8) the hour/day/week
repeat-run matrix has real controlled-clock integration coverage across
equities/ETF/crypto, including the SEC/ETF long-TTL artifact-lane paths. See
the finish-plan item 4 entry below for full detail.)

Previously (2026-07-25, Deterministic conflict handling / review-LLM
deletion, PR #487 — COMPLETED. Deletes the conditional Sonnet/Haiku review
LLM (`REVIEW_SYSTEM_PROMPT`, `execute_review_task`, `WorkerSupervisor.review_llm`,
`intel_v3_distributed_review_model`/`_fallback_model`) and replaces it with a
small deterministic policy (`conflict_policy_v1.py`, ≤180 lines): the SAME
review-trigger thresholds (moved from `run_scheduler_v1.should_review`, now a
thin delegator), deterministic conflict assessment, and a fixed guardrail —
directional signal neutralized to HOLD, confidence capped at 0.49. The
`TASK_REVIEW_CONFLICT` task type, `axis="review"` output row, existing DB
schema, `conflict_review_status` vocabulary, and source-lineage review
manifest are all REINTERPRETED (not replaced) as deterministic conflict
resolution — `execute_conflict_resolution_task` reuses `source_lineage_v1`'s
existing review-input/lineage helpers purely as audit material, makes ZERO
`llm.ask_json`/provider calls, and fails closed
(`conflict_task_without_conflict`) if a durable conflict task's current
inputs no longer show a conflict. `decision_tasks_v1.aggregate_advisory_signal`
now aggregates only non-review outputs; a valid deterministic conflict row
overlays `advisory_signal` (HOLD, confidence ≤ 0.49) onto the ordinary
`pre_conflict_advisory_signal`, both preserved in the decision audit record.
`decision_policy_v1.decide()` is UNTOUCHED and remains the only visible-action
authority — existing portfolio-fit/risk priority still independently produces
TRIM/SELL over a neutralized conflict signal. See the reduced finish plan
below — this entry is item 3.)

Previously (2026-07-24, Run Intel source-reference lineage PR 2/7, patched
same-PR TWICE — makes `evidence_bundle.source_refs`/specialist
`evidence_refs`/review `evidence_refs` genuinely source-linked instead of
PR 1's honest "0 of N", then closes six release-blocker semantic gaps
(round 2), then closes six further normal-path lineage-trust gaps
(round 3): axis-specific manifest structure enforcement, sentiment-catalyst
supplied-lane precision, terminal-task-outcome vs effective-evidence
separation, internal-artifact-id-free fingerprints, one normalized
review-prompt/fingerprint object, and derived (never self-asserted) review
manifest validation.)

Previously (2026-07-23, Run Intel trust contract PR 1/7 — production session
`a51e977b-561a-4e98-baa8-59ad56a877ff` audit found 31/31 decided but 0 evidence
bundles/specialist outputs with source references, 5/7 required conflict reviews
failed silently, `research_axis_readiness={}` mislabeled successful technical/
sentiment outputs as unusable, and every nonempty decision blocker rendered as
"Evidence blocked" regardless of category.)

Previously (2026-07-22, Haiku specialist output completion fix, PR #484 — production
failure: session 7c4069a1-cc07-4c1e-a7d4-3bea67dd206d froze 31 holdings but completed 14
decided / 17 NO CALL / 22 terminal task failures because Haiku returned verbose/Markdown-
fenced/truncated JSON at 5-ticker batches with an unbounded ~350 tokens/ticker budget, and
the whole batch retried through the durable task's full attempt budget instead of
repairing just the missing tickers. Release-blocker follow-up on the same PR:
`LLMClient.ask_json()`'s own internal same-model truncation retry was still silently
doubling an already-bounded specialist batch call, invisible to the specialist's call
count and the 1800-token ceiling — `ask_json()` gained `retry_truncated_response` (default
True; specialist calls pass False), and the quota/auth-only repair skip was widened to
any actual provider-call failure (rate-limit/transient included) so it's never confused
with a ticker-level JSON parse failure. Fix landed entirely inside the existing specialist
execution seam — no Run Intel architecture, collector, decision-policy, publication, or
frontend change. Same-day: distributed Run Intel model cost routing — standard specialist
analysis moved to Haiku 4.5 with no Sonnet escalation, conditional conflict review stays
on Sonnet 5 with a Haiku fallback; migration 027's owner-guard trigger variable bug
corrected. 2026-07-21: Run Intel distributed workflow — the bounded-drain execution
architecture is replaced by a durable SQL task graph executed by a backend worker
supervisor; the browser only creates one session and polls status. Contract:
`docs/ai/RUN_INTEL_DISTRIBUTED_WORKFLOW.md`; migration
`v2/database/027_intel_run_distributed_tasks.sql`. Deploy Cash work from 2026-07-19
unchanged; consolidation evidence remains in `REFACTOR_REPORT.md`.)

## Product architecture (read this first)

The authenticated app has exactly **three primary views**:

1. **Positions** (`/dashboard/positions`) — certified portfolio truth: totals, allocation split,
   freshness, per-holding detail with Intel v3 action/evidence labels, and
   reconciliation-gated FIFO tax-lot estimates (`GET /api/v1/positions/tax-lots`).
2. **Advisor** (`/dashboard/advisor`) — the **single user-facing recommendation surface**, four
   sections: (A) system readiness + bounded Run Intel, (B) deterministic Intel v3 holding
   actions (`IntelV3Cockpit`), (C) new-cash plan via the canonical Paycheck Advisor endpoint,
   (D) collapsed trust/repair drawer.
3. **Watchlist** (`/dashboard/watchlist`) — user-defined price_below/price_above criteria
   (`/api/v1/watchlist`, table `watchlist_items`, migration `v2/database/025_watchlist.sql`,
   RLS owner policy). The app never auto-selects watchlist stocks and they never enter the
   Paycheck Advisor candidate set.

Operational subpages (not primary nav): `/dashboard/import`, `/settings`,
`/dashboard/position/[ticker]`, login. `/dashboard` redirects to Positions; all legacy product
routes redirect (map in `v2/frontend/src/lib/route-redirects.ts`).

## Run Intel trust-recovery sequence (LOCKED — active scope, reduced finish plan)

Restoring truthful Run Intel trust after the 2026-07-23 production audit of
session `a51e977b-561a-4e98-baa8-59ad56a877ff` (31 frozen holdings, 31
persisted decisions, session `completed` — but 0 evidence bundles/specialist
outputs carried a source reference, 5 of 7 required conflict reviews failed
with no visible trace, distributed publication wrote
`research_axis_readiness={}` so 31/31 successful technical and 31/31
successful sentiment outputs displayed as unusable, and the UI collapsed
every nonempty decision blocker into "Evidence blocked" regardless of
category). This sequence is the active scope for Run Intel work until
production certification passes; do not start unrelated Run Intel slices
ahead of it. The original seven-PR plan is REDUCED to the five items below —
PR 3 deletes the conflict-review LLM instead of repairing it, which retires
the old items 3 (conflict-review reliability) and 7 (performance) as
separate PRs:

1. **Publish and display a truthful Run Intel trust contract — COMPLETED —
   PR #485.** New pure projection
   `run_trust_contract_v1`
   (`v2/backend/app/services/intelligence/v3/distributed/run_trust_contract_v1.py`):
   session coverage; per-axis specialist coverage split into **required vs.
   optional** per asset type (`task_contracts_v1.REQUIRED_AXES_BY_ASSET` /
   `OPTIONAL_AXES_BY_ASSET`), each succeeded/missing/failed/not_applicable —
   a valid persisted specialist output (`score` and `confidence` both
   present) is the only proof an axis succeeded; a terminal task
   (`SUCCEEDED`/`DEGRADED`) without one counts as failed, never succeeded;
   conflict-review coverage that requires BOTH a successfully-terminal review
   task AND a valid persisted `axis="review"` output — `TASK_DEGRADED` is
   never review success merely for being terminal — with explicit
   not_required/succeeded/failed/pending states, `failed` and `pending` both
   blocking trust and `is_source_validated`; source lineage as
   full/partial/missing per ticker, computed over **every** output that fed
   `aggregate_advisory_signal()` (including the review axis when present),
   not one arbitrary axis; and a deterministic decision-constraint classifier
   (evidence_quality / source_lineage / price_context / portfolio_policy /
   risk / conflict_review / other — non-exclusive) that no longer conflates
   UNDERWEIGHT (room to add) with a portfolio-policy limitation, distinguishes
   SUPPRESSED price context (unconfirmed) from assessed FULL/EXPENSIVE
   valuation states, and preserves an `other` category for any real persisted
   blocker text that doesn't match a known category instead of silently
   dropping it. A per-ticker `trust_status`
   (healthy/limited/blocked/unknown) is derived from these same required-axis/
   review/lineage facts — never a separate heuristic — and an overall
   `overall_status` that can only be `healthy` when every required axis,
   every required review, and full decision-influencing lineage pass; any
   required gap forces `blocked`; only optional gaps or partial lineage
   produce `limited`. Wired into BOTH session-native publication
   (`session_publication_v1.py`, persisted on `payload.run_trust_contract`)
   AND a read-time, **fail-closed** enrichment of pre-existing snapshots
   (`intel_v3_service._enrich_snapshot_with_run_trust_contract`, keyed off
   `run_session_id`, zero provider/LLM calls): when the session, ticker rows,
   or task rows can't be read (missing, empty, or a raised exception), the
   enrichment applies an explicit `unknown`/`pending` trust overlay
   (`_apply_unknown_trust_overlay`) instead of ever preserving a stale
   optimistic `source_validated`/committee status — old cards flip to
   explicitly `unknown`, they never stay silently "healthy" on a failed read.
   `research_axis_readiness={}` placeholder replaced with real per-axis
   readiness; `snapshot_builder._build_source_pack_status` now requires full
   source lineage AND a non-failed, non-pending review status before
   "source_validated" when lineage/review info is available (distributed
   sessions), preserving legacy evidence-band-only behavior when it isn't
   (non-distributed callers). Frontend: `AdvisorReadinessPanel` shows an
   independent "Analysis trust" status (healthy/limited/blocked/
   not_applicable/unknown) plus session/axis/conflict-review/source-lineage
   summary lines, separate from the renamed "Holdings decided" coverage
   metric; `IntelV3Drawer` gained a "What's limiting this holding" section
   listing each decision-constraint category separately, using
   `decision_bands`-aware wording so SUPPRESSED/FULL/EXPENSIVE price context
   read as distinct, accurate states rather than one generic "isn't
   confirmed" claim; `IntelV3HoldingsPanel`'s evidence band keeps its
   existing technical/sentiment `axis_coverage` chips unchanged, but the
   portfolio "better supported / evidence limited / data issues" counts now
   derive directly from backend per-ticker `trust_status`
   (`buildPortfolioEvidenceSummary`) instead of a second frontend safety
   heuristic, so the summary, holding cards, and drawer agree on one backend
   trust state; `buildSafetyDisplay` no longer treats every nonempty blocker
   as "Evidence blocked" — only an actual `evidence_quality` constraint is.
   Financial truth rows (`portfolio_financial_truth`/`current_price_truth`/
   `books_reconciliation`) are untouched — still sourced only from the
   existing `/api/advisor/readiness` truth endpoint. No SQL. **PR 2 still
   owns reference generation** — this PR does not generate any
   `evidence_refs`/`source_refs`, it only reports lineage truthfully against
   whatever already exists (currently "0 of N" in production). **Runtime
   caveat:** fixture and unit/integration test validation is complete, but
   production verification of the historical-session, fail-closed enrichment
   path (a real read against the existing production session with a stale/
   missing task graph) is still required after deployment — it has not yet
   been exercised against live production data.
2. **Source quality / source-reference generation — IN PROGRESS — PR #486.**
   New pure module `source_lineage_v1.py`
   (`v2/backend/app/services/intelligence/v3/distributed/source_lineage_v1.py`):
   versioned, structured source references (`provider_observation` for direct
   yfinance/CoinGecko lane task outputs; `research_artifact_source` for
   canonical `research_artifact_sources` rows on SEC/ETF artifact-backed
   lanes — an artifact id alone never counts), deterministic dedup, bounded
   prompt-safe compact projection, per-axis lineage manifest construction
   (full/partial/missing), and review-derived lineage. Legacy opaque strings
   and malformed objects are structurally rejected, never truthy.
   `evidence_bundle_v1.py` now derives `source_refs_by_lane`/`source_refs`/
   `source_ref_gaps`/`quality.source_linked_lane_count`/`source_ref_count`
   from the actual terminal lane task outputs (direct lanes) and a single
   bulk `research_artifact_sources` query per ticker bundle (artifact lanes),
   fails closed to a lineage gap (never a bundle crash or lost evidence) on a
   source-row read failure, and strips `task_id`/`observed_at` from the
   fingerprint (alongside the existing `as_of`/`cache_hit`/`generated_at`/
   `fetched_at`) so a fresh session/task id never defeats cross-session
   specialist reuse. `specialist_agents_v1.py`: each axis's persisted
   `evidence_refs` is now an axis-scoped manifest derived from
   `AXIS_CANDIDATE_LANES` ∩ the bundle's own usable lanes (no more copying
   the whole-bundle ref list onto every axis); the compact prompt bundle
   gained a bounded `evidence_sources` projection (identity fields only, no
   citation invention); `PROMPT_VERSION` bumped to
   `distributed_specialist_v2`; cross-session reuse
   (`find_reusable_specialist_output`) now also requires an exact
   `prompt_version` match (a legacy unsourced output is never reused) and
   rebuilds `evidence_refs` from the CURRENT session's bundle lineage rather
   than copying the reused row's (possibly stale) references; a successful
   conflict review's `evidence_refs` is now a derived manifest that unions +
   dedupes the valid references of every non-review input it reconciled
   (full only when every reconciled input was itself full) with a
   deterministic `input_fingerprint` (previously always `""`).
   `run_trust_contract_v1._has_min_source_lineage`/`_lineage_status_for_outputs`
   now validate the structural manifest via `source_lineage_v1` instead of a
   raw truthy/nonempty-list count — malformed objects and legacy opaque
   strings read as missing lineage, never as validated. Session-native
   publication's `session_evidence_refs` is unchanged code (still
   `bundle.source_refs`) but now carries genuine structured references
   instead of an always-empty list. No SQL, no new provider, no new env var,
   zero additional provider/LLM calls, `decision_policy_v1.decide()`
   untouched. **Runtime caveat:** fixture/unit/integration test validation is
   complete; production verification that a fresh Run Intel run now
   genuinely produces nonzero source-linked lineage is deferred to the final
   fresh Run Intel certification run (the final certification run, item 5
   in the reduced finish plan) so Anthropic funds aren't spent
   on a dedicated verification run after every recovery PR.

   **Same-PR patch (six release-blocker semantic defects, still PR #486):**
   (1) `parse_axis_manifest` no longer trusts a persisted `status` field — it
   independently re-derives status from the manifest's own lane/reference
   structure (unique/disjoint/union-consistent lanes, every reference
   structurally valid + lane-consistent + ticker-matched when asked, every
   linked lane backed by a reference); a self-reported status that disagrees
   with the derived one makes the WHOLE manifest malformed (`missing`
   everywhere) — proven with explicit "claims full but isn't" tests (empty
   refs, unrelated-lane ref, wrong-ticker ref, a missing expected lane, a
   review claiming full with nonempty `missing_ref_axes`). (2) A new shared
   `specialist_agents_v1.axis_evidence_context()` helper is the ONE source of
   truth for "evidence actually supplied to this axis" (used identically by
   the prompt, persisted `evidence_refs`, reuse rebinding, and the source
   projection) — derived from the axis's own compact-bundle content, never
   the bundle-wide `usable_lanes` list; artifact-backed lanes only count as
   supplied once `evidence_bundle_v1` has verified the parent
   `research_artifacts` row's ownership (`user_id`), ticker scope, active
   status and a nonempty payload — a wrong-user/wrong-ticker/empty-payload
   artifact never leaks into the bundle, prompt or persisted reference. (3)
   The bundle `input_fingerprint` no longer hashes raw reference objects
   (which carried `task_id`/`artifact_id`/`artifact_source_id` and, for the
   PRICE lane specifically, a digest of the volatile intraday price) —
   `source_lineage_v1.fingerprint_source_refs`/`source_identity_projection`
   build a canonical, session/task-independent per-lane identity projection
   instead, so an ordinary price tick alone no longer invalidates every
   specialist's cross-session reuse while a genuine technical/fundamental/
   news/crypto/artifact evidence or provider/source-identity change still
   does. (4) Artifact parent + source-row reads are both scoped to the
   ticker bundle's `user_id` and bulked to one query each (the same
   validated parent rows are reused for the SEC/ETF/macro display summaries,
   closing a prior N+1); every reference list is bounded (8/lane, 24/
   axis-or-review-manifest, deterministic sort before truncation, an
   additive `truncated_ref_count`, free-text fields capped at 200 chars, no
   source URLs/excerpts/full reference objects logged). (5) Conflict review
   now reconciles ONLY valid (score+confidence present) specialist rows; the
   bounded prompt input carries lineage status/linked/missing lanes per
   axis; the persisted `input_fingerprint` is built from the EXACT bounded
   prompt input (order-independent) plus ticker+prompt_version — changes
   with any reviewed finding/risk/score/confidence/lineage-status/missing-
   lane/source-identity change; review model/token-budget/retry/call-count
   were untouched at the time (PR 3 later deleted the review LLM entirely
   rather than repairing this — see PR 3 above). (6) `run_trust_contract_v1` tracks
   full/partial/missing OUTPUT counts separately
   (`outputs_full_lineage`/`outputs_partial_lineage`/`outputs_missing_lineage`,
   additive alongside the preserved `outputs_with_source_refs`/
   `outputs_missing_source_refs`) — `source_health` is `healthy` ONLY when
   every valid output is full, `limited` for any partial output or a
   full+missing mix, `blocked` when none are sourced; an all-partial run can
   no longer misread as healthy (the exact release-blocker this patch
   fixes). Same invariants preserved throughout: zero new provider/LLM
   calls, no SQL, no env vars, no frontend files, `decision_policy_v1`/
   visible actions unchanged. Full backend suite: **8619 passed, 0 failed**
   (up from 8580 pre-patch; +39 net new focused tests across
   `test_source_lineage_v1.py` and `test_run_trust_contract_v1.py` covering
   every explicit scenario above). Production source-lineage behavior
   remains NOT runtime-proven — same final-certification-run deferral as above.

   **Round-3 same-PR patch (six further normal-path lineage-trust gaps,
   still PR #486):** the round-2 patch fixed source-health/ownership/
   bounding/status-trust, but six normal-path inconsistencies could still
   create false "full" lineage or unnecessary specialist LLM reruns. (1)
   **Axis-specific manifest structure**: `parse_axis_manifest` now checks
   `expected_lanes`/`linked_lanes`/`missing_ref_lanes` (and every reference's
   own lane) against `AXIS_CANDIDATE_LANES[axis]`, never the wider
   `SUPPORTED_LANES` — a self-consistent "technical" manifest sourced only
   by fundamentals lanes (or a "sentiment" manifest carrying
   technicals/fundamentals) now fails closed instead of validating.
   `_is_unique_list_of` and a new `_validate_truncated_ref_count` fail
   closed on any malformed/non-hashable/out-of-range input (never raise);
   raw persisted refs beyond `MAX_REFS_PER_MANIFEST` are rejected.
   (2) **Corrected producer-to-screen contract**: `_axis_supplied_lanes`
   now also recognizes substantive SEC-catalyst evidence carried in
   AXIS_SENTIMENT's own compact `catalysts` LIST (previously only detected
   via the `sec` dict, which AXIS_SENTIMENT never populates) — a real
   catalyst artifact now correctly counts as `LANE_SEC_CATALYST` supplied,
   with the existing full/partial-once-referenced derivation applying
   unchanged. `_payload_only` now strips `artifact_id`/`artifact_type`/
   `skill_pack` wherever an artifact-summary shape appears — directly,
   nested in a dict, or inside a list — so the catalysts list (previously
   missed) no longer leaks internal storage identifiers into the specialist
   prompt. (3) **Effective evidence vs citation-gap distinction**:
   `evidence_bundle_v1` now derives two explicit concepts — the raw
   TERMINAL lane outcome (task succeeded) vs EFFECTIVE evidence lanes
   (`bundle.usable_lanes`, the scheduler/specialist authority). An
   artifact-backed lane is effective only when its parent belongs to the
   frozen user, matches ticker/scope, is active and not `invalidated_at`,
   matches the artifact_type/skill_pack/scope_kind contract derived from
   the EXISTING adapter constants (SEC CompanyFacts/catalyst, ETF NPORT,
   FRED macro — never guessed), and carries real lane-specific evidence
   (`observation_count`/`catalyst_count`/`holdings_count` > 0 — governance-
   only assessment fields never count). A missing/invalid/non-substantive
   parent is excluded from `usable_lanes` AND `source_ref_gaps` and appears
   in `degraded_lanes` with a bounded `artifact_invalid:{lane}:{reason}`
   entry in `degradation_reasons` — never a citation gap, since there is no
   usable evidence to have a citation gap over. A valid substantive artifact
   with unreadable source rows still stays in `usable_lanes` and visible to
   the specialist, and honestly appears in `source_ref_gaps`. (4) **No
   internal artifact IDs in fingerprints**: `artifact_id` added to
   `_VOLATILE_FINGERPRINT_KEYS` — replacing an internal artifact/task
   storage row with the same external evidence and same external source no
   longer changes `input_fingerprint` (full-bundle integration tests, not
   only pure reference-projection tests, prove this). (5) **One normalized
   review-prompt/fingerprint contract**: new
   `source_lineage_v1.build_review_prompt_context()` is the SINGLE pure
   helper producing the exact bounded, normalized object used for BOTH the
   review LLM prompt (`json.dumps`) and `review_input_fingerprint` —
   `execute_review_task` no longer hand-builds a separate `prompt_outputs`
   shape. Compact source projection gained a bounded `identity_token`
   (from `source_id`/`source_hash`/sanitized URL for artifacts, from
   `output_digest` for non-price provider observations, none for price so
   intraday ticks don't defeat reuse) so a genuine external-source change is
   distinguishable from an internal replay-locator change even in the
   compact projection. (6) **Derived, not self-asserted, review manifest
   validation**: review manifests now carry `input_axis_lineage` (one entry
   per reconciled axis, status from strict per-axis validation) as the ONE
   source of truth — `derived_from_axes`/`missing_ref_axes`/`status` are all
   re-derived from it, never independently hand-authored, and a persisted
   disagreement makes the whole manifest malformed. New
   `validate_review_against_current_outputs()` cross-validates a persisted
   review's claims against the CURRENT valid non-review outputs for that
   ticker (via `run_trust_contract_v1._output_lineage_status`) — a review
   claiming a different axis set or different per-axis status than what is
   presently true for the ticker (stale or forged) reads as missing
   lineage, never full. Same invariants preserved: zero new provider/LLM
   calls (verified via `test_distributed_golden_run.py`'s exact call-count
   accounting, unchanged), no SQL, no env vars, no frontend files,
   `decision_policy_v1`/visible actions/allocation/Deploy Cash unchanged,
   review-LLM deletion (PR 3) and the final operational-reliability PR (item
   4) scope untouched. Full backend suite:
   **8670 passed, 0 failed** (up from 8619 pre-round-3; +51 net new focused
   tests, mostly in `test_source_lineage_v1.py` — axis-candidate-lane
   enforcement, malformed-input fail-closed proofs, truncated-ref-count
   validation, forged-review-manifest rejection, sentiment-catalyst
   supplied-lane proofs, a parameterized per-axis supplied-lanes contract
   test, artifact-fingerprint-stability full-bundle integration tests, and
   `build_review_prompt_context`/fingerprint single-source-of-truth proofs
   — plus a forged-review-claim proof and updated review-manifest fixtures
   in `test_run_trust_contract_v1.py`). Files touched:
   `source_lineage_v1.py`, `evidence_bundle_v1.py`, `specialist_agents_v1.py`,
   `run_trust_contract_v1.py`, `test_source_lineage_v1.py`,
   `test_run_trust_contract_v1.py`. Production source-lineage behavior
   remains NOT runtime-proven — unchanged, still deferred to the single
   final certification run (item 5 in the reduced finish plan).
3. Deterministic conflict handling / review-LLM deletion — **COMPLETED —
   PR #487, merged.** Deletes the conditional Sonnet/Haiku review LLM rather than
   repairing it: `conflict_policy_v1.py` (≤180 lines, currently exactly 180)
   is the ONE strict deterministic authority — trigger thresholds, conflict
   assessment, the HOLD/≤0.49-confidence guardrail, disagreement-vs-
   low-confidence copy, the shared fingerprint, and the current-row
   activation contract. `execute_conflict_resolution_task` replaces
   `execute_review_task`; `WorkerSupervisor.review_llm` and the review
   model/fallback settings are deleted (verified zero remaining references
   repo-wide). Zero `llm.ask_json`/provider calls for conflict handling.
   **Round-2 fixes (producer-to-screen audit, 5 defects):**
   - **Strict input authority**: `conflict_policy_v1.normalize_valid_inputs`
     is the single source every consumer (assessment, aggregation,
     execution, fingerprinting, decision-time validation) calls — a
     duplicate axis excludes EVERY occurrence, order-independent, malformed
     rows/weights never raise. `aggregate_advisory_signal` enforces this
     itself and always excludes `AXIS_REVIEW`, even if a caller passes the
     full output list.
   - **Activation contract**: a deterministic row alters `advisory_signal`
     ONLY when its `TASK_REVIEW_CONFLICT` task is `TASK_SUCCEEDED`, exactly
     one `axis=review` row exists, model/prompt_version/stance/score/
     confidence match exactly, lineage validates against CURRENT strict
     inputs, conflict is still detected on recompute, and its
     `input_fingerprint` matches `conflict_policy_v1.conflict_fingerprint`
     recomputed fresh — the executor and the decision/trust readers call
     the identical function. `run_trust_contract_v1._has_valid_review_output`
     applies this ONLY to `deterministic_conflict_policy_v1`-tagged rows;
     genuine historical LLM-review rows keep their original validity gate,
     never reinterpreted.
   - **Historical replay truth**: the already-decided retry branch now calls
     `_replay_persisted_decision`, never `resolve_conflict_advisory` —
     it rebuilds the verdict/aggregate from the PERSISTED audit record
     (`advisory_signal`, `decision_input`), never recomputes under current
     conflict rules. A historical LLM-reviewed decision replays its exact
     action even if current specialist inputs would newly conflict (proven
     by a fixture test with `decide`/`resolve_conflict_advisory` monkeypatched
     to raise if called). The persisted decision-record shape is additive
     (new audit fields) — not byte-for-byte identical to pre-PR3 records.
   - **Truthful, method-neutral UI copy**: per-holding status text
     ("Specialist signal handling completed/could not complete safely/is
     still pending for this holding"; not_required: "No specialist conflict
     or low-confidence case was detected") and the session line ("N
     specialist conflict or low-confidence cases — M completed, K failed")
     are shared vocabulary across historical LLM-reviewed and new
     deterministic holdings — never a global "handled deterministically"
     claim. Disagreement vs. low-confidence wording is truthful and
     axis-display-mapped (no raw schema identifiers). Fixture screenshot
     regenerated against the corrected copy.
   - **Acceptance matrix**: new focused tests for strict-input edge cases,
     the full activation-contract matrix (pending/failed/orphan/wrong-model/
     wrong-prompt-version/wrong-stance/wrong-score/wrong-confidence/stale-
     fingerprint/aligned-current-inputs/invalid-lineage), exact actions
     (material conflict → HOLD/LOW; +OVERWEIGHT → TRIM; +CRITICAL risk+BREACH
     → SELL; low-confidence-only-major-holding → HOLD/LOW with no
     "disagreed" wording), and the historical-replay fixture.
   `decision_policy_v1.decide()` is untouched — portfolio-fit/risk priority
   remains the sole visible-action authority over a neutralized signal.
   No SQL, no new env vars, no provider changes.
   **Test totals:** full backend **8679 passed, 0 failed** (the one
   previously-flaky `test_ttl_reused_lane_keeps_honest_identity_zero_
   provider_calls` was diagnosed as a genuine bug — a hardcoded absolute
   calendar timestamp racing the real-wall-clock 24h TTL cutoff — and fixed
   to use a wall-clock-relative timestamp without weakening its
   zero-provider-call assertion); full frontend **664 passed, 0 failed**;
   TypeScript clean; frontend production build clean.
   **LOC:** `conflict_policy_v1.py` stays at exactly 180 lines. Final net
   production-source delta vs `main` is **+190 lines** (measured across
   every changed production file, tests/docs excluded) — over the original
   ≤+100 target, after trimming docstrings and deleting the now-orphaned
   `source_lineage_v1.review_input_fingerprint` (dead since Phase 1 replaced
   its only caller; −36 lines, plus its dedicated dead test class removed).
   The remaining overshoot is the strict shared activation-contract
   validation (reused identically by the executor, the decision reader, and
   the trust contract) plus the historical-replay-preservation path, both
   required to close the 5 round-2 defects without cutting correctness or
   test coverage; docstrings were compressed wherever reducible without
   losing the invariant they document. **Runtime caveat:** fixture/unit/
   integration validation is complete; production behavior is NOT
   runtime-proven — deferred to the single final certification run (item 5
   above), same deferral pattern as PR #485/#486.
4. One final operational-reliability PR — **OPEN — PR #488, not yet merged,
   patched a second round.** Makes Run Intel dependable whether clicked
   while active, immediately after completion, or an hour/day/week later.
   - **1. Frozen financial-truth preflight** (`financial_truth_baseline_v1`
     split into a shared `_gather_truth_sections` core plus a public
     backward-compatible diagnostic and a new strict
     `run_financial_truth_baseline_strict` that raises `FinancialTruthReadError`
     on a core DB read failure instead of silently reporting an empty
     portfolio). `session_control_v1._run_truth_preflight` calls the strict
     API, and its ONE passing result supplies BOTH the pass/block verdict AND
     the exact `open_positions`/`price_rows` that `create_distributed_session`
     freezes into scope — no second positions/price_history query, no TOCTOU
     gap. Duplicate active tickers now BLOCK (`portfolio_reconciliation_failed`)
     instead of silently deduping to the first row.
   - **2. Corrected currency domain model**
     (`evidence_normalization_v1.normalize_fundamentals`): statement fields
     (`revenue`/`free_cash_flow`/`operating_cash_flow`/`net_income`/
     `total_debt`/`cash`/`ebitda`) use the reporting/financial-statement
     currency; quote/security fields (`market_cap`/`target_mean_price`/`eps`)
     use the quote currency — a TSM-shaped reporter (USD market cap/target/
     EPS, TWD revenue/cash/FCF) is labeled correctly on both sides
     simultaneously, never conflated. `eps` carries `unit: "per_share"` (a
     currency amount, not a dimensionless ratio). Every accepted number
     passes `math.isfinite` (NaN/±infinity rejected); non-ISO/mixed-case
     currency units (`GBp`/`GBX`/lowercase) are rejected, never silently
     coerced to `GBP`.
   - **3. Current yfinance news contract**
     (`evidence_normalization_v1._normalize_news_item`, single authority —
     `data_sources.fetch_yfinance_news_sync` now returns raw provider items
     verbatim): parses BOTH the legacy top-level shape and the current
     nested `{id, content: {title, summary, pubDate, provider, canonicalUrl}}`
     shape. Relevance precedence is now strict: provider `related_tickers`
     metadata, when present and nonempty, is authoritative — a mismatch
     REJECTS even when the headline text matches; exact-token text matching
     is a fallback only when metadata is absent, and is disabled for
     ambiguous 1-2 character tickers.
   - **4. Fail-closed, asset-scoped collector cache** (`collectors_v1`): a new
     `CacheReadError` distinguishes a legitimate cache miss (returns `None`,
     safe to fetch) from a cache READ failure (raises, forcing
     `TASK_FAILED_RETRYABLE` with zero provider calls — an outage is never
     reinterpreted as expired evidence). Cache identity now includes
     `asset_type` (never cross-asset-type reuse). A new
     `find_recent_macro_output` implements the documented 24h macro reuse
     contract on the EXISTING task table (user+task_type scoped, no
     fabricated ticker, no second table) — portfolio-context collection
     remains a plain current-session DB read, never a lane cache hit.
   - **5. One prompt/fingerprint helper** (`specialist_agents_v1`):
     `axis_evidence_context` is now the ONLY source of the compact prompt
     bundle, supplied lanes, lineage manifest, AND `input_fingerprint` — the
     separately-maintained `axis_input_fingerprint` function is gone.
     `market`/`prior_action` are excluded from BOTH the prompt and the
     fingerprint together (previously `market` reached the prompt while
     being excluded only from the fingerprint) — no axis's compact bundle
     ever includes `market` anymore, so `AXIS_CANDIDATE_LANES`
     (`source_lineage_v1`) no longer lists `LANE_PRICE` for any axis. The
     fingerprint now includes the exact `evidence_sources` projection the
     LLM sees, so a genuine source-identity change invalidates reuse.
   - **6. Literal cost/reuse metrics** (`worker_supervisor_v1`): `cache_hits`/
     `lanes_refreshed` count ONLY a successful evidence-lane adoption/
     collection — the session-level portfolio-context DB read and any
     degraded/no-data/failed-retryable collector attempt count in neither
     total anymore (previously every non-cache-hit completion, including
     the portfolio-context read, silently inflated `lanes_refreshed`).
     `session_control_v1._evidence_summary_line` now renders a counter pair
     once EITHER sibling exists (an immediate rerun that reuses every lane
     never sets `lanes_refreshed` at all — the absent key is a genuine zero,
     not a reason to suppress the whole line).
   - **7. Blocked-preflight UI** (`AdvisorReadinessPanel`/`advisor-readiness.ts`):
     `IntelV3SessionStatus`/`AdvisorRunModel` carry the backend's existing
     `status`/`code`/`message`/`repair_action`/`provider_calls`/`llm_calls`
     fields; the run-status region renders the bounded repair action once
     when present — no new card/drawer/button, no raw internal code ever
     rendered. `portfolio_scope_empty` keeps its pre-existing "Add positions"
     behavior unchanged (no repair action shown there).
   - **8. Hour/day/week controlled-clock matrix**
     (`test_run_intel_ttl_matrix_v1.py`, new): real
     scheduler/collector/specialist/supervisor dispatch, two sessions per
     interval, durable-row timestamps shifted backward (never the TTL
     constants) to simulate immediate/+1h/+1d/+1w. Covers equities, an ETF,
     and crypto, including the SEC/ETF long-TTL artifact-lane paths, via
     deterministic provider-boundary fixtures — no paid/unavailable provider
     ever enabled.
   - `decision_policy_v1.decide()`, allocation policy, and Deploy Cash are
     unchanged. No SQL, no new env var, no new provider, no FX service, no
     new agent, no new table.
   - **Test totals:** full backend **8767 passed, 0 failed**. Full frontend
     **677 passed, 0 failed**. TypeScript clean. Frontend production build
     compiles/type-checks cleanly (static prerender needs Supabase env vars
     this sandbox doesn't have — unrelated to this diff, already deploying
     green on Vercel per the PR's own preview checks).
   - **LOC:** net production-source delta vs `main` is now **+580 lines**
     (up from the originally reported +458 — measured across every changed
     production file, tests/docs excluded). The specific redundant paths
     item 10 asked to delete were all removed as part of the fixes
     themselves (the independent post-preflight scope reload, the
     separately-maintained `axis_input_fingerprint`, the old currency
     classification, the permissive cache-miss fallback) — none survive as
     parallel/dead code. The remaining growth is genuine new logic for 8
     independently-real correctness gaps (strict-failure preflight
     semantics, a second currency domain, nested news parsing, a fail-closed
     cache exception path, a unified fingerprint helper, literal metric
     gating, UI wiring, and controlled-clock test infrastructure) — per
     item 10's own "do not code-golf correctness" instruction, no comment or
     logic was stripped merely to chase the number.
   - **Runtime caveat:** fixture/unit/integration validation is complete
     (including real end-to-end supervisor-driven proofs across the
     immediate/1h/1d/1w matrix); production behavior is NOT runtime-proven —
     deferred to the single final certification run (item 5), same
     deferral pattern as every prior entry in this sequence. No live Run
     Intel session was run in this PR or this patch.
5. One final paid production certification run — NOT STARTED. The only
   place this sequence claims live production success.

## The decision spine (one spine, no competitors)

1. Certified portfolio/transaction truth (`portfolio_service`, `import_service`, positions)
2. Current-price truth (`price_engine`; repair: `current_price_truth_repair_v1`)
3. Intel v3 evidence production (`services/intelligence/*`; agents/LLMs are **labeled evidence
   producers only**, used by the protected refresh adapters and the cert harness)
4. **Intel v3 deterministic policy** (`intelligence/v3/decision_policy_v1.decide()`) — the only
   owner of visible Buy/Hold/Trim/Sell actions
5. Allocation policy + guardrails (`allocation_policy_v1`: ETF floor 40%, stock sleeve target,
   concentration/group caps, min-trade, VTI>VOO>SPY>QQQ, cash invariants
   `allocated_cash <= cash_to_deploy`, `unallocated_cash >= 0`)
6. **Paycheck Advisor** (`POST /api/v1/advisor/paycheck-plan/preview`, cert-gated via the
   frontend Route Handler that attaches server-only `FINANCE_RUNTIME_CERT_SECRET`) — the
   canonical answer to "I have $X — what should I buy, how much, and why"
7. One Advisor view rendering both

**Intel v3 and Paycheck Advisor are complementary layers, never competitors**: Intel v3 owns
deterministic actions for existing holdings; Paycheck Advisor consumes certified truth +
Intel v3 evidence + allocation policy to place new cash. The preview response carries additive
`explanations` buckets (selected / evidence_eligible_policy_blocked / evidence_blocked /
concentration / group-cap / stale-price / missing-truth / below-min-trade / max-positions)
mapped from the Stage 12C/13A/13C diagnostic — presentation only, no new allocation math.

## Decisions that must NOT be re-litigated (historical context)

- **Do not rebuild a visible LLM/agent recommendation surface.** The legacy
  `recommendation_engine` insight-card path (`GET /recommendations/`, `AgentInsightCard`,
  `PortfolioSynthesisPanel`, flag `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED`) was removed
  in the consolidation because LLM output must never own visible Buy/Hold/Trim/Sell authority
  (`KNOWN_FAILURE_MODES.md`). `recommendation_engine.py` + `services/agents/*` + `services/ai/`
  survive ONLY as internal evidence producers for the Intel v3 refresh adapters and the
  cert harness (`diagnostics.py`) — never re-expose them as a user surface.
- **Do not rebuild a duplicate Deploy surface.** Deploy v3 (`routers/deploy_v3.py`,
  `services/deploy/*`) and the legacy allocation engine chain (`allocation_engine`,
  `deployment_engine`, `adaptive_deployment`, `regime_engine`, deposits schedule with hardcoded
  weights, `decision_engine`, `personalized_decision_engine`, `strategy_engine`,
  `simulation_engine`, decision logs/journal, AI rebalance) were deliberately retired
  (2026-07-18). New-cash sizing belongs to Paycheck Advisor; holding actions to Intel v3.
- **Do not build a separate/new Paycheck model or endpoint family.** The paycheck evolution
  lineage (Stage 12B policy → 12C ETF preference → 12D preview read model → 12E UI → 13A
  evidence-aware stock gating → 13C production `current_holdings` snapshot contract + independent
  policy/evidence gates, PR #471) all lives in `allocation_policy_v1.py` behind the one endpoint.
  Extend it additively there.
- **Do not delete the Advisor allocation spine** (`allocation_policy_v1`,
  `paycheck_plan_preview`) — rejected PR #472 did exactly that and was rejected for it.
- **Deploy Cash refreshes price truth on the explicit click, a stale or missing price is never
  eligible for new cash, and a degraded-but-calculable plan is preserved (not erased)** (product
  recovery, patched same-day to close a release blocker): `POST /advisor/paycheck-plan/preview`
  now runs `current_price_truth_repair_v1.run_current_price_truth_repair(dry_run=False)` for
  stale/missing open-position prices BEFORE `run_next_buy_policy_diagnostic` — permitted only
  because Deploy Cash is an explicit user action (never on page load/polling). The repair's own
  fetch phase runs with bounded concurrency (`_MAX_CONCURRENT_FETCHES=5`, `asyncio.gather` under
  a semaphore) instead of serial per-ticker waits; writes stay sequential and
  price_history-only. The repair is best-effort — an unexpected exception never blocks the plan;
  the response carries an additive `price_truth_repair` summary
  (`refreshed`/`partial`/`unavailable` + counts). **Canonical fix (not a presentation filter):**
  `allocation_policy_v1._compute_gaps` now excludes a ticker from candidacy on either a
  **missing** price (`no_price_available`, unchanged) OR a **stale** price
  (`stale_price_not_eligible_for_new_cash`, new) — a stale ticker can never itself receive new
  cash, and when it's excluded the existing allocator automatically reranks/reallocates to
  another fresh eligible candidate (no new allocator, no duplicated math). Cash bounds,
  concentration rules, evidence gates, and the VTI>VOO>SPY>QQQ preference are unchanged.
  `paycheck_plan_preview.build_paycheck_plan_preview()` (defense-in-depth) independently verifies
  that invariant before preserving a degraded plan — no selected candidate may appear in
  `missing_price_tickers`/`stale_price_tickers` — and suppresses the entire plan (forces
  `status=blocked`, zeroes `allocation_summary`) rather than ever displaying unsafe dollar
  guidance if it is ever violated (e.g. by a future allocator regression or a hand-built
  diagnostic payload); this never re-filters or recomputes the diagnostic's own selection/cash-plan
  math. Only `blocked` (no computable portfolio value, reconciliation beyond tolerance, no safe
  candidate prices, or the defense-in-depth invariant firing) empties the plan — `degraded`
  (non-fatal residual price/provider limitations on some OTHER, non-candidate holding) preserves
  the diagnostic's own calculated candidates verbatim, keeping `trusted=false`/`status=degraded`
  and the existing caveats. No new allocator, no policy/cap loosening, no fabricated candidates;
  the decision spine (certified truth → repaired price truth → Intel v3 evidence →
  `allocation_policy_v1` → `paycheck_plan_preview` → Advisor cash-plan section) is unchanged.
- **Run Intel is a durable distributed task graph** (contract:
  `docs/ai/RUN_INTEL_DISTRIBUTED_WORKFLOW.md`; migration
  `v2/database/027_intel_run_distributed_tasks.sql`; replaces the bounded-drain
  session flow, the browser continuation loop, and any Run Intel path through
  `run_analyst_refresh_only()`):
  * Control plane: `POST /intel/v3/run` (browser-minted UUID per click) freezes the
    portfolio scope into `intel_run_tickers` (one row per ACTIVE holding — batch size can
    never redefine run scope), seeds `intel_run_tasks` (generic durable queue: leases,
    SKIP LOCKED claim RPC `claim_intel_run_tasks` + CAS fallback, idempotent logical task
    identity), activates the in-process worker supervisor, returns fast. Zero provider/LLM/
    policy/snapshot work in-request. One active session per user (partial unique index);
    a click during an active run adopts it.
  * Status plane: `GET /intel/v3/sessions/{id}/status` + `/sessions/active` — read-only,
    plain-English `plain_status`; polling observes work, never performs it. Page close
    never stops the run; returning rediscovers the active session.
  * Worker: `distributed/worker_supervisor_v1` (in-process asyncio; started by /run
    AND a startup probe that survives transient boot-time DB failures and exits only
    after a successful zero-session query; DB outages are never treated as idle —
    bounded backoff + jitter, zero provider/LLM work). Scheduler (`run_scheduler_v1`)
    creates dependency waves: ticker-scoped lane collectors → immutable evidence
    bundles → asset-compatible specialist batches (≤5, global LLM semaphore) →
    deterministic conflict-triggered review → per-ticker deterministic decision →
    session-native portfolio join + publication. Failure isolation: lane → lane,
    specialist → batch/axis, ticker → ticker (`no_call` EVIDENCE INCOMPLETE, never
    fabricated verdict rows); session terminal states `completed` /
    `completed_with_gaps` / `failed`.
  * ONE decision authority: `decide()` runs exactly once per ticker inside the
    decision task; the complete input+output persist on `intel_run_tickers.decision`;
    compat rows (`agent_runs`/`agent_insights`/`recommendations`) are written AFTER
    with the final action (projections, not advisory inputs). Publication
    (`session_publication_v1`) rebuilds cards verbatim from persisted decisions —
    zero `decide()` calls, zero global-recommendation reads (test-fenced), full
    frozen-scope accounting (decided / NO CALL / failed with plain-English gaps),
    distributed certification (card action == persisted action, no foreign/stale
    cards), snapshot_source `worker_certified` vs `worker_certified_with_gaps`
    (non-green amber in UI). Task graph is fail-closed (get_or_create with verified
    duplicates only, expected-graph verification before created→running,
    supervisor-driven repair of every partial-create shape) and claim-token fenced
    (fresh token per claim; completion + every side-effect write require current
    ownership; stale reclaimed workers cannot mutate outputs).
  * Collectors reuse existing providers (data_sources fetchers + breakers/semaphores,
    SEC/ETF/macro research-worker runners writing `research_artifacts`); lane TTL reuse +
    specialist `input_fingerprint` reuse skip duplicate provider/LLM work (reuse ignores
    the producing model — a Sonnet-era output stays reusable under Haiku routing).
    Specialists are pure (bundle-only input, `LLMClient.ask_json`, strict compact JSON —
    no markdown/fences/commentary, ≤2 key_findings/risks/missing_evidence/limitations,
    ~120 chars/string, no visible action word), per-(ticker,axis) rows in
    `intel_run_specialist_outputs`. Normal specialist batches are capped at
    `INTEL_V3_DISTRIBUTED_HAIKU_MAX_SPECIALIST_BATCH` (default 2, independent of the
    unrelated architectural `INTEL_V3_DISTRIBUTED_MAX_SPECIALIST_BATCH=5` ceiling for
    other models) with a bounded 650-tokens/ticker output budget (min 700, max 1800/call).
    A batch call's missing/malformed tickers are repaired ONE TICKER PER CALL — never a
    validated peer — bounding a 2-ticker batch to ≤3 total LLM calls (1 initial + ≤2
    individual repairs) instead of retrying the whole durable task 3×. A quota/
    authentication provider error makes exactly one call, skips repair, and returns
    `TASK_FAILED_RETRYABLE` without ever discarding an already-persisted peer ticker.
    Deterministic `decision_policy_v1.decide()` remains the only visible action authority;
    specialist scores aggregate into an advisory signal only.
  * Retired: `intel_run_session_flow_v1`, `analyst_refresh_on_demand_drain_v1`, the
    browser auto-continuation in `useRunIntelV3`, session-scoped
    `analyst_refresh_jobs` enqueue. Unfinished legacy (workflow_version=1) sessions were
    marked `superseded` by migration 027. The legacy analyst-refresh worker Railway
    service remains only for the flag-off background path and can never see distributed
    sessions or claim session-linked jobs.
  * Tests: `test_distributed_architecture_boundary.py` (static import/symbol fences),
    `test_distributed_run_creation.py` (34-ticker fast create, forbidden seams),
    `test_distributed_sql_contract.py` (migration 027 + retention),
    `test_distributed_collectors_and_store.py` (ticker scoping, lane isolation, TTL,
    atomic claims/leases/double-completion), `test_distributed_specialists_and_review.py`,
    `test_distributed_decision_and_publication.py` (deterministic authority, NO CALL,
    publication-only retry), `test_distributed_golden_run.py` (34-holding golden run with
    exact provider/LLM accounting + 83f28044-shaped 32-ticker regression).
- **Cost guard posture stays** (ACTIVE): `INTEL_BACKGROUND_WORKERS_ENABLED=false` master kill
  switch, `INTEL_V3_SNAPSHOT_WRITES_ENABLED` write guard, interval clamps. Do not re-enable
  background workers casually; see `docs/deploy/RAILWAY_COST_GUARD.md`.
- **Truth/repair diagnostics are protected operator infrastructure** (Stage 10B/11A/11B/12C
  lineage): `/diagnostics/finance-intel/*` (~36 cert-gated endpoints incl. financial-truth
  baseline, books reconciliation, current-price repair, next-buy diagnostic). They are not
  primary navigation and must not be deleted for looking unused.
- **Policy tickers live in config** (`v2/backend/app/policy_tickers.json`, loader
  `services/policy_tickers.py`, override `POLICY_TICKERS_FILE`) with exact-parity tests
  (`test_policy_tickers.py`). Never re-hardcode ticker membership in policy modules; provider
  symbol-translation maps stay in provider code.
- **Tax lots are estimates, reconciliation-gated** (`tax_lot_engine.py`): every production
  tx_type explicitly classified; unsupported/unknown share events block authoritative display;
  calendar-anniversary long-term logic; shares tolerance max(0.0001, 0.1%), basis 2%;
  no dollar tax-liability math anywhere; US-federal estimates-only labeling.
- **`recommendations_trusted` is always False** in the preview contract; `numeric_plan_trusted`
  gates investable presentation. Never render an untrusted plan as actionable.

## Current test/build state (post-consolidation)

- Backend: full suite green (`8290 passed, 0 failed` at consolidation; includes the conftest
  event-loop guard and stale-fixture modernization — both test-only).
- Frontend: full jest green; `tsc --noEmit` clean; `next build` green.
- Baseline before consolidation (main @ PR #471): backend 93 failed / 8910 passed
  (documented pre-existing failures), frontend 3 suites failing to compile.
- Distributed Run Intel workflow PR (2026-07-21): full backend suite green (8400+ passed,
  0 failed — Tier 3: broad architecture/schema change + mission-mandated), full frontend
  jest green (596 passed), `tsc --noEmit` clean, `next build` green (placeholder
  `NEXT_PUBLIC_*` env vars needed to prerender locally — sandbox-only).
- Distributed Run Intel model cost routing PR (2026-07-22): full backend suite green
  (8467 passed, 0 failed, includes 16 new focused tests); no frontend files changed.
- Haiku specialist output completion fix PR #484 (2026-07-22): full backend suite green
  (8500 passed, 0 failed, includes 33 new focused tests in
  `test_specialist_output_completion_v1.py` — 27 from the initial patch, 4 from the first
  release-blocker follow-up (counts actual provider requests against a real `LLMClient`),
  2 from the second release-blocker follow-up (`primary_max_attempts=1` — a rate-limit/
  transient failure now costs exactly one real `_single_call()`, not up to 4); the
  34-holding golden run's exact LLM-call accounting moved from 22 to 49 calls to reflect
  the new default 2-ticker Haiku batch cap, same complete coverage); no frontend files
  changed; no SQL.
- Run Intel trust contract PR 1/7 (2026-07-23): full backend suite green (8521 passed,
  0 failed — Tier 3, broad cross-cutting change: `run_trust_contract_v1` is a new
  shared projection consumed by both session-native publication and the shared
  `snapshot_builder.py`/`intel_v3_service.py` read path used by every Intel v3
  snapshot read, plus 33 new focused tests in `test_run_trust_contract_v1.py` and
  `test_run_trust_contract_integration.py`); full frontend jest green (639 passed,
  25 new), `tsc --noEmit` clean, `next build` green (same placeholder
  `NEXT_PUBLIC_*` env vars as prior PRs needed to prerender locally — sandbox-only).
  No SQL.
- Run Intel source-reference lineage PR 2/7 (2026-07-24, initial slice): full
  backend suite green (8580 passed, 0 failed — Tier 3, cross-cutting: new
  `source_lineage_v1.py` module consumed by `evidence_bundle_v1.py`,
  `specialist_agents_v1.py` and `run_trust_contract_v1.py`; 29 new focused
  tests in `test_source_lineage_v1.py` plus manifest-shape updates to
  `test_run_trust_contract_v1.py`'s fixture helper). No frontend files
  changed. No SQL, no new provider, no new env var, zero additional
  provider/LLM calls.
- Run Intel source-reference lineage PR 2/7 (2026-07-24, same-PR patch —
  six release-blocker semantic defects): full backend suite green (8619
  passed, 0 failed — same Tier 3 justification; +39 net new focused tests
  across `test_source_lineage_v1.py` — strict "never full" manifest-validation
  proofs, artifact-ownership/wrong-user/wrong-ticker/empty-payload gap
  proofs, fingerprint source-identity-projection proofs (price digest
  excluded, other lanes retained, gaps retained), bounded-reference-storage
  proofs (per-lane/per-manifest caps + free-text capping),
  review-input-fingerprint sensitivity proofs — and `test_run_trust_contract_v1.py`
  (`TestSourceHealthSemantics`: all-partial-never-healthy, all-full-healthy,
  all-missing-blocked, mixed-limited, zero-outputs-unknown, ticker-level
  full-requires-every-output-full preserved). `test_distributed_golden_run.py`
  (exact provider/LLM call accounting) and
  `test_distributed_durability_fixes.py::TestD4FingerprintStability` (the
  pre-existing intraday-noise/analytical-change fingerprint proofs) both
  still green, confirming zero call-count regressions and no loss of the
  original fingerprint-stability guarantee. Still no frontend files changed
  (verified: `session_evidence_refs` and the
  `outputs_with_source_refs`/`has_source_refs` fields the frontend actually
  reads are unaffected string/count vocabulary, never the raw reference
  arrays or the new `outputs_full_lineage`/`outputs_partial_lineage`/
  `outputs_missing_lineage` diagnostic counts) — no frontend test run
  needed. No SQL, no new provider, no new env var, zero additional
  provider/LLM calls. `ai_pr_readiness_check.py --base-ref origin/main`:
  pass-with-advisory-warnings (10 files / ~1679 lines vs. the Level 1 soft
  limits — expected for a same-PR fix of six identified semantic defects in
  one cross-cutting module, not scope creep); `certify_v4_1.py`: all PASS.
- Run Intel source-reference lineage PR 2/7 (2026-07-24, round-3 same-PR
  patch — six further normal-path lineage-trust gaps, see item 2 above for
  the full defect list): full backend suite green (**8670 passed, 0
  failed** — same Tier 3 justification, same 6 files touched:
  `source_lineage_v1.py`, `evidence_bundle_v1.py`, `specialist_agents_v1.py`,
  `run_trust_contract_v1.py`, `test_source_lineage_v1.py`,
  `test_run_trust_contract_v1.py`; +51 net new focused tests over the
  round-2 baseline). `test_distributed_golden_run.py` (exact provider/LLM
  call accounting) and `test_distributed_durability_fixes.py` both still
  green — zero new provider/LLM calls, no call-count regressions. Still no
  frontend files changed, no SQL, no new env var,
  `decision_policy_v1`/visible actions/allocation/Deploy Cash unchanged.
  `ai_pr_readiness_check.py --base-ref origin/main`: pass-with-advisory-
  warnings (10 files / ~3165 added+deleted lines vs. the Level 1 soft
  limits — expected: the user's round-3 request explicitly named six
  interrelated normal-path lineage-trust defects to resolve "in this same
  patch," and CLAUDE.md's capability-slice guidance treats one coherent
  capability as one slice even when it spans related tests/code in the
  same module, not scope creep); `certify_v4_1.py`: all PASS. **Runtime
  caveat unchanged**: production source-lineage behavior remains NOT
  runtime-proven — deferred to the single final certification run after
  the final certification run (item 5), same as every prior entry in this sequence.
- Deterministic conflict handling / review-LLM deletion PR #487 (2026-07-25,
  merged): full backend **8679 passed, 0 failed**; full frontend **664
  passed, 0 failed**; TypeScript clean; production build clean.
- Run Intel operational-reliability PR, finish-plan item 4 (2026-07-25, see
  full entry above), patched a second round to close eight connected
  correctness gaps: full backend **8767 passed, 0 failed**; full frontend
  **677 passed, 0 failed**; TypeScript clean; frontend production build
  compiles/type-checks cleanly (static prerender needs Supabase env vars
  this sandbox doesn't have — sandbox-only, unrelated to the diff). No SQL,
  no new env var, no new provider, no new agent. Net production-source
  delta vs `main`: **+580 lines** (up from the originally reported +458 —
  justified in the item 4 entry above: the specific redundant paths asked
  to be deleted were removed, the remaining growth is genuine new logic for
  8 independently-real correctness gaps, never code-golfed). Runtime
  caveat unchanged — production behavior remains unproven, deferred to the
  single final certification run, which has NOT started.

## SQL / env state

- Migration `v2/database/027_intel_run_distributed_tasks.sql` is REQUIRED (manual,
  idempotent) for the distributed Run Intel workflow; until applied, POST /intel/v3/run
  returns an explicit retryable `run_session_create_failed` (no legacy fallback). It also
  marks unfinished legacy sessions `superseded`. Optional tuning env vars (defaults in
  code, no manual action): `INTEL_V3_DISTRIBUTED_MAX_COLLECTOR_CONCURRENCY=4`,
  `INTEL_V3_DISTRIBUTED_MAX_LLM_CONCURRENCY=2`,
  `INTEL_V3_DISTRIBUTED_MAX_SPECIALIST_BATCH=5`,
  `INTEL_V3_DISTRIBUTED_HAIKU_MAX_SPECIALIST_BATCH=2` (narrows Haiku-routed specialist
  batches further; never exceeds the max-specialist-batch ceiling above),
  `INTEL_V3_DISTRIBUTED_TASK_LEASE_SECONDS=300`,
  `INTEL_V3_DISTRIBUTED_MAX_TASK_ATTEMPTS=3`.
  `INTEL_V3_ON_DEMAND_REFRESH_ENABLED` no longer affects Run Intel (kept only so set
  deployments don't fail validation). Migration 027's two owner-guard trigger functions
  were corrected in place (source-file fix, not a new migration): the PL/pgSQL variable
  `session_user` shadowed the reserved `SESSION_USER` builtin instead of holding the
  local lookup, so the owner comparison silently checked the wrong value; renamed to
  `v_session_user_id` with qualified table aliases and `IS DISTINCT FROM`.
- Distributed Run Intel model cost routing (2026-07-22): `WorkerSupervisor` now builds
  two separate `LLMClient` instances instead of one shared client — standard specialist
  analysis (`TASK_SPECIALIST_ANALYSIS`) routes to `intel_v3_distributed_specialist_model`
  (default `claude-haiku-4-5-20251001`) with fallback disabled (a specialist failure
  retries the durable task on the same model, never auto-escalating to Sonnet); the
  conditional conflict-review agent (`TASK_REVIEW_CONFLICT`) routes to
  `intel_v3_distributed_review_model` (default `claude-sonnet-5`) with fallback to
  `intel_v3_distributed_review_fallback_model` (default `claude-haiku-4-5-20251001`).
  `decision_policy_v1.decide()` remains the only visible Buy/Hold/Trim/Sell authority;
  `TASK_TICKER_DECISION`/publication still make zero LLM calls. `LLMClient.fallback_model`
  is now `Optional[str]` — no fallback when null/empty/identical to the primary model;
  unrelated legacy `LLMClient()` callers (orchestrator, etc.) keep their Sonnet 4.6 →
  Haiku 4.5 default failover unchanged. Env vars (all additive, existing deployments
  unaffected without setting them): `INTEL_V3_DISTRIBUTED_SPECIALIST_MODEL`,
  `INTEL_V3_DISTRIBUTED_REVIEW_MODEL`, `INTEL_V3_DISTRIBUTED_REVIEW_FALLBACK_MODEL`.
- Haiku specialist output completion fix (2026-07-22, no SQL): root cause was an unbounded
  token budget (`350 * batch_size`) combined with 5-ticker Haiku batches and a single
  batched repair retry that re-requested already-valid tickers — Haiku's verbose/fenced/
  truncated responses then exhausted the durable task's whole 3-attempt budget on tickers
  that had already succeeded. `specialist_agents_v1.py`: `_specialist_token_budget()`
  replaces the bare multiplier (650/ticker, clamped 700–1800); the repair loop now issues
  one call PER missing/malformed ticker (never a validated peer), bounding a 2-ticker batch
  to ≤3 total calls; `SpecialistBatchOutcome` gained `repair_calls`/`truncated_calls`/
  `quota_or_auth_failures`/`requested_tickers`/`partial_success` for observability.
  `agents/llm.py`: new `_classify_provider_exception()` (quota/authentication/rate_limit/
  transient) — quota/auth stop the same-model backoff loop after one attempt (a configured
  fallback MODEL, e.g. the review agent's Sonnet→Haiku, still runs — only same-model
  retries and specialist repair calls are skipped); new `_extract_json(..., reject_prose=)`
  strict mode (trim + strip one outer fence, no prose-object scanning) used only by
  specialist calls via `ask_json(..., reject_prose=True)` — the default prose-tolerant
  behavior for all other `ask_json` callers is unchanged. `worker_supervisor_v1.py`:
  `_effective_specialist_batch_cap()` applies `intel_v3_distributed_haiku_max_specialist_batch`
  (default 2) whenever the configured specialist model name contains "haiku", clamped to
  never exceed the unrelated `intel_v3_distributed_max_specialist_batch` ceiling; new
  per-task structured log line + 4 session metrics counters (additive to the existing
  JSONB `intel_run_sessions.metrics`, no schema change).
  **Release-blocker follow-up (same PR #484):** the specialist repair loop above was
  correctly bounded at the wrapper (`ask_json`) call-count level, but `LLMClient` itself
  still silently repeated a truncated response against the SAME model/prompt/batch one
  layer down (`_call_with_backoff` at a larger token budget) — invisible to the
  specialist's own ≤3-calls bound and the 1800-token ceiling, since prior tests only
  mocked `ask_json()` and never counted actual `_single_call()` provider requests.
  `ask_json()` gained `retry_truncated_response: bool = True` (legacy default —
  unrelated callers, including the review agent's Sonnet→Haiku fallback, keep the
  internal retry); specialist calls pass `retry_truncated_response=False`, so a
  detected truncation is still recorded in metadata but never silently repeated —
  the specialist's own per-ticker repair owns it instead. Separately, the
  quota/auth-only "skip repair" gate was widened: ANY actual provider-call failure
  (an exhausted rate-limit/transient retry too, not just quota/auth) now skips the
  per-ticker repair loop and returns a retryable task outcome — a parse/truncation
  failure (provider answered, JSON was bad) has no classification and remains
  repair-eligible; a genuine transport failure never gets reinterpreted as
  ticker-level malformed JSON, and an already-validated peer ticker is never
  discarded or re-requested. 4 new tests use a REAL `LLMClient` with only
  `_single_call()` stubbed (never `ask_json()`) to count actual provider requests.
  **Second release-blocker follow-up (same PR #484):** `retry_truncated_response=False`
  closed the hidden truncation retry, but `_call_with_backoff`'s own `max_attempts`
  still defaulted to 4 — a rate-limit/transient failure on one specialist `ask_json()`
  call could still cost up to 4 actual `_single_call()` provider requests internally
  before returning, since the prior rate-limit test only asserted the wrapper-level
  `outcome.llm_calls == 1` and never counted real `_single_call()` invocations.
  `ask_json()` gained `primary_max_attempts: int = 4` (legacy default, threaded into
  the primary `_call_with_backoff(..., max_attempts=primary_max_attempts)` call only —
  the truncation-retry and fallback-model backoff calls are untouched); specialist
  calls now pass `primary_max_attempts=1`, so ANY provider-level failure (quota/auth,
  rate-limit, transient, timeout) costs exactly one actual provider request per
  `ask_json()` call — the durable task's own retry/backoff owns trying again, never
  `LLMClient`'s internal loop. 2 more tests prove this: a legacy caller with the
  default argument still gets up to 4 real backoff attempts, and a quota/auth failure
  makes exactly 1 real `_single_call()` request (in addition to the existing
  rate-limit/provider-failure tests, which now also assert the exact `_single_call()`
  count, not just the wrapper-level `outcome.llm_calls`).
- Migration `v2/database/025_watchlist.sql` is REQUIRED (manual, additive) for Watchlist;
  endpoints return 503 `watchlist_migration_required` until applied. Everything else unchanged.
- `FINANCE_RUNTIME_CERT_SECRET` (Vercel, server-only) + `FINANCE_RUNTIME_CERT_ENABLED=true`
  and cert user config (Railway) power the Advisor cash plan.
- `INTEL_V3_ON_DEMAND_REFRESH_ENABLED=true` (Railway) recommended so Run Intel drains without
  the optional worker. `INTEL_V3_SNAPSHOT_WRITES_ENABLED=true` needed for new snapshots.
- Backend `INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true` (Railway) MUST stay set — the Advisor view's
  Intel section reads `GET /intel/v3/snapshot`, which 404s without it. Only the frontend
  `NEXT_PUBLIC_...` variant of this name is dead. Backend boot-required vars remain
  `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`,
  `ENCRYPTION_KEY`.
- `NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED` is no longer read by any code — safe to
  remove from Vercel at leisure (documented cleanup, not required).
