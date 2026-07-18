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


# Membership lives in app/policy_tickers.json ("intel_risk_watch_tickers").
from ..policy_tickers import ticker_set as _policy_ticker_set

_INTEL_RISK_WATCH_TICKERS: frozenset[str] = _policy_ticker_set("intel_risk_watch_tickers")


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
    trusted_signals: list[str],
    incomplete_signals: list[str],
) -> str:
    n_trusted = len(trusted_signals)
    insufficient = "insufficient_data" in blockers or data_status == "INSUFFICIENT_DATA"

    if insufficient and n_trusted == 0:
        return "Not enough data to be confident. Wait for more signals before acting."

    if "agreement_conflict" in blockers:
        return "There is a conflict between market signals and analyst view. Stay cautious."

    if n_trusted == 0:
        return "No complete dimension evidence yet. This is a placeholder watch only."

    if posture == "ACCUMULATE":
        if incomplete_signals:
            return "Evidence supports a measured buy, while missing areas keep confidence moderate."
        return "Evidence supports the buy posture. Keep sizing disciplined as new data arrives."

    if posture == "HOLD":
        if incomplete_signals:
            return "There is usable evidence, but gaps keep this as a hold for now."
        return "Evidence is usable but balanced, so holding is appropriate for now."

    if posture in {"TRIM", "AVOID"}:
        return "Evidence and risk context support a cautious stance; reduce or avoid adding exposure."

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
        trusted_signals=trusted_signals,
        incomplete_signals=incomplete_signals,
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


# ── Intel Card Narrative Contract v1 ─────────────────────────────────────────
#
# Single source of truth for Evidence Check copy.  All six card voices
# (action badge, confidence badge, WHY, ACTION, RISK, Evidence Check) must
# agree.  This contract produces the Evidence Check primary text
# (evidence_summary → posture_reason) and secondary text (final_takeaway →
# caveat) from the VISIBLE action, not from the reasoning_v2 posture.
#
# Forbidden phrases for BUY cards (must never appear in Evidence Check):
_FORBIDDEN_FOR_BUY: tuple[str, ...] = (
    "reviewing before taking action",
    "not yet complete",
    "early signal",
    "not enough data",
    "wait for more signals",
    "stay on watchlist",
    "watchlist read only",
    "placeholder watch",
    "on watchlist",
)

# Forbidden buy phrases for TRIM/SELL cards:
_FORBIDDEN_FOR_TRIM_SELL: tuple[str, ...] = (
    "accumulate",
    "add candidate",
    "entry opportunity",
    "constructive view",
    "supports a buy",
    "measured buy",
    "regular contribution",
)


def detect_intel_card_conflict(
    *,
    visible_action: str,
    posture_reason: Optional[str],
    caveat: Optional[str],
) -> list[str]:
    """Return conflict reason codes for action/copy mismatches.

    Pure function — deterministic, no IO.
    Returns empty list when no conflicts detected.
    Used before the narrative contract is applied to count pre-fix conflicts.
    """
    action = (visible_action or "HOLD").upper()
    flags: list[str] = []
    texts = [t for t in [posture_reason, caveat] if t]

    if action == "BUY":
        for text in texts:
            lower = text.lower()
            for phrase in _FORBIDDEN_FOR_BUY:
                if phrase in lower:
                    flags.append(f"buy_hold_lang:{phrase[:30]}")
                    break

    elif action in {"TRIM", "SELL"}:
        for text in texts:
            lower = text.lower()
            for phrase in _FORBIDDEN_FOR_TRIM_SELL:
                if phrase in lower:
                    flags.append(f"trim_sell_buy_lang:{phrase[:30]}")
                    break

    elif action == "HOLD":
        for text in texts:
            lower = text.lower()
            if re.search(r"\b(add now|buy now|enter now)\b", lower):
                flags.append("hold_immediate_buy_lang")
                break

    return flags


def _nc_buy_evidence_summary(
    *,
    conviction: str,
    trusted_signals: list[str],
    incomplete_signals: list[str],
    is_etf: bool,
) -> str:
    trusted_str = _join_plain(trusted_signals) if trusted_signals else None
    missing_str = _join_plain(incomplete_signals) if incomplete_signals else None
    n_trusted = len(trusted_signals)
    n_missing = len(incomplete_signals)
    plural = "are" if n_missing > 1 else "is"

    if is_etf:
        if n_trusted == 0:
            return (
                "Regular contribution target. "
                "Fund-level evidence is more limited than single-stock evidence — "
                "confidence is capped, but consistent accumulation is appropriate."
            )
        if n_missing > 0:
            return (
                f"Regular contribution or measured accumulation target. "
                f"Reliable evidence on {trusted_str}. "
                "Fund-level coverage may be more limited than single-stock evidence, "
                "which is expected for this asset type."
            )
        return (
            f"Regular contribution or measured accumulation target. "
            f"Evidence on {trusted_str} supports continued accumulation."
        )

    # No trusted signals — should be rare for BUY (gate collapses to HOLD at n==0)
    if n_trusted == 0:
        return (
            "A buy signal is present, but evidence coverage is very limited. "
            "Size modestly and monitor for stronger confirmation."
        )

    if conviction == "HIGH":
        if n_missing > 0:
            return (
                f"Reliable evidence on {trusted_str} supports a constructive view. "
                f"{missing_str.capitalize()} {plural} not yet available, "
                "which keeps conviction moderate rather than high."
            )
        return f"Strong evidence on {trusted_str} supports a high-conviction buy at current sizing."

    if conviction == "MEDIUM":
        if n_missing > 0:
            return (
                f"Evidence on {trusted_str} supports a measured buy. "
                f"{missing_str.capitalize()} {plural} still incomplete — "
                "size gradually rather than treating this as a full conviction call."
            )
        return f"Evidence on {trusted_str} supports a measured buy at current positioning."

    # LOW conviction BUY
    if n_missing > 0:
        return (
            f"Evidence on {trusted_str} is available but {missing_str} {plural} still incomplete. "
            "This is a limited-confidence buy — size modestly until coverage improves."
        )
    return (
        f"Available evidence on {trusted_str} supports a buy at limited confidence. "
        "Watch for stronger signals before sizing up."
    )


def _nc_buy_final_takeaway(
    *,
    conviction: str,
    trusted_signals: list[str],
    incomplete_signals: list[str],
    is_etf: bool,
) -> str:
    n_missing = len(incomplete_signals)
    missing_str = _join_plain(incomplete_signals) if incomplete_signals else None

    if is_etf:
        return "Regular contribution target. Fund-level evidence limits are expected and do not indicate a hold."

    if conviction == "HIGH" and n_missing == 0:
        return "Evidence supports the buy posture. Keep sizing disciplined as new data arrives."

    if n_missing > 0:
        return (
            f"Missing {missing_str} lowers conviction but does not override the buy signal. "
            "Size gradually."
        )
    return "Evidence supports a measured buy. Keep sizing disciplined."


def _nc_hold_evidence_summary(
    *,
    trusted_signals: list[str],
    incomplete_signals: list[str],
) -> str:
    trusted_str = _join_plain(trusted_signals) if trusted_signals else None
    missing_str = _join_plain(incomplete_signals) if incomplete_signals else None
    n_trusted = len(trusted_signals)
    n_missing = len(incomplete_signals)
    plural = "are" if n_missing > 1 else "is"

    if n_trusted == 0:
        if n_missing > 0:
            return (
                f"Not enough complete evidence on {missing_str} to act. "
                "Monitoring before adding."
            )
        return "Not enough evidence to act on yet. Waiting for more complete coverage."

    if n_missing > 0:
        return (
            f"Some evidence is available on {trusted_str}, "
            f"but {missing_str} {plural} still incomplete. "
            "Not enough conviction to add or reduce at this time."
        )
    return (
        f"Evidence on {trusted_str} is available but balanced. "
        "Current signals do not favor adding or reducing."
    )


def _nc_hold_final_takeaway(
    *,
    trusted_signals: list[str],
    incomplete_signals: list[str],
) -> str:
    n_trusted = len(trusted_signals)
    n_missing = len(incomplete_signals)

    if n_trusted == 0:
        return "Wait for more complete signals before acting."
    if n_missing > 0:
        return "Hold current position while waiting for stronger or more complete signals."
    return "Evidence is usable but balanced — hold current position."


def _nc_trim_sell_evidence_summary(
    *,
    action: str,
    trusted_signals: list[str],
    incomplete_signals: list[str],
) -> str:
    trusted_str = _join_plain(trusted_signals) if trusted_signals else None
    n_trusted = len(trusted_signals)

    if action == "SELL":
        if n_trusted > 0:
            return (
                f"Evidence on {trusted_str} points to exiting or significantly reducing exposure. "
                "Current signals do not support holding or adding."
            )
        return (
            "Current signals favor exiting or significantly reducing exposure. "
            "Review position against targets."
        )

    # TRIM
    if n_trusted > 0:
        return (
            f"Evidence and positioning on {trusted_str} suggest reducing exposure here. "
            "Current signals favor lightening the position rather than adding."
        )
    return (
        "Current signals suggest reducing exposure. "
        "Review position size against targets and tax considerations."
    )


def _nc_trim_sell_final_takeaway(*, action: str) -> str:
    if action == "SELL":
        return "Consider full or substantial reduction, taking tax implications into account."
    return "Consider partial reduction; review against target allocation and tax implications."


def build_intel_card_narrative_contract(
    *,
    visible_action: str,
    conviction_label: str,
    trusted_signals: list[str],
    incomplete_signals: list[str],
    ticker: str,
    category: str,
) -> dict[str, Any]:
    """Build action-consistent Evidence Check narrative contract v1.

    Single source of truth for Evidence Check copy. All voices agree with
    the visible action badge. Forbidden HOLD/wait language never appears on
    BUY cards; forbidden BUY language never appears on TRIM/SELL cards.

    Pure function — deterministic, no IO, no LLM calls.

    Returns dict with keys:
      action, confidence_label, evidence_summary, reliable_labels,
      missing_labels, final_takeaway, conflict_flags,
      narrative_contract_version
    """
    action = (visible_action or "HOLD").upper().strip()
    conviction = (conviction_label or "LOW").upper().strip()
    if conviction not in {"HIGH", "MEDIUM", "LOW"}:
        conviction = "LOW"
    cat_low = (category or "").lower()
    is_etf = "etf" in cat_low

    if action == "BUY":
        evidence_summary = _nc_buy_evidence_summary(
            conviction=conviction,
            trusted_signals=trusted_signals,
            incomplete_signals=incomplete_signals,
            is_etf=is_etf,
        )
        final_takeaway = _nc_buy_final_takeaway(
            conviction=conviction,
            trusted_signals=trusted_signals,
            incomplete_signals=incomplete_signals,
            is_etf=is_etf,
        )
    elif action == "HOLD":
        evidence_summary = _nc_hold_evidence_summary(
            trusted_signals=trusted_signals,
            incomplete_signals=incomplete_signals,
        )
        final_takeaway = _nc_hold_final_takeaway(
            trusted_signals=trusted_signals,
            incomplete_signals=incomplete_signals,
        )
    elif action in {"TRIM", "SELL"}:
        evidence_summary = _nc_trim_sell_evidence_summary(
            action=action,
            trusted_signals=trusted_signals,
            incomplete_signals=incomplete_signals,
        )
        final_takeaway = _nc_trim_sell_final_takeaway(action=action)
    else:
        evidence_summary = "Evidence is available. Review before acting."
        final_takeaway = "Stay cautious until the signal clarifies."

    return {
        "action": action,
        "confidence_label": conviction,
        "evidence_summary": evidence_summary,
        "reliable_labels": list(trusted_signals),
        "missing_labels": list(incomplete_signals),
        "final_takeaway": final_takeaway,
        "conflict_flags": [],
        "narrative_contract_version": "v1",
    }


# ── After-sanitize conflict detection (Workstream C) ─────────────────────────
#
# Checks ALL visible text fields — including analyst fields that the narrative
# contract does not override — for action/copy mismatches.  Run AFTER the
# contract is applied so any remaining conflicts are real output conflicts.

# Patterns that indicate HOLD/wait language on BUY cards.
_BUY_HOLD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bhold\b"),
    re.compile(r"\bwait\b"),
    re.compile(r"\bwatchlist\b"),
    re.compile(r"\bearly signal\b"),
    re.compile(r"\bno allocation\b"),
    re.compile(r"\bnot yet complete\b"),
    re.compile(r"\bstay on watch\b"),
    re.compile(r"\bincomplete picture\b"),
    re.compile(r"\bnot a complete picture\b"),
)

# Patterns that indicate BUY/accumulate language on HOLD cards.
_HOLD_BUY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\badd now\b"),
    re.compile(r"\bbuy now\b"),
    re.compile(r"\benter now\b"),
    re.compile(r"\baccumulate aggressively\b"),
)

# BUY/accumulate language forbidden on TRIM/SELL cards.
_TRIM_BUY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\badd candidate\b"),
    re.compile(r"\bentry opportunity\b"),
    re.compile(r"\bconstructive view\b"),
    re.compile(r"\bmeasured buy\b"),
    re.compile(r"\bregular contribution target\b"),
    re.compile(r"\baccumulate\b"),
)

# Analyst fields that carry action-specific copy to the card.
_ANALYST_TEXT_FIELDS = (
    "action_reason",
    "primary_driver",
    "differentiation",
    "risk_flag",
    "summary",
    "reasoning_summary",
)

# HOLD/wait copy that INSUFFICIENT_DATA fallback verdicts inject.
_FALLBACK_HOLD_PHRASES: frozenset[str] = frozenset({
    "hold — no allocation until signal improves.",
    "no allocation until signal improves",
    "hold until signal improves",
})


def detect_analyst_field_conflicts(
    *,
    visible_action: str,
    card_fields: dict[str, Any],
) -> list[str]:
    """Detect action/copy mismatches in ALL visible card text fields.

    Called AFTER the narrative contract is applied (after-sanitize pass).
    Checks analyst reasoning fields that the contract does not override.

    Pure function — deterministic, no IO.
    Returns a list of conflict reason codes; empty list means no conflicts.
    """
    action = (visible_action or "HOLD").upper()
    flags: list[str] = []

    for field in _ANALYST_TEXT_FIELDS:
        text = str(card_fields.get(field) or "").lower()
        if not text:
            continue

        if action == "BUY":
            for pattern in _BUY_HOLD_PATTERNS:
                if pattern.search(text):
                    flags.append(f"buy_hold_lang:{field}:{pattern.pattern[:25]}")
                    break

        elif action == "HOLD":
            for pattern in _HOLD_BUY_PATTERNS:
                if pattern.search(text):
                    flags.append(f"hold_buy_lang:{field}")
                    break

        elif action in {"TRIM", "SELL"}:
            for pattern in _TRIM_BUY_PATTERNS:
                if pattern.search(text):
                    flags.append(f"trim_buy_lang:{field}")
                    break

    return flags


def sanitize_analyst_fields_for_action(
    *,
    visible_action: str,
    card_fields: dict[str, Any],
) -> dict[str, Any]:
    """Return a copy of card_fields with conflicting analyst text replaced.

    Fail-closed: for BUY cards, any action_reason containing HOLD/wait
    language (from INSUFFICIENT_DATA fallback verdicts) is replaced with
    measured-buy copy.  For TRIM/SELL, any buy language is replaced.

    Pure function — deterministic, no IO, no LLM.
    Returns the sanitized dict; callers replace fields in the card.
    """
    action = (visible_action or "HOLD").upper()
    out = dict(card_fields)

    if action == "BUY":
        action_reason = str(out.get("action_reason") or "").lower()
        # Replace INSUFFICIENT_DATA fallback "Hold — no allocation" with
        # action-consistent measured-buy copy.
        is_fallback_hold = any(phrase in action_reason for phrase in _FALLBACK_HOLD_PHRASES)
        has_hold_pattern = any(p.search(action_reason) for p in _BUY_HOLD_PATTERNS)
        if is_fallback_hold or has_hold_pattern:
            out["action_reason"] = "Size modestly and monitor for confirmation before adding more."
        # Sanitize primary_driver if it contains hard hold language.
        primary_driver = str(out.get("primary_driver") or "").lower()
        if any(phrase in primary_driver for phrase in _FALLBACK_HOLD_PHRASES):
            out["primary_driver"] = out.get("action_reason", "")
        # Wipe differentiation if it contains forbidden phrases.
        differentiation = str(out.get("differentiation") or "").lower()
        if any(p.search(differentiation) for p in _BUY_HOLD_PATTERNS):
            out["differentiation"] = None

    elif action in {"TRIM", "SELL"}:
        action_reason = str(out.get("action_reason") or "").lower()
        has_buy_pattern = any(p.search(action_reason) for p in _TRIM_BUY_PATTERNS)
        if has_buy_pattern:
            out["action_reason"] = "Reduce exposure here; current signals favor lightening the position."

    return out
