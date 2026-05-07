# PR Review Checklist — Finance Tracker

Use this checklist before merge and inside `/pre-pr-self-audit`.

## Required checks

- Severity classification: Level 0/1/2/3 with reason.
- Root cause vs symptom: explain why the fix addresses the cause.
- Downstream contract audit: changed outputs, consumers, behavior changes, files intentionally not changed.
- Test coverage audit: smallest sufficient suite plus one adversarial invariant test or rationale.
- Runtime/data audit: required for providers, LLM calls, workers, DB calls, snapshot endpoints, caches, or route behavior.
- Data trust / claim safety audit: deterministic source, evidence sufficiency, unavailable-data honesty.
- Decision authority audit: visible Buy/Hold/Trim/Sell authority remains deterministic backend policy.
- UI leakage audit: no raw metrics, metric keys, diagnostics, shadow labels, posture labels, or jargon in visible UI.
- SQL/migration audit: Supabase SQL yes/no and manual action yes/no.
- Env var audit: new/changed env vars yes/no; default and rollback behavior.
- Feature flag/rollback audit: flag or safe rollback path for risky changes.
- PR summary accuracy: do not overclaim; list tests actually run and known limitations.

## Do not merge if

- LLMs/agents/research artifacts can own final visible action authority.
- Raw diagnostics, metric keys, internal labels, or jargon can leak to UI.
- Visible decision behavior changes without deterministic policy/snapshot tests.
- SQL/env/provider/LLM/runtime impact is hidden or ambiguous.
- Missing/stale/weak data fabricates evidence instead of suppressing affected axes.
- A shared contract changed without consumer tests or explicit rationale.
- The implementation is a symptom patch after repeated related failures.
