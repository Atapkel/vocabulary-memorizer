"""Small helpers for Telegram message operations."""

from telegram.error import BadRequest


async def edit_text(query, text, reply_markup=None, parse_mode=None):
    """Ignore an edit only when Telegram confirms it changes nothing."""
    try:
        return await query.edit_message_text(
            text, reply_markup=reply_markup, parse_mode=parse_mode,
        )
    except BadRequest as exc:
        if "message is not modified" in str(exc).casefold():
            return None
        raise
