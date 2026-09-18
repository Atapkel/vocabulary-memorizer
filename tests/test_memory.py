import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from wordbox import memory, storage


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


if __name__ == "__main__":
    unittest.main()
