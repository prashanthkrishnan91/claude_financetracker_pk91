# Contract Audit Skill

Use before PR summary whenever outputs, payloads, routes, UI data, evidence adapters, persistence contracts, snapshot contracts, or shared functions change.

Return:
- changed outputs/contracts
- downstream consumers
- behavior changes
- files intentionally not changed
- tests or rationale proving safety

Finance-specific checks:
- deterministic Intel v3 backend policy remains final visible Buy/Hold/Trim/Sell authority
- snapshot endpoint and frontend source-of-truth stay aligned
- research artifacts/workers remain supporting evidence only
- UI does not receive raw diagnostics, raw metric keys, shadow labels, posture labels, or advanced jargon

Fail if downstream consumers are not checked.
