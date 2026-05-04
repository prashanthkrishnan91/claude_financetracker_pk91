# Merge-Gate Audit: Intel v2 posture consistency

Date: 2026-05-04
Scope: Focused audit of posture consistency fix (page load vs Run Agents)

## Verdict
MERGE WITH NON-BLOCKING NOTES

## Files reviewed
- CLAUDE.md
- docs/ai/HANDOFF.md
- docs/ai/PROMPT_LIBRARY.md
- docs/ai/skills/merge_gate.md (closest relevant skill)
- v2/backend/app/services/recommendation_engine.py
- v2/backend/app/models/recommendation.py
- v2/frontend/src/app/dashboard/recommendations/page.tsx
- v2/frontend/src/components/cards/AgentInsightCard.tsx
- v2/backend/tests/test_intel_read_projection.py
- v2/backend/tests/test_reasoning_v2_plain_english.py

## Invariant check summary
1) **Same backend-owned posture contract:** PASS. Posture is derived in backend via `_derive_intel_posture` and returned as `intel_posture_label` / `intel_filter_bucket`.
2) **Fallback safety without stale veto:** PASS. `_build_intel_read_for_card` returns provenance; posture ignores fallback `insufficient_data` by using `None` when not primary.
3) **Pre-gate posture semantics only:** PASS. `_pre_gate_*` values feed posture, while display safety gate still downgrades card-facing action/conviction text.
4) **BUY + true current-run insufficient_data => Review:** PASS via Rule 5.5.
5) **Forbidden bullish language blocked on insufficient-data cards:** PASS in sanitizer + tests.
6) **Add Candidate requires current evidence contract:** PASS with one caveat below.
7) **Risk Watch / Trim Candidate override precedence:** PASS by rule ordering.
8) **Top filter counts use posture buckets:** PASS in recommendations page filter/count logic.
9) **Why this view remains coverage summary without raw metric keys:** PASS; `posture_reason` used as primary explanation and tests assert no raw keys.
10) **Business Read hidden:** PASS in frontend tests.
11) **Deploy/allocation math untouched:** PASS (no related file changes in scope).

## Required test coverage check
Required scenarios are covered by changed tests:
- page-load fallback `_reasoning_v2` does not collapse BUY-like posture into Watchlist
- Run Agents vs page-load consistency behavior
- BUY + true insufficient_data => Review
- stale fallback insufficient_data cannot veto current posture
- insufficient-data bullish language blocked
- raw metric keys not rendered
- filter counts use Intel posture buckets

## Non-blocking notes
- The fallback-ignore logic is durable for stale `insufficient_data`, but continued reliance on fallback for `posture_reason`/coverage text means page-load explanatory copy can still differ from fresh run copy. This is acceptable for now because posture contract is correct and deterministic.
- Tests use local logic mirrors for some recommendation engine functions; this is fast and focused, but any future drift risk should be watched.

## SQL / README
- SQL needed: No.
- README changed: No (behavioral fix internal to v2 posture contract; no public setup/runtime changes).
