"""Evidence freshness contract (Stage 3.0b v1).

Single source of truth for per-source SLA windows, source-state classification,
and the four Intel v3 run modes (FAST_CERTIFIED / REFRESH_THEN_RUN /
PARTIAL_CERTIFIED / BLOCKED_UNCERTIFIED).

Pure module — no IO, no DB, no LLM, no provider calls. Every threshold here is a
deterministic constant; callers (the orchestrator, the diagnostics builder, the
frontend banner) read these labels rather than re-inventing them.

References:
  docs/ai/INTEL_V3_EVIDENCE_REFRESH_ORCHESTRATOR.md §4 (SLA table) and §5 (run
  modes). When that doc changes the SLA windows or mode names, update this
  module — not the other way around.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ── Source classes ────────────────────────────────────────────────────────────

# These names are stable identifiers used in diagnostics, logs, and tests. They
# match docs/ai/INTEL_V3_EVIDENCE_REFRESH_ORCHESTRATOR.md §4.
SOURCE_RECOMMENDATIONS = "recommendations"
SOURCE_AGENT_INSIGHTS = "agent_insights"
SOURCE_POSITIONS = "positions"
SOURCE_PORTFOLIO_SNAPSHOT = "portfolio_snapshot"
SOURCE_PRICE_LATEST = "price_latest"
SOURCE_PRICE_HISTORY = "price_history"
SOURCE_RESEARCH_ARTIFACTS = "research_artifacts"


# Critical sources gate the visible action label. Non-critical sources can be
# stale without forcing BLOCKED_UNCERTIFIED.
CRITICAL_SOURCES: frozenset[str] = frozenset({
    SOURCE_RECOMMENDATIONS,
    SOURCE_AGENT_INSIGHTS,
    SOURCE_POSITIONS,
})

NON_CRITICAL_SOURCES: frozenset[str] = frozenset({
    SOURCE_PORTFOLIO_SNAPSHOT,
    SOURCE_PRICE_LATEST,
    SOURCE_PRICE_HISTORY,
    SOURCE_RESEARCH_ARTIFACTS,
})

ALL_SOURCES: frozenset[str] = CRITICAL_SOURCES | NON_CRITICAL_SOURCES


# ── Source states ─────────────────────────────────────────────────────────────

STATE_FRESH = "FRESH"
STATE_STALE = "STALE"
STATE_HARD_STALE = "HARD_STALE"
STATE_MISSING = "MISSING"
STATE_UNKNOWN = "UNKNOWN"


# ── Run modes ─────────────────────────────────────────────────────────────────

RUN_MODE_FAST_CERTIFIED = "FAST_CERTIFIED"
RUN_MODE_REFRESH_THEN_RUN = "REFRESH_THEN_RUN"
RUN_MODE_PARTIAL_CERTIFIED = "PARTIAL_CERTIFIED"
RUN_MODE_BLOCKED_UNCERTIFIED = "BLOCKED_UNCERTIFIED"


# ── Trust status (UI bridge) ──────────────────────────────────────────────────

TRUST_TRUSTED = "trusted"
TRUST_PARTIAL = "partial_trust"
TRUST_UNCERTIFIED = "uncertified"


# ── Per-source SLA windows (hours) ───────────────────────────────────────────
#
# fresh_hours    : ≤ this many hours since last refresh → FRESH
# stale_hours    : > fresh_hours and ≤ stale_hours       → STALE (refreshable)
# anything above stale_hours                              → HARD_STALE
#
# These are the deterministic constants the orchestrator and tests share.

@dataclass(frozen=True)
class SourceSLA:
    fresh_hours: float
    stale_hours: float
    critical: bool


SOURCE_SLAS: dict[str, SourceSLA] = {
    # Critical (gates visible action)
    SOURCE_RECOMMENDATIONS:    SourceSLA(fresh_hours=24.0,  stale_hours=168.0, critical=True),   # 1d / 7d
    SOURCE_AGENT_INSIGHTS:     SourceSLA(fresh_hours=48.0,  stale_hours=168.0, critical=True),   # 2d / 7d
    SOURCE_POSITIONS:          SourceSLA(fresh_hours=24.0,  stale_hours=168.0, critical=True),   # 1d / 7d
    # Non-critical (sizing / context)
    SOURCE_PORTFOLIO_SNAPSHOT: SourceSLA(fresh_hours=24.0,  stale_hours=168.0, critical=False),
    SOURCE_PRICE_LATEST:       SourceSLA(fresh_hours=0.25,  stale_hours=4.0,   critical=False),  # 15m / 4h
    SOURCE_PRICE_HISTORY:      SourceSLA(fresh_hours=24.0,  stale_hours=120.0, critical=False),  # 1d / 5d
    SOURCE_RESEARCH_ARTIFACTS: SourceSLA(fresh_hours=168.0, stale_hours=720.0, critical=False),  # 7d / 30d
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_iso(ts: Any) -> Optional[datetime]:
    """Parse an ISO timestamp string into a tz-aware UTC datetime; None on failure."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _age_hours(ts: Any, now: datetime) -> Optional[float]:
    dt = _parse_iso(ts)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 3600.0


# ── Per-source state classification ───────────────────────────────────────────

@dataclass
class SourceFreshnessState:
    """Classification result for a single source class on a single run.

    timestamps is the set of representative timestamps the orchestrator saw for
    this class (may be empty). oldest_age_hours / newest_age_hours are None when
    no timestamps were observed.
    """
    source: str
    state: str
    is_critical: bool
    fresh_count: int = 0
    stale_count: int = 0
    hard_stale_count: int = 0
    missing_count: int = 0
    oldest_age_hours: Optional[float] = None
    newest_age_hours: Optional[float] = None
    oldest_timestamp: Optional[str] = None
    newest_timestamp: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source":            self.source,
            "state":             self.state,
            "is_critical":       self.is_critical,
            "fresh_count":       self.fresh_count,
            "stale_count":       self.stale_count,
            "hard_stale_count":  self.hard_stale_count,
            "missing_count":     self.missing_count,
            "oldest_age_hours":  self.oldest_age_hours,
            "newest_age_hours":  self.newest_age_hours,
            "oldest_timestamp":  self.oldest_timestamp,
            "newest_timestamp":  self.newest_timestamp,
        }


def classify_source_state(
    *,
    source: str,
    timestamps: list[str] | None,
    expected_count: int = 0,
    now: Optional[datetime] = None,
) -> SourceFreshnessState:
    """Classify a source's freshness from a list of observed timestamps.

    Rules:
      - Unknown source class → STATE_UNKNOWN, treated as non-critical.
      - No timestamps at all and expected_count > 0 → MISSING.
      - No timestamps at all and expected_count == 0 → UNKNOWN (no signal yet).
      - Per-timestamp bucketing into FRESH / STALE / HARD_STALE based on SLA.
      - The aggregate state is the WORST observed bucket across all timestamps,
        plus any rows that should exist but are missing count as MISSING.
      - "Missing rows" promotes the aggregate state to at least MISSING when a
        critical source has expected_count > observed_count.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    sla = SOURCE_SLAS.get(source)
    is_critical = sla.critical if sla else False

    timestamps = timestamps or []
    parsed_with_raw: list[tuple[datetime, str]] = []
    for raw in timestamps:
        dt = _parse_iso(raw)
        if dt is not None:
            parsed_with_raw.append((dt, raw))

    if sla is None:
        # Unknown source class — surface honestly rather than guessing.
        return SourceFreshnessState(
            source=source,
            state=STATE_UNKNOWN,
            is_critical=False,
            missing_count=max(0, expected_count - len(parsed_with_raw)),
        )

    fresh = stale = hard = 0
    for dt, _raw in parsed_with_raw:
        age = (now - dt).total_seconds() / 3600.0
        if age <= sla.fresh_hours:
            fresh += 1
        elif age <= sla.stale_hours:
            stale += 1
        else:
            hard += 1

    missing = max(0, expected_count - len(parsed_with_raw))

    if parsed_with_raw:
        oldest_dt, oldest_raw = max(parsed_with_raw, key=lambda x: (now - x[0]))
        newest_dt, newest_raw = min(parsed_with_raw, key=lambda x: (now - x[0]))
        oldest_age = (now - oldest_dt).total_seconds() / 3600.0
        newest_age = (now - newest_dt).total_seconds() / 3600.0
        oldest_ts: Optional[str] = oldest_raw
        newest_ts: Optional[str] = newest_raw
    else:
        oldest_age = newest_age = None
        oldest_ts = newest_ts = None

    # Aggregate state: worst-observed bucket. Missing trumps fresh-only; hard
    # trumps stale; stale trumps fresh; otherwise FRESH or UNKNOWN/MISSING.
    if hard > 0:
        agg = STATE_HARD_STALE
    elif missing > 0:
        agg = STATE_MISSING
    elif stale > 0:
        agg = STATE_STALE
    elif fresh > 0:
        agg = STATE_FRESH
    elif expected_count > 0:
        agg = STATE_MISSING
    else:
        agg = STATE_UNKNOWN

    return SourceFreshnessState(
        source=source,
        state=agg,
        is_critical=is_critical,
        fresh_count=fresh,
        stale_count=stale,
        hard_stale_count=hard,
        missing_count=missing,
        oldest_age_hours=oldest_age,
        newest_age_hours=newest_age,
        oldest_timestamp=oldest_ts,
        newest_timestamp=newest_ts,
    )


# ── Run-mode classifier ───────────────────────────────────────────────────────

@dataclass
class RunModeDecision:
    """Result of run-mode classification, before and after refresh."""
    run_mode: str
    trust_status: str
    should_refresh: bool
    refresh_targets: list[str] = field(default_factory=list)
    blocked_sources: list[str] = field(default_factory=list)
    critical_stale_sources: list[str] = field(default_factory=list)
    non_critical_stale_sources: list[str] = field(default_factory=list)


def classify_run_mode(
    states: dict[str, SourceFreshnessState],
    *,
    refresh_attempted: bool = False,
    refresh_successful_count: int = 0,
    refresh_failed_count: int = 0,
) -> RunModeDecision:
    """Pick exactly one of the four run modes from the source-state map.

    Logic (mirrors INTEL_V3_EVIDENCE_REFRESH_ORCHESTRATOR.md §5):

    Before refresh:
      - All sources FRESH (or UNKNOWN-non-critical with no signal yet) → FAST_CERTIFIED
      - Any critical source HARD_STALE / MISSING → BLOCKED_UNCERTIFIED (cannot
        certify; refresh may still attempt but pre-refresh mode is BLOCKED).
      - Any source STALE → REFRESH_THEN_RUN (attempt refresh on stale targets).
      - Otherwise non-critical hard/missing → PARTIAL_CERTIFIED.

    After refresh:
      - If all critical sources are now FRESH and non-critical sources are at
        worst STALE → REFRESH_THEN_RUN (the refresh ran and certified the run).
      - If any critical source is still HARD_STALE / MISSING → BLOCKED_UNCERTIFIED.
      - Otherwise → PARTIAL_CERTIFIED.

    trust_status is a UI bridge:
      FAST_CERTIFIED              → trusted
      REFRESH_THEN_RUN  (success) → trusted
      PARTIAL_CERTIFIED           → partial_trust
      BLOCKED_UNCERTIFIED         → uncertified
    """
    critical_states = {s.source: s for s in states.values() if s.is_critical}
    non_critical_states = {s.source: s for s in states.values() if not s.is_critical}

    def _bad_critical(s: SourceFreshnessState) -> bool:
        return s.state in (STATE_HARD_STALE, STATE_MISSING)

    def _stale_any(s: SourceFreshnessState) -> bool:
        return s.state == STATE_STALE

    critical_bad = [s for s in critical_states.values() if _bad_critical(s)]
    critical_stale = [s for s in critical_states.values() if _stale_any(s)]
    non_critical_bad = [s for s in non_critical_states.values() if _bad_critical(s)]
    non_critical_stale = [s for s in non_critical_states.values() if _stale_any(s)]

    refresh_targets = [
        s.source for s in states.values()
        if s.state in (STATE_STALE, STATE_HARD_STALE, STATE_MISSING)
    ]
    blocked_sources = [
        s.source for s in states.values()
        if s.state in (STATE_HARD_STALE, STATE_MISSING) and s.is_critical
    ]

    # Pre-refresh classification.
    if not refresh_attempted:
        if critical_bad:
            # Critical missing/hard-stale — pre-refresh assessment is BLOCKED.
            # The orchestrator may still attempt refresh on stale sub-targets,
            # but freshness alone cannot certify the run.
            return RunModeDecision(
                run_mode=RUN_MODE_BLOCKED_UNCERTIFIED,
                trust_status=TRUST_UNCERTIFIED,
                should_refresh=bool(refresh_targets),
                refresh_targets=refresh_targets,
                blocked_sources=blocked_sources,
                critical_stale_sources=[s.source for s in critical_stale],
                non_critical_stale_sources=[s.source for s in non_critical_stale],
            )
        if critical_stale or non_critical_stale or non_critical_bad:
            return RunModeDecision(
                run_mode=RUN_MODE_REFRESH_THEN_RUN,
                trust_status=TRUST_PARTIAL,  # provisional until refresh runs
                should_refresh=True,
                refresh_targets=refresh_targets,
                blocked_sources=blocked_sources,
                critical_stale_sources=[s.source for s in critical_stale],
                non_critical_stale_sources=[s.source for s in non_critical_stale],
            )
        return RunModeDecision(
            run_mode=RUN_MODE_FAST_CERTIFIED,
            trust_status=TRUST_TRUSTED,
            should_refresh=False,
            refresh_targets=[],
            blocked_sources=[],
            critical_stale_sources=[],
            non_critical_stale_sources=[],
        )

    # Post-refresh classification.
    if critical_bad:
        return RunModeDecision(
            run_mode=RUN_MODE_BLOCKED_UNCERTIFIED,
            trust_status=TRUST_UNCERTIFIED,
            should_refresh=False,
            refresh_targets=[],
            blocked_sources=blocked_sources,
            critical_stale_sources=[s.source for s in critical_stale],
            non_critical_stale_sources=[s.source for s in non_critical_stale],
        )

    # Critical now FRESH (or at worst STALE). If anything stale or non-critical
    # bad remains we degrade to PARTIAL_CERTIFIED; otherwise the refresh
    # successfully certified the run.
    any_stale_or_bad = bool(
        critical_stale or non_critical_stale or non_critical_bad
        or refresh_failed_count > 0
    )

    if not any_stale_or_bad and refresh_successful_count > 0:
        return RunModeDecision(
            run_mode=RUN_MODE_REFRESH_THEN_RUN,
            trust_status=TRUST_TRUSTED,
            should_refresh=False,
            refresh_targets=[],
            blocked_sources=[],
            critical_stale_sources=[],
            non_critical_stale_sources=[],
        )

    if not any_stale_or_bad:
        # Refresh was attempted but produced no successful calls. All sources
        # look fine; treat as PARTIAL_CERTIFIED so we don't claim a refresh.
        return RunModeDecision(
            run_mode=RUN_MODE_PARTIAL_CERTIFIED,
            trust_status=TRUST_PARTIAL,
            should_refresh=False,
            refresh_targets=[],
            blocked_sources=[],
            critical_stale_sources=[],
            non_critical_stale_sources=[],
        )

    return RunModeDecision(
        run_mode=RUN_MODE_PARTIAL_CERTIFIED,
        trust_status=TRUST_PARTIAL,
        should_refresh=False,
        refresh_targets=[],
        blocked_sources=blocked_sources,
        critical_stale_sources=[s.source for s in critical_stale],
        non_critical_stale_sources=[s.source for s in non_critical_stale],
    )


# ── Plain-English banner copy (single source of truth) ────────────────────────

# Frontend reads run_mode from diagnostics and renders these strings verbatim.
# Keep them plain-English — no diagnostic jargon, no raw metric keys.
BANNER_COPY: dict[str, str] = {
    RUN_MODE_FAST_CERTIFIED:      "Fresh certified — using current evidence.",
    RUN_MODE_REFRESH_THEN_RUN:    "Refreshed stale evidence before running.",
    RUN_MODE_PARTIAL_CERTIFIED:   "Partial: some evidence stale or unavailable.",
    RUN_MODE_BLOCKED_UNCERTIFIED: "Blocked: current evidence unavailable — showing last certified analysis.",
}
