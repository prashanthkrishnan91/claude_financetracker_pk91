"""Resend email provider client — thin HTTP wrapper.

Uses httpx (already in requirements). Returns a normalized ResendSendResult
that is mockable in tests. Never raises — all errors are captured in the result.

No LLM calls. No Intel v3, Deploy, Watchtower, or candidate mutations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_RESEND_BASE_URL = "https://api.resend.com"
_TIMEOUT_SECONDS = 15.0


@dataclass
class ResendSendResult:
    """Normalized result from a Resend send attempt."""

    success: bool
    provider_message_id: Optional[str]
    failure_reason: Optional[str]


class ResendClient:
    """Thin synchronous wrapper around the Resend /emails API.

    Construct with an api_key. Call send_email() for each message.
    base_url is injectable for testing (point at a mock server).
    """

    def __init__(self, api_key: str, *, base_url: str = _RESEND_BASE_URL) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def send_email(
        self,
        *,
        from_addr: str,
        to_addrs: list[str],
        subject: str,
        body: str,
    ) -> ResendSendResult:
        """Send one email via Resend. Returns a normalized result; never raises."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "from": from_addr,
            "to": to_addrs,
            "subject": subject,
            "text": body,
        }
        try:
            response = httpx.post(
                f"{self._base_url}/emails",
                json=payload,
                headers=headers,
                timeout=_TIMEOUT_SECONDS,
            )
            if response.status_code in (200, 201):
                data: dict = {}
                try:
                    data = response.json()
                except Exception:
                    pass
                msg_id = data.get("id")
                logger.info(
                    "resend_client.send_success provider_message_id=%s status=%s",
                    msg_id,
                    response.status_code,
                )
                return ResendSendResult(
                    success=True,
                    provider_message_id=str(msg_id) if msg_id else None,
                    failure_reason=None,
                )
            else:
                reason = f"http_{response.status_code}: {response.text[:200]}"
                logger.warning("resend_client.send_http_error reason=%r", reason)
                return ResendSendResult(
                    success=False,
                    provider_message_id=None,
                    failure_reason=reason,
                )
        except Exception as exc:
            reason = str(exc)[:300]
            logger.warning("resend_client.send_exception reason=%r", reason)
            return ResendSendResult(
                success=False,
                provider_message_id=None,
                failure_reason=reason,
            )
