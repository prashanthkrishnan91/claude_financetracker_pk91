"""Watchtower evidence collector v1 (Build 1D).

Reads existing DB tables to build EvidenceRecord observations for the freshness
ledger. No new tables required — derives state from:
  - portfolio_snapshots        → price freshness, weight freshness
  - positions                  → position freshness (user-imported data)
  - recommendations            → recommendation freshness
  - agent_insights             → analyst_llm freshness
  - intel_v3_snapshots         → snapshot freshness
  - research_artifacts         → technical evidence freshness (Stage 5F artifacts)

For evidence types not yet collected by this app (fundamental, sec_filing,
news_sentiment) it returns MISSING records — honestly, not silently.

For technical evidence: Stage 5F research artifacts (technical_signal type) with
is_usable=True are surfaced as FRESH/AGING EvidenceRecords per ticker. Stale or
absent artifacts fall back to a portfolio-scope MISSING record so the collector
remains honest about gaps.

Pure async read path. No writes, no LLM calls, no provider calls.
Fast by design: all reads are indexed queries on existing tables.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from .watchtower_freshness_ledger_v1 import (
    EVIDENCE_TYPE_ANALYST_LLM,
    EVIDENCE_TYPE_FUNDAMENTAL,
    EVIDENCE_TYPE_NEWS_SENTIMENT,
    EVIDENCE_TYPE_PORTFOLIO_WEIGHT,
    EVIDENCE_TYPE_POSITION,
    EVIDENCE_TYPE_PRICE,
    EVIDENCE_TYPE_RECOMMENDATION,
    EVIDENCE_TYPE_SEC_FILING,
    EVIDENCE_TYPE_SNAPSHOT,
    EVIDENCE_TYPE_TECHNICAL,
    FRESHNESS_AGING,
    FRESHNESS_FRESH,
    FRESHNESS_MISSING,
    EvidenceRecord,
    build_evidence_record,
)

logger = logging.getLogger(__name__)


async def collect_evidence_records(
    user_id: UUID,
    client: Any,
    *,
    now: Optional[datetime] = None,
) -> list[EvidenceRecord]:
    """Collect freshness observations from existing DB tables.

    Returns a list of EvidenceRecord for all evidence types we can assess.
    Types not yet collected (technical, fundamental, sec_filing, news_sentiment)
    surface as MISSING records so callers see the honest gap.

    Target: <200ms (all queries are single-row or indexed lookups).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    records: list[EvidenceRecord] = []

    # Run all DB reads concurrently
    (
        tickers,
        snap_data,
        rec_rows,
        insight_rows,
        snap_rows,
        usable_technical_arts,
    ) = await asyncio.gather(
        _fetch_tickers(user_id, client),
        _fetch_portfolio_snapshot(user_id, client),
        _fetch_latest_recommendations(user_id, client),
        _fetch_latest_agent_insights(user_id, client),
        _fetch_latest_intel_snapshot(user_id, client),
        _fetch_latest_usable_research_artifacts(user_id, client, artifact_type="technical_signal"),
        return_exceptions=True,
    )

    # Normalize exceptions to empty defaults
    if isinstance(tickers, Exception):
        logger.warning("watchtower.collect tickers_failed err=%s", tickers)
        tickers = []
    if isinstance(snap_data, Exception):
        logger.warning("watchtower.collect snapshot_failed err=%s", snap_data)
        snap_data = {}
    if isinstance(rec_rows, Exception):
        logger.warning("watchtower.collect recs_failed err=%s", rec_rows)
        rec_rows = {}
    if isinstance(insight_rows, Exception):
        logger.warning("watchtower.collect insights_failed err=%s", insight_rows)
        insight_rows = {}
    if isinstance(snap_rows, Exception):
        logger.warning("watchtower.collect intel_snap_failed err=%s", snap_rows)
        snap_rows = {}
    if isinstance(usable_technical_arts, Exception):
        logger.warning("watchtower.collect technical_arts_failed err=%s", usable_technical_arts)
        usable_technical_arts = {}

    # ── Price evidence ────────────────────────────────────────────────────────
    # Derived from portfolio_snapshots.positions_data[*].market_value_certified_at
    price_certs: dict[str, Optional[datetime]] = snap_data.get("price_certs", {})
    snap_at: Optional[datetime] = snap_data.get("snapshot_at")

    for ticker in (tickers or []):
        t = (ticker or "").upper()
        if not t:
            continue
        cert = price_certs.get(t)
        records.append(build_evidence_record(
            evidence_type=EVIDENCE_TYPE_PRICE,
            ticker=t,
            scope="ticker",
            as_of=cert,
            collected_at=cert,
            source="portfolio_snapshots.market_value_certified_at",
            now=now,
        ))

    # ── Position evidence ─────────────────────────────────────────────────────
    # Position deploy-freshness is tied to price certification, not snapshot_at.
    # After a Watchtower price refresh, market_value_certified_at is written per
    # position. A position is deploy-fresh only when its price was recently verified.
    for ticker in (tickers or []):
        t = (ticker or "").upper()
        if not t:
            continue
        pos_verified_at = price_certs.get(t) or snap_at
        records.append(build_evidence_record(
            evidence_type=EVIDENCE_TYPE_POSITION,
            ticker=t,
            scope="ticker",
            as_of=pos_verified_at,
            collected_at=pos_verified_at,
            source="portfolio_snapshots.market_value_certified_at",
            now=now,
        ))

    # ── Portfolio weight evidence ─────────────────────────────────────────────
    # Portfolio weights are derived from the same snapshot
    records.append(build_evidence_record(
        evidence_type=EVIDENCE_TYPE_PORTFOLIO_WEIGHT,
        ticker=None,
        scope="portfolio",
        as_of=snap_at,
        collected_at=snap_at,
        source="portfolio_snapshots.snapshot_at",
        now=now,
    ))

    # ── Recommendation evidence ───────────────────────────────────────────────
    for ticker in (tickers or []):
        t = (ticker or "").upper()
        if not t:
            continue
        rec_at = rec_rows.get(t)
        records.append(build_evidence_record(
            evidence_type=EVIDENCE_TYPE_RECOMMENDATION,
            ticker=t,
            scope="ticker",
            as_of=rec_at,
            collected_at=rec_at,
            source="recommendations.created_at",
            now=now,
        ))

    # ── Analyst LLM evidence ──────────────────────────────────────────────────
    for ticker in (tickers or []):
        t = (ticker or "").upper()
        if not t:
            continue
        insight_at = insight_rows.get(t)
        records.append(build_evidence_record(
            evidence_type=EVIDENCE_TYPE_ANALYST_LLM,
            ticker=t,
            scope="ticker",
            as_of=insight_at,
            collected_at=insight_at,
            source="agent_insights.created_at",
            now=now,
        ))

    # ── Snapshot evidence ─────────────────────────────────────────────────────
    snap_created_at = snap_rows.get("created_at")
    snap_source = snap_rows.get("snapshot_source", "unknown")
    is_certified = snap_source == "worker_certified"
    records.append(build_evidence_record(
        evidence_type=EVIDENCE_TYPE_SNAPSHOT,
        ticker=None,
        scope="portfolio",
        as_of=snap_created_at,
        collected_at=snap_created_at,
        source="intel_v3_snapshots",
        now=now,
        source_quality="worker_certified" if is_certified else snap_source,
    ))

    # ── Technical evidence from Stage 5F research artifacts ──────────────────
    # For each ticker with a usable (is_usable=True) technical_signal artifact,
    # emit an EvidenceRecord from the artifact's generated_at timestamp. Only
    # FRESH and AGING records are emitted — stale artifacts cannot be auto-
    # refreshed by this worker, so they are treated as absent from the ledger
    # (a fresh write will surface them again when the next artifact arrives).
    _any_technical_fresh = False
    for ticker in (tickers or []):
        t = (ticker or "").upper()
        if not t:
            continue
        art_ts = usable_technical_arts.get(t)
        if art_ts is None:
            continue
        rec = build_evidence_record(
            evidence_type=EVIDENCE_TYPE_TECHNICAL,
            ticker=t,
            scope="ticker",
            as_of=art_ts,
            collected_at=art_ts,
            source="research_artifacts.technical_signal",
            now=now,
        )
        if rec.freshness_status in (FRESHNESS_FRESH, FRESHNESS_AGING):
            records.append(rec)
            _any_technical_fresh = True

    if not _any_technical_fresh:
        # No usable fresh technical artifacts — honest gap, not an error.
        records.append(EvidenceRecord(
            evidence_type=EVIDENCE_TYPE_TECHNICAL,
            ticker=None,
            scope="portfolio",
            as_of=None,
            collected_at=None,
            source="not_yet_collected",
            freshness_status=FRESHNESS_MISSING,
            freshness_sla_seconds=0,
            deploy_eligible=True,
            decision_eligible=True,
            reason="technical not yet collected by this application",
        ))

    # ── Not-yet-collected types: surface as MISSING ───────────────────────────
    # These are honest gaps, not errors. Future builds will fill them.
    for etype in (EVIDENCE_TYPE_FUNDAMENTAL,
                  EVIDENCE_TYPE_SEC_FILING, EVIDENCE_TYPE_NEWS_SENTIMENT):
        records.append(EvidenceRecord(
            evidence_type=etype,
            ticker=None,
            scope="portfolio",
            as_of=None,
            collected_at=None,
            source="not_yet_collected",
            freshness_status=FRESHNESS_MISSING,
            freshness_sla_seconds=0,
            deploy_eligible=True,   # missing non-critical types don't block deploy
            decision_eligible=True,
            reason=f"{etype} not yet collected by this application",
        ))

    return records


# ── Private DB helpers ────────────────────────────────────────────────────────

async def _fetch_tickers(user_id: UUID, client: Any) -> list[str]:
    result = await asyncio.to_thread(
        lambda: client.table("positions")
        .select("ticker")
        .eq("user_id", str(user_id))
        .execute()
    )
    return [
        row["ticker"] for row in (result.data or [])
        if isinstance(row, dict) and row.get("ticker")
    ]


async def _fetch_portfolio_snapshot(user_id: UUID, client: Any) -> dict[str, Any]:
    """Return snapshot_at and per-ticker price cert timestamps."""
    result = await asyncio.to_thread(
        lambda: client.table("portfolio_snapshots")
        .select("snapshot_at,positions_data")
        .eq("user_id", str(user_id))
        .order("snapshot_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return {"snapshot_at": None, "price_certs": {}}

    row = rows[0] or {}
    snap_at = _parse_iso(row.get("snapshot_at"))
    price_certs: dict[str, Optional[datetime]] = {}
    for pos in (row.get("positions_data") or []):
        if not isinstance(pos, dict):
            continue
        t = (pos.get("ticker") or "").upper()
        if not t:
            continue
        cert = _parse_iso(pos.get("market_value_certified_at"))
        price_certs[t] = cert or snap_at  # fall back to snapshot_at if no per-position cert

    return {"snapshot_at": snap_at, "price_certs": price_certs}


async def _fetch_latest_recommendations(
    user_id: UUID,
    client: Any,
) -> dict[str, Optional[datetime]]:
    """Latest recommendation created_at per ticker (uppercase keys)."""
    result = await asyncio.to_thread(
        lambda: client.table("recommendations")
        .select("ticker,created_at")
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .limit(500)
        .execute()
    )
    latest: dict[str, Optional[datetime]] = {}
    for row in (result.data or []):
        t = (row.get("ticker") or "").upper()
        if t and t not in latest:
            latest[t] = _parse_iso(row.get("created_at"))
    return latest


async def _fetch_latest_agent_insights(
    user_id: UUID,
    client: Any,
) -> dict[str, Optional[datetime]]:
    """Latest agent_insight created_at per ticker (uppercase keys)."""
    result = await asyncio.to_thread(
        lambda: client.table("agent_insights")
        .select("ticker,created_at")
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .limit(500)
        .execute()
    )
    latest: dict[str, Optional[datetime]] = {}
    for row in (result.data or []):
        t = (row.get("ticker") or "").upper()
        if t and t not in latest:
            latest[t] = _parse_iso(row.get("created_at"))
    return latest


async def _fetch_latest_intel_snapshot(
    user_id: UUID,
    client: Any,
) -> dict[str, Any]:
    """Latest intel_v3_snapshots row metadata (flat columns only, not payload)."""
    result = await asyncio.to_thread(
        lambda: client.table("intel_v3_snapshots")
        .select("created_at,snapshot_source")
        .eq("user_id", str(user_id))
        .eq("is_active", True)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return {"created_at": None, "snapshot_source": "none"}
    row = rows[0] or {}
    return {
        "created_at": _parse_iso(row.get("created_at")),
        "snapshot_source": row.get("snapshot_source") or "unknown",
    }


async def _fetch_latest_usable_research_artifacts(
    user_id: UUID,
    client: Any,
    *,
    artifact_type: str,
) -> dict[str, datetime]:
    """Return ticker → latest usable artifact generated_at for active artifacts.

    Uses is_active=True as a proxy for usable (Stage 5A guarantees at most one
    active row per identity). Avoids reading payload JSONB to eliminate egress.

    Returns empty dict on any failure — callers treat absence as MISSING.
    """
    try:
        result = await asyncio.to_thread(
            lambda: client.table("research_artifacts")
            .select("ticker,generated_at")
            .eq("user_id", str(user_id))
            .eq("artifact_type", artifact_type)
            .eq("is_active", True)
            .order("generated_at", desc=True)
            .execute()
        )
        latest: dict[str, datetime] = {}
        for row in (result.data or []):
            t = (row.get("ticker") or "").strip().upper()
            if not t or t in latest:
                continue
            gen_at = _parse_iso(row.get("generated_at"))
            if gen_at is not None:
                latest[t] = gen_at
        return latest
    except Exception as exc:
        logger.warning(
            "watchtower.collect research_artifacts_failed artifact_type=%s err=%s",
            artifact_type, exc,
        )
        return {}


def _parse_iso(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
