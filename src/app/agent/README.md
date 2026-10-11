# LangChain Core assistant

**Spec:** §6.5 LangChain Core: Talking to the LLM and Tools · **Owner:** @participant3
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

_Bonus (§9): one Knowledge Graph query exposed as a tool._

## How `agent.py` works

`agent.py` is a read-only **Planner → Evaluator → (human approval) → Executor** flow built as a
LangGraph `StateGraph`, with `langchain-core` messages/tools and `AzureChatOpenAI` as the chat
model. The LLM never calls a tool directly: it first writes a plan, the plan is checked, a human
approves it, and only then are the approved tools run.

```
question ─► planner ─► evaluator ─┬─ approve ─► approval (interrupt) ─► executor ─► AssistantAnswer
              ▲                   │                 └─ user says no ─► executor (refuses)
              └──── revise ◄──────┤   (max MAX_REVISIONS = 2 revisions)
                                  └─ reject / revisions used up ─► executor (refuses)
```

| Node | What it does |
|---|---|
| `planner` | LLM (structured output `Plan`: approach + at most 4 `PlanStep(tool, args, purpose)`) using only the tools in `TOOLS`. On a revision it also receives the reviewer's feedback. |
| `evaluator` | First `check_plan` (deterministic, no LLM), then `judge_plan` (LLM, `PlanReview`: `approve` / `revise` / `reject`). A deterministic violation goes straight back to the planner as `revise` without calling the judge LLM. On `approve` it also builds `plan_text` (full plan + a plain-language summary for the business user). |
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
   `source_measure`, `limitations`) from `SystemMessage` + `HumanMessage` + that message history. The
   executor prompt says to use only the tool results and never invent numbers.
4. `source_measure` is then **overwritten in code** with the names of the tools that ran, so the
   execution path is traceable and not LLM-written. Tool failures are appended to `limitations`.
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

### Tools in `TOOLS` (the approved allowlist)

| Tool | Status |
|---|---|
| `get_kpi` | **Real.** From `tools/kpi_tools.py`: governed KPI from the Gold table `gold_kpi_equipment_month`, calculated in Databricks. See [`tools/README.md`](tools/README.md). |
| `query_kpi_table` | **Real.** From `tools/detail_tools.py`: an LLM writes one read-only SQL SELECT on the KPI table and its dimensions, a parser guardrail checks it, Databricks runs it (at most 200 rows, up to 3 attempts). |

The planner and judge prompts are generated from `TOOLS` (`TOOL_DOCS`) and from `KPIS` (valid KPI
names with definition and unit), so adding a tool or KPI updates the prompts automatically.

## Running it

```python
from app.agent.agent import run
answer = run("What was availability of equipment EQ-001 in 2026-01?")
```

`run(question, approve=ask_on_console)` starts a graph run with a fresh `thread_id`,
and when the graph pauses at the approval step it calls `approve(plan_text)` (default: a `[y/N]`
prompt in the console) and resumes with the answer. For the API, pass an `approve` callback that
returns the user's decision instead of reading from the console.

The graph is compiled with an in-memory checkpointer (`MemorySaver`), which `interrupt()` needs;
state is lost on restart.

### Configuration

Read at import time, so importing `agent.py` fails with `KeyError` if one is missing. Values come from
the environment or `.env` (loaded with `python-dotenv`):

| Variable | Used for |
|---|---|
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT_NAME`, `OPENAI_API_VERSION` | `AzureChatOpenAI` chat model |
| `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN`, `GOLD_CATALOG`, `GOLD_SCHEMA`, `GOLD_TABLE_PREFIX` | `get_kpi` (see `tools/README.md`) |

| `LLM_TIMEOUT_SECONDS` (default `60`), `LLM_MAX_RETRIES` (default `2`) | optional, timeout of one LLM request and number of retries |

No secrets in code; deployed secrets come from Azure Key Vault.

### Timeouts and retries

Every LLM call goes through one `AzureChatOpenAI` client created with `timeout=LLM_TIMEOUT_SECONDS` and
`max_retries=LLM_MAX_RETRIES`, so one call makes at most 1 + 2 attempts of 60 s each (about 3 minutes plus
backoff in the worst case). The client retries connection errors, timeouts, 429 and 5xx responses; other
errors (e.g. 400) fail immediately. When the call still fails, `run()` logs `run_failed` and re-raises, so
the caller (the API) decides how to answer. A question makes several LLM calls (plan, judge, summary,
answer), so the total time is bounded per call, not per question. `get_kpi` / Databricks has no timeout yet.

## Logging

Structured logs go to `src/app/agent/.log/agent.log` (hidden folder, created on first import, ignored by git
through `*.log*`). One JSON object per line, rotated at 1 MB with 3 backups. Every line has `ts`, `level`,
`event` and a `run_id`, a UUID created per question in `run()`, which is the correlation ID of that
question (it is also the LangGraph `thread_id`).

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
| Message types | `SystemMessage`/`HumanMessage` in `_ask` and `execute`, `AIMessage(tool_calls=...)` and `ToolMessage` in `execute` |
| Typed tools | `get_kpi` (typed `KpiName`, pydantic `KpiRequest`) |
| Validate arguments before execution | `check_plan` and `KpiRequest` |
| Structured output | `AssistantAnswer` via `with_structured_output(..., method="function_calling")` |
| Tool errors / unavailable data | caught in `execute`, reported in `limitations`; `get_kpi` adds limitations for empty or undefined results |
| Out-of-scope requests | judge returns `reject` → nothing is executed, reason returned in `limitations` |
| No consequential actions | read-only tools only, SQL guardrail in `detail_tools.py`, human approval before any tool runs |

## Not implemented yet

- Tests for `check_plan` and the run loop with a mocked LLM
- Use the API request's correlation ID as `run_id` and move the logger to `core/logging.py` (today
  `agent.py` generates its own `run_id` and writes its own file)
- Prompt-injection guarding of the user question
- Azure OpenAI settings in `core/config.py` (`OPENAI_API_VERSION` is missing from the env template)
- Wiring `run()` into `services/assistant_service.py` and `/assistant/query`
- The filtered detail/metadata tool: `get_sql_query` and `get_mock_equipment_status` are
  placeholders, `detail_tools.py` is not connected
- Knowledge Graph bonus tool
- `prompts/system_prompt.py` and `schemas.py` are still docstring-only; the prompts and
  `AssistantAnswer` currently live in `agent.py`

## Note on dependencies

The spec asks for `langchain-core` only. The implementation also uses `langgraph` (graph and
`interrupt`) and `langchain-openai` (`AzureChatOpenAI`). If that is not acceptable for the
deliverable, record the decision in an ADR (`docs/adr/`).
