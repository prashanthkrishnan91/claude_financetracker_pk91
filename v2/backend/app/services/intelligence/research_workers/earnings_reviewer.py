"""Phase 3 — Earnings Reviewer dark-run worker scaffold.

What this worker does:
  - Takes one WorkerInput (ticker + optional holding context).
  - Produces a narrow "catalyst_window" research artifact capturing:
      * what was reviewed (fields inspected),
      * what was found (from existing persisted holding_context, if any),
      * what was missing (all external data, since no provider is configured yet).
  - Uses ONLY existing available data from holding_context or deterministic
    placeholder-safe context — NO external provider calls in this phase.
  - Records limitations_or_missing_evidence explicitly.
  - Sets confidence_or_trust_level='UNKNOWN', freshness_status='UNKNOWN'
    because no authoritative external source is available in Phase 3 dark-run.

What this worker NEVER does:
  - Calls the deterministic decision kernel (the v3 policy function).
  - Imports the v3 decision policy module.
  - Writes to intel_v3_snapshots or any visible-decision table.
  - Produces payload keys: final_action, buy, sell, trim, hold, final_conviction,
    final_allocation, deploy_amount, deploy_dollar, deploy_shares.
  - Sets safe_for_decision = True.
  - Runs on page load.
  - Calls any external provider.
  - Fabricate earnings data without a grounded source.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from .contracts import (
    AuditEventRecord,
    FactRecord,
    SourceRecord,
    WorkerInput,
    WorkerOutput,
    compute_input_fingerprint,
    compute_replay_idempotency_key,
)

_SKILL_PACK = "earnings_reviewer"
_ARTIFACT_TYPE = "catalyst_window"
_SCOPE_KIND = "ticker"
_WORKER_NAME = "earnings_reviewer_v1"
_MODEL_VERSION = "none_phase3_dark_run"

# Fields the worker intends to review when a real provider is available.
_INTENDED_REVIEW_FIELDS = [
    "earnings_date_next",
    "earnings_date_last",
    "eps_actual_last",
    "eps_estimate_last",
    "eps_surprise_direction",
    "revenue_actual_last",
    "revenue_estimate_last",
    "guidance_direction",
    "guidance_text_excerpt",
    "post_earnings_reaction_pct",
]


def run(worker_input: WorkerInput) -> WorkerOutput:
    """Produce a dark-run Earnings Reviewer artifact for one ticker.

    Phase 3: no external provider. Uses holding_context if supplied for
    any already-persisted earnings signals. Records all missing data.
    """
    ticker = worker_input.ticker.upper().strip()
    ts_now = datetime.now(timezone.utc).isoformat()

    # Build input fingerprint from stable inputs (no timestamps).
    fingerprint_data: dict[str, Any] = {
        "skill_pack": _SKILL_PACK,
        "ticker": ticker,
        "model_version": _MODEL_VERSION,
        "phase": "phase3_dark_run",
    }
    # Include holding context keys (not values) for structural fingerprinting.
    if worker_input.holding_context:
        fingerprint_data["context_keys"] = sorted(worker_input.holding_context.keys())
    input_fingerprint = compute_input_fingerprint(fingerprint_data)

    # Replay key: same ticker + no-external-source phase collapses to one active row.
    source_refs_fingerprint = "no_external_source_phase3"
    replay_key = compute_replay_idempotency_key(
        skill_pack=_SKILL_PACK,
        scope_kind=_SCOPE_KIND,
        ticker=ticker,
        source_refs_fingerprint=source_refs_fingerprint,
        model_version=_MODEL_VERSION,
    )

    # Extract whatever earnings signals are available from holding_context.
    found_fields: list[str] = []
    missing_fields: list[str] = list(_INTENDED_REVIEW_FIELDS)
    context_notes: list[str] = []

    if worker_input.holding_context:
        ctx = worker_input.holding_context
        # Check for any pre-existing earnings signals in the holding context.
        for field_name in _INTENDED_REVIEW_FIELDS:
            if ctx.get(field_name) is not None:
                found_fields.append(field_name)
                missing_fields.remove(field_name)
        if ctx.get("analyst_drivers"):
            context_notes.append("analyst_drivers present in holding context")
        if ctx.get("primary_driver"):
            context_notes.append("primary_driver present in holding context")

    limitations: list[str] = [
        "Phase 3 dark-run: no external earnings calendar provider configured.",
        "No transcript provider configured.",
        f"Missing fields: {', '.join(missing_fields)}" if missing_fields else "No missing fields.",
    ]
    if context_notes:
        limitations.append(f"Context notes: {'; '.join(context_notes)}")

    # Payload — no forbidden keys anywhere in this structure.
    payload: dict[str, Any] = {
        "review_status": "dark_run_no_external_source",
        "worker_phase": "phase3_dark_run",
        "reviewed_ticker": ticker,
        "intended_review_fields": _INTENDED_REVIEW_FIELDS,
        "found_fields": found_fields,
        "missing_fields": missing_fields,
        "review_notes": (
            "Earnings Reviewer Phase 3 scaffold. "
            "No external provider is configured. "
            "Artifact records what would be reviewed and what is currently missing. "
            "When a provider is added in a future phase, this worker will populate "
            "found_fields with sourced evidence."
        ),
    }
    if context_notes:
        payload["context_notes"] = context_notes

    # Evidence summary — plain English, no forbidden content.
    if found_fields:
        summary = (
            f"Earnings review for {ticker}: found {len(found_fields)} field(s) "
            f"in persisted context ({', '.join(found_fields[:3])}{'...' if len(found_fields) > 3 else ''}). "
            f"Missing {len(missing_fields)} field(s) pending provider configuration."
        )
    else:
        summary = (
            f"Earnings review for {ticker}: dark-run scaffold only. "
            f"No external earnings data available. "
            f"{len(missing_fields)} field(s) pending provider configuration."
        )

    # No external sources in this phase — sources list is empty.
    # The DB and truth contract treat this as UNKNOWN confidence (correct).
    sources: list[SourceRecord] = []

    # One fact: a sourced_claim recording the review status.
    facts: list[FactRecord] = [
        FactRecord(
            fact_kind="sourced_claim",
            axis_hint="catalyst",
            structured_payload={
                "claim": "earnings_review_attempted",
                "review_status": "dark_run_no_external_source",
                "found_fields_count": len(found_fields),
                "missing_fields_count": len(missing_fields),
                "worker_phase": "phase3_dark_run",
            },
            is_quote_grounded=False,
        )
    ]

    # Audit: one event recording this worker invocation.
    audit_events: list[AuditEventRecord] = [
        AuditEventRecord(
            tool_call="earnings_reviewer_run",
            status="completed",
            model_id=None,
            model_version=_MODEL_VERSION,
            cost_estimate_usd=0.0,
            latency_ms=0,
        )
    ]

    return WorkerOutput(
        worker_run_id=worker_input.worker_run_id,
        ticker=ticker,
        artifact_type=_ARTIFACT_TYPE,
        skill_pack=_SKILL_PACK,
        scope_kind=_SCOPE_KIND,
        artifact_payload=payload,
        sources=sources,
        facts=facts,
        audit_events=audit_events,
        evidence_summary_plain_english=summary,
        limitations_or_missing_evidence=limitations,
        confidence_or_trust_level="UNKNOWN",
        freshness_status="UNKNOWN",
        input_fingerprint=input_fingerprint,
        replay_idempotency_key=replay_key,
        source_window_start=None,
        source_window_end=None,
        expires_at=None,
        parent_intel_run_id=worker_input.parent_intel_run_id,
        generated_by_model=None,
        model_version=_MODEL_VERSION,
    )
