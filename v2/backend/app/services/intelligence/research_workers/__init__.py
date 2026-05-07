"""Phase 3/3.5 research workers — dark-run, off by default.

Public surface:
    run_earnings_reviewer_dark — env-gated callable for the Earnings Reviewer scaffold.
    run_validation             — Phase 3.5 explicit validation harness.

Kill switches for workers (both must be True to run):
    INTEL_V3_RESEARCH_WORKERS_ENABLED=true   (global)
    INTEL_V3_EARNINGS_REVIEWER_ENABLED=true  (per-worker)

Kill switch for validation harness (all three must be True):
    INTEL_V3_RESEARCH_WORKER_VALIDATION_ENABLED=true  (Phase 3.5 gate)
    INTEL_V3_RESEARCH_WORKERS_ENABLED=true             (Phase 3 global)
    INTEL_V3_EARNINGS_REVIEWER_ENABLED=true            (Phase 3 per-worker)

Architecture constraints:
    - Workers NEVER call decide() from decision_policy_v1.
    - Workers NEVER write to intel_v3_snapshots.
    - safe_for_decision is always False.
    - No page-load execution.
    - Validation harness is explicit-invocation only.
"""
from .runner import run_earnings_reviewer_dark
from .validation_harness import ValidationSummary, run_validation

__all__ = ["run_earnings_reviewer_dark", "run_validation", "ValidationSummary"]
