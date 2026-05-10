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

- **Roadmap stage:** Stage 1 → Stage 2 transition (Intel v3 correctness/evidence trust → Deploy exact-dollar action plans).
- **Active build queue item:** confirm/define the Deploy action-plan foundation.
- **North-star reminder:** Intel → Deploy → Watchtower; deterministic backend Intel v3 policy owns visible Buy/Hold/Trim/Sell authority.
- **Source of truth:** `docs/product/ROADMAP.md`, `docs/product/BUILD_QUEUE.md`, `docs/product/NORTH_STAR.md`, `docs/ai/HANDOFF.md`.

## Latest merged PRs

- 2026-05-10 — Repo cleanup: removed legacy Streamlit v1 app (`v1/`, root `App.py`, `requirements.txt`, `.streamlit/`, `.devcontainer/`), removed obsolete v2 `/api/v1/positions/seed-v1` endpoint and `migration_service.py`, compressed progress logs, and added `docs/ai/REPO_HYGIENE.md` + `scripts/repo_hygiene/audit_repo_hygiene.py`. v2 is now the only active product.
- 2026-05-10 — Intel v3 Living Cockpit Status Reconciliation + Intel v4 Upgrade Path docs (`artifacts/Intel_v3_Living_Cockpit_Status_Reconciliation_and_Intel_v4_Upgrade_Path.md`). Defines the Unified Intelligence Spine; absorbs/defers/rejects external tool references; preserves deterministic decision authority.
- 2026-05-07 — Phase 7A: SEC CompanyFacts Financial Evidence v1. Earnings Reviewer artifacts now carry source-linked XBRL metric observations. Still no artifact consumption; `safe_for_decision=False`.
- 2026-05-07 — Phase 6B: SEC Production Validation + Readiness Observability. Phase 6A SEC-backed artifacts pass `eligible_for_truth_adapter=True` while `eligible_for_decision_consumption=False` always.
- 2026-05-06 — Intel v3 Snapshot Spine + Premium Cockpit UI (Level 3 rebuild). Single deterministic snapshot becomes the visible authority for Buy/Hold/Trim/Sell.

## Durable architecture decisions

- Visible Buy/Hold/Trim/Sell authority is owned by the deterministic Intel v3 policy. LLMs, agents, and research workers may produce sourced artifacts but never own final visible action authority.
- Research artifacts (Phase 2–7A) remain backend-only. `safe_for_decision` is DB-hard-locked False; `eligible_for_decision_consumption` is always False until a future explicit promotion gate.
- Truth Adapter Readiness Contract (Phase 5, 12 conditions) gates any future artifact consumption. See `docs/ai/INTEL_V3_TRUTH_ADAPTER_READINESS_CONTRACT.md`.
- Unified Intelligence Spine: Ingestion → Source Governance → Research Artifacts → Eval & Replay → Deterministic Policy → Deploy → Watchtower → Certified-Snapshot UI. Every external tool must land on a stage of this spine or be rejected.
- Missing/stale/weak/conflicting evidence suppresses; never fabricates. UI stays plain-English.
- All money values use `NUMERIC(18,6)`. SHA-256 canonical fingerprints for transaction dedup.
- AES-256-GCM for at-rest API key storage; JWT (Supabase Auth) for request auth.

## Next recommended step

Clarify or implement the Deploy action-plan foundation slice (one capability slice). Use the OS v4 work-order shape and the Deterministic Decision Authority + Backend-only Scaffold packs (`docs/ai/SAFETY_PACKS_AND_ARCHETYPES.md`).

## Unresolved risks

- Stage 2 (Deploy) entry gate not yet declared formally — Deploy action-plan foundation is the next plumbing piece.
- Watchtower trigger model is scoped but unbuilt; no live alerts.
- Research artifact UX is intentionally deferred until decision/action loop is stable.

## Compaction note

Older PR-by-PR detail (Intel v2 / Intel v3 PRs 3–13, Phases 0.5–6A, runtime-certification fixes, narrative-contract Sev-1 fix, deploy UI tweaks, v2.0.0 + v2.1.0 phase reports) was compressed on 2026-05-10 into this current-state log. Durable decisions live above; durable workflow lessons live in `docs/ai/MISS_LEDGER.md`; durable product decisions live in `docs/product/DECISION_LOG.md`. The earlier `progress_log_archive.md` was removed during the same cleanup.
