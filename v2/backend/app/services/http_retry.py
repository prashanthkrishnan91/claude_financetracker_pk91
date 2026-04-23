"""Shared outbound retry helpers for transient network transport errors."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)
T = TypeVar("T")

_RETRY_DELAYS = (0.5, 1.0, 2.0)
_TRANSIENT_TYPES = (
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.WriteError,
)


def is_transient_http_error(exc: Exception) -> bool:
    if isinstance(exc, _TRANSIENT_TYPES):
        return True
    msg = str(exc).lower()
    return any(k in msg for k in ("timeout", "http2", "connection reset", "remote protocol"))


def run_with_retry_sync(fn: Callable[[], T], *, op_name: str) -> T:
    last: Exception | None = None
    for idx, delay in enumerate((0.0, *_RETRY_DELAYS), start=1):
        if delay > 0:
            time.sleep(delay)
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if not is_transient_http_error(exc) or idx >= len(_RETRY_DELAYS) + 1:
                logger.warning("outbound.final_failure op=%s err=%s", op_name, exc)
                raise
            last = exc
            continue
    if last:
        raise last
    raise RuntimeError(f"retry helper exhausted unexpectedly: {op_name}")


async def run_with_retry_async(fn: Callable[[], Any], *, op_name: str):
    last: Exception | None = None
    for idx, delay in enumerate((0.0, *_RETRY_DELAYS), start=1):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            if not is_transient_http_error(exc) or idx >= len(_RETRY_DELAYS) + 1:
                logger.warning("outbound.final_failure op=%s err=%s", op_name, exc)
                raise
            last = exc
            continue
    if last:
        raise last
    raise RuntimeError(f"retry helper exhausted unexpectedly: {op_name}")
