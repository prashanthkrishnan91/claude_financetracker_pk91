"""Resilient market data facade — single entry point for upstream quotes.

Callers should import ``ResilientMarketProvider`` / ``get_market_snapshot``
from this package rather than reaching into ``price_engine`` or ``io_layer``
directly. The facade guarantees:

  * cached-first lookups with TTLs per data family (prices/news/fundamentals)
  * per-provider circuit breakers + concurrency semaphores
  * a deterministic return schema — even when every upstream fails
"""

from .distributed_lock import (
    DEFAULT_LOCK_TTL_S,
    DEFAULT_WAIT_TIMEOUT_S,
    DistributedLock,
    InMemoryLockBackend,
    LockResult,
    RedisLockBackend,
    SupabaseLockBackend,
    get_distributed_lock,
    get_worker_id,
)
from .request_coalescer import (
    RequestCoalescer,
    get_request_coalescer,
    make_key,
)
from .resilient_provider import (
    ResilientMarketProvider,
    get_market_snapshot,
)
from .system_mode import (
    SystemMode,
    SystemModeManager,
    SystemModeState,
    get_system_mode_manager,
)

__all__ = [
    "DEFAULT_LOCK_TTL_S",
    "DEFAULT_WAIT_TIMEOUT_S",
    "DistributedLock",
    "InMemoryLockBackend",
    "LockResult",
    "RedisLockBackend",
    "RequestCoalescer",
    "ResilientMarketProvider",
    "SupabaseLockBackend",
    "SystemMode",
    "SystemModeManager",
    "SystemModeState",
    "get_distributed_lock",
    "get_market_snapshot",
    "get_request_coalescer",
    "get_system_mode_manager",
    "get_worker_id",
    "make_key",
]
