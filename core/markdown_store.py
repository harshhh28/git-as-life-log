from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import frontmatter

from core.time_utils import ist_now

REQUIRED_KEYS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "note": ("type", "topic", "people", "start", "end", "mood"),
    "journal": ("type", "date", "topic", "people", "mood"),
}


@dataclass
class ValidationResult:
    valid: bool
    missing_keys: list[str]
    normalized: dict[str, Any]


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    post = frontmatter.load(path)
    return dict(post.metadata), post.content


def write_markdown(path: Path, metadata: dict[str, Any], body: str) -> None:
    ensure_parent(path)
    post = frontmatter.Post(body, **metadata)
    path.write_text(frontmatter.dumps(post), encoding="utf-8")


def append_markdown_section(path: Path, metadata: dict[str, Any], section: str) -> None:
    existing_meta, body = read_markdown(path)
    merged = existing_meta or metadata
    section = section.strip()
    if body.strip():
        body = body.rstrip() + "\n\n" + section + "\n"
    else:
        body = section + "\n"
    write_markdown(path, merged, body)


def normalize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(metadata)
    people = normalized.get("people", [])
    if people is None:
        people = []
    elif isinstance(people, str):
        people = [p.strip() for p in people.split(",") if p.strip()]
    elif not isinstance(people, list):
        people = [str(people).strip()] if str(people).strip() else []
    normalized["people"] = people

    if "tags" in normalized and isinstance(normalized["tags"], str):
        normalized["tags"] = [t.strip() for t in normalized["tags"].split(",") if t.strip()]

    return normalized


def validate_metadata(metadata: dict[str, Any], entry_type: str) -> ValidationResult:
    normalized = normalize_metadata(metadata)
    required = REQUIRED_KEYS_BY_TYPE.get(entry_type, ("type",))
    missing = []
    for k in required:
        val = normalized.get(k)
        # An empty list is valid for list-type fields like "people" (solo activities are common).
        # Only flag truly absent or None/empty-string values as missing.
        if val is None or val == "":
            missing.append(k)
        elif k not in normalized:
            missing.append(k)
    return ValidationResult(valid=not missing, missing_keys=missing, normalized=normalized)


def daily_file_path(root: Path, target_date: date) -> Path:
    return root / "journal" / "daily" / f"{target_date.isoformat()}.md"


def weekly_file_path(root: Path, target_date: date) -> Path:
    iso = target_date.isocalendar()
    return root / "journal" / "weekly" / f"{iso.year}-W{iso.week:02d}.md"


def monthly_file_path(root: Path, target_date: date) -> Path:
    return root / "journal" / "monthly" / f"{target_date.year}-{target_date.month:02d}.md"


def render_note_section(meta: dict[str, Any]) -> str:
    raw_people = meta.get("people", [])
    if raw_people is None:
        raw_people = []
    if isinstance(raw_people, str):
        raw_people = [raw_people]
    people = ", ".join(str(p) for p in raw_people if str(p).strip()) or "n/a"
    return (
        f"## Note: {meta.get('topic', 'general')}\n"
        f"- People: {people}\n"
        f"- Start: {meta.get('start', 'n/a')}\n"
        f"- End: {meta.get('end', 'n/a')}\n"
        f"- Mood: {meta.get('mood', 'neutral')}\n"
        f"- Details: {meta.get('summary', '').strip() or 'n/a'}\n"
    )


def render_daily_body(highlights: list[str] | None = None) -> str:
    points = highlights or []
    bullet_block = "\n".join(f"- {p}" for p in points) if points else "- "
    return f"# Daily Log\n\n## Highlights\n{bullet_block}\n\n## Work\n- \n\n## Personal\n- \n"


def parse_json_from_text(text: str) -> dict[str, Any]:
    # Supports models returning fenced JSON, plain JSON, or JSON with trailing text.
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()

    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(stripped)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Fallback: locate the first JSON object within mixed text.
    start = stripped.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(stripped[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            start = stripped.find("{", start + 1)
            continue
        break
    raise ValueError("Unable to parse JSON object from model output.")


def now_iso() -> str:
    return ist_now().isoformat(timespec="minutes")
