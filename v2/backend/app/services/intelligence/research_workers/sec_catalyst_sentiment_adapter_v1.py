"""Stage 8C PR 2 — SEC/company catalyst evidence → sentiment_event v2 adapter.

Takes a SecEdgarProviderResult (filing metadata from the existing SEC EDGAR
provider) and produces a WorkerOutput with:
  artifact_type = "sentiment_event"
  skill_pack    = "sec_catalyst_sentiment_evidence_v1"

Each filing (10-K, 10-Q, 8-K) is mapped deterministically to:
  - catalyst_category  (earnings / corporate_action)
  - materiality        (HIGH / MEDIUM)
  - freshness_status   (FRESH / STALE based on filing_date age vs. today)
  - source_authority   PRIMARY_AUTHORITY — SEC filings are official company-authored
                       documents; Stage 5B credibility registry confirms this for
                       source_kind="sec_filing".
  - ticker_match_confidence  HIGH — exact CIK match through SEC EDGAR confirms the
                              filing belongs to this exact ticker's registrant.
  - sentiment_polarity       None — SEC filings do not provide scored polarity.
  - completeness_band  COMPLETE for 10-K; PARTIAL for 10-Q and 8-K.

Routine/noisy filings are excluded before producing any artifact:
  - STALE events (beyond per-form freshness window).
  - Forms not in the material set (only 10-K, 10-Q, 8-K are processed).

Architecture contracts (non-negotiable):
  - Pure adapter: takes SecEdgarProviderResult, returns WorkerOutput.
  - No IO, no DB reads, no LLM calls, no new HTTP calls, no provider imports.
  - Reuses existing sec_edgar_provider.py data — no additional fetch requests.
  - No fabrication: None polarity is never mapped to neutral.
  - source_authority="PRIMARY_AUTHORITY" only for confirmed CIK-matched filings.
  - BTC/XRP/ETF ineligibility is enforced by the v2 adapter's ineligibility guard.
  - No forbidden payload keys (buy/sell/trim/hold/recommendation/action).
  - safe_for_decision: never touched.
  - Writes NO artifact when there are no material+fresh filings.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

from app.services.intelligence.research_workers.contracts import (
    AuditEventRecord,
    FactRecord,
    SourceRecord,
    WorkerInput,
    WorkerOutput,
    compute_input_fingerprint,
    compute_replay_idempotency_key,
)
from app.services.intelligence.v3.sentiment_event_adapter_v2 import (
    SentimentEventV2Input,
    SentimentEventV2Output,
    normalize_and_evaluate,
)

SEC_CATALYST_SKILL_PACK = "sec_catalyst_sentiment_evidence_v1"
SEC_CATALYST_ARTIFACT_TYPE = "sentiment_event"
SEC_CATALYST_MODEL_VERSION = "sec_catalyst_sentiment_adapter.v1"
_SCOPE_KIND = "ticker"

# ── Form-type deterministic attribute map ─────────────────────────────────────
# Maps SEC form type → (catalyst_category_raw, materiality_raw,
# completeness_band, freshness_window_days).
#
# freshness_window_days: how recently a filing must have been filed to be
# considered FRESH for catalyst-sentiment purposes.
#   10-K annual:    180 days (same cadence as sec_companyfacts_adapter_v1)
#   10-Q quarterly: 90 days  (quarterly cadence)
#   8-K event:      30 days  (material events lose catalyst relevance quickly)

@dataclass(frozen=True)
class _FormAttributes:
    catalyst_category_raw: str
    materiality_raw: str
    completeness_band: str
    freshness_window_days: int


_FORM_ATTRIBUTES: dict[str, _FormAttributes] = {
    "10-K": _FormAttributes(
        catalyst_category_raw="earnings",
        materiality_raw="high",
        completeness_band="COMPLETE",
        freshness_window_days=180,
    ),
    "10-Q": _FormAttributes(
        catalyst_category_raw="earnings",
        materiality_raw="medium",
        completeness_band="PARTIAL",
        freshness_window_days=90,
    ),
    "8-K": _FormAttributes(
        catalyst_category_raw="corporate_action",
        materiality_raw="high",
        completeness_band="PARTIAL",
        freshness_window_days=30,
    ),
}

_MATERIAL_FORMS: frozenset[str] = frozenset(_FORM_ATTRIBUTES.keys())


# ── Freshness helper ──────────────────────────────────────────────────────────

def _compute_freshness(filing_date_str: str, window_days: int, today: date) -> str:
    """Return FRESH if filing_date is within window_days of today, else STALE."""
    if not filing_date_str:
        return "STALE"
    try:
        filed = date.fromisoformat(filing_date_str[:10])
        age_days = (today - filed).days
        return "FRESH" if age_days <= window_days else "STALE"
    except Exception:
        return "STALE"


# ── Adapter result ────────────────────────────────────────────────────────────

@dataclass
class SecCatalystSentimentAdapterResult:
    """Structured result of the SEC catalyst sentiment adapter.

    Carries the data needed to build a WorkerOutput (or a no-artifact decision).
    """
    has_material_filings: bool
    sources: list[SourceRecord]
    facts: list[FactRecord]
    freshness_status: str
    confidence_or_trust_level: str
    source_refs_fingerprint: str
    artifact_payload_extra: dict[str, Any]
    summary: str
    limitations: list[str]
    source_window_start: Optional[str]
    source_window_end: Optional[str]
    # Structured log data
    catalyst_count: int
    skipped_stale_count: int
    skipped_routine_count: int
    best_tier: Optional[str]


def _build_no_filings_result(ticker: str, reason: str) -> SecCatalystSentimentAdapterResult:
    return SecCatalystSentimentAdapterResult(
        has_material_filings=False,
        sources=[],
        facts=[],
        freshness_status="UNKNOWN",
        confidence_or_trust_level="UNKNOWN",
        source_refs_fingerprint=f"sec_catalyst_no_filings:{ticker}:{reason}",
        artifact_payload_extra={"filing_count": 0, "catalyst_count": 0, "skip_reason": reason},
        summary=f"SEC catalyst sentiment for {ticker}: no material filings ({reason}).",
        limitations=[f"No material SEC filings found: {reason}."],
        source_window_start=None,
        source_window_end=None,
        catalyst_count=0,
        skipped_stale_count=0,
        skipped_routine_count=0,
        best_tier=None,
    )


def adapt_sec_catalyst_sentiment(
    ticker: str,
    provider_result: Any,  # SecEdgarProviderResult — imported lazily to avoid circular
    fetched_at: str,
    holding_context: Optional[dict[str, Any]] = None,
    _today: Optional[date] = None,
) -> SecCatalystSentimentAdapterResult:
    """Convert SecEdgarProviderResult filing list → catalyst sentiment adapter result.

    Args:
        ticker:          Upper-cased ticker symbol.
        provider_result: SecEdgarProviderResult from sec_edgar_provider.fetch_for_ticker().
        fetched_at:      ISO 8601 fetch timestamp for SourceRecord.fetched_at.
        holding_context: Optional holding metadata for asset-type ineligibility guard.
        _today:          Injectable date for deterministic tests. Defaults to today (UTC).

    Returns:
        SecCatalystSentimentAdapterResult with has_material_filings=False when
        there are no fresh material filings (no artifact should be written).
    """
    today = _today or datetime.now(timezone.utc).date()

    # ── Fail-soft: provider fetch failures ───────────────────────────────────
    if not provider_result.is_success:
        return _build_no_filings_result(
            ticker, f"provider_fetch_status:{provider_result.fetch_status}"
        )

    # ── Fail-soft: no CIK means we cannot confirm ticker match ───────────────
    if not provider_result.cik:
        return _build_no_filings_result(ticker, "no_cik")

    filings = provider_result.filings or []
    if not filings:
        return _build_no_filings_result(ticker, "no_filings")

    sources: list[SourceRecord] = []
    facts: list[FactRecord] = []

    skipped_stale_count = 0
    skipped_routine_count = 0
    catalyst_count = 0
    filing_dates_fresh: list[str] = []
    tiers_produced: list[str] = []

    for filing in filings:
        form_type = (filing.form_type or "").upper().strip()
        if form_type not in _MATERIAL_FORMS:
            skipped_routine_count += 1
            continue

        attrs = _FORM_ATTRIBUTES[form_type]
        freshness = _compute_freshness(
            filing.filing_date, attrs.freshness_window_days, today
        )

        # Routine/noisy: stale events are not decision-useful sentiment.
        if freshness == "STALE":
            skipped_stale_count += 1
            continue

        # Ticker match is confirmed by CIK — HIGH confidence.
        event_id = f"{ticker}:{filing.accession_number}"

        v2_inp = SentimentEventV2Input(
            ticker=ticker,
            event_id=event_id,
            source_authority="PRIMARY_AUTHORITY",
            source_kind="sec_filing",
            provider_name="sec_edgar",
            freshness_status=freshness,
            source_count=1,
            fact_count=1,
            is_contradicted=False,
            completeness_band=attrs.completeness_band,
            sentiment_polarity=None,
            catalyst_category_raw=attrs.catalyst_category_raw,
            materiality_raw=attrs.materiality_raw,
            ticker_match_confidence_raw="high",
            event_published_at=filing.filing_date,
            source_url=filing.filing_url,
            holding_context=holding_context,
        )
        v2_out: SentimentEventV2Output = normalize_and_evaluate(v2_inp)

        # Ineligible assets (crypto/ETF) — still skip, no artifact.
        from app.services.intelligence.v3.sentiment_event_adapter_v2 import (
            DECISION_USEFULNESS_INELIGIBLE,
        )
        if v2_out.decision_usefulness_tier == DECISION_USEFULNESS_INELIGIBLE:
            skipped_routine_count += 1
            continue

        source_idx = len(sources)
        sources.append(SourceRecord(
            source_kind="sec_filing",
            provider_name="sec_edgar",
            provider_version=SEC_CATALYST_MODEL_VERSION,
            source_url=v2_out.safe_source_url,
            source_id=filing.accession_number,
            source_published_at=filing.filing_date,
            fetched_at=fetched_at,
            section_reference=form_type,
        ))

        # Stage 5D comparable-fact fix: catalyst_item facts must carry claim_key +
        # text_value so the contradiction detector counts them as comparable
        # (comparable_fact_count > 0). Without these, _compute_band() returns
        # BAND_THIN → SUPPRESSED_INCOMPLETE even when the v2 quality gate passes.
        # catalyst_event_type / catalyst_category are non-metric structured fields —
        # they do not fabricate sentiment_polarity or trigger a policy action.
        fact_payload = {
            **v2_out.structured_payload,
            "claim_key": "catalyst_event_type",
            "text_value": v2_out.catalyst_category,
        }

        facts.append(FactRecord(
            fact_kind="catalyst_item",
            structured_payload=fact_payload,
            axis_hint="catalyst",
            period=filing.report_date,
            as_of=filing.filing_date,
            is_quote_grounded=False,  # derived classification, not a direct quote
            source_index=source_idx,
        ))

        catalyst_count += 1
        filing_dates_fresh.append(filing.filing_date)
        tiers_produced.append(v2_out.decision_usefulness_tier)

    if not sources:
        reason = "stale_only" if skipped_stale_count > 0 else "no_material_fresh_filings"
        return SecCatalystSentimentAdapterResult(
            has_material_filings=False,
            sources=[],
            facts=[],
            freshness_status="UNKNOWN",
            confidence_or_trust_level="UNKNOWN",
            source_refs_fingerprint=f"sec_catalyst_no_material:{ticker}:{reason}",
            artifact_payload_extra={"filing_count": len(filings), "catalyst_count": 0, "skip_reason": reason},
            summary=f"SEC catalyst sentiment for {ticker}: no material fresh filings ({reason}).",
            limitations=[f"No material fresh SEC filings found: {reason}."],
            source_window_start=None,
            source_window_end=None,
            catalyst_count=0,
            skipped_stale_count=skipped_stale_count,
            skipped_routine_count=skipped_routine_count,
            best_tier=None,
        )

    # ── Overall freshness and confidence ─────────────────────────────────────
    freshness_status = "FRESH" if filing_dates_fresh else "STALE"
    # All sources are CIK-confirmed SEC EDGAR PRIMARY_AUTHORITY.
    confidence_or_trust_level = "HIGH"

    # Source window: earliest → latest filing date.
    filing_dates_sorted = sorted(filing_dates_fresh)
    source_window_start = filing_dates_sorted[0] if filing_dates_sorted else None
    source_window_end = filing_dates_sorted[-1] if filing_dates_sorted else None

    # Deterministic fingerprint from sorted accession numbers of contributing filings.
    accessions_sorted = sorted(s.source_id for s in sources if s.source_id)
    fp_data = {
        "ticker": ticker,
        "provider": "sec_edgar",
        "cik": provider_result.cik,
        "accessions": accessions_sorted,
    }
    source_refs_fingerprint = hashlib.sha256(
        json.dumps(fp_data, sort_keys=True).encode()
    ).hexdigest()[:32]

    # Best tier: READY > LIMITED > NOT_USABLE.
    _tier_rank = {"READY": 3, "LIMITED": 2, "NOT_USABLE": 1, "INELIGIBLE": 0}
    best_tier = max(tiers_produced, key=lambda t: _tier_rank.get(t, 0)) if tiers_produced else None

    from app.services.intelligence.v3.sentiment_event_adapter_v2 import (
        DECISION_USEFULNESS_READY, DECISION_USEFULNESS_LIMITED,
    )
    usable_count = sum(
        1 for t in tiers_produced
        if t in (DECISION_USEFULNESS_READY, DECISION_USEFULNESS_LIMITED)
    )

    summary = (
        f"SEC catalyst sentiment for {ticker}: {catalyst_count} material filing(s) "
        f"(freshness=FRESH, cik={provider_result.cik}). "
        f"{usable_count} usable catalyst event(s). No polarity scoring."
    )
    limitations = [
        "SEC filings provide form-level catalyst classification only — no item-level 8-K parsing.",
        "sentiment_polarity is always None — SEC filings do not provide scored polarity.",
        "Freshness windows: 10-K=180d, 10-Q=90d, 8-K=30d.",
    ]
    if skipped_stale_count > 0:
        limitations.append(
            f"{skipped_stale_count} filing(s) excluded as STALE beyond per-form freshness window."
        )

    return SecCatalystSentimentAdapterResult(
        has_material_filings=True,
        sources=sources,
        facts=facts,
        freshness_status=freshness_status,
        confidence_or_trust_level=confidence_or_trust_level,
        source_refs_fingerprint=source_refs_fingerprint,
        artifact_payload_extra={
            "filing_count": len(filings),
            "catalyst_count": catalyst_count,
            "usable_count": usable_count,
            "skipped_stale_count": skipped_stale_count,
            "skipped_routine_count": skipped_routine_count,
            "cik": provider_result.cik,
            "best_decision_usefulness_tier": best_tier,
        },
        summary=summary,
        limitations=limitations,
        source_window_start=source_window_start,
        source_window_end=source_window_end,
        catalyst_count=catalyst_count,
        skipped_stale_count=skipped_stale_count,
        skipped_routine_count=skipped_routine_count,
        best_tier=best_tier,
    )


# ── WorkerOutput builder ──────────────────────────────────────────────────────

def build_sec_catalyst_sentiment_worker_output(
    worker_input: WorkerInput,
    provider_result: Any,  # SecEdgarProviderResult
    fetched_at: str,
) -> Optional[WorkerOutput]:
    """Build WorkerOutput for the SEC catalyst sentiment lane.

    Returns None when there are no fresh material filings to write.
    Returns WorkerOutput when at least one fresh material filing was processed.

    Args:
        worker_input:    Standard WorkerInput (user_id, ticker, etc.).
        provider_result: SecEdgarProviderResult from sec_edgar_provider.
        fetched_at:      ISO 8601 fetch timestamp.
    """
    ticker = (worker_input.ticker or "").upper().strip()

    result = adapt_sec_catalyst_sentiment(
        ticker=ticker,
        provider_result=provider_result,
        fetched_at=fetched_at,
        holding_context=worker_input.holding_context,
    )

    if not result.has_material_filings:
        return None

    fp_data: dict[str, Any] = {
        "skill_pack": SEC_CATALYST_SKILL_PACK,
        "ticker": ticker,
        "model_version": SEC_CATALYST_MODEL_VERSION,
        "phase": "stage8c_sec_catalyst_sentiment",
    }
    if worker_input.holding_context:
        fp_data["context_keys"] = sorted(worker_input.holding_context.keys())
    input_fingerprint = compute_input_fingerprint(fp_data)

    replay_key = compute_replay_idempotency_key(
        skill_pack=SEC_CATALYST_SKILL_PACK,
        scope_kind=_SCOPE_KIND,
        ticker=ticker,
        source_refs_fingerprint=result.source_refs_fingerprint,
        model_version=SEC_CATALYST_MODEL_VERSION,
    )

    payload: dict[str, Any] = {
        "lane": SEC_CATALYST_SKILL_PACK,
        "reviewed_ticker": ticker,
        "worker_phase": "stage8c_sec_catalyst_sentiment",
        "provider": "sec_edgar",
    }
    payload.update(result.artifact_payload_extra)

    audit_events: list[AuditEventRecord] = [
        AuditEventRecord(
            tool_call=f"{SEC_CATALYST_SKILL_PACK}_run",
            status="completed",
            model_id=None,
            model_version=SEC_CATALYST_MODEL_VERSION,
            cost_estimate_usd=0.0,
            latency_ms=0,
        )
    ]

    return WorkerOutput(
        worker_run_id=worker_input.worker_run_id,
        ticker=ticker,
        artifact_type=SEC_CATALYST_ARTIFACT_TYPE,
        skill_pack=SEC_CATALYST_SKILL_PACK,
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
        model_version=SEC_CATALYST_MODEL_VERSION,
    )
