# LangChain Core assistant

**Spec:** §6.5 LangChain Core: Talking to the LLM and Tools · **Owner:** @participant3
**Deliverable:** LangChain Core integration → `docs/07-langchain-integration.md`

**Acceptance condition:** a user question can trigger a validated tool call, receive a result
grounded in the Gold or semantic layer and produce an evidence-based response with a traceable
execution path.

## Must implement (`langchain-core` only)

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
