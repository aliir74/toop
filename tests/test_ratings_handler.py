from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from toop.handlers.ratings import _conn, handle_refresh_ratings
from toop.players import add_player


@pytest.fixture(autouse=True)
def patch_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    monkeypatch.setattr("toop.handlers.ratings.settings", MagicMock(CALIBRATION_THRESHOLD=5))


def _update(user_id: int = 42, with_message: bool = True) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=user_id)
    if with_message:
        msg = MagicMock()
        msg.reply_text = AsyncMock()
        u.effective_message = msg
    else:
        u.effective_message = None
    return u


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    return ctx


def test_conn_returns_connection(conn: sqlite3.Connection) -> None:
    assert _conn(_ctx(conn)) is conn


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


async def test_refresh_ratings_rejects_non_admin(conn: sqlite3.Connection) -> None:
    update = _update(user_id=999)
    await handle_refresh_ratings(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "admin-only" in reply


async def test_refresh_ratings_returns_early_without_message(
    conn: sqlite3.Connection,
) -> None:
    update = _update(with_message=False)
    # Admin passes the guard, but no message to reply to — must return cleanly.
    await handle_refresh_ratings(update, _ctx(conn))


async def test_refresh_ratings_refits_and_replies(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    update = _update()
    await handle_refresh_ratings(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Refit ratings" in reply
    assert "rows" in reply
