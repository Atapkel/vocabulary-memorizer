import html
import json
import re
from difflib import SequenceMatcher
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .config import MAX_CONTEXT, MAX_WORD, ANSWER_MATCH_THRESHOLD
from .storage import get_counts
from .memory import due_counts, inbox_count

def esc(s):
    return html.escape(str(s)) if s else ""


def esc_limit(value, limit: int):
    """Escape user text while staying within a Telegram message budget."""
    output = []
    used = 0
    for char in str(value or ""):
        escaped = html.escape(char)
        if used + len(escaped) > limit - 1:
            return "".join(output) + "…"
        output.append(escaped)
        used += len(escaped)
    return "".join(output)


def fit_html_sections(sections, limit=3900):
    """Keep complete HTML sections so Telegram never receives a cut tag."""
    result = []
    length = 0
    for section in sections:
        if length + len(section) + (1 if result else 0) > limit:
            continue
        result.append(section)
        length += len(section) + (1 if len(result) > 1 else 0)
    return "\n".join(result)


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
    production = w.get("direction") == "production"
    prompt = w.get("translation") if production else w.get("word")
    prompt_label = "Kazakh meaning" if production else "Word"
    expected = "the foreign word" if production else "the Kazakh meaning"
    # Put a concise example on the question side. Imported/generated examples
    # are preferred; source context remains a useful fallback for older cards.
    example_sentence = w.get("example") or w.get("context")
    context = "" if production or not example_sentence else f"💬 <i>{highlight_context(example_sentence, w['word'])}</i>\n\n"
    return (
        f"🧠 <b>WORD REVIEW</b>  {flag}\n"
        f"<i>{esc(w['state']).capitalize()} card</i>\n\n"
        f"❓ <b>{esc(prompt)}</b>\n"
        f"<i>{prompt_label}</i>\n\n"
        f"{context}💭 <i>Recall {expected} before revealing the answer.</i>"
    )


def card_back_text(w):
    flag = "🇷🇺" if w.get("target_language") == "russian" else "🇬🇧"
    lines = [f"✅ <b>WORD ANSWER</b>  {flag}", "", f"🔤 <b>{esc(w['word'])}</b>", ""]
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
    return fit_html_sections(lines)


def rating_keyboard(word_id, review):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Study again", callback_data=f"rate|{word_id}|dont_know"),
         InlineKeyboardButton("✅ Remembered", callback_data=f"rate|{word_id}|know")],
        [InlineKeyboardButton("🏠 Main menu", callback_data="menu|home")],
    ])


def reveal_answer_keyboard(word_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👀 Reveal answer", callback_data=f"reveal|{word_id}")],
        [InlineKeyboardButton("🏠 Main menu", callback_data="menu|home")],
    ])


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Review words", callback_data="menu|review"),
         InlineKeyboardButton("📖 Review lessons", callback_data="memory|review")],
        [InlineKeyboardButton("📥 Unknown words", callback_data="memory|word_list"),
         InlineKeyboardButton("📝 Add lesson note", callback_data="memory|lessons")],
        [InlineKeyboardButton("📊 My progress", callback_data="menu|stats"),
         InlineKeyboardButton("⏰ Reminders", callback_data="memory|settings")],
        [InlineKeyboardButton("📋 JSON format guides", callback_data="memory|guides"),
         InlineKeyboardButton("✨ More options", callback_data="memory|home")],
    ])


def dashboard_text():
    c = get_counts()
    _, lesson_due = due_counts()
    return (
        "🧠 <b>WORD BOX</b>\n"
        "<i>Your personal space for words and lessons</i>\n\n"
        "🔥 <b>Ready to review</b>\n"
        f"   • {c['due_now']} word cards\n"
        f"   • {lesson_due} lesson cards\n\n"
        f"📥 <b>Unknown words saved:</b> {inbox_count('word')}\n\n"
        "✍️ <b>Quick add</b>\n"
        "Send a word or a comma-separated list in this chat. I’ll save it for you.\n\n"
        "<i>Choose an action below to make cards, review, or manage reminders.</i>"
    )


def stats_text(counts, lesson_due=0):
    return (
        "📊 <b>YOUR PROGRESS</b>\n\n"
        f"📚 <b>{counts['total']}</b> word cards in your library\n"
        f"🔥 <b>{counts['due_now']}</b> words + <b>{lesson_due}</b> lessons ready now\n\n"
        "<b>Word cards by stage</b>\n"
        f"🌱 New  <b>{counts['new']}</b>\n"
        f"🧩 Learning  <b>{counts['learning']}</b>\n"
        f"🌳 Reviewing  <b>{counts['review']}</b>\n"
        f"🪲 Paused after repeated misses  <b>{counts['suspended']}</b>\n\n"
        "<i>Small, regular reviews add up.</i>"
    )


def library_text(rows):
    if not rows:
        return ("📚 <b>WORD LIBRARY</b>\n\n"
                "No study cards yet. Save a word in chat, make a word prompt, and import the JSON.")
    lines = [
        f"{index}. <b>{esc_limit(row['word'], 100)}</b>"
        f"  <i>· {esc(row['state'])}</i>"
        for index, row in enumerate(rows[:30], 1)
    ]
    return "📚 <b>WORD LIBRARY</b>\n<i>First 30 cards by review date</i>\n\n" + "\n".join(lines)


def leeches_text(rows):
    if not rows:
        return ("🪲 <b>DIFFICULT WORDS</b>\n\n"
                "✨ No cards are paused after repeated misses.")
    lines = [
        f"• <b>{esc_limit(row['word'], 90)}</b> → {esc_limit(row['translation'], 100)}\n"
        f"  <i>{row['lapses']} misses</i> · <code>{esc_limit(row['id'], 80)}</code>"
        for row in rows[:10]
    ]
    return ("🪲 <b>DIFFICULT WORDS</b>\n<i>Showing up to 10 paused cards</i>\n"
            "<i>These cards are paused so you can improve them first.</i>\n\n"
            + "\n".join(lines)
            + "\n\n💡 Add a memory hook with <code>/note WORD_ID text</code>, "
              "then try <code>/reset WORD_ID</code>.")


def import_help_text():
    return ("📥 <b>IMPORT WORD CARDS</b>\n\n"
            "Send a JSON array from your LLM, or attach a <code>.json</code> export from Word Studio.\n\n"
            "📄 <b>File limit:</b> 2 MB · <b>Batch limit:</b> 500 cards\n"
            "🔒 <i>Existing cards keep their review progress.</i>")


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
    verdict = "✅ <b>Looks right</b>" if similarity >= ANSWER_MATCH_THRESHOLD else "🔎 <b>Take another look</b>"
    direction_label = "foreign word" if w.get("direction") == "production" else "Kazakh meaning"
    feedback = (
        f"{verdict}\n\n"
        f"✍️ <b>Your answer</b>\n{esc(str(answer)[:300])}\n\n"
        f"🎯 <b>Expected {direction_label}</b>\n{esc(expected)}\n\n"
        f"<i>Choose how well you remembered it.</i>"
    )
    back = card_back_text(w)
    return feedback + ("\n\n" + back if len(feedback) + len(back) + 2 <= 3900 else "")


def game_round(cards, round_number):
    """Create either a meaning match or a context-based find-the-word round."""
    target = cards[0]
    choices = cards[:]
    # Alternate modes so a short game practises both meanings and contextual use.
    mode = "find" if round_number % 2 and target.get("context") else "match"
    if mode == "find":
        blank = re.sub(re.escape(target["word"]), "_____", target["context"], count=1, flags=re.IGNORECASE)
        prompt = (
            "🎮 <b>FIND THE WORD</b>\n\n"
            f"{esc(blank)}\n\n"
            f"🇰🇿 Hint: <b>{esc(target['translation'])}</b>\n\n"
            "<i>Which word completes the sentence?</i>"
        )
        label = lambda card: card["word"]
    else:
        prompt = (
            "🎮 <b>QUICK MATCH</b>\n\n"
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
        "Reply with ONLY a valid JSON array using exactly these fields:\n"
        '[{"word":"...","target_language":"english or russian","context":"","translation":"Kazakh meaning",'
        '"explanation":"simple target-language definition",'
        '"example":"...","synonyms":"..."}]\n\n'
        "Words:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    )
