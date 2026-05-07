"""Phase 3 research workers — dark-run, off by default.

Public surface:
    run_earnings_reviewer_dark — env-gated callable for the Earnings Reviewer scaffold.

Kill switches (both must be True to run):
    INTEL_V3_RESEARCH_WORKERS_ENABLED=true   (global)
    INTEL_V3_EARNINGS_REVIEWER_ENABLED=true  (per-worker)

Architecture constraints:
    - Workers NEVER call decide() from decision_policy_v1.
    - Workers NEVER write to intel_v3_snapshots.
    - safe_for_decision is always False.
    - No page-load execution.
"""
from .runner import run_earnings_reviewer_dark

__all__ = ["run_earnings_reviewer_dark"]
