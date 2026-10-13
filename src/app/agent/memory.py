"""Conversation memory: the last few question/answer turns of one conversation, so the planner can resolve
follow-ups ("and for pumps?"). Stored in Redis when REDIS_URL is set, otherwise in this process (lost on restart).

Only compact turns are kept (question, answer, filters, source_measure), never tool results: the history helps
to understand a question, it is not evidence. Memory never breaks answering: a Redis error is logged and the
conversation simply continues without it."""

import json
import logging
import os
from collections import defaultdict

logger = logging.getLogger("app.agent")  # same log file as agent.py (configured there)

MAX_TURNS = int(os.getenv("MEMORY_TURNS", "6"))
TTL_SECONDS = int(os.getenv("MEMORY_TTL_SECONDS", "3600"))
MAX_ANSWER_CHARS = 500


class ConversationMemory:
    def __init__(self, redis_client=None, max_turns: int = MAX_TURNS, ttl_seconds: int = TTL_SECONDS):
        self._redis = redis_client  # None = in-process fallback
        self._local: dict[str, list[dict]] = defaultdict(list)
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(conversation_id: str) -> str:
        return f"pm:memory:{conversation_id}"

    def history(self, conversation_id: str | None) -> list[dict]:
        if not conversation_id:
            return []
        if self._redis is None:
            return list(self._local.get(conversation_id, []))
        try:
            return [json.loads(t) for t in self._redis.lrange(self._key(conversation_id), 0, -1)]
        except Exception as e:  # BLE001: Redis down or bad data, memory is optional
            logger.warning("memory_read_failed", extra={"fields": {"error": type(e).__name__}})
            return []

    def add(self, conversation_id: str | None, turn: dict) -> None:
        if not conversation_id:
            return
        turn = {**turn, "answer": str(turn.get("answer", ""))[:MAX_ANSWER_CHARS]}
        if self._redis is None:
            self._local[conversation_id] = (self._local[conversation_id] + [turn])[-self.max_turns:]
            return
        try:
            key = self._key(conversation_id)
            self._redis.rpush(key, json.dumps(turn, default=str))
            self._redis.ltrim(key, -self.max_turns, -1)
            self._redis.expire(key, self.ttl_seconds)
        except Exception as e:  # BLE001
            logger.warning("memory_write_failed", extra={"fields": {"error": type(e).__name__}})


def format_history(turns: list[dict]) -> str:
    """Text for the {history} prompt variable; empty when there is nothing to remember."""
    if not turns:
        return ""
    lines = []
    for i, t in enumerate(turns, 1):
        lines.append(f"{i}. Question: {t.get('question', '')}\n   Answer: {t.get('answer', '')}\n"
                     f"   Filters: {json.dumps(t.get('filters', {}), default=str)}")
    return "Earlier in this conversation (oldest first):\n" + "\n".join(lines) + "\n"


def make_memory() -> ConversationMemory:
    """Redis-backed when REDIS_URL is set and reachable, otherwise in-process."""
    url = os.environ.get("REDIS_URL")
    if not url:
        return ConversationMemory()
    try:
        import redis

        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        client.ping()
        return ConversationMemory(client)
    except Exception as e:  # BLE001: no Redis at startup, fall back instead of failing the API
        logger.warning("memory_redis_unavailable", extra={"fields": {"error": type(e).__name__}})
        return ConversationMemory()
