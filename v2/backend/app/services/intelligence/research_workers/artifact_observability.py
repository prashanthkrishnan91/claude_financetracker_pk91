"""Phase 4 — read-only research artifact observability service.

Purpose:
    Aggregate-counter diagnostics over existing research artifacts.
    Answers: How many artifacts exist? Are they all safe_for_decision=false?
    Any forbidden payload keys? What are confidence/freshness distributions?

Enabled only when:
    INTEL_V3_RESEARCH_ARTIFACT_OBSERVABILITY_ENABLED=true

Architecture invariants (non-negotiable):
    - NEVER imports or calls decide() from decision_policy_v1.
    - NEVER writes to intel_v3_snapshots, research_artifacts, or any artifact table.
    - NEVER runs on page load — explicit operator invocation only.
    - NEVER feeds artifacts into the visible decision path.
    - NEVER returns raw payload JSON, source URLs, quotes, excerpts, or full facts.
    - NEVER returns raw DB rows.
    - All failures are contained and returned in errors[]; none propagate to callers.
    - safe_for_decision remains False — this service only reads and counts.
    - Logs structured INFO only when
      INTEL_V3_RESEARCH_ARTIFACT_OBSERVABILITY_INFO_LOGS_ENABLED=true.
    - Logs are aggregate and safe — no full payloads, no secrets, no raw user data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.config import Settings, get_settings
from .artifact_truth_readiness import evaluate_artifact_truth_readiness
from .contracts import _has_forbidden_key
from .sec_metric_evidence_snapshot_dry_run import run_sec_metric_evidence_snapshot_dry_run
from .sec_metric_truth_adapter_dry_run import run_sec_metric_truth_adapter_dry_run


@dataclass
class ArtifactObservabilitySummary:
    """Compact read-only aggregate summary of recent research artifacts.

    All fields are safe to log and return via the diagnostics endpoint.
    No raw payloads, source URLs, quotes, or full facts are included.
    """
    observability_enabled: bool
    requested_tickers: list[str]
    normalized_tickers: list[str]
    lookback_days: int
    max_rows: int
    artifact_count: int
    by_ticker: dict[str, int]
    by_artifact_type: dict[str, int]
    by_skill_pack: dict[str, int]
    by_confidence_or_trust_level: dict[str, int]
    by_freshness_status: dict[str, int]
    safe_for_decision_false_count: int
    unexpected_safe_for_decision_true_count: int
    forbidden_payload_violation_count: int
    active_count: int
    inactive_count: int
    invalidated_count: int  # rows where invalidated_at IS NOT NULL
    expired_count: int
    artifacts_with_sources_count: int
    artifacts_without_sources_count: int
    artifacts_with_facts_count: int
    artifacts_without_facts_count: int
    missing_evidence_count: int  # artifacts with non-empty limitations_or_missing_evidence
    visible_snapshot_unchanged: bool
    errors: list[str] = field(default_factory=list)
    # Phase 6B: Truth Adapter Readiness aggregates (all default 0/{}/True — backward-compatible)
    readiness_evaluated_count: int = 0
    eligible_for_truth_adapter_count: int = 0
    ineligible_for_truth_adapter_count: int = 0
    eligible_for_decision_consumption_count: int = 0  # Phase 5/6B invariant: always 0
    safe_for_decision_db_promotion_blocked_count: int = 0  # always = readiness_evaluated_count
    fail_closed_count: int = 0  # always = readiness_evaluated_count
    by_readiness_reason_code: dict[str, int] = field(default_factory=dict)
    artifacts_with_source_linked_facts_count: int = 0
    artifacts_without_source_linked_facts_count: int = 0
    phase5_ready_but_decision_blocked_count: int = 0
    readiness_visible_snapshot_unchanged: bool = True
    # Phase 7A: metric_observation aggregate counters (default 0 — backward-compatible).
    artifacts_with_metric_observations_count: int = 0
    metric_observation_fact_count: int = 0
    # Phase 7C: metric observation mix aggregates (default {}0 — backward-compatible).
    # Aggregate-only: no raw values, no structured_payload, no source URLs exposed.
    by_metric_observation_tag: dict[str, int] = field(default_factory=dict)
    by_metric_observation_unit: dict[str, int] = field(default_factory=dict)
    by_metric_observation_form: dict[str, int] = field(default_factory=dict)
    artifacts_with_companyfacts_metric_observations_count: int = 0
    # Phase 8A: SEC metric truth adapter dry-run (all default off/0/{} — backward-compatible).
    # Aggregate-only: no raw values, no structured_payload, no source URLs.
    # dry_run_safe_for_decision is always False; visible_snapshot_unchanged always True.
    sec_metric_truth_adapter_dry_run_enabled: bool = False
    sec_metric_truth_adapter_dry_run_safe_for_decision: bool = False
    sec_metric_truth_adapter_artifacts_evaluated_count: int = 0
    sec_metric_truth_adapter_source_linked_metric_fact_count: int = 0
    sec_metric_truth_adapter_unmapped_metric_fact_count: int = 0
    sec_metric_truth_adapter_by_ticker: dict[str, int] = field(default_factory=dict)
    sec_metric_truth_adapter_by_bucket: dict[str, int] = field(default_factory=dict)
    sec_metric_truth_adapter_by_tag: dict[str, int] = field(default_factory=dict)
    sec_metric_truth_adapter_by_unit: dict[str, int] = field(default_factory=dict)
    sec_metric_truth_adapter_by_form: dict[str, int] = field(default_factory=dict)
    sec_metric_truth_adapter_missing_buckets_by_ticker: dict[str, list] = field(default_factory=dict)
    sec_metric_truth_adapter_visible_snapshot_unchanged: bool = True
    # Phase 8B: SEC metric evidence snapshot dry-run (all default off/0/{} — backward-compatible).
    # Per-ticker diagnostic contract: present/missing buckets, group coverage, readiness, blockers.
    # snapshot_safe_for_decision is always False; visible_snapshot_unchanged always True.
    sec_metric_evidence_snapshot_dry_run_enabled: bool = False
    sec_metric_evidence_snapshot_safe_for_decision: bool = False
    sec_metric_evidence_snapshot_visible_snapshot_unchanged: bool = True
    sec_metric_evidence_snapshot_tickers_evaluated_count: int = 0
    sec_metric_evidence_snapshot_tickers_with_any_source_linked_evidence_count: int = 0
    sec_metric_evidence_snapshot_tickers_ready_for_future_adapter_count: int = 0
    sec_metric_evidence_snapshot_tickers_blocked_from_decision_count: int = 0
    sec_metric_evidence_snapshot_by_ticker: dict[str, dict] = field(default_factory=dict)


def _disabled_summary(
    requested_tickers: list[str],
    lookback_days: int,
    max_rows: int,
    reason: str,
) -> ArtifactObservabilitySummary:
    """Return a no-op summary when observability is disabled."""
    return ArtifactObservabilitySummary(
        observability_enabled=False,
        requested_tickers=list(requested_tickers),
        normalized_tickers=[],
        lookback_days=lookback_days,
        max_rows=max_rows,
        artifact_count=0,
        by_ticker={},
        by_artifact_type={},
        by_skill_pack={},
        by_confidence_or_trust_level={},
        by_freshness_status={},
        safe_for_decision_false_count=0,
        unexpected_safe_for_decision_true_count=0,
        forbidden_payload_violation_count=0,
        active_count=0,
        inactive_count=0,
        invalidated_count=0,
        expired_count=0,
        artifacts_with_sources_count=0,
        artifacts_without_sources_count=0,
        artifacts_with_facts_count=0,
        artifacts_without_facts_count=0,
        missing_evidence_count=0,
        visible_snapshot_unchanged=True,
        errors=[reason],
    )


def _safe_str_counter(rows: list[dict], key: str) -> dict[str, int]:
    """Count occurrences of a string-valued column across rows."""
    counts: dict[str, int] = {}
    for row in rows:
        val = str(row.get(key) or "UNKNOWN")
        counts[val] = counts.get(val, 0) + 1
    return counts


def summarize_recent_research_artifacts(
    user_id: str,
    db_client: Any,
    tickers: Optional[list[str]] = None,
    lookback_days: int = 30,
    max_rows: int = 250,
    settings: Optional[Settings] = None,
) -> ArtifactObservabilitySummary:
    """Read-only aggregate summary of recent research artifacts for a user.

    Returns ArtifactObservabilitySummary regardless of outcome. Never raises.

    Kill switch:
        settings.intel_v3_research_artifact_observability_enabled must be True.

    Args:
        user_id:       User scope for artifact reads.
        db_client:     Supabase-compatible client (real or fake in tests).
        tickers:       Optional ticker filter. None or [] means all tickers.
        lookback_days: Days back from now for the created_at window.
        max_rows:      Row cap to avoid heavy scans.
        settings:      Settings override; defaults to get_settings().
    """
    if settings is None:
        settings = get_settings()

    requested_tickers: list[str] = list(tickers or [])

    if not settings.intel_v3_research_artifact_observability_enabled:
        logger.debug("artifact_observability_skip reason=observability_flag_off")
        return _disabled_summary(
            requested_tickers,
            lookback_days,
            max_rows,
            "intel_v3_research_artifact_observability_enabled=false",
        )

    normalized_tickers = list(
        dict.fromkeys(t.upper().strip() for t in requested_tickers if t.strip())
    )

    errors: list[str] = []
    artifact_rows: list[dict] = []

    # ── Query research_artifacts ──────────────────────────────────────────────
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        query = (
            db_client.table("research_artifacts")
            .select(
                "id,ticker,artifact_type,skill_pack,"
                "confidence_or_trust_level,freshness_status,"
                "safe_for_decision,is_active,created_at,expires_at,"
                "invalidated_at,limitations_or_missing_evidence,payload"
            )
            .eq("user_id", user_id)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(max_rows)
        )
        if normalized_tickers:
            query = query.in_("ticker", normalized_tickers)
        result = query.execute()
        artifact_rows = list(result.data or [])
    except Exception as exc:  # noqa: BLE001
        errors.append(f"artifact_query_error error={exc}")

    now_utc = datetime.now(timezone.utc)

    # ── Aggregate counters ────────────────────────────────────────────────────
    artifact_count = len(artifact_rows)
    by_ticker: dict[str, int] = {}
    by_artifact_type: dict[str, int] = {}
    by_skill_pack: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    by_freshness: dict[str, int] = {}
    safe_for_decision_false_count = 0
    unexpected_safe_for_decision_true_count = 0
    forbidden_payload_violation_count = 0
    active_count = 0
    inactive_count = 0
    invalidated_count = 0
    expired_count = 0
    missing_evidence_count = 0

    for row in artifact_rows:
        ticker = str(row.get("ticker") or "UNKNOWN")
        by_ticker[ticker] = by_ticker.get(ticker, 0) + 1

        atype = str(row.get("artifact_type") or "UNKNOWN")
        by_artifact_type[atype] = by_artifact_type.get(atype, 0) + 1

        sp = str(row.get("skill_pack") or "UNKNOWN")
        by_skill_pack[sp] = by_skill_pack.get(sp, 0) + 1

        ctl = str(row.get("confidence_or_trust_level") or "UNKNOWN")
        by_confidence[ctl] = by_confidence.get(ctl, 0) + 1

        fs = str(row.get("freshness_status") or "UNKNOWN")
        by_freshness[fs] = by_freshness.get(fs, 0) + 1

        sfd = row.get("safe_for_decision")
        if sfd:
            unexpected_safe_for_decision_true_count += 1
        else:
            safe_for_decision_false_count += 1

        is_active = bool(row.get("is_active"))
        if is_active:
            active_count += 1
        else:
            inactive_count += 1

        if row.get("invalidated_at") is not None:
            invalidated_count += 1

        expires_at_raw = row.get("expires_at")
        if expires_at_raw:
            try:
                exp = datetime.fromisoformat(str(expires_at_raw).replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < now_utc:
                    expired_count += 1
            except Exception:  # noqa: BLE001
                pass

        limitations = row.get("limitations_or_missing_evidence")
        if limitations:
            try:
                if isinstance(limitations, list) and len(limitations) > 0:
                    missing_evidence_count += 1
                elif isinstance(limitations, str) and limitations.strip() not in ("", "[]", "null"):
                    missing_evidence_count += 1
            except Exception:  # noqa: BLE001
                pass

        payload = row.get("payload")
        if payload is not None:
            try:
                if isinstance(payload, dict):
                    forbidden_key = _has_forbidden_key(payload)
                    if forbidden_key is not None:
                        forbidden_payload_violation_count += 1
                        errors.append(
                            f"forbidden_key_in_payload ticker={ticker} key={forbidden_key}"
                        )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"forbidden_key_check_error ticker={ticker} error={exc}")

    # ── Child table counts (sources / facts) ──────────────────────────────────
    artifacts_with_sources_count = 0
    artifacts_without_sources_count = artifact_count
    artifacts_with_facts_count = 0
    artifacts_without_facts_count = artifact_count

    if artifact_rows:
        artifact_ids = [str(r["id"]) for r in artifact_rows if r.get("id")]
        if artifact_ids:
            try:
                src_result = (
                    db_client.table("research_artifact_sources")
                    .select("artifact_id")
                    .eq("user_id", user_id)
                    .in_("artifact_id", artifact_ids)
                    .execute()
                )
                src_rows = list(src_result.data or [])
                ids_with_sources = {str(r["artifact_id"]) for r in src_rows if r.get("artifact_id")}
                artifacts_with_sources_count = len(ids_with_sources)
                artifacts_without_sources_count = artifact_count - artifacts_with_sources_count
            except Exception as exc:  # noqa: BLE001
                errors.append(f"sources_query_error error={exc}")

            try:
                fact_result = (
                    db_client.table("research_artifact_facts")
                    .select("artifact_id")
                    .eq("user_id", user_id)
                    .in_("artifact_id", artifact_ids)
                    .execute()
                )
                fact_rows = list(fact_result.data or [])
                ids_with_facts = {str(r["artifact_id"]) for r in fact_rows if r.get("artifact_id")}
                artifacts_with_facts_count = len(ids_with_facts)
                artifacts_without_facts_count = artifact_count - artifacts_with_facts_count
            except Exception as exc:  # noqa: BLE001
                errors.append(f"facts_query_error error={exc}")

    # ── Phase 6B: Readiness evaluation ───────────────────────────────────────
    # Query full source/fact details per artifact and evaluate Phase 5 readiness.
    # Fail-closed: if queries fail, errors[] records the failure; evaluation skips
    # affected artifacts. Never returns raw source_url, structured_payload, or rows.
    readiness_evaluated_count = 0
    eligible_for_truth_adapter_count = 0
    ineligible_for_truth_adapter_count = 0
    eligible_for_decision_consumption_count = 0  # invariant: always 0
    safe_for_decision_db_promotion_blocked_count = 0
    fail_closed_count = 0
    by_readiness_reason_code: dict[str, int] = {}
    artifacts_with_source_linked_facts_count = 0
    artifacts_without_source_linked_facts_count = 0
    phase5_ready_but_decision_blocked_count = 0
    # Phase 7A/7C counters.
    artifacts_with_metric_observations_count = 0
    metric_observation_fact_count = 0
    by_metric_observation_tag: dict[str, int] = {}
    by_metric_observation_unit: dict[str, int] = {}
    by_metric_observation_form: dict[str, int] = {}
    artifacts_with_companyfacts_metric_observations_count = 0

    if artifact_rows:
        artifact_ids_6b = [str(r["id"]) for r in artifact_rows if r.get("id")]
        if artifact_ids_6b:
            # Full source details for readiness evaluation (internal — never returned).
            readiness_sources_by_artifact: dict[str, list[dict]] = {}
            try:
                src6b_result = (
                    db_client.table("research_artifact_sources")
                    .select(
                        "id,artifact_id,source_kind,provider_name,"
                        "source_url,source_id,source_hash,section_reference"
                    )
                    .eq("user_id", user_id)
                    .in_("artifact_id", artifact_ids_6b)
                    .execute()
                )
                for row in (src6b_result.data or []):
                    aid = str(row.get("artifact_id", ""))
                    if aid:
                        readiness_sources_by_artifact.setdefault(aid, []).append(row)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"readiness_sources_query_error error={exc}")

            # Full fact details for readiness evaluation (internal — never returned).
            readiness_facts_by_artifact: dict[str, list[dict]] = {}
            try:
                fact6b_result = (
                    db_client.table("research_artifact_facts")
                    .select("id,artifact_id,fact_kind,structured_payload,source_id")
                    .eq("user_id", user_id)
                    .in_("artifact_id", artifact_ids_6b)
                    .execute()
                )
                for row in (fact6b_result.data or []):
                    aid = str(row.get("artifact_id", ""))
                    if aid:
                        readiness_facts_by_artifact.setdefault(aid, []).append(row)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"readiness_facts_query_error error={exc}")

            # Per-artifact readiness evaluation — aggregate counters only.
            for artifact_row in artifact_rows:
                aid = str(artifact_row.get("id", ""))
                artifact_sources = readiness_sources_by_artifact.get(aid, [])
                artifact_facts = readiness_facts_by_artifact.get(aid, [])

                # Source-linked facts: any fact with a non-empty source_id.
                has_source_linked = any(bool(f.get("source_id")) for f in artifact_facts)
                if has_source_linked:
                    artifacts_with_source_linked_facts_count += 1
                else:
                    artifacts_without_source_linked_facts_count += 1

                # Phase 7A/7C: count metric_observation facts and aggregate tag/unit/form mix.
                # Aggregate-only — no raw values or payloads are accumulated or returned.
                artifact_metric_count = 0
                artifact_has_companyfacts = False
                for f in artifact_facts:
                    if str(f.get("fact_kind") or "") != "metric_observation":
                        continue
                    artifact_metric_count += 1
                    sp = f.get("structured_payload") or {}
                    if isinstance(sp, dict):
                        tag = str(sp.get("tag") or "UNKNOWN")
                        by_metric_observation_tag[tag] = by_metric_observation_tag.get(tag, 0) + 1
                        unit = str(sp.get("unit") or "UNKNOWN")
                        by_metric_observation_unit[unit] = by_metric_observation_unit.get(unit, 0) + 1
                        form = str(sp.get("form") or "UNKNOWN")
                        by_metric_observation_form[form] = by_metric_observation_form.get(form, 0) + 1
                        if sp.get("claim") == "sec_companyfact_observed":
                            artifact_has_companyfacts = True
                if artifact_metric_count > 0:
                    artifacts_with_metric_observations_count += 1
                    metric_observation_fact_count += artifact_metric_count
                if artifact_has_companyfacts:
                    artifacts_with_companyfacts_metric_observations_count += 1

                try:
                    result = evaluate_artifact_truth_readiness(
                        artifact=artifact_row,
                        sources=artifact_sources,
                        facts=artifact_facts,
                    )
                    readiness_evaluated_count += 1

                    # Derive all counters from the actual result — never assume invariants.
                    if result.eligible_for_decision_consumption:
                        eligible_for_decision_consumption_count += 1
                    if result.safe_for_decision_db_promotion_blocked:
                        safe_for_decision_db_promotion_blocked_count += 1
                    if result.fail_closed:
                        fail_closed_count += 1

                    if result.eligible_for_truth_adapter:
                        eligible_for_truth_adapter_count += 1
                        # Ready for truth adapter but blocked from decision consumption.
                        if not result.eligible_for_decision_consumption:
                            phase5_ready_but_decision_blocked_count += 1
                    else:
                        ineligible_for_truth_adapter_count += 1

                    for code in result.reason_codes:
                        by_readiness_reason_code[code] = (
                            by_readiness_reason_code.get(code, 0) + 1
                        )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"readiness_eval_error artifact_id={aid} error={exc}")

    # ── Phase 8A: SEC metric truth adapter dry-run ────────────────────────────
    # Aggregate-only mapping of source-linked companyfacts metric observations
    # into internal evidence buckets. Reuses already-fetched fact data from the
    # Phase 6B/7C query above — no additional DB calls.
    # Kill switch: settings.intel_v3_sec_metric_truth_adapter_dry_run_enabled.
    dry_run_result = None
    if settings.intel_v3_sec_metric_truth_adapter_dry_run_enabled and artifact_rows:
        try:
            dry_run_result = run_sec_metric_truth_adapter_dry_run(
                artifact_rows=artifact_rows,
                facts_by_artifact=readiness_facts_by_artifact,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"sec_metric_dry_run_error error={exc}")

    # ── Phase 8B: SEC metric evidence snapshot dry-run ────────────────────────
    # Converts Phase 8A bucket counts into a per-ticker diagnostic contract.
    # Reuses Phase 8A result and already-fetched fact data — no DB re-query.
    # Kill switch: settings.intel_v3_sec_metric_evidence_snapshot_dry_run_enabled.
    # Requires dry_run_result (Phase 8A) to be non-None to proceed.
    snapshot_result = None
    if (
        settings.intel_v3_sec_metric_evidence_snapshot_dry_run_enabled
        and dry_run_result is not None
    ):
        try:
            snapshot_result = run_sec_metric_evidence_snapshot_dry_run(
                adapter_result=dry_run_result,
                artifact_rows=artifact_rows,
                facts_by_artifact=readiness_facts_by_artifact,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"sec_metric_evidence_snapshot_error error={exc}")

    summary = ArtifactObservabilitySummary(
        observability_enabled=True,
        requested_tickers=requested_tickers,
        normalized_tickers=normalized_tickers,
        lookback_days=lookback_days,
        max_rows=max_rows,
        artifact_count=artifact_count,
        by_ticker=by_ticker,
        by_artifact_type=by_artifact_type,
        by_skill_pack=by_skill_pack,
        by_confidence_or_trust_level=by_confidence,
        by_freshness_status=by_freshness,
        safe_for_decision_false_count=safe_for_decision_false_count,
        unexpected_safe_for_decision_true_count=unexpected_safe_for_decision_true_count,
        forbidden_payload_violation_count=forbidden_payload_violation_count,
        active_count=active_count,
        inactive_count=inactive_count,
        invalidated_count=invalidated_count,
        expired_count=expired_count,
        artifacts_with_sources_count=artifacts_with_sources_count,
        artifacts_without_sources_count=artifacts_without_sources_count,
        artifacts_with_facts_count=artifacts_with_facts_count,
        artifacts_without_facts_count=artifacts_without_facts_count,
        missing_evidence_count=missing_evidence_count,
        # Structural guarantee: this function never writes to intel_v3_snapshots.
        visible_snapshot_unchanged=True,
        errors=errors,
        # Phase 6B readiness aggregates.
        readiness_evaluated_count=readiness_evaluated_count,
        eligible_for_truth_adapter_count=eligible_for_truth_adapter_count,
        ineligible_for_truth_adapter_count=ineligible_for_truth_adapter_count,
        eligible_for_decision_consumption_count=eligible_for_decision_consumption_count,
        safe_for_decision_db_promotion_blocked_count=safe_for_decision_db_promotion_blocked_count,
        fail_closed_count=fail_closed_count,
        by_readiness_reason_code=by_readiness_reason_code,
        artifacts_with_source_linked_facts_count=artifacts_with_source_linked_facts_count,
        artifacts_without_source_linked_facts_count=artifacts_without_source_linked_facts_count,
        phase5_ready_but_decision_blocked_count=phase5_ready_but_decision_blocked_count,
        readiness_visible_snapshot_unchanged=True,
        artifacts_with_metric_observations_count=artifacts_with_metric_observations_count,
        metric_observation_fact_count=metric_observation_fact_count,
        by_metric_observation_tag=by_metric_observation_tag,
        by_metric_observation_unit=by_metric_observation_unit,
        by_metric_observation_form=by_metric_observation_form,
        artifacts_with_companyfacts_metric_observations_count=artifacts_with_companyfacts_metric_observations_count,
        # Phase 8A: SEC metric truth adapter dry-run fields.
        sec_metric_truth_adapter_dry_run_enabled=dry_run_result is not None,
        sec_metric_truth_adapter_dry_run_safe_for_decision=False,
        sec_metric_truth_adapter_artifacts_evaluated_count=(
            dry_run_result.artifacts_evaluated_count if dry_run_result else 0
        ),
        sec_metric_truth_adapter_source_linked_metric_fact_count=(
            dry_run_result.source_linked_metric_fact_count if dry_run_result else 0
        ),
        sec_metric_truth_adapter_unmapped_metric_fact_count=(
            dry_run_result.unmapped_metric_fact_count if dry_run_result else 0
        ),
        sec_metric_truth_adapter_by_ticker=(
            dry_run_result.by_ticker if dry_run_result else {}
        ),
        sec_metric_truth_adapter_by_bucket=(
            dry_run_result.by_bucket if dry_run_result else {}
        ),
        sec_metric_truth_adapter_by_tag=(
            dry_run_result.by_tag if dry_run_result else {}
        ),
        sec_metric_truth_adapter_by_unit=(
            dry_run_result.by_unit if dry_run_result else {}
        ),
        sec_metric_truth_adapter_by_form=(
            dry_run_result.by_form if dry_run_result else {}
        ),
        sec_metric_truth_adapter_missing_buckets_by_ticker=(
            dry_run_result.missing_buckets_by_ticker if dry_run_result else {}
        ),
        sec_metric_truth_adapter_visible_snapshot_unchanged=True,
        # Phase 8B: SEC metric evidence snapshot dry-run fields.
        sec_metric_evidence_snapshot_dry_run_enabled=snapshot_result is not None,
        sec_metric_evidence_snapshot_safe_for_decision=False,
        sec_metric_evidence_snapshot_visible_snapshot_unchanged=True,
        sec_metric_evidence_snapshot_tickers_evaluated_count=(
            snapshot_result.tickers_evaluated_count if snapshot_result else 0
        ),
        sec_metric_evidence_snapshot_tickers_with_any_source_linked_evidence_count=(
            snapshot_result.tickers_with_any_source_linked_evidence_count if snapshot_result else 0
        ),
        sec_metric_evidence_snapshot_tickers_ready_for_future_adapter_count=(
            snapshot_result.tickers_ready_for_future_adapter_count if snapshot_result else 0
        ),
        sec_metric_evidence_snapshot_tickers_blocked_from_decision_count=(
            snapshot_result.tickers_blocked_from_decision_count if snapshot_result else 0
        ),
        sec_metric_evidence_snapshot_by_ticker=(
            snapshot_result.by_ticker if snapshot_result else {}
        ),
    )

    if settings.intel_v3_research_artifact_observability_info_logs_enabled:
        logger.info(
            "artifact_observability_complete "
            "artifact_count=%d active=%d inactive=%d expired=%d "
            "safe_for_decision_false=%d unexpected_safe_true=%d "
            "forbidden_payload_violations=%d missing_evidence=%d "
            "with_sources=%d with_facts=%d visible_snapshot_unchanged=%s "
            "readiness_evaluated=%d eligible_truth_adapter=%d "
            "eligible_decision_consumption=%d phase5_ready_blocked=%d "
            "safe_for_decision_db_promotion_blocked=%d fail_closed=%d "
            "artifacts_with_metric_obs=%d metric_obs_facts=%d "
            "artifacts_with_companyfacts_obs=%d "
            "metric_obs_tags=%d metric_obs_units=%d metric_obs_forms=%d "
            "dry_run_enabled=%s dry_run_source_linked_facts=%d "
            "dry_run_unmapped=%d dry_run_buckets=%d",
            summary.artifact_count,
            summary.active_count,
            summary.inactive_count,
            summary.expired_count,
            summary.safe_for_decision_false_count,
            summary.unexpected_safe_for_decision_true_count,
            summary.forbidden_payload_violation_count,
            summary.missing_evidence_count,
            summary.artifacts_with_sources_count,
            summary.artifacts_with_facts_count,
            summary.visible_snapshot_unchanged,
            summary.readiness_evaluated_count,
            summary.eligible_for_truth_adapter_count,
            summary.eligible_for_decision_consumption_count,
            summary.phase5_ready_but_decision_blocked_count,
            summary.safe_for_decision_db_promotion_blocked_count,
            summary.fail_closed_count,
            summary.artifacts_with_metric_observations_count,
            summary.metric_observation_fact_count,
            summary.artifacts_with_companyfacts_metric_observations_count,
            len(summary.by_metric_observation_tag),
            len(summary.by_metric_observation_unit),
            len(summary.by_metric_observation_form),
            summary.sec_metric_truth_adapter_dry_run_enabled,
            summary.sec_metric_truth_adapter_source_linked_metric_fact_count,
            summary.sec_metric_truth_adapter_unmapped_metric_fact_count,
            len(summary.sec_metric_truth_adapter_by_bucket),
        )

    return summary
