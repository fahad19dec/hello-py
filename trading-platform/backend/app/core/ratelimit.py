"""Fixed-window rate limiting, shared across API instances through Redis.

If Redis is unreachable the limiter keeps working from an in-process counter
instead of either failing every request or silently dropping all limits. The
fallback is per instance, so limits are looser while Redis is down; the
outage is logged.
"""
import logging
import threading
import time
from functools import lru_cache
from typing import Protocol

import redis

from .config import get_settings
from .errors import AppError

log = logging.getLogger("app.ratelimit")


class Limiter(Protocol):
    def hit(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Count one event. Returns (events in the current window, seconds until it resets)."""

    def peek(self, key: str) -> tuple[int, int]:
        """Current (count, seconds until reset) without counting an event."""

    def reset(self, key: str) -> None: ...


class MemoryLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, float]] = {}  # key -> (count, expires_at)

    def _live(self, key: str, now: float) -> tuple[int, float] | None:
        entry = self._windows.get(key)
        if entry and entry[1] > now:
            return entry
        self._windows.pop(key, None)
        return None

    def hit(self, key: str, window_seconds: int) -> tuple[int, int]:
        now = time.monotonic()
        with self._lock:
            entry = self._live(key, now)
            count, expires = (entry[0] + 1, entry[1]) if entry else (1, now + window_seconds)
            self._windows[key] = (count, expires)
            return count, max(1, round(expires - now))

    def peek(self, key: str) -> tuple[int, int]:
        now = time.monotonic()
        with self._lock:
            entry = self._live(key, now)
            return (entry[0], max(1, round(entry[1] - now))) if entry else (0, 0)

    def reset(self, key: str) -> None:
        with self._lock:
            self._windows.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()


_HIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then redis.call('EXPIRE', KEYS[1], ARGV[1]); ttl = tonumber(ARGV[1]) end
return {count, ttl}
"""


class RedisLimiter:
    def __init__(self, url: str, prefix: str = "rl:") -> None:
        # Short timeouts: a slow Redis must not make sign-in slow.
        self._redis = redis.Redis.from_url(url, socket_connect_timeout=0.3, socket_timeout=0.3)
        self._script = self._redis.register_script(_HIT_SCRIPT)
        self._prefix = prefix

    def hit(self, key: str, window_seconds: int) -> tuple[int, int]:
        count, ttl = self._script(keys=[self._prefix + key], args=[window_seconds])
        return int(count), int(ttl)

    def peek(self, key: str) -> tuple[int, int]:
        pipe = self._redis.pipeline()
        pipe.get(self._prefix + key)
        pipe.ttl(self._prefix + key)
        value, ttl = pipe.execute()
        return (int(value), max(int(ttl), 1)) if value is not None else (0, 0)

    def reset(self, key: str) -> None:
        self._redis.delete(self._prefix + key)


class ResilientLimiter:
    """Uses the primary limiter, falling back to a local one while it is down."""

    RETRY_AFTER_SECONDS = 5.0

    def __init__(self, primary: Limiter, fallback: Limiter) -> None:
        self._primary = primary
        self._fallback = fallback
        self._down_until = 0.0

    def _call(self, name: str, *args):
        if time.monotonic() >= self._down_until:
            try:
                return getattr(self._primary, name)(*args)
            except redis.RedisError as exc:
                self._down_until = time.monotonic() + self.RETRY_AFTER_SECONDS
                log.error("rate limiter: Redis unavailable (%s); using per-instance limits", type(exc).__name__)
        return getattr(self._fallback, name)(*args)

    def hit(self, key: str, window_seconds: int) -> tuple[int, int]:
        return self._call("hit", key, window_seconds)

    def peek(self, key: str) -> tuple[int, int]:
        return self._call("peek", key)

    def reset(self, key: str) -> None:
        return self._call("reset", key)


@lru_cache
def get_limiter() -> Limiter:
    settings = get_settings()
    if settings.rate_limit_backend == "memory":
        return MemoryLimiter()
    return ResilientLimiter(RedisLimiter(settings.redis_url), MemoryLimiter())


def too_many(retry_after: int) -> AppError:
    minutes = max(1, round(retry_after / 60))
    wait = f"{retry_after} seconds" if retry_after < 90 else f"{minutes} minutes"
    return AppError(
        429,
        "too_many_requests",
        f"Too many attempts. Try again in about {wait}.",
        headers={"Retry-After": str(max(1, retry_after))},
    )


def enforce(key: str, *, limit: int, window_seconds: int) -> None:
    """Count one event against `key` and raise 429 once the limit is exceeded."""
    count, retry_after = get_limiter().hit(key, window_seconds)
    if count > limit:
        raise too_many(retry_after)
