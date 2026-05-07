from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from crewai import Agent, Crew, Task

from agents.llm import build_groq_llm
from core.git_store import GitStore
from core.markdown_store import (
    daily_file_path,
    monthly_file_path,
    now_iso,
    weekly_file_path,
    write_markdown,
)
from core.time_utils import ist_today


@dataclass
class SummaryResult:
    summary_path: str
    period: str
    commit: str


def _summary_agent() -> Agent:
    return Agent(
        role="Life Summary Analyst",
        goal="Synthesize and summarize daily journals into cohesive, highly structured weekly and monthly insights, extracting actionable metrics and identifying overarching trends.",
        backstory=(
            "You are an expert life coach and behavioral analyst. Your strength lies in detecting "
            "subtle patterns across work, social life, health, and personal focus from scattered journal entries. "
            "You are objective, encouraging, and data-driven."
        ),
        llm=build_groq_llm(),
        verbose=False,
    )


def _collect_daily_entries(life_log_root: Path, start_day: date, end_day: date) -> str:
    blocks = []
    day = start_day
    while day <= end_day:
        p = daily_file_path(life_log_root, day)
        if p.exists():
            blocks.append(f"## {day.isoformat()}\n{p.read_text(encoding='utf-8')}")
        day += timedelta(days=1)
    return "\n\n".join(blocks)


def summarize_week(
    repo_root: Path, life_log_root: Path, target_day: date | None = None
) -> SummaryResult:
    target_day = target_day or ist_today()
    week_start = target_day - timedelta(days=target_day.weekday())
    week_end = week_start + timedelta(days=6)
    source_text = _collect_daily_entries(life_log_root, week_start, week_end)
    if not source_text.strip():
        raise ValueError("No daily entries found for this week.")

    agent = _summary_agent()
    task = Task(
        description=(
            "Analyze the following week of daily logs and produce a structured, comprehensive weekly summary.\n"
            "Your output must be formatted as Markdown and contain exactly the following sections:\n"
            "1. **Highlights**: The top 3-5 events, achievements, or notable moments of the week.\n"
            "2. **Metrics**: Quantified counts where possible (e.g., number of notes, unique people mentioned, habit consistency).\n"
            "3. **Themes & Trends**: Dominant topics, recurrent emotions, or recurring challenges faced over the week.\n"
            "4. **Risks & Blockers**: Areas of friction, negative habits, or ignored goals that need attention.\n"
            "5. **Next Week Focus**: 2-3 actionable, concrete intentions for the upcoming week based on this week's data.\n\n"
            "Ensure the tone is analytical, objective, and supportive. Do not include introductory or concluding conversational text, just the markdown.\n\n"
            f"### ENTRIES:\n{source_text}"
        ),
        expected_output="A well-formatted Markdown document containing the weekly summary categorized into Highlights, Metrics, Themes & Trends, Risks & Blockers, and Next Week Focus.",
        agent=agent,
    )
    summary_md = str(Crew(agents=[agent], tasks=[task], verbose=False).kickoff())
    path = weekly_file_path(life_log_root, target_day)
    metadata = {
        "type": "weekly_summary",
        "generated_at": now_iso(),
        "week_of": week_start.isoformat(),
    }
    write_markdown(path, metadata, summary_md)

    git = GitStore(repo_root)
    commit_msg = f"summary: generate weekly summary {path.stem}"
    result = git.commit_paths([path], commit_msg)
    return SummaryResult(str(path), "weekly", result.commit_hash or result.message)


def summarize_month(
    repo_root: Path, life_log_root: Path, target_day: date | None = None
) -> SummaryResult:
    target_day = target_day or ist_today()
    month_start = date(target_day.year, target_day.month, 1)
    month_end = date(target_day.year, target_day.month, 28)
    while True:
        try:
            month_end = month_end.replace(day=month_end.day + 1)
        except ValueError:
            break
    source_text = _collect_daily_entries(life_log_root, month_start, month_end)
    if not source_text.strip():
        raise ValueError("No daily entries found for this month.")

    agent = _summary_agent()
    task = Task(
        description=(
            "Review the provided daily logs for the entire month and generate a deep-dive monthly reflection.\n"
            "Your output must be formatted as Markdown and contain exactly the following sections:\n"
            "1. **Major Wins & Milestones**: The biggest accomplishments and positive events of the month.\n"
            "2. **Key Metrics**: Aggregated quantitative data (e.g., total notes, people interacted with most frequently).\n"
            "3. **Relationship Dynamics**: Shifts or notable consistencies in social interactions and dynamics.\n"
            "4. **Health & Wellbeing**: An assessment of physical and mental health trends throughout the month.\n"
            "5. **Focus & Productivity**: Where did time and energy go? What were the primary projects or distractions?\n"
            "6. **Next Month Intentions**: 3-4 high-level goals and behavioral adjustments for the next month.\n\n"
            "Ensure the synthesis pulls back from day-to-day noise to focus on the 'big picture'. Keep the tone analytical and insightful. Output strictly the markdown without conversational filler.\n\n"
            f"### ENTRIES:\n{source_text}"
        ),
        expected_output="A comprehensive Markdown document containing the monthly reflection categorized into Major Wins, Key Metrics, Relationship Dynamics, Health & Wellbeing, Focus & Productivity, and Next Month Intentions.",
        agent=agent,
    )
    summary_md = str(Crew(agents=[agent], tasks=[task], verbose=False).kickoff())
    path = monthly_file_path(life_log_root, target_day)
    metadata = {
        "type": "monthly_summary",
        "generated_at": now_iso(),
        "month": f"{target_day.year}-{target_day.month:02d}",
    }
    write_markdown(path, metadata, summary_md)

    git = GitStore(repo_root)
    commit_msg = f"summary: generate monthly summary {path.stem}"
    result = git.commit_paths([path], commit_msg)
    return SummaryResult(str(path), "monthly", result.commit_hash or result.message)


def summarize_today(life_log_root: Path, target_day: date | None = None) -> str:
    target_day = target_day or ist_today()
    p = daily_file_path(life_log_root, target_day)
    if not p.exists():
        return "No entries found for today yet."
    
    source_text = p.read_text(encoding='utf-8')
    if not source_text.strip():
        return "Today's entry is empty."

    agent = _summary_agent()
    task = Task(
        description=(
            "You are providing a quick, friendly, and concise daily recap for the user based on today's journal entry.\n"
            "Summarize the activities, highlights, and any notable details in 2-3 sentences.\n"
            "Keep the tone encouraging. Do not use markdown headers or extra formatting, just return a short plain paragraph.\n\n"
            f"### ENTRY:\n{source_text}"
        ),
        expected_output="A short 2-3 sentence paragraph summarizing today's entry.",
        agent=agent,
    )
    return str(Crew(agents=[agent], tasks=[task], verbose=False).kickoff())

