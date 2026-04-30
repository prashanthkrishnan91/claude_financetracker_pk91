# AI Usage Ledger — Investing App

Purpose: calibrate future ChatGPT → Claude/Codex prompt estimates using observed usage, not optimism.

Keep this short. Record only meaningful prompts or surprising cost events.

| Date | Work type | Model | Expected usage | Actual usage | Extra cost | Files/PR | Lesson |
|---|---|---:|---:|---:|---:|---|---|
| 2026-04-29 | UI foundation PR | Sonnet | 8–18% estimated | ~23% before merge / ~29% lifecycle | unknown | PR #128, 8 changed files | 8-file UI foundation is Medium-High, not Low-Medium. Include pre-merge + post-merge estimate. |

## Current calibration

- Codex small bug/audit: Low expected usage.
- Sonnet focused implementation with 1–3 primary files: Medium, roughly 6–15% session.
- Sonnet UI foundation with 8 files: Medium-High, roughly 20–30% lifecycle.
- Sonnet UI foundation with 10+ files: High, can approach 40–55% lifecycle.
- Post-PR Sonnet continuation can add 5–10%; stop the Claude chat after PR.

## Required fields for future entries

- Prompt type
- Model
- Predicted usage
- Actual before/after session %
- Extra cost if shown
- Files changed / PR number
- What caused overrun or savings
