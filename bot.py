"""Run the private Word Box Telegram bot."""

import asyncio
import logging
import sys

from telegram import BotCommand
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from wordbox.config import ALLOWED_USER_ID, BOT_TOKEN
from wordbox.handlers import (
    cmd_add, cmd_game, cmd_leeches, cmd_list, cmd_note, cmd_prompt,
    cmd_reset, cmd_review, cmd_start, cmd_stats, on_callback, on_document, on_text,
)
from wordbox.memory_handlers import (
    cmd_delete, cmd_lesson, cmd_lessonimport, cmd_lessonprompt, cmd_lessons,
    cmd_memory, cmd_unknowns,
    cmd_quiet, cmd_reminders, cmd_settings, cmd_timezone, cmd_unknown,
    cmd_unknownprompt, on_memory_callback, reminder_loop,
)
from wordbox.storage import init_db


async def post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "Open the main menu"),
            BotCommand("review", "Review due word cards"),
            BotCommand("lessons", "Review due lesson cards"),
            BotCommand("unknowns", "View and delete saved words"),
            BotCommand("unknownprompt", "Make an LLM prompt for saved words"),
            BotCommand("lesson", "Save a lesson note"),
            BotCommand("lessonprompt", "Make an LLM prompt for lessons"),
            BotCommand("add", "Import word cards from JSON"),
            BotCommand("lessonimport", "Import lesson cards from JSON"),
            BotCommand("stats", "See your review progress"),
            BotCommand("settings", "View reminder settings"),
            BotCommand("delete", "Remove a saved unknown word"),
        ])
    except TelegramError:
        logging.exception("Could not update Telegram command menu")
    app.bot_data["reminder_task"] = asyncio.create_task(reminder_loop(app))


async def post_shutdown(app: Application) -> None:
    task = app.bot_data.get("reminder_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not BOT_TOKEN or not ALLOWED_USER_ID or not ALLOWED_USER_ID.isdigit():
        sys.exit("Set BOT_TOKEN and a numeric ALLOWED_USER_ID before starting the bot.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    commands = {
        "start": cmd_start, "help": cmd_start, "stats": cmd_stats, "list": cmd_list,
        "leeches": cmd_leeches, "note": cmd_note, "reset": cmd_reset,
        "review": cmd_review, "learn": cmd_review, "game": cmd_game,
        "add": cmd_add, "prompt": cmd_prompt, "memory": cmd_memory,
        "unknown": cmd_unknown, "unknownprompt": cmd_unknownprompt,
        "unknowns": cmd_unknowns, "delete": cmd_delete,
        "lesson": cmd_lesson, "lessonprompt": cmd_lessonprompt,
        "lessonimport": cmd_lessonimport, "lessons": cmd_lessons,
        "settings": cmd_settings, "timezone": cmd_timezone,
        "quiet": cmd_quiet, "reminders": cmd_reminders,
    }
    for name, handler in commands.items():
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_memory_callback, pattern=r"^memory\|"))
    app.add_handler(CallbackQueryHandler(on_callback))
    logging.info("Word Box bot starting")
    app.run_polling()


if __name__ == "__main__":
    main()
