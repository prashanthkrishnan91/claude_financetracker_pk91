"""Phase 3/3.5/4/5 research workers — dark-run, off by default.

Public surface:
    run_earnings_reviewer_dark       — env-gated callable for the Earnings Reviewer scaffold.
    run_validation                   — Phase 3.5 explicit validation harness.
    summarize_recent_research_artifacts — Phase 4 read-only observability aggregate.
    evaluate_artifact_truth_readiness   — Phase 5 pure readiness contract evaluator.
    ArtifactReadinessResult             — Phase 5 readiness result dataclass.

Kill switches for workers (both must be True to run):
    INTEL_V3_RESEARCH_WORKERS_ENABLED=true   (global)
    INTEL_V3_EARNINGS_REVIEWER_ENABLED=true  (per-worker)

Kill switch for validation harness (all three must be True):
    INTEL_V3_RESEARCH_WORKER_VALIDATION_ENABLED=true  (Phase 3.5 gate)
    INTEL_V3_RESEARCH_WORKERS_ENABLED=true             (Phase 3 global)
    INTEL_V3_EARNINGS_REVIEWER_ENABLED=true            (Phase 3 per-worker)

Kill switch for Phase 4 observability (independent):
    INTEL_V3_RESEARCH_ARTIFACT_OBSERVABILITY_ENABLED=true

Phase 5 truth adapter readiness contract:
    evaluate_artifact_truth_readiness() is a pure function — no kill switch needed.
    eligible_for_decision_consumption is always False in Phase 5.

Architecture constraints:
    - Workers NEVER call decide() from decision_policy_v1.
    - Workers NEVER write to intel_v3_snapshots.
    - safe_for_decision is always False.
    - No page-load execution.
    - Validation harness is explicit-invocation only.
    - Observability service is read-only and explicit-invocation only.
    - Phase 5 readiness evaluator is pure/read-only/fail-closed.
"""
from .artifact_observability import ArtifactObservabilitySummary, summarize_recent_research_artifacts
from .artifact_truth_readiness import ArtifactReadinessResult, evaluate_artifact_truth_readiness
from .runner import run_earnings_reviewer_dark
from .validation_harness import ValidationSummary, run_validation

__all__ = [
    "run_earnings_reviewer_dark",
    "run_validation",
    "ValidationSummary",
    "summarize_recent_research_artifacts",
    "ArtifactObservabilitySummary",
    "evaluate_artifact_truth_readiness",
    "ArtifactReadinessResult",
]
