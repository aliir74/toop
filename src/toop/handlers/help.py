from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from toop.commands import render_help
from toop.config import settings

logger = logging.getLogger(__name__)


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the commands the caller can use, rendered from toop.commands.

    The admin sees every command; everyone else sees only the public subset.
    """
    message = update.effective_message
    if message is None:
        return
    user = update.effective_user
    is_admin = (
        user is not None
        and settings.ADMIN_TELEGRAM_ID != 0
        and user.id == settings.ADMIN_TELEGRAM_ID
    )
    await message.reply_text(render_help(admin=is_admin))
