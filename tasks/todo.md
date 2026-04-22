# AI Pipeline Fix — Context Builder + Single LLM Call

## Goal
Enforce: DB → Context Builder → Single LLM Call → Persist Results

## Plan
1. Create `v2/backend/app/services/ai/__init__.py` + `context_builder.py`
   - `build_portfolio_context(user_id) -> dict` with `portfolio`, `macro`, `insights`
   - Pure data aggregation — NO LLM calls
2. Refactor `v2/backend/app/services/agents/orchestrator.py`
   - Remove per-ticker fan-out (sentiment/technical/fundamental LLM calls)
   - Single Claude call guarded by a module-level `LLM_SEMAPHORE`
   - Early return with `status="no_data"` when portfolio is empty
   - Persist agent_insights + recommendations from the single response
3. Keep downstream schema stable (agent_insights, recommendations, portfolio_advice)
4. Verify tests still pass (`test_agent_pipeline_hardening.py`, etc.)
