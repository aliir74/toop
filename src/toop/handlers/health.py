from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from toop.admin import require_admin
from toop.config import settings

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


HEALTH_SQL = """
SELECT
    p.telegram_id,
    p.display_name,
    p.is_calibrating,
    (SELECT MAX(answered_at) FROM answered_prompts ap WHERE ap.voter_id = p.telegram_id)
        AS last_voted,
    (SELECT COUNT(*) FROM answered_prompts ap WHERE ap.voter_id = p.telegram_id)
        AS lifetime,
    (SELECT COUNT(*) FROM answered_prompts ap
     WHERE ap.voter_id = p.telegram_id AND ap.answered_at >= DATE('now', '-30 days'))
        AS last_30d,
    (SELECT COUNT(*) FROM pending_prompts pp WHERE pp.voter_id = p.telegram_id)
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


# /coverage — most under-sampled pairs across the group, per axis.

COVERAGE_SQL = """
WITH active AS (
    SELECT telegram_id FROM players WHERE active=1
),
pairs AS (
    SELECT a.telegram_id AS pa, b.telegram_id AS pb
    FROM active a JOIN active b ON a.telegram_id < b.telegram_id
)
SELECT
    p.pa,
    p.pb,
    COALESCE((SELECT a_wins + b_wins FROM vote_aggregates va
              WHERE va.player_a=p.pa AND va.player_b=p.pb AND va.axis='attack'), 0)
        AS attack_total,
    COALESCE((SELECT a_wins + b_wins FROM vote_aggregates va
              WHERE va.player_a=p.pa AND va.player_b=p.pb AND va.axis='defense'), 0)
        AS defense_total,
    COALESCE((SELECT a_wins + b_wins FROM vote_aggregates va
              WHERE va.player_a=p.pa AND va.player_b=p.pb AND va.axis='setting'), 0)
        AS setting_total
FROM pairs p
ORDER BY attack_total + defense_total + setting_total ASC, p.pa, p.pb
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
    lines = ["Coverage gaps (least-sampled pairs):"]
    for r in rows:
        name_a = names.get(r["pa"], f"#{r['pa']}")
        name_b = names.get(r["pb"], f"#{r['pb']}")
        lines.append(
            f"• {name_a} vs {name_b} — "
            f"attack: {r['attack_total']} · "
            f"defense: {r['defense_total']} · "
            f"setting: {r['setting_total']}"
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
    "settings",  # re-exported for monkeypatch convenience in tests
]
