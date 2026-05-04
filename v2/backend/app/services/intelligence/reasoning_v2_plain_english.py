"""reasoning_v2 plain-English translator — deterministic, pure, no IO.

Translates a reasoning_v2 dict (from agent_runs.allocation["_reasoning_v2"])
into a compact plain-English intel_read projection suitable for the frontend.

Contract invariants:
  * Pure function: no IO, DB, network, randomness, datetime.now, or LLM calls.
  * Same inputs always produce identical dict output.
  * Output contains no raw metric key names.
  * Missing or malformed input returns None safely.
  * INSUFFICIENT_DATA remains conservative and never fabricates confidence.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# Evidence key → plain-English label (published_dimensions use _score suffix).
_EV_KEY_TO_LABEL: dict[str, str] = {
    "quality_score": "business quality",
    "valuation_score": "valuation",
    "growth_score": "growth",
    "risk_score": "risk",
    "momentum_score": "recent market behavior",
}

# Raw dimension key → plain-English label (suppressed_dimensions use bare names).
_DIM_KEY_TO_LABEL: dict[str, str] = {
    "quality": "business quality",
    "valuation": "valuation",
    "growth": "growth",
    "risk": "risk",
    "momentum": "recent market behavior",
}

_POSTURE_LABEL: dict[str, str] = {
    "ACCUMULATE": "constructive",
    "HOLD": "neutral",
    "TRIM": "cautious",
    "AVOID": "cautious",
    "WATCH": "on watch",
}

_VALID_POSTURES = frozenset(_POSTURE_LABEL)

# Forbidden bullish phrases for the insufficient_data card copy sanitizer.
# Any primary_driver or differentiation text containing these phrases must not
# be rendered on an insufficient-data card — replace with conservative fallback.
_FORBIDDEN_BULLISH_PHRASES: frozenset[str] = frozenset([
    "accumulate",
    "buy",
    "entry opportunity",
    "re-rating opportunity",
    "high-conviction idea",
    "add aggressively",
    "strong buy",
    "deploy",
])


def is_safe_for_insufficient_data(text: str | None) -> bool:
    """Return True if text contains no forbidden bullish phrases.

    Pure function — deterministic, no IO, no LLM.
    Used by card assembly to decide whether to preserve ticker-specific copy
    or fall back to conservative_why / null under insufficient_data.

    None or empty string is treated as safe (no forbidden content).
    """
    if not text:
        return True
    lower = text.lower()
    phrase_patterns: tuple[re.Pattern[str], ...] = (
        re.compile(r"\baccumulate\b"),
        re.compile(r"\bbuy\b"),
        re.compile(r"\bentry(?:\s+point|\s+opportunity)\b"),
        re.compile(r"\bre-rating opportunity\b"),
        re.compile(r"\bhigh-conviction idea\b"),
        re.compile(r"\badd aggressively\b"),
        re.compile(r"\badd\s+shares\b"),
        re.compile(r"\bstrong buy\b"),
        re.compile(r"\bdeploy(?:\s+capital)?\b"),
    )
    return not any(pattern.search(lower) for pattern in phrase_patterns)


def _join_plain(items: list[str]) -> str:
    """Join list into plain-English comma list with 'and'."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _build_summary(
    *,
    posture: str,
    posture_label: str,
    trusted_signals: list[str],
    incomplete_signals: list[str],
    data_status: str,
    blockers: list[str],
) -> str:
    has_trusted = bool(trusted_signals)
    has_incomplete = bool(incomplete_signals)

    if not has_trusted and not has_incomplete:
        return (
            "Not enough evidence on any dimension yet. "
            "Staying on watch until signals strengthen."
        )

    if not has_trusted:
        incomplete_str = _join_plain(incomplete_signals)
        return (
            f"Data on {incomplete_str} is still incomplete. "
            "Not enough evidence to comment on any dimension yet."
        )

    trusted_str = _join_plain(trusted_signals)

    if not has_incomplete:
        if posture == "WATCH":
            return (
                f"Evidence on {trusted_str} is available, "
                "but the overall picture still calls for watching this one closely."
            )
        return f"Evidence on {trusted_str} supports a {posture_label} view."

    incomplete_str = _join_plain(incomplete_signals)
    are_str = "are" if len(incomplete_signals) > 1 else "is"

    if posture == "WATCH":
        return (
            f"The system has enough evidence to comment on {trusted_str}, "
            f"but {incomplete_str} {are_str} still incomplete. "
            "That is why this stays on watch — not complete enough for a strong view."
        )

    return (
        f"Some evidence on {trusted_str} is available, "
        f"but {incomplete_str} {are_str} still incomplete. "
        f"Treat this as a {posture_label} read, not a complete picture."
    )


def _build_conservative_action(
    trusted_signals: list[str],
    incomplete_signals: list[str],
) -> str:
    """Signal-specific watchlist ACTION copy for insufficient-data cards."""
    if incomplete_signals:
        incomplete_str = _join_plain(incomplete_signals)
        return (
            f"Stay on watchlist. Recheck after {incomplete_str} evidence improves "
            "or a new agent run fills those gaps."
        )
    if trusted_signals:
        return "Stay on watchlist. Watch for more complete evidence before adding."
    return "Stay on watchlist. Wait for more complete evidence."


def _build_conservative_why(
    trusted_signals: list[str],
    incomplete_signals: list[str],
) -> str:
    """Concise 1-sentence WHY copy for insufficient-data cards.

    Distinct from intel_read.summary and intel_read.bottom_line so WHY and
    WHY THIS VIEW are complementary rather than redundant.
    """
    if trusted_signals and incomplete_signals:
        trusted_str = _join_plain(trusted_signals)
        incomplete_str = _join_plain(incomplete_signals)
        are_str = "are" if len(incomplete_signals) > 1 else "is"
        return (
            f"Evidence on {trusted_str} is present, "
            f"but {incomplete_str} {are_str} still incomplete — "
            "watchlist read only."
        )
    if trusted_signals:
        trusted_str = _join_plain(trusted_signals)
        return (
            f"Evidence on {trusted_str} is available, "
            "but coverage is not complete enough for a conviction position."
        )
    if incomplete_signals:
        incomplete_str = _join_plain(incomplete_signals)
        are_str = "are" if len(incomplete_signals) > 1 else "is"
        return (
            f"{incomplete_str.capitalize()} {are_str} still incomplete. "
            "Not enough evidence to form a view."
        )
    return "Not enough evidence on any dimension yet. This is a watchlist position only."


def _build_bottom_line(
    trusted_signals: list[str],
    incomplete_signals: list[str],
) -> str:
    """Short conclusion shown in WHY THIS VIEW for insufficient-data cards.

    Complements conservative_why (WHY): WHY states what is present vs. missing;
    bottom_line states the conclusion and what to wait for.
    """
    if trusted_signals and incomplete_signals:
        trusted_str = _join_plain(trusted_signals)
        incomplete_str = _join_plain(incomplete_signals)
        are_str = "are" if len(incomplete_signals) > 1 else "is"
        return (
            f"Evidence is strongest on {trusted_str}, but {incomplete_str} {are_str} "
            "still missing — keep this on the watchlist for now."
        )
    if trusted_signals:
        return "Some signals available, but not complete enough for a confident position."
    if incomplete_signals:
        return "Too many coverage gaps to form a confident view yet."
    return "Not enough evidence yet to form a view."


_INTEL_RISK_WATCH_TICKERS: frozenset[str] = frozenset(
    {"BTC", "XRP", "RIVN", "KLAR", "BLSH", "STUB"}
)


def build_posture_reason(
    *,
    posture_label: str,
    trusted_signals: list[str],
    incomplete_signals: list[str],
    ticker: str,
    category: str,
) -> str:
    """Card-specific explanation of why this Intel posture was assigned.

    Pure function — deterministic, no IO. Called after _derive_intel_posture
    assigns the posture_label so it can reference it directly.
    Used to populate intel_read.posture_reason for the WhyThisView section.
    """
    cat_low = (category or "").lower()
    ticker_up = (ticker or "").upper()

    if posture_label == "Add Candidate":
        if "etf" in cat_low:
            return (
                "Core index or dividend ETF — regular contribution target "
                "regardless of short-term signal completeness."
            )
        if trusted_signals:
            trusted_str = _join_plain(trusted_signals)
            return (
                f"Evidence on {trusted_str} supports a constructive view. "
                "Consider adding on weakness when position sizing allows."
            )
        return (
            "Evidence coverage is sufficient to consider adding. "
            "Watch price action and position sizing before adding."
        )

    if posture_label == "Trim Candidate":
        return (
            "Current signal points to reducing exposure here. "
            "Review position size against targets and tax considerations."
        )

    if posture_label == "Risk Watch":
        if ticker_up in _INTEL_RISK_WATCH_TICKERS:
            return (
                "High-risk or speculative position. "
                "Monitor closely — not a core holding candidate until evidence strengthens."
            )
        return (
            "Bearish or weak technical momentum. "
            "Watch for stabilization before adding; current signal calls for caution."
        )

    if posture_label == "Review":
        if trusted_signals and incomplete_signals:
            trusted_str = _join_plain(trusted_signals)
            incomplete_str = _join_plain(incomplete_signals)
            are_str = "are" if len(incomplete_signals) > 1 else "is"
            return (
                f"Evidence on {trusted_str} warrants attention, "
                f"but {incomplete_str} {are_str} still missing. "
                "Reviewing before taking action — the setup is interesting but not yet complete."
            )
        if trusted_signals:
            return (
                "Some evidence available and worth tracking. "
                "Reviewing before acting — waiting for more complete coverage."
            )
        return "Partial evidence available. Reviewing before taking action."

    # Watchlist
    if incomplete_signals:
        incomplete_str = _join_plain(incomplete_signals)
        are_str = "are" if len(incomplete_signals) > 1 else "is"
        return (
            f"Coverage still thin on {incomplete_str}. "
            "Holding current position while waiting for evidence to build."
        )
    return "Monitoring for a clearer signal before acting."


def _build_caveat(
    *,
    posture: str,
    data_status: str,
    blockers: list[str],
    has_trusted: bool,
) -> str:
    if "insufficient_data" in blockers or data_status == "INSUFFICIENT_DATA":
        return "Not enough data to be confident. Wait for more signals before acting."
    if "agreement_conflict" in blockers:
        return "There is a conflict between market signals and analyst view. Stay cautious."
    if not has_trusted:
        return "No complete dimension evidence yet. This is a placeholder watch only."
    return "Treat this as an early signal, not a complete picture."


def build_intel_read(r2: Any) -> Optional[dict[str, Any]]:
    """Translate a reasoning_v2 dict into a safe plain-English intel_read projection.

    Pure function — no IO, DB, network, randomness, datetime.now, or LLM calls.
    Same inputs always produce identical dict output.

    Args:
        r2: Full reasoning_v2 dict from agent_runs.allocation["_reasoning_v2"][ticker].
            Must contain evidence.deterministic.coverage, action.posture, and
            deploy_signals.blockers for a complete projection.

    Returns:
        Plain-English intel_read dict with keys: title, posture_label, summary,
        trusted_signals, incomplete_signals, caveat. Returns None if input is
        not a valid dict or structurally unusable.
    """
    if not isinstance(r2, dict):
        return None

    # Extract posture
    action = r2.get("action") or {}
    posture = str(action.get("posture") or "WATCH").upper().strip()
    if posture not in _VALID_POSTURES:
        posture = "WATCH"
    posture_label = _POSTURE_LABEL[posture]

    # Extract data quality status
    data_quality = r2.get("data_quality") or {}
    data_status = str(data_quality.get("status") or "INSUFFICIENT_DATA").upper().strip()

    # Extract coverage block from evidence
    evidence = r2.get("evidence") or {}
    deterministic = evidence.get("deterministic") or {}
    coverage = deterministic.get("coverage") or {}

    published_dimensions = list(coverage.get("published_dimensions") or [])
    suppressed_dimensions = list(coverage.get("suppressed_dimensions") or [])

    # Extract deploy blockers
    deploy_signals = r2.get("deploy_signals") or {}
    blockers = list(deploy_signals.get("blockers") or [])

    # Map evidence keys → plain-English labels (no raw metric names in output)
    trusted_signals = [
        _EV_KEY_TO_LABEL[d]
        for d in published_dimensions
        if d in _EV_KEY_TO_LABEL
    ]
    # Deduplicate while preserving order (coverage aggregation may produce duplicates
    # if a dimension appears in both evidence keys and subscore keys)
    seen: set[str] = set()
    trusted_signals_dedup: list[str] = []
    for s in trusted_signals:
        if s not in seen:
            seen.add(s)
            trusted_signals_dedup.append(s)
    trusted_signals = trusted_signals_dedup

    incomplete_signals = [
        _DIM_KEY_TO_LABEL[d]
        for d in suppressed_dimensions
        if d in _DIM_KEY_TO_LABEL
    ]
    seen_inc: set[str] = set()
    incomplete_signals_dedup: list[str] = []
    for s in incomplete_signals:
        if s not in seen_inc:
            seen_inc.add(s)
            incomplete_signals_dedup.append(s)
    incomplete_signals = incomplete_signals_dedup

    summary = _build_summary(
        posture=posture,
        posture_label=posture_label,
        trusted_signals=trusted_signals,
        incomplete_signals=incomplete_signals,
        data_status=data_status,
        blockers=blockers,
    )
    caveat = _build_caveat(
        posture=posture,
        data_status=data_status,
        blockers=blockers,
        has_trusted=bool(trusted_signals),
    )

    # Backend hint: True when data is insufficient and WATCH is forced.
    # Not rendered by the frontend WhyThisView component; used by card assembly
    # to downgrade BUY/HIGH CONVICTION labels that would contradict intel_read.
    is_insufficient_data = (
        data_status == "INSUFFICIENT_DATA" or "insufficient_data" in blockers
    )

    return {
        "title": "Why this view?",
        "posture_label": posture_label,
        "summary": summary,
        "trusted_signals": trusted_signals,
        "incomplete_signals": incomplete_signals,
        "caveat": caveat,
        "insufficient_data": is_insufficient_data,
        "conservative_action": (
            _build_conservative_action(trusted_signals, incomplete_signals)
            if is_insufficient_data else None
        ),
        "conservative_why": (
            _build_conservative_why(trusted_signals, incomplete_signals)
            if is_insufficient_data else None
        ),
        "bottom_line": (
            _build_bottom_line(trusted_signals, incomplete_signals)
            if is_insufficient_data else None
        ),
    }
