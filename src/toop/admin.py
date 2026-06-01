from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from toop.config import settings

logger = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]

ADMIN_REJECT_MESSAGE = "Sorry, this command is admin-only."


def require_admin(handler: Handler) -> Handler:
    """Gate a handler so only ADMIN_TELEGRAM_ID can invoke it.

    Non-admin callers get a polite reject; admins proceed. Admin id of 0 (unset)
    rejects everyone — fail-closed.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        admin_id = settings.ADMIN_TELEGRAM_ID
        if user is None or admin_id == 0 or user.id != admin_id:
            logger.info(
                "rejected admin command from %s (admin=%s)",
                user.id if user else "?",
                admin_id,
            )
            if update.effective_message is not None:
                await update.effective_message.reply_text(ADMIN_REJECT_MESSAGE)
            return
        await handler(update, context)

    return wrapper
