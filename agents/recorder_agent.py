from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from crewai import Agent, Crew, Task
from dateutil import parser as dt_parser

from agents.llm import build_groq_llm
from core.git_store import GitStore
from core.markdown_store import (
    append_markdown_section,
    daily_file_path,
    parse_json_from_text,
    render_daily_body,
    render_note_section,
    validate_metadata,
    write_markdown,
)
from core.time_utils import ist_now, ist_today, parse_user_datetime_ist, to_ist_iso


@dataclass
class RecorderResult:
    metadata: dict
    daily_path: str
    calendar_path: str
    commit: str


def _recorder_agent() -> Agent:
    return Agent(
        role="Life Log Recorder",
        goal="Extract structured metadata from unstructured note text.",
        backstory="You normalize personal notes into strict note metadata.",
        llm=build_groq_llm(model="llama-3.1-8b-instant", temperature=0.1),
        verbose=False,
    )


def _raw_text_has_date(raw_text: str) -> bool:
    patterns = [
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",
        r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b",
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b",
        r"\b(?:today|tomorrow|yesterday)\b",
    ]
    lowered = raw_text.lower()
    return any(re.search(p, lowered) for p in patterns)


_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _extract_explicit_date(raw_text: str) -> date | None:
    lowered = raw_text.lower()
    iso_match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", lowered)
    if iso_match:
        year, month, day = (int(x) for x in iso_match.groups())
        return date(year, month, day)

    dmy_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", lowered)
    if dmy_match:
        day, month, year = (int(x) for x in dmy_match.groups())
        return date(year, month, day)

    word_match = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\s+(\d{4})\b",
        lowered,
    )
    if word_match:
        day_s, month_s, year_s = word_match.groups()
        month = _MONTHS.get(month_s)
        if month:
            return date(int(year_s), month, int(day_s))

    month_word_match = re.search(
        r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(\d{4})\b",
        lowered,
    )
    if month_word_match:
        month_s, day_s, year_s = month_word_match.groups()
        month = _MONTHS.get(month_s)
        if month:
            return date(int(year_s), month, int(day_s))

    return None


def _resolve_note_date(raw_text: str) -> date:
    if not _raw_text_has_date(raw_text):
        return ist_today()

    # Handle relative day keywords first — before dateutil, which can misparse
    # numbers in phrases like "10 min" as date components.
    lowered = raw_text.lower()
    if re.search(r"\btoday\b", lowered):
        return ist_today()
    if re.search(r"\byesterday\b", lowered):
        from datetime import timedelta

        return ist_today() - timedelta(days=1)
    if re.search(r"\btomorrow\b", lowered):
        from datetime import timedelta

        return ist_today() + timedelta(days=1)

    explicit_date = _extract_explicit_date(raw_text)
    if explicit_date is not None:
        return explicit_date
    try:
        parsed = dt_parser.parse(raw_text, fuzzy=True, default=ist_now().replace(tzinfo=None))
        if parsed.year < 2000 or parsed.year > 2100:
            return ist_today()
        return parsed.date()
    except (ValueError, TypeError, OverflowError):
        return ist_today()


def run_recorder(repo_root: Path, life_log_root: Path, raw_text: str) -> RecorderResult:
    agent = _recorder_agent()
    today_ist = ist_today().isoformat()
    task = Task(
        description=(
            "Parse this raw note text and return JSON only with keys: "
            "type, topic, people, start, end, mood, summary.\n\n"
            "Interpret all date/time values in IST if timezone is not explicitly provided. "
            f"If the user gives time but no date, use today's IST date: {today_ist}. "
            "Prefer ISO format.\n\n"
            f"RAW:\n{raw_text}"
        ),
        expected_output="Strict JSON object only.",
        agent=agent,
    )
    parsed_text = str(Crew(agents=[agent], tasks=[task], verbose=False).kickoff())
    metadata = parse_json_from_text(parsed_text)
    metadata.setdefault("type", "note")
    metadata.setdefault("mood", "neutral")
    metadata.setdefault("people", [])
    metadata.setdefault("topic", "general")
    metadata.setdefault("summary", raw_text.strip())
    if not metadata.get("summary"):
        metadata["summary"] = raw_text.strip()
    if not metadata.get("topic") or str(metadata.get("topic")).lower() in {
        "none",
        "null",
        "n/a",
    }:
        metadata["topic"] = "general"
    if not metadata.get("mood") or str(metadata.get("mood")).lower() in {"none", "null", "n/a"}:
        metadata["mood"] = "neutral"
    validation = validate_metadata(metadata, entry_type=metadata["type"])
    if not validation.valid:
        raise ValueError(f"Recorder extracted invalid metadata. Missing: {validation.missing_keys}")
    metadata = validation.normalized

    note_date = _resolve_note_date(raw_text)
    if not metadata.get("start"):
        metadata["start"] = f"{note_date.isoformat()}T00:00+05:30"
    if not metadata.get("end"):
        metadata["end"] = metadata["start"]
    start_dt = parse_user_datetime_ist(str(metadata["start"]))
    # Always anchor date from raw input (or IST today fallback), never trust LLM date blindly.
    start_dt = start_dt.replace(year=note_date.year, month=note_date.month, day=note_date.day)
    metadata["start"] = to_ist_iso(start_dt)
    if metadata.get("end"):
        end_dt = parse_user_datetime_ist(str(metadata["end"]))
        end_dt = end_dt.replace(year=note_date.year, month=note_date.month, day=note_date.day)
        metadata["end"] = to_ist_iso(end_dt)
    target_day = start_dt.date()
    month_dir = life_log_root / "calendar" / f"{target_day.year}-{target_day.month:02d}"
    calendar_path = month_dir / f"{target_day.isoformat()}-events.md"
    daily_path = daily_file_path(life_log_root, target_day)

    if not daily_path.exists():
        daily_meta = {
            "type": "journal",
            "date": target_day.isoformat(),
            "topic": metadata.get("topic", "personal"),
            "people": metadata.get("people", []),
            "mood": metadata.get("mood", "neutral"),
            "tags": [],
        }
        write_markdown(daily_path, daily_meta, render_daily_body())

    append_markdown_section(calendar_path, metadata, render_note_section(metadata))
    append_markdown_section(
        daily_path,
        {"type": "journal", "date": target_day.isoformat()},
        render_note_section(metadata),
    )

    git = GitStore(repo_root)
    commit_msg = (
        f"record: add {metadata.get('type', 'note')} for {target_day.isoformat()} "
        f"({metadata.get('topic', 'unknown')})"
    )
    result = git.commit_paths([calendar_path, daily_path], commit_msg)
    return RecorderResult(
        metadata=metadata,
        daily_path=str(daily_path),
        calendar_path=str(calendar_path),
        commit=result.commit_hash or result.message,
    )
