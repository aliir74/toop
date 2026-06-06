from __future__ import annotations

import logging
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

from toop.commands import menu_commands
from toop.config import settings
from toop.db import get_connection, init_db
from toop.handlers.alerts import dk_alert_job
from toop.handlers.change_player import (
    handle_change_player,
    handle_change_promote_callback,
    handle_change_remove_callback,
)
from toop.handlers.health import handle_coverage, handle_health
from toop.handlers.help import handle_help
from toop.handlers.ops import handle_backup_db, handle_version
from toop.handlers.poll import handle_poll_answer, weekly_attendance_job
from toop.handlers.roster import (
    handle_add_ghost,
    handle_add_pick_callback,
    handle_add_player,
    handle_add_player_text,
    handle_contacts,
    handle_disable_callback,
    handle_disable_voting,
    handle_dk_report,
    handle_enable_callback,
    handle_enable_voting,
    handle_link_ghost_callback,
    handle_link_player,
    handle_link_real_callback,
    handle_list_players,
    handle_pause_dur_callback,
    handle_pause_pick_callback,
    handle_pause_voting,
    handle_remove_callback,
    handle_remove_player,
    handle_rename,
    handle_rename_callback,
    handle_rename_text,
    handle_set_photo,
    handle_set_photo_callback,
    handle_set_photo_photo,
    handle_set_photo_text,
    handle_unset_photo,
    handle_unset_photo_callback,
)
from toop.handlers.sessions import (
    handle_list_sessions,
    handle_open_session,
)
from toop.handlers.snapshot import (
    auto_snapshot_job,
    handle_publish,
    handle_snapshot,
    handle_swap,
)
from toop.handlers.voting import (
    handle_nudge,
    handle_start,
    handle_vote_callback,
    handle_vote_command,
)
from toop.sessions import WEEKDAY_INDEX

logger = logging.getLogger(__name__)


async def _register_commands(app: Application) -> None:
    """Push the command list to Telegram so the `/` menu stays in sync.

    Public commands go to the default scope (every user); the full list goes to
    the admin's private chat — a chat-scope set_my_commands replaces the default
    for that chat, so the admin needs the public commands included too. Admin id
    of 0 (unset) skips the admin scope entirely.
    """

    def _to_bot_commands(admin: bool) -> list[BotCommand]:
        return [BotCommand(c.name, c.short()) for c in menu_commands(admin=admin)]

    await app.bot.set_my_commands(_to_bot_commands(admin=False), scope=BotCommandScopeDefault())
    admin_id = settings.ADMIN_TELEGRAM_ID
    if admin_id != 0:
        await app.bot.set_my_commands(
            _to_bot_commands(admin=True), scope=BotCommandScopeChat(chat_id=admin_id)
        )


def main() -> None:
    """Entry point for the توپ bot."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    settings.require_runtime()
    conn = get_connection(settings.DATABASE_PATH)
    init_db(conn)

    app = Application.builder().token(settings.BOT_TOKEN).post_init(_register_commands).build()
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
    app.add_handler(CommandHandler("set_photo", handle_set_photo))
    app.add_handler(CommandHandler("unset_photo", handle_unset_photo))
    app.add_handler(CommandHandler("contacts", handle_contacts))
    app.add_handler(CommandHandler("open_session", handle_open_session))
    app.add_handler(CommandHandler("sessions", handle_list_sessions))
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("vote", handle_vote_command))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("nudge", handle_nudge))
    app.add_handler(CommandHandler("snapshot", handle_snapshot))
    app.add_handler(CommandHandler("swap", handle_swap))
    app.add_handler(CommandHandler("change_player", handle_change_player))
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
        poll_weekday = WEEKDAY_INDEX[settings.SESSION_POLL_WEEKDAY.lower()]
        app.job_queue.run_daily(
            weekly_attendance_job,
            time=time(
                hour=settings.SESSION_POLL_HOUR, minute=0, tzinfo=ZoneInfo(settings.SESSION_POLL_TZ)
            ),
            days=(poll_weekday,),
            name="attendance_poll",
        )
        logger.info(
            "attendance_poll scheduled: weekday=%s hour=%s %s",
            settings.SESSION_POLL_WEEKDAY,
            settings.SESSION_POLL_HOUR,
            settings.SESSION_POLL_TZ,
        )
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(CallbackQueryHandler(handle_vote_callback, pattern=r"^v:"))
    app.add_handler(CallbackQueryHandler(handle_rename_callback, pattern=r"^rename:"))
    app.add_handler(CallbackQueryHandler(handle_set_photo_callback, pattern=r"^setphoto:"))
    app.add_handler(CallbackQueryHandler(handle_unset_photo_callback, pattern=r"^unsetphoto:"))
    app.add_handler(CallbackQueryHandler(handle_remove_callback, pattern=r"^rmpick:"))
    app.add_handler(CallbackQueryHandler(handle_disable_callback, pattern=r"^dispick:"))
    app.add_handler(CallbackQueryHandler(handle_change_remove_callback, pattern=r"^cprm:"))
    app.add_handler(CallbackQueryHandler(handle_change_promote_callback, pattern=r"^cpadd:"))
    app.add_handler(CallbackQueryHandler(handle_enable_callback, pattern=r"^enpick:"))
    app.add_handler(CallbackQueryHandler(handle_pause_pick_callback, pattern=r"^pausepick:"))
    app.add_handler(CallbackQueryHandler(handle_pause_dur_callback, pattern=r"^pausedur:"))
    app.add_handler(CallbackQueryHandler(handle_link_ghost_callback, pattern=r"^lnkghost:"))
    app.add_handler(CallbackQueryHandler(handle_link_real_callback, pattern=r"^lnkreal:"))
    app.add_handler(CallbackQueryHandler(handle_add_pick_callback, pattern=r"^addpick:"))
    # Lower-priority groups so /commands still reach their CommandHandler above.
    # Each consumes a private text only when its own flow is pending; rename and
    # add live in separate groups so both get to inspect every private message.
    app.add_handler(
        MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_rename_text),
        group=1,
    )
    app.add_handler(
        MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_add_player_text),
        group=2,
    )
    # /set_photo: a private photo fulfils a pending capture; a private text while
    # pending nudges or cancels. Separate groups so each sees every DM message.
    app.add_handler(
        MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_set_photo_photo),
        group=3,
    )
    app.add_handler(
        MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_set_photo_text),
        group=4,
    )

    logger.info(
        "توپ starting (admin=%s, group=%s)",
        settings.ADMIN_TELEGRAM_ID,
        settings.GROUP_CHAT_ID,
    )
    app.run_polling()  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    main()
