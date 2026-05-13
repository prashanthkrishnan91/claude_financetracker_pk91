"""Canonical Evidence Artifact contract (Stage 3.0b v1 — §2 of north-star).

Every piece of evidence the platform consumes — regardless of source —
conforms to the `EvidenceArtifact` shape defined here. Mappers in this
module translate existing repo rows (recommendations, agent_insights,
portfolio price points, …) into artifacts without rewriting them. This is
the boundary contract every future consumer (confidence calibration,
decision replay, Opportunity Scout shortlists) reads against.

Pure module — no IO, no DB, no LLM, no provider calls. Mappers are
side-effect-free and accept either ORM rows or plain dicts.

Reference: `docs/ai/INVESTMENT_INTELLIGENCE_PLATFORM_NORTH_STAR.md` §2.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional


# ── Enums ────────────────────────────────────────────────────────────────────

class AssetType(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    CRYPTO = "crypto"
    BOND = "bond"
    INDEX = "index"
    MACRO = "macro"
    OTHER = "other"


class SourceQuality(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class TrustStatus(str, Enum):
    TRUSTED = "trusted"
    PARTIAL = "partial_trust"
    UNCERTIFIED = "uncertified"


class RateLimitStatus(str, Enum):
    OK = "ok"
    NEAR_LIMIT = "near_limit"
    LIMITED = "limited"
    UNKNOWN = "unknown"


# ── Allowed policy axes ──────────────────────────────────────────────────────
#
# Stable names used by `decide()` consumers. Artifacts list which axes they
# may influence; any consumer that respects the contract refuses to read a
# field outside that artifact's allowed axes. This is the spine that keeps
# research/analyst artifacts from owning final action authority.

POLICY_AXIS_ACTION = "action"
POLICY_AXIS_SIZING = "sizing"
POLICY_AXIS_RISK = "risk"
POLICY_AXIS_PORTFOLIO_FIT = "portfolio_fit"
POLICY_AXIS_CONTEXT = "context"  # informational only — never an axis decide() reads


# ── Artifact dataclass ───────────────────────────────────────────────────────

@dataclass
class EvidenceArtifact:
    """Single canonical evidence artifact. See north-star §2 for field semantics."""
    ticker: Optional[str]
    asset_type: AssetType
    source_class: str
    source_name: str
    evidence_type: str
    value: Any
    produced_at: Optional[datetime]
    fetched_at: Optional[datetime]
    certified_at: Optional[datetime]
    expires_at: Optional[datetime]
    freshness_sla_hours: Optional[float]
    source_quality: SourceQuality
    confidence: Optional[float]
    trust_status: TrustStatus
    allowed_policy_axis: list[str] = field(default_factory=list)
    evidence_id: str = ""
    source_id: Optional[str] = None
    provider_error: Optional[str] = None
    rate_limit_status: RateLimitStatus = RateLimitStatus.UNKNOWN
    error_reason: Optional[str] = None
    policy_version: str = "v3.1"

    def __post_init__(self) -> None:
        if not self.evidence_id:
            self.evidence_id = _content_hash_id(self)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe serialization for diagnostics / snapshot embedding."""
        d = asdict(self)
        for k in ("produced_at", "fetched_at", "certified_at", "expires_at"):
            v = d.get(k)
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        # Convert enums to their string values.
        d["asset_type"] = self.asset_type.value
        d["source_quality"] = self.source_quality.value
        d["trust_status"] = self.trust_status.value
        d["rate_limit_status"] = self.rate_limit_status.value
        return d

    def is_inside_sla(self, now: Optional[datetime] = None) -> bool:
        """True iff this artifact's age is within freshness_sla_hours.

        Returns False when certified_at is unknown or SLA is unset — caller
        decides how to treat unknown freshness, but the artifact never claims
        to be inside SLA without evidence.
        """
        now = now or datetime.now(timezone.utc)
        if self.certified_at is None or self.freshness_sla_hours is None:
            return False
        age_h = (now - self.certified_at).total_seconds() / 3600.0
        return age_h <= float(self.freshness_sla_hours)

    def with_downgraded_trust(self, reason: str) -> "EvidenceArtifact":
        """Return a copy whose trust_status is downgraded one step.

        TRUSTED → PARTIAL → UNCERTIFIED (no change once at UNCERTIFIED).
        Records the reason in error_reason without mutating the original.
        """
        next_status = {
            TrustStatus.TRUSTED: TrustStatus.PARTIAL,
            TrustStatus.PARTIAL: TrustStatus.UNCERTIFIED,
            TrustStatus.UNCERTIFIED: TrustStatus.UNCERTIFIED,
        }[self.trust_status]
        new = EvidenceArtifact(**{**asdict(self), "trust_status": next_status, "error_reason": reason})
        # Restore enum types after the asdict round-trip.
        new.asset_type = self.asset_type
        new.source_quality = self.source_quality
        new.trust_status = next_status
        new.rate_limit_status = self.rate_limit_status
        return new


# ── Mappers — existing repo rows → EvidenceArtifact ──────────────────────────

def _content_hash_id(artifact: "EvidenceArtifact") -> str:
    """Deterministic 16-char id derived from artifact identity fields."""
    key = "|".join([
        artifact.ticker or "",
        artifact.source_class,
        artifact.source_name,
        artifact.evidence_type,
        artifact.certified_at.isoformat() if artifact.certified_at else "",
        json.dumps(artifact.value, sort_keys=True, default=str)[:128],
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _trust_from_age(
    certified_at: Optional[datetime],
    sla_hours: Optional[float],
    now: Optional[datetime] = None,
) -> TrustStatus:
    """Map an age vs SLA window into a trust status.

    Rules (no fabricated freshness):
      - unknown certified_at → UNCERTIFIED
      - inside SLA → TRUSTED
      - between SLA and 4×SLA → PARTIAL
      - beyond 4×SLA → UNCERTIFIED
    """
    if certified_at is None or sla_hours is None:
        return TrustStatus.UNCERTIFIED
    now = now or datetime.now(timezone.utc)
    age_h = (now - certified_at).total_seconds() / 3600.0
    if age_h <= sla_hours:
        return TrustStatus.TRUSTED
    if age_h <= sla_hours * 4.0:
        return TrustStatus.PARTIAL
    return TrustStatus.UNCERTIFIED


def map_recommendation_row(
    row: dict[str, Any],
    *,
    sla_hours: float = 24.0,
    now: Optional[datetime] = None,
) -> EvidenceArtifact:
    """Map a `recommendations` row to an EvidenceArtifact.

    recommendations.action is analyst-derived persisted evidence — never the
    final visible action. allowed_policy_axis reflects that boundary.
    """
    ticker = (row.get("ticker") or "").upper() or None
    certified_at = _parse_dt(row.get("created_at"))
    fetched_at = certified_at
    return EvidenceArtifact(
        ticker=ticker,
        asset_type=AssetType.STOCK,
        source_class="analyst_thesis",
        source_name="recommendations_table",
        evidence_type="analyst_action",
        value={
            "action": (row.get("action") or "").upper() or None,
            "technical_signal": row.get("technical_signal"),
            "conviction_score": row.get("conviction_score"),
        },
        produced_at=certified_at,
        fetched_at=fetched_at,
        certified_at=certified_at,
        expires_at=None,
        freshness_sla_hours=sla_hours,
        source_quality=SourceQuality.MEDIUM,
        confidence=_to_float(row.get("conviction_score")),
        trust_status=_trust_from_age(certified_at, sla_hours, now=now),
        allowed_policy_axis=[POLICY_AXIS_ACTION, POLICY_AXIS_CONTEXT],
        source_id=str(row.get("id")) if row.get("id") is not None else None,
    )


def map_agent_insight_row(
    insight: dict[str, Any],
    run: dict[str, Any] | None,
    *,
    sla_hours: float = 48.0,
    now: Optional[datetime] = None,
) -> EvidenceArtifact:
    """Map an `agent_insights` row (joined with its `agent_runs` row) to an artifact.

    analyst_verdict is LLM/agent-produced. allowed_policy_axis includes
    action only because the existing decide() already consumes analyst
    drivers/risks — but the artifact records source_class=analyst_thesis so
    confidence calibration can independently downgrade stale analyst evidence.
    """
    ticker = (insight.get("ticker") or "").upper() or None
    finished_at = _parse_dt((run or {}).get("finished_at"))
    av = insight.get("analyst_verdict") or {}
    if not isinstance(av, dict):
        av = {}
    return EvidenceArtifact(
        ticker=ticker,
        asset_type=AssetType.STOCK,
        source_class="analyst_thesis",
        source_name="agent_orchestrator",
        evidence_type="analyst_verdict",
        value={
            "analyst_action":     av.get("action"),
            "conviction_level":   av.get("conviction_level"),
            "drivers":            av.get("drivers"),
            "risks":              av.get("risks"),
            "risk_flag":          av.get("risk_flag"),
            "data_quality_label": av.get("data_quality_label"),
            "used_fallback":      bool(av.get("used_fallback", False)),
        },
        produced_at=finished_at,
        fetched_at=finished_at,
        certified_at=finished_at,
        expires_at=None,
        freshness_sla_hours=sla_hours,
        source_quality=SourceQuality.MEDIUM,
        confidence=_to_float(insight.get("analyst_confidence")),
        trust_status=_trust_from_age(finished_at, sla_hours, now=now),
        allowed_policy_axis=[POLICY_AXIS_ACTION, POLICY_AXIS_RISK, POLICY_AXIS_CONTEXT],
        source_id=str((run or {}).get("id")) if (run or {}).get("id") else None,
    )


def map_portfolio_position(
    position_row: dict[str, Any],
    *,
    snapshot_at: Optional[str | datetime] = None,
    sla_hours: float = 24.0,
    now: Optional[datetime] = None,
) -> EvidenceArtifact:
    """Map a position entry (from `positions` or `portfolio_snapshots.positions_data`).

    Portfolio state never owns action axis — only sizing / portfolio_fit.
    """
    ticker = (position_row.get("ticker") or "").upper() or None
    certified_at = _parse_dt(
        position_row.get("market_value_certified_at")
        or snapshot_at
        or position_row.get("updated_at")
    )
    market_value = position_row.get("market_value_usd") or position_row.get("market_value")
    return EvidenceArtifact(
        ticker=ticker,
        asset_type=_asset_type_from_category(position_row.get("category")),
        source_class="portfolio_state",
        source_name="portfolio_service",
        evidence_type="position_market_value",
        value={
            "shares":           _to_float(position_row.get("shares")),
            "avg_cost":         _to_float(position_row.get("avg_cost")),
            "market_value_usd": _to_float(market_value),
            "category":         position_row.get("category"),
        },
        produced_at=certified_at,
        fetched_at=certified_at,
        certified_at=certified_at,
        expires_at=None,
        freshness_sla_hours=sla_hours,
        source_quality=SourceQuality.HIGH,
        confidence=None,
        trust_status=_trust_from_age(certified_at, sla_hours, now=now),
        allowed_policy_axis=[POLICY_AXIS_SIZING, POLICY_AXIS_PORTFOLIO_FIT, POLICY_AXIS_CONTEXT],
    )


def map_price_result(
    ticker: str,
    price_result: Any,
    *,
    now: Optional[datetime] = None,
    sla_hours: float = 0.25,
) -> EvidenceArtifact:
    """Map a `PriceResult` (or dict shaped like one) to a market_price artifact.

    PriceResult.is_stale=True or is_valid=False → trust downgraded.
    """
    now = now or datetime.now(timezone.utc)
    if isinstance(price_result, dict):
        is_valid = bool(price_result.get("is_valid"))
        is_stale = bool(price_result.get("is_stale"))
        source = price_result.get("source") or "unknown"
        mid_price = price_result.get("mid_price")
        provider_error = price_result.get("error")
    else:
        is_valid = bool(getattr(price_result, "is_valid", False))
        is_stale = bool(getattr(price_result, "is_stale", False))
        source = getattr(price_result, "source", None) or "unknown"
        mid_price = getattr(price_result, "mid_price", None)
        provider_error = getattr(price_result, "error", None)

    if is_valid and not is_stale:
        trust = TrustStatus.TRUSTED
        certified_at = now
    elif is_valid and is_stale:
        trust = TrustStatus.PARTIAL
        certified_at = None
    else:
        trust = TrustStatus.UNCERTIFIED
        certified_at = None

    return EvidenceArtifact(
        ticker=(ticker or "").upper() or None,
        asset_type=AssetType.STOCK,
        source_class="market_price",
        source_name=str(source).split("(")[0],
        evidence_type="latest_price",
        value={"mid_price": _to_float(mid_price), "raw_source": source},
        produced_at=certified_at,
        fetched_at=now,
        certified_at=certified_at,
        expires_at=None,
        freshness_sla_hours=sla_hours,
        source_quality=SourceQuality.HIGH if (is_valid and not is_stale) else SourceQuality.LOW,
        confidence=None,
        trust_status=trust,
        allowed_policy_axis=[POLICY_AXIS_SIZING, POLICY_AXIS_CONTEXT],
        provider_error=str(provider_error) if provider_error else None,
        rate_limit_status=RateLimitStatus.LIMITED if (provider_error and "limit" in str(provider_error).lower()) else RateLimitStatus.OK,
    )


# ── Aggregation helpers ──────────────────────────────────────────────────────

def summarize_artifact_set(artifacts: Iterable[EvidenceArtifact]) -> dict[str, Any]:
    """Compact per-source-class summary for snapshot diagnostics.

    Pure function. Counts trust statuses per source class and reports the
    oldest certified_at observed. Used by the orchestrator to surface the
    artifact-contract view of the snapshot without embedding the raw set.
    """
    by_class: dict[str, dict[str, Any]] = {}
    for art in artifacts:
        cls = art.source_class
        slot = by_class.setdefault(cls, {
            "count":          0,
            "trusted":        0,
            "partial":        0,
            "uncertified":    0,
            "oldest_certified_at": None,
            "with_provider_error": 0,
            "rate_limited":   0,
        })
        slot["count"] += 1
        if art.trust_status == TrustStatus.TRUSTED:
            slot["trusted"] += 1
        elif art.trust_status == TrustStatus.PARTIAL:
            slot["partial"] += 1
        else:
            slot["uncertified"] += 1
        if art.provider_error:
            slot["with_provider_error"] += 1
        if art.rate_limit_status == RateLimitStatus.LIMITED:
            slot["rate_limited"] += 1
        if art.certified_at:
            prev = slot["oldest_certified_at"]
            iso = art.certified_at.isoformat()
            if prev is None or iso < prev:
                slot["oldest_certified_at"] = iso
    return by_class


# ── Internal helpers ─────────────────────────────────────────────────────────

def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _asset_type_from_category(category: Any) -> AssetType:
    if not isinstance(category, str):
        return AssetType.STOCK
    cat = category.upper()
    if cat in ("ETF",):
        return AssetType.ETF
    if cat in ("CRYPTO", "BTC", "ETH"):
        return AssetType.CRYPTO
    if cat in ("BOND",):
        return AssetType.BOND
    return AssetType.STOCK
