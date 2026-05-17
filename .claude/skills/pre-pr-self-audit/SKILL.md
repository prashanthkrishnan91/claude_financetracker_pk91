# Pre-PR Self-Audit Skill

Use before opening or updating a PR.

Return:
- assumptions and success criteria
- every success criterion mapped to file/function/test/evidence
- contract/runtime/SQL/claim-data gates run or rationale
- limitations and out-of-scope
- manual actions checklist
- HANDOFF/README update decision

Checklist (run before first push):
- [ ] USAGE_LEDGER row committed in the same commit as code (Level 1+ PRs require this — "unavailable" in PR body does not waive it)
- [ ] PR body includes all required template sections: Summary, Severity, Validation, SQL / env / providers / UI, AI usage note, AI PR readiness
- [ ] `python3 scripts/workflow/ai_pr_readiness_check.py --base-ref origin/main` exits 0

Fail the self-audit if:
- downstream consumers are not checked for changed contracts
- runtime/SQL gate is skipped for snapshot, DB, env, provider, or worker changes
- claim/data-safety gate is skipped for visible decisions, text, data, or evidence changes
- PR summary would overclaim evidence not actually proven
