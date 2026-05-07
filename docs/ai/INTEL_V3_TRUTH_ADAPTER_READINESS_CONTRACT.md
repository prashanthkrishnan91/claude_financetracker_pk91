# Intel v3 Truth Adapter Readiness Contract — Phase 5

**Status:** Phase 5 — contract defined, evaluation only, no artifact consumption.  
**Date:** 2026-05-07  
**Invariants:** safe_for_decision remains DB-hard-locked false. No visible decision drift.

---

## What Phase 5 Does

Defines and implements a backend-only readiness contract that evaluates whether a research artifact would be structurally eligible for future deterministic consumption by the Intel v3 policy (the "truth adapter" path).

**Phase 5 does NOT:**
- Consume artifacts into the decision path.
- Import artifacts into `decision_policy_v1.py`.
- Modify `decide()` or `IntelV3Service`.
- Set `safe_for_decision=True` anywhere.
- Change any visible snapshot, action, copy, or UI.
- Require any SQL migration.
- Add any external provider, LLM call, or agent.

---

## Current Production Artifact Status (Phase 4)

All current production artifacts are **excluded** by this contract:

| Field | Current value | Contract requirement | Verdict |
|---|---|---|---|
| `confidence_or_trust_level` | `UNKNOWN` | `HIGH`, `MEDIUM`, or `LOW` | FAIL |
| `freshness_status` | `UNKNOWN` | `FRESH` or `STALE` | FAIL |
| source count | 0 | ≥1 valid source | FAIL |
| `missing_evidence_count` | 3 | — | informational |
| `safe_for_decision` | `false` | never `true` | PASS (invariant) |

These artifacts produce `eligible_for_truth_adapter=False` with reason codes:
`unknown_or_invalid_confidence`, `unknown_or_invalid_freshness`, `no_valid_sources`.

---

## Readiness Contract — 12 Conditions

Every condition must pass. Any failure → ineligible (fail-closed).

| # | Condition | Fail code |
|---|---|---|
| 1 | `is_active=True` and `invalidated_at IS NULL` | `not_active`, `invalidated` |
| 2 | Not expired: `expires_at IS NULL OR expires_at >= now()` | `expired`, `expires_at_malformed` |
| 3 | `artifact_type` in supported registry | `unsupported_artifact_type` |
| 3 | `skill_pack` in supported registry | `unsupported_skill_pack` |
| 4 | `confidence_or_trust_level` ∈ {HIGH, MEDIUM, LOW} | `unknown_or_invalid_confidence` |
| 5 | `freshness_status` ∈ {FRESH, STALE} | `unknown_or_invalid_freshness` |
| 6 | ≥1 valid source (non-empty `source_kind` + `provider_name`) | `no_valid_sources` |
| 7 | ≥1 valid fact (non-empty `fact_kind` + `structured_payload` dict) | `no_valid_facts` |
| 8 | Facts with `source_id` set must match a valid source | `fact_source_not_found` |
| 9/10 | Payload and fact payloads contain no forbidden decision-authority keys | `forbidden_payload_key=*`, `forbidden_fact_payload_key=*` |
| 11 | `safe_for_decision=True` is NOT required/checked — DB hard-locks it false | (invariant) |
| 12 | Any malformed/null/ambiguous field makes artifact ineligible | multiple |

### Supported Registries (Phase 5)

```python
SUPPORTED_ARTIFACT_TYPES = {"catalyst_window"}
SUPPORTED_SKILL_PACKS    = {"earnings_reviewer"}
```

These grow only when a new worker is implemented and certified.

---

## Readiness Result Fields

| Field | Phase 5 value |
|---|---|
| `eligible_for_truth_adapter` | True only when all 12 conditions pass |
| `eligible_for_decision_consumption` | **Always False** — DB promotion not enabled |
| `fail_closed` | **Always True** |
| `safe_for_decision_db_promotion_blocked` | **Always True** |
| `reason_codes` | List of fail codes; empty when eligible |
| `source_count` | Count of valid sources |
| `fact_count` | Count of valid facts |
| `confidence_or_trust_level` | Normalized string or None |
| `freshness_status` | Normalized string or None |
| `forbidden_payload_violation` | True if any forbidden key found |

---

## Prerequisite Gate for Future Consumption (Phase 6+)

Before any artifact can influence visible Intel v3 decisions, ALL of the following must be satisfied:

1. **Provider-backed sources** — real external providers (SEC EDGAR, earnings APIs, news feeds) must produce at least one valid source per artifact.
2. **Grounded facts** — facts must be traceable to sources with explicit source linkage.
3. **Confidence/trust calibration** — workers must classify `confidence_or_trust_level` as HIGH, MEDIUM, or LOW based on source quality and coverage.
4. **Freshness classification** — workers must classify `freshness_status` as FRESH or STALE based on explicit time windows.
5. **DB migration explicitly allowing `safe_for_decision=True`** — the Phase 2.1 CHECK constraint `research_artifacts_safe_for_decision_phase2_chk CHECK (safe_for_decision = FALSE)` must be explicitly dropped or replaced by a migration approved and applied after review. This requires explicit operator approval, a new migration file under `v2/database/`, and production runbook.
6. **Deterministic adapter mapping** — a new "truth adapter" module must map validated artifact facts into the allowed evidence fields consumed by `decide()` in `decision_policy_v1.py`. This module must be reviewed and certified before enabling.
7. **Certification proving visible decision stability** — a full certification run must prove that consuming artifacts either produces no visible snapshot change (shadow mode) or produces only intended, auditable, deterministic changes with explicit approval.

---

## LLM/Agent Authority Boundary (Permanent)

**LLMs, agents, and research workers never own final Buy/Hold/Trim/Sell authority.**

- `decide()` in `decision_policy_v1.py` is the sole visible decision authority.
- Research workers produce sourced artifacts with evidence observations only.
- Workers must never produce payload keys: `final_action`, `buy`, `sell`, `trim`, `hold`, `final_conviction`, `final_allocation`, `deploy_amount`, `deploy_dollar`, `deploy_shares`, `action`, `recommendation`, `target_price`, `allocation`.
- Artifacts influence visible decisions only via the deterministic truth adapter (Phase 6+), never directly.

---

## Implementation Location

```
v2/backend/app/services/intelligence/research_workers/artifact_truth_readiness.py
```

**Module properties:**
- Pure function — no DB calls, no external calls, no side effects.
- Deterministic — same inputs always produce same output.
- Fail-closed — any exception triggers ineligible result, never raises.
- No page-load execution — explicit invocation only.
- Does not import `decision_policy_v1`, `IntelV3Service`, or any frontend code.

**Tests:**
```
v2/backend/tests/test_intel_v3_phase5_truth_adapter_readiness.py
```
