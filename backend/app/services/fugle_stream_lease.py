"""One Fugle stream owner per API credential, including rolling deployments."""
from __future__ import annotations

import asyncio
from hashlib import sha256
from threading import RLock
import time
from typing import Callable
from uuid import uuid4

from ..config import get_settings


_LOCAL_LOCK = RLock()
_LOCAL_LEASES: dict[str, tuple[str, float]] = {}
_RENEW = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('EXPIRE', KEYS[1], ARGV[2]) else return 0 end"
_RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"


class FugleStreamLease:
    def __init__(self, api_key: str, *, redis_client=None, ttl_seconds: int = 15,
                 allow_local: bool | None = None, monotonic: Callable = time.monotonic) -> None:
        settings = get_settings()
        self.key = f"moneymoney:fugle-stream-owner:{sha256(api_key.encode()).hexdigest()[:24]}"
        self.owner = str(uuid4())
        self.ttl_seconds = max(5, int(ttl_seconds))
        self._now = monotonic
        self._allow_local = (settings.runtime_mode == "local" and settings.app_env == "development") if allow_local is None else allow_local
        self._redis = redis_client
        if self._redis is None and settings.redis_url:
            from redis import Redis
            self._redis = Redis.from_url(settings.redis_url, decode_responses=True,
                                         socket_timeout=2, socket_connect_timeout=2)
        self.last_error: str | None = None

    async def acquire(self) -> bool:
        return await asyncio.to_thread(self._acquire)

    async def renew(self) -> bool:
        return await asyncio.to_thread(self._renew)

    async def release(self) -> None:
        await asyncio.to_thread(self._release)

    def _acquire(self) -> bool:
        if self._redis is not None:
            try:
                claimed = self._redis.set(self.key, self.owner, nx=True, ex=self.ttl_seconds)
                self.last_error = None
                return bool(claimed)
            except Exception as exc:
                self.last_error = type(exc).__name__
                return False
        if not self._allow_local:
            self.last_error = "SharedLeaseUnavailable"
            return False
        with _LOCAL_LOCK:
            current = _LOCAL_LEASES.get(self.key)
            if current and current[1] > self._now():
                return False
            _LOCAL_LEASES[self.key] = (self.owner, self._now() + self.ttl_seconds)
            return True

    def _renew(self) -> bool:
        if self._redis is not None:
            try:
                renewed = self._redis.eval(_RENEW, 1, self.key, self.owner, self.ttl_seconds)
                self.last_error = None
                return bool(renewed)
            except Exception as exc:
                self.last_error = type(exc).__name__
                return False
        if not self._allow_local:
            return False
        with _LOCAL_LOCK:
            current = _LOCAL_LEASES.get(self.key)
            if not current or current[0] != self.owner or current[1] <= self._now():
                return False
            _LOCAL_LEASES[self.key] = (self.owner, self._now() + self.ttl_seconds)
            return True

    def _release(self) -> None:
        if self._redis is not None:
            try:
                self._redis.eval(_RELEASE, 1, self.key, self.owner)
            except Exception as exc:
                self.last_error = type(exc).__name__
            return
        with _LOCAL_LOCK:
            current = _LOCAL_LEASES.get(self.key)
            if current and current[0] == self.owner:
                _LOCAL_LEASES.pop(self.key, None)
