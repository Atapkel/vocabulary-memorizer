import tempfile
import unittest
import asyncio
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wordbox import handlers, memory, memory_handlers, storage, views


class TelegramHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in {"b", "i", "code", "pre"}:
            raise AssertionError(f"Unsupported Telegram HTML tag: {tag}")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise AssertionError(f"Unbalanced Telegram HTML tag: {tag}")


class MemoryFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "vocab.db")
        self.db_patch = patch.object(storage, "DB_PATH", self.path)
        self.db_patch.start()
        storage.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_inbox_import_and_review(self):
        self.assertEqual(memory.collect("word", ["learn", "learn"]), (1, 2))
        self.assertEqual(memory.inbox_items("word"), ["learn"])
        added, skipped, _ = storage.add_words([{"word": "learn", "translation": "үйрену"}])
        self.assertEqual((added, skipped), (1, 0))
        memory.remove_inbox("word", ["learn"])
        self.assertEqual(memory.inbox_count("word"), 0)

        added, skipped, _ = memory.import_lessons([
            {"topic": "Biology", "question": "What is DNA?", "answer": "A molecule carrying genetic information."}
        ])
        self.assertEqual((added, skipped), (1, 0))
        card = memory.due_lesson()
        self.assertEqual(card["question"], "What is DNA?")
        self.assertTrue(memory.rate_lesson(card["id"], "good"))
        self.assertIsNone(memory.due_lesson())
        self.assertFalse(memory.rate_lesson(card["id"], "good"))

    def test_reminder_slots_and_quiet_hours(self):
        prefs = {"timezone": "Asia/Qyzylorda", "quiet_start": "22:00", "quiet_end": "08:00", "reminders": "on"}
        self.assertIsNone(memory.reminder_slot(datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc), prefs))
        slot = memory.reminder_slot(datetime(2026, 9, 18, 4, 1, tzinfo=timezone.utc), prefs)
        self.assertEqual(slot, "2026-09-18:540")
        prefs["last_reminder_slot"] = slot
        self.assertIsNone(memory.reminder_slot(datetime(2026, 9, 18, 4, 30, tzinfo=timezone.utc), prefs))
        self.assertIsNone(memory.reminder_slot(datetime(2026, 9, 18, 18, 0, tzinfo=timezone.utc), prefs))

    def test_delete_unknown_word_does_not_delete_imported_card(self):
        memory.collect("word", ["Take off", "TAKE OFF", "another"])
        storage.add_words([{"word": "Take off", "translation": "ұшу"}])
        self.assertEqual(memory.delete_unknown("  take   off "), 2)
        self.assertEqual(memory.inbox_items("word"), ["another"])
        self.assertIsNotNone(storage.get_due_words()[0])
        self.assertEqual(memory.delete_unknown("missing"), 0)

    def test_plain_text_defaults_to_unknown_words_but_import_stays_import(self):
        replies = []

        async def reply(text, **kwargs):
            replies.append(text)

        async def send(text, user_data):
            update = SimpleNamespace(message=SimpleNamespace(text=text, reply_text=reply))
            context = SimpleNamespace(user_data=user_data)
            with patch.object(handlers, "authorized", return_value=True):
                await handlers.on_text(update, context)

        asyncio.run(send("apple, orange", {}))
        self.assertEqual(memory.inbox_items("word"), ["apple", "orange"])
        asyncio.run(send('[{"word":"cat","translation":"мысық"}]', {"awaiting_import": True}))
        self.assertEqual(memory.inbox_count("word"), 2)
        self.assertEqual(storage.get_counts()["total"], 1)
        self.assertTrue(any("WORD CARDS IMPORTED" in message for message in replies))

    def test_styled_screens_escape_user_text_and_fit_telegram(self):
        memory.collect("word", ["<script>& curious"])
        card = {"topic": "<Biology>&" * 20, "question": "<&" * 500,
                "answer": "<&" * 1500, "hint": "<&" * 300}
        screens = [
            views.dashboard_text(),
            views.stats_text(storage.get_counts()),
            views.library_text([{"word": "<script>&", "state": "new", "priority": "high"}]),
            views.leeches_text([{"word": "<x>&", "translation": "<&", "lapses": 8, "id": "<id>&"}]),
            memory_handlers.section_text(),
            memory_handlers.unknown_list_view()[0],
            memory_handlers.settings_text(),
            memory_handlers.lesson_front_text(card),
            memory_handlers.lesson_back_text(card),
        ]
        for screen in screens:
            with self.subTest(screen=screen[:50]):
                self.assertLessEqual(len(screen), 4096)
                parser = TelegramHtmlParser()
                parser.feed(screen)
                parser.close()
                self.assertEqual(parser.stack, [])
                self.assertNotIn("<script>", screen)

        memory.collect("word", ["<&" * 26 + str(i) for i in range(20)])
        long_library = views.library_text([
            {"word": "<&" * 40, "state": "learning", "priority": "high"}
            for _ in range(30)
        ])
        long_inbox = memory_handlers.unknown_list_view()[0]
        self.assertLessEqual(len(long_library), 4096)
        self.assertLessEqual(len(long_inbox), 4096)


if __name__ == "__main__":
    unittest.main()
