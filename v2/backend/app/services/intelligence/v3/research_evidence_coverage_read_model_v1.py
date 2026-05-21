"""Stage 5J — Research Evidence Coverage Read Model v1.

Deterministic, read-only summarization layer over previously-written Stage 5A–5I
research artifacts. Answers the question:

    "For this user's portfolio right now, which research evidence lanes have a
    usable artifact, which are missing, which are suppressed, and which are
    stale/unknown — without fabricating any evidence and without changing any
    visible Buy/Hold/Trim/Sell decision?"

Architecture contracts (non-negotiable):
  - Pure read function. No writes to research_artifacts. No writes to
    intel_v3_snapshots or recommendations. No DB schema changes.
  - NEVER calls decide(). NEVER imports decision_policy_v1.
  - NEVER triggers an evidence run, an LLM call, a provider call, or page-load
    work.
  - NEVER fabricates a missing artifact: a missing lane is reported as MISSING,
    not as "no evidence found yet" with a fake assessment.
  - Preserves Stage 5E suppression semantics: USABLE / USABLE_WITH_LIMITATIONS
    are the only "available" labels; everything else is unavailable evidence.
  - safe_for_decision is always False. This is an evidence-readiness bridge,
    not a recommendation engine.
  - Returns deterministic typed output that can later feed an evidence-to-
    decision adapter — but does not itself wire any decision.

Lane registry (the canonical map between coverage-lane name and the underlying
artifact identity tuple, as produced by Stages 5F/5H/5I writers):

    Ticker-scope lanes:
      sec_company_facts -> (fundamental_quality, sec_companyfacts_evidence_v1)
      fundamentals      -> (fundamental_quality, fundamentals_evidence_v1)
      technicals        -> (technical_signal,    technicals_evidence_v1)
      news_sentiment    -> (sentiment_event,     news_sentiment_evidence_v1)

    Portfolio-scope lane:
      macro_context     -> (portfolio_exposure,  fred_macro_evidence_v1)

Duplicate / idempotency handling:
  Stage 5A's clean-replacement policy already guarantees at most one active
  artifact per (user, artifact_type, skill_pack, scope_kind, COALESCE(ticker,'')).
  This read model defensively re-picks the latest by generated_at within the
  active set if the DB ever returns more than one (e.g. mid-write race).

Coverage status values (per lane × ticker):
  READY:             USABLE artifact present.
  LIMITED:           USABLE_WITH_LIMITATIONS artifact present.
  SUPPRESSED:        artifact present but a SUPPRESSED_* label was assigned.
  NOT_EVALUABLE:     artifact present but enrichment metadata not evaluable.
  STALE_OR_UNKNOWN:  artifact present but freshness_status is STALE/UNKNOWN
                     (overrides downstream interpretation only — does not
                     reclassify usability).
  MISSING:           no active artifact exists for this lane × ticker.

Diagnostics safety:
  Returned summary contains only aggregate metadata: artifact_id, lane
  identity, usability label, authority band, completeness band, freshness,
  contradiction flag, generated_at, model_version, suppression_reason.
  NEVER returns: raw payloads, source URLs, source quotes, fact contents,
  API keys, secrets, or user PII.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from app.services.intelligence.v3.sentiment_quality_threshold_v1 import (
    SENTINEL_EDITORIAL_CONTEXT_REASON,
)

logger = logging.getLogger(__name__)

READ_MODEL_VERSION = "research_evidence_coverage.v1"


# ── Lane registry ─────────────────────────────────────────────────────────────

LANE_SEC_COMPANY_FACTS = "sec_company_facts"
LANE_FUNDAMENTALS = "fundamentals"
LANE_TECHNICALS = "technicals"
LANE_NEWS_SENTIMENT = "news_sentiment"
LANE_MACRO_CONTEXT = "macro_context"

# (lane_name, artifact_type, skill_pack)
TICKER_LANE_REGISTRY: tuple[tuple[str, str, str], ...] = (
    (LANE_SEC_COMPANY_FACTS, "fundamental_quality", "sec_companyfacts_evidence_v1"),
    (LANE_FUNDAMENTALS,      "fundamental_quality", "fundamentals_evidence_v1"),
    (LANE_TECHNICALS,        "technical_signal",    "technicals_evidence_v1"),
    (LANE_NEWS_SENTIMENT,    "sentiment_event",     "news_sentiment_evidence_v1"),
)

MACRO_LANE_IDENTITY: tuple[str, str, str] = (
    LANE_MACRO_CONTEXT, "portfolio_exposure", "fred_macro_evidence_v1",
)


# ── Coverage status constants ─────────────────────────────────────────────────

STATUS_READY = "READY"
STATUS_LIMITED = "LIMITED"
STATUS_SUPPRESSED = "SUPPRESSED"
STATUS_NOT_EVALUABLE = "NOT_EVALUABLE"
STATUS_STALE_OR_UNKNOWN = "STALE_OR_UNKNOWN"
STATUS_MISSING = "MISSING"

_AVAILABLE_STATUSES = frozenset({STATUS_READY, STATUS_LIMITED})


# Stage 5E label constants (kept local to avoid a hard import on the truth
# adapter for what is structurally a read-only consumer of stored strings).
_LABEL_USABLE = "USABLE"
_LABEL_USABLE_WITH_LIMITATIONS = "USABLE_WITH_LIMITATIONS"
_LABEL_NOT_EVALUABLE = "NOT_EVALUABLE"
_SUPPRESSED_PREFIX = "SUPPRESSED_"


# ── Typed output ──────────────────────────────────────────────────────────────


@dataclass
class LaneCoverage:
    """Coverage entry for one (lane, ticker) — or for the portfolio-scope macro
    lane (ticker is None).

    Every field is safe to surface in a diagnostics response: no raw payloads,
    no source URLs, no fact contents.
    """
    lane: str
    artifact_type: str
    skill_pack: str
    scope_kind: str               # "ticker" or "portfolio"
    ticker: Optional[str]         # None for portfolio-scope
    artifact_id: Optional[str]
    status: str                   # READY | LIMITED | SUPPRESSED | NOT_EVALUABLE | STALE_OR_UNKNOWN | MISSING
    usability_label: Optional[str]
    is_usable: bool               # True only when status in {READY, LIMITED}
    suppression_reason: Optional[str]
    source_authority: Optional[str]     # strongest_authority_level
    completeness_band: Optional[str]    # COMPLETE | PARTIAL | THIN | NOT_EVALUABLE
    has_contradictions: Optional[bool]
    freshness_status: Optional[str]
    confidence_or_trust_level: Optional[str]
    model_version: Optional[str]
    generated_at: Optional[str]
    expires_at: Optional[str]
    # Diagnostic: why this lane is not usable. None when status is READY or LIMITED.
    # Values: "no_active_artifact" | "freshness_stale" | "freshness_unknown" |
    #         "usability_suppressed" | "usability_not_evaluable" |
    #         "editorial_context_present_not_decision_useful" (news_sentiment only:
    #           artifact exists, editorial-context authority, present but not useful) |
    #         "suppressed_data_quality_issue" (news_sentiment only: suppressed for a
    #           non-editorial reason such as contradictions or unknown source)
    missing_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "artifact_type": self.artifact_type,
            "skill_pack": self.skill_pack,
            "scope_kind": self.scope_kind,
            "ticker": self.ticker,
            "artifact_id": self.artifact_id,
            "status": self.status,
            "usability_label": self.usability_label,
            "is_usable": self.is_usable,
            "suppression_reason": self.suppression_reason,
            "source_authority": self.source_authority,
            "completeness_band": self.completeness_band,
            "has_contradictions": self.has_contradictions,
            "freshness_status": self.freshness_status,
            "confidence_or_trust_level": self.confidence_or_trust_level,
            "model_version": self.model_version,
            "generated_at": self.generated_at,
            "expires_at": self.expires_at,
            "missing_reason": self.missing_reason,
        }


@dataclass
class TickerCoverage:
    ticker: str
    lanes: dict[str, LaneCoverage] = field(default_factory=dict)
    ready_lane_count: int = 0
    limited_lane_count: int = 0
    suppressed_lane_count: int = 0
    missing_lane_count: int = 0
    stale_or_unknown_lane_count: int = 0
    not_evaluable_lane_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "lanes": {k: v.to_dict() for k, v in self.lanes.items()},
            "ready_lane_count": self.ready_lane_count,
            "limited_lane_count": self.limited_lane_count,
            "suppressed_lane_count": self.suppressed_lane_count,
            "missing_lane_count": self.missing_lane_count,
            "stale_or_unknown_lane_count": self.stale_or_unknown_lane_count,
            "not_evaluable_lane_count": self.not_evaluable_lane_count,
        }


@dataclass
class ResearchEvidenceCoverageSummary:
    schema_version: str
    user_id: str
    generated_at: str
    portfolio_ticker_count: int
    ticker_coverage: dict[str, TickerCoverage]
    portfolio_macro_coverage: LaneCoverage
    lane_counts: dict[str, int]            # lane -> total active artifacts found
    usability_counts: dict[str, int]       # usability label string -> count
    missing_lane_counts: dict[str, int]    # lane -> count of (ticker,lane) missing
    suppressed_counts: dict[str, int]      # suppression label -> count
    stale_or_unknown_counts: dict[str, int]  # lane -> count of stale/unknown
    ready_artifact_count: int              # total ready (READY or LIMITED) across portfolio
    safe_for_decision: bool = False
    no_guessing: bool = True
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "user_id": self.user_id,
            "generated_at": self.generated_at,
            "portfolio_ticker_count": self.portfolio_ticker_count,
            "ticker_coverage": {k: v.to_dict() for k, v in self.ticker_coverage.items()},
            "portfolio_macro_coverage": self.portfolio_macro_coverage.to_dict(),
            "lane_counts": dict(self.lane_counts),
            "usability_counts": dict(self.usability_counts),
            "missing_lane_counts": dict(self.missing_lane_counts),
            "suppressed_counts": dict(self.suppressed_counts),
            "stale_or_unknown_counts": dict(self.stale_or_unknown_counts),
            "ready_artifact_count": self.ready_artifact_count,
            "safe_for_decision": self.safe_for_decision,
            "no_guessing": self.no_guessing,
            "errors": list(self.errors),
        }


# ── Safe artifact column subset for read query ────────────────────────────────

# Includes payload because Stage 5B/5C/5D/5E enrichment lives there. We extract
# only the small assessment subdicts; we never re-emit the raw payload.
_READ_COLUMNS = (
    "id,artifact_type,skill_pack,scope_kind,ticker,"
    "confidence_or_trust_level,freshness_status,generated_at,expires_at,"
    "is_active,model_version,safe_for_decision,payload"
)


# ── Public API ────────────────────────────────────────────────────────────────


def compute_research_evidence_coverage(
    *,
    user_id: str,
    tickers: Iterable[str],
    db_client: Any,
) -> ResearchEvidenceCoverageSummary:
    """Compute a deterministic coverage summary over active research artifacts.

    Args:
        user_id:    The user whose artifacts we are summarizing.
        tickers:    The current portfolio's tickers. Used to drive missing-lane
                    detection — a ticker without any artifact for a lane is
                    reported as MISSING (not omitted). Order is preserved and
                    duplicates are dropped, but case is normalized to upper.
        db_client:  Supabase-compatible client. Must support the same
                    chained-builder API used elsewhere in the v3 services.

    Returns:
        ResearchEvidenceCoverageSummary — always non-None. DB errors are
        captured in .errors and produce an empty/MISSING summary; this read
        model never raises into the caller's path.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    norm_tickers = _normalize_tickers(tickers)
    errors: list[str] = []

    rows = _fetch_active_artifact_rows(user_id=user_id, db_client=db_client, errors=errors)

    ticker_coverage: dict[str, TickerCoverage] = {t: TickerCoverage(ticker=t) for t in norm_tickers}

    lane_counts: dict[str, int] = {}
    usability_counts: dict[str, int] = {}
    missing_lane_counts: dict[str, int] = {}
    suppressed_counts: dict[str, int] = {}
    stale_or_unknown_counts: dict[str, int] = {}
    ready_artifact_count = 0

    # ── Per-ticker lanes ──────────────────────────────────────────────────────
    for lane_name, artifact_type, skill_pack in TICKER_LANE_REGISTRY:
        for ticker in norm_tickers:
            row = _pick_latest_active(
                rows,
                artifact_type=artifact_type,
                skill_pack=skill_pack,
                scope_kind="ticker",
                ticker=ticker,
            )
            cov = _build_lane_coverage(
                lane=lane_name,
                artifact_type=artifact_type,
                skill_pack=skill_pack,
                scope_kind="ticker",
                ticker=ticker,
                row=row,
            )
            ticker_coverage[ticker].lanes[lane_name] = cov
            _accumulate(
                cov,
                lane_counts=lane_counts,
                usability_counts=usability_counts,
                missing_lane_counts=missing_lane_counts,
                suppressed_counts=suppressed_counts,
                stale_or_unknown_counts=stale_or_unknown_counts,
            )
            if cov.is_usable:
                ready_artifact_count += 1
            _bump_ticker_counters(ticker_coverage[ticker], cov)

    # ── Portfolio-scope macro lane ────────────────────────────────────────────
    lane_name, artifact_type, skill_pack = MACRO_LANE_IDENTITY
    macro_row = _pick_latest_active(
        rows,
        artifact_type=artifact_type,
        skill_pack=skill_pack,
        scope_kind="portfolio",
        ticker=None,
    )
    macro_coverage = _build_lane_coverage(
        lane=lane_name,
        artifact_type=artifact_type,
        skill_pack=skill_pack,
        scope_kind="portfolio",
        ticker=None,
        row=macro_row,
    )
    _accumulate(
        macro_coverage,
        lane_counts=lane_counts,
        usability_counts=usability_counts,
        missing_lane_counts=missing_lane_counts,
        suppressed_counts=suppressed_counts,
        stale_or_unknown_counts=stale_or_unknown_counts,
    )
    if macro_coverage.is_usable:
        ready_artifact_count += 1

    return ResearchEvidenceCoverageSummary(
        schema_version=READ_MODEL_VERSION,
        user_id=user_id,
        generated_at=now_iso,
        portfolio_ticker_count=len(norm_tickers),
        ticker_coverage=ticker_coverage,
        portfolio_macro_coverage=macro_coverage,
        lane_counts=lane_counts,
        usability_counts=usability_counts,
        missing_lane_counts=missing_lane_counts,
        suppressed_counts=suppressed_counts,
        stale_or_unknown_counts=stale_or_unknown_counts,
        ready_artifact_count=ready_artifact_count,
        errors=errors,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for t in tickers or ():
        if not isinstance(t, str):
            continue
        norm = t.strip().upper()
        if not norm:
            continue
        seen.setdefault(norm, None)
    return list(seen.keys())


def _fetch_active_artifact_rows(
    *,
    user_id: str,
    db_client: Any,
    errors: list[str],
) -> list[dict[str, Any]]:
    """Read all active artifacts for this user. Fail-soft on any DB error."""
    try:
        result = (
            db_client.table("research_artifacts")
            .select(_READ_COLUMNS)
            .eq("user_id", user_id)
            .eq("is_active", True)
            .execute()
        )
        rows = result.data or []
        # Defensive: only dicts.
        return [r for r in rows if isinstance(r, dict)]
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "research_evidence_coverage_query_failure user_id=%s error=%s",
            user_id,
            exc,
        )
        errors.append(f"query_failure:{type(exc).__name__}")
        return []


def _pick_latest_active(
    rows: list[dict[str, Any]],
    *,
    artifact_type: str,
    skill_pack: str,
    scope_kind: str,
    ticker: Optional[str],
) -> Optional[dict[str, Any]]:
    """Return the most-recent active row for one evidence lane identity.

    Stage 5A guarantees at most one active row, but we still pick deterministically
    by generated_at desc as a defensive measure for mid-write races.
    """
    matches: list[dict[str, Any]] = []
    for r in rows:
        if r.get("artifact_type") != artifact_type:
            continue
        if r.get("skill_pack") != skill_pack:
            continue
        if r.get("scope_kind") != scope_kind:
            continue
        row_ticker = r.get("ticker")
        if ticker is None:
            if row_ticker is not None:
                continue
        else:
            if not isinstance(row_ticker, str) or row_ticker.strip().upper() != ticker:
                continue
        matches.append(r)

    if not matches:
        return None
    matches.sort(key=lambda r: r.get("generated_at") or "", reverse=True)
    return matches[0]


def _build_lane_coverage(
    *,
    lane: str,
    artifact_type: str,
    skill_pack: str,
    scope_kind: str,
    ticker: Optional[str],
    row: Optional[dict[str, Any]],
) -> LaneCoverage:
    if row is None:
        return LaneCoverage(
            lane=lane,
            artifact_type=artifact_type,
            skill_pack=skill_pack,
            scope_kind=scope_kind,
            ticker=ticker,
            artifact_id=None,
            status=STATUS_MISSING,
            usability_label=None,
            is_usable=False,
            suppression_reason=None,
            source_authority=None,
            completeness_band=None,
            has_contradictions=None,
            freshness_status=None,
            confidence_or_trust_level=None,
            model_version=None,
            generated_at=None,
            expires_at=None,
            missing_reason="no_active_artifact",
        )

    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}

    usability = payload.get("truth_usability_assessment") if isinstance(payload, dict) else None
    credibility = payload.get("source_credibility_assessment") if isinstance(payload, dict) else None
    contradiction = payload.get("contradiction_assessment") if isinstance(payload, dict) else None
    completeness = payload.get("evidence_completeness_assessment") if isinstance(payload, dict) else None

    usability_label = _safe_str(usability.get("usability_label")) if isinstance(usability, dict) else None
    is_usable_field = bool(usability.get("is_usable")) if isinstance(usability, dict) else False
    suppression_reason = _safe_str(usability.get("suppression_reason")) if isinstance(usability, dict) else None

    source_authority = (
        _safe_str(credibility.get("strongest_authority_level")) if isinstance(credibility, dict) else None
    )
    completeness_band = (
        _safe_str(completeness.get("completeness_band")) if isinstance(completeness, dict) else None
    )
    has_contradictions: Optional[bool] = None
    if isinstance(contradiction, dict):
        if contradiction.get("is_evaluable") is False:
            has_contradictions = None
        else:
            has_contradictions = bool(contradiction.get("has_contradictions"))

    freshness_status = _safe_str(row.get("freshness_status"))
    confidence = _safe_str(row.get("confidence_or_trust_level"))

    if lane == LANE_NEWS_SENTIMENT:
        # Stage 8B: use sentiment-specific classifier to sub-classify suppression reasons.
        status, missing_reason = _classify_sentiment_status(
            usability_label=usability_label,
            is_usable_field=is_usable_field,
            freshness_status=freshness_status,
            source_authority=source_authority,
        )
    else:
        status, missing_reason = _classify_status(
            usability_label=usability_label,
            is_usable_field=is_usable_field,
            freshness_status=freshness_status,
        )

    return LaneCoverage(
        lane=lane,
        artifact_type=artifact_type,
        skill_pack=skill_pack,
        scope_kind=scope_kind,
        ticker=ticker,
        artifact_id=_safe_str(row.get("id")),
        status=status,
        usability_label=usability_label,
        is_usable=status in _AVAILABLE_STATUSES,
        suppression_reason=suppression_reason,
        source_authority=source_authority,
        completeness_band=completeness_band,
        has_contradictions=has_contradictions,
        freshness_status=freshness_status,
        confidence_or_trust_level=confidence,
        model_version=_safe_str(row.get("model_version")),
        generated_at=_safe_str(row.get("generated_at")),
        expires_at=_safe_str(row.get("expires_at")),
        missing_reason=missing_reason,
    )


def _classify_status(
    *,
    usability_label: Optional[str],
    is_usable_field: bool,
    freshness_status: Optional[str],
) -> tuple[str, Optional[str]]:
    """Map the Stage 5E label + DB freshness into a coverage status + missing_reason.

    Freshness STALE/UNKNOWN overrides only the *unsuppressed* available labels
    (USABLE / USABLE_WITH_LIMITATIONS). Suppressed and not-evaluable labels are
    reported as such — re-classifying a SUPPRESSED_CONTRADICTED artifact as
    "stale" would hide the real reason it cannot be consumed.

    Returns: (status, missing_reason). missing_reason is None when status is
    READY or LIMITED (lane is usable — no reason needed).
    """
    if usability_label is None:
        return STATUS_NOT_EVALUABLE, "usability_not_evaluable"
    if usability_label == _LABEL_USABLE:
        if _is_stale_or_unknown(freshness_status):
            reason = (
                "freshness_stale"
                if isinstance(freshness_status, str) and freshness_status.upper() == "STALE"
                else "freshness_unknown"
            )
            return STATUS_STALE_OR_UNKNOWN, reason
        return (STATUS_READY, None) if is_usable_field else (STATUS_NOT_EVALUABLE, "usability_not_evaluable")
    if usability_label == _LABEL_USABLE_WITH_LIMITATIONS:
        if _is_stale_or_unknown(freshness_status):
            reason = (
                "freshness_stale"
                if isinstance(freshness_status, str) and freshness_status.upper() == "STALE"
                else "freshness_unknown"
            )
            return STATUS_STALE_OR_UNKNOWN, reason
        return (STATUS_LIMITED, None) if is_usable_field else (STATUS_NOT_EVALUABLE, "usability_not_evaluable")
    if usability_label.startswith(_SUPPRESSED_PREFIX):
        return STATUS_SUPPRESSED, "usability_suppressed"
    if usability_label == _LABEL_NOT_EVALUABLE:
        return STATUS_NOT_EVALUABLE, "usability_not_evaluable"
    return STATUS_NOT_EVALUABLE, "usability_not_evaluable"


# Weak authority levels for sentiment sub-classification (Stage 8B).
_SENTIMENT_EDITORIAL_AUTHORITY_LEVELS = frozenset({"EDITORIAL_CONTEXT", "UNKNOWN"})


def _classify_sentiment_status(
    *,
    usability_label: Optional[str],
    is_usable_field: bool,
    freshness_status: Optional[str],
    source_authority: Optional[str],
) -> tuple[str, Optional[str]]:
    """Like _classify_status but with sentiment-specific missing_reason sub-classification.

    For SUPPRESSED news_sentiment artifacts, distinguishes:
      - editorial_context_present_not_decision_useful: SUPPRESSED_INCOMPLETE caused by
        EDITORIAL_CONTEXT or UNKNOWN authority — the artifact is present but editorial
        context only, which is correct suppression by design (not a data quality error).
      - suppressed_data_quality_issue: suppressed for another reason (contradictions,
        unknown source flagged by a non-editorial route, etc.).

    For USABLE / USABLE_WITH_LIMITATIONS and MISSING / NOT_EVALUABLE cases, delegates
    to the generic _classify_status — no change in behaviour.
    """
    status, missing_reason = _classify_status(
        usability_label=usability_label,
        is_usable_field=is_usable_field,
        freshness_status=freshness_status,
    )
    if status == STATUS_SUPPRESSED:
        if (
            usability_label == "SUPPRESSED_INCOMPLETE"
            and source_authority in _SENTIMENT_EDITORIAL_AUTHORITY_LEVELS
        ):
            return STATUS_SUPPRESSED, SENTINEL_EDITORIAL_CONTEXT_REASON
        return STATUS_SUPPRESSED, "suppressed_data_quality_issue"
    return status, missing_reason


def _is_stale_or_unknown(freshness_status: Optional[str]) -> bool:
    if not isinstance(freshness_status, str):
        return True
    upper = freshness_status.upper()
    return upper in {"STALE", "UNKNOWN"}


def _safe_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    if value is None:
        return None
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return None


def _accumulate(
    cov: LaneCoverage,
    *,
    lane_counts: dict[str, int],
    usability_counts: dict[str, int],
    missing_lane_counts: dict[str, int],
    suppressed_counts: dict[str, int],
    stale_or_unknown_counts: dict[str, int],
) -> None:
    if cov.status == STATUS_MISSING:
        missing_lane_counts[cov.lane] = missing_lane_counts.get(cov.lane, 0) + 1
        return

    lane_counts[cov.lane] = lane_counts.get(cov.lane, 0) + 1
    if cov.usability_label:
        usability_counts[cov.usability_label] = usability_counts.get(cov.usability_label, 0) + 1
    if cov.status == STATUS_SUPPRESSED and cov.usability_label:
        suppressed_counts[cov.usability_label] = suppressed_counts.get(cov.usability_label, 0) + 1
    if cov.status == STATUS_STALE_OR_UNKNOWN:
        stale_or_unknown_counts[cov.lane] = stale_or_unknown_counts.get(cov.lane, 0) + 1


def _bump_ticker_counters(tc: TickerCoverage, cov: LaneCoverage) -> None:
    if cov.status == STATUS_READY:
        tc.ready_lane_count += 1
    elif cov.status == STATUS_LIMITED:
        tc.limited_lane_count += 1
    elif cov.status == STATUS_SUPPRESSED:
        tc.suppressed_lane_count += 1
    elif cov.status == STATUS_MISSING:
        tc.missing_lane_count += 1
    elif cov.status == STATUS_STALE_OR_UNKNOWN:
        tc.stale_or_unknown_lane_count += 1
    elif cov.status == STATUS_NOT_EVALUABLE:
        tc.not_evaluable_lane_count += 1


def log_coverage_summary(summary: ResearchEvidenceCoverageSummary) -> None:
    """Emit a compact one-line structured log of the coverage summary.

    Safe to call after explicit evidence dispatch. No raw payloads, no secrets.
    """
    logger.info(
        "research_evidence_coverage_summary user_id=%s portfolio_ticker_count=%d "
        "ready_artifact_count=%d lane_counts=%s usability_counts=%s "
        "missing_lane_counts=%s suppressed_counts=%s stale_or_unknown_counts=%s "
        "macro_status=%s macro_artifact_id=%s",
        summary.user_id,
        summary.portfolio_ticker_count,
        summary.ready_artifact_count,
        summary.lane_counts,
        summary.usability_counts,
        summary.missing_lane_counts,
        summary.suppressed_counts,
        summary.stale_or_unknown_counts,
        summary.portfolio_macro_coverage.status,
        summary.portfolio_macro_coverage.artifact_id or "none",
    )
