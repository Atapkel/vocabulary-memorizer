"""Telegram flows for unknown words, lessons, and reminders."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .config import ALLOWED_USER_ID
from .memory import (
    collect, due_counts, due_lesson, import_lessons, inbox_count, inbox_items,
    lesson_by_id, lesson_prompt, rate_lesson, reminder_slot, remove_inbox,
    set_setting, settings, valid_time, word_prompt,
)
from .views import esc

log = logging.getLogger(__name__)


def allowed(update: Update) -> bool:
    return str(update.effective_user.id) == str(ALLOWED_USER_ID)


def memory_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Unknown words", callback_data="memory|unknown"),
         InlineKeyboardButton("📖 Lessons", callback_data="memory|lessons")],
        [InlineKeyboardButton("📝 Word prompt", callback_data="memory|word_prompt"),
         InlineKeyboardButton("📝 Lesson prompt", callback_data="memory|lesson_prompt")],
        [InlineKeyboardButton("🧠 Review lessons", callback_data="memory|review"),
         InlineKeyboardButton("⏰ Reminder settings", callback_data="memory|settings")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu|home")],
    ])


def section_text() -> str:
    words, lessons = due_counts()
    return ("🧠 <b>Memory</b>\n\n"
            f"Due: <b>{words}</b> words, <b>{lessons}</b> lesson cards\n"
            f"Inbox: <b>{inbox_count('word')}</b> unknown words, <b>{inbox_count('lesson')}</b> lesson notes\n\n"
            "Collect notes, ask for a prompt, paste the resulting JSON, and review from memory.")


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await update.message.reply_text(section_text(), reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)


async def cmd_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    raw = update.message.text.partition(" ")[2]
    if not raw.strip():
        context.user_data["collecting"] = "word"
        await update.message.reply_text("Send unknown words separated by commas or new lines. Use /unknownprompt when ready.")
        return
    await save_unknown(update.message.reply_text, raw)


async def save_unknown(reply, raw: str):
    entries = [" ".join(part.split()) for part in re.split(r"[,;\n]+", raw)]
    words = list(dict.fromkeys(part for part in entries if part))
    if len(words) > 100:
        await reply("Send up to 100 words per message. Split the list into several messages.")
        return
    if any(len(word) > 80 or len(word.split()) > 6 for word in words):
        await reply("Each word or phrase must be at most 80 characters and 6 words.")
        return
    if not words:
        await reply("No words found. Separate them with commas or new lines.")
        return
    added, _ = collect("word", words)
    await reply(f"📥 Saved {added} new words. Inbox: {inbox_count('word')}. Use /unknownprompt when ready.", reply_markup=memory_keyboard())


async def cmd_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        context.user_data["collecting"] = "lesson"
        await update.message.reply_text("Send a lesson note or fact (up to 1500 characters). Use /lessonprompt when ready.")
        return
    await save_lesson_note(update.message.reply_text, raw)


async def save_lesson_note(reply, raw: str):
    note = raw.strip()
    if len(note) > 1500:
        await reply("Please split this lesson note into messages under 1500 characters.")
        return
    added, _ = collect("lesson", [note])
    await reply(f"📖 Saved {added} new lesson note. Inbox: {inbox_count('lesson')}. Use /lessonprompt when ready.", reply_markup=memory_keyboard())


async def send_prompt(reply, kind: str, context: ContextTypes.DEFAULT_TYPE):
    notes = inbox_items(kind, 10 if kind == "lesson" else 15)
    if not notes:
        await reply("The inbox is empty. Add material first.", reply_markup=memory_keyboard())
        return
    while notes:
        prompt = word_prompt(notes) if kind == "word" else lesson_prompt(notes)
        if len(prompt) <= 3800:
            break
        notes.pop()
    command = "/add" if kind == "word" else "/lessonimport"
    if not notes:
        await reply("The prompt is too long. Send shorter notes so it fits in Telegram.")
        return
    context.user_data[f"prompted_{kind}"] = notes
    await reply(f"Copy this into an LLM, then paste its JSON reply using {command}. Check the facts before importing.")
    await reply(prompt)


async def cmd_unknownprompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await send_prompt(update.message.reply_text, "word", context)


async def cmd_lessonprompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await send_prompt(update.message.reply_text, "lesson", context)


async def cmd_lessonimport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        context.user_data["collecting"] = "lesson_import"
        await update.message.reply_text("Paste the JSON array returned by the LLM in your next message.")
    else:
        await do_lesson_import(update.message.reply_text, raw, context)


async def do_lesson_import(reply, raw: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        payload = json.loads(raw)
        added, skipped, errors = import_lessons(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        await reply(f"Could not import lesson cards: {exc}")
        return
    if added:
        remove_inbox("lesson", context.user_data.pop("prompted_lesson", []))
    details = "\n" + "\n".join(esc(e) for e in errors) if errors else ""
    await reply(f"✅ Lesson cards added: {added}; skipped: {skipped}.{details}", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)


async def send_lesson_card(reply):
    card = due_lesson()
    if not card:
        await reply("✅ No lesson cards due now.", reply_markup=memory_keyboard())
        return
    await reply(
        f"📖 <b>{esc(card['topic'])}</b>\n\n<b>{esc(card['question'])}</b>\n\nTry to answer from memory, then reveal it.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👁 Reveal answer", callback_data=f"memory|reveal|{card['id']}")], [InlineKeyboardButton("🏠 Menu", callback_data="menu|home")]]),
        parse_mode=ParseMode.HTML,
    )


async def cmd_lessons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await send_lesson_card(update.message.reply_text)


def settings_text() -> str:
    p = settings()
    return ("⏰ <b>Reminder settings</b>\n\n"
            f"Status: <b>{esc(p['reminders'])}</b>\nTimezone: <code>{esc(p['timezone'])}</code>\n"
            f"Quiet hours: <b>{p['quiet_start']}–{p['quiet_end']}</b>\n"
            "Review checks: 09:00, 14:00, 19:00 local time. Only due material triggers a message.\n\n"
            "<code>/timezone Asia/Qyzylorda</code>\n<code>/quiet 22:00 08:00</code>\n"
            "<code>/reminders on</code> or <code>/reminders off</code>")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await update.message.reply_text(settings_text(), reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)


async def cmd_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    value = update.message.text.partition(" ")[2].strip()
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        await update.message.reply_text("Use a valid IANA timezone, for example /timezone Asia/Qyzylorda.")
        return
    set_setting("timezone", value)
    await cmd_settings(update, context)


async def cmd_quiet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    parts = update.message.text.split()
    if len(parts) != 3 or not all(valid_time(x) for x in parts[1:]):
        await update.message.reply_text("Use /quiet 22:00 08:00 (24-hour local time).")
        return
    set_setting("quiet_start", parts[1])
    set_setting("quiet_end", parts[2])
    await cmd_settings(update, context)


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    value = update.message.text.partition(" ")[2].strip().lower()
    if value not in {"on", "off"}:
        await update.message.reply_text("Use /reminders on or /reminders off.")
        return
    set_setting("reminders", value)
    await cmd_settings(update, context)


async def on_collecting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    kind = context.user_data.get("collecting")
    if not kind:
        return False
    context.user_data.pop("collecting", None)
    if kind == "word":
        await save_unknown(update.message.reply_text, update.message.text)
    elif kind == "lesson":
        await save_lesson_note(update.message.reply_text, update.message.text)
    elif kind == "lesson_import":
        await do_lesson_import(update.message.reply_text, update.message.text, context)
    return True


async def on_memory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not allowed(update):
        await query.answer()
        return
    await query.answer()
    parts = query.data.split("|")
    action = parts[1]
    if action == "home":
        await query.edit_message_text(section_text(), reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
    elif action == "unknown":
        context.user_data["collecting"] = "word"
        await query.edit_message_text("📥 Send unknown words separated by commas or new lines. Use /unknownprompt to get the LLM prompt.", reply_markup=memory_keyboard())
    elif action == "lessons":
        context.user_data["collecting"] = "lesson"
        await query.edit_message_text("📖 Send a lesson note or fact. Use /lessonprompt to get the LLM prompt.", reply_markup=memory_keyboard())
    elif action == "settings":
        await query.edit_message_text(settings_text(), reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
    elif action == "word_prompt":
        await send_prompt(query.message.reply_text, "word", context)
    elif action == "lesson_prompt":
        await send_prompt(query.message.reply_text, "lesson", context)
    elif action == "review":
        await send_lesson_card(query.edit_message_text)
    elif action in {"reveal", "rate"} and len(parts) >= 3:
        try:
            card_id = int(parts[2])
        except ValueError:
            return
        card = lesson_by_id(card_id)
        if not card:
            await query.edit_message_text("That card no longer exists.", reply_markup=memory_keyboard())
            return
        if action == "reveal":
            await query.edit_message_text(
                f"📖 <b>{esc(card['topic'])}</b>\n\n{esc(card['question'])}\n\n✅ <b>{esc(card['answer'])}</b>" +
                (f"\n\n💡 {esc(card['hint'])}" if card['hint'] else ""),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Again", callback_data=f"memory|rate|{card_id}|again"), InlineKeyboardButton("✅ Remembered", callback_data=f"memory|rate|{card_id}|good")]]),
                parse_mode=ParseMode.HTML,
            )
        elif len(parts) == 4 and parts[3] in {"again", "good"}:
            if rate_lesson(card_id, parts[3]):
                await send_lesson_card(query.edit_message_text)
            else:
                await query.edit_message_text("This card was already reviewed. Choose another.", reply_markup=memory_keyboard())


async def reminder_loop(app):
    """Poll preferences and due counts; persist one send per local review slot."""
    while True:
        try:
            prefs = settings()
            slot = reminder_slot(datetime.now(timezone.utc), prefs)
            if slot:
                words, lessons = due_counts()
                if words or lessons:
                    text = f"🧠 Time to review: {words} words and {lessons} lesson cards are due. Try a short session of up to 20 cards."
                    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Review words", callback_data="menu|review"), InlineKeyboardButton("Review lessons", callback_data="memory|review")]])
                    await app.bot.send_message(chat_id=int(ALLOWED_USER_ID), text=text, reply_markup=keyboard)
                    set_setting("last_reminder_slot", slot)
        except Exception:
            log.exception("Reminder check failed")
        await asyncio.sleep(60)
