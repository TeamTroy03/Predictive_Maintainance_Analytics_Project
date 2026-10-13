"""Response cache of the API: Redis when REDIS_URL is set and reachable, otherwise a dict in the process.

Values are JSON strings. A Redis error is logged and treated as a miss, so the cache never breaks a request
(the endpoint just queries Gold again)."""

import logging
import os
import time

logger = logging.getLogger("api")

PREFIX = "pm:cache:"


class TTLCache:
    def __init__(self, redis_client=None, seconds: int = 60):
        self._redis = redis_client  # None = in-process fallback
        self._local: dict[str, tuple[float, str]] = {}  # key -> (expires at, value)
        self.seconds = seconds

    def get(self, key: str) -> str | None:
        if self._redis is None:
            hit = self._local.get(key)
            return hit[1] if hit and hit[0] > time.monotonic() else None
        try:
            return self._redis.get(PREFIX + key)
        except Exception as e:  # BLE001: Redis down, the cache is optional
            logger.warning("cache_read_failed", extra={"fields": {"error": type(e).__name__}})
            return None

    def set(self, key: str, value: str) -> None:
        if self._redis is None:
            self._local[key] = (time.monotonic() + self.seconds, value)
            return
        try:
            self._redis.setex(PREFIX + key, self.seconds, value)
        except Exception as e:  # BLE001
            logger.warning("cache_write_failed", extra={"fields": {"error": type(e).__name__}})


def make_cache(seconds: int = 60) -> TTLCache:
    """Redis-backed when REDIS_URL is set and answers a ping, otherwise in-process."""
    url = os.environ.get("REDIS_URL")
    if not url:
        return TTLCache(seconds=seconds)
    try:
        import redis

        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        client.ping()
        return TTLCache(client, seconds)
    except Exception as e:  # BLE001: no Redis at startup, fall back instead of failing the API
        logger.warning("cache_redis_unavailable", extra={"fields": {"error": type(e).__name__}})
        return TTLCache(seconds=seconds)