# Claim / Data Safety Gate Skill

Use when user-visible text, cards, actions, decisions, evidence, or LLM-visible prose changes.

Return:
- visible/user-facing claims or actions affected
- deterministic/source evidence for each risky claim
- missing/stale/weak data suppression behavior
- authority boundary check for LLMs/agents/research artifacts
- raw metrics/diagnostics/internal-label leakage check

Finance-specific checks:
- deterministic Intel v3 policy remains final visible action authority
- LLMs, agents, and research artifacts remain supporting evidence only
- finance claims are deterministic, sourced, auditable, or honestly unavailable
- weak/missing/stale data suppresses affected axes instead of fabricating evidence
- raw metrics, metric keys, diagnostics, shadow labels, posture labels, and jargon cannot leak to UI

Fail if internal-only data can reach UI or if non-deterministic systems can own visible actions.
