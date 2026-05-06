from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from crewai import Agent, Crew, Task

from agents.llm import build_groq_llm
from core.git_store import GitStore
from core.markdown_store import now_iso, parse_json_from_text
from core.time_utils import ist_today

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency path
    np = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - optional dependency path
    SentenceTransformer = None


@dataclass
class SearchAnswer:
    answer: str
    sources: list[str]
    confidence: str


_EMBEDDER: SentenceTransformer | None = None
_EMBEDDER_MODEL = "all-MiniLM-L6-v2"
_STOPWORDS = {
    "what",
    "when",
    "did",
    "do",
    "was",
    "were",
    "is",
    "the",
    "a",
    "an",
    "i",
    "me",
    "my",
    "on",
    "at",
    "to",
    "for",
    "in",
    "of",
    "today",
}


def _search_agent() -> Agent:
    return Agent(
        role="Life Fact Finder",
        goal="Answer factual timeline questions by combining markdown evidence with git history context.",
        backstory="You produce concise and evidence-based answers with source files.",
        llm=build_groq_llm(),
        verbose=False,
    )


def _get_embedder() -> SentenceTransformer | None:
    global _EMBEDDER
    if SentenceTransformer is None:
        return None
    if _EMBEDDER is None:
        _EMBEDDER = SentenceTransformer(_EMBEDDER_MODEL)
    return _EMBEDDER


def _split_into_chunks(text: str, chunk_lines: int = 24) -> list[dict[str, Any]]:
    lines = text.splitlines()
    chunks: list[dict[str, Any]] = []
    for idx in range(0, len(lines), chunk_lines):
        part = lines[idx : idx + chunk_lines]
        chunk_text = "\n".join(part).strip()
        if not chunk_text:
            continue
        chunks.append(
            {
                "start_line": idx + 1,
                "end_line": idx + len(part),
                "text": chunk_text,
            }
        )
    return chunks


def _extract_target_person(question: str) -> str | None:
    mention = re.search(r"(@[a-zA-Z0-9_]+)", question)
    return mention.group(1).lower() if mention else None


def _is_last_meeting_question(question: str) -> bool:
    lowered = question.lower()
    return ("last" in lowered) and ("meet" in lowered or "met" in lowered)


def _is_today_question(question: str) -> bool:
    lowered = question.lower()
    return "today" in lowered or "current day" in lowered


def _extract_query_terms(question: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9_@]+", question.lower())
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


def _extract_date_from_path(path: str) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.replace("\\", "/"))
    return m.group(1) if m else None


def _extract_date_from_text(text: str) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def _deterministic_activity_when(
    life_log_root: Path, question: str
) -> tuple[str, list[str], str] | None:
    terms = _extract_query_terms(question)
    if not terms or "when" not in question.lower():
        return None
    candidates: list[tuple[int, str, str, str | None]] = []
    for md in life_log_root.rglob("*.md"):
        if "templates" in md.parts:
            continue
        text = md.read_text(encoding="utf-8")
        lowered = text.lower()
        score = sum(1 for t in terms if t in lowered)
        if score <= 0:
            continue
        date_hint = _extract_date_from_path(str(md)) or _extract_date_from_text(text[:1500])
        candidates.append((score, str(md), text[:1200], date_hint))
    if not candidates:
        return None

    # Prefer highest score; then dated records; then daily/calendar files over rollups.
    def _priority(item: tuple[int, str, str, str | None]) -> tuple[int, int, int]:
        score, path, _, date_hint = item
        norm = path.replace("\\", "/").lower()
        path_bias = 1 if ("/daily/" in norm or "/calendar/" in norm) else 0
        return (score, 1 if date_hint else 0, path_bias)

    candidates.sort(key=_priority, reverse=True)
    score, path, snippet, dt = candidates[0]
    if not dt:
        return None
    preferred_sources = [path]
    daily_path = life_log_root / "journal" / "daily" / f"{dt}.md"
    if daily_path.exists():
        preferred_sources = [str(daily_path), path]
    return (
        f"You most likely did that on {dt}, based on matching notes in your life log (score={score}).",
        preferred_sources,
        "high",
    )


def _answer_today_question(life_log_root: Path) -> SearchAnswer:
    today = ist_today()
    daily_path = life_log_root / "journal" / "daily" / f"{today.isoformat()}.md"
    if not daily_path.exists():
        return SearchAnswer(
            answer=f"No daily entry found for {today.isoformat()} yet.",
            sources=[],
            confidence="high",
        )
    text = daily_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    def _section_items(header: str) -> list[str]:
        items: list[str] = []
        in_section = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                in_section = stripped == header
                continue
            if in_section and stripped.startswith("- "):
                value = stripped[2:].strip()
                if value and value.lower() not in {"n/a", ""}:
                    items.append(value)
        return items

    meeting_titles = []
    meeting_notes = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## Meeting:") or stripped.startswith("## Event:"):
            title = stripped.replace("## Meeting:", "").replace("## Event:", "").strip()
            if title and title.lower() not in {"none", "unknown", "n/a"}:
                meeting_titles.append(title)
        if stripped.startswith("- Notes:") or stripped.startswith("- Details:"):
            note = stripped.replace("- Notes:", "").replace("- Details:", "").strip()
            if note and note.lower() not in {"none", "unknown", "n/a"}:
                meeting_notes.append(note)

    highlights = _section_items("## Highlights")
    work = _section_items("## Work")
    personal = _section_items("## Personal")

    activity_fragments: list[str] = []
    if meeting_notes:
        activity_fragments.extend(meeting_notes[:3])
    if highlights:
        activity_fragments.extend(highlights[:2])
    if work:
        activity_fragments.extend(work[:2])
    if personal:
        activity_fragments.extend(personal[:2])
    if meeting_titles and not activity_fragments:
        activity_fragments.extend(meeting_titles[:2])

    if not activity_fragments:
        answer = f"On {today.isoformat()}, I found your daily entry, but it does not include clear activity notes yet."
    elif len(activity_fragments) == 1:
        activity = activity_fragments[0].strip().rstrip(".")
        answer = f"On {today.isoformat()}, you {activity}."
    else:
        cleaned = [a.strip().rstrip(".") for a in activity_fragments[:3]]
        answer = f"On {today.isoformat()}, you: " + "; ".join(cleaned) + "."

    return SearchAnswer(answer=answer, sources=[str(daily_path)], confidence="high")


def _find_last_meeting_with_person(
    life_log_root: Path, person: str
) -> tuple[datetime, str, str] | None:
    # Parses event/meeting sections generated by recorder rendering.
    candidates: list[tuple[datetime, str, str]] = []
    for md in life_log_root.rglob("*.md"):
        if "templates" in md.parts:
            continue
        text = md.read_text(encoding="utf-8")
        sections = re.findall(r"## (?:Meeting|Event):.*?(?=\n## |\Z)", text, flags=re.S)
        for section in sections:
            people_match = re.search(r"- People:\s*(.+)", section)
            start_match = re.search(r"- Start:\s*([^\n]+)", section)
            if not people_match or not start_match:
                continue
            people = [p.strip().lower() for p in people_match.group(1).split(",")]
            if person not in people:
                continue
            try:
                start_dt = datetime.fromisoformat(
                    start_match.group(1).strip().replace("Z", "+00:00")
                )
            except ValueError:
                continue
            candidates.append((start_dt, str(md), section.strip()))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])


def _build_index(life_log_root: Path, index_file: Path) -> dict:
    entries: list[dict[str, Any]] = []
    for md in life_log_root.rglob("*.md"):
        if ".git" in md.parts:
            continue
        text = md.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith("- People:"):
                entries.append({"file": str(md), "line": line.strip()})
            if "project:" in line.lower():
                entries.append({"file": str(md), "line": line.strip()})
    index = {"generated_at": now_iso(), "entries": entries}
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


def _build_semantic_index(life_log_root: Path, index_file: Path) -> dict[str, Any]:
    embedder = _get_embedder()
    chunks: list[dict[str, Any]] = []
    for md in life_log_root.rglob("*.md"):
        if "templates" in md.parts:
            continue
        text = md.read_text(encoding="utf-8")
        file_chunks = _split_into_chunks(text)
        if not file_chunks:
            continue
        vectors = None
        if embedder is not None:
            vectors = embedder.encode([c["text"] for c in file_chunks], normalize_embeddings=True)
        for idx, c in enumerate(file_chunks):
            item: dict[str, Any] = {
                "file": str(md),
                "start_line": c["start_line"],
                "end_line": c["end_line"],
                "text": c["text"],
            }
            if vectors is not None:
                item["embedding"] = vectors[idx].tolist()
            chunks.append(item)
    index = {
        "generated_at": now_iso(),
        "embedding_model": _EMBEDDER_MODEL if embedder is not None else None,
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text(json.dumps(index), encoding="utf-8")
    return index


def _load_or_build_semantic_index(life_log_root: Path, rebuild: bool = False) -> dict[str, Any]:
    path = life_log_root / "meta" / "indices" / "semantic_chunks_index.json"
    if rebuild or not path.exists() or _semantic_index_is_stale(life_log_root, path):
        return _build_semantic_index(life_log_root, path)
    return json.loads(path.read_text(encoding="utf-8"))


def _semantic_index_is_stale(life_log_root: Path, index_path: Path) -> bool:
    if not index_path.exists():
        return True
    index_mtime = index_path.stat().st_mtime
    for md in life_log_root.rglob("*.md"):
        if "templates" in md.parts:
            continue
        if md.stat().st_mtime > index_mtime:
            return True
    return False


def _lexical_score(query: str, text: str) -> float:
    q_tokens = [t for t in re.findall(r"[a-zA-Z0-9_@]+", query.lower()) if len(t) > 1]
    if not q_tokens:
        return 0.0
    lower = text.lower()
    hits = sum(1 for token in q_tokens if token in lower)
    return hits / len(q_tokens)


def _retrieve_top_chunks(
    question: str, semantic_index: dict[str, Any], top_k: int = 8
) -> list[dict[str, Any]]:
    chunks = semantic_index.get("chunks", [])
    if not chunks:
        return []

    embedder = _get_embedder()
    if embedder is not None and np is not None and chunks and "embedding" in chunks[0]:
        q_vec = embedder.encode([question], normalize_embeddings=True)[0]
        scored = []
        for chunk in chunks:
            emb = np.array(chunk["embedding"], dtype=float)
            score = float(np.dot(q_vec, emb))
            scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:top_k]]

    # Fallback if embedding dependencies aren't available.
    scored = [(_lexical_score(question, c.get("text", "")), c) for c in chunks]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:top_k] if item[0] > 0]


def answer_life_query(
    repo_root: Path, life_log_root: Path, question: str, rebuild_index: bool = False
) -> SearchAnswer:
    if _is_today_question(question):
        return _answer_today_question(life_log_root)

    deterministic_when = _deterministic_activity_when(life_log_root, question)
    if deterministic_when is not None:
        ans, src, conf = deterministic_when
        return SearchAnswer(answer=ans, sources=src, confidence=conf)

    index_path = life_log_root / "meta" / "indices" / "people_project_index.json"
    if rebuild_index or not index_path.exists():
        _build_index(life_log_root, index_path)

    person = _extract_target_person(question)
    if person and _is_last_meeting_question(question):
        hit = _find_last_meeting_with_person(life_log_root, person)
        if hit:
            when, file_path, _snippet = hit
            answer = (
                f"You last met {person} on {when.date().isoformat()} at "
                f"{when.strftime('%H:%M')} ({when.tzname() or 'local time'})."
            )
            return SearchAnswer(answer=answer, sources=[file_path], confidence="high")
        return SearchAnswer(
            answer=f"No meeting found with {person} in your life_log entries.",
            sources=[],
            confidence="high",
        )

    semantic_index = _load_or_build_semantic_index(life_log_root, rebuild=rebuild_index)
    retrieved = _retrieve_top_chunks(question, semantic_index, top_k=8)
    if not retrieved:
        return SearchAnswer(
            answer="I could not find relevant entries for this question in your life_log yet.",
            sources=[],
            confidence="medium",
        )

    context_chunks = [
        (
            f"FILE: {c['file']} (lines {c.get('start_line', '?')}-{c.get('end_line', '?')})\n"
            f"{c.get('text', '')}"
        )
        for c in retrieved
    ]
    source_files = {c["file"] for c in retrieved if "file" in c}

    git = GitStore(repo_root)
    git_history = git.git_log_text(path="life_log", max_count=100)
    agent = _search_agent()
    task = Task(
        description=(
            "Answer the user question with structured output JSON keys: answer, confidence, sources.\n"
            "Use only evidence from provided retrieved chunks and git history.\n"
            "If exact answer is unknown, say so clearly but still provide best evidence.\n"
            "Question:\n"
            f"{question}\n\n"
            "Retrieved evidence:\n"
            f"{chr(10).join(context_chunks)}\n\n"
            "Git history:\n"
            f"{git_history}"
        ),
        expected_output='JSON object with "answer", "confidence", and "sources".',
        agent=agent,
    )
    result = str(Crew(agents=[agent], tasks=[task], verbose=False).kickoff()).strip()
    try:
        parsed = parse_json_from_text(result)
    except Exception:
        # Fallback: keep UX stable even when model ignores JSON instruction.
        return SearchAnswer(
            answer=result.strip() or "I could not produce a reliable answer for this question.",
            sources=sorted(source_files)[:3],
            confidence="low",
        )
    sources = parsed.get("sources") or sorted(source_files)[:5]
    return SearchAnswer(parsed.get("answer", ""), sources, parsed.get("confidence", "medium"))
