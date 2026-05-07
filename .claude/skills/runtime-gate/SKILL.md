# Runtime / SQL Gate Skill

Use when provider, LLM, worker, DB, cache, snapshot, SQL, env, or route behavior changes.

Return:
- new live calls, workers, DB calls, cache behavior, or route behavior
- snapshot/source-of-truth impact
- SQL/manual actions and sanity checks
- feature flag defaults and rollback
- runtime certification/log evidence needed yes/no and why
- Vercel/Railway redeploy needed yes/no and why

Finance-specific rule: fail if production-visible snapshot behavior, SQL persistence, or runtime certification is claimed without tests, SQL sanity, runtime evidence, logs, or explicit limitation.
