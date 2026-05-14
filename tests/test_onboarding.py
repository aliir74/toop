from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.constants import ChatType

from toop.handlers.voting import (
    START_DM,
    START_GROUP,
    _build_nudge_templates,
    handle_nudge,
    handle_start,
)
from toop.players import add_player
from toop.voting_queue import bootstrap_calibration_prompts


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _dm_update() -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=1)
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


def test_bootstrap_creates_9_prompts_with_3_veterans(conn: sqlite3.Connection) -> None:
    for i in range(1, 5):
        add_player(conn, i, f"P{i}", f"p{i}")
    # Promote 3 veterans (graduate them)
    conn.execute("UPDATE players SET is_calibrating=0 WHERE telegram_id IN (1, 2, 3)")
    conn.commit()
    # Add the new player and bootstrap from them
    add_player(conn, 100, "Newcomer", "newcomer")
    inserted = bootstrap_calibration_prompts(conn, new_player_id=100)
    assert inserted == 9
    rows = conn.execute(
        "SELECT voter_id, COUNT(*) AS n FROM pending_prompts GROUP BY voter_id"
    ).fetchall()
    voter_counts = {r["voter_id"]: r["n"] for r in rows}
    assert len(voter_counts) == 3
    for n in voter_counts.values():
        assert n == 3


def test_bootstrap_falls_back_when_no_veterans(conn: sqlite3.Connection) -> None:
    for i in range(1, 5):
        add_player(conn, i, f"P{i}", f"p{i}")
    # All still calibrating — fallback should still pick 3 random players
    add_player(conn, 100, "Newcomer", "newcomer")
    inserted = bootstrap_calibration_prompts(conn, new_player_id=100)
    assert inserted == 9


def test_bootstrap_with_only_2_players_returns_zero_prompts(conn: sqlite3.Connection) -> None:
    """A 2-player roster has no third 'anchor' to compare against."""
    add_player(conn, 1, "Vet", "vet")
    conn.execute("UPDATE players SET is_calibrating=0 WHERE telegram_id=1")
    conn.commit()
    add_player(conn, 100, "Newcomer", "newcomer")
    inserted = bootstrap_calibration_prompts(conn, new_player_id=100)
    assert inserted == 0


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
    # Alice has 5 answered, Bob has 0, Carol has 2
    for axis in ("attack", "defense", "setting", "attack", "defense"):
        conn.execute(
            "INSERT OR IGNORE INTO answered_prompts (voter_id, player_a, player_b, axis) "
            "VALUES (?, ?, ?, ?)",
            (1, 2, 3, axis),
        )
    for axis in ("attack", "defense"):
        conn.execute(
            "INSERT INTO answered_prompts (voter_id, player_a, player_b, axis) VALUES (?, ?, ?, ?)",
            (3, 1, 2, axis),
        )
    conn.commit()
    templates = _build_nudge_templates(conn, limit=5)
    # Bob (0) first, Carol (2) second, Alice (5) third
    assert "Bob" in templates[0]
    assert "Carol" in templates[1]
    assert "Alice" in templates[2]
    assert "0 lifetime votes" in templates[0]


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
