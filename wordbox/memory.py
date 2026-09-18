"""Persistent inboxes, lesson cards, and reminder preferences."""

import json
import time
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from .storage import get_conn
from .scheduling import rate


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def collect(kind: str, items: list[str]) -> tuple[int, int]:
    if kind not in {"word", "lesson"}:
        raise ValueError("Unknown inbox")
    with db() as conn:
        before = conn.total_changes
        for item in items:
            conn.execute(
                "INSERT OR IGNORE INTO inbox(kind, content, created_at) VALUES (?, ?, ?)",
                (kind, item, int(time.time())),
            )
        return conn.total_changes - before, len(items)


def inbox_items(kind: str, limit: int = 15) -> list[str]:
    with db() as conn:
        return [r["content"] for r in conn.execute(
            "SELECT content FROM inbox WHERE kind=? ORDER BY id LIMIT ?", (kind, limit)
        )]


def inbox_count(kind: str) -> int:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM inbox WHERE kind=?", (kind,)).fetchone()[0]


def unknown_words(limit: int = 20) -> list[dict]:
    """Newest saved unknown words, for the inbox screen."""
    with db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id, content FROM inbox WHERE kind='word' ORDER BY id DESC LIMIT ?", (limit,)
        )]


def delete_unknown(word: str) -> int:
    """Delete an inbox entry by its word or phrase; learned cards are untouched."""
    normalized = " ".join(word.split()).casefold()
    if not normalized:
        return 0
    with db() as conn:
        rows = conn.execute("SELECT id, content FROM inbox WHERE kind='word'").fetchall()
        ids = [(row["id"],) for row in rows if row["content"].casefold() == normalized]
        conn.executemany("DELETE FROM inbox WHERE id=?", ids)
        return len(ids)


def delete_unknown_by_id(item_id: int) -> bool:
    with db() as conn:
        return bool(conn.execute(
            "DELETE FROM inbox WHERE id=? AND kind='word'", (item_id,)
        ).rowcount)


def remove_inbox(kind: str, items: list[str]) -> None:
    with db() as conn:
        conn.executemany("DELETE FROM inbox WHERE kind=? AND content=?", [(kind, i) for i in items])


def import_lessons(payload: object) -> tuple[int, int, list[str]]:
    if not isinstance(payload, list) or len(payload) > 100:
        raise ValueError("Send a JSON array of at most 100 lesson cards")
    added, skipped, errors = 0, 0, []
    now = int(time.time())
    with db() as conn:
        for index, card in enumerate(payload, 1):
            if not isinstance(card, dict):
                skipped += 1
                errors.append(f"#{index}: card must be an object")
                continue
            fields = {k: card.get(k) for k in ("topic", "question", "answer", "hint")}
            if any(not isinstance(fields[k], str) or not fields[k].strip() for k in ("topic", "question", "answer")):
                skipped += 1
                errors.append(f"#{index}: topic, question and answer are required")
                continue
            if any(len(fields[k]) > n for k, n in (("topic", 100), ("question", 500), ("answer", 1500))):
                skipped += 1
                errors.append(f"#{index}: field too long")
                continue
            hint = fields["hint"] if isinstance(fields["hint"], str) else ""
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO lesson_cards(topic, question, answer, hint, due) VALUES (?, ?, ?, ?, ?)",
                (fields["topic"].strip(), fields["question"].strip(), fields["answer"].strip(), hint[:300].strip(), now),
            )
            if conn.total_changes > before:
                added += 1
            else:
                skipped += 1
    return added, skipped, errors[:10]


def due_lesson() -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM lesson_cards WHERE state!='suspended' AND due<=? ORDER BY due, id LIMIT 1",
            (int(time.time()),),
        ).fetchone()
        return dict(row) if row else None


def lesson_by_id(card_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM lesson_cards WHERE id=?", (card_id,)).fetchone()
        return dict(row) if row else None


def rate_lesson(card_id: int, rating: str) -> bool:
    with db() as conn:
        row = conn.execute("SELECT * FROM lesson_cards WHERE id=?", (card_id,)).fetchone()
        if not row or row["state"] == "suspended" or row["due"] > int(time.time()):
            return False
        updated = rate(dict(row), rating)
        conn.execute(
            """UPDATE lesson_cards SET state=?, step=?, interval=?, ef=?, due=?, reps=?, lapses=? WHERE id=?""",
            (updated["state"], updated["step"], updated["interval"], updated["ef"], updated["due"],
             updated["reps"], updated["lapses"], card_id),
        )
        return True


def due_counts() -> tuple[int, int]:
    now = int(time.time())
    with db() as conn:
        words = conn.execute("SELECT COUNT(*) FROM reviews WHERE state!='suspended' AND due<=?", (now,)).fetchone()[0]
        lessons = conn.execute("SELECT COUNT(*) FROM lesson_cards WHERE state!='suspended' AND due<=?", (now,)).fetchone()[0]
        return words, lessons


DEFAULT_SETTINGS = {"timezone": "Asia/Qyzylorda", "quiet_start": "22:00", "quiet_end": "08:00", "reminders": "on"}


def settings() -> dict[str, str]:
    with db() as conn:
        result = DEFAULT_SETTINGS.copy()
        result.update({r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")})
        return result


def set_setting(key: str, value: str) -> None:
    if key not in DEFAULT_SETTINGS and key != "last_reminder_slot":
        raise ValueError("Unknown setting")
    with db() as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def valid_time(value: str) -> bool:
    try:
        return datetime.strptime(value, "%H:%M").strftime("%H:%M") == value
    except ValueError:
        return False


def in_quiet_window(now_minutes: int, start: str, end: str) -> bool:
    a, b = [int(h) * 60 + int(m) for h, m in (s.split(":") for s in (start, end))]
    if a == b:
        return False
    return a <= now_minutes < b if a < b else now_minutes >= a or now_minutes < b


def reminder_slot(now: datetime, prefs: dict[str, str]) -> str | None:
    local = now.astimezone(ZoneInfo(prefs["timezone"]))
    minutes = local.hour * 60 + local.minute
    if prefs["reminders"] != "on" or in_quiet_window(minutes, prefs["quiet_start"], prefs["quiet_end"]):
        return None
    slots = (9 * 60, 14 * 60, 19 * 60)
    eligible = [s for s in slots if minutes >= s]
    if not eligible:
        return None
    slot = f"{local.date().isoformat()}:{eligible[-1]}"
    return slot if slot != prefs.get("last_reminder_slot") else None


def word_prompt(words: list[str]) -> str:
    from .views import chatgpt_prompt
    return chatgpt_prompt(words)


def lesson_prompt(notes: list[str]) -> str:
    return (
        "Turn these lesson notes into short active-recall flashcards. Cover important facts and concepts; "
        "split complex ideas into small questions. Use only facts supported by the notes. "
        "If a fact is ambiguous, omit it. Reply with ONLY a valid JSON array, no Markdown fences. "
        "Each object must have exactly: "
        '{"topic":"lesson name","question":"one clear question","answer":"concise correct answer","hint":"optional cue"}. '
        "Write in the language of the notes. Create at most 20 cards. Notes: " + json.dumps(notes, ensure_ascii=False)
    )
