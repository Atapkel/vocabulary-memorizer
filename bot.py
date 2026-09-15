"""
Word Box — a personal Telegram vocabulary trainer with spaced repetition (SM-2 style),
backed by SQLite. Single-user by design (locked to one Telegram user id).

Setup:
    pip install python-telegram-bot --upgrade
    export BOT_TOKEN="123456:ABC-your-bot-token"
    export ALLOWED_USER_ID="123456789"
    python3 vocab_bot.py

Get BOT_TOKEN from @BotFather on Telegram.
Get your ALLOWED_USER_ID by messaging @userinfobot on Telegram.
"""

import os
import sys
import json
import html
import re
import sqlite3
import time
import logging
from difflib import SequenceMatcher
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

load_dotenv()


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = os.getenv("VOCAB_DB_PATH", "vocab.db")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")

LEARNING_STEPS_MIN = [1, 10]  # minutes, same shape as the web version
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_CARDS = 500
MAX_CONTEXT = 500
MAX_WORD = 80
PRIORITIES = {"high", "normal", "low"}
LEECH_THRESHOLD = 8
ANSWER_MATCH_THRESHOLD = 0.82


# ---------------------------------------------------------------- storage --

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id TEXT PRIMARY KEY,
            word TEXT NOT NULL,
            context TEXT,
            translation TEXT,
            explanation TEXT,
            pos TEXT,
            synonyms TEXT,
            example TEXT,
            created_at INTEGER,
            priority TEXT DEFAULT 'normal'
        )
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(words)")}
    if "target_language" not in columns:
        conn.execute("ALTER TABLE words ADD COLUMN target_language TEXT DEFAULT 'english'")
    if "pronunciation" not in columns:
        conn.execute("ALTER TABLE words ADD COLUMN pronunciation TEXT")
    if "priority" not in columns:
        conn.execute("ALTER TABLE words ADD COLUMN priority TEXT DEFAULT 'normal'")
    if "note" not in columns:
        conn.execute("ALTER TABLE words ADD COLUMN note TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            word_id TEXT PRIMARY KEY REFERENCES words(id) ON DELETE CASCADE,
            state TEXT DEFAULT 'new',
            step INTEGER DEFAULT 0,
            interval INTEGER DEFAULT 0,
            ef REAL DEFAULT 2.5,
            due INTEGER,
            reps INTEGER DEFAULT 0,
            lapses INTEGER DEFAULT 0
        )
    """)
    review_columns = {row["name"] for row in conn.execute("PRAGMA table_info(reviews)")}
    if "direction" not in review_columns:
        conn.execute("ALTER TABLE reviews ADD COLUMN direction TEXT DEFAULT 'recognition'")
    conn.commit()
    conn.close()


def add_words(word_list):
    conn = get_conn()
    added, skipped = 0, 0
    now = int(time.time())
    errors = []
    for index, w in enumerate(word_list):
        if not isinstance(w, dict):
            skipped += 1
            errors.append(f"#{index + 1}: card is not an object")
            continue
        wid = w.get("id")
        word = w.get("word")
        if not isinstance(word, str) or not word.strip() or len(word.strip()) > MAX_WORD or len(word.split()) > 6:
            skipped += 1
            errors.append(f"#{index + 1}: invalid word or phrase")
            continue
        if not isinstance(w.get("translation"), str) or not w["translation"].strip():
            skipped += 1
            errors.append(f"#{index + 1}: Kazakh translation is required")
            continue
        word = " ".join(word.split())
        wid = wid.strip()[:80] if isinstance(wid, str) and wid.strip() else "t" + __import__("uuid").uuid4().hex[:12]
        lang = str(w.get("target_language") or "").lower()
        if lang not in ("english", "russian"):
            lang = "russian" if any(("а" <= c.lower() <= "я") or c.lower() == "ё" for c in word) else "english"
        context_text = " ".join(str(w.get("context") or "").split())[:MAX_CONTEXT]
        priority = str(w.get("priority") or "normal").lower().strip()
        if priority not in PRIORITIES:
            priority = "normal"
        exists = conn.execute(
            """SELECT 1 FROM words WHERE id=? OR
               (lower(trim(word))=lower(trim(?)) AND target_language=? AND context=?)""",
            (wid, word, lang, context_text),
        ).fetchone()
        if exists:
            skipped += 1
            continue
        conn.execute(
            """INSERT INTO words (id, word, context, translation, explanation, pos, synonyms, example,
               created_at, target_language, pronunciation, priority, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (wid, word, context_text, str(w.get("translation") or "")[:300],
             str(w.get("explanation") or "")[:800], "",
             str(w.get("synonyms") or "")[:300], str(w.get("example") or "")[:300],
             w.get("created_at") if isinstance(w.get("created_at"), (int, float)) else now,
             lang, "", priority, str(w.get("note") or "")[:1000]),
        )
        conn.execute(
            """INSERT INTO reviews (word_id, state, step, interval, ef, due, reps, lapses)
               VALUES (?, 'new', 0, 0, 2.5, ?, 0, 0)""",
            (wid, now),
        )
        added += 1
    conn.commit()
    conn.close()
    return added, skipped, errors[:10]


def get_due_words(limit=1):
    conn = get_conn()
    now = int(time.time())
    rows = conn.execute(
        """SELECT w.*, r.state, r.step, r.interval, r.ef, r.due, r.reps, r.lapses, r.direction
           FROM words w JOIN reviews r ON w.id = r.word_id
           WHERE r.state != 'suspended' AND r.due <= ?
           ORDER BY CASE w.priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, r.due ASC LIMIT ?""",
        (now, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_word_with_review(word_id):
    conn = get_conn()
    row = conn.execute(
        """SELECT w.*, r.state, r.step, r.interval, r.ef, r.due, r.reps, r.lapses, r.direction
           FROM words w JOIN reviews r ON w.id = r.word_id WHERE w.id=?""",
        (word_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_game_cards(limit=4):
    """Random active cards for play; games deliberately do not change SRS data."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT w.*, r.state, r.direction FROM words w JOIN reviews r ON w.id=r.word_id
           WHERE r.state != 'suspended' AND w.translation != ''
           ORDER BY RANDOM() LIMIT ?""", (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_review(word_id, r):
    conn = get_conn()
    conn.execute(
        """UPDATE reviews SET state=?, step=?, interval=?, ef=?, due=?, reps=?, lapses=?, direction=?
           WHERE word_id=?""",
        (r["state"], r["step"], r["interval"], r["ef"], r["due"], r["reps"], r["lapses"],
         r.get("direction", "recognition"), word_id),
    )
    conn.commit()
    conn.close()


def get_counts():
    conn = get_conn()
    now = int(time.time())
    total = conn.execute("SELECT COUNT(*) c FROM words").fetchone()["c"]
    newc = conn.execute("SELECT COUNT(*) c FROM reviews WHERE state='new'").fetchone()["c"]
    learning = conn.execute("SELECT COUNT(*) c FROM reviews WHERE state='learning'").fetchone()["c"]
    review = conn.execute("SELECT COUNT(*) c FROM reviews WHERE state='review'").fetchone()["c"]
    due_now = conn.execute("SELECT COUNT(*) c FROM reviews WHERE state != 'suspended' AND due<=?", (now,)).fetchone()["c"]
    suspended = conn.execute("SELECT COUNT(*) c FROM reviews WHERE state='suspended'").fetchone()["c"]
    conn.close()
    return dict(total=total, new=newc, learning=learning, review=review, due_now=due_now, suspended=suspended)


def next_due_timestamp():
    conn = get_conn()
    row = conn.execute("SELECT MIN(due) d FROM reviews WHERE state != 'suspended' AND due > ?", (int(time.time()),)).fetchone()
    conn.close()
    return row["d"] if row and row["d"] else None


def list_words(limit=30):
    conn = get_conn()
    rows = conn.execute(
        """SELECT w.word, w.priority, r.state, r.due FROM words w JOIN reviews r ON w.id=r.word_id
           ORDER BY r.due ASC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_leeches(limit=30):
    conn = get_conn()
    rows = conn.execute(
        """SELECT w.id, w.word, w.translation, r.lapses FROM words w
           JOIN reviews r ON w.id=r.word_id WHERE r.state='suspended'
           ORDER BY r.lapses DESC, w.word ASC LIMIT ?""", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_note(word_id, note):
    conn = get_conn()
    changed = conn.execute("UPDATE words SET note=? WHERE id=?", (note[:1000], word_id)).rowcount
    conn.commit()
    conn.close()
    return bool(changed)


def reset_word(word_id):
    conn = get_conn()
    changed = conn.execute(
        """UPDATE reviews SET state='new', step=0, interval=0, ef=2.5, due=?, reps=0,
           lapses=0, direction='recognition' WHERE word_id=?""", (int(time.time()), word_id)
    ).rowcount
    conn.commit()
    conn.close()
    return bool(changed)


# ------------------------------------------ automatic binary review engine --

def rate(review, rating):
    """Schedule from the learner's simple Know / Don't know answer."""
    rating = {"know": "good", "dont_know": "again"}.get(rating, rating)
    if rating not in {"again", "good"}:
        raise ValueError("Invalid review answer")
    now = int(time.time())
    r = dict(review)
    if r["state"] in ("new", "learning"):
        if rating == "again":
            r["state"] = "learning"; r["step"] = 0; r["lapses"] += 1; r["direction"] = "recognition"
            r["due"] = now + LEARNING_STEPS_MIN[0] * 60
        elif rating == "good":
            next_step = r["step"] + 1
            if next_step >= len(LEARNING_STEPS_MIN):
                r["state"] = "review"; r["interval"] = 1; r["reps"] = 1
                r["due"] = now + 1 * 86400
            else:
                r["state"] = "learning"; r["step"] = next_step
                r["due"] = now + LEARNING_STEPS_MIN[next_step] * 60
    else:
        qmap = {"again": 2, "good": 4}
        q = qmap[rating]
        if q < 3:
            r["lapses"] += 1; r["reps"] = 0; r["state"] = "learning"; r["step"] = 0; r["direction"] = "recognition"
            r["ef"] = max(1.3, r["ef"] - 0.2)
            r["due"] = now + LEARNING_STEPS_MIN[0] * 60
        else:
            r["reps"] += 1
            if r["reps"] == 1:
                r["interval"] = 1
            elif r["reps"] == 2:
                r["interval"] = 6
            else:
                r["interval"] = round(r["interval"] * r["ef"])
            if r.get("priority") == "high":
                r["interval"] = max(1, round(r["interval"] * 0.7))
            elif r.get("priority") == "low":
                r["interval"] = max(1, round(r["interval"] * 1.25))
            r["ef"] = max(1.3, r["ef"] + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))
            r["due"] = now + r["interval"] * 86400
            # Once recognition is established, alternate recall directions.
            if r["reps"] >= 2:
                r["direction"] = "production" if r.get("direction") == "recognition" else "recognition"
    if r["lapses"] >= LEECH_THRESHOLD:
        r["state"] = "suspended"
        r["due"] = None
    return r


# --------------------------------------------------------------- helpers --

def esc(s):
    return html.escape(str(s)) if s else ""


def highlight_context(context, word):
    if not context:
        return ""
    context = str(context)
    if len(context) > MAX_CONTEXT:
        context = context[:MAX_CONTEXT - 2].rstrip() + " …"
    idx = context.lower().find(word.lower())
    if idx == -1:
        return esc(context)
    before, match, after = context[:idx], context[idx:idx+len(word)], context[idx+len(word):]
    return f"{esc(before)}<b>{esc(match)}</b>{esc(after)}"


def card_front_text(w):
    flag = "🇷🇺" if w.get("target_language") == "russian" else "🇬🇧"
    priority = "🔥 <b>HIGH PRIORITY</b>\n" if w.get("priority") == "high" else ""
    production = w.get("direction") == "production"
    prompt = w.get("translation") if production else w.get("word")
    prompt_label = "Kazakh meaning" if production else "Word"
    expected = "the foreign word" if production else "the Kazakh meaning"
    context = "" if production else f"💬 {highlight_context(w.get('context'), w['word'])}\n\n"
    return (
        f"🧠 <b>REVIEW</b> · {flag} <i>{esc(w['state'])}</i>\n{priority}\n"
        f"<b>{esc(prompt)}</b>\n<i>{prompt_label}</i>\n\n"
        f"{context}<i>Type {expected} from memory.</i>"
    )


def card_back_text(w):
    flag = "🇷🇺" if w.get("target_language") == "russian" else "🇬🇧"
    lines = [f"🧠 <b>ANSWER</b> · {flag}", "", f"<b>{esc(w['word'])}</b>", ""]
    if w.get("translation"):
        lines.append(f"🇰🇿 <b>Kazakh meaning</b>\n{esc(w['translation'])}")
    if w.get("explanation"):
        lines.append(f"\n💡 <b>Simple definition</b>\n{esc(w['explanation'])}")
    if w.get("context"):
        lines.append(f"\n💬 <b>Original context</b>\n{highlight_context(w['context'], w['word'])}")
    if w.get("example"):
        lines.append(f"\n✍️ <b>New example</b>\n{esc(w['example'])}")
    if w.get("synonyms"):
        lines.append(f"\n🔗 <b>Related words</b>\n{esc(w['synonyms'])}")
    if w.get("note"):
        lines.append(f"\n📝 <b>My note</b>\n{esc(w['note'])}")
    return "\n".join(lines)[:3900]


def rating_keyboard(word_id, review):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ I don’t know", callback_data=f"rate|{word_id}|dont_know"),
         InlineKeyboardButton("✅ I know", callback_data=f"rate|{word_id}|know")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu|home")],
    ])


def show_answer_keyboard(word_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⌨️ Type answer", callback_data=f"show|{word_id}")],
        [InlineKeyboardButton("🏠 Menu", callback_data="menu|home")],
    ])


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Start review", callback_data="menu|review"),
         InlineKeyboardButton("📚 Library", callback_data="menu|list")],
        [InlineKeyboardButton("📊 Progress", callback_data="menu|stats"),
         InlineKeyboardButton("➕ Import", callback_data="menu|add")],
        [InlineKeyboardButton("🪲 Leeches", callback_data="menu|leeches")],
        [InlineKeyboardButton("🎮 Word games", callback_data="menu|game")],
    ])


def dashboard_text():
    c = get_counts()
    return (
        "🧠 <b>Word Box</b>\n"
        "<i>English &amp; Russian vocabulary through Kazakh</i>\n\n"
        f"🔥 Due now: <b>{c['due_now']}</b>\n"
        f"🌱 New: <b>{c['new']}</b>\n"
        f"🧩 Learning: <b>{c['learning']}</b>\n"
        f"🌳 Mature: <b>{c['review']}</b>\n"
        f"🪲 Suspended: <b>{c['suspended']}</b>\n\n"
        "<i>Choose what you want to do.</i>"
    )


def word_list_from_text(raw):
    items = []
    for item in re.split(r"[,;\n]+", raw):
        item = " ".join(item.strip().split())
        if item and len(item) <= MAX_WORD and len(item.split()) <= 6:
            items.append(item)
    return list(dict.fromkeys(items))[:15]


def normalize_answer(value):
    """Compare answers forgivingly while preserving the correct answer for grading."""
    value = str(value).casefold().replace("ё", "е")
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def answer_feedback(answer, w):
    expected = w["word"] if w.get("direction") == "production" else w["translation"]
    typed = normalize_answer(answer)
    accepted = [normalize_answer(part) for part in re.split(r"[;,/]|\bor\b", expected, flags=re.IGNORECASE)]
    similarity = max((SequenceMatcher(None, typed, candidate).ratio() for candidate in accepted if candidate), default=0)
    verdict = "✅ <b>Looks correct</b>" if similarity >= ANSWER_MATCH_THRESHOLD else "🔎 <b>Check your answer</b>"
    direction_label = "foreign word" if w.get("direction") == "production" else "Kazakh meaning"
    return (
        f"{verdict}\n\n"
        f"✍️ <b>Your answer</b>\n{esc(answer)}\n\n"
        f"✅ <b>Correct {direction_label}</b>\n{esc(expected)}\n\n"
        f"<i>Grade your recall honestly, then continue.</i>\n\n"
        f"{card_back_text(w)}"
    )[:3900]


def game_round(cards, round_number):
    """Create either a meaning match or a context-based find-the-word round."""
    target = cards[0]
    choices = cards[:]
    # Alternate modes so a short game practises both meanings and contextual use.
    mode = "find" if round_number % 2 and target.get("context") else "match"
    if mode == "find":
        blank = re.sub(re.escape(target["word"]), "_____", target["context"], count=1, flags=re.IGNORECASE)
        prompt = (
            "🔎 <b>Find the word</b>\n\n"
            f"{esc(blank)}\n\n"
            f"🇰🇿 Hint: <b>{esc(target['translation'])}</b>\n\n"
            "Which word completes the sentence?"
        )
        label = lambda card: card["word"]
    else:
        prompt = (
            "🧩 <b>Quick match</b>\n\n"
            f"What is the Kazakh meaning of <b>{esc(target['word'])}</b>?"
        )
        label = lambda card: card["translation"]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(str(label(card))[:60], callback_data=f"game|{target['id']}|{card['id']}")]
        for card in choices
    ] + [[InlineKeyboardButton("🏠 Finish game", callback_data="menu|home")]])
    return target, prompt, keyboard


def chatgpt_prompt(words):
    payload = []
    for word in words:
        lang = "russian" if any(("а" <= char.lower() <= "я") or char.lower() == "ё" for char in word) else "english"
        payload.append({"word": word, "target_language": lang})
    return (
        "Create vocabulary cards for a native Kazakh speaker intensively learning English and Russian.\n\n"
        "For every word or phrase: translate its most useful meaning into natural Kazakh; write a simple "
        "one-sentence definition in the target language, one short natural example, and up to two useful synonyms.\n\n"
        "Set priority to high for core, very frequent, or especially practical words; normal for useful everyday "
        "words; low only for rare or narrowly specialised words. Use frequency and usefulness, not word length.\n\n"
        "Reply with ONLY a valid JSON array using exactly these fields:\n"
        '[{"word":"...","target_language":"english or russian","context":"","translation":"Kazakh meaning",'
        '"explanation":"simple target-language definition",'
        '"example":"...","synonyms":"...","priority":"high, normal, or low"}]\n\n'
        "Words:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    )


async def send_next_card(message_edit_target):
    """message_edit_target: an Update's message or a CallbackQuery — must support edit/reply."""
    due = get_due_words(limit=1)
    if not due:
        nxt = next_due_timestamp()
        if nxt:
            text = "✅ <b>All caught up</b>\n\n<i>I’ll bring cards back automatically when reviewing them becomes useful.</i>"
        else:
            text = "No words yet — send me a .json file or use /add to import your list."
        await message_edit_target(text, reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)
        return
    w = due[0]
    await message_edit_target(card_front_text(w), reply_markup=show_answer_keyboard(w["id"]), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------- guards --

def authorized(update: Update) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return str(update.effective_user.id) == str(ALLOWED_USER_ID)


# --------------------------------------------------------------- handlers --

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("This bot is private.")
        return
    await update.message.reply_text(dashboard_text(), reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)


async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Build a compact ChatGPT prompt for words with no context."""
    if not authorized(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    words = word_list_from_text(raw)
    if not words:
        await update.message.reply_text(
            "📝 <b>Make an import prompt</b>\n\n"
            "Send words after the command, separated by commas.\n\n"
            "<code>/prompt yes, no, although, прийти к выводу</code>\n\n"
            "I will return text to paste into ChatGPT. It will ask for Kazakh meanings and priority.",
            parse_mode=ParseMode.HTML,
        )
        return
    prompt = chatgpt_prompt(words)
    await update.message.reply_text(
        f"📝 <b>ChatGPT prompt for {len(words)} words</b>\n"
        "<i>Copy the text below, send it to ChatGPT, then import ChatGPT’s JSON reply with /add.</i>\n\n"
        f"<pre>{esc(prompt)}</pre>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    c = get_counts()
    await update.message.reply_text(
        "📊 <b>Your progress</b>\n\n"
        f"📚 Total cards: <b>{c['total']}</b>\n"
        f"🔥 Due now: <b>{c['due_now']}</b>\n"
        f"🌱 New: <b>{c['new']}</b>\n"
        f"🧩 Learning: <b>{c['learning']}</b>\n"
        f"🌳 Mature: <b>{c['review']}</b>",
        reply_markup=main_keyboard(), parse_mode=ParseMode.HTML,
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    words = list_words()
    if not words:
        await update.message.reply_text("No words yet. Use /add to import your list.")
        return
    lines = []
    for w in words:
        badge = " 🔥" if w.get("priority") == "high" else ""
        lines.append(f"• <b>{esc(w['word'])}</b>{badge} · <i>{esc(w['state'])}</i>")
    await update.message.reply_text(
        "📚 <b>Library</b> <i>(first 30)</i>\n\n" + "\n".join(lines),
        reply_markup=main_keyboard(), parse_mode=ParseMode.HTML,
    )


async def cmd_leeches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    leeches = list_leeches()
    if not leeches:
        await update.message.reply_text("✅ No suspended cards. Keep it up!", reply_markup=main_keyboard())
        return
    lines = [
        f"• <code>{esc(w['id'])}</code> — <b>{esc(w['word'])}</b> → {esc(w['translation'])} "
        f"(<i>{w['lapses']} lapses</i>)" for w in leeches
    ]
    await update.message.reply_text(
        "🪲 <b>Leeches</b> <i>(suspended after repeated misses)</i>\n\n" + "\n".join(lines) +
        "\n\nAdd a clearer note or mnemonic, then use <code>/reset WORD_ID</code> to study one again.",
        reply_markup=main_keyboard(), parse_mode=ParseMode.HTML,
    )


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    parts = update.message.text.split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text("Usage: <code>/note WORD_ID your memory hook</code>", parse_mode=ParseMode.HTML)
        return
    if set_note(parts[1], parts[2].strip()):
        await update.message.reply_text("📝 Note saved.", reply_markup=main_keyboard())
    else:
        await update.message.reply_text("No card found with that ID.")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    word_id = update.message.text.partition(" ")[2].strip()
    if not word_id:
        await update.message.reply_text("Usage: <code>/reset WORD_ID</code>", parse_mode=ParseMode.HTML)
    elif reset_word(word_id):
        await update.message.reply_text("🌱 Card reset to new and ready to review.", reply_markup=main_keyboard())
    else:
        await update.message.reply_text("No card found with that ID.")


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await send_next_card(update.message.reply_text)


async def start_game(context: ContextTypes.DEFAULT_TYPE, reply_fn):
    cards = get_game_cards()
    if len(cards) < 2:
        await reply_fn("Add at least two active cards before playing a game.", reply_markup=main_keyboard())
        return
    context.user_data["game"] = {"score": 0, "round": 0}
    target, prompt, keyboard = game_round(cards, 0)
    context.user_data["game"]["target_id"] = target["id"]
    await reply_fn(
        "🎮 <b>Word sprint</b> · Score: <b>0</b>\n"
        "<i>Match meanings and complete contexts. Game scores never change your review schedule.</i>\n\n" + prompt,
        reply_markup=keyboard, parse_mode=ParseMode.HTML,
    )


async def cmd_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await start_game(context, update.message.reply_text)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    text_after = update.message.text.partition(" ")[2].strip()
    if text_after:
        await do_import(update.message.reply_text, text_after)
    else:
        context.user_data["awaiting_import"] = True
        await update.message.reply_text(
            "➕ <b>Import cards</b>\n\nSend the JSON exported by local Word Studio. "
            "You can paste an array or attach a <code>.json</code> file up to 2 MB.\n\n"
            "<i>Duplicates are skipped and existing review progress is preserved.</i>",
            parse_mode=ParseMode.HTML,
        )


async def do_import(reply_fn, text):
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("Expected a JSON array")
        if len(parsed) > MAX_IMPORT_CARDS:
            raise ValueError("Import at most 500 cards at a time")
    except Exception as e:
        await reply_fn(f"Could not parse JSON: {e}")
        return
    added, skipped, errors = add_words(parsed)
    result = f"✅ <b>Import complete</b>\n\nAdded: <b>{added}</b>\nSkipped: <b>{skipped}</b>"
    if errors:
        result += "\n\n⚠️ <b>First issues</b>\n" + "\n".join(esc(e) for e in errors)
    await reply_fn(result, reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    doc = update.message.document
    if not (doc.file_name or "").lower().endswith(".json"):
        await update.message.reply_text("Please send a .json file.")
        return
    if doc.file_size and doc.file_size > MAX_IMPORT_BYTES:
        await update.message.reply_text("That file is larger than 2 MB. Split it into smaller imports.")
        return
    file = await doc.get_file()
    data = await file.download_as_bytearray()
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        await update.message.reply_text("That file is not valid UTF-8 JSON.")
        return
    await do_import(update.message.reply_text, decoded)
    context.user_data["awaiting_import"] = False


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    pending = context.user_data.pop("awaiting_answer", None)
    if pending:
        w = get_word_with_review(pending["word_id"])
        if not w or w["state"] == "suspended":
            await update.message.reply_text("That card is no longer available. Start another review.", reply_markup=main_keyboard())
            return
        await update.message.reply_text(
            answer_feedback(update.message.text, w), reply_markup=rating_keyboard(w["id"], w), parse_mode=ParseMode.HTML,
        )
    elif context.user_data.get("awaiting_import"):
        context.user_data["awaiting_import"] = False
        await do_import(update.message.reply_text, update.message.text)
    else:
        await update.message.reply_text("Choose an action below.", reply_markup=main_keyboard())


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not authorized(update):
        await query.answer()
        return
    await query.answer()
    data = query.data.split("|")
    action = data[0]

    async def edit(text, reply_markup=None, parse_mode=None):
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

    if action == "menu":
        destination = data[1]
        if destination == "home":
            await edit(dashboard_text(), main_keyboard(), ParseMode.HTML)
        elif destination == "review":
            await send_next_card(edit)
        elif destination == "stats":
            c = get_counts()
            await edit(
                "📊 <b>Your progress</b>\n\n"
                f"📚 Total: <b>{c['total']}</b>\n🔥 Due now: <b>{c['due_now']}</b>\n"
                f"🌱 New: <b>{c['new']}</b>\n🧩 Learning: <b>{c['learning']}</b>\n🌳 Mature: <b>{c['review']}</b>\n🪲 Suspended: <b>{c['suspended']}</b>",
                main_keyboard(), ParseMode.HTML,
            )
        elif destination == "list":
            rows = list_words()
            lines = [f"• <b>{esc(w['word'])}</b>{' 🔥' if w.get('priority') == 'high' else ''} · <i>{esc(w['state'])}</i>" for w in rows]
            await edit("📚 <b>Library</b> <i>(first 30)</i>\n\n" + ("\n".join(lines) if lines else "No cards yet."), main_keyboard(), ParseMode.HTML)
        elif destination == "leeches":
            rows = list_leeches()
            lines = [f"• <code>{esc(w['id'])}</code> — <b>{esc(w['word'])}</b> (<i>{w['lapses']} lapses</i>)" for w in rows]
            await edit(
                "🪲 <b>Leeches</b>\n\n" + ("\n".join(lines) if lines else "No suspended cards.") +
                "\n\nUse <code>/note WORD_ID text</code>, then <code>/reset WORD_ID</code> when ready.",
                main_keyboard(), ParseMode.HTML,
            )
        elif destination == "game":
            await start_game(context, edit)
        else:
            context.user_data["awaiting_import"] = True
            await edit("➕ <b>Import cards</b>\n\nSend a Word Studio JSON file (maximum 2 MB).", main_keyboard(), ParseMode.HTML)
        return

    if action == "game":
        game = context.user_data.get("game")
        if not game:
            await edit("That game has finished. Start a new one with /game.", main_keyboard(), ParseMode.HTML)
            return
        if len(data) == 2 and data[1] == "next":
            cards = get_game_cards()
            if len(cards) < 2:
                await edit("Not enough active cards to continue.", main_keyboard())
                return
            target, prompt, keyboard = game_round(cards, game["round"])
            game["target_id"] = target["id"]
            await edit(
                f"🎮 <b>Word sprint</b> · Score: <b>{game['score']}</b>\n\n{prompt}",
                keyboard, ParseMode.HTML,
            )
            return
        if len(data) != 3 or data[1] != game.get("target_id"):
            await edit("That round expired. Start a new game with /game.", main_keyboard(), ParseMode.HTML)
            return
        target = get_word_with_review(data[1])
        if not target:
            await edit("That card no longer exists.", main_keyboard())
            return
        correct = data[2] == data[1]
        if correct:
            game["score"] += 1
        game["round"] += 1
        verdict = "🎉 <b>Correct!</b>" if correct else "💡 <b>Almost — here is the match.</b>"
        await edit(
            f"{verdict}\n\n<b>{esc(target['word'])}</b> → 🇰🇿 <b>{esc(target['translation'])}</b>\n\n"
            f"Score: <b>{game['score']}</b> · Round: <b>{game['round']}</b>",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Next round", callback_data="game|next")],
                [InlineKeyboardButton("🏠 Finish game", callback_data="menu|home")],
            ]), ParseMode.HTML,
        )
        return

    word_id = data[1]

    if action == "show":
        w = get_word_with_review(word_id)
        if not w:
            await edit("That word no longer exists.")
            return
        context.user_data["awaiting_answer"] = {"word_id": word_id}
        await edit(
            card_front_text(w) + "\n\n✍️ <b>Send your answer as a message now.</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Cancel", callback_data="menu|home")]]),
            parse_mode=ParseMode.HTML,
        )
    elif action == "rate":
        rating = data[2]
        w = get_word_with_review(word_id)
        if not w:
            await edit("That word no longer exists.")
            return
        updated = rate(w, rating)
        save_review(word_id, updated)
        context.user_data.pop("awaiting_answer", None)
        await send_next_card(edit)


def main():
    if not BOT_TOKEN:
        print("Set the BOT_TOKEN environment variable (get one from @BotFather).", file=sys.stderr)
        sys.exit(1)
    if not ALLOWED_USER_ID:
        print("Set ALLOWED_USER_ID. The bot refuses to start without an owner id.", file=sys.stderr)
        sys.exit(1)
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("leeches", cmd_leeches))
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("review", cmd_review))
    app.add_handler(CommandHandler("learn", cmd_review))
    app.add_handler(CommandHandler("game", cmd_game))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("prompt", cmd_prompt))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
    log.info("Word Box bot starting…")
    app.run_polling()


if __name__ == "__main__":
    main()
