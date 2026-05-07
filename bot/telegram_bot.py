from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

AWAITING_RECORD_TEXT = 1
AWAITING_QUESTION_TEXT = 2
AWAITING_FLUSH_CONFIRM = 3
TELEGRAM_MAX_CHARS = 4096
TRUNC_SUFFIX = "\n...truncated, see repo for full output"
MENU = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("Record note", callback_data="menu_record")],
        [InlineKeyboardButton("Ask your life", callback_data="menu_ask")],
        [InlineKeyboardButton("Summarize today", callback_data="menu_today")],
        [InlineKeyboardButton("Summarize week", callback_data="menu_sum_yesterday")],
        [InlineKeyboardButton("Summarize month", callback_data="menu_sum_month")],
        [InlineKeyboardButton("Flush all data", callback_data="menu_flush_all")],
    ]
)


@dataclass
class BotConfig:
    token: str
    allowed_chat_id: int


def _deps():
    from agents.orchestrator import (
        LIFE_LOG_ROOT,
        run_life_hygiene,
        run_record_note,
        run_search,
        run_summarize_month,
        run_summarize_yesterday,
    )
    from core.time_utils import IST, ist_now, ist_today

    return {
        "LIFE_LOG_ROOT": LIFE_LOG_ROOT,
        "run_life_hygiene": run_life_hygiene,
        "run_record_note": run_record_note,
        "run_search": run_search,
        "run_summarize_month": run_summarize_month,
        "run_summarize_yesterday": run_summarize_yesterday,
        "run_summarize_today": run_summarize_today,
        "IST": IST,
        "ist_now": ist_now,
        "ist_today": ist_today,
    }


def _processed_messages_path() -> Path:
    return _deps()["LIFE_LOG_ROOT"] / "meta" / "processed_messages.json"


def _load_config() -> BotConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN. Add it to environment and retry.")
    if not os.getenv("GROQ_API_KEY", "").strip():
        raise RuntimeError("Missing GROQ_API_KEY. Add it to environment and retry.")
    raw_allowed = os.getenv("ALLOWED_CHAT_ID", "").strip()
    if not raw_allowed:
        raise RuntimeError("Missing ALLOWED_CHAT_ID. Bot is single-user in this release.")
    try:
        allowed_chat_id = int(raw_allowed)
    except ValueError as exc:
        raise RuntimeError("ALLOWED_CHAT_ID must be an integer chat id.") from exc
    return BotConfig(token=token, allowed_chat_id=allowed_chat_id)


def _is_authorized(update: Update, allowed_chat_id: int) -> bool:
    chat_id = update.effective_chat.id if update.effective_chat else None
    return chat_id == allowed_chat_id


async def _reject_unauthorized(update: Update) -> None:
    msg = "This bot is restricted to one authorized user in the current release."
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except BadRequest:
            pass
        await update.callback_query.message.reply_text(msg)
    elif update.effective_message:
        await update.effective_message.reply_text(msg)


def _load_processed_messages() -> dict[str, Any]:
    processed_path = _processed_messages_path()
    if not processed_path.exists():
        return {"items": []}
    try:
        return json.loads(processed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"items": []}


def _save_processed_messages(data: dict[str, Any]) -> None:
    processed_path = _processed_messages_path()
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _message_hash(message_text: str, bucket_dt: datetime) -> str:
    bucket = bucket_dt.astimezone(_deps()["IST"]).strftime("%Y-%m-%dT%H:%M")
    raw = f"{message_text.strip()}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_duplicate_message(message_text: str, bucket_dt: datetime) -> bool:
    data = _load_processed_messages()
    digest = _message_hash(message_text, bucket_dt)
    return any(item.get("hash") == digest for item in data.get("items", []))


def _register_message(message_text: str, bucket_dt: datetime) -> None:
    data = _load_processed_messages()
    digest = _message_hash(message_text, bucket_dt)
    data.setdefault("items", []).append(
        {
            "hash": digest,
            "bucket": bucket_dt.astimezone(_deps()["IST"]).strftime("%Y-%m-%dT%H:%M"),
            "created_at": _deps()["ist_now"]().isoformat(timespec="seconds"),
        }
    )
    # Keep recent history bounded.
    data["items"] = data["items"][-5000:]
    _save_processed_messages(data)


def _clip_text(text: str, limit: int = TELEGRAM_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(TRUNC_SUFFIX))
    return text[:keep] + TRUNC_SUFFIX


def _split_text(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < 100:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def _format_result(
    status: str,
    summary: str,
    result: dict[str, Any] | None = None,
    sources: list[str] | None = None,
) -> str:
    def _esc(value: Any) -> str:
        return html.escape(str(value), quote=False)

    def _code(value: Any) -> str:
        return f"<code>{_esc(value)}</code>"

    def _clean(value: Any) -> str:
        text = str(value)
        # Normalize escaped backslashes from JSON/model output for readable paths.
        return text.replace("\\\\", "\\")

    parts = [f"<b>Status:</b> {_esc(status)}", f"<b>Summary:</b> {_esc(summary)}"]
    result = result or {}
    if result.get("daily_path"):
        parts.append(f"<b>Daily:</b> {_code(_clean(result['daily_path']))}")
    if result.get("calendar_path"):
        parts.append(f"<b>Calendar:</b> {_code(_clean(result['calendar_path']))}")
    if result.get("summary_path"):
        parts.append(f"<b>Summary File:</b> {_code(_clean(result['summary_path']))}")
    if result.get("report_path"):
        parts.append(f"<b>Report:</b> {_code(_clean(result['report_path']))}")
    if result.get("commit"):
        parts.append(f"<b>Commit:</b> {_code(_clean(result['commit']))}")
    answer_text = _clean(result.get("answer", "")).strip()
    if answer_text:
        parts.append("<b>Answer:</b>")
        parts.append(f"<pre>{_esc(answer_text)}</pre>")
    if sources:
        short_sources = [_clean(src) for src in sources[:3]]
        parts.append("<b>Sources:</b>")
        for src in short_sources:
            parts.append(f"- <code>{_esc(src)}</code>")
    return "\n".join(parts)


async def _send_response(message, text: str) -> None:
    for chunk in _split_text(_clip_text(text)):
        await message.reply_text(chunk, parse_mode="HTML")


async def _send_menu(message) -> None:
    await message.reply_text("Choose next action:", reply_markup=MENU)


def _summary_answer_from_result(result: dict[str, Any]) -> str:
    summary_path = result.get("summary_path")
    period = str(result.get("period", "")).strip()
    if not summary_path:
        return "Summary generation completed."
    try:
        text = Path(str(summary_path)).read_text(encoding="utf-8")
    except OSError:
        return f"Summary generated for {period}."

    lines: list[str] = []
    in_frontmatter = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter or not stripped:
            continue
        if stripped.startswith("title:") or stripped.startswith("type:"):
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith(("- ", "* ")):
            lines.append(stripped[2:].strip().rstrip("."))
        else:
            lines.append(stripped.rstrip("."))
        if len(lines) >= 2:
            break

    if not lines:
        return f"Summary generated for {period}."
    if len(lines) == 1:
        return f"For {period}, {lines[0]}."
    return f"For {period}, {lines[0]}. Also: {lines[1]}."


def _hygiene_answer_from_result(result: dict[str, Any]) -> str:
    created = result.get("created_files", []) or []
    fixed = result.get("fixed_files", []) or []
    if not created and not fixed:
        return "Life hygiene completed. No issues were found in recent entries."
    parts: list[str] = []
    if fixed:
        parts.append(f"fixed {len(fixed)} file(s)")
    if created:
        parts.append(f"created {len(created)} file(s)")
    details = " and ".join(parts)
    return f"Life hygiene completed and {details}."


async def _run_with_thinking(message, label: str, work):
    thinking = await message.reply_text(f"{label}... thinking")
    task = asyncio.create_task(asyncio.to_thread(work))
    try:
        # Keep user informed while long-running work is in progress.
        while not task.done():
            try:
                await message.chat.send_action(action=ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(2)
        result = await task
        await thinking.edit_text(f"{label}... done")
        return result, None
    except Exception:
        await thinking.edit_text(f"{label}... failed")
        return None, "error"





def _flush_life_log_data() -> dict[str, Any]:
    root = _deps()["LIFE_LOG_ROOT"]
    deleted = 0
    preserved = {"templates", "meta"}
    for md in root.rglob("*.md"):
        rel_parts = md.relative_to(root).parts
        if rel_parts and rel_parts[0] in preserved and "health_reports" not in rel_parts:
            continue
        if md.exists():
            md.unlink()
            deleted += 1
    for js in root.rglob("*.json"):
        rel_parts = js.relative_to(root).parts
        if rel_parts and rel_parts[0] == "meta":
            js.unlink(missing_ok=True)
            deleted += 1

    # Recreate expected skeleton files.
    for path in [
        root / "calendar" / ".gitkeep",
        root / "journal" / "daily" / ".gitkeep",
        root / "journal" / "weekly" / ".gitkeep",
        root / "journal" / "monthly" / ".gitkeep",
        root / "meta" / "indices" / ".gitkeep",
        root / "meta" / "health_reports" / ".gitkeep",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return {"deleted_files": deleted, "root": str(root)}


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config: BotConfig = context.application.bot_data["config"]
    if not _is_authorized(update, config.allowed_chat_id):
        await _reject_unauthorized(update)
        return ConversationHandler.END
    if update.effective_message:
        await update.effective_message.reply_text(
            "Choose an action from the menu:", reply_markup=MENU
        )
    return ConversationHandler.END


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config: BotConfig = context.application.bot_data["config"]
    if not _is_authorized(update, config.allowed_chat_id):
        await _reject_unauthorized(update)
        return ConversationHandler.END
    if update.effective_message:
        await update.effective_message.reply_text(
            "/start -> menu\n"
            "Record note -> stores your note and commits\n"
            "Ask your life -> search across life_log\n"
            "/today -> today's entry status",
            reply_markup=MENU,
        )
    return ConversationHandler.END


async def today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config: BotConfig = context.application.bot_data["config"]
    if not _is_authorized(update, config.allowed_chat_id):
        await _reject_unauthorized(update)
        return ConversationHandler.END
    if update.effective_message:
        result, err = await _run_with_thinking(
            update.effective_message,
            "Summarize today",
            lambda: _deps()["run_summarize_today"](),
        )
        if not err:
            enriched_result = dict(result)
            enriched_result["answer"] = result.get("answer", "")
            text = _format_result(
                "ok", "Today's summary generated.", result=enriched_result
            )
        else:
            text = _format_result("error", "Unable to summarize today right now.")
        await _send_response(update.effective_message, text)
        await _send_menu(update.effective_message)
    return ConversationHandler.END


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config: BotConfig = context.application.bot_data["config"]
    if not _is_authorized(update, config.allowed_chat_id):
        await _reject_unauthorized(update)
        return ConversationHandler.END
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    try:
        await query.answer()
    except BadRequest:
        # Callback query can expire if user taps old buttons.
        if query.message:
            await query.message.reply_text("That menu expired. Send /start to get a fresh menu.")
        return ConversationHandler.END

    action = query.data
    if action == "menu_record":
        await query.message.reply_text("Send any raw note text to record.")
        return AWAITING_RECORD_TEXT
    if action == "menu_ask":
        await query.message.reply_text("Send your question (example: What did I do today?)")
        return AWAITING_QUESTION_TEXT
    if action == "menu_today":
        result, err = await _run_with_thinking(
            query.message,
            "Summarize today",
            lambda: _deps()["run_summarize_today"](),
        )
        if not err:
            enriched_result = dict(result)
            enriched_result["answer"] = result.get("answer", "")
            text = _format_result(
                "ok", "Today's summary generated.", result=enriched_result
            )
        else:
            text = _format_result("error", "Unable to summarize today right now.")
        await _send_response(query.message, text)
        await _send_menu(query.message)
        return ConversationHandler.END
    if action == "menu_flush_all":
        await query.message.reply_text(
            "Dangerous action.\n"
            "This will delete journal/calendar/summaries/search indices.\n"
            "Type exactly `FLUSH` to confirm, or /cancel to abort."
        )
        return AWAITING_FLUSH_CONFIRM
    if action == "menu_sum_yesterday":
        result, err = await _run_with_thinking(
            query.message,
            "Summarize week",
            lambda: _deps()["run_summarize_yesterday"](),
        )
        if not err:
            enriched_result = dict(result)
            enriched_result["answer"] = _summary_answer_from_result(result)
            sources = [f"FILE: {result['summary_path']}"] if result.get("summary_path") else []
            text = _format_result(
                "ok", "Weekly summary generated.", result=enriched_result, sources=sources
            )
        else:
            text = _format_result("error", "Unable to summarize yesterday right now.")
        await _send_response(query.message, text)
        await _send_menu(query.message)
        return ConversationHandler.END
    if action == "menu_sum_month":
        result, err = await _run_with_thinking(
            query.message,
            "Summarize month",
            lambda: _deps()["run_summarize_month"](),
        )
        if not err:
            enriched_result = dict(result)
            enriched_result["answer"] = _summary_answer_from_result(result)
            sources = [f"FILE: {result['summary_path']}"] if result.get("summary_path") else []
            text = _format_result(
                "ok", "Monthly summary generated.", result=enriched_result, sources=sources
            )
        else:
            text = _format_result("error", "Unable to summarize month right now.")
        await _send_response(query.message, text)
        await _send_menu(query.message)
        return ConversationHandler.END
    if action == "menu_hygiene":
        result, err = await _run_with_thinking(
            query.message,
            "Run life hygiene",
            lambda: _deps()["run_life_hygiene"](),
        )
        if not err:
            enriched_result = dict(result)
            enriched_result["answer"] = _hygiene_answer_from_result(result)
            sources = [f"FILE: {result['report_path']}"] if result.get("report_path") else []
            text = _format_result(
                "ok", "Life hygiene check completed.", result=enriched_result, sources=sources
            )
        else:
            text = _format_result("error", "Unable to run hygiene check right now.")
        await _send_response(query.message, text)
        await _send_menu(query.message)
        return ConversationHandler.END
    return ConversationHandler.END


async def record_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config: BotConfig = context.application.bot_data["config"]
    if not _is_authorized(update, config.allowed_chat_id):
        await _reject_unauthorized(update)
        return ConversationHandler.END
    msg = update.effective_message
    if msg is None or not msg.text:
        return ConversationHandler.END
    message_time = msg.date or _deps()["ist_now"]()
    if _is_duplicate_message(msg.text, message_time):
        await _send_response(msg, _format_result("ok", "Already recorded (duplicate ignored)."))
        await _send_menu(msg)
        return ConversationHandler.END
    result, err = await _run_with_thinking(
        msg,
        "Record note",
        lambda: _deps()["run_record_note"](msg.text),
    )
    if not err:
        _register_message(msg.text, message_time)
        summary = result.get("metadata", {}).get("summary", "Recorded successfully.")
        text = _format_result("ok", summary, result=result)
    else:
        text = _format_result("error", "Unable to record this note right now.")
    await _send_response(msg, text)
    await _send_menu(msg)
    return ConversationHandler.END


async def question_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config: BotConfig = context.application.bot_data["config"]
    if not _is_authorized(update, config.allowed_chat_id):
        await _reject_unauthorized(update)
        return ConversationHandler.END
    msg = update.effective_message
    if msg is None or not msg.text:
        return ConversationHandler.END
    result, err = await _run_with_thinking(
        msg,
        "Search",
        lambda: _deps()["run_search"](msg.text),
    )
    if not err:
        text = _format_result(
            "ok",
            "Search completed.",
            result={"answer": result.get("answer", "")},
            sources=result.get("sources", []),
        )
    else:
        text = _format_result("error", "Unable to answer right now.")
    await _send_response(msg, text)
    await _send_menu(msg)
    return ConversationHandler.END


async def flush_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    config: BotConfig = context.application.bot_data["config"]
    if not _is_authorized(update, config.allowed_chat_id):
        await _reject_unauthorized(update)
        return ConversationHandler.END
    msg = update.effective_message
    if msg is None or not msg.text:
        return ConversationHandler.END
    if msg.text.strip() != "FLUSH":
        await msg.reply_text(
            "Flush cancelled. Type exactly `FLUSH` next time.",
        )
        await _send_menu(msg)
        return ConversationHandler.END
    result, err = await _run_with_thinking(msg, "Flush all data", _flush_life_log_data)
    if err:
        text = _format_result("error", "Unable to flush data right now.")
    else:
        text = _format_result(
            "ok",
            "All life_log data files were flushed.",
            result={"answer": f"Deleted files: {result['deleted_files']}"},
        )
    await _send_response(msg, text)
    await _send_menu(msg)
    return ConversationHandler.END


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_message:
        await update.effective_message.reply_text("Cancelled.", reply_markup=MENU)
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Keep failures user-safe and avoid noisy stack traces in Telegram runtime loop.
    if isinstance(update, Update):
        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(
                "Something went wrong. Please try again or send /start."
            )
            return
        if update.effective_message:
            await update.effective_message.reply_text(
                "Something went wrong. Please try again or send /start."
            )


def build_application() -> Application:
    config = _load_config()
    app = Application.builder().token(config.token).build()
    app.bot_data["config"] = config

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern=r"^menu_")],
        states={
            AWAITING_RECORD_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, record_text_handler)
            ],
            AWAITING_QUESTION_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, question_text_handler)
            ],
            AWAITING_FLUSH_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, flush_confirm_handler)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("today", today_handler))
    app.add_handler(conv)
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    app = build_application()
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
