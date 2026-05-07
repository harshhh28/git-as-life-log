from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from agents.life_guard_agent import run_life_guard
from agents.recorder_agent import run_recorder
from agents.search_fact_agent import answer_life_query
from agents.summary_agent import summarize_month, summarize_week, summarize_today
from core.time_utils import ist_today

REPO_ROOT = Path(__file__).resolve().parents[1]
LIFE_LOG_ROOT = REPO_ROOT / "life_log"


def run_record_note(raw_text: str) -> dict:
    result = run_recorder(REPO_ROOT, LIFE_LOG_ROOT, raw_text)
    return {
        "daily_path": result.daily_path,
        "calendar_path": result.calendar_path,
        "commit": result.commit,
        "metadata": result.metadata,
    }


def run_summarize_yesterday() -> dict:
    d = ist_today() - timedelta(days=1)
    result = summarize_week(REPO_ROOT, LIFE_LOG_ROOT, target_day=d)
    return {"summary_path": result.summary_path, "period": result.period, "commit": result.commit}


def run_summarize_month() -> dict:
    result = summarize_month(REPO_ROOT, LIFE_LOG_ROOT)
    return {"summary_path": result.summary_path, "period": result.period, "commit": result.commit}





def run_search(question: str) -> dict:
    answer = answer_life_query(REPO_ROOT, LIFE_LOG_ROOT, question)
    return {"answer": answer.answer, "sources": answer.sources, "confidence": answer.confidence}


def run_summarize_today() -> dict:
    answer = summarize_today(LIFE_LOG_ROOT)
    today = ist_today()
    daily_path = LIFE_LOG_ROOT / "journal" / "daily" / f"{today.isoformat()}.md"
    return {"answer": answer, "daily_path": str(daily_path) if daily_path.exists() else None}

