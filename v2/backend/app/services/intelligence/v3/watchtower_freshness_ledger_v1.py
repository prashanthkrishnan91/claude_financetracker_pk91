"""Watchtower Fresh Evidence Foundation — freshness ledger contract v1 (Build 1D).

Single source of truth for:
  - Evidence type identifiers.
  - Per-type freshness SLAs (configurable constants, not scattered magic numbers).
  - FreshnessStatus classification (fresh / aging / stale / missing / failed).
  - Per-type deploy_eligible and decision_eligible rules.
  - EvidenceRecord dataclass capturing all required freshness fields.

Pure module — no IO, no DB, no LLM calls.

Design:
  - Deploy-critical types: price, position, portfolio_weight.
    These must be fresh before any dollar deployment plan is generated.
  - Decision-critical types: recommendation, analyst_llm, position.
    These gate Intel v3 certification (mirrors certified_intel_run_contract_v1).
  - Event-driven types: fundamental, sec_filing.
    Fresh if latest known filing/facts are current — not re-fetched every click.
    Currently "missing" (not yet collected by this app) → not blocking.
  - Optional enrichment types: news_sentiment, technical.
    news_sentiment is time-bound and cannot silently drive action if stale.
    technical/volatility: currently missing → reported honestly, not blocking.

SLAs use seconds for precision. All thresholds are named constants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ── Evidence type identifiers ─────────────────────────────────────────────────

EVIDENCE_TYPE_PRICE = "price"
EVIDENCE_TYPE_POSITION = "position"
EVIDENCE_TYPE_PORTFOLIO_WEIGHT = "portfolio_weight"
EVIDENCE_TYPE_TECHNICAL = "technical"
EVIDENCE_TYPE_FUNDAMENTAL = "fundamental"
EVIDENCE_TYPE_SEC_FILING = "sec_filing"
EVIDENCE_TYPE_NEWS_SENTIMENT = "news_sentiment"
EVIDENCE_TYPE_ANALYST_LLM = "analyst_llm"
EVIDENCE_TYPE_RECOMMENDATION = "recommendation"
EVIDENCE_TYPE_SNAPSHOT = "snapshot"

ALL_EVIDENCE_TYPES: frozenset[str] = frozenset({
    EVIDENCE_TYPE_PRICE,
    EVIDENCE_TYPE_POSITION,
    EVIDENCE_TYPE_PORTFOLIO_WEIGHT,
    EVIDENCE_TYPE_TECHNICAL,
    EVIDENCE_TYPE_FUNDAMENTAL,
    EVIDENCE_TYPE_SEC_FILING,
    EVIDENCE_TYPE_NEWS_SENTIMENT,
    EVIDENCE_TYPE_ANALYST_LLM,
    EVIDENCE_TYPE_RECOMMENDATION,
    EVIDENCE_TYPE_SNAPSHOT,
})


# ── Deploy eligibility rules ──────────────────────────────────────────────────
#
# These types, when stale or missing, block dollar deployment plans.
# LLM analyst text can explain but cannot override freshness blocks.

DEPLOY_CRITICAL_TYPES: frozenset[str] = frozenset({
    EVIDENCE_TYPE_PRICE,
    EVIDENCE_TYPE_POSITION,
    EVIDENCE_TYPE_PORTFOLIO_WEIGHT,
})

# Intel certification critical types — gates worker_certified snapshot.
# Mirrors conditions in certified_intel_run_contract_v1.py.
DECISION_CRITICAL_TYPES: frozenset[str] = frozenset({
    EVIDENCE_TYPE_RECOMMENDATION,
    EVIDENCE_TYPE_ANALYST_LLM,
    EVIDENCE_TYPE_POSITION,
})


# ── Freshness SLA constants (all in seconds) ──────────────────────────────────
#
# fresh_seconds  : age ≤ this → FRESH
# aging_seconds  : age > fresh_seconds and ≤ aging_seconds → AGING
# stale_seconds  : age > aging_seconds and ≤ stale_seconds → STALE
# beyond stale   : if evidence was collected → MISSING or FAILED
#
# These are the only source of freshness thresholds in the Watchtower layer.
# Do not hard-code these anywhere else.

@dataclass(frozen=True)
class EvidenceSLA:
    fresh_seconds: int
    aging_seconds: int
    stale_seconds: int


FRESHNESS_SLA_CONFIG: dict[str, EvidenceSLA] = {
    # Intel analysis — 15 min is sufficient for explanation/recommendation display
    EVIDENCE_TYPE_PRICE: EvidenceSLA(
        fresh_seconds=900,      # 15 min
        aging_seconds=3_600,    # 1 h
        stale_seconds=14_400,   # 4 h
    ),
    # Position/holdings: user-imported data; fresh if within a day
    EVIDENCE_TYPE_POSITION: EvidenceSLA(
        fresh_seconds=86_400,   # 24 h
        aging_seconds=172_800,  # 48 h
        stale_seconds=604_800,  # 7 d
    ),
    # Portfolio weights: derived from position market values
    EVIDENCE_TYPE_PORTFOLIO_WEIGHT: EvidenceSLA(
        fresh_seconds=86_400,   # 24 h
        aging_seconds=172_800,  # 48 h
        stale_seconds=604_800,  # 7 d
    ),
    # Technical / volatility / momentum: frequent market data
    EVIDENCE_TYPE_TECHNICAL: EvidenceSLA(
        fresh_seconds=14_400,   # 4 h
        aging_seconds=28_800,   # 8 h
        stale_seconds=86_400,   # 24 h
    ),
    # Fundamentals: event-driven; fresh if latest known filing/facts are current
    EVIDENCE_TYPE_FUNDAMENTAL: EvidenceSLA(
        fresh_seconds=604_800,  # 7 d
        aging_seconds=1_814_400,  # 21 d
        stale_seconds=5_184_000,  # 60 d
    ),
    # SEC filings: event-driven; only re-fetch on new filings
    EVIDENCE_TYPE_SEC_FILING: EvidenceSLA(
        fresh_seconds=604_800,  # 7 d
        aging_seconds=1_814_400,  # 21 d
        stale_seconds=5_184_000,  # 60 d
    ),
    # News/sentiment: time-bound; stale news cannot silently drive action
    EVIDENCE_TYPE_NEWS_SENTIMENT: EvidenceSLA(
        fresh_seconds=86_400,   # 24 h
        aging_seconds=172_800,  # 48 h
        stale_seconds=604_800,  # 7 d
    ),
    # Analyst LLM: explanation freshness (not Deploy authority)
    EVIDENCE_TYPE_ANALYST_LLM: EvidenceSLA(
        fresh_seconds=172_800,  # 48 h
        aging_seconds=432_000,  # 5 d
        stale_seconds=604_800,  # 7 d
    ),
    # Recommendation: deterministic policy output; gates Intel certification
    EVIDENCE_TYPE_RECOMMENDATION: EvidenceSLA(
        fresh_seconds=86_400,   # 24 h
        aging_seconds=172_800,  # 48 h
        stale_seconds=604_800,  # 7 d
    ),
    # Snapshot: the certified Intel v3 snapshot
    EVIDENCE_TYPE_SNAPSHOT: EvidenceSLA(
        fresh_seconds=14_400,   # 4 h
        aging_seconds=86_400,   # 24 h
        stale_seconds=604_800,  # 7 d
    ),
}


# ── Deploy SLA constants (stricter than Intel SLAs for price/weights) ────────
#
# Deploy-critical types require near-real-time freshness before any dollar
# deployment plan is generated. Separate from Intel SLAs, which govern when
# evidence is "current enough" for analysis and explanation display.
#
# PRICE deploy-fresh: 5 min — a 7-minute-old price is too old for dollar sizing.
# PORTFOLIO_WEIGHT deploy-fresh: same as price — weights are derived from
#   market values in the same portfolio snapshot; stale price → stale weight.
# POSITION: same as Intel SLA — user-imported data; 24 h is appropriate.

DEPLOY_SLA_CONFIG: dict[str, EvidenceSLA] = {
    EVIDENCE_TYPE_PRICE: EvidenceSLA(
        fresh_seconds=300,      # 5 min
        aging_seconds=900,      # 15 min (same as Intel fresh_seconds)
        stale_seconds=1_800,    # 30 min
    ),
    EVIDENCE_TYPE_POSITION: EvidenceSLA(
        fresh_seconds=86_400,   # 24 h — same as Intel; user-imported data
        aging_seconds=172_800,  # 48 h
        stale_seconds=604_800,  # 7 d
    ),
    EVIDENCE_TYPE_PORTFOLIO_WEIGHT: EvidenceSLA(
        fresh_seconds=300,      # 5 min — derived from price; same threshold
        aging_seconds=900,      # 15 min
        stale_seconds=1_800,    # 30 min
    ),
}


# ── Freshness status values ───────────────────────────────────────────────────

FRESHNESS_FRESH = "fresh"
FRESHNESS_AGING = "aging"
FRESHNESS_STALE = "stale"
FRESHNESS_MISSING = "missing"
FRESHNESS_FAILED = "failed"

_STALE_STATUSES = frozenset({FRESHNESS_STALE, FRESHNESS_MISSING, FRESHNESS_FAILED})
_INELIGIBLE_STATUSES = frozenset({FRESHNESS_MISSING, FRESHNESS_FAILED, FRESHNESS_STALE})


# ── EvidenceRecord — the canonical freshness record ───────────────────────────

@dataclass
class EvidenceRecord:
    """One freshness observation for a single evidence type and ticker/scope.

    Fields match the Watchtower Fresh Evidence Foundation v1 contract spec.
    as_of is what the data represents (e.g. the price timestamp).
    collected_at is when we gathered it from the source or DB.
    """
    evidence_type: str
    ticker: Optional[str]           # None for portfolio-level evidence
    scope: str                      # "ticker" | "portfolio"
    as_of: Optional[datetime]       # data's own timestamp
    collected_at: Optional[datetime]  # when we read/wrote it
    source: str                     # which provider / table
    freshness_status: str           # fresh | aging | stale | missing | failed
    freshness_sla_seconds: int      # max_age_seconds from config
    deploy_eligible: bool
    decision_eligible: bool
    reason: Optional[str]           # why not eligible, if applicable
    source_quality: Optional[str] = None
    confidence: Optional[float] = None
    last_error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_type":       self.evidence_type,
            "ticker":              self.ticker,
            "scope":               self.scope,
            "as_of":               self.as_of.isoformat() if self.as_of else None,
            "collected_at":        self.collected_at.isoformat() if self.collected_at else None,
            "source":              self.source,
            "freshness_status":    self.freshness_status,
            "freshness_sla_seconds": self.freshness_sla_seconds,
            "deploy_eligible":     self.deploy_eligible,
            "decision_eligible":   self.decision_eligible,
            "reason":              self.reason,
            "source_quality":      self.source_quality,
            "confidence":          self.confidence,
            "last_error":          self.last_error,
        }


# ── Classification helpers ────────────────────────────────────────────────────

def _parse_iso(ts: Any) -> Optional[datetime]:
    """Parse ISO timestamp string to tz-aware UTC datetime; None on failure."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def classify_freshness_status(
    *,
    evidence_type: str,
    as_of: Optional[datetime],
    collected_at: Optional[datetime],
    now: datetime,
    last_error: Optional[str] = None,
) -> str:
    """Classify a single evidence observation's freshness status.

    Uses collected_at (when we gathered it) as the primary freshness clock.
    Falls back to as_of if collected_at is absent.
    Returns FAILED if last_error is set and no timestamp is present.
    """
    if last_error and not collected_at and not as_of:
        return FRESHNESS_FAILED

    ref = collected_at or as_of
    if ref is None:
        return FRESHNESS_MISSING

    sla = FRESHNESS_SLA_CONFIG.get(evidence_type)
    if sla is None:
        # Unknown type — surface as missing rather than guessing
        return FRESHNESS_MISSING

    age_seconds = (now - ref).total_seconds()
    if age_seconds < 0:
        # Clock skew — treat as fresh
        return FRESHNESS_FRESH
    if age_seconds <= sla.fresh_seconds:
        return FRESHNESS_FRESH
    if age_seconds <= sla.aging_seconds:
        return FRESHNESS_AGING
    if age_seconds <= sla.stale_seconds:
        return FRESHNESS_STALE
    return FRESHNESS_STALE  # beyond stale_seconds → still STALE (not MISSING; data exists)


def classify_deploy_freshness_status(
    *,
    evidence_type: str,
    as_of: Optional[datetime],
    collected_at: Optional[datetime],
    now: datetime,
    last_error: Optional[str] = None,
) -> str:
    """Classify freshness status using Deploy SLAs for deploy-critical types.

    For non-deploy-critical types, falls back to the standard Intel SLA.
    Deploy SLAs are stricter: price/portfolio_weight require <5 min freshness
    before any dollar deployment plan is generated.
    """
    if evidence_type not in DEPLOY_CRITICAL_TYPES:
        return classify_freshness_status(
            evidence_type=evidence_type,
            as_of=as_of,
            collected_at=collected_at,
            now=now,
            last_error=last_error,
        )

    if last_error and not collected_at and not as_of:
        return FRESHNESS_FAILED

    ref = collected_at or as_of
    if ref is None:
        return FRESHNESS_MISSING

    sla = DEPLOY_SLA_CONFIG.get(evidence_type) or FRESHNESS_SLA_CONFIG.get(evidence_type)
    if sla is None:
        return FRESHNESS_MISSING

    age_seconds = (now - ref).total_seconds()
    if age_seconds < 0:
        return FRESHNESS_FRESH
    if age_seconds <= sla.fresh_seconds:
        return FRESHNESS_FRESH
    if age_seconds <= sla.aging_seconds:
        return FRESHNESS_AGING
    return FRESHNESS_STALE


def is_deploy_eligible_for_type(
    evidence_type: str,
    freshness_status: str,
) -> tuple[bool, Optional[str]]:
    """Return (deploy_eligible, reason) for one evidence record.

    Deploy-critical types must be FRESH or AGING.
    Non-deploy-critical types never block deploy regardless of staleness.
    LLM analyst text can explain but does not override deploy blocks.
    """
    if evidence_type not in DEPLOY_CRITICAL_TYPES:
        return True, None
    if freshness_status in (FRESHNESS_FRESH, FRESHNESS_AGING):
        return True, None
    reason = f"{evidence_type} is {freshness_status} — deploy blocked until refreshed"
    return False, reason


def is_decision_eligible_for_type(
    evidence_type: str,
    freshness_status: str,
) -> tuple[bool, Optional[str]]:
    """Return (decision_eligible, reason) for one evidence record.

    Decision-critical types must be FRESH or AGING for Intel certification.
    """
    if evidence_type not in DECISION_CRITICAL_TYPES:
        return True, None
    if freshness_status in (FRESHNESS_FRESH, FRESHNESS_AGING):
        return True, None
    reason = f"{evidence_type} is {freshness_status} — Intel certification blocked"
    return False, reason


def build_evidence_record(
    *,
    evidence_type: str,
    ticker: Optional[str],
    scope: str,
    as_of: Optional[datetime],
    collected_at: Optional[datetime],
    source: str,
    now: datetime,
    source_quality: Optional[str] = None,
    confidence: Optional[float] = None,
    last_error: Optional[str] = None,
) -> EvidenceRecord:
    """Construct a fully-classified EvidenceRecord."""
    sla = FRESHNESS_SLA_CONFIG.get(evidence_type)
    sla_seconds = sla.fresh_seconds if sla else 0

    freshness_status = classify_freshness_status(
        evidence_type=evidence_type,
        as_of=as_of,
        collected_at=collected_at,
        now=now,
        last_error=last_error,
    )
    # Deploy eligibility uses stricter Deploy SLAs for deploy-critical types.
    # freshness_status (Intel SLA) is kept for analysis/display; deploy_eligible
    # uses the separate Deploy SLA so dollar-deployment gates are independently strict.
    deploy_freshness = classify_deploy_freshness_status(
        evidence_type=evidence_type,
        as_of=as_of,
        collected_at=collected_at,
        now=now,
        last_error=last_error,
    )
    deploy_elig, deploy_reason = is_deploy_eligible_for_type(evidence_type, deploy_freshness)
    decision_elig, decision_reason = is_decision_eligible_for_type(evidence_type, freshness_status)
    reason = deploy_reason or decision_reason

    return EvidenceRecord(
        evidence_type=evidence_type,
        ticker=ticker,
        scope=scope,
        as_of=as_of,
        collected_at=collected_at,
        source=source,
        freshness_status=freshness_status,
        freshness_sla_seconds=sla_seconds,
        deploy_eligible=deploy_elig,
        decision_eligible=decision_elig,
        reason=reason,
        source_quality=source_quality,
        confidence=confidence,
        last_error=last_error,
    )
