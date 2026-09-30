# 07 — LangChain Core integration

**Spec:** §7 Deliverable "LangChain Core integration"; §6.5 · **Owner:** @participant3

**Required evidence:** prompts, structured output, at least two bound tools, tool-result handling
and grounded-response examples.

## Prompt templates
_TODO_

## Tools
| Tool | Type | Arguments (typed) | Data source |
|---|---|---|---|
| | KPI / aggregate | | |
| | Filtered detail / metadata | | |

## Tool-call execution path
_TODO: HumanMessage → AIMessage(tool_calls) → validation → execution → ToolMessage → final AIMessage._

## Structured output schema
`answer`, `evidence`, `filters`, `source_or_measure`, `limitations`

## Failure handling
| Case | Behavior |
|---|---|
| Malformed arguments | |
| Unavailable data | |
| Tool error | |
| Out-of-scope request | |

## Grounded-response examples
_TODO: at least two full traces._
