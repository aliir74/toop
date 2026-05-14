from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.config import settings

logger = logging.getLogger(__name__)


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _commit_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[3],
            timeout=2,
        )
        return out.stdout.strip() or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


@require_admin
async def handle_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    started_at = context.bot_data.get("started_at")
    uptime_text = "?"
    if isinstance(started_at, datetime):
        uptime_text = _format_uptime((datetime.now(UTC) - started_at).total_seconds())
    sha = _commit_sha()
    await message.reply_text(f"توپ commit `{sha}` · uptime {uptime_text}", parse_mode="Markdown")


@require_admin
async def handle_backup_db(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    src = Path(settings.DATABASE_PATH)
    if not src.exists():
        await message.reply_text(f"DB file not found at {src}")
        return
    backup_dir = src.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dst = backup_dir / f"toop-{stamp}.db"
    # Use SQLite's online backup so we don't race the running connection.
    conn = _conn(context)
    backup_conn = sqlite3.connect(str(dst))
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()
    size_kb = dst.stat().st_size // 1024
    await message.reply_text(f"💾 Backup saved → `{dst}` ({size_kb} KB)", parse_mode="Markdown")


__all__ = [
    "handle_version",
    "handle_backup_db",
    "_format_uptime",
    "_commit_sha",
    "shutil",  # re-export for tests; unused otherwise
]
