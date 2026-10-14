"""The planner and judge prompts carry today's date and where the data ends, so 'now' can be turned into a YYYY-MM month
that has data (no LLM call)."""

from app.agent.prompts.system_prompt import JUDGE_PROMPT, PLANNER_PROMPT

DATA_NOTE = "The Gold data ends on 2026-09-21; the last month with data is 2026-09 (it may be only partly filled). "


def test_planner_prompt_contains_today():
    messages = PLANNER_PROMPT.format_messages(
        today="2026-10-13", data_note="", history="", tool_docs="-", kpi_docs="-", question="availability now?",
        feedback_note=""
    )
    assert "Today's date is 2026-10-13" in messages[0].content


def test_judge_prompt_contains_today():
    messages = JUDGE_PROMPT.format_messages(
        today="2026-10-13", data_note="", history="", tool_docs="-", question="q", plan="{}")
    assert "Today's date is 2026-10-13" in messages[0].content


def test_planner_and_judge_prompts_carry_where_the_data_ends():
    planner = PLANNER_PROMPT.format_messages(
        today="2026-10-13", data_note=DATA_NOTE, history="", tool_docs="-", kpi_docs="-", question="availability now?",
        feedback_note=""
    )
    judge = JUDGE_PROMPT.format_messages(
        today="2026-10-13", data_note=DATA_NOTE, history="", tool_docs="-", question="q", plan="{}")
    assert DATA_NOTE in planner[0].content and DATA_NOTE in judge[0].content
    assert "end at the last month with data" in planner[0].content


def test_prompts_still_render_without_a_data_date():
    planner = PLANNER_PROMPT.format_messages(
        today="2026-10-13", data_note="", history="", tool_docs="-", kpi_docs="-", question="q", feedback_note=""
    )
    assert "The Gold data ends" not in planner[0].content


def test_planner_and_judge_prompts_carry_the_history():
    history = "Earlier in this conversation (oldest first):\n1. Question: availability for pumps?\n"
    planner = PLANNER_PROMPT.format_messages(
        today="2026-10-13", data_note="", history=history, tool_docs="-", kpi_docs="-", question="and compressors?",
        feedback_note=""
    )
    judge = JUDGE_PROMPT.format_messages(
        today="2026-10-13", data_note="", history=history, tool_docs="-", question="q", plan="{}")
    assert "availability for pumps?" in planner[0].content
    assert "availability for pumps?" in judge[0].content
