"""Phase 3 research worker contracts — input/output types.

These dataclasses define the boundary between:
  - Callers (runner.py) that invoke a worker with a WorkerInput.
  - Workers (earnings_reviewer.py and future workers) that return a WorkerOutput.
  - The artifact store writer that persists a WorkerOutput to the DB.

Architecture constraints (non-negotiable):
  - Workers NEVER produce forbidden keys: final_action, buy, sell, trim, hold,
    final_conviction, final_allocation, deploy_amount, deploy_dollar, deploy_shares.
  - safe_for_decision is always False in every artifact row — enforced by DB constraint
    and by the writer (never settable by workers).
  - Workers NEVER import or call decide() from decision_policy_v1.
  - Workers NEVER alter intel_v3_snapshots or any visible Intel v3 surface.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

# Immutable set of forbidden payload keys (mirrors the DB CHECK + trigger).
FORBIDDEN_PAYLOAD_KEYS: frozenset[str] = frozenset({
    "final_action",
    "final_conviction",
    "final_allocation",
    "deploy_amount",
    "deploy_dollar",
    "deploy_shares",
    "buy",
    "sell",
    "trim",
    "hold",
})


def _has_forbidden_key(payload: Any) -> Optional[str]:
    """Recursively check a dict/list for any forbidden key (case-insensitive).

    Returns the first forbidden key found, or None.
    Mirrors the PL/pgSQL walker in migration 017 so validation is identical.
    """
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k.lower() in FORBIDDEN_PAYLOAD_KEYS:
                return k
            found = _has_forbidden_key(v)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _has_forbidden_key(item)
            if found is not None:
                return found
    return None


def validate_payload(payload: dict, label: str = "payload") -> None:
    """Raise ValueError if payload contains any forbidden key at any depth."""
    found = _has_forbidden_key(payload)
    if found is not None:
        raise ValueError(
            f"Forbidden key '{found}' in {label}. Workers must not store "
            "final Buy/Hold/Trim/Sell authority. See INTEL_V3_FINANCE_AGENT_SKILL_PACK_AUDIT.md §4."
        )


def compute_replay_idempotency_key(
    skill_pack: str,
    scope_kind: str,
    ticker: str,
    source_refs_fingerprint: str,
    model_version: str,
) -> str:
    """Deterministic replay key — identical inputs collapse to identical key."""
    raw = json.dumps(
        [skill_pack, scope_kind, ticker, source_refs_fingerprint, model_version],
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def compute_input_fingerprint(data: dict) -> str:
    """Deterministic hash of worker input for audit trail."""
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class WorkerInput:
    """Input supplied to every research worker."""
    user_id: str
    ticker: str
    worker_run_id: str
    parent_intel_run_id: Optional[str] = None
    holding_context: Optional[dict[str, Any]] = field(default=None)


@dataclass
class SourceRecord:
    """One citation row for research_artifact_sources."""
    source_kind: str
    provider_name: str
    provider_version: Optional[str] = None
    source_url: Optional[str] = None
    source_id: Optional[str] = None
    source_published_at: Optional[str] = None  # ISO 8601
    quote_or_excerpt: Optional[str] = None
    section_reference: Optional[str] = None
    source_hash: Optional[str] = None


@dataclass
class FactRecord:
    """One typed observation row for research_artifact_facts."""
    fact_kind: str
    structured_payload: dict[str, Any]
    axis_hint: Optional[str] = None
    severity: Optional[str] = None
    period: Optional[str] = None
    as_of: Optional[str] = None  # ISO 8601
    is_quote_grounded: bool = False
    # Index into the WorkerOutput.sources list; writer resolves to DB source_id.
    source_index: Optional[int] = None

    def __post_init__(self) -> None:
        validate_payload(self.structured_payload, label="FactRecord.structured_payload")


@dataclass
class AuditEventRecord:
    """One audit row for worker_audit_events."""
    tool_call: str
    status: str  # completed | failed | rejected | timeout | cost_capped
    input_digest: Optional[str] = None
    output_digest: Optional[str] = None
    model_id: Optional[str] = None
    model_version: Optional[str] = None
    latency_ms: Optional[int] = None
    cost_estimate_usd: Optional[float] = None
    rejected_claims: Optional[list[dict[str, Any]]] = None
    error_message: Optional[str] = None


@dataclass
class WorkerOutput:
    """Everything the DB writer needs to persist one artifact + supporting rows."""
    worker_run_id: str
    ticker: str
    artifact_type: str
    skill_pack: str
    scope_kind: str
    artifact_payload: dict[str, Any]
    sources: list[SourceRecord]
    facts: list[FactRecord]
    audit_events: list[AuditEventRecord]
    evidence_summary_plain_english: Optional[str]
    limitations_or_missing_evidence: list[str]
    confidence_or_trust_level: str  # HIGH | MEDIUM | LOW | UNKNOWN
    freshness_status: str           # FRESH | STALE | UNKNOWN
    input_fingerprint: str
    replay_idempotency_key: str
    source_window_start: Optional[str] = None  # ISO 8601
    source_window_end: Optional[str] = None    # ISO 8601
    expires_at: Optional[str] = None           # ISO 8601
    parent_intel_run_id: Optional[str] = None
    generated_by_model: Optional[str] = None
    model_version: Optional[str] = None

    def __post_init__(self) -> None:
        validate_payload(self.artifact_payload, label="WorkerOutput.artifact_payload")
