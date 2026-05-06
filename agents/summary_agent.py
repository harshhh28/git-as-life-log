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
        goal="Summarize daily journals into weekly and monthly insights with quantified metrics.",
        backstory="You detect trends in work, social, health, and focus based on journal logs.",
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
            "Summarize this week of daily logs into markdown with sections:\n"
            "Highlights, Metrics (meetings/projects/workouts), Risks, Next Week Focus.\n"
            "Include concrete counts when possible.\n\n"
            f"ENTRIES:\n{source_text}"
        ),
        expected_output="Markdown weekly summary.",
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
            "Create a monthly reflection from these daily logs.\n"
            "Return markdown with sections: Wins, Metrics, Relationships, Health, Focus Trends, Next Month Intentions.\n\n"
            f"ENTRIES:\n{source_text}"
        ),
        expected_output="Markdown monthly summary.",
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
