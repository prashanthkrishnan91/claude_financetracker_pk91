# Intel v3 — Finance Agent Skill Pack Audit (Phase 1 Spec)

Status: Phase 1 planning/spec only. No runtime code, no SQL, no providers, no UI changes. Future phases are gated on the deterministic Intel v3 visible-decision contract remaining green in production.

Date: 2026-05-07
Owner: Intel v3 architecture
Severity: Level 1 (focused multi-file docs/spec, scoped, no runtime risk)

References:
- `artifacts/Intel_v3_Architecture_Plan_Draft2.pdf` — present in repo.
- `artifacts/Intel_v3_Architecture_Plan_Draft2_Anthropic_Finance_Agent_Addendum.md` — present in repo. Phase numbering and the architecture-boundary language in this audit are aligned with this addendum.
- `docs/ai/HANDOFF.md` — Phase 0 (PR #220, #221, #222) and Phase 0.5 (PR #223) certified state.
- `v2/backend/app/services/intelligence/v3/*` — current deterministic decision spine.
- `v2/backend/tests/test_intel_v3_phase0_5_regression_guardrail.py` — current regression contract.

---

## 1. Executive Decision Boundary

This is the non-negotiable rule that all future research-worker work must respect.

1. The deterministic Intel v3 policy (`decide()` in `v2/backend/app/services/intelligence/v3/decision_policy_v1.py`) is the sole authority for the visible Buy/Hold/Trim/Sell action on every held card.
2. Agents, LLMs, finance-agent skill packs, research workers, and any future external/managed agent infrastructure may only produce sourced research artifacts. They never write the visible action label, conviction, allocation, deployment amount, or final recommendation.
3. Agents and workers must not bypass the existing Data Truth / evidence-quality contract (`v2/backend/app/services/intelligence/v3/data_truth_contracts.py`, `data_truth_v1.py`, `existing_signal_truth_adapter.py`). Any artifact that is unsourced, weakly sourced, stale, or fabricated is treated as MISSING/WEAK by the deterministic policy and must not influence visible action.
4. LLMs may summarize, extract, compare, debate, or red-team evidence, but only when (a) the input is grounded in a named source, (b) the output is replayable, and (c) the artifact carries provenance. LLMs never directly emit Buy/Hold/Trim/Sell.
5. The visible cockpit (`IntelV3Cockpit`, `IntelV3Card`, `IntelV3Drawer`) must continue to read only from `intel_v3_snapshots` (source_path = `intel_v3_snapshot`). No agent/LLM call may run on page load.
6. The legacy `RecommendationService` aggregation path (`recommendation_engine.py`) remains intentionally decoupled per PR #220. Future research artifacts must not re-couple it.

Hard rule: an artifact contract field of `final_action`, `buy/hold/trim/sell`, `final_conviction`, `final_allocation`, `deploy_amount`, or any equivalent action-authority field is forbidden.

---

## 2. Current-State Repo Audit

This section records what exists in the repo today (as of post-PR #223) so future PRs do not drift the boundary.

### 2.1 v3 snapshot source-of-truth path

- `v2/backend/app/services/intelligence/v3/intel_v3_service.py::IntelV3Service`
  - `get_latest_snapshot()` — reads latest active row from `intel_v3_snapshots` table. Zero LLM calls. Zero provider calls. Emits `intel_v3_snapshot_response_summary`.
  - `run_v3()` — builds decisions and persists a new snapshot. Emits `intel_v3_snapshot_created` and `intel_v3_snapshot_certification_summary`.
- `v2/backend/app/routers/intel_v3.py`
  - `GET /intel/v3/snapshot` — feature-flag-gated read.
  - `POST /intel/v3/run` — feature-flag-gated run.
  - `GET /intel/v3/runs/{run_id}` — run status placeholder.
- Feature flag: `INTEL_V3_VISIBLE_SNAPSHOT_ENABLED`.

### 2.2 Run vs snapshot separation

- One `run_id` per call to `run_v3()`.
- One `snapshot_id` per persisted snapshot row.
- `snapshot_id` and `run_id` are coherent across all cards within a snapshot (asserted by `assert_snapshot_certification_clean()` in `test_intel_v3_phase0_5_regression_guardrail.py`).
- Run status is observable; snapshot is the immutable visible artifact.

### 2.3 ReadOnlyEvidenceAdapter role

- `v2/backend/app/services/intelligence/v3/read_only_evidence_adapter.py::ReadOnlyEvidenceAdapter`
  - Reads persisted `recommendations`, `positions`, `agent_runs`, and `agent_insights` rows only.
  - Does NOT call `recommendation_engine`, does NOT call LLMs, does NOT call providers.
  - Emits `intel_v3_evidence_source_summary` with `source_mode=read_only_persisted`, `attempted_llm_calls=0`, `generated_legacy_recommendations=false`.
  - Returns `(cards, stats)` where `stats` reports `active_position_count`, `persisted_recommendation_count`, `persisted_agent_insight_count`, `missing_recommendation_count`, `missing_evidence_count`, `stale_or_missing_source_count`.
- This adapter is the only place v3 reads existing signals. It is the natural integration seam for future research artifacts (read-only, with provenance).

### 2.4 Deterministic decision kernel

- `v2/backend/app/services/intelligence/v3/decision_policy_v1.py::decide()` — pure function. No IO, no LLM, no DB. Independent-axis priority order: SELL → TRIM → BUY → HOLD. Conviction capping rules per docstring.
- Inputs are constructed via `existing_signal_adapter.build_truth_aware_decision_input()` and never directly from LLM output.
- Visible rationale assembled here from sanitized evidence text via `_clean_evidence_text()` (drops raw metric keys and price targets).

### 2.5 Certification / guardrail tests

- `v2/backend/app/services/intelligence/v3/source_validator_lite.py::certify_snapshot_cards()` — returns 12 stats including `hard_violations`, `generic_copy_count`, `repeated_skeleton_count`, `ticker_prefix_only_reason_count`, `weak_buy_rationale_count`, `action_conflict_count`, `raw_metric_key_count`, `posture_label_count`.
- `v2/backend/tests/test_intel_v3_phase0_5_regression_guardrail.py::assert_snapshot_certification_clean()` — single reusable assertion covering schema version, action set, action-count consistency, snapshot/run ID coherence, and zero hard/posture/raw-key violations.
- Static source guards confirm no LLM imports and no legacy aggregation in v3 path.

### 2.6 Schema/version contracts

- Snapshot `schema_version == "v3.1"` (exact match enforced).
- Action set: `{BUY, HOLD, TRIM, SELL}` only.
- `legacy_path_used == false`.
- `source_path == "intel_v3_snapshot"`.
- `attempted_llm_calls == 0`, `page_load_llm_calls == 0`, `generated_legacy_recommendations == false`.

### 2.7 Decoupled legacy paths (must not be reconnected)

- `v2/backend/app/services/recommendation_engine.py` (legacy aggregation).
- `RecommendationService.get_insight_cards()` and `_compute_insight_cards()`.
- Any LLM-on-page-load enrichment.
- `useRecommendations()`, `useLatestAgentRun()`, `useDecisionLog()` legacy frontend hooks (gated behind `legacyEnabled = !INTEL_V3_ENABLED`).

### 2.8 Reusable seams for future research artifacts

- `ReadOnlyEvidenceAdapter` can be extended to additionally read from a future `research_artifacts` table without touching `decide()`.
- `DecisionInputV3` accepts optional evidence text fields (`primary_driver`, `risk_flag_text`, `action_reason`, `analyst_drivers`) — these are the only LLM-touchable channel into the visible rationale, and they pass through `_clean_evidence_text()` first.
- `intel_v3_snapshot_certification_summary` log already carries a stable schema for adding new artifact-derived fields without breaking existing parsers.

### 2.9 Components that must stay decoupled from visible decisions

- Any future LLM/agent/worker code paths.
- `agent_runs`, `agent_insights` are read-only inputs for evidence text only; they never set actions.
- Any provider API client. Providers may populate research artifacts (Phase 3+); they must not be called on page load and must not write `intel_v3_snapshots` rows directly.

---

## 3. Finance Agent Skill Pack Audit

Each skill pack is an audit category, not an implementation. None are built in this PR. The "Phase" tag below indicates the earliest phase it may be considered.

For each skill pack, the same rules apply:
- It produces sourced artifacts only.
- It must respect freshness/staleness.
- It must fail closed (artifact = MISSING/WEAK) when sources are missing or weak.
- Its output may only enter visible behavior via the deterministic v3 policy.

### 3.1 Filing / Transcript Risk Extraction (Phase 3 candidate)

- Purpose: extract risk language and material facts from 10-K/10-Q/8-K, earnings transcripts, and S-1/F-1 filings.
- Allowed questions: What new risk factors appeared this quarter? What language changed since last filing? What guidance/segment data is disclosed?
- Allowed inputs: SEC EDGAR filings, transcript text from a named provider.
- Allowed outputs: extracted risk items, language-change diffs, segment fact rows, source URL + filing date.
- Required provenance: filing accession number, source URL, filing date, section reference, extraction model + version.
- Forbidden outputs: action labels, conviction labels, price targets, "buy"/"sell" recommendations, opinion without quote.
- Deterministic consumption: risk severity counts may inform the existing `RiskBand` axis input via the truth adapter, never directly.
- Freshness: filing-date-anchored. Stale = older than the most recent qualifying filing for the issuer.
- Failure modes: filing not found; transcript provider stale; OCR garbage; hallucinated quote — all must mark artifact WEAK and skip visible influence.
- Required tests before implementation: deterministic golden-fixture test on a known filing; replay test; "no quote present → no claim emitted" test.
- Cost/rate limits: cap per-issuer and per-run; cache by accession number.
- Phase: 3 (single narrow worker).

### 3.2 Earnings & Catalyst Watch (Phase 3 candidate)

- Purpose: track upcoming earnings dates and material catalyst windows; record post-event surprise/guidance evidence.
- Allowed questions: When is the next earnings date? What was the prior surprise/direction? What was the post-call guidance change?
- Allowed inputs: vendor calendar, transcript text (post-event).
- Allowed outputs: catalyst calendar entries with date/window, surprise direction and magnitude with source, guidance-change text quote.
- Required provenance: vendor name, fetch timestamp, event date, transcript URL.
- Forbidden outputs: "buy on earnings", price targets, projected EPS opinions, action labels.
- Deterministic consumption: presence of an imminent catalyst window may inform a future `pre_catalyst_caution` axis input. Implementation deferred.
- Freshness: pre-event window (T-N days) and post-event window (T+M days). Stale = beyond window.
- Failure modes: vendor calendar drift; missed event; ambiguous guidance — fail closed.
- Required tests: known-historical-quarter fixture replay; "no transcript → no surprise claim" test.
- Phase: 3.

### 3.3 Valuation Context (Phase 3–4 candidate)

- Purpose: compare a name's current valuation to its own history and a peer set.
- Allowed questions: Where does the current multiple sit vs 5y history? Vs peer median? Are growth/quality/risk roughly comparable?
- Allowed inputs: vendor fundamentals, peer set definition.
- Allowed outputs: range/percentile context, peer median context, qualifier (e.g. "below 5y median, peer set has higher growth"). Plain-English text + structured numeric.
- Required provenance: data vendor, as-of date, peer set membership and source, multiple definition.
- Forbidden outputs: price targets, "fairly valued / overvalued" as a final label, action implication.
- Deterministic consumption: may feed `PriceBand` axis only via truth adapter; final Buy/Hold/Trim/Sell is decided by `decide()`.
- Freshness: as-of date must be within configured window.
- Failure modes: peer set drift; data vendor stale; single-multiple distortion — fail closed.
- Required tests: peer-set stability test; multiple-vs-history fixture test; "stale data → SUPPRESSED" test.
- Phase: 3 then 4.

### 3.4 Fundamental Quality / Business Durability (Phase 3+ candidate)

- Purpose: summarize durable-quality evidence (returns on capital, margins trend, balance sheet strength) without prescribing action.
- Allowed inputs: vendor fundamentals.
- Allowed outputs: structured quality-axis observations + plain-English summary.
- Forbidden outputs: action labels, "high quality stock to buy" rhetoric.
- Deterministic consumption: feeds existing evidence-quality / attractiveness axes only via truth adapter.
- Phase: 3+.

### 3.5 Capital Allocation, Dilution, Buyback, Dividend Behavior (Phase 4 candidate)

- Purpose: factual record of share count change, buybacks, issuance, dividend policy changes.
- Forbidden outputs: opinion on whether buybacks/dividends are "good"; no action label.
- Phase: 4.

### 3.6 Risk Red-Team / Controversy / Regulatory Risk (Phase 3 candidate)

- Purpose: surface counter-thesis risks, recent regulatory events, and reputational/legal concerns with sources.
- Forbidden outputs: a SELL recommendation; a generic "risky" label without sourced evidence.
- Deterministic consumption: contributes to `RiskBand` only via truth adapter.
- Phase: 3.

### 3.7 Analyst Estimate / Revision Context (Phase 3–4 candidate)

- Purpose: report direction and magnitude of consensus revisions.
- Forbidden outputs: a synthetic price target; an action label; sentiment numbers without a source.
- Phase: 3 (read) → 4 (richer integration).

### 3.8 News / Event Evidence Normalization (Phase 3 candidate)

- Purpose: normalize incoming news into typed event records (regulatory, M&A, leadership, product) with source URL and date.
- Forbidden outputs: price impact opinion, action implication.
- Phase: 3.

### 3.9 ETF / Fund / Crypto-specific Evidence (Phase 4 candidate)

- Purpose: where the held asset is an ETF/fund/crypto, capture asset-class-appropriate evidence (expense, methodology, holdings, network/protocol facts) with provenance.
- Forbidden outputs: equity-style action labels for non-equity assets.
- Phase: 4.

### 3.10 Portfolio Exposure / Concentration Context (Phase 4 candidate)

- Purpose: produce factual portfolio-level exposure artifacts (sector, theme, factor, single-name concentration) for the deterministic governor.
- Note: today, `portfolio_governor_lite.py` already computes weights deterministically. This skill pack would only enrich the diagnostic context, never override the governor.
- Forbidden outputs: action labels.
- Phase: 4.

### 3.11 Hidden-Gems / Opportunity Scout (Phase 5 candidate, research artifact only)

- Purpose: produce a research-artifact-grade candidate shortlist of names worth researching, with reasons and missing-evidence flags.
- Allowed outputs: candidate watchlist artifacts only.
- Forbidden outputs: any deploy/buy authority, automatic addition to holdings, ranking that drives Deploy.
- Promotion rule: a candidate cannot affect Deploy until it has passed the same evidence/truth contract as existing held positions.
- Phase: 5.

### 3.12 Provider candidate classes (planning only — no commitments)

- Filing/transcript: SEC EDGAR (free), one paid transcript provider (placeholder).
- Calendar / estimates / fundamentals: existing provider integration to be evaluated; new vendor only with a budget gate.
- News: licensed feed required; free news sources are not authoritative for visible artifacts.
- Hard constraint: no vendor commitment is made in this PR. Concrete vendor selection is a Phase-2/3 decision with a budget gate.

---

## 4. Research Artifact Contract v1 (Planning Only)

The artifact contract below is described in prose / pseudocode. No SQL, no Python class, no schema, and no migration is added in this PR. The contract is the basis for the Phase 2 SQL/store proposal.

Required artifact fields (high level):

- `artifact_id` — UUID, immutable.
- `artifact_schema_version` — semver-like string; first version `"artifact.v1"`.
- `ticker_or_scope` — ticker symbol, ETF symbol, watchlist candidate ID, or `portfolio` for portfolio-scope artifacts.
- `artifact_type` / `skill_pack` — one of the audit-defined skill packs above.
- `generated_at` — UTC timestamp of artifact creation.
- `source_window` — `[as_of_start, as_of_end]` representing the time horizon the artifact reasons over.
- `source_refs` — list of `{provider, url_or_id, fetched_at, source_kind}`. At least one entry required for any non-empty `extracted_facts`.
- `provider_or_source_identity` — primary provider/source name and version.
- `extracted_facts` — typed structured observations (e.g. `metric_observation`, `risk_item`, `catalyst_item`, `thesis_pillar` from the addendum).
- `evidence_summary_plain_english` — short, source-grounded summary text. Must pass `_clean_evidence_text()`-equivalent sanitization (no raw metric keys, no price targets, no fake precision) before reaching any visible surface.
- `confidence_or_trust_level` — `HIGH | MEDIUM | LOW | UNKNOWN`. Used to gate downstream consumption.
- `freshness_status` — `FRESH | STALE | UNKNOWN` per skill pack's freshness rule.
- `limitations_or_missing_evidence` — list of explicit gaps; used by the truth adapter to mark `safe_for_decision=false` if material.
- `deterministic_policy_inputs_allowed` — explicit list of axis hints this artifact may legitimately influence (e.g. `["evidence_axis_band", "risk_axis_band"]`). Anything outside this list is ignored by the truth adapter.
- `deterministic_policy_inputs_forbidden` — explicit deny list for this artifact type.
- `trace_run_ids` — `{worker_run_id, parent_intel_run_id_if_any}`.
- `replay_idempotency_key` — deterministic key over `(skill_pack, ticker_or_scope, source_refs, model_version)` so re-runs collapse rather than duplicate.
- `expiration_or_staleness_handling` — TTL or "until next event" rule per skill pack.
- `audit_log` — list of `{tool_call, input_digest, output_digest, model_id, latency_ms, cost_estimate, rejected_claims}` entries.

Forbidden fields (HARD rule):

- `final_action`
- `buy` / `hold` / `trim` / `sell` (or any synonym)
- `final_conviction`
- `final_allocation`
- `deploy_amount` / `deploy_dollar` / `deploy_shares`
- any field that, by name or content, asserts visible recommendation authority

Validation gate for the future `research_artifacts` table (Phase 2): a row that contains any forbidden field name (or its content equivalent) must be rejected at write time and logged.

---

## 5. Worker Boundary Contract

A future "research worker" is any background process or function that produces artifact rows. The boundary below is what every worker must satisfy.

What a worker MAY do:

- Read from configured external/internal sources within its skill pack scope.
- Extract structured facts and attach `source_refs`.
- Call an LLM only for source-grounded extraction/summarization/red-team/comparison.
- Write rows to the future `research_artifacts` store (Phase 2+) with full provenance.
- Emit audit log records (`worker_audit_event`) including model id, latency, cost, and rejected claims.
- Fail closed: when sources are missing/weak/stale, write an artifact with `confidence_or_trust_level=LOW`, `freshness_status=STALE/UNKNOWN`, and explicit `limitations_or_missing_evidence`. Do not synthesize.

What a worker MAY NOT do:

- Call into or mutate `intel_v3_snapshots` directly.
- Invoke `decide()` or any decision-policy function, or write any visible Buy/Hold/Trim/Sell field.
- Replace, shadow, or wrap the deterministic decision policy with an LLM-driven equivalent.
- Set `final_conviction`, `final_allocation`, `deploy_amount`, or any equivalent.
- Bypass the truth adapter and feed visible UI text directly.
- Expose raw metric keys or internal diagnostics in any visible field.
- Use stale/missing data silently.
- Fabricate quotes, sources, or numbers.
- Re-couple to `recommendation_engine.py` or any legacy aggregation.
- Run on Intel page load.

---

## 6. Phased Roadmap

Each phase below has explicit objective / allowed / forbidden / test gates / production validation gates / rollback / size hints. Phases are sequential; later phases must not start until prior gates are green in production.

### Phase 1 — Skill Pack Audit / Architecture Spec (this PR)

- Objective: ratify the boundary, current-state audit, skill packs, artifact contract, worker contract, roadmap, acceptance criteria, and risk register.
- Allowed: this doc + small HANDOFF.md and progress_log.md updates.
- Forbidden: any runtime code, SQL, frontend, provider, LLM call, behavior change.
- Test gates: docs only — confirm no runtime files changed via `git diff`.
- Production validation: not applicable (docs-only).
- Rollback: revert this PR.
- Size / model: small docs PR; Sonnet sufficient.

### Phase 2 — Research Artifact Store v1 Planning + SQL Proposal

- Objective: propose SQL schema(s) for `research_artifacts` and supporting tables (`sourced_claim`, `metric_observation`, `risk_item`, `catalyst_item`, `thesis_pillar`, `worker_audit_event`) following Phase 4 of the addendum.
- Allowed: SQL proposal in `v2/database/` as a draft migration NOT yet applied; doc updates.
- Forbidden: applying the migration to production; backend runtime code that reads/writes new tables; UI changes.
- Test gates: SQL lint; review against forbidden-field list in §4; cardinality and index review.
- Production validation: not applicable yet.
- Rollback: revert PR; if migration is conditionally applied, drop new tables (additive, no other coupling).
- Size / model: medium; Sonnet/Opus planner with budget gate.

### Phase 3 — Single Narrow Worker Scaffold (Dark-Run Mode)

- Objective: implement exactly one narrow worker (recommended: Earnings Reviewer or Filing Risk Extractor), running off-path, writing artifacts only.
- Allowed: a flag-gated background worker entrypoint; artifact writes only; full audit logging; no read into the visible snapshot path.
- Forbidden: any change to `decide()`, `IntelV3Service`, `read_only_evidence_adapter.py`, or any visible UI surface; any page-load LLM call; any change to certification detectors.
- Test gates: golden-fixture replay; idempotency by `replay_idempotency_key`; "no source → no claim" test; budget/timeout caps; rejected-claim logging test.
- Production validation gates:
  - Worker only runs when its dedicated flag is enabled (`RESEARCH_WORKER_<NAME>_ENABLED`).
  - `intel_v3_snapshot_response_summary` and `intel_v3_snapshot_certification_summary` remain unchanged in production.
  - No new entries in legacy aggregation logs.
- Rollback: kill switch via flag; artifacts table is read-only by visible path so disabling worker is safe.
- Size / model: medium; Sonnet for build, Codex for tests.

### Phase 4 — Truth-Aware Artifact Ingestion into v3 Shadow Diagnostics

- Objective: extend `existing_signal_truth_adapter` (or sibling) to read artifacts and emit shadow diagnostic axis hints alongside, but not into, the visible decision.
- Allowed: shadow diagnostic counters, log-only fields in `intel_v3_snapshot_certification_summary` (e.g. `shadow_artifact_evidence_band_counts`), unit tests proving shadow inputs do not change visible action.
- Forbidden: visible action, conviction, allocation, or rationale change; raw metric keys in UI; any artifact field reaching UI without sanitization.
- Test gates:
  - Property test: for any synthetic artifact set, visible Buy/Hold/Trim/Sell distribution is unchanged.
  - Ablation test: enabling/disabling artifact source does not change visible output.
- Production validation gates:
  - Shadow logs visible in Railway; visible cards unchanged; certification clean.
- Rollback: flag-gated.
- Size / model: medium; Sonnet.

### Phase 5 — Deterministic Policy Integration Behind Safe Flags

- Objective: allow specific, named axis hints from artifacts to enter `decide()` inputs through the truth adapter only, behind a per-axis flag, with full certification and per-axis kill switches.
- Allowed: per-axis flags such as `RESEARCH_ARTIFACT_AXIS_RISK_ENABLED`, `..._EVIDENCE_ENABLED`; conservative caps; certification additions.
- Forbidden: any LLM/agent setting `final_action` directly; any artifact bypassing the truth adapter; any cross-axis blanket integration; any unsigned schema change.
- Test gates:
  - Visible action distribution drift tests with explicit thresholds and a documented owner sign-off.
  - "Forbidden field cannot influence visible action" test — synthetic artifact with `final_action` field must be rejected and logged.
- Production validation gates:
  - Daily certification review; opt-in cohort if available; hard rollback runbook.
- Rollback: per-axis flag off → behavior reverts to Phase 0 baseline.
- Size / model: high; require Phase 1 spec, Phase 4 shadow diagnostics, and a focused review.

### Phase 6 — Plain-English Source Trail UI

- Objective: surface artifact-derived plain-English text and source links in `IntelV3Drawer` only after backend contracts are stable and §7 acceptance criteria pass repeatedly in production.
- Allowed: read-only display of sanitized artifact text; clickable source URL; "evidence freshness" badge; explicit "no evidence yet" empty state.
- Forbidden: any UI element that implies the artifact is the action authority; raw metric keys; broker-style fake precision.
- Test gates: snapshot tests; accessibility review; a11y for source links.
- Production validation gates: no regression in visible card distribution; certification clean; UI baseline preserved.
- Size / model: medium UI PR; Sonnet.

### Later — Deploy Integration

- Only after Intel v3 policy and artifact contracts have been certified in production for an extended period. Specifically out of scope of all phases above.

---

## 7. Acceptance Criteria for All Future Implementation PRs

Every future PR derived from this roadmap must satisfy ALL of the following gates, restated in the PR description:

1. No agent/LLM has final action authority. `decide()` remains the sole producer of visible Buy/Hold/Trim/Sell.
2. No page-load LLM calls. `intel_v3_snapshot_response_summary attempted_llm_calls=0`, `page_load_llm_calls=0` remain truthful.
3. No visible behavior drift unless explicitly scoped and flag-gated. The PR must declare flags and the cohort.
4. No legacy `recommendation_engine` aggregation re-coupling. Static guards in `test_intel_v3_phase0_5_regression_guardrail.py` stay green.
5. No raw metric-key UI. `raw_metric_key_count == 0` in certification.
6. No fake precision. `_FAKE_PRECISION_RE` checks remain enforced.
7. No unsourced claims. Every artifact has at least one `source_refs` entry; otherwise it is `LOW` confidence and excluded from any consumption.
8. Replayable / auditable artifact outputs. `replay_idempotency_key` collapses re-runs.
9. Idempotent worker behavior. Re-running on the same input must not duplicate artifacts.
10. Freshness/staleness handled explicitly per skill pack.
11. Tests prove forbidden artifact fields cannot influence visible action directly.
12. `intel_v3_snapshot_certification_summary` stays clean: `hard_violations=0`, `posture_label_count=0`, `action_conflict_count=0`, `repeated_skeleton_count=0`, `ticker_prefix_only_reason_count=0`, `weak_buy_rationale_count=0`.

A PR that cannot make every claim above truthfully must be split or descoped.

---

## 8. Risk Register

| # | Risk | Mitigation |
|---|---|---|
| 1 | Agent authority creep — a worker quietly starts writing visible action fields. | Forbidden-field deny-list at artifact write; static-source guard in tests; certification must remain clean; per-axis flags only. |
| 2 | Provider cost explosions — per-run cost grows uncapped. | Per-skill-pack cost/timeout caps; flag-gated rollout; cost recorded in `worker_audit_event`; budget review per phase. |
| 3 | Hallucinated or weakly sourced claims. | Required `source_refs` for any `extracted_facts`; LOW confidence by default; truth adapter ignores LOW where material; "no quote → no claim" test. |
| 4 | Stale evidence used silently. | Per-skill-pack freshness rule; `freshness_status` field; truth adapter treats STALE as not-safe-for-decision unless explicitly allowed. |
| 5 | Duplicate / conflicting artifacts. | `replay_idempotency_key`; conflict resolution rule (newest wins within source_window) recorded in audit log. |
| 6 | Hidden coupling to legacy `recommendation_engine`. | Static-source guards continue to forbid `get_insight_cards`/`recommendation_engine` references in v3 path. |
| 7 | Schema drift. | `artifact_schema_version` mandatory; migrations are additive; certification fields versioned. |
| 8 | UI overexposure of internal diagnostics. | UI reads only sanitized artifact text; raw metric keys forbidden; certification detectors enforce. |
| 9 | Slow runtime / page-load paths. | Workers are off-path and async; visible path remains snapshot read only; page-load LLM call count must remain zero. |
| 10 | Non-replayable LLM outputs. | Idempotency key + audit log + cached input/output digests; deterministic prompt + tool versioning. |
| 11 | Overengineering before v3 policy is stable. | Phases gated on prior phase certification; this PR is docs-only. |
| 12 | Implicit final-conviction smuggling via `confidence_or_trust_level`. | `confidence_or_trust_level` is artifact trust, not decision conviction; truth adapter must not use it as conviction. |

---

## 9. Implementation-Prompt Templates for Later

Short, safe future prompt skeletons. Each is a starting point only and must be re-scoped before use.

### 9.1 Phase 2 — Artifact Store Planning + SQL Proposal Prompt

> You are acting as a senior data architect for the Finance Tracker Intel v3 system. Phase 1 spec at `docs/ai/INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md` is merged. Produce a Phase 2 PLANNING + DRAFT SQL PR that proposes (does not apply) the `research_artifacts` and supporting tables. Forbidden fields: `final_action`, `final_conviction`, `final_allocation`, `deploy_amount` and synonyms — write a check that rejects these at write time. Do not modify `decide()`, the v3 service, the read-only evidence adapter, or any UI. Update HANDOFF.md and progress_log.md. State Supabase SQL: Yes (draft only).

### 9.2 Phase 3 — Single-Worker Dark-Run Scaffold Prompt

> You are acting as a senior backend engineer for Finance Tracker Intel v3. Phase 1 spec and Phase 2 artifact store are merged. Implement exactly one narrow worker (default: Earnings Reviewer) running off-path. Allowed: a flag-gated worker entrypoint, artifact writes only, audit log, golden-fixture replay test, idempotency test, "no source → no claim" test. Forbidden: any change to `decide()`, `IntelV3Service`, `ReadOnlyEvidenceAdapter`, certification detectors, or visible UI. Add no page-load LLM call. Verify `intel_v3_snapshot_certification_summary` remains unchanged. Provide a kill-switch flag and a runbook. State Supabase SQL: No (only existing Phase 2 tables).

### 9.3 Phase 4 — Artifact-to-Shadow-Diagnostics Integration Prompt

> You are acting as a senior backend engineer for Finance Tracker Intel v3. Implement shadow ingestion of Phase 3 artifacts into a new diagnostic adapter that emits log-only counters in `intel_v3_snapshot_certification_summary`. The visible Buy/Hold/Trim/Sell distribution must be unchanged for any artifact input. Add a property test enforcing this. Forbidden: any change to `decide()` outputs visible to users; any UI surface change. Provide a flag and rollback. State Supabase SQL: No.

### 9.4 Merge-Gate / Certification Audit Prompt (reusable)

> You are acting as a merge-gate reviewer for an Intel v3 PR. Verify each acceptance criterion in §7 of `docs/ai/INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md`. Confirm: no LLM final action authority; zero page-load LLM calls; no visible behavior drift unless declared and flag-gated; no legacy aggregation re-coupling; certification clean; forbidden artifact fields rejected; idempotent worker behavior; freshness handled. Produce a PASS / FAIL report with file/line citations.

---

## 10. Out of Scope (this PR)

- Any runtime backend or frontend code change.
- Any SQL or migration change.
- Any provider or LLM integration.
- Any change to certification detectors or test thresholds.
- Any redesign of UI, Deploy integration, or card copy.
- Any change to the deterministic decision policy.

If any of the above is required to make this spec actionable, the answer is to split into another PR, not to widen this one.
