"""Distributed lock primitive for cross-worker coordination.

Contract (see v4 distributed-correctness spec, task #1):
  * ``DistributedLock.acquire_or_wait(key, factory, *, ttl_s, wait_s)`` is the
    one entry point. At most ONE worker globally executes ``factory()`` per
    ``key`` per ``ttl_s`` window; every other worker blocks until the winning
    worker publishes the result, or until ``wait_s`` elapses.
  * Auto-expiring locks: the dispatcher registers a lock row with
    ``expires_at = now + ttl_s``. If the dispatcher crashes mid-run the lock
    expires naturally and the next caller takes over — no deadlocks.
  * Structured fallbacks: if every backend is disabled (no Redis URL and no
    Supabase client) the lock is a transparent no-op that always runs the
    factory locally. Callers still get in-process coalescing from
    ``RequestCoalescer`` — the distributed lock is an additional, NOT
    substitute, layer.

Pluggable backends:
  * ``RedisLockBackend``       — preferred; SETNX-based with WATCH/MULTI for
                                 result publication. Only used when ``REDIS_URL``
                                 env is set AND the ``redis`` pip package is
                                 importable.
  * ``SupabaseLockBackend``    — fallback; uses the ``api_call_ledger`` table.
                                 INSERT ON CONFLICT DO NOTHING decides who
                                 dispatches; waiters poll the row for the
                                 result. Requires the table from migration
                                 ``007_distributed_locks.sql``.
  * ``InMemoryLockBackend``    — test / single-worker fallback. Not
                                 cross-worker safe — still honours the API so
                                 unit tests exercise the full code path.

Publication format (JSON, stored in backend):
  ``{"value": <any JSON-safe>, "error": <str|null>, "ts": <float>}``

When a factory raises, the error string is published so waiters see the same
failure and can apply their own fallback. This preserves the "no exception
propagates upstream" contract set out in the stability spec.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol

logger = logging.getLogger(__name__)

# Default lock TTL — spec allows 30–60s; 45s is a comfortable middle ground
# that covers slow-provider latency without holding a dead lock too long.
DEFAULT_LOCK_TTL_S = 45.0
# Default waiter budget — how long a loser polls for the winner's result
# before giving up and running locally (graceful fallback).
DEFAULT_WAIT_TIMEOUT_S = 20.0
# Poll interval for backends that don't support pub/sub (Supabase fallback).
_POLL_INTERVAL_S = 0.25


# ── Worker identity ────────────────────────────────────────────────────────────
# Stable per-process id — embedded in lock owner rows + duplicate-call logs
# so operators can correlate "who acquired this" with "who did the fetch".

_WORKER_ID = f"{os.getenv('RAILWAY_SERVICE_NAME', 'local')}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def get_worker_id() -> str:
    return _WORKER_ID


# ── Structured result payload ──────────────────────────────────────────────────


@dataclass
class LockResult:
    """Outcome of a ``acquire_or_wait`` call.

    ``source`` tells the caller how the value was obtained:
      * ``"dispatched"`` — this worker won the lock and ran the factory.
      * ``"awaited"``    — this worker waited and received the dispatcher's result.
      * ``"local"``      — no backend available / timed out; ran factory locally.
      * ``"error"``      — dispatcher (or local factory) raised; ``error`` is set.
    """

    value: Any = None
    source: str = "local"
    error: Optional[str] = None
    worker_id: str = field(default_factory=get_worker_id)


# ── Backend protocol ──────────────────────────────────────────────────────────


class LockBackend(Protocol):
    """Abstract contract for a cross-worker lock + result publication store."""

    def is_available(self) -> bool: ...

    async def try_acquire(self, key: str, *, ttl_s: float) -> bool: ...

    async def publish_result(
        self, key: str, value: Any, *, error: Optional[str], ttl_s: float
    ) -> None: ...

    async def wait_for_result(
        self, key: str, *, timeout_s: float
    ) -> Optional[LockResult]: ...

    async def release(self, key: str) -> None: ...


# ── In-memory backend (tests / single-worker fallback) ────────────────────────


@dataclass
class _InMemoryEntry:
    owner: str
    expires_at: float
    result: Any = None
    error: Optional[str] = None
    resolved: bool = False


class InMemoryLockBackend:
    """Single-process fallback — useful in tests and when no DSN is configured.

    NOT cross-worker safe. The distributed lock class falls back to this when
    no Redis/Supabase backend is wired up so the API surface stays consistent
    regardless of deployment.
    """

    def __init__(self) -> None:
        self._store: dict[str, _InMemoryEntry] = {}
        self._lock = asyncio.Lock()
        # Condition per key — waiters wake on publish / release.
        self._conds: dict[str, asyncio.Condition] = {}

    def is_available(self) -> bool:
        return True

    async def _cond_for(self, key: str) -> asyncio.Condition:
        async with self._lock:
            cond = self._conds.get(key)
            if cond is None:
                cond = asyncio.Condition()
                self._conds[key] = cond
            return cond

    async def try_acquire(self, key: str, *, ttl_s: float) -> bool:
        now = time.time()
        async with self._lock:
            entry = self._store.get(key)
            # A fresh entry blocks re-acquisition whether it's still
            # running OR has a resolved result within its TTL window —
            # in the resolved case we want the next caller to READ the
            # result via wait_for_result, not overwrite it with a new
            # dispatch.
            if entry is not None and entry.expires_at > now:
                return False
            self._store[key] = _InMemoryEntry(
                owner=get_worker_id(),
                expires_at=now + ttl_s,
            )
            return True

    async def publish_result(
        self, key: str, value: Any, *, error: Optional[str], ttl_s: float
    ) -> None:
        cond = await self._cond_for(key)
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                # Publish-without-acquire: still record so waiters unblock.
                entry = _InMemoryEntry(
                    owner=get_worker_id(),
                    expires_at=time.time() + ttl_s,
                )
                self._store[key] = entry
            entry.result = value
            entry.error = error
            entry.resolved = True
            entry.expires_at = time.time() + ttl_s
        async with cond:
            cond.notify_all()

    async def wait_for_result(
        self, key: str, *, timeout_s: float
    ) -> Optional[LockResult]:
        cond = await self._cond_for(key)
        deadline = time.time() + timeout_s
        async with cond:
            while True:
                async with self._lock:
                    entry = self._store.get(key)
                    if entry is not None and entry.resolved:
                        return LockResult(
                            value=entry.result,
                            source="awaited",
                            error=entry.error,
                        )
                    if entry is None or entry.expires_at <= time.time():
                        # Lock vanished / expired without a publish — loser
                        # should fall back to local execution.
                        return None
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(cond.wait(), timeout=min(remaining, 1.0))
                except asyncio.TimeoutError:
                    continue

    async def release(self, key: str) -> None:
        """Hard-release — only removes un-resolved (orphan) locks.

        After ``publish_result`` the entry stays in the store for its TTL
        window so subsequent waiters see the published result instead of
        re-dispatching. Release is therefore a cleanup path for
        crashed / cancelled dispatchers; successful dispatchers don't need
        to release because the resolved entry auto-expires naturally.
        """
        cond = await self._cond_for(key)
        async with self._lock:
            entry = self._store.get(key)
            if entry is not None and not entry.resolved:
                self._store.pop(key, None)
        async with cond:
            cond.notify_all()


# ── Redis backend ─────────────────────────────────────────────────────────────


class RedisLockBackend:
    """Redis-based distributed lock — preferred production backend.

    Uses atomic ``SET key NX PX`` for lock acquisition and a sibling
    ``result:{key}`` record for result publication. Waiters poll with a
    short interval (no pub/sub requirement — keeps the Redis setup minimal).

    Enabled only when both ``REDIS_URL`` env is set AND the ``redis.asyncio``
    package is importable; otherwise ``is_available()`` returns ``False`` and
    the caller falls through to the Supabase / in-memory backends.
    """

    def __init__(self, url: Optional[str] = None) -> None:
        self._url = url or os.getenv("REDIS_URL", "").strip()
        self._client = None
        self._import_ok = False
        self._probe()

    def _probe(self) -> None:
        if not self._url:
            return
        try:
            import redis.asyncio as _redis  # type: ignore

            self._client_factory = _redis.from_url
            self._import_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("Redis backend disabled: %s", exc)
            self._import_ok = False

    def _client_async(self):
        if not self._import_ok or not self._url:
            return None
        if self._client is None:
            self._client = self._client_factory(
                self._url, decode_responses=True, socket_timeout=1.5
            )
        return self._client

    def is_available(self) -> bool:
        return self._import_ok and bool(self._url)

    async def try_acquire(self, key: str, *, ttl_s: float) -> bool:
        client = self._client_async()
        if client is None:
            return False
        try:
            lock_key = f"lock:{key}"
            # ``nx=True`` → only set if absent; ``px=`` → millisecond TTL.
            ok = await client.set(
                lock_key, get_worker_id(), nx=True, px=int(ttl_s * 1000)
            )
            return bool(ok)
        except Exception as exc:  # noqa: BLE001
            logger.debug("redis try_acquire failed for %s: %s", key, exc)
            return False

    async def publish_result(
        self, key: str, value: Any, *, error: Optional[str], ttl_s: float
    ) -> None:
        client = self._client_async()
        if client is None:
            return
        payload = json.dumps(
            {"value": value, "error": error, "ts": time.time()},
            default=str,
        )
        try:
            await client.set(f"result:{key}", payload, px=int(ttl_s * 1000))
        except Exception as exc:  # noqa: BLE001
            logger.debug("redis publish failed for %s: %s", key, exc)

    async def wait_for_result(
        self, key: str, *, timeout_s: float
    ) -> Optional[LockResult]:
        client = self._client_async()
        if client is None:
            return None
        deadline = time.time() + timeout_s
        result_key = f"result:{key}"
        while time.time() < deadline:
            try:
                raw = await client.get(result_key)
            except Exception as exc:  # noqa: BLE001
                logger.debug("redis wait_for_result error for %s: %s", key, exc)
                return None
            if raw is not None:
                try:
                    payload = json.loads(raw)
                except Exception:  # noqa: BLE001
                    payload = {"value": None, "error": "invalid-payload"}
                return LockResult(
                    value=payload.get("value"),
                    source="awaited",
                    error=payload.get("error"),
                )
            await asyncio.sleep(_POLL_INTERVAL_S)
        return None

    async def release(self, key: str) -> None:
        client = self._client_async()
        if client is None:
            return
        try:
            await client.delete(f"lock:{key}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("redis release failed for %s: %s", key, exc)


# ── Supabase backend ──────────────────────────────────────────────────────────


class SupabaseLockBackend:
    """Supabase advisory-lock-style fallback using a lock-ledger table.

    Table contract (created by migration ``007_distributed_locks.sql``):

      api_call_ledger(
        key TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',     -- running|done|error
        result JSONB,
        error TEXT
      )

    ``try_acquire`` uses INSERT ON CONFLICT DO NOTHING, which is atomic in
    Postgres — at most one worker inserts a row for a given key. Waiters poll
    the row for ``status='done'|'error'``. If the row's ``expires_at`` lapses
    without a resolution, a new caller re-acquires with UPDATE … WHERE
    expires_at < now().
    """

    TABLE = "api_call_ledger"

    def __init__(self) -> None:
        self._available: Optional[bool] = None

    def _client(self):
        try:
            from ...database import get_supabase_client
            return get_supabase_client()
        except Exception as exc:  # noqa: BLE001
            logger.debug("supabase lock backend unavailable: %s", exc)
            self._available = False
            return None

    def is_available(self) -> bool:
        if self._available is False:
            return False
        if os.getenv("DISTRIBUTED_LOCK_BACKEND", "auto").lower() in {"off", "disabled"}:
            self._available = False
            return False
        # Lazy: only set True once we've successfully used the client.
        return self._available is None or self._available is True

    async def try_acquire(self, key: str, *, ttl_s: float) -> bool:
        client = self._client()
        if client is None:
            return False

        def _insert() -> bool:
            from datetime import datetime, timedelta, timezone

            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(seconds=ttl_s)
            try:
                # First try to claim an expired row (UPDATE where expires_at < now)
                # — this handles the dispatcher-crashed case without violating
                # the PK on reinsert.
                upd = (
                    client.table(self.TABLE)
                    .update({
                        "owner": get_worker_id(),
                        "acquired_at": now.isoformat(),
                        "expires_at": expires_at.isoformat(),
                        "status": "running",
                        "result": None,
                        "error": None,
                    })
                    .eq("key", key)
                    .lt("expires_at", now.isoformat())
                    .execute()
                )
                if upd.data:
                    self._available = True
                    return True
                # Fresh claim — if the key doesn't exist yet, INSERT wins.
                ins = client.table(self.TABLE).insert({
                    "key": key,
                    "owner": get_worker_id(),
                    "acquired_at": now.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "status": "running",
                }).execute()
                self._available = True
                return bool(ins.data)
            except Exception as exc:  # noqa: BLE001
                # A unique-violation means another worker beat us to the row.
                # Every other exception degrades the backend to "unavailable".
                msg = str(exc).lower()
                if "duplicate" in msg or "unique" in msg or "23505" in msg:
                    return False
                logger.debug("supabase lock try_acquire failed for %s: %s", key, exc)
                self._available = False
                return False

        return await asyncio.to_thread(_insert)

    async def publish_result(
        self, key: str, value: Any, *, error: Optional[str], ttl_s: float
    ) -> None:
        client = self._client()
        if client is None:
            return
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_s)
        status = "error" if error else "done"
        serialised = _jsonable(value) if error is None else None

        def _update() -> None:
            try:
                client.table(self.TABLE).update({
                    "status": status,
                    "result": serialised,
                    "error": error,
                    "expires_at": expires_at.isoformat(),
                }).eq("key", key).execute()
            except Exception as exc:  # noqa: BLE001
                logger.debug("supabase lock publish failed for %s: %s", key, exc)
                self._available = False

        await asyncio.to_thread(_update)

    async def wait_for_result(
        self, key: str, *, timeout_s: float
    ) -> Optional[LockResult]:
        client = self._client()
        if client is None:
            return None
        deadline = time.time() + timeout_s

        def _poll() -> Optional[dict[str, Any]]:
            try:
                rows = (
                    client.table(self.TABLE)
                    .select("status,result,error,expires_at")
                    .eq("key", key)
                    .limit(1)
                    .execute()
                ).data or []
            except Exception as exc:  # noqa: BLE001
                logger.debug("supabase lock wait error for %s: %s", key, exc)
                return None
            return rows[0] if rows else None

        while time.time() < deadline:
            row = await asyncio.to_thread(_poll)
            if row is None:
                # Either table unavailable or the row vanished — give up.
                return None
            status = row.get("status")
            if status in {"done", "error"}:
                return LockResult(
                    value=row.get("result"),
                    source="awaited",
                    error=row.get("error"),
                )
            await asyncio.sleep(_POLL_INTERVAL_S)
        return None

    async def release(self, key: str) -> None:
        """Only drop the row when it's still ``running`` (i.e. orphan).

        Resolved rows (status in {done, error}) stay in the table for their
        TTL so peer workers can still read the published result. The
        ``purge_api_call_ledger`` SQL function sweeps them on a schedule.
        """
        client = self._client()
        if client is None:
            return

        def _delete() -> None:
            try:
                client.table(self.TABLE).delete().eq("key", key).eq(
                    "status", "running"
                ).execute()
            except Exception as exc:  # noqa: BLE001
                logger.debug("supabase lock release failed for %s: %s", key, exc)

        await asyncio.to_thread(_delete)


def _jsonable(value: Any) -> Any:
    """Best-effort coerce a factory result into JSON-safe shape."""
    try:
        json.dumps(value, default=str)
        return value
    except Exception:  # noqa: BLE001
        return None


# ── Façade ────────────────────────────────────────────────────────────────────


class DistributedLock:
    """Public API — composes backends with graceful fallback.

    Preference order (first available wins):
      1. Redis  — when ``REDIS_URL`` is set
      2. Supabase — when a Supabase client is available
      3. In-memory — always available; single-worker only

    Callers don't see which backend served them — the ``LockResult.source``
    field records whether the result was dispatched locally or awaited from
    a shared store.
    """

    def __init__(self, *, backends: Optional[list[LockBackend]] = None) -> None:
        self._backends: list[LockBackend] = backends or self._default_backends()

    @staticmethod
    def _default_backends() -> list[LockBackend]:
        chain: list[LockBackend] = []
        # Redis first — only adds itself when ``REDIS_URL`` + ``redis`` are present.
        redis_backend = RedisLockBackend()
        if redis_backend.is_available():
            chain.append(redis_backend)
        # Supabase next — requires the ``api_call_ledger`` table migration.
        supabase_backend = SupabaseLockBackend()
        chain.append(supabase_backend)
        # Always include in-memory as a last-resort single-worker fallback.
        chain.append(InMemoryLockBackend())
        return chain

    def _pick(self) -> LockBackend:
        for backend in self._backends:
            if backend.is_available():
                return backend
        # Guaranteed non-empty — InMemoryLockBackend is always available.
        return self._backends[-1]

    async def acquire_or_wait(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        ttl_s: float = DEFAULT_LOCK_TTL_S,
        wait_s: float = DEFAULT_WAIT_TIMEOUT_S,
    ) -> LockResult:
        """Dispatch ``factory`` at most once globally per ``key``.

        Behaviour:
          * If this worker wins the lock, runs ``factory`` and publishes the
            result (or error) for waiters. Returns ``LockResult(source=
            "dispatched")``.
          * If another worker holds the lock, waits up to ``wait_s`` for the
            published result. Returns ``LockResult(source="awaited")``.
          * If no distributed backend is available OR the wait times out,
            falls back to executing ``factory`` locally. Returns
            ``LockResult(source="local")``.

        NEVER raises — factory exceptions are captured in ``error``.
        """
        backend = self._pick()
        try:
            got = await backend.try_acquire(key, ttl_s=ttl_s)
        except Exception as exc:  # noqa: BLE001 — backend must not crash caller
            logger.debug("distributed lock acquire crashed: %s", exc)
            got = False

        if got:
            # Dispatcher path — run the factory and publish the outcome.
            try:
                value = await factory()
                try:
                    await backend.publish_result(
                        key, value, error=None, ttl_s=ttl_s
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("publish_result failed for %s: %s", key, exc)
                return LockResult(value=value, source="dispatched", error=None)
            except BaseException as exc:
                err = f"{type(exc).__name__}: {exc}"[:300]
                try:
                    await backend.publish_result(
                        key, None, error=err, ttl_s=ttl_s
                    )
                except Exception as pub_exc:  # noqa: BLE001
                    logger.debug("error publish failed for %s: %s", key, pub_exc)
                return LockResult(value=None, source="error", error=err)
            finally:
                try:
                    await backend.release(key)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("release failed for %s: %s", key, exc)

        # Waiter path — try to await the dispatcher's result.
        try:
            result = await backend.wait_for_result(key, timeout_s=wait_s)
        except Exception as exc:  # noqa: BLE001
            logger.debug("wait_for_result crashed for %s: %s", key, exc)
            result = None

        if result is not None:
            return result

        # Nothing came back — either the backend is unavailable, the
        # dispatcher crashed, or the wait budget expired. Run locally as
        # the last-resort safety net so the caller still gets a value.
        logger.info(
            "distributed lock fallback to local dispatch key=%s worker=%s",
            key, get_worker_id(),
        )
        try:
            value = await factory()
            return LockResult(value=value, source="local", error=None)
        except BaseException as exc:
            err = f"{type(exc).__name__}: {exc}"[:300]
            return LockResult(value=None, source="error", error=err)


# ── Module-level singleton ─────────────────────────────────────────────────────

_SINGLETON: Optional[DistributedLock] = None


def get_distributed_lock() -> DistributedLock:
    """Return the process-wide distributed lock instance (lazy)."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = DistributedLock()
    return _SINGLETON


def _set_lock_for_testing(lock: Optional[DistributedLock]) -> None:
    """Test hook — swap the module singleton (pass ``None`` to reset)."""
    global _SINGLETON
    _SINGLETON = lock
