"""One conservative REST budget per Fugle credential, shared across workers."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import hashlib
import math
from threading import RLock
import time
from typing import Mapping
from uuid import uuid4


ACQUIRE_SCRIPT = """
local now = tonumber(ARGV[1])
local configured = tonumber(ARGV[2])
local ratio = tonumber(ARGV[3])
local actual = tonumber(redis.call('HGET', KEYS[2], 'limit') or '0')
local limit = math.max(1, math.floor(math.min(configured, actual > 0 and actual or 60) * .9))
local ceiling = math.max(1, math.floor(limit * ratio))
local blocked = tonumber(redis.call('HGET', KEYS[2], 'blocked') or '0')
local nextAt = tonumber(redis.call('HGET', KEYS[2], 'next') or '0')
if blocked > now then return {0, math.ceil(blocked-now), actual, limit} end
if nextAt > now then return {0, math.ceil(nextAt-now), actual, limit} end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now-60000)
local count = redis.call('ZCARD', KEYS[1])
if count >= ceiling then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, math.max(1, math.ceil(tonumber(oldest[2])+60000-now)), actual, limit}
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], 120000)
redis.call('HSET', KEYS[2], 'next', now+60000/limit)
redis.call('PEXPIRE', KEYS[2], 86400000)
return {1, 0, actual, limit}
"""

OBSERVE_SCRIPT = """
if tonumber(ARGV[1]) > 0 then redis.call('HSET', KEYS[1], 'limit', ARGV[1]) end
local prior = tonumber(redis.call('HGET', KEYS[1], 'blocked') or '0')
if tonumber(ARGV[2]) > prior then redis.call('HSET', KEYS[1], 'blocked', ARGV[2]) end
redis.call('PEXPIRE', KEYS[1], 86400000)
return 1
"""


class FugleBudgetUnavailable(RuntimeError):
    pass


class FugleRequestBudget:
    def __init__(self, api_key: str, *, redis_client=None, configured_limit: int = 600,
                 clock=time.time, sleep=asyncio.sleep) -> None:
        digest = hashlib.sha256(api_key.strip().encode()).hexdigest()
        self._key = f"fugle:rest-budget:{{{digest}}}"
        self._redis = redis_client
        self._configured = max(1, configured_limit)
        self._clock, self._sleep = clock, sleep
        self._lock = RLock()
        self._calls: deque[float] = deque()
        self._actual: int | None = None
        self._remaining: int | None = None
        self._blocked_until = 0.0
        self._next = 0.0
        self._storage = "redis" if redis_client is not None else "local"

    def diagnostics(self) -> dict:
        with self._lock:
            return {"limitPerMinute": self._actual, "configuredLimit": self._configured,
                    "effectivePerMinute": max(1, math.floor(min(self._configured, self._actual or 60) * .9)),
                    "remaining": self._remaining,
                    "blockedUntil": datetime.fromtimestamp(self._blocked_until, UTC).isoformat() if self._blocked_until > self._clock() else None,
                    "storage": self._storage, "entitlementKnown": self._actual is not None}

    def _try_local(self, ratio: float) -> float:
        with self._lock:
            now = self._clock()
            if self._blocked_until > now:
                return self._blocked_until - now
            if self._next > now:
                return self._next - now
            while self._calls and self._calls[0] <= now - 60:
                self._calls.popleft()
            effective = self.diagnostics()["effectivePerMinute"]
            if len(self._calls) >= max(1, math.floor(effective * ratio)):
                return max(.001, self._calls[0] + 60 - now)
            self._calls.append(now)
            self._next = now + 60 / effective
            return 0

    async def acquire(self, *, priority: str = "normal") -> None:
        # Low-priority metadata cannot consume capacity reserved for live quotes.
        ratio = 1.0 if priority == "priority" else .8 if priority == "normal" else .5
        while True:
            if self._redis is None:
                delay = self._try_local(ratio)
            else:
                try:
                    result = await asyncio.to_thread(self._redis.eval, ACQUIRE_SCRIPT, 2,
                        self._key + ":requests", self._key + ":state",
                        self._clock() * 1000, self._configured, ratio, uuid4().hex)
                    admitted, delay_ms, actual, _effective = map(int, result)
                    with self._lock:
                        self._storage = "redis"
                        if actual:
                            self._actual = actual
                    delay = 0 if admitted else max(.001, delay_ms / 1000)
                except Exception as exc:
                    with self._lock:
                        self._storage = "redis-unavailable"
                    raise FugleBudgetUnavailable("Shared Fugle quota storage unavailable") from exc
            if delay <= 0:
                return
            # Cancellable waits; a server cooldown applies to every consumer.
            await self._sleep(min(delay, 30))

    async def observe_response(self, status_code: int, headers: Mapping[str, str]) -> None:
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        def number(name):
            try:
                return max(0, int(float(normalized[name])))
            except (KeyError, TypeError, ValueError):
                return None
        actual = number("x-ratelimit-limit")
        remaining = number("x-ratelimit-remaining")
        now = self._clock()
        until = 0.0
        if status_code == 429 or remaining == 0:
            retry = normalized.get("retry-after", "")
            try:
                seconds = max(0.0, float(retry))
            except ValueError:
                try:
                    seconds = max(0.0, parsedate_to_datetime(retry).timestamp() - now)
                except (TypeError, ValueError, OverflowError):
                    reset = number("x-ratelimit-reset")
                    seconds = max(1, reset - now) if reset and reset > now else 60
            until = now + max(1, seconds)
        with self._lock:
            if actual:
                self._actual = actual
            self._remaining = remaining
            self._blocked_until = max(self._blocked_until, until)
        if self._redis is not None:
            try:
                await asyncio.to_thread(self._redis.eval, OBSERVE_SCRIPT, 1,
                                       self._key + ":state", actual or 0, until * 1000)
            except Exception as exc:
                with self._lock:
                    self._storage = "redis-unavailable"
                raise FugleBudgetUnavailable("Shared Fugle quota update unavailable") from exc


_budgets: dict[str, FugleRequestBudget] = {}
_budgets_lock = RLock()


class _MissingSharedStorage:
    def eval(self, *args):
        raise FugleBudgetUnavailable("Production Fugle requests require shared Redis quota storage")


def get_fugle_request_budget(api_key: str) -> FugleRequestBudget:
    from ..config import get_settings
    identity = hashlib.sha256(api_key.strip().encode()).hexdigest()
    with _budgets_lock:
        if identity not in _budgets:
            settings = get_settings()
            redis_client = None
            if settings.redis_url:
                from redis import Redis
                redis_client = Redis.from_url(settings.redis_url, decode_responses=True,
                                               socket_connect_timeout=2, socket_timeout=2)
            elif settings.runtime_mode != "local" or settings.app_env.lower() not in {"development", "dev", "local", "test", "testing"}:
                redis_client = _MissingSharedStorage()
            _budgets[identity] = FugleRequestBudget(api_key, redis_client=redis_client,
                configured_limit=getattr(settings, "fugle_rest_requests_per_minute", 600))
        return _budgets[identity]
