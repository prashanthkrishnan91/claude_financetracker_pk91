# Pre-PR Self-Audit Skill

Use before opening or updating a PR.

Return:
- assumptions and success criteria
- every success criterion mapped to file/function/test/evidence
- contract/runtime/SQL/claim-data gates run or rationale
- limitations and out-of-scope
- manual actions checklist
- HANDOFF/README update decision

Fail the self-audit if:
- downstream consumers are not checked for changed contracts
- runtime/SQL gate is skipped for snapshot, DB, env, provider, or worker changes
- claim/data-safety gate is skipped for visible decisions, text, data, or evidence changes
- PR summary would overclaim evidence not actually proven
