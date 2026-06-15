# v2 Progress Log

> **Convention.** Keep only:
> - current active phase,
> - latest merged PRs (last 3–5),
> - next step,
> - unresolved risks,
> - durable architecture decisions worth replaying in future prompts.
>
> Move or delete stale raw dumps. Target size **~150–250 lines**; only expand temporarily for a major release.
> Never append PR-by-PR forever. Replace or summarize older entries when this file grows.
> Long historical detail belongs in `docs/product/DECISION_LOG.md` or `docs/ai/MISS_LEDGER.md`, not here.

---

## Current active phase

- **Roadmap stage:** COST GUARD EMERGENCY — background worker RAM + snapshot storage incident (2026-06-15). Workers disabled. PR in review.
- **Next after cost guard merges:** Stage 10C VTI-only DCA benchmark (read-only, deterministic). Then Stage 2.3 exact-dollar math.
- **North-star reminder:** Intel → Deploy → Watchtower; deterministic backend Intel v3 policy owns visible Buy/Hold/Trim/Sell authority.

### 2026-06-15: Cost Guard Emergency PR

**Incident:** Supabase storage exceeded 0.5 GB free tier (intel_v3_snapshots ~497.9 MB, market_snapshots ~18.1 MB). Railway cost ~$18 mid-cycle from always-on worker RAM. Emergency SQL cleanup reduced DB to ~0.04 GB.

**Root cause:** Watchtower + analyst + email workers ran with 60s polling with no cost gate. Snapshot writes were unbounded. No master kill switch existed.

**Changes in this PR (no UI, no provider, no investment logic):**
- `INTEL_BACKGROUND_WORKERS_ENABLED=false` master kill switch in all worker entrypoints.
- `INTEL_V3_SNAPSHOT_WRITES_ENABLED=false` write guard in `_persist_snapshot`.
- Interval clamping: Watchtower ≥6h, Analyst ≥12h, Email ≥24h (bypass: `COST_GUARD_ALLOW_AGGRESSIVE_POLLING=true`).
- `v2/database/cost_guard_retention_cleanup.sql` — bounded 7-day DELETE for generated tables.
- `scripts/cost_guard_health.py` — diagnostic flag/interval/count report.
- 17 tests in `test_cost_guard.py`.
- `docs/deploy/RAILWAY_COST_GUARD.md`.

**Current Railway env (emergency, already set):**
```
INTEL_BACKGROUND_WORKERS_ENABLED=false
INTEL_V3_WATCHTOWER_ENABLED=false
INTEL_V3_RESEARCH_WORKERS_ENABLED=false
ALERT_EMAIL_DELIVERY_ENABLED=false
INTEL_V3_SNAPSHOT_WRITES_ENABLED=false
```
- **Source of truth:** `docs/product/ROADMAP.md`, `docs/product/BUILD_QUEUE.md`, `docs/product/NORTH_STAR.md`, `docs/ai/HANDOFF.md`.

## Latest merged PRs

- 2026-05-11 — Stage 2.2: Deploy policy + target-allocation readiness bridge. Added `deploy_target_allocation_bridge.py` (certify_target_allocation, build_certified_target_allocations: explicit-source only, rejects placeholder labels) and `deploy_policy_bridge.py` (certify_sizing_policy, build_policy_from_config: WHOLE_DOLLAR/NEAREST_DOLLAR/NO_ROUNDING, non-negative min_trade). Bridges into DeploySizingInputBundle; production path stays exact_dollar_ready=False; synthetic certified path unlocks exact_dollar_ready=True (readiness gate only). 48 new + 192 prior = 240 passed / 0 failed. No SQL, no UI, no routes.
- 2026-05-11 — Stage 2.1: Deploy sizing input contract. Added `deploy_sizing_contracts.py` and `deploy_sizing_builder.py`. Trust/suppression model; three readiness gates; policy UNSUPPORTED, target NOT_EVALUATED placeholders. 143 pass.
- 2026-05-11 — Stage 2.0: Deploy Foundation v1. New backend-only domain seam (`app/services/deploy/`). BUY/TRIM/SELL scaffold; HOLD never actionable; dollar fields null. 74 tests pass.
- 2026-05-10 — Final test-suite cleanup: backend full-suite stabilized at 3,926 passed / 0 failed. See HANDOFF for details.
- 2026-05-10 — Repo cleanup: removed legacy Streamlit v1 app (`v1/`, root `App.py`, `requirements.txt`, `.streamlit/`, `.devcontainer/`), removed obsolete v2 `/api/v1/positions/seed-v1` endpoint and `migration_service.py`, compressed progress logs, and added `docs/ai/REPO_HYGIENE.md` + `scripts/repo_hygiene/audit_repo_hygiene.py`. v2 is now the only active product.
- 2026-05-10 — Intel v3 Living Cockpit Status Reconciliation + Intel v4 Upgrade Path docs (`artifacts/Intel_v3_Living_Cockpit_Status_Reconciliation_and_Intel_v4_Upgrade_Path.md`). Defines the Unified Intelligence Spine; absorbs/defers/rejects external tool references; preserves deterministic decision authority.
- 2026-05-07 — Phase 7A: SEC CompanyFacts Financial Evidence v1. Earnings Reviewer artifacts now carry source-linked XBRL metric observations. Still no artifact consumption; `safe_for_decision=False`.
- 2026-05-07 — Phase 6B: SEC Production Validation + Readiness Observability. Phase 6A SEC-backed artifacts pass `eligible_for_truth_adapter=True` while `eligible_for_decision_consumption=False` always.

## Durable architecture decisions

- Visible Buy/Hold/Trim/Sell authority is owned by the deterministic Intel v3 policy. LLMs, agents, and research workers may produce sourced artifacts but never own final visible action authority.
- Research artifacts (Phase 2–7A) remain backend-only. `safe_for_decision` is DB-hard-locked False; `eligible_for_decision_consumption` is always False until a future explicit promotion gate.
- Truth Adapter Readiness Contract (Phase 5, 12 conditions) gates any future artifact consumption. See `docs/ai/INTEL_V3_TRUTH_ADAPTER_READINESS_CONTRACT.md`.
- Unified Intelligence Spine: Ingestion → Source Governance → Research Artifacts → Eval & Replay → Deterministic Policy → Deploy → Watchtower → Certified-Snapshot UI. Every external tool must land on a stage of this spine or be rejected.
- Missing/stale/weak/conflicting evidence suppresses; never fabricates. UI stays plain-English.
- All money values use `NUMERIC(18,6)`. SHA-256 canonical fingerprints for transaction dedup.
- AES-256-GCM for at-rest API key storage; JWT (Supabase Auth) for request auth.

## Next recommended step

Stage 2.2 — Deploy exact-dollar math: implement recommended_dollar_amount and estimated_share_quantity in DeployPlanItem using the certified DeploySizingInputBundle seam. Gate on exact_dollar_ready=True before computing amounts. Use the Deploy/Watchtower Boundary Pack + Deterministic Decision Authority Pack.

## Unresolved risks

- Exact-dollar math not yet implemented — dollar fields remain None. Sizing seam is now certified and typed (Stage 2.1); math is next.
- Target allocation logic is NOT_EVALUATED placeholder — no optimizer exists.
- Minimum-trade and rounding policy are UNSUPPORTED placeholders — future stage.
- Watchtower trigger model is scoped but unbuilt; no live alerts.
- Research artifact UX is intentionally deferred until decision/action loop is stable.

## Compaction note

Older PR-by-PR detail (Intel v2 / Intel v3 PRs 3–13, Phases 0.5–6A, runtime-certification fixes, narrative-contract Sev-1 fix, deploy UI tweaks, v2.0.0 + v2.1.0 phase reports) was compressed on 2026-05-10 into this current-state log. Durable decisions live above; durable workflow lessons live in `docs/ai/MISS_LEDGER.md`; durable product decisions live in `docs/product/DECISION_LOG.md`. The earlier `progress_log_archive.md` was removed during the same cleanup.
