# Finance Tracker — North Star

One-page product constitution. Keep current. If this drifts, fix here first.

## Mission

Finance Tracker is a deterministic personal investment cockpit.

Product flow:

```
Intel → Deploy → Watchtower
```

- **Intel** produces deterministic, auditable Buy/Hold/Trim/Sell decisions.
- **Deploy** converts certified decisions into exact-dollar action plans.
- **Watchtower** monitors meaningful changes and sends rare actionable alerts later.

## Non-negotiables

- Deterministic backend policy owns final visible Buy/Hold/Trim/Sell authority.
- LLMs, agents, and research artifacts may support research, but must never own final visible action authority.
- The UI must stay plain-English and useful for an amateur investor: no raw metric keys, diagnostics, shadow labels, posture labels, or advanced jargon leakage.
- No auto-trading.
- Missing / stale / weak data suppresses affected axes; it must not fabricate evidence.

## Source-of-truth references

- `artifacts/Intel_v3_Architecture_Plan_Draft2_Anthropic_Finance_Agent_Addendum.md` — Intel architecture plan.
- `artifacts/Intel_v3_Architecture_Plan_Draft3_Living_Investment_Cockpit_Addendum.md` — Living investment cockpit addendum.
- `artifacts/Intel_v3_Living_Cockpit_Status_Reconciliation_and_Intel_v4_Upgrade_Path.md` — status reconciliation and v4 upgrade path.
- `docs/ai/HANDOFF.md` — current state.

## What this means in practice

- Any product slice that does not move Intel, Deploy, or Watchtower forward is out of scope unless it unblocks the spine.
- Visible decisions are owned by deterministic policy, not LLMs/agents.
- Premium polish is real, but it waits until the decision/action loop is stable.

## Out of bounds

See `docs/product/DO_NOT_BUILD_YET.md`.
