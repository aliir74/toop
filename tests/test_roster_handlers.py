from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from toop.contacts import upsert_contact
from toop.handlers.roster import (
    handle_add_ghost,
    handle_add_player,
    handle_contacts,
    handle_disable_voting,
    handle_dk_report,
    handle_enable_voting,
    handle_link_player,
    handle_list_players,
    handle_pause_voting,
    handle_remove_player,
)
from toop.players import add_ghost_player, add_player, list_active_players


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))


def _admin_update(text: str) -> MagicMock:
    u = MagicMock()
    u.effective_user = MagicMock(id=42)
    msg = MagicMock()
    msg.text = text
    msg.reply_text = AsyncMock()
    u.effective_message = msg
    return u


def _context(conn: sqlite3.Connection, args: list[str], chat_id: int | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"conn": conn}
    ctx.args = args
    bot = MagicMock()
    if chat_id is None:
        bot.get_chat = AsyncMock(side_effect=BadRequest("chat not found"))
    else:
        bot.get_chat = AsyncMock(return_value=MagicMock(id=chat_id))
    ctx.bot = bot
    return ctx


async def test_add_player_round_trip(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_player @alice "Alice Smith"')
    ctx = _context(conn, args=["@alice", '"Alice', 'Smith"'], chat_id=111)
    await handle_add_player(update, ctx)
    players = list_active_players(conn)
    assert len(players) == 1
    assert players[0].telegram_id == 111
    assert players[0].display_name == "Alice Smith"
    update.effective_message.reply_text.assert_awaited_once()


async def test_add_player_unknown_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_player @ghost "Ghost"')
    ctx = _context(conn, args=["@ghost", '"Ghost"'], chat_id=None)
    await handle_add_player(update, ctx)
    assert list_active_players(conn) == []
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "DM me /start" in reply


async def test_add_player_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/add_player @alice")
    ctx = _context(conn, args=["@alice"])
    await handle_add_player(update, ctx)
    update.effective_message.reply_text.assert_awaited_once()
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_add_player_by_id_success(admin_settings: None, conn: sqlite3.Connection) -> None:
    # Contact has DM'd the bot and carries a username — add purely by numeric id.
    upsert_contact(conn, 7290468940, username="meysam", display_name="Meysam Bz")
    update = _admin_update('/add_player 7290468940 "Meysam Bz"')
    # chat_id=None so the @handle resolution path would fail if it were taken.
    ctx = _context(conn, args=["7290468940", '"Meysam', 'Bz"'], chat_id=None)
    await handle_add_player(update, ctx)
    players = list_active_players(conn)
    assert len(players) == 1
    assert players[0].telegram_id == 7290468940
    assert players[0].display_name == "Meysam Bz"
    assert players[0].username == "meysam"


async def test_add_player_by_id_null_username(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    # No-username contact — the whole point of add-by-id.
    upsert_contact(conn, 5299711301, username=None, display_name="SHH")
    update = _admin_update('/add_player 5299711301 "SHH"')
    ctx = _context(conn, args=["5299711301", '"SHH"'], chat_id=None)
    await handle_add_player(update, ctx)
    players = list_active_players(conn)
    assert len(players) == 1
    assert players[0].telegram_id == 5299711301
    assert players[0].username is None
    assert "(no username)" in update.effective_message.reply_text.await_args.args[0]


async def test_add_player_by_id_not_a_contact(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    # Unknown id with no contacts row — can't be DM'd later, so reject.
    update = _admin_update('/add_player 999 "Ghost"')
    ctx = _context(conn, args=["999", '"Ghost"'], chat_id=None)
    await handle_add_player(update, ctx)
    assert list_active_players(conn) == []
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "hasn't DM'd the bot yet" in reply
    assert "999" in reply


async def test_add_player_unknown_username_points_to_id_path(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update('/add_player @ghost "Ghost"')
    ctx = _context(conn, args=["@ghost", '"Ghost"'], chat_id=None)
    await handle_add_player(update, ctx)
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "/contacts" in reply
    assert "/add_player <id>" in reply


async def test_remove_player_round_trip(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_update = _admin_update('/add_player @alice "Alice"')
    add_ctx = _context(conn, args=["@alice", '"Alice"'], chat_id=111)
    await handle_add_player(add_update, add_ctx)

    remove_update = _admin_update("/remove_player @alice")
    remove_ctx = _context(conn, args=["@alice"], chat_id=111)
    await handle_remove_player(remove_update, remove_ctx)
    assert list_active_players(conn) == []


async def test_list_players_empty(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/list_players")
    ctx = _context(conn, args=[])
    await handle_list_players(update, ctx)
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "empty" in reply.lower()


async def test_list_players_with_calibration_marker(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_update = _admin_update('/add_player @alice "Alice"')
    add_ctx = _context(conn, args=["@alice", '"Alice"'], chat_id=111)
    await handle_add_player(add_update, add_ctx)

    list_update = _admin_update("/list_players")
    list_ctx = _context(conn, args=[])
    await handle_list_players(list_update, list_ctx)
    reply = list_update.effective_message.reply_text.await_args.args[0]
    assert "Alice" in reply
    assert "calibrating" in reply


async def test_non_admin_blocked(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr("toop.admin.settings", MagicMock(ADMIN_TELEGRAM_ID=42))
    update = _admin_update('/add_player @alice "Alice"')
    update.effective_user = MagicMock(id=99)
    ctx = _context(conn, args=["@alice", '"Alice"'], chat_id=111)
    await handle_add_player(update, ctx)
    assert list_active_players(conn) == []


# ----- branch coverage additions -----

from toop.handlers.roster import _conn, _parse_add_args  # noqa: E402


def test_conn_raises_when_missing() -> None:
    ctx = MagicMock()
    ctx.bot_data = {}
    with pytest.raises(RuntimeError, match="DB connection missing"):
        _conn(ctx)


def test_parse_add_args_unbalanced_quote() -> None:
    assert _parse_add_args('/add_player @alice "Unclosed') is None


def test_parse_add_args_too_few_tokens() -> None:
    assert _parse_add_args("/add_player @alice") is None


def test_parse_add_args_empty_username() -> None:
    assert _parse_add_args('/add_player @ "Name"') is None


def test_parse_add_args_numeric_id() -> None:
    assert _parse_add_args('/add_player 7290468940 "Meysam Bz"') == (7290468940, "Meysam Bz")


def test_parse_add_args_empty_display_name() -> None:
    assert _parse_add_args('/add_player @alice ""') is None


async def test_add_player_no_text(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_player @a "A"')
    update.effective_message.text = None
    await handle_add_player(update, _context(conn, args=["@a"], chat_id=111))
    update.effective_message.reply_text.assert_not_called()


async def test_add_player_revives_soft_deleted(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    await handle_add_player(
        _admin_update('/add_player @alice "Alice"'),
        _context(conn, args=["@alice", '"Alice"'], chat_id=111),
    )
    await handle_remove_player(
        _admin_update("/remove_player @alice"),
        _context(conn, args=["@alice"], chat_id=111),
    )
    update = _admin_update('/add_player @alice "Alice"')
    await handle_add_player(update, _context(conn, args=["@alice", '"Alice"'], chat_id=111))
    assert "revived" in update.effective_message.reply_text.await_args.args[0]


async def test_remove_player_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/remove_player @x")
    update.effective_message = None
    await handle_remove_player(update, _context(conn, args=["@x"], chat_id=111))


async def test_remove_player_no_args(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/remove_player")
    await handle_remove_player(update, _context(conn, args=[], chat_id=111))
    assert update.effective_message.reply_text.await_args.args[0].startswith("Usage")


async def test_remove_player_empty_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/remove_player @")
    await handle_remove_player(update, _context(conn, args=["@"], chat_id=111))
    assert update.effective_message.reply_text.await_args.args[0].startswith("Usage")


async def test_remove_player_unknown_username(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/remove_player @ghost")
    await handle_remove_player(update, _context(conn, args=["@ghost"], chat_id=None))
    assert "Couldn't find" in update.effective_message.reply_text.await_args.args[0]


async def test_remove_player_not_on_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/remove_player @ghost")
    await handle_remove_player(update, _context(conn, args=["@ghost"], chat_id=222))
    assert "wasn't in the active roster" in update.effective_message.reply_text.await_args.args[0]


async def test_list_players_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/list_players")
    update.effective_message = None
    await handle_list_players(update, _context(conn, args=[]))


# ----- /contacts -----


async def test_contacts_empty(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/contacts")
    await handle_contacts(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Nobody has DM'd me yet" in reply


async def test_contacts_flags_non_roster(admin_settings: None, conn: sqlite3.Connection) -> None:
    # Bob is on the roster; Newbie has only DM'd the bot.
    add_player(conn, 111, "Bob", "bob")
    upsert_contact(conn, 111, username="bob", display_name="Bob")
    upsert_contact(conn, 222, username="newbie", display_name="New Bie")

    update = _admin_update("/contacts")
    await handle_contacts(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "@bob" in reply
    assert "@newbie" in reply
    # Only the non-roster contact is flagged.
    assert reply.count("🆕 not on roster") == 1
    assert "available to /add_player (1" in reply
    # Non-roster contact gets a ready-to-copy add-by-id command line.
    assert '/add_player 222 "New Bie"' in reply
    # Roster member (Bob) gets no copy line.
    assert "/add_player 111" not in reply


async def test_contacts_all_on_roster_no_flag(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    upsert_contact(conn, 111, username="bob", display_name="Bob")
    update = _admin_update("/contacts")
    await handle_contacts(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not on roster" not in reply
    assert "available to /add_player" not in reply


async def test_contacts_returns_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/contacts")
    update.effective_message = None
    await handle_contacts(update, _context(conn, args=[]))


# ----- pause / disable / enable voting -----


def _pool(conn: sqlite3.Connection, telegram_id: int) -> sqlite3.Row:
    return conn.execute(
        "SELECT in_pool, pool_paused_until FROM players WHERE telegram_id=?",
        (telegram_id,),
    ).fetchone()


async def test_pause_voting_by_id_sets_timer(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/pause_voting 111 2w")
    await handle_pause_voting(update, _context(conn, args=["111", "2w"]))
    assert _pool(conn, 111)["pool_paused_until"] is not None
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "rate" in reply.lower()


async def test_pause_voting_by_username(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/pause_voting @bob 10d")
    await handle_pause_voting(update, _context(conn, args=["@bob", "10d"]))
    assert _pool(conn, 111)["pool_paused_until"] is not None


async def test_pause_voting_bad_duration(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/pause_voting 111 soon")
    await handle_pause_voting(update, _context(conn, args=["111", "soon"]))
    assert _pool(conn, 111)["pool_paused_until"] is None
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "duration" in reply.lower() or "usage" in reply.lower()


async def test_pause_voting_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/pause_voting")
    await handle_pause_voting(update, _context(conn, args=[]))
    update.effective_message.reply_text.assert_awaited_once()


async def test_pause_voting_unknown_player(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/pause_voting @ghost 2w")
    await handle_pause_voting(update, _context(conn, args=["@ghost", "2w"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "couldn't find" in reply.lower() or "not" in reply.lower()


async def test_disable_voting(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/disable_voting 111")
    await handle_disable_voting(update, _context(conn, args=["111"]))
    assert _pool(conn, 111)["in_pool"] == 0


async def test_disable_voting_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/disable_voting")
    await handle_disable_voting(update, _context(conn, args=[]))
    update.effective_message.reply_text.assert_awaited_once()


async def test_disable_voting_unknown(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/disable_voting @ghost")
    await handle_disable_voting(update, _context(conn, args=["@ghost"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "couldn't find" in reply.lower() or "not" in reply.lower()


async def test_enable_voting_clears_both(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Bob", "bob")
    update = _admin_update("/disable_voting 111")
    await handle_disable_voting(update, _context(conn, args=["111"]))
    update2 = _admin_update("/enable_voting 111")
    await handle_enable_voting(update2, _context(conn, args=["111"]))
    row = _pool(conn, 111)
    assert row["in_pool"] == 1
    assert row["pool_paused_until"] is None


async def test_enable_voting_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/enable_voting")
    await handle_enable_voting(update, _context(conn, args=[]))
    update.effective_message.reply_text.assert_awaited_once()


async def test_enable_voting_unknown(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/enable_voting 999")
    await handle_enable_voting(update, _context(conn, args=["999"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "not" in reply.lower() or "couldn't" in reply.lower()


async def test_pool_handlers_return_without_message(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    for handler in (
        handle_pause_voting,
        handle_disable_voting,
        handle_enable_voting,
        handle_dk_report,
    ):
        update = _admin_update("/x")
        update.effective_message = None
        await handler(update, _context(conn, args=[]))


async def test_dk_report_lists_by_rate(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 1, "Alice", "alice")
    add_player(conn, 2, "Bob", "bob")
    conn.execute(
        "INSERT INTO vote_aggregates (player_a, player_b, axis, a_wins, b_wins, dont_know) "
        "VALUES (1, 2, 'attack', 1, 1, 4)"
    )
    conn.commit()
    update = _admin_update("/dk_report")
    await handle_dk_report(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Alice" in reply
    assert "Bob" in reply
    assert "%" in reply


async def test_dk_report_empty(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/dk_report")
    await handle_dk_report(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "No players" in reply


async def test_add_ghost_creates_and_hints_link(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update('/add_ghost "Late Joiner"')
    await handle_add_ghost(update, _context(conn, args=[]))
    players = list_active_players(conn)
    assert len(players) == 1
    assert players[0].is_ghost is True
    assert players[0].display_name == "Late Joiner"
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "/link_player" in reply
    assert str(players[0].telegram_id) in reply


async def test_add_ghost_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/add_ghost")
    await handle_add_ghost(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_add_ghost_unbalanced_quote(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update('/add_ghost "Unclosed')
    await handle_add_ghost(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_add_ghost_no_text_returns(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/add_ghost")
    update.effective_message.text = None
    await handle_add_ghost(update, _context(conn, args=[]))
    update.effective_message.reply_text.assert_not_awaited()


# ----- /link_player -----


async def test_link_player_by_id_success(admin_settings: None, conn: sqlite3.Connection) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    g = ghost.telegram_id
    upsert_contact(conn, 555, username="latejoiner", display_name="Late Joiner")
    update = _admin_update(f"/link_player {g} 555")
    await handle_link_player(update, _context(conn, args=[str(g), "555"], chat_id=None))
    assert conn.execute("SELECT 1 FROM players WHERE telegram_id=555").fetchone() is not None
    assert conn.execute("SELECT 1 FROM players WHERE telegram_id=?", (g,)).fetchone() is None
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Linked" in reply


async def test_link_player_by_username_success(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    g = ghost.telegram_id
    upsert_contact(conn, 555, username="latejoiner", display_name="Late Joiner")
    update = _admin_update(f"/link_player {g} @latejoiner")
    # chat_id=555 so get_chat resolves @latejoiner → 555.
    await handle_link_player(update, _context(conn, args=[str(g), "@latejoiner"], chat_id=555))
    assert conn.execute("SELECT 1 FROM players WHERE telegram_id=555").fetchone() is not None


async def test_link_player_bad_usage(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/link_player")
    await handle_link_player(update, _context(conn, args=[]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_link_player_non_digit_ghost(admin_settings: None, conn: sqlite3.Connection) -> None:
    update = _admin_update("/link_player abc 555")
    await handle_link_player(update, _context(conn, args=["abc", "555"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert reply.startswith("Usage:")


async def test_link_player_not_a_ghost(admin_settings: None, conn: sqlite3.Connection) -> None:
    add_player(conn, 111, "Real", "real")  # a normal player, not a ghost
    update = _admin_update("/link_player 111 555")
    await handle_link_player(update, _context(conn, args=["111", "555"]))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "isn't a ghost" in reply


async def test_link_player_username_unresolved(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    g = ghost.telegram_id
    update = _admin_update(f"/link_player {g} @nope")
    await handle_link_player(update, _context(conn, args=[str(g), "@nope"], chat_id=None))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Couldn't find" in reply


async def test_link_player_real_not_contact(admin_settings: None, conn: sqlite3.Connection) -> None:
    ghost = add_ghost_player(conn, "Late Joiner")
    g = ghost.telegram_id
    update = _admin_update(f"/link_player {g} 555")  # 555 never DM'd the bot
    await handle_link_player(update, _context(conn, args=[str(g), "555"], chat_id=None))
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "hasn't DM'd" in reply


async def test_link_player_no_message_returns(
    admin_settings: None, conn: sqlite3.Connection
) -> None:
    update = _admin_update("/link_player")
    update.effective_message = None
    await handle_link_player(update, _context(conn, args=[]))
