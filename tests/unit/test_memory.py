"""Conversation memory: in-process and Redis backends (a tiny fake client, no Redis needed)."""

import json

from app.agent.memory import ConversationMemory, format_history

TURN = {"question": "availability for pumps?", "answer": "93.1 %", "filters": {"equipment_type": "Pump"}}


class FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.ttl: dict[str, int] = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists[key][start:] if start < 0 else self.lists[key][start:end + 1]

    def expire(self, key, seconds):
        self.ttl[key] = seconds

    def lrange(self, key, start, end):
        return list(self.lists.get(key, []))


class BrokenRedis:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError("redis is down")

        return fail


def test_in_process_memory_keeps_the_last_turns_per_conversation():
    memory = ConversationMemory(max_turns=2)
    for i in range(3):
        memory.add("c1", {**TURN, "question": f"q{i}"})
    memory.add("c2", TURN)
    assert [t["question"] for t in memory.history("c1")] == ["q1", "q2"]
    assert len(memory.history("c2")) == 1
    assert memory.history("unknown") == []


def test_no_conversation_id_means_no_memory():
    memory = ConversationMemory()
    memory.add(None, TURN)
    assert memory.history(None) == []


def test_long_answers_are_truncated():
    memory = ConversationMemory()
    memory.add("c1", {**TURN, "answer": "x" * 5000})
    assert len(memory.history("c1")[0]["answer"]) == 500


def test_redis_backend_stores_json_trims_and_expires():
    client = FakeRedis()
    memory = ConversationMemory(client, max_turns=2, ttl_seconds=60)
    for i in range(3):
        memory.add("c1", {**TURN, "question": f"q{i}"})
    assert [json.loads(t)["question"] for t in client.lists["pm:memory:c1"]] == ["q1", "q2"]
    assert client.ttl["pm:memory:c1"] == 60
    assert [t["question"] for t in memory.history("c1")] == ["q1", "q2"]


def test_a_broken_redis_never_raises():
    memory = ConversationMemory(BrokenRedis())
    memory.add("c1", TURN)
    assert memory.history("c1") == []


def test_format_history():
    assert format_history([]) == ""
    text = format_history([TURN])
    assert "availability for pumps?" in text and "93.1 %" in text and "Pump" in text
