"""Chat prompt templates of the agent: answer only from approved tools over Gold / the semantic model; never fabricate
data or take actions. Each template is a system + human message pair; agent.py fills the {variables} and pipes the
template into the LLM, so no message list is built by hand there."""

import json

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# ---------- Formatters: the text that fills the {tool_docs}, {kpi_docs} and {kpi_formulas} variables ----------


def format_tool_docs(tools: dict) -> str:
    """One line per approved tool: name, description and argument schema."""
    return "\n".join(
        f"- {t.name}: {' '.join(t.description.split())} Arguments: {json.dumps(t.args)}" for t in tools.values()
    )


def format_kpi_docs(kpis: dict) -> str:
    """One line per KPI: name, meaning and unit."""
    return "\n".join(f"  {name}: {k['definition']} ({k['unit']})" for name, k in kpis.items())


def format_kpi_formulas(kpis: dict) -> str:
    """One line per KPI: name and SQL formula."""
    return "\n".join(f"  {name}: {k['formula']}" for name, k in kpis.items())


# ---------- Templates ----------

# vars: tool_docs, kpi_docs, question, feedback_note ("" or the reviewer's feedback)
PLANNER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "You plan read-only analytics for an industrial predictive-maintenance assistant. "
        "Return a plan of at most 4 steps using ONLY these tools:\n"
        "{tool_docs}\n"
        "Valid kpi_name values with their meaning:\n"
        "{kpi_docs}\n"
        "KPI periods are whole months written YYYY-MM (start_month and end_month, both inclusive). "
        "Use the most relevant tool(s) for the question and do not add steps it does not need. "
        "Use the exact argument names above. Do not execute anything and do not invent data or arguments.")),
    ("human", "{question}{feedback_note}"),
])

# vars: tool_docs, question, plan (JSON)
JUDGE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "You review a plan for a read-only maintenance assistant. These are the only approved tools "
        "and what they do:\n{tool_docs}\n"
        "Approve only if the plan answers the question using these tools (a tool is suitable if its "
        "description covers what the question asks), stays read-only and invents no data. Otherwise "
        "return 'revise' with feedback_for_planner, or 'reject' if the question cannot be answered.")),
    ("human", "Question: {question}\n\nPlan: {plan}"),
])

# vars: question, plan (full plan text)
SUMMARY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "Summarize this analysis plan for a business user in 2-3 sentences. Describe only what "
        "will be looked up and why, in business terms. No tool names, parameters or technical terms.")),
    ("human", "Question: {question}\n\nPlan:\n{plan}"),
])

# vars: question, tool_messages (the AIMessage with the tool calls + one ToolMessage per result)
EXECUTOR_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "Answer the question using ONLY the tool results provided. Never invent numbers. "
        "Fill answer, evidence, filters, source_measure and limitations. "
        "Mention missing or failed data in limitations.")),
    ("human", "{question}"),
    MessagesPlaceholder("tool_messages"),
])

# vars: table, kpi_formulas, question, schema, attempts ("" or the failed tries so far)
WRITER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "You write ONE Databricks SQL SELECT on the table {table}, joined to the dimension tables when names are needed, "
        "that answers the user's question. Return only the SQL, no explanation. Read-only; only the tables described below. "
        "Rows whose equipment_key, site_key or equipment_type_key is -1 are unknown members: always exclude them. "
        "Month columns are the first day of the month as an integer yyyyMMdd (April 2026 = 20260401). "
        "When a KPI is asked for, compute it with these formulas over the summed columns, never by averaging ratios:\n"
        "{kpi_formulas}")),
    ("human", "Question: {question}\n\n{schema}{attempts}"),
])