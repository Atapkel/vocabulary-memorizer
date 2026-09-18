import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from .config import ALLOWED_USER_ID, MAX_IMPORT_BYTES, MAX_IMPORT_CARDS
from .storage import (add_words, get_due_words, get_word_with_review, get_game_cards, save_review, get_counts, next_due_timestamp, list_words, list_leeches, set_note, reset_word)
from .scheduling import rate
from .views import (esc, card_front_text, card_back_text, rating_keyboard, reveal_answer_keyboard, main_keyboard, dashboard_text, stats_text, library_text, leeches_text, import_help_text, word_list_from_text, answer_feedback, game_round, chatgpt_prompt)
from .memory import due_counts, remove_inbox
from .memory_handlers import on_collecting, save_unknown

async def send_next_card(message_edit_target):
    """message_edit_target: an Update's message or a CallbackQuery — must support edit/reply."""
    due = get_due_words(limit=1)
    if not due:
        nxt = next_due_timestamp()
        if nxt:
            text = "🎉 <b>ALL CAUGHT UP</b>\n\n✨ You’ve reviewed every word due right now. Come back when the next card is ready."
        else:
            text = "🌱 <b>NO WORD CARDS YET</b>\n\nSend a word in chat, make an LLM prompt, then import the JSON to start reviewing."
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
        await update.message.reply_text("🔒 <b>PRIVATE BOT</b>\n\nThis account does not have access.", parse_mode=ParseMode.HTML)
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
            "📝 <b>MAKE WORD CARDS</b>\n\n"
            "Send words after the command, separated by commas.\n\n"
            "<code>/prompt yes, no, although, прийти к выводу</code>\n\n"
            "💡 <i>I’ll give you a prompt to paste into an LLM.</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    prompt = chatgpt_prompt(words)
    await update.message.reply_text(
        f"📝 <b>WORD CARD PROMPT</b>\n<i>{len(words)} words</i>\n\n"
        "📋 Copy the next message into an LLM. Check its JSON reply, then import it with <code>/add</code>.\n\n"
        "<i>The prompt is plain text for easy copying.</i>",
        parse_mode=ParseMode.HTML,
    )
    await update.message.reply_text(prompt)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    _, lesson_due = due_counts()
    await update.message.reply_text(stats_text(get_counts(), lesson_due), reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(library_text(list_words()), reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)


async def cmd_leeches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(leeches_text(list_leeches()), reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    parts = update.message.text.split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text("📝 <b>ADD A MEMORY HOOK</b>\n\nUse <code>/note WORD_ID your memory hook</code>.", parse_mode=ParseMode.HTML)
        return
    if set_note(parts[1], parts[2].strip()):
        await update.message.reply_text("✅ <b>Memory hook saved</b>\n\nYou’ll see it when reviewing this word.", reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("🔎 <b>Card not found</b>\n\nCheck the ID under Difficult words.", parse_mode=ParseMode.HTML)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    word_id = update.message.text.partition(" ")[2].strip()
    if not word_id:
        await update.message.reply_text("🌱 <b>RESTART A CARD</b>\n\nUse <code>/reset WORD_ID</code>.", parse_mode=ParseMode.HTML)
    elif reset_word(word_id):
        await update.message.reply_text("✅ <b>Card restarted</b>\n\nIt’s ready for review again.", reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("🔎 <b>Card not found</b>\n\nCheck the ID under Difficult words.", parse_mode=ParseMode.HTML)


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await send_next_card(update.message.reply_text)


async def start_game(context: ContextTypes.DEFAULT_TYPE, reply_fn):
    cards = get_game_cards()
    if len(cards) < 2:
        await reply_fn("🎮 <b>GAME LOCKED</b>\n\nAdd at least two study cards to play.", reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)
        return
    context.user_data["game"] = {"score": 0, "round": 0}
    target, prompt, keyboard = game_round(cards, 0)
    context.user_data["game"]["target_id"] = target["id"]
    await reply_fn(
        "🎮 <b>WORD SPRINT</b>\n"
        "🏅 Score <b>0</b> · <i>Games don’t change review dates.</i>\n\n" + prompt,
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
        await update.message.reply_text(import_help_text(), reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)


async def do_import(reply_fn, text):
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("Expected a JSON array")
        if len(parsed) > MAX_IMPORT_CARDS:
            raise ValueError("Import at most 500 cards at a time")
    except Exception as e:
        await reply_fn(f"⚠️ <b>IMPORT FAILED</b>\n\n{esc(e)}\n\n<i>Check the JSON and try again with /add.</i>", parse_mode=ParseMode.HTML)
        return
    added, skipped, errors = add_words(parsed)
    if added:
        remove_inbox("word", [w["word"].strip() for w in parsed if isinstance(w, dict) and isinstance(w.get("word"), str)])
    result = f"✅ <b>WORD CARDS IMPORTED</b>\n\n🌱 Added  <b>{added}</b>\n↪️ Skipped  <b>{skipped}</b>"
    if errors:
        result += "\n\n⚠️ <b>Things to check</b>\n" + "\n".join(esc(e) for e in errors)
    await reply_fn(result, reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    doc = update.message.document
    if not (doc.file_name or "").lower().endswith(".json"):
        await update.message.reply_text("📄 <b>JSON FILE NEEDED</b>\n\nPlease attach a <code>.json</code> file.", parse_mode=ParseMode.HTML)
        return
    if doc.file_size and doc.file_size > MAX_IMPORT_BYTES:
        await update.message.reply_text("📦 <b>FILE TOO LARGE</b>\n\nSplit the export into files under 2 MB.", parse_mode=ParseMode.HTML)
        return
    file = await doc.get_file()
    data = await file.download_as_bytearray()
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        await update.message.reply_text("⚠️ <b>INVALID FILE</b>\n\nSave it as UTF-8 JSON and try again.", parse_mode=ParseMode.HTML)
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
            await update.message.reply_text("🔄 <b>CARD UNAVAILABLE</b>\n\nStart another review from the menu.", reply_markup=main_keyboard(), parse_mode=ParseMode.HTML)
            return
        await update.message.reply_text(
            answer_feedback(update.message.text, w), reply_markup=rating_keyboard(w["id"], w), parse_mode=ParseMode.HTML,
        )
    elif context.user_data.get("awaiting_import"):
        context.user_data["awaiting_import"] = False
        await do_import(update.message.reply_text, update.message.text)
    elif await on_collecting(update, context):
        return
    elif update.message.text.lstrip().startswith(("[", "{")):
        await update.message.reply_text("📥 <b>READY TO IMPORT?</b>\n\nUse <code>/add</code> for word cards or <code>/lessonimport</code> for lesson cards, then paste the JSON.", parse_mode=ParseMode.HTML)
    else:
        await save_unknown(update.message.reply_text, update.message.text)


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
            _, lesson_due = due_counts()
            await edit(stats_text(get_counts(), lesson_due), main_keyboard(), ParseMode.HTML)
        elif destination == "list":
            await edit(library_text(list_words()), main_keyboard(), ParseMode.HTML)
        elif destination == "leeches":
            await edit(leeches_text(list_leeches()), main_keyboard(), ParseMode.HTML)
        elif destination == "game":
            await start_game(context, edit)
        else:
            context.user_data["awaiting_import"] = True
            await edit(import_help_text(), main_keyboard(), ParseMode.HTML)
        return

    if action == "game":
        game = context.user_data.get("game")
        if not game:
            await edit("🎮 <b>GAME OVER</b>\n\nStart a new round with <code>/game</code>.", main_keyboard(), ParseMode.HTML)
            return
        if len(data) == 2 and data[1] == "next":
            cards = get_game_cards()
            if len(cards) < 2:
                await edit("🎮 <b>MORE CARDS NEEDED</b>\n\nAdd at least two active cards to keep playing.", main_keyboard(), ParseMode.HTML)
                return
            target, prompt, keyboard = game_round(cards, game["round"])
            game["target_id"] = target["id"]
            await edit(
                f"🎮 <b>WORD SPRINT</b>\n🏅 Score <b>{game['score']}</b>\n\n{prompt}",
                keyboard, ParseMode.HTML,
            )
            return
        if len(data) != 3 or data[1] != game.get("target_id"):
            await edit("⌛ <b>ROUND EXPIRED</b>\n\nStart a new game with <code>/game</code>.", main_keyboard(), ParseMode.HTML)
            return
        target = get_word_with_review(data[1])
        if not target:
            await edit("🔎 <b>CARD NOT FOUND</b>\n\nChoose another game.", main_keyboard(), ParseMode.HTML)
            return
        correct = data[2] == data[1]
        if correct:
            game["score"] += 1
        game["round"] += 1
        verdict = "🎉 <b>GREAT MATCH!</b>" if correct else "💡 <b>HERE’S THE MATCH</b>"
        await edit(
            f"{verdict}\n\n🔤 <b>{esc(target['word'])}</b>\n🇰🇿 {esc(target['translation'])}\n\n"
            f"🏅 Score <b>{game['score']}</b>  ·  Round <b>{game['round']}</b>",
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
            await edit("🔎 <b>WORD NOT FOUND</b>\n\nReturn to the menu and choose another card.", main_keyboard(), ParseMode.HTML)
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
            await edit("🔎 <b>WORD NOT FOUND</b>\n\nReturn to the menu and choose another card.", main_keyboard(), ParseMode.HTML)
            return
        updated = rate(w, rating)
        save_review(word_id, updated)
        context.user_data.pop("awaiting_answer", None)
        await send_next_card(edit)
