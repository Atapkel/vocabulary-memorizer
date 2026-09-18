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
from .guides import lesson_guide_text, word_guide_text
from .memory import (
    collect, delete_unknown, delete_unknown_by_id, due_counts, due_lesson,
    import_lessons, inbox_count, inbox_items, unknown_words,
    lesson_by_id, lesson_prompt, rate_lesson, reminder_slot, remove_inbox,
    set_setting, settings, valid_time, word_prompt,
)
from .views import esc, esc_limit
from .telegram_ui import edit_text

log = logging.getLogger(__name__)


def allowed(update: Update) -> bool:
    return str(update.effective_user.id) == str(ALLOWED_USER_ID)


def memory_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Saved words", callback_data="memory|word_list"),
         InlineKeyboardButton("📖 Add note", callback_data="memory|lessons")],
        [InlineKeyboardButton("📝 Word prompt", callback_data="memory|word_prompt"),
         InlineKeyboardButton("📝 Lesson prompt", callback_data="memory|lesson_prompt")],
        [InlineKeyboardButton("📋 Word JSON guide", callback_data="memory|word_guide"),
         InlineKeyboardButton("📋 Lesson JSON guide", callback_data="memory|lesson_guide")],
        [InlineKeyboardButton("📥 Import words", callback_data="menu|add"),
         InlineKeyboardButton("📥 Import lessons", callback_data="memory|lesson_import")],
        [InlineKeyboardButton("📚 Word library", callback_data="menu|list"),
         InlineKeyboardButton("🎮 Games", callback_data="menu|game")],
        [InlineKeyboardButton("🪲 Difficult words", callback_data="menu|leeches"),
         InlineKeyboardButton("⏰ Reminders", callback_data="memory|settings")],
        [InlineKeyboardButton("🏠 Main menu", callback_data="menu|home")],
    ])


def section_text() -> str:
    words, lessons = due_counts()
    return ("🧠 <b>WORDS &amp; LESSONS</b>\n"
            "<i>Capture now, remember later.</i>\n\n"
            "📥 <b>Save a word</b>\n"
            "Send a word, phrase, or comma-separated list right here.\n\n"
            "📖 <b>Save a lesson</b>\n"
            "Tap <i>Add a lesson note</i> and send a fact or explanation.\n\n"
            "📝 <b>Make review cards</b>\n"
            "Get an LLM prompt, check the JSON answer, then import it.\n\n"
            f"📦 Saved: <b>{inbox_count('word')}</b> words · <b>{inbox_count('lesson')}</b> notes\n"
            f"🔥 Due: <b>{words}</b> words · <b>{lessons}</b> lessons")


def unknown_list_view():
    rows = unknown_words()
    total = inbox_count("word")
    if not rows:
        return "📥 <b>UNKNOWN WORDS</b>\n\n🌱 Nothing saved yet. Send a word in this chat to add it.", memory_keyboard()
    lines = [f"{index}. <b>{esc_limit(row['content'], 130)}</b>" for index, row in enumerate(rows, 1)]
    keyboard = [[InlineKeyboardButton(f"🗑 {row['content'][:35]}", callback_data=f"memory|delete|{row['id']}")]
                for row in rows]
    keyboard.append([InlineKeyboardButton("📝 Get LLM prompt", callback_data="memory|word_prompt")])
    keyboard.append([InlineKeyboardButton("📋 Word JSON guide", callback_data="memory|word_guide")])
    keyboard.append([InlineKeyboardButton("📥 Import word cards", callback_data="menu|add")])
    keyboard.append([InlineKeyboardButton("⬅️ Words & lessons", callback_data="memory|home")])
    return (f"📥 <b>UNKNOWN WORDS</b>\n<i>{total} saved · showing the newest {len(rows)}</i>\n\n"
            + "\n".join(lines)
            + "\n\n🗑 Tap a word below to remove it, or use <code>/delete word</code>.\n"
              "<i>Imported study cards stay in your library.</i>",
            InlineKeyboardMarkup(keyboard))


async def cmd_unknowns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        text, keyboard = unknown_list_view()
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    word = update.message.text.partition(" ")[2].strip()
    if not word:
        await update.message.reply_text("🗑 <b>REMOVE A SAVED WORD</b>\n\nSend <code>/delete word</code> or <code>/delete take off</code>.", parse_mode=ParseMode.HTML)
        return
    removed = delete_unknown(word)
    result = (f"✅ <b>REMOVED FROM INBOX</b>\n\n🗑 {esc(word)}\n<i>{removed} saved entry removed. Imported cards are unchanged.</i>"
              if removed else f"🔎 <b>WORD NOT FOUND</b>\n\nNo saved unknown word matches <b>{esc(word)}</b>.")
    await update.message.reply_text(result, reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await update.message.reply_text(section_text(), reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)


def guide_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔤 Word JSON", callback_data="memory|word_guide"),
         InlineKeyboardButton("📖 Lesson JSON", callback_data="memory|lesson_guide")],
        [InlineKeyboardButton("📥 Import words", callback_data="menu|add"),
         InlineKeyboardButton("📥 Import lessons", callback_data="memory|lesson_import")],
        [InlineKeyboardButton("🏠 Main menu", callback_data="menu|home")],
    ])


async def cmd_jsonhelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    kind = update.message.text.partition(" ")[2].strip().lower()
    if kind == "word":
        text = word_guide_text()
    elif kind == "lesson":
        text = lesson_guide_text()
    else:
        text = "📋 <b>JSON FORMAT GUIDES</b>\n\nChoose the card type you want to create. Each guide includes a copyable example and import steps."
    await update.message.reply_text(text, reply_markup=guide_keyboard(), parse_mode=ParseMode.HTML)


async def cmd_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    raw = update.message.text.partition(" ")[2]
    if not raw.strip():
        context.user_data["collecting"] = "word"
        await update.message.reply_text("📥 <b>SAVE UNKNOWN WORDS</b>\n\nSend one word, a phrase, or a comma-separated list.\n\n<i>Example: curious, take off, удивление</i>", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
        return
    await save_unknown(update.message.reply_text, raw)


async def save_unknown(reply, raw: str):
    entries = [" ".join(part.split()) for part in re.split(r"[,;\n]+", raw)]
    words = list(dict.fromkeys(part for part in entries if part))
    if len(words) > 100:
        await reply("⚠️ <b>TOO MANY WORDS</b>\n\nSend up to 100 in one message. You can send several messages.", parse_mode=ParseMode.HTML)
        return
    if any(len(word) > 80 or len(word.split()) > 6 for word in words):
        await reply("⚠️ <b>PHRASE TOO LONG</b>\n\nKeep each entry under 80 characters and six words.", parse_mode=ParseMode.HTML)
        return
    if not words:
        await reply("🔎 <b>NO WORDS FOUND</b>\n\nSeparate words with commas or new lines.", parse_mode=ParseMode.HTML)
        return
    added, _ = collect("word", words)
    await reply(f"✅ <b>WORDS SAVED</b>\n\n📥 New: <b>{added}</b>\n📦 In your inbox: <b>{inbox_count('word')}</b>\n\n<i>Send more anytime, or make study cards with the prompt below.</i>", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)


async def cmd_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        context.user_data["collecting"] = "lesson"
        await update.message.reply_text("📖 <b>ADD A LESSON NOTE</b>\n\nSend a fact, explanation, or passage you want to remember.\n\n<i>Up to 1,500 characters per message.</i>", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
        return
    await save_lesson_note(update.message.reply_text, raw)


async def save_lesson_note(reply, raw: str):
    note = raw.strip()
    if len(note) > 1500:
        await reply("⚠️ <b>NOTE TOO LONG</b>\n\nSplit it into messages under 1,500 characters.", parse_mode=ParseMode.HTML)
        return
    added, _ = collect("lesson", [note])
    await reply(f"✅ <b>LESSON NOTE SAVED</b>\n\n📖 New: <b>{added}</b>\n📦 In your inbox: <b>{inbox_count('lesson')}</b>\n\n<i>Make review cards when you’re ready.</i>", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)


async def send_prompt(reply, kind: str, context: ContextTypes.DEFAULT_TYPE):
    notes = inbox_items(kind, 10 if kind == "lesson" else 15)
    if not notes:
        await reply("🌱 <b>INBOX IS EMPTY</b>\n\nSend a word or lesson note first, then make a prompt.", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
        return
    while notes:
        prompt = word_prompt(notes) if kind == "word" else lesson_prompt(notes)
        if len(prompt) <= 3800:
            break
        notes.pop()
    command = "/add" if kind == "word" else "/lessonimport"
    if not notes:
        await reply("⚠️ <b>PROMPT TOO LONG</b>\n\nShorten the note so the prompt fits in a Telegram message.", parse_mode=ParseMode.HTML)
        return
    context.user_data[f"prompted_{kind}"] = notes
    subject = "WORD" if kind == "word" else "LESSON"
    await reply(
        f"📝 <b>{subject} CARD PROMPT</b>\n\n"
        "1️⃣ Copy the next message into an LLM.\n"
        "2️⃣ Check the facts in its JSON answer.\n"
        f"3️⃣ Send <code>{command}</code>, then paste that JSON.\n\n"
        "<i>The next message is plain text so it’s easy to copy.</i>",
        parse_mode=ParseMode.HTML,
    )
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
        await update.message.reply_text("📥 <b>IMPORT LESSON CARDS</b>\n\nPaste the LLM’s JSON array in your next message. I’ll check it before saving the cards.", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
    else:
        await do_lesson_import(update.message.reply_text, raw, context)


async def do_lesson_import(reply, raw: str, context: ContextTypes.DEFAULT_TYPE):
    try:
        payload = json.loads(raw)
        added, skipped, errors = import_lessons(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        await reply(f"⚠️ <b>LESSON IMPORT FAILED</b>\n\n{esc(exc)}\n\n<i>Check the JSON and try /lessonimport again.</i>", parse_mode=ParseMode.HTML)
        return
    if added:
        remove_inbox("lesson", context.user_data.pop("prompted_lesson", []))
    details = "\n\n⚠️ <b>Things to check</b>\n" + "\n".join(esc(e) for e in errors) if errors else ""
    await reply(f"✅ <b>LESSON CARDS IMPORTED</b>\n\n🌱 Added  <b>{added}</b>\n↪️ Skipped  <b>{skipped}</b>{details}", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)


def lesson_front_text(card: dict) -> str:
    return (
        f"📖 <b>LESSON REVIEW</b>\n<i>{esc_limit(card['topic'], 400)}</i>\n\n"
        f"❓ <b>{esc_limit(card['question'], 2800)}</b>\n\n"
        "💭 <i>Answer from memory before revealing the answer.</i>"
    )


def lesson_back_text(card: dict) -> str:
    return (
        f"✅ <b>LESSON ANSWER</b>\n<i>{esc_limit(card['topic'], 300)}</i>\n\n"
        f"❓ {esc_limit(card['question'], 750)}\n\n"
        f"🎯 <b>{esc_limit(card['answer'], 2450)}</b>"
        + (f"\n\n💡 <i>{esc_limit(card['hint'], 250)}</i>" if card['hint'] else "")
        + "\n\n<i>How well did you remember it?</i>"
    )


async def send_lesson_card(reply):
    card = due_lesson()
    if not card:
        await reply("🎉 <b>LESSONS CAUGHT UP</b>\n\n✨ No lesson cards are due right now. Check back later.", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
        return
    await reply(
        lesson_front_text(card),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👀 Reveal answer", callback_data=f"memory|reveal|{card['id']}")], [InlineKeyboardButton("🏠 Main menu", callback_data="menu|home")]]),
        parse_mode=ParseMode.HTML,
    )


async def cmd_lessons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if allowed(update):
        await send_lesson_card(update.message.reply_text)


def settings_text() -> str:
    p = settings()
    status = "🟢 On" if p["reminders"] == "on" else "⚪ Off"
    return ("⏰ <b>REMINDER SETTINGS</b>\n\n"
            f"🔔 <b>Status:</b> {status}\n"
            f"🌍 <b>Time zone:</b> <code>{esc(p['timezone'])}</code>\n"
            f"🌙 <b>Quiet hours:</b> {esc(p['quiet_start'])}–{esc(p['quiet_end'])}\n\n"
            "<b>Review checks</b>\n09:00 · 14:00 · 19:00 in your local time\n"
            "<i>You’ll hear from me only when cards are due, outside quiet hours.</i>\n\n"
            "<b>Change your settings</b>\n"
            "<code>/timezone Asia/Qyzylorda</code>\n"
            "<code>/quiet 22:00 08:00</code>\n"
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
        await update.message.reply_text("🌍 <b>TIME ZONE NOT FOUND</b>\n\nUse a region and city, such as <code>/timezone Asia/Qyzylorda</code>.", parse_mode=ParseMode.HTML)
        return
    set_setting("timezone", value)
    await cmd_settings(update, context)


async def cmd_quiet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    parts = update.message.text.split()
    if len(parts) != 3 or not all(valid_time(x) for x in parts[1:]):
        await update.message.reply_text("🌙 <b>SET QUIET HOURS</b>\n\nUse 24-hour local times: <code>/quiet 22:00 08:00</code>.", parse_mode=ParseMode.HTML)
        return
    set_setting("quiet_start", parts[1])
    set_setting("quiet_end", parts[2])
    await cmd_settings(update, context)


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    value = update.message.text.partition(" ")[2].strip().lower()
    if value not in {"on", "off"}:
        await update.message.reply_text("🔔 <b>REMINDER STATUS</b>\n\nSend <code>/reminders on</code> or <code>/reminders off</code>.", parse_mode=ParseMode.HTML)
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
    async def edit(text, reply_markup=None, parse_mode=None):
        return await edit_text(query, text, reply_markup=reply_markup, parse_mode=parse_mode)

    parts = query.data.split("|")
    action = parts[1]
    if action == "home":
        await edit(section_text(), reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
    elif action == "guides":
        await edit("📋 <b>JSON FORMAT GUIDES</b>\n\nChoose the card type you want to create. Each guide includes a copyable example and import steps.", reply_markup=guide_keyboard(), parse_mode=ParseMode.HTML)
    elif action == "word_guide":
        await edit(word_guide_text(), reply_markup=guide_keyboard(), parse_mode=ParseMode.HTML)
    elif action == "lesson_guide":
        await edit(lesson_guide_text(), reply_markup=guide_keyboard(), parse_mode=ParseMode.HTML)
    elif action == "word_list":
        text, keyboard = unknown_list_view()
        await edit(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    elif action == "delete" and len(parts) == 3:
        try:
            item_id = int(parts[2])
        except ValueError:
            return
        if not delete_unknown_by_id(item_id):
            return
        text, keyboard = unknown_list_view()
        await edit(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    elif action == "unknown":
        context.user_data["collecting"] = "word"
        await edit("📥 <b>SAVE UNKNOWN WORDS</b>\n\nSend a word, phrase, or comma-separated list in your next message.\n\n<i>Example: curious, take off, удивление</i>", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
    elif action == "lessons":
        context.user_data["collecting"] = "lesson"
        await edit("📖 <b>ADD A LESSON NOTE</b>\n\nSend a fact, explanation, or passage in your next message.\n\n<i>Up to 1,500 characters.</i>", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
    elif action == "settings":
        await edit(settings_text(), reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
    elif action == "word_prompt":
        await send_prompt(query.message.reply_text, "word", context)
    elif action == "lesson_prompt":
        await send_prompt(query.message.reply_text, "lesson", context)
    elif action == "lesson_import":
        context.user_data["collecting"] = "lesson_import"
        await edit("📥 <b>IMPORT LESSON CARDS</b>\n\nPaste the lesson JSON array in your next message. I’ll check it before saving the cards.", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
    elif action == "review":
        await send_lesson_card(edit)
    elif action in {"reveal", "rate"} and len(parts) >= 3:
        try:
            card_id = int(parts[2])
        except ValueError:
            return
        card = lesson_by_id(card_id)
        if not card:
            await edit("🔎 <b>CARD NOT FOUND</b>\n\nChoose another lesson card.", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)
            return
        if action == "reveal":
            await edit(
                lesson_back_text(card),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Study again", callback_data=f"memory|rate|{card_id}|again"), InlineKeyboardButton("✅ Remembered", callback_data=f"memory|rate|{card_id}|good")]]),
                parse_mode=ParseMode.HTML,
            )
        elif len(parts) == 4 and parts[3] in {"again", "good"}:
            if rate_lesson(card_id, parts[3]):
                await send_lesson_card(edit)
            else:
                await edit("🔄 <b>ALREADY REVIEWED</b>\n\nChoose another card when it’s due.", reply_markup=memory_keyboard(), parse_mode=ParseMode.HTML)


async def reminder_loop(app):
    """Poll preferences and due counts; persist one send per local review slot."""
    while True:
        try:
            prefs = settings()
            slot = reminder_slot(datetime.now(timezone.utc), prefs)
            if slot:
                words, lessons = due_counts()
                if words or lessons:
                    text = ("🧠 <b>A LITTLE REVIEW TIME</b>\n\n"
                            f"🔥 <b>{words}</b> word cards ready\n"
                            f"📖 <b>{lessons}</b> lesson cards ready\n\n"
                            "<i>A short session of up to 20 cards is enough for now.</i>")
                    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🧠 Review words", callback_data="menu|review"), InlineKeyboardButton("📖 Review lessons", callback_data="memory|review")]])
                    await app.bot.send_message(chat_id=int(ALLOWED_USER_ID), text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
                    set_setting("last_reminder_slot", slot)
        except Exception:
            log.exception("Reminder check failed")
        await asyncio.sleep(60)
