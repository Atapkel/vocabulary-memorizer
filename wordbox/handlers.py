import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from .config import ALLOWED_USER_ID, MAX_IMPORT_BYTES, MAX_IMPORT_CARDS
from .storage import (add_words, get_due_words, get_word_with_review, get_game_cards, save_review, get_counts, next_due_timestamp, list_words, list_leeches, set_note, reset_word)
from .scheduling import rate
from .views import (esc, card_front_text, card_back_text, rating_keyboard, reveal_answer_keyboard, main_keyboard, dashboard_text, word_list_from_text, answer_feedback, game_round, chatgpt_prompt)
from .memory import remove_inbox
from .memory_handlers import on_collecting

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
    await message_edit_target(card_front_text(w), reply_markup=reveal_answer_keyboard(w["id"]), parse_mode=ParseMode.HTML)


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
    if added:
        remove_inbox("word", [w["word"].strip() for w in parsed if isinstance(w, dict) and isinstance(w.get("word"), str)])
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
    if await on_collecting(update, context):
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

    if action == "reveal":
        w = get_word_with_review(word_id)
        if not w:
            await edit("That word no longer exists.")
            return
        await edit(
            card_back_text(w),
            reply_markup=rating_keyboard(w["id"], w),
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

