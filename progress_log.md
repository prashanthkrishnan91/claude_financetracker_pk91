
## 2026-05-06 Intel v3 backend coupling fix
- Root cause: POST /api/v1/intel/v3/run invoked RecommendationService.get_insight_cards -> recommendation_engine._compute_insight_cards, which emitted recommendations.aggregate.start and legacy v2 intel_response_certification_summary (page_load path).
- Fix: run_v3 now uses read-only persisted evidence adapter (`read_only_evidence_adapter.py`) and logs `intel_v3_evidence_source_summary` with `generated_legacy_recommendations=false`, `attempted_llm_calls=0`.
- Validation checklist: ensure no `recommendations.aggregate.start` log during v3 run; no v2 page_load certification summary; v3 certification has zero hard violations and copy-quality counts at zero.


## 2026-05-06 Post-PR-220 certification follow-up
- PR #220 successfully decoupled Intel v3 run path from legacy recommendation aggregation (read-only persisted evidence path confirmed in production).
- Remaining production blockers identified: `intel_v3.weight_map_failed` due to `positions.current_value` lookup against a non-existent column, plus non-zero rationale skeleton counters (`ticker_prefix_only_reason_count`, `repeated_skeleton_count`).
- Validation checklist for next production run:
  1. Confirm no `intel_v3.weight_map_failed` log lines.
  2. Confirm certification summary has `repeated_skeleton_count=0`, `ticker_prefix_only_reason_count=0`, `weak_buy_rationale_count=0`, `generic_copy_count=0`.
  3. Confirm `hard_violations=0`, `action_conflict_count=0`, `raw_metric_key_count=0`, `posture_label_count=0`.
  4. Confirm no `recommendations.aggregate.start` and no legacy `schema_version="v2"` page-load certification during v3 run window.
