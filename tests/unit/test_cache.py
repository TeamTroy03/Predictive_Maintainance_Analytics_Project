"""Response cache: in-process and Redis backends (a tiny fake client, no Redis needed)."""

from app.api.dashboard import cached
from app.api.schemas import StatusCounts
from app.core.cache import TTLCache


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, seconds, value):
        self.data[key], self.ttl[key] = value, seconds


class BrokenRedis:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError("redis is down")

        return fail


def counts() -> StatusCounts:
    return StatusCounts(operational=1, warning=2, critical=3, offline=4)


def test_in_process_cache_expires():
    cache = TTLCache(seconds=60)
    cache.set("k", "v")
    assert cache.get("k") == "v"
    expired = TTLCache(seconds=-1)
    expired.set("k", "v")
    assert expired.get("k") is None


def test_redis_cache_shares_one_entry_with_a_ttl():
    redis = FakeRedis()
    built = []

    def build():
        built.append(1)
        return counts()

    first = cached(TTLCache(redis, 60), "fleet", StatusCounts, build)
    second = cached(TTLCache(redis, 60), "fleet", StatusCounts, build)  # another process, same Redis
    assert first == second == counts()
    assert len(built) == 1
    assert redis.ttl == {"pm:cache:fleet": 60}


def test_redis_down_means_a_miss_not_an_error():
    cache = TTLCache(BrokenRedis(), 60)
    assert cached(cache, "fleet", StatusCounts, counts) == counts()