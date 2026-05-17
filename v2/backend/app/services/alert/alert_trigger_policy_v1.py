"""Alert Trigger Policy v1 — deterministic alert-worthiness evaluation.

This module is PURE: given inputs, it produces structured output. No DB, no LLM, no IO.
It does NOT mutate Intel v3 decisions, Deploy sizing, Watchtower data, or feedback rows.

Policy version: v1

Rules:
  1. Candidates only for BUY / TRIM / SELL actions with STRONG or PARTIAL evidence bands.
     PARTIAL maps to AxisBand.OK from snapshot_builder._EVIDENCE_QUALITY_TO_BAND —
     it is the display label for "OK" axis quality. THIN, SUPPRESSED, blank, or unknown
     evidence bands never produce actionable candidates.
  2. HOLD actions are never candidates.
  3. new_actionable_action candidate: action changed from non-actionable (or ticker newly
     appears in portfolio) between prior and current snapshot. When no prior snapshot
     exists (first ever evaluation), no candidates are created (conservative / low-noise).
  4. conviction_upgrade candidate: BUY conviction increases (LOW→MEDIUM, LOW→HIGH,
     MEDIUM→HIGH) while action stays BUY and evidence band is STRONG or PARTIAL.
  5. Feedback suppression:
     - executed → indefinite suppression for same user/ticker/action
     - ignored | not_relevant → 7-day cooldown
     - too_risky → 7-day cooldown
     - snoozed → cooldown_until field if set, else 14-day default
     - user_note → never suppresses
     - skipped → never suppresses
  6. Dedupe key is snapshot-scoped so re-evaluating the same snapshot is idempotent.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

POLICY_VERSION = "v1"

# Evidence bands that permit actionable candidates.
# PARTIAL = snapshot_builder._EVIDENCE_QUALITY_TO_BAND[AxisBand.OK.value] — production
# cards carry "PARTIAL" for what the axis layer calls "OK" quality evidence.
# THIN, SUPPRESSED, blank, and unknown bands are all non-actionable.
_ACTIONABLE_BANDS = frozenset({"STRONG", "PARTIAL"})

# Actions that may produce candidates (HOLD never does)
_ACTIONABLE_ACTIONS = frozenset({"BUY", "TRIM", "SELL"})

# Feedback cooldown windows
_COOLDOWN_DAYS_IGNORED = 7
_COOLDOWN_DAYS_NOT_RELEVANT = 7
_COOLDOWN_DAYS_TOO_RISKY = 7
_COOLDOWN_DAYS_SNOOZED = 14

# Conviction ordering for upgrade detection (higher value = stronger conviction)
_CONVICTION_RANK: dict[str, int] = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


@dataclass
class AlertCandidate:
    """A single alert candidate produced by the policy."""

    user_id: str
    ticker: str
    source_area: str        # intel | deploy | watchtower
    candidate_type: str     # new_actionable_action | conviction_upgrade
    action_type: Optional[str]  # BUY | TRIM | SELL | None
    severity: str           # low | normal | high
    reason_code: str
    plain_english_reason: str
    dedupe_key: str
    policy_version: str = POLICY_VERSION
    source_snapshot_id: Optional[str] = None
    source_run_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    status: str = "candidate"


@dataclass
class AlertSuppression:
    """A candidate that was evaluated but suppressed by evidence or feedback rules."""

    ticker: str
    candidate_type: str
    action_type: Optional[str]
    suppression_reason: str
    dedupe_key: str


@dataclass
class AlertPolicyResult:
    """Output of the alert trigger policy for one user/snapshot evaluation."""

    user_id: str
    evaluated_ticker_count: int
    candidates: list[AlertCandidate] = field(default_factory=list)
    suppressions: list[AlertSuppression] = field(default_factory=list)
    policy_version: str = POLICY_VERSION


# ── Internal helpers ────────────────────────────────────────────────────────────


def _build_dedupe_key(
    user_id: str,
    ticker: str,
    candidate_type: str,
    action_type: Optional[str],
    snapshot_id: Optional[str],
) -> str:
    """Build an idempotency key for this candidate, scoped to the snapshot.

    Including snapshot_id means re-evaluating the same snapshot produces the
    same key (idempotent persistence), while a new snapshot produces a new key
    (allowing a fresh candidate if the action changes again).
    """
    parts = [
        user_id,
        ticker.upper(),
        candidate_type,
        action_type or "",
        snapshot_id or "",
        POLICY_VERSION,
    ]
    raw = ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _parse_timestamp(value: object) -> Optional[datetime]:
    """Parse a timestamp from a DB row field. Returns None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _is_suppressed_by_feedback(
    user_id: str,
    ticker: str,
    action_type: Optional[str],
    feedback_rows: list[dict],
    now: datetime,
) -> tuple[bool, str]:
    """Check whether recent feedback suppresses a candidate.

    Returns (is_suppressed, plain_english_reason). Checks rows in order; the
    first matching suppression wins. user_note and skipped never suppress.
    """
    ticker_upper = ticker.upper()

    for row in feedback_rows:
        if row.get("user_id") != user_id:
            continue
        row_ticker = (row.get("ticker") or "").upper()
        if row_ticker != ticker_upper:
            continue

        fb_type = row.get("feedback_type", "")
        fb_action = row.get("action_type")

        # action_type filter: only suppress when both sides specify an action
        # and they differ.
        if action_type and fb_action and fb_action != action_type:
            continue

        fb_time = _parse_timestamp(row.get("created_at"))
        if fb_time is None:
            continue

        age_days = (now - fb_time).days

        if fb_type == "executed":
            return True, f"User marked {ticker_upper} {action_type or 'action'} as executed"

        if fb_type in ("ignored", "not_relevant") and age_days < _COOLDOWN_DAYS_IGNORED:
            return (
                True,
                f"User marked {ticker_upper} as {fb_type} {age_days}d ago "
                f"(cooldown {_COOLDOWN_DAYS_IGNORED}d)",
            )

        if fb_type == "too_risky" and age_days < _COOLDOWN_DAYS_TOO_RISKY:
            return (
                True,
                f"User marked {ticker_upper} as too_risky {age_days}d ago "
                f"(cooldown {_COOLDOWN_DAYS_TOO_RISKY}d)",
            )

        if fb_type == "snoozed":
            cooldown_until = _parse_timestamp(row.get("cooldown_until"))
            if cooldown_until and now < cooldown_until:
                return True, f"User snoozed {ticker_upper} until {cooldown_until.date()}"
            # Default snoozed cooldown applies when no cooldown_until is set
            if cooldown_until is None and age_days < _COOLDOWN_DAYS_SNOOZED:
                return (
                    True,
                    f"User snoozed {ticker_upper} {age_days}d ago "
                    f"(cooldown {_COOLDOWN_DAYS_SNOOZED}d)",
                )

        # user_note and skipped: fall through (no suppression)

    return False, ""


def _conviction_rank(conviction: Optional[str]) -> int:
    return _CONVICTION_RANK.get((conviction or "").upper(), 0)


def _severity_for_action(action: str, conviction: str) -> str:
    if action == "SELL":
        return "high"
    if action == "TRIM":
        return "normal"
    if action == "BUY":
        conv = conviction.upper()
        if conv == "HIGH":
            return "high"
        if conv == "MEDIUM":
            return "normal"
        return "low"
    return "normal"


def _plain_english_reason(
    action: str,
    ticker: str,
    conviction: str,
    band: str,
    prior_action: Optional[str],
) -> str:
    conv_label = conviction.lower()
    band_label = band.lower()

    if action == "BUY":
        if prior_action and prior_action != action and prior_action in _ACTIONABLE_ACTIONS:
            return (
                f"{ticker} action changed to BUY "
                f"({conv_label} conviction, {band_label} evidence)."
            )
        return (
            f"{ticker} is a new BUY opportunity "
            f"({conv_label} conviction, {band_label} evidence)."
        )
    if action == "TRIM":
        return (
            f"{ticker} signals a TRIM — consider reducing your position "
            f"({conv_label} conviction)."
        )
    if action == "SELL":
        return (
            f"{ticker} signals a SELL — consider exiting your position "
            f"({conv_label} conviction)."
        )
    return f"{ticker} action: {action} ({conv_label} conviction, {band_label} evidence)."


# ── Public API ──────────────────────────────────────────────────────────────────


def evaluate_snapshot_for_alert_candidates(
    *,
    user_id: str,
    current_snapshot_cards: list[dict],
    prior_snapshot_cards: list[dict] | None,
    feedback_rows: list[dict],
    snapshot_id: Optional[str] = None,
    run_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> AlertPolicyResult:
    """Evaluate current snapshot cards and produce structured alert candidates.

    This function is PURE — it reads inputs and returns output; no side effects.

    Args:
        user_id: Owner of the snapshot being evaluated.
        current_snapshot_cards: current_holdings cards from the Intel v3 snapshot.
            Each card must have: ticker, action, conviction, evidence_band.
        prior_snapshot_cards: Cards from the immediately prior snapshot. Pass None
            when no prior snapshot exists (conservative: no candidates created).
            Pass an empty list when a prior snapshot existed but had no holdings.
        feedback_rows: Recent action_feedback_events rows for this user.
            Used for suppression / cooldown logic only; never mutated.
        snapshot_id: ID of the current snapshot (for dedupe key + provenance).
        run_id: Run ID for provenance only.
        now: Override current time (for testing).

    Returns:
        AlertPolicyResult with candidates and suppressions.
        Never mutates Intel v3, Deploy, Watchtower, or feedback data.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    result = AlertPolicyResult(
        user_id=user_id,
        evaluated_ticker_count=len(current_snapshot_cards),
    )

    # Conservative: when there is no prior snapshot, we have no transition baseline.
    # Skip all candidates to avoid a flood on first evaluation.
    if prior_snapshot_cards is None:
        logger.info(
            "alert_trigger_policy.no_prior_snapshot user_id=%s snapshot_id=%s "
            "cards=%d policy_version=%s — skipping candidates (no baseline)",
            user_id,
            snapshot_id,
            len(current_snapshot_cards),
            POLICY_VERSION,
        )
        return result

    # Build lookup from prior snapshot
    prior_by_ticker: dict[str, dict] = {}
    for card in prior_snapshot_cards:
        t = (card.get("ticker") or "").upper()
        if t:
            prior_by_ticker[t] = card

    for card in current_snapshot_cards:
        ticker = (card.get("ticker") or "").upper()
        if not ticker:
            continue

        current_action = (card.get("action") or "").upper()
        current_conviction = (card.get("conviction") or "").upper()
        current_band = (card.get("evidence_band") or "").upper()

        prior_card = prior_by_ticker.get(ticker)
        prior_action = (prior_card.get("action") or "").upper() if prior_card else None
        prior_conviction = (prior_card.get("conviction") or "").upper() if prior_card else None

        # ── Rule 1: Weak / missing evidence → no actionable candidate ──────────
        if current_band not in _ACTIONABLE_BANDS and current_action in _ACTIONABLE_ACTIONS:
            dedupe = _build_dedupe_key(
                user_id, ticker, "new_actionable_action", current_action, snapshot_id
            )
            result.suppressions.append(
                AlertSuppression(
                    ticker=ticker,
                    candidate_type="new_actionable_action",
                    action_type=current_action,
                    suppression_reason=(
                        f"Evidence band '{current_band}' is below actionable threshold "
                        f"(STRONG or PARTIAL required)"
                    ),
                    dedupe_key=dedupe,
                )
            )
            continue

        # ── Rule 2: HOLD and non-supported actions are never candidates ─────────
        if current_action not in _ACTIONABLE_ACTIONS:
            continue

        # ── Rule 3: Weak evidence for this branch (already checked above for ──
        # ── actionable actions; re-guard for safety) ────────────────────────────
        if current_band not in _ACTIONABLE_BANDS:
            continue

        # ── Rule 4: New actionable action ───────────────────────────────────────
        #   - Ticker newly appears in portfolio (not in prior snapshot)
        #   - OR action changed to a different actionable action
        #   - OR action was HOLD/non-actionable in prior snapshot
        is_newly_actionable = (
            prior_card is None  # new ticker added to portfolio
            or prior_action != current_action
            or prior_action not in _ACTIONABLE_ACTIONS
        )

        if is_newly_actionable:
            dedupe = _build_dedupe_key(
                user_id, ticker, "new_actionable_action", current_action, snapshot_id
            )
            suppressed, sup_reason = _is_suppressed_by_feedback(
                user_id, ticker, current_action, feedback_rows, now
            )
            if suppressed:
                result.suppressions.append(
                    AlertSuppression(
                        ticker=ticker,
                        candidate_type="new_actionable_action",
                        action_type=current_action,
                        suppression_reason=sup_reason,
                        dedupe_key=dedupe,
                    )
                )
            else:
                reason = _plain_english_reason(
                    action=current_action,
                    ticker=ticker,
                    conviction=current_conviction,
                    band=current_band,
                    prior_action=prior_action,
                )
                result.candidates.append(
                    AlertCandidate(
                        user_id=user_id,
                        ticker=ticker,
                        source_area="intel",
                        candidate_type="new_actionable_action",
                        action_type=current_action,
                        severity=_severity_for_action(current_action, current_conviction),
                        reason_code=f"action_became_{current_action.lower()}",
                        plain_english_reason=reason,
                        dedupe_key=dedupe,
                        policy_version=POLICY_VERSION,
                        source_snapshot_id=snapshot_id,
                        source_run_id=run_id,
                    )
                )
            continue

        # ── Rule 5: Conviction upgrade for sustained BUY ─────────────────────────
        if (
            current_action == "BUY"
            and prior_action == "BUY"
            and prior_conviction is not None
            and _conviction_rank(current_conviction) > _conviction_rank(prior_conviction)
            and current_conviction in ("MEDIUM", "HIGH")
        ):
            dedupe = _build_dedupe_key(
                user_id, ticker, "conviction_upgrade", "BUY", snapshot_id
            )
            suppressed, sup_reason = _is_suppressed_by_feedback(
                user_id, ticker, "BUY", feedback_rows, now
            )
            if suppressed:
                result.suppressions.append(
                    AlertSuppression(
                        ticker=ticker,
                        candidate_type="conviction_upgrade",
                        action_type="BUY",
                        suppression_reason=sup_reason,
                        dedupe_key=dedupe,
                    )
                )
            else:
                reason = (
                    f"{ticker} BUY conviction upgraded from {prior_conviction} to "
                    f"{current_conviction} with {current_band.lower()} evidence."
                )
                result.candidates.append(
                    AlertCandidate(
                        user_id=user_id,
                        ticker=ticker,
                        source_area="intel",
                        candidate_type="conviction_upgrade",
                        action_type="BUY",
                        severity="normal",
                        reason_code="buy_conviction_upgraded",
                        plain_english_reason=reason,
                        dedupe_key=dedupe,
                        policy_version=POLICY_VERSION,
                        source_snapshot_id=snapshot_id,
                        source_run_id=run_id,
                    )
                )

    logger.info(
        "alert_trigger_policy.evaluated user_id=%s evaluated=%d candidates=%d "
        "suppressions=%d snapshot_id=%s policy_version=%s",
        user_id,
        result.evaluated_ticker_count,
        len(result.candidates),
        len(result.suppressions),
        snapshot_id,
        POLICY_VERSION,
    )
    return result
