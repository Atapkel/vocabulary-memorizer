"""Copyable JSON examples for the two card import formats."""

import html
import json


WORD_EXAMPLE = [{
    "word": "curious",
    "translation": "білуге құмар",
    "target_language": "english",
    "context": "She is curious about space.",
    "explanation": "Wanting to learn or know more.",
    "example": "The curious child asked questions.",
    "synonyms": "inquisitive",
    "note": "",
}]

LESSON_EXAMPLE = [{
    "topic": "Biology",
    "question": "What does DNA store?",
    "answer": "Genetic information.",
    "hint": "Think about inherited traits.",
}]


def word_guide_text() -> str:
    example = html.escape(json.dumps(WORD_EXAMPLE, ensure_ascii=False, indent=2))
    return (
        "📋 <b>WORD JSON GUIDE</b>\n\n"
        "Send a <b>JSON array</b>, even for one word. Each object is one card.\n\n"
        "<b>Required</b>\n"
        "• <code>word</code> — English or Russian word or short phrase\n"
        "• <code>translation</code> — Kazakh meaning\n\n"
        "<b>Optional</b>\n"
        "• <code>target_language</code> — <code>english</code> or <code>russian</code>; detected if omitted\n"
        "• <code>context</code>, <code>explanation</code>, <code>example</code>, "
        "<code>synonyms</code>, <code>note</code> — helpful extra detail\n\n"
        "<b>Example you can copy</b>\n"
        f"<pre>{example}</pre>\n\n"
        "📥 <b>To import:</b> send <code>/add</code>, then paste the JSON in your next message. "
        "A <code>.json</code> file also works.\n"
        "<i>Use double quotes. Leave out Markdown fences and trailing commas.</i>"
    )


def lesson_guide_text() -> str:
    example = html.escape(json.dumps(LESSON_EXAMPLE, ensure_ascii=False, indent=2))
    return (
        "📋 <b>LESSON JSON GUIDE</b>\n\n"
        "Send a <b>JSON array</b>, even for one lesson card. Each object is one question to review.\n\n"
        "<b>Required</b>\n"
        "• <code>topic</code> — lesson name or subject\n"
        "• <code>question</code> — one clear recall question\n"
        "• <code>answer</code> — short, correct answer\n\n"
        "<b>Optional</b>\n"
        "• <code>hint</code> — a small clue shown after the answer\n\n"
        "<b>Example you can copy</b>\n"
        f"<pre>{example}</pre>\n\n"
        "📥 <b>To import:</b> send <code>/lessonimport</code>, then paste the JSON in your next message.\n"
        "<i>Use double quotes. Leave out Markdown fences and trailing commas.</i>"
    )
