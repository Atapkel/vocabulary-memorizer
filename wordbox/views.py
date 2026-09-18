import html
import json
import re
from difflib import SequenceMatcher
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .config import MAX_CONTEXT, MAX_WORD, ANSWER_MATCH_THRESHOLD
from .storage import get_counts

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
    # Put a concise example on the question side. Imported/generated examples
    # are preferred; source context remains a useful fallback for older cards.
    example_sentence = w.get("example") or w.get("context")
    context = "" if production or not example_sentence else f"💬 {highlight_context(example_sentence, w['word'])}\n\n"
    return (
        f"🧠 <b>REVIEW</b> · {flag} <i>{esc(w['state'])}</i>\n{priority}\n"
        f"<b>{esc(prompt)}</b>\n<i>{prompt_label}</i>\n\n"
        f"{context}<i>Recall {expected} from memory, then reveal it.</i>"
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


def reveal_answer_keyboard(word_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁️ Reveal answer", callback_data=f"reveal|{word_id}")],
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
        [InlineKeyboardButton("🧠 Memory & reminders", callback_data="memory|home")],
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

