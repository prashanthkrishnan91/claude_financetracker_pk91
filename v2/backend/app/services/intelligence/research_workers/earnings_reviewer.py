"""Phase 3 / Phase 6A — Earnings Reviewer dark-run worker.

Phase 3 behavior (sec_config=None):
  - Produces a narrow "catalyst_window" research artifact from holding_context only.
  - No external provider calls. confidence_or_trust_level=UNKNOWN, freshness_status=UNKNOWN.
  - source_refs_fingerprint="no_external_source_phase3".
  - Unchanged from Phase 3 — all Phase 3 tests still pass.

Phase 6A behavior (sec_config provided):
  - Calls the SEC EDGAR provider to fetch recent 10-K/10-Q/8-K filing metadata.
  - On success: produces source-linked FactRecords, MEDIUM/LOW confidence,
    FRESH/STALE freshness, and a fingerprint derived from accession numbers.
  - On any failure: fails closed → UNKNOWN/UNKNOWN/no-source artifact with limitation recorded.
  - source_refs_fingerprint differs from Phase 3 key in all cases.

What this worker NEVER does (both phases):
  - Calls the deterministic decision kernel (the v3 policy function decide()).
  - Imports the v3 decision policy module.
  - Writes to intel_v3_snapshots or any visible-decision table.
  - Produces payload keys: final_action, buy, sell, trim, hold, final_conviction,
    final_allocation, deploy_amount, deploy_dollar, deploy_shares,
    action, recommendation, target_price, allocation.
  - Sets safe_for_decision = True.
  - Runs on page load.
  - Fabricates earnings data without a grounded source.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

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

# Model versions identify which execution path produced the artifact.
# Different model_version → different replay_idempotency_key → separate DB row.
_MODEL_VERSION_DARK_RUN = "none_phase3_dark_run"    # Phase 3 scaffold (no provider)
_MODEL_VERSION_SEC = "sec_edgar_phase6a_v1"         # Phase 6A SEC-grounded path

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


def run(
    worker_input: WorkerInput,
    sec_config: Optional[Any] = None,
    _http_get_fn: Optional[Callable[[str], Any]] = None,
) -> WorkerOutput:
    """Produce an Earnings Reviewer artifact for one ticker.

    Args:
        worker_input:  Input with user_id, ticker, optional holding_context.
        sec_config:    Optional SecEdgarProviderConfig. If None: Phase 3 behavior.
                       If provided: Phase 6A SEC-grounded path attempted.
        _http_get_fn:  Optional HTTP GET callable for testing. Only used when
                       sec_config is not None. Pass a fake callable to avoid
                       real SEC EDGAR HTTP calls in tests.

    Returns:
        WorkerOutput — always. Never raises.
    """
    if sec_config is not None:
        return _run_sec_grounded(worker_input, sec_config, _http_get_fn=_http_get_fn)
    return _run_phase3_dark(worker_input)


# ── Phase 3 dark-run scaffold (unchanged from Phase 3) ───────────────────────

def _run_phase3_dark(worker_input: WorkerInput) -> WorkerOutput:
    """Phase 3: no external provider. Uses holding_context only. UNKNOWN confidence."""
    ticker = worker_input.ticker.upper().strip()
    ts_now = datetime.now(timezone.utc).isoformat()

    fingerprint_data: dict[str, Any] = {
        "skill_pack": _SKILL_PACK,
        "ticker": ticker,
        "model_version": _MODEL_VERSION_DARK_RUN,
        "phase": "phase3_dark_run",
    }
    if worker_input.holding_context:
        fingerprint_data["context_keys"] = sorted(worker_input.holding_context.keys())
    input_fingerprint = compute_input_fingerprint(fingerprint_data)

    source_refs_fingerprint = "no_external_source_phase3"
    replay_key = compute_replay_idempotency_key(
        skill_pack=_SKILL_PACK,
        scope_kind=_SCOPE_KIND,
        ticker=ticker,
        source_refs_fingerprint=source_refs_fingerprint,
        model_version=_MODEL_VERSION_DARK_RUN,
    )

    found_fields: list[str] = []
    missing_fields: list[str] = list(_INTENDED_REVIEW_FIELDS)
    context_notes: list[str] = []

    if worker_input.holding_context:
        ctx = worker_input.holding_context
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

    sources: list[SourceRecord] = []
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

    audit_events: list[AuditEventRecord] = [
        AuditEventRecord(
            tool_call="earnings_reviewer_run",
            status="completed",
            model_id=None,
            model_version=_MODEL_VERSION_DARK_RUN,
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
        model_version=_MODEL_VERSION_DARK_RUN,
    )


# ── Phase 6A SEC-grounded path ────────────────────────────────────────────────

def _run_sec_grounded(
    worker_input: WorkerInput,
    sec_config: Any,
    _http_get_fn: Optional[Callable[[str], Any]] = None,
) -> WorkerOutput:
    """Phase 6A: fetch SEC EDGAR filing metadata, adapt to source-linked facts.

    Fail-closed on any SEC provider failure: produces UNKNOWN/no-source artifact
    with limitation recorded. Never raises.
    """
    from .sec_edgar_provider import fetch_for_ticker
    from .earnings_sec_adapter import adapt_sec_result

    ticker = worker_input.ticker.upper().strip()

    # Fingerprint includes the ticker and SEC model version for stable but distinct
    # input tracking (values, not timestamps).
    fingerprint_data: dict[str, Any] = {
        "skill_pack": _SKILL_PACK,
        "ticker": ticker,
        "model_version": _MODEL_VERSION_SEC,
        "phase": "phase6a_sec_grounded",
    }
    if worker_input.holding_context:
        fingerprint_data["context_keys"] = sorted(worker_input.holding_context.keys())
    input_fingerprint = compute_input_fingerprint(fingerprint_data)

    # Fetch SEC data (fail-closed — never raises).
    sec_result = fetch_for_ticker(ticker, sec_config, http_get_fn=_http_get_fn)

    # Adapt to source/fact/confidence/freshness (fail-closed — never raises).
    adapted = adapt_sec_result(sec_result)

    # Build replay key using the SEC-specific model version and source fingerprint.
    # This ensures Phase 6A artifacts never collapse with Phase 3 artifacts.
    replay_key = compute_replay_idempotency_key(
        skill_pack=_SKILL_PACK,
        scope_kind=_SCOPE_KIND,
        ticker=ticker,
        source_refs_fingerprint=adapted.source_refs_fingerprint,
        model_version=_MODEL_VERSION_SEC,
    )

    # Artifact payload — no forbidden keys.
    payload: dict[str, Any] = {
        "review_status": adapted.review_status,
        "worker_phase": "phase6a_sec_grounded",
        "reviewed_ticker": ticker,
        "sec_fetch_status": sec_result.fetch_status,
        "sec_filing_count": len(adapted.sources),
        "intended_review_fields": _INTENDED_REVIEW_FIELDS,
    }
    if sec_result.cik:
        payload["sec_cik"] = sec_result.cik
    if sec_result.request_count > 0:
        payload["sec_request_count"] = sec_result.request_count

    # Evidence summary for observability.
    if adapted.sources:
        summary = (
            f"Earnings review for {ticker}: SEC EDGAR source-grounded — "
            f"{len(adapted.sources)} filing(s) retrieved "
            f"(confidence={adapted.confidence_or_trust_level}, "
            f"freshness={adapted.freshness_status}). "
            f"Filing metadata only — no transcript or analyst estimate provider."
        )
    else:
        summary = (
            f"Earnings review for {ticker}: SEC EDGAR fetch attempted but no grounding "
            f"produced (status={sec_result.fetch_status}). "
            f"Fail-closed: confidence=UNKNOWN, freshness=UNKNOWN."
        )

    # Combine all limitations.
    base_limitations = [
        "Phase 6A: SEC EDGAR filing metadata only — no earnings transcript provider.",
        "No analyst EPS estimate or guidance provider configured.",
        "SEC facts do not represent analyst expectations or forward earnings guidance.",
    ]
    limitations = base_limitations + adapted.limitations

    audit_events: list[AuditEventRecord] = [
        AuditEventRecord(
            tool_call="earnings_reviewer_sec_run",
            status="completed",
            model_id=None,
            model_version=_MODEL_VERSION_SEC,
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
        sources=adapted.sources,
        facts=adapted.facts,
        audit_events=audit_events,
        evidence_summary_plain_english=summary,
        limitations_or_missing_evidence=limitations,
        confidence_or_trust_level=adapted.confidence_or_trust_level,
        freshness_status=adapted.freshness_status,
        input_fingerprint=input_fingerprint,
        replay_idempotency_key=replay_key,
        source_window_start=adapted.source_window_start,
        source_window_end=adapted.source_window_end,
        expires_at=adapted.expires_at,
        parent_intel_run_id=worker_input.parent_intel_run_id,
        generated_by_model=None,
        model_version=_MODEL_VERSION_SEC,
    )
