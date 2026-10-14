# LangChain Core assistant

**Spec:** §6.5 LangChain Core: Talking to the LLM and Tools
**Deliverable:** LangChain Core integration → `docs/langchain_integration.md`

**Acceptance condition:** a user question can trigger a validated tool call, receive a result
grounded in the Gold or semantic layer and produce an evidence-based response with a traceable
execution path.

## Must implement

- Correct use of `SystemMessage`, `HumanMessage`, `AIMessage`, `ToolMessage`
- Prompt templates: business context, available evidence, response format, limitations
- ≥ 2 typed tools bound to the chat model:
  - KPI/aggregate tool (e.g. MTBF or availability by site/equipment type/period)
  - Filtered detail/metadata tool (e.g. overdue work orders for an equipment ID)
- Detect tool calls → validate arguments → execute approved function → return `ToolMessage`
- Structured output: `answer`, `evidence`, `filters`, `source_or_measure`, `limitations`
- Handle malformed arguments, unavailable data, tool errors and out-of-scope requests

The LLM must not bypass the semantic model, fabricate data or perform consequential actions.

_Bonus (§9): one Knowledge Graph query exposed as a tool: done, `query_graph` (see below and `tools/README.md`)._

## How `agent.py` works

`agent.py` is a read-only **Planner → Evaluator → (human approval) → Executor** flow built as a
LangGraph `StateGraph`, with `langchain-core` messages/tools and `AzureChatOpenAI` as the chat
model. The LLM never calls a tool directly: it first writes a plan, the plan is checked, a human
approves it, and only then are the approved tools run.

| File | Contents |
|---|---|
| `agent.py` | The graph, `check_plan`, `execute`, the number check, logging, and the entry points (`run`, `start_run`, `finish_run`) |
| `prompts/system_prompt.py` | The `ChatPromptTemplate`s (planner, judge, summary, executor, SQL writer) and the formatters that fill `{tool_docs}`, `{kpi_docs}`, `{kpi_formulas}`. The planner and judge prompts also get `{today}` and `{history}` |
| `memory.py` | Conversation memory (Redis or in-process), see [Conversation memory](#conversation-memory-memorypy) |
| `schemas.py` | `Plan`, `PlanStep`, `PlanReview`, `AssistantAnswer` and the graph `State` |
| `tools/` | The approved tools, see [`tools/README.md`](tools/README.md) |

```
question ─► planner ─► evaluator ─┬─ approve ─► approval (interrupt) ─► executor ─► AssistantAnswer
              ▲                   │                 └─ user says no ─► executor (refuses)
              └──── revise ◄──────┤   (max MAX_REVISIONS = 2 revisions)
                                  └─ reject / revisions used up ─► executor (refuses)
```

| Node | What it does |
|---|---|
| `planner` | LLM (structured output `Plan`: approach + at most 4 `PlanStep(tool, args, purpose)`) using only the tools in `TOOLS`. On a revision it also receives the reviewer's feedback. |
| `evaluator` | First `check_plan` (deterministic, no LLM), then `judge_plan` (LLM, `PlanReview`: `approve` / `revise` / `reject`). A deterministic violation goes straight back to the planner as `revise` without calling the judge LLM. On `approve` it also builds `plan_text` (only a plain-language summary for the business user; tool names and arguments stay in `State["plan"]` and the `plan_created` log). |
| `approval` | Calls LangGraph `interrupt(plan_text)` and pauses until the user approves or declines. |
| `executor` | If the plan was not approved nothing is executed and an empty answer with the reason in `limitations` is returned. Otherwise `execute()` runs the steps (see below). |

### Deterministic plan check (`check_plan`)

Run on every plan before the judge LLM sees it. A plan is rejected for:

- a tool that is not in the `TOOLS` allowlist;
- missing or unknown argument names (compared with the tool's input schema);
- for `get_kpi`: anything `KpiRequest` rejects (unknown KPI name, months not `YYYY-MM`, end before
  start, malformed filters), i.e. the same validation the tool itself applies.

### Execution (`execute`)

1. The approved plan is turned into one `AIMessage` with `tool_calls` (ids `step0`, `step1`, ...).
2. Each tool is invoked through the `TOOLS` allowlist; the result becomes a `ToolMessage` with the
   matching `tool_call_id`. A tool exception is not raised: the result is `None` and
   `Tool '<name>' failed: ...` is added to `limitations`.
3. The chat model is asked for the structured `AssistantAnswer` (`answer`, `evidence`, `filters`,
   `source_measure`, `limitations`) through `EXECUTOR_PROMPT` (system message + the question as human
   message + that `AIMessage`/`ToolMessage` history). The executor prompt says to use only the tool
   results and never invent numbers.
4. `source_measure` is then **overwritten in code** with the names of the tools that ran (plus the SQL
   that ran, for `query_kpi_table`), so the execution path is traceable and not LLM-written. Tool
   failures are appended to `limitations`.
5. **Evidence from code**: when a tool result has a `value`, `answer.evidence` is **replaced** by lines built
   in code from the results (`get_kpi: value=0.93123 ratio (0-1), rows=1234, as of ...`), so the exact tool
   figures are always shown and never depend on the LLM copying them.
6. **Number check** (`unverified_numbers`): every number written in `answer` and `evidence` must match a
   numeric value of a tool result (`value`, `row_count`, ...), or that value as a percentage (0.931 → 93.1%),
   rounded or truncated to the digits shown. Dates, times, equipment IDs and years (`2026-04`, `EQ-10001`,
   `2026`) are ignored, so they cannot make an invented number look valid. Counts must match exactly. Numbers
   that match nothing are added to `limitations` ("These numbers were not found in the tool results, please
   verify: ...") and logged. It is a simple heuristic that **warns, it does not block**: a correct number
   attached to the wrong KPI or equipment passes, and legitimate numbers can be flagged (unit conversions
   such as hours → days, numbers taken from the question text).
7. `execute()` also returns a **trace** (one `{tool, args, ok, result}` per step); the executor node stores
   it in the graph state as `trace`, and the API turns it into its `evidence` list.

### Tools in `TOOLS` (the approved allowlist)

| Tool | Status |
|---|---|
| `query_graph` | **Real.** From `tools/graph_tools.py`: relationship questions (failure modes of an asset or type, repeat-failure chains, what fails after what) over the knowledge graph built from Gold (`gold_kg_nodes`, `gold_kg_edges`). Fixed templates, no LLM-written Cypher; Neo4j when `NEO4J_URI` is set, in-memory graph as fallback. See [`tools/README.md`](tools/README.md). |
| `get_kpi` | **Real.** From `tools/kpi_tools.py`: governed KPI from the Gold table `gold_kpi_equipment_month`, calculated in Databricks. See [`tools/README.md`](tools/README.md). |
| `query_kpi_table` | **Real.** From `tools/detail_tools.py`; its only argument is a plain-language `question`. An LLM writes one read-only SQL SELECT on `kpi_table` and the dimensions `dim_equipment`, `dim_equipment_type` and `dim_site`, a sqlglot guardrail checks it, Databricks runs it (at most 200 rows, up to 3 attempts, each failure goes back to the writer). Its result has the `sql` that ran, `rows`, `row_count` and `attempts`. |

The planner and judge prompts are generated from `TOOLS` (`TOOL_DOCS`) and from `KPIS` (valid KPI
names with definition and unit), so adding a tool or KPI updates the prompts automatically.

## Running it

```python
from app.agent.agent import run
answer = run("What was availability of equipment EQ-001 in 2026-01?")
```

`run(question, approve=ask_on_console)` starts a graph run with a fresh `thread_id` and returns the
`AssistantAnswer`. When the graph pauses at the approval step it calls `approve(plan_text)` (default: a
`[y/N]` prompt in the console) and resumes with the answer. There is no `__main__` or example-question
script: call `run()` from your own code or a REPL.

The API cannot block on a console, so it uses the two halves of `run()` directly:
`start_run(question, run_id=None)` runs until the approval step and returns `(run_id, plan_text, state)`
(`plan_text` is `None` if the run already finished, e.g. a rejected plan), and `finish_run(run_id, approved)`
resumes it and returns the final state (`answer` plus `trace`, one `{tool, args, ok, result}` per executed
step). `run_exists(run_id)` and `pending_plan(run_id)` tell an unknown run from one waiting for approval.
The API passes its correlation ID as `run_id` (see `src/app/api/agent.py`).

The graph is compiled with an in-memory checkpointer (`MemorySaver`), which `interrupt()` needs;
state is lost on restart.

### Configuration

The four Azure OpenAI variables are read at import time, so importing `agent.py` fails with `KeyError` if
one is missing (the API therefore imports the agent lazily). Values come from
the environment or `.env` (loaded with `python-dotenv`):

| Variable | Used for |
|---|---|
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT_NAME`, `OPENAI_API_VERSION` | `AzureChatOpenAI` chat model |
| `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN`, `GOLD_CATALOG`, `GOLD_SCHEMA`, `GOLD_TABLE_PREFIX` | `get_kpi` and `query_kpi_table` (read when a tool runs, see `tools/README.md`) |
| `LLM_TIMEOUT_SECONDS` (default `60`), `LLM_MAX_RETRIES` (default `2`) | optional, timeout of one LLM request and number of retries |
| `REDIS_URL`, `MEMORY_TURNS` (default `6`), `MEMORY_TTL_SECONDS` (default `3600`) | optional, conversation memory (see below) |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` (default `neo4j`), `NEO4J_TIMEOUT_SECONDS` (default `5`) | optional, `query_graph` backend; unset = in-memory graph (see `tools/README.md`) |
| `GRAPH_TABLE_PREFIX` (default: `GOLD_TABLE_PREFIX`), `GRAPH_CACHE_SECONDS` (default `3600`), `GRAPH_SYNC_ON_STARTUP` (default `true`) | optional, prefix of the `gold_kg_*` tables, cache time of the in-memory graph, and whether the API copies the Gold graph into Neo4j at startup when Gold has a newer load |

No secrets in code; deployed secrets come from Azure Key Vault.

### Today's date

The planner and judge prompts state today's date (UTC, read on every call by `_today()` in `agent.py`), so
"now", "this month" or "last quarter" become whole `YYYY-MM` months. "Now" means the current month; Gold may not
have data for all of it yet, `get_kpi` reports the as-of date and limitations.

### Conversation memory (`memory.py`)

`POST /assistant/query` accepts an optional `conversation_id` (the frontend sends one per page load). Questions
with the same ID share a memory of the last `MEMORY_TURNS` answered questions, which is added to the planner and
judge prompts so follow-ups such as "and for compressors?" can be resolved.

- Only `{question, answer (max 500 chars), filters, source_measure}` is stored, never tool results. The prompts say
  the history is not evidence, so every number still comes from a tool and the number check is unchanged.
- Declined or rejected questions are not stored.
- Backend: Redis when `REDIS_URL` is set and answers a ping at startup, otherwise a dict in the process (lost on
  restart, the default for tests). A Redis error is logged to `agent.log` and the question is answered without memory.
- The pending plans and the LangGraph checkpointer are still in memory, so the API still needs one worker.

Run Redis locally and point the API at it:

```bash
docker run -d --name pm-redis -p 6379:6379 redis:7-alpine
# .env
REDIS_URL=redis://localhost:6379/0
# look at a conversation
docker exec pm-redis redis-cli lrange pm:memory:<conversation_id> 0 -1
```

### Knowledge graph (`query_graph`)

Relationship questions (failure modes of an asset or type, repeat-failure chains, what fails after what) go to the `query_graph` tool (`tools/graph_tools.py`, all details in [`tools/README.md`](tools/README.md)). The graph is the pair of Gold tables `gold_kg_nodes` / `gold_kg_edges`, already built in Databricks; this code only reads them. The LLM picks one of four fixed templates and never writes Cypher.

- **Backend:** Neo4j when `NEO4J_URI` is set, otherwise (or when Neo4j fails) an in-memory NetworkX graph built from the same two tables. The result says which one answered (`backend`) and adds a limitation when it fell back. Neo4j is not retried for 60 s after a failure.
- **Sync:** when the API starts it copies the tables into Neo4j in a background thread, only if Gold's `gold_loaded_ts` is newer than the copy (`GRAPH_SYNC_ON_STARTUP=false` disables it). Start Neo4j first and wait until it answers, otherwise that one sync fails and Neo4j stays empty until the next restart.
- **Tables:** `<prefix>gold_kg_*`, prefix from `GRAPH_TABLE_PREFIX`, else `GOLD_TABLE_PREFIX` (a `.env` with `GOLD_TABLE_PREFIX=""` and tables named `test_gold_kg_*` needs `GRAPH_TABLE_PREFIX=test_`).

Containers, `.env` and commands (Redis and Neo4j together):

```bash
# Redis: conversation memory (follow-up questions). Neo4j: store of the knowledge graph used by the assistant's query_graph tool.
docker run -d --name pm-redis -p 6379:6379 redis:7-alpine
docker run -d --name pm-neo4j -p 127.0.0.1:7687:7687 -e NEO4J_AUTH=neo4j/<password> neo4j:5
# next days: docker start pm-redis pm-neo4j      stop: docker stop pm-redis pm-neo4j      remove: docker rm -f pm-redis pm-neo4j

# wait until both answer (Neo4j needs 10-20 s after it starts), otherwise the startup graph sync below fails once
docker exec pm-redis redis-cli ping                                          # PONG
docker exec pm-neo4j cypher-shell -u neo4j -p <password> "RETURN 1"          # prints 1
```

Add to `.env` (both services are optional: without them the memory stays in the process and the graph is built in memory):

```
REDIS_URL=redis://localhost:6379/0
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<the password of docker run>
GRAPH_TABLE_PREFIX=test_            # prefix of the gold_kg_* tables if it differs from GOLD_TABLE_PREFIX
```

Commands:

```bash
uv run python -m app.agent.tools.graph_sync --dry-run   # reads gold_kg_nodes / gold_kg_edges and prints the counts, no Neo4j
uv run python -m app.agent.tools.graph_sync             # manual refresh of Neo4j (the API also does it when it starts, if Gold is newer)
uv run pytest tests/unit/test_graph_tools.py            # unit tests of query_graph and the sync (no Neo4j, Databricks or LLM)
docker exec pm-neo4j cypher-shell -u neo4j -p <password> "MATCH (n:Node) RETURN n.node_type, count(*) ORDER BY 1"   # look inside
```

### Timeouts and retries

Every LLM call goes through one `AzureChatOpenAI` client created with `timeout=LLM_TIMEOUT_SECONDS` and
`max_retries=LLM_MAX_RETRIES`, so one call makes at most 1 + 2 attempts of 60 s each (about 3 minutes plus
backoff in the worst case). The client retries connection errors, timeouts, 429 and 5xx responses; other
errors (e.g. 400) fail immediately. When the call still fails, `start_run`/`finish_run` log `run_failed` and
re-raise, so the caller (the API) decides how to answer. A question makes several LLM calls (plan, judge,
summary, answer, plus the SQL writer and more plans on revisions), so the total time is bounded per call,
not per question. `get_kpi` and `query_kpi_table` have no Databricks timeout yet.

## Logging

Structured logs go to `src/app/agent/.log/agent.log` (hidden folder, created on first import, ignored by git
through `*.log*`). One JSON object per line, rotated at 1 MB with 3 backups. Every line has `ts`, `level`,
`event` and a `run_id`, the correlation ID of that question (it is also the LangGraph `thread_id`). It is a
fresh UUID, or the ID the caller passes to `start_run` (the API passes its `X-Correlation-ID`).

| Event | Fields besides `run_id` |
|---|---|
| `run_started` | `question` |
| `plan_created` | `revision`, `approach`, `steps`, `duration_s` |
| `plan_reviewed` | `verdict`, `reasons`, `by` (`check_plan` or `judge`), `revisions`, `duration_s` |
| `plan_decision` | `approved` |
| `not_executed` | `verdict`, `reasons` (rejected, declined or revisions used up) |
| `tool_call` | `tool`, `args`, `ok`, `error` (on failure, level WARNING), `duration_s` |
| `answer_ready` | `unverified_numbers`, `limitations` (count), `duration_s` |
| `run_finished` | `verdict`, `duration_s` |
| `run_failed` | `error`, `exception` (traceback), `duration_s`, level ERROR |

Questions, plans and tool arguments are logged, tool results and answers are not, and no secrets or
environment values are written. Follow one question with e.g.
`grep <run_id> src/app/agent/.log/agent.log`.

## How the requirements are covered

| Requirement | Where |
|---|---|
| Message types | System/Human messages come from the `ChatPromptTemplate`s in `prompts/system_prompt.py` (used by `_ask`, `plan_text`, the SQL writer); `AIMessage(tool_calls=...)` and `ToolMessage` are built in `execute` |
| Prompt templates | `prompts/system_prompt.py` (business context, tool/KPI evidence, response format via structured output, limitations) |
| Typed tools | `get_kpi` (typed `KpiName`, pydantic `KpiRequest`), `query_graph` (typed `TemplateName`, pydantic `GraphRequest`) and `query_kpi_table` (typed `question: str`, guarded SQL) |
| Tools bound to the chat model | **Not used.** The planner chooses tools through the structured `Plan` and `execute` invokes them from the allowlist; `bind_tools` is never called. This differs from the spec wording, so decide whether to record it in an ADR |
| Validate arguments before execution | `check_plan`, `KpiRequest` and `GraphRequest` |
| Structured output | `AssistantAnswer` via `with_structured_output(..., method="function_calling")` |
| Tool errors / unavailable data | caught in `execute`, reported in `limitations`; `get_kpi` adds limitations for empty or undefined results |
| Out-of-scope requests | judge returns `reject` → nothing is executed, reason returned in `limitations` |
| No consequential actions | read-only tools only, SQL guardrail in `detail_tools.py`, human approval before any tool runs |

## Tests

| File | What it covers |
|---|---|
| `tools/.tests/test_kpi_tools.py` | `get_kpi` against the real Gold tables (marked `integration`) |
| `tests/unit/test_detail_tools.py` | SQL guardrail (`check_sql`, `limit_rows`) and the atomic tools |
| `tests/unit/test_assistant.py` | The API's assistant endpoints with a **fake** agent (no LLM), including `conversation_id` |
| `tests/unit/test_memory.py` | Conversation memory: in-process and Redis backends (fake client), cap, truncation, Redis failures |
| `tests/unit/test_graph_tools.py`, `tests/integration/test_graph_neo4j.py` | `query_graph`: templates, validation, Neo4j / memory backends and fallback, sync; see `tools/README.md` |
| `tests/unit/test_prompts.py` | The planner and judge prompts contain today's date and the conversation history |
| `tests/integration/test_assistant_live.py` | API → agent → Azure OpenAI → Gold: approval, plan revision, rejection, full LLM transcript with `-s` |

## Not implemented yet

- Unit tests for `check_plan` and the graph with a mocked LLM (only the live integration test exercises them)
- Move the logger to `core/logging.py` (the API now passes its correlation ID as `run_id`, but `agent.py`
  still writes its own `.log/agent.log` file)
- Prompt-injection guarding of the user question
- Azure OpenAI settings in `core/config.py`
- Persistent checkpointer: the API's two-step approval (`start_run` / `finish_run`) keeps pending plans in
  memory (`MemorySaver`), so it needs a single worker and a restart loses them
- Knowledge graph: no periodic refresh (Neo4j is synced when the API starts, a Gold run while it stays up needs a restart or a manual sync) and only four templates (no asset similarity or multi-hop paths yet)

## Note on dependencies

The spec asks for `langchain-core` only. The implementation also uses `langgraph` (graph and
`interrupt`) and `langchain-openai` (`AzureChatOpenAI`). If that is not acceptable for the
deliverable, record the decision in an ADR (`docs/adr/`).
