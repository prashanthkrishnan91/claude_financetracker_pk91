
## 2026-05-06 Intel v3 backend coupling fix
- Root cause: POST /api/v1/intel/v3/run invoked RecommendationService.get_insight_cards -> recommendation_engine._compute_insight_cards, which emitted recommendations.aggregate.start and legacy v2 intel_response_certification_summary (page_load path).
- Fix: run_v3 now uses read-only persisted evidence adapter (`read_only_evidence_adapter.py`) and logs `intel_v3_evidence_source_summary` with `generated_legacy_recommendations=false`, `attempted_llm_calls=0`.
- Validation checklist: ensure no `recommendations.aggregate.start` log during v3 run; no v2 page_load certification summary; v3 certification has zero hard violations and copy-quality counts at zero.
