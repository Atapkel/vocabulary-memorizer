import sqlite3
import time
import uuid
from .config import DB_PATH, MAX_WORD, MAX_CONTEXT

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
            created_at INTEGER
        )
    """)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(words)")}
    if "target_language" not in columns:
        conn.execute("ALTER TABLE words ADD COLUMN target_language TEXT DEFAULT 'english'")
    if "pronunciation" not in columns:
        conn.execute("ALTER TABLE words ADD COLUMN pronunciation TEXT")
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
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS inbox (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(kind, content)
        );
        CREATE TABLE IF NOT EXISTS lesson_cards (
            id INTEGER PRIMARY KEY, topic TEXT NOT NULL, question TEXT NOT NULL,
            answer TEXT NOT NULL, hint TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'new', step INTEGER NOT NULL DEFAULT 0,
            interval INTEGER NOT NULL DEFAULT 0, ef REAL NOT NULL DEFAULT 2.5,
            due INTEGER NOT NULL, reps INTEGER NOT NULL DEFAULT 0,
            lapses INTEGER NOT NULL DEFAULT 0,
            UNIQUE(topic, question)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
    """)
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
        wid = wid.strip()[:80] if isinstance(wid, str) and wid.strip() else "t" + uuid.uuid4().hex[:12]
        lang = str(w.get("target_language") or "").lower()
        if lang not in ("english", "russian"):
            lang = "russian" if any(("а" <= c.lower() <= "я") or c.lower() == "ё" for c in word) else "english"
        context_text = " ".join(str(w.get("context") or "").split())[:MAX_CONTEXT]
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
               created_at, target_language, pronunciation, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (wid, word, context_text, str(w.get("translation") or "")[:300],
             str(w.get("explanation") or "")[:800], "",
             str(w.get("synonyms") or "")[:300], str(w.get("example") or "")[:300],
             w.get("created_at") if isinstance(w.get("created_at"), (int, float)) else now,
             lang, "", str(w.get("note") or "")[:1000]),
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
           ORDER BY r.due ASC, w.id ASC LIMIT ?""",
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
        """SELECT w.word, r.state, r.due FROM words w JOIN reviews r ON w.id=r.word_id
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
