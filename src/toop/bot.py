from __future__ import annotations

import logging
from datetime import UTC, datetime, time

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from toop.config import settings
from toop.db import get_connection, init_db
from toop.handlers.alerts import dk_alert_job
from toop.handlers.health import handle_coverage, handle_health
from toop.handlers.ops import handle_backup_db, handle_version
from toop.handlers.ratings import handle_refresh_ratings
from toop.handlers.roster import (
    handle_add_ghost,
    handle_add_player,
    handle_contacts,
    handle_disable_callback,
    handle_disable_voting,
    handle_dk_report,
    handle_enable_voting,
    handle_link_player,
    handle_list_players,
    handle_pause_voting,
    handle_remove_callback,
    handle_remove_player,
    handle_rename,
    handle_rename_callback,
    handle_rename_text,
)
from toop.handlers.rsvp import handle_lock_in, handle_rsvp_callback
from toop.handlers.sessions import (
    handle_close_session,
    handle_list_sessions,
    handle_open_session,
)
from toop.handlers.snapshot import (
    auto_snapshot_job,
    handle_publish,
    handle_snapshot,
    handle_swap,
    handle_teams,
)
from toop.handlers.voting import (
    handle_nudge,
    handle_start,
    handle_vote_callback,
    handle_vote_command,
)
from toop.sessions import WEEKDAY_INDEX

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
    app.bot_data["started_at"] = datetime.now(UTC)

    app.add_handler(CommandHandler("add_player", handle_add_player))
    app.add_handler(CommandHandler("remove_player", handle_remove_player))
    app.add_handler(CommandHandler("pause_voting", handle_pause_voting))
    app.add_handler(CommandHandler("disable_voting", handle_disable_voting))
    app.add_handler(CommandHandler("enable_voting", handle_enable_voting))
    app.add_handler(CommandHandler("dk_report", handle_dk_report))
    app.add_handler(CommandHandler("add_ghost", handle_add_ghost))
    app.add_handler(CommandHandler("link_player", handle_link_player))
    app.add_handler(CommandHandler("list_players", handle_list_players))
    app.add_handler(CommandHandler("rename", handle_rename))
    app.add_handler(CommandHandler("contacts", handle_contacts))
    app.add_handler(CommandHandler("open_session", handle_open_session))
    app.add_handler(CommandHandler("close_session", handle_close_session))
    app.add_handler(CommandHandler("sessions", handle_list_sessions))
    app.add_handler(CommandHandler("lock_in", handle_lock_in))
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("vote", handle_vote_command))
    app.add_handler(CommandHandler("nudge", handle_nudge))
    app.add_handler(CommandHandler("refresh_ratings", handle_refresh_ratings))
    app.add_handler(CommandHandler("snapshot", handle_snapshot))
    app.add_handler(CommandHandler("teams", handle_teams))
    app.add_handler(CommandHandler("swap", handle_swap))
    app.add_handler(CommandHandler("publish", handle_publish))
    app.add_handler(CommandHandler("health", handle_health))
    app.add_handler(CommandHandler("coverage", handle_coverage))
    app.add_handler(CommandHandler("version", handle_version))
    app.add_handler(CommandHandler("backup_db", handle_backup_db))

    if app.job_queue is not None:
        weekday = WEEKDAY_INDEX[settings.SESSION_WEEKDAY.lower()]
        app.job_queue.run_daily(
            auto_snapshot_job,
            time=time(hour=settings.SNAPSHOT_HOUR, minute=0, tzinfo=UTC),
            days=(weekday,),
            name="auto_snapshot",
        )
        logger.info(
            "auto_snapshot scheduled: weekday=%s hour=%s UTC",
            settings.SESSION_WEEKDAY,
            settings.SNAPSHOT_HOUR,
        )
        app.job_queue.run_daily(
            dk_alert_job,
            time=time(hour=settings.SNAPSHOT_HOUR, minute=0, tzinfo=UTC),
            name="dk_alert",
        )
        logger.info("dk_alert scheduled daily at hour=%s UTC", settings.SNAPSHOT_HOUR)
    app.add_handler(CallbackQueryHandler(handle_rsvp_callback, pattern=r"^rsvp:"))
    app.add_handler(CallbackQueryHandler(handle_vote_callback, pattern=r"^v:"))
    app.add_handler(CallbackQueryHandler(handle_rename_callback, pattern=r"^rename:"))
    app.add_handler(CallbackQueryHandler(handle_remove_callback, pattern=r"^rmpick:"))
    app.add_handler(CallbackQueryHandler(handle_disable_callback, pattern=r"^dispick:"))
    # Lower-priority group so /commands still reach their CommandHandler above;
    # this only consumes a private text message when a rename is pending.
    app.add_handler(
        MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_rename_text),
        group=1,
    )

    logger.info(
        "توپ starting (admin=%s, group=%s)",
        settings.ADMIN_TELEGRAM_ID,
        settings.GROUP_CHAT_ID,
    )
    app.run_polling()  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    main()
