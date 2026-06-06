from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType

from toop.handlers.voting import (
    _build_nudge_templates,
    handle_nudge,
    handle_start,
)
from toop.i18n import t
from toop.players import add_player
from toop.rating import INDICATORS

START_DM = t("vote.start_dm", "en")
START_GROUP = t("vote.start_group", "en")


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _dm_update() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=1, username="user1", full_name="User 1")
    chat = MagicMock()
    chat.type = ChatType.PRIVATE
    u.effective_chat = chat
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _group_update() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=1)
    chat = MagicMock()
    chat.type = ChatType.GROUP
    u.effective_chat = chat
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _admin_update() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _ctx(conn: sqlite3.Connection) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    return ctx


def _give_scores(conn: sqlite3.Connection, voter: int, player: int, n: int) -> None:
    for ind in INDICATORS[:n]:
        conn.execute(
            "INSERT INTO scores (voter_id, player_id, indicator, score) VALUES (?, ?, ?, 3)",
            (voter, player, ind),
        )
    conn.commit()


async def test_start_dm_friendly_intro(conn: sqlite3.Connection) -> None:
    update = _dm_update()
    await handle_start(update, _ctx(conn))
    update.effective_message.reply_text.assert_awaited_once_with(START_DM)


async def test_start_group_shorter(conn: sqlite3.Connection) -> None:
    update = _group_update()
    await handle_start(update, _ctx(conn))
    update.effective_message.reply_text.assert_awaited_once_with(START_GROUP)


def test_nudge_templates_sort_ascending_completion(conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    add_player(conn, 3, "Carol", "carol")
    # Alice gives 5 ratings, Carol gives 2, Bob gives 0.
    _give_scores(conn, 1, 2, 5)
    _give_scores(conn, 3, 1, 2)
    templates = _build_nudge_templates(conn, limit=5)
    assert "Bob" in templates[0]
    assert "Carol" in templates[1]
    assert "Alice" in templates[2]
    assert "0 lifetime ratings" in templates[0]


async def test_nudge_admin_only(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    update = _admin_update()
    await handle_nudge(update, _ctx(conn))
    update.effective_message.reply_text.assert_awaited_once()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Alice" in reply
    assert "Manual sends only" in reply


async def test_nudge_blocked_for_non_admin(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update()
    update.effective_user = MagicMock(id=99)
    await handle_nudge(update, _ctx(conn))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "admin-only" in reply.lower()
