from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from core.git_store import GitStore
from core.markdown_store import (
    daily_file_path,
    read_markdown,
    render_daily_body,
    validate_metadata,
    write_markdown,
)
from core.time_utils import ist_today


@dataclass
class LifeGuardReport:
    report_path: str
    created_files: list[str]
    fixed_files: list[str]
    commit: str


def run_life_guard(
    repo_root: Path,
    life_log_root: Path,
    lookback_days: int = 14,
    auto_fix: bool = True,
) -> LifeGuardReport:
    today = ist_today()
    created: list[str] = []
    fixed: list[str] = []
    touched_paths: list[Path] = []

    for delta in range(1, lookback_days + 1):
        d = today - timedelta(days=delta)
        p = daily_file_path(life_log_root, d)
        if not p.exists() and auto_fix:
            metadata = {
                "type": "journal",
                "date": d.isoformat(),
                "project": "personal",
                "people": [],
                "mood": "neutral",
                "tags": [],
            }
            write_markdown(p, metadata, render_daily_body(["Auto-created by Life-Guard"]))
            created.append(str(p))
            touched_paths.append(p)
            continue
        if p.exists():
            meta, body = read_markdown(p)
            validation = validate_metadata(meta, entry_type="journal")
            if (not validation.valid) and auto_fix:
                normalized = validation.normalized
                normalized.setdefault("type", "journal")
                normalized.setdefault("date", d.isoformat())
                normalized.setdefault("project", "personal")
                normalized.setdefault("people", [])
                normalized.setdefault("mood", "neutral")
                write_markdown(p, normalized, body or render_daily_body())
                fixed.append(str(p))
                touched_paths.append(p)

    report_path = life_log_root / "meta" / "health_reports" / f"{today.isoformat()}.md"
    report_body = (
        "# Life-Guard Report\n\n"
        f"- Lookback Days: {lookback_days}\n"
        f"- Created Missing Files: {len(created)}\n"
        f"- Fixed Malformed Files: {len(fixed)}\n\n"
        "## Created\n"
        + ("\n".join(f"- {p}" for p in created) if created else "- none")
        + "\n\n## Fixed\n"
        + ("\n".join(f"- {p}" for p in fixed) if fixed else "- none")
        + "\n"
    )
    write_markdown(
        report_path,
        {"type": "life_guard_report", "date": today.isoformat(), "lookback_days": lookback_days},
        report_body,
    )
    touched_paths.append(report_path)

    git = GitStore(repo_root)
    commit_msg = f"life-guard: hygiene check ({lookback_days} days)"
    result = git.commit_paths(touched_paths, commit_msg)
    return LifeGuardReport(
        report_path=str(report_path),
        created_files=created,
        fixed_files=fixed,
        commit=result.commit_hash or result.message,
    )
