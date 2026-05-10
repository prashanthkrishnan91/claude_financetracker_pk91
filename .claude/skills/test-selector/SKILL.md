# Test Selector Skill

Use before coding and before PR summary.

Read `docs/ai/TEST_ROUTING.md`, then return:
- changed areas
- smallest sufficient tests
- downstream consumer tests needed
- one adversarial test for the riskiest invariant, or rationale for no new test
- skipped tests and why

Finance rule: decision-policy changes require deterministic policy tests plus snapshot/source-of-truth tests. UI changes require plain-English/no-raw-diagnostics checks.
