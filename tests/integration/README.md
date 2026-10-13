# Integration tests

**Spec:** §6.7 (end-to-end API ↔ Gold ↔ LangChain), §6.8, §7

**Acceptance condition:** against a deployed or local environment, tests prove the API reaches the
Gold/semantic layer, the assistant executes a validated tool call end to end, auth rejects missing or
invalid credentials, and `/health` and `/ready` report correctly. Mark tests with
`@pytest.mark.integration`.

## Running

```bash
python -m pytest tests/integration -m integration -v -s
```

Needs the Azure OpenAI and Databricks variables in `.env` (see `.env.example`); otherwise every test is
skipped. It spends LLM quota (about 25 calls, 2-3 minutes) and runs read-only Gold queries only. `-s` prints
the full communication with the LLM for each flow: every planner, judge, summary and answer exchange, then the
agent log events of that run. CI excludes these tests with `-m "not integration"`.

`test_assistant_live.py` runs the real API app (in-process), agent, LLM and Gold, and checks: nothing runs before
the human approves, tools run after approval, the answer matches the tool value, the planner revises a plan the
reviewer sends back, and out-of-scope questions, declined plans and plans that stay invalid are rejected
without running any tool.

`test_graph_neo4j.py` is a contract test for the assistant's `query_graph` tool: the same fixture graph (`tests/unit/graph_fixture.py`)
is synced into a real Neo4j and both backends (Neo4j and in memory) must answer every template identically; the sync must be idempotent.
It needs a **throwaway** Neo4j because the sync replaces everything in its database: set `NEO4J_TEST_URI` (and `NEO4J_TEST_PASSWORD`,
optionally `NEO4J_TEST_USER` / `NEO4J_TEST_DATABASE`); without it every test is skipped, and it refuses a database that already holds
another graph. It needs no Databricks or LLM.

```bash
NEO4J_TEST_URI=bolt://127.0.0.1:7687 NEO4J_TEST_PASSWORD=<test-password> uv run pytest tests/integration/test_graph_neo4j.py -m integration -v
```
