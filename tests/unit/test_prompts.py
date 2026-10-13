"""The planner and judge prompts carry today's date, so 'now' can be turned into a YYYY-MM month (no LLM call)."""

from app.agent.prompts.system_prompt import JUDGE_PROMPT, PLANNER_PROMPT


def test_planner_prompt_contains_today():
    messages = PLANNER_PROMPT.format_messages(
        today="2026-10-13", history="", tool_docs="-", kpi_docs="-", question="availability now?", feedback_note=""
    )
    assert "Today's date is 2026-10-13" in messages[0].content


def test_judge_prompt_contains_today():
    messages = JUDGE_PROMPT.format_messages(today="2026-10-13", history="", tool_docs="-", question="q", plan="{}")
    assert "Today's date is 2026-10-13" in messages[0].content


def test_planner_and_judge_prompts_carry_the_history():
    history = "Earlier in this conversation (oldest first):\n1. Question: availability for pumps?\n"
    planner = PLANNER_PROMPT.format_messages(
        today="2026-10-13", history=history, tool_docs="-", kpi_docs="-", question="and compressors?", feedback_note=""
    )
    judge = JUDGE_PROMPT.format_messages(today="2026-10-13", history=history, tool_docs="-", question="q", plan="{}")
    assert "availability for pumps?" in planner[0].content
    assert "availability for pumps?" in judge[0].content
