Use `.claude/skills/ai-repo-os/SKILL.md` runtime gate.

Run when provider, LLM, worker, DB, cache, snapshot, SQL, env, or route behavior changes.

Return:
- new live calls/workers/db/cache behavior
- snapshot/source-of-truth impact
- SQL/manual actions
- flag defaults and rollback
- runtime certification/log evidence needed or not

Fail if production behavior is claimed without tests, SQL sanity, runtime cert evidence, logs, or explicit limitation.
