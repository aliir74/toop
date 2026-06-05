from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from toop.admin import require_admin

logger = logging.getLogger(__name__)


def _conn(context: ContextTypes.DEFAULT_TYPE) -> sqlite3.Connection:
    conn = context.bot_data.get("conn")
    if conn is None:
        raise RuntimeError("DB connection missing from bot_data")
    return conn


def _humanize_age(answered_at: str | None) -> str:
    if not answered_at:
        return "never"
    try:
        ts = datetime.fromisoformat(answered_at.replace(" ", "T"))
    except ValueError:
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - ts
    days = delta.days
    if days >= 1:
        return f"{days}d ago"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours}h ago"
    return "today"


def _calibration_marker(is_calibrating: bool, lifetime: int) -> str:
    if not is_calibrating:
        return "✓"
    return "⚠" if lifetime > 0 else "✗"


# `pending` is the exact count of (rateable player, indicator) targets this voter
# hasn't scored or skipped — mirrors voting_queue.select_next_score_target.
HEALTH_SQL = """
WITH indicators(indicator) AS (
    VALUES ('attack'), ('receive'), ('block'), ('setting'), ('serve'), ('positioning')
)
SELECT
    p.telegram_id,
    p.display_name,
    p.is_calibrating,
    (SELECT MAX(updated_at) FROM scores s WHERE s.voter_id = p.telegram_id)
        AS last_voted,
    (SELECT COUNT(*) FROM scores s WHERE s.voter_id = p.telegram_id)
        AS lifetime,
    (SELECT COUNT(*) FROM scores s
     WHERE s.voter_id = p.telegram_id AND s.updated_at >= DATE('now', '-30 days'))
        AS last_30d,
    (SELECT COUNT(*) FROM players rp CROSS JOIN indicators i
     WHERE rp.active = 1 AND rp.in_pool = 1
       AND (rp.pool_paused_until IS NULL OR rp.pool_paused_until <= CURRENT_TIMESTAMP)
       AND rp.telegram_id != p.telegram_id
       AND NOT EXISTS (SELECT 1 FROM scores s
            WHERE s.voter_id = p.telegram_id AND s.player_id = rp.telegram_id
              AND s.indicator = i.indicator)
       AND NOT EXISTS (SELECT 1 FROM score_skips sk
            WHERE sk.voter_id = p.telegram_id AND sk.player_id = rp.telegram_id
              AND sk.indicator = i.indicator))
        AS pending
FROM players p
WHERE p.active = 1
"""


def build_health_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(HEALTH_SQL).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "telegram_id": r["telegram_id"],
                "display_name": r["display_name"],
                "last_voted": r["last_voted"],
                "last_voted_human": _humanize_age(r["last_voted"]),
                "lifetime": r["lifetime"],
                "last_30d": r["last_30d"],
                "pending": r["pending"],
                "calibration": _calibration_marker(bool(r["is_calibrating"]), r["lifetime"]),
            }
        )

    def _sort_key(row: dict) -> tuple:
        if row["last_voted"] is None:
            return (0, 0, row["display_name"].lower())
        try:
            ts = datetime.fromisoformat(row["last_voted"].replace(" ", "T"))
        except ValueError:
            return (0, 0, row["display_name"].lower())
        return (1, ts.timestamp(), row["display_name"].lower())

    out.sort(key=_sort_key)
    return out


def format_health(rows: list[dict]) -> str:
    if not rows:
        return "Roster is empty."
    header = f"{'Player':<16}{'Last vote':<12}{'Lifetime':<10}{'30d':<6}{'Pending':<9}{'Cal'}"
    sep = "-" * len(header)
    lines = [header, sep]
    for r in rows:
        name = r["display_name"][:15]
        lines.append(
            f"{name:<16}"
            f"{r['last_voted_human']:<12}"
            f"{r['lifetime']:<10}"
            f"{r['last_30d']:<6}"
            f"{r['pending']:<9}"
            f"{r['calibration']}"
        )
    return "```\n" + "\n".join(lines) + "\n```"


@require_admin
async def handle_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    rows = build_health_rows(_conn(context))
    await message.reply_text(format_health(rows), parse_mode=ParseMode.MARKDOWN)


# /coverage — the active players with the fewest ratings, per indicator.

COVERAGE_SQL = """
SELECT
    p.telegram_id,
    (SELECT COUNT(*) FROM scores s WHERE s.player_id=p.telegram_id AND s.indicator='attack')
        AS attack,
    (SELECT COUNT(*) FROM scores s WHERE s.player_id=p.telegram_id AND s.indicator='receive')
        AS receive,
    (SELECT COUNT(*) FROM scores s WHERE s.player_id=p.telegram_id AND s.indicator='block')
        AS block,
    (SELECT COUNT(*) FROM scores s WHERE s.player_id=p.telegram_id AND s.indicator='setting')
        AS setting,
    (SELECT COUNT(*) FROM scores s WHERE s.player_id=p.telegram_id AND s.indicator='serve')
        AS serve,
    (SELECT COUNT(*) FROM scores s WHERE s.player_id=p.telegram_id AND s.indicator='positioning')
        AS positioning,
    (SELECT COUNT(*) FROM scores s WHERE s.player_id=p.telegram_id) AS total
FROM players p
WHERE p.active = 1
ORDER BY total ASC, p.telegram_id
LIMIT ?
"""


def _name_lookup(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT telegram_id, display_name FROM players WHERE active=1").fetchall()
    return {r["telegram_id"]: r["display_name"] for r in rows}


def build_coverage(conn: sqlite3.Connection, limit: int = 10) -> str:
    rows = conn.execute(COVERAGE_SQL, (limit,)).fetchall()
    if not rows:
        return "Not enough players to compute coverage."
    names = _name_lookup(conn)
    lines = ["Coverage gaps (least-rated players):"]
    for r in rows:
        name = names.get(r["telegram_id"], f"#{r['telegram_id']}")
        lines.append(
            f"• {name} — "
            f"attack: {r['attack']} · "
            f"receive: {r['receive']} · "
            f"block: {r['block']} · "
            f"setting: {r['setting']} · "
            f"serve: {r['serve']} · "
            f"positioning: {r['positioning']}"
        )
    return "\n".join(lines)


@require_admin
async def handle_coverage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(build_coverage(_conn(context), limit=10))


__all__ = [
    "handle_health",
    "handle_coverage",
    "build_health_rows",
    "format_health",
    "build_coverage",
]
