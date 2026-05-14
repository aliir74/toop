from __future__ import annotations

import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from toop.config import settings
from toop.db import get_connection, init_db
from toop.handlers.roster import (
    handle_add_player,
    handle_list_players,
    handle_remove_player,
)
from toop.handlers.rsvp import handle_lock_in, handle_rsvp_callback
from toop.handlers.sessions import (
    handle_close_session,
    handle_list_sessions,
    handle_open_session,
)
from toop.handlers.voting import (
    handle_nudge,
    handle_start,
    handle_vote_callback,
    handle_vote_command,
)

logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point for the توپ bot."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    settings.require_runtime()
    conn = get_connection(settings.DATABASE_PATH)
    init_db(conn)

    app = Application.builder().token(settings.BOT_TOKEN).build()
    app.bot_data["conn"] = conn

    app.add_handler(CommandHandler("add_player", handle_add_player))
    app.add_handler(CommandHandler("remove_player", handle_remove_player))
    app.add_handler(CommandHandler("list_players", handle_list_players))
    app.add_handler(CommandHandler("open_session", handle_open_session))
    app.add_handler(CommandHandler("close_session", handle_close_session))
    app.add_handler(CommandHandler("sessions", handle_list_sessions))
    app.add_handler(CommandHandler("lock_in", handle_lock_in))
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("vote", handle_vote_command))
    app.add_handler(CommandHandler("nudge", handle_nudge))
    app.add_handler(CallbackQueryHandler(handle_rsvp_callback, pattern=r"^rsvp:"))
    app.add_handler(CallbackQueryHandler(handle_vote_callback, pattern=r"^v:"))

    logger.info(
        "توپ starting (admin=%s, group=%s)",
        settings.ADMIN_TELEGRAM_ID,
        settings.GROUP_CHAT_ID,
    )
    app.run_polling()


if __name__ == "__main__":
    main()
