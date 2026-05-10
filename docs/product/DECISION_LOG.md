# Decision Log

Product decisions are recorded here so we do not re-litigate direction.

## Template

```
## YYYY-MM-DD — Decision title
- Decision:
- Why:
- Alternatives rejected:
- What would change our mind:
- Roadmap impact:
```

## Seed decisions

## 2026-05-10 — Deterministic backend policy owns visible decisions
- Decision: Final visible Buy/Hold/Trim/Sell decisions are owned by deterministic backend policy. LLMs, agents, and research artifacts are support-only and never own final visible action authority.
- Why: Trust and auditability are non-negotiable for an amateur-investor cockpit; LLM-driven visible decisions break that trust.
- Alternatives rejected: Allow LLM/research to override deterministic policy on edge cases.
- What would change our mind: A regulatory/oversight shift plus rigorous evaluation that makes LLM authority safer than deterministic policy.
- Roadmap impact: Anchors Stage 1 Intel certification; constrains Stage 2 Deploy and all later stages.

## 2026-05-10 — Product sequence is Intel → Deploy → Watchtower
- Decision: Build Intel correctness first, then Deploy action plans, then Watchtower triggers and alerts. Research UX and design polish wait.
- Why: Each stage depends on the previous one; reversing the order produces brittle UX and untrustworthy actions.
- Alternatives rejected: Build Watchtower or design polish in parallel before Deploy stabilizes.
- What would change our mind: A demonstrated product wedge that requires Watchtower or design polish ahead of Deploy.
- Roadmap impact: Defines stage ordering and gates in `docs/product/ROADMAP.md`.

## 2026-05-10 — Design polish waits until decision/action loop is stable
- Decision: Major design transformation only after Deploy / Watchtower loop is stable.
- Why: Design polish on top of unstable decisions and actions wastes design work and rots fast.
- Alternatives rejected: Concurrent design sprint during Stage 2 / Stage 3.
- What would change our mind: A clearly identified surface where design is the limiting factor for trust.
- Roadmap impact: Stage 6 is gated by Finance Design Polish Gate; design changes before then are capped UI fixes only.
