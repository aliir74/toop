from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.i18n import indicator_label, t
from toop.voting_queue import PendingCounts, pending_counts_by_voter

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


# Completion-only stats: when they last voted, and how much they have given.
# What they still OWE is not computed here — voting_queue.pending_counts_by_voter
# owns that, so the offerable-target rules (pool membership, the ندیدمش cooldown,
# the revote window) have exactly one definition instead of a copy that drifts.
HEALTH_SQL = """
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
        AS last_30d
FROM players p
WHERE p.active = 1
"""


def build_health_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(HEALTH_SQL).fetchall()
    counts = pending_counts_by_voter(conn)
    out = []
    for r in rows:
        # .get, not [] — HEALTH_SQL selects every active player including
        # ghosts, so the two row sets are allowed to disagree.
        pending = counts.get(r["telegram_id"], PendingCounts(0, 0))
        out.append(
            {
                "telegram_id": r["telegram_id"],
                "display_name": r["display_name"],
                "last_voted": r["last_voted"],
                "last_voted_human": _humanize_age(r["last_voted"]),
                "lifetime": r["lifetime"],
                "last_30d": r["last_30d"],
                "pending": pending.unscored,
                "stale": pending.stale,
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
        return t("health.roster_empty")
    # The monospace table keeps latin/LTR headers on purpose: fixed-width column
    # alignment inside a ``` block breaks under RTL Persian, and this is an
    # admin-only technical diagnostic. Only the empty-state line is translated.
    header = f"{'Player':<16}{'Last vote':<12}{'Life':<6}{'30d':<6}{'New':<6}{'Stale':<7}{'Cal'}"
    sep = "-" * len(header)
    lines = [header, sep]
    for r in rows:
        name = r["display_name"][:15]
        lines.append(
            f"{name:<16}"
            f"{r['last_voted_human']:<12}"
            f"{r['lifetime']:<6}"
            f"{r['last_30d']:<6}"
            f"{r['pending']:<6}"
            f"{r['stale']:<7}"
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
        return t("coverage.not_enough")
    names = _name_lookup(conn)
    indicators = ("attack", "receive", "block", "setting", "serve", "positioning")
    lines = [t("coverage.header")]
    for r in rows:
        name = names.get(r["telegram_id"], f"#{r['telegram_id']}")
        labels = " · ".join(f"{indicator_label(ind)}: {r[ind]}" for ind in indicators)
        lines.append(t("coverage.row", name=name, labels=labels))
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
