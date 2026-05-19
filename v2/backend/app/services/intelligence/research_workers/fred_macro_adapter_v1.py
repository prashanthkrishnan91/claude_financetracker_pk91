"""Stage 5I — FRED Official Macro Evidence Adapter v1 (pure, no IO).

Converts a FredProviderResult into a single portfolio-scope WorkerOutput
suitable for ResearchArtifactServiceV1.write_artifact().

artifact_type = "portfolio_exposure"  (existing DB constraint — see TODO below)
skill_pack    = "fred_macro_evidence_v1"
model_version = "fred_official_macro_v1"
scope_kind    = "portfolio"            (one artifact per explicit run; ticker IS NULL)
source_kind   = "other"                (existing DB enum; FRED authority encoded
                                        at the provider_registry layer — see TODO)

TODO (Stage 5J+): the semantically correct artifact_type for this lane is
`macro_context` and the semantically correct source_kind is something like
`official_macro_data`. Both require SQL migrations against migration 017's
CHECK constraints. Stage 5I deliberately uses the least misleading existing
enums to avoid SQL surface area; the `provider="fred"` payload key and the
`fred_macro_evidence_v1` skill_pack keep this lane distinguishable from
`portfolio_exposure` artifacts written by future workers.

Stage 5I patch — provider-aware credibility (no SQL):
  Until the dedicated source_kind enum is added, the Stage 5B
  source_credibility_registry_v1 applies a narrow provider-aware override
  that classifies sources with (source_kind="other", provider_name="fred",
  source_id in FRED allowlist OR source_url matching fred.stlouisfed.org)
  as PRIMARY_AUTHORITY / OFFICIAL_PUBLIC_DATA for CLAIM_OFFICIAL_MACRO_DATA
  only. Generic source_kind="other" sources from unknown providers stay
  UNKNOWN/INSUFFICIENT. This keeps the artifact source-grounded and usable
  through Stage 5E truth adapter without weakening UNKNOWN handling.

Source linking:
  One SourceRecord per successful FRED series. provider_name="fred",
  source_id=series_id, source_published_at=last_updated when available.

Fact mapping:
  One FactRecord per recent observation per series, preserving:
    series_id, observation_date, value, units, frequency, realtime_start,
    realtime_end, fred_category.
  No directional inferences, no investment conclusions, no derived signals.

No-data / error path:
  When provider_result has no usable series, returns an honest thin-evidence
  result with limitations recorded. Never fabricates values, dates, or units.

Hard constraints (non-negotiable):
  - Never calls decide() or imports the decision policy.
  - Never writes to intel_v3_snapshots or recommendations.
  - safe_for_decision always False.
  - No new LLM calls.
  - No paid provider activation.
  - No fabrication of missing observations.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
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
from .fred_provider_v1 import (
    FredObservation,
    FredProviderResult,
    FredSeriesFetchResult,
)

# ── Artifact type / skill_pack / model_version ────────────────────────────────

_ARTIFACT_TYPE = "portfolio_exposure"        # see module TODO
_SKILL_PACK = "fred_macro_evidence_v1"
_MODEL_VERSION = "fred_official_macro_v1"
_SCOPE_KIND = "portfolio"
_PROVIDER_NAME = "fred"
_SOURCE_KIND = "other"                       # see module TODO
_FRESHNESS_FRESH_DAYS = 60                   # macro releases cadence (monthly/quarterly)


# ── Internal result type ──────────────────────────────────────────────────────


@dataclass
class _AdapterResult:
    sources: list[SourceRecord] = field(default_factory=list)
    facts: list[FactRecord] = field(default_factory=list)
    confidence_or_trust_level: str = "UNKNOWN"
    freshness_status: str = "UNKNOWN"
    source_refs_fingerprint: str = "no_data"
    summary: str = ""
    limitations: list[str] = field(default_factory=list)
    source_window_start: Optional[str] = None
    source_window_end: Optional[str] = None
    artifact_payload_extra: dict[str, Any] = field(default_factory=dict)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _latest_observation_date(observations: list[FredObservation]) -> Optional[str]:
    dates = [o.date for o in observations if o.date]
    if not dates:
        return None
    return max(dates)


def _earliest_observation_date(observations: list[FredObservation]) -> Optional[str]:
    dates = [o.date for o in observations if o.date]
    if not dates:
        return None
    return min(dates)


def _freshness_from_latest_dates(
    latest_dates: list[str],
    fetched_at: str,
) -> str:
    """FRESH if any successful series has a value within _FRESHNESS_FRESH_DAYS."""
    if not latest_dates:
        return "UNKNOWN"
    try:
        now = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except Exception:
        now = datetime.now(timezone.utc)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)

    newest: Optional[datetime] = None
    for d in latest_dates:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if newest is None or dt > newest:
            newest = dt
    if newest is None:
        return "UNKNOWN"
    age_days = (now - newest).days
    return "FRESH" if age_days <= _FRESHNESS_FRESH_DAYS else "STALE"


def _confidence_from_series_count(series_count: int) -> str:
    """Deterministic mapping from successful-series count to confidence band.

    Authority of FRED itself is encoded in the provider_registry; this band
    reflects breadth of macro coverage in the artifact.
    """
    if series_count >= 6:
        return "HIGH"
    if series_count >= 3:
        return "MEDIUM"
    if series_count >= 1:
        return "LOW"
    return "UNKNOWN"


def _compute_macro_digest(
    successful: list[FredSeriesFetchResult],
) -> str:
    """Deterministic SHA-256 digest over successful series + their observations."""
    entries: list[dict[str, Any]] = []
    for s in successful:
        for o in s.observations:
            entries.append({
                "series_id": s.series_id,
                "date": o.date,
                "value": o.value,
                "realtime_start": o.realtime_start or "",
                "realtime_end": o.realtime_end or "",
            })
    entries.sort(key=lambda x: (x["series_id"], x["date"], x["realtime_start"]))
    raw = json.dumps(entries, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ── Public adapter function ───────────────────────────────────────────────────


def adapt_fred_macro(
    provider_result: FredProviderResult,
    fetched_at: str,
) -> _AdapterResult:
    """Convert FredProviderResult → adapter result.

    Honest thin-evidence when no series produced usable observations.
    Never fabricates.
    """
    # ── Global no-data paths ─────────────────────────────────────────────────
    if provider_result.fetch_status == "no_api_key":
        return _AdapterResult(
            source_refs_fingerprint="fred_no_api_key",
            summary=(
                "FRED macro evidence: FRED_API_KEY not configured — no API call "
                "was made. No macro observations available."
            ),
            limitations=[
                "FRED_API_KEY not set in environment — required for FRED API calls.",
            ],
            artifact_payload_extra={
                "fetch_status": "no_api_key",
                "series_attempted": 0,
                "series_succeeded": 0,
                "observation_count": 0,
            },
        )

    if not provider_result.successful_series:
        status = provider_result.fetch_status
        msg = provider_result.error_message or f"fetch_status={status}"
        return _AdapterResult(
            source_refs_fingerprint=f"fred_no_data_{status}",
            summary=(
                f"FRED macro evidence: no usable observations "
                f"(fetch_status={status})."
            ),
            limitations=[
                f"FRED fetch did not produce usable observations: {msg}",
                "No macro observations recorded.",
            ],
            artifact_payload_extra={
                "fetch_status": status,
                "series_attempted": len(provider_result.series_results),
                "series_succeeded": 0,
                "observation_count": 0,
            },
        )

    successful = provider_result.successful_series
    sources: list[SourceRecord] = []
    facts: list[FactRecord] = []
    latest_dates: list[str] = []
    earliest_dates: list[str] = []
    total_obs = 0

    for idx, series in enumerate(successful):
        meta = series.metadata
        title = meta.title if meta and meta.title else series.series_id
        units = meta.units if meta else None
        frequency = meta.frequency if meta else None
        last_updated = meta.last_updated if meta else None
        observation_start = meta.observation_start if meta else None
        observation_end = meta.observation_end if meta else None

        sources.append(SourceRecord(
            source_kind=_SOURCE_KIND,
            provider_name=_PROVIDER_NAME,
            provider_version=_MODEL_VERSION,
            source_url=(
                f"https://fred.stlouisfed.org/series/{series.series_id}"
            ),
            source_id=series.series_id,
            source_published_at=last_updated or fetched_at,
            fetched_at=fetched_at,
            section_reference=f"fred_series:{series.category or 'macro'}",
        ))

        latest = _latest_observation_date(series.observations)
        earliest = _earliest_observation_date(series.observations)
        if latest:
            latest_dates.append(latest)
        if earliest:
            earliest_dates.append(earliest)

        for obs in series.observations:
            facts.append(FactRecord(
                fact_kind="metric_observation",
                structured_payload={
                    "metric_name": series.series_id,
                    "metric_label": title,
                    "value": obs.value,
                    "unit": units,
                    "frequency": frequency,
                    "observation_date": obs.date,
                    "realtime_start": obs.realtime_start,
                    "realtime_end": obs.realtime_end,
                    "fred_category": series.category,
                    "macro_category": series.category,
                    "fred_last_updated": last_updated,
                    "fred_observation_start": observation_start,
                    "fred_observation_end": observation_end,
                    "provider": _PROVIDER_NAME,
                    "series_id": series.series_id,
                    "lane": "macro",
                },
                # DB CHECK constraint on research_artifact_facts.axis_hint allows only
                # {'evidence','risk','price','quality','catalyst','exposure'} or NULL
                # (migration 017). Stage 5I originally used 'macro' which violated the
                # constraint and failed the artifact write. Stage 5I.1 stores axis_hint
                # as NULL — true macro identity is preserved in structured_payload
                # (provider="fred", macro_category, series_id, metric_name,
                # observation_date) and in skill_pack="fred_macro_evidence_v1".
                axis_hint=None,
                period=obs.date,
                as_of=obs.date,
                is_quote_grounded=True,
                source_index=idx,
            ))
            total_obs += 1

    confidence = _confidence_from_series_count(len(successful))
    freshness = _freshness_from_latest_dates(latest_dates, fetched_at)
    fingerprint = f"fred_macro_{_compute_macro_digest(successful)}"

    source_window_start = min(earliest_dates) if earliest_dates else None
    source_window_end = max(latest_dates) if latest_dates else None

    limitations: list[str] = [
        "FRED macro observations are official Federal Reserve data — "
        "describe the economic environment, not investment recommendations.",
        "No directional interpretation or investment conclusion has been derived.",
        "Allowlisted macro series only (Fed funds, Treasury yields, CPI, "
        "unemployment, payrolls, GDP, optional yield spread). Not exhaustive.",
        "source_kind is stored as 'other' until a dedicated DB enum is added; "
        "the Stage 5B source credibility registry applies a narrow provider-aware "
        "override (provider=fred + allowlisted FRED series id / URL) so these "
        "sources classify as PRIMARY_AUTHORITY / OFFICIAL_PUBLIC_DATA for "
        "official_macro_data claims only — never for investment recommendations.",
    ]
    if provider_result.error_message:
        limitations.append(
            f"Partial fetch outcomes: {provider_result.error_message}"
        )
    skipped_or_failed = [
        s for s in provider_result.series_results if not s.is_success
    ]
    if skipped_or_failed:
        limitations.append(
            f"{len(skipped_or_failed)} requested series did not yield usable "
            "observations (see series_status payload)."
        )

    summary = (
        f"FRED macro evidence: {len(successful)} series with usable observations "
        f"(out of {len(provider_result.series_results)} requested). "
        f"Total observations={total_obs}. confidence={confidence}, "
        f"freshness={freshness}. Official Federal Reserve source. "
        f"No analyst opinions, no investment recommendations."
    )

    series_status = {
        s.series_id: {
            "fetch_status": s.fetch_status,
            "category": s.category,
            "observation_count": len(s.observations),
            "latest_observation_date": _latest_observation_date(s.observations),
        }
        for s in provider_result.series_results
    }

    return _AdapterResult(
        sources=sources,
        facts=facts,
        confidence_or_trust_level=confidence,
        freshness_status=freshness,
        source_refs_fingerprint=fingerprint,
        summary=summary,
        limitations=limitations,
        source_window_start=source_window_start,
        source_window_end=source_window_end,
        artifact_payload_extra={
            "fetch_status": provider_result.fetch_status,
            "series_attempted": len(provider_result.series_results),
            "series_succeeded": len(successful),
            "observation_count": total_obs,
            "series_status": series_status,
            "request_count": provider_result.request_count,
        },
    )


# ── WorkerOutput builder ──────────────────────────────────────────────────────


def build_fred_macro_worker_output(
    worker_input: WorkerInput,
    provider_result: FredProviderResult,
    fetched_at: str,
) -> WorkerOutput:
    """Build a portfolio-scope WorkerOutput from a FredProviderResult.

    This is the single callable that the runner uses to create the macro
    evidence artifact for ResearchArtifactServiceV1.write_artifact().
    """
    result = adapt_fred_macro(provider_result, fetched_at)

    fp_data: dict[str, Any] = {
        "skill_pack": _SKILL_PACK,
        "scope": _SCOPE_KIND,
        "model_version": _MODEL_VERSION,
        "phase": "stage5i_fred_macro",
        "series_attempted": result.artifact_payload_extra.get("series_attempted", 0),
        "series_succeeded": result.artifact_payload_extra.get("series_succeeded", 0),
    }
    input_fingerprint = compute_input_fingerprint(fp_data)

    replay_key = compute_replay_idempotency_key(
        skill_pack=_SKILL_PACK,
        scope_kind=_SCOPE_KIND,
        ticker="",                                  # portfolio-scope has no ticker
        source_refs_fingerprint=result.source_refs_fingerprint,
        model_version=_MODEL_VERSION,
    )

    payload: dict[str, Any] = {
        "lane": _SKILL_PACK,
        "scope": _SCOPE_KIND,
        "worker_phase": "stage5i_fred_macro_evidence",
        "provider": _PROVIDER_NAME,
    }
    payload.update(result.artifact_payload_extra)

    audit_events: list[AuditEventRecord] = [
        AuditEventRecord(
            tool_call="fred_macro_evidence_run",
            status="completed",
            model_id=None,
            model_version=_MODEL_VERSION,
            cost_estimate_usd=0.0,
            latency_ms=0,
        )
    ]

    return WorkerOutput(
        worker_run_id=worker_input.worker_run_id,
        ticker=None,                                # portfolio-scope, ticker IS NULL
        artifact_type=_ARTIFACT_TYPE,
        skill_pack=_SKILL_PACK,
        scope_kind=_SCOPE_KIND,
        artifact_payload=payload,
        sources=result.sources,
        facts=result.facts,
        audit_events=audit_events,
        evidence_summary_plain_english=result.summary,
        limitations_or_missing_evidence=result.limitations,
        confidence_or_trust_level=result.confidence_or_trust_level,
        freshness_status=result.freshness_status,
        input_fingerprint=input_fingerprint,
        replay_idempotency_key=replay_key,
        source_window_start=result.source_window_start,
        source_window_end=result.source_window_end,
        expires_at=None,
        parent_intel_run_id=worker_input.parent_intel_run_id,
        generated_by_model=None,
        model_version=_MODEL_VERSION,
    )
