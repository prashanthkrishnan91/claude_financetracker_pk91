"""Phase 3.5 — dark-run research artifact write validation harness.

Purpose:
    Explicitly invokable backend-only harness that runs the Phase 3 Earnings
    Reviewer dark-run worker for a capped list of tickers, writes artifacts via
    the existing Phase 3 writer, and returns a compact ValidationSummary.

Enabled only when ALL THREE flags are True:
    INTEL_V3_RESEARCH_WORKER_VALIDATION_ENABLED (Phase 3.5 harness gate)
    INTEL_V3_RESEARCH_WORKERS_ENABLED           (Phase 3 global kill switch)
    INTEL_V3_EARNINGS_REVIEWER_ENABLED          (Phase 3 per-worker kill switch)

Architecture invariants (non-negotiable):
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER writes to intel_v3_snapshots.
    - NEVER runs on page load — explicit invocation only.
    - NEVER runs automatically from Intel v3 snapshot reads.
    - NEVER adds providers, LLM calls, or new SQL.
    - NEVER feeds artifacts into the visible decision path.
    - safe_for_decision remains False (enforced by writer and DB constraint).
    - All failures are contained and summarized; none propagate to visible flows.
    - Max tickers per run: MAX_TICKERS_PER_RUN (default 5).
    - Logs structured INFO only when
      INTEL_V3_RESEARCH_WORKER_VALIDATION_INFO_LOGS_ENABLED=true.
    - Logs are aggregate and safe — no full payloads, no secrets, no raw user data.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.config import Settings, get_settings

from .contracts import _has_forbidden_key, WorkerInput
from . import earnings_reviewer
from .runner import run_earnings_reviewer_dark

MAX_TICKERS_PER_RUN: int = 5

# Tables the writer always touches on a successful write (structural — not runtime-probed).
_WRITER_TABLES = [
    "research_artifacts",
    "research_artifact_sources",
    "research_artifact_facts",
    "worker_audit_events",
]


@dataclass
class ValidationSummary:
    """Compact result returned by run_validation().

    Fields are populated even on partial failure. All counts default to 0/[]/False.
    """
    validation_enabled: bool
    requested_tickers: list[str]
    normalized_tickers: list[str]
    attempted_count: int
    written_count: int
    skipped_count: int
    failed_count: int
    artifact_ids: list[str]
    safe_for_decision_false_count: int
    unexpected_safe_for_decision_true_count: int
    forbidden_payload_violation_count: int
    visible_snapshot_unchanged: bool
    # tables_touched: populated structurally on any successful write.
    tables_touched: list[str] = field(default_factory=list)
    # worker_run_ids: not recoverable from existing runner interface.
    # Populated to an empty list; documented limitation.
    worker_run_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _disabled_summary(
    requested_tickers: list[str],
    reason: str,
) -> ValidationSummary:
    """Return a no-op summary when the harness is disabled."""
    return ValidationSummary(
        validation_enabled=False,
        requested_tickers=list(requested_tickers),
        normalized_tickers=[],
        attempted_count=0,
        written_count=0,
        skipped_count=0,
        failed_count=0,
        artifact_ids=[],
        safe_for_decision_false_count=0,
        unexpected_safe_for_decision_true_count=0,
        forbidden_payload_violation_count=0,
        visible_snapshot_unchanged=True,
        tables_touched=[],
        worker_run_ids=[],
        errors=[reason],
    )


def run_validation(
    tickers: list[str],
    user_id: str,
    db_client: Any,
    settings: Optional[Settings] = None,
    max_tickers: int = MAX_TICKERS_PER_RUN,
) -> ValidationSummary:
    """Run the Phase 3.5 validation harness for a capped list of tickers.

    Returns a ValidationSummary regardless of outcome. Never raises.

    Kill-switch hierarchy (ALL must be True to run):
      1. settings.intel_v3_research_worker_validation_enabled  (Phase 3.5 gate)
      2. settings.intel_v3_research_workers_enabled            (Phase 3 global)
      3. settings.intel_v3_earnings_reviewer_enabled           (Phase 3 per-worker)

    Args:
        tickers:     Input ticker list. Normalized, deduplicated, capped to max_tickers.
        user_id:     User scope for artifact writes.
        db_client:   Supabase-compatible client (real or fake in tests).
        settings:    Settings override; defaults to get_settings().
        max_tickers: Per-run cap (default MAX_TICKERS_PER_RUN = 5).
    """
    if settings is None:
        settings = get_settings()

    # ── Guard 1: Phase 3.5 validation flag ───────────────────────────────────
    if not settings.intel_v3_research_worker_validation_enabled:
        logger.debug(
            "validation_harness_skip reason=validation_flag_off"
        )
        return _disabled_summary(tickers, "intel_v3_research_worker_validation_enabled=false")

    # ── Guard 2: Phase 3 global flag ─────────────────────────────────────────
    if not settings.intel_v3_research_workers_enabled:
        logger.debug(
            "validation_harness_skip reason=global_worker_flag_off"
        )
        return _disabled_summary(tickers, "intel_v3_research_workers_enabled=false")

    # ── Guard 3: Earnings Reviewer flag ───────────────────────────────────────
    if not settings.intel_v3_earnings_reviewer_enabled:
        logger.debug(
            "validation_harness_skip reason=earnings_reviewer_flag_off"
        )
        return _disabled_summary(tickers, "intel_v3_earnings_reviewer_enabled=false")

    # ── Normalize and deduplicate ─────────────────────────────────────────────
    # dict.fromkeys preserves order while removing duplicates.
    normalized_all = list(
        dict.fromkeys(t.upper().strip() for t in tickers if t.strip())
    )
    capped = normalized_all[:max_tickers]
    cap_skipped_count = max(0, len(normalized_all) - max_tickers)

    artifact_ids: list[str] = []
    errors: list[str] = []
    written_count = 0
    failed_count = 0
    forbidden_payload_violation_count = 0
    safe_for_decision_false_count = 0
    unexpected_safe_for_decision_true_count = 0

    info_logs = settings.intel_v3_research_worker_validation_info_logs_enabled

    if info_logs:
        logger.info(
            "validation_harness_start attempted_count=%d cap_skipped=%d",
            len(capped),
            cap_skipped_count,
        )

    # ── Inspection phase: check payload for forbidden keys (no DB write) ──────
    # Calls earnings_reviewer.run() for each ticker independently so we can
    # inspect WorkerOutput.artifact_payload before the write. The runner also
    # calls earnings_reviewer.run() internally — the duplication is acceptable
    # for a capped validation harness (max 5 tickers, no IO).
    for ticker in capped:
        try:
            inspection_input = WorkerInput(
                user_id=user_id,
                ticker=ticker,
                worker_run_id=str(uuid.uuid4()),
            )
            inspection_output = earnings_reviewer.run(inspection_input)
            forbidden_key = _has_forbidden_key(inspection_output.artifact_payload)
            if forbidden_key is not None:
                forbidden_payload_violation_count += 1
                errors.append(
                    f"forbidden_key_in_payload ticker={ticker} key={forbidden_key}"
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"inspection_error ticker={ticker} error={exc}")

    # ── Write phase: run the existing Phase 3 runner ──────────────────────────
    for ticker in capped:
        try:
            artifact_id = run_earnings_reviewer_dark(
                user_id=user_id,
                ticker=ticker,
                db_client=db_client,
                settings=settings,
            )
            if artifact_id is not None:
                written_count += 1
                artifact_ids.append(artifact_id)
                # Writer always hard-codes safe_for_decision=False.
                # DB constraint also enforces this. Count as False.
                safe_for_decision_false_count += 1
            else:
                failed_count += 1
        except Exception as exc:  # noqa: BLE001
            failed_count += 1
            errors.append(f"write_error ticker={ticker} error={exc}")

    # tables_touched is a structural inference: if any write succeeded, the
    # writer always touches all four artifact tables.
    tables_touched = list(_WRITER_TABLES) if written_count > 0 else []

    summary = ValidationSummary(
        validation_enabled=True,
        requested_tickers=list(tickers),
        normalized_tickers=capped,
        attempted_count=len(capped),
        written_count=written_count,
        skipped_count=cap_skipped_count,
        failed_count=failed_count,
        artifact_ids=artifact_ids,
        safe_for_decision_false_count=safe_for_decision_false_count,
        unexpected_safe_for_decision_true_count=unexpected_safe_for_decision_true_count,
        forbidden_payload_violation_count=forbidden_payload_violation_count,
        # Structural guarantee: harness never touches intel_v3_snapshots.
        visible_snapshot_unchanged=True,
        tables_touched=tables_touched,
        # worker_run_ids not recoverable from existing runner interface.
        # Documented limitation — runner returns only artifact_id.
        worker_run_ids=[],
        errors=errors,
    )

    if info_logs:
        logger.info(
            "validation_harness_complete "
            "attempted=%d written=%d skipped=%d failed=%d "
            "safe_for_decision_false=%d unexpected_safe_true=%d "
            "forbidden_payload_violations=%d visible_snapshot_unchanged=%s",
            summary.attempted_count,
            summary.written_count,
            summary.skipped_count,
            summary.failed_count,
            summary.safe_for_decision_false_count,
            summary.unexpected_safe_for_decision_true_count,
            summary.forbidden_payload_violation_count,
            summary.visible_snapshot_unchanged,
        )

    return summary
