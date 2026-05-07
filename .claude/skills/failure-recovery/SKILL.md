# Failure Recovery Skill

Use after a failed patch, failed PR review, failed runtime validation, failed SQL check, or UI regression.

Return:
- what failed
- whether this is first failure or repeated failure
- updated severity classification
- root cause hypothesis
- missing test/evidence that would have caught it
- recommended next move: small follow-up, full plumbing analysis, or split plan

Finance rules:
- after one failed patch, reclassify
- after two related patches, stop patching and move to full plumbing analysis or split plan
- if visible decisions are wrong, debug visible decision plumbing first, not shadow diagnostics
- do not patch UI when backend/API contract is wrong
- do not claim SQL/runtime success without SQL sanity or runtime evidence
