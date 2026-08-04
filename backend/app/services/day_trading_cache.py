import json
from typing import Any, cast

try:
    from redis import Redis  # pyright: ignore[reportMissingImports]
except ImportError:  # Redis is optional in local Mock-only development.
    Redis = None  # type: ignore[assignment,misc]

from ..config import get_settings


class DayTradingCache:
    """Redis-backed latest-state cache with a safe in-memory fallback for Mock mode."""

    def __init__(self) -> None:
        redis_url = get_settings().redis_url
        self._redis = Redis.from_url(redis_url, decode_responses=True) if redis_url and Redis else None
        self._memory: dict[str, str] = {}

    @property
    def mode(self) -> str:
        return "redis" if self._redis else "memory"

    @property
    def healthy(self) -> bool:
        if not self._redis:
            return False
        try:
            return bool(self._redis.ping())
        except Exception:
            return False

    def put(self, key: str, payload: Any, ttl: int = 120) -> None:
        encoded = json.dumps(payload, ensure_ascii=False)
        self._memory[key] = encoded
        if self._redis:
            try:
                self._redis.setex(f"moneymoney:{key}", ttl, encoded)
            except Exception:
                pass

    def get(self, key: str) -> Any | None:
        encoded = self._memory.get(key)
        if self._redis:
            try:
                cached = self._redis.get(f"moneymoney:{key}")
                if cached:
                    encoded = cast(str, cached)
            except Exception:
                pass
        return json.loads(encoded) if encoded else None

    def publish(self, event_type: str, payload: Any) -> None:
        if not self._redis:
            return
        try:
            self._redis.publish(
                "moneymoney:day-trading",
                json.dumps({"type": event_type, "data": payload}, ensure_ascii=False),
            )
        except Exception:
            pass

    def claim_once(self, key: str, ttl: int = 86_400) -> bool:
        """Atomically claims a notification key; memory remains the safe fallback."""
        memory_key = f"claim:{key}"
        if memory_key in self._memory:
            return False
        if self._redis:
            try:
                claimed = self._redis.set(f"moneymoney:{memory_key}", "1", ex=ttl, nx=True)
                if not claimed:
                    return False
            except Exception:
                pass
        self._memory[memory_key] = "1"
        return True


day_trading_cache = DayTradingCache()
