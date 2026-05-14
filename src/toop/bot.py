from __future__ import annotations

import logging

from telegram.ext import Application

from toop.config import settings
from toop.db import get_connection, init_db

logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point for the توپ bot."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    conn = get_connection(settings.DATABASE_PATH)
    init_db(conn)

    app = Application.builder().token(settings.BOT_TOKEN).build()

    logger.info("توپ starting (admin=%s, group=%s)", settings.ADMIN_TELEGRAM_ID, settings.GROUP_CHAT_ID)
    app.run_polling()


if __name__ == "__main__":
    main()
