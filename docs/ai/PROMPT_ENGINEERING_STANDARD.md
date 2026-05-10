# Prompt Engineering Standard — Finance

## Core principle

Every meaningful prompt must be a work order, not a vague request.

A good prompt contains:

- task type
- product roadmap stage
- build queue item
- source-of-truth files to read
- objective
- feature/product contract
- success criteria
- golden scenarios / evals
- scope boundaries
- required OS skills
- required reviewer agents
- validation expectations
- tool-failure behavior
- PR summary requirements
- stop condition

## Prompt structure

Use this structure for implementation prompts:

```
<task_context>
Repo:
Roadmap stage:
Build queue item:
Source-of-truth files:
Why this matters:
</task_context>

<objective>
One clear outcome.
</objective>

<feature_contract>
User outcome:
Backend/API contract:
Frontend/UI contract:
Data/persistence contract:
Trust/safety invariant:
Performance/latency expectation:
Out of scope:
</feature_contract>

<success_criteria>
- observable result
- invariant preserved
- evidence expected
</success_criteria>

<golden_scenarios>
List 3-7 scenarios that must work or remain unchanged.
</golden_scenarios>

<constraints>
Non-negotiables and scope boundaries.
</constraints>

<required_process>
OS skills, reviewer agents, roadmap check, workflow retrospective.
</required_process>

<validation>
Tests/checks/runtime/UI/SQL/deployment expectations.
</validation>

<tool_failure_policy>
If a command/tool/test/log check fails, classify it as:
- app bug
- test issue
- environment/tooling issue
- insufficient access
- expected limitation
Then state evidence and safest next step.
</tool_failure_policy>

<final_output>
PR summary fields and stop condition.
</final_output>
```

## Prompt lint checklist

Before giving PK a prompt, verify:

- Is there one primary objective?
- Is the roadmap stage / build queue item named?
- Are source-of-truth files explicit?
- Is the feature contract clear?
- Are golden scenarios included for Level 2+ implementation?
- Are scope boundaries strong enough?
- Are required agents relevant, not excessive?
- Is validation specific?
- Is the stop condition explicit?
- Does the prompt avoid old bulky guardrail style?
- Does it avoid "do everything" ambiguity?
- Is it safe for blind copy/paste?

## When to use examples

Include examples only when they reduce ambiguity:

- expected JSON shape
- desired PR summary format
- before/after UI behavior
- accepted/rejected claim examples
- golden scenario examples

Do not include examples as filler.

## Coverage-first review prompts

For audits / reviews:

- First pass: list every plausible issue, even low confidence.
- Second pass: classify severity and confidence.
- Final pass: decide blockers vs non-blockers.

Do not ask reviewers to report only blockers at the start.

## Ask / Plan before Code

For Level 2/3 features:

- First produce or verify the feature contract and implementation plan.
- Then code the coherent slice.
- If the feature contract is unclear, stop and propose the split.

Do not let Claude code broad features from a vague idea.

## Finance-specific prompt invariants

- Always state "deterministic backend policy owns visible decisions" when decision/snapshot/action surfaces are touched.
- Always forbid LLM/agent/research final visible action authority.
- Always require plain-English UI and forbid raw metric keys / diagnostics / shadow labels / posture labels / advanced jargon.
- Always require honest suppression on missing/stale/weak data.
- Always state SQL/env/runtime certification expectations explicitly when persistence/snapshot work is in scope.
- Forbid auto-trading or broker-execution language as a default constraint.
