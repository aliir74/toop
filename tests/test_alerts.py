from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import TelegramError

from toop.handlers.alerts import dk_alert_job
from toop.players import add_player


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "toop.handlers.alerts.settings",
        MagicMock(
            ADMIN_TELEGRAM_ID=42, DK_ALERT_MIN_PROMPTS=3, DK_ALERT_RATE=0.5, DEFAULT_PAUSE_DAYS=14
        ),
    )


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.bot = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return ctx


def _agg(conn: sqlite3.Connection, a: int, b: int, dk: int, aw: int = 0, bw: int = 0) -> None:
    conn.execute(
        "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins, dont_know) "
        "VALUES (?, ?, 'attack', ?, ?, ?)",
        (a, b, aw, bw, dk),
    )
    conn.commit()


async def test_dk_alert_dms_admin_for_flagged_player(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Unknown", "unknown")
    add_player(conn, 2, "Known", "known")
    # Pair (1,2): 4 don't-know, 1+1 winners → player 1 dk_count 4, total 6, rate .67.
    _agg(conn, 1, 2, dk=4, aw=1, bw=1)
    ctx = _ctx(conn)
    await dk_alert_job(ctx)
    ctx.bot.send_message.assert_awaited_once()
    kwargs = ctx.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert "/pause_voting 1 14d" in kwargs["text"]


async def test_dk_alert_no_flag_no_dm(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Fine", "fine")
    add_player(conn, 2, "Fine2", "fine2")
    _agg(conn, 1, 2, dk=1, aw=5, bw=5)  # low dk count and rate
    ctx = _ctx(conn)
    await dk_alert_job(ctx)
    ctx.bot.send_message.assert_not_awaited()


async def test_dk_alert_skips_already_paused(conn: sqlite3.Connection) -> None:
    # Player 1 is the only one over threshold: dk spread across two pairs (4 total),
    # while partners 2 and 3 each sit at dk=2 (< MIN of 3).
    add_player(conn, 1, "Unknown", "unknown")
    add_player(conn, 2, "Known", "known")
    add_player(conn, 3, "Other", "other")
    _agg(conn, 1, 2, dk=2)
    _agg(conn, 1, 3, dk=2)
    future = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE players SET pool_paused_until=? WHERE telegram_id=1", (future,))
    conn.commit()
    ctx = _ctx(conn)
    await dk_alert_job(ctx)
    ctx.bot.send_message.assert_not_awaited()


async def test_dk_alert_admin_unset_skips(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "toop.handlers.alerts.settings",
        MagicMock(
            ADMIN_TELEGRAM_ID=0, DK_ALERT_MIN_PROMPTS=3, DK_ALERT_RATE=0.5, DEFAULT_PAUSE_DAYS=14
        ),
    )
    add_player(conn, 1, "Unknown", "unknown")
    add_player(conn, 2, "Known", "known")
    _agg(conn, 1, 2, dk=4, aw=1, bw=1)
    ctx = _ctx(conn)
    await dk_alert_job(ctx)
    ctx.bot.send_message.assert_not_awaited()


async def test_dk_alert_handles_telegram_error(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    add_player(conn, 1, "Unknown", "unknown")
    add_player(conn, 2, "Known", "known")
    _agg(conn, 1, 2, dk=4, aw=1, bw=1)
    ctx = _ctx(conn)
    ctx.bot.send_message = AsyncMock(side_effect=TelegramError("blocked"))
    with caplog.at_level(logging.WARNING, logger="toop.handlers.alerts"):
        await dk_alert_job(ctx)
    assert any("failed to DM admin" in r.message for r in caplog.records)


def test_alerts_conn_missing_raises() -> None:
    from toop.handlers.alerts import _conn

    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)
