from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

# Ensure project-root imports work when Streamlit runs from streamlit_app/.
ROOT = Path(__file__).resolve().parents[1]
LIFE_LOG_ROOT = ROOT / "life_log"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ist_today():
    from core.time_utils import IST, ist_now, ist_today

    return {"IST": IST, "ist_now": ist_now, "ist_today": ist_today}


def _orchestrator():
    from agents.orchestrator import (
        run_life_hygiene,
        run_record_import,
        run_search,
        run_summarize_month,
        run_summarize_yesterday,
    )

    return {
        "run_life_hygiene": run_life_hygiene,
        "run_record_import": run_record_import,
        "run_search": run_search,
        "run_summarize_month": run_summarize_month,
        "run_summarize_yesterday": run_summarize_yesterday,
    }


def read_file(path: Path) -> str:
    if not path.exists():
        return f"_No file found: `{path}`_"
    return path.read_text(encoding="utf-8")


def list_summary_files(kind: str) -> list[Path]:
    folder = LIFE_LOG_ROOT / "journal" / kind
    if not folder.exists():
        return []
    return sorted(folder.glob("*.md"))


def _processed_events_path() -> Path:
    return LIFE_LOG_ROOT / "meta" / "processed_events.json"


def _load_processed_events() -> dict[str, Any]:
    processed_path = _processed_events_path()
    if not processed_path.exists():
        return {"items": []}
    try:
        return json.loads(processed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"items": []}


def _save_processed_events(data: dict[str, Any]) -> None:
    processed_path = _processed_events_path()
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _message_hash(message_text: str, bucket_dt: datetime) -> str:
    bucket = bucket_dt.astimezone(_ist_today()["IST"]).strftime("%Y-%m-%dT%H:%M")
    raw = f"{message_text.strip()}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_duplicate_message(message_text: str, bucket_dt: datetime) -> bool:
    data = _load_processed_events()
    digest = _message_hash(message_text, bucket_dt)
    return any(item.get("hash") == digest for item in data.get("items", []))


def _register_message(message_text: str, bucket_dt: datetime) -> None:
    data = _load_processed_events()
    digest = _message_hash(message_text, bucket_dt)
    data.setdefault("items", []).append(
        {
            "hash": digest,
            "bucket": bucket_dt.astimezone(_ist_today()["IST"]).strftime("%Y-%m-%dT%H:%M"),
            "created_at": _ist_today()["ist_now"]().isoformat(timespec="seconds"),
        }
    )
    data["items"] = data["items"][-5000:]
    _save_processed_events(data)


def _today_summary_text() -> str:
    today = _ist_today()["ist_today"]()
    daily_path = LIFE_LOG_ROOT / "journal" / "daily" / f"{today.isoformat()}.md"
    if not daily_path.exists():
        return f"No entries found for {today.isoformat()} yet."
    text = daily_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    event_titles: list[str] = []
    details: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## Meeting:") or stripped.startswith("## Event:"):
            title = stripped.replace("## Meeting:", "").replace("## Event:", "").strip()
            if title and title.lower() not in {"none", "unknown", "n/a", "general"}:
                event_titles.append(title)
        if stripped.startswith("- Details:") or stripped.startswith("- Notes:"):
            note = stripped.replace("- Details:", "").replace("- Notes:", "").strip()
            if note and note.lower() not in {"none", "unknown", "n/a"}:
                details.append(note)
    activity_parts: list[str] = []
    if details:
        activity_parts.extend([d.strip().rstrip(".") for d in details[:3]])
    if event_titles:
        activity_parts.extend([t.strip().rstrip(".") for t in event_titles[:2]])

    def _smart_title(text_value: str) -> str:
        acronyms = {"srh", "pbks", "ipl", "b2b", "ui", "ux", "api"}
        words = text_value.split()
        out = []
        for w in words:
            key = re.sub(r"[^a-zA-Z0-9]", "", w).lower()
            out.append(w.upper() if key in acronyms else w)
        return " ".join(out)

    if not activity_parts:
        return f"On {today.isoformat()}, your entry exists but specific activity details were not captured yet."
    if len(activity_parts) == 1:
        main = _smart_title(activity_parts[0])
        return f"On {today.isoformat()}, you {main}."
    first = _smart_title(activity_parts[0])
    rest = "; ".join(_smart_title(x) for x in activity_parts[1:3])
    answer = f"On {today.isoformat()}, you {first}."
    if rest:
        answer += f" Also: {rest}."
    return answer


st.set_page_config(page_title="Git-as-Life-Log", layout="wide")
st.sidebar.title("Git-as-Life-Log")
page = st.sidebar.radio("Navigate", ["Daily Journal", "Summaries", "Ask Your Life", "Run Agents"])

if page == "Daily Journal":
    st.header("Daily Journal")
    picked = st.date_input("Pick date", value=_ist_today()["ist_today"]())
    path = LIFE_LOG_ROOT / "journal" / "daily" / f"{picked.isoformat()}.md"
    st.markdown(read_file(path))

elif page == "Summaries":
    st.header("Weekly / Monthly Summaries")
    summary_type = st.selectbox("Summary type", ["weekly", "monthly"])
    files = list_summary_files(summary_type)
    labels = [f.name for f in files]
    chosen = st.selectbox("Select file", labels) if labels else None
    if chosen:
        path = LIFE_LOG_ROOT / "journal" / summary_type / chosen
        st.markdown(read_file(path))
    else:
        st.info("No summary files found yet.")

elif page == "Ask Your Life":
    st.header("Ask Questions About Your Life")
    q = st.text_input("Example: When did I last meet @alice?")
    if st.button("Ask") and q.strip():
        with st.spinner("Searching your life log..."):
            try:
                result = _orchestrator()["run_search"](q)
                st.write(result["answer"])
                st.caption(f"Confidence: {result['confidence']}")
                if result["sources"]:
                    st.caption("Sources: " + ", ".join(result["sources"]))
            except Exception as exc:
                st.error(f"Search failed: {exc}")

elif page == "Run Agents":
    st.header("Run Agents")
    if st.button("Summarize today"):
        try:
            st.info(_today_summary_text())
        except Exception as exc:
            st.error(f"Today summary failed: {exc}")

    raw_import = st.text_area("Raw event import (calendar/email text)")
    if st.button("Record import"):
        if not raw_import.strip():
            st.warning("Add raw event text first.")
        else:
            with st.spinner("Running Recorder Agent..."):
                try:
                    message_time = _ist_today()["ist_now"]()
                    if _is_duplicate_message(raw_import, message_time):
                        st.info("Already recorded (duplicate ignored).")
                    else:
                        result = _orchestrator()["run_record_import"](raw_import)
                        _register_message(raw_import, message_time)
                        st.success("Recorder completed.")
                        st.json(result)
                except Exception as exc:
                    st.error(f"Recorder failed: {exc}")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Summarize yesterday (weekly rollup)"):
            with st.spinner("Running Summary Agent..."):
                try:
                    result = _orchestrator()["run_summarize_yesterday"]()
                    st.success("Weekly summary generated.")
                    st.json(result)
                except Exception as exc:
                    st.error(f"Summary failed: {exc}")
    with c2:
        if st.button("Summarize month"):
            with st.spinner("Running Summary Agent..."):
                try:
                    result = _orchestrator()["run_summarize_month"]()
                    st.success("Monthly summary generated.")
                    st.json(result)
                except Exception as exc:
                    st.error(f"Monthly summary failed: {exc}")

    if st.button("Check life hygiene"):
        with st.spinner("Running Life-Guard Agent..."):
            try:
                result = _orchestrator()["run_life_hygiene"]()
                st.success("Life-Guard completed.")
                st.json(result)
            except Exception as exc:
                st.error(f"Life-Guard failed: {exc}")
