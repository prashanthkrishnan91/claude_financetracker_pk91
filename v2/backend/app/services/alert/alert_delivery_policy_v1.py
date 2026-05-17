"""Alert Delivery Policy v1 — provider-neutral delivery spec builder.

Pure module: given a candidate row (dict from DB), returns a DeliverySpec.
No IO, no LLM, no external providers. Does not mutate candidates, Intel v3,
Deploy, Watchtower, or feedback rows.

Delivery modes:
  - immediate: SELL + high severity only (genuinely time-critical)
  - digest: default for everything else

Channels (provider-neutral labels):
  - email (default)
  - push (reserved for future stage)
  - in_app (reserved for future stage)

Content rules:
  - Uses candidate plain_english_reason verbatim — no fabricated prices or targets
  - Appends non-broker disclaimer: "Review in the app before acting."
  - No raw metric-heavy language
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

OUTBOX_POLICY_VERSION = "v1"

_DISCLAIMER = "Review in the app before acting."

# Only SELL + high severity warrants immediate delivery.
# BUY/TRIM/conviction_upgrade default to digest even at high severity.
_IMMEDIATE_ACTIONS = frozenset({"SELL"})


@dataclass
class DeliverySpec:
    """Provider-neutral delivery specification for one outbox entry."""

    user_id: str
    alert_candidate_id: str
    ticker: str
    channel: str
    delivery_mode: str          # immediate | digest
    severity: str               # low | normal | high
    subject: str
    plain_english_body: str
    dedupe_key: str
    policy_version: str = OUTBOX_POLICY_VERSION
    scheduled_for: Optional[str] = None


def build_outbox_dedupe_key(
    user_id: str,
    alert_candidate_id: str,
    channel: str,
) -> str:
    """Build an idempotency key for this outbox entry.

    Scoped to (user_id, alert_candidate_id, channel, policy_version).
    Re-processing the same candidate+channel pair produces the same key.
    Different candidates for the same ticker produce different keys, but
    noisy-repeat suppression in the outbox service catches that case.
    """
    parts = [user_id, alert_candidate_id, channel, OUTBOX_POLICY_VERSION]
    raw = ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _build_subject(action_type: Optional[str], ticker: str, candidate_type: str) -> str:
    action = (action_type or "").upper()
    if action == "SELL":
        return f"Urgent Alert: {ticker} — SELL signal"
    if action == "TRIM":
        return f"Alert: {ticker} — TRIM signal"
    if action == "BUY":
        if candidate_type == "conviction_upgrade":
            return f"Update: {ticker} — BUY conviction upgraded"
        return f"Opportunity: {ticker} — BUY signal"
    return f"Alert: {ticker}"


def _build_body(plain_english_reason: str) -> str:
    return f"{plain_english_reason}\n\n{_DISCLAIMER}"


def _resolve_delivery_mode(action_type: Optional[str], severity: str) -> str:
    action = (action_type or "").upper()
    if action in _IMMEDIATE_ACTIONS and severity == "high":
        return "immediate"
    return "digest"


def build_delivery_spec(
    candidate_row: dict,
    channel: str = "email",
) -> Optional[DeliverySpec]:
    """Build a provider-neutral delivery spec from a persisted candidate row.

    Returns None if the candidate is not eligible for delivery.
    Only rows with status='candidate' are eligible.
    """
    if candidate_row.get("status") != "candidate":
        return None

    user_id = str(candidate_row.get("user_id") or "")
    candidate_id = str(candidate_row.get("id") or "")
    ticker = (candidate_row.get("ticker") or "").upper()
    action_type = candidate_row.get("action_type")
    severity = candidate_row.get("severity") or "normal"
    candidate_type = candidate_row.get("candidate_type") or ""
    plain_english_reason = candidate_row.get("plain_english_reason") or ""

    if not (user_id and candidate_id and ticker):
        return None

    dedupe_key = build_outbox_dedupe_key(user_id, candidate_id, channel)
    subject = _build_subject(action_type, ticker, candidate_type)
    body = _build_body(plain_english_reason)
    mode = _resolve_delivery_mode(action_type, severity)

    logger.debug(
        "alert_delivery_policy.spec_built user_id=%s ticker=%s "
        "channel=%s delivery_mode=%s severity=%s",
        user_id,
        ticker,
        channel,
        mode,
        severity,
    )
    return DeliverySpec(
        user_id=user_id,
        alert_candidate_id=candidate_id,
        ticker=ticker,
        channel=channel,
        delivery_mode=mode,
        severity=severity,
        subject=subject,
        plain_english_body=body,
        dedupe_key=dedupe_key,
        policy_version=OUTBOX_POLICY_VERSION,
    )
